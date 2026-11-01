"""Deterministic single-request Assistant decision processing.

Phase 4 intentionally stops at public questions/results or a validated pending
write intent.  This module never calls a Schedule write function.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Callable, Protocol
from uuid import RFC_4122, UUID

import sqlalchemy as sa
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assistant import repository
from app.modules.assistant.decisions import (
    CandidateQuestionIntents,
    ConfirmationQuestionIntent,
    DecisionAsk,
    DecisionConfirm,
    DecisionContext,
    DecisionCancel,
    DecisionCreateIntent,
    DecisionEnvelope,
    DecisionFail,
    DecisionListIntent,
    DecisionNoMatch,
    DecisionProvider,
    DecisionProviderTimeout,
    DecisionSelectCandidate,
    PendingCreateIntent,
    PendingDeleteIntent,
    PendingTargetIntent,
    PendingUpdateIntent,
    PendingWriteIntent,
    TimeInterpretation,
    TimeTrustSource,
    validated_candidate_intent,
)
from app.modules.assistant.enums import (
    AssistantConfirmationOperation,
    AssistantFailureCode,
    AssistantFailureNextAction,
    AssistantOutcome,
    AssistantOutcomeOperation,
    AssistantRequestStatus,
    AssistantResultKind,
)
from app.modules.assistant.model import (
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
)
from app.modules.assistant.schedule_read import (
    ScheduleRead,
    ScheduleReadPort,
    ScheduleReadUnavailable,
)
from app.modules.assistant.schemas import (
    AssistantCandidate,
    AssistantConfirmationChanges,
    AssistantResultCandidateSelection,
    AssistantResultConfirmation,
    AssistantResultFailure,
    AssistantResultNeedMoreInfo,
    AssistantResultOutcome,
    AssistantScheduleSummary,
)
from app.modules.assistant.serialization import (
    SnapshotSerializationError,
    serialize_snapshot,
)
from app.modules.assistant.runtime_failures import timeout_snapshot
from app.modules.assistant.evidence.normalize import title_contains
from app.modules.assistant.runtime_guard import ProcessDeadlineGuard
from app.modules.schedules.schemas import ScheduleCreate, ScheduleUpdate

QUESTION_TTL = timedelta(minutes=10)
# Kept back from the request deadline for SQLite waits and finalize (Issue #29).
PROVIDER_SAFETY_MARGIN_SECONDS = 10.0
# A title-filtered list reads the bounded range first; beyond this it asks to narrow.
LIST_TITLE_SCAN_LIMIT = 100  # the read port allows at most 101 rows per read
RELATIVE_TIME_QUESTION = (
    "서버 시각을 신뢰할 수 없습니다. 절대 날짜와 시각을 알려 주세요."
)
LIST_NARROWING_QUESTION = (
    "일정이 100개를 초과합니다. 기간, category 또는 완료 여부를 좁혀 주세요."
)
NO_MATCH_TEXT = "조건에 맞는 일정을 찾지 못했습니다."
ALREADY_APPLIED_TEXT = "이미 요청한 상태입니다."
CANCELLED_TEXT = "요청한 변경을 취소했습니다."
MODEL_OUTPUT_INVALID_TEXT = "입력을 해석하지 못했습니다. 내용을 수정해 주세요."
MODEL_TIMEOUT_TEXT = "모델 응답 시간이 초과되었습니다. 나중에 다시 시도해 주세요."
SCHEDULE_UNAVAILABLE_TEXT = "일정 정보를 읽을 수 없습니다. 나중에 다시 시도해 주세요."
INTERNAL_ERROR_TEXT = "요청을 처리하지 못했습니다."

_PENDING_WRITE_ADAPTER = TypeAdapter(PendingWriteIntent)


class ProcessOneResult(StrEnum):
    SKIPPED = "skipped"
    COMPLETED = "completed"
    PENDING_WRITE = "pending_write"
    LOST_FINALIZE = "lost_finalize"


class SafeWritePort(Protocol):
    def execute(
        self, *, request_id: UUID, runtime_guard: ProcessDeadlineGuard
    ) -> ProcessOneResult: ...


class _ClaimResult(StrEnum):
    CLAIMED = "claimed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    STOPPED = "stopped"


class _InvalidDecision(ValueError):
    pass


class _FinalizeLost(RuntimeError):
    pass


@dataclass(frozen=True)
class _StoredInput:
    request_id: UUID
    conversation_id: UUID
    input: dict[str, object]
    task_reference_accepted_at: datetime
    control_state_version: int | None
    control_state: object | None
    answered_question_kind: str | None
    answered_question_payload: dict[str, object] | None
    pending_context: object | None


@dataclass(frozen=True)
class _QuestionDraft:
    id: UUID
    kind: str
    payload: dict[str, object]
    candidates: object | None
    intents: object | None
    created_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True)
class _FinalizePlan:
    result: BaseModel | None = None
    question: _QuestionDraft | None = None
    pending_write: BaseModel | None = None
    control_state_version: int | None = None
    control_state: object | None = None


@dataclass(frozen=True)
class _PreparedPlan:
    result_kind: str | None
    result: dict[str, object] | None
    question: _QuestionDraft | None
    pending_write_version: int | None
    pending_write: dict[str, object] | None
    control_state_version: int | None
    control_state: object | None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock values must be timezone-aware")
    return value.astimezone(timezone.utc)


def _new_uuid(factory: Callable[[], UUID]) -> UUID:
    value = factory()
    if not isinstance(value, UUID) or value.version != 4 or value.variant != RFC_4122:
        raise ValueError("Assistant UUID factory must return RFC variant UUID v4")
    return value


def _failure(
    code: AssistantFailureCode,
    next_action: AssistantFailureNextAction,
    text: str,
) -> _FinalizePlan:
    return _FinalizePlan(
        result=AssistantResultFailure(
            kind=AssistantResultKind.FAILURE,
            text=text,
            code=code,
            next_action=next_action,
        )
    )


def _claim(
    session_factory: sessionmaker[Session],
    *,
    request_id: UUID,
    now_factory: Callable[[], datetime],
    runtime_guard: ProcessDeadlineGuard,
) -> _ClaimResult:
    with session_factory() as session:
        with session.begin():
            now = _aware_utc(now_factory())
            if runtime_guard.stop_requested():
                return _ClaimResult.STOPPED
            if runtime_guard.deadline_reached():
                outcome = repository.complete_prewrite_request(
                    session,
                    request_id=request_id,
                    result_json=timeout_snapshot(),
                    at=now,
                )
                if outcome is repository.PrewriteCompletionResult.WRITE_STARTED:
                    raise RuntimeError("active write marker found before claim")
                if outcome is repository.PrewriteCompletionResult.COMPLETED:
                    return _ClaimResult.TIMED_OUT
                return _ClaimResult.SKIPPED
            claimed = repository.claim_accepted_request(
                session, request_id=request_id, processing_at=now
            )
            return _ClaimResult.CLAIMED if claimed else _ClaimResult.SKIPPED


def _load_stored_input(
    session_factory: sessionmaker[Session], *, request_id: UUID
) -> _StoredInput:
    # The Assistant engine uses BEGIN IMMEDIATE for this short snapshot read.  All
    # ORM values are copied before the session closes, so no lock survives into I/O.
    with session_factory() as session:
        request = repository.get_request(session, request_id)
        if request is None or request.status != AssistantRequestStatus.PROCESSING.value:
            raise RuntimeError("claimed Assistant request is missing")
        conversation = repository.get_conversation(session, request.conversation_id)
        if conversation is None:
            raise RuntimeError("Assistant conversation is missing")
        normalized = request.normalized_body_json
        if not isinstance(normalized, dict) or not isinstance(
            normalized.get("input"), dict
        ):
            raise RuntimeError("stored Assistant input is invalid")
        answered = repository.find_answered_question(session, request_id)
        answered_payload: dict[str, object] | None = None
        if answered is not None:
            if not isinstance(answered.payload_json, dict):
                raise RuntimeError("stored question payload is invalid")
            answered_payload = dict(answered.payload_json)
        return _StoredInput(
            request_id=request.id,
            conversation_id=request.conversation_id,
            input=dict(normalized["input"]),
            task_reference_accepted_at=request.task_reference_accepted_at,
            control_state_version=conversation.control_state_version,
            control_state=conversation.control_state_json,
            answered_question_kind=(answered.kind if answered is not None else None),
            answered_question_payload=answered_payload,
            pending_context=(
                answered.candidate_intents_json if answered is not None else None
            ),
        )


def _decision_context(
    stored: _StoredInput,
    *,
    server_time_trusted: bool,
    time_budget_seconds: float | None = None,
) -> DecisionContext:
    return DecisionContext(
        request_id=stored.request_id,
        conversation_id=stored.conversation_id,
        input=stored.input,
        task_reference_accepted_at=stored.task_reference_accepted_at,
        timezone="Asia/Seoul",
        server_time_trusted=server_time_trusted,
        is_question_follow_up=stored.answered_question_kind is not None,
        answered_question_kind=stored.answered_question_kind,
        answered_question_payload=stored.answered_question_payload,
        pending_context=stored.pending_context,
        control_state_version=stored.control_state_version,
        control_state=stored.control_state,
        time_budget_seconds=time_budget_seconds,
    )


def _provider_time_budget(runtime_guard: ProcessDeadlineGuard) -> float:
    """Time left for the model before the request deadline, keeping a margin.

    The margin leaves room for SQLite waits and finalize.  A model call still
    running at the deadline would mark the runtime unhealthy, so a request that
    waited too long in the queue fails as a timeout without calling the model.
    """

    budget = runtime_guard.deadline - runtime_guard.monotonic_clock.now() - PROVIDER_SAFETY_MARGIN_SECONDS
    if budget <= 0:
        raise DecisionProviderTimeout("no time left before the request deadline")
    return budget


def _call_provider(
    provider: DecisionProvider | None, context: DecisionContext
) -> DecisionEnvelope:
    if provider is None:
        raise RuntimeError("DecisionProvider is unavailable after admission")
    try:
        raw = provider.decide(context)
        validated = DecisionEnvelope.model_validate(raw)
        payload = validated.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        serialize_snapshot(payload)
        return DecisionEnvelope.model_validate(payload)
    except DecisionProviderTimeout:
        raise
    except (ValidationError, SnapshotSerializationError, ValueError, TypeError) as exc:
        raise _InvalidDecision("DecisionProvider returned invalid output") from exc


def _summary(schedule: ScheduleRead) -> AssistantScheduleSummary:
    return AssistantScheduleSummary(
        id=schedule.id,
        title=schedule.title,
        scheduled_at=schedule.scheduled_at,
        category=schedule.category,
        completed=schedule.completed,
        updated_at=schedule.updated_at,
    )


def _no_match(*, operation: AssistantOutcomeOperation | None = None) -> _FinalizePlan:
    fields: dict[str, object] = {}
    if operation is not None:
        fields["operation"] = operation
    return _FinalizePlan(
        result=AssistantResultOutcome(
            kind=AssistantResultKind.OUTCOME,
            text=NO_MATCH_TEXT,
            outcome=AssistantOutcome.NO_MATCH,
            schedules_changed=False,
            **fields,
        )
    )


def _already_applied() -> _FinalizePlan:
    return _FinalizePlan(
        result=AssistantResultOutcome(
            kind=AssistantResultKind.OUTCOME,
            text=ALREADY_APPLIED_TEXT,
            outcome=AssistantOutcome.NO_MATCH,
            schedules_changed=False,
            operation=AssistantOutcomeOperation.UPDATE,
        )
    )


def _changes_without_noops(
    changes: AssistantConfirmationChanges, schedule: ScheduleRead
) -> AssistantConfirmationChanges | None:
    kept_fields = {
        field
        for field in changes.model_fields_set
        if getattr(changes, field) != getattr(schedule, field)
    }
    if not kept_fields:
        return None
    # Reuse both the public confirmation and Schedule update validators.
    json_values = changes.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
        include=kept_fields,
    )
    ScheduleUpdate.model_validate(json_values)
    return AssistantConfirmationChanges.model_validate(json_values)


def _target_intent_for_current_schedule(
    *,
    operation: AssistantConfirmationOperation,
    changes: AssistantConfirmationChanges | None,
    time_interpretation: TimeInterpretation,
    schedule: ScheduleRead,
) -> PendingTargetIntent | None:
    if operation is AssistantConfirmationOperation.DELETE:
        return PendingDeleteIntent(
            target_schedule_id=schedule.id,
            expected_updated_at=schedule.updated_at,
            time_interpretation=time_interpretation,
        )
    assert changes is not None
    remaining = _changes_without_noops(changes, schedule)
    if remaining is None:
        return None
    return PendingUpdateIntent(
        target_schedule_id=schedule.id,
        expected_updated_at=schedule.updated_at,
        changes=remaining,
        time_interpretation=time_interpretation,
    )


def _question_plan(
    *,
    result: BaseModel,
    question_id: UUID,
    intents: object | None,
    candidates: object | None,
    issue_time: datetime,
    expires_at: datetime | None,
    control_state_version: int | None = None,
    control_state: object | None = None,
) -> _FinalizePlan:
    payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    return _FinalizePlan(
        result=result,
        question=_QuestionDraft(
            id=question_id,
            kind=str(payload["kind"]),
            payload=payload,
            candidates=candidates,
            intents=intents,
            created_at=issue_time,
            expires_at=expires_at,
        ),
        control_state_version=control_state_version,
        control_state=control_state,
    )


def _ask_plan(
    text: str,
    *,
    issue_time: datetime,
    uuid_factory: Callable[[], UUID],
    control_state_version: int | None = None,
    control_state: object | None = None,
) -> _FinalizePlan:
    question_id = _new_uuid(uuid_factory)
    result = AssistantResultNeedMoreInfo(
        kind=AssistantResultKind.NEED_MORE_INFO,
        text=text,
        question_id=question_id,
    )
    return _question_plan(
        result=result,
        question_id=question_id,
        intents=None,
        candidates=None,
        issue_time=issue_time,
        expires_at=None,
        control_state_version=control_state_version,
        control_state=control_state,
    )


def _confirmation_plan(
    *,
    text: str,
    intent: PendingTargetIntent,
    schedule: ScheduleRead,
    issue_time: datetime,
    uuid_factory: Callable[[], UUID],
    control_state_version: int | None = None,
    control_state: object | None = None,
) -> _FinalizePlan:
    question_id = _new_uuid(uuid_factory)
    expires_at = issue_time + QUESTION_TTL
    changes = intent.changes if isinstance(intent, PendingUpdateIntent) else None
    optional_fields: dict[str, object] = {}
    if changes is not None:
        optional_fields["changes"] = changes
    result = AssistantResultConfirmation(
        kind=AssistantResultKind.CONFIRMATION,
        text=text,
        question_id=question_id,
        expires_at=expires_at,
        operation=intent.operation,
        target=_summary(schedule),
        **optional_fields,
    )
    stored = ConfirmationQuestionIntent(intent=intent).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    return _question_plan(
        result=result,
        question_id=question_id,
        intents=stored,
        candidates=None,
        issue_time=issue_time,
        expires_at=expires_at,
        control_state_version=control_state_version,
        control_state=control_state,
    )


def _from_saved_selection(
    *,
    stored: _StoredInput,
    schedule_reader: ScheduleReadPort,
    issue_time_factory: Callable[[], datetime],
    uuid_factory: Callable[[], UUID],
    server_time_trusted: bool,
) -> _FinalizePlan:
    candidate_id = stored.input.get("candidateId")
    raw = stored.pending_context
    if not isinstance(candidate_id, str):
        raise RuntimeError("stored selection candidate is invalid")
    try:
        saved = validated_candidate_intent(raw, candidate_id)
    except (ValidationError, KeyError, ValueError) as exc:
        raise RuntimeError("stored selection intent is incomplete") from exc
    if (
        saved.time_interpretation is TimeInterpretation.RELATIVE
        and not server_time_trusted
    ):
        return _ask_plan(
            RELATIVE_TIME_QUESTION,
            issue_time=issue_time_factory(),
            uuid_factory=uuid_factory,
        )
    schedule = schedule_reader.get(saved.target_schedule_id)
    if schedule is None:
        return _no_match(operation=AssistantOutcomeOperation(saved.operation))
    intent = _target_intent_for_current_schedule(
        operation=AssistantConfirmationOperation(saved.operation),
        changes=(saved.changes if isinstance(saved, PendingUpdateIntent) else None),
        time_interpretation=saved.time_interpretation,
        schedule=schedule,
    )
    if intent is None:
        return _already_applied()
    return _confirmation_plan(
        text="이 일정에 요청한 변경을 적용할까요?",
        intent=intent,
        schedule=schedule,
        issue_time=issue_time_factory(),
        uuid_factory=uuid_factory,
    )


def _from_saved_confirmation(
    *,
    stored: _StoredInput,
    issue_time_factory: Callable[[], datetime],
    uuid_factory: Callable[[], UUID],
    server_time_trusted: bool,
) -> _FinalizePlan:
    try:
        envelope = ConfirmationQuestionIntent.model_validate(stored.pending_context)
    except ValidationError as exc:
        raise RuntimeError("stored confirmation intent is incomplete") from exc
    approved = stored.input.get("approved")
    if approved is False:
        return _FinalizePlan(
            result=AssistantResultOutcome(
                kind=AssistantResultKind.OUTCOME,
                text=CANCELLED_TEXT,
                outcome=AssistantOutcome.CANCELLED,
                schedules_changed=False,
                operation=AssistantOutcomeOperation(envelope.intent.operation),
            )
        )
    if approved is not True:
        raise RuntimeError("stored confirmation answer is invalid")
    if (
        envelope.intent.time_interpretation is TimeInterpretation.RELATIVE
        and not server_time_trusted
    ):
        return _ask_plan(
            RELATIVE_TIME_QUESTION,
            issue_time=issue_time_factory(),
            uuid_factory=uuid_factory,
        )
    return _FinalizePlan(pending_write=envelope.intent)


def _provider_decision_plan(
    envelope: DecisionEnvelope,
    *,
    schedule_reader: ScheduleReadPort,
    issue_time_factory: Callable[[], datetime],
    uuid_factory: Callable[[], UUID],
    server_time_trusted: bool,
) -> _FinalizePlan:
    decision = envelope.decision
    control = {
        "control_state_version": envelope.control_state_version,
        "control_state": envelope.control_state,
    }
    if isinstance(decision, DecisionAsk):
        return _ask_plan(
            decision.text,
            issue_time=issue_time_factory(),
            uuid_factory=uuid_factory,
            **control,
        )

    relative = False
    if isinstance(decision, DecisionSelectCandidate):
        relative = any(
            item.time_interpretation is TimeInterpretation.RELATIVE
            for item in decision.candidates
        )
    elif isinstance(
        decision, (DecisionConfirm, DecisionCreateIntent, DecisionListIntent)
    ):
        relative = decision.time_interpretation is TimeInterpretation.RELATIVE
    if relative and not server_time_trusted:
        return _ask_plan(
            RELATIVE_TIME_QUESTION,
            issue_time=issue_time_factory(),
            uuid_factory=uuid_factory,
            **control,
        )

    if isinstance(decision, DecisionCreateIntent):
        schedule_create = ScheduleCreate.model_validate(
            {
                "title": decision.title,
                "scheduledAt": decision.scheduled_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "category": decision.category.value,
            }
        )
        pending = PendingCreateIntent(
            title=schedule_create.title,
            scheduled_at=decision.scheduled_at,
            category=schedule_create.category,
            time_interpretation=decision.time_interpretation,
        )
        return _FinalizePlan(pending_write=pending, **control)

    if isinstance(decision, DecisionListIntent):
        if decision.title is None:
            schedules = schedule_reader.list(
                from_utc=decision.from_at,
                to_utc=decision.to_at,
                category=decision.category,
                completed=decision.completed,
                limit=101,
            )
        else:
            # Title filtering happens after the read, so read the whole bounded range.
            scanned = schedule_reader.list(
                from_utc=decision.from_at,
                to_utc=decision.to_at,
                category=decision.category,
                completed=decision.completed,
                limit=LIST_TITLE_SCAN_LIMIT + 1,
            )
            if len(scanned) > LIST_TITLE_SCAN_LIMIT:
                return _ask_plan(
                    LIST_NARROWING_QUESTION,
                    issue_time=issue_time_factory(),
                    uuid_factory=uuid_factory,
                    **control,
                )
            schedules = [
                schedule
                for schedule in scanned
                if title_contains(decision.title, schedule.title)
            ]
        if len(schedules) > 100:
            return _ask_plan(
                LIST_NARROWING_QUESTION,
                issue_time=issue_time_factory(),
                uuid_factory=uuid_factory,
                **control,
            )
        return _FinalizePlan(
            result=AssistantResultOutcome(
                kind=AssistantResultKind.OUTCOME,
                text=decision.text,
                outcome=AssistantOutcome.SUCCESS,
                schedules_changed=False,
                operation=AssistantOutcomeOperation.LIST,
                items=[_summary(schedule) for schedule in schedules],
            ),
            **control,
        )

    if isinstance(decision, DecisionConfirm):
        schedule = schedule_reader.get(decision.schedule_id)
        if schedule is None:
            return _FinalizePlan(
                result=_no_match(
                    operation=AssistantOutcomeOperation(decision.operation)
                ).result,
                **control,
            )
        intent = _target_intent_for_current_schedule(
            operation=decision.operation,
            changes=decision.changes,
            time_interpretation=decision.time_interpretation,
            schedule=schedule,
        )
        if intent is None:
            return _FinalizePlan(result=_already_applied().result, **control)
        return _confirmation_plan(
            text=decision.text,
            intent=intent,
            schedule=schedule,
            issue_time=issue_time_factory(),
            uuid_factory=uuid_factory,
            **control,
        )

    if isinstance(decision, DecisionSelectCandidate):
        resolved: list[tuple[object, ScheduleRead, PendingTargetIntent]] = []
        had_noop_target = False
        for proposed in decision.candidates:
            schedule = schedule_reader.get(proposed.schedule_id)
            if schedule is None:
                continue
            intent = _target_intent_for_current_schedule(
                operation=proposed.operation,
                changes=proposed.changes,
                time_interpretation=proposed.time_interpretation,
                schedule=schedule,
            )
            if intent is None:
                had_noop_target = True
                continue
            resolved.append((proposed, schedule, intent))
        if not resolved:
            result = _already_applied() if had_noop_target else _no_match()
            return _FinalizePlan(result=result.result, **control)
        if len(resolved) == 1:
            _, schedule, intent = resolved[0]
            return _confirmation_plan(
                text=decision.text,
                intent=intent,
                schedule=schedule,
                issue_time=issue_time_factory(),
                uuid_factory=uuid_factory,
                **control,
            )
        presentation_keys = [
            (item[1].title, item[1].scheduled_at, item[1].category)
            for item in resolved
        ]
        if len(set(presentation_keys)) != len(presentation_keys):
            return _FinalizePlan(
                result=AssistantResultFailure(
                    kind=AssistantResultKind.FAILURE,
                    text=decision.text,
                    code=AssistantFailureCode.TARGET_AMBIGUOUS,
                    next_action=AssistantFailureNextAction.REVISE_INPUT,
                ),
                **control,
            )

        question_id = _new_uuid(uuid_factory)
        forbidden_ids = {str(item[1].id) for item in resolved}
        forbidden_ids.add(str(question_id))
        public_candidates: list[AssistantCandidate] = []
        intent_map: dict[str, PendingTargetIntent] = {}
        for _, schedule, intent in resolved:
            for _attempt in range(100):
                candidate_id = str(_new_uuid(uuid_factory))
                if candidate_id not in forbidden_ids and candidate_id not in intent_map:
                    break
            else:
                raise RuntimeError("UUID factory could not produce an opaque candidate ID")
            public_candidates.append(
                AssistantCandidate(
                    candidate_id=candidate_id,
                    title=schedule.title,
                    scheduled_at=schedule.scheduled_at,
                    category=schedule.category,
                )
            )
            intent_map[candidate_id] = intent
        issue_time = issue_time_factory()
        expires_at = issue_time + QUESTION_TTL
        result = AssistantResultCandidateSelection(
            kind=AssistantResultKind.CANDIDATE_SELECTION,
            text=decision.text,
            question_id=question_id,
            expires_at=expires_at,
            candidates=public_candidates,
        )
        candidate_snapshots = [
            item.model_dump(mode="json", by_alias=True, exclude_none=True)
            for item in public_candidates
        ]
        stored_intents = CandidateQuestionIntents(
            candidates=intent_map
        ).model_dump(mode="json", by_alias=True, exclude_none=True)
        return _question_plan(
            result=result,
            question_id=question_id,
            intents=stored_intents,
            candidates=candidate_snapshots,
            issue_time=issue_time,
            expires_at=expires_at,
            **control,
        )

    if isinstance(decision, DecisionNoMatch):
        return _FinalizePlan(
            result=_no_match(operation=AssistantOutcomeOperation(decision.operation)).result,
            **control,
        )

    if isinstance(decision, DecisionCancel):
        fields: dict[str, object] = {}
        if decision.operation is not None:
            fields["operation"] = AssistantOutcomeOperation(decision.operation)
        return _FinalizePlan(
            result=AssistantResultOutcome(
                kind=AssistantResultKind.OUTCOME,
                text=decision.text,
                outcome=AssistantOutcome.CANCELLED,
                schedules_changed=False,
                **fields,
            ),
            **control,
        )

    if isinstance(decision, DecisionFail):
        return _FinalizePlan(
            result=AssistantResultFailure(
                kind=AssistantResultKind.FAILURE,
                text=decision.text,
                code=decision.code,
                next_action=decision.next_action,
            ),
            **control,
        )
    raise _InvalidDecision("unknown Decision variant")


def _prepare(plan: _FinalizePlan) -> _PreparedPlan:
    result: dict[str, object] | None = None
    result_kind: str | None = None
    if plan.result is not None:
        result = plan.result.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        serialize_snapshot(result)
        result_kind = str(result["kind"])

    question = plan.question
    if question is not None:
        serialize_snapshot(question.payload)
        if question.candidates is not None:
            serialize_snapshot(question.candidates)
        if question.intents is not None:
            serialize_snapshot(question.intents)

    pending_json: dict[str, object] | None = None
    pending_version: int | None = None
    if plan.pending_write is not None:
        validated = _PENDING_WRITE_ADAPTER.validate_python(plan.pending_write)
        pending_json = validated.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
        serialize_snapshot(pending_json)
        pending_version = 1

    if plan.control_state is not None:
        serialize_snapshot(plan.control_state)

    return _PreparedPlan(
        result_kind=result_kind,
        result=result,
        question=question,
        pending_write_version=pending_version,
        pending_write=pending_json,
        control_state_version=plan.control_state_version,
        control_state=plan.control_state,
    )


def _finalize(
    session_factory: sessionmaker[Session],
    *,
    request_id: UUID,
    plan: _PreparedPlan,
    now: datetime,
    now_factory: Callable[[], datetime],
    runtime_guard: ProcessDeadlineGuard,
) -> ProcessOneResult:
    try:
        with session_factory() as session:
            with session.begin():
                request = repository.get_request(session, request_id)
                if (
                    request is None
                    or request.status != AssistantRequestStatus.PROCESSING.value
                ):
                    return ProcessOneResult.LOST_FINALIZE

                if runtime_guard.deadline_reached():
                    outcome = repository.complete_prewrite_request(
                        session,
                        request_id=request_id,
                        result_json=timeout_snapshot(),
                        at=_aware_utc(now_factory()),
                    )
                    if outcome is repository.PrewriteCompletionResult.WRITE_STARTED:
                        raise RuntimeError("active write marker found during finalize")
                    if outcome is repository.PrewriteCompletionResult.COMPLETED:
                        return ProcessOneResult.COMPLETED
                    return ProcessOneResult.LOST_FINALIZE
                if runtime_guard.stop_requested():
                    return ProcessOneResult.LOST_FINALIZE

                if plan.question is not None:
                    question = plan.question
                    repository.add_question(
                        session,
                        AssistantQuestion(
                            id=question.id,
                            conversation_id=request.conversation_id,
                            source_request_id=request.id,
                            kind=question.kind,
                            payload_json=question.payload,
                            candidates_json=question.candidates,
                            candidate_intents_json=question.intents,
                            status=AssistantQuestionStatus.ACTIVE.value,
                            created_at=question.created_at,
                            expires_at=question.expires_at,
                            deactivated_at=None,
                            answered_by_request_id=None,
                        ),
                    )

                if plan.control_state is not None:
                    conversation = repository.get_conversation(
                        session, request.conversation_id
                    )
                    if conversation is None:
                        raise RuntimeError("Assistant conversation disappeared")
                    conversation.control_state_version = plan.control_state_version
                    conversation.control_state_json = plan.control_state

                if plan.pending_write is not None:
                    pending = session.execute(
                        sa.update(AssistantRequest)
                        .where(
                            AssistantRequest.id == request_id,
                            AssistantRequest.status
                            == AssistantRequestStatus.PROCESSING.value,
                            AssistantRequest.write_started.is_(False),
                        )
                        .values(
                            pending_write_version=plan.pending_write_version,
                            pending_write_json=plan.pending_write,
                        )
                    )
                    if int(pending.rowcount or 0) != 1:
                        raise _FinalizeLost
                    return ProcessOneResult.PENDING_WRITE

                if plan.result is None or plan.result_kind is None:
                    raise RuntimeError(
                        "finalized request has neither result nor pending write"
                    )
                completed = session.execute(
                    sa.update(AssistantRequest)
                    .where(
                        AssistantRequest.id == request_id,
                        AssistantRequest.status
                        == AssistantRequestStatus.PROCESSING.value,
                        AssistantRequest.write_started.is_(False),
                    )
                    .values(
                        status=AssistantRequestStatus.COMPLETED.value,
                        result_kind=plan.result_kind,
                        result_json=plan.result,
                        completed_at=now,
                        retention_started_at=(
                            None if plan.question is not None else now
                        ),
                    )
                )
                if int(completed.rowcount or 0) != 1:
                    raise _FinalizeLost
                return ProcessOneResult.COMPLETED
    except _FinalizeLost:
        return ProcessOneResult.LOST_FINALIZE


def process_one(
    session_factory: sessionmaker[Session],
    *,
    request_id: UUID,
    provider: DecisionProvider | None,
    schedule_reader: ScheduleReadPort,
    clock: object,
    time_trust_source: TimeTrustSource,
    uuid_factory: Callable[[], UUID],
    runtime_guard: ProcessDeadlineGuard,
    safe_writer: SafeWritePort,
) -> ProcessOneResult:
    """Claim, decide, persist an intent, and execute it in this worker call."""

    now_method = getattr(clock, "now", None)
    if not callable(now_method):
        raise TypeError("clock must provide now()")
    claim = _claim(
        session_factory,
        request_id=request_id,
        now_factory=now_method,
        runtime_guard=runtime_guard,
    )
    if claim in {_ClaimResult.SKIPPED, _ClaimResult.STOPPED}:
        return ProcessOneResult.SKIPPED
    if claim is _ClaimResult.TIMED_OUT:
        return ProcessOneResult.COMPLETED

    try:
        stored = _load_stored_input(session_factory, request_id=request_id)
        server_time_trusted = time_trust_source.is_trusted()
        if type(server_time_trusted) is not bool:
            raise TypeError("time trust source must return bool")

        def issue_time_factory() -> datetime:
            return _aware_utc(now_method())

        input_type = stored.input.get("type")
        if input_type == "selection":
            plan = _from_saved_selection(
                stored=stored,
                schedule_reader=schedule_reader,
                issue_time_factory=issue_time_factory,
                uuid_factory=uuid_factory,
                server_time_trusted=server_time_trusted,
            )
        elif input_type == "confirmation":
            plan = _from_saved_confirmation(
                stored=stored,
                issue_time_factory=issue_time_factory,
                uuid_factory=uuid_factory,
                server_time_trusted=server_time_trusted,
            )
        else:
            context = _decision_context(
                stored,
                server_time_trusted=server_time_trusted,
                time_budget_seconds=_provider_time_budget(runtime_guard),
            )
            envelope = _call_provider(provider, context)
            plan = _provider_decision_plan(
                envelope,
                schedule_reader=schedule_reader,
                issue_time_factory=issue_time_factory,
                uuid_factory=uuid_factory,
                server_time_trusted=server_time_trusted,
            )
        prepared = _prepare(plan)
    except DecisionProviderTimeout:
        prepared = _prepare(
            _failure(
                AssistantFailureCode.MODEL_TIMEOUT,
                AssistantFailureNextAction.RETRY_LATER,
                MODEL_TIMEOUT_TEXT,
            )
        )
    except _InvalidDecision:
        prepared = _prepare(
            _failure(
                AssistantFailureCode.MODEL_OUTPUT_INVALID,
                AssistantFailureNextAction.REVISE_INPUT,
                MODEL_OUTPUT_INVALID_TEXT,
            )
        )
    except ScheduleReadUnavailable:
        prepared = _prepare(
            _failure(
                AssistantFailureCode.SCHEDULE_UNAVAILABLE,
                AssistantFailureNextAction.RETRY_LATER,
                SCHEDULE_UNAVAILABLE_TEXT,
            )
        )
    except Exception:
        prepared = _prepare(
            _failure(
                AssistantFailureCode.INTERNAL_ERROR,
                AssistantFailureNextAction.NONE,
                INTERNAL_ERROR_TEXT,
            )
        )

    completed_at = (
        prepared.question.created_at
        if prepared.question is not None
        else _aware_utc(now_method())
    )
    finalized = _finalize(
        session_factory,
        request_id=request_id,
        plan=prepared,
        now=completed_at,
        now_factory=now_method,
        runtime_guard=runtime_guard,
    )
    if finalized is ProcessOneResult.PENDING_WRITE:
        return safe_writer.execute(
            request_id=request_id, runtime_guard=runtime_guard
        )
    return finalized
