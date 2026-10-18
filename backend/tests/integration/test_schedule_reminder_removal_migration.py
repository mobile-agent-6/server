from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command

from tests.conftest import alembic_config

PREVIOUS_REVISION = "20261008_0002"
CURRENT_REVISION = "20261017_0003"
INSTANT = "2026-10-17 04:30:00.000000"


def _revision(connection) -> str:
    return str(
        connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    )


def _insert_schedule(
    connection,
    *,
    identifier: UUID,
    category: str,
    title: str,
    reminder: int | None,
    completed: bool = False,
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


def _protected_schedule_rows(connection) -> list[dict[str, object]]:
    rows = connection.exec_driver_sql(
        "SELECT id, title, scheduled_at, category, completed, created_at, updated_at, "
        "deleted_at FROM schedules ORDER BY id"
    )
    return [dict(row) for row in rows.mappings()]


def _idempotency_rows(connection) -> list[dict[str, object]]:
    rows = connection.exec_driver_sql(
        "SELECT scope, idempotency_key, fingerprint, schedule_id, response_body, "
        "created_at, expires_at FROM idempotency_records ORDER BY scope, idempotency_key"
    )
    return [dict(row) for row in rows.mappings()]


def _table_state(connection, table_name: str) -> dict[str, object]:
    inspector = sa.inspect(connection)
    columns = [
        (
            column["name"],
            str(column["type"]),
            bool(column["nullable"]),
            column.get("default"),
            int(column.get("primary_key", 0)),
        )
        for column in inspector.get_columns(table_name)
    ]
    indexes = sorted(
        (
            str(index["name"]),
            tuple(index["column_names"]),
            bool(index["unique"]),
        )
        for index in inspector.get_indexes(table_name)
    )
    rows = connection.exec_driver_sql(
        f'SELECT * FROM "{table_name}" ORDER BY rowid'
    )
    return {
        "columns": columns,
        "indexes": indexes,
        "rows": [dict(row) for row in rows.mappings()],
    }


def _assert_current_schema(connection) -> None:
    inspector = sa.inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("schedules")}
    assert "reminder_minutes_before" not in columns
    assert {name for name, column in columns.items() if column["nullable"]} == {
        "deleted_at"
    }
    assert inspector.get_pk_constraint("schedules")["name"] == "pk_schedules"
    checks = inspector.get_check_constraints("schedules")
    assert [check["name"] for check in checks] == ["ck_schedules_category"]
    check_sql = str(checks[0]["sqltext"])
    for category in ("INBOUND", "INVENTORY", "OUTBOUND", "RETURN", "OTHER"):
        assert category in check_sql
    indexes = {
        index["name"]: index["column_names"]
        for index in inspector.get_indexes("schedules")
    }
    assert indexes == {
        "ix_schedules_active_time": [
            "deleted_at",
            "scheduled_at",
            "created_at",
            "id",
        ]
    }


@pytest.mark.integration
def test_reminder_upgrade_preserves_schedules_and_idempotency_records(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reminder-upgrade.db"
    config = alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    identifiers = [uuid4() for _ in range(5)]
    categories = ("INBOUND", "INVENTORY", "OUTBOUND", "RETURN", "OTHER")
    snapshot = (
        f'{{"id":"{identifiers[1]}","title":"기존 재고",'
        '"reminderMinutesBefore":10,"reminderAt":"2026-10-17T04:20:00Z"}'
    )

    with engine.begin() as connection:
        for index, (identifier, category) in enumerate(zip(identifiers, categories)):
            _insert_schedule(
                connection,
                identifier=identifier,
                category=category,
                title=f"기존 일정 {index}",
                reminder=10 if index % 2 else None,
                completed=index == 2,
                deleted_at=INSTANT if index == 4 else None,
            )
        connection.exec_driver_sql(
            "INSERT INTO idempotency_records "
            "(scope, idempotency_key, fingerprint, schedule_id, response_body, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "default",
                "legacy-reminder-key",
                "a" * 64,
                identifiers[1].hex,
                snapshot,
                INSTANT,
                "2026-10-18 04:30:00.000000",
            ),
        )
        before_schedules = _protected_schedule_rows(connection)
        before_idempotency = _idempotency_rows(connection)

    command.upgrade(config, CURRENT_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == CURRENT_REVISION
        assert _protected_schedule_rows(connection) == before_schedules
        assert _idempotency_rows(connection) == before_idempotency
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
        _assert_current_schema(connection)
    engine.dispose()


@pytest.mark.integration
def test_reminder_downgrade_restores_nullable_column_with_only_null_values(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reminder-downgrade.db"
    config = alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    identifiers = [uuid4(), uuid4()]
    with engine.begin() as connection:
        _insert_schedule(
            connection,
            identifier=identifiers[0],
            category="INVENTORY",
            title="알림 있었던 일정",
            reminder=10,
        )
        _insert_schedule(
            connection,
            identifier=identifiers[1],
            category="RETURN",
            title="알림 없던 삭제 일정",
            reminder=None,
            completed=True,
            deleted_at=INSTANT,
        )
        protected = _protected_schedule_rows(connection)

    command.upgrade(config, CURRENT_REVISION)
    command.downgrade(config, PREVIOUS_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == PREVIOUS_REVISION
        assert _protected_schedule_rows(connection) == protected
        columns = {
            column["name"]: column
            for column in sa.inspect(connection).get_columns("schedules")
        }
        assert columns["reminder_minutes_before"]["nullable"] is True
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM schedules WHERE reminder_minutes_before IS NOT NULL"
        ).scalar_one() == 0

    command.upgrade(config, CURRENT_REVISION)
    with engine.connect() as connection:
        assert _revision(connection) == CURRENT_REVISION
        assert _protected_schedule_rows(connection) == protected
        _assert_current_schema(connection)
    engine.dispose()


@pytest.mark.integration
def test_reminder_upgrade_preflight_failure_keeps_previous_revision_and_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reminder-failure.db"
    config = alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    identifier = uuid4()
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        _insert_schedule(
            connection,
            identifier=identifier,
            category="INVALID",
            title="사전 검사 거절",
            reminder=10,
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    with pytest.raises(RuntimeError, match="unexpected categories"):
        command.upgrade(config, CURRENT_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == PREVIOUS_REVISION
        columns = {
            column["name"]
            for column in sa.inspect(connection).get_columns("schedules")
        }
        assert "reminder_minutes_before" in columns
        row = connection.exec_driver_sql(
            "SELECT id, category, reminder_minutes_before FROM schedules"
        ).one()
        assert tuple(row) == (identifier.hex, "INVALID", 10)
        assert "_alembic_tmp_schedules" not in sa.inspect(connection).get_table_names()
    engine.dispose()


@pytest.mark.integration
def test_reminder_upgrade_rejects_leftover_batch_table_without_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reminder-leftover-table.db"
    config = alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    identifier = uuid4()
    with engine.begin() as connection:
        _insert_schedule(
            connection,
            identifier=identifier,
            category="INBOUND",
            title="보존할 입고 일정",
            reminder=10,
        )
        connection.exec_driver_sql(
            "CREATE TABLE _alembic_tmp_schedules "
            "(marker TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO _alembic_tmp_schedules (marker, payload) VALUES (?, ?)",
            ("prepared", "do-not-delete"),
        )
        source_before = _table_state(connection, "schedules")
        leftover_before = _table_state(connection, "_alembic_tmp_schedules")

    with pytest.raises(RuntimeError, match="leftover batch table"):
        command.upgrade(config, CURRENT_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == PREVIOUS_REVISION
        assert _table_state(connection, "schedules") == source_before
        assert (
            _table_state(connection, "_alembic_tmp_schedules") == leftover_before
        )
    engine.dispose()


@pytest.mark.integration
def test_reminder_upgrade_rejects_unexpected_columns_without_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reminder-unexpected-columns.db"
    config = alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    identifier = uuid4()
    with engine.begin() as connection:
        _insert_schedule(
            connection,
            identifier=identifier,
            category="RETURN",
            title="보존할 반품 일정",
            reminder=None,
        )
        connection.exec_driver_sql(
            "ALTER TABLE schedules ADD COLUMN unexpected_marker TEXT"
        )
        connection.exec_driver_sql(
            "UPDATE schedules SET unexpected_marker = ? WHERE id = ?",
            ("preserve-me", identifier.hex),
        )
        source_before = _table_state(connection, "schedules")

    with pytest.raises(RuntimeError, match="unexpected source schema"):
        command.upgrade(config, CURRENT_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == PREVIOUS_REVISION
        assert _table_state(connection, "schedules") == source_before
        assert "_alembic_tmp_schedules" not in sa.inspect(connection).get_table_names()
    engine.dispose()


@pytest.mark.integration
def test_reminder_upgrade_rejects_unexpected_indexes_without_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reminder-unexpected-indexes.db"
    config = alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    identifier = uuid4()
    with engine.begin() as connection:
        _insert_schedule(
            connection,
            identifier=identifier,
            category="OTHER",
            title="보존할 기타 일정",
            reminder=10,
        )
        connection.exec_driver_sql(
            "CREATE INDEX ix_schedules_unexpected_title ON schedules (title)"
        )
        source_before = _table_state(connection, "schedules")

    with pytest.raises(RuntimeError, match="unexpected indexes"):
        command.upgrade(config, CURRENT_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == PREVIOUS_REVISION
        assert _table_state(connection, "schedules") == source_before
        assert "_alembic_tmp_schedules" not in sa.inspect(connection).get_table_names()
    engine.dispose()
