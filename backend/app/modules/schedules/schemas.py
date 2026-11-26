"""Strict public Schedule request and response schemas."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from pydantic import (
    AliasGenerator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
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
_OPENAPI_DATETIME_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_OPENAPI_TITLE_PATTERN = r"^[^\u0000-\u001F\u007F-\u009F]*$"


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


ApiDateTime = Annotated[
    datetime,
    BeforeValidator(parse_api_datetime),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": _OPENAPI_DATETIME_PATTERN,
            "description": (
                "RFC 3339 datetime with seconds and uppercase Z or a colon-separated "
                "offset; fractional seconds may contain 1 to 6 digits."
            ),
        }
    ),
]
Title = Annotated[
    str,
    BeforeValidator(normalize_title),
    WithJsonSchema(
        {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
            "pattern": _OPENAPI_TITLE_PATTERN,
            "description": (
                "Trimmed before validation; 1 to 100 Unicode code points; "
                "Cc control characters are not allowed."
            ),
        }
    ),
]


def parse_category(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return Category(value)
    except ValueError as exc:
        raise PydanticCustomError("enum", "invalid category") from exc


CategoryValue = Annotated[Category, BeforeValidator(parse_category)]


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


class ScheduleUpdate(RequestModel):
    model_config = ConfigDict(json_schema_extra={"minProperties": 1})

    title: Title = None  # type: ignore[assignment]
    scheduled_at: ApiDateTime = None  # type: ignore[assignment]
    category: CategoryValue = None  # type: ignore[assignment]
    completed: bool = None  # type: ignore[assignment]


class ScheduleResponse(ResponseModel):
    id: UUID
    title: str
    scheduled_at: datetime = Field(json_schema_extra={"format": "date-time"})
    category: Category
    completed: bool
    created_at: datetime = Field(json_schema_extra={"format": "date-time"})
    updated_at: datetime = Field(json_schema_extra={"format": "date-time"})

    @field_serializer("scheduled_at", "created_at", "updated_at")
    def serialize_required_datetime(self, value: datetime) -> str:
        utc_value = value.astimezone(timezone.utc)
        return utc_value.isoformat().replace("+00:00", "Z")


class ScheduleListResponse(ResponseModel):
    items: list[ScheduleResponse]


def schedule_response(
    *,
    id: UUID,
    title: str,
    scheduled_at: datetime,
    category: Category,
    completed: bool,
    created_at: datetime,
    updated_at: datetime,
) -> ScheduleResponse:
    return ScheduleResponse(
        id=id,
        title=title,
        scheduled_at=scheduled_at,
        category=category,
        completed=completed,
        created_at=created_at,
        updated_at=updated_at,
    )
