"""Single-request Schedule write orchestration owned by the backend runtime."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from threading import Event
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assistant import repository
from app.modules.assistant.decisions import (
    ConfirmationQuestionIntent,
    PendingCreateIntent,
    PendingDeleteIntent,
    PendingUpdateIntent,
    PendingWriteIntent,
)
from app.modules.assistant.enums import (
    AssistantConfirmationOperation,
    AssistantFailureCode,
    AssistantFailureNextAction,
    AssistantOutcome,
    AssistantOutcomeOperation,
    AssistantResultKind,
    AssistantUncertainCode,
    AssistantUncertainNextAction,
)
from app.modules.assistant.model import (
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
    AssistantWriteCommitMode,
    AssistantWriteGate,
    AssistantWriteOperation,
)
from app.modules.assistant.processor import ProcessOneResult
from app.modules.assistant.runtime_failures import PROCESSING_TIMEOUT_TEXT
from app.modules.assistant.runtime_guard import ProcessDeadlineGuard
from app.modules.assistant.schemas import (
    AssistantConfirmationChanges,
    AssistantResultConfirmation,
    AssistantResultFailure,
    AssistantResultOutcome,
    AssistantResultUncertain,
    AssistantScheduleSummary,
)
from app.modules.assistant.serialization import serialize_snapshot
from app.modules.schedules import repository as schedule_repository
from app.modules.schedules.assistant_write import (
    ASSISTANT_IDEMPOTENCY_SCOPE,
    AssistantCreateEvidence,
    AssistantCreateRejected,
    create_idempotent,
    lookup_create_evidence,
)
from app.modules.schedules.model import Schedule
from app.modules.schedules.schemas import ScheduleCreate, ScheduleUpdate

QUESTION_TTL = timedelta(minutes=10)
CREATE_SUCCESS_TEXT = "일정을 등록했어요."
UPDATE_SUCCESS_TEXT = "일정을 변경했어요."
DELETE_SUCCESS_TEXT = "일정을 삭제했어요."
NO_MATCH_TEXT = "조건에 맞는 일정을 찾지 못했어요."
ALREADY_APPLIED_TEXT = "이미 요청한 상태예요."
RECONFIRM_TEXT = "일정이 변경됐어요. 현재 내용을 확인하고 다시 승인해 주세요."
SCHEDULE_REJECTED_TEXT = (
    "일정에 적용할 수 없는 요청이에요. 내용을 확인해 다시 말씀해 주세요."
)
SCHEDULE_UNAVAILABLE_TEXT = (
    "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요."
)
INTERNAL_ERROR_TEXT = "요청을 처리하지 못했어요. 잠시 후 다시 시도해 주세요."
UNCERTAIN_TEXT = (
    "일정 처리 결과를 확인할 수 없어요. 일정 상태를 먼저 확인해 주세요."
)

_PENDING_ADAPTER = TypeAdapter(PendingWriteIntent)
_DEFAULT_GATE_RETRY_DELAYS = (0.01, 0.02, 0.05, 0.1, 0.25)


class WallClock(Protocol):
    def now(self) -> datetime: ...


class _AcquireResult(StrEnum):
    ACQUIRED = "acquired"
    COMPLETED = "completed"
    CONTENDED = "contended"
    LOST = "lost"


class _LostFinalize(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock values must be timezone-aware")
    return value.astimezone(timezone.utc)


def _snapshot(model: object) -> dict[str, object]:
    dump = getattr(model, "model_dump")
    value = dump(mode="json", by_alias=True, exclude_none=True)
    serialize_snapshot(value)
    return value


def _failure_snapshot(
    code: AssistantFailureCode,
    next_action: AssistantFailureNextAction,
    text: str,
) -> dict[str, object]:
    return _snapshot(
        AssistantResultFailure(
            kind=AssistantResultKind.FAILURE,
            text=text,
            code=code,
            next_action=next_action,
        )
    )


def uncertain_snapshot() -> dict[str, object]:
    return _snapshot(
        AssistantResultUncertain(
            kind=AssistantResultKind.UNCERTAIN,
            text=UNCERTAIN_TEXT,
            code=AssistantUncertainCode.EXECUTION_RESULT_UNKNOWN,
            next_action=AssistantUncertainNextAction.CHECK_STATUS,
        )
    )


def _success_snapshot(operation: AssistantOutcomeOperation, schedule_id: UUID):
    text = {
        AssistantOutcomeOperation.CREATE: CREATE_SUCCESS_TEXT,
        AssistantOutcomeOperation.UPDATE: UPDATE_SUCCESS_TEXT,
        AssistantOutcomeOperation.DELETE: DELETE_SUCCESS_TEXT,
    }[operation]
    return _snapshot(
        AssistantResultOutcome(
            kind=AssistantResultKind.OUTCOME,
            text=text,
            outcome=AssistantOutcome.SUCCESS,
            schedules_changed=True,
            operation=operation,
            affected_schedule_ids=[schedule_id],
        )
    )


def _no_match_snapshot(operation: AssistantOutcomeOperation):
    return _snapshot(
        AssistantResultOutcome(
            kind=AssistantResultKind.OUTCOME,
            text=NO_MATCH_TEXT,
            outcome=AssistantOutcome.NO_MATCH,
            schedules_changed=False,
            operation=operation,
        )
    )


def _already_applied_snapshot():
    return _snapshot(
        AssistantResultOutcome(
            kind=AssistantResultKind.OUTCOME,
            text=ALREADY_APPLIED_TEXT,
            outcome=AssistantOutcome.NO_MATCH,
            schedules_changed=False,
            operation=AssistantOutcomeOperation.UPDATE,
        )
    )


def _schedule_summary(schedule: Schedule) -> AssistantScheduleSummary:
    return AssistantScheduleSummary(
        id=schedule.id,
        title=schedule.title,
        scheduled_at=schedule.scheduled_at,
        category=schedule.category,
        completed=schedule.completed,
        updated_at=schedule.updated_at,
    )


def _schedule_create(intent: PendingCreateIntent) -> ScheduleCreate:
    return ScheduleCreate.model_validate(
        {
            "title": intent.title,
            "scheduledAt": intent.scheduled_at.isoformat().replace("+00:00", "Z"),
            "category": intent.category.value,
        }
    )


def _schedule_update(intent: PendingUpdateIntent) -> ScheduleUpdate:
    return ScheduleUpdate.model_validate(
        intent.changes.model_dump(mode="json", by_alias=True, exclude_none=True)
    )


def _update_values(update: ScheduleUpdate) -> dict[str, object]:
    return {field: getattr(update, field) for field in update.model_fields_set}


def _remaining_changes(
    update: ScheduleUpdate, schedule: Schedule
) -> AssistantConfirmationChanges | None:
    remaining_fields = {
        field
        for field in update.model_fields_set
        if getattr(update, field) != getattr(schedule, field)
    }
    if not remaining_fields:
        return None
    json_values = update.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
        include=remaining_fields,
    )
    ScheduleUpdate.model_validate(json_values)
    return AssistantConfirmationChanges.model_validate(json_values)


def _gate_owned(session: Session, request_id: UUID) -> bool:
    return (
        session.scalar(
            sa.select(AssistantWriteGate.owner_request_id).where(
                AssistantWriteGate.singleton_key == 1
            )
        )
        == request_id
    )


def _complete_marked(
    session: Session,
    *,
    request_id: UUID,
    result_kind: AssistantResultKind,
    result_json: dict[str, object],
    completed_at: datetime,
    retention_started_at: datetime | None,
    require_gate: bool = True,
) -> None:
    conditions = [
        AssistantRequest.id == request_id,
        AssistantRequest.status == "processing",
        AssistantRequest.write_started.is_(True),
    ]
    if require_gate:
        conditions.append(
            sa.exists(
                sa.select(sa.literal(1)).where(
                    AssistantWriteGate.singleton_key == 1,
                    AssistantWriteGate.owner_request_id == request_id,
                )
            )
        )
    result = session.execute(
        sa.update(AssistantRequest)
        .where(*conditions)
        .values(
            status="completed",
            result_kind=result_kind.value,
            result_json=result_json,
            completed_at=completed_at,
            retention_started_at=retention_started_at,
            pending_write_version=None,
            pending_write_json=None,
        )
    )
    if int(result.rowcount or 0) != 1:
        raise _LostFinalize("Assistant marked completion CAS lost")


def _release_required(session: Session, request_id: UUID) -> None:
    if not repository.release_write_gate(session, owner_request_id=request_id):
        raise _LostFinalize("Assistant gate release CAS lost")


def complete_marked_failure(
    session: Session,
    *,
    request_id: UUID,
    result_json: dict[str, object],
    at: datetime,
) -> None:
    _complete_marked(
        session,
        request_id=request_id,
        result_kind=AssistantResultKind.FAILURE,
        result_json=result_json,
        completed_at=at,
        retention_started_at=at,
    )
    _release_required(session, request_id)


def complete_marked_uncertain(
    session: Session, *, request_id: UUID, at: datetime
) -> None:
    result = session.execute(
        sa.update(AssistantRequest)
        .where(
            AssistantRequest.id == request_id,
            AssistantRequest.status == "processing",
            AssistantRequest.write_started.is_(True),
            sa.exists(
                sa.select(sa.literal(1)).where(
                    AssistantWriteGate.singleton_key == 1,
                    AssistantWriteGate.owner_request_id == request_id,
                )
            ),
        )
        .values(
            status="completed",
            result_kind=AssistantResultKind.UNCERTAIN.value,
            result_json=uncertain_snapshot(),
            completed_at=at,
            retention_started_at=None,
            had_uncertain=True,
            uncertain_at=sa.func.coalesce(AssistantRequest.uncertain_at, at),
            uncertain_resolved_at=None,
        )
    )
    if int(result.rowcount or 0) != 1:
        raise _LostFinalize("Assistant uncertain CAS lost")


def timeout_request(
    session: Session,
    *,
    request_id: UUID,
    at: datetime,
    prewrite_result_json: dict[str, object],
) -> repository.PrewriteCompletionResult:
    """Resolve a due request after the sweeper wins SQLite's write lock."""

    request = repository.get_request(session, request_id)
    if request is None or request.status == "completed":
        return repository.PrewriteCompletionResult.NOOP
    if not request.write_started:
        return repository.complete_prewrite_request(
            session,
            request_id=request_id,
            result_json=prewrite_result_json,
            at=at,
        )
    if request.write_commit_mode == AssistantWriteCommitMode.ATOMIC.value:
        complete_marked_failure(
            session,
            request_id=request_id,
            result_json=prewrite_result_json,
            at=at,
        )
        return repository.PrewriteCompletionResult.COMPLETED
    if (
        request.write_commit_mode == AssistantWriteCommitMode.IDEMPOTENT_EXTERNAL.value
        and request.write_operation == AssistantWriteOperation.CREATE.value
    ):
        complete_marked_uncertain(session, request_id=request_id, at=at)
        return repository.PrewriteCompletionResult.COMPLETED
    raise RuntimeError("Assistant timeout found an invalid write marker")


class AssistantSafeWriter:
    """Validate one persisted intent, mark it, and execute it at most once."""

    def __init__(
        self,
        *,
        assistant_session_factory: sessionmaker[Session],
        schedule_session_factory: sessionmaker[Session],
        clock: WallClock,
        uuid_factory: Callable[[], UUID],
        gate_waiter: Callable[[float], None] | None = None,
        gate_retry_delays: tuple[float, ...] = _DEFAULT_GATE_RETRY_DELAYS,
        on_unhealthy: Callable[[], None] | None = None,
        after_marker: Callable[[UUID], None] | None = None,
        after_create_commit: Callable[[UUID], None] | None = None,
    ) -> None:
        if not gate_retry_delays or any(delay < 0 for delay in gate_retry_delays):
            raise ValueError("gate retry delays must be non-negative")
        self._assistant_sessions = assistant_session_factory
        self._schedule_sessions = schedule_session_factory
        self._clock = clock
        self._uuid_factory = uuid_factory
        self._gate_waiter = gate_waiter or (lambda delay: Event().wait(delay))
        self._gate_retry_delays = gate_retry_delays
        self._on_unhealthy = on_unhealthy or (lambda: None)
        self._after_marker = after_marker
        self._after_create_commit = after_create_commit

    def _unhealthy(self) -> ProcessOneResult:
        self._on_unhealthy()
        return ProcessOneResult.LOST_FINALIZE

    def _load_pending(self, request_id: UUID) -> PendingWriteIntent:
        with self._assistant_sessions() as session:
            request = repository.get_request(session, request_id)
            if (
                request is None
                or request.status != "processing"
                or request.write_started
                or request.pending_write_version != 1
            ):
                raise _LostFinalize("pending Assistant request is unavailable")
            return _PENDING_ADAPTER.validate_python(request.pending_write_json)

    def _complete_prewrite_failure(
        self,
        request_id: UUID,
        *,
        code: AssistantFailureCode,
        next_action: AssistantFailureNextAction,
        text: str,
    ) -> ProcessOneResult:
        snapshot = _failure_snapshot(code, next_action, text)
        try:
            with self._assistant_sessions() as session:
                with session.begin():
                    outcome = repository.complete_prewrite_request(
                        session,
                        request_id=request_id,
                        result_json=snapshot,
                        at=_utc(self._clock.now()),
                    )
            if outcome is repository.PrewriteCompletionResult.COMPLETED:
                return ProcessOneResult.COMPLETED
            return ProcessOneResult.LOST_FINALIZE
        except Exception:
            return self._unhealthy()

    def _acquire_once(
        self,
        request_id: UUID,
        *,
        operation: AssistantWriteOperation,
        guard: ProcessDeadlineGuard,
    ) -> _AcquireResult:
        with self._assistant_sessions() as session:
            with session.begin():
                if guard.stop_requested():
                    return _AcquireResult.LOST
                if guard.deadline_reached():
                    outcome = repository.complete_prewrite_request(
                        session,
                        request_id=request_id,
                        result_json=_failure_snapshot(
                            AssistantFailureCode.PROCESSING_TIMEOUT,
                            AssistantFailureNextAction.RETRY_LATER,
                            PROCESSING_TIMEOUT_TEXT,
                        ),
                        at=_utc(self._clock.now()),
                    )
                    return (
                        _AcquireResult.COMPLETED
                        if outcome is repository.PrewriteCompletionResult.COMPLETED
                        else _AcquireResult.LOST
                    )
                request = repository.get_request(session, request_id)
                if (
                    request is None
                    or request.status != "processing"
                    or request.write_started
                    or request.pending_write_version != 1
                ):
                    return _AcquireResult.LOST
                _PENDING_ADAPTER.validate_python(request.pending_write_json)

                unresolved = repository.find_oldest_unacknowledged_uncertain(session)
                if unresolved is not None:
                    outcome = repository.complete_prewrite_request(
                        session,
                        request_id=request_id,
                        result_json=_failure_snapshot(
                            AssistantFailureCode.SCHEDULE_UNAVAILABLE,
                            AssistantFailureNextAction.RETRY_LATER,
                            SCHEDULE_UNAVAILABLE_TEXT,
                        ),
                        at=_utc(self._clock.now()),
                    )
                    return (
                        _AcquireResult.COMPLETED
                        if outcome is repository.PrewriteCompletionResult.COMPLETED
                        else _AcquireResult.LOST
                    )

                gate = repository.get_write_gate(session)
                if gate.owner_request_id is not None:
                    owner = repository.get_request(session, gate.owner_request_id)
                    owner_is_running = (
                        owner is not None and owner.status == "processing"
                    )
                    owner_is_unacknowledged = bool(
                        owner is not None
                        and owner.had_uncertain
                        and owner.uncertain_acknowledged_at is None
                        and owner.uncertain_resolved_at is None
                    )
                    if owner_is_unacknowledged:
                        outcome = repository.complete_prewrite_request(
                            session,
                            request_id=request_id,
                            result_json=_failure_snapshot(
                                AssistantFailureCode.SCHEDULE_UNAVAILABLE,
                                AssistantFailureNextAction.RETRY_LATER,
                                SCHEDULE_UNAVAILABLE_TEXT,
                            ),
                            at=_utc(self._clock.now()),
                        )
                        return (
                            _AcquireResult.COMPLETED
                            if outcome is repository.PrewriteCompletionResult.COMPLETED
                            else _AcquireResult.LOST
                        )
                    if owner_is_running:
                        return _AcquireResult.CONTENDED
                    repository.release_write_gate(
                        session, owner_request_id=gate.owner_request_id
                    )

                acquired_at = _utc(self._clock.now())
                gate_result = session.execute(
                    sa.update(AssistantWriteGate)
                    .where(
                        AssistantWriteGate.singleton_key == 1,
                        AssistantWriteGate.owner_request_id.is_(None),
                    )
                    .values(
                        owner_request_id=request_id,
                        acquired_at=acquired_at,
                        generation=AssistantWriteGate.generation + 1,
                    )
                )
                if int(gate_result.rowcount or 0) != 1:
                    return _AcquireResult.CONTENDED
                commit_mode = (
                    AssistantWriteCommitMode.IDEMPOTENT_EXTERNAL
                    if operation is AssistantWriteOperation.CREATE
                    else AssistantWriteCommitMode.ATOMIC
                )
                marker_values: dict[str, object] = {
                    "write_started": True,
                    "write_started_at": acquired_at,
                    "write_operation": operation.value,
                    "write_commit_mode": commit_mode.value,
                }
                if operation is AssistantWriteOperation.CREATE:
                    marker_values.update(
                        {
                            "create_idempotency_scope": ASSISTANT_IDEMPOTENCY_SCOPE,
                            "create_idempotency_key": str(request_id),
                        }
                    )
                marker = session.execute(
                    sa.update(AssistantRequest)
                    .where(
                        AssistantRequest.id == request_id,
                        AssistantRequest.status == "processing",
                        AssistantRequest.write_started.is_(False),
                    )
                    .values(**marker_values)
                )
                if int(marker.rowcount or 0) != 1:
                    raise _LostFinalize("Assistant marker CAS lost")
                return _AcquireResult.ACQUIRED

    def _acquire(
        self,
        request_id: UUID,
        *,
        operation: AssistantWriteOperation,
        guard: ProcessDeadlineGuard,
    ) -> ProcessOneResult | None:
        for attempt in range(len(self._gate_retry_delays) + 1):
            try:
                result = self._acquire_once(
                    request_id, operation=operation, guard=guard
                )
            except (ValidationError, ValueError):
                return self._complete_prewrite_failure(
                    request_id,
                    code=AssistantFailureCode.INTERNAL_ERROR,
                    next_action=AssistantFailureNextAction.NONE,
                    text=INTERNAL_ERROR_TEXT,
                )
            except Exception:
                return self._unhealthy()
            if result is _AcquireResult.ACQUIRED:
                return None
            if result is _AcquireResult.COMPLETED:
                return ProcessOneResult.COMPLETED
            if result is _AcquireResult.LOST:
                return ProcessOneResult.LOST_FINALIZE
            if attempt < len(self._gate_retry_delays):
                self._gate_waiter(self._gate_retry_delays[attempt])
        return self._complete_prewrite_failure(
            request_id,
            code=AssistantFailureCode.SCHEDULE_UNAVAILABLE,
            next_action=AssistantFailureNextAction.RETRY_LATER,
            text=SCHEDULE_UNAVAILABLE_TEXT,
        )

    def _marked_preflight(
        self, request_id: UUID, guard: ProcessDeadlineGuard
    ) -> ProcessOneResult | None:
        try:
            with self._assistant_sessions() as session:
                with session.begin():
                    request = repository.get_request(session, request_id)
                    if (
                        request is None
                        or request.status != "processing"
                        or not request.write_started
                        or not _gate_owned(session, request_id)
                    ):
                        return ProcessOneResult.LOST_FINALIZE
                    if guard.deadline_reached():
                        complete_marked_failure(
                            session,
                            request_id=request_id,
                            result_json=_failure_snapshot(
                                AssistantFailureCode.PROCESSING_TIMEOUT,
                                AssistantFailureNextAction.RETRY_LATER,
                                PROCESSING_TIMEOUT_TEXT,
                            ),
                            at=_utc(self._clock.now()),
                        )
                        return ProcessOneResult.COMPLETED
                    if guard.stop_requested():
                        return ProcessOneResult.LOST_FINALIZE
            return None
        except Exception:
            return self._unhealthy()

    def _create_evidence(
        self, request_id: UUID, data: ScheduleCreate
    ) -> AssistantCreateEvidence | None:
        with self._schedule_sessions() as session:
            return lookup_create_evidence(
                session,
                scope=ASSISTANT_IDEMPOTENCY_SCOPE,
                idempotency_key=str(request_id),
                data=data,
            )

    def resolve_create_evidence(self, *, request_id: UUID) -> ProcessOneResult:
        """Resolve only from an exact record; never starts a Schedule write."""

        try:
            with self._assistant_sessions() as session:
                request = repository.get_request(session, request_id)
                if (
                    request is None
                    or request.write_operation != AssistantWriteOperation.CREATE.value
                    or request.write_commit_mode
                    != AssistantWriteCommitMode.IDEMPOTENT_EXTERNAL.value
                    or request.create_idempotency_scope != ASSISTANT_IDEMPOTENCY_SCOPE
                    or request.create_idempotency_key != str(request_id)
                ):
                    return ProcessOneResult.LOST_FINALIZE
                intent = _PENDING_ADAPTER.validate_python(request.pending_write_json)
                if not isinstance(intent, PendingCreateIntent):
                    return self._unhealthy()
            evidence = self._create_evidence(request_id, _schedule_create(intent))
            if evidence is None:
                return ProcessOneResult.LOST_FINALIZE
            return self._finalize_create_success(request_id, evidence)
        except Exception:
            return self._unhealthy()

    def _finalize_create_success(
        self, request_id: UUID, evidence: AssistantCreateEvidence
    ) -> ProcessOneResult:
        now = _utc(self._clock.now())
        snapshot = _success_snapshot(
            AssistantOutcomeOperation.CREATE, evidence.schedule_id
        )
        try:
            with self._assistant_sessions() as session:
                with session.begin():
                    request = repository.get_request(session, request_id)
                    if request is None:
                        return ProcessOneResult.LOST_FINALIZE
                    if request.status == "completed":
                        if (
                            request.result_kind == AssistantResultKind.UNCERTAIN.value
                            and request.uncertain_resolved_at is None
                        ):
                            result = session.execute(
                                sa.update(AssistantRequest)
                                .where(
                                    AssistantRequest.id == request_id,
                                    AssistantRequest.result_kind
                                    == AssistantResultKind.UNCERTAIN.value,
                                    AssistantRequest.uncertain_resolved_at.is_(None),
                                )
                                .values(
                                    result_kind=AssistantResultKind.OUTCOME.value,
                                    result_json=snapshot,
                                    uncertain_resolved_at=now,
                                    retention_started_at=now,
                                    pending_write_version=None,
                                    pending_write_json=None,
                                )
                            )
                            if int(result.rowcount or 0) != 1:
                                raise _LostFinalize("late create resolution CAS lost")
                            repository.release_write_gate(
                                session, owner_request_id=request_id
                            )
                            return ProcessOneResult.COMPLETED
                        return ProcessOneResult.COMPLETED
                    _complete_marked(
                        session,
                        request_id=request_id,
                        result_kind=AssistantResultKind.OUTCOME,
                        result_json=snapshot,
                        completed_at=now,
                        retention_started_at=now,
                    )
                    _release_required(session, request_id)
                    return ProcessOneResult.COMPLETED
        except _LostFinalize:
            return ProcessOneResult.LOST_FINALIZE
        except Exception:
            return self._unhealthy()

    def _mark_create_uncertain(self, request_id: UUID) -> ProcessOneResult:
        try:
            with self._assistant_sessions() as session:
                with session.begin():
                    request = repository.get_request(session, request_id)
                    if request is not None and request.status == "completed":
                        return ProcessOneResult.COMPLETED
                    complete_marked_uncertain(
                        session,
                        request_id=request_id,
                        at=_utc(self._clock.now()),
                    )
            return ProcessOneResult.COMPLETED
        except Exception:
            return self._unhealthy()

    def _mark_create_rejected(self, request_id: UUID) -> ProcessOneResult:
        try:
            with self._assistant_sessions() as session:
                with session.begin():
                    complete_marked_failure(
                        session,
                        request_id=request_id,
                        result_json=_failure_snapshot(
                            AssistantFailureCode.SCHEDULE_REJECTED,
                            AssistantFailureNextAction.REVISE_INPUT,
                            SCHEDULE_REJECTED_TEXT,
                        ),
                        at=_utc(self._clock.now()),
                    )
            return ProcessOneResult.COMPLETED
        except Exception:
            return self._unhealthy()

    def _execute_create(
        self,
        request_id: UUID,
        intent: PendingCreateIntent,
    ) -> ProcessOneResult:
        data = _schedule_create(intent)
        try:
            evidence = create_idempotent(
                self._schedule_sessions,
                data=data,
                scope=ASSISTANT_IDEMPOTENCY_SCOPE,
                idempotency_key=str(request_id),
                clock=self._clock,
                uuid_factory=self._uuid_factory,
            )
            if self._after_create_commit is not None:
                self._after_create_commit(request_id)
        except AssistantCreateRejected:
            return self._mark_create_rejected(request_id)
        except Exception:
            try:
                evidence = self._create_evidence(request_id, data)
            except Exception:
                evidence = None
            if evidence is None:
                return self._mark_create_uncertain(request_id)
        return self._finalize_create_success(request_id, evidence)

    def _reconfirmation(
        self,
        session: Session,
        *,
        request_id: UUID,
        schedule: Schedule,
        intent: PendingUpdateIntent | PendingDeleteIntent,
        changes: AssistantConfirmationChanges | None,
        now: datetime,
    ) -> None:
        question_id = self._uuid_factory()
        expires_at = now + QUESTION_TTL
        operation = AssistantConfirmationOperation(intent.operation)
        optional: dict[str, object] = {}
        if changes is not None:
            optional["changes"] = changes
            pending = PendingUpdateIntent(
                target_schedule_id=schedule.id,
                expected_updated_at=schedule.updated_at,
                changes=changes,
                time_interpretation=intent.time_interpretation,
            )
        else:
            pending = PendingDeleteIntent(
                target_schedule_id=schedule.id,
                expected_updated_at=schedule.updated_at,
                time_interpretation=intent.time_interpretation,
            )
        result_model = AssistantResultConfirmation(
            kind=AssistantResultKind.CONFIRMATION,
            text=RECONFIRM_TEXT,
            question_id=question_id,
            expires_at=expires_at,
            operation=operation,
            target=_schedule_summary(schedule),
            **optional,
        )
        result = _snapshot(result_model)
        stored_intent = ConfirmationQuestionIntent(intent=pending).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        serialize_snapshot(stored_intent)
        _complete_marked(
            session,
            request_id=request_id,
            result_kind=AssistantResultKind.CONFIRMATION,
            result_json=result,
            completed_at=now,
            retention_started_at=None,
        )
        session.add(
            AssistantQuestion(
                id=question_id,
                conversation_id=session.scalar(
                    sa.select(AssistantRequest.conversation_id).where(
                        AssistantRequest.id == request_id
                    )
                ),
                source_request_id=request_id,
                kind=AssistantResultKind.CONFIRMATION.value,
                payload_json=result,
                candidates_json=None,
                candidate_intents_json=stored_intent,
                status=AssistantQuestionStatus.ACTIVE.value,
                created_at=now,
                expires_at=expires_at,
                deactivated_at=None,
                answered_by_request_id=None,
            )
        )
        _release_required(session, request_id)

    def _execute_atomic(
        self,
        request_id: UUID,
        intent: PendingUpdateIntent | PendingDeleteIntent,
        guard: ProcessDeadlineGuard,
    ) -> ProcessOneResult:
        try:
            with self._assistant_sessions() as session:
                with session.begin():
                    request = repository.get_request(session, request_id)
                    expected_operation = intent.operation
                    if (
                        request is None
                        or request.status != "processing"
                        or not request.write_started
                        or request.write_operation != expected_operation
                        or request.write_commit_mode
                        != AssistantWriteCommitMode.ATOMIC.value
                        or not _gate_owned(session, request_id)
                    ):
                        raise _LostFinalize("atomic marker ownership lost")
                    now = _utc(self._clock.now())
                    if guard.deadline_reached():
                        complete_marked_failure(
                            session,
                            request_id=request_id,
                            result_json=_failure_snapshot(
                                AssistantFailureCode.PROCESSING_TIMEOUT,
                                AssistantFailureNextAction.RETRY_LATER,
                                PROCESSING_TIMEOUT_TEXT,
                            ),
                            at=now,
                        )
                        return ProcessOneResult.COMPLETED
                    if guard.stop_requested():
                        raise _LostFinalize("shutdown won before atomic write")

                    if isinstance(intent, PendingUpdateIntent):
                        update = _schedule_update(intent)
                        values = _update_values(update)
                        scheduled_at = values.get("scheduled_at")
                        if isinstance(scheduled_at, datetime) and scheduled_at < now:
                            complete_marked_failure(
                                session,
                                request_id=request_id,
                                result_json=_failure_snapshot(
                                    AssistantFailureCode.SCHEDULE_REJECTED,
                                    AssistantFailureNextAction.REVISE_INPUT,
                                    SCHEDULE_REJECTED_TEXT,
                                ),
                                at=now,
                            )
                            return ProcessOneResult.COMPLETED
                        changed = schedule_repository.update_active_if_unchanged(
                            session,
                            schedule_id=intent.target_schedule_id,
                            expected_updated_at=intent.expected_updated_at,
                            changes=values,
                            updated_at=now,
                        )
                        operation = AssistantOutcomeOperation.UPDATE
                    else:
                        changed = schedule_repository.soft_delete_active_if_unchanged(
                            session,
                            schedule_id=intent.target_schedule_id,
                            expected_updated_at=intent.expected_updated_at,
                            deleted_at=now,
                        )
                        operation = AssistantOutcomeOperation.DELETE

                    if changed == 1:
                        _complete_marked(
                            session,
                            request_id=request_id,
                            result_kind=AssistantResultKind.OUTCOME,
                            result_json=_success_snapshot(
                                operation, intent.target_schedule_id
                            ),
                            completed_at=now,
                            retention_started_at=now,
                        )
                        _release_required(session, request_id)
                        return ProcessOneResult.COMPLETED

                    current = schedule_repository.get_active_by_id(
                        session, intent.target_schedule_id
                    )
                    if current is None:
                        _complete_marked(
                            session,
                            request_id=request_id,
                            result_kind=AssistantResultKind.OUTCOME,
                            result_json=_no_match_snapshot(operation),
                            completed_at=now,
                            retention_started_at=now,
                        )
                        _release_required(session, request_id)
                        return ProcessOneResult.COMPLETED
                    if isinstance(intent, PendingUpdateIntent):
                        remaining = _remaining_changes(update, current)
                        if remaining is None:
                            _complete_marked(
                                session,
                                request_id=request_id,
                                result_kind=AssistantResultKind.OUTCOME,
                                result_json=_already_applied_snapshot(),
                                completed_at=now,
                                retention_started_at=now,
                            )
                            _release_required(session, request_id)
                            return ProcessOneResult.COMPLETED
                    else:
                        remaining = None
                    self._reconfirmation(
                        session,
                        request_id=request_id,
                        schedule=current,
                        intent=intent,
                        changes=remaining,
                        now=now,
                    )
                    return ProcessOneResult.COMPLETED
        except _LostFinalize:
            return self._resolve_atomic_exception(request_id)
        except Exception:
            return self._resolve_atomic_exception(request_id)

    def _resolve_atomic_exception(self, request_id: UUID) -> ProcessOneResult:
        try:
            with self._assistant_sessions() as session:
                with session.begin():
                    request = repository.get_request(session, request_id)
                    if request is not None and request.status == "completed":
                        return ProcessOneResult.COMPLETED
                    if (
                        request is None
                        or request.status != "processing"
                        or not request.write_started
                        or request.write_commit_mode
                        != AssistantWriteCommitMode.ATOMIC.value
                        or not _gate_owned(session, request_id)
                    ):
                        raise _LostFinalize("atomic exception state is not provable")
                    complete_marked_failure(
                        session,
                        request_id=request_id,
                        result_json=_failure_snapshot(
                            AssistantFailureCode.SCHEDULE_UNAVAILABLE,
                            AssistantFailureNextAction.RETRY_LATER,
                            SCHEDULE_UNAVAILABLE_TEXT,
                        ),
                        at=_utc(self._clock.now()),
                    )
                    return ProcessOneResult.COMPLETED
        except Exception:
            return self._unhealthy()

    def execute(
        self, *, request_id: UUID, runtime_guard: ProcessDeadlineGuard
    ) -> ProcessOneResult:
        try:
            intent = self._load_pending(request_id)
            now = _utc(self._clock.now())
            if isinstance(intent, PendingCreateIntent):
                create = _schedule_create(intent)
                if create.scheduled_at < now:
                    return self._complete_prewrite_failure(
                        request_id,
                        code=AssistantFailureCode.SCHEDULE_REJECTED,
                        next_action=AssistantFailureNextAction.REVISE_INPUT,
                        text=SCHEDULE_REJECTED_TEXT,
                    )
                operation = AssistantWriteOperation.CREATE
            elif isinstance(intent, PendingUpdateIntent):
                update = _schedule_update(intent)
                if (
                    "scheduled_at" in update.model_fields_set
                    and update.scheduled_at < now
                ):
                    return self._complete_prewrite_failure(
                        request_id,
                        code=AssistantFailureCode.SCHEDULE_REJECTED,
                        next_action=AssistantFailureNextAction.REVISE_INPUT,
                        text=SCHEDULE_REJECTED_TEXT,
                    )
                operation = AssistantWriteOperation.UPDATE
            else:
                operation = AssistantWriteOperation.DELETE
        except _LostFinalize:
            return ProcessOneResult.LOST_FINALIZE
        except (ValidationError, ValueError):
            return self._complete_prewrite_failure(
                request_id,
                code=AssistantFailureCode.INTERNAL_ERROR,
                next_action=AssistantFailureNextAction.NONE,
                text=INTERNAL_ERROR_TEXT,
            )
        except Exception:
            return self._unhealthy()

        acquired = self._acquire(request_id, operation=operation, guard=runtime_guard)
        if acquired is not None:
            return acquired
        if self._after_marker is not None:
            self._after_marker(request_id)
        preflight = self._marked_preflight(request_id, runtime_guard)
        if preflight is not None:
            return preflight
        if isinstance(intent, PendingCreateIntent):
            return self._execute_create(request_id, intent)
        return self._execute_atomic(request_id, intent, runtime_guard)
