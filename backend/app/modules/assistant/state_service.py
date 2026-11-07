"""Persistent Assistant admission, status, and cancellation services."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import is_database_unavailable
from app.modules.assistant import repository
from app.modules.assistant.admission import (
    AdmissionDatabaseUnavailable,
    AdmissionKind,
    AdmissionResult,
    admit_request,
    request_fingerprint,
)
from app.modules.assistant.enums import (
    AssistantCancelTargetState,
    AssistantCancelTargetType,
    AssistantOutcome,
    AssistantRequestStatus,
    AssistantResultKind,
)
from app.modules.assistant.errors import (
    AssistantAcknowledgementNotApplicable,
    AssistantConversationBusy,
    AssistantConversationExpired,
    AssistantConversationNotFound,
    AssistantDatabaseUnavailable,
    AssistantQuestionExpired,
    AssistantRequestExpiredForGet,
    AssistantRequestExpiredForPost,
    AssistantRequestIdConflict,
    AssistantRequestNotFound,
    AssistantStaleQuestion,
    AssistantTargetNotFound,
    AssistantUnavailable,
    AssistantUnresolvedUncertain,
)
from app.modules.assistant.model import (
    AssistantQuestionStatus,
    AssistantRequest,
)
from app.modules.assistant.readiness import is_assistant_schema_unavailable
from app.modules.assistant.schemas import (
    AssistantCancelResponse,
    AssistantCancelTarget,
    AssistantRequestAcceptedResponse,
    AssistantRequestCreate,
    AssistantRequestStatusResponse,
    AssistantResultOutcome,
)
from app.modules.assistant.serialization import SnapshotSerializationError

CANCELLED_RESULT_TEXT = "요청을 취소했습니다."
CANCEL_RESPONSE_TEXT = {
    AssistantCancelTargetState.CANCELLED: "대상을 취소했습니다.",
    AssistantCancelTargetState.NOT_GUARANTEED: (
        "취소 여부를 보장할 수 없습니다. 원래 요청을 확인해 주세요."
    ),
    AssistantCancelTargetState.ALREADY_COMPLETED: (
        "요청이 이미 완료되었습니다."
    ),
    AssistantCancelTargetState.UNCERTAIN: (
        "요청 결과가 아직 불확실합니다."
    ),
    AssistantCancelTargetState.NOT_ACTIVE: (
        "질문이 더 이상 활성 상태가 아닙니다."
    ),
}


class AssistantStateCorrupted(RuntimeError):
    """Stored Assistant state cannot be safely exposed through the public DTO."""


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for related in (
            current.__cause__,
            current.__context__,
            getattr(current, "orig", None),
        ):
            if isinstance(related, BaseException):
                pending.append(related)


def _exception_chain_contains(exc: BaseException, kind: type[BaseException]) -> bool:
    return any(isinstance(item, kind) for item in _exception_chain(exc))


def _raise_storage_error(
    exc: BaseException, *, assistant_request_id: UUID | None = None
) -> None:
    chain = tuple(_exception_chain(exc))
    if any(is_database_unavailable(item) for item in chain) or any(
        isinstance(item, AdmissionDatabaseUnavailable) for item in chain
    ):
        raise AssistantDatabaseUnavailable(
            assistant_request_id=assistant_request_id
        ) from None
    if any(is_assistant_schema_unavailable(item) for item in chain):
        raise AssistantUnavailable(
            assistant_request_id=assistant_request_id
        ) from None
    if _exception_chain_contains(exc, SnapshotSerializationError):
        raise AssistantStateCorrupted(
            "Stored Assistant state failed validation."
        ) from None
    raise exc


def _status_response(
    *,
    request_id: UUID,
    conversation_id: UUID | None,
    status: str | None,
    result_kind: str | None,
    result: object | None,
) -> AssistantRequestStatusResponse:
    if conversation_id is None or status is None:
        raise AssistantStateCorrupted(
            "Stored Assistant request identity is incomplete."
        )
    if status == AssistantRequestStatus.COMPLETED.value:
        if not isinstance(result, dict) or result.get("kind") != result_kind:
            raise AssistantStateCorrupted(
                "Stored Assistant result does not match its state."
            )
    elif result_kind is not None or result is not None:
        raise AssistantStateCorrupted(
            "Stored incomplete Assistant request contains a result."
        )

    payload: dict[str, object] = {
        "requestId": request_id,
        "conversationId": conversation_id,
        "status": status,
    }
    if result is not None:
        payload["result"] = result
    try:
        return AssistantRequestStatusResponse.model_validate(payload)
    except ValidationError:
        raise AssistantStateCorrupted(
            "Stored Assistant state failed public response validation."
        ) from None


def _admission_error(result: AdmissionResult) -> None:
    common = {"assistant_request_id": result.request_id}
    if result.kind is AdmissionKind.ASSISTANT_UNAVAILABLE:
        raise AssistantUnavailable(**common)
    if result.kind is AdmissionKind.REQUEST_ID_CONFLICT:
        raise AssistantRequestIdConflict(**common)
    if result.kind is AdmissionKind.REQUEST_EXPIRED:
        raise AssistantRequestExpiredForPost(**common)
    if result.kind is AdmissionKind.CONVERSATION_NOT_FOUND:
        raise AssistantConversationNotFound(**common)
    if result.kind is AdmissionKind.CONVERSATION_EXPIRED:
        raise AssistantConversationExpired(**common)
    if result.kind is AdmissionKind.CONVERSATION_BUSY:
        raise AssistantConversationBusy(
            **common, blocking_request_id=result.blocking_request_id
        )
    if result.kind is AdmissionKind.STALE_QUESTION:
        raise AssistantStaleQuestion(
            **common,
            active_question_id=result.active_question_id,
            active_question_request_id=result.active_question_request_id,
        )
    if result.kind is AdmissionKind.QUESTION_EXPIRED:
        raise AssistantQuestionExpired(**common)
    if result.kind is AdmissionKind.UNRESOLVED_UNCERTAIN:
        raise AssistantUnresolvedUncertain(
            **common, blocking_request_id=result.blocking_request_id
        )
    if result.kind is AdmissionKind.ACKNOWLEDGEMENT_NOT_APPLICABLE:
        raise AssistantAcknowledgementNotApplicable(**common)
    raise AssistantStateCorrupted("Unknown Assistant admission result.")


def admit_assistant_request(
    session_factory: sessionmaker[Session],
    *,
    data: AssistantRequestCreate,
    accepted_at: datetime,
    new_conversation_id: UUID,
    runtime_ready: bool,
    provider_available: bool,
) -> AssistantRequestAcceptedResponse | AssistantRequestStatusResponse:
    """Admit validated input and map every persistent result to the public contract."""

    try:
        result = admit_request(
            session_factory,
            data=data,
            fingerprint=request_fingerprint(data),
            accepted_at=accepted_at,
            new_conversation_id=new_conversation_id,
            runtime_ready=runtime_ready,
            provider_available=provider_available,
        )
    except Exception as exc:
        _raise_storage_error(exc, assistant_request_id=data.request_id)
        raise AssertionError("unreachable")

    if result.kind is AdmissionKind.ACCEPTED:
        if result.conversation_id is None:
            raise AssistantStateCorrupted(
                "Accepted Assistant request has no conversation."
            )
        return AssistantRequestAcceptedResponse(
            request_id=result.request_id,
            conversation_id=result.conversation_id,
            status=AssistantRequestStatus.ACCEPTED,
        )
    if result.kind is AdmissionKind.REPLAY:
        return _status_response(
            request_id=result.request_id,
            conversation_id=result.conversation_id,
            status=result.status,
            result_kind=result.result_kind,
            result=result.result,
        )
    _admission_error(result)
    raise AssertionError("unreachable")


def get_assistant_request(
    session_factory: sessionmaker[Session], *, request_id: UUID
) -> AssistantRequestStatusResponse:
    """Read one request snapshot without performing any write or execution work."""

    try:
        with session_factory() as session:
            stored = repository.get_request(session, request_id)
            if stored is None:
                raise AssistantRequestNotFound(assistant_request_id=request_id)
            if stored.tombstone:
                raise AssistantRequestExpiredForGet(
                    assistant_request_id=request_id
                )
            return _status_response(
                request_id=stored.id,
                conversation_id=stored.conversation_id,
                status=stored.status,
                result_kind=stored.result_kind,
                result=stored.result_json,
            )
    except (AssistantRequestNotFound, AssistantRequestExpiredForGet):
        raise
    except Exception as exc:
        _raise_storage_error(exc, assistant_request_id=request_id)
        raise AssertionError("unreachable")


def _request_cancel_state(
    session: Session, request: AssistantRequest, *, now: datetime
) -> AssistantCancelTargetState:
    if request.tombstone:
        return AssistantCancelTargetState.ALREADY_COMPLETED
    if request.status == AssistantRequestStatus.COMPLETED.value:
        if request.result_kind == AssistantResultKind.UNCERTAIN.value:
            return AssistantCancelTargetState.UNCERTAIN
        return AssistantCancelTargetState.ALREADY_COMPLETED
    if request.status not in {
        AssistantRequestStatus.ACCEPTED.value,
        AssistantRequestStatus.PROCESSING.value,
    }:
        raise AssistantStateCorrupted("Stored Assistant request status is invalid.")
    if request.write_started:
        return AssistantCancelTargetState.NOT_GUARANTEED

    result = AssistantResultOutcome(
        kind=AssistantResultKind.OUTCOME,
        text=CANCELLED_RESULT_TEXT,
        outcome=AssistantOutcome.CANCELLED,
        schedules_changed=False,
    )
    cancelled = repository.complete_request_as_cancelled(
        session,
        request_id=request.id,
        result_json=result.model_dump(
            mode="json", by_alias=True, exclude_none=True
        ),
        at=now,
    )
    if not cancelled:
        raise AssistantStateCorrupted("Assistant cancellation CAS lost.")
    return AssistantCancelTargetState.CANCELLED


def cancel_assistant_target(
    session_factory: sessionmaker[Session],
    *,
    conversation_id: UUID,
    target: AssistantCancelTarget,
    now: datetime,
) -> AssistantCancelResponse:
    """Judge and mutate one cancellation target in a serialized transaction."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    try:
        with session_factory() as session:
            with session.begin():
                conversation = repository.get_conversation(session, conversation_id)
                if conversation is None:
                    raise AssistantConversationNotFound()

                if target.type is AssistantCancelTargetType.REQUEST:
                    stored_request = repository.get_request(session, target.id)
                    if (
                        stored_request is None
                        or stored_request.conversation_id != conversation_id
                    ):
                        raise AssistantTargetNotFound()
                    state = _request_cancel_state(session, stored_request, now=now)
                else:
                    question = repository.get_question(session, target.id)
                    if (
                        question is None
                        or question.conversation_id != conversation_id
                    ):
                        raise AssistantTargetNotFound()
                    if question.status == AssistantQuestionStatus.ACTIVE.value:
                        if question.expires_at is not None and question.expires_at <= now:
                            repository.expire_question(question, at=now)
                            repository.start_question_source_retention(
                                session, question, at=now
                            )
                            state = AssistantCancelTargetState.NOT_ACTIVE
                        else:
                            repository.cancel_question(question, at=now)
                            repository.start_question_source_retention(
                                session, question, at=now
                            )
                            state = AssistantCancelTargetState.CANCELLED
                    elif question.status in {
                        AssistantQuestionStatus.ANSWERED.value,
                        AssistantQuestionStatus.CANCELLED.value,
                        AssistantQuestionStatus.EXPIRED.value,
                        AssistantQuestionStatus.INVALIDATED.value,
                    }:
                        state = AssistantCancelTargetState.NOT_ACTIVE
                    else:
                        raise AssistantStateCorrupted(
                            "Stored Assistant question status is invalid."
                        )

                return AssistantCancelResponse(
                    target=target,
                    target_state=state,
                    text=CANCEL_RESPONSE_TEXT[state],
                )
    except (AssistantConversationNotFound, AssistantTargetNotFound):
        raise
    except Exception as exc:
        _raise_storage_error(exc)
        raise AssertionError("unreachable")
