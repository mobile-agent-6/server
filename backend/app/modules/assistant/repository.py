"""Database queries used by serialized Assistant admission and future CAS flows."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
)


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
    request: AssistantRequest,
    *,
    result_json: dict[str, object],
    at: datetime,
) -> None:
    request.status = "completed"
    request.result_kind = "outcome"
    request.result_json = result_json
    request.completed_at = at
    request.cancelled_at = at
    request.retention_started_at = at
    request.pending_write_version = None
    request.pending_write_json = None


def cancel_question(question: AssistantQuestion, *, at: datetime) -> None:
    question.status = AssistantQuestionStatus.CANCELLED.value
    question.deactivated_at = at
