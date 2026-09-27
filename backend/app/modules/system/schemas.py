"""Public health and time synchronization schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import AliasGenerator, BaseModel, ConfigDict, Field, field_serializer
from pydantic.alias_generators import to_camel

from app.modules.schedules.schemas import ApiDateTime


class SystemRequestModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(
            validation_alias=to_camel,
            serialization_alias=to_camel,
        ),
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class SystemResponseModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


def _serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class HealthResponse(SystemResponseModel):
    status: Literal["ok"]
    api_version: Literal["v1"]
    server_time: datetime = Field(json_schema_extra={"format": "date-time"})

    @field_serializer("server_time")
    def serialize_server_time(self, value: datetime) -> str:
        return _serialize_datetime(value)


class TimeSyncRequest(SystemRequestModel):
    current_time: ApiDateTime


class TimeSyncResponse(SystemResponseModel):
    adjusted: bool
    previous_server_time: datetime = Field(
        json_schema_extra={"format": "date-time"}
    )
    server_time: datetime = Field(json_schema_extra={"format": "date-time"})

    @field_serializer("previous_server_time", "server_time")
    def serialize_times(self, value: datetime) -> str:
        return _serialize_datetime(value)
