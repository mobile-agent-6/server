"""Assistant schema probe and schema-error classification."""

from __future__ import annotations

import sqlite3

import sqlalchemy as sa
from alembic.migration import MigrationContext
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantRequest,
    AssistantWriteGate,
)

_ASSISTANT_MODELS = (
    AssistantConversation,
    AssistantRequest,
    AssistantQuestion,
    AssistantWriteGate,
)


class AssistantSchemaUnavailable(RuntimeError):
    """The configured database does not expose the expected Assistant schema."""


def probe_assistant_schema(engine: Engine, *, expected_head: str) -> None:
    """Verify the migration head and exact Assistant table columns without writes."""

    with engine.connect() as connection:
        current_heads = tuple(
            MigrationContext.configure(connection).get_current_heads()
        )
        if current_heads != (expected_head,):
            raise AssistantSchemaUnavailable("Assistant migration head is not ready")

        inspector = sa.inspect(connection)
        table_names = set(inspector.get_table_names())
        for model in _ASSISTANT_MODELS:
            table = model.__table__
            if table.name not in table_names:
                raise AssistantSchemaUnavailable("Assistant table is not ready")
            actual_columns = {
                str(column["name"])
                for column in inspector.get_columns(table.name)
            }
            if actual_columns != set(table.columns.keys()):
                raise AssistantSchemaUnavailable("Assistant table schema is not ready")
            connection.execute(sa.select(table).limit(0))


def is_assistant_schema_unavailable(exc: BaseException) -> bool:
    """Recognize SQLite errors caused by a missing or incompatible schema."""

    if isinstance(exc, AssistantSchemaUnavailable):
        return True
    if not isinstance(exc, DBAPIError):
        return False
    original = exc.orig
    if not isinstance(original, sqlite3.Error):
        return False
    code = getattr(original, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & 0xFF) == sqlite3.SQLITE_ERROR
