"""Create Schedule and idempotency tables.

Revision ID: 20260927_0001
Revises:
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260927_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("completed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("reminder_minutes_before", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "category IN ('INVENTORY', 'RETURN', 'MEETING', 'PERSONAL', 'OTHER')",
            name=op.f("ck_schedules_category"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedules")),
    )
    op.create_index(
        "ix_schedules_active_time",
        "schedules",
        ["deleted_at", "scheduled_at", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "idempotency_records",
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("fingerprint", sa.CHAR(length=64), nullable=False),
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "scope", "idempotency_key", name=op.f("pk_idempotency_records")
        ),
    )
    op.create_index(
        "ix_idempotency_records_expires_at",
        "idempotency_records",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_idempotency_records_expires_at", table_name="idempotency_records"
    )
    op.drop_table("idempotency_records")
    op.drop_index("ix_schedules_active_time", table_name="schedules")
    op.drop_table("schedules")
