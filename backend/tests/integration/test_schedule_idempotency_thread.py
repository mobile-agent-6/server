from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.schedules.model import IdempotencyRecord, Schedule
from tests.api.test_schedule_api import create_payload


@pytest.mark.integration
@pytest.mark.race
def test_two_thread_same_key_never_creates_duplicate_schedule(
    client: TestClient,
) -> None:
    barrier = Barrier(2)

    def post() -> tuple[int, str | None]:
        barrier.wait(timeout=5)
        response = client.post(
            "/api/v1/schedules",
            json=create_payload(),
            headers={"Idempotency-Key": "thread-smoke-key"},
        )
        return response.status_code, response.headers.get("Idempotent-Replayed")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: post(), range(2)))

    statuses = sorted(status for status, _ in results)
    assert statuses in ([201, 201], [201, 503])
    if statuses == [201, 201]:
        assert sorted(replayed for _, replayed in results if replayed) == ["true"]

    with Session(client.app.state.engine) as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 1
        assert (
            session.scalar(
                sa.select(sa.func.count()).select_from(IdempotencyRecord)
            )
            == 1
        )
