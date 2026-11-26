from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.core.clock import FixedClock
from app.core.exceptions import DatabaseUnavailable, IdempotencyKeyConflict
from app.modules.schedules import repository, service
from app.modules.schedules.idempotency import canonical_fingerprint
from app.modules.schedules.model import IdempotencyRecord
from app.modules.schedules.schemas import ScheduleCreate

NOW = datetime(2026, 9, 13, 4, 30, tzinfo=timezone.utc)


def create_data(**overrides: object) -> ScheduleCreate:
    payload: dict[str, object] = {
        "title": "회의",
        "scheduledAt": "2026-09-13T04:45:00Z",
        "category": "OUTBOUND",
    }
    payload.update(overrides)
    return ScheduleCreate.model_validate(payload)


def record(data: ScheduleCreate, *, fingerprint: str | None = None) -> IdempotencyRecord:
    schedule_id = uuid4()
    return IdempotencyRecord(
        scope="default",
        idempotency_key="race-key",
        fingerprint=fingerprint or canonical_fingerprint(data),
        schedule_id=schedule_id,
        response_body=f'{{"id":"{schedule_id}","snapshot":true}}',
        created_at=NOW,
        expires_at=NOW + timedelta(hours=24),
    )


def integrity_error() -> sa.exc.IntegrityError:
    return sa.exc.IntegrityError(
        "insert", {}, sqlite3.IntegrityError("UNIQUE constraint failed")
    )


def test_integrity_race_rolls_back_then_replays_with_same_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = create_data()
    winner = record(data)
    lookups: list[datetime] = []

    def find(*args, now: datetime, **kwargs):
        lookups.append(now)
        return None if len(lookups) == 1 else winner

    session = Mock()
    session.commit.side_effect = integrity_error()
    monkeypatch.setattr(repository, "find_active_idempotency", find)
    monkeypatch.setattr(repository, "delete_expired_idempotency", Mock())
    monkeypatch.setattr(repository, "add_schedule", Mock())
    monkeypatch.setattr(repository, "add_idempotency_record", Mock())

    result = service.create_schedule(
        session,
        data=data,
        idempotency_key="race-key",
        clock=FixedClock(NOW),
    )

    session.rollback.assert_called_once_with()
    assert lookups == [NOW, NOW]
    assert result.replayed is True
    assert result.schedule_id == winner.schedule_id
    assert result.body_json == winner.response_body


def test_integrity_race_with_different_fingerprint_returns_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = create_data()
    session = Mock()
    session.commit.side_effect = integrity_error()
    monkeypatch.setattr(
        repository,
        "find_active_idempotency",
        Mock(side_effect=[None, record(data, fingerprint="0" * 64)]),
    )
    monkeypatch.setattr(repository, "delete_expired_idempotency", Mock())
    monkeypatch.setattr(repository, "add_schedule", Mock())
    monkeypatch.setattr(repository, "add_idempotency_record", Mock())

    with pytest.raises(IdempotencyKeyConflict):
        service.create_schedule(
            session,
            data=data,
            idempotency_key="race-key",
            clock=FixedClock(NOW),
        )
    session.rollback.assert_called_once_with()


def test_integrity_race_without_record_remains_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = integrity_error()
    session = Mock()
    session.commit.side_effect = failure
    monkeypatch.setattr(
        repository, "find_active_idempotency", Mock(side_effect=[None, None])
    )
    monkeypatch.setattr(repository, "delete_expired_idempotency", Mock())
    monkeypatch.setattr(repository, "add_schedule", Mock())
    monkeypatch.setattr(repository, "add_idempotency_record", Mock())

    with pytest.raises(sa.exc.IntegrityError) as captured:
        service.create_schedule(
            session,
            data=create_data(),
            idempotency_key="race-key",
            clock=FixedClock(NOW),
        )
    assert captured.value is failure
    session.rollback.assert_called_once_with()


def test_integrity_race_requery_uses_database_unavailable_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    locked = sqlite3.OperationalError("database is locked")
    locked.sqlite_errorcode = sqlite3.SQLITE_BUSY
    database_error = sa.exc.OperationalError("select", {}, locked)
    session = Mock()
    session.commit.side_effect = integrity_error()
    monkeypatch.setattr(
        repository,
        "find_active_idempotency",
        Mock(side_effect=[None, database_error]),
    )
    monkeypatch.setattr(repository, "delete_expired_idempotency", Mock())
    monkeypatch.setattr(repository, "add_schedule", Mock())
    monkeypatch.setattr(repository, "add_idempotency_record", Mock())

    with pytest.raises(DatabaseUnavailable):
        service.create_schedule(
            session,
            data=create_data(),
            idempotency_key="race-key",
            clock=FixedClock(NOW),
        )
    assert session.rollback.call_count == 2


def test_active_replay_precedes_past_schedule_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = create_data(scheduledAt="2026-09-13T04:29:59Z")
    winner = record(data)
    session = Mock()
    monkeypatch.setattr(
        repository, "find_active_idempotency", Mock(return_value=winner)
    )
    add_schedule = Mock()
    add_record = Mock()
    delete_expired = Mock()
    monkeypatch.setattr(repository, "add_schedule", add_schedule)
    monkeypatch.setattr(repository, "add_idempotency_record", add_record)
    monkeypatch.setattr(repository, "delete_expired_idempotency", delete_expired)

    result = service.create_schedule(
        session,
        data=data,
        idempotency_key="race-key",
        clock=FixedClock(NOW),
    )

    assert result.replayed is True
    add_schedule.assert_not_called()
    add_record.assert_not_called()
    delete_expired.assert_not_called()
    session.commit.assert_not_called()
