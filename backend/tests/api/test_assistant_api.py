from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

from app.core.middleware import AssistantRequestBodyMiddleware

REQUEST_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CONVERSATION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
QUESTION_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def request_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "requestId": REQUEST_ID,
        "input": {"type": "text", "text": "재고 확인"},
    }
    payload.update(overrides)
    return payload


def cancel_payload() -> dict[str, object]:
    return {"target": {"type": "request", "id": REQUEST_ID}}


def encoded(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def padded(payload: object, size: int) -> bytes:
    raw = encoded(payload)
    assert len(raw) <= size
    return raw + b" " * (size - len(raw))


def assert_problem(response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    request_id = UUID(response.headers["X-Request-ID"])
    body = response.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["requestId"] == str(request_id)
    return body


def direct_asgi_call(
    application,
    *,
    path: str,
    body: bytes | None = None,
    receive_messages: list[Message] | None = None,
    headers: list[tuple[bytes, bytes]],
    timeout_seconds: float | None = None,
) -> tuple[int | None, dict[str, str], bytes]:
    messages: list[Message]
    if receive_messages is None:
        assert body is not None
        messages = [
            {"type": "http.request", "body": body, "more_body": False}
        ]
    else:
        assert body is None
        messages = list(receive_messages)
    sent: list[Message] = []

    async def receive() -> Message:
        if messages:
            return messages.pop(0)  # type: ignore[return-value]
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"ignored=true",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "root_path": "",
    }

    async def call_application() -> None:
        if timeout_seconds is None:
            await application(scope, receive, send)
        else:
            await asyncio.wait_for(
                application(scope, receive, send), timeout=timeout_seconds
            )

    asyncio.run(call_application())
    start = next(
        (message for message in sent if message["type"] == "http.response.start"),
        None,
    )
    if start is None:
        return None, {}, b""
    response_headers = {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in start["headers"]
    }
    content = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return start["status"], response_headers, content


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/assistant/requests", request_payload()),
        (
            f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel",
            cancel_payload(),
        ),
    ],
)
def test_assistant_post_body_size_boundaries(
    client: TestClient, path: str, payload: dict[str, object]
) -> None:
    allowed = client.post(
        path,
        content=padded(payload, 16_384),
        headers={"Content-Type": "application/json"},
    )
    assert allowed.status_code == 503

    rejected = client.post(
        path,
        content=padded(payload, 16_385),
        headers={"Content-Type": "application/json"},
    )
    body = assert_problem(rejected, 413, "REQUEST_BODY_TOO_LARGE")
    assert "assistantRequestId" not in body


def test_body_limit_uses_received_bytes_not_content_length(application) -> None:
    for content_length in (None, b"1", b"999999"):
        headers = [(b"content-type", b"application/json")]
        if content_length is not None:
            headers.append((b"content-length", content_length))
        status, response_headers, content = direct_asgi_call(
            application,
            path="/api/v1/assistant/requests",
            body=padded(request_payload(), 16_385),
            headers=headers,
        )
        assert status == 413
        assert response_headers["content-type"].startswith("application/problem+json")
        assert UUID(response_headers["x-request-id"])
        assert json.loads(content)["code"] == "REQUEST_BODY_TOO_LARGE"

    status, _, content = direct_asgi_call(
        application,
        path="/api/v1/assistant/requests",
        body=encoded(request_payload()),
        headers=[
            (b"content-type", b"application/json"),
            (b"content-length", b"999999"),
        ],
    )
    assert status == 503
    assert json.loads(content)["assistantRequestId"] == REQUEST_ID


def test_body_limit_rejects_cumulative_16_385_bytes_across_chunks(application) -> None:
    body = padded(request_payload(), 16_385)
    status, response_headers, content = direct_asgi_call(
        application,
        path="/api/v1/assistant/requests",
        receive_messages=[
            {"type": "http.request", "body": body[:8_192], "more_body": True},
            {"type": "http.request", "body": body[8_192:], "more_body": False},
        ],
        headers=[(b"content-type", b"application/json")],
    )

    assert status == 413
    assert response_headers["content-type"].startswith("application/problem+json")
    assert json.loads(content)["code"] == "REQUEST_BODY_TOO_LARGE"


def test_body_limit_allows_cumulative_16_384_bytes_across_chunks(application) -> None:
    body = padded(request_payload(), 16_384)
    status, _, content = direct_asgi_call(
        application,
        path="/api/v1/assistant/requests",
        receive_messages=[
            {"type": "http.request", "body": body[:8_192], "more_body": True},
            {"type": "http.request", "body": body[8_192:], "more_body": False},
        ],
        headers=[(b"content-type", b"application/json")],
    )

    assert status == 503
    assert json.loads(content)["code"] == "ASSISTANT_UNAVAILABLE"


def test_disconnect_before_body_completion_stops_without_response() -> None:
    downstream_called = False

    async def downstream(scope: Scope, receive, send) -> None:
        nonlocal downstream_called
        del scope, receive
        downstream_called = True
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [],
            }
        )

    middleware = AssistantRequestBodyMiddleware(downstream)
    status, response_headers, content = direct_asgi_call(
        middleware,
        path="/api/v1/assistant/requests",
        receive_messages=[
            {"type": "http.request", "body": b"not logged", "more_body": True},
            {"type": "http.disconnect"},
        ],
        headers=[(b"content-type", b"application/json")],
        timeout_seconds=1,
    )

    assert downstream_called is False
    assert status is None
    assert response_headers == {}
    assert content == b""


def test_body_middleware_replays_chunks_and_disconnect_without_loss() -> None:
    original: list[Message] = [
        {"type": "http.request", "body": b'{"a":', "more_body": True},
        {"type": "http.request", "body": b"1}", "more_body": False},
        {"type": "http.disconnect"},
    ]
    remaining = list(original)
    received: list[Message] = []

    async def downstream(scope: Scope, receive, send) -> None:
        del scope, send
        received.extend([await receive(), await receive(), await receive()])

    middleware = AssistantRequestBodyMiddleware(downstream)
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/assistant/requests/",
        "raw_path": b"/api/v1/assistant/requests/",
        "query_string": b"query=ignored",
        "headers": [(b"content-type", b"application/json; charset=utf-8")],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
        "root_path": "",
    }

    async def receive() -> Message:
        return remaining.pop(0)

    async def send(message: Message) -> None:
        raise AssertionError(message)

    asyncio.run(middleware(scope, receive, send))
    assert received == original


def test_oversized_body_precedes_invalid_content_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/assistant/requests",
        content=b"x" * 16_385,
        headers={"Content-Type": "text/plain"},
    )
    assert_problem(response, 413, "REQUEST_BODY_TOO_LARGE")


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/assistant/requests/", request_payload()),
        (
            f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel/",
            cancel_payload(),
        ),
    ],
)
def test_trailing_slash_assistant_posts_enforce_body_limit(
    client: TestClient, path: str, payload: dict[str, object]
) -> None:
    response = client.post(
        path,
        content=padded(payload, 16_385),
        headers={"Content-Type": "application/json"},
    )

    assert_problem(response, 413, "REQUEST_BODY_TOO_LARGE")


def test_schedule_post_is_not_subject_to_assistant_body_limit(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/schedules",
        content=padded(
            {
                "title": "재고 확인",
                "scheduledAt": "2026-09-13T04:45:00Z",
                "category": "INVENTORY",
            },
            16_385,
        ),
        headers={
            "Content-Type": "application/json",
            "Idempotency-Key": "assistant-body-gate-scope-regression",
        },
    )

    assert response.status_code == 201
    assert response.json().get("code") != "REQUEST_BODY_TOO_LARGE"


@pytest.mark.parametrize("content_type", ["text/plain", "", "application/problem+json"])
def test_invalid_content_type_is_422_without_json_parsing(
    client: TestClient, content_type: str
) -> None:
    headers = {"Content-Type": content_type} if content_type else {}
    response = client.post(
        "/api/v1/assistant/requests",
        content=b"{malformed",
        headers=headers,
    )
    body = assert_problem(response, 422, "VALIDATION_ERROR")
    assert body["errors"] == [
        {
            "location": "body",
            "pointer": "",
            "code": "INVALID_TYPE",
            "detail": "Content-Type must be application/json.",
        }
    ]


@pytest.mark.parametrize("payload", [[], "text"])
def test_non_object_json_is_invalid_type(client: TestClient, payload: object) -> None:
    response = client.post("/api/v1/assistant/requests", json=payload)
    body = assert_problem(response, 422, "VALIDATION_ERROR")
    assert body["errors"][0] == {
        "location": "body",
        "pointer": "",
        "code": "INVALID_TYPE",
        "detail": "The value has an invalid type.",
    }


def test_malformed_json_and_utf8_are_400(client: TestClient) -> None:
    for content in (b'{"requestId":', b'{"input":"\xff"}'):
        response = client.post(
            "/api/v1/assistant/requests",
            content=content,
            headers={"Content-Type": "application/json"},
        )
        body = assert_problem(response, 400, "MALFORMED_REQUEST")
        assert "assistantRequestId" not in body


def test_cancel_uses_the_same_content_type_and_json_error_order(
    client: TestClient,
) -> None:
    path = f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel"
    invalid_type = client.post(
        path,
        content=b"{malformed",
        headers={"Content-Type": "text/plain"},
    )
    assert_problem(invalid_type, 422, "VALIDATION_ERROR")

    malformed = client.post(
        path,
        content=b"{malformed",
        headers={"Content-Type": "application/json"},
    )
    assert_problem(malformed, 400, "MALFORMED_REQUEST")


def test_schema_validation_pointer_codes_and_no_logical_id(client: TestClient) -> None:
    invalid_uuid = client.post(
        "/api/v1/assistant/requests",
        json=request_payload(requestId="aaaaaaaa-aaaa-1aaa-8aaa-aaaaaaaaaaaa"),
    )
    body = assert_problem(invalid_uuid, 422, "VALIDATION_ERROR")
    assert body["errors"][0]["pointer"] == "/requestId"
    assert body["errors"][0]["code"] == "INVALID_VALUE"
    assert "assistantRequestId" not in body

    invalid_discriminator = client.post(
        "/api/v1/assistant/requests",
        json=request_payload(input={"type": "unknown"}),
    )
    body = assert_problem(invalid_discriminator, 422, "VALIDATION_ERROR")
    assert body["errors"][0]["pointer"] == "/input/type"
    assert body["errors"][0]["code"] == "INVALID_ENUM"

    invalid_text = client.post(
        "/api/v1/assistant/requests",
        content=(
            b'{"requestId":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
            b'"input":{"type":"text","text":"\\ud800"}}'
        ),
        headers={"Content-Type": "application/json"},
    )
    body = assert_problem(invalid_text, 422, "VALIDATION_ERROR")
    assert body["errors"][0]["pointer"] == "/input/text"
    assert body["errors"][0]["code"] == "INVALID_VALUE"
    assert "assistantRequestId" not in body


def test_path_and_body_validation_precede_unavailable(client: TestClient) -> None:
    post = client.post(
        "/api/v1/assistant/requests",
        json=request_payload(input={"type": "text"}),
    )
    assert_problem(post, 422, "VALIDATION_ERROR")

    get = client.get("/api/v1/assistant/requests/not-a-uuid")
    body = assert_problem(get, 422, "VALIDATION_ERROR")
    assert body["errors"][0]["location"] == "path"
    assert body["errors"][0]["parameter"] == "requestId"

    cancel_path = client.post(
        "/api/v1/assistant/conversations/not-a-uuid/cancel",
        json=cancel_payload(),
    )
    assert_problem(cancel_path, 422, "VALIDATION_ERROR")

    cancel_body = client.post(
        f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel",
        json={"target": {"type": "request", "id": "not-a-uuid"}},
    )
    assert_problem(cancel_body, 422, "VALIDATION_ERROR")


def test_valid_skeleton_endpoints_return_unavailable_without_side_effects(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_side_effect(*args, **kwargs):
        raise AssertionError((args, kwargs))

    monkeypatch.setattr(client.app.state, "session_factory", unexpected_side_effect)
    monkeypatch.setattr(client.app.state.engine, "connect", unexpected_side_effect)

    post = client.post("/api/v1/assistant/requests", json=request_payload())
    post_body = assert_problem(post, 503, "ASSISTANT_UNAVAILABLE")
    assert post_body["assistantRequestId"] == REQUEST_ID

    get = client.get(f"/api/v1/assistant/requests/{REQUEST_ID.upper()}")
    get_body = assert_problem(get, 503, "ASSISTANT_UNAVAILABLE")
    assert get_body["assistantRequestId"] == REQUEST_ID

    cancel = client.post(
        f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel",
        json=cancel_payload(),
    )
    cancel_body = assert_problem(cancel, 503, "ASSISTANT_UNAVAILABLE")
    assert "assistantRequestId" not in cancel_body


def test_assistant_error_contract_isolated_from_schedule_errors(client: TestClient) -> None:
    response = client.get("/api/v1/schedules/not-a-uuid")
    body = assert_problem(response, 422, "VALIDATION_ERROR")
    assert {
        "assistantRequestId",
        "blockingRequestId",
        "activeQuestionId",
        "activeQuestionRequestId",
    }.isdisjoint(body)
