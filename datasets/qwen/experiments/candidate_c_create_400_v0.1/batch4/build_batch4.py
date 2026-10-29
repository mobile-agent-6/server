"""Build batch 4: 50 non-create negatives (update 20, delete 15, list 15) for create training.

Status: PROPOSED_UNREVIEWED / BE-PROPOSED.  Run 004 checkpoint 600 turned
"... 일정을 ... 으로 옮겨 줘" (lk28, an update) into a create, because all 465
training rows were creates.  These rows teach the operation kind only; the
create validator profile does not apply, so each target is checked with the
Issue #30 contract (contracts.validate_candidate_c) and span checks here.

Scope kept unambiguous on purpose: single turn, trusted server time, explicit
target title, no candidate choice, no change_value or query_scope gaps, no
category-only list.  Model input never contains existing schedules.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ISSUE30 = ROOT.parent / "structured_output_contract_v0.1"
for path in (ROOT / "pilot", ROOT / "batch1", ISSUE30, ROOT.parent / "multiturn_400_v0.1"):
    sys.path.insert(0, str(path))
sys.dont_write_bytecode = True

from build_batch1 import EM, LD, ME, WE, WK, WM, YE  # noqa: E402
import ollama_eval as v1  # noqa: E402

OUTPUT = HERE / "conversations-v0.1.jsonl"
TARGETS = HERE / "candidate-c-targets-v0.1.jsonl"
PREVIEW = HERE / "training-row-preview-v0.1.jsonl"
BUCKET = "non_create_negative"
MAX_PER_FAMILY = 5


def N(number: int, family: str, kind: str, text: str, ref: str, *,
      target: dict | None = None, change: dict | None = None, filt: dict | None = None) -> dict[str, Any]:
    return {"number": number, "family": family, "kind": kind, "text": text, "ref": ref,
            "target": target or {}, "change": change or {}, "filter": filt or {}}


SPECS = [
    # ---- update: 20 ----
    N(1, "neg_update_time", "update", "협력사 단가 회의를 오후 4시로 옮겨 줘", WM,
      target={"title": "협력사 단가 회의"}, change={"time": "오후 4시"}),
    N(2, "neg_update_time", "update", "내일 창고 점검 시간을 오전 11시로 바꿔 줘", WE,
      target={"title": "창고 점검", "date": "내일"}, change={"time": "오전 11시"}),
    N(3, "neg_update_time", "update", "반품 교육 일정 오후 2시 반으로 변경해 줘", EM,
      target={"title": "반품 교육"}, change={"time": "오후 2시 반"}),
    N(4, "neg_update_time", "update", "입고 서류 정리는 오전 9시로 당겨 줘", WK,
      target={"title": "입고 서류 정리"}, change={"time": "오전 9시"}),
    N(5, "neg_update_time", "update", "재고 마감 회의 17시로 미뤄 줘", ME,
      target={"title": "재고 마감 회의"}, change={"time": "17시"}),
    N(6, "neg_update_date", "update", "출고 설명회를 모레로 옮겨 줘", LD,
      target={"title": "출고 설명회"}, change={"date": "모레"}),
    N(7, "neg_update_date", "update", "연말 결산 보고를 2029년 1월 5일로 바꿔 줘", YE,
      target={"title": "연말 결산 보고"}, change={"date": "2029년 1월 5일"}),
    N(8, "neg_update_date", "update", "거래처 방문 일정을 이번 주 금요일로 미뤄 주세요", WM,
      target={"title": "거래처 방문"}, change={"date": "이번 주 금요일"}),
    N(9, "neg_update_date", "update", "피킹 교육은 글피로 변경해 주세요", WE,
      target={"title": "피킹 교육"}, change={"date": "글피"}),
    N(10, "neg_update_date", "update", "창고 소독 작업 4월 12일로 옮겨 줘", EM,
      target={"title": "창고 소독 작업"}, change={"date": "4월 12일"}),
    N(11, "neg_update_title", "update", "물류 회의 제목을 물류 전략 회의로 바꿔 줘", WK,
      target={"title": "물류 회의"}, change={"title": "물류 전략 회의"}),
    N(12, "neg_update_title", "update", "안전 교육을 소방 교육으로 이름 바꿔 줘", ME,
      target={"title": "안전 교육"}, change={"title": "소방 교육"}),
    N(13, "neg_update_title", "update", "팀 회의 제목 주간 회고로 변경해 주세요", LD,
      target={"title": "팀 회의"}, change={"title": "주간 회고"}),
    N(14, "neg_update_title", "update", "송년회 일정 이름을 신년 인사회로 고쳐 줘", YE,
      target={"title": "송년회"}, change={"title": "신년 인사회"}),
    N(15, "neg_update_title", "update", "상담 일정을 인턴 면담으로 바꿔 주세요", WM,
      target={"title": "상담"}, change={"title": "인턴 면담"}),
    N(16, "neg_update_datetime", "update", "협력사 미팅을 내일 오후 3시로 옮겨 줘", WE,
      target={"title": "협력사 미팅"}, change={"date": "내일", "time": "오후 3시"}),
    N(17, "neg_update_datetime", "update", "포장 검수를 모레 오전 10시로 바꿔 줘", EM,
      target={"title": "포장 검수"}, change={"date": "모레", "time": "오전 10시"}),
    N(18, "neg_update_datetime", "update", "배송 기사 교육 5월 20일 오후 2시로 변경해 주세요", WK,
      target={"title": "배송 기사 교육"}, change={"date": "5월 20일", "time": "오후 2시"}),
    N(19, "neg_update_datetime", "update", "월례 회의를 7월 3일 14시로 미뤄 줘", ME,
      target={"title": "월례 회의"}, change={"date": "7월 3일", "time": "14시"}),
    N(20, "neg_update_datetime", "update", "보안 점검 일정을 글피 오후 4시로 옮겨 주세요", LD,
      target={"title": "보안 점검"}, change={"date": "글피", "time": "오후 4시"}),

    # ---- delete: 15 ----
    N(21, "neg_delete_title", "delete", "협력사 친목 모임 취소해 줘", WM, target={"title": "협력사 친목 모임"}),
    N(22, "neg_delete_title", "delete", "반품 협의 일정 삭제해 줘", WE, target={"title": "반품 협의"}),
    N(23, "neg_delete_title", "delete", "재고 이관 작업 지워 줘", EM, target={"title": "재고 이관 작업"}),
    N(24, "neg_delete_title", "delete", "창고 견학 일정 빼 줘", WK, target={"title": "창고 견학"}),
    N(25, "neg_delete_title", "delete", "입고 리허설 없애 줘", ME, target={"title": "입고 리허설"}),
    N(26, "neg_delete_dated", "delete", "내일 출고 점검 취소해 줘", LD,
      target={"title": "출고 점검", "date": "내일"}),
    N(27, "neg_delete_dated", "delete", "2029년 1월 3일 시무식 일정 삭제해 줘", YE,
      target={"title": "시무식", "date": "2029년 1월 3일"}),
    N(28, "neg_delete_dated", "delete", "모레 거래처 미팅 취소해 줘", WM,
      target={"title": "거래처 미팅", "date": "모레"}),
    N(29, "neg_delete_dated", "delete", "이번 주 금요일 피킹 점검 지워 줘", WE,
      target={"title": "피킹 점검", "date": "이번 주 금요일"}),
    N(30, "neg_delete_dated", "delete", "4월 10일 반품 회의 삭제해 줘", EM,
      target={"title": "반품 회의", "date": "4월 10일"}),
    N(31, "neg_delete_polite", "delete", "협력 업체 방문 일정 취소해 주세요", WK, target={"title": "협력 업체 방문"}),
    N(32, "neg_delete_polite", "delete", "내일 품질 회의 삭제해 주세요", ME,
      target={"title": "품질 회의", "date": "내일"}),
    N(33, "neg_delete_polite", "delete", "재고 실사 일정은 취소해 주실래요", LD, target={"title": "재고 실사"}),
    N(34, "neg_delete_polite", "delete", "연간 평가 면담 지워 주세요", YE, target={"title": "연간 평가 면담"}),
    N(35, "neg_delete_polite", "delete", "포장 작업대 정리 일정 빼 주세요", WM, target={"title": "포장 작업대 정리"}),

    # ---- list: 15 ----
    N(36, "neg_list_date", "list", "내일은 무슨 일정이 잡혀 있지?", WE, filt={"date": "내일"}),
    N(37, "neg_list_date", "list", "글피에 뭐 있는지 알려 줘", EM, filt={"date": "글피"}),
    N(38, "neg_list_date", "list", "이번 주 일요일 일정 보여 줘", WK, filt={"date": "이번 주 일요일"}),
    N(39, "neg_list_date", "list", "7월 5일에 잡힌 일정 있어?", ME, filt={"date": "7월 5일"}),
    N(40, "neg_list_date", "list", "2028년 3월 3일 일정 확인해 줘", LD, filt={"date": "2028년 3월 3일"}),
    N(41, "neg_list_title", "list", "시무식 언제야?", YE, filt={"title": "시무식"}),
    N(42, "neg_list_title", "list", "협력사 미팅 일정 찾아 줘", WM, filt={"title": "협력사 미팅"}),
    N(43, "neg_list_title", "list", "품질 감사 회의 있는지 봐 줘", WE, filt={"title": "품질 감사 회의"}),
    N(44, "neg_list_title", "list", "거래처 정산 일정 보여 줘", EM, filt={"title": "거래처 정산"}),
    N(45, "neg_list_title", "list", "창고 보수 작업 잡혀 있어?", WK, filt={"title": "창고 보수 작업"}),
    N(46, "neg_list_date_category", "list", "모레 반품 일정 다 보여 줘", ME, filt={"date": "모레", "category": "반품"}),
    N(47, "neg_list_date_category", "list", "내일 입고 일정 알려 줘", LD, filt={"date": "내일", "category": "입고"}),
    N(48, "neg_list_date_category", "list", "이번 주 토요일 재고 관련 일정 뭐 있어?", WM,
      filt={"date": "이번 주 토요일", "category": "재고"}),
    N(49, "neg_list_date_category", "list", "모레 출고 일정 확인해 줘", YE, filt={"date": "모레", "category": "출고"}),
    N(50, "neg_list_date_category", "list", "이번 주 금요일 반품 일정 보여 줘", WE,
      filt={"date": "이번 주 금요일", "category": "반품"}),
]

ROLE_ORDER = ("title", "date", "time", "category")


def _spans(slots: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    return {role: [{"status": "adopted", "text": slots[role], "turn": 1}] for role in ROLE_ORDER if role in slots}


def make_case(spec: dict[str, Any]) -> dict[str, Any]:
    case_id = f"c3-neg-b4-{spec['number']:03d}"
    return {
        "meta": {
            "caseId": case_id,
            "status": "PROPOSED",
            "datasetRole": "training_candidate",
            "authoredOn": "2026-10-28",
            "notes": (f"state=PROPOSED_UNREVIEWED;bucket={BUCKET};familyId={spec['family']};"
                      f"templateLineage=candidate-c-create-b4-v0.1:{spec['family']};kind={spec['kind']}"),
        },
        "context": {
            "referenceTime": spec["ref"], "timezone": "Asia/Seoul", "serverTimeTrusted": True,
            "priorContext": {"kind": "none"},
            "turns": [{"turn": 1, "inputType": "text", "inputSource": "typed_text", "text": spec["text"]}],
            "initialSchedules": [],
        },
        "semantics": {"operations": [{"index": 1, "kind": spec["kind"], "missingInformation": []}]},
    }


def make_target(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "missingInformation": [],
        "operations": [{
            "changeEvidence": _spans(spec["change"]), "filterEvidence": _spans(spec["filter"]),
            "index": 1, "kind": spec["kind"], "missingInformation": [],
            "targetEvidence": _spans(spec["target"]), "unsupportedEvidence": [],
        }],
        "unsupportedEvidence": [],
    }


def authoring_errors() -> list[str]:
    errors: list[str] = []
    for spec in SPECS:
        cid = spec["number"]
        roles = {"update": ("target", "change"), "delete": ("target",), "list": ("filter",)}[spec["kind"]]
        for slot in ("target", "change", "filter"):
            if spec[slot] and slot not in roles:
                errors.append(f"{cid}: {slot} not allowed for {spec['kind']}")
        if "title" not in spec["target"] and spec["kind"] != "list":
            errors.append(f"{cid}: update/delete needs an explicit target title")
        for slot in roles:
            for text in spec[slot].values():
                if spec["text"].count(text) != 1:
                    errors.append(f"{cid}: span {text!r} must occur exactly once")
        if spec["kind"] == "update" and not spec["change"]:
            errors.append(f"{cid}: update without change")
    for family, count in Counter(spec["family"] for spec in SPECS).items():
        if count > MAX_PER_FAMILY:
            errors.append(f"FAMILY_OVER_LIMIT {family} {count}")
    if len({spec["text"] for spec in SPECS}) != len(SPECS):
        errors.append("duplicate utterance")
    errors.extend(_contamination_errors())
    return errors


LOCKED_SIMILARITY_LIMIT = 0.3  # review b4: old 037 "모레 일정 알려 줘" vs locked lk22 = 0.333 here; current max 0.29


def _contamination_errors() -> list[str]:
    """Compare utterances and target titles with locked56, Test 50 and the create batches."""
    sys.path.insert(0, str(ROOT))
    import validator  # noqa: PLC0415

    extra = (("pilot30", validator.PILOT), ("batch1", validator.BATCH1_CONFIG.conversations),
             ("batch2", validator.BATCH2_CONFIG.conversations), ("batch3", validator.BATCH3_CONFIG.conversations),
             ("test50", validator.TEST50_CONFIG.conversations))
    sources = validator._source_utterances(extra)
    protected = [(name, text) for name, text in sources if name.startswith(("locked56", "test50"))]
    locked_titles = set()
    for path in (validator.LOCKED56, validator.TEST50_CONFIG.conversations):
        for case in validator.load_jsonl(path):
            for operation in case["semantics"]["operations"]:
                for role in ("target", "change", "filter"):
                    for span in (operation.get("evidence", {}).get(role, {}) or {}).get("title", {}).get("adoptedSpans", []):
                        locked_titles.add(span["text"])
    errors = []
    for spec in SPECS:
        text = validator._normalize_text(spec["text"])
        if any(text == source for _name, source in sources):
            errors.append(f"{spec['number']}: utterance exactly matches an existing source")
        near = max((validator._jaccard(text, source), name) for name, source in protected)
        if near[0] >= LOCKED_SIMILARITY_LIMIT:
            errors.append(f"{spec['number']}: utterance close to {near[1]} ({near[0]:.2f})")
        for slots in (spec["target"], spec["change"], spec["filter"]):
            if slots.get("title") in locked_titles:
                errors.append(f"{spec['number']}: title {slots['title']!r} used in locked56 or Test 50")
    return errors


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> None:
    errors = authoring_errors()
    if errors:
        raise RuntimeError("\n".join(errors))
    cases, targets, preview = [], [], []
    system = v1.prompt_text("C")
    for spec in SPECS:
        case = make_case(spec)
        target = make_target(spec)
        raw = canonical(target)
        parsed = v1.contracts.validate_candidate_c(raw, user_turns=v1._visible_text_turns(case, 1))
        if [op.get("kind") if isinstance(op, dict) else getattr(op, "kind", None)
                for op in (parsed["operations"] if isinstance(parsed, dict) else parsed.operations)] != [spec["kind"]]:
            raise RuntimeError(f"{case['meta']['caseId']}: contract parse changed the operation kind")
        cases.append(case)
        targets.append({"caseId": case["meta"]["caseId"], "turn": 1, "target": target, "status": "PROPOSED_UNREVIEWED"})
        preview.append({"caseId": case["meta"]["caseId"], "turn": 1, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": v1.render_extraction_context(case, 1, stage="EVIDENCE")},
            {"role": "assistant", "content": raw},
        ]})
    for path, rows in ((OUTPUT, cases), (TARGETS, targets), (PREVIEW, preview)):
        path.write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
    print(canonical({"status": "PROPOSED_UNREVIEWED", "caseCount": len(cases),
                     "sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
                     "targetsSha256": hashlib.sha256(TARGETS.read_bytes()).hexdigest(),
                     "kinds": dict(Counter(spec["kind"] for spec in SPECS))}))


if __name__ == "__main__":
    main()
