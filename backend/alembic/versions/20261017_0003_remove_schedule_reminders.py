"""Remove Schedule reminder persistence.

Revision ID: 20261017_0003
Revises: 20261008_0002
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_0003"
down_revision: str | None = "20261008_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_TEMP_TABLE = "_alembic_tmp_schedules"
_EXPECTED_COLUMNS = {
    "id",
    "title",
    "scheduled_at",
    "category",
    "completed",
    "reminder_minutes_before",
    "created_at",
    "updated_at",
    "deleted_at",
}
_EXPECTED_CATEGORIES = {"INBOUND", "INVENTORY", "OUTBOUND", "RETURN", "OTHER"}


def _validate_upgrade_source() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if _BATCH_TEMP_TABLE in tables:
        raise RuntimeError("Schedule reminder migration refused a leftover batch table")
    columns = {column["name"] for column in inspector.get_columns("schedules")}
    if columns != _EXPECTED_COLUMNS:
        raise RuntimeError("Schedule reminder migration refused an unexpected source schema")
    indexes = {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("schedules")
    }
    if indexes != {
        "ix_schedules_active_time": (
            "deleted_at",
            "scheduled_at",
            "created_at",
            "id",
        )
    }:
        raise RuntimeError("Schedule reminder migration refused unexpected indexes")
    categories = set(
        connection.execute(sa.text("SELECT DISTINCT category FROM schedules")).scalars()
    )
    if not categories <= _EXPECTED_CATEGORIES:
        raise RuntimeError("Schedule reminder migration refused unexpected categories")


def upgrade() -> None:
    _validate_upgrade_source()
    with op.batch_alter_table("schedules", recreate="always") as batch_op:
        batch_op.drop_column("reminder_minutes_before")


def downgrade() -> None:
    with op.batch_alter_table("schedules", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column("reminder_minutes_before", sa.Integer(), nullable=True)
        )
