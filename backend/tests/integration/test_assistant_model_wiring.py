"""The app connects the model server only when asked, and survives without it."""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.clock import FixedClock
from app.core.config import Settings
from app.main import create_app
from app.modules.assistant.evidence.normalize import SEOUL
from app.modules.schedules.model import Schedule
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


def test_model_is_off_by_default(database_path: Path) -> None:
    application = _app(database_path)
    try:
        with TestClient(application):
            assert application.state.assistant_provider_available is False
            assert application.state.assistant_time_trust_source.is_trusted() is False
    finally:
        application.state.engine.dispose()
