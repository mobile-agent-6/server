from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest

from app.core.config import Settings
from app.core.database import create_api_engine, create_session_factory
from app.modules.schedules import repository
from app.modules.schedules.category import Category
from app.modules.schedules.model import Schedule

NOW = datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(minutes=5)
SCHEDULE_ID = UUID("77777777-7777-4777-8777-777777777777")


@pytest.fixture
def schedule_store(database_path: Path):
    engine = create_api_engine(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    )
    factory = create_session_factory(engine)
    with factory.begin() as session:
        session.add(
            Schedule(
                id=SCHEDULE_ID,
                title="재고 확인",
                scheduled_at=NOW + timedelta(hours=1),
                category=Category.INVENTORY,
                completed=False,
                created_at=OLD,
                updated_at=OLD,
                deleted_at=None,
            )
        )
    yield factory
    engine.dispose()


def test_conditional_update_matches_expected_timestamp_and_never_commits(
    schedule_store,
) -> None:
    with schedule_store() as session:
        assert (
            repository.update_active_if_unchanged(
                session,
                schedule_id=SCHEDULE_ID,
                expected_updated_at=OLD,
                changes={"completed": True},
                updated_at=NOW,
            )
            == 1
        )
        session.rollback()
    with schedule_store() as session:
        assert session.get(Schedule, SCHEDULE_ID).completed is False
        assert (
            repository.update_active_if_unchanged(
                session,
                schedule_id=SCHEDULE_ID,
                expected_updated_at=NOW,
                changes={"completed": True},
                updated_at=NOW,
            )
            == 0
        )


def test_conditional_delete_matches_expected_timestamp_and_never_commits(
    schedule_store,
) -> None:
    with schedule_store() as session:
        assert (
            repository.soft_delete_active_if_unchanged(
                session,
                schedule_id=SCHEDULE_ID,
                expected_updated_at=OLD,
                deleted_at=NOW,
            )
            == 1
        )
        session.rollback()
    with schedule_store() as session:
        assert session.get(Schedule, SCHEDULE_ID).deleted_at is None
        assert (
            repository.soft_delete_active_if_unchanged(
                session,
                schedule_id=SCHEDULE_ID,
                expected_updated_at=NOW,
                deleted_at=NOW,
            )
            == 0
        )
