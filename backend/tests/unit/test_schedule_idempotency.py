from __future__ import annotations

from app.modules.schedules.idempotency import (
    CREATE_METHOD,
    CREATE_ROUTE,
    FINGERPRINT_VERSION,
    canonical_fingerprint,
)
from app.modules.schedules.schemas import ScheduleCreate


def create_data(**overrides: object) -> ScheduleCreate:
    payload: dict[str, object] = {
        "title": "회의",
        "scheduledAt": "2026-09-13T04:45:00Z",
        "category": "OUTBOUND",
    }
    payload.update(overrides)
    return ScheduleCreate.model_validate(payload)


def test_fingerprint_is_stable_after_title_and_utc_normalization() -> None:
    normalized = canonical_fingerprint(create_data())
    equivalent = canonical_fingerprint(
        create_data(title="  회의  ", scheduledAt="2026-09-13T13:45:00+09:00")
    )

    assert normalized == equivalent
    assert len(normalized) == 64
    assert normalized == normalized.lower()


def test_fingerprint_includes_each_normalized_payload_field() -> None:
    original = canonical_fingerprint(create_data())
    variants = (
        create_data(title="다른 회의"),
        create_data(scheduledAt="2026-09-13T04:45:01Z"),
        create_data(category="RETURN"),
    )

    assert all(canonical_fingerprint(variant) != original for variant in variants)


def test_fingerprint_includes_version_method_and_canonical_route() -> None:
    data = create_data()
    original = canonical_fingerprint(data)

    assert canonical_fingerprint(data, version=FINGERPRINT_VERSION + 1) != original
    assert canonical_fingerprint(data, method="PUT") != original
    assert canonical_fingerprint(data, route="/api/v2/schedules") != original
    assert CREATE_METHOD == "POST"
    assert CREATE_ROUTE == "/api/v1/schedules"
    assert FINGERPRINT_VERSION == 2


def test_fingerprint_is_independent_of_json_layout_and_request_headers() -> None:
    first = ScheduleCreate.model_validate(
        {
            "title": "한글 일정",
            "scheduledAt": "2026-09-13T04:45:00.100000Z",
            "category": "OTHER",
        }
    )
    reordered = ScheduleCreate.model_validate_json(
        '{ "category": "OTHER", "scheduledAt": "2026-09-13T04:45:00.1Z", '
        '"title": "한글 일정" }'
    )

    assert canonical_fingerprint(first) == canonical_fingerprint(reordered)
    assert "idempotency_key" not in canonical_fingerprint.__annotations__
