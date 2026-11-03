"""Non-circular API-to-database checks for intentionally wrong model output.

The fixture contains human-authored expected API results and complete final
Schedule rows.  This module deliberately does not import the evidence
normalizer or planner used by production to compute those results.
"""

from __future__ import annotations

import ast
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.clock import FixedClock
from app.core.config import Settings
from app.main import create_app
from app.modules.assistant.evidence.provider import EvidenceDecisionProvider
from app.modules.assistant.schedule_read import ScheduleReadPort, SqlAlchemyScheduleReader
from app.modules.schedules.model import Schedule
from tests.conftest import migrate

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "assistant_model_output_safety_v1.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
SEOUL = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 29, 10, 0, tzinfo=SEOUL)


class _Trust:
    def is_trusted(self) -> bool:
        return True


class _LazyReader:
    def __init__(self) -> None:
        self.inner: ScheduleReadPort | None = None

    def get(self, schedule_id):
        assert self.inner is not None
        return self.inner.get(schedule_id)

    def list(self, **kwargs):
        assert self.inner is not None
        return self.inner.list(**kwargs)


class _FixedModel:
    def __init__(self, model_output: dict) -> None:
        self.model_output = model_output
        self.calls = 0

    def extract(self, **kwargs) -> str:
        self.calls += 1
        return json.dumps(self.model_output, ensure_ascii=False)


def _wait(client: TestClient, request_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/assistant/requests/{request_id}").json()
        if body.get("status") == "completed":
            return body
        Event().wait(0.005)
    raise AssertionError("request did not complete")


def _schedule_rows(application) -> list[dict[str, object]]:
    with application.state.session_factory() as session:
        rows = session.scalars(
            sa.select(Schedule)
            .where(Schedule.deleted_at.is_(None))
            .order_by(Schedule.scheduled_at, Schedule.title)
        ).all()
    return [
        {
            "title": row.title,
            "scheduledAt": row.scheduled_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "category": row.category.value,
            "completed": row.completed,
        }
        for row in rows
    ]


@pytest.mark.parametrize("case", CASES, ids=[case["caseId"] for case in CASES])
def test_wrong_model_output_never_writes_unapproved_schedule(
    case: dict, tmp_path: Path
) -> None:
    model = _FixedModel(case["modelOutput"])
    reader = _LazyReader()
    provider = EvidenceDecisionProvider(model, reader)
    database = tmp_path / (case["caseId"] + ".db")
    migrate(database)
    application = create_app(
        Settings(
            _env_file=None,
            database_url=f"sqlite:///{database}",
        ),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=provider,
        _assistant_time_trust_source_for_tests=_Trust(),
        _assistant_uuid_factory_for_tests=uuid4,
    )
    application.state.clock = FixedClock(NOW)
    try:
        with TestClient(application) as client:
            reader.inner = SqlAlchemyScheduleReader(application.state.session_factory)
            request_id = str(uuid4())
            response = client.post(
                "/api/v1/assistant/requests",
                json={
                    "requestId": request_id,
                    "input": {"type": "text", "text": case["input"]},
                },
            )
            assert response.status_code == 202
            result = _wait(client, request_id)["result"]
            for key, expected in case["expectedResult"].items():
                assert result.get(key) == expected, (case["caseId"], key, result)
            assert _schedule_rows(application) == case["expectedSchedules"]
        assert model.calls == case["expectedModelCalls"]
    finally:
        application.state.engine.dispose()


def test_safety_fixture_is_independent_of_production_value_interpreters() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "app.modules.assistant.evidence.normalize" not in imported
    assert "app.modules.assistant.evidence.planner" not in imported
