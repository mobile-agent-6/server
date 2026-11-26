from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command

from app.core.database import Base
from app.modules.assistant import model as assistant_models  # noqa: F401
from tests.conftest import alembic_config

PREVIOUS_REVISION = "20261017_0003"
CURRENT_REVISION = "20261017_0004"
INSTANT = "2026-10-18 04:30:00.000000"
ASSISTANT_TABLES = {
    "assistant_conversations",
    "assistant_requests",
    "assistant_questions",
    "assistant_write_gate",
}


def _revision(connection) -> str:
    return str(
        connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
    )


def _insert_conversation(connection, conversation_id) -> None:
    connection.exec_driver_sql(
        "INSERT INTO assistant_conversations "
        "(id, created_at, last_valid_input_at) VALUES (?, ?, ?)",
        (conversation_id.hex, INSTANT, INSTANT),
    )


def _insert_request(
    connection,
    *,
    request_id,
    conversation_id,
    status: str,
    result_kind: str | None = None,
    had_uncertain: bool = False,
    unresolved_uncertain: bool = False,
) -> None:
    completed_at = INSTANT if status == "completed" else None
    result_json = '{"kind":"outcome"}' if result_kind else None
    retention_started_at = (
        INSTANT
        if status == "completed" and result_kind != "uncertain"
        else None
    )
    uncertain_at = INSTANT if had_uncertain else None
    uncertain_resolved_at = (
        None if unresolved_uncertain else (INSTANT if had_uncertain else None)
    )
    connection.exec_driver_sql(
        "INSERT INTO assistant_requests "
        "(id, conversation_id, fingerprint_version, fingerprint_hash, "
        "normalized_body_json, status, result_kind, result_json, accepted_at, "
        "completed_at, task_reference_accepted_at, had_uncertain, uncertain_at, "
        "uncertain_resolved_at, retention_started_at) "
        "VALUES (?, ?, 1, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            request_id.hex,
            conversation_id.hex,
            "a" * 64,
            status,
            result_kind,
            result_json,
            INSTANT,
            completed_at,
            INSTANT,
            had_uncertain,
            uncertain_at,
            uncertain_resolved_at,
            retention_started_at,
        ),
    )


def _sql_text(value: object | None) -> str:
    return "" if value is None else str(value)


def _schema_snapshot(connection, table_name: str) -> dict[str, object]:
    inspector = sa.inspect(connection)
    return {
        "columns": [
            (
                column["name"],
                str(column["type"]),
                bool(column["nullable"]),
                column.get("default"),
                int(column.get("primary_key", 0)),
            )
            for column in inspector.get_columns(table_name)
        ],
        "indexes": sorted(
            (
                index["name"],
                tuple(index["column_names"]),
                bool(index["unique"]),
                _sql_text(
                    index.get("dialect_options", {}).get("sqlite_where")
                ),
            )
            for index in inspector.get_indexes(table_name)
        ),
        "checks": sorted(
            (constraint["name"], constraint["sqltext"])
            for constraint in inspector.get_check_constraints(table_name)
        ),
    }


@pytest.mark.integration
def test_fresh_upgrade_creates_exact_schema_and_singleton_gate(tmp_path: Path) -> None:
    database = tmp_path / "assistant-fresh.db"
    config = alembic_config(database)

    command.upgrade(config, "head")
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        assert _revision(connection) == CURRENT_REVISION
        assert set(inspector.get_table_names()) == {
            "alembic_version",
            "schedules",
            "idempotency_records",
            *ASSISTANT_TABLES,
        }
        assert connection.exec_driver_sql(
            "SELECT singleton_key, owner_request_id, acquired_at, generation "
            "FROM assistant_write_gate"
        ).one() == (1, None, None, 0)
        assert "active_question_id" not in {
            column["name"]
            for column in inspector.get_columns("assistant_conversations")
        }
        assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    engine.dispose()


@pytest.mark.integration
def test_upgrade_from_0003_preserves_schedule_and_idempotency_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant-upgrade.db"
    config = alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    schedule_id = uuid4()
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO schedules "
            "(id, title, scheduled_at, category, completed, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                schedule_id.hex,
                "보존 일정",
                INSTANT,
                "INVENTORY",
                True,
                INSTANT,
                INSTANT,
            ),
        )
        connection.exec_driver_sql(
            "INSERT INTO idempotency_records "
            "(scope, idempotency_key, fingerprint, schedule_id, response_body, "
            "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "default",
                "preserved-key",
                "b" * 64,
                schedule_id.hex,
                '{"preserved":true}',
                INSTANT,
                "2026-10-19 04:30:00.000000",
            ),
        )
        schedules_before = connection.exec_driver_sql(
            "SELECT * FROM schedules ORDER BY id"
        ).all()
        idempotency_before = connection.exec_driver_sql(
            "SELECT * FROM idempotency_records ORDER BY scope, idempotency_key"
        ).all()

    command.upgrade(config, CURRENT_REVISION)

    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT * FROM schedules ORDER BY id"
        ).all() == schedules_before
        assert connection.exec_driver_sql(
            "SELECT * FROM idempotency_records ORDER BY scope, idempotency_key"
        ).all() == idempotency_before
    engine.dispose()


@pytest.mark.integration
def test_migration_and_metadata_match_assistant_schema(tmp_path: Path) -> None:
    database = tmp_path / "assistant-metadata.db"
    command.upgrade(alembic_config(database), CURRENT_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    inspector = sa.inspect(engine)

    for table_name in sorted(ASSISTANT_TABLES):
        table = Base.metadata.tables[table_name]
        assert {column["name"] for column in inspector.get_columns(table_name)} == {
            column.name for column in table.columns
        }
        assert inspector.get_pk_constraint(table_name)["name"] == next(
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, sa.PrimaryKeyConstraint)
        )
        assert {item["name"] for item in inspector.get_check_constraints(table_name)} == {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint)
        }
        assert {
            (
                item["name"],
                tuple(item["column_names"]),
                bool(item["unique"]),
                _sql_text(item.get("dialect_options", {}).get("sqlite_where")),
            )
            for item in inspector.get_indexes(table_name)
        } == {
            (
                index.name,
                tuple(column.name for column in index.columns),
                bool(index.unique),
                _sql_text(index.dialect_options["sqlite"].get("where")),
            )
            for index in table.indexes
        }

        actual_foreign_keys = {
            (
                item["name"],
                tuple(item["constrained_columns"]),
                item["referred_table"],
                tuple(item["referred_columns"]),
                item["options"].get("ondelete"),
            )
            for item in inspector.get_foreign_keys(table_name)
        }
        expected_foreign_keys = {
            (
                constraint.name,
                tuple(element.parent.name for element in constraint.elements),
                next(iter(constraint.elements)).column.table.name,
                tuple(element.column.name for element in constraint.elements),
                constraint.ondelete,
            )
            for constraint in table.constraints
            if isinstance(constraint, sa.ForeignKeyConstraint)
        }
        assert actual_foreign_keys == expected_foreign_keys
        assert {
            item["name"] for item in inspector.get_unique_constraints(table_name)
        } == {
            constraint.name
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
            and not isinstance(constraint, sa.PrimaryKeyConstraint)
        }
    engine.dispose()


@pytest.mark.integration
def test_safe_downgrade_preserves_existing_tables_and_reupgrades(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant-safe-downgrade.db"
    config = alembic_config(database)
    command.upgrade(config, CURRENT_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    conversation_id = uuid4()
    request_id = uuid4()
    schedule_id = uuid4()
    with engine.begin() as connection:
        _insert_conversation(connection, conversation_id)
        _insert_request(
            connection,
            request_id=request_id,
            conversation_id=conversation_id,
            status="completed",
            result_kind="outcome",
        )
        connection.exec_driver_sql(
            "INSERT INTO schedules "
            "(id, title, scheduled_at, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (schedule_id.hex, "보존", INSTANT, "OTHER", INSTANT, INSTANT),
        )
        schedule_before = connection.exec_driver_sql(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id.hex,)
        ).one()

    command.downgrade(config, PREVIOUS_REVISION)
    with engine.connect() as connection:
        assert _revision(connection) == PREVIOUS_REVISION
        assert ASSISTANT_TABLES.isdisjoint(sa.inspect(connection).get_table_names())
        assert connection.exec_driver_sql(
            "SELECT * FROM schedules WHERE id = ?", (schedule_id.hex,)
        ).one() == schedule_before

    command.upgrade(config, CURRENT_REVISION)
    with engine.connect() as connection:
        assert _revision(connection) == CURRENT_REVISION
        assert ASSISTANT_TABLES <= set(sa.inspect(connection).get_table_names())
        assert connection.exec_driver_sql(
            "SELECT COUNT(*) FROM assistant_write_gate"
        ).scalar_one() == 1
    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("status", ["accepted", "processing"])
def test_downgrade_refuses_blocking_request_without_schema_or_data_changes(
    tmp_path: Path, status: str
) -> None:
    database = tmp_path / f"assistant-{status}-downgrade.db"
    config = alembic_config(database)
    command.upgrade(config, CURRENT_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    conversation_id = uuid4()
    request_id = uuid4()
    with engine.begin() as connection:
        _insert_conversation(connection, conversation_id)
        _insert_request(
            connection,
            request_id=request_id,
            conversation_id=conversation_id,
            status=status,
        )
        schema_before = {
            table: _schema_snapshot(connection, table)
            for table in ASSISTANT_TABLES
        }

    with pytest.raises(RuntimeError, match="accepted or processing"):
        command.downgrade(config, PREVIOUS_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == CURRENT_REVISION
        assert connection.exec_driver_sql(
            "SELECT id, status FROM assistant_requests"
        ).one() == (request_id.hex, status)
        assert {
            table: _schema_snapshot(connection, table)
            for table in ASSISTANT_TABLES
        } == schema_before
    engine.dispose()


@pytest.mark.integration
def test_downgrade_refuses_unresolved_uncertain_without_changes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant-uncertain-downgrade.db"
    config = alembic_config(database)
    command.upgrade(config, CURRENT_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    conversation_id = uuid4()
    request_id = uuid4()
    with engine.begin() as connection:
        _insert_conversation(connection, conversation_id)
        _insert_request(
            connection,
            request_id=request_id,
            conversation_id=conversation_id,
            status="completed",
            result_kind="uncertain",
            had_uncertain=True,
            unresolved_uncertain=True,
        )

    with pytest.raises(RuntimeError, match="unresolved uncertain"):
        command.downgrade(config, PREVIOUS_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == CURRENT_REVISION
        assert connection.exec_driver_sql(
            "SELECT id, had_uncertain, uncertain_resolved_at "
            "FROM assistant_requests"
        ).one() == (request_id.hex, 1, None)
        assert ASSISTANT_TABLES <= set(sa.inspect(connection).get_table_names())
    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("artifact", ["unexpected_column", "leftover_table"])
def test_downgrade_rejects_unexpected_schema_before_any_drop(
    tmp_path: Path, artifact: str
) -> None:
    database = tmp_path / f"assistant-{artifact}.db"
    config = alembic_config(database)
    command.upgrade(config, CURRENT_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        if artifact == "unexpected_column":
            connection.exec_driver_sql(
                "ALTER TABLE assistant_conversations ADD COLUMN unexpected TEXT"
            )
        else:
            connection.exec_driver_sql(
                "CREATE TABLE _alembic_tmp_assistant_requests (marker TEXT)"
            )
        tables_before = set(sa.inspect(connection).get_table_names())

    with pytest.raises(RuntimeError, match="unexpected|leftover"):
        command.downgrade(config, PREVIOUS_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == CURRENT_REVISION
        assert set(sa.inspect(connection).get_table_names()) == tables_before
    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("artifact", ["unexpected_table", "leftover_table"])
def test_upgrade_rejects_unexpected_0003_source_before_any_create(
    tmp_path: Path, artifact: str
) -> None:
    database = tmp_path / f"assistant-upgrade-{artifact}.db"
    config = alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    table_name = (
        "unexpected_table"
        if artifact == "unexpected_table"
        else "_alembic_tmp_assistant_requests"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE TABLE "{table_name}" (marker TEXT)')
        connection.exec_driver_sql(
            f'INSERT INTO "{table_name}" (marker) VALUES (?)', ("preserve",)
        )

    with pytest.raises(RuntimeError, match="unexpected|leftover"):
        command.upgrade(config, CURRENT_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == PREVIOUS_REVISION
        assert ASSISTANT_TABLES.isdisjoint(sa.inspect(connection).get_table_names())
        assert connection.exec_driver_sql(
            f'SELECT marker FROM "{table_name}"'
        ).scalar_one() == "preserve"
    engine.dispose()


@pytest.mark.integration
def test_upgrade_rejects_unexpected_source_index_without_writes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant-upgrade-index.db"
    config = alembic_config(database)
    command.upgrade(config, PREVIOUS_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE INDEX ix_schedules_unexpected_title ON schedules (title)"
        )

    with pytest.raises(RuntimeError, match="Schedule indexes"):
        command.upgrade(config, CURRENT_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == PREVIOUS_REVISION
        assert "ix_schedules_unexpected_title" in {
            index["name"] for index in sa.inspect(connection).get_indexes("schedules")
        }
        assert ASSISTANT_TABLES.isdisjoint(sa.inspect(connection).get_table_names())
    engine.dispose()


@pytest.mark.integration
def test_downgrade_rejects_unexpected_assistant_index_without_drops(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant-downgrade-index.db"
    config = alembic_config(database)
    command.upgrade(config, CURRENT_REVISION)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE INDEX ix_assistant_conversations_unexpected "
            "ON assistant_conversations (created_at)"
        )

    with pytest.raises(RuntimeError, match="Assistant indexes"):
        command.downgrade(config, PREVIOUS_REVISION)

    with engine.connect() as connection:
        assert _revision(connection) == CURRENT_REVISION
        assert ASSISTANT_TABLES <= set(sa.inspect(connection).get_table_names())
        assert "ix_assistant_conversations_unexpected" in {
            index["name"]
            for index in sa.inspect(connection).get_indexes(
                "assistant_conversations"
            )
        }
    engine.dispose()
