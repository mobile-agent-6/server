from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base, UTCDateTime, create_api_engine, create_session_factory
from app.modules.assistant import model as assistant_models  # noqa: F401
from app.modules.schedules.category import Category
from app.modules.schedules.model import Schedule
from tests.conftest import alembic_config


@pytest.mark.integration
def test_migration_upgrade_downgrade_upgrade_and_schema_contract(tmp_path: Path) -> None:
    database = tmp_path / "migration.db"
    config = alembic_config(database)

    command.upgrade(config, "head")
    inspector = sa.inspect(sa.create_engine(f"sqlite:///{database}"))
    assert set(inspector.get_table_names()) == {
        "alembic_version",
        "assistant_conversations",
        "assistant_questions",
        "assistant_requests",
        "assistant_write_gate",
        "idempotency_records",
        "schedules",
    }
    assert inspector.get_pk_constraint("schedules")["name"] == "pk_schedules"
    assert (
        inspector.get_pk_constraint("idempotency_records")["name"]
        == "pk_idempotency_records"
    )
    checks = inspector.get_check_constraints("schedules")
    assert [check["name"] for check in checks] == ["ck_schedules_category"]
    check_sql = checks[0]["sqltext"]
    assert check_sql is not None
    for category in ("INBOUND", "INVENTORY", "OUTBOUND", "RETURN", "OTHER"):
        assert category in check_sql
    assert "MEETING" not in check_sql
    assert "PERSONAL" not in check_sql
    assert "PICKING" not in check_sql
    indexes = {index["name"]: index["column_names"] for index in inspector.get_indexes("schedules")}
    assert indexes["ix_schedules_active_time"] == [
        "deleted_at",
        "scheduled_at",
        "created_at",
        "id",
    ]
    assert inspector.get_foreign_keys("idempotency_records") == []
    columns = {column["name"]: column for column in inspector.get_columns("schedules")}
    assert columns["completed"]["default"] in {"0", "false", "(0)"}
    assert {name for name, column in columns.items() if column["nullable"]} == {
        "deleted_at",
    }

    command.downgrade(config, "base")
    assert sa.inspect(sa.create_engine(f"sqlite:///{database}")).get_table_names() == [
        "alembic_version"
    ]
    command.upgrade(config, "head")
    assert "schedules" in sa.inspect(sa.create_engine(f"sqlite:///{database}")).get_table_names()


@pytest.mark.integration
def test_metadata_has_same_business_tables_columns_and_indexes(database_path: Path) -> None:
    inspector = sa.inspect(sa.create_engine(f"sqlite:///{database_path}"))
    for table_name in (
        "schedules",
        "idempotency_records",
        "assistant_conversations",
        "assistant_requests",
        "assistant_questions",
        "assistant_write_gate",
    ):
        reflected = {column["name"] for column in inspector.get_columns(table_name)}
        assert reflected == set(Base.metadata.tables[table_name].columns.keys())
        actual_indexes = {
            (index["name"], tuple(index["column_names"]))
            for index in inspector.get_indexes(table_name)
        }
        expected_indexes = {
            (index.name, tuple(column.name for column in index.columns))
            for index in Base.metadata.tables[table_name].indexes
        }
        assert actual_indexes == expected_indexes
        assert inspector.get_pk_constraint(table_name)["name"] == next(
            constraint.name
            for constraint in Base.metadata.tables[table_name].constraints
            if isinstance(constraint, sa.PrimaryKeyConstraint)
        )
    assert {item["name"] for item in inspector.get_check_constraints("schedules")} == {
        constraint.name
        for constraint in Base.metadata.tables["schedules"].constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    category_type = Base.metadata.tables["schedules"].c.category.type
    assert isinstance(category_type, sa.Enum)
    assert category_type.enums == [
        "INBOUND",
        "INVENTORY",
        "OUTBOUND",
        "RETURN",
        "OTHER",
    ]


@pytest.mark.integration
def test_api_connection_applies_pragmas_and_mode_rw(database_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_api_engine(settings)
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one().lower() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
    factory = create_session_factory(engine)
    assert factory.kw["expire_on_commit"] is False
    assert UTCDateTime.impl.timezone is True
    engine.dispose()


@pytest.mark.integration
def test_select_does_not_force_explicit_deferred_begin(database_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_api_engine(settings)
    raw = engine.raw_connection()
    try:
        driver = raw.driver_connection
        assert driver.in_transaction is False
        driver.execute("SELECT 1").fetchone()
        assert driver.in_transaction is False
    finally:
        raw.close()
        engine.dispose()


@pytest.mark.integration
def test_api_connection_does_not_create_missing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    engine = create_api_engine(
        Settings(_env_file=None, database_url=f"sqlite:///{missing}")
    )
    with pytest.raises(OperationalError):
        engine.connect()
    assert not missing.exists()
    engine.dispose()


@pytest.mark.integration
def test_uuid_v4_and_utc_datetime_round_trip(database_path: Path) -> None:
    engine = create_api_engine(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    )
    identifier = uuid4()
    instant = datetime(2026, 9, 13, 13, 45, 0, 123456, tzinfo=timezone.utc)
    with Session(engine, expire_on_commit=False) as session:
        session.add(
            Schedule(
                id=identifier,
                title="회의",
                scheduled_at=instant,
                category=Category.OUTBOUND,
                completed=False,
                created_at=instant,
                updated_at=instant,
                deleted_at=None,
            )
        )
        session.commit()
    with Session(engine) as session:
        loaded = session.get(Schedule, identifier)
        assert loaded is not None
        assert loaded.id.version == 4
        assert loaded.scheduled_at == instant
        assert loaded.scheduled_at.tzinfo is timezone.utc
    engine.dispose()


@pytest.mark.integration
def test_utc_datetime_rejects_naive_values(database_path: Path) -> None:
    engine = create_api_engine(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    )
    with Session(engine) as session:
        session.add(
            Schedule(
                id=uuid4(),
                title="회의",
                scheduled_at=datetime(2026, 9, 13, 4, 45),
                category=Category.OUTBOUND,
                completed=False,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
                deleted_at=None,
            )
        )
        with pytest.raises(sa.exc.StatementError, match="timezone-aware"):
            session.commit()
    engine.dispose()


def test_migration_does_not_import_application_utc_type() -> None:
    migration = Path("alembic/versions/20260927_0001_initial_schedule_schema.py").read_text()
    assert "UTCDateTime" not in migration
    assert "server_default=sa.false()" in migration


@pytest.mark.integration
def test_alembic_requires_explicit_environment_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCHEDULE_APP_ENV", raising=False)
    alembic = Config("config/alembic.ini")
    with pytest.raises(RuntimeError, match="SCHEDULE_APP_ENV"):
        command.current(alembic)


@pytest.mark.integration
def test_database_constraints_and_boolean_default(database_path: Path) -> None:
    engine = sa.create_engine(f"sqlite:///{database_path}")
    identifier = uuid4().hex
    instant = "2026-09-13 04:45:00.000000"
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO schedules "
            "(id, title, scheduled_at, category, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (identifier, "출고", instant, "OUTBOUND", instant, instant),
        )
        assert connection.exec_driver_sql(
            "SELECT completed FROM schedules WHERE id = ?", (identifier,)
        ).scalar_one() == 0
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO schedules "
                "(id, title, scheduled_at, category, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uuid4().hex, "오류", instant, "INVALID", instant, instant),
            )
        for removed in ("MEETING", "PERSONAL", "PICKING"):
            with pytest.raises(sa.exc.IntegrityError):
                connection.exec_driver_sql(
                    "INSERT INTO schedules "
                    "(id, title, scheduled_at, category, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (uuid4().hex, "제거 값", instant, removed, instant, instant),
                )

    with engine.begin() as connection:
        values = (
            "default",
            "same-key",
            "0" * 64,
            uuid4().hex,
            "{}",
            instant,
            instant,
        )
        connection.exec_driver_sql(
            "INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)", values
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.exec_driver_sql(
                "INSERT INTO idempotency_records VALUES (?, ?, ?, ?, ?, ?, ?)", values
            )
    engine.dispose()
