#!/usr/bin/env python3
"""Run the public API contract against a real loopback uvicorn process."""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID


sys.dont_write_bytecode = True

LOOPBACK = "127.0.0.1"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_DATABASE = (BACKEND_ROOT / "data" / "app.db").resolve(strict=False)
ASSISTANT_REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
ASSISTANT_CONVERSATION_ID = UUID("22222222-2222-4222-8222-222222222222")
UNAVAILABLE_REQUEST_ID = UUID("33333333-3333-4333-8333-333333333333")
MISSING_REQUEST_ID = UUID("44444444-4444-4444-8444-444444444444")
MISSING_CONVERSATION_ID = UUID("55555555-5555-4555-8555-555555555555")


class SmokeFailure(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SmokeFailure(
                f"response {self.status} did not contain valid JSON"
            ) from exc


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def temporary_environment(database_path: Path, backup_dir: Path, port: int) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("SCHEDULE_")
    }
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "SCHEDULE_APP_ENV": "development",
            "SCHEDULE_DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "SCHEDULE_API_HOST": LOOPBACK,
            "SCHEDULE_API_PORT": str(port),
            "SCHEDULE_BACKUP_DIR": str(backup_dir),
            "SCHEDULE_LOG_FILE": "",
            "SCHEDULE_TIME_SYNC_ENABLED": "false",
        }
    )
    return environment


def choose_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind((LOOPBACK, 0))
        return int(reservation.getsockname()[1])


def run_migrations(environment: dict[str, str]) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SmokeFailure(
            "Alembic upgrade failed:\n" + completed.stdout + completed.stderr
        )


def seed_assistant_success(database_path: Path) -> None:
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.core.config import Settings
    from app.core.database import create_api_engine, create_session_factory
    from app.modules.assistant.model import AssistantConversation, AssistantRequest

    settings = Settings(
        _env_file=None,
        app_env="development",
        database_url=f"sqlite:///{database_path.as_posix()}",
        backup_dir=database_path.parent / "backups",
        time_sync_enabled=False,
    )
    require(
        settings.database_path == database_path,
        "Assistant fixture engine did not resolve to the temporary database",
    )
    engine = create_api_engine(settings)
    session_factory = create_session_factory(engine)
    recorded_at = datetime.now(timezone.utc)
    result = {
        "kind": "outcome",
        "text": "The smoke fixture completed.",
        "outcome": "no_match",
        "schedulesChanged": False,
    }
    try:
        with session_factory.begin() as session:
            session.add(
                AssistantConversation(
                    id=ASSISTANT_CONVERSATION_ID,
                    created_at=recorded_at,
                    last_valid_input_at=recorded_at,
                    expired_at=None,
                    control_state_version=None,
                    control_state_json=None,
                )
            )
            session.flush()
            session.add(
                AssistantRequest(
                    id=ASSISTANT_REQUEST_ID,
                    conversation_id=ASSISTANT_CONVERSATION_ID,
                    fingerprint_version=1,
                    fingerprint_hash="a" * 64,
                    normalized_body_json={"requestId": str(ASSISTANT_REQUEST_ID)},
                    status="completed",
                    result_kind="outcome",
                    result_json=result,
                    accepted_at=recorded_at,
                    processing_at=recorded_at,
                    completed_at=recorded_at,
                    task_reference_accepted_at=recorded_at,
                    write_started=False,
                    write_started_at=None,
                    write_operation=None,
                    write_commit_mode=None,
                    pending_write_version=None,
                    pending_write_json=None,
                    create_idempotency_key=None,
                    create_idempotency_scope=None,
                    had_uncertain=False,
                    uncertain_at=None,
                    uncertain_acknowledged_at=None,
                    uncertain_acknowledged_by_request_id=None,
                    uncertain_resolved_at=None,
                    retention_started_at=recorded_at,
                    tombstone=False,
                    tombstoned_at=None,
                    cancelled_at=None,
                )
            )
    finally:
        engine.dispose()


def request(
    port: int,
    method: str,
    path: str,
    *,
    json_body: object | None = None,
    raw_body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    require(
        json_body is None or raw_body is None,
        "request cannot have both json_body and raw_body",
    )
    request_headers = dict(headers or {})
    body = raw_body
    if json_body is not None:
        body = json.dumps(
            json_body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    connection = http.client.HTTPConnection(LOOPBACK, port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        response_body = response.read()
        response_headers = {
            name.casefold(): value for name, value in response.getheaders()
        }
        return HttpResponse(response.status, response_headers, response_body)
    finally:
        connection.close()


def request_id(response: HttpResponse) -> str:
    raw_request_id = response.headers.get("x-request-id")
    require(raw_request_id is not None, "response is missing X-Request-ID")
    try:
        parsed = UUID(raw_request_id)
    except ValueError as exc:
        raise SmokeFailure("X-Request-ID is not a UUID") from exc
    require(parsed.version == 4, "X-Request-ID is not UUID v4")
    return str(parsed)


def assert_json_response(response: HttpResponse, status: int) -> Any:
    require(response.status == status, f"expected HTTP {status}, got {response.status}")
    media_type = response.headers.get("content-type", "").split(";", 1)[0]
    require(media_type == "application/json", f"unexpected success media type: {media_type}")
    request_id(response)
    return response.json()


def assert_problem(
    response: HttpResponse,
    *,
    status: int,
    code: str,
    title: str,
    path: str,
    forbidden_values: tuple[str, ...],
) -> dict[str, Any]:
    require(response.status == status, f"expected HTTP {status}, got {response.status}")
    media_type = response.headers.get("content-type", "").split(";", 1)[0]
    require(
        media_type == "application/problem+json",
        f"unexpected problem media type: {media_type}",
    )
    expected_request_id = request_id(response)
    body = response.json()
    require(isinstance(body, dict), "Problem response is not an object")
    require(body.get("status") == status, "Problem status does not match HTTP status")
    require(body.get("code") == code, f"expected Problem code {code}")
    require(body.get("title") == title, f"unexpected title for {code}")
    require(body.get("requestId") == expected_request_id, "Problem requestId/header mismatch")
    require(body.get("instance") == path.split("?", 1)[0], "unexpected Problem instance")
    detail = body.get("detail")
    require(isinstance(detail, str) and bool(detail.strip()), "Problem detail is empty")
    normalized = detail.casefold()
    unsafe_fragments = (
        "traceback",
        "sqlite:///",
        "operationalerror",
        "sqlalchemy.exc",
        'file "',
        *(value.casefold() for value in forbidden_values),
    )
    require(
        all(fragment not in normalized for fragment in unsafe_fragments if fragment),
        "Problem detail exposed an internal value",
    )
    return body


def wait_until_ready(process: subprocess.Popen[bytes], port: int, log_path: Path) -> None:
    deadline = time.monotonic() + 10
    last_error = "server did not answer"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            response = request(port, "GET", "/health")
            if response.status == 200:
                return
            last_error = f"health returned HTTP {response.status}"
        except OSError as exc:
            last_error = type(exc).__name__
        time.sleep(0.05)
    log_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    raise SmokeFailure(f"uvicorn readiness failed ({last_error})\n{log_tail}")


def verify_server_identity(process: subprocess.Popen[bytes], port: int) -> None:
    require(process.poll() is None, "uvicorn exited after readiness")
    seeded_path = f"/api/v1/assistant/requests/{ASSISTANT_REQUEST_ID}"
    seeded = assert_json_response(request(port, "GET", seeded_path), 200)
    require(
        seeded.get("requestId") == str(ASSISTANT_REQUEST_ID),
        "seeded Assistant request ID does not match",
    )
    require(
        seeded.get("conversationId") == str(ASSISTANT_CONVERSATION_ID),
        "seeded Assistant conversation ID does not match",
    )
    require(seeded.get("status") == "completed", "seeded request is not completed")
    require(
        seeded.get("result", {}).get("kind") == "outcome",
        "seeded Assistant result does not match",
    )
    require(process.poll() is None, "uvicorn exited during identity verification")


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_http_checks(port: int, temporary_root: Path, database_path: Path) -> int:
    checks = 0
    forbidden = (str(temporary_root), str(database_path), database_path.name)

    def passed() -> None:
        nonlocal checks
        checks += 1

    health = assert_json_response(request(port, "GET", "/health"), 200)
    require(health["status"] == "ok", "health status is not ok")
    passed()

    openapi = assert_json_response(request(port, "GET", "/openapi.json"), 200)
    required_paths = {
        "/api/v1/assistant/requests",
        "/api/v1/assistant/requests/{requestId}",
        "/api/v1/assistant/conversations/{conversationId}/cancel",
    }
    require(required_paths <= set(openapi["paths"]), "Assistant paths are missing from OpenAPI")
    required_components = {
        "AssistantRequestCreate",
        "AssistantRequestAcceptedResponse",
        "AssistantRequestStatusResponse",
        "AssistantCancelRequest",
        "AssistantCancelResponse",
        "AssistantResultOutcome",
        "AssistantInputText",
        "AssistantRequestStatus",
    }
    require(
        required_components <= set(openapi["components"]["schemas"]),
        "Assistant components are missing from OpenAPI",
    )
    passed()

    unknown_path = "/api/v1/unknown"
    assert_problem(
        request(port, "GET", unknown_path),
        status=404,
        code="ROUTE_NOT_FOUND",
        title="Route not found",
        path=unknown_path,
        forbidden_values=forbidden,
    )
    passed()

    schedules_method = request(port, "PUT", "/api/v1/schedules")
    assert_problem(
        schedules_method,
        status=405,
        code="METHOD_NOT_ALLOWED",
        title="Method not allowed",
        path="/api/v1/schedules",
        forbidden_values=forbidden,
    )
    require(
        {value.strip() for value in schedules_method.headers.get("allow", "").split(",")}
        == {"GET", "POST"},
        "Schedule Allow header does not contain GET and POST",
    )
    passed()

    assistant_method = request(port, "GET", "/api/v1/assistant/requests")
    assert_problem(
        assistant_method,
        status=405,
        code="METHOD_NOT_ALLOWED",
        title="Method not allowed",
        path="/api/v1/assistant/requests",
        forbidden_values=forbidden,
    )
    require(
        assistant_method.headers.get("allow") == "POST",
        "Assistant Allow header is not POST",
    )
    passed()

    malformed = request(
        port,
        "POST",
        "/api/v1/schedules",
        raw_body=b"{",
        headers={"Content-Type": "application/json", "Idempotency-Key": "malformed"},
    )
    assert_problem(
        malformed,
        status=400,
        code="MALFORMED_REQUEST",
        title="Malformed request",
        path="/api/v1/schedules",
        forbidden_values=forbidden,
    )
    passed()

    now = datetime.now(timezone.utc)
    schedule_payload = {
        "title": "Contract smoke schedule",
        "scheduledAt": iso_z(now + timedelta(hours=1)),
        "category": "INVENTORY",
    }
    unknown_field = request(
        port,
        "POST",
        "/api/v1/schedules",
        json_body={**schedule_payload, "unknownField": True},
        headers={"Idempotency-Key": "unknown-field"},
    )
    unknown_body = assert_problem(
        unknown_field,
        status=422,
        code="VALIDATION_ERROR",
        title="Request validation failed",
        path="/api/v1/schedules",
        forbidden_values=forbidden,
    )
    require(unknown_body["errors"][0]["code"] == "UNKNOWN_FIELD", "wrong field error")
    passed()

    idempotency_key = "api-contract-smoke"
    created_response = request(
        port,
        "POST",
        "/api/v1/schedules",
        json_body=schedule_payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    created = assert_json_response(created_response, 201)
    schedule_id = UUID(created["id"])
    expected_location = f"/api/v1/schedules/{schedule_id}"
    require(created_response.headers.get("location") == expected_location, "wrong Location header")
    require(
        "idempotent-replayed" not in created_response.headers,
        "first create was marked replayed",
    )
    passed()

    replay = request(
        port,
        "POST",
        "/api/v1/schedules",
        json_body=schedule_payload,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert_json_response(replay, 201)
    require(replay.body == created_response.body, "idempotent replay body changed")
    require(replay.headers.get("location") == expected_location, "replay Location changed")
    require(replay.headers.get("idempotent-replayed") == "true", "replay header missing")
    passed()

    conflict = request(
        port,
        "POST",
        "/api/v1/schedules",
        json_body={**schedule_payload, "title": "Different input"},
        headers={"Idempotency-Key": idempotency_key},
    )
    assert_problem(
        conflict,
        status=409,
        code="IDEMPOTENCY_KEY_CONFLICT",
        title="Idempotency key conflict",
        path="/api/v1/schedules",
        forbidden_values=forbidden,
    )
    passed()

    fetched = assert_json_response(request(port, "GET", expected_location), 200)
    require(fetched == created, "fetched Schedule differs from create response")
    passed()

    updated = assert_json_response(
        request(
            port,
            "PATCH",
            expected_location,
            json_body={"title": "Updated contract smoke schedule"},
        ),
        200,
    )
    require(updated["title"] == "Updated contract smoke schedule", "Schedule patch failed")
    passed()

    query = urlencode(
        {
            "from": iso_z(now - timedelta(hours=1)),
            "to": iso_z(now + timedelta(hours=2)),
        }
    )
    listed = assert_json_response(
        request(port, "GET", f"/api/v1/schedules?{query}"), 200
    )
    require(
        [item["id"] for item in listed["items"]] == [str(schedule_id)],
        "Schedule list mismatch",
    )
    passed()

    missing_schedule_path = "/api/v1/schedules/66666666-6666-4666-8666-666666666666"
    assert_problem(
        request(port, "GET", missing_schedule_path),
        status=404,
        code="SCHEDULE_NOT_FOUND",
        title="Schedule not found",
        path=missing_schedule_path,
        forbidden_values=forbidden,
    )
    passed()

    deleted = request(port, "DELETE", expected_location)
    require(deleted.status == 204, f"expected HTTP 204, got {deleted.status}")
    require(deleted.body == b"", "204 response contained a body")
    require("content-type" not in deleted.headers, "204 response declared Content-Type")
    request_id(deleted)
    passed()

    assert_problem(
        request(port, "GET", expected_location),
        status=404,
        code="SCHEDULE_NOT_FOUND",
        title="Schedule not found",
        path=expected_location,
        forbidden_values=forbidden,
    )
    passed()

    cancel_path = f"/api/v1/assistant/conversations/{ASSISTANT_CONVERSATION_ID}/cancel"
    cancelled = assert_json_response(
        request(
            port,
            "POST",
            cancel_path,
            json_body={"target": {"type": "request", "id": str(ASSISTANT_REQUEST_ID)}},
        ),
        200,
    )
    require(cancelled["targetState"] == "already_completed", "Assistant cancel state changed")
    passed()

    missing_request_path = f"/api/v1/assistant/requests/{MISSING_REQUEST_ID}"
    assert_problem(
        request(port, "GET", missing_request_path),
        status=404,
        code="ASSISTANT_REQUEST_NOT_FOUND",
        title="Assistant request not found",
        path=missing_request_path,
        forbidden_values=forbidden,
    )
    passed()

    assistant_post_path = "/api/v1/assistant/requests"
    oversized = request(
        port,
        "POST",
        assistant_post_path,
        raw_body=b"{" + (b" " * 16_384),
        headers={"Content-Type": "text/plain"},
    )
    assert_problem(
        oversized,
        status=413,
        code="REQUEST_BODY_TOO_LARGE",
        title="Request body too large",
        path=assistant_post_path,
        forbidden_values=forbidden,
    )
    passed()

    wrong_content_type = request(
        port,
        "POST",
        assistant_post_path,
        raw_body=b"{",
        headers={"Content-Type": "text/plain"},
    )
    assert_problem(
        wrong_content_type,
        status=422,
        code="VALIDATION_ERROR",
        title="Request validation failed",
        path=assistant_post_path,
        forbidden_values=forbidden,
    )
    passed()

    malformed_assistant = request(
        port,
        "POST",
        assistant_post_path,
        raw_body=b"{",
        headers={"Content-Type": "application/json"},
    )
    assert_problem(
        malformed_assistant,
        status=400,
        code="MALFORMED_REQUEST",
        title="Malformed request",
        path=assistant_post_path,
        forbidden_values=forbidden,
    )
    passed()

    invalid_schema = request(port, "POST", assistant_post_path, json_body={})
    assert_problem(
        invalid_schema,
        status=422,
        code="VALIDATION_ERROR",
        title="Request validation failed",
        path=assistant_post_path,
        forbidden_values=forbidden,
    )
    passed()

    unavailable = request(
        port,
        "POST",
        assistant_post_path,
        json_body={
            "requestId": str(UNAVAILABLE_REQUEST_ID),
            "input": {"type": "text", "text": "Check inventory"},
        },
    )
    unavailable_body = assert_problem(
        unavailable,
        status=503,
        code="ASSISTANT_UNAVAILABLE",
        title="Assistant unavailable",
        path=assistant_post_path,
        forbidden_values=forbidden,
    )
    require(
        unavailable_body.get("assistantRequestId") == str(UNAVAILABLE_REQUEST_ID),
        "Assistant recovery request ID is missing",
    )
    passed()

    unavailable_get_path = f"/api/v1/assistant/requests/{UNAVAILABLE_REQUEST_ID}"
    absent = assert_problem(
        request(port, "GET", unavailable_get_path),
        status=404,
        code="ASSISTANT_REQUEST_NOT_FOUND",
        title="Assistant request not found",
        path=unavailable_get_path,
        forbidden_values=forbidden,
    )
    require(
        absent.get("assistantRequestId") == str(UNAVAILABLE_REQUEST_ID),
        "missing request recovery ID is absent",
    )
    passed()

    missing_cancel_path = (
        f"/api/v1/assistant/conversations/{MISSING_CONVERSATION_ID}/cancel"
    )
    missing_cancel = assert_problem(
        request(
            port,
            "POST",
            missing_cancel_path,
            json_body={"target": {"type": "request", "id": str(MISSING_REQUEST_ID)}},
        ),
        status=404,
        code="ASSISTANT_CONVERSATION_NOT_FOUND",
        title="Assistant conversation not found",
        path=missing_cancel_path,
        forbidden_values=forbidden,
    )
    require(
        "assistantRequestId" not in missing_cancel,
        "cancel error exposed an irrelevant Assistant request ID",
    )
    passed()

    return checks


def verify_database(database_path: Path) -> dict[str, object]:
    with sqlite3.connect(database_path) as connection:
        database_file = Path(connection.execute("PRAGMA database_list").fetchone()[2]).resolve()
        require(database_file == database_path, "verification opened the wrong database")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        schedule_count = connection.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
        deleted_schedule_count = connection.execute(
            "SELECT COUNT(*) FROM schedules WHERE deleted_at IS NOT NULL"
        ).fetchone()[0]
        assistant_count = connection.execute(
            "SELECT COUNT(*) FROM assistant_requests"
        ).fetchone()[0]
        unavailable_count = connection.execute(
            "SELECT COUNT(*) FROM assistant_requests WHERE id = ?",
            (UNAVAILABLE_REQUEST_ID.hex,),
        ).fetchone()[0]
    require(integrity == "ok", "temporary database integrity check failed")
    require(schedule_count == 1, "unexpected Schedule row count")
    require(deleted_schedule_count == 1, "deleted Schedule has no deleted_at value")
    require(assistant_count == 1, "unexpected Assistant request row count")
    require(unavailable_count == 0, "unavailable Assistant request was persisted")
    return {
        "integrity": integrity,
        "alembicRevision": revision,
        "scheduleRows": schedule_count,
        "softDeletedScheduleRows": deleted_schedule_count,
        "assistantRequestRows": assistant_count,
    }


def run() -> dict[str, object]:
    result: dict[str, object]
    with tempfile.TemporaryDirectory(prefix="schedule-api-contract-smoke.") as raw_root:
        temporary_root = Path(raw_root).resolve()
        require(
            not temporary_root.is_relative_to(BACKEND_ROOT),
            "temporary directory must be outside the backend tree",
        )
        database_path = (temporary_root / "smoke.db").resolve()
        require(database_path != PROJECT_DATABASE, "refusing to use the project database")
        backup_dir = temporary_root / "backups"
        backup_dir.mkdir()
        log_path = temporary_root / "uvicorn.log"
        port = choose_loopback_port()
        environment = temporary_environment(database_path, backup_dir, port)

        run_migrations(environment)
        require(database_path.is_file(), "Alembic did not create the temporary database")
        seed_assistant_success(database_path)

        process: subprocess.Popen[bytes] | None = None
        try:
            with log_path.open("wb") as log_file:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "app.main:app",
                        "--host",
                        LOOPBACK,
                        "--port",
                        str(port),
                        "--log-level",
                        "info",
                    ],
                    cwd=BACKEND_ROOT,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                )
                wait_until_ready(process, port, log_path)
                verify_server_identity(process, port)
                checks = 1 + run_http_checks(port, temporary_root, database_path)
        except Exception as exc:
            log_tail = (
                log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                if log_path.exists()
                else ""
            )
            raise SmokeFailure(f"{exc}\nuvicorn log tail:\n{log_tail}") from exc
        finally:
            if process is not None:
                stop_process(process)

        require(
            process is not None and process.returncode == 0,
            "uvicorn did not shut down cleanly",
        )
        database = verify_database(database_path)
        result = {
            "result": "PASS",
            "httpChecks": checks,
            "host": LOOPBACK,
            "temporaryDatabase": True,
            "uvicornExitCode": process.returncode,
            **database,
        }
    return result


def main() -> int:
    try:
        result = run()
    except Exception as exc:
        print(f"API contract smoke FAIL: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
