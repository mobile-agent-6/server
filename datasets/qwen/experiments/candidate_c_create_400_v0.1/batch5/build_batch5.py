"""Build batch 5: 36 non-create negatives covering the gaps batch 4 review listed.

Status: PROPOSED_UNREVIEWED / BE-PROPOSED.  Run 005 stopped on locked lk23: the
model listed "2028년 10월" and "입고" but dropped the completed filter "아직 안 끝난".
Batch 4 had no completed filter, futureScope, cue-bearing list title, target plus
change dates, category or completion update, or missing change value.

Span conventions follow the Issue #30 oracle (checked on locked outputs only for
shape, never copying sentences): completed filter keeps the phrase ("아직 안 끝난"),
"분류가 X" keeps the whole phrase as category, completion update keeps the request
phrase ("완료로 표시해 줘"), and an update without a new value reports change_value.
Each target passes contracts.validate_candidate_c and the common gate role check.
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
for path in (ROOT / "pilot", ROOT / "batch1", ROOT / "batch4", ISSUE30, ROOT.parent / "multiturn_400_v0.1"):
    sys.path.insert(0, str(path))
sys.dont_write_bytecode = True

from build_batch1 import EM, LD, ME, WE, WK, WM, YE  # noqa: E402
from build_batch4 import SPECS as B4_SPECS  # noqa: E402
import common_gate_v0_2 as gate_v2  # noqa: E402
import ollama_eval as v1  # noqa: E402

OUTPUT = HERE / "conversations-v0.1.jsonl"
TARGETS = HERE / "candidate-c-targets-v0.1.jsonl"
PREVIEW = HERE / "training-row-preview-v0.1.jsonl"
BUCKET = "non_create_negative"
MAX_PER_FAMILY = 5
LOCKED_SIMILARITY_LIMIT = 0.35
ROLE_ORDER = ("title", "date", "time", "category", "completedState", "completed", "futureScope")


def N(number: int, family: str, kind: str, text: str, ref: str, *, target: dict | None = None,
      change: dict | None = None, filt: dict | None = None, missing: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"number": number, "family": family, "kind": kind, "text": text, "ref": ref,
            "target": target or {}, "change": change or {}, "filter": filt or {}, "missing": list(missing)}


SPECS = [
    # ---- list with completed filter: 8 ----
    N(1, "neg5_list_completed", "list", "완료된 출고 서류 점검 일정 보여 줘", WM,
      filt={"title": "출고 서류 점검", "completed": "완료된"}),
    N(2, "neg5_list_completed", "list", "아직 안 끝난 협력사 정산 일정 알려 줘", WE,
      filt={"title": "협력사 정산", "completed": "아직 안 끝난"}),
    N(3, "neg5_list_completed", "list", "이번 주 금요일 일정 중에 완료된 것만 보여 줘", EM,
      filt={"date": "이번 주 금요일", "completed": "완료된"}),
    N(4, "neg5_list_completed", "list", "내일 반품 일정 중 미완료인 거 알려 줘", WK,
      filt={"date": "내일", "category": "반품", "completed": "미완료"}),
    N(5, "neg5_list_completed", "list", "7월 재고 일정 중에 아직 안 끝난 거 뭐야?", ME,
      filt={"date": "7월", "category": "재고", "completed": "아직 안 끝난"}),
    N(6, "neg5_list_completed_b", "list", "완료된 지게차 점검 기록 찾아 줘", LD,
      filt={"title": "지게차 점검 기록", "completed": "완료된"}),
    N(7, "neg5_list_completed_b", "list", "모레 입고 일정 중에 완료된 것 있어?", YE,
      filt={"date": "모레", "category": "입고", "completed": "완료된"}),
    N(8, "neg5_list_completed_b", "list", "2028년 3월 일정 가운데 아직 안 끝난 출고 건 보여 줘", WE,
      filt={"date": "2028년 3월", "completed": "아직 안 끝난", "category": "출고"}),
    # ---- list futureScope: 5 ----
    N(9, "neg5_list_future", "list", "앞으로 남은 거래처 미팅 일정 알려 줘", WM,
      filt={"title": "거래처 미팅", "futureScope": "앞으로 남은"}),
    N(10, "neg5_list_future", "list", "앞으로 남은 반품 일정 다 보여 줘", EM,
      filt={"category": "반품", "futureScope": "앞으로 남은"}),
    N(11, "neg5_list_future", "list", "안전 교육 일정 앞으로 남은 거 있어?", WK,
      filt={"title": "안전 교육", "futureScope": "앞으로 남은"}),
    N(12, "neg5_list_future", "list", "앞으로 남은 출고 일정 뭐 있는지 알려 줘", ME,
      filt={"category": "출고", "futureScope": "앞으로 남은"}),
    N(13, "neg5_list_future", "list", "앞으로 남은 냉동기 정비 일정 찾아 줘", LD,
      filt={"title": "냉동기 정비", "futureScope": "앞으로 남은"}),
    # ---- list title containing a category cue (title only): 5 ----
    N(14, "neg5_list_cue_title", "list", "입고 차량 대기 정리 일정 다 보여 줘", WE,
      filt={"title": "입고 차량 대기 정리"}),
    N(15, "neg5_list_cue_title", "list", "재고 위치 변경 작업 언제 잡혀 있어?", EM,
      filt={"title": "재고 위치 변경 작업"}),
    N(16, "neg5_list_cue_title", "list", "반품 사유 정리 회의 일정 찾아 줘", WK,
      filt={"title": "반품 사유 정리 회의"}),
    N(17, "neg5_list_cue_title", "list", "출고 박스 규격 점검 있는지 봐 줘", ME,
      filt={"title": "출고 박스 규격 점검"}),
    N(18, "neg5_list_cue_title", "list", "포장 테이프 발주 일정 알려 줘", YE,
      filt={"title": "포장 테이프 발주"}),
    # ---- list "분류가 X" phrasing: 3 ----
    N(19, "neg5_list_category_phrase", "list", "분류가 반품인 내일 일정 보여 줘", WM,
      filt={"category": "분류가 반품인", "date": "내일"}),
    N(20, "neg5_list_category_phrase", "list", "모레 일정 중에 분류가 출고인 것만 알려 줘", WE,
      filt={"date": "모레", "category": "분류가 출고인"}),
    N(21, "neg5_list_category_phrase", "list", "분류가 재고인 이번 주 토요일 일정 뭐 있어?", EM,
      filt={"category": "분류가 재고인", "date": "이번 주 토요일"}),
    N(37, "neg5_list_category_phrase", "list", "분류가 반품이고 완료된 모레 일정 알려 줘", LD,
      filt={"category": "분류가 반품", "completed": "완료된", "date": "모레"}),
    # ---- update target date + change date: 6 ----
    N(22, "neg5_update_target_change_date", "update", "3월 20일 협력사 교육을 3월 21일 오후 2시로 옮겨 줘", WE,
      target={"date": "3월 20일", "title": "협력사 교육"}, change={"date": "3월 21일", "time": "오후 2시"}),
    N(23, "neg5_update_target_change_date", "update", "내일 있는 창고 청소를 모레 오전 9시로 미뤄 줘", WM,
      target={"date": "내일", "title": "창고 청소"}, change={"date": "모레", "time": "오전 9시"}),
    N(24, "neg5_update_target_change_date", "update", "5월 15일 재고 보고를 5월 16일 오전 10시 30분으로 변경해 주세요", WK,
      target={"date": "5월 15일", "title": "재고 보고"}, change={"date": "5월 16일", "time": "오전 10시 30분"}),
    N(25, "neg5_update_target_change_date", "update", "7월 3일 거래처 상담을 7월 4일 오후 3시로 바꿔 줘", ME,
      target={"date": "7월 3일", "title": "거래처 상담"}, change={"date": "7월 4일", "time": "오후 3시"}),
    N(26, "neg5_update_target_change_date", "update", "2029년 1월 4일 연초 목표 점검을 2029년 1월 6일 오전 11시로 옮겨 주세요", YE,
      target={"date": "2029년 1월 4일", "title": "연초 목표 점검"}, change={"date": "2029년 1월 6일", "time": "오전 11시"}),
    N(27, "neg5_update_target_change_date_b", "update", "모레 하역장 점검을 글피 오후 1시로 미뤄 줘", LD,
      target={"date": "모레", "title": "하역장 점검"}, change={"date": "글피", "time": "오후 1시"}),
    # ---- update category change: 3 ----
    N(28, "neg5_update_category", "update", "주말 하역 인력 배치는 반품으로 분류 바꿔 줘", WM,
      target={"title": "주말 하역 인력 배치"}, change={"category": "반품"}),
    N(29, "neg5_update_category", "update", "야간 정리 작업을 재고 분류로 변경해 주세요", EM,
      target={"title": "야간 정리 작업"}, change={"category": "재고"}),
    N(30, "neg5_update_category", "update", "물류 파트너 간담회 카테고리를 입고로 바꿔 줘", ME,
      target={"title": "물류 파트너 간담회"}, change={"category": "입고"}),
    # ---- update completion: 3 ----
    N(31, "neg5_update_completed", "update", "소화기 점검표 작성 일정 완료 처리 부탁해요", WE,
      target={"title": "소화기 점검표 작성"}, change={"completed": "완료 처리 부탁해요"}),
    N(32, "neg5_update_completed", "update", "팔레트 수거 일정 완료 처리해 줘", WK,
      target={"title": "팔레트 수거"}, change={"completed": "완료 처리해 줘"}),
    N(33, "neg5_update_completed", "update", "협력사 서류 제출 끝났으니 완료로 바꿔 주세요", LD,
      target={"title": "협력사 서류 제출"}, change={"completed": "완료로 바꿔 주세요"}),
    # ---- update without a new value: 3 ----
    N(34, "neg5_update_no_value", "update", "물류 보고 회의 시간 바꿔 줘", WM,
      target={"title": "물류 보고 회의"}, missing=("change_value",)),
    N(35, "neg5_update_no_value", "update", "반품 상담 일정 날짜 좀 옮겨 주세요", WE,
      target={"title": "반품 상담"}, missing=("change_value",)),
    N(36, "neg5_update_no_value", "update", "창고 보안 교육 일정 변경하고 싶어", EM,
      target={"title": "창고 보안 교육"}, missing=("change_value",)),
]


def _spans(slots: dict[str, str]) -> dict[str, list[dict[str, Any]]]:
    return {role: [{"status": "adopted", "text": slots[role], "turn": 1}] for role in ROLE_ORDER if role in slots}


def make_case(spec: dict[str, Any]) -> dict[str, Any]:
    case_id = f"c3-neg-b5-{spec['number']:03d}"
    return {
        "meta": {
            "caseId": case_id, "status": "PROPOSED", "datasetRole": "training_candidate", "authoredOn": "2026-10-29",
            "notes": (f"state=PROPOSED_UNREVIEWED;bucket={BUCKET};familyId={spec['family']};"
                      f"templateLineage=candidate-c-create-b5-v0.1:{spec['family']};kind={spec['kind']}"),
        },
        "context": {
            "referenceTime": spec["ref"], "timezone": "Asia/Seoul", "serverTimeTrusted": True,
            "priorContext": {"kind": "none"},
            "turns": [{"turn": 1, "inputType": "text", "inputSource": "typed_text", "text": spec["text"]}],
            "initialSchedules": [],
        },
        "semantics": {"operations": [{"index": 1, "kind": spec["kind"],
                                      "missingInformation": [{"item": m, "detectedAtTurn": 1} for m in spec["missing"]]}]},
    }


def make_target(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "missingInformation": list(spec["missing"]),
        "operations": [{
            "changeEvidence": _spans(spec["change"]), "filterEvidence": _spans(spec["filter"]),
            "index": 1, "kind": spec["kind"], "missingInformation": list(spec["missing"]),
            "targetEvidence": _spans(spec["target"]), "unsupportedEvidence": [],
        }],
        "unsupportedEvidence": [],
    }


def authoring_errors() -> list[str]:
    errors: list[str] = []
    for spec in SPECS:
        cid = spec["number"]
        for slot in ("target", "change", "filter"):
            for text in spec[slot].values():
                if spec["text"].count(text) != 1:
                    errors.append(f"{cid}: span {text!r} must occur exactly once")
        if spec["kind"] == "update" and not spec["change"] and spec["missing"] != ["change_value"]:
            errors.append(f"{cid}: update without change must report change_value")
    for family, count in Counter(spec["family"] for spec in SPECS).items():
        if count > MAX_PER_FAMILY:
            errors.append(f"FAMILY_OVER_LIMIT {family} {count}")
    texts = [spec["text"] for spec in SPECS] + [spec["text"] for spec in B4_SPECS]
    if len(set(texts)) != len(texts):
        errors.append("duplicate utterance within batch 4 and 5")
    errors.extend(_contamination_errors())
    return errors


def _contamination_errors() -> list[str]:
    sys.path.insert(0, str(ROOT))
    import validator  # noqa: PLC0415

    extra = (("test50", validator.TEST50_CONFIG.conversations),)
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
        near = max((validator._jaccard(text, source), name) for name, source in protected)
        if near[0] >= LOCKED_SIMILARITY_LIMIT:
            errors.append(f"{spec['number']}: utterance close to {near[1]} ({near[0]:.2f})")
        for slots in (spec["target"], spec["change"], spec["filter"]):
            title = slots.get("title")
            if title and any(title == t or title in t or t in title
                             or validator._jaccard(validator._normalize_text(title), validator._normalize_text(t)) >= 0.35
                             for t in locked_titles):
                errors.append(f"{spec['number']}: title {title!r} overlaps or resembles a locked56 or Test 50 title")
    return errors


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> None:
    errors = authoring_errors()
    system = v1.prompt_text("C")
    cases, targets, preview = [], [], []
    for spec in SPECS:
        case = make_case(spec)
        target = make_target(spec)
        raw = canonical(target)
        turns = v1._visible_text_turns(case, 1)
        v1.contracts.validate_candidate_c(raw, user_turns=turns)
        gate = gate_v2.validate_candidate_c_role_semantics(target, user_turns=turns, reference_time=spec["ref"])
        if not gate.accepted:
            errors.append(f"{case['meta']['caseId']}: gate role check {gate.code}")
        cases.append(case)
        targets.append({"caseId": case["meta"]["caseId"], "turn": 1, "target": target, "status": "PROPOSED_UNREVIEWED"})
        preview.append({"caseId": case["meta"]["caseId"], "turn": 1, "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": v1.render_extraction_context(case, 1, stage="EVIDENCE")},
            {"role": "assistant", "content": raw},
        ]})
    if errors:
        raise RuntimeError("\n".join(errors))
    for path, rows in ((OUTPUT, cases), (TARGETS, targets), (PREVIEW, preview)):
        path.write_text("".join(canonical(row) + "\n" for row in rows), encoding="utf-8")
    print(canonical({"status": "PROPOSED_UNREVIEWED", "caseCount": len(cases),
                     "sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
                     "targetsSha256": hashlib.sha256(TARGETS.read_bytes()).hexdigest(),
                     "kinds": dict(Counter(spec["kind"] for spec in SPECS))}))


if __name__ == "__main__":
    main()
