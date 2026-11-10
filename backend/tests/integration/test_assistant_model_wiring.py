"""The app connects the model server only when asked, and survives without it."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

import sqlalchemy as sa
import pytest
from fastapi.testclient import TestClient

from app.core.clock import FixedClock
from app.core.config import Settings
from app.main import create_app
from app.modules.assistant.evidence import model_client
from app.modules.assistant.evidence.model_client import (
    EXPECTED_TRAINING_PROMPT_SHA256,
    ChatCompletionsModel,
)
from app.modules.assistant.evidence.normalize import SEOUL
from app.modules.assistant.model import AssistantRequest
from app.modules.schedules.model import Schedule
from app.modules.system.time_sync import DisabledTimeSyncAdapter
from tests.unit.test_assistant_evidence_model_client import FakeServer

from datetime import datetime

NOW = datetime(2026, 10, 29, 10, 0, tzinfo=SEOUL)
CREATE_OUTPUT = {
    "operations": [
        {
            "index": 1,
            "kind": "create",
            "targetEvidence": {},
            "changeEvidence": {
                "title": [{"turn": 1, "text": "입고 검수", "status": "adopted"}],
                "date": [{"turn": 1, "text": "내일", "status": "adopted"}],
                "time": [{"turn": 1, "text": "오후 3시 반", "status": "adopted"}],
                "category": [{"turn": 1, "text": "입고", "status": "adopted"}],
            },
            "filterEvidence": {},
            "unsupportedEvidence": [],
            "missingInformation": [],
        }
    ],
    "unsupportedEvidence": [],
    "missingInformation": [],
}


def _app(database_path: Path, **settings):
    application = create_app(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}", **settings),
        _assistant_runtime_ready_for_tests=True,
    )
    application.state.clock = FixedClock(NOW)
    return application


def _wait(client: TestClient, request_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/assistant/requests/{request_id}").json()
        if body.get("status") == "completed":
            return body
        Event().wait(0.005)
    raise AssertionError("request did not complete")


def test_model_server_connects_and_creates(database_path: Path) -> None:
    server = FakeServer()
    server.body = {"choices": [{"message": {"content": json.dumps(CREATE_OUTPUT, ensure_ascii=False)}}]}
    application = _app(
        database_path,
        assistant_model_enabled=True,
        assistant_model_base_url=server.url,
        assistant_trust_server_time=True,
    )
    try:
        with TestClient(application) as client:
            assert application.state.assistant_provider_available is True
            request_id = str(uuid4())
            response = client.post(
                "/api/v1/assistant/requests",
                json={"requestId": request_id, "input": {"type": "text", "text": "내일 오후 3시 반에 입고 검수 잡아 줘"}},
            )
            assert response.status_code == 202
            result = _wait(client, request_id)["result"]
            assert (result["kind"], result["outcome"], result["operation"]) == ("outcome", "success", "create")
            with application.state.session_factory() as session:
                row = session.scalars(sa.select(Schedule)).one()
            assert row.title == "입고 검수"
            assert row.scheduled_at.astimezone(SEOUL) == datetime(2026, 10, 30, 15, 30, tzinfo=SEOUL)
        user_message = server.requests[0]["body"]["messages"][1]["content"]
        assert '"serverTimeTrusted":true' in user_message
        assert '"taskReferenceTime":"2026-10-29T10:00:00+09:00"' in user_message
    finally:
        server.close()
        application.state.engine.dispose()


def test_phone_time_sync_lets_the_model_use_relative_dates(database_path: Path) -> None:
    server = FakeServer()
    server.body = {"choices": [{"message": {"content": json.dumps(CREATE_OUTPUT, ensure_ascii=False)}}]}
    application = _app(database_path, assistant_model_enabled=True, assistant_model_base_url=server.url)
    application.state.time_sync_adapter = DisabledTimeSyncAdapter(application.state.clock)
    try:
        with TestClient(application) as client:
            synced = client.post("/api/v1/system/time-sync", json={"currentTime": "2026-10-29T10:00:10+09:00"})
            assert synced.status_code == 200
            request_id = str(uuid4())
            client.post(
                "/api/v1/assistant/requests",
                json={"requestId": request_id, "input": {"type": "text", "text": "내일 오후 3시 반에 입고 검수 잡아 줘"}},
            )
            result = _wait(client, request_id)["result"]
            assert (result["kind"], result["outcome"]) == ("outcome", "success")
        assert '"serverTimeTrusted":true' in server.requests[0]["body"]["messages"][1]["content"]
    finally:
        server.close()
        application.state.engine.dispose()


def test_model_server_down_keeps_assistant_unavailable(database_path: Path) -> None:
    application = _app(database_path, assistant_model_enabled=True, assistant_model_base_url="http://127.0.0.1:9")
    try:
        with TestClient(application) as client:
            assert application.state.assistant_provider_available is False
            response = client.post(
                "/api/v1/assistant/requests",
                json={"requestId": str(uuid4()), "input": {"type": "text", "text": "회의 잡아 줘"}},
            )
            assert response.status_code == 503
            assert client.get("/health").status_code == 200
    finally:
        application.state.engine.dispose()


@pytest.mark.parametrize(
    ("prompt_contents", "expected_sha256", "expected_exception_class"),
    [
        (
            b"not the training prompt",
            hashlib.sha256(b"not the training prompt").hexdigest(),
            "TrainingPromptIntegrityError",
        ),
        (None, None, "FileNotFoundError"),
    ],
)
def test_invalid_training_prompt_stays_unavailable_before_model_readiness(
    database_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    prompt_contents: bytes | None,
    expected_sha256: str | None,
    expected_exception_class: str,
) -> None:
    damaged_prompt = tmp_path / "prompt.txt"
    if prompt_contents is not None:
        damaged_prompt.write_bytes(prompt_contents)
    monkeypatch.setattr(model_client, "PROMPT_PATH", damaged_prompt)
    readiness_calls = 0

    def count_readiness(_model: ChatCompletionsModel) -> bool:
        nonlocal readiness_calls
        readiness_calls += 1
        return True

    monkeypatch.setattr(ChatCompletionsModel, "is_ready", count_readiness)
    caplog.set_level(logging.WARNING)
    application = _app(
        database_path,
        assistant_model_enabled=True,
        assistant_model_base_url="http://127.0.0.1:9",
    )
    try:
        with TestClient(application) as client:
            assert application.state.assistant_provider_available is False
            response = client.post(
                "/api/v1/assistant/requests",
                json={
                    "requestId": str(uuid4()),
                    "input": {"type": "text", "text": "회의 잡아 줘"},
                },
            )
            assert response.status_code == 503
            assert response.json()["code"] == "ASSISTANT_UNAVAILABLE"
            assert client.get("/health").status_code == 200
            assert readiness_calls == 0
            with application.state.assistant_session_factory() as session:
                assert session.scalar(sa.select(sa.func.count()).select_from(AssistantRequest)) == 0
            with application.state.session_factory() as session:
                assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0

            schedule = client.post(
                "/api/v1/schedules",
                headers={"Idempotency-Key": "prompt-integrity-schedule"},
                json={
                    "title": "Prompt integrity isolation",
                    "scheduledAt": "2026-11-01T09:00:00Z",
                    "category": "OTHER",
                },
            )
            assert schedule.status_code == 201
            with application.state.session_factory() as session:
                assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 1
            with application.state.assistant_session_factory() as session:
                assert session.scalar(sa.select(sa.func.count()).select_from(AssistantRequest)) == 0

        failures = [
            record
            for record in caplog.records
            if getattr(record, "event", None) == "assistant.prompt_integrity_failed"
        ]
        assert len(failures) == 2  # startup and the first text request's reconnect attempt
        assert {record.promptSha256 for record in failures} == {expected_sha256}
        assert {record.exceptionClass for record in failures} == {
            expected_exception_class
        }
        assert str(damaged_prompt) not in caplog.text
    finally:
        application.state.engine.dispose()


def test_first_request_reconnects_a_model_server_that_started_late(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    readiness = iter((False, True))
    monkeypatch.setattr(ChatCompletionsModel, "is_ready", lambda self: next(readiness))
    monkeypatch.setattr(
        ChatCompletionsModel,
        "extract",
        lambda self, **kwargs: json.dumps(CREATE_OUTPUT, ensure_ascii=False),
    )
    application = _app(
        database_path,
        assistant_model_enabled=True,
        assistant_model_base_url="http://127.0.0.1:9",
        assistant_trust_server_time=True,
    )
    try:
        with TestClient(application) as client:
            assert application.state.assistant_provider_available is False
            request_id = str(uuid4())
            response = client.post(
                "/api/v1/assistant/requests",
                json={
                    "requestId": request_id,
                    "input": {"type": "text", "text": "내일 오후 3시 반에 입고 검수 잡아 줘"},
                },
            )
            assert response.status_code == 202
            assert application.state.assistant_provider_available is True
            result = _wait(client, request_id)["result"]
            assert (result["kind"], result["outcome"], result["operation"]) == (
                "outcome",
                "success",
                "create",
            )
        calls = [
            record
            for record in caplog.records
            if getattr(record, "event", None) == "assistant.model_call"
        ]
        assert calls
        assert {record.promptSha256 for record in calls} == {
            EXPECTED_TRAINING_PROMPT_SHA256
        }
    finally:
        application.state.engine.dispose()


def test_model_is_off_by_default(database_path: Path) -> None:
    application = _app(database_path)
    try:
        with TestClient(application):
            assert application.state.assistant_provider_available is False
            assert application.state.assistant_time_trust_source.is_trusted() is False
    finally:
        application.state.engine.dispose()
