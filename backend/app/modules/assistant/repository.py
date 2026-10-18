"""Database queries used by serialized Assistant admission and future CAS flows."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
    AssistantWriteGate,
)


class PrewriteCompletionResult(StrEnum):
    COMPLETED = "completed"
    NOOP = "noop"
    WRITE_STARTED = "write_started"


def get_request(session: Session, request_id: UUID) -> AssistantRequest | None:
    return session.get(AssistantRequest, request_id)


def get_conversation(
    session: Session, conversation_id: UUID
) -> AssistantConversation | None:
    return session.get(AssistantConversation, conversation_id)


def find_blocking_request(
    session: Session, conversation_id: UUID
) -> AssistantRequest | None:
    return session.scalar(
        sa.select(AssistantRequest)
        .where(
            AssistantRequest.conversation_id == conversation_id,
            AssistantRequest.status.in_(("accepted", "processing")),
        )
        .order_by(AssistantRequest.accepted_at.asc(), AssistantRequest.id.asc())
        .limit(1)
    )


def find_active_question(
    session: Session, conversation_id: UUID
) -> AssistantQuestion | None:
    return session.scalar(
        sa.select(AssistantQuestion)
        .where(
            AssistantQuestion.conversation_id == conversation_id,
            AssistantQuestion.status == AssistantQuestionStatus.ACTIVE.value,
        )
        .limit(1)
    )


def get_question(session: Session, question_id: UUID) -> AssistantQuestion | None:
    return session.get(AssistantQuestion, question_id)


def get_write_gate(session: Session) -> AssistantWriteGate:
    gate = session.get(AssistantWriteGate, 1)
    if gate is None:
        raise RuntimeError("Assistant write gate singleton is missing")
    return gate


def find_answered_question(
    session: Session, answer_request_id: UUID
) -> AssistantQuestion | None:
    return session.scalar(
        sa.select(AssistantQuestion)
        .where(
            AssistantQuestion.answered_by_request_id == answer_request_id,
            AssistantQuestion.status == AssistantQuestionStatus.ANSWERED.value,
        )
        .limit(1)
    )


def claim_accepted_request(
    session: Session, *, request_id: UUID, processing_at: datetime
) -> bool:
    result = session.execute(
        sa.update(AssistantRequest)
        .where(
            AssistantRequest.id == request_id,
            AssistantRequest.status == "accepted",
        )
        .values(status="processing", processing_at=processing_at)
    )
    return int(result.rowcount or 0) == 1


def has_active_write_marker(session: Session) -> bool:
    return (
        session.scalar(
            sa.select(sa.literal(True))
            .where(
                AssistantRequest.status.in_(("accepted", "processing")),
                AssistantRequest.write_started.is_(True),
            )
            .limit(1)
        )
        is True
    )


def recover_prewrite_requests(
    session: Session, *, result_json: dict[str, object], at: datetime
) -> int:
    result = session.execute(
        sa.update(AssistantRequest)
        .where(
            AssistantRequest.status.in_(("accepted", "processing")),
            AssistantRequest.write_started.is_(False),
        )
        .values(
            status="completed",
            result_kind="failure",
            result_json=result_json,
            completed_at=at,
            retention_started_at=at,
            pending_write_version=None,
            pending_write_json=None,
        )
    )
    return int(result.rowcount or 0)


def complete_prewrite_request(
    session: Session,
    *,
    request_id: UUID,
    result_json: dict[str, object],
    at: datetime,
) -> PrewriteCompletionResult:
    result = session.execute(
        sa.update(AssistantRequest)
        .where(
            AssistantRequest.id == request_id,
            AssistantRequest.status.in_(("accepted", "processing")),
            AssistantRequest.write_started.is_(False),
        )
        .values(
            status="completed",
            result_kind="failure",
            result_json=result_json,
            completed_at=at,
            retention_started_at=at,
            pending_write_version=None,
            pending_write_json=None,
        )
    )
    if int(result.rowcount or 0) == 1:
        return PrewriteCompletionResult.COMPLETED
    marker = session.scalar(
        sa.select(AssistantRequest.write_started).where(
            AssistantRequest.id == request_id,
            AssistantRequest.status.in_(("accepted", "processing")),
        )
    )
    if marker is True:
        return PrewriteCompletionResult.WRITE_STARTED
    return PrewriteCompletionResult.NOOP


def add_question(session: Session, question: AssistantQuestion) -> None:
    session.add(question)


def find_oldest_unacknowledged_uncertain(
    session: Session, *, excluding_request_id: UUID | None = None
) -> AssistantRequest | None:
    statement = (
        sa.select(AssistantRequest)
        .where(
            AssistantRequest.had_uncertain.is_(True),
            AssistantRequest.uncertain_acknowledged_at.is_(None),
            AssistantRequest.uncertain_resolved_at.is_(None),
        )
        .order_by(AssistantRequest.uncertain_at.asc(), AssistantRequest.id.asc())
        .limit(1)
    )
    if excluding_request_id is not None:
        statement = statement.where(AssistantRequest.id != excluding_request_id)
    return session.scalar(statement)


def conversation_has_unresolved_uncertain(
    session: Session, conversation_id: UUID
) -> bool:
    return (
        session.scalar(
            sa.select(sa.literal(True))
            .where(
                AssistantRequest.conversation_id == conversation_id,
                AssistantRequest.had_uncertain.is_(True),
                AssistantRequest.uncertain_resolved_at.is_(None),
            )
            .limit(1)
        )
        is True
    )


def add_conversation(session: Session, conversation: AssistantConversation) -> None:
    session.add(conversation)


def add_request(session: Session, request: AssistantRequest) -> None:
    session.add(request)


def expire_question(question: AssistantQuestion, *, at: datetime) -> None:
    question.status = AssistantQuestionStatus.EXPIRED.value
    question.deactivated_at = at


def answer_question(
    question: AssistantQuestion, *, answer_request_id: UUID, at: datetime
) -> None:
    question.status = AssistantQuestionStatus.ANSWERED.value
    question.deactivated_at = at
    question.answered_by_request_id = answer_request_id


def complete_request_as_cancelled(
    session: Session,
    *,
    request_id: UUID,
    result_json: dict[str, object],
    at: datetime,
) -> bool:
    result = session.execute(
        sa.update(AssistantRequest)
        .where(
            AssistantRequest.id == request_id,
            AssistantRequest.status.in_(("accepted", "processing")),
            AssistantRequest.write_started.is_(False),
        )
        .values(
            status="completed",
            result_kind="outcome",
            result_json=result_json,
            completed_at=at,
            cancelled_at=at,
            retention_started_at=at,
            pending_write_version=None,
            pending_write_json=None,
        )
    )
    return int(result.rowcount or 0) == 1


def start_question_source_retention(
    session: Session, question: AssistantQuestion, *, at: datetime
) -> None:
    """Start retention exactly when an active question becomes inactive."""

    session.execute(
        sa.update(AssistantRequest)
        .where(
            AssistantRequest.id == question.source_request_id,
            AssistantRequest.status == "completed",
            AssistantRequest.retention_started_at.is_(None),
        )
        .values(retention_started_at=at)
    )


def release_write_gate(session: Session, *, owner_request_id: UUID) -> bool:
    """Release only the gate generation still owned by the given request."""

    result = session.execute(
        sa.update(AssistantWriteGate)
        .where(
            AssistantWriteGate.singleton_key == 1,
            AssistantWriteGate.owner_request_id == owner_request_id,
        )
        .values(owner_request_id=None, acquired_at=None)
    )
    return int(result.rowcount or 0) == 1


def cancel_question(question: AssistantQuestion, *, at: datetime) -> None:
    question.status = AssistantQuestionStatus.CANCELLED.value
    question.deactivated_at = at
