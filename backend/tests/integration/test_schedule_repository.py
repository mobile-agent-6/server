from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_api_engine
from app.modules.schedules.category import Category
from app.modules.schedules.model import Schedule
from app.modules.schedules.repository import list_active


@pytest.mark.integration
def test_list_query_plan_uses_active_time_index(database_path: Path) -> None:
    engine = create_api_engine(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    )
    now = datetime(2026, 9, 13, 4, 30, tzinfo=timezone.utc)
    with Session(engine) as session:
        for offset in range(40):
            scheduled = now + timedelta(hours=offset)
            session.add(
                Schedule(
                    id=uuid4(),
                    title=f"일정 {offset}",
                    scheduled_at=scheduled,
                    category=Category.OTHER,
                    completed=False,
                    created_at=now + timedelta(microseconds=offset),
                    updated_at=now,
                    deleted_at=None,
                )
            )
        session.commit()
        plan = session.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT * FROM schedules "
                "WHERE deleted_at IS NULL AND scheduled_at >= :start "
                "AND scheduled_at < :end "
                "ORDER BY scheduled_at, created_at, id"
            ),
            {"start": now, "end": now + timedelta(days=2)},
        ).all()
        assert any("ix_schedules_active_time" in row[-1] for row in plan)

        results = list_active(
            session,
            from_utc=now,
            to_utc=now + timedelta(hours=10),
            category=Category.OTHER,
            completed=False,
        )
        assert [item.scheduled_at for item in results] == sorted(
            item.scheduled_at for item in results
        )
    engine.dispose()
