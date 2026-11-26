"""Bounded lock-owner cleanup of retained Assistant details."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from app.modules.assistant.model import AssistantQuestion, AssistantRequest
from app.modules.schedules.assistant_write import ASSISTANT_IDEMPOTENCY_SCOPE
from app.modules.schedules.model import IdempotencyRecord

DETAIL_RETENTION = timedelta(hours=24)
CLEANUP_BATCH_SIZE = 100
CLEANUP_INTERVAL_SECONDS = 3600.0
MINIMAL_QUESTION_PAYLOAD: dict[str, object] = {}
_QUESTION_RESULT_KINDS = ("need_more_info", "candidate_selection", "confirmation")


class WallClock(Protocol):
    def now(self) -> datetime: ...


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cleanup clock must return timezone-aware datetimes")
    return value.astimezone(timezone.utc)


def _eligible_result(cutoff: datetime):
    inactive_question = sa.exists(
        sa.select(sa.literal(1)).where(
            AssistantQuestion.source_request_id == AssistantRequest.id,
            AssistantQuestion.status != "active",
            AssistantQuestion.deactivated_at.is_not(None),
            AssistantQuestion.deactivated_at <= cutoff,
        )
    )
    return sa.or_(
        AssistantRequest.result_kind.in_(("outcome", "failure")),
        sa.and_(
            AssistantRequest.result_kind.in_(_QUESTION_RESULT_KINDS),
            inactive_question,
        ),
    )


def cleanup_once(
    session_factory: sessionmaker[Session],
    *,
    wall_clock: WallClock,
    batch_size: int = CLEANUP_BATCH_SIZE,
    retention: timedelta = DETAIL_RETENTION,
) -> int:
    """Tombstone at most one deterministic batch in one transaction."""

    if batch_size < 1:
        raise ValueError("cleanup batch size must be positive")
    now = _utc(wall_clock.now())
    cutoff = now - retention
    cleaned = 0
    with session_factory() as session:
        with session.begin():
            requests = list(
                session.scalars(
                    sa.select(AssistantRequest)
                    .where(
                        AssistantRequest.status == "completed",
                        AssistantRequest.tombstone.is_(False),
                        AssistantRequest.retention_started_at.is_not(None),
                        AssistantRequest.retention_started_at <= cutoff,
                        _eligible_result(cutoff),
                        ~sa.exists(
                            sa.select(sa.literal(1)).where(
                                AssistantQuestion.source_request_id
                                == AssistantRequest.id,
                                AssistantQuestion.status == "active",
                            )
                        ),
                    )
                    .order_by(
                        AssistantRequest.retention_started_at.asc(),
                        AssistantRequest.id.asc(),
                    )
                    .limit(batch_size)
                )
            )
            for request in requests:
                scope = request.create_idempotency_scope
                key = request.create_idempotency_key
                if scope == ASSISTANT_IDEMPOTENCY_SCOPE and key is not None:
                    session.execute(
                        sa.delete(IdempotencyRecord).where(
                            IdempotencyRecord.scope == scope,
                            IdempotencyRecord.idempotency_key == key,
                        )
                    )
                session.execute(
                    sa.update(AssistantQuestion)
                    .where(
                        AssistantQuestion.source_request_id == request.id,
                        AssistantQuestion.status != "active",
                    )
                    .values(
                        payload_json=MINIMAL_QUESTION_PAYLOAD,
                        candidates_json=None,
                        candidate_intents_json=None,
                    )
                )
                result = session.execute(
                    sa.update(AssistantRequest)
                    .where(
                        AssistantRequest.id == request.id,
                        AssistantRequest.status == "completed",
                        AssistantRequest.tombstone.is_(False),
                        AssistantRequest.retention_started_at <= cutoff,
                        _eligible_result(cutoff),
                        ~sa.exists(
                            sa.select(sa.literal(1)).where(
                                AssistantQuestion.source_request_id
                                == AssistantRequest.id,
                                AssistantQuestion.status == "active",
                            )
                        ),
                    )
                    .values(
                        tombstone=True,
                        tombstoned_at=now,
                        result_kind=None,
                        result_json=None,
                        fingerprint_version=None,
                        fingerprint_hash=None,
                        normalized_body_json=None,
                        pending_write_version=None,
                        pending_write_json=None,
                        create_idempotency_scope=None,
                        create_idempotency_key=None,
                        write_started=False,
                        write_started_at=None,
                        write_operation=None,
                        write_commit_mode=None,
                        processing_at=None,
                        cancelled_at=None,
                    )
                )
                if int(result.rowcount or 0) != 1:
                    raise RuntimeError("Assistant cleanup tombstone CAS lost")
                cleaned += 1
    return cleaned
