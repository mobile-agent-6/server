"""Public Assistant enum values."""

from __future__ import annotations

from enum import StrEnum


class AssistantRequestStatus(StrEnum):
    ACCEPTED = "accepted"
    PROCESSING = "processing"
    COMPLETED = "completed"


class AssistantInputType(StrEnum):
    TEXT = "text"
    SELECTION = "selection"
    CONFIRMATION = "confirmation"


class AssistantResultKind(StrEnum):
    NEED_MORE_INFO = "need_more_info"
    CANDIDATE_SELECTION = "candidate_selection"
    CONFIRMATION = "confirmation"
    OUTCOME = "outcome"
    FAILURE = "failure"
    UNCERTAIN = "uncertain"


class AssistantConfirmationOperation(StrEnum):
    UPDATE = "update"
    DELETE = "delete"


class AssistantOutcome(StrEnum):
    SUCCESS = "success"
    CANCELLED = "cancelled"
    NO_MATCH = "no_match"


class AssistantOutcomeOperation(StrEnum):
    CREATE = "create"
    LIST = "list"
    UPDATE = "update"
    DELETE = "delete"


class AssistantFailureCode(StrEnum):
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    SCHEDULE_REJECTED = "SCHEDULE_REJECTED"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    SCHEDULE_UNAVAILABLE = "SCHEDULE_UNAVAILABLE"
    PROCESSING_INTERRUPTED = "PROCESSING_INTERRUPTED"
    PROCESSING_TIMEOUT = "PROCESSING_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AssistantFailureNextAction(StrEnum):
    REVISE_INPUT = "revise_input"
    RETRY_LATER = "retry_later"
    NONE = "none"


class AssistantUncertainCode(StrEnum):
    EXECUTION_RESULT_UNKNOWN = "EXECUTION_RESULT_UNKNOWN"


class AssistantUncertainNextAction(StrEnum):
    CHECK_STATUS = "check_status"


class AssistantCancelTargetType(StrEnum):
    REQUEST = "request"
    QUESTION = "question"


class AssistantCancelTargetState(StrEnum):
    CANCELLED = "cancelled"
    NOT_GUARANTEED = "not_guaranteed"
    ALREADY_COMPLETED = "already_completed"
    UNCERTAIN = "uncertain"
    NOT_ACTIVE = "not_active"
