"""Schedule HTTP routes."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.clock import SystemClock
from app.core.database import get_session
from app.core.exceptions import ValidationFailure
from app.core.problem_details import Problem
from app.modules.schedules import service
from app.modules.schedules.category import Category
from app.modules.schedules.schemas import (
    ApiDateTime,
    ScheduleCreate,
    ScheduleListResponse,
    ScheduleResponse,
    ScheduleUpdate,
)

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])

REQUEST_ID_HEADER = {
    "X-Request-ID": {"description": "Server-generated request UUID", "schema": {"type": "string", "format": "uuid"}}
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


def _clock(request: Request) -> SystemClock:
    return request.app.state.clock


def _require_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
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


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key")],
) -> str:
    if len(value) == 0:
        code = "TOO_SHORT"
        detail = "The header must contain at least one character."
    elif len(value) > 255:
        code = "TOO_LONG"
        detail = "The header cannot exceed 255 characters."
    elif any(ord(character) > 127 or ord(character) < 32 or ord(character) == 127 for character in value):
        code = "INVALID_FORMAT"
        detail = "The header must contain control-free ASCII characters."
    else:
        return value
    raise ValidationFailure(
        [
            {
                "location": "header",
                "parameter": "Idempotency-Key",
                "code": code,
                "detail": detail,
            }
        ]
    )


def _strict_completed(
    value: str | None = Query(default=None, alias="completed"),
) -> bool | None:
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValidationFailure(
        [
            {
                "location": "query",
                "parameter": "completed",
                "code": "INVALID_FORMAT",
                "detail": "Only lowercase true or false is allowed.",
            }
        ]
    )


@router.get(
    "",
    response_model=ScheduleListResponse,
    operation_id="listSchedules",
    responses={200: {"headers": REQUEST_ID_HEADER}, **_problem_responses(422, 500, 503)},
)
def list_schedules(
    from_: Annotated[ApiDateTime, Query(alias="from")],
    to: Annotated[ApiDateTime, Query()],
    session: Annotated[Session, Depends(get_session)],
    completed: Annotated[bool | None, Depends(_strict_completed)],
    category: Annotated[Category | None, Query()] = None,
) -> ScheduleListResponse:
    return service.list_schedules(
        session,
        from_utc=from_,
        to_utc=to,
        category=category,
        completed=completed,
    )


@router.post(
    "",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="createSchedule",
    dependencies=[Depends(_require_json_content_type)],
    responses={
        201: {
            "headers": {
                **REQUEST_ID_HEADER,
                "Location": {"schema": {"type": "string"}},
                "Idempotent-Replayed": {"schema": {"type": "string"}},
            }
        },
        **_problem_responses(400, 409, 422, 500, 503),
    },
)
def create_schedule(
    data: ScheduleCreate,
    response: Response,
    _key: Annotated[str, Depends(_idempotency_key)],
    session: Annotated[Session, Depends(get_session)],
    clock: Annotated[SystemClock, Depends(_clock)],
) -> ScheduleResponse:
    created = service.create_schedule(session, data=data, clock=clock)
    response.headers["Location"] = f"/api/v1/schedules/{created.id}"
    return created


@router.get(
    "/{scheduleId}",
    response_model=ScheduleResponse,
    operation_id="getSchedule",
    responses={200: {"headers": REQUEST_ID_HEADER}, **_problem_responses(404, 422, 500, 503)},
)
def get_schedule(
    schedule_id: Annotated[UUID, Path(alias="scheduleId")],
    session: Annotated[Session, Depends(get_session)],
) -> ScheduleResponse:
    return service.get_schedule(session, schedule_id)


@router.patch(
    "/{scheduleId}",
    response_model=ScheduleResponse,
    operation_id="updateSchedule",
    dependencies=[Depends(_require_json_content_type)],
    responses={200: {"headers": REQUEST_ID_HEADER}, **_problem_responses(400, 404, 422, 500, 503)},
)
def update_schedule(
    schedule_id: Annotated[UUID, Path(alias="scheduleId")],
    data: ScheduleUpdate,
    session: Annotated[Session, Depends(get_session)],
    clock: Annotated[SystemClock, Depends(_clock)],
) -> ScheduleResponse:
    return service.update_schedule(
        session, schedule_id=schedule_id, data=data, clock=clock
    )


@router.delete(
    "/{scheduleId}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteSchedule",
    responses={204: {"headers": REQUEST_ID_HEADER}, **_problem_responses(404, 422, 500, 503)},
)
def delete_schedule(
    schedule_id: Annotated[UUID, Path(alias="scheduleId")],
    session: Annotated[Session, Depends(get_session)],
    clock: Annotated[SystemClock, Depends(_clock)],
) -> Response:
    service.delete_schedule(session, schedule_id=schedule_id, clock=clock)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
