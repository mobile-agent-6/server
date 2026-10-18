from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.problem_details import PROBLEM_TITLES


_FORBIDDEN_DETAIL_FRAGMENTS = (
    "traceback",
    "sqlite:///",
    ".db",
    "/private/",
    "/tmp/",
    "operationalerror",
    "sqlalchemy.exc",
    'file "',
)


def assert_problem_contract(response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status
    assert response.headers["content-type"].split(";", 1)[0] == (
        "application/problem+json"
    )
    request_id = UUID(response.headers["X-Request-ID"])
    assert request_id.version == 4

    body = response.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["title"] == PROBLEM_TITLES[code]
    assert body["requestId"] == str(request_id)

    detail = body["detail"]
    assert isinstance(detail, str)
    assert detail.strip()
    normalized_detail = detail.casefold()
    assert all(
        fragment not in normalized_detail
        for fragment in _FORBIDDEN_DETAIL_FRAGMENTS
    )
    return body
