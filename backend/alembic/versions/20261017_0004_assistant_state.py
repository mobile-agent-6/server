"""Create Assistant persistence and serialized admission state.

Revision ID: 20261017_0004
Revises: 20261017_0003
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_0004"
down_revision: str | None = "20261017_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREVIOUS_TABLES = {"alembic_version", "idempotency_records", "schedules"}
_ASSISTANT_TABLES = {
    "assistant_conversations",
    "assistant_requests",
    "assistant_questions",
    "assistant_write_gate",
}
_EXPECTED_SCHEDULE_COLUMNS = {
    "id",
    "title",
    "scheduled_at",
    "category",
    "completed",
    "created_at",
    "updated_at",
    "deleted_at",
}
_EXPECTED_IDEMPOTENCY_COLUMNS = {
    "scope",
    "idempotency_key",
    "fingerprint",
    "schedule_id",
    "response_body",
    "created_at",
    "expires_at",
}


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _indexes(
    inspector: sa.Inspector, table_name: str
) -> dict[str, tuple[tuple[str, ...], bool]]:
    return {
        str(index["name"]): (
            tuple(str(column) for column in index["column_names"]),
            bool(index["unique"]),
        )
        for index in inspector.get_indexes(table_name)
    }


def _validate_upgrade_source() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if any(name.startswith("_alembic_tmp_") for name in tables):
        raise RuntimeError("Assistant migration refused a leftover Alembic batch table")
    if tables != _PREVIOUS_TABLES:
        raise RuntimeError("Assistant migration refused an unexpected source schema")
    if _column_names(inspector, "schedules") != _EXPECTED_SCHEDULE_COLUMNS:
        raise RuntimeError("Assistant migration refused unexpected Schedule columns")
    if (
        _column_names(inspector, "idempotency_records")
        != _EXPECTED_IDEMPOTENCY_COLUMNS
    ):
        raise RuntimeError("Assistant migration refused unexpected idempotency columns")
    if _indexes(inspector, "schedules") != {
        "ix_schedules_active_time": (
            ("deleted_at", "scheduled_at", "created_at", "id"),
            False,
        )
    }:
        raise RuntimeError("Assistant migration refused unexpected Schedule indexes")
    if _indexes(inspector, "idempotency_records") != {
        "ix_idempotency_records_expires_at": (("expires_at",), False)
    }:
        raise RuntimeError("Assistant migration refused unexpected idempotency indexes")


def _validate_downgrade_source() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    tables = set(inspector.get_table_names())
    if any(name.startswith("_alembic_tmp_") for name in tables):
        raise RuntimeError(
            "Assistant downgrade refused a leftover Alembic batch table"
        )
    if tables != _PREVIOUS_TABLES | _ASSISTANT_TABLES:
        raise RuntimeError("Assistant downgrade refused an unexpected source schema")

    expected_columns = {
        "assistant_conversations": {
            "id",
            "created_at",
            "last_valid_input_at",
            "expired_at",
            "control_state_version",
            "control_state_json",
        },
        "assistant_requests": {
            "id",
            "conversation_id",
            "fingerprint_version",
            "fingerprint_hash",
            "normalized_body_json",
            "status",
            "result_kind",
            "result_json",
            "accepted_at",
            "processing_at",
            "completed_at",
            "task_reference_accepted_at",
            "write_started",
            "write_started_at",
            "write_operation",
            "write_commit_mode",
            "pending_write_version",
            "pending_write_json",
            "create_idempotency_key",
            "create_idempotency_scope",
            "had_uncertain",
            "uncertain_at",
            "uncertain_acknowledged_at",
            "uncertain_acknowledged_by_request_id",
            "uncertain_resolved_at",
            "retention_started_at",
            "tombstone",
            "tombstoned_at",
            "cancelled_at",
        },
        "assistant_questions": {
            "id",
            "conversation_id",
            "source_request_id",
            "kind",
            "payload_json",
            "candidates_json",
            "candidate_intents_json",
            "status",
            "created_at",
            "expires_at",
            "deactivated_at",
            "answered_by_request_id",
        },
        "assistant_write_gate": {
            "singleton_key",
            "owner_request_id",
            "acquired_at",
            "generation",
        },
    }
    if any(
        _column_names(inspector, table_name) != columns
        for table_name, columns in expected_columns.items()
    ):
        raise RuntimeError("Assistant downgrade refused unexpected Assistant columns")
    expected_indexes = {
        "assistant_conversations": {
            "ix_assistant_conversations_expiration": (
                ("expired_at", "last_valid_input_at", "id"),
                False,
            )
        },
        "assistant_requests": {
            "uq_assistant_requests_conversation_blocking": (
                ("conversation_id",),
                True,
            ),
            "ix_assistant_requests_conversation_blocking_order": (
                ("conversation_id", "accepted_at", "id"),
                False,
            ),
            "ix_assistant_requests_unacknowledged_uncertain": (
                ("uncertain_at", "id"),
                False,
            ),
            "ix_assistant_requests_retention": (
                ("tombstone", "retention_started_at", "id"),
                False,
            ),
        },
        "assistant_questions": {
            "uq_assistant_questions_conversation_active": (
                ("conversation_id",),
                True,
            ),
            "ix_assistant_questions_source_request": (
                ("source_request_id", "id"),
                False,
            ),
        },
        "assistant_write_gate": {},
    }
    if any(
        _indexes(inspector, table_name) != indexes
        for table_name, indexes in expected_indexes.items()
    ):
        raise RuntimeError("Assistant downgrade refused unexpected Assistant indexes")

    blocking = connection.execute(
        sa.text(
            "SELECT id FROM assistant_requests "
            "WHERE status IN ('accepted', 'processing') LIMIT 1"
        )
    ).first()
    if blocking is not None:
        raise RuntimeError(
            "Assistant downgrade refused accepted or processing request data"
        )
    unresolved = connection.execute(
        sa.text(
            "SELECT id FROM assistant_requests "
            "WHERE had_uncertain = 1 AND uncertain_resolved_at IS NULL LIMIT 1"
        )
    ).first()
    if unresolved is not None:
        raise RuntimeError("Assistant downgrade refused unresolved uncertain data")


def upgrade() -> None:
    _validate_upgrade_source()

    op.create_table(
        "assistant_conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_valid_input_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("control_state_version", sa.Integer(), nullable=True),
        sa.Column("control_state_json", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "(control_state_version IS NULL AND control_state_json IS NULL) OR "
            "(control_state_version >= 1 AND control_state_json IS NOT NULL)",
            name=op.f("ck_assistant_conversations_control_state_pair"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_conversations")),
    )
    op.create_index(
        "ix_assistant_conversations_expiration",
        "assistant_conversations",
        ["expired_at", "last_valid_input_at", "id"],
        unique=False,
    )

    op.create_table(
        "assistant_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint_version", sa.Integer(), nullable=True),
        sa.Column("fingerprint_hash", sa.CHAR(length=64), nullable=True),
        sa.Column("normalized_body_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result_kind", sa.String(length=32), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "task_reference_accepted_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "write_started", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("write_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("write_operation", sa.String(length=16), nullable=True),
        sa.Column("write_commit_mode", sa.String(length=24), nullable=True),
        sa.Column("pending_write_version", sa.Integer(), nullable=True),
        sa.Column("pending_write_json", sa.Text(), nullable=True),
        sa.Column("create_idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("create_idempotency_scope", sa.String(length=64), nullable=True),
        sa.Column(
            "had_uncertain", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("uncertain_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "uncertain_acknowledged_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("uncertain_acknowledged_by_request_id", sa.Uuid(), nullable=True),
        sa.Column("uncertain_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tombstone", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('accepted', 'processing', 'completed')",
            name=op.f("ck_assistant_requests_status"),
        ),
        sa.CheckConstraint(
            "result_kind IS NULL OR result_kind IN "
            "('need_more_info', 'candidate_selection', 'confirmation', "
            "'outcome', 'failure', 'uncertain')",
            name=op.f("ck_assistant_requests_result_kind"),
        ),
        sa.CheckConstraint(
            "fingerprint_hash IS NULL OR "
            "(length(fingerprint_hash) = 64 AND "
            "fingerprint_hash NOT GLOB '*[^0-9a-f]*')",
            name=op.f("ck_assistant_requests_fingerprint_hash"),
        ),
        sa.CheckConstraint(
            "tombstone = 1 OR "
            "(fingerprint_version IS NOT NULL AND fingerprint_version >= 1 AND "
            "fingerprint_hash IS NOT NULL AND normalized_body_json IS NOT NULL)",
            name=op.f("ck_assistant_requests_detail_or_tombstone"),
        ),
        sa.CheckConstraint(
            "(status IN ('accepted', 'processing') AND completed_at IS NULL AND "
            "result_kind IS NULL AND result_json IS NULL AND tombstone = 0) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND "
            "((tombstone = 0 AND result_kind IS NOT NULL AND result_json IS NOT NULL "
            "AND tombstoned_at IS NULL) OR "
            "(tombstone = 1 AND result_kind IS NULL AND result_json IS NULL "
            "AND tombstoned_at IS NOT NULL)))",
            name=op.f("ck_assistant_requests_completion_state"),
        ),
        sa.CheckConstraint(
            "had_uncertain = 0 OR uncertain_at IS NOT NULL",
            name=op.f("ck_assistant_requests_uncertain_timestamp"),
        ),
        sa.CheckConstraint(
            "result_kind IS NULL OR result_kind != 'uncertain' OR "
            "(had_uncertain = 1 AND uncertain_at IS NOT NULL AND "
            "uncertain_resolved_at IS NULL)",
            name=op.f("ck_assistant_requests_uncertain_result_history"),
        ),
        sa.CheckConstraint(
            "(uncertain_acknowledged_at IS NULL AND "
            "uncertain_acknowledged_by_request_id IS NULL) OR "
            "(uncertain_acknowledged_at IS NOT NULL AND "
            "uncertain_acknowledged_by_request_id IS NOT NULL AND had_uncertain = 1)",
            name=op.f("ck_assistant_requests_uncertain_acknowledgement"),
        ),
        sa.CheckConstraint(
            "uncertain_resolved_at IS NULL OR had_uncertain = 1",
            name=op.f("ck_assistant_requests_uncertain_resolution"),
        ),
        sa.CheckConstraint(
            "uncertain_resolved_at IS NULL OR tombstone = 1 OR "
            "result_kind IN ('outcome', 'failure')",
            name=op.f("ck_assistant_requests_uncertain_resolution_kind"),
        ),
        sa.CheckConstraint(
            "(write_started = 0 AND write_started_at IS NULL) OR "
            "(write_started = 1 AND write_started_at IS NOT NULL AND "
            "write_operation IS NOT NULL AND write_commit_mode IS NOT NULL AND "
            "status IN ('processing', 'completed'))",
            name=op.f("ck_assistant_requests_write_marker"),
        ),
        sa.CheckConstraint(
            "write_operation IS NULL OR write_operation IN ('create', 'update', 'delete')",
            name=op.f("ck_assistant_requests_write_operation"),
        ),
        sa.CheckConstraint(
            "write_commit_mode IS NULL OR "
            "write_commit_mode IN ('atomic', 'idempotent_external')",
            name=op.f("ck_assistant_requests_write_commit_mode"),
        ),
        sa.CheckConstraint(
            "(pending_write_version IS NULL AND pending_write_json IS NULL) OR "
            "(pending_write_version >= 1 AND pending_write_json IS NOT NULL)",
            name=op.f("ck_assistant_requests_pending_write_pair"),
        ),
        sa.CheckConstraint(
            "(create_idempotency_scope IS NULL AND create_idempotency_key IS NULL) OR "
            "(create_idempotency_scope IS NOT NULL AND "
            "create_idempotency_key IS NOT NULL)",
            name=op.f("ck_assistant_requests_create_idempotency_pair"),
        ),
        sa.CheckConstraint(
            "write_operation != 'create' OR "
            "write_commit_mode != 'idempotent_external' OR "
            "(create_idempotency_scope IS NOT NULL AND "
            "create_idempotency_key IS NOT NULL)",
            name=op.f("ck_assistant_requests_external_create_idempotency"),
        ),
        sa.CheckConstraint(
            "tombstone = 0 OR status = 'completed'",
            name=op.f("ck_assistant_requests_tombstone_completed"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["assistant_conversations.id"],
            name=op.f(
                "fk_assistant_requests_conversation_id_assistant_conversations"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uncertain_acknowledged_by_request_id"],
            ["assistant_requests.id"],
            name=op.f(
                "fk_assistant_requests_uncertain_acknowledged_by_request_id_assistant_requests"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_requests")),
        sa.UniqueConstraint(
            "create_idempotency_scope",
            "create_idempotency_key",
            name="uq_assistant_requests_create_idempotency",
        ),
    )
    op.create_index(
        "uq_assistant_requests_conversation_blocking",
        "assistant_requests",
        ["conversation_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('accepted', 'processing')"),
    )
    op.create_index(
        "ix_assistant_requests_conversation_blocking_order",
        "assistant_requests",
        ["conversation_id", "accepted_at", "id"],
        unique=False,
        sqlite_where=sa.text("status IN ('accepted', 'processing')"),
    )
    op.create_index(
        "ix_assistant_requests_unacknowledged_uncertain",
        "assistant_requests",
        ["uncertain_at", "id"],
        unique=False,
        sqlite_where=sa.text(
            "had_uncertain = 1 AND uncertain_acknowledged_at IS NULL "
            "AND uncertain_resolved_at IS NULL"
        ),
    )
    op.create_index(
        "ix_assistant_requests_retention",
        "assistant_requests",
        ["tombstone", "retention_started_at", "id"],
        unique=False,
    )

    op.create_table(
        "assistant_questions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("source_request_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("candidates_json", sa.Text(), nullable=True),
        sa.Column("candidate_intents_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_by_request_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('need_more_info', 'candidate_selection', 'confirmation')",
            name=op.f("ck_assistant_questions_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'answered', 'cancelled', 'expired', 'invalidated')",
            name=op.f("ck_assistant_questions_status"),
        ),
        sa.CheckConstraint(
            "(status = 'active' AND deactivated_at IS NULL AND "
            "answered_by_request_id IS NULL) OR "
            "(status = 'answered' AND deactivated_at IS NOT NULL AND "
            "answered_by_request_id IS NOT NULL) OR "
            "(status IN ('cancelled', 'expired', 'invalidated') AND "
            "deactivated_at IS NOT NULL AND answered_by_request_id IS NULL)",
            name=op.f("ck_assistant_questions_lifecycle"),
        ),
        sa.ForeignKeyConstraint(
            ["answered_by_request_id"],
            ["assistant_requests.id"],
            name=op.f(
                "fk_assistant_questions_answered_by_request_id_assistant_requests"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["assistant_conversations.id"],
            name=op.f(
                "fk_assistant_questions_conversation_id_assistant_conversations"
            ),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_request_id"],
            ["assistant_requests.id"],
            name=op.f("fk_assistant_questions_source_request_id_assistant_requests"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assistant_questions")),
    )
    op.create_index(
        "uq_assistant_questions_conversation_active",
        "assistant_questions",
        ["conversation_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_assistant_questions_source_request",
        "assistant_questions",
        ["source_request_id", "id"],
        unique=False,
    )

    op.create_table(
        "assistant_write_gate",
        sa.Column("singleton_key", sa.Integer(), nullable=False),
        sa.Column("owner_request_id", sa.Uuid(), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "singleton_key = 1",
            name=op.f("ck_assistant_write_gate_singleton"),
        ),
        sa.CheckConstraint(
            "(owner_request_id IS NULL AND acquired_at IS NULL) OR "
            "(owner_request_id IS NOT NULL AND acquired_at IS NOT NULL)",
            name=op.f("ck_assistant_write_gate_owner_pair"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_request_id"],
            ["assistant_requests.id"],
            name=op.f(
                "fk_assistant_write_gate_owner_request_id_assistant_requests"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "singleton_key", name=op.f("pk_assistant_write_gate")
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO assistant_write_gate "
            "(singleton_key, owner_request_id, acquired_at, generation) "
            "VALUES (1, NULL, NULL, 0)"
        )
    )


def downgrade() -> None:
    _validate_downgrade_source()

    op.drop_index(
        "ix_assistant_questions_source_request", table_name="assistant_questions"
    )
    op.drop_index(
        "uq_assistant_questions_conversation_active",
        table_name="assistant_questions",
    )
    op.drop_index(
        "ix_assistant_requests_retention", table_name="assistant_requests"
    )
    op.drop_index(
        "ix_assistant_requests_unacknowledged_uncertain",
        table_name="assistant_requests",
    )
    op.drop_index(
        "ix_assistant_requests_conversation_blocking_order",
        table_name="assistant_requests",
    )
    op.drop_index(
        "uq_assistant_requests_conversation_blocking",
        table_name="assistant_requests",
    )
    op.drop_index(
        "ix_assistant_conversations_expiration",
        table_name="assistant_conversations",
    )
    op.drop_table("assistant_questions")
    op.drop_table("assistant_write_gate")
    op.drop_table("assistant_requests")
    op.drop_table("assistant_conversations")
