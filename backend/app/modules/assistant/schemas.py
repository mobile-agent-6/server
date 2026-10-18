"""Strict public Assistant request and response schemas."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal
from uuid import RFC_4122, UUID

from pydantic import (
    AliasGenerator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
    field_serializer,
    model_validator,
)
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from app.modules.assistant.enums import (
    AssistantCancelTargetState,
    AssistantCancelTargetType,
    AssistantConfirmationOperation,
    AssistantFailureCode,
    AssistantFailureNextAction,
    AssistantInputType,
    AssistantOutcome,
    AssistantOutcomeOperation,
    AssistantRequestStatus,
    AssistantResultKind,
    AssistantUncertainCode,
    AssistantUncertainNextAction,
)
from app.modules.schedules.schemas import ApiDateTime, CategoryValue, Title, parse_api_datetime

_UUID_PATTERN = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)

ASSISTANT_WHITE_SPACE = frozenset(
    chr(code_point)
    for code_point in (
        *range(0x0009, 0x000E),
        0x0020,
        0x0085,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
    )
)
_ALLOWED_CONTROL_CHARACTERS = frozenset({"\t", "\n", "\r"})


def parse_assistant_uuid(value: object) -> object:
    """Accept only canonical RFC variant UUID v4 strings and normalize case."""

    if not isinstance(value, str):
        return value
    if _UUID_PATTERN.fullmatch(value) is None:
        raise PydanticCustomError("invalid_format", "UUID must use canonical hyphen form")
    parsed = UUID(value)
    if parsed.version != 4 or parsed.variant != RFC_4122:
        raise PydanticCustomError("invalid_value", "UUID must be RFC variant version 4")
    return parsed


AssistantUuid = Annotated[UUID, BeforeValidator(parse_assistant_uuid)]


def _coalesce_surrogate_pairs(value: str) -> str:
    characters: list[str] = []
    index = 0
    while index < len(value):
        code_point = ord(value[index])
        if 0xD800 <= code_point <= 0xDBFF:
            if index + 1 >= len(value):
                raise PydanticCustomError("invalid_value", "text contains an unmatched surrogate")
            low = ord(value[index + 1])
            if not 0xDC00 <= low <= 0xDFFF:
                raise PydanticCustomError("invalid_value", "text contains an unmatched surrogate")
            characters.append(chr(0x10000 + ((code_point - 0xD800) << 10) + low - 0xDC00))
            index += 2
            continue
        if 0xDC00 <= code_point <= 0xDFFF:
            raise PydanticCustomError("invalid_value", "text contains an unmatched surrogate")
        characters.append(value[index])
        index += 1
    return "".join(characters)


def normalize_assistant_text(value: object) -> object:
    """Validate and trim Assistant text in the public contract's required order."""

    if not isinstance(value, str):
        return value
    decoded = _coalesce_surrogate_pairs(value)
    if any(
        unicodedata.category(character) == "Cc"
        and character not in _ALLOWED_CONTROL_CHARACTERS
        for character in decoded
    ):
        raise PydanticCustomError("invalid_value", "text contains a forbidden control character")
    length = len(decoded)
    if length == 0:
        raise PydanticCustomError("string_too_short", "text is too short")
    if length > 500:
        raise PydanticCustomError("string_too_long", "text is too long")

    start = 0
    end = length
    while start < end and decoded[start] in ASSISTANT_WHITE_SPACE:
        start += 1
    while end > start and decoded[end - 1] in ASSISTANT_WHITE_SPACE:
        end -= 1
    trimmed = decoded[start:end]
    if not trimmed:
        raise PydanticCustomError("string_too_short", "text is too short")
    return trimmed


AssistantText = Annotated[str, BeforeValidator(normalize_assistant_text)]


def _parse_assistant_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise PydanticCustomError("timezone_required", "timezone offset is required")
        return value.astimezone(timezone.utc)
    return parse_api_datetime(value)


def _serialize_assistant_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


AssistantDateTime = Annotated[
    datetime,
    BeforeValidator(_parse_assistant_datetime),
    PlainSerializer(_serialize_assistant_datetime, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]


def _enum_parser(enum_type: type[StrEnum]):
    def parse(value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return enum_type(value)
        except ValueError as exc:
            raise PydanticCustomError("enum", "invalid enum value") from exc

    return parse


AssistantRequestStatusValue = Annotated[
    AssistantRequestStatus, BeforeValidator(_enum_parser(AssistantRequestStatus))
]
AssistantConfirmationOperationValue = Annotated[
    AssistantConfirmationOperation,
    BeforeValidator(_enum_parser(AssistantConfirmationOperation)),
]
AssistantOutcomeValue = Annotated[
    AssistantOutcome, BeforeValidator(_enum_parser(AssistantOutcome))
]
AssistantOutcomeOperationValue = Annotated[
    AssistantOutcomeOperation,
    BeforeValidator(_enum_parser(AssistantOutcomeOperation)),
]
AssistantFailureCodeValue = Annotated[
    AssistantFailureCode, BeforeValidator(_enum_parser(AssistantFailureCode))
]
AssistantFailureNextActionValue = Annotated[
    AssistantFailureNextAction,
    BeforeValidator(_enum_parser(AssistantFailureNextAction)),
]
AssistantCancelTargetTypeValue = Annotated[
    AssistantCancelTargetType,
    BeforeValidator(_enum_parser(AssistantCancelTargetType)),
]
AssistantCancelTargetStateValue = Annotated[
    AssistantCancelTargetState,
    BeforeValidator(_enum_parser(AssistantCancelTargetState)),
]


class _AssistantRequestModel(BaseModel):
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


class _AssistantResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class AssistantInputText(_AssistantRequestModel):
    type: Literal[AssistantInputType.TEXT]
    text: AssistantText
    question_id: AssistantUuid = None  # type: ignore[assignment]


class AssistantInputSelection(_AssistantRequestModel):
    type: Literal[AssistantInputType.SELECTION]
    question_id: AssistantUuid
    candidate_id: str


class AssistantInputConfirmation(_AssistantRequestModel):
    type: Literal[AssistantInputType.CONFIRMATION]
    question_id: AssistantUuid
    approved: bool


AssistantInput = Annotated[
    AssistantInputText | AssistantInputSelection | AssistantInputConfirmation,
    Field(discriminator="type"),
]


class AssistantRequestCreate(_AssistantRequestModel):
    request_id: AssistantUuid
    conversation_id: AssistantUuid = None  # type: ignore[assignment]
    input: AssistantInput
    acknowledged_uncertain_request_id: AssistantUuid = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def validate_input_context(self) -> "AssistantRequestCreate":
        if isinstance(self.input, AssistantInputText):
            if self.input.question_id is not None and self.conversation_id is None:
                raise ValueError("questionId requires conversationId")
            if (
                self.acknowledged_uncertain_request_id is not None
                and self.input.question_id is not None
            ):
                raise ValueError("acknowledgement is allowed only for a new text task")
        else:
            if self.conversation_id is None:
                raise ValueError("selection and confirmation require conversationId")
            if self.acknowledged_uncertain_request_id is not None:
                raise ValueError("acknowledgement is allowed only for text input")
        return self


class AssistantCandidate(_AssistantResponseModel):
    candidate_id: str
    title: str
    scheduled_at: AssistantDateTime
    category: CategoryValue


class AssistantScheduleSummary(_AssistantResponseModel):
    id: AssistantUuid
    title: str
    scheduled_at: AssistantDateTime
    category: CategoryValue
    completed: bool
    updated_at: AssistantDateTime


class AssistantConfirmationChanges(_AssistantResponseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
        json_schema_extra={"minProperties": 1},
    )

    title: Title = None  # type: ignore[assignment]
    scheduled_at: ApiDateTime = None  # type: ignore[assignment]
    category: CategoryValue = None  # type: ignore[assignment]
    completed: bool = None  # type: ignore[assignment]

    @field_serializer("scheduled_at", when_used="unless-none")
    def serialize_scheduled_at(self, value: datetime) -> str:
        return _serialize_assistant_datetime(value)

    @model_validator(mode="after")
    def require_change(self) -> "AssistantConfirmationChanges":
        if not self.model_fields_set:
            raise ValueError("at least one Schedule update field is required")
        return self


class AssistantResultNeedMoreInfo(_AssistantResponseModel):
    kind: Literal[AssistantResultKind.NEED_MORE_INFO]
    text: str
    question_id: AssistantUuid


class AssistantResultCandidateSelection(_AssistantResponseModel):
    kind: Literal[AssistantResultKind.CANDIDATE_SELECTION]
    text: str
    question_id: AssistantUuid
    expires_at: AssistantDateTime
    candidates: list[AssistantCandidate] = Field(min_length=2, max_length=10)


_CONFIRMATION_SCHEMA_CONDITIONS = {
    "allOf": [
        {
            "if": {"properties": {"operation": {"const": "update"}}},
            "then": {"required": ["changes"]},
        },
        {
            "if": {"properties": {"operation": {"const": "delete"}}},
            "then": {"not": {"required": ["changes"]}},
        },
    ]
}


class AssistantResultConfirmation(_AssistantResponseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
        json_schema_extra=_CONFIRMATION_SCHEMA_CONDITIONS,
    )

    kind: Literal[AssistantResultKind.CONFIRMATION]
    text: str
    question_id: AssistantUuid
    expires_at: AssistantDateTime
    operation: AssistantConfirmationOperationValue
    target: AssistantScheduleSummary
    changes: AssistantConfirmationChanges = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def validate_changes(self) -> "AssistantResultConfirmation":
        if self.operation is AssistantConfirmationOperation.UPDATE and self.changes is None:
            raise ValueError("update confirmation requires changes")
        if self.operation is AssistantConfirmationOperation.DELETE and self.changes is not None:
            raise ValueError("delete confirmation must omit changes")
        return self


_OUTCOME_SCHEMA_CONDITIONS = {
    "oneOf": [
        {
            "properties": {
                "outcome": {"const": "success"},
                "operation": {"enum": ["create", "update", "delete"]},
                "schedulesChanged": {"const": True},
                "affectedScheduleIds": {"minItems": 1, "maxItems": 1},
            },
            "required": ["operation", "affectedScheduleIds"],
            "not": {"required": ["items"]},
        },
        {
            "properties": {
                "outcome": {"const": "success"},
                "operation": {"const": "list"},
                "schedulesChanged": {"const": False},
            },
            "required": ["operation", "items"],
            "not": {"required": ["affectedScheduleIds"]},
        },
        {
            "properties": {
                "outcome": {"enum": ["cancelled", "no_match"]},
                "schedulesChanged": {"const": False},
            },
            "not": {
                "anyOf": [
                    {"required": ["affectedScheduleIds"]},
                    {"required": ["items"]},
                ]
            },
        },
    ]
}


class AssistantResultOutcome(_AssistantResponseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
        json_schema_extra=_OUTCOME_SCHEMA_CONDITIONS,
    )

    kind: Literal[AssistantResultKind.OUTCOME]
    text: str
    outcome: AssistantOutcomeValue
    schedules_changed: bool
    operation: AssistantOutcomeOperationValue = None  # type: ignore[assignment]
    affected_schedule_ids: list[AssistantUuid] = None  # type: ignore[assignment]
    items: list[AssistantScheduleSummary] = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def validate_outcome_fields(self) -> "AssistantResultOutcome":
        if self.outcome is AssistantOutcome.SUCCESS:
            if self.operation is None:
                raise ValueError("successful outcome requires an operation")
            if self.operation is AssistantOutcomeOperation.LIST:
                if self.schedules_changed or self.items is None:
                    raise ValueError("successful list requires unchanged schedules and items")
                if self.affected_schedule_ids is not None:
                    raise ValueError("successful list must omit affectedScheduleIds")
            else:
                if not self.schedules_changed:
                    raise ValueError("successful write must change schedules")
                if self.affected_schedule_ids is None or len(self.affected_schedule_ids) != 1:
                    raise ValueError("successful write requires exactly one affected schedule")
                if self.items is not None:
                    raise ValueError("successful write must omit items")
        else:
            if self.schedules_changed:
                raise ValueError("cancelled and no-match outcomes cannot change schedules")
            if self.affected_schedule_ids is not None or self.items is not None:
                raise ValueError("cancelled and no-match outcomes must omit result data")
        return self


_FAILURE_SCHEMA_CONDITIONS = {
    "oneOf": [
        {
            "properties": {
                "code": {
                    "enum": [
                        "MODEL_OUTPUT_INVALID",
                        "SCHEDULE_REJECTED",
                        "TARGET_AMBIGUOUS",
                    ]
                },
                "nextAction": {"const": "revise_input"},
            }
        },
        {
            "properties": {
                "code": {
                    "enum": [
                        "MODEL_TIMEOUT",
                        "SCHEDULE_UNAVAILABLE",
                        "PROCESSING_INTERRUPTED",
                        "PROCESSING_TIMEOUT",
                    ]
                },
                "nextAction": {"const": "retry_later"},
            }
        },
        {
            "properties": {
                "code": {"const": "INTERNAL_ERROR"},
                "nextAction": {"const": "none"},
            }
        },
    ]
}


class AssistantResultFailure(_AssistantResponseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
        json_schema_extra=_FAILURE_SCHEMA_CONDITIONS,
    )

    kind: Literal[AssistantResultKind.FAILURE]
    text: str
    code: AssistantFailureCodeValue
    next_action: AssistantFailureNextActionValue

    @model_validator(mode="after")
    def validate_next_action(self) -> "AssistantResultFailure":
        expected = {
            AssistantFailureCode.MODEL_OUTPUT_INVALID: AssistantFailureNextAction.REVISE_INPUT,
            AssistantFailureCode.SCHEDULE_REJECTED: AssistantFailureNextAction.REVISE_INPUT,
            AssistantFailureCode.TARGET_AMBIGUOUS: AssistantFailureNextAction.REVISE_INPUT,
            AssistantFailureCode.MODEL_TIMEOUT: AssistantFailureNextAction.RETRY_LATER,
            AssistantFailureCode.SCHEDULE_UNAVAILABLE: AssistantFailureNextAction.RETRY_LATER,
            AssistantFailureCode.PROCESSING_INTERRUPTED: AssistantFailureNextAction.RETRY_LATER,
            AssistantFailureCode.PROCESSING_TIMEOUT: AssistantFailureNextAction.RETRY_LATER,
            AssistantFailureCode.INTERNAL_ERROR: AssistantFailureNextAction.NONE,
        }[self.code]
        if self.next_action is not expected:
            raise ValueError("nextAction does not match the failure code")
        return self


class AssistantResultUncertain(_AssistantResponseModel):
    kind: Literal[AssistantResultKind.UNCERTAIN]
    text: str
    code: Literal[AssistantUncertainCode.EXECUTION_RESULT_UNKNOWN]
    next_action: Literal[AssistantUncertainNextAction.CHECK_STATUS]


AssistantResult = Annotated[
    AssistantResultNeedMoreInfo
    | AssistantResultCandidateSelection
    | AssistantResultConfirmation
    | AssistantResultOutcome
    | AssistantResultFailure
    | AssistantResultUncertain,
    Field(discriminator="kind"),
]


class AssistantRequestAcceptedResponse(_AssistantResponseModel):
    request_id: AssistantUuid
    conversation_id: AssistantUuid
    status: Literal[AssistantRequestStatus.ACCEPTED]


class AssistantRequestStatusResponse(_AssistantResponseModel):
    request_id: AssistantUuid
    conversation_id: AssistantUuid
    status: AssistantRequestStatusValue
    result: AssistantResult = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def validate_result_state(self) -> "AssistantRequestStatusResponse":
        if self.status is AssistantRequestStatus.COMPLETED and self.result is None:
            raise ValueError("completed status requires result")
        if self.status is not AssistantRequestStatus.COMPLETED and self.result is not None:
            raise ValueError("incomplete status must omit result")
        return self


class AssistantCancelTarget(_AssistantRequestModel):
    type: AssistantCancelTargetTypeValue
    id: AssistantUuid


class AssistantCancelRequest(_AssistantRequestModel):
    target: AssistantCancelTarget


class AssistantCancelResponse(_AssistantResponseModel):
    target: AssistantCancelTarget
    target_state: AssistantCancelTargetStateValue
    text: str
