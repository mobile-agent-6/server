"""Deterministic spoken summaries for Assistant result DTOs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.assistant.enums import (
    AssistantConfirmationOperation,
    AssistantResultKind,
)
from app.modules.schedules.category import Category

_KST = timezone(timedelta(hours=9))
_ORDINALS = (
    "첫 번째",
    "두 번째",
    "세 번째",
    "네 번째",
    "다섯 번째",
    "여섯 번째",
    "일곱 번째",
    "여덟 번째",
    "아홉 번째",
    "열 번째",
)
_CATEGORY_LABELS = {
    Category.INBOUND: "입고",
    Category.INVENTORY: "재고",
    Category.OUTBOUND: "출고",
    Category.RETURN: "반품",
    Category.OTHER: "기타",
}


def _spoken_time(value: datetime) -> str:
    local = value.astimezone(_KST)
    period = "오전" if local.hour < 12 else "오후"
    hour = local.hour % 12 or 12
    minute = f" {local.minute}분" if local.minute else ""
    return f"{local.month}월 {local.day}일 {period} {hour}시{minute}"


def _category_label(value: Category | str) -> str:
    try:
        category = value if isinstance(value, Category) else Category(value)
    except ValueError:
        return "기타"
    return _CATEGORY_LABELS[category]


def _schedule_phrase(schedule: object, *, include_completion: bool = False) -> str:
    title = getattr(schedule, "title")
    scheduled_at = getattr(schedule, "scheduled_at")
    category = _category_label(getattr(schedule, "category"))
    parts = [f"'{title}'", _spoken_time(scheduled_at), category]
    if include_completion:
        parts.append("완료" if getattr(schedule, "completed") else "미완료")
    return f"{', '.join(parts)} 일정"


def _confirmation_sentence(result: object) -> str:
    target = getattr(result, "target")
    operation = getattr(result, "operation")
    if operation is AssistantConfirmationOperation.DELETE:
        return f"{_schedule_phrase(target, include_completion=True)}을 삭제할까요?"

    changes = getattr(result, "changes")
    descriptions: list[str] = []
    if changes.title is not None:
        descriptions.append(f"제목을 '{changes.title}'로")
    if changes.scheduled_at is not None:
        descriptions.append(f"시간을 {_spoken_time(changes.scheduled_at)}로")
    if changes.category is not None:
        descriptions.append(f"카테고리를 {_category_label(changes.category)}로")
    if changes.completed is not None:
        descriptions.append("완료 상태로" if changes.completed else "미완료 상태로")
    joined = ", ".join(descriptions)
    return f"'{target.title}' 일정의 {joined} 변경할까요?"


def generate_tts_sentence(status_response: object) -> str:
    result = getattr(status_response, "result", None)
    if result is None:
        return "처리를 완료했습니다."

    kind = getattr(result, "kind")
    if kind is AssistantResultKind.CANDIDATE_SELECTION:
        candidates = result.candidates
        parts = [f"일정 후보는 총 {len(candidates)}건입니다."]
        for index, candidate in enumerate(candidates):
            parts.append(f"{_ORDINALS[index]}는 {_schedule_phrase(candidate)}입니다.")
        parts.append("몇 번째 일정을 선택하시겠어요?")
        return " ".join(parts)

    if kind is AssistantResultKind.CONFIRMATION:
        return _confirmation_sentence(result)

    if kind is AssistantResultKind.OUTCOME and result.items is not None:
        items = result.items
        count = len(items)
        if count == 0:
            return "조건에 맞는 일정을 찾지 못했어요."
        if count == 1:
            return f"요청한 일정은 {_schedule_phrase(items[0])}입니다."
        parts = [f"요청한 일정은 총 {count}건입니다."]
        if count > 3:
            parts.append("먼저 세 건을 안내할게요.")
        for index, item in enumerate(items[:3]):
            parts.append(f"{_ORDINALS[index]}는 {_schedule_phrase(item)}입니다.")
        if count > 3:
            parts.append("나머지는 화면에서 확인하거나 조회 조건을 좁혀 주세요.")
        return " ".join(parts)

    return result.text
