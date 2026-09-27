from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.core.clock import FixedClock
from app.core.exceptions import ScheduleNotFound
from app.modules.schedules import repository, service
from app.modules.schedules.category import Category
from app.modules.schedules.schemas import ScheduleUpdate
from app.modules.schedules.schemas import ScheduleCreate

NOW = datetime(2026, 9, 13, 4, 30, tzinfo=timezone.utc)


def schedule(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": uuid4(),
        "title": "회의",
        "scheduled_at": datetime(2026, 9, 13, 4, 45, tzinfo=timezone.utc),
        "category": Category.MEETING,
        "completed": False,
        "reminder_minutes_before": 10,
        "created_at": NOW,
        "updated_at": NOW,
        "deleted_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_no_op_patch_does_not_write_or_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    current = schedule()
    session = Mock()
    update = Mock()
    monkeypatch.setattr(repository, "get_active_by_id", lambda *args: current)
    monkeypatch.setattr(repository, "update_active", update)

    result = service.update_schedule(
        session,
        schedule_id=current.id,
        data=ScheduleUpdate.model_validate({"title": "회의"}),
        clock=FixedClock(NOW),
    )

    assert result.updated_at == NOW
    update.assert_not_called()
    session.commit.assert_not_called()


def test_create_reuses_one_clock_value_and_does_not_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Mock()
    clock = Mock()
    clock.now.return_value = NOW
    add = Mock()
    read = Mock()
    monkeypatch.setattr(repository, "add_schedule", add)
    monkeypatch.setattr(repository, "get_active_by_id", read)

    result = service.create_schedule(
        session,
        data=ScheduleCreate.model_validate(
            {
                "title": "회의",
                "scheduledAt": "2026-09-13T04:45:00Z",
                "category": "MEETING",
                "reminderMinutesBefore": None,
            }
        ),
        clock=clock,
    )

    clock.now.assert_called_once_with()
    add.assert_called_once()
    session.commit.assert_called_once_with()
    read.assert_not_called()
    assert result.created_at == result.updated_at == NOW


def test_changed_patch_uses_conditional_row_count_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = schedule()
    session = Mock()
    update = Mock(return_value=1)
    monkeypatch.setattr(repository, "get_active_by_id", lambda *args: current)
    monkeypatch.setattr(repository, "update_active", update)

    result = service.update_schedule(
        session,
        schedule_id=current.id,
        data=ScheduleUpdate.model_validate({"completed": True}),
        clock=FixedClock(NOW),
    )

    assert result.completed is True
    update.assert_called_once_with(
        session,
        schedule_id=current.id,
        changes={"completed": True},
        updated_at=NOW,
    )
    session.commit.assert_called_once_with()


def test_patch_row_count_zero_rolls_back_and_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = schedule()
    session = Mock()
    monkeypatch.setattr(repository, "get_active_by_id", lambda *args: current)
    monkeypatch.setattr(repository, "update_active", lambda *args, **kwargs: 0)

    with pytest.raises(ScheduleNotFound):
        service.update_schedule(
            session,
            schedule_id=current.id,
            data=ScheduleUpdate.model_validate({"completed": True}),
            clock=FixedClock(NOW),
        )
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_delete_row_count_zero_is_concurrent_redelete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = schedule()
    session = Mock()
    monkeypatch.setattr(repository, "get_by_id_including_deleted", lambda *args: current)
    monkeypatch.setattr(repository, "soft_delete_active", lambda *args, **kwargs: 0)

    service.delete_schedule(
        session, schedule_id=current.id, clock=FixedClock(NOW)
    )

    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_redelete_preserves_timestamps_without_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = schedule(deleted_at=NOW)
    session = Mock()
    delete = Mock()
    monkeypatch.setattr(repository, "get_by_id_including_deleted", lambda *args: current)
    monkeypatch.setattr(repository, "soft_delete_active", delete)

    service.delete_schedule(
        session, schedule_id=current.id, clock=FixedClock(NOW)
    )

    delete.assert_not_called()
    session.commit.assert_not_called()
