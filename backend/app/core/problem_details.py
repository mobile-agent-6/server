"""Problem Details models, exception mapping, and final error boundary."""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.exceptions import ApplicationError, ValidationFailure

logger = logging.getLogger(__name__)

PROBLEM_TITLES = {
    "MALFORMED_REQUEST": "Malformed request",
    "REQUEST_BODY_TOO_LARGE": "Request body too large",
    "SCHEDULE_NOT_FOUND": "Schedule not found",
    "ASSISTANT_REQUEST_NOT_FOUND": "Assistant request not found",
    "ASSISTANT_CONVERSATION_NOT_FOUND": "Assistant conversation not found",
    "ASSISTANT_TARGET_NOT_FOUND": "Assistant target not found",
    "ROUTE_NOT_FOUND": "Route not found",
    "METHOD_NOT_ALLOWED": "Method not allowed",
    "IDEMPOTENCY_KEY_CONFLICT": "Idempotency key conflict",
    "REQUEST_ID_CONFLICT": "Request ID conflict",
    "CONVERSATION_BUSY": "Conversation busy",
    "UNRESOLVED_UNCERTAIN": "Unresolved uncertain result",
    "STALE_QUESTION": "Stale question",
    "QUESTION_EXPIRED": "Question expired",
    "ACKNOWLEDGEMENT_NOT_APPLICABLE": "Acknowledgement not applicable",
    "REQUEST_EXPIRED": "Request expired",
    "CONVERSATION_EXPIRED": "Conversation expired",
    "CLOCK_AUTO_SYNC_ACTIVE": "Automatic time synchronization is active",
    "VALIDATION_ERROR": "Request validation failed",
    "EMPTY_UPDATE": "Empty update",
    "SCHEDULE_IN_THE_PAST": "Schedule is in the past",
    "INVALID_DATE_RANGE": "Invalid date range",
    "DATE_RANGE_TOO_LARGE": "Date range too large",
    "CLOCK_TIME_OUT_OF_RANGE": "Clock time out of range",
    "INTERNAL_ERROR": "Internal server error",
    "DATABASE_UNAVAILABLE": "Database unavailable",
    "TIME_SYNC_UNAVAILABLE": "Time synchronization unavailable",
    "ASSISTANT_UNAVAILABLE": "Assistant unavailable",
}


class ValidationErrorItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    location: Literal["body", "query", "header", "path"]
    pointer: str | None = None
    parameter: str | None = None
    code: str
    detail: str


class Problem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: UUID = Field(serialization_alias="requestId")
    errors: list[ValidationErrorItem] | None = None
    assistant_request_id: UUID | None = Field(
        default=None, serialization_alias="assistantRequestId"
    )
    blocking_request_id: UUID | None = Field(
        default=None, serialization_alias="blockingRequestId"
    )
    active_question_id: UUID | None = Field(
        default=None, serialization_alias="activeQuestionId"
    )
    active_question_request_id: UUID | None = Field(
        default=None, serialization_alias="activeQuestionRequestId"
    )


def _problem_type(code: str) -> str:
    return "https://example.invalid/problems/" + code.lower().replace("_", "-")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    detail: str,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
    assistant_request_id: UUID | str | None = None,
    blocking_request_id: UUID | str | None = None,
    active_question_id: UUID | str | None = None,
    active_question_request_id: UUID | str | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    body: dict[str, Any] = {
        "type": _problem_type(code),
        "title": PROBLEM_TITLES[code],
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "code": code,
        "requestId": request_id,
    }
    if errors is not None:
        body["errors"] = errors
    optional_fields = {
        "assistantRequestId": assistant_request_id,
        "blockingRequestId": blocking_request_id,
        "activeQuestionId": active_question_id,
        "activeQuestionRequestId": active_question_request_id,
    }
    body.update(
        {name: str(value) for name, value in optional_fields.items() if value is not None}
    )
    response_headers = {"X-Request-ID": request_id}
    if headers:
        response_headers.update(headers)
    return JSONResponse(
        body,
        status_code=status,
        media_type="application/problem+json",
        headers=response_headers,
    )


def _json_pointer(parts: tuple[Any, ...]) -> str:
    return "".join(
        "/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


_PARAMETER_NAMES = {
    "from_": "from",
    "to": "to",
    "schedule_id": "scheduleId",
    "request_id": "requestId",
    "conversation_id": "conversationId",
    "idempotency_key": "Idempotency-Key",
}


def _validation_code(error: dict[str, Any]) -> str:
    error_type = error["type"]
    if error_type == "missing":
        if tuple(error.get("loc", ())) == ("body",):
            return "INVALID_TYPE"
        return "REQUIRED"
    if error_type == "extra_forbidden":
        return "UNKNOWN_FIELD"
    if error.get("input") is None and len(tuple(error.get("loc", ()))) > 1:
        return "NULL_NOT_ALLOWED"
    if error_type in {"enum", "union_tag_invalid", "literal_error"}:
        return "INVALID_ENUM" if error_type != "literal_error" else "INVALID_VALUE"
    if error_type == "union_tag_not_found":
        return "REQUIRED"
    if error_type in {"string_too_short", "too_short"}:
        return "TOO_SHORT"
    if error_type in {"string_too_long", "too_long"}:
        return "TOO_LONG"
    custom = {
        "control_character": "INVALID_VALUE",
        "timezone_required": "TIMEZONE_REQUIRED",
        "invalid_format": "INVALID_FORMAT",
        "invalid_type": "INVALID_TYPE",
        "invalid_value": "INVALID_VALUE",
    }
    if error_type in custom:
        return custom[error_type]
    if error_type in {"uuid_parsing", "datetime_parsing"}:
        return "INVALID_FORMAT"
    if error_type.endswith("_type") or error_type in {
        "is_instance_of",
        "model_attributes_type",
    }:
        return "INVALID_TYPE"
    return "INVALID_VALUE"


def _validation_detail(code: str) -> str:
    return {
        "REQUIRED": "This value is required.",
        "UNKNOWN_FIELD": "This field is not allowed.",
        "NULL_NOT_ALLOWED": "Null is not allowed for this field.",
        "TOO_SHORT": "This value is too short.",
        "TOO_LONG": "This value is too long.",
        "INVALID_ENUM": "The value is not one of the allowed choices.",
        "TIMEZONE_REQUIRED": "A timezone offset is required.",
        "INVALID_FORMAT": "The value has an invalid format.",
        "INVALID_TYPE": "The value has an invalid type.",
        "INVALID_VALUE": "The value is invalid.",
    }[code]


def validation_items(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for error in errors:
        loc = tuple(error.get("loc", ()))
        raw_location = str(loc[0]) if loc else "body"
        location = raw_location if raw_location in {"body", "query", "header", "path"} else "body"
        rest = loc[1:] if loc and raw_location == location else loc
        if error["type"] in {"union_tag_invalid", "union_tag_not_found"}:
            rest = (*rest, "type")
        if (
            len(rest) >= 3
            and rest[0] in {"input", "result"}
            and rest[1]
            in {
                "text",
                "selection",
                "confirmation",
                "need_more_info",
                "candidate_selection",
                "outcome",
                "failure",
                "uncertain",
            }
        ):
            rest = (rest[0], *rest[2:])
        code = _validation_code(error)
        item: dict[str, Any] = {
            "location": location,
            "code": code,
            "detail": _validation_detail(code),
        }
        if location == "body":
            item["pointer"] = _json_pointer(rest)
        else:
            parameter = str(rest[-1]) if rest else ""
            item["parameter"] = _PARAMETER_NAMES.get(parameter, parameter)
        items.append(item)
    return items


def _allowed_methods(request: Request) -> str:
    methods: set[str] = set()

    def collect(routes: list[Any]) -> None:
        for route in routes:
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                collect(original_router.routes)
                continue
            path_regex = getattr(route, "path_regex", None)
            if path_regex is not None and path_regex.fullmatch(request.url.path):
                methods.update(getattr(route, "methods", set()) or set())

    collect(request.app.router.routes)
    return ", ".join(sorted(methods))


async def application_error_handler(request: Request, exc: ApplicationError) -> Response:
    errors = exc.errors if isinstance(exc, ValidationFailure) else None
    return problem_response(
        request,
        status=exc.status,
        code=exc.code,
        detail=exc.detail,
        errors=errors,
        assistant_request_id=getattr(exc, "assistant_request_id", None),
        blocking_request_id=getattr(exc, "blocking_request_id", None),
        active_question_id=getattr(exc, "active_question_id", None),
        active_question_request_id=getattr(
            exc, "active_question_request_id", None
        ),
    )


async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    errors = exc.errors()
    if any(error["type"] == "json_invalid" for error in errors):
        return problem_response(
            request,
            status=400,
            code="MALFORMED_REQUEST",
            detail="The JSON request body is malformed.",
        )
    return problem_response(
        request,
        status=422,
        code="VALIDATION_ERROR",
        detail="One or more fields are invalid.",
        errors=validation_items(errors),
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    if exc.status_code == 400:
        return problem_response(
            request,
            status=400,
            code="MALFORMED_REQUEST",
            detail="The JSON request body is malformed.",
        )
    if exc.status_code == 404:
        return problem_response(
            request,
            status=404,
            code="ROUTE_NOT_FOUND",
            detail="The requested route does not exist.",
        )
    if exc.status_code == 405:
        return problem_response(
            request,
            status=405,
            code="METHOD_NOT_ALLOWED",
            detail="The method is not allowed for this route.",
            headers={"Allow": _allowed_methods(request)},
        )
    return problem_response(
        request,
        status=exc.status_code,
        code="INTERNAL_ERROR",
        detail="An unexpected server error occurred.",
    )


async def unexpected_error_response(request: Request, exc: BaseException) -> Response:
    logger.exception(
        "unhandled request error",
        exc_info=exc,
        extra={
            "event": "request.failed",
            "requestId": _request_id(request),
            "errorCode": "INTERNAL_ERROR",
        },
    )
    return problem_response(
        request,
        status=500,
        code="INTERNAL_ERROR",
        detail="An unexpected server error occurred.",
    )


class ProblemBoundaryMiddleware:
    """Convert exceptions escaping Starlette's handled-exception layer to Problem Details."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            request = Request(scope, receive=receive)
            response = await unexpected_error_response(request, exc)
            await response(scope, receive, send)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApplicationError, application_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
