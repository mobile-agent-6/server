"""Canonical Schedule creation fingerprints."""

from __future__ import annotations

import hashlib
import json
from datetime import timezone

from app.modules.schedules.schemas import ScheduleCreate

FINGERPRINT_VERSION = 2
CREATE_METHOD = "POST"
CREATE_ROUTE = "/api/v1/schedules"


def canonical_fingerprint(
    data: ScheduleCreate,
    *,
    version: int = FINGERPRINT_VERSION,
    method: str = CREATE_METHOD,
    route: str = CREATE_ROUTE,
) -> str:
    """Return the lowercase SHA-256 hash of the normalized creation request."""

    canonical = {
        "body": {
            "category": data.category.value,
            "scheduledAt": data.scheduled_at.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
            "title": data.title,
        },
        "method": method,
        "route": route,
        "version": version,
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
