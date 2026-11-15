from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.assistant.schemas import AssistantRequestStatusResponse
from app.modules.voice.errors import VoiceAnswerUnclear
from app.modules.voice.router import _input_payload, _parse_candidate_selection
from app.modules.voice.tts import generate_tts_sentence

NOW = datetime(2026, 11, 15, 3, 0, tzinfo=timezone.utc)


def candidates(count: int = 10) -> list[dict[str, str]]:
    return [{"candidateId": f"candidate-{index}"} for index in range(1, count + 1)]


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ("첫 번째", "candidate-1"),
        ("2번", "candidate-2"),
        ("열 번째", "candidate-10"),
        ("11번", None),
        ("열두 번째", None),
        ("첫 번째랑 두 번째", None),
        ("두 번 말했잖아", None),
        ("이번 거", None),
        ("네", None),
    ],
)
def test_candidate_selection_requires_one_in_range_ordinal(
    transcript: str, expected: str | None
) -> None:
    assert _parse_candidate_selection(transcript, candidates()) == expected


@pytest.mark.parametrize(("transcript", "approved"), [("네", True), ("아니요", False)])
def test_confirmation_maps_yes_and_no(transcript: str, approved: bool) -> None:
    question_id = uuid4()
    question = SimpleNamespace(
        id=question_id,
        kind="confirmation",
        candidates_json=None,
        expires_at=NOW + timedelta(minutes=5),
    )

    assert _input_payload(
        transcript=transcript,
        active_question=question,
        question_id=question_id,
        now=NOW,
    ) == {
        "type": "confirmation",
        "questionId": str(question_id),
        "approved": approved,
    }


def test_confirmation_unknown_is_not_silently_rejected() -> None:
    question_id = uuid4()
    question = SimpleNamespace(
        id=question_id,
        kind="confirmation",
        candidates_json=None,
        expires_at=NOW + timedelta(minutes=5),
    )
    with pytest.raises(VoiceAnswerUnclear):
        _input_payload(
            transcript="내일 오후 3시로 바꿔 줘",
            active_question=question,
            question_id=question_id,
            now=NOW,
        )


def test_mismatched_question_is_left_for_admission() -> None:
    question = SimpleNamespace(
        id=uuid4(),
        kind="confirmation",
        candidates_json=None,
        expires_at=NOW + timedelta(minutes=5),
    )
    supplied_id = uuid4()
    assert _input_payload(
        transcript="네",
        active_question=question,
        question_id=supplied_id,
        now=NOW,
    ) == {"type": "text", "text": "네", "questionId": str(supplied_id)}


def status_with_result(result: dict[str, object]) -> AssistantRequestStatusResponse:
    return AssistantRequestStatusResponse.model_validate(
        {
            "requestId": str(uuid4()),
            "conversationId": str(uuid4()),
            "status": "completed",
            "result": result,
        }
    )


def schedule(title: str, hour: int, category: str = "INVENTORY") -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "title": title,
        "scheduledAt": f"2026-11-16T{hour:02d}:00:00+09:00",
        "category": category,
        "completed": False,
        "updatedAt": "2026-11-15T12:00:00+09:00",
    }


def test_tts_list_reads_structured_schedule_fields() -> None:
    response = status_with_result(
        {
            "kind": "outcome",
            "text": "요청한 일정이에요.",
            "outcome": "success",
            "schedulesChanged": False,
            "operation": "list",
            "items": [schedule("입고 대조", 14, "INBOUND")],
        }
    )

    assert generate_tts_sentence(response) == (
        "요청한 일정은 '입고 대조', 11월 16일 오후 2시, 입고 일정입니다."
    )


def test_tts_four_items_reads_first_three() -> None:
    response = status_with_result(
        {
            "kind": "outcome",
            "text": "요청한 일정이에요.",
            "outcome": "success",
            "schedulesChanged": False,
            "operation": "list",
            "items": [schedule(f"일정 {index}", 9 + index) for index in range(4)],
        }
    )

    spoken = generate_tts_sentence(response)
    assert "먼저 세 건을 안내할게요." in spoken
    assert "'일정 0'" in spoken
    assert "'일정 1'" in spoken
    assert "'일정 2'" in spoken
    assert "'일정 3'" not in spoken


def test_tts_candidate_and_confirmation_use_public_dto() -> None:
    candidate_response = status_with_result(
        {
            "kind": "candidate_selection",
            "text": "하나를 선택해 주세요.",
            "questionId": str(uuid4()),
            "expiresAt": "2026-11-15T12:10:00+09:00",
            "candidates": [
                {
                    "candidateId": "a",
                    "title": "재고 조사",
                    "scheduledAt": "2026-11-16T10:00:00+09:00",
                    "category": "INVENTORY",
                },
                {
                    "candidateId": "b",
                    "title": "반품 확인",
                    "scheduledAt": "2026-11-16T11:00:00+09:00",
                    "category": "RETURN",
                },
            ],
        }
    )
    assert "첫 번째는 '재고 조사', 11월 16일 오전 10시, 재고 일정입니다." in generate_tts_sentence(
        candidate_response
    )

    confirmation_response = status_with_result(
        {
            "kind": "confirmation",
            "text": "다음 내용으로 변경할까요?",
            "questionId": str(uuid4()),
            "expiresAt": "2026-11-15T12:10:00+09:00",
            "operation": "update",
            "target": schedule("입고 대조", 14, "INBOUND"),
            "changes": {"title": "입고 최종 대조"},
        }
    )
    assert generate_tts_sentence(confirmation_response) == (
        "'입고 대조' 일정의 제목을 '입고 최종 대조'로 변경할까요?"
    )


def test_tts_write_success_keeps_agreed_short_text() -> None:
    response = status_with_result(
        {
            "kind": "outcome",
            "text": "일정을 변경했어요.",
            "outcome": "success",
            "schedulesChanged": True,
            "operation": "update",
            "affectedScheduleIds": [str(uuid4())],
        }
    )
    assert generate_tts_sentence(response) == "일정을 변경했어요."


def test_tts_delete_confirmation_includes_completion_state() -> None:
    response = status_with_result(
        {
            "kind": "confirmation",
            "text": "이 일정을 삭제할까요?",
            "questionId": str(uuid4()),
            "expiresAt": "2026-11-15T12:10:00+09:00",
            "operation": "delete",
            "target": schedule("입고 대조", 14, "INBOUND"),
        }
    )

    assert generate_tts_sentence(response) == (
        "'입고 대조', 11월 16일 오후 2시, 입고, 미완료 일정을 삭제할까요?"
    )
