"""Marker-aware startup recovery that never re-executes a Schedule write."""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from pydantic import TypeAdapter
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assistant import repository
from app.modules.assistant.decisions import PendingCreateIntent, PendingWriteIntent
from app.modules.assistant.enums import AssistantOutcomeOperation, AssistantResultKind
from app.modules.assistant.model import AssistantRequest, AssistantWriteCommitMode
from app.modules.assistant.runtime_failures import interrupted_snapshot
from app.modules.assistant.safe_write import _success_snapshot
from app.modules.schedules.assistant_write import lookup_create_evidence
from app.modules.schedules.schemas import ScheduleCreate

_PENDING_ADAPTER = TypeAdapter(PendingWriteIntent)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("wall clock must return timezone-aware datetimes")
    return value.astimezone(timezone.utc)


def _create_data(intent: PendingCreateIntent) -> ScheduleCreate:
    return ScheduleCreate.model_validate(
        {
            "title": intent.title,
            "scheduledAt": intent.scheduled_at.isoformat().replace("+00:00", "Z"),
            "category": intent.category.value,
        }
    )


def _require_one(result: object) -> None:
    if int(getattr(result, "rowcount", 0) or 0) != 1:
        raise RuntimeError("Assistant startup recovery CAS lost")


def recover_startup_requests(
    session_factory: sessionmaker[Session], *, recovered_at: datetime
) -> int:
    """Resolve every recoverable row in one IMMEDIATE transaction."""

    recovered_at = _utc(recovered_at)
    interrupted = interrupted_snapshot()
    recovered = 0
    with session_factory() as session:
        with session.begin():
            requests = list(
                session.scalars(
                    sa.select(AssistantRequest)
                    .where(
                        sa.or_(
                            AssistantRequest.status.in_(("accepted", "processing")),
                            sa.and_(
                                AssistantRequest.status == "completed",
                                AssistantRequest.result_kind
                                == AssistantResultKind.UNCERTAIN.value,
                                AssistantRequest.uncertain_resolved_at.is_(None),
                            ),
                        )
                    )
                    .order_by(AssistantRequest.accepted_at, AssistantRequest.id)
                )
            )
            for request in requests:
                if (
                    request.status in {"accepted", "processing"}
                    and not request.write_started
                ):
                    result = session.execute(
                        sa.update(AssistantRequest)
                        .where(
                            AssistantRequest.id == request.id,
                            AssistantRequest.status.in_(("accepted", "processing")),
                            AssistantRequest.write_started.is_(False),
                        )
                        .values(
                            status="completed",
                            result_kind=AssistantResultKind.FAILURE.value,
                            result_json=interrupted,
                            completed_at=recovered_at,
                            retention_started_at=recovered_at,
                            pending_write_version=None,
                            pending_write_json=None,
                        )
                    )
                    _require_one(result)
                    repository.release_write_gate(session, owner_request_id=request.id)
                    recovered += 1
                    continue

                if request.write_commit_mode == AssistantWriteCommitMode.ATOMIC.value:
                    if request.status != "processing":
                        continue
                    result = session.execute(
                        sa.update(AssistantRequest)
                        .where(
                            AssistantRequest.id == request.id,
                            AssistantRequest.status == "processing",
                            AssistantRequest.write_started.is_(True),
                            AssistantRequest.write_commit_mode
                            == AssistantWriteCommitMode.ATOMIC.value,
                        )
                        .values(
                            status="completed",
                            result_kind=AssistantResultKind.FAILURE.value,
                            result_json=interrupted,
                            completed_at=recovered_at,
                            retention_started_at=recovered_at,
                            pending_write_version=None,
                            pending_write_json=None,
                        )
                    )
                    _require_one(result)
                    repository.release_write_gate(session, owner_request_id=request.id)
                    recovered += 1
                    continue

                if (
                    request.write_commit_mode
                    != AssistantWriteCommitMode.IDEMPOTENT_EXTERNAL.value
                    or request.write_operation != "create"
                    or request.create_idempotency_scope is None
                    or request.create_idempotency_key is None
                ):
                    raise RuntimeError(
                        "Assistant recovery found an invalid write marker"
                    )
                intent = _PENDING_ADAPTER.validate_python(request.pending_write_json)
                if not isinstance(intent, PendingCreateIntent):
                    raise RuntimeError(
                        "Assistant create marker has a non-create intent"
                    )
                evidence = lookup_create_evidence(
                    session,
                    scope=request.create_idempotency_scope,
                    idempotency_key=request.create_idempotency_key,
                    data=_create_data(intent),
                )
                result_kind = AssistantResultKind.FAILURE
                result_json = interrupted
                if evidence is not None:
                    result_kind = AssistantResultKind.OUTCOME
                    result_json = _success_snapshot(
                        operation=AssistantOutcomeOperation.CREATE,
                        schedule_id=evidence.schedule_id,
                    )

                if request.status == "processing":
                    result = session.execute(
                        sa.update(AssistantRequest)
                        .where(
                            AssistantRequest.id == request.id,
                            AssistantRequest.status == "processing",
                            AssistantRequest.write_started.is_(True),
                        )
                        .values(
                            status="completed",
                            result_kind=result_kind.value,
                            result_json=result_json,
                            completed_at=recovered_at,
                            retention_started_at=recovered_at,
                            pending_write_version=None,
                            pending_write_json=None,
                        )
                    )
                else:
                    result = session.execute(
                        sa.update(AssistantRequest)
                        .where(
                            AssistantRequest.id == request.id,
                            AssistantRequest.status == "completed",
                            AssistantRequest.result_kind
                            == AssistantResultKind.UNCERTAIN.value,
                            AssistantRequest.uncertain_resolved_at.is_(None),
                        )
                        .values(
                            result_kind=result_kind.value,
                            result_json=result_json,
                            uncertain_resolved_at=recovered_at,
                            retention_started_at=recovered_at,
                            pending_write_version=None,
                            pending_write_json=None,
                        )
                    )
                _require_one(result)
                repository.release_write_gate(session, owner_request_id=request.id)
                recovered += 1
    return recovered
