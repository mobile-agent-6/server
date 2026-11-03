from __future__ import annotations

from datetime import datetime

import pytest

from app.modules.assistant.evidence.contract import (
    EvidenceOperation,
    EvidenceOutput,
    OperationKind,
    Span,
    SpanStatus,
)
from app.modules.assistant.evidence.gate import EvidenceGateError
from app.modules.assistant.evidence.normalize import SEOUL
from app.modules.assistant.evidence.safety import (
    EvidenceQuestion,
    PreWriteSafetyError,
    check_role_span_overlaps,
    evidence_question,
    pre_write_question,
)

REFERENCE = datetime(2026, 11, 3, 10, 0, tzinfo=SEOUL)


def _span(text: str, *, status: SpanStatus = SpanStatus.ADOPTED) -> Span:
    return Span(turn=1, text=text, status=status)


def _operation(
    *,
    kind: OperationKind = OperationKind.CREATE,
    target: dict[str, tuple[Span, ...]] | None = None,
    change: dict[str, tuple[Span, ...]] | None = None,
) -> EvidenceOperation:
    return EvidenceOperation(
        index=1,
        kind=kind,
        target=target or {},
        change=change or {},
        filter={},
        unsupported=(),
        missing_information=(),
    )


def test_missing_date_modifier_is_not_silently_dropped() -> None:
    operation = _operation(
        change={
            "title": (_span("회의"),),
            "date": (_span("12월 5일"),),
            "time": (_span("오후 3시"),),
        }
    )

    assert evidence_question(
        operation,
        turns=["내년 12월 5일 오후 3시에 회의 등록해 줘"],
        reference=REFERENCE,
    ) is EvidenceQuestion.DATE


@pytest.mark.parametrize(
    ("turn", "evidence"),
    [
        ("내후년 12월 5일 오후 3시에 회의 등록해 줘", "12월 5일"),
        ("2주 뒤 금요일 오후 3시에 회의 등록해 줘", "금요일"),
        ("일주일 뒤 금요일 오후 3시에 회의 등록해 줘", "금요일"),
        ("한 달 뒤 5일 오후 3시에 회의 등록해 줘", "5일"),
        ("2개월 후 5일 오후 3시에 회의 등록해 줘", "5일"),
        ("3일 전 오후 3시에 회의 등록해 줘", "3일"),
    ],
)
def test_missing_date_modifiers_require_clarification(
    turn: str, evidence: str
) -> None:
    operation = _operation(
        change={
            "title": (_span("회의"),),
            "date": (_span(evidence),),
            "time": (_span("오후 3시"),),
        }
    )

    assert evidence_question(
        operation,
        turns=[turn],
        reference=REFERENCE,
    ) is EvidenceQuestion.DATE


def test_rejected_clock_phrase_does_not_require_model_evidence() -> None:
    operation = _operation(
        change={
            "title": (_span("출고 서류 점검"),),
            "date": (_span("2028년 4월 13일"),),
            "time": (_span("오전 11시"),),
        }
    )

    assert evidence_question(
        operation,
        turns=["2028년 4월 13일 오전 11시에 출고 서류 점검 넣어줘, 10시는 아니고"],
        reference=REFERENCE,
    ) is None


def test_conflicting_adopted_dates_require_clarification_in_any_order() -> None:
    for values in (("모레", "내일"), ("내일", "모레")):
        operation = _operation(
            change={
                "title": (_span("회의"),),
                "date": tuple(_span(value) for value in values),
                "time": (_span("오후 3시"),),
            }
        )
        assert evidence_question(
            operation,
            turns=["모레 아니 내일 오후 3시에 회의 등록해 줘"],
            reference=REFERENCE,
        ) is EvidenceQuestion.DATE


def test_complementary_time_fragments_are_allowed() -> None:
    operation = _operation(
        change={
            "title": (_span("회의"),),
            "date": (_span("내일"),),
            "time": (_span("오후"), _span("3시")),
        }
    )

    assert evidence_question(
        operation,
        turns=["내일 오후 3시에 회의 등록해 줘"],
        reference=REFERENCE,
    ) is None


def test_semantic_role_overlap_is_rejected_but_title_category_nesting_is_allowed() -> None:
    bad = _operation(
        change={
            "title": (_span("내일 오후 3시 회의"),),
            "date": (_span("내일"),),
            "time": (_span("오후 3시"),),
        }
    )
    with pytest.raises(EvidenceGateError, match="EVIDENCE_ROLE_OVERLAP"):
        check_role_span_overlaps(
            EvidenceOutput((bad,), (), ()),
            user_turns={1: "내일 오후 3시 회의 등록해 줘"},
        )

    allowed = _operation(
        change={
            "title": (_span("입고 검수"),),
            "category": (_span("입고"),),
            "date": (_span("내일"),),
            "time": (_span("오후 3시"),),
        }
    )
    check_role_span_overlaps(
        EvidenceOutput((allowed,), (), ()),
        user_turns={1: "내일 오후 3시에 입고 검수 등록해 줘"},
    )


def test_pre_write_check_rejects_backend_value_without_matching_evidence() -> None:
    operation = _operation(
        change={
            "title": (_span("회의"),),
            "date": (_span("내일"),),
            "time": (_span("오후 3시"),),
        }
    )
    decision = {
        "kind": "create_intent",
        "title": "다른 회의",
        "scheduledAt": "2026-11-04T15:00:00+09:00",
        "category": "OTHER",
        "timeInterpretation": "relative",
    }

    with pytest.raises(PreWriteSafetyError, match="planned title"):
        pre_write_question(
            decision,
            operation,
            turns=["내일 오후 3시에 회의 등록해 줘"],
            reference=REFERENCE,
            server_time_trusted=True,
        )


def test_pre_write_check_rejects_datetime_without_matching_evidence() -> None:
    operation = _operation(
        change={
            "title": (_span("회의"),),
            "date": (_span("내일"),),
            "time": (_span("오후 3시"),),
        }
    )
    decision = {
        "kind": "create_intent",
        "title": "회의",
        "scheduledAt": "2026-11-04T16:00:00+09:00",
        "category": "OTHER",
        "timeInterpretation": "relative",
    }

    with pytest.raises(PreWriteSafetyError, match="planned datetime"):
        pre_write_question(
            decision,
            operation,
            turns=["내일 오후 3시에 회의 등록해 줘"],
            reference=REFERENCE,
            server_time_trusted=True,
        )
