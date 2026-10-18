from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.modules.assistant.decisions import (
    DecisionContext,
    DecisionEnvelope,
    DecisionProviderTimeout,
    UntrustedTimeSource,
)
from app.modules.assistant.serialization import CONTROL_STATE_MAX_BYTES
from tests.fakes.assistant import ScriptedDecisionProvider


BASE = {
    "text": "처리 안내",
    "timeInterpretation": "absolute",
}
SCHEDULE_ID = "77777777-7777-4777-8777-777777777777"


@pytest.mark.parametrize(
    "decision",
    [
        {"kind": "ask", "text": "언제 실행할까요?"},
        {
            "kind": "select_candidate",
            "text": "대상을 골라 주세요.",
            "candidates": [
                {
                    "scheduleId": SCHEDULE_ID,
                    "operation": "update",
                    "changes": {"completed": True},
                    "timeInterpretation": "absolute",
                },
                {
                    "scheduleId": "88888888-8888-4888-8888-888888888888",
                    "operation": "delete",
                    "timeInterpretation": "absolute",
                },
            ],
        },
        {
            "kind": "confirm",
            "scheduleId": SCHEDULE_ID,
            "operation": "update",
            "changes": {"title": "새 제목"},
            **BASE,
        },
        {
            "kind": "create_intent",
            "title": "새 일정",
            "scheduledAt": "2026-10-19T01:00:00Z",
            "category": "OTHER",
            **BASE,
        },
        {
            "kind": "list_intent",
            "from": "2026-10-18T00:00:00Z",
            "to": "2026-10-19T00:00:00Z",
            **BASE,
        },
        {
            "kind": "fail",
            "text": "대상을 구분할 수 없습니다.",
            "code": "TARGET_AMBIGUOUS",
            "nextAction": "revise_input",
        },
    ],
)
def test_six_decision_variants_round_trip_as_strict_json(
    decision: dict[str, object],
) -> None:
    envelope = DecisionEnvelope.model_validate({"decision": decision})

    dumped = envelope.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert DecisionEnvelope.model_validate(dumped).decision.kind == decision["kind"]


@pytest.mark.parametrize(
    "decision",
    [
        {"kind": "ask", "text": "질문", "extra": True},
        {
            "kind": "confirm",
            "text": "삭제할까요?",
            "scheduleId": SCHEDULE_ID,
            "operation": "delete",
            "changes": {"completed": True},
            "timeInterpretation": "absolute",
        },
        {
            "kind": "confirm",
            "text": "수정할까요?",
            "scheduleId": SCHEDULE_ID,
            "operation": "update",
            "timeInterpretation": "absolute",
        },
        {
            "kind": "create_intent",
            "title": "새 일정",
            "scheduledAt": "2026-10-19T01:00:00Z",
            "category": "OTHER",
            "reminderMinutesBefore": 10,
            **BASE,
        },
        {
            "kind": "fail",
            "text": "금지된 실패",
            "code": "MODEL_TIMEOUT",
            "nextAction": "retry_later",
        },
        {
            "kind": "list_intent",
            "from": "2026-10-18T00:00:00Z",
            "to": "2026-10-19T00:00:00Z",
            "limit": 10,
            **BASE,
        },
        {
            "kind": "confirm",
            "scheduleId": SCHEDULE_ID,
            "operation": "delete",
            "expectedUpdatedAt": "2026-10-18T00:00:00Z",
            **BASE,
        },
    ],
)
def test_decision_rejects_extra_notification_and_invalid_combinations(
    decision: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DecisionEnvelope.model_validate({"decision": decision})


def test_control_state_is_versioned_strict_and_bounded() -> None:
    valid = DecisionEnvelope.model_validate(
        {
            "decision": {"kind": "ask", "text": "질문"},
            "controlStateVersion": 1,
            "controlState": {"step": 1},
        }
    )
    assert valid.control_state == {"step": 1}

    with pytest.raises(ValidationError):
        DecisionEnvelope.model_validate(
            {
                "decision": {"kind": "ask", "text": "질문"},
                "controlState": {"step": 1},
            }
        )
    with pytest.raises(ValidationError):
        DecisionEnvelope.model_validate(
            {
                "decision": {"kind": "ask", "text": "질문"},
                "controlStateVersion": 1,
                "controlState": {"large": "x" * CONTROL_STATE_MAX_BYTES},
            }
        )


@pytest.mark.parametrize("candidate_count", [1, 11])
def test_candidate_decision_enforces_public_cardinality(candidate_count: int) -> None:
    candidates = [
        {
            "scheduleId": f"70000000-0000-4000-8000-{index:012x}",
            "operation": "delete",
            "timeInterpretation": "absolute",
        }
        for index in range(candidate_count)
    ]
    with pytest.raises(ValidationError):
        DecisionEnvelope.model_validate(
            {
                "decision": {
                    "kind": "select_candidate",
                    "text": "선택하세요.",
                    "candidates": candidates,
                }
            }
        )


def test_candidate_decision_rejects_duplicate_schedule_ids() -> None:
    candidate = {
        "scheduleId": SCHEDULE_ID,
        "operation": "delete",
        "timeInterpretation": "absolute",
    }
    with pytest.raises(ValidationError):
        DecisionEnvelope.model_validate(
            {
                "decision": {
                    "kind": "select_candidate",
                    "text": "선택하세요.",
                    "candidates": [candidate, candidate],
                }
            }
        )


@pytest.mark.parametrize(
    ("from_value", "to_value"),
    [
        ("2026-10-19T00:00:00Z", "2026-10-18T00:00:00Z"),
        ("2026-01-01T00:00:00Z", "2026-05-01T00:00:00Z"),
    ],
)
def test_list_decision_rejects_invalid_ranges(
    from_value: str, to_value: str
) -> None:
    with pytest.raises(ValidationError):
        DecisionEnvelope.model_validate(
            {
                "decision": {
                    "kind": "list_intent",
                    "text": "조회",
                    "from": from_value,
                    "to": to_value,
                    "timeInterpretation": "absolute",
                }
            }
        )


def test_time_interpretation_and_context_pair_are_strict() -> None:
    with pytest.raises(ValidationError):
        DecisionEnvelope.model_validate(
            {
                "decision": {
                    "kind": "create_intent",
                    "text": "등록",
                    "title": "일정",
                    "scheduledAt": "2026-10-19T00:00:00Z",
                    "category": "OTHER",
                    "timeInterpretation": "guessed",
                }
            }
        )
    with pytest.raises(ValidationError):
        DecisionContext(
            request_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            conversation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            input={"type": "text", "text": "재고 확인"},
            task_reference_accepted_at="2026-10-18T00:00:00Z",
            server_time_trusted=False,
            is_question_follow_up=False,
            control_state_version=1,
        )
    assert UntrustedTimeSource().is_trusted() is False


def test_scripted_fake_records_order_and_injects_failures() -> None:
    context = DecisionContext(
        request_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        conversation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        input={"type": "text", "text": "재고 확인"},
        task_reference_accepted_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
        server_time_trusted=True,
        is_question_follow_up=False,
    )
    first = DecisionEnvelope.model_validate(
        {"decision": {"kind": "ask", "text": "질문"}}
    )
    fake = ScriptedDecisionProvider([first, DecisionProviderTimeout("timeout")])

    assert fake.decide(context) is first
    with pytest.raises(DecisionProviderTimeout):
        fake.decide(context)
    assert fake.call_count == 2
    fake.assert_exhausted()
    with pytest.raises(AssertionError, match="script is empty"):
        fake.decide(context)


def test_context_is_json_safe_and_has_no_database_or_write_capability() -> None:
    context = DecisionContext(
        request_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        conversation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        input={"type": "text", "text": "재고 확인"},
        task_reference_accepted_at="2026-10-18T00:00:00Z",
        server_time_trusted=False,
        is_question_follow_up=False,
    )

    assert context.timezone == "Asia/Seoul"
    for forbidden in ("session", "repository", "schedule_writer", "orm"):
        assert not hasattr(context, forbidden)
