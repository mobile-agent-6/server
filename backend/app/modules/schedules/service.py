"""Schedule business rules and transaction boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.database import is_database_unavailable
from app.core.exceptions import (
    DatabaseUnavailable,
    DateRangeTooLarge,
    EmptyUpdate,
    IdempotencyKeyConflict,
    InvalidDateRange,
    ScheduleInThePast,
    ScheduleNotFound,
)
from app.modules.schedules import repository
from app.modules.schedules.category import Category
from app.modules.schedules.idempotency import canonical_fingerprint
from app.modules.schedules.model import IdempotencyRecord, Schedule
from app.modules.schedules.schemas import (
    ScheduleCreate,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleUpdate,
    schedule_response,
)


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True)
class CreateScheduleResult:
    schedule_id: UUID
    body_json: str
    replayed: bool


IDEMPOTENCY_SCOPE = "default"
IDEMPOTENCY_TTL = timedelta(hours=24)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _from_model(model: Schedule, **overrides: object) -> ScheduleResponse:
    values: dict[str, object] = {
        "id": model.id,
        "title": model.title,
        "scheduled_at": model.scheduled_at,
        "category": model.category,
        "completed": model.completed,
        "reminder_minutes_before": model.reminder_minutes_before,
        "created_at": model.created_at,
        "updated_at": model.updated_at,
    }
    values.update(overrides)
    return schedule_response(**values)  # type: ignore[arg-type]


def _translate_db_error(session: Session, exc: BaseException) -> None:
    session.rollback()
    if is_database_unavailable(exc):
        raise DatabaseUnavailable from exc
    raise exc


def _idempotency_result(
    record: IdempotencyRecord, fingerprint: str
) -> CreateScheduleResult:
    if record.fingerprint != fingerprint:
        raise IdempotencyKeyConflict
    return CreateScheduleResult(
        schedule_id=record.schedule_id,
        body_json=record.response_body,
        replayed=True,
    )


def list_schedules(
    session: Session,
    *,
    from_utc: datetime,
    to_utc: datetime,
    category: Category | None,
    completed: bool | None,
) -> ScheduleListResponse:
    from_utc = _utc(from_utc)
    to_utc = _utc(to_utc)
    if to_utc <= from_utc:
        raise InvalidDateRange
    if to_utc - from_utc > timedelta(days=92):
        raise DateRangeTooLarge
    try:
        schedules = repository.list_active(
            session,
            from_utc=from_utc,
            to_utc=to_utc,
            category=category,
            completed=completed,
        )
    except SQLAlchemyError as exc:
        _translate_db_error(session, exc)
    return ScheduleListResponse(items=[_from_model(schedule) for schedule in schedules])


def get_schedule(session: Session, schedule_id: UUID) -> ScheduleResponse:
    try:
        schedule = repository.get_active_by_id(session, schedule_id)
    except SQLAlchemyError as exc:
        _translate_db_error(session, exc)
    if schedule is None:
        raise ScheduleNotFound
    return _from_model(schedule)


def create_schedule(
    session: Session,
    *,
    data: ScheduleCreate,
    idempotency_key: str,
    clock: Clock,
) -> CreateScheduleResult:
    now = _utc(clock.now())
    fingerprint = canonical_fingerprint(data)
    try:
        existing = repository.find_active_idempotency(
            session,
            scope=IDEMPOTENCY_SCOPE,
            idempotency_key=idempotency_key,
            now=now,
        )
    except SQLAlchemyError as exc:
        _translate_db_error(session, exc)
    if existing is not None:
        return _idempotency_result(existing, fingerprint)

    if data.scheduled_at < now:
        raise ScheduleInThePast
    schedule = Schedule(
        id=uuid4(),
        title=data.title,
        scheduled_at=data.scheduled_at,
        category=data.category,
        completed=False,
        reminder_minutes_before=data.reminder_minutes_before,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    response = _from_model(schedule)
    body_json = response.model_dump_json(by_alias=True)
    record = IdempotencyRecord(
        scope=IDEMPOTENCY_SCOPE,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        schedule_id=schedule.id,
        response_body=body_json,
        created_at=now,
        expires_at=now + IDEMPOTENCY_TTL,
    )
    try:
        repository.delete_expired_idempotency(
            session,
            scope=IDEMPOTENCY_SCOPE,
            idempotency_key=idempotency_key,
            now=now,
        )
        repository.add_schedule(session, schedule)
        repository.add_idempotency_record(session, record)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        try:
            concurrent = repository.find_active_idempotency(
                session,
                scope=IDEMPOTENCY_SCOPE,
                idempotency_key=idempotency_key,
                now=now,
            )
        except SQLAlchemyError as lookup_exc:
            _translate_db_error(session, lookup_exc)
        if concurrent is None:
            raise exc
        return _idempotency_result(concurrent, fingerprint)
    except SQLAlchemyError as exc:
        _translate_db_error(session, exc)
    return CreateScheduleResult(
        schedule_id=schedule.id,
        body_json=body_json,
        replayed=False,
    )


def update_schedule(
    session: Session,
    *,
    schedule_id: UUID,
    data: ScheduleUpdate,
    clock: Clock,
) -> ScheduleResponse:
    if not data.model_fields_set:
        raise EmptyUpdate
    try:
        schedule = repository.get_active_by_id(session, schedule_id)
    except SQLAlchemyError as exc:
        _translate_db_error(session, exc)
    if schedule is None:
        raise ScheduleNotFound

    changes: dict[str, object] = {}
    for field in data.model_fields_set:
        value = getattr(data, field)
        if field == "scheduled_at":
            value = _utc(value)
        if value != getattr(schedule, field):
            changes[field] = value
    if not changes:
        return _from_model(schedule)

    now = _utc(clock.now())
    if "scheduled_at" in changes and changes["scheduled_at"] < now:
        raise ScheduleInThePast
    response = _from_model(schedule, **changes, updated_at=now)
    try:
        changed = repository.update_active(
            session, schedule_id=schedule_id, changes=changes, updated_at=now
        )
        if changed == 0:
            session.rollback()
            raise ScheduleNotFound
        session.commit()
    except SQLAlchemyError as exc:
        _translate_db_error(session, exc)
    return response


def delete_schedule(session: Session, *, schedule_id: UUID, clock: Clock) -> None:
    try:
        schedule = repository.get_by_id_including_deleted(session, schedule_id)
    except SQLAlchemyError as exc:
        _translate_db_error(session, exc)
    if schedule is None:
        raise ScheduleNotFound
    if schedule.deleted_at is not None:
        return

    now = _utc(clock.now())
    try:
        changed = repository.soft_delete_active(
            session, schedule_id=schedule_id, deleted_at=now
        )
        if changed == 0:
            session.rollback()
            return
        session.commit()
    except SQLAlchemyError as exc:
        _translate_db_error(session, exc)
