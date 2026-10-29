"""Internal, provider-independent Assistant decision contracts.

These DTOs are a backend boundary only.  They deliberately contain no model
transport, database session, ORM object, or Schedule write capability.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel
from pydantic_core import PydanticCustomError

from app.modules.assistant.enums import (
    AssistantConfirmationOperation,
    AssistantFailureCode,
    AssistantFailureNextAction,
)
from app.modules.assistant.schemas import (
    AssistantConfirmationChanges,
    AssistantConfirmationOperationValue,
    AssistantDateTime,
    AssistantFailureCodeValue,
    AssistantFailureNextActionValue,
    AssistantOutcomeOperationValue,
    AssistantText,
    AssistantUuid,
)
from app.modules.assistant.serialization import serialize_snapshot
from app.modules.schedules.schemas import CategoryValue, Title


class TimeInterpretation(StrEnum):
    """Whether an interpreted time depended on the task reference instant."""

    ABSOLUTE = "absolute"
    RELATIVE = "relative"


def _parse_time_interpretation(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return TimeInterpretation(value)
    except ValueError as exc:
        raise PydanticCustomError("enum", "invalid time interpretation") from exc


TimeInterpretationValue = Annotated[
    TimeInterpretation, BeforeValidator(_parse_time_interpretation)
]


class _InternalModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class DecisionAsk(_InternalModel):
    kind: Literal["ask"]
    text: AssistantText


class CandidateIntent(_InternalModel):
    schedule_id: AssistantUuid
    operation: AssistantConfirmationOperationValue
    changes: AssistantConfirmationChanges | None = None
    time_interpretation: TimeInterpretationValue

    @model_validator(mode="after")
    def validate_operation(self) -> "CandidateIntent":
        if self.operation is AssistantConfirmationOperation.UPDATE:
            if self.changes is None:
                raise ValueError("update candidate requires changes")
        elif self.changes is not None:
            raise ValueError("delete candidate must omit changes")
        return self


class DecisionSelectCandidate(_InternalModel):
    kind: Literal["select_candidate"]
    text: AssistantText
    candidates: list[CandidateIntent] = Field(min_length=2, max_length=10)

    @model_validator(mode="after")
    def reject_duplicate_targets(self) -> "DecisionSelectCandidate":
        identifiers = [candidate.schedule_id for candidate in self.candidates]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("candidate Schedule IDs must be unique")
        return self


class DecisionConfirm(_InternalModel):
    kind: Literal["confirm"]
    text: AssistantText
    schedule_id: AssistantUuid
    operation: AssistantConfirmationOperationValue
    changes: AssistantConfirmationChanges | None = None
    time_interpretation: TimeInterpretationValue

    @model_validator(mode="after")
    def validate_operation(self) -> "DecisionConfirm":
        if self.operation is AssistantConfirmationOperation.UPDATE:
            if self.changes is None:
                raise ValueError("update confirmation requires changes")
        elif self.changes is not None:
            raise ValueError("delete confirmation must omit changes")
        return self


class DecisionCreateIntent(_InternalModel):
    kind: Literal["create_intent"]
    text: AssistantText
    title: Title
    scheduled_at: AssistantDateTime
    category: CategoryValue
    time_interpretation: TimeInterpretationValue


class DecisionListIntent(_InternalModel):
    kind: Literal["list_intent"]
    text: AssistantText
    from_at: AssistantDateTime = Field(alias="from")
    to_at: AssistantDateTime = Field(alias="to")
    category: CategoryValue | None = None
    completed: bool | None = None
    # Keep only schedules whose title contains this text, ignoring spaces and case.
    title: Title | None = None
    time_interpretation: TimeInterpretationValue

    @model_validator(mode="after")
    def validate_range(self) -> "DecisionListIntent":
        from datetime import timedelta

        if self.to_at <= self.from_at:
            raise ValueError("to must be after from")
        if self.to_at - self.from_at > timedelta(days=92):
            raise ValueError("date range cannot exceed 92 days")
        return self


class DecisionFail(_InternalModel):
    kind: Literal["fail"]
    text: AssistantText
    code: AssistantFailureCodeValue
    next_action: AssistantFailureNextActionValue

    @model_validator(mode="after")
    def validate_provider_failure(self) -> "DecisionFail":
        allowed = {
            (
                AssistantFailureCode.TARGET_AMBIGUOUS,
                AssistantFailureNextAction.REVISE_INPUT,
            ),
            (
                AssistantFailureCode.INTERNAL_ERROR,
                AssistantFailureNextAction.NONE,
            ),
        }
        if (self.code, self.next_action) not in allowed:
            raise ValueError("provider cannot claim this failure code/action pair")
        return self


class DecisionNoMatch(_InternalModel):
    """The backend searched for a target schedule and found none."""

    kind: Literal["no_match"]
    operation: AssistantOutcomeOperationValue


class DecisionCancel(_InternalModel):
    """The user declined to continue; nothing is written."""

    kind: Literal["cancel"]
    text: AssistantText
    operation: AssistantOutcomeOperationValue | None = None


Decision = Annotated[
    DecisionAsk
    | DecisionSelectCandidate
    | DecisionConfirm
    | DecisionCreateIntent
    | DecisionListIntent
    | DecisionNoMatch
    | DecisionCancel
    | DecisionFail,
    Field(discriminator="kind"),
]


class DecisionEnvelope(_InternalModel):
    decision: Decision
    control_state_version: int | None = Field(default=None, ge=1)
    control_state: object | None = None

    @model_validator(mode="after")
    def validate_control_state(self) -> "DecisionEnvelope":
        if (self.control_state_version is None) != (self.control_state is None):
            raise ValueError("control state and version must be supplied together")
        if self.control_state is not None:
            serialize_snapshot(self.control_state)
        return self


class DecisionContext(_InternalModel):
    """Immutable, JSON-safe input exposed to a DecisionProvider."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
        frozen=True,
    )

    request_id: AssistantUuid
    conversation_id: AssistantUuid
    input: dict[str, object]
    task_reference_accepted_at: AssistantDateTime
    timezone: Literal["Asia/Seoul"] = "Asia/Seoul"
    server_time_trusted: bool
    is_question_follow_up: bool
    answered_question_kind: str | None = None
    answered_question_payload: dict[str, object] | None = None
    pending_context: object | None = None
    control_state_version: int | None = Field(default=None, ge=1)
    control_state: object | None = None

    @model_validator(mode="after")
    def validate_json_context(self) -> "DecisionContext":
        if (self.control_state_version is None) != (self.control_state is None):
            raise ValueError("control state and version must be supplied together")
        for value in (
            self.input,
            self.answered_question_payload,
            self.pending_context,
            self.control_state,
        ):
            if value is not None:
                serialize_snapshot(value)
        return self


class PendingCreateIntent(_InternalModel):
    version: Literal[1] = 1
    operation: Literal["create"] = "create"
    title: Title
    scheduled_at: AssistantDateTime
    category: CategoryValue
    time_interpretation: TimeInterpretationValue


class PendingUpdateIntent(_InternalModel):
    version: Literal[1] = 1
    operation: Literal["update"] = "update"
    target_schedule_id: AssistantUuid
    expected_updated_at: AssistantDateTime
    changes: AssistantConfirmationChanges
    time_interpretation: TimeInterpretationValue


class PendingDeleteIntent(_InternalModel):
    version: Literal[1] = 1
    operation: Literal["delete"] = "delete"
    target_schedule_id: AssistantUuid
    expected_updated_at: AssistantDateTime
    time_interpretation: TimeInterpretationValue


PendingTargetIntent = Annotated[
    PendingUpdateIntent | PendingDeleteIntent,
    Field(discriminator="operation"),
]
PendingWriteIntent = Annotated[
    PendingCreateIntent | PendingUpdateIntent | PendingDeleteIntent,
    Field(discriminator="operation"),
]


class CandidateQuestionIntents(_InternalModel):
    version: Literal[1] = 1
    candidates: dict[str, PendingTargetIntent]


def validated_candidate_intent(
    snapshot: object, candidate_id: str
) -> PendingTargetIntent:
    """Return one intent only after validating the entire stored snapshot."""

    if not isinstance(snapshot, dict):
        raise ValueError("candidate intent snapshot must be an object")
    version = snapshot.get("version")
    if type(version) is not int or version != 1:
        raise ValueError("candidate intent snapshot version must be explicitly 1")
    parsed = CandidateQuestionIntents.model_validate(snapshot)
    return parsed.candidates[candidate_id]


class ConfirmationQuestionIntent(_InternalModel):
    version: Literal[1] = 1
    intent: PendingTargetIntent


class DecisionProvider(Protocol):
    def decide(self, context: DecisionContext) -> DecisionEnvelope: ...


class DecisionProviderTimeout(TimeoutError):
    """The provider exceeded its configured internal timeout."""


class TimeTrustSource(Protocol):
    def is_trusted(self) -> bool: ...


class UntrustedTimeSource:
    """Safe production default until a Pi time-trust source is defined."""

    def is_trusted(self) -> bool:
        return False
