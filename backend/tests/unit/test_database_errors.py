import sqlite3

import pytest
import sqlalchemy as sa

from app.core.database import is_database_unavailable


@pytest.mark.parametrize(
    "code",
    [
        sqlite3.SQLITE_BUSY,
        sqlite3.SQLITE_LOCKED,
        sqlite3.SQLITE_IOERR,
        sqlite3.SQLITE_FULL,
        sqlite3.SQLITE_CANTOPEN,
        sqlite3.SQLITE_IOERR | (3 << 8),
    ],
)
def test_transient_sqlite_codes_are_unavailable(code: int) -> None:
    original = sqlite3.OperationalError("database failure")
    original.sqlite_errorcode = code
    error = sa.exc.OperationalError("statement", {}, original)
    assert is_database_unavailable(error) is True


def test_schema_error_and_integrity_error_are_not_unavailable() -> None:
    schema = sqlite3.OperationalError("no such table")
    schema.sqlite_errorcode = sqlite3.SQLITE_ERROR
    assert is_database_unavailable(sa.exc.OperationalError("select", {}, schema)) is False

    integrity = sqlite3.IntegrityError("constraint failed")
    integrity.sqlite_errorcode = sqlite3.SQLITE_CONSTRAINT
    assert is_database_unavailable(sa.exc.IntegrityError("insert", {}, integrity)) is False


def test_pool_timeout_and_invalidated_connection_are_unavailable() -> None:
    assert is_database_unavailable(sa.exc.TimeoutError("pool timeout")) is True
    original = sqlite3.OperationalError("connection lost")
    error = sa.exc.OperationalError("select", {}, original, connection_invalidated=True)
    assert is_database_unavailable(error) is True
