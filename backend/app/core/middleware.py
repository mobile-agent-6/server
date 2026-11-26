"""Request ID and request logging middleware."""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import Request
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.problem_details import problem_response

logger = logging.getLogger(__name__)

ASSISTANT_BODY_LIMIT = 16_384


def _is_assistant_post_path(scope: Scope) -> bool:
    if scope.get("method") != "POST":
        return False
    path = str(scope.get("path", ""))
    normalized_path = path.rstrip("/") or "/"
    if normalized_path == "/api/v1/assistant/requests":
        return True
    parts = normalized_path.split("/")
    return (
        len(parts) == 7
        and parts[:5] == ["", "api", "v1", "assistant", "conversations"]
        and bool(parts[5])
        and parts[6] == "cancel"
    )


class AssistantRequestBodyMiddleware:
    """Enforce Assistant POST body size and media type before JSON decoding."""

    def __init__(self, app: ASGIApp, *, limit: int = ASSISTANT_BODY_LIMIT) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_assistant_post_path(scope):
            await self.app(scope, receive, send)
            return

        messages: list[Message] = []
        received_bytes = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            messages.append(message)
            if message["type"] != "http.request":
                continue
            received_bytes += len(message.get("body", b""))
            if received_bytes > self.limit:
                request = Request(scope)
                response = problem_response(
                    request,
                    status=413,
                    code="REQUEST_BODY_TOO_LARGE",
                    detail="The Assistant request body cannot exceed 16,384 bytes.",
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        content_type = ""
        for raw_name, raw_value in scope.get("headers", []):
            if raw_name.lower() == b"content-type":
                content_type = raw_value.decode("latin-1")
                break
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            request = Request(scope)
            response = problem_response(
                request,
                status=422,
                code="VALIDATION_ERROR",
                detail="One or more fields are invalid.",
                errors=[
                    {
                        "location": "body",
                        "pointer": "",
                        "code": "INVALID_TYPE",
                        "detail": "Content-Type must be application/json.",
                    }
                ],
            )
            await response(scope, receive, send)
            return

        next_message = 0

        async def replay_receive() -> Message:
            nonlocal next_message
            if next_message < len(messages):
                message = messages[next_message]
                next_message += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid4())
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        started = time.monotonic()
        state["request_started_monotonic"] = started
        status = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request completed",
                extra={
                    "event": "request.completed",
                    "requestId": request_id,
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status,
                    "durationMs": round((time.monotonic() - started) * 1000, 3),
                },
            )
