"""Versioned canonical Assistant request fingerprints."""

from __future__ import annotations

import hashlib
import json

from app.modules.assistant.schemas import AssistantRequestCreate

FINGERPRINT_VERSION = 1


def canonical_request_json(
    data: AssistantRequestCreate,
    *,
    version: int = FINGERPRINT_VERSION,
) -> str:
    """Return deterministic JSON for a validated, normalized request body."""

    canonical = {
        "body": data.model_dump(mode="json", by_alias=True, exclude_none=True),
        "version": version,
    }
    return json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_fingerprint(
    data: AssistantRequestCreate,
    *,
    version: int = FINGERPRINT_VERSION,
) -> str:
    """Return the lowercase SHA-256 hash of a canonical Assistant request."""

    encoded = canonical_request_json(data, version=version).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
