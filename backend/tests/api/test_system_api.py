from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.clock import FixedClock
from app.core.config import Settings
from app.main import create_app
from app.modules.schedules.model import IdempotencyRecord, Schedule
from app.modules.system import service as system_service
from app.modules.system.time_sync import TimeSyncAdapterError
from tests.api.test_schedule_api import create_schedule
from tests.conftest import FIXED_NOW


class FakeTimeSyncAdapter:
    def __init__(
        self,
        current: datetime,
        *,
        ntp_active: bool = False,
        failure: str | None = None,
    ) -> None:
        self.current = current
        self.ntp_active = ntp_active
        self.failure = failure
        self.calls: list[object] = []

    def read_time(self) -> datetime:
        self.calls.append("read")
        if self.failure == "read":
            raise TimeSyncAdapterError("read failed")
        return self.current

    def is_ntp_active(self, *, timeout_seconds: float) -> bool:
        self.calls.append("ntp")
        if self.failure == "ntp":
            raise TimeSyncAdapterError("NTP failed")
        return self.ntp_active

    def set_time(self, target: datetime, *, timeout_seconds: float) -> None:
        self.calls.append(("set", target))
        if self.failure == "set":
            raise TimeSyncAdapterError("set failed")
        self.current = target


def assert_request_id(response) -> None:
    request_id = UUID(response.headers["X-Request-ID"])
    assert request_id.version == 4
    if response.headers.get("content-type", "").startswith(
        "application/problem+json"
    ):
        assert response.json()["requestId"] == str(request_id)


def health_for_path(path: Path):
    app = create_app(Settings(_env_file=None, database_url=f"sqlite:///{path}"))
    app.state.clock = FixedClock(FIXED_NOW)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/health")
    app.state.engine.dispose()
    return response


def test_health_returns_expected_head_and_shared_clock(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "apiVersion": "v1",
        "serverTime": "2026-09-13T04:30:00Z",
    }
    assert_request_id(response)


def test_health_missing_database_is_503_without_creation(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    response = health_for_path(missing)

    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert not missing.exists()
    assert_request_id(response)


@pytest.mark.parametrize("schema", ["empty", "without-version"])
def test_health_empty_or_missing_alembic_version_is_503(
    tmp_path: Path, schema: str
) -> None:
    database = tmp_path / f"{schema}.db"
    with sqlite3.connect(database) as connection:
        if schema == "without-version":
            connection.execute("CREATE TABLE placeholder (id INTEGER PRIMARY KEY)")

    response = health_for_path(database)

    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert_request_id(response)


def test_health_old_or_multiple_current_heads_are_503(
    database_path: Path,
) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE alembic_version SET version_num = ?", ("old_revision",)
        )
    old = health_for_path(database_path)
    assert old.status_code == 503

    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM alembic_version")
        connection.executemany(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            [("20260927_0001",), ("unexpected_second_head",)],
        )
    multiple = health_for_path(database_path)
    assert multiple.status_code == 503


def test_multiple_expected_migration_heads_fail_startup_resolution() -> None:
    class MultipleHeads:
        def get_heads(self):
            return ["first", "second"]

    with pytest.raises(RuntimeError, match="exactly one"):
        system_service.resolve_expected_head(scripts=MultipleHeads())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [("io", 503, "DATABASE_UNAVAILABLE"), ("bug", 500, "INTERNAL_ERROR")],
)
def test_health_database_io_and_unexpected_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    status: int,
    code: str,
) -> None:
    if failure == "io":
        original = sqlite3.OperationalError("disk I/O error")
        original.sqlite_errorcode = sqlite3.SQLITE_IOERR
        error: Exception = sa.exc.OperationalError("select", {}, original)
    else:
        error = RuntimeError("private implementation detail")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(system_service.MigrationContext, "configure", fail)
    response = client.get("/health")

    assert response.status_code == status
    assert response.json()["code"] == code
    assert "private implementation detail" not in response.text
    assert_request_id(response)


def test_time_sync_no_op_uses_no_idempotency_key_or_ntp(client: TestClient) -> None:
    adapter = FakeTimeSyncAdapter(FIXED_NOW, ntp_active=True)
    client.app.state.time_sync_adapter = adapter

    response = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2026-09-13T13:30:00+09:00"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "adjusted": False,
        "previousServerTime": "2026-09-13T04:30:00Z",
        "serverTime": "2026-09-13T04:30:00Z",
    }
    assert adapter.calls == ["read"]
    assert_request_id(response)


def test_time_sync_adjusts_with_fake_adapter_and_does_not_touch_db(
    client: TestClient,
) -> None:
    assert create_schedule(client, key="system-db-boundary").status_code == 201
    with Session(client.app.state.engine) as session:
        before = (
            session.scalar(sa.select(sa.func.count()).select_from(Schedule)),
            session.scalar(
                sa.select(sa.func.count()).select_from(IdempotencyRecord)
            ),
        )

    adapter = FakeTimeSyncAdapter(FIXED_NOW - timedelta(minutes=5))
    client.app.state.settings.time_sync_enabled = True
    client.app.state.time_sync_adapter = adapter
    response = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2026-09-13T04:30:00Z"},
    )

    assert response.status_code == 200
    assert response.json()["adjusted"] is True
    assert response.json()["previousServerTime"] == "2026-09-13T04:25:00Z"
    assert adapter.calls[0:2] == ["read", "ntp"]
    assert isinstance(adapter.calls[2], tuple)
    assert adapter.calls[3] == "read"
    assert_request_id(response)

    with Session(client.app.state.engine) as session:
        after = (
            session.scalar(sa.select(sa.func.count()).select_from(Schedule)),
            session.scalar(
                sa.select(sa.func.count()).select_from(IdempotencyRecord)
            ),
        )
    assert after == before == (1, 1)


def test_time_sync_disabled_and_ntp_active_problem_contract(
    client: TestClient,
) -> None:
    disabled_adapter = FakeTimeSyncAdapter(
        FIXED_NOW - timedelta(minutes=5), ntp_active=True
    )
    client.app.state.time_sync_adapter = disabled_adapter
    disabled = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2026-09-13T04:30:00Z"},
    )
    assert disabled.status_code == 503
    assert disabled.json()["code"] == "TIME_SYNC_UNAVAILABLE"
    assert disabled_adapter.calls == ["read"]
    assert_request_id(disabled)

    client.app.state.settings.time_sync_enabled = True
    ntp_adapter = FakeTimeSyncAdapter(
        FIXED_NOW - timedelta(minutes=5), ntp_active=True
    )
    client.app.state.time_sync_adapter = ntp_adapter
    ntp = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2026-09-13T04:30:00Z"},
    )
    assert ntp.status_code == 409
    assert ntp.json()["code"] == "CLOCK_AUTO_SYNC_ACTIVE"
    assert ntp_adapter.calls == ["read", "ntp"]
    assert_request_id(ntp)


def test_time_sync_lock_and_adapter_failures_are_503(client: TestClient) -> None:
    client.app.state.settings.time_sync_enabled = True
    client.app.state.settings.time_sync_lock_timeout_seconds = 0.001
    client.app.state.time_sync_adapter = FakeTimeSyncAdapter(
        FIXED_NOW - timedelta(minutes=5)
    )
    client.app.state.time_sync_lock.acquire()
    try:
        locked = client.post(
            "/api/v1/system/time-sync",
            json={"currentTime": "2026-09-13T04:30:00Z"},
        )
    finally:
        client.app.state.time_sync_lock.release()
    assert locked.status_code == 503
    assert locked.json()["code"] == "TIME_SYNC_UNAVAILABLE"
    assert_request_id(locked)

    client.app.state.time_sync_adapter = FakeTimeSyncAdapter(
        FIXED_NOW - timedelta(minutes=5), failure="set"
    )
    failed = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2026-09-13T04:30:00Z"},
    )
    assert failed.status_code == 503
    assert failed.json()["code"] == "TIME_SYNC_UNAVAILABLE"
    assert_request_id(failed)

    client.app.state.time_sync_adapter = FakeTimeSyncAdapter(FIXED_NOW)
    recovered = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2026-09-13T04:30:00Z"},
    )
    assert recovered.status_code == 200


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "REQUIRED"),
        ({"currentTime": "2026-09-13T04:30:00"}, "TIMEZONE_REQUIRED"),
        ({"currentTime": "2026-09-13T04:30:00.1234567Z"}, "INVALID_FORMAT"),
        ({"currentTime": 1}, "INVALID_FORMAT"),
        (
            {"currentTime": "2026-09-13T04:30:00Z", "unknown": True},
            "UNKNOWN_FIELD",
        ),
    ],
)
def test_time_sync_strict_body_validation(
    client: TestClient, payload: object, expected: str
) -> None:
    client.app.state.time_sync_adapter = FakeTimeSyncAdapter(FIXED_NOW)
    response = client.post("/api/v1/system/time-sync", json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["errors"][0]["code"] == expected
    assert_request_id(response)


def test_time_sync_content_type_json_shape_and_range_errors(
    client: TestClient,
) -> None:
    client.app.state.time_sync_adapter = FakeTimeSyncAdapter(FIXED_NOW)
    missing_type = client.post(
        "/api/v1/system/time-sync", content=b'{"currentTime":"2026-09-13T04:30:00Z"}'
    )
    assert missing_type.status_code == 422
    assert missing_type.json()["errors"][0]["code"] == "INVALID_TYPE"

    scalar = client.post("/api/v1/system/time-sync", json=[1, 2])
    assert scalar.status_code == 422
    assert scalar.json()["errors"][0]["pointer"] == ""

    malformed = client.post(
        "/api/v1/system/time-sync",
        content=b'{"currentTime":',
        headers={"Content-Type": "application/json"},
    )
    assert malformed.status_code == 400
    assert malformed.json()["code"] == "MALFORMED_REQUEST"

    outside = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2035-01-01T00:00:00Z"},
    )
    assert outside.status_code == 422
    assert outside.json()["code"] == "CLOCK_TIME_OUT_OF_RANGE"


def test_time_sync_does_not_replay_stale_payload(client: TestClient) -> None:
    adapter = FakeTimeSyncAdapter(FIXED_NOW - timedelta(minutes=5))
    client.app.state.settings.time_sync_enabled = True
    client.app.state.time_sync_adapter = adapter
    payload = {"currentTime": "2026-09-13T04:30:00Z"}

    first = client.post("/api/v1/system/time-sync", json=payload)
    adapter.current = FIXED_NOW - timedelta(minutes=5)
    second = client.post("/api/v1/system/time-sync", json=payload)

    assert first.status_code == second.status_code == 200
    assert first.json()["adjusted"] is second.json()["adjusted"] is True
    assert sum(1 for call in adapter.calls if isinstance(call, tuple)) == 2


def test_system_routes_have_exact_405_allow(client: TestClient) -> None:
    health = client.post("/health")
    sync = client.get("/api/v1/system/time-sync")

    assert health.status_code == sync.status_code == 405
    assert set(health.headers["Allow"].split(", ")) == {"GET"}
    assert set(sync.headers["Allow"].split(", ")) == {"POST"}
    assert_request_id(health)
    assert_request_id(sync)


def test_time_sync_controls_assistant_time_trust(client: TestClient) -> None:
    trust = client.app.state.assistant_time_trust_source
    assert trust.is_trusted() is False

    client.app.state.time_sync_adapter = FakeTimeSyncAdapter(FIXED_NOW)
    synced = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2026-09-13T04:30:00Z"},
    )
    assert synced.status_code == 200
    assert trust.is_trusted() is True

    # The Pi clock is off by 5 minutes and cannot be fixed.
    client.app.state.time_sync_adapter = FakeTimeSyncAdapter(
        FIXED_NOW - timedelta(minutes=5)
    )
    failed = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2026-09-13T04:30:00Z"},
    )
    assert failed.status_code == 503
    assert trust.is_trusted() is False

    # Bad phone input says nothing about the Pi clock.
    client.app.state.time_sync_adapter = FakeTimeSyncAdapter(FIXED_NOW)
    client.post("/api/v1/system/time-sync", json={"currentTime": "2026-09-13T04:30:00Z"})
    out_of_range = client.post(
        "/api/v1/system/time-sync",
        json={"currentTime": "2040-01-01T00:00:00Z"},
    )
    assert out_of_range.status_code == 422
    assert trust.is_trusted() is True
