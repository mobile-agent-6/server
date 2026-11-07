from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_assistant_engine, create_session_factory
from app.modules.assistant.enums import (
    AssistantCancelTargetState,
    AssistantRequestStatus,
)
from app.modules.assistant.errors import (
    AssistantDatabaseUnavailable,
    AssistantTargetNotFound,
)
from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
)
from app.modules.assistant.schemas import AssistantCancelTarget
from app.modules.assistant.state_service import (
    AssistantStateCorrupted,
    cancel_assistant_target,
    get_assistant_request,
)

NOW = datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)
SCHEDULE_ID = "77777777-7777-4777-8777-777777777777"
QUESTION_ID = "44444444-4444-4444-8444-444444444444"


@pytest.fixture
def state_store(database_path: Path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_assistant_engine(settings)
    factory = create_session_factory(engine)
    yield factory, engine
    engine.dispose()


def add_conversation(session: Session, conversation_id: UUID) -> None:
    session.add(
        AssistantConversation(
            id=conversation_id,
            created_at=NOW - timedelta(hours=1),
            last_valid_input_at=NOW - timedelta(minutes=30),
            expired_at=None,
            control_state_version=None,
            control_state_json=None,
        )
    )


def add_request(
    session: Session,
    *,
    conversation_id: UUID,
    request_id: UUID,
    status: str = "accepted",
    result: dict[str, object] | None = None,
    write_started: bool = False,
) -> AssistantRequest:
    completed = status == "completed"
    request = AssistantRequest(
        id=request_id,
        conversation_id=conversation_id,
        fingerprint_version=1,
        fingerprint_hash="a" * 64,
        normalized_body_json={"requestId": str(request_id)},
        status=status,
        result_kind=(str(result["kind"]) if result is not None else None),
        result_json=result,
        accepted_at=NOW - timedelta(minutes=10),
        processing_at=(NOW - timedelta(minutes=9) if status == "processing" else None),
        completed_at=(NOW - timedelta(minutes=5) if completed else None),
        task_reference_accepted_at=NOW - timedelta(minutes=10),
        write_started=write_started,
        write_started_at=(NOW - timedelta(minutes=8) if write_started else None),
        write_operation=("update" if write_started else None),
        write_commit_mode=("atomic" if write_started else None),
        pending_write_version=None,
        pending_write_json=None,
        create_idempotency_key=None,
        create_idempotency_scope=None,
        had_uncertain=(result is not None and result["kind"] == "uncertain"),
        uncertain_at=(
            NOW - timedelta(minutes=5)
            if result is not None and result["kind"] == "uncertain"
            else None
        ),
        uncertain_acknowledged_at=None,
        uncertain_acknowledged_by_request_id=None,
        uncertain_resolved_at=None,
        retention_started_at=(
            NOW - timedelta(minutes=5)
            if completed and result is not None and result["kind"] != "uncertain"
            else None
        ),
        tombstone=False,
        tombstoned_at=None,
        cancelled_at=None,
    )
    session.add(request)
    return request


PUBLIC_RESULTS = [
    {
        "kind": "need_more_info",
        "text": "Please provide a time.",
        "questionId": QUESTION_ID,
    },
    {
        "kind": "candidate_selection",
        "text": "Choose a schedule.",
        "questionId": QUESTION_ID,
        "expiresAt": "2026-10-18T05:10:00Z",
        "candidates": [
            {
                "candidateId": "A",
                "title": "First",
                "scheduledAt": "2026-10-18T06:00:00Z",
                "category": "INVENTORY",
            },
            {
                "candidateId": "B",
                "title": "Second",
                "scheduledAt": "2026-10-18T07:00:00Z",
                "category": "RETURN",
            },
        ],
    },
    {
        "kind": "confirmation",
        "text": "Apply this update?",
        "questionId": QUESTION_ID,
        "expiresAt": "2026-10-18T05:10:00Z",
        "operation": "update",
        "target": {
            "id": SCHEDULE_ID,
            "title": "Inventory check",
            "scheduledAt": "2026-10-18T06:00:00Z",
            "category": "INVENTORY",
            "completed": False,
            "updatedAt": "2026-10-18T04:00:00Z",
        },
        "changes": {"completed": True},
    },
    {
        "kind": "outcome",
        "text": "No matching schedule was found.",
        "outcome": "no_match",
        "schedulesChanged": False,
    },
    {
        "kind": "failure",
        "text": "The request could not be processed.",
        "code": "MODEL_OUTPUT_INVALID",
        "nextAction": "revise_input",
    },
    {
        "kind": "uncertain",
        "text": "The execution result is unknown.",
        "code": "EXECUTION_RESULT_UNKNOWN",
        "nextAction": "check_status",
    },
]


@pytest.mark.parametrize("result", PUBLIC_RESULTS)
def test_completed_snapshots_are_strictly_revalidated_for_public_get(
    state_store, result: dict[str, object]
) -> None:
    factory, _ = state_store
    conversation_id = uuid4()
    request_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=request_id,
            status="completed",
            result=result,
        )

    response = get_assistant_request(factory, request_id=request_id)

    assert response.request_id == request_id
    assert response.conversation_id == conversation_id
    assert response.status.value == "completed"
    assert response.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )["result"] == result


def test_corrupt_result_is_not_exposed_or_guessed(state_store) -> None:
    factory, engine = state_store
    conversation_id = uuid4()
    request_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=request_id,
            status="completed",
            result=PUBLIC_RESULTS[3],
        )
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE assistant_requests SET result_json = :result WHERE id = :id"
            ),
            {
                "id": request_id.hex,
                "result": '{"kind":"failure","text":"private-corruption"}',
            },
        )

    with pytest.raises(AssistantStateCorrupted) as captured:
        get_assistant_request(factory, request_id=request_id)

    assert "private-corruption" not in str(captured.value)


def request_target(request_id: UUID) -> AssistantCancelTarget:
    return AssistantCancelTarget.model_validate(
        {"type": "request", "id": str(request_id)}
    )


def question_target(question_id: UUID) -> AssistantCancelTarget:
    return AssistantCancelTarget.model_validate(
        {"type": "question", "id": str(question_id)}
    )


@pytest.mark.parametrize("initial_status", ["accepted", "processing"])
def test_request_cancel_completes_once_without_extending_conversation(
    state_store, initial_status: str
) -> None:
    factory, _ = state_store
    conversation_id = uuid4()
    request_id = uuid4()
    last_valid_input = NOW - timedelta(minutes=30)
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=request_id,
            status=initial_status,
        )

    first = cancel_assistant_target(
        factory,
        conversation_id=conversation_id,
        target=request_target(request_id),
        now=NOW,
    )
    second = cancel_assistant_target(
        factory,
        conversation_id=conversation_id,
        target=request_target(request_id),
        now=NOW + timedelta(seconds=1),
    )

    assert first.target_state is AssistantCancelTargetState.CANCELLED
    assert second.target_state is AssistantCancelTargetState.ALREADY_COMPLETED
    assert first.text == "대상을 취소했습니다."
    assert second.text == "요청이 이미 완료되었습니다."
    with factory() as session:
        stored = session.get(AssistantRequest, request_id)
        conversation = session.get(AssistantConversation, conversation_id)
        assert stored is not None and conversation is not None
        assert stored.status == "completed"
        assert stored.result_json == {
            "kind": "outcome",
            "outcome": "cancelled",
            "schedulesChanged": False,
            "text": "요청을 취소했습니다.",
        }
        assert stored.completed_at == stored.cancelled_at == stored.retention_started_at == NOW
        assert conversation.last_valid_input_at == last_valid_input


def test_concurrent_request_cancel_has_one_transition_and_stable_followup(
    state_store,
) -> None:
    factory, _ = state_store
    conversation_id = uuid4()
    request_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=request_id,
        )
    barrier = Barrier(2)

    def cancel_once() -> AssistantCancelTargetState:
        barrier.wait(timeout=5)
        return cancel_assistant_target(
            factory,
            conversation_id=conversation_id,
            target=request_target(request_id),
            now=NOW,
        ).target_state

    with ThreadPoolExecutor(max_workers=2) as executor:
        states = list(executor.map(lambda _: cancel_once(), range(2)))

    assert sorted(state.value for state in states) == [
        "already_completed",
        "cancelled",
    ]


def test_tombstoned_request_cancel_is_already_completed_without_mutation(
    state_store,
) -> None:
    factory, engine = state_store
    conversation_id = uuid4()
    request_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        request = add_request(
            session,
            conversation_id=conversation_id,
            request_id=request_id,
            status="completed",
            result=PUBLIC_RESULTS[3],
        )
        request.tombstone = True
        request.tombstoned_at = NOW
        request.result_kind = None
        request.result_json = None

    with engine.connect() as connection:
        before = tuple(
            connection.execute(
                sa.text("SELECT * FROM assistant_requests WHERE id = :id"),
                {"id": request_id.hex},
            ).one()
        )

    response = cancel_assistant_target(
        factory,
        conversation_id=conversation_id,
        target=request_target(request_id),
        now=NOW + timedelta(minutes=1),
    )

    assert response.target_state is AssistantCancelTargetState.ALREADY_COMPLETED
    with engine.connect() as connection:
        after = tuple(
            connection.execute(
                sa.text("SELECT * FROM assistant_requests WHERE id = :id"),
                {"id": request_id.hex},
            ).one()
        )
    assert after == before


def test_cancel_database_lock_rolls_back_without_partial_state(
    state_store, database_path: Path
) -> None:
    factory, engine = state_store
    conversation_id = uuid4()
    request_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=request_id,
        )

    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    fast_engine = create_assistant_engine(settings, busy_timeout_ms=0)
    fast_factory = create_session_factory(fast_engine)
    locked_connection = engine.connect()
    transaction = locked_connection.begin()
    try:
        with pytest.raises(AssistantDatabaseUnavailable):
            cancel_assistant_target(
                fast_factory,
                conversation_id=conversation_id,
                target=request_target(request_id),
                now=NOW,
            )
    finally:
        transaction.rollback()
        locked_connection.close()
        fast_engine.dispose()

    with factory() as session:
        stored = session.get(AssistantRequest, request_id)
        assert stored is not None
        assert stored.status == AssistantRequestStatus.ACCEPTED.value
        assert stored.result_kind is None
        assert stored.result_json is None
        assert stored.completed_at is None
        assert stored.cancelled_at is None
        assert stored.retention_started_at is None


def test_write_marker_and_uncertain_results_are_not_overwritten(state_store) -> None:
    factory, _ = state_store
    conversation_id = uuid4()
    marked_id = uuid4()
    uncertain_id = uuid4()
    uncertain = PUBLIC_RESULTS[-1]
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=marked_id,
            status="processing",
            write_started=True,
        )
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=uncertain_id,
            status="completed",
            result=uncertain,
        )
    marked_before = None
    with factory() as session:
        marked_before = session.get(AssistantRequest, marked_id).result_json  # type: ignore[union-attr]

    marked_response = cancel_assistant_target(
        factory,
        conversation_id=conversation_id,
        target=request_target(marked_id),
        now=NOW,
    )
    assert marked_response.target_state is AssistantCancelTargetState.NOT_GUARANTEED
    assert marked_response.text == (
        "취소 여부를 보장할 수 없습니다. 원래 요청을 확인해 주세요."
    )

    uncertain_response = cancel_assistant_target(
        factory,
        conversation_id=conversation_id,
        target=request_target(uncertain_id),
        now=NOW + timedelta(seconds=1),
    )
    assert uncertain_response.target_state is AssistantCancelTargetState.UNCERTAIN
    assert uncertain_response.text == "요청 결과가 아직 불확실합니다."
    with factory() as session:
        marked = session.get(AssistantRequest, uncertain_id)
        assert marked is not None
        assert marked.result_json == uncertain
        assert marked_before is None


def test_question_cancel_and_expiry_do_not_change_source_result(state_store) -> None:
    factory, _ = state_store
    conversation_id = uuid4()
    source_request_id = uuid4()
    active_id = uuid4()
    expired_id = uuid4()
    source_result = PUBLIC_RESULTS[3]
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=source_request_id,
            status="completed",
            result=source_result,
        )
        session.flush()
        session.add_all(
            [
                AssistantQuestion(
                    id=active_id,
                    conversation_id=conversation_id,
                    source_request_id=source_request_id,
                    kind="need_more_info",
                    payload_json={"kind": "need_more_info", "text": "Question"},
                    candidates_json=None,
                    candidate_intents_json=None,
                    status="active",
                    created_at=NOW - timedelta(minutes=1),
                    expires_at=NOW + timedelta(minutes=1),
                    deactivated_at=None,
                    answered_by_request_id=None,
                ),
            ]
        )

    cancelled = cancel_assistant_target(
        factory,
        conversation_id=conversation_id,
        target=question_target(active_id),
        now=NOW,
    )
    assert cancelled.target_state is AssistantCancelTargetState.CANCELLED
    assert cancelled.text == "대상을 취소했습니다."

    with factory.begin() as session:
        session.add(
            AssistantQuestion(
                id=expired_id,
                conversation_id=conversation_id,
                source_request_id=source_request_id,
                kind="confirmation",
                payload_json={"kind": "confirmation", "text": "Confirm"},
                candidates_json=None,
                candidate_intents_json=None,
                status="active",
                created_at=NOW - timedelta(minutes=2),
                expires_at=NOW,
                deactivated_at=None,
                answered_by_request_id=None,
            )
        )

    expired = cancel_assistant_target(
        factory,
        conversation_id=conversation_id,
        target=question_target(expired_id),
        now=NOW,
    )
    assert expired.target_state is AssistantCancelTargetState.NOT_ACTIVE
    assert expired.text == "질문이 더 이상 활성 상태가 아닙니다."
    with factory() as session:
        active = session.get(AssistantQuestion, active_id)
        old = session.get(AssistantQuestion, expired_id)
        source = session.get(AssistantRequest, source_request_id)
        assert active is not None and old is not None and source is not None
        assert active.status == AssistantQuestionStatus.CANCELLED.value
        assert old.status == AssistantQuestionStatus.EXPIRED.value
        assert source.result_json == source_result


def test_concurrent_question_cancel_is_deterministic(state_store) -> None:
    factory, _ = state_store
    conversation_id = uuid4()
    source_request_id = uuid4()
    question_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=source_request_id,
            status="completed",
            result=PUBLIC_RESULTS[3],
        )
        session.flush()
        session.add(
            AssistantQuestion(
                id=question_id,
                conversation_id=conversation_id,
                source_request_id=source_request_id,
                kind="need_more_info",
                payload_json={"kind": "need_more_info", "text": "Question"},
                candidates_json=None,
                candidate_intents_json=None,
                status="active",
                created_at=NOW,
                expires_at=None,
                deactivated_at=None,
                answered_by_request_id=None,
            )
        )
    barrier = Barrier(2)

    def cancel_once() -> AssistantCancelTargetState:
        barrier.wait(timeout=5)
        return cancel_assistant_target(
            factory,
            conversation_id=conversation_id,
            target=question_target(question_id),
            now=NOW,
        ).target_state

    with ThreadPoolExecutor(max_workers=2) as executor:
        states = list(executor.map(lambda _: cancel_once(), range(2)))

    assert sorted(state.value for state in states) == ["cancelled", "not_active"]


@pytest.mark.parametrize(
    "status",
    [
        AssistantQuestionStatus.ANSWERED.value,
        AssistantQuestionStatus.CANCELLED.value,
        AssistantQuestionStatus.EXPIRED.value,
        AssistantQuestionStatus.INVALIDATED.value,
    ],
)
def test_inactive_question_cancel_is_not_active_without_mutation(
    state_store, status: str
) -> None:
    factory, _ = state_store
    conversation_id = uuid4()
    source_request_id = uuid4()
    question_id = uuid4()
    deactivated_at = NOW - timedelta(minutes=1)
    with factory.begin() as session:
        add_conversation(session, conversation_id)
        add_request(
            session,
            conversation_id=conversation_id,
            request_id=source_request_id,
            status="completed",
            result=PUBLIC_RESULTS[3],
        )
        session.flush()
        session.add(
            AssistantQuestion(
                id=question_id,
                conversation_id=conversation_id,
                source_request_id=source_request_id,
                kind="need_more_info",
                payload_json={"kind": "need_more_info", "text": "Question"},
                candidates_json=None,
                candidate_intents_json=None,
                status=status,
                created_at=NOW - timedelta(minutes=2),
                expires_at=None,
                deactivated_at=deactivated_at,
                answered_by_request_id=(
                    source_request_id
                    if status == AssistantQuestionStatus.ANSWERED.value
                    else None
                ),
            )
        )

    response = cancel_assistant_target(
        factory,
        conversation_id=conversation_id,
        target=question_target(question_id),
        now=NOW,
    )

    assert response.target_state is AssistantCancelTargetState.NOT_ACTIVE
    with factory() as session:
        question = session.get(AssistantQuestion, question_id)
        assert question is not None
        assert question.status == status
        assert question.deactivated_at == deactivated_at


def test_cancel_hides_target_owned_by_another_conversation(state_store) -> None:
    factory, _ = state_store
    owner_id = uuid4()
    other_id = uuid4()
    request_id = uuid4()
    question_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, owner_id)
        add_conversation(session, other_id)
        add_request(
            session,
            conversation_id=owner_id,
            request_id=request_id,
            status="completed",
            result=PUBLIC_RESULTS[3],
        )
        session.flush()
        session.add(
            AssistantQuestion(
                id=question_id,
                conversation_id=owner_id,
                source_request_id=request_id,
                kind="need_more_info",
                payload_json={"kind": "need_more_info", "text": "Question"},
                candidates_json=None,
                candidate_intents_json=None,
                status="active",
                created_at=NOW,
                expires_at=None,
                deactivated_at=None,
                answered_by_request_id=None,
            )
        )

    for target in (request_target(request_id), question_target(question_id)):
        with pytest.raises(AssistantTargetNotFound):
            cancel_assistant_target(
                factory,
                conversation_id=other_id,
                target=target,
                now=NOW,
            )
