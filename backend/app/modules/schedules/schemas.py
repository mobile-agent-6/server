"""Strict public Schedule request and response schemas."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AliasGenerator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_serializer,
)
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from app.modules.schedules.category import Category

_DATETIME_PATTERN = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})"
    r"(?P<fraction>\.\d{1,6})?(?P<offset>Z|[+-]\d{2}:\d{2})$"
)
_DATETIME_WITHOUT_ZONE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?$"
)


def parse_api_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise PydanticCustomError("invalid_format", "datetime must be an RFC 3339 string")
    if _DATETIME_WITHOUT_ZONE.fullmatch(value):
        raise PydanticCustomError("timezone_required", "timezone offset is required")
    if not _DATETIME_PATTERN.fullmatch(value):
        raise PydanticCustomError("invalid_format", "invalid RFC 3339 datetime")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc)
    except OverflowError as exc:
        raise PydanticCustomError("invalid_value", "datetime is outside UTC range") from exc
    except ValueError as exc:
        raise PydanticCustomError("invalid_format", "invalid RFC 3339 datetime") from exc


def normalize_title(value: object) -> str:
    if not isinstance(value, str):
        return value  # type: ignore[return-value]
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise PydanticCustomError("control_character", "control characters are not allowed")
    normalized = value.strip()
    length = len(normalized)
    if length < 1:
        raise PydanticCustomError("string_too_short", "title is too short")
    if length > 100:
        raise PydanticCustomError("string_too_long", "title is too long")
    return normalized


ApiDateTime = Annotated[datetime, BeforeValidator(parse_api_datetime)]
Title = Annotated[str, BeforeValidator(normalize_title)]


def parse_category(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return Category(value)
    except ValueError as exc:
        raise PydanticCustomError("enum", "invalid category") from exc


CategoryValue = Annotated[Category, BeforeValidator(parse_category)]


def parse_reminder(value: object) -> object:
    if value is None:
        return None
    if type(value) is not int:
        raise PydanticCustomError("invalid_type", "reminder must be an integer or null")
    if value != 10:
        raise PydanticCustomError("invalid_value", "reminder must be 10 or null")
    return value


ReminderValue = Annotated[Literal[10] | None, BeforeValidator(parse_reminder)]


class RequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(
            validation_alias=to_camel,
            serialization_alias=to_camel,
        ),
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class ResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class ScheduleCreate(RequestModel):
    title: Title
    scheduled_at: ApiDateTime
    category: CategoryValue
    reminder_minutes_before: ReminderValue


class ScheduleUpdate(RequestModel):
    title: Title = None  # type: ignore[assignment]
    scheduled_at: ApiDateTime = None  # type: ignore[assignment]
    category: CategoryValue = None  # type: ignore[assignment]
    completed: bool = None  # type: ignore[assignment]
    reminder_minutes_before: ReminderValue = None


class ScheduleResponse(ResponseModel):
    id: UUID
    title: str
    scheduled_at: datetime
    category: Category
    completed: bool
    reminder_minutes_before: int | None
    reminder_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("scheduled_at", "reminder_at", "created_at", "updated_at")
    def serialize_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat().replace("+00:00", "Z")


class ScheduleListResponse(ResponseModel):
    items: list[ScheduleResponse]


def reminder_at(scheduled_at: datetime, minutes: int | None) -> datetime | None:
    if minutes is None:
        return None
    return scheduled_at - timedelta(minutes=minutes)


def schedule_response(
    *,
    id: UUID,
    title: str,
    scheduled_at: datetime,
    category: Category,
    completed: bool,
    reminder_minutes_before: int | None,
    created_at: datetime,
    updated_at: datetime,
) -> ScheduleResponse:
    return ScheduleResponse(
        id=id,
        title=title,
        scheduled_at=scheduled_at,
        category=category,
        completed=completed,
        reminder_minutes_before=reminder_minutes_before,
        reminder_at=reminder_at(scheduled_at, reminder_minutes_before),
        created_at=created_at,
        updated_at=updated_at,
    )
