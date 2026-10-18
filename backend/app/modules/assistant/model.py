"""Assistant persistence models for requests, conversations, questions, and CAS state."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, UTCDateTime
from app.modules.assistant.enums import AssistantRequestStatus, AssistantResultKind
from app.modules.assistant.serialization import CONTROL_STATE_MAX_BYTES, JsonSnapshot


class AssistantQuestionStatus(StrEnum):
    ACTIVE = "active"
    ANSWERED = "answered"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class AssistantWriteOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class AssistantWriteCommitMode(StrEnum):
    ATOMIC = "atomic"
    IDEMPOTENT_EXTERNAL = "idempotent_external"


_REQUEST_STATUSES = tuple(item.value for item in AssistantRequestStatus)
_RESULT_KINDS = tuple(item.value for item in AssistantResultKind)
_QUESTION_KINDS = (
    AssistantResultKind.NEED_MORE_INFO.value,
    AssistantResultKind.CANDIDATE_SELECTION.value,
    AssistantResultKind.CONFIRMATION.value,
)
_QUESTION_STATUSES = tuple(item.value for item in AssistantQuestionStatus)
_WRITE_OPERATIONS = tuple(item.value for item in AssistantWriteOperation)
_WRITE_COMMIT_MODES = tuple(item.value for item in AssistantWriteCommitMode)


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"
    __table_args__ = (
        sa.CheckConstraint(
            "(control_state_version IS NULL AND control_state_json IS NULL) OR "
            "(control_state_version >= 1 AND control_state_json IS NOT NULL)",
            name="control_state_pair",
        ),
        sa.Index(
            "ix_assistant_conversations_expiration",
            "expired_at",
            "last_valid_input_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_valid_input_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    expired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    control_state_version: Mapped[int | None] = mapped_column(sa.Integer())
    control_state_json: Mapped[object | None] = mapped_column(
        JsonSnapshot(max_bytes=CONTROL_STATE_MAX_BYTES)
    )


class AssistantRequest(Base):
    __tablename__ = "assistant_requests"
    __table_args__ = (
        sa.CheckConstraint(
            f"status IN ({_sql_values(_REQUEST_STATUSES)})",
            name="status",
        ),
        sa.CheckConstraint(
            "result_kind IS NULL OR "
            f"result_kind IN ({_sql_values(_RESULT_KINDS)})",
            name="result_kind",
        ),
        sa.CheckConstraint(
            "fingerprint_hash IS NULL OR "
            "(length(fingerprint_hash) = 64 AND "
            "fingerprint_hash NOT GLOB '*[^0-9a-f]*')",
            name="fingerprint_hash",
        ),
        sa.CheckConstraint(
            "tombstone = 1 OR "
            "(fingerprint_version IS NOT NULL AND fingerprint_version >= 1 AND "
            "fingerprint_hash IS NOT NULL AND normalized_body_json IS NOT NULL)",
            name="detail_or_tombstone",
        ),
        sa.CheckConstraint(
            "(status IN ('accepted', 'processing') AND completed_at IS NULL AND "
            "result_kind IS NULL AND result_json IS NULL AND tombstone = 0) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND "
            "((tombstone = 0 AND result_kind IS NOT NULL AND result_json IS NOT NULL "
            "AND tombstoned_at IS NULL) OR "
            "(tombstone = 1 AND result_kind IS NULL AND result_json IS NULL "
            "AND tombstoned_at IS NOT NULL)))",
            name="completion_state",
        ),
        sa.CheckConstraint(
            "had_uncertain = 0 OR uncertain_at IS NOT NULL",
            name="uncertain_timestamp",
        ),
        sa.CheckConstraint(
            "result_kind IS NULL OR result_kind != 'uncertain' OR "
            "(had_uncertain = 1 AND uncertain_at IS NOT NULL AND "
            "uncertain_resolved_at IS NULL)",
            name="uncertain_result_history",
        ),
        sa.CheckConstraint(
            "(uncertain_acknowledged_at IS NULL AND "
            "uncertain_acknowledged_by_request_id IS NULL) OR "
            "(uncertain_acknowledged_at IS NOT NULL AND "
            "uncertain_acknowledged_by_request_id IS NOT NULL AND had_uncertain = 1)",
            name="uncertain_acknowledgement",
        ),
        sa.CheckConstraint(
            "uncertain_resolved_at IS NULL OR had_uncertain = 1",
            name="uncertain_resolution",
        ),
        sa.CheckConstraint(
            "uncertain_resolved_at IS NULL OR tombstone = 1 OR "
            "result_kind IN ('outcome', 'failure')",
            name="uncertain_resolution_kind",
        ),
        sa.CheckConstraint(
            "(write_started = 0 AND write_started_at IS NULL) OR "
            "(write_started = 1 AND write_started_at IS NOT NULL AND "
            "write_operation IS NOT NULL AND write_commit_mode IS NOT NULL AND "
            "status IN ('processing', 'completed'))",
            name="write_marker",
        ),
        sa.CheckConstraint(
            "write_operation IS NULL OR "
            f"write_operation IN ({_sql_values(_WRITE_OPERATIONS)})",
            name="write_operation",
        ),
        sa.CheckConstraint(
            "write_commit_mode IS NULL OR "
            f"write_commit_mode IN ({_sql_values(_WRITE_COMMIT_MODES)})",
            name="write_commit_mode",
        ),
        sa.CheckConstraint(
            "(pending_write_version IS NULL AND pending_write_json IS NULL) OR "
            "(pending_write_version >= 1 AND pending_write_json IS NOT NULL)",
            name="pending_write_pair",
        ),
        sa.CheckConstraint(
            "(create_idempotency_scope IS NULL AND create_idempotency_key IS NULL) OR "
            "(create_idempotency_scope IS NOT NULL AND "
            "create_idempotency_key IS NOT NULL)",
            name="create_idempotency_pair",
        ),
        sa.CheckConstraint(
            "write_operation != 'create' OR "
            "write_commit_mode != 'idempotent_external' OR "
            "(create_idempotency_scope IS NOT NULL AND "
            "create_idempotency_key IS NOT NULL)",
            name="external_create_idempotency",
        ),
        sa.CheckConstraint(
            "tombstone = 0 OR status = 'completed'",
            name="tombstone_completed",
        ),
        sa.UniqueConstraint(
            "create_idempotency_scope",
            "create_idempotency_key",
            name="uq_assistant_requests_create_idempotency",
        ),
        sa.Index(
            "uq_assistant_requests_conversation_blocking",
            "conversation_id",
            unique=True,
            sqlite_where=sa.text("status IN ('accepted', 'processing')"),
        ),
        sa.Index(
            "ix_assistant_requests_conversation_blocking_order",
            "conversation_id",
            "accepted_at",
            "id",
            sqlite_where=sa.text("status IN ('accepted', 'processing')"),
        ),
        sa.Index(
            "ix_assistant_requests_unacknowledged_uncertain",
            "uncertain_at",
            "id",
            sqlite_where=sa.text(
                "had_uncertain = 1 AND uncertain_acknowledged_at IS NULL "
                "AND uncertain_resolved_at IS NULL"
            ),
        ),
        sa.Index(
            "ix_assistant_requests_retention",
            "tombstone",
            "retention_started_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("assistant_conversations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    fingerprint_version: Mapped[int | None] = mapped_column(sa.Integer())
    fingerprint_hash: Mapped[str | None] = mapped_column(sa.CHAR(64))
    normalized_body_json: Mapped[object | None] = mapped_column(JsonSnapshot())
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    result_kind: Mapped[str | None] = mapped_column(sa.String(32))
    result_json: Mapped[object | None] = mapped_column(JsonSnapshot())
    accepted_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    processing_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    task_reference_accepted_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False
    )
    write_started: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    write_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    write_operation: Mapped[str | None] = mapped_column(sa.String(16))
    write_commit_mode: Mapped[str | None] = mapped_column(sa.String(24))
    pending_write_version: Mapped[int | None] = mapped_column(sa.Integer())
    pending_write_json: Mapped[object | None] = mapped_column(JsonSnapshot())
    create_idempotency_key: Mapped[str | None] = mapped_column(sa.String(255))
    create_idempotency_scope: Mapped[str | None] = mapped_column(sa.String(64))
    had_uncertain: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    uncertain_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    uncertain_acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    uncertain_acknowledged_by_request_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("assistant_requests.id", ondelete="RESTRICT"),
    )
    uncertain_resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    retention_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    tombstone: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    tombstoned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cancelled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AssistantQuestion(Base):
    __tablename__ = "assistant_questions"
    __table_args__ = (
        sa.CheckConstraint(
            f"kind IN ({_sql_values(_QUESTION_KINDS)})",
            name="kind",
        ),
        sa.CheckConstraint(
            f"status IN ({_sql_values(_QUESTION_STATUSES)})",
            name="status",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND deactivated_at IS NULL AND "
            "answered_by_request_id IS NULL) OR "
            "(status = 'answered' AND deactivated_at IS NOT NULL AND "
            "answered_by_request_id IS NOT NULL) OR "
            "(status IN ('cancelled', 'expired', 'invalidated') AND "
            "deactivated_at IS NOT NULL AND answered_by_request_id IS NULL)",
            name="lifecycle",
        ),
        sa.Index(
            "uq_assistant_questions_conversation_active",
            "conversation_id",
            unique=True,
            sqlite_where=sa.text("status = 'active'"),
        ),
        sa.Index(
            "ix_assistant_questions_source_request",
            "source_request_id",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("assistant_conversations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_request_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("assistant_requests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    payload_json: Mapped[object] = mapped_column(JsonSnapshot(), nullable=False)
    candidates_json: Mapped[object | None] = mapped_column(JsonSnapshot())
    candidate_intents_json: Mapped[object | None] = mapped_column(JsonSnapshot())
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deactivated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    answered_by_request_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("assistant_requests.id", ondelete="RESTRICT"),
    )


class AssistantWriteGate(Base):
    __tablename__ = "assistant_write_gate"
    __table_args__ = (
        sa.CheckConstraint("singleton_key = 1", name="singleton"),
        sa.CheckConstraint(
            "(owner_request_id IS NULL AND acquired_at IS NULL) OR "
            "(owner_request_id IS NOT NULL AND acquired_at IS NOT NULL)",
            name="owner_pair",
        ),
    )

    singleton_key: Mapped[int] = mapped_column(sa.Integer(), primary_key=True)
    owner_request_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("assistant_requests.id", ondelete="RESTRICT"),
    )
    acquired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    generation: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, default=0, server_default="0"
    )
