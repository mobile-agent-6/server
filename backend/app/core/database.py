"""SQLAlchemy engine, session, metadata, and SQLite connection policy."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from datetime import datetime, timezone
from urllib.parse import quote

import sqlalchemy as sa
from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import DateTime, TypeDecorator

from app.core.config import Settings

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist aware datetimes as UTC and restore UTC awareness on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: sa.Dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime")
        try:
            return value.astimezone(timezone.utc)
        except (OverflowError, ValueError) as exc:
            raise ValueError("datetime cannot be represented in UTC") from exc

    def process_result_value(self, value: datetime | None, dialect: sa.Dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


def api_database_url(settings: Settings) -> str:
    """Return a SQLite URI that opens only the configured existing file."""

    encoded_path = quote(settings.database_path.as_posix(), safe="/")
    return f"sqlite+pysqlite:///file:{encoded_path}?mode=rw&uri=true"


def _configure_sqlite_connection(
    dbapi_connection: sqlite3.Connection, _connection_record: object, *, timeout_ms: int
) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={timeout_ms}")
    finally:
        cursor.close()


def create_api_engine(settings: Settings) -> Engine:
    engine = sa.create_engine(
        api_database_url(settings),
        connect_args={
            "check_same_thread": False,
            "timeout": settings.sqlite_busy_timeout_ms / 1000,
        },
        pool_timeout=settings.db_pool_timeout_seconds,
    )
    event.listen(
        engine,
        "connect",
        lambda connection, record: _configure_sqlite_connection(
            connection, record, timeout_ms=settings.sqlite_busy_timeout_ms
        ),
    )
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_session(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
    finally:
        session.close()


_TRANSIENT_SQLITE_CODES = {
    sqlite3.SQLITE_BUSY,
    sqlite3.SQLITE_LOCKED,
    sqlite3.SQLITE_IOERR,
    sqlite3.SQLITE_FULL,
    sqlite3.SQLITE_CANTOPEN,
}


def is_database_unavailable(exc: BaseException) -> bool:
    if isinstance(exc, SQLAlchemyTimeoutError):
        return True
    if not isinstance(exc, DBAPIError):
        return False
    if exc.connection_invalidated:
        return True
    original = exc.orig
    if not isinstance(original, sqlite3.Error):
        return False
    code = getattr(original, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & 0xFF) in _TRANSIENT_SQLITE_CODES
