"""Build DATA-only, unreviewed failure and selection probes outside the 400 split."""

import copy
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import run_eval
from runtime.replay_multiturn_pilot import replay, validate_scenario


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "train" / "scenarios_UNREVIEWED.jsonl"
OUT = ROOT / "diagnostic"
sources = {case["id"]: case for case in run_eval.load_cases(SOURCE)}
probes = []
rubric = {}


def add_case(case, questions, fault_count):
    validate_scenario(case)
    replay(case)
    rubric[case["id"]] = questions
    client = run_eval.ReferenceClient([action for turn in case["turns"]
                                       for action in turn["model_outputs"]])
    result = run_eval.run_case(case, client, rubric)
    if not result["provisional_match"] or len(result["fault_events"]) != fault_count:
        raise ValueError("diagnostic replay failed: " + case["id"])
    probes.append(case)


for name, effect, retry_user in (
    ("commit-response-lost", "commit_then_timeout", "재시도"),
    ("post-rejected", "reject_before_commit", "오전 10시로 다시 등록해줘."),
):
    case = copy.deepcopy(sources["in-create-time"])
    case["id"] = "diagnostic-create-" + name
    case["conversation_group_id"] = "diagnostic:api-failure:" + name
    case["created_id"] = str(uuid5(NAMESPACE_URL, case["id"]))
    case["expect_final"][0]["id"] = case["created_id"]
    case["faults"] = [{"method": "POST", "occurrence": 1, "effect": effect}]
    create_turn = case["turns"][1]
    create_turn["expect"]["reply_kind"] = "error"
    retry = copy.deepcopy(create_turn)
    retry["user"] = retry_user
    retry["model_outputs"] = ([] if effect == "commit_then_timeout"
                              else copy.deepcopy(create_turn["model_outputs"]))
    retry["expect"]["reply_kind"] = "created"
    case["turns"].append(retry)
    add_case(case, {"1": "time"}, 1)

stale = copy.deepcopy(sources["in-update-approve"])
stale["id"] = "diagnostic-update-stale-before-approval"
stale["conversation_group_id"] = "diagnostic:stale-update"
stale["faults"] = [{"method": "GET", "occurrence": 2, "effect": "stale_before_read"}]
stale["turns"][1]["expect"] = {"reply_kind": "stale", "writes": 0,
                                 "api_methods": ["GET"]}
stale["expect_final"] = [{key: value for key, value in item.items()
                          if key not in {"createdAt", "updatedAt"}}
                         for item in stale["initial_schedules"]]
add_case(stale, {}, 1)

crowded = copy.deepcopy(sources["ret-delete-select-approve"])
crowded["id"] = "diagnostic-delete-eleven-candidates"
crowded["conversation_group_id"] = "diagnostic:selection-over-ten"
crowded["turns"] = crowded["turns"][:1]
crowded["turns"][0]["expect"]["reply_contains"] = ["좁혀"]
for index in range(9):
    extra = copy.deepcopy(crowded["initial_schedules"][0])
    extra["id"] = str(uuid5(NAMESPACE_URL, crowded["id"] + ":" + str(index)))
    extra["title"] = f"반품 추가 확인 {index + 1}"
    crowded["initial_schedules"].append(extra)
crowded["expect_final"] = [{key: value for key, value in item.items()
                            if key not in {"createdAt", "updatedAt"}}
                           for item in crowded["initial_schedules"]]
add_case(crowded, {"1": "scope"}, 0)

category = copy.deepcopy(sources["out-update-title"])
category["id"] = "diagnostic-update-category-explicit"
category["conversation_group_id"] = "diagnostic:category-update"
category["turns"][0]["user"] = "내일 주문 상품 피킹 일정 카테고리를 기타로 바꿔줘."
category["turns"][0]["model_outputs"][1]["request"]["changes"] = {"category": "OTHER"}
category["expect_final"][0]["title"] = category["initial_schedules"][0]["title"]
category["expect_final"][0]["category"] = "OTHER"
add_case(category, {}, 0)

# Keep these supplemental cases outside the historical 400 so their addition
# cannot silently change an already reported score or a review source hash.
selection = copy.deepcopy(sources["ret-delete-select-approve"])
selection["id"] = "diagnostic-delete-select-two-candidates"
selection["conversation_group_id"] = "diagnostic:selection-two"
add_case(selection, {"1": "selection"}, 0)

for method, source_id in (("PATCH", "in-update-approve"),
                          ("DELETE", "in-delete-approve")):
    for effect in ("reject_before_commit", "commit_then_timeout"):
        case = copy.deepcopy(sources[source_id])
        case["id"] = f"diagnostic-{method.lower()}-{effect.replace('_', '-')}"
        case["conversation_group_id"] = f"diagnostic:{method.lower()}:{effect}"
        case["faults"] = [{"method": method, "occurrence": 1, "effect": effect}]
        expected = case["turns"][-1]["expect"]
        expected["reply_kind"] = "error"
        expected["api_methods"] = ["GET", method] + (["GET"] if effect == "commit_then_timeout" else [])
        if effect == "reject_before_commit":
            case["expect_final"] = [
                {key: value for key, value in item.items()
                 if key not in {"createdAt", "updatedAt"}}
                for item in case["initial_schedules"]]
        add_case(case, {}, 1)

OUT.mkdir(exist_ok=True)
run_eval.save_jsonl(OUT / "scenarios_UNREVIEWED.jsonl", probes)
run_eval.save_json(OUT / "question_rubric.json", rubric)
print(f"{len(probes)} unreviewed diagnostic probes replayed")
