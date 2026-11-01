"""Opt-in smoke test with a real model server (Issue #29, #37 M2).

Skipped unless ASSISTANT_SMOKE_MODEL_URL points to a running
OpenAI-compatible server with the trained adapter, for example:

    mlx_lm.server --model <model> --adapter-path <adapters> --port 8088 \\
        --temp 0.0 --chat-template-args '{"enable_thinking": false}'
    ASSISTANT_SMOKE_MODEL_URL=http://127.0.0.1:8088 pytest tests/integration/test_assistant_model_smoke.py
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.clock import FixedClock
from app.core.config import Settings
from app.main import create_app
from app.modules.assistant.evidence.normalize import SEOUL
from app.modules.schedules.model import Schedule

SMOKE_URL = os.environ.get("ASSISTANT_SMOKE_MODEL_URL")
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not SMOKE_URL, reason="set ASSISTANT_SMOKE_MODEL_URL to run with a real model"),
]
NOW = datetime(2026, 10, 29, 10, 0, tzinfo=SEOUL)  # Thursday


class Session:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.conversation_id: str | None = None
        self.last: dict = {}
        self.timings: list[tuple[str, float]] = []

    def send(self, input_value: dict, *, label: str) -> dict:
        request_id = str(uuid4())
        payload: dict = {"requestId": request_id, "input": input_value}
        if self.conversation_id:
            payload["conversationId"] = self.conversation_id
        started = time.monotonic()
        response = self.client.post("/api/v1/assistant/requests", json=payload)
        assert response.status_code == 202, response.text
        deadline = started + 240
        while time.monotonic() < deadline:
            body = self.client.get(f"/api/v1/assistant/requests/{request_id}").json()
            if body.get("status") == "completed":
                break
            Event().wait(0.05)
        else:
            raise AssertionError(f"{label}: no result")
        self.timings.append((label, time.monotonic() - started))
        self.conversation_id = body["conversationId"]
        self.last = body["result"]
        return self.last

    def text(self, text: str, *, answer: bool = False) -> dict:
        value: dict = {"type": "text", "text": text}
        if answer and self.last.get("questionId"):
            value["questionId"] = self.last["questionId"]
        return self.send(value, label=text)

    def approve(self) -> dict:
        return self.send(
            {"type": "confirmation", "questionId": self.last["questionId"], "approved": True}, label="approve"
        )


def _rows(application) -> list[Schedule]:
    with application.state.session_factory() as session:
        return list(session.scalars(sa.select(Schedule).where(Schedule.deleted_at.is_(None))))


def _snapshot(application) -> list[tuple[str, str, str]]:
    return sorted(
        (r.title, r.category.value, r.scheduled_at.astimezone(SEOUL).strftime("%m-%d %H:%M")) for r in _rows(application)
    )


def test_real_model_flows(database_path: Path) -> None:
    application = create_app(
        Settings(
            _env_file=None,
            database_url=f"sqlite:///{database_path}",
            assistant_model_enabled=True,
            assistant_model_base_url=SMOKE_URL or "",
            assistant_trust_server_time=True,
            assistant_model_timeout_seconds=120,
        ),
        _assistant_runtime_ready_for_tests=True,
    )
    application.state.clock = FixedClock(NOW)
    try:
        with TestClient(application) as client:
            assert application.state.assistant_provider_available is True
            session = Session(client)

            # Flows the run 006 adapter handles (2026-11-01).
            created = session.text("내일 오후 3시 반에 입고 검수 잡아 줘")
            assert (created["kind"], created.get("outcome")) == ("outcome", "success"), created
            second = session.text("모레 오후 2시에 운영 회의 잡아 줘")
            assert (second["kind"], second.get("outcome")) == ("outcome", "success"), second

            listed = session.text("앞으로 남은 입고 일정 보여 줘")
            assert listed["kind"] == "outcome" and listed["operation"] == "list", listed
            assert [item["title"] for item in listed["items"]] == ["입고 검수"]

            confirm = session.text("입고 검수 일정을 오후 5시로 옮겨 줘")
            assert confirm["kind"] == "confirmation", confirm
            moved = session.approve()
            assert (moved["kind"], moved.get("outcome")) == ("outcome", "success"), moved

            delete = session.text("운영 회의 일정 삭제해 줘")
            assert delete["kind"] == "confirmation", delete
            deleted = session.approve()
            assert (deleted["kind"], deleted.get("outcome")) == ("outcome", "success"), deleted

            expected = [("입고 검수", "INBOUND", "10-30 17:00")]
            assert _snapshot(application) == expected

            # A missing time is asked, then the answer creates.
            session.conversation_id = None
            session.last = {}
            asked = session.text("모레 재고 회의 잡아 줘")
            assert asked["kind"] == "need_more_info", asked
            answered = session.text("오후 2시", answer=True)
            assert (answered["kind"], answered.get("outcome")) == ("outcome", "success"), answered

            # An unsupported reminder asks for consent; the yes is not sent to the model.
            session.conversation_id = None
            session.last = {}
            consent = session.text("모레 오전 9시에 재고 실사 등록하고 알림도 켜 줘")
            assert consent["kind"] == "need_more_info", consent
            granted = session.text("응, 알림 없이 등록해", answer=True)
            assert (granted["kind"], granted.get("outcome")) == ("outcome", "success"), granted

            expected += [("재고 실사", "INVENTORY", "10-31 09:00"), ("재고 회의", "INVENTORY", "10-31 14:00")]
            assert _snapshot(application) == sorted(expected)

            # Two schedules share a title: the user picks one, approves, and only that one goes.
            for text in ("내일 오전 10시에 주간 회의 잡아 줘", "모레 오전 10시에 주간 회의 잡아 줘"):
                session.conversation_id = None
                session.last = {}
                made = session.text(text)
                assert (made["kind"], made.get("outcome")) == ("outcome", "success"), made
            session.conversation_id = None
            session.last = {}
            choose = session.text("주간 회의 일정 삭제해 줘")
            assert choose["kind"] == "candidate_selection", choose
            assert len(choose["candidates"]) == 2
            later = max(choose["candidates"], key=lambda item: item["scheduledAt"])
            confirm = session.send(
                {"type": "selection", "questionId": choose["questionId"], "candidateId": later["candidateId"]},
                label="select",
            )
            assert confirm["kind"] == "confirmation", confirm
            removed = session.approve()
            assert (removed["kind"], removed.get("outcome")) == ("outcome", "success"), removed
            expected.append(("주간 회의", "OTHER", "10-30 10:00"))
            assert _snapshot(application) == sorted(expected)

            # Flows the current adapter is weak at: they may fail, but must never write.
            report = []
            for turns in (["입고 검수 완료로 표시해 줘"],):
                before = _snapshot(application)
                session.conversation_id = None
                session.last = {}
                result = session.text(turns[0])
                for answer in turns[1:]:
                    if result["kind"] != "need_more_info":
                        break
                    result = session.text(answer, answer=True)
                if result["kind"] == "confirmation":
                    result = session.approve()
                after = _snapshot(application)
                succeeded = result.get("outcome") == "success"
                report.append((turns[-1], result["kind"], result.get("outcome"), succeeded))
                if not succeeded:
                    assert after == before, (turns, result)
            with application.state.engine.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
            print("\nweak flows:", report)
            print("\nsmoke timings:", [(label, round(seconds, 1)) for label, seconds in session.timings])
    finally:
        application.state.engine.dispose()
