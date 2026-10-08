from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command

from tests.conftest import alembic_config

LEGACY_REVISION = "20260927_0001"
CURRENT_REVISION = "20261008_0002"
INSTANT = "2026-09-13 04:30:00.000000"


def _configure_confirmed_meetings(config, *identifiers: UUID) -> None:
    arguments = []
    if identifiers:
        joined = ",".join(str(identifier) for identifier in identifiers)
        arguments.append(f"confirmed_meeting_ids={joined}")
    config.cmd_opts = Namespace(x=arguments)


def _insert_schedule(
    connection,
    *,
    identifier: UUID,
    category: str,
    title: str,
    completed: bool = False,
    reminder: int | None = None,
    deleted_at: str | None = None,
) -> None:
    connection.exec_driver_sql(
        "INSERT INTO schedules "
        "(id, title, scheduled_at, category, completed, reminder_minutes_before, "
        "created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            identifier.hex,
            title,
            INSTANT,
            category,
            completed,
            reminder,
            INSTANT,
            INSTANT,
            deleted_at,
        ),
    )


def _revision(connection) -> str:
    revision = connection.exec_driver_sql(
        "SELECT version_num FROM alembic_version"
    ).scalar_one()
    return str(revision)


def _schedule_rows(connection) -> list[dict[str, object]]:
    result = connection.exec_driver_sql(
        "SELECT id, title, scheduled_at, category, completed, reminder_minutes_before, "
        "created_at, updated_at, deleted_at FROM schedules ORDER BY id"
    )
    return [dict(row) for row in result.mappings()]


@pytest.mark.integration
def test_category_upgrade_preserves_rows_indexes_and_idempotency_records(
    tmp_path: Path,
) -> None:
    database = tmp_path / "category-upgrade.db"
    config = alembic_config(database)
    command.upgrade(config, LEGACY_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    inventory_id = uuid4()
    return_id = uuid4()
    meeting_id = uuid4()
    deleted_meeting_id = uuid4()
    other_id = uuid4()
    snapshot = '{"category":"MEETING","title":"legacy snapshot"}'

    with engine.begin() as connection:
        _insert_schedule(
            connection,
            identifier=inventory_id,
            category="INVENTORY",
            title="재고 일정",
            reminder=10,
        )
        _insert_schedule(
            connection,
            identifier=return_id,
            category="RETURN",
            title="반품 일정",
            completed=True,
        )
        _insert_schedule(
            connection,
            identifier=meeting_id,
            category="MEETING",
            title="확인된 회의 일정",
            reminder=10,
        )
        _insert_schedule(
            connection,
            identifier=deleted_meeting_id,
            category="MEETING",
            title="삭제된 확인 회의 일정",
            deleted_at=INSTANT,
        )
        _insert_schedule(
            connection,
            identifier=other_id,
            category="OTHER",
            title="기존 기타 일정",
        )
        connection.exec_driver_sql(
            "INSERT INTO idempotency_records "
            "(scope, idempotency_key, fingerprint, schedule_id, response_body, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "default",
                "legacy-key",
                "a" * 64,
                meeting_id.hex,
                snapshot,
                INSTANT,
                "2026-09-14 04:30:00.000000",
            ),
        )
        before_schedules = _schedule_rows(connection)
        before_idempotency = tuple(
            connection.exec_driver_sql(
                "SELECT scope, idempotency_key, fingerprint, schedule_id, response_body, "
                "created_at, expires_at FROM idempotency_records"
            ).one()
        )

    _configure_confirmed_meetings(config, meeting_id, deleted_meeting_id)
    command.upgrade(config, "head")

    with engine.begin() as connection:
        after_schedules = _schedule_rows(connection)
        after_idempotency = tuple(
            connection.exec_driver_sql(
                "SELECT scope, idempotency_key, fingerprint, schedule_id, response_body, "
                "created_at, expires_at FROM idempotency_records"
            ).one()
        )
        assert _revision(connection) == CURRENT_REVISION
        assert len(after_schedules) == len(before_schedules) == 5
        expected_schedules = [dict(row) for row in before_schedules]
        for row in expected_schedules:
            if row["id"] in {meeting_id.hex, deleted_meeting_id.hex}:
                row["category"] = "OTHER"
        assert after_schedules == expected_schedules
        assert after_idempotency == before_idempotency
        assert after_idempotency[4] == snapshot

        check_sql = sa.inspect(connection).get_check_constraints("schedules")[0][
            "sqltext"
        ]
        assert check_sql is not None
        for category in ("INBOUND", "INVENTORY", "OUTBOUND", "RETURN", "OTHER"):
            assert category in check_sql
        assert "MEETING" not in check_sql
        assert "PERSONAL" not in check_sql
        assert "PICKING" not in check_sql
        indexes = {
            index["name"]: index["column_names"]
            for index in sa.inspect(connection).get_indexes("schedules")
        }
        assert indexes["ix_schedules_active_time"] == [
            "deleted_at",
            "scheduled_at",
            "created_at",
            "id",
        ]

        _insert_schedule(
            connection,
            identifier=uuid4(),
            category="INBOUND",
            title="새 입고 일정",
        )
        _insert_schedule(
            connection,
            identifier=uuid4(),
            category="OUTBOUND",
            title="새 출고 일정",
        )
        for removed in ("MEETING", "PERSONAL", "PICKING"):
            with pytest.raises(sa.exc.IntegrityError):
                _insert_schedule(
                    connection,
                    identifier=uuid4(),
                    category=removed,
                    title="제거된 값",
                )

    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("category", ["MEETING", "PERSONAL"])
def test_category_upgrade_refuses_unapproved_legacy_rows_before_writing(
    tmp_path: Path, category: str
) -> None:
    database = tmp_path / f"refuse-{category.lower()}.db"
    config = alembic_config(database)
    command.upgrade(config, LEGACY_REVISION)
    identifier = uuid4()
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        _insert_schedule(
            connection,
            identifier=identifier,
            category=category,
            title="승인되지 않은 기존 일정",
        )

    with pytest.raises(RuntimeError, match=category):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert _revision(connection) == LEGACY_REVISION
        row = _schedule_rows(connection)[0]
        assert row["id"] == identifier.hex
        assert row["category"] == category
    engine.dispose()


@pytest.mark.integration
def test_category_upgrade_requires_exact_meeting_confirmation_set(
    tmp_path: Path,
) -> None:
    database = tmp_path / "exact-confirmation.db"
    config = alembic_config(database)
    command.upgrade(config, LEGACY_REVISION)
    meeting_id = uuid4()
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        _insert_schedule(
            connection,
            identifier=meeting_id,
            category="MEETING",
            title="확인 대상",
        )

    _configure_confirmed_meetings(config, uuid4())
    with pytest.raises(RuntimeError, match="exact existing MEETING IDs"):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert _revision(connection) == LEGACY_REVISION
        assert _schedule_rows(connection)[0]["category"] == "MEETING"
    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("category", "message"),
    [("PICKING", "PICKING"), ("UNEXPECTED", "unexpected legacy category")],
)
def test_category_upgrade_refuses_unsupported_stored_value_before_writing(
    tmp_path: Path, category: str, message: str
) -> None:
    database = tmp_path / f"unsupported-{category.lower()}.db"
    config = alembic_config(database)
    command.upgrade(config, LEGACY_REVISION)
    identifier = uuid4()
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        _insert_schedule(
            connection,
            identifier=identifier,
            category=category,
            title="지원하지 않는 기존 일정",
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    with pytest.raises(RuntimeError, match=message):
        command.upgrade(config, "head")

    with engine.connect() as connection:
        assert _revision(connection) == LEGACY_REVISION
        row = _schedule_rows(connection)[0]
        assert row["id"] == identifier.hex
        assert row["category"] == category
    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("category", ["INBOUND", "OUTBOUND"])
def test_category_downgrade_refuses_new_categories_without_changing_data(
    tmp_path: Path, category: str
) -> None:
    database = tmp_path / f"downgrade-{category.lower()}.db"
    config = alembic_config(database)
    command.upgrade(config, "head")
    identifier = uuid4()
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        _insert_schedule(
            connection,
            identifier=identifier,
            category=category,
            title="새 카테고리 일정",
        )

    with pytest.raises(RuntimeError, match="INBOUND or OUTBOUND"):
        command.downgrade(config, LEGACY_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == CURRENT_REVISION
        assert _schedule_rows(connection)[0]["category"] == category
    engine.dispose()


@pytest.mark.integration
def test_category_downgrade_keeps_converted_other_and_can_upgrade_again(
    tmp_path: Path,
) -> None:
    database = tmp_path / "downgrade-other.db"
    config = alembic_config(database)
    command.upgrade(config, LEGACY_REVISION)
    meeting_id = uuid4()
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        _insert_schedule(
            connection,
            identifier=meeting_id,
            category="MEETING",
            title="변환 후 복원 불가 회의",
        )

    _configure_confirmed_meetings(config, meeting_id)
    command.upgrade(config, "head")
    command.downgrade(config, LEGACY_REVISION)
    with engine.connect() as connection:
        assert _revision(connection) == LEGACY_REVISION
        assert _schedule_rows(connection)[0]["category"] == "OTHER"

    _configure_confirmed_meetings(config)
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert _revision(connection) == CURRENT_REVISION
        assert _schedule_rows(connection)[0]["category"] == "OTHER"
    engine.dispose()
