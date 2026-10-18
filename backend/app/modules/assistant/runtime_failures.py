"""Strict public failure snapshots used by runtime-only terminal paths."""

from __future__ import annotations

from app.modules.assistant.enums import (
    AssistantFailureCode,
    AssistantFailureNextAction,
    AssistantResultKind,
)
from app.modules.assistant.schemas import AssistantResultFailure
from app.modules.assistant.serialization import serialize_snapshot

PROCESSING_INTERRUPTED_TEXT = (
    "요청 처리가 중단되었습니다. 잠시 후 다시 시도해 주세요."
)
PROCESSING_TIMEOUT_TEXT = (
    "요청 처리 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
)


def failure_snapshot(
    code: AssistantFailureCode, *, text: str
) -> dict[str, object]:
    result = AssistantResultFailure(
        kind=AssistantResultKind.FAILURE,
        text=text,
        code=code,
        next_action=AssistantFailureNextAction.RETRY_LATER,
    ).model_dump(mode="json", by_alias=True, exclude_none=True)
    serialize_snapshot(result)
    return result


def interrupted_snapshot() -> dict[str, object]:
    return failure_snapshot(
        AssistantFailureCode.PROCESSING_INTERRUPTED,
        text=PROCESSING_INTERRUPTED_TEXT,
    )


def timeout_snapshot() -> dict[str, object]:
    return failure_snapshot(
        AssistantFailureCode.PROCESSING_TIMEOUT,
        text=PROCESSING_TIMEOUT_TEXT,
    )
