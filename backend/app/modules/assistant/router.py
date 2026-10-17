"""Phase-one Assistant HTTP contract skeleton."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

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
        **_problem_responses(400, 409, 413, 422, 500, 503),
    },
)
def create_assistant_request(
    data: AssistantRequestCreate,
) -> AssistantRequestAcceptedResponse:
    raise AssistantUnavailable(assistant_request_id=data.request_id)


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
    request_id: Annotated[AssistantUuid, Path(alias="requestId")],
) -> AssistantRequestStatusResponse:
    raise AssistantUnavailable(assistant_request_id=request_id)


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
    conversation_id: Annotated[AssistantUuid, Path(alias="conversationId")],
    data: AssistantCancelRequest,
) -> AssistantCancelResponse:
    del conversation_id, data
    raise AssistantUnavailable()
