"""Strict, bounded JSON serialization for Assistant persistence snapshots."""

from __future__ import annotations

import json
import math
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.types import TypeDecorator

# Control state is an internal, versioned decision snapshot rather than a transcript.
# 64 KiB leaves room for bounded structured context while making accidental storage of
# model prompts, responses, or full conversation history fail at the persistence edge.
CONTROL_STATE_MAX_BYTES = 65_536
SNAPSHOT_MAX_BYTES = 65_536


class SnapshotSerializationError(ValueError):
    """Raised when an Assistant snapshot is not strict, bounded JSON."""


def _reject_constant(value: str) -> None:
    raise SnapshotSerializationError(f"non-finite JSON number is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotSerializationError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _validate_json_value(value: object, *, active_containers: set[int]) -> None:
    value_type = type(value)
    if value is None or value_type is str:
        return
    # bool must be classified before int because bool subclasses int.
    if value_type is bool:
        return
    if value_type is int:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise SnapshotSerializationError(
                "non-finite JSON numbers are not allowed"
            )
        return
    if value_type is not dict and value_type is not list:
        raise SnapshotSerializationError(
            f"snapshot contains unsupported type: {value_type.__name__}"
        )

    container_id = id(value)
    if container_id in active_containers:
        raise SnapshotSerializationError("snapshot contains a circular reference")
    active_containers.add(container_id)
    try:
        if value_type is dict:
            for key, item in cast(dict[object, object], value).items():
                if type(key) is not str:
                    raise SnapshotSerializationError(
                        "snapshot object keys must be strings"
                    )
                _validate_json_value(item, active_containers=active_containers)
        else:
            for item in cast(list[object], value):
                _validate_json_value(item, active_containers=active_containers)
    finally:
        active_containers.remove(container_id)


def serialize_snapshot(value: object, *, max_bytes: int = SNAPSHOT_MAX_BYTES) -> str:
    """Serialize a JSON-compatible value canonically and enforce a UTF-8 bound."""

    _validate_json_value(value, active_containers=set())
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SnapshotSerializationError("snapshot is not strict JSON") from exc
    if len(serialized.encode("utf-8")) > max_bytes:
        raise SnapshotSerializationError(
            f"snapshot exceeds the {max_bytes}-byte persistence limit"
        )
    return serialized


def deserialize_snapshot(
    value: str, *, max_bytes: int = SNAPSHOT_MAX_BYTES
) -> object:
    """Decode strict JSON without accepting duplicate keys or non-finite numbers."""

    if not isinstance(value, str):
        raise SnapshotSerializationError("stored snapshot must be text")
    if len(value.encode("utf-8")) > max_bytes:
        raise SnapshotSerializationError(
            f"stored snapshot exceeds the {max_bytes}-byte persistence limit"
        )
    try:
        return json.loads(
            value,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_object,
        )
    except (json.JSONDecodeError, SnapshotSerializationError) as exc:
        if isinstance(exc, SnapshotSerializationError):
            raise
        raise SnapshotSerializationError("stored snapshot is invalid JSON") from exc


class JsonSnapshot(TypeDecorator[object]):
    """Persist Python JSON values as canonical TEXT and decode them strictly."""

    impl = sa.Text
    cache_ok = True

    def __init__(self, *, max_bytes: int = SNAPSHOT_MAX_BYTES) -> None:
        super().__init__()
        self.max_bytes = max_bytes

    def process_bind_param(self, value: object | None, dialect: sa.Dialect):
        del dialect
        if value is None:
            return None
        return serialize_snapshot(value, max_bytes=self.max_bytes)

    def process_result_value(self, value: str | None, dialect: sa.Dialect):
        del dialect
        if value is None:
            return None
        return deserialize_snapshot(value, max_bytes=self.max_bytes)

    @property
    def python_type(self) -> type[object]:
        return object

    def copy(self, **kw: object) -> "JsonSnapshot":
        del kw
        return JsonSnapshot(max_bytes=self.max_bytes)
