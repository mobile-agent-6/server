"""Assistant-specific application errors."""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import ApplicationError


class AssistantError(ApplicationError):
    """Application error carrying optional Assistant recovery identifiers."""

    def __init__(
        self,
        *,
        assistant_request_id: UUID | None = None,
        blocking_request_id: UUID | None = None,
        active_question_id: UUID | None = None,
        active_question_request_id: UUID | None = None,
    ) -> None:
        super().__init__(self.detail)
        self.assistant_request_id = assistant_request_id
        self.blocking_request_id = blocking_request_id
        self.active_question_id = active_question_id
        self.active_question_request_id = active_question_request_id


class AssistantUnavailable(AssistantError):
    status = 503
    code = "ASSISTANT_UNAVAILABLE"
    detail = "The Assistant service is temporarily unavailable."


class AssistantDatabaseUnavailable(AssistantError):
    status = 503
    code = "DATABASE_UNAVAILABLE"
    detail = "The database is temporarily unavailable."


class AssistantRequestNotFound(AssistantError):
    status = 404
    code = "ASSISTANT_REQUEST_NOT_FOUND"
    detail = "The requested Assistant request does not exist."


class AssistantConversationNotFound(AssistantError):
    status = 404
    code = "ASSISTANT_CONVERSATION_NOT_FOUND"
    detail = "The requested Assistant conversation does not exist."


class AssistantTargetNotFound(AssistantError):
    status = 404
    code = "ASSISTANT_TARGET_NOT_FOUND"
    detail = "The requested Assistant cancellation target does not exist."


class AssistantRequestIdConflict(AssistantError):
    status = 409
    code = "REQUEST_ID_CONFLICT"
    detail = "The Assistant request ID was already used for different input."


class AssistantRequestExpiredForPost(AssistantError):
    status = 409
    code = "REQUEST_EXPIRED"
    detail = "The Assistant request details are no longer available."


class AssistantRequestExpiredForGet(AssistantError):
    status = 410
    code = "REQUEST_EXPIRED"
    detail = "The Assistant request details are no longer available."


class AssistantConversationBusy(AssistantError):
    status = 409
    code = "CONVERSATION_BUSY"
    detail = "The Assistant conversation is already processing a request."


class AssistantUnresolvedUncertain(AssistantError):
    status = 409
    code = "UNRESOLVED_UNCERTAIN"
    detail = "An unacknowledged uncertain Assistant result blocks new work."


class AssistantStaleQuestion(AssistantError):
    status = 409
    code = "STALE_QUESTION"
    detail = "The Assistant input does not match the active question."


class AssistantQuestionExpired(AssistantError):
    status = 409
    code = "QUESTION_EXPIRED"
    detail = "The Assistant question has expired."


class AssistantAcknowledgementNotApplicable(AssistantError):
    status = 409
    code = "ACKNOWLEDGEMENT_NOT_APPLICABLE"
    detail = "The specified Assistant request cannot be acknowledged."


class AssistantConversationExpired(AssistantError):
    status = 409
    code = "CONVERSATION_EXPIRED"
    detail = "The Assistant conversation has expired."
