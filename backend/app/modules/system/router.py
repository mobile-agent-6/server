"""System health and time synchronization HTTP routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request

from app.core.exceptions import ValidationFailure
from app.core.problem_details import Problem
from app.modules.system import service
from app.modules.system.schemas import HealthResponse, TimeSyncRequest, TimeSyncResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])

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


def _require_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0]
    if content_type.strip().lower() != "application/json":
        raise ValidationFailure(
            [
                {
                    "location": "body",
                    "pointer": "",
                    "code": "INVALID_TYPE",
                    "detail": "Content-Type must be application/json.",
                }
            ]
        )


@router.get(
    "/health",
    response_model=HealthResponse,
    operation_id="healthCheck",
    responses={
        200: {"headers": REQUEST_ID_HEADER},
        **_problem_responses(500, 503),
    },
)
def health_check(request: Request) -> HealthResponse:
    return service.health(
        engine=request.app.state.engine,
        expected_head=request.app.state.expected_alembic_head,
        clock=request.app.state.clock,
    )


@router.post(
    "/api/v1/system/time-sync",
    response_model=TimeSyncResponse,
    operation_id="syncSystemTime",
    dependencies=[Depends(_require_json_content_type)],
    responses={
        200: {"headers": REQUEST_ID_HEADER},
        **_problem_responses(400, 409, 422, 500, 503),
    },
)
def sync_system_time(data: TimeSyncRequest, request: Request) -> TimeSyncResponse:
    started = request.state.request_started_monotonic
    result = service.sync_system_time(
        data=data,
        settings=request.app.state.settings,
        adapter=request.app.state.time_sync_adapter,
        process_lock=request.app.state.time_sync_lock,
        monotonic_clock=request.app.state.monotonic_clock,
        request_started_monotonic=started,
    )
    logger.warning(
        "system time synchronization processed",
        extra={
            "event": "system.time_sync",
            "requestId": request.state.request_id,
            "previousServerTime": result.previous_server_time,
            "requestedClientTime": data.current_time,
            "resultServerTime": result.server_time,
            "adjusted": result.adjusted,
            "clientIp": request.client.host if request.client else None,
        },
    )
    return result
