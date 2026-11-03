"""Assistant HTTP routes over the persistent state services."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Path, Request, status
from starlette.responses import JSONResponse

from app.core.problem_details import Problem
from app.modules.assistant.errors import AssistantUnavailable
from app.modules.assistant.enums import (
    AssistantCancelTargetState,
    AssistantCancelTargetType,
)
from app.modules.assistant.runtime import (
    AssistantRuntimeController,
    RegistrationOutcome,
    compensate_registration_failure,
)
from app.modules.assistant.schemas import (
    AssistantCancelRequest,
    AssistantCancelResponse,
    AssistantRequestAcceptedResponse,
    AssistantRequestCreate,
    AssistantRequestStatusResponse,
    AssistantInputText,
    AssistantUuid,
)
from app.modules.assistant.state_service import (
    admit_assistant_request,
    cancel_assistant_target as cancel_target,
    get_assistant_request as get_request_status,
)

router = APIRouter(prefix="/api/v1/assistant", tags=["assistant"])

REQUEST_ID_HEADER = {
    "X-Request-ID": {
        "description": "Server-generated request UUID",
        "schema": {"type": "string", "format": "uuid"},
    }
}


def _problem_responses(*statuses: int) -> dict[int, dict[str, object]]:
    return {
        code: {
            "model": Problem,
            "description": "Problem Details",
            "content": {"application/problem+json": {}},
            "headers": REQUEST_ID_HEADER,
        }
        for code in statuses
    }


def _assistant_session_factory(request: Request):
    if not request.app.state.assistant_schema_ready:
        return None
    return request.app.state.assistant_session_factory


def _runtime(request: Request) -> AssistantRuntimeController | None:
    runtime = request.app.state.assistant_runtime
    return runtime if isinstance(runtime, AssistantRuntimeController) else None


def _runtime_ready(request: Request) -> bool:
    runtime = _runtime(request)
    return bool(
        request.app.state.assistant_runtime_ready
        and runtime is not None
        and runtime.snapshot().ready
    )


def _provider_available(request: Request, data: AssistantRequestCreate) -> bool:
    if request.app.state.assistant_provider_available:
        return True
    # Selection and confirmation use the persisted intent and never call the
    # model.  A new text request may reconnect a model server that started
    # after the backend, which is the usual Orange Pi boot order race.
    reconnect = request.app.state.assistant_provider_reconnect
    if isinstance(data.input, AssistantInputText) and reconnect is not None:
        return bool(reconnect())
    return False


def _compensate_registration_failure(
    request: Request, *, request_id: AssistantUuid
) -> None:
    runtime = _runtime(request)
    if runtime is not None:
        runtime.mark_unhealthy()
    try:
        compensate_registration_failure(
            request.app.state.assistant_session_factory,
            request_id=request_id,
            wall_clock=request.app.state.clock,
        )
    except Exception as exc:
        logging.getLogger(__name__).error(
            "Assistant registration compensation failed: %s",
            type(exc).__name__,
            extra={"event": "assistant.registration_compensation_failed"},
        )


@router.post(
    "/requests",
    response_model=AssistantRequestAcceptedResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="createAssistantRequest",
    responses={
        202: {"headers": REQUEST_ID_HEADER},
        200: {
            "model": AssistantRequestStatusResponse,
            "description": "Existing request status",
            "headers": REQUEST_ID_HEADER,
        },
        **_problem_responses(400, 404, 409, 413, 422, 500, 503),
    },
)
def create_assistant_request(
    request: Request,
    data: AssistantRequestCreate,
) -> AssistantRequestAcceptedResponse | JSONResponse:
    session_factory = _assistant_session_factory(request)
    if session_factory is None:
        raise AssistantUnavailable(assistant_request_id=data.request_id)
    runtime = _runtime(request)
    if runtime is not None:
        runtime.try_lazy_timeout()
    accepted_at = request.app.state.clock.now()
    response = admit_assistant_request(
        session_factory,
        data=data,
        accepted_at=accepted_at,
        new_conversation_id=request.app.state.assistant_uuid_factory(),
        runtime_ready=_runtime_ready(request),
        provider_available=_provider_available(request, data),
    )
    if isinstance(response, AssistantRequestStatusResponse):
        return JSONResponse(
            response.model_dump(mode="json", by_alias=True, exclude_none=True),
            status_code=status.HTTP_200_OK,
        )
    if runtime is None:
        _compensate_registration_failure(request, request_id=data.request_id)
        return response
    try:
        registration = runtime.register_accepted(
            request_id=data.request_id,
            accepted_at=accepted_at,
        )
    except Exception as exc:
        logging.getLogger(__name__).error(
            "Assistant deadline registration failed: %s",
            type(exc).__name__,
            extra={"event": "assistant.deadline_registration_failed"},
        )
        _compensate_registration_failure(request, request_id=data.request_id)
    else:
        if registration is RegistrationOutcome.UNAVAILABLE:
            logging.getLogger(__name__).error(
                "Assistant deadline registration was unavailable",
                extra={"event": "assistant.deadline_registration_unavailable"},
            )
            _compensate_registration_failure(request, request_id=data.request_id)
    return response


@router.get(
    "/requests/{requestId}",
    response_model=AssistantRequestStatusResponse,
    response_model_exclude_none=True,
    operation_id="getAssistantRequest",
    responses={
        200: {"headers": REQUEST_ID_HEADER},
        **_problem_responses(404, 410, 422, 500, 503),
    },
)
def get_assistant_request(
    request: Request,
    request_id: Annotated[AssistantUuid, Path(alias="requestId")],
) -> AssistantRequestStatusResponse:
    if not request.app.state.assistant_schema_ready:
        raise AssistantUnavailable(assistant_request_id=request_id)
    runtime = _runtime(request)
    if runtime is not None:
        runtime.try_lazy_timeout(request_id)
    return get_request_status(
        request.app.state.session_factory,
        request_id=request_id,
    )


@router.post(
    "/conversations/{conversationId}/cancel",
    response_model=AssistantCancelResponse,
    operation_id="cancelAssistantTarget",
    responses={
        200: {"headers": REQUEST_ID_HEADER},
        **_problem_responses(400, 404, 413, 422, 500, 503),
    },
)
def cancel_assistant_target(
    request: Request,
    conversation_id: Annotated[AssistantUuid, Path(alias="conversationId")],
    data: AssistantCancelRequest,
) -> AssistantCancelResponse:
    session_factory = _assistant_session_factory(request)
    if session_factory is None:
        raise AssistantUnavailable()
    runtime = _runtime(request)
    if runtime is not None:
        lazy_request_id = (
            data.target.id
            if data.target.type is AssistantCancelTargetType.REQUEST
            else None
        )
        runtime.try_lazy_timeout(lazy_request_id)
    response = cancel_target(
        session_factory,
        conversation_id=conversation_id,
        target=data.target,
        now=request.app.state.clock.now(),
    )
    if (
        runtime is not None
        and data.target.type is AssistantCancelTargetType.REQUEST
        and response.target_state
        in {
            AssistantCancelTargetState.CANCELLED,
            AssistantCancelTargetState.ALREADY_COMPLETED,
            AssistantCancelTargetState.UNCERTAIN,
        }
    ):
        runtime.discard(data.target.id)
    return response
