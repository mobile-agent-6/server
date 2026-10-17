"""Assistant-specific application errors."""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import ApplicationError


class AssistantUnavailable(ApplicationError):
    status = 503
    code = "ASSISTANT_UNAVAILABLE"
    detail = "The Assistant service is temporarily unavailable."

    def __init__(self, *, assistant_request_id: UUID | None = None) -> None:
        super().__init__(self.detail)
        self.assistant_request_id = assistant_request_id
