"""Read-only Schedule boundary used by Assistant decision processing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.schedules import repository
from app.modules.schedules.category import Category
from app.modules.schedules.model import Schedule


@dataclass(frozen=True)
class ScheduleRead:
    id: UUID
    title: str
    scheduled_at: datetime
    category: Category
    completed: bool
    updated_at: datetime


class ScheduleReadPort(Protocol):
    def get(self, schedule_id: UUID) -> ScheduleRead | None: ...

    def list(
        self,
        *,
        from_utc: datetime,
        to_utc: datetime,
        category: Category | None,
        completed: bool | None,
        limit: int,
    ) -> list[ScheduleRead]: ...


class ScheduleReadUnavailable(RuntimeError):
    """The read-only Schedule boundary could not access persistence."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Schedule read datetimes must be timezone-aware")
    return value.astimezone(timezone.utc)


def _from_model(model: Schedule) -> ScheduleRead:
    return ScheduleRead(
        id=model.id,
        title=model.title,
        scheduled_at=model.scheduled_at,
        category=model.category,
        completed=model.completed,
        updated_at=model.updated_at,
    )


class SqlAlchemyScheduleReader:
    """Use an ordinary read session and expose no Schedule write method."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def get(self, schedule_id: UUID) -> ScheduleRead | None:
        try:
            with self._session_factory() as session:
                model = repository.get_active_by_id(session, schedule_id)
                return None if model is None else _from_model(model)
        except SQLAlchemyError as exc:
            raise ScheduleReadUnavailable("Schedule read failed") from exc

    def list(
        self,
        *,
        from_utc: datetime,
        to_utc: datetime,
        category: Category | None,
        completed: bool | None,
        limit: int,
    ) -> list[ScheduleRead]:
        normalized_from = _utc(from_utc)
        normalized_to = _utc(to_utc)
        if normalized_to <= normalized_from:
            raise ValueError("to must be after from")
        if normalized_to - normalized_from > timedelta(days=92):
            raise ValueError("date range cannot exceed 92 days")
        if not 1 <= limit <= 101:
            raise ValueError("Assistant Schedule read limit must be 1..101")
        try:
            with self._session_factory() as session:
                models = repository.list_active(
                    session,
                    from_utc=normalized_from,
                    to_utc=normalized_to,
                    category=category,
                    completed=completed,
                    limit=limit,
                )
                return [_from_model(model) for model in models]
        except SQLAlchemyError as exc:
            raise ScheduleReadUnavailable("Schedule read failed") from exc
