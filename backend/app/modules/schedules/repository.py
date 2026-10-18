"""SQLAlchemy queries for Schedule persistence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.schedules.category import Category
from app.modules.schedules.model import IdempotencyRecord, Schedule


def list_active(
    session: Session,
    *,
    from_utc: datetime,
    to_utc: datetime,
    category: Category | None,
    completed: bool | None,
    limit: int | None = None,
) -> list[Schedule]:
    statement = sa.select(Schedule).where(
        Schedule.deleted_at.is_(None),
        Schedule.scheduled_at >= from_utc,
        Schedule.scheduled_at < to_utc,
    )
    if category is not None:
        statement = statement.where(Schedule.category == category)
    if completed is not None:
        statement = statement.where(Schedule.completed == completed)
    statement = statement.order_by(
        Schedule.scheduled_at.asc(), Schedule.created_at.asc(), Schedule.id.asc()
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.scalars(statement))


def get_active_by_id(session: Session, schedule_id: UUID) -> Schedule | None:
    return session.scalar(
        sa.select(Schedule).where(
            Schedule.id == schedule_id, Schedule.deleted_at.is_(None)
        )
    )


def get_by_id_including_deleted(session: Session, schedule_id: UUID) -> Schedule | None:
    return session.get(Schedule, schedule_id)


def add_schedule(session: Session, schedule: Schedule) -> None:
    session.add(schedule)


def find_active_idempotency(
    session: Session,
    *,
    scope: str,
    idempotency_key: str,
    now: datetime,
) -> IdempotencyRecord | None:
    return session.scalar(
        sa.select(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.idempotency_key == idempotency_key,
            IdempotencyRecord.expires_at > now,
        )
    )


def find_idempotency_exact(
    session: Session,
    *,
    scope: str,
    idempotency_key: str,
) -> IdempotencyRecord | None:
    """Read recovery evidence without applying the public API TTL rule."""

    return session.get(IdempotencyRecord, (scope, idempotency_key))


def delete_expired_idempotency(
    session: Session,
    *,
    scope: str,
    idempotency_key: str,
    now: datetime,
) -> int:
    result = session.execute(
        sa.delete(IdempotencyRecord).where(
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.idempotency_key == idempotency_key,
            IdempotencyRecord.expires_at <= now,
        )
    )
    return int(result.rowcount or 0)


def add_idempotency_record(
    session: Session, record: IdempotencyRecord
) -> None:
    session.add(record)


def update_active(
    session: Session,
    *,
    schedule_id: UUID,
    changes: dict[str, object],
    updated_at: datetime,
) -> int:
    values = {**changes, "updated_at": updated_at}
    result = session.execute(
        sa.update(Schedule)
        .where(Schedule.id == schedule_id, Schedule.deleted_at.is_(None))
        .values(**values)
    )
    return int(result.rowcount or 0)


def soft_delete_active(
    session: Session, *, schedule_id: UUID, deleted_at: datetime
) -> int:
    result = session.execute(
        sa.update(Schedule)
        .where(Schedule.id == schedule_id, Schedule.deleted_at.is_(None))
        .values(deleted_at=deleted_at, updated_at=deleted_at)
    )
    return int(result.rowcount or 0)


def update_active_if_unchanged(
    session: Session,
    *,
    schedule_id: UUID,
    expected_updated_at: datetime,
    changes: dict[str, object],
    updated_at: datetime,
) -> int:
    """Apply an Assistant update CAS without committing the caller transaction."""

    values = {**changes, "updated_at": updated_at}
    result = session.execute(
        sa.update(Schedule)
        .where(
            Schedule.id == schedule_id,
            Schedule.deleted_at.is_(None),
            Schedule.updated_at == expected_updated_at,
        )
        .values(**values)
    )
    return int(result.rowcount or 0)


def soft_delete_active_if_unchanged(
    session: Session,
    *,
    schedule_id: UUID,
    expected_updated_at: datetime,
    deleted_at: datetime,
) -> int:
    """Apply an Assistant delete CAS without committing the caller transaction."""

    result = session.execute(
        sa.update(Schedule)
        .where(
            Schedule.id == schedule_id,
            Schedule.deleted_at.is_(None),
            Schedule.updated_at == expected_updated_at,
        )
        .values(deleted_at=deleted_at, updated_at=deleted_at)
    )
    return int(result.rowcount or 0)
