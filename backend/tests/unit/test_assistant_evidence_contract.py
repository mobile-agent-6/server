from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime

import pytest

from app.modules.assistant.evidence.contract import (
    EvidenceContractError,
    OperationKind,
    SpanStatus,
    parse_evidence_output,
)
from app.modules.assistant.evidence.gate import (
    CueKind,
    EvidenceGateError,
    analyze_text,
    check_roles,
    check_turn_prefix,
)
from app.modules.assistant.evidence.normalize import SEOUL

REFERENCE = datetime(2026, 10, 29, 10, 0, tzinfo=SEOUL)
TURNS = {1: "내일 오후 3시 반에 입고 검수 잡아 줘"}


def span(text: str, turn: int = 1, status: str = "adopted") -> dict:
    return {"turn": turn, "text": text, "status": status}


def operation(kind: str = "create", **evidence: dict) -> dict:
    return {
        "index": 1,
        "kind": kind,
        "targetEvidence": evidence.get("target", {}),
        "changeEvidence": evidence.get("change", {}),
        "filterEvidence": evidence.get("filter", {}),
        "unsupportedEvidence": [],
        "missingInformation": [],
    }


CREATE = {
    "operations": [
        operation(
            change={
                "title": [span("입고 검수")],
                "date": [span("내일")],
                "time": [span("오후 3시 반")],
                "category": [span("입고")],
            }
        )
    ],
    "unsupportedEvidence": [],
    "missingInformation": [],
}


def parse(value: dict | str, turns: dict[int, str] = TURNS):
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return parse_evidence_output(raw, user_turns=turns)


# ------------------------------------------------------------- contract


def test_valid_create_output() -> None:
    output = parse(CREATE)
    assert len(output.operations) == 1
    item = output.operations[0]
    assert item.kind is OperationKind.CREATE
    assert [s.text for s in item.change["title"]] == ["입고 검수"]
    assert item.change["time"][0].status is SpanStatus.ADOPTED
    check_roles(output, reference=REFERENCE)


def test_span_whitespace_and_width_are_normalized() -> None:
    value = deepcopy(CREATE)
    value["operations"][0]["changeEvidence"]["title"] = [span("입고  검수")]
    assert parse(value).operations[0].change["title"][0].text == "입고  검수"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda v: v["operations"][0]["changeEvidence"].update(title=[span("출고 검수")]), "SPAN_NOT_IN_TURN"),
        (lambda v: v["operations"][0]["changeEvidence"].update(title=[span("입고 검수", turn=2)]), "INVALID_TURN"),
        (lambda v: v["operations"][0].update(kind="move"), "INVALID_OPERATION_KIND"),
        (lambda v: v["operations"][0].update(index=2), "OPERATION_ORDER"),
        (lambda v: v["operations"][0].update(extra=1), "EXTRA_FIELD"),
        (lambda v: v.pop("missingInformation"), "MISSING_FIELD"),
        (lambda v: v["operations"][0]["changeEvidence"].update(place=[span("입고")]), "EXTRA_FIELD"),
        (lambda v: v["operations"][0]["changeEvidence"].update(title=[]), "INVALID_SPAN_LIST"),
        (lambda v: v["operations"][0]["changeEvidence"].update(title=[span("입고 검수", status="superseded")]), "NO_ADOPTED_SPAN"),
        (lambda v: v["operations"][0]["changeEvidence"].update(title=[{**span("입고 검수"), "status": "maybe"}]), "INVALID_SPAN_STATUS"),
        (lambda v: v.update(missingInformation=["weather"]), "INVALID_MISSING_INFORMATION"),
        (lambda v: v["operations"][0].update(note="2026-10-30T15:30:00+09:00"), "FORBIDDEN_DATETIME"),
        (lambda v: v.update(scheduleId="77777777-7777-4777-8777-777777777777"), "FORBIDDEN_SCHEDULE_ID"),
    ],
)
def test_contract_rejections(mutate, code: str) -> None:
    value = deepcopy(CREATE)
    mutate(value)
    with pytest.raises(EvidenceContractError) as error:
        parse(value)
    assert error.value.code == code


def test_duplicate_key_is_rejected() -> None:
    raw = '{"operations": [], "operations": [], "unsupportedEvidence": [], "missingInformation": []}'
    with pytest.raises(EvidenceContractError) as error:
        parse(raw)
    assert error.value.code == "DUPLICATE_KEY"


@pytest.mark.parametrize("raw", ["not json", "[]", "\"text\""])
def test_non_object_output_is_rejected(raw: str) -> None:
    with pytest.raises(EvidenceContractError):
        parse(raw)


def test_correction_needs_marker_and_order() -> None:
    turns = {1: "3시, 아니 4시로 해 줘"}
    good = {
        "operations": [
            operation(
                change={
                    "time": [span("3시", status="superseded"), span("아니", status="correction_marker"), span("4시")],
                }
            )
        ],
        "unsupportedEvidence": [],
        "missingInformation": [],
    }
    parse(good, turns)

    missing_marker = deepcopy(good)
    missing_marker["operations"][0]["changeEvidence"]["time"] = [span("3시", status="superseded"), span("4시")]
    with pytest.raises(EvidenceContractError) as error:
        parse(missing_marker, turns)
    assert error.value.code == "CORRECTION_MARKER_MISSING"

    wrong_order = deepcopy(good)
    wrong_order["operations"][0]["changeEvidence"]["time"] = [
        span("4시", status="superseded"),
        span("아니", status="correction_marker"),
        span("3시"),
    ]
    with pytest.raises(EvidenceContractError) as error:
        parse(wrong_order, turns)
    assert error.value.code == "CORRECTION_ORDER"


def test_correction_across_turns() -> None:
    turns = {1: "반품 사유 분류 잡아 줘", 2: "모레요", 3: "오전 10시로 하고, 날짜는 모레 말고 글피로 바꿔 줘"}
    value = {
        "operations": [
            operation(
                change={
                    "title": [span("반품 사유 분류")],
                    "date": [
                        span("모레", turn=2, status="superseded"),
                        span("말고", turn=3, status="correction_marker"),
                        span("글피", turn=3),
                    ],
                    "time": [span("오전 10시", turn=3)],
                }
            )
        ],
        "unsupportedEvidence": [],
        "missingInformation": [],
    }
    check_roles(parse(value, turns), reference=REFERENCE)


# ----------------------------------------------------------------- roles


def test_role_not_allowed_for_kind() -> None:
    value = deepcopy(CREATE)
    value["operations"][0]["filterEvidence"] = {"date": [span("내일")]}
    with pytest.raises(EvidenceGateError) as error:
        check_roles(parse(value), reference=REFERENCE)
    assert error.value.code == "ROLE_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("slot", "text"),
    [("date", "입고 검수"), ("time", "입고 검수"), ("category", "내일"), ("title", "내일"), ("title", "잡아 줘")],
)
def test_words_in_the_wrong_slot_are_rejected(slot: str, text: str) -> None:
    value = deepcopy(CREATE)
    value["operations"][0]["changeEvidence"][slot] = [span(text)]
    with pytest.raises(EvidenceGateError) as error:
        check_roles(parse(value), reference=REFERENCE)
    assert error.value.code == "EVIDENCE_MEANING_MISMATCH"


def test_same_span_in_two_roles_is_rejected() -> None:
    turns = {1: "입고 일정 입고로 바꿔 줘"}
    value = {
        "operations": [
            operation(
                "update",
                target={"title": [span("입고 일정")]},
                change={"title": [span("입고 일정")]},
            )
        ],
        "unsupportedEvidence": [],
        "missingInformation": [],
    }
    with pytest.raises(EvidenceGateError) as error:
        check_roles(parse(value, turns), reference=REFERENCE)
    assert error.value.code == "EVIDENCE_ROLE_CONFLICT"


def test_unsupported_date_expression_is_a_date_not_a_role_error() -> None:
    turns = {1: "다음 주 금요일 오후 2시에 회의 잡아 줘"}
    value = {
        "operations": [
            operation(change={"title": [span("회의")], "date": [span("다음 주 금요일")], "time": [span("오후 2시")]})
        ],
        "unsupportedEvidence": [],
        "missingInformation": [],
    }
    check_roles(parse(value, turns), reference=REFERENCE)


def test_list_month_and_future_scope_filters() -> None:
    turns = {1: "앞으로 남은 10월 입고 일정 보여 줘"}
    value = {
        "operations": [
            operation(
                "list",
                filter={"futureScope": [span("앞으로 남은")], "date": [span("10월")], "category": [span("입고")]},
            )
        ],
        "unsupportedEvidence": [],
        "missingInformation": [],
    }
    check_roles(parse(value, turns), reference=REFERENCE)


# ----------------------------------------------------------- turn prefix


def test_turn_prefix() -> None:
    check_turn_prefix([1, 2, 3], 3)
    check_turn_prefix([1, 2, 3, 4], 3)
    for numbers, through, code in (
        ([1, 3], 3, "TURNS_INCOMPLETE"),
        ([2, 3], 3, "TURNS_INCOMPLETE"),
        ([1, 1, 2], 2, "TURNS_DUPLICATE"),
        ([2, 1], 2, "TURNS_OUT_OF_ORDER"),
        ([0, 1], 1, "TURNS_INVALID"),
        ([1], 0, "TURNS_INCOMPLETE"),
    ):
        with pytest.raises(EvidenceGateError) as error:
            check_turn_prefix(numbers, through)
        assert error.value.code == code


# --------------------------------------------- unsupported request finder


@pytest.mark.parametrize(
    "text",
    ["내일 오후 4시에 반품 송장 회신 확인 등록하고 알림도 켜 줘", "회의 등록하고 30분 전에 문자 보내 줘", "미리 알려 줘"],
)
def test_unsupported_requests_are_found(text: str) -> None:
    assert analyze_text(text).has_unsupported_request is True


def test_exclusion_is_not_a_request() -> None:
    analysis = analyze_text("그래, 문자는 빼고 등록해")
    assert {finding.kind for finding in analysis.findings} == {CueKind.EXCLUSION}
    assert analysis.has_unsupported_request is False
    assert analysis.supported_operation is True


def test_cue_inside_a_title_is_not_a_request() -> None:
    analysis = analyze_text("내일 오전 10시 알림판 점검 등록해 줘")
    assert analysis.has_unsupported_request is False
    assert [finding.kind for finding in analysis.findings] == [CueKind.TITLE_NOUN]


def test_plain_request_has_no_findings() -> None:
    analysis = analyze_text("내일 오후 3시 회의 잡아 줘")
    assert analysis.findings == ()
    assert analysis.supported_operation is True


def test_create_request_with_connective_is_supported() -> None:
    analysis = analyze_text("내일 오후 3시에 출고 검수 일정을 잡되 알림도 켜 줘")
    assert analysis.has_unsupported_request is True
    assert analysis.supported_operation is True


@pytest.mark.parametrize(
    "text",
    [
        "내일 오후 3시에 출고 검수 일정 잡자 알림도 켜 줘",
        "내일 오후 3시에 출고 검수 일정 잡을래 알림도 켜 줘",
    ],
)
def test_colloquial_create_requests_are_supported(text: str) -> None:
    analysis = analyze_text(text)
    assert analysis.has_unsupported_request is True
    assert analysis.supported_operation is True


@pytest.mark.parametrize(
    "text",
    [
        "잡되게 굴지 말고 알림 켜 줘",
        "잡되지 않게 알림 켜 줘",
    ],
)
def test_japdoeda_adjective_is_not_a_schedule_operation(text: str) -> None:
    analysis = analyze_text(text)
    assert analysis.has_unsupported_request is True
    assert analysis.supported_operation is False
