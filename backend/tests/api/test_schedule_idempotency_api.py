from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.schedules import repository
from app.modules.schedules.model import IdempotencyRecord, Schedule
from tests.api.test_schedule_api import create_payload, create_schedule


def counts(client: TestClient) -> tuple[int, int]:
    with Session(client.app.state.engine) as session:
        schedules = session.scalar(sa.select(sa.func.count()).select_from(Schedule))
        records = session.scalar(
            sa.select(sa.func.count()).select_from(IdempotencyRecord)
        )
    return int(schedules or 0), int(records or 0)


def test_replay_is_byte_exact_and_survives_patch_delete(client: TestClient) -> None:
    key = "snapshot-key"
    first = create_schedule(client, key=key, title="  재고 확인  ")
    first_request_id = first.headers["X-Request-ID"]

    replay = create_schedule(
        client,
        key=key,
        title="재고 확인",
        scheduledAt="2026-09-13T13:45:00+09:00",
    )
    assert replay.status_code == 201
    assert replay.content == first.content
    assert replay.headers["Location"] == first.headers["Location"]
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.headers["X-Request-ID"] != first_request_id
    assert "Idempotent-Replayed" not in first.headers
    assert counts(client) == (1, 1)

    schedule_id = first.json()["id"]
    assert client.patch(
        f"/api/v1/schedules/{schedule_id}", json={"title": "수정된 제목"}
    ).status_code == 200
    assert client.delete(f"/api/v1/schedules/{schedule_id}").status_code == 204

    after_delete = create_schedule(client, key=key)
    assert after_delete.status_code == 201
    assert after_delete.content == first.content
    assert after_delete.headers["Location"] == first.headers["Location"]
    assert after_delete.headers["Idempotent-Replayed"] == "true"
    assert counts(client) == (1, 1)


def test_replay_precedes_business_rule_but_not_body_validation(
    client: TestClient,
) -> None:
    key = "validation-order-key"
    first = create_schedule(client, key=key)
    client.app.state.clock.advance(timedelta(hours=1))

    replay = create_schedule(client, key=key)
    assert replay.status_code == 201
    assert replay.content == first.content
    assert replay.headers["Idempotent-Replayed"] == "true"

    invalid = client.post(
        "/api/v1/schedules",
        json={"title": "재고 확인"},
        headers={"Idempotency-Key": key},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "VALIDATION_ERROR"
    assert counts(client) == (1, 1)


def test_same_key_with_different_payload_returns_conflict(client: TestClient) -> None:
    key = "conflict-key"
    assert create_schedule(client, key=key).status_code == 201

    conflict = create_schedule(client, key=key, title="다른 일정")

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert conflict.headers["content-type"].startswith("application/problem+json")
    assert conflict.json()["requestId"] == conflict.headers["X-Request-ID"]
    assert counts(client) == (1, 1)


def test_different_keys_do_not_change_request_fingerprint(client: TestClient) -> None:
    assert create_schedule(client, key="first-key").status_code == 201
    assert create_schedule(client, key="second-key").status_code == 201

    with Session(client.app.state.engine) as session:
        records = list(
            session.scalars(
                sa.select(IdempotencyRecord).order_by(
                    IdempotencyRecord.idempotency_key
                )
            )
        )
    assert len(records) == 2
    assert records[0].fingerprint == records[1].fingerprint


def test_expired_key_is_reused_at_exact_24_hour_boundary(
    client: TestClient,
) -> None:
    key = "expiry-key"
    first = create_schedule(client, key=key)
    assert first.status_code == 201

    client.app.state.clock.advance(timedelta(hours=24) - timedelta(microseconds=1))
    before_boundary = create_schedule(
        client,
        key=key,
        title="새 일정",
        scheduledAt="2026-09-14T04:45:00Z",
    )
    assert before_boundary.status_code == 409

    client.app.state.clock.advance(timedelta(microseconds=1))
    at_boundary = create_schedule(
        client,
        key=key,
        title="새 일정",
        scheduledAt="2026-09-14T04:45:00Z",
    )
    assert at_boundary.status_code == 201
    assert at_boundary.json()["id"] != first.json()["id"]
    assert "Idempotent-Replayed" not in at_boundary.headers
    assert counts(client) == (2, 1)

    with Session(client.app.state.engine) as session:
        record = session.get(IdempotencyRecord, ("default", key))
        assert record is not None
        assert record.schedule_id.hex == at_boundary.json()["id"].replace("-", "")
        assert record.expires_at == client.app.state.clock.now() + timedelta(hours=24)


def test_4xx_and_rollback_5xx_do_not_store_records(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rejected = create_schedule(
        client,
        key="past-key",
        scheduledAt="2026-09-13T04:29:59Z",
    )
    assert rejected.status_code == 422
    assert counts(client) == (0, 0)

    original = sqlite3.OperationalError("database is locked")
    original.sqlite_errorcode = sqlite3.SQLITE_BUSY
    failure = sa.exc.OperationalError("insert", {}, original)

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(repository, "add_idempotency_record", fail)
    unavailable = create_schedule(client, key="rollback-key")
    assert unavailable.status_code == 503
    assert unavailable.json()["code"] == "DATABASE_UNAVAILABLE"
    assert counts(client) == (0, 0)


@pytest.mark.parametrize(
    ("lookup_failure", "expected_status", "expected_code"),
    [
        (None, 500, "INTERNAL_ERROR"),
        ("busy", 503, "DATABASE_UNAVAILABLE"),
    ],
)
def test_integrity_race_requery_failure_maps_to_problem_details(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    lookup_failure: str | None,
    expected_status: int,
    expected_code: str,
) -> None:
    integrity = sa.exc.IntegrityError(
        "insert", {}, sqlite3.IntegrityError("constraint failed")
    )
    busy = sqlite3.OperationalError("database is locked")
    busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
    unavailable = sa.exc.OperationalError("select", {}, busy)
    lookups = 0

    def find(*args, **kwargs):
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            return None
        if lookup_failure == "busy":
            raise unavailable
        return None

    def fail_insert(*args, **kwargs):
        raise integrity

    monkeypatch.setattr(repository, "find_active_idempotency", find)
    monkeypatch.setattr(repository, "add_idempotency_record", fail_insert)

    response = create_schedule(client, key="race-problem-key")

    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code
    assert response.json()["requestId"] == response.headers["X-Request-ID"]
    assert lookups == 2
    assert counts(client) == (0, 0)


def test_success_persists_schedule_and_snapshot_in_one_commit(
    client: TestClient,
) -> None:
    response = create_schedule(client, key="stored-key")

    with Session(client.app.state.engine) as session:
        schedule = session.scalar(sa.select(Schedule))
        record = session.get(IdempotencyRecord, ("default", "stored-key"))
        assert schedule is not None
        assert record is not None
        assert record.scope == "default"
        assert record.schedule_id == schedule.id
        assert record.response_body.encode() == response.content
        assert record.created_at == schedule.created_at
        assert record.expires_at == record.created_at + timedelta(hours=24)
        assert len(record.fingerprint) == 64
