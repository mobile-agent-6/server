from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.assistant.serialization import (
    SnapshotSerializationError,
    deserialize_snapshot,
    serialize_snapshot,
)


def test_serialization_rejects_nested_non_string_object_key() -> None:
    value = {"outer": [{"nested": {1: "invalid"}}]}

    with pytest.raises(SnapshotSerializationError, match="keys must be strings"):
        serialize_snapshot(value)


@pytest.mark.parametrize(
    "value",
    [
        ("tuple",),
        {"set"},
        b"bytes",
        datetime(2026, 10, 18, tzinfo=timezone.utc),
        uuid4(),
    ],
    ids=["tuple", "set", "bytes", "datetime", "uuid"],
)
def test_serialization_rejects_nested_non_json_value(value: object) -> None:
    with pytest.raises(SnapshotSerializationError, match="unsupported type"):
        serialize_snapshot({"outer": [value]})


def test_serialization_accepts_only_json_values_and_finite_float() -> None:
    value = {
        "string": "값",
        "integer": 42,
        "float": -1.25,
        "booleans": [True, False],
        "null": None,
        "nested": {"items": []},
    }

    serialized = serialize_snapshot(value)

    assert serialized == (
        '{"booleans":[true,false],"float":-1.25,"integer":42,'
        '"nested":{"items":[]},"null":null,"string":"값"}'
    )
    assert deserialize_snapshot(serialized) == value


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_serialization_rejects_non_finite_float(value: float) -> None:
    with pytest.raises(SnapshotSerializationError, match="non-finite"):
        serialize_snapshot({"nested": [value]})
