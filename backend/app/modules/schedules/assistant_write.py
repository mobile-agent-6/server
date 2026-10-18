"""Assistant-only Schedule create transaction and recovery evidence validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from uuid import RFC_4122, UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.schedules import repository
from app.modules.schedules.idempotency import canonical_fingerprint
from app.modules.schedules.model import IdempotencyRecord, Schedule
from app.modules.schedules.schemas import (
    ScheduleCreate,
    ScheduleResponse,
    schedule_response,
)

ASSISTANT_IDEMPOTENCY_SCOPE = "assistant-v1"
ASSISTANT_IDEMPOTENCY_TTL = timedelta(hours=24)


class Clock(Protocol):
    def now(self) -> datetime: ...


class AssistantCreateEvidenceInvalid(RuntimeError):
    """An exact key exists but cannot prove the requested create result."""


class AssistantCreateRejected(ValueError):
    """The Schedule became invalid before the external transaction wrote."""


@dataclass(frozen=True)
class AssistantCreateEvidence:
    schedule_id: UUID
    response: ScheduleResponse
    replayed: bool


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock values must be timezone-aware")
    return value.astimezone(timezone.utc)


def _new_uuid(factory: Callable[[], UUID]) -> UUID:
    value = factory()
    if value.version != 4 or value.variant != RFC_4122:
        raise ValueError("Schedule UUID factory must return an RFC variant UUID v4")
    return value


def _response(model: Schedule) -> ScheduleResponse:
    return schedule_response(
        id=model.id,
        title=model.title,
        scheduled_at=model.scheduled_at,
        category=model.category,
        completed=model.completed,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def validate_create_evidence(
    session: Session,
    *,
    record: IdempotencyRecord,
    data: ScheduleCreate,
) -> AssistantCreateEvidence:
    """Strictly prove that one exact record represents the requested Schedule."""

    expected_fingerprint = canonical_fingerprint(data)
    if record.fingerprint != expected_fingerprint:
        raise AssistantCreateEvidenceInvalid("Assistant create fingerprint mismatch")
    try:
        response = ScheduleResponse.model_validate_json(
            record.response_body, strict=True
        )
    except (ValidationError, ValueError) as exc:
        raise AssistantCreateEvidenceInvalid(
            "Assistant create response snapshot is invalid"
        ) from exc
    if response.id != record.schedule_id:
        raise AssistantCreateEvidenceInvalid("Assistant create schedule ID mismatch")
    if (
        response.title != data.title
        or response.scheduled_at != data.scheduled_at
        or response.category is not data.category
        or response.completed
    ):
        raise AssistantCreateEvidenceInvalid("Assistant create snapshot mismatch")
    return AssistantCreateEvidence(
        schedule_id=record.schedule_id,
        response=response,
        replayed=True,
    )


def lookup_create_evidence(
    session: Session,
    *,
    scope: str,
    idempotency_key: str,
    data: ScheduleCreate,
) -> AssistantCreateEvidence | None:
    record = repository.find_idempotency_exact(
        session,
        scope=scope,
        idempotency_key=idempotency_key,
    )
    if record is None:
        return None
    return validate_create_evidence(session, record=record, data=data)


def create_idempotent(
    session_factory: sessionmaker[Session],
    *,
    data: ScheduleCreate,
    scope: str,
    idempotency_key: str,
    clock: Clock,
    uuid_factory: Callable[[], UUID],
) -> AssistantCreateEvidence:
    """Own the external Schedule+idempotency transaction used by Assistant."""

    if scope != ASSISTANT_IDEMPOTENCY_SCOPE:
        raise ValueError("invalid Assistant idempotency scope")
    now = _utc(clock.now())
    with session_factory() as session:
        with session.begin():
            existing = lookup_create_evidence(
                session,
                scope=scope,
                idempotency_key=idempotency_key,
                data=data,
            )
            if existing is not None:
                return existing
            if data.scheduled_at < now:
                raise AssistantCreateRejected("Assistant create is in the past")
            schedule = Schedule(
                id=_new_uuid(uuid_factory),
                title=data.title,
                scheduled_at=data.scheduled_at,
                category=data.category,
                completed=False,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            response = _response(schedule)
            repository.add_schedule(session, schedule)
            repository.add_idempotency_record(
                session,
                IdempotencyRecord(
                    scope=scope,
                    idempotency_key=idempotency_key,
                    fingerprint=canonical_fingerprint(data),
                    schedule_id=schedule.id,
                    response_body=response.model_dump_json(by_alias=True),
                    created_at=now,
                    expires_at=now + ASSISTANT_IDEMPOTENCY_TTL,
                ),
            )
        return AssistantCreateEvidence(
            schedule_id=schedule.id,
            response=response,
            replayed=False,
        )
