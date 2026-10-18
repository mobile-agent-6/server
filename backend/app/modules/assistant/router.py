"""Assistant HTTP routes over the persistent state services."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Request, status
from starlette.responses import JSONResponse

from app.core.problem_details import Problem
from app.modules.assistant.errors import AssistantUnavailable
from app.modules.assistant.schemas import (
    AssistantCancelRequest,
    AssistantCancelResponse,
    AssistantRequestAcceptedResponse,
    AssistantRequestCreate,
    AssistantRequestStatusResponse,
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
    response = admit_assistant_request(
        session_factory,
        data=data,
        accepted_at=request.app.state.clock.now(),
        new_conversation_id=request.app.state.assistant_uuid_factory(),
        new_requests_enabled=request.app.state.assistant_new_requests_enabled,
    )
    if isinstance(response, AssistantRequestStatusResponse):
        return JSONResponse(
            response.model_dump(mode="json", by_alias=True, exclude_none=True),
            status_code=status.HTTP_200_OK,
        )
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
    return cancel_target(
        session_factory,
        conversation_id=conversation_id,
        target=data.target,
        now=request.app.state.clock.now(),
    )
