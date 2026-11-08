from __future__ import annotations

from datetime import datetime, timedelta, timezone
import multiprocessing
import os
from pathlib import Path
import signal
from threading import Event
from uuid import UUID

import sqlalchemy as sa
import pytest
from fastapi.testclient import TestClient

from app.core.clock import FixedClock, FixedMonotonicClock
from app.core.config import Settings
from app.core.database import (
    create_api_engine,
    create_assistant_engine,
    create_session_factory,
)
from app.main import create_app
from app.modules.assistant import repository
from app.modules.assistant.admission import admit_request, request_fingerprint
from app.modules.assistant.decisions import DecisionEnvelope
from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantRequest,
    AssistantWriteGate,
)
from app.modules.assistant.processor import ProcessOneResult, process_one
from app.modules.assistant.recovery import recover_startup_requests
from app.modules.assistant.runtime_failures import timeout_snapshot
from app.modules.assistant.runtime_guard import ProcessDeadlineGuard
from app.modules.assistant.safe_write import AssistantSafeWriter, timeout_request
from app.modules.assistant.schedule_read import SqlAlchemyScheduleReader
from app.modules.assistant.schemas import AssistantCancelTarget, AssistantRequestCreate
from app.modules.assistant.state_service import cancel_assistant_target
from app.modules.schedules import repository as schedule_repository
from app.modules.schedules.category import Category
from app.modules.schedules.idempotency import canonical_fingerprint
from app.modules.schedules.model import IdempotencyRecord, Schedule
from app.modules.schedules.assistant_write import create_idempotent
from app.modules.schedules.schemas import ScheduleCreate
from app.modules.assistant import safe_write as safe_write_module
from tests.fakes.assistant import ScriptedDecisionProvider

NOW = datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(minutes=5)
REQUEST_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CONVERSATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SCHEDULE_ID = UUID("77777777-7777-4777-8777-777777777777")
SCHEDULE_B = UUID("88888888-8888-4888-8888-888888888888")
CREATE_PENDING = {
    "version": 1,
    "operation": "create",
    "title": "반품 검수",
    "scheduledAt": "2026-10-19T05:00:00Z",
    "category": "RETURN",
    "timeInterpretation": "absolute",
}


class TrustedTime:
    def is_trusted(self) -> bool:
        return True


class Uuids:
    def __init__(self) -> None:
        self.value = 1

    def __call__(self) -> UUID:
        value = UUID(f"99999999-9999-4999-8999-{self.value:012x}")
        self.value += 1
        return value


class AdvancingClock:
    def __init__(self) -> None:
        self.current = NOW

    def now(self) -> datetime:
        value = self.current
        self.current += timedelta(hours=1)
        return value


@pytest.fixture
def stores(database_path: Path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    assistant_engine = create_assistant_engine(settings)
    api_engine = create_api_engine(settings)
    assistant = create_session_factory(assistant_engine)
    api = create_session_factory(api_engine)
    yield assistant, api
    assistant_engine.dispose()
    api_engine.dispose()


def guard(now: float = 0.0, deadline: float = 300.0) -> ProcessDeadlineGuard:
    return ProcessDeadlineGuard(
        monotonic_clock=FixedMonotonicClock(now),
        deadline=deadline,
        stop_signal=Event(),
    )


def pending_request(
    assistant,
    *,
    request_id: UUID = REQUEST_ID,
    conversation_id: UUID = CONVERSATION_ID,
    pending: dict[str, object],
) -> None:
    with assistant.begin() as session:
        session.add(
            AssistantConversation(
                id=conversation_id,
                created_at=NOW,
                last_valid_input_at=NOW,
                expired_at=None,
                control_state_version=None,
                control_state_json=None,
            )
        )
        session.add(
            AssistantRequest(
                id=request_id,
                conversation_id=conversation_id,
                fingerprint_version=1,
                fingerprint_hash="a" * 64,
                normalized_body_json={"requestId": str(request_id)},
                status="processing",
                result_kind=None,
                result_json=None,
                accepted_at=NOW,
                processing_at=NOW,
                completed_at=None,
                task_reference_accepted_at=NOW,
                write_started=False,
                write_started_at=None,
                write_operation=None,
                write_commit_mode=None,
                pending_write_version=1,
                pending_write_json=pending,
                create_idempotency_key=None,
                create_idempotency_scope=None,
                had_uncertain=False,
                uncertain_at=None,
                uncertain_acknowledged_at=None,
                uncertain_acknowledged_by_request_id=None,
                uncertain_resolved_at=None,
                retention_started_at=None,
                tombstone=False,
                tombstoned_at=None,
                cancelled_at=None,
            )
        )


def schedule(api, *, updated_at: datetime = OLD) -> None:
    with api.begin() as session:
        session.add(
            Schedule(
                id=SCHEDULE_ID,
                title="재고 확인",
                scheduled_at=NOW + timedelta(hours=1),
                category=Category.INVENTORY,
                completed=False,
                created_at=OLD,
                updated_at=updated_at,
                deleted_at=None,
            )
        )


def writer(assistant, api, **kwargs) -> AssistantSafeWriter:
    return AssistantSafeWriter(
        assistant_session_factory=assistant,
        schedule_session_factory=api,
        clock=FixedClock(NOW),
        uuid_factory=Uuids(),
        **kwargs,
    )


def completed_uncertain_create(assistant, api) -> None:
    pending_request(assistant, pending=dict(CREATE_PENDING))

    def timeout_after_marker(request_id: UUID) -> None:
        with assistant.begin() as session:
            assert (
                timeout_request(
                    session,
                    request_id=request_id,
                    at=NOW,
                    prewrite_result_json=timeout_snapshot(),
                ).value
                == "completed"
            )

    assert (
        writer(assistant, api, after_marker=timeout_after_marker).execute(
            request_id=REQUEST_ID, runtime_guard=guard()
        )
        is ProcessOneResult.LOST_FINALIZE
    )


def _sigkill_after_create_commit(database_path: str) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    assistant_engine = create_assistant_engine(settings)
    api_engine = create_api_engine(settings)

    def terminate(_request_id: UUID) -> None:
        os.kill(os.getpid(), signal.SIGKILL)

    safe_writer = AssistantSafeWriter(
        assistant_session_factory=create_session_factory(assistant_engine),
        schedule_session_factory=create_session_factory(api_engine),
        clock=FixedClock(NOW),
        uuid_factory=Uuids(),
        after_create_commit=terminate,
    )
    safe_writer.execute(request_id=REQUEST_ID, runtime_guard=guard())


def test_create_intent_executes_once_and_records_assistant_idempotency(stores) -> None:
    assistant, api = stores
    data = AssistantRequestCreate.model_validate(
        {"requestId": str(REQUEST_ID), "input": {"type": "text", "text": "등록"}}
    )
    admitted = admit_request(
        assistant,
        data=data,
        fingerprint=request_fingerprint(data),
        accepted_at=NOW,
        new_conversation_id=CONVERSATION_ID,
        runtime_ready=True,
        provider_available=True,
    )
    assert admitted.kind.value == "accepted"
    provider = ScriptedDecisionProvider(
        [
            DecisionEnvelope.model_validate(
                {
                    "decision": {
                        "kind": "create_intent",
                        "text": "등록합니다.",
                        "title": "반품 검수",
                        "scheduledAt": "2026-10-19T05:00:00Z",
                        "category": "RETURN",
                        "timeInterpretation": "absolute",
                    }
                }
            )
        ]
    )
    result = process_one(
        assistant,
        request_id=REQUEST_ID,
        provider=provider,
        schedule_reader=SqlAlchemyScheduleReader(api),
        clock=FixedClock(NOW),
        time_trust_source=TrustedTime(),
        uuid_factory=Uuids(),
        runtime_guard=guard(),
        safe_writer=writer(assistant, api),
    )
    assert result is ProcessOneResult.COMPLETED
    with api() as session:
        schedules = list(session.scalars(sa.select(Schedule)))
        records = list(session.scalars(sa.select(IdempotencyRecord)))
        request = session.get(AssistantRequest, REQUEST_ID)
        gate = session.get(AssistantWriteGate, 1)
        assert len(schedules) == len(records) == 1
        assert records[0].scope == "assistant-v1"
        assert records[0].idempotency_key == str(REQUEST_ID)
        assert request.result_json["outcome"] == "success"
        assert request.result_json["affectedScheduleIds"] == [str(schedules[0].id)]
        assert gate.owner_request_id is None

    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.LOST_FINALIZE
    )
    with api() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 1


def test_candidate_selection_to_approved_scheduled_at_update_executes_once(
    stores,
) -> None:
    assistant, api = stores
    schedule(api)
    with api.begin() as session:
        session.add(
            Schedule(
                id=SCHEDULE_B,
                title="반품 확인",
                scheduled_at=NOW + timedelta(hours=2),
                category=Category.RETURN,
                completed=False,
                created_at=OLD,
                updated_at=OLD,
                deleted_at=None,
            )
        )

    initial = AssistantRequestCreate.model_validate(
        {
            "requestId": str(REQUEST_ID),
            "input": {"type": "text", "text": "재고 확인 시간을 바꿔 줘"},
        }
    )
    admitted = admit_request(
        assistant,
        data=initial,
        fingerprint=request_fingerprint(initial),
        accepted_at=NOW,
        new_conversation_id=CONVERSATION_ID,
        runtime_ready=True,
        provider_available=True,
    )
    assert admitted.kind.value == "accepted"

    target_text = "2026-10-19T09:00:00.123456+09:00"
    target_utc = datetime(2026, 10, 19, 0, 0, 0, 123456, tzinfo=timezone.utc)
    provider = ScriptedDecisionProvider(
        [
            DecisionEnvelope.model_validate(
                {
                    "decision": {
                        "kind": "select_candidate",
                        "text": "대상을 선택하세요.",
                        "candidates": [
                            {
                                "scheduleId": str(SCHEDULE_ID),
                                "operation": "update",
                                "changes": {"scheduledAt": target_text},
                                "timeInterpretation": "absolute",
                            },
                            {
                                "scheduleId": str(SCHEDULE_B),
                                "operation": "update",
                                "changes": {"scheduledAt": target_text},
                                "timeInterpretation": "absolute",
                            },
                        ],
                    }
                }
            )
        ]
    )
    uuids = Uuids()
    assert (
        process_one(
            assistant,
            request_id=REQUEST_ID,
            provider=provider,
            schedule_reader=SqlAlchemyScheduleReader(api),
            clock=FixedClock(NOW),
            time_trust_source=TrustedTime(),
            uuid_factory=uuids,
            runtime_guard=guard(),
            safe_writer=writer(assistant, api),
        )
        is ProcessOneResult.COMPLETED
    )
    assert provider.call_count == 1

    with assistant() as session:
        candidate_question = session.scalar(
            sa.select(AssistantQuestion).where(AssistantQuestion.status == "active")
        )
        assert candidate_question is not None
        selected = next(
            candidate_id
            for candidate_id, intent in candidate_question.candidate_intents_json[
                "candidates"
            ].items()
            if intent["targetScheduleId"] == str(SCHEDULE_ID)
        )
        assert candidate_question.candidate_intents_json["candidates"][selected][
            "changes"
        ] == {"scheduledAt": "2026-10-19T00:00:00.123456Z"}

    selection_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    selection = AssistantRequestCreate.model_validate(
        {
            "requestId": str(selection_id),
            "conversationId": str(CONVERSATION_ID),
            "input": {
                "type": "selection",
                "questionId": str(candidate_question.id),
                "candidateId": selected,
            },
        }
    )
    selected_admission = admit_request(
        assistant,
        data=selection,
        fingerprint=request_fingerprint(selection),
        accepted_at=NOW + timedelta(seconds=1),
        new_conversation_id=CONVERSATION_ID,
        runtime_ready=True,
        provider_available=False,
    )
    assert selected_admission.kind.value == "accepted"
    no_provider = ScriptedDecisionProvider([])
    assert (
        process_one(
            assistant,
            request_id=selection_id,
            provider=no_provider,
            schedule_reader=SqlAlchemyScheduleReader(api),
            clock=FixedClock(NOW + timedelta(seconds=1)),
            time_trust_source=TrustedTime(),
            uuid_factory=uuids,
            runtime_guard=guard(),
            safe_writer=writer(assistant, api),
        )
        is ProcessOneResult.COMPLETED
    )

    with assistant() as session:
        confirmation = session.scalar(
            sa.select(AssistantQuestion).where(AssistantQuestion.status == "active")
        )
        assert confirmation is not None
        assert confirmation.kind == "confirmation"
        assert confirmation.candidate_intents_json["intent"]["changes"] == {
            "scheduledAt": "2026-10-19T00:00:00.123456Z"
        }

    approval_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    approval = AssistantRequestCreate.model_validate(
        {
            "requestId": str(approval_id),
            "conversationId": str(CONVERSATION_ID),
            "input": {
                "type": "confirmation",
                "questionId": str(confirmation.id),
                "approved": True,
            },
        }
    )
    approved_admission = admit_request(
        assistant,
        data=approval,
        fingerprint=request_fingerprint(approval),
        accepted_at=NOW + timedelta(seconds=2),
        new_conversation_id=CONVERSATION_ID,
        runtime_ready=True,
        provider_available=False,
    )
    assert approved_admission.kind.value == "accepted"
    assert (
        process_one(
            assistant,
            request_id=approval_id,
            provider=no_provider,
            schedule_reader=SqlAlchemyScheduleReader(api),
            clock=FixedClock(NOW + timedelta(seconds=2)),
            time_trust_source=TrustedTime(),
            uuid_factory=uuids,
            runtime_guard=guard(),
            safe_writer=writer(assistant, api),
        )
        is ProcessOneResult.COMPLETED
    )

    assert no_provider.call_count == 0
    with api() as session:
        changed = session.get(Schedule, SCHEDULE_ID)
        untouched = session.get(Schedule, SCHEDULE_B)
        completed = session.get(AssistantRequest, approval_id)
        assert changed.scheduled_at == target_utc
        assert untouched.scheduled_at == NOW + timedelta(hours=2)
        assert completed.result_json["outcome"] == "success"
        assert completed.result_json["affectedScheduleIds"] == [str(SCHEDULE_ID)]
        assert session.get(AssistantWriteGate, 1).owner_request_id is None


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_expected_updated_at_atomic_write_and_gate_release(
    stores, operation: str
) -> None:
    assistant, api = stores
    schedule(api)
    pending: dict[str, object] = {
        "version": 1,
        "operation": operation,
        "targetScheduleId": str(SCHEDULE_ID),
        "expectedUpdatedAt": OLD.isoformat().replace("+00:00", "Z"),
        "timeInterpretation": "absolute",
    }
    if operation == "update":
        pending["changes"] = {"completed": True}
    pending_request(assistant, pending=pending)

    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )
    with api() as session:
        stored = session.get(Schedule, SCHEDULE_ID)
        request = session.get(AssistantRequest, REQUEST_ID)
        gate = session.get(AssistantWriteGate, 1)
        assert request.result_json["outcome"] == "success"
        assert request.result_json["operation"] == operation
        assert gate.owner_request_id is None
        if operation == "update":
            assert stored.completed is True
            assert stored.deleted_at is None
        else:
            assert stored.deleted_at == NOW


def test_updated_target_is_not_overwritten_and_gets_fresh_confirmation(stores) -> None:
    assistant, api = stores
    schedule(api, updated_at=NOW)
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "update",
            "targetScheduleId": str(SCHEDULE_ID),
            "expectedUpdatedAt": OLD.isoformat().replace("+00:00", "Z"),
            "changes": {"completed": True},
            "timeInterpretation": "absolute",
        },
    )
    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )
    with api() as session:
        stored = session.get(Schedule, SCHEDULE_ID)
        request = session.get(AssistantRequest, REQUEST_ID)
        question = session.scalar(sa.select(AssistantQuestion))
        assert stored.completed is False
        assert request.result_kind == "confirmation"
        assert request.retention_started_at is None
        assert question.candidate_intents_json["intent"]["expectedUpdatedAt"] == (
            "2026-10-18T05:00:00Z"
        )
        assert question.expires_at == NOW + timedelta(minutes=10)


def test_updated_target_scheduled_at_gets_fresh_confirmation(stores) -> None:
    assistant, api = stores
    schedule(api, updated_at=NOW)
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "update",
            "targetScheduleId": str(SCHEDULE_ID),
            "expectedUpdatedAt": OLD.isoformat().replace("+00:00", "Z"),
            "changes": {
                "title": "재고 확인",
                "scheduledAt": "2026-10-19T09:00:00.123456+09:00",
            },
            "timeInterpretation": "absolute",
        },
    )

    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )

    with api() as session:
        stored = session.get(Schedule, SCHEDULE_ID)
        request = session.get(AssistantRequest, REQUEST_ID)
        question = session.scalar(sa.select(AssistantQuestion))
        assert stored.scheduled_at == NOW + timedelta(hours=1)
        assert request.result_kind == "confirmation"
        assert request.retention_started_at is None
        assert request.result_json["changes"] == {
            "scheduledAt": "2026-10-19T00:00:00.123456Z"
        }
        assert question.candidate_intents_json["intent"]["changes"] == {
            "scheduledAt": "2026-10-19T00:00:00.123456Z"
        }
        assert question.candidate_intents_json["intent"]["expectedUpdatedAt"] == (
            "2026-10-18T05:00:00Z"
        )
        assert session.get(AssistantWriteGate, 1).owner_request_id is None


def test_marker_crash_recovery_never_reexecutes_create(stores) -> None:
    assistant, api = stores
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "create",
            "title": "반품 검수",
            "scheduledAt": "2026-10-19T05:00:00Z",
            "category": "RETURN",
            "timeInterpretation": "absolute",
        },
    )

    def crash(_request_id: UUID) -> None:
        raise SystemExit

    with pytest.raises(SystemExit):
        writer(assistant, api, after_marker=crash).execute(
            request_id=REQUEST_ID, runtime_guard=guard()
        )
    with assistant() as session:
        marked = session.get(AssistantRequest, REQUEST_ID)
        gate = session.get(AssistantWriteGate, 1)
        assert marked.write_started is True
        assert marked.write_operation == "create"
        assert marked.write_commit_mode == "idempotent_external"
        assert marked.create_idempotency_scope == "assistant-v1"
        assert marked.create_idempotency_key == str(REQUEST_ID)
        assert gate.owner_request_id == REQUEST_ID
    assert (
        recover_startup_requests(assistant, recovered_at=NOW + timedelta(minutes=1))
        == 1
    )
    with api() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.result_json["code"] == "PROCESSING_INTERRUPTED"
        assert session.get(AssistantWriteGate, 1).owner_request_id is None


def test_create_commit_crash_recovers_success_from_expired_exact_record(stores) -> None:
    assistant, api = stores
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "create",
            "title": "반품 검수",
            "scheduledAt": "2026-10-19T05:00:00Z",
            "category": "RETURN",
            "timeInterpretation": "absolute",
        },
    )

    def crash(_request_id: UUID) -> None:
        raise SystemExit

    with pytest.raises(SystemExit):
        writer(assistant, api, after_create_commit=crash).execute(
            request_id=REQUEST_ID, runtime_guard=guard()
        )
    with api.begin() as session:
        record = session.get(IdempotencyRecord, ("assistant-v1", str(REQUEST_ID)))
        record.expires_at = NOW - timedelta(seconds=1)
    assert (
        recover_startup_requests(assistant, recovered_at=NOW + timedelta(minutes=1))
        == 1
    )
    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.result_json["outcome"] == "success"
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 1
        assert session.get(AssistantWriteGate, 1).owner_request_id is None


def test_completed_uncertain_create_recovery_without_evidence_records_failure(
    stores,
) -> None:
    assistant, api = stores
    completed_uncertain_create(assistant, api)
    recovered_at = NOW + timedelta(minutes=1)

    with assistant() as session:
        before = session.get(AssistantRequest, REQUEST_ID)
        assert before.status == "completed"
        assert before.result_kind == "uncertain"
        assert before.result_json["code"] == "EXECUTION_RESULT_UNKNOWN"
        assert before.had_uncertain is True
        assert before.uncertain_at == NOW
        assert before.uncertain_resolved_at is None
        assert before.create_idempotency_scope == "assistant-v1"
        assert before.create_idempotency_key == str(REQUEST_ID)
        assert session.get(AssistantWriteGate, 1).owner_request_id == REQUEST_ID
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord))
            == 0
        )

    assert recover_startup_requests(assistant, recovered_at=recovered_at) == 1

    with assistant() as session:
        recovered = session.get(AssistantRequest, REQUEST_ID)
        assert recovered.status == "completed"
        assert recovered.result_kind == "failure"
        assert recovered.result_json["code"] == "PROCESSING_INTERRUPTED"
        assert recovered.result_json["nextAction"] == "retry_later"
        assert recovered.had_uncertain is True
        assert recovered.uncertain_at == NOW
        assert recovered.uncertain_resolved_at == recovered_at
        assert recovered.retention_started_at == recovered_at
        assert session.get(AssistantWriteGate, 1).owner_request_id is None
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord))
            == 0
        )


def test_completed_uncertain_create_recovery_uses_exact_evidence_without_rewrite(
    stores,
) -> None:
    assistant, api = stores
    completed_uncertain_create(assistant, api)
    evidence = create_idempotent(
        api,
        data=ScheduleCreate.model_validate(
            {
                "title": CREATE_PENDING["title"],
                "scheduledAt": CREATE_PENDING["scheduledAt"],
                "category": CREATE_PENDING["category"],
            }
        ),
        scope="assistant-v1",
        idempotency_key=str(REQUEST_ID),
        clock=FixedClock(NOW),
        uuid_factory=Uuids(),
    )
    recovered_at = NOW + timedelta(minutes=1)

    with api() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 1
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord))
            == 1
        )

    assert recover_startup_requests(assistant, recovered_at=recovered_at) == 1

    with api() as session:
        recovered = session.get(AssistantRequest, REQUEST_ID)
        assert recovered.result_kind == "outcome"
        assert recovered.result_json["outcome"] == "success"
        assert recovered.result_json["schedulesChanged"] is True
        assert recovered.result_json["affectedScheduleIds"] == [
            str(evidence.schedule_id)
        ]
        assert recovered.had_uncertain is True
        assert recovered.uncertain_at == NOW
        assert recovered.uncertain_resolved_at == recovered_at
        assert recovered.retention_started_at == recovered_at
        assert session.get(AssistantWriteGate, 1).owner_request_id is None
        schedules = list(session.scalars(sa.select(Schedule)))
        assert [stored.id for stored in schedules] == [evidence.schedule_id]
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord))
            == 1
        )


@pytest.mark.integration
def test_real_sigkill_after_create_commit_recovers_from_tmp_database_only(
    database_path: Path,
) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    assistant_engine = create_assistant_engine(settings)
    assistant = create_session_factory(assistant_engine)
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "create",
            "title": "SIGKILL 검증",
            "scheduledAt": "2026-10-19T05:00:00Z",
            "category": "RETURN",
            "timeInterpretation": "absolute",
        },
    )
    assistant_engine.dispose()

    child = multiprocessing.get_context("spawn").Process(
        target=_sigkill_after_create_commit,
        args=(str(database_path),),
    )
    child.start()
    child.join(timeout=10)
    assert child.exitcode == -signal.SIGKILL

    recovery_engine = create_assistant_engine(settings)
    recovery_factory = create_session_factory(recovery_engine)
    assert (
        recover_startup_requests(
            recovery_factory, recovered_at=NOW + timedelta(minutes=1)
        )
        == 1
    )
    with recovery_factory() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.result_json["outcome"] == "success"
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 1
    recovery_engine.dispose()


@pytest.mark.parametrize(
    ("operation", "expected_kind", "gate_held"),
    [("update", "failure", False), ("create", "uncertain", True)],
)
def test_marker_aware_timeout_has_no_unproven_schedule_effect(
    stores, operation: str, expected_kind: str, gate_held: bool
) -> None:
    assistant, api = stores
    if operation == "update":
        schedule(api)
        pending = {
            "version": 1,
            "operation": "update",
            "targetScheduleId": str(SCHEDULE_ID),
            "expectedUpdatedAt": OLD.isoformat().replace("+00:00", "Z"),
            "changes": {"completed": True},
            "timeInterpretation": "absolute",
        }
    else:
        pending = {
            "version": 1,
            "operation": "create",
            "title": "반품 검수",
            "scheduledAt": "2026-10-19T05:00:00Z",
            "category": "RETURN",
            "timeInterpretation": "absolute",
        }
    pending_request(assistant, pending=pending)

    def timeout_after_marker(request_id: UUID) -> None:
        with assistant.begin() as session:
            assert (
                timeout_request(
                    session,
                    request_id=request_id,
                    at=NOW,
                    prewrite_result_json=timeout_snapshot(),
                ).value
                == "completed"
            )

    result = writer(assistant, api, after_marker=timeout_after_marker).execute(
        request_id=REQUEST_ID, runtime_guard=guard()
    )
    assert result is ProcessOneResult.LOST_FINALIZE
    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        gate = session.get(AssistantWriteGate, 1)
        assert request.result_kind == expected_kind
        assert (gate.owner_request_id == REQUEST_ID) is gate_held
        schedule_row = session.get(Schedule, SCHEDULE_ID)
        if schedule_row is not None:
            assert schedule_row.completed is False
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord))
            == 0
        )


@pytest.mark.parametrize(
    "pending",
    [
        {
            "version": 1,
            "operation": "create",
            "title": "과거 일정",
            "scheduledAt": "2026-10-18T04:59:59Z",
            "category": "RETURN",
            "timeInterpretation": "absolute",
        },
        {
            "version": 1,
            "operation": "update",
            "targetScheduleId": str(SCHEDULE_ID),
            "expectedUpdatedAt": OLD.isoformat().replace("+00:00", "Z"),
            "changes": {"scheduledAt": "2026-10-18T04:59:59Z"},
            "timeInterpretation": "absolute",
        },
    ],
)
def test_past_schedule_is_rejected_before_marker_or_write(stores, pending) -> None:
    assistant, api = stores
    if pending["operation"] == "update":
        schedule(api)
    pending_request(assistant, pending=pending)
    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )
    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.result_json["code"] == "SCHEDULE_REJECTED"
        assert request.write_started is False
        assert session.get(AssistantWriteGate, 1).owner_request_id is None
        rows = list(session.scalars(sa.select(Schedule)))
        assert all(row.scheduled_at > NOW for row in rows)


def test_create_becoming_past_after_marker_is_confirmed_rejection(stores) -> None:
    assistant, api = stores
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "create",
            "title": "경계 일정",
            "scheduledAt": "2026-10-18T06:30:00Z",
            "category": "RETURN",
            "timeInterpretation": "absolute",
        },
    )
    safe_writer = AssistantSafeWriter(
        assistant_session_factory=assistant,
        schedule_session_factory=api,
        clock=AdvancingClock(),
        uuid_factory=Uuids(),
    )
    assert (
        safe_writer.execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )
    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.write_started is True
        assert request.result_json["code"] == "SCHEDULE_REJECTED"
        assert session.get(AssistantWriteGate, 1).owner_request_id is None
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0


def test_corrupt_pending_snapshot_fails_without_schedule_write(stores) -> None:
    assistant, api = stores
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "create",
            "title": "손상",
            "scheduledAt": "2026-10-19T05:00:00Z",
            "category": "RETURN",
            "timeInterpretation": "absolute",
            "unknown": True,
        },
    )
    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )
    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.result_json["code"] == "INTERNAL_ERROR"
        assert request.result_json["text"] == "요청을 처리하지 못했어요."
        assert request.write_started is False
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0


@pytest.mark.parametrize(
    ("state", "expected_text"), [("deleted", "조건에 맞는"), ("noop", "이미 요청한")]
)
def test_update_cas_zero_classifies_deleted_and_already_applied(
    stores, state: str, expected_text: str
) -> None:
    assistant, api = stores
    schedule(api, updated_at=NOW)
    with api.begin() as session:
        stored = session.get(Schedule, SCHEDULE_ID)
        if state == "deleted":
            stored.deleted_at = NOW
        else:
            stored.completed = True
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "update",
            "targetScheduleId": str(SCHEDULE_ID),
            "expectedUpdatedAt": OLD.isoformat().replace("+00:00", "Z"),
            "changes": {"completed": True},
            "timeInterpretation": "absolute",
        },
    )
    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )
    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.result_json["outcome"] == "no_match"
        assert expected_text in request.result_json["text"]
        assert request.result_json["schedulesChanged"] is False


def test_delete_cas_zero_issues_current_snapshot_confirmation(stores) -> None:
    assistant, api = stores
    schedule(api, updated_at=NOW)
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "delete",
            "targetScheduleId": str(SCHEDULE_ID),
            "expectedUpdatedAt": OLD.isoformat().replace("+00:00", "Z"),
            "timeInterpretation": "absolute",
        },
    )
    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )
    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        question = session.scalar(sa.select(AssistantQuestion))
        assert request.result_kind == "confirmation"
        assert request.result_json["operation"] == "delete"
        assert question.candidate_intents_json["intent"]["expectedUpdatedAt"] == (
            "2026-10-18T05:00:00Z"
        )


def test_atomic_exception_rolls_back_schedule_then_records_unavailable(
    stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    assistant, api = stores
    schedule(api)
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "update",
            "targetScheduleId": str(SCHEDULE_ID),
            "expectedUpdatedAt": OLD.isoformat().replace("+00:00", "Z"),
            "changes": {"completed": True},
            "timeInterpretation": "absolute",
        },
    )
    original = schedule_repository.update_active_if_unchanged

    def fail_after_update(*args, **kwargs):
        assert original(*args, **kwargs) == 1
        raise RuntimeError("injected atomic rollback")

    monkeypatch.setattr(
        schedule_repository, "update_active_if_unchanged", fail_after_update
    )
    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )
    with api() as session:
        assert session.get(Schedule, SCHEDULE_ID).completed is False
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.result_json["code"] == "SCHEDULE_UNAVAILABLE"
        assert session.get(AssistantWriteGate, 1).owner_request_id is None


def test_create_exception_without_exact_evidence_is_uncertain(
    stores, monkeypatch: pytest.MonkeyPatch
) -> None:
    assistant, api = stores
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "create",
            "title": "불확실",
            "scheduledAt": "2026-10-19T05:00:00Z",
            "category": "RETURN",
            "timeInterpretation": "absolute",
        },
    )
    monkeypatch.setattr(
        safe_write_module,
        "create_idempotent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )
    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.result_kind == "uncertain"
        assert "schedulesChanged" not in request.result_json
        assert request.had_uncertain is True
        assert request.retention_started_at is None
        assert session.get(AssistantWriteGate, 1).owner_request_id == REQUEST_ID


def test_create_with_same_key_different_fingerprint_is_uncertain_without_new_rows(
    stores,
) -> None:
    assistant, api = stores
    pending_request(assistant, pending=dict(CREATE_PENDING))
    other_data = ScheduleCreate.model_validate(
        {
            "title": "다른 일정",
            "scheduledAt": CREATE_PENDING["scheduledAt"],
            "category": CREATE_PENDING["category"],
        }
    )
    evidence = create_idempotent(
        api,
        data=other_data,
        scope="assistant-v1",
        idempotency_key=str(REQUEST_ID),
        clock=FixedClock(NOW),
        uuid_factory=Uuids(),
    )
    with api.begin() as session:
        session.add(
            IdempotencyRecord(
                scope="default",
                idempotency_key=str(REQUEST_ID),
                fingerprint=canonical_fingerprint(other_data),
                schedule_id=evidence.schedule_id,
                response_body=evidence.response.model_dump_json(by_alias=True),
                created_at=NOW,
                expires_at=NOW + timedelta(hours=24),
            )
        )
    with api() as session:
        schedules_before = [
            dict(row)
            for row in session.execute(
                sa.select(Schedule.__table__).order_by(Schedule.id)
            ).mappings()
        ]
        records_before = [
            dict(row)
            for row in session.execute(
                sa.select(IdempotencyRecord.__table__).order_by(
                    IdempotencyRecord.scope, IdempotencyRecord.idempotency_key
                )
            ).mappings()
        ]
        assert [record["scope"] for record in records_before] == [
            "assistant-v1",
            "default",
        ]

    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.COMPLETED
    )

    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.result_kind == "uncertain"
        assert request.result_json["code"] == "EXECUTION_RESULT_UNKNOWN"
        assert request.result_json["nextAction"] == "check_status"
        assert "schedulesChanged" not in request.result_json
        assert request.had_uncertain is True
        assert request.uncertain_at == NOW
        assert request.uncertain_resolved_at is None
        assert request.retention_started_at is None
        assert session.get(AssistantWriteGate, 1).owner_request_id == REQUEST_ID
        schedules_after = [
            dict(row)
            for row in session.execute(
                sa.select(Schedule.__table__).order_by(Schedule.id)
            ).mappings()
        ]
        records_after = [
            dict(row)
            for row in session.execute(
                sa.select(IdempotencyRecord.__table__).order_by(
                    IdempotencyRecord.scope, IdempotencyRecord.idempotency_key
                )
            ).mappings()
        ]
        assert schedules_after == schedules_before
        assert records_after == records_before


def test_uncertain_ack_is_atomic_with_insert_and_owner_release(stores) -> None:
    assistant, api = stores
    pending_request(
        assistant,
        pending={
            "version": 1,
            "operation": "create",
            "title": "반품 검수",
            "scheduledAt": "2026-10-19T05:00:00Z",
            "category": "RETURN",
            "timeInterpretation": "absolute",
        },
    )

    def timeout_after_marker(request_id: UUID) -> None:
        with assistant.begin() as session:
            timeout_request(
                session,
                request_id=request_id,
                at=NOW,
                prewrite_result_json=timeout_snapshot(),
            )

    writer(assistant, api, after_marker=timeout_after_marker).execute(
        request_id=REQUEST_ID, runtime_guard=guard()
    )
    next_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    rejected = AssistantRequestCreate.model_validate(
        {
            "requestId": str(next_id),
            "acknowledgedUncertainRequestId": str(REQUEST_ID),
            "input": {"type": "text", "text": "새 작업"},
        }
    )
    result = admit_request(
        assistant,
        data=rejected,
        fingerprint=request_fingerprint(rejected),
        accepted_at=NOW + timedelta(seconds=1),
        new_conversation_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        runtime_ready=True,
        provider_available=False,
    )
    assert result.kind.value == "assistant_unavailable"
    with assistant() as session:
        original = session.get(AssistantRequest, REQUEST_ID)
        assert original.uncertain_acknowledged_at is None
        assert session.get(AssistantWriteGate, 1).owner_request_id == REQUEST_ID
        assert session.get(AssistantRequest, next_id) is None

    result = admit_request(
        assistant,
        data=rejected,
        fingerprint=request_fingerprint(rejected),
        accepted_at=NOW + timedelta(seconds=1),
        new_conversation_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        runtime_ready=True,
        provider_available=True,
    )
    assert result.kind.value == "accepted"
    with assistant() as session:
        original = session.get(AssistantRequest, REQUEST_ID)
        assert original.uncertain_acknowledged_by_request_id == next_id
        assert session.get(AssistantWriteGate, 1).owner_request_id is None


def test_acknowledged_unresolved_uncertain_allows_next_create_to_complete(
    stores,
) -> None:
    assistant, api = stores
    completed_uncertain_create(assistant, api)
    with assistant() as session:
        original = session.get(AssistantRequest, REQUEST_ID)
        original_result = dict(original.result_json)

    next_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    next_conversation_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    data = AssistantRequestCreate.model_validate(
        {
            "requestId": str(next_id),
            "acknowledgedUncertainRequestId": str(REQUEST_ID),
            "input": {"type": "text", "text": "새 일정을 등록해 줘"},
        }
    )
    admitted = admit_request(
        assistant,
        data=data,
        fingerprint=request_fingerprint(data),
        accepted_at=NOW + timedelta(seconds=1),
        new_conversation_id=next_conversation_id,
        runtime_ready=True,
        provider_available=True,
    )
    assert admitted.kind.value == "accepted"
    with assistant() as session:
        original = session.get(AssistantRequest, REQUEST_ID)
        assert original.result_json == original_result
        assert original.uncertain_resolved_at is None
        assert original.uncertain_acknowledged_at == NOW + timedelta(seconds=1)
        assert original.uncertain_acknowledged_by_request_id == next_id
        assert session.get(AssistantRequest, next_id).status == "accepted"
        assert session.get(AssistantWriteGate, 1).owner_request_id is None

    provider = ScriptedDecisionProvider(
        [
            DecisionEnvelope.model_validate(
                {
                    "decision": {
                        "kind": "create_intent",
                        "text": "등록합니다.",
                        "title": "후속 일정",
                        "scheduledAt": "2026-10-19T06:00:00Z",
                        "category": "OTHER",
                        "timeInterpretation": "absolute",
                    }
                }
            )
        ]
    )
    assert (
        process_one(
            assistant,
            request_id=next_id,
            provider=provider,
            schedule_reader=SqlAlchemyScheduleReader(api),
            clock=FixedClock(NOW + timedelta(seconds=1)),
            time_trust_source=TrustedTime(),
            uuid_factory=Uuids(),
            runtime_guard=guard(),
            safe_writer=writer(assistant, api),
        )
        is ProcessOneResult.COMPLETED
    )
    assert provider.call_count == 1

    with api() as session:
        original = session.get(AssistantRequest, REQUEST_ID)
        completed = session.get(AssistantRequest, next_id)
        assert original.result_json == original_result
        assert original.uncertain_resolved_at is None
        assert original.uncertain_acknowledged_at == NOW + timedelta(seconds=1)
        assert completed.result_kind == "outcome"
        assert completed.result_json["outcome"] == "success"
        assert completed.result_json["schedulesChanged"] is True
        assert len(completed.result_json["affectedScheduleIds"]) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 1
        assert session.get(AssistantWriteGate, 1).owner_request_id is None


def test_cancel_before_marker_prevents_late_writer_and_schedule_effect(stores) -> None:
    assistant, api = stores
    pending_request(assistant, pending=dict(CREATE_PENDING))
    response = cancel_assistant_target(
        assistant,
        conversation_id=CONVERSATION_ID,
        target=AssistantCancelTarget.model_validate(
            {"type": "request", "id": str(REQUEST_ID)}
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert response.target_state.value == "cancelled"

    assert (
        writer(assistant, api).execute(request_id=REQUEST_ID, runtime_guard=guard())
        is ProcessOneResult.LOST_FINALIZE
    )
    with api() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.status == "completed"
        assert request.result_json["outcome"] == "cancelled"
        assert request.write_started is False
        assert request.write_started_at is None
        assert request.write_operation is None
        assert session.get(AssistantWriteGate, 1).owner_request_id is None
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord))
            == 0
        )


def test_marker_before_cancel_returns_not_guaranteed_and_preserves_owner(
    stores,
) -> None:
    assistant, api = stores
    pending_request(assistant, pending=dict(CREATE_PENDING))

    def stop_after_marker(_request_id: UUID) -> None:
        raise SystemExit

    with pytest.raises(SystemExit):
        writer(assistant, api, after_marker=stop_after_marker).execute(
            request_id=REQUEST_ID, runtime_guard=guard()
        )

    response = cancel_assistant_target(
        assistant,
        conversation_id=CONVERSATION_ID,
        target=AssistantCancelTarget.model_validate(
            {"type": "request", "id": str(REQUEST_ID)}
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert response.target_state.value == "not_guaranteed"
    with assistant() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request.status == "processing"
        assert request.result_kind is None
        assert request.result_json is None
        assert request.write_started is True
        assert request.write_operation == "create"
        assert request.write_commit_mode == "idempotent_external"
        assert session.get(AssistantWriteGate, 1).owner_request_id == REQUEST_ID
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0

    other_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    with assistant.begin() as session:
        assert (
            repository.release_write_gate(session, owner_request_id=other_id) is False
        )
        assert session.get(AssistantWriteGate, 1).owner_request_id == REQUEST_ID
        assert (
            repository.release_write_gate(session, owner_request_id=REQUEST_ID) is True
        )
    with assistant() as session:
        assert session.get(AssistantWriteGate, 1).owner_request_id is None


def test_running_gate_owner_contention_uses_bounded_injected_waits(stores) -> None:
    assistant, api = stores
    pending_request(assistant, pending=dict(CREATE_PENDING))

    def stop_after_marker(_request_id: UUID) -> None:
        raise SystemExit

    with pytest.raises(SystemExit):
        writer(assistant, api, after_marker=stop_after_marker).execute(
            request_id=REQUEST_ID, runtime_guard=guard()
        )

    next_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    next_conversation_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    pending_request(
        assistant,
        request_id=next_id,
        conversation_id=next_conversation_id,
        pending={**CREATE_PENDING, "title": "대기 요청"},
    )
    waits: list[float] = []
    retry_delays = (0.1, 0.2, 0.4)
    monotonic = FixedMonotonicClock(5.0)
    runtime_guard = ProcessDeadlineGuard(
        monotonic_clock=monotonic,
        deadline=30.0,
        stop_signal=Event(),
    )
    contended_writer = AssistantSafeWriter(
        assistant_session_factory=assistant,
        schedule_session_factory=api,
        clock=FixedClock(NOW),
        uuid_factory=Uuids(),
        gate_waiter=waits.append,
        gate_retry_delays=retry_delays,
    )

    assert (
        contended_writer.execute(request_id=next_id, runtime_guard=runtime_guard)
        is ProcessOneResult.COMPLETED
    )
    assert waits == list(retry_delays)
    assert monotonic.now() == 5.0
    assert runtime_guard.deadline == 30.0

    with assistant() as session:
        owner = session.get(AssistantRequest, REQUEST_ID)
        blocked = session.get(AssistantRequest, next_id)
        assert owner.status == "processing"
        assert owner.write_started is True
        assert blocked.status == "completed"
        assert blocked.result_kind == "failure"
        assert blocked.result_json["code"] == "SCHEDULE_UNAVAILABLE"
        assert blocked.result_json["nextAction"] == "retry_later"
        assert blocked.write_started is False
        assert blocked.write_started_at is None
        assert blocked.write_operation is None
        assert session.get(AssistantWriteGate, 1).owner_request_id == REQUEST_ID
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0
        assert (
            session.scalar(sa.select(sa.func.count()).select_from(IdempotencyRecord))
            == 0
        )


def test_late_create_resolution_cannot_release_a_new_gate_owner(stores) -> None:
    assistant, api = stores
    create_pending = {
        "version": 1,
        "operation": "create",
        "title": "반품 검수",
        "scheduledAt": "2026-10-19T05:00:00Z",
        "category": "RETURN",
        "timeInterpretation": "absolute",
    }
    pending_request(assistant, pending=create_pending)

    def timeout_after_marker(request_id: UUID) -> None:
        with assistant.begin() as session:
            timeout_request(
                session,
                request_id=request_id,
                at=NOW,
                prewrite_result_json=timeout_snapshot(),
            )

    original_writer = writer(assistant, api, after_marker=timeout_after_marker)
    original_writer.execute(request_id=REQUEST_ID, runtime_guard=guard())
    next_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    with assistant.begin() as session:
        session.add(
            AssistantRequest(
                id=next_id,
                conversation_id=CONVERSATION_ID,
                fingerprint_version=1,
                fingerprint_hash="d" * 64,
                normalized_body_json={"requestId": str(next_id)},
                status="processing",
                result_kind=None,
                result_json=None,
                accepted_at=NOW + timedelta(seconds=1),
                processing_at=NOW + timedelta(seconds=1),
                completed_at=None,
                task_reference_accepted_at=NOW + timedelta(seconds=1),
                write_started=False,
                write_started_at=None,
                write_operation=None,
                write_commit_mode=None,
                pending_write_version=1,
                pending_write_json={**create_pending, "title": "새 작업"},
                create_idempotency_key=None,
                create_idempotency_scope=None,
                had_uncertain=False,
                uncertain_at=None,
                uncertain_acknowledged_at=None,
                uncertain_acknowledged_by_request_id=None,
                uncertain_resolved_at=None,
                retention_started_at=None,
                tombstone=False,
                tombstoned_at=None,
                cancelled_at=None,
            )
        )
        session.flush()
        original = session.get(AssistantRequest, REQUEST_ID)
        original.uncertain_acknowledged_at = NOW + timedelta(seconds=1)
        original.uncertain_acknowledged_by_request_id = next_id
        repository.release_write_gate(session, owner_request_id=REQUEST_ID)

    def crash(_request_id: UUID) -> None:
        raise SystemExit

    with pytest.raises(SystemExit):
        writer(assistant, api, after_marker=crash).execute(
            request_id=next_id, runtime_guard=guard()
        )
    evidence = create_idempotent(
        api,
        data=ScheduleCreate.model_validate(
            {
                "title": "반품 검수",
                "scheduledAt": "2026-10-19T05:00:00Z",
                "category": "RETURN",
            }
        ),
        scope="assistant-v1",
        idempotency_key=str(REQUEST_ID),
        clock=FixedClock(NOW),
        uuid_factory=Uuids(),
    )
    assert evidence.schedule_id is not None
    assert (
        original_writer.resolve_create_evidence(request_id=REQUEST_ID)
        is ProcessOneResult.COMPLETED
    )
    with assistant() as session:
        original = session.get(AssistantRequest, REQUEST_ID)
        assert original.result_json["outcome"] == "success"
        assert original.uncertain_resolved_at == NOW
        assert session.get(AssistantWriteGate, 1).owner_request_id == next_id


def test_production_without_provider_executes_complete_confirmation(
    database_path: Path,
) -> None:
    application = create_app(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    )
    application.state.clock = FixedClock(NOW)
    with TestClient(application) as client:
        with application.state.session_factory.begin() as session:
            session.add(
                Schedule(
                    id=SCHEDULE_ID,
                    title="재고 확인",
                    scheduled_at=NOW + timedelta(hours=1),
                    category=Category.INVENTORY,
                    completed=False,
                    created_at=OLD,
                    updated_at=OLD,
                    deleted_at=None,
                )
            )
        source_id = UUID("11111111-1111-4111-8111-111111111111")
        question_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
        with application.state.assistant_session_factory.begin() as session:
            session.add(
                AssistantConversation(
                    id=CONVERSATION_ID,
                    created_at=OLD,
                    last_valid_input_at=OLD,
                    expired_at=None,
                    control_state_version=None,
                    control_state_json=None,
                )
            )
            result = {
                "kind": "confirmation",
                "text": "삭제할까요?",
                "questionId": str(question_id),
                "expiresAt": "2026-10-18T05:10:00Z",
                "operation": "delete",
                "target": {
                    "id": str(SCHEDULE_ID),
                    "title": "재고 확인",
                    "scheduledAt": "2026-10-18T06:00:00Z",
                    "category": "INVENTORY",
                    "completed": False,
                    "updatedAt": "2026-10-18T04:55:00Z",
                },
            }
            session.add(
                AssistantRequest(
                    id=source_id,
                    conversation_id=CONVERSATION_ID,
                    fingerprint_version=1,
                    fingerprint_hash="1" * 64,
                    normalized_body_json={"requestId": str(source_id)},
                    status="completed",
                    result_kind="confirmation",
                    result_json=result,
                    accepted_at=OLD,
                    processing_at=OLD,
                    completed_at=OLD,
                    task_reference_accepted_at=OLD,
                    write_started=False,
                    write_started_at=None,
                    write_operation=None,
                    write_commit_mode=None,
                    pending_write_version=None,
                    pending_write_json=None,
                    create_idempotency_key=None,
                    create_idempotency_scope=None,
                    had_uncertain=False,
                    uncertain_at=None,
                    uncertain_acknowledged_at=None,
                    uncertain_acknowledged_by_request_id=None,
                    uncertain_resolved_at=None,
                    retention_started_at=None,
                    tombstone=False,
                    tombstoned_at=None,
                    cancelled_at=None,
                )
            )
            session.flush()
            session.add(
                AssistantQuestion(
                    id=question_id,
                    conversation_id=CONVERSATION_ID,
                    source_request_id=source_id,
                    kind="confirmation",
                    payload_json=result,
                    candidates_json=None,
                    candidate_intents_json={
                        "version": 1,
                        "intent": {
                            "version": 1,
                            "operation": "delete",
                            "targetScheduleId": str(SCHEDULE_ID),
                            "expectedUpdatedAt": "2026-10-18T04:55:00Z",
                            "timeInterpretation": "absolute",
                        },
                    },
                    status="active",
                    created_at=OLD,
                    expires_at=NOW + timedelta(minutes=10),
                    deactivated_at=None,
                    answered_by_request_id=None,
                )
            )

        request_id = UUID("22222222-2222-4222-8222-222222222222")
        accepted = client.post(
            "/api/v1/assistant/requests",
            json={
                "requestId": str(request_id),
                "conversationId": str(CONVERSATION_ID),
                "input": {
                    "type": "confirmation",
                    "questionId": str(question_id),
                    "approved": True,
                },
            },
        )
        assert accepted.status_code == 202
        for _ in range(100):
            status = client.get(f"/api/v1/assistant/requests/{request_id}")
            if status.json()["status"] == "completed":
                break
            Event().wait(0.01)
        assert status.json()["result"]["outcome"] == "success"
        assert status.json()["result"]["operation"] == "delete"
        with application.state.session_factory() as session:
            assert session.get(Schedule, SCHEDULE_ID).deleted_at == NOW
        assert application.state.assistant_provider_available is False
        assert application.state.assistant_runtime_ready is True
    application.state.engine.dispose()
