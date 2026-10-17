"""Schedule and idempotency persistence models."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UTCDateTime
from app.modules.schedules.category import Category


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        sa.Index(
            "ix_schedules_active_time",
            "deleted_at",
            "scheduled_at",
            "created_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    category: Mapped[Category] = mapped_column(
        sa.Enum(
            Category,
            name="category",
            native_enum=False,
            create_constraint=True,
            length=32,
        ),
        nullable=False,
    )
    completed: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        sa.Index("ix_idempotency_records_expires_at", "expires_at"),
    )

    scope: Mapped[str] = mapped_column(sa.String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(sa.String(255), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(sa.CHAR(64), nullable=False)
    schedule_id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    response_body: Mapped[str] = mapped_column(sa.Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
