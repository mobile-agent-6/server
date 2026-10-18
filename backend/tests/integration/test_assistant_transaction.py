from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import (
    create_api_engine,
    create_assistant_engine,
    create_session_factory,
)
from app.modules.assistant.model import AssistantConversation
from tests.conftest import migrate

NOW = datetime(2026, 10, 18, 4, 30, tzinfo=timezone.utc)


def _settings(database: Path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{database}")


@pytest.mark.integration
def test_assistant_engine_uses_driver_autocommit_and_begin_immediate(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant-immediate.db"
    migrate(database)
    engine = create_assistant_engine(_settings(database))
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda connection, cursor, statement, parameters, context, executemany: (
            statements.append(statement)
        ),
    )

    raw = engine.raw_connection()
    try:
        assert raw.driver_connection.isolation_level is None
    finally:
        raw.close()

    with engine.begin() as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1

    assert statements[0] == "BEGIN IMMEDIATE"
    assert "BEGIN" not in statements
    engine.dispose()


@pytest.mark.integration
@pytest.mark.parametrize("release", ["rollback", "commit"])
def test_begin_immediate_reserves_writer_until_transaction_releases(
    tmp_path: Path, release: str
) -> None:
    database = tmp_path / "assistant-lock.db"
    migrate(database)
    first_engine = create_assistant_engine(_settings(database))
    second_engine = create_assistant_engine(_settings(database), busy_timeout_ms=0)
    first = first_engine.connect()
    first_transaction = first.begin()
    try:
        assert first.exec_driver_sql("SELECT 1").scalar_one() == 1
        with pytest.raises(OperationalError) as captured:
            with second_engine.begin():
                pass
        original = captured.value.orig
        assert isinstance(original, sqlite3.Error)
        assert getattr(original, "sqlite_errorcode", 0) & 0xFF in {
            sqlite3.SQLITE_BUSY,
            sqlite3.SQLITE_LOCKED,
        }
    finally:
        getattr(first_transaction, release)()
        first.close()

    with second_engine.begin() as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
    first_engine.dispose()
    second_engine.dispose()


@pytest.mark.integration
def test_assistant_session_rolls_back_exception_and_returns_connection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant-rollback.db"
    migrate(database)
    engine = create_assistant_engine(_settings(database))
    factory = create_session_factory(engine)
    conversation_id = uuid4()

    with pytest.raises(RuntimeError, match="injected"):
        with factory() as session:
            with session.begin():
                session.add(
                    AssistantConversation(
                        id=conversation_id,
                        created_at=NOW,
                        last_valid_input_at=NOW,
                        expired_at=None,
                        control_state_version=None,
                        control_state_json=None,
                    )
                )
                session.flush()
                raise RuntimeError("injected")

    with factory() as session:
        assert session.get(AssistantConversation, conversation_id) is None
    with engine.begin() as connection:
        assert connection.exec_driver_sql("SELECT 1").scalar_one() == 1
    engine.dispose()


@pytest.mark.integration
def test_assistant_session_commit_persists_and_api_engine_remains_deferred(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant-commit.db"
    migrate(database)
    settings = _settings(database)
    assistant_engine = create_assistant_engine(settings)
    factory = create_session_factory(assistant_engine)
    conversation_id = uuid4()

    with factory.begin() as session:
        session.add(
            AssistantConversation(
                id=conversation_id,
                created_at=NOW,
                last_valid_input_at=NOW,
                expired_at=None,
                control_state_version=None,
                control_state_json=None,
            )
        )
    with factory() as session:
        assert session.get(AssistantConversation, conversation_id) is not None

    api_engine = create_api_engine(settings)
    raw = api_engine.raw_connection()
    try:
        driver = raw.driver_connection
        assert driver.isolation_level is not None
        driver.execute("SELECT 1").fetchone()
        assert driver.in_transaction is False
    finally:
        raw.close()
        api_engine.dispose()
        assistant_engine.dispose()


@pytest.mark.integration
def test_assistant_engine_preserves_rw_and_sqlite_pragmas(tmp_path: Path) -> None:
    database = tmp_path / "assistant-pragmas.db"
    migrate(database)
    engine = create_assistant_engine(_settings(database))
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5000
    engine.dispose()

    missing = tmp_path / "missing.db"
    missing_engine = create_assistant_engine(_settings(missing))
    with pytest.raises(OperationalError):
        missing_engine.connect()
    assert not missing.exists()
    missing_engine.dispose()
