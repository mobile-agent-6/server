"""Build the 30-case Candidate-C create pilot deterministically.

This script writes only proposed, unreviewed development probes.  It does not
create the 400-case dataset, open a Test split, call a model, or touch backend
state.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from spec_checks import check_specs

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ISSUE30 = ROOT.parent / "structured_output_contract_v0.1"
OUTPUT = HERE / "conversations-v0.1.jsonl"
TARGETS = HERE / "candidate-c-targets-v0.1.jsonl"
SOURCE_REGISTRY = ROOT / "source-registry-v0.1.jsonl"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evidence(
    adopted: list[tuple[int, str]],
    *,
    superseded: list[tuple[int, str]] | None = None,
    markers: list[tuple[int, str]] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "adoptedSpans": [{"turn": turn, "text": text} for turn, text in adopted]
    }
    if superseded:
        value["supersededSpans"] = [
            {"turn": turn, "text": text} for turn, text in superseded
        ]
    if markers:
        value["correctionMarkerSpans"] = [
            {"turn": turn, "text": text} for turn, text in markers
        ]
    return value


def turn(text: str, *, answer: int | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "turn": 0,
        "inputType": "text",
        "inputSource": "typed_text",
        "text": text,
    }
    if answer is not None:
        value["answersQuestionOfTurn"] = answer
    return value


def expected(
    kind: str,
    *,
    write: int = 0,
    model_call: str = "model_call_expected",
    purpose: str | None = None,
    outcome: str | None = None,
    failure: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "write": write,
        "modelCallPolicy": model_call,
        "purpose": purpose,
        "outcome": outcome,
        "failure": failure,
    }


def spec(
    number: int,
    bucket: str,
    family: str,
    text_turns: list[dict[str, Any]],
    *,
    reference: str = "2028-02-10T08:20:00+09:00",
    trusted: bool = True,
    title: dict[str, Any] | None,
    date: dict[str, Any] | None,
    time: dict[str, Any] | None,
    category_evidence: dict[str, Any] | None,
    scheduled_at: str | None,
    category: str,
    missing: list[dict[str, Any]] | None = None,
    expectations: list[dict[str, Any]],
    unsupported: tuple[int, str, str] | None = None,
    gap: tuple[str, str, str] | None = None,
    target_overrides: dict[int, dict[str, dict[str, Any] | None]] | None = None,
    correction_provenance: str | None = None,
    final_status: str = "success",
    batch: str = "pilot",
    dataset_role: str = "development_probe",
    authored_on: str = "2026-10-26",
) -> dict[str, Any]:
    for index, item in enumerate(text_turns, 1):
        item["turn"] = index
    width = 2 if batch == "pilot" else 3
    return {
        "caseId": f"c3-create-{batch}-{number:0{width}d}",
        "bucket": bucket,
        "family": family,
        "lineage": f"candidate-c-create-{batch}-v0.1:{family}",
        "datasetRole": dataset_role,
        "authoredOn": authored_on,
        "referenceTime": reference,
        "serverTimeTrusted": trusted,
        "turns": text_turns,
        "titleEvidence": title,
        "dateEvidence": date,
        "timeEvidence": time,
        "categoryEvidence": category_evidence,
        "scheduledAt": scheduled_at,
        "category": category,
        "missing": missing or [],
        "expectations": expectations,
        "unsupported": unsupported,
        "gap": gap,
        "targetOverrides": target_overrides or {},
        "correctionProvenance": correction_provenance,
        "finalStatus": final_status,
    }


SPECS = [
    # Direct create: 6
    spec(1, "direct_create", "direct_absolute_inbound", [turn("2028년 2월 12일 오전 9시에 입고 검수 등록해줘")],
         title=evidence([(1, "입고 검수")]), date=evidence([(1, "2028년 2월 12일")]),
         time=evidence([(1, "오전 9시")]), category_evidence=evidence([(1, "입고")]),
         scheduled_at="2028-02-12T09:00:00+09:00", category="INBOUND",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(2, "direct_create", "direct_relative_inventory", [turn("내일 오후 2시에 재고 실사 일정 잡아줘")],
         title=evidence([(1, "재고 실사")]), date=evidence([(1, "내일")]),
         time=evidence([(1, "오후 2시")]), category_evidence=evidence([(1, "재고")]),
         scheduled_at="2028-02-11T14:00:00+09:00", category="INVENTORY",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(3, "direct_create", "direct_24h_outbound", [turn("2028년 2월 14일 18시에 출고 마감 넣어줘")],
         title=evidence([(1, "출고 마감")]), date=evidence([(1, "2028년 2월 14일")]),
         time=evidence([(1, "18시")]), category_evidence=evidence([(1, "출고")]),
         scheduled_at="2028-02-14T18:00:00+09:00", category="OUTBOUND",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(4, "direct_create", "direct_halfhour_return", [turn("모레 오전 11시 반에 반품 분류 등록해줘")],
         title=evidence([(1, "반품 분류")]), date=evidence([(1, "모레")]),
         time=evidence([(1, "오전 11시 반")]), category_evidence=evidence([(1, "반품")]),
         scheduled_at="2028-02-12T11:30:00+09:00", category="RETURN",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(5, "direct_create", "direct_other", [turn("2028년 2월 15일 오후 4시에 협력사 통화 일정 잡아줘")],
         title=evidence([(1, "협력사 통화")]), date=evidence([(1, "2028년 2월 15일")]),
         time=evidence([(1, "오후 4시")]), category_evidence=None,
         scheduled_at="2028-02-15T16:00:00+09:00", category="OTHER",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(6, "direct_create", "direct_halfhour_inventory", [turn("내일 오전 10시 반에 재고 선반 점검 넣어줘")],
         title=evidence([(1, "재고 선반 점검")]), date=evidence([(1, "내일")]),
         time=evidence([(1, "오전 10시 반")]), category_evidence=evidence([(1, "재고")]),
         scheduled_at="2028-02-11T10:30:00+09:00", category="INVENTORY",
         expectations=[expected("outcome", write=1, outcome="success")]),

    # Missing information and follow-up: 6
    spec(7, "missing_and_followup", "missing_time", [turn("내일 입고 차량 확인 등록해줘"), turn("오전 10시", answer=1)],
         title=evidence([(1, "입고 차량 확인")]), date=evidence([(1, "내일")]),
         time=evidence([(2, "오전 10시")]), category_evidence=evidence([(1, "입고")]),
         scheduled_at="2028-02-11T10:00:00+09:00", category="INBOUND",
         missing=[{"item": "time", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         expectations=[expected("need_more_info", purpose="missing_information"), expected("outcome", write=1, outcome="success")]),
    spec(8, "missing_and_followup", "missing_date", [turn("오후 3시에 반품 회수 등록해줘"), turn("2028년 2월 13일", answer=1)],
         title=evidence([(1, "반품 회수")]), date=evidence([(2, "2028년 2월 13일")]),
         time=evidence([(1, "오후 3시")]), category_evidence=evidence([(1, "반품")]),
         scheduled_at="2028-02-13T15:00:00+09:00", category="RETURN",
         missing=[{"item": "date", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         expectations=[expected("need_more_info", purpose="missing_information"), expected("outcome", write=1, outcome="success")]),
    spec(9, "missing_and_followup", "missing_ampm", [turn("내일 3시에 출고 포장 점검 넣어줘"), turn("오후 3시로 해줘", answer=1)],
         title=evidence([(1, "출고 포장 점검")]), date=evidence([(1, "내일")]),
         time=evidence([(2, "오후 3시")]), category_evidence=evidence([(1, "출고")]),
         scheduled_at="2028-02-11T15:00:00+09:00", category="OUTBOUND",
         missing=[{"item": "ampm", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         target_overrides={1: {"time": evidence([(1, "3시")])}},
         expectations=[expected("need_more_info", purpose="ampm"), expected("outcome", write=1, outcome="success")]),
    spec(10, "missing_and_followup", "missing_title_other", [turn("내일 오후 1시에 일정 하나 잡아줘"), turn("창고 안전 교육이야", answer=1)],
         title=evidence([(2, "창고 안전 교육")]), date=evidence([(1, "내일")]),
         time=evidence([(1, "오후 1시")]), category_evidence=None,
         scheduled_at="2028-02-11T13:00:00+09:00", category="OTHER",
         missing=[{"item": "task_or_title", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         expectations=[expected("need_more_info", purpose="task_or_title_unclear"), expected("outcome", write=1, outcome="success")]),
    spec(11, "missing_and_followup", "untrusted_relative_followup", [turn("내일 오전 9시에 입고 서류 확인 등록해줘"), turn("2028년 2월 11일 오전 9시야", answer=1)],
         trusted=False, title=evidence([(1, "입고 서류 확인")]), date=evidence([(2, "2028년 2월 11일")]),
         time=evidence([(2, "오전 9시")]), category_evidence=evidence([(1, "입고")]),
         scheduled_at="2028-02-11T09:00:00+09:00", category="INBOUND",
         missing=[{"item": "absolute_datetime_due_to_untrusted_time", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         target_overrides={1: {"date": evidence([(1, "내일")]), "time": evidence([(1, "오전 9시")])}},
         expectations=[expected("need_more_info", purpose="absolute_datetime_required"), expected("outcome", write=1, outcome="success")]),
    spec(12, "missing_and_followup", "vague_title_followup", [turn("모레 오후 2시에 그거 등록해줘"), turn("재고 장비 점검이야", answer=1)],
         title=evidence([(2, "재고 장비 점검")]), date=evidence([(1, "모레")]),
         time=evidence([(1, "오후 2시")]), category_evidence=evidence([(2, "재고")]),
         scheduled_at="2028-02-12T14:00:00+09:00", category="INVENTORY",
         missing=[{"item": "task_or_title", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         expectations=[expected("need_more_info", purpose="task_or_title_unclear"), expected("outcome", write=1, outcome="success")]),

    # Corrections: 4 representable + 2 deliberate contract gaps
    spec(13, "correction", "correction_date", [turn("2028년 2월 12일 말고 2028년 2월 13일 오전 9시에 입고 검수 등록해줘")],
         title=evidence([(1, "입고 검수")]),
         date=evidence([(1, "2028년 2월 13일")], superseded=[(1, "2028년 2월 12일")], markers=[(1, "말고")]),
         time=evidence([(1, "오전 9시")]), category_evidence=evidence([(1, "입고")]),
         scheduled_at="2028-02-13T09:00:00+09:00", category="INBOUND",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(14, "correction", "correction_time", [turn("내일 오전 9시 말고 오후 3시에 재고 조사 넣어줘")],
         title=evidence([(1, "재고 조사")]), date=evidence([(1, "내일")]),
         time=evidence([(1, "오후 3시")], superseded=[(1, "오전 9시")], markers=[(1, "말고")]),
         category_evidence=evidence([(1, "재고")]), scheduled_at="2028-02-11T15:00:00+09:00", category="INVENTORY",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(15, "correction", "correction_title_other", [turn("협력사 방문 말고 안전 교육을 2028년 2월 14일 오전 10시에 등록해줘")],
         title=evidence([(1, "안전 교육")], superseded=[(1, "협력사 방문")], markers=[(1, "말고")]),
         date=evidence([(1, "2028년 2월 14일")]), time=evidence([(1, "오전 10시")]), category_evidence=None,
         scheduled_at="2028-02-14T10:00:00+09:00", category="OTHER",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(16, "correction", "correction_title_category", [turn("입고 검수 말고 출고 검수를 2028년 2월 15일 오후 1시에 등록해줘")],
         title=evidence([(1, "출고 검수")], superseded=[(1, "입고 검수")], markers=[(1, "말고")]),
         date=evidence([(1, "2028년 2월 15일")]), time=evidence([(1, "오후 1시")]),
         category_evidence=evidence([(1, "출고")], superseded=[(1, "입고")], markers=[(1, "말고")]),
         scheduled_at="2028-02-15T13:00:00+09:00", category="OUTBOUND",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(17, "correction", "marker_after_new_adopted_only", [turn("내일 출고 확인을 오후 3시에 등록해줘, 오후 2시는 말고")],
         title=evidence([(1, "출고 확인")]), date=evidence([(1, "내일")]),
         time=evidence([(1, "오후 3시")]),
         category_evidence=evidence([(1, "출고")]), scheduled_at="2028-02-11T15:00:00+09:00", category="OUTBOUND",
         correction_provenance="excluded_out_of_order_marker_after_adopted",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(18, "correction", "pending_cross_turn_adopted_only", [turn("모레 오전 10시 반으로 하나 잡아 둘래"), turn("반품 확인이고, 아 오후 3시", answer=1)],
         title=evidence([(2, "반품 확인")]), date=evidence([(1, "모레")]),
         time=evidence([(2, "오후 3시")]),
         category_evidence=evidence([(2, "반품")]), scheduled_at="2028-02-12T15:00:00+09:00", category="RETURN",
         missing=[{"item": "task_or_title", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         target_overrides={1: {"time": evidence([(1, "오전 10시 반")])}},
         correction_provenance="excluded_cross_turn_without_marker",
         expectations=[expected("need_more_info", purpose="task_or_title_unclear"), expected("outcome", write=1, outcome="success")]),

    # Time safety: 6
    spec(19, "time_safety", "untrusted_relative_unresolved", [turn("내일 오전 9시에 재고 수량 확인 등록해줘")],
         trusted=False, title=evidence([(1, "재고 수량 확인")]), date=evidence([(1, "내일")]),
         time=evidence([(1, "오전 9시")]), category_evidence=evidence([(1, "재고")]),
         scheduled_at=None, category="INVENTORY",
         missing=[{"item": "absolute_datetime_due_to_untrusted_time", "detectedAtTurn": 1}],
         final_status="not_executed_pending_info",
         expectations=[expected("need_more_info", purpose="absolute_datetime_required")]),
    spec(20, "time_safety", "untrusted_absolute", [turn("2028년 2월 16일 오후 2시에 입고 문서 확인 등록해줘")],
         trusted=False, title=evidence([(1, "입고 문서 확인")]), date=evidence([(1, "2028년 2월 16일")]),
         time=evidence([(1, "오후 2시")]), category_evidence=evidence([(1, "입고")]),
         scheduled_at="2028-02-16T14:00:00+09:00", category="INBOUND",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(21, "time_safety", "past_time_rejected", [turn("오늘 오전 9시에 출고 차량 확인 등록해줘")],
         reference="2028-02-10T15:00:00+09:00", title=evidence([(1, "출고 차량 확인")]), date=evidence([(1, "오늘")]),
         time=evidence([(1, "오전 9시")]), category_evidence=evidence([(1, "출고")]),
         scheduled_at="2028-02-10T09:00:00+09:00", category="OUTBOUND", final_status="schedule_rejected",
         expectations=[expected("failure", failure="SCHEDULE_REJECTED")]),
    spec(22, "time_safety", "year_boundary_relative", [turn("내일 오전 8시에 반품 정산 등록해줘")],
         reference="2028-12-31T20:00:00+09:00", title=evidence([(1, "반품 정산")]), date=evidence([(1, "내일")]),
         time=evidence([(1, "오전 8시")]), category_evidence=evidence([(1, "반품")]),
         scheduled_at="2029-01-01T08:00:00+09:00", category="RETURN",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(23, "time_safety", "midnight_24h", [turn("2028년 2월 17일 0시 30분에 재고 백업 등록해줘")],
         title=evidence([(1, "재고 백업")]), date=evidence([(1, "2028년 2월 17일")]),
         time=evidence([(1, "0시 30분")]), category_evidence=evidence([(1, "재고")]),
         scheduled_at="2028-02-17T00:30:00+09:00", category="INVENTORY",
         expectations=[expected("outcome", write=1, outcome="success")]),
    spec(24, "time_safety", "ambiguous_ampm_unresolved", [turn("2028년 2월 18일 7시에 입고 준비 등록해줘")],
         title=evidence([(1, "입고 준비")]), date=evidence([(1, "2028년 2월 18일")]),
         time=evidence([(1, "7시")]), category_evidence=evidence([(1, "입고")]),
         scheduled_at=None, category="INBOUND",
         missing=[{"item": "ampm", "detectedAtTurn": 1}], final_status="not_executed_pending_info",
         expectations=[expected("need_more_info", purpose="ampm")]),

    # Mixed unsupported + consent: 6
    spec(25, "mixed_unsupported_consent", "mixed_reminder_grant", [turn("내일 오전 9시에 입고 회의 등록하고 알림도 설정해줘"), turn("알림은 빼고 등록해줘", answer=1)],
         title=evidence([(1, "입고 회의")]), date=evidence([(1, "내일")]), time=evidence([(1, "오전 9시")]),
         category_evidence=evidence([(1, "입고")]), scheduled_at="2028-02-11T09:00:00+09:00", category="INBOUND",
         missing=[{"item": "user_consent", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         unsupported=(1, "알림도 설정해줘", "reminder"),
         expectations=[expected("need_more_info", purpose="consent_without_unsupported"), expected("outcome", write=1, model_call="no_model_call", outcome="success")]),
    spec(26, "mixed_unsupported_consent", "mixed_reminder_decline", [turn("내일 오후 2시에 재고 회의 등록하고 리마인더도 설정해줘"), turn("아니, 등록하지 마", answer=1)],
         title=evidence([(1, "재고 회의")]), date=evidence([(1, "내일")]), time=evidence([(1, "오후 2시")]),
         category_evidence=evidence([(1, "재고")]), scheduled_at="2028-02-11T14:00:00+09:00", category="INVENTORY",
         missing=[{"item": "user_consent", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         unsupported=(1, "리마인더도 설정해줘", "reminder"), final_status="rejected_by_user",
         expectations=[expected("need_more_info", purpose="consent_without_unsupported"), expected("outcome", model_call="no_model_call", outcome="cancelled")]),
    spec(27, "mixed_unsupported_consent", "mixed_text_grant", [turn("2028년 2월 12일 오후 4시에 출고 회의 등록하고 문자도 보내줘"), turn("문자는 빼고 등록해줘", answer=1)],
         title=evidence([(1, "출고 회의")]), date=evidence([(1, "2028년 2월 12일")]), time=evidence([(1, "오후 4시")]),
         category_evidence=evidence([(1, "출고")]), scheduled_at="2028-02-12T16:00:00+09:00", category="OUTBOUND",
         missing=[{"item": "user_consent", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         unsupported=(1, "문자도 보내줘", "other"),
         expectations=[expected("need_more_info", purpose="consent_without_unsupported"), expected("outcome", write=1, model_call="no_model_call", outcome="success")]),
    spec(28, "mixed_unsupported_consent", "mixed_push_decline", [turn("내일 오전 11시에 반품 회의 넣고 푸시 설정해줘"), turn("아니요, 하지 마", answer=1)],
         title=evidence([(1, "반품 회의")]), date=evidence([(1, "내일")]), time=evidence([(1, "오전 11시")]),
         category_evidence=evidence([(1, "반품")]), scheduled_at="2028-02-11T11:00:00+09:00", category="RETURN",
         missing=[{"item": "user_consent", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         unsupported=(1, "푸시 설정해줘", "reminder"), final_status="rejected_by_user",
         expectations=[expected("need_more_info", purpose="consent_without_unsupported"), expected("outcome", model_call="no_model_call", outcome="cancelled")]),
    spec(29, "mixed_unsupported_consent", "mixed_offset_grant", [turn("내일 오전 10시에 재고 실사 등록하고 30분 전에 알려줘"), turn("알림 없이 등록해줘", answer=1)],
         title=evidence([(1, "재고 실사")]), date=evidence([(1, "내일")]), time=evidence([(1, "오전 10시")]),
         category_evidence=evidence([(1, "재고")]), scheduled_at="2028-02-11T10:00:00+09:00", category="INVENTORY",
         missing=[{"item": "user_consent", "detectedAtTurn": 1, "resolvedAtTurn": 2}],
         unsupported=(1, "30분 전에 알려줘", "reminder"),
         expectations=[expected("need_more_info", purpose="consent_without_unsupported"), expected("outcome", write=1, model_call="no_model_call", outcome="success")]),
    spec(30, "mixed_unsupported_consent", "mixed_pending", [turn("2028년 2월 19일 오후 3시에 협력사 미팅 등록하고 알림도 설정해줘")],
         title=evidence([(1, "협력사 미팅")]), date=evidence([(1, "2028년 2월 19일")]), time=evidence([(1, "오후 3시")]),
         category_evidence=None, scheduled_at="2028-02-19T15:00:00+09:00", category="OTHER",
         missing=[{"item": "user_consent", "detectedAtTurn": 1}], unsupported=(1, "알림도 설정해줘", "reminder"),
         final_status="not_executed_pending_info",
         expectations=[expected("need_more_info", purpose="consent_without_unsupported")]),
]


def _policy_ref(policy_id: str, status: str, path: str) -> dict[str, Any]:
    return {"policyId": policy_id, "status": status, "sources": [{"path": path}]}


def _candidate_spans(value: dict[str, Any], through_turn: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, status in (
        ("adoptedSpans", "adopted"),
        ("supersededSpans", "superseded"),
        ("correctionMarkerSpans", "correction_marker"),
    ):
        for span in value.get(key, []):
            if int(span["turn"]) <= through_turn:
                rows.append({"turn": span["turn"], "text": span["text"], "status": status})
    return rows


def make_candidate_target(specification: dict[str, Any], through_turn: int) -> dict[str, Any]:
    """Build an explicit layer-3 target for one model-call boundary.

    Final semantic evidence is not projected backwards when a later answer
    replaces an earlier value.  `targetOverrides` records the value that was
    adopted at that earlier boundary.
    """

    overrides = specification["targetOverrides"].get(through_turn, {})
    slots: dict[str, list[dict[str, Any]]] = {}
    for name in ("title", "date", "time", "category"):
        source = overrides.get(name, specification[f"{name}Evidence"])
        if source is None:
            continue
        spans = _candidate_spans(source, through_turn)
        if spans and any(span["status"] == "adopted" for span in spans):
            slots[name] = spans
    active_missing = [
        item["item"]
        for item in specification["missing"]
        if int(item["detectedAtTurn"]) <= through_turn
        and (item.get("resolvedAtTurn") is None or through_turn < int(item["resolvedAtTurn"]))
    ]
    unsupported: list[dict[str, Any]] = []
    if specification["unsupported"] is not None:
        cue_turn, cue_text, _feature = specification["unsupported"]
        if cue_turn <= through_turn:
            unsupported.append({"turn": cue_turn, "text": cue_text, "status": "adopted"})
    return {
        "operations": [{
            "index": 1,
            "kind": "create",
            "targetEvidence": {},
            "changeEvidence": slots,
            "filterEvidence": {},
            "unsupportedEvidence": unsupported,
            "missingInformation": active_missing,
        }],
        "unsupportedEvidence": [],
        "missingInformation": active_missing,
    }


def _result(specification: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": item["kind"],
        "operationIndex": 1,
        "representation": {"status": "current_api"},
    }
    if item["kind"] == "need_more_info":
        result["questionPurpose"] = item["purpose"]
    elif item["kind"] == "outcome":
        result["outcome"] = item["outcome"]
        result["outcomeOperation"] = "create"
        if item["outcome"] == "success":
            result["representation"] = {
                "status": "pending_verification",
                "verificationIds": ["V4"],
                "note": "실제 backend 생성 결과에 근거한 상세 성공 문구 위치는 V4 검증 대상",
            }
    elif item["kind"] == "failure":
        result["failureCode"] = item["failure"]
    return result


def _consent_resolved(specification: dict[str, Any], through_turn: int) -> bool:
    """True once the user answered the unsupported-feature consent question."""
    return any(
        item["item"] == "user_consent"
        and item.get("resolvedAtTurn") is not None
        and int(item["resolvedAtTurn"]) <= through_turn
        for item in specification["missing"]
    )


def _turn_expectation(specification: dict[str, Any], number: int, item: dict[str, Any]) -> dict[str, Any]:
    write = item["write"]
    if item["kind"] == "outcome" and item["outcome"] == "success":
        facts = [
            {"fact": "schedule_full_title", "source": "backend_result", "operationIndex": 1,
             "value": specification["titleEvidence"]["adoptedSpans"][-1]["text"]},
        ]
    elif item["kind"] == "failure":
        facts = []
    elif (
        item["kind"] == "need_more_info"
        and specification["unsupported"] is not None
        and _consent_resolved(specification, number)
    ):
        # Consent is settled; this question asks for another missing item.
        facts = [{"fact": "missing_item_question", "source": "oracle_constant", "operationIndex": 1}]
    elif specification["unsupported"] is not None:
        facts = [{"fact": "unsupported_feature_notice", "source": "oracle_constant", "operationIndex": 1}]
    elif item["kind"] == "need_more_info":
        facts = [{"fact": "missing_item_question", "source": "oracle_constant", "operationIndex": 1}]
    else:
        facts = []
    row: dict[str, Any] = {
        "turn": number,
        "expectedResult": _result(specification, item),
        "expectedWriteCount": write,
        "modelCallPolicy": item["modelCallPolicy"],
        "requiredResponseFacts": facts,
        "forbiddenClaims": [{"claim": "write_success_not_backed_by_result"}],
    }
    if specification["unsupported"] is not None:
        row["forbiddenClaims"].extend([
            {"claim": "reminder_configured"},
            {"claim": "unsupported_feature_executed"},
        ])
    if write == 1:
        row["expectedWrite"] = {
            "operation": "create",
            "operationIndex": 1,
            "values": {
                "title": specification["titleEvidence"]["adoptedSpans"][-1]["text"],
                "scheduledAt": specification["scheduledAt"],
                "category": specification["category"],
            },
        }
    if specification["gap"] is not None:
        row["contractGapIds"] = [specification["gap"][0]]
    return row


def make_case(specification: dict[str, Any]) -> dict[str, Any]:
    change: dict[str, Any] = {}
    for key in ("title", "date", "time"):
        value = specification[f"{key}Evidence"]
        if value is not None:
            change[key] = value
    if specification["categoryEvidence"] is not None:
        change["category"] = specification["categoryEvidence"]

    date_texts = [span["text"] for span in (specification["dateEvidence"] or {}).get("adoptedSpans", [])]
    absolute = any("년" in text and "월" in text and "일" in text for text in date_texts)
    normalized_change: dict[str, Any] = {
        "title": specification["titleEvidence"]["adoptedSpans"][-1]["text"],
        "category": specification["category"],
        "categoryBasis": "explicit_cue" if specification["categoryEvidence"] else "D1_other_for_clear_task",
    }
    if specification["scheduledAt"] is not None:
        normalized_change.update({
            "scheduledAt": specification["scheduledAt"],
            "dateSource": "explicit_change_date",
        })
    basis = ["TP1"] if absolute else ["TP2", "API-RELATIVE-BASE"]
    rules = [*basis, "API-CATEGORY-ENUM", "API-CREATE-IMMEDIATE"]
    decisions: list[str] = []
    if specification["category"] == "OTHER":
        rules.append("D1")
        decisions.append("D1")
    if specification["unsupported"] is not None:
        rules.append("D5")
        decisions.append("D5")

    unsupported_requests: list[dict[str, Any]] = []
    if specification["unsupported"] is not None:
        cue_turn, cue_text, feature = specification["unsupported"]
        unsupported_row: dict[str, Any] = {
            "feature": feature,
            "cue": evidence([(cue_turn, cue_text)]),
            "mixedWithSupportedOperation": True,
            "relatedOperationIndexes": [1],
            "policy": "D5",
        }
        if feature == "other":
            unsupported_row["featureNote"] = "문자 전송"
        unsupported_requests.append(unsupported_row)

    gap_rows: list[dict[str, Any]] = []
    if specification["gap"] is not None:
        gap_id, _code, description = specification["gap"]
        gap_rows.append({
            "id": gap_id,
            "status": "CONTRACT_GAP",
            "description": description,
            "decisionOwner": "AI·BE",
            "blocksModelScore": True,
            "affectedTurns": list(range(1, len(specification["turns"]) + 1)),
            "nonBlockingRationale": "30건 시범은 공백을 드러내는 개발 probe이므로 기록은 유지하되 400건 확장은 중단한다",
        })

    refs = [
        _policy_ref("API-CATEGORY-ENUM", "BE-FE-AGREED", "docs/api.md"),
        _policy_ref("API-CREATE-IMMEDIATE", "BE-FE-AGREED", "docs/api.md"),
        _policy_ref("TP1", "BE-PROPOSED", "docs/ai_structured_output_contract_proposal.md") if absolute
        else _policy_ref("TP2", "BE-PROPOSED", "docs/ai_structured_output_contract_proposal.md"),
    ]
    if not absolute:
        refs.append(_policy_ref("API-RELATIVE-BASE", "BE-FE-AGREED", "docs/api.md"))
    if "D1" in decisions:
        refs.append(_policy_ref("D1", "BE-PROPOSED", "docs/ai_structured_output_contract_proposal.md"))
    if "D5" in decisions:
        refs.append(_policy_ref("D5", "BE-PROPOSED", "docs/ai_structured_output_contract_proposal.md"))
    if specification["gap"] is not None:
        refs.append(_policy_ref(specification["gap"][0], "CONTRACT_GAP", "annotation-guide-v0.1.md"))

    tag_map = {
        "direct_create": [],
        "missing_and_followup": ["missing_information", "follow_up_answer"],
        "correction": ["correction"],
        "time_safety": [],
        "mixed_unsupported_consent": ["unsupported_mixed", "follow_up_answer"],
    }
    scenario_tags = ["create", *tag_map[specification["bucket"]]]
    if absolute:
        scenario_tags.append("absolute_time_with_year")
    else:
        scenario_tags.append("relative_time")
    if any(
        re.search(r"오늘|내일|모레|글피|이번\s*주\s*[월화수목금토일]요일", str(item["text"]))
        for item in specification["turns"]
        if item["inputType"] == "text"
    ) and "relative_time" not in scenario_tags:
        scenario_tags.append("relative_time")
    if not specification["serverTimeTrusted"]:
        scenario_tags.append("untrusted_server_time")
    if specification["scheduledAt"] is not None:
        if specification["scheduledAt"][:4] != specification["referenceTime"][:4]:
            scenario_tags.append("year_boundary")
    adopted_time_texts = [
        span["text"] for span in (specification["timeEvidence"] or {}).get("adoptedSpans", [])
    ]
    if any(
        re.search(r"(?:^|\s)(?:0|1[3-9]|2[0-3])\s*시", text)
        and not re.search(r"오전|오후|아침|저녁", text)
        for text in adopted_time_texts
    ):
        scenario_tags.append("clock_24h")
    if specification["category"] == "OTHER":
        scenario_tags.append("category_other")
    if any(item["item"] == "ampm" for item in specification["missing"]):
        scenario_tags.append("ampm_missing")
    if any(item["item"] == "task_or_title" for item in specification["missing"]):
        scenario_tags.append("task_unclear")
    scenario_tags = list(dict.fromkeys(scenario_tags))

    return {
        "meta": {
            "schemaVersion": "issue30-semantic-oracle-v0.1",
            "caseId": specification["caseId"],
            "status": "PROPOSED",
            "reviewState": "single-review proposed",
            "datasetRole": specification["datasetRole"],
            "scenarioTags": scenario_tags,
            "authoredOn": specification["authoredOn"],
            "notes": (
                f"state=PROPOSED_UNREVIEWED;bucket={specification['bucket']};"
                f"familyId={specification['family']};templateLineage={specification['lineage']}"
                + (
                    f";correctionProvenance={specification['correctionProvenance']}"
                    if specification["correctionProvenance"] is not None else ""
                )
            ),
        },
        "context": {
            "referenceTime": specification["referenceTime"],
            "timezone": "Asia/Seoul",
            "serverTimeTrusted": specification["serverTimeTrusted"],
            "priorContext": {"kind": "none"},
            "turns": specification["turns"],
            "initialSchedules": [],
        },
        "semantics": {
            "operations": [{
                "index": 1,
                "kind": "create",
                "sourceTurn": 1,
                "evidence": {"target": {}, "change": change, "filter": {}},
                "normalized": {
                    "timeInterpretation": {
                        "interpretation": "absolute" if absolute else "relative",
                        "basis": basis,
                        "dependsOnReferenceTime": not absolute,
                    },
                    "change": normalized_change,
                    "ruleRefs": list(dict.fromkeys(rules)),
                },
                "missingInformation": specification["missing"],
                "appliedDecisions": decisions,
                "expectedFinalStatus": specification["finalStatus"],
            }],
            "unsupportedRequests": unsupported_requests,
            "expectedTurns": [
                _turn_expectation(specification, number, item)
                for number, item in enumerate(specification["expectations"], 1)
            ],
            "appliedDecisions": decisions,
            "policyRefs": refs,
            "policyIndependentInvariants": [
                {"check": "max_one_write_per_turn", "statement": "turn당 Schedule write는 최대 1건이다"},
                {"check": "no_invented_date_or_time", "statement": "사용자 원문에 없는 날짜와 시각을 생성하지 않는다"},
            ],
            "forbiddenValues": [],
            "contractGaps": gap_rows,
            "scoring": {
                "excludedFromModelScore": True,
                "exclusionReason": "CONTRACT_GAP" if gap_rows else "development_probe",
                "primaryCauseOrder": ["TRANSPORT", "FORMAT", "MODEL_SEMANTIC", "NORMALIZER", "MAPPING"],
            },
        },
    }


def write_source_registry() -> None:
    sources = [
        ("existing_400", ROOT.parent / "multiturn_400_v0.1" / "scenarios_all_400_UNREVIEWED.jsonl", "train_validation_reuse_candidate_only"),
        ("issue30_development_12", ISSUE30 / "development-cases-v0.1.jsonl", "forbidden_for_training"),
        ("issue30_locked_56", ISSUE30 / "locked-cases-v0.1.jsonl", "forbidden_for_training"),
    ]
    rows = [
        {
            "sourceId": source_id,
            "path": str(path.relative_to(ROOT.parents[3])),
            "sha256": sha256(path),
            "policy": policy,
        }
        for source_id, path, policy in sources
    ]
    SOURCE_REGISTRY.write_text("\n".join(canonical(row) for row in rows) + "\n", encoding="utf-8")


def main() -> None:
    check_specs(SPECS)
    cases = [make_case(item) for item in SPECS]
    if len(cases) != 30 or len({case["meta"]["caseId"] for case in cases}) != 30:
        raise RuntimeError("pilot must contain 30 unique cases")
    OUTPUT.write_text("\n".join(canonical(case) for case in cases) + "\n", encoding="utf-8")
    targets = [
        {
            "caseId": specification["caseId"],
            "turn": through_turn,
            "target": make_candidate_target(specification, through_turn),
            "status": "PROPOSED_UNREVIEWED",
        }
        for specification in SPECS
        for through_turn in range(1, len(specification["turns"]) + 1)
    ]
    TARGETS.write_text("\n".join(canonical(row) for row in targets) + "\n", encoding="utf-8")
    write_source_registry()
    print(canonical({
        "status": "PROPOSED_UNREVIEWED",
        "caseCount": len(cases),
        "output": str(OUTPUT.relative_to(ROOT)),
        "sha256": sha256(OUTPUT),
        "targetCount": len(targets),
        "targetsSha256": sha256(TARGETS),
    }))


if __name__ == "__main__":
    main()
