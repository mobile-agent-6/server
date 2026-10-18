from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.database import create_assistant_engine, create_session_factory
from app.modules.assistant.admission import (
    AdmissionDatabaseUnavailable,
    AdmissionKind,
    RequestFingerprint,
    admit_request,
    request_fingerprint,
)
from app.modules.assistant.enums import AssistantResultKind
from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
    AssistantWriteGate,
)
from app.modules.assistant.schemas import AssistantRequestCreate
from app.modules.assistant.serialization import (
    CONTROL_STATE_MAX_BYTES,
    SnapshotSerializationError,
)

NOW = datetime(2026, 10, 18, 4, 30, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(hours=1)


@pytest.fixture
def assistant_store(database_path: Path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_assistant_engine(settings)
    factory = create_session_factory(engine)
    yield factory, engine, settings
    engine.dispose()


def request_data(
    *,
    request_id: UUID | None = None,
    conversation_id: UUID | None = None,
    text: str = "재고 확인",
    question_id: UUID | None = None,
    acknowledged_request_id: UUID | None = None,
) -> AssistantRequestCreate:
    payload: dict[str, object] = {
        "requestId": str(request_id or uuid4()),
        "input": {"type": "text", "text": text},
    }
    if conversation_id is not None:
        payload["conversationId"] = str(conversation_id)
    if question_id is not None:
        payload["input"] = {
            "type": "text",
            "text": text,
            "questionId": str(question_id),
        }
    if acknowledged_request_id is not None:
        payload["acknowledgedUncertainRequestId"] = str(acknowledged_request_id)
    return AssistantRequestCreate.model_validate(payload)


def selection_data(
    *, request_id: UUID, conversation_id: UUID, question_id: UUID
) -> AssistantRequestCreate:
    return AssistantRequestCreate.model_validate(
        {
            "requestId": str(request_id),
            "conversationId": str(conversation_id),
            "input": {
                "type": "selection",
                "questionId": str(question_id),
                "candidateId": "Candidate-A",
            },
        }
    )


def admit(
    factory: sessionmaker[Session],
    data: AssistantRequestCreate,
    *,
    now: datetime = NOW,
    new_conversation_id: UUID | None = None,
    fingerprint: RequestFingerprint | None = None,
    runtime_ready: bool = True,
    provider_available: bool = True,
):
    return admit_request(
        factory,
        data=data,
        fingerprint=fingerprint or request_fingerprint(data),
        accepted_at=now,
        new_conversation_id=new_conversation_id or uuid4(),
        runtime_ready=runtime_ready,
        provider_available=provider_available,
    )


def add_conversation(
    session: Session,
    *,
    conversation_id: UUID | None = None,
    last_valid_input_at: datetime = EARLIER,
    control_state: object | None = None,
) -> AssistantConversation:
    conversation = AssistantConversation(
        id=conversation_id or uuid4(),
        created_at=last_valid_input_at,
        last_valid_input_at=last_valid_input_at,
        expired_at=None,
        control_state_version=1 if control_state is not None else None,
        control_state_json=control_state,
    )
    session.add(conversation)
    return conversation


def add_completed_request(
    session: Session,
    *,
    conversation_id: UUID,
    request_id: UUID | None = None,
    result_kind: str = AssistantResultKind.OUTCOME.value,
    task_reference: datetime = EARLIER,
    had_uncertain: bool = False,
    uncertain_at: datetime | None = None,
    acknowledged_at: datetime | None = None,
    acknowledged_by: UUID | None = None,
    resolved_at: datetime | None = None,
) -> AssistantRequest:
    identifier = request_id or uuid4()
    request = AssistantRequest(
        id=identifier,
        conversation_id=conversation_id,
        fingerprint_version=1,
        fingerprint_hash="a" * 64,
        normalized_body_json={"requestId": str(identifier)},
        status="completed",
        result_kind=result_kind,
        result_json={"kind": result_kind, "text": "완료"},
        accepted_at=task_reference,
        processing_at=task_reference,
        completed_at=task_reference,
        task_reference_accepted_at=task_reference,
        write_started=False,
        write_started_at=None,
        write_operation=None,
        write_commit_mode=None,
        pending_write_version=None,
        pending_write_json=None,
        create_idempotency_key=None,
        create_idempotency_scope=None,
        had_uncertain=had_uncertain,
        uncertain_at=uncertain_at,
        uncertain_acknowledged_at=acknowledged_at,
        uncertain_acknowledged_by_request_id=acknowledged_by,
        uncertain_resolved_at=resolved_at,
        retention_started_at=(
            None if result_kind == AssistantResultKind.UNCERTAIN.value else task_reference
        ),
        tombstone=False,
        tombstoned_at=None,
        cancelled_at=None,
    )
    session.add(request)
    return request


def add_active_question(
    session: Session,
    *,
    conversation_id: UUID,
    source_request_id: UUID,
    question_id: UUID | None = None,
    kind: str = AssistantResultKind.NEED_MORE_INFO.value,
    expires_at: datetime | None = None,
) -> AssistantQuestion:
    question = AssistantQuestion(
        id=question_id or uuid4(),
        conversation_id=conversation_id,
        source_request_id=source_request_id,
        kind=kind,
        payload_json={"kind": kind, "text": "질문"},
        candidates_json=([] if kind == "candidate_selection" else None),
        candidate_intents_json=([] if kind == "candidate_selection" else None),
        status=AssistantQuestionStatus.ACTIVE.value,
        created_at=EARLIER,
        expires_at=expires_at,
        deactivated_at=None,
        answered_by_request_id=None,
    )
    session.add(question)
    return question


def row_count(session: Session, model: type[object]) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


def test_first_text_is_atomic_and_replay_does_not_extend_conversation(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    request_id = uuid4()
    conversation_id = uuid4()
    data = request_data(request_id=request_id, text="  재고 확인  ")

    accepted = admit(
        factory,
        data,
        now=NOW,
        new_conversation_id=conversation_id,
    )
    replay = admit(
        factory,
        data,
        now=NOW + timedelta(hours=2),
        new_conversation_id=uuid4(),
    )

    assert accepted.kind is AdmissionKind.ACCEPTED
    assert accepted.conversation_id == conversation_id
    assert replay.kind is AdmissionKind.REPLAY
    assert replay.conversation_id == conversation_id
    assert replay.status == "accepted"
    with factory() as session:
        assert row_count(session, AssistantConversation) == 1
        assert row_count(session, AssistantRequest) == 1
        conversation = session.get(AssistantConversation, conversation_id)
        request = session.get(AssistantRequest, request_id)
        assert conversation is not None
        assert conversation.last_valid_input_at == NOW
        assert request is not None
        assert request.normalized_body_json["input"]["text"] == "재고 확인"
        assert request.task_reference_accepted_at == NOW


def test_readiness_blocks_only_new_ids_after_existing_id_decisions(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    accepted_data = request_data(text="same")
    assert admit(factory, accepted_data).kind is AdmissionKind.ACCEPTED

    replay = admit(factory, accepted_data, runtime_ready=False)
    conflict = admit(
        factory,
        request_data(request_id=accepted_data.request_id, text="different"),
        runtime_ready=False,
    )
    unavailable_data = request_data()
    unavailable = admit(
        factory,
        unavailable_data,
        runtime_ready=False,
    )

    assert replay.kind is AdmissionKind.REPLAY
    assert conflict.kind is AdmissionKind.REQUEST_ID_CONFLICT
    assert unavailable.kind is AdmissionKind.ASSISTANT_UNAVAILABLE
    with factory() as session:
        assert session.get(AssistantRequest, unavailable_data.request_id) is None


def test_same_request_id_different_fingerprint_conflicts_without_writes(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    request_id = uuid4()
    first = request_data(request_id=request_id, text="재고 확인")
    accepted = admit(factory, first)
    assert accepted.kind is AdmissionKind.ACCEPTED

    conflict = admit(
        factory,
        request_data(request_id=request_id, text="다른 작업"),
        now=NOW + timedelta(minutes=1),
    )

    assert conflict.kind is AdmissionKind.REQUEST_ID_CONFLICT
    with factory() as session:
        assert row_count(session, AssistantRequest) == 1
        conversation = session.get(AssistantConversation, accepted.conversation_id)
        assert conversation is not None
        assert conversation.last_valid_input_at == NOW


def test_same_request_id_different_fingerprint_version_conflicts(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    data = request_data()
    assert admit(factory, data).kind is AdmissionKind.ACCEPTED

    different_version = request_fingerprint(data, version=2)
    result = admit(
        factory,
        data,
        now=NOW + timedelta(minutes=1),
        fingerprint=different_version,
    )

    assert result.kind is AdmissionKind.REQUEST_ID_CONFLICT
    with factory() as session:
        assert row_count(session, AssistantRequest) == 1


def test_tombstone_precedes_body_fingerprint_and_returns_expired(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    request_id = uuid4()
    original = request_data(request_id=request_id)
    accepted = admit(factory, original)
    with factory.begin() as session:
        request = session.get(AssistantRequest, request_id)
        assert request is not None
        request.status = "completed"
        request.completed_at = NOW
        request.fingerprint_version = None
        request.fingerprint_hash = None
        request.normalized_body_json = None
        request.tombstone = True
        request.tombstoned_at = NOW

    result = admit(
        factory,
        request_data(request_id=request_id, text="완전히 다른 본문"),
        now=NOW + timedelta(days=2),
    )

    assert result.kind is AdmissionKind.REQUEST_EXPIRED
    assert result.conversation_id == accepted.conversation_id
    with factory() as session:
        request = session.get(AssistantRequest, request_id)
        assert request is not None
        assert request.had_uncertain is False
        assert request.completed_at == NOW
        assert request.tombstoned_at == NOW


def test_missing_and_expired_conversations_reject_without_request_reservation(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    missing_conversation = uuid4()
    missing_data = request_data(conversation_id=missing_conversation)
    assert admit(factory, missing_data).kind is AdmissionKind.CONVERSATION_NOT_FOUND

    expired_conversation = uuid4()
    with factory.begin() as session:
        add_conversation(
            session,
            conversation_id=expired_conversation,
            last_valid_input_at=NOW - timedelta(hours=24),
        )
    expired_data = request_data(conversation_id=expired_conversation)
    assert admit(factory, expired_data).kind is AdmissionKind.CONVERSATION_EXPIRED

    with factory() as session:
        assert session.get(AssistantRequest, missing_data.request_id) is None
        assert session.get(AssistantRequest, expired_data.request_id) is None
        expired = session.get(AssistantConversation, expired_conversation)
        assert expired is not None
        assert expired.expired_at is None
        assert expired.last_valid_input_at == NOW - timedelta(hours=24)


def test_busy_returns_deterministic_blocker_without_extending_lifetime(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    first = request_data()
    accepted = admit(factory, first, now=EARLIER)
    assert accepted.conversation_id is not None
    second = request_data(conversation_id=accepted.conversation_id)

    result = admit(factory, second, now=NOW)

    assert result.kind is AdmissionKind.CONVERSATION_BUSY
    assert result.blocking_request_id == first.request_id
    with factory() as session:
        assert session.get(AssistantRequest, second.request_id) is None
        conversation = session.get(AssistantConversation, accepted.conversation_id)
        assert conversation is not None
        assert conversation.last_valid_input_at == EARLIER


def test_active_question_answer_is_atomic_and_inherits_task_reference(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    conversation_id = uuid4()
    source_id = uuid4()
    question_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id=conversation_id)
        add_completed_request(
            session,
            conversation_id=conversation_id,
            request_id=source_id,
            result_kind=AssistantResultKind.NEED_MORE_INFO.value,
            task_reference=EARLIER,
        )
        session.flush()
        add_active_question(
            session,
            conversation_id=conversation_id,
            source_request_id=source_id,
            question_id=question_id,
        )

    answer = request_data(
        conversation_id=conversation_id,
        question_id=question_id,
        text="오후 세 시",
    )
    result = admit(factory, answer)

    assert result.kind is AdmissionKind.ACCEPTED
    with factory() as session:
        question = session.get(AssistantQuestion, question_id)
        request = session.get(AssistantRequest, answer.request_id)
        assert question is not None
        assert question.status == AssistantQuestionStatus.ANSWERED.value
        assert question.answered_by_request_id == answer.request_id
        assert question.deactivated_at == NOW
        assert request is not None
        assert request.task_reference_accepted_at == EARLIER


@pytest.mark.parametrize("wrong_input", ["missing_id", "wrong_id", "wrong_type"])
def test_active_question_mismatch_is_stale_and_preserves_database(
    assistant_store, wrong_input: str
) -> None:
    factory, _, _ = assistant_store
    conversation_id = uuid4()
    source_id = uuid4()
    question_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id=conversation_id)
        add_completed_request(
            session,
            conversation_id=conversation_id,
            request_id=source_id,
            result_kind=AssistantResultKind.NEED_MORE_INFO.value,
        )
        session.flush()
        add_active_question(
            session,
            conversation_id=conversation_id,
            source_request_id=source_id,
            question_id=question_id,
        )

    request_id = uuid4()
    if wrong_input == "missing_id":
        data = request_data(
            request_id=request_id,
            conversation_id=conversation_id,
        )
    elif wrong_input == "wrong_id":
        data = request_data(
            request_id=request_id,
            conversation_id=conversation_id,
            question_id=uuid4(),
        )
    else:
        data = selection_data(
            request_id=request_id,
            conversation_id=conversation_id,
            question_id=question_id,
        )

    result = admit(factory, data)

    assert result.kind is AdmissionKind.STALE_QUESTION
    assert result.active_question_id == question_id
    assert result.active_question_request_id == source_id
    with factory() as session:
        question = session.get(AssistantQuestion, question_id)
        assert question is not None
        assert question.status == AssistantQuestionStatus.ACTIVE.value
        assert question.deactivated_at is None
        assert session.get(AssistantRequest, request_id) is None


def test_expired_question_rejection_is_read_only_but_successful_new_task_expires_it(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    conversation_id = uuid4()
    source_id = uuid4()
    question_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id=conversation_id)
        add_completed_request(
            session,
            conversation_id=conversation_id,
            request_id=source_id,
            result_kind=AssistantResultKind.NEED_MORE_INFO.value,
        )
        session.flush()
        add_active_question(
            session,
            conversation_id=conversation_id,
            source_request_id=source_id,
            question_id=question_id,
            expires_at=NOW - timedelta(seconds=1),
        )

    expired_answer = request_data(
        conversation_id=conversation_id,
        question_id=question_id,
    )
    rejected = admit(factory, expired_answer)
    assert rejected.kind is AdmissionKind.QUESTION_EXPIRED
    with factory() as session:
        question = session.get(AssistantQuestion, question_id)
        assert question is not None
        assert question.status == AssistantQuestionStatus.ACTIVE.value
        assert session.get(AssistantRequest, expired_answer.request_id) is None

    new_task = request_data(conversation_id=conversation_id, text="새 작업")
    accepted = admit(factory, new_task)
    assert accepted.kind is AdmissionKind.ACCEPTED
    with factory() as session:
        question = session.get(AssistantQuestion, question_id)
        assert question is not None
        assert question.status == AssistantQuestionStatus.EXPIRED.value
        assert question.deactivated_at == NOW


def test_acknowledgement_and_new_request_commit_together(assistant_store) -> None:
    factory, _, _ = assistant_store
    uncertain_conversation_id = uuid4()
    uncertain_request_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id=uncertain_conversation_id)
        add_completed_request(
            session,
            conversation_id=uncertain_conversation_id,
            request_id=uncertain_request_id,
            result_kind=AssistantResultKind.UNCERTAIN.value,
            had_uncertain=True,
            uncertain_at=EARLIER,
        )

    data = request_data(acknowledged_request_id=uncertain_request_id)
    result = admit(factory, data)

    assert result.kind is AdmissionKind.ACCEPTED
    with factory() as session:
        uncertain = session.get(AssistantRequest, uncertain_request_id)
        assert uncertain is not None
        assert uncertain.uncertain_acknowledged_at == NOW
        assert uncertain.uncertain_acknowledged_by_request_id == data.request_id
        assert uncertain.result_kind == AssistantResultKind.UNCERTAIN.value
        assert uncertain.uncertain_resolved_at is None
        assert session.get(AssistantRequest, data.request_id) is not None


@pytest.mark.parametrize("target_state", ["acknowledged", "resolved"])
def test_already_acknowledged_or_resolved_uncertain_satisfies_acknowledgement(
    assistant_store, target_state: str
) -> None:
    factory, _, _ = assistant_store
    target_conversation = uuid4()
    target_id = uuid4()
    acknowledged_by = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id=target_conversation)
        if target_state == "acknowledged":
            acknowledgement_conversation = add_conversation(session)
            add_completed_request(
                session,
                conversation_id=acknowledgement_conversation.id,
                request_id=acknowledged_by,
            )
            session.flush()
        add_completed_request(
            session,
            conversation_id=target_conversation,
            request_id=target_id,
            result_kind=(
                AssistantResultKind.OUTCOME.value
                if target_state == "resolved"
                else AssistantResultKind.UNCERTAIN.value
            ),
            had_uncertain=True,
            uncertain_at=EARLIER,
            acknowledged_at=(NOW if target_state == "acknowledged" else None),
            acknowledged_by=(
                acknowledged_by if target_state == "acknowledged" else None
            ),
            resolved_at=(NOW if target_state == "resolved" else None),
        )

    data = request_data(acknowledged_request_id=target_id)
    assert admit(factory, data).kind is AdmissionKind.ACCEPTED
    with factory() as session:
        target = session.get(AssistantRequest, target_id)
        assert target is not None
        if target_state == "acknowledged":
            assert target.uncertain_acknowledged_by_request_id == acknowledged_by
        else:
            assert target.uncertain_resolved_at == NOW
            assert target.uncertain_acknowledged_at is None


def test_resolved_uncertain_tombstone_can_satisfy_acknowledgement(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    target_id = uuid4()
    with factory.begin() as session:
        conversation = add_conversation(session)
        target = add_completed_request(
            session,
            conversation_id=conversation.id,
            request_id=target_id,
            result_kind=AssistantResultKind.OUTCOME.value,
            had_uncertain=True,
            uncertain_at=EARLIER,
            resolved_at=NOW,
        )
        target.fingerprint_version = None
        target.fingerprint_hash = None
        target.normalized_body_json = None
        target.result_kind = None
        target.result_json = None
        target.tombstone = True
        target.tombstoned_at = NOW

    data = request_data(acknowledged_request_id=target_id)
    assert admit(factory, data).kind is AdmissionKind.ACCEPTED
    with factory() as session:
        target = session.get(AssistantRequest, target_id)
        assert target is not None
        assert target.tombstone is True
        assert target.had_uncertain is True
        assert target.uncertain_resolved_at == NOW


def test_busy_rejection_does_not_apply_valid_acknowledgement(assistant_store) -> None:
    factory, _, _ = assistant_store
    busy_data = request_data()
    busy = admit(factory, busy_data)
    assert busy.conversation_id is not None
    uncertain_conversation = uuid4()
    target_id = uuid4()
    with factory.begin() as session:
        add_conversation(session, conversation_id=uncertain_conversation)
        add_completed_request(
            session,
            conversation_id=uncertain_conversation,
            request_id=target_id,
            result_kind=AssistantResultKind.UNCERTAIN.value,
            had_uncertain=True,
            uncertain_at=EARLIER,
        )

    rejected_data = request_data(
        conversation_id=busy.conversation_id,
        acknowledged_request_id=target_id,
    )

    result = admit(factory, rejected_data, now=NOW + timedelta(minutes=1))

    assert result.kind is AdmissionKind.CONVERSATION_BUSY
    with factory() as session:
        target = session.get(AssistantRequest, target_id)
        assert target is not None
        assert target.uncertain_acknowledged_at is None
        assert session.get(AssistantRequest, rejected_data.request_id) is None


@pytest.mark.parametrize("target_state", ["missing", "not_uncertain", "self"])
def test_invalid_acknowledgement_leaves_no_request_or_conversation(
    assistant_store, target_state: str
) -> None:
    factory, _, _ = assistant_store
    target_id = uuid4()
    if target_state == "not_uncertain":
        with factory.begin() as session:
            conversation = add_conversation(session)
            add_completed_request(
                session,
                conversation_id=conversation.id,
                request_id=target_id,
            )
    request_id = target_id if target_state == "self" else uuid4()
    data = request_data(
        request_id=request_id,
        acknowledged_request_id=target_id,
    )
    with factory() as session:
        conversations_before = row_count(session, AssistantConversation)

    result = admit(factory, data)

    assert result.kind is AdmissionKind.ACKNOWLEDGEMENT_NOT_APPLICABLE
    with factory() as session:
        assert session.get(AssistantRequest, request_id) is None
        assert row_count(session, AssistantConversation) == conversations_before


def test_other_unresolved_uncertain_rolls_back_earlier_acknowledgement(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    first_id = uuid4()
    acknowledged_id = uuid4()
    with factory.begin() as session:
        first_conversation = add_conversation(session)
        second_conversation = add_conversation(session)
        add_completed_request(
            session,
            conversation_id=first_conversation.id,
            request_id=first_id,
            result_kind=AssistantResultKind.UNCERTAIN.value,
            had_uncertain=True,
            uncertain_at=EARLIER,
        )
        add_completed_request(
            session,
            conversation_id=second_conversation.id,
            request_id=acknowledged_id,
            result_kind=AssistantResultKind.UNCERTAIN.value,
            had_uncertain=True,
            uncertain_at=NOW - timedelta(minutes=30),
        )

    data = request_data(acknowledged_request_id=acknowledged_id)
    result = admit(factory, data)

    assert result.kind is AdmissionKind.UNRESOLVED_UNCERTAIN
    assert result.blocking_request_id == first_id
    with factory() as session:
        target = session.get(AssistantRequest, acknowledged_id)
        assert target is not None
        assert target.uncertain_acknowledged_at is None
        assert session.get(AssistantRequest, data.request_id) is None


def test_oldest_uncertain_uses_timestamp_then_request_id(assistant_store) -> None:
    factory, _, _ = assistant_store
    first_id = UUID("00000000-0000-4000-8000-000000000001")
    second_id = UUID("00000000-0000-4000-8000-000000000002")
    with factory.begin() as session:
        for request_id in (second_id, first_id):
            conversation = add_conversation(session)
            add_completed_request(
                session,
                conversation_id=conversation.id,
                request_id=request_id,
                result_kind=AssistantResultKind.UNCERTAIN.value,
                had_uncertain=True,
                uncertain_at=EARLIER,
            )

    data = request_data()
    result = admit(factory, data)

    assert result.kind is AdmissionKind.UNRESOLVED_UNCERTAIN
    assert result.blocking_request_id == first_id
    with factory() as session:
        assert session.get(AssistantRequest, data.request_id) is None


@pytest.mark.parametrize(
    ("blocking_state", "expected"),
    [
        ("accepted", AdmissionKind.CONVERSATION_BUSY),
        ("unresolved_uncertain", AdmissionKind.UNRESOLVED_UNCERTAIN),
        ("resolved_uncertain", AdmissionKind.CONVERSATION_EXPIRED),
    ],
)
def test_conversation_expiration_honors_blocking_state(
    assistant_store, blocking_state: str, expected: AdmissionKind
) -> None:
    factory, _, _ = assistant_store
    conversation_id = uuid4()
    with factory.begin() as session:
        add_conversation(
            session,
            conversation_id=conversation_id,
            last_valid_input_at=NOW - timedelta(hours=24),
        )
        if blocking_state == "accepted":
            pending = request_data(conversation_id=conversation_id)
            session.add(
                AssistantRequest(
                    id=pending.request_id,
                    conversation_id=conversation_id,
                    fingerprint_version=1,
                    fingerprint_hash=request_fingerprint(pending).hash,
                    normalized_body_json=pending.model_dump(
                        mode="json", by_alias=True, exclude_none=True
                    ),
                    status="accepted",
                    result_kind=None,
                    result_json=None,
                    accepted_at=EARLIER,
                    processing_at=None,
                    completed_at=None,
                    task_reference_accepted_at=EARLIER,
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
        else:
            add_completed_request(
                session,
                conversation_id=conversation_id,
                result_kind=(
                    AssistantResultKind.OUTCOME.value
                    if blocking_state == "resolved_uncertain"
                    else AssistantResultKind.UNCERTAIN.value
                ),
                had_uncertain=True,
                uncertain_at=EARLIER,
                resolved_at=(NOW if blocking_state == "resolved_uncertain" else None),
            )

    result = admit(factory, request_data(conversation_id=conversation_id))
    assert result.kind is expected


@pytest.mark.parametrize("different_body", [False, True])
def test_concurrent_same_request_id_creates_exactly_one_row(
    assistant_store, different_body: bool
) -> None:
    factory, _, _ = assistant_store
    request_id = uuid4()
    first = request_data(request_id=request_id, text="첫 본문")
    second = request_data(
        request_id=request_id,
        text="다른 본문" if different_body else "첫 본문",
    )
    barrier = Barrier(2)

    def run(data: AssistantRequestCreate):
        barrier.wait(timeout=5)
        return admit(factory, data, new_conversation_id=uuid4()).kind

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, (first, second)))

    expected_second = (
        AdmissionKind.REQUEST_ID_CONFLICT
        if different_body
        else AdmissionKind.REPLAY
    )
    assert sorted(results) == sorted([AdmissionKind.ACCEPTED, expected_second])
    with factory() as session:
        assert row_count(session, AssistantRequest) == 1
        assert row_count(session, AssistantConversation) == 1


def test_database_lock_is_classified_and_does_not_reserve_request_id(
    assistant_store,
) -> None:
    factory, engine, settings = assistant_store
    locked_connection = engine.connect()
    transaction = locked_connection.begin()
    fast_engine = create_assistant_engine(settings, busy_timeout_ms=0)
    fast_factory = create_session_factory(fast_engine)
    data = request_data()
    try:
        with pytest.raises(AdmissionDatabaseUnavailable):
            admit(fast_factory, data)
    finally:
        transaction.rollback()
        locked_connection.close()
        fast_engine.dispose()

    with factory() as session:
        assert session.get(AssistantRequest, data.request_id) is None


def test_json_and_utc_values_round_trip_and_control_state_is_bounded(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    conversation_id = uuid4()
    state = {"step": 2, "nested": {"items": ["가", True, None]}}
    with factory.begin() as session:
        add_conversation(
            session,
            conversation_id=conversation_id,
            last_valid_input_at=NOW,
            control_state=state,
        )

    with factory() as session:
        loaded = session.get(AssistantConversation, conversation_id)
        assert loaded is not None
        assert loaded.id.version == 4
        assert loaded.created_at == NOW
        assert loaded.created_at.tzinfo is timezone.utc
        assert loaded.control_state_json == state

    with pytest.raises(StatementError) as captured:
        with factory.begin() as session:
            add_conversation(
                session,
                control_state={"payload": "가" * CONTROL_STATE_MAX_BYTES},
            )
    assert isinstance(captured.value.orig, SnapshotSerializationError)


def test_strict_snapshot_serialization_rejects_non_json_value(assistant_store) -> None:
    factory, _, _ = assistant_store
    with pytest.raises(StatementError) as captured:
        with factory.begin() as session:
            add_conversation(session, control_state={"value": float("nan")})
    assert isinstance(captured.value.orig, SnapshotSerializationError)


def test_strict_snapshot_deserialization_rejects_corrupt_stored_json(
    assistant_store,
) -> None:
    factory, engine, _ = assistant_store
    conversation_id = uuid4()
    with factory.begin() as session:
        add_conversation(
            session,
            conversation_id=conversation_id,
            control_state={"valid": True},
        )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE assistant_conversations SET control_state_json = ? WHERE id = ?",
            ('{"duplicate":1,"duplicate":2}', conversation_id.hex),
        )

    with factory() as session, pytest.raises(
        SnapshotSerializationError, match="duplicate"
    ):
        session.get(AssistantConversation, conversation_id)


def test_valid_pending_write_and_create_idempotency_round_trip_and_uniqueness(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    first = request_data()
    second = request_data()
    assert admit(factory, first).kind is AdmissionKind.ACCEPTED
    assert admit(factory, second).kind is AdmissionKind.ACCEPTED

    with factory.begin() as session:
        request = session.get(AssistantRequest, first.request_id)
        assert request is not None
        request.status = "processing"
        request.processing_at = NOW
        request.write_started = True
        request.write_started_at = NOW
        request.write_operation = "create"
        request.write_commit_mode = "idempotent_external"
        request.pending_write_version = 1
        request.pending_write_json = {"operation": "create", "title": "재고"}
        request.create_idempotency_scope = "assistant-create"
        request.create_idempotency_key = "stable-key"

    with factory() as session:
        request = session.get(AssistantRequest, first.request_id)
        assert request is not None
        assert request.pending_write_json == {
            "operation": "create",
            "title": "재고",
        }
        assert request.write_started_at == NOW

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            request = session.get(AssistantRequest, second.request_id)
            assert request is not None
            request.write_operation = "create"
            request.write_commit_mode = "idempotent_external"
            request.create_idempotency_scope = "assistant-create"
            request.create_idempotency_key = "stable-key"


def test_question_snapshot_keeps_candidate_intents_for_provider_free_followup(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    with factory.begin() as session:
        conversation = add_conversation(session)
        source = add_completed_request(
            session,
            conversation_id=conversation.id,
            result_kind=AssistantResultKind.CANDIDATE_SELECTION.value,
        )
        # Result detail exists while its active question keeps the retention
        # clock open; the clock can start when that question becomes inactive.
        source.retention_started_at = None
        session.flush()
        question = add_active_question(
            session,
            conversation_id=conversation.id,
            source_request_id=source.id,
            kind=AssistantResultKind.CANDIDATE_SELECTION.value,
            expires_at=NOW + timedelta(minutes=10),
        )
        question.candidates_json = [
            {"candidateId": "Candidate-A", "title": "재고 확인"}
        ]
        question.candidate_intents_json = {
            "Candidate-A": {
                "operation": "update",
                "changes": {"completed": True},
            }
        }
        source_id = source.id
        question_id = question.id

    with factory() as session:
        source = session.get(AssistantRequest, source_id)
        assert source is not None
        assert source.retention_started_at is None
        question = session.get(AssistantQuestion, question_id)
        assert question is not None
        assert question.candidates_json[0]["candidateId"] == "Candidate-A"
        assert question.candidate_intents_json["Candidate-A"] == {
            "operation": "update",
            "changes": {"completed": True},
        }


def test_database_invariants_reject_duplicate_busy_question_and_gate(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    conversation_id = uuid4()
    first_data = request_data(conversation_id=conversation_id)
    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            add_conversation(session, conversation_id=conversation_id)
            for data in (first_data, request_data(conversation_id=conversation_id)):
                session.add(
                    AssistantRequest(
                        id=data.request_id,
                        conversation_id=conversation_id,
                        fingerprint_version=1,
                        fingerprint_hash=request_fingerprint(data).hash,
                        normalized_body_json=data.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                        status="accepted",
                        result_kind=None,
                        result_json=None,
                        accepted_at=NOW,
                        processing_at=None,
                        completed_at=None,
                        task_reference_accepted_at=NOW,
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

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            conversation = add_conversation(session)
            first_source = add_completed_request(
                session, conversation_id=conversation.id
            )
            second_source = add_completed_request(
                session, conversation_id=conversation.id
            )
            session.flush()
            add_active_question(
                session,
                conversation_id=conversation.id,
                source_request_id=first_source.id,
            )
            add_active_question(
                session,
                conversation_id=conversation.id,
                source_request_id=second_source.id,
            )

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            session.add(
                AssistantWriteGate(
                    singleton_key=2,
                    owner_request_id=None,
                    acquired_at=None,
                    generation=0,
                )
            )


@pytest.mark.parametrize("field", ["kind", "status"])
def test_question_constraints_reject_invalid_kind_and_status(
    assistant_store, field: str
) -> None:
    factory, _, _ = assistant_store
    with factory.begin() as session:
        conversation = add_conversation(session)
        source = add_completed_request(session, conversation_id=conversation.id)
        session.flush()
        question = add_active_question(
            session,
            conversation_id=conversation.id,
            source_request_id=source.id,
        )
        question_id = question.id

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            question = session.get(AssistantQuestion, question_id)
            assert question is not None
            setattr(question, field, "invalid")


def test_write_gate_owner_requires_matching_acquired_timestamp(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    data = request_data()
    assert admit(factory, data).kind is AdmissionKind.ACCEPTED

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            gate = session.get(AssistantWriteGate, 1)
            assert gate is not None
            gate.owner_request_id = data.request_id


@pytest.mark.parametrize(
    "mutation",
    [
        {"fingerprint_hash": "A" * 64},
        {"status": "completed"},
        {"result_kind": "invalid"},
        {"had_uncertain": True, "uncertain_at": None},
        {"write_started": True, "write_started_at": NOW},
        {"write_operation": "invalid"},
        {"write_commit_mode": "invalid"},
        {"pending_write_version": 1},
        {"create_idempotency_key": "unpaired"},
        {
            "write_operation": "create",
            "write_commit_mode": "idempotent_external",
        },
        {"tombstone": True},
    ],
)
def test_request_constraints_reject_invalid_persistence_combinations(
    assistant_store, mutation: dict[str, object]
) -> None:
    factory, _, _ = assistant_store
    data = request_data()
    result = admit(factory, data)
    assert result.kind is AdmissionKind.ACCEPTED

    with pytest.raises(IntegrityError):
        with factory.begin() as session:
            request = session.get(AssistantRequest, data.request_id)
            assert request is not None
            for name, value in mutation.items():
                setattr(request, name, value)


def test_fingerprint_input_validation_rejects_bad_version_hash_and_naive_time(
    assistant_store,
) -> None:
    factory, _, _ = assistant_store
    data = request_data()
    with pytest.raises(ValueError, match="fingerprint"):
        admit(
            factory,
            data,
            fingerprint=RequestFingerprint(version=0, hash="A" * 64),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        admit(factory, data, now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="UUID v4"):
        admit(
            factory,
            data,
            new_conversation_id=UUID(
                "00000000-0000-1000-8000-000000000001"
            ),
        )
