from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
import pytest

from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.database import create_assistant_engine, create_session_factory
from app.modules.assistant.cleanup import cleanup_once
from app.modules.assistant.admission import admit_request, request_fingerprint
from app.modules.assistant.errors import AssistantRequestExpiredForGet
from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantRequest,
)
from app.modules.assistant.state_service import get_assistant_request
from app.modules.assistant.schemas import AssistantRequestCreate
from app.modules.schedules.model import IdempotencyRecord

NOW = datetime(2026, 10, 19, 5, 0, tzinfo=timezone.utc)
CONVERSATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


@pytest.fixture
def cleanup_store(database_path: Path):
    engine = create_assistant_engine(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    )
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def add_conversation(session) -> None:
    session.add(
        AssistantConversation(
            id=CONVERSATION_ID,
            created_at=NOW - timedelta(days=3),
            last_valid_input_at=NOW - timedelta(days=2),
            expired_at=None,
            control_state_version=None,
            control_state_json=None,
        )
    )


def add_completed(
    session,
    *,
    index: int,
    retained_at: datetime | None,
    result_kind: str = "outcome",
    create_marker: bool = False,
) -> AssistantRequest:
    request_id = UUID(f"10000000-0000-4000-8000-{index:012x}")
    if result_kind == "uncertain":
        result = {
            "kind": "uncertain",
            "text": "확인할 수 없습니다.",
            "code": "EXECUTION_RESULT_UNKNOWN",
            "nextAction": "check_status",
        }
    else:
        result = {
            "kind": result_kind,
            "text": "완료",
            "outcome": "no_match",
            "schedulesChanged": False,
        }
    request = AssistantRequest(
        id=request_id,
        conversation_id=CONVERSATION_ID,
        fingerprint_version=1,
        fingerprint_hash=f"{index:064x}"[-64:],
        normalized_body_json={"requestId": str(request_id)},
        status="completed",
        result_kind=result_kind,
        result_json=result,
        accepted_at=NOW - timedelta(days=3),
        processing_at=NOW - timedelta(days=3) + timedelta(seconds=1),
        completed_at=NOW - timedelta(days=2),
        task_reference_accepted_at=NOW - timedelta(days=3),
        write_started=create_marker,
        write_started_at=NOW - timedelta(days=2) if create_marker else None,
        write_operation="create" if create_marker else None,
        write_commit_mode="idempotent_external" if create_marker else None,
        pending_write_version=None,
        pending_write_json=None,
        create_idempotency_key=str(request_id) if create_marker else None,
        create_idempotency_scope="assistant-v1" if create_marker else None,
        had_uncertain=result_kind == "uncertain",
        uncertain_at=NOW - timedelta(days=2) if result_kind == "uncertain" else None,
        uncertain_acknowledged_at=None,
        uncertain_acknowledged_by_request_id=None,
        uncertain_resolved_at=None,
        retention_started_at=retained_at,
        tombstone=False,
        tombstoned_at=None,
        cancelled_at=None,
    )
    session.add(request)
    return request


def test_cleanup_boundary_is_bounded_and_get_is_read_only(cleanup_store) -> None:
    with cleanup_store.begin() as session:
        add_conversation(session)
        before = add_completed(
            session,
            index=1,
            retained_at=NOW - timedelta(hours=24) + timedelta(microseconds=1),
        )
        exact = add_completed(session, index=2, retained_at=NOW - timedelta(hours=24))
        unresolved = add_completed(
            session, index=3, retained_at=None, result_kind="uncertain"
        )
        ids = before.id, exact.id, unresolved.id

    before_get = None
    with cleanup_store() as session:
        row = session.get(AssistantRequest, ids[0])
        before_get = (row.result_json, row.tombstone, row.retention_started_at)
    assert (
        get_assistant_request(cleanup_store, request_id=ids[0]).status.value
        == "completed"
    )
    with cleanup_store() as session:
        row = session.get(AssistantRequest, ids[0])
        assert (row.result_json, row.tombstone, row.retention_started_at) == before_get

    assert cleanup_once(cleanup_store, wall_clock=FixedClock(NOW), batch_size=1) == 1
    with cleanup_store() as session:
        assert session.get(AssistantRequest, ids[0]).tombstone is False
        cleaned = session.get(AssistantRequest, ids[1])
        assert cleaned.tombstone is True
        assert cleaned.result_json is None
        assert cleaned.normalized_body_json is None
        assert session.get(AssistantRequest, ids[2]).tombstone is False


def test_cleanup_removes_only_exact_assistant_record_and_minimizes_question(
    cleanup_store,
) -> None:
    with cleanup_store.begin() as session:
        add_conversation(session)
        request = add_completed(
            session,
            index=4,
            retained_at=NOW - timedelta(hours=24),
            create_marker=True,
        )
        session.flush()
        session.add(
            AssistantQuestion(
                id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
                conversation_id=CONVERSATION_ID,
                source_request_id=request.id,
                kind="confirmation",
                payload_json={"private": "detail"},
                candidates_json=[{"private": True}],
                candidate_intents_json={"private": True},
                status="cancelled",
                created_at=NOW - timedelta(days=2),
                expires_at=NOW - timedelta(days=2) + timedelta(minutes=10),
                deactivated_at=NOW - timedelta(days=2),
                answered_by_request_id=None,
            )
        )
        for scope in ("assistant-v1", "default"):
            session.add(
                IdempotencyRecord(
                    scope=scope,
                    idempotency_key=str(request.id),
                    fingerprint="f" * 64,
                    schedule_id=UUID("77777777-7777-4777-8777-777777777777"),
                    response_body="{}",
                    created_at=NOW - timedelta(days=2),
                    expires_at=NOW + timedelta(days=2),
                )
            )
        request_id = request.id

    assert cleanup_once(cleanup_store, wall_clock=FixedClock(NOW)) == 1
    with cleanup_store() as session:
        request = session.get(AssistantRequest, request_id)
        question = session.scalar(sa.select(AssistantQuestion))
        assert request.tombstone is True
        assert request.write_started is False
        assert request.write_operation is None
        assert request.create_idempotency_scope is None
        assert question.payload_json == {}
        assert question.candidates_json is None
        assert question.candidate_intents_json is None
        assert session.get(IdempotencyRecord, ("assistant-v1", str(request_id))) is None
        assert session.get(IdempotencyRecord, ("default", str(request_id))) is not None


def test_cleanup_batch_is_deterministically_limited_to_one_hundred(
    cleanup_store,
) -> None:
    with cleanup_store.begin() as session:
        add_conversation(session)
        for index in range(100, 201):
            add_completed(
                session,
                index=index,
                retained_at=NOW - timedelta(hours=25, seconds=index),
            )

    assert cleanup_once(cleanup_store, wall_clock=FixedClock(NOW)) == 100
    with cleanup_store() as session:
        tombstones = session.scalar(
            sa.select(sa.func.count())
            .select_from(AssistantRequest)
            .where(AssistantRequest.tombstone.is_(True))
        )
        remaining = session.scalar(
            sa.select(AssistantRequest.id)
            .where(AssistantRequest.tombstone.is_(False))
            .order_by(AssistantRequest.retention_started_at, AssistantRequest.id)
        )
        assert tombstones == 100
        assert remaining == UUID("10000000-0000-4000-8000-000000000064")


def test_tombstone_get_expires_and_had_uncertain_ack_contract_survives(
    cleanup_store,
) -> None:
    with cleanup_store.begin() as session:
        add_conversation(session)
        request = add_completed(
            session,
            index=300,
            retained_at=NOW - timedelta(hours=24),
        )
        request.had_uncertain = True
        request.uncertain_at = NOW - timedelta(days=2)
        request.uncertain_resolved_at = NOW - timedelta(hours=24)
        request_id = request.id
    assert cleanup_once(cleanup_store, wall_clock=FixedClock(NOW)) == 1
    with pytest.raises(AssistantRequestExpiredForGet):
        get_assistant_request(cleanup_store, request_id=request_id)

    next_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    data = AssistantRequestCreate.model_validate(
        {
            "requestId": str(next_id),
            "acknowledgedUncertainRequestId": str(request_id),
            "input": {"type": "text", "text": "새 작업"},
        }
    )
    admitted = admit_request(
        cleanup_store,
        data=data,
        fingerprint=request_fingerprint(data),
        accepted_at=NOW,
        new_conversation_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        runtime_ready=True,
        provider_available=True,
    )
    assert admitted.kind.value == "accepted"


def test_question_result_waits_for_actual_deactivation_boundary(cleanup_store) -> None:
    question_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    with cleanup_store.begin() as session:
        add_conversation(session)
        request = add_completed(
            session,
            index=400,
            retained_at=NOW - timedelta(hours=24),
        )
        request.result_kind = "confirmation"
        request.result_json = {"kind": "confirmation", "text": "확인"}
        session.flush()
        session.add(
            AssistantQuestion(
                id=question_id,
                conversation_id=CONVERSATION_ID,
                source_request_id=request.id,
                kind="confirmation",
                payload_json={"kind": "confirmation", "text": "확인"},
                candidates_json=None,
                candidate_intents_json=None,
                status="active",
                created_at=NOW - timedelta(days=2),
                expires_at=NOW + timedelta(minutes=10),
                deactivated_at=None,
                answered_by_request_id=None,
            )
        )
        request_id = request.id

    assert cleanup_once(cleanup_store, wall_clock=FixedClock(NOW)) == 0
    with cleanup_store.begin() as session:
        question = session.get(AssistantQuestion, question_id)
        question.status = "cancelled"
        question.deactivated_at = NOW - timedelta(hours=24)
    assert cleanup_once(cleanup_store, wall_clock=FixedClock(NOW)) == 1
    with cleanup_store() as session:
        assert session.get(AssistantRequest, request_id).tombstone is True
