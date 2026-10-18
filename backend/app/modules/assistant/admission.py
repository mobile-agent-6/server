"""Serialized Assistant request admission without HTTP or external I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import RFC_4122, UUID

from sqlalchemy.orm import Session, sessionmaker

from app.core.database import is_database_unavailable
from app.modules.assistant import repository
from app.modules.assistant.enums import AssistantResultKind
from app.modules.assistant.fingerprint import FINGERPRINT_VERSION, canonical_fingerprint
from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
)
from app.modules.assistant.schemas import (
    AssistantInputConfirmation,
    AssistantInputSelection,
    AssistantInputText,
    AssistantRequestCreate,
)

CONVERSATION_INACTIVITY_LIMIT = timedelta(hours=24)
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AdmissionKind(StrEnum):
    ACCEPTED = "accepted"
    REPLAY = "replay"
    ASSISTANT_UNAVAILABLE = "assistant_unavailable"
    REQUEST_ID_CONFLICT = "request_id_conflict"
    REQUEST_EXPIRED = "request_expired"
    CONVERSATION_NOT_FOUND = "conversation_not_found"
    CONVERSATION_EXPIRED = "conversation_expired"
    CONVERSATION_BUSY = "conversation_busy"
    STALE_QUESTION = "stale_question"
    QUESTION_EXPIRED = "question_expired"
    UNRESOLVED_UNCERTAIN = "unresolved_uncertain"
    ACKNOWLEDGEMENT_NOT_APPLICABLE = "acknowledgement_not_applicable"


@dataclass(frozen=True)
class RequestFingerprint:
    version: int
    hash: str


@dataclass(frozen=True)
class AdmissionResult:
    kind: AdmissionKind
    request_id: UUID
    conversation_id: UUID | None = None
    status: str | None = None
    result_kind: str | None = None
    result: object | None = None
    blocking_request_id: UUID | None = None
    active_question_id: UUID | None = None
    active_question_request_id: UUID | None = None


class AdmissionDatabaseUnavailable(RuntimeError):
    """The serialized admission transaction could not access SQLite."""


def request_fingerprint(
    data: AssistantRequestCreate, *, version: int = FINGERPRINT_VERSION
) -> RequestFingerprint:
    return RequestFingerprint(
        version=version,
        hash=canonical_fingerprint(data, version=version),
    )


def _validate_inputs(
    *,
    fingerprint: RequestFingerprint,
    accepted_at: datetime,
    new_conversation_id: UUID,
) -> None:
    if fingerprint.version < 1 or _LOWER_SHA256.fullmatch(fingerprint.hash) is None:
        raise ValueError("fingerprint must be a versioned lowercase SHA-256 hash")
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("accepted_at must be timezone-aware")
    if (
        new_conversation_id.version != 4
        or new_conversation_id.variant != RFC_4122
    ):
        raise ValueError("new_conversation_id must be an RFC variant UUID v4")


def _existing_result(request: AssistantRequest) -> AdmissionResult:
    if request.tombstone:
        return AdmissionResult(
            kind=AdmissionKind.REQUEST_EXPIRED,
            request_id=request.id,
            conversation_id=request.conversation_id,
        )
    return AdmissionResult(
        kind=AdmissionKind.REPLAY,
        request_id=request.id,
        conversation_id=request.conversation_id,
        status=request.status,
        result_kind=request.result_kind,
        result=request.result_json,
    )


def _conversation_is_expired(
    session: Session,
    conversation: AssistantConversation,
    *,
    now: datetime,
    blocking_request: AssistantRequest | None,
) -> bool:
    if conversation.expired_at is not None:
        return True
    if now < conversation.last_valid_input_at + CONVERSATION_INACTIVITY_LIMIT:
        return False
    if blocking_request is not None:
        return False
    return not repository.conversation_has_unresolved_uncertain(
        session, conversation.id
    )


def _question_id(data: AssistantRequestCreate) -> UUID | None:
    return data.input.question_id


def _question_accepts_input(
    question: AssistantQuestion, data: AssistantRequestCreate
) -> bool:
    expected_type = {
        AssistantResultKind.NEED_MORE_INFO.value: AssistantInputText,
        AssistantResultKind.CANDIDATE_SELECTION.value: AssistantInputSelection,
        AssistantResultKind.CONFIRMATION.value: AssistantInputConfirmation,
    }[question.kind]
    return isinstance(data.input, expected_type)


def _stale_result(
    data: AssistantRequestCreate,
    *,
    conversation_id: UUID,
    active_question: AssistantQuestion | None,
) -> AdmissionResult:
    return AdmissionResult(
        kind=AdmissionKind.STALE_QUESTION,
        request_id=data.request_id,
        conversation_id=conversation_id,
        active_question_id=(active_question.id if active_question else None),
        active_question_request_id=(
            active_question.source_request_id if active_question else None
        ),
    )


def _admit_in_transaction(
    session: Session,
    *,
    data: AssistantRequestCreate,
    fingerprint: RequestFingerprint,
    accepted_at: datetime,
    new_conversation_id: UUID,
    new_requests_enabled: bool,
) -> AdmissionResult:
    existing = repository.get_request(session, data.request_id)
    if existing is not None:
        if existing.tombstone:
            return _existing_result(existing)
        if (
            existing.fingerprint_version == fingerprint.version
            and existing.fingerprint_hash == fingerprint.hash
        ):
            return _existing_result(existing)
        return AdmissionResult(
            kind=AdmissionKind.REQUEST_ID_CONFLICT,
            request_id=data.request_id,
            conversation_id=existing.conversation_id,
        )

    if not new_requests_enabled:
        return AdmissionResult(
            kind=AdmissionKind.ASSISTANT_UNAVAILABLE,
            request_id=data.request_id,
            conversation_id=data.conversation_id,
        )

    new_conversation = data.conversation_id is None
    if new_conversation:
        conversation = AssistantConversation(
            id=new_conversation_id,
            created_at=accepted_at,
            last_valid_input_at=accepted_at,
            expired_at=None,
            control_state_version=None,
            control_state_json=None,
        )
        conversation_id = new_conversation_id
        blocking_request = None
    else:
        conversation_id = data.conversation_id
        assert conversation_id is not None
        conversation = repository.get_conversation(session, conversation_id)
        if conversation is None:
            return AdmissionResult(
                kind=AdmissionKind.CONVERSATION_NOT_FOUND,
                request_id=data.request_id,
                conversation_id=conversation_id,
            )
        blocking_request = repository.find_blocking_request(session, conversation_id)
        if _conversation_is_expired(
            session,
            conversation,
            now=accepted_at,
            blocking_request=blocking_request,
        ):
            return AdmissionResult(
                kind=AdmissionKind.CONVERSATION_EXPIRED,
                request_id=data.request_id,
                conversation_id=conversation_id,
            )
        if blocking_request is not None:
            return AdmissionResult(
                kind=AdmissionKind.CONVERSATION_BUSY,
                request_id=data.request_id,
                conversation_id=conversation_id,
                blocking_request_id=blocking_request.id,
            )

    supplied_question_id = _question_id(data)
    active_question = (
        None
        if new_conversation
        else repository.find_active_question(session, conversation_id)
    )
    expired_active_question: AssistantQuestion | None = None
    valid_active_question = active_question
    if (
        active_question is not None
        and active_question.expires_at is not None
        and active_question.expires_at <= accepted_at
    ):
        expired_active_question = active_question
        valid_active_question = None

    if (
        expired_active_question is not None
        and supplied_question_id == expired_active_question.id
    ):
        return AdmissionResult(
            kind=AdmissionKind.QUESTION_EXPIRED,
            request_id=data.request_id,
            conversation_id=conversation_id,
        )

    answered_question: AssistantQuestion | None = None
    if valid_active_question is not None:
        if (
            supplied_question_id != valid_active_question.id
            or not _question_accepts_input(valid_active_question, data)
        ):
            return _stale_result(
                data,
                conversation_id=conversation_id,
                active_question=valid_active_question,
            )
        answered_question = valid_active_question
    elif supplied_question_id is not None:
        supplied_question = repository.get_question(session, supplied_question_id)
        if (
            supplied_question is not None
            and supplied_question.conversation_id == conversation_id
            and supplied_question.status == AssistantQuestionStatus.EXPIRED.value
        ):
            return AdmissionResult(
                kind=AdmissionKind.QUESTION_EXPIRED,
                request_id=data.request_id,
                conversation_id=conversation_id,
            )
        return _stale_result(
            data,
            conversation_id=conversation_id,
            active_question=None,
        )

    acknowledgement_target: AssistantRequest | None = None
    acknowledgement_id = data.acknowledged_uncertain_request_id
    if acknowledgement_id is not None:
        if acknowledgement_id == data.request_id:
            return AdmissionResult(
                kind=AdmissionKind.ACKNOWLEDGEMENT_NOT_APPLICABLE,
                request_id=data.request_id,
                conversation_id=conversation_id,
            )
        acknowledgement_target = repository.get_request(session, acknowledgement_id)
        if acknowledgement_target is None or not acknowledgement_target.had_uncertain:
            return AdmissionResult(
                kind=AdmissionKind.ACKNOWLEDGEMENT_NOT_APPLICABLE,
                request_id=data.request_id,
                conversation_id=conversation_id,
            )

    unresolved = repository.find_oldest_unacknowledged_uncertain(
        session, excluding_request_id=acknowledgement_id
    )
    if unresolved is not None:
        return AdmissionResult(
            kind=AdmissionKind.UNRESOLVED_UNCERTAIN,
            request_id=data.request_id,
            conversation_id=conversation_id,
            blocking_request_id=unresolved.id,
        )

    if new_conversation:
        repository.add_conversation(session, conversation)

    task_reference_accepted_at = accepted_at
    if answered_question is not None:
        source_request = repository.get_request(
            session, answered_question.source_request_id
        )
        if source_request is None:
            raise RuntimeError("active question source request is missing")
        task_reference_accepted_at = source_request.task_reference_accepted_at

    request = AssistantRequest(
        id=data.request_id,
        conversation_id=conversation_id,
        fingerprint_version=fingerprint.version,
        fingerprint_hash=fingerprint.hash,
        normalized_body_json=data.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ),
        status="accepted",
        result_kind=None,
        result_json=None,
        accepted_at=accepted_at,
        processing_at=None,
        completed_at=None,
        task_reference_accepted_at=task_reference_accepted_at,
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
    repository.add_request(session, request)
    session.flush()

    if expired_active_question is not None:
        repository.expire_question(expired_active_question, at=accepted_at)
    if answered_question is not None:
        repository.answer_question(
            answered_question,
            answer_request_id=data.request_id,
            at=accepted_at,
        )
    if (
        acknowledgement_target is not None
        and acknowledgement_target.uncertain_acknowledged_at is None
        and acknowledgement_target.uncertain_resolved_at is None
    ):
        acknowledgement_target.uncertain_acknowledged_at = accepted_at
        acknowledgement_target.uncertain_acknowledged_by_request_id = data.request_id

    conversation.last_valid_input_at = accepted_at
    return AdmissionResult(
        kind=AdmissionKind.ACCEPTED,
        request_id=data.request_id,
        conversation_id=conversation_id,
        status="accepted",
    )


def admit_request(
    session_factory: sessionmaker[Session],
    *,
    data: AssistantRequestCreate,
    fingerprint: RequestFingerprint,
    accepted_at: datetime,
    new_conversation_id: UUID,
    new_requests_enabled: bool,
) -> AdmissionResult:
    """Run all admission decisions and successful mutations in one transaction."""

    _validate_inputs(
        fingerprint=fingerprint,
        accepted_at=accepted_at,
        new_conversation_id=new_conversation_id,
    )
    try:
        with session_factory() as session:
            with session.begin():
                return _admit_in_transaction(
                    session,
                    data=data,
                    fingerprint=fingerprint,
                    accepted_at=accepted_at,
                    new_conversation_id=new_conversation_id,
                    new_requests_enabled=new_requests_enabled,
                )
    except Exception as exc:
        if is_database_unavailable(exc):
            raise AdmissionDatabaseUnavailable(
                "Assistant admission database is unavailable"
            ) from exc
        raise
