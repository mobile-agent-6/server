"""Application exceptions with stable public problem codes."""

from __future__ import annotations

from typing import Any


class ApplicationError(Exception):
    status = 500
    code = "INTERNAL_ERROR"
    detail = "An unexpected server error occurred."


class ScheduleNotFound(ApplicationError):
    status = 404
    code = "SCHEDULE_NOT_FOUND"
    detail = "The requested schedule does not exist."


class EmptyUpdate(ApplicationError):
    status = 422
    code = "EMPTY_UPDATE"
    detail = "At least one update field is required."


class ScheduleInThePast(ApplicationError):
    status = 422
    code = "SCHEDULE_IN_THE_PAST"
    detail = "The schedule time cannot be in the past."


class InvalidDateRange(ApplicationError):
    status = 422
    code = "INVALID_DATE_RANGE"
    detail = "The end of the date range must be after its start."


class DateRangeTooLarge(ApplicationError):
    status = 422
    code = "DATE_RANGE_TOO_LARGE"
    detail = "The date range cannot exceed 92 days."


class IdempotencyKeyConflict(ApplicationError):
    status = 409
    code = "IDEMPOTENCY_KEY_CONFLICT"
    detail = "The idempotency key was already used for a different request."


class DatabaseUnavailable(ApplicationError):
    status = 503
    code = "DATABASE_UNAVAILABLE"
    detail = "The database is temporarily unavailable."


class ValidationFailure(ApplicationError):
    status = 422
    code = "VALIDATION_ERROR"
    detail = "One or more fields are invalid."

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__(self.detail)
        self.errors = errors
