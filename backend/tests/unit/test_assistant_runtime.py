from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import UUID

import sqlalchemy as sa

from app.core.clock import FixedClock, FixedMonotonicClock
from app.core.config import Settings
from app.core.database import create_assistant_engine, create_session_factory
from app.modules.assistant.model import AssistantConversation, AssistantRequest
from app.modules.assistant.processor import ProcessOneResult
from app.modules.assistant.runtime import (
    AssistantRuntimeController,
    RegistrationOutcome,
)

NOW = datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)
REQUEST_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CONVERSATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class FakeOwnershipLock:
    def __init__(self) -> None:
        self.released = False

    def release(self) -> None:
        self.released = True


def add_request(factory, *, write_started: bool = False) -> None:
    with factory.begin() as session:
        session.add(
            AssistantConversation(
                id=CONVERSATION_ID,
                created_at=NOW,
                last_valid_input_at=NOW,
                expired_at=None,
                control_state_version=None,
                control_state_json=None,
            )
        )
        session.add(
            AssistantRequest(
                id=REQUEST_ID,
                conversation_id=CONVERSATION_ID,
                fingerprint_version=1,
                fingerprint_hash="a" * 64,
                normalized_body_json={"requestId": str(REQUEST_ID)},
                status="processing" if write_started else "accepted",
                result_kind=None,
                result_json=None,
                accepted_at=NOW,
                processing_at=NOW if write_started else None,
                completed_at=None,
                task_reference_accepted_at=NOW,
                write_started=write_started,
                write_started_at=NOW if write_started else None,
                write_operation="update" if write_started else None,
                write_commit_mode="atomic" if write_started else None,
                pending_write_version=None,
                pending_write_json=None,
                create_idempotency_key=None,
                create_idempotency_scope=None,
                had_uncertain=False,
                uncertain_at=None,
                uncertain_acknowledged_at=None,
                uncertain_acknowledged_by_request_id=None,
                uncertain_resolved_at=None,
                retention_started_at=None,
                tombstone=False,
                tombstoned_at=None,
                cancelled_at=None,
            )
        )


def make_runtime(database_path: Path, process_request):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_assistant_engine(settings)
    timeout_engine = create_assistant_engine(settings, busy_timeout_ms=0)
    factory = create_session_factory(engine)
    timeout_factory = create_session_factory(timeout_engine)
    monotonic = FixedMonotonicClock()
    wall = FixedClock(NOW)
    ownership = FakeOwnershipLock()
    runtime = AssistantRuntimeController(
        ownership_lock=ownership,
        timeout_session_factory=timeout_factory,
        process_request=process_request,
        wall_clock=wall,
        monotonic_clock=monotonic,
        cleanup_resources=lambda: (timeout_engine.dispose(), engine.dispose()),
        shutdown_grace_seconds=0.2,
    )
    return runtime, factory, monotonic, wall, ownership


def test_lazy_timeout_uses_exact_boundary_and_duplicate_does_not_extend_deadline(
    database_path: Path,
) -> None:
    processed = Event()

    def process_request(_request_id, _guard):
        processed.set()
        return ProcessOneResult.PENDING_WRITE

    runtime, factory, monotonic, wall, ownership = make_runtime(
        database_path, process_request
    )
    add_request(factory)
    assert runtime.start()
    assert runtime.register_accepted(
        request_id=REQUEST_ID, accepted_at=NOW
    ) is RegistrationOutcome.REGISTERED
    assert processed.wait(1)

    monotonic.advance(100)
    assert runtime.register_accepted(
        request_id=REQUEST_ID, accepted_at=NOW + timedelta(seconds=100)
    ) is RegistrationOutcome.DUPLICATE
    monotonic.advance(199.999)
    runtime.try_lazy_timeout(REQUEST_ID)
    with factory() as session:
        assert session.get(AssistantRequest, REQUEST_ID).status == "accepted"

    wall.advance(timedelta(days=30))
    monotonic.advance(0.001)
    runtime.try_lazy_timeout(REQUEST_ID)
    with factory() as session:
        stored = session.get(AssistantRequest, REQUEST_ID)
        assert stored.status == "completed"
        assert stored.result_json["code"] == "PROCESSING_TIMEOUT"
        assert stored.completed_at == wall.now()
        assert stored.retention_started_at == wall.now()

    assert runtime.shutdown()
    assert ownership.released


def test_timeout_refuses_to_classify_active_write_marker(database_path: Path) -> None:
    runtime, factory, monotonic, _wall, ownership = make_runtime(
        database_path,
        lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE,
    )
    add_request(factory, write_started=True)
    assert runtime.start()
    assert runtime.register_accepted(
        request_id=REQUEST_ID, accepted_at=NOW
    ) is RegistrationOutcome.REGISTERED
    monotonic.advance(300)

    runtime.try_lazy_timeout(REQUEST_ID)

    assert runtime.snapshot().healthy is False
    with factory() as session:
        stored = session.get(AssistantRequest, REQUEST_ID)
        assert stored.status == "processing"
        assert stored.write_started is True
        assert stored.result_json is None
    assert runtime.shutdown()
    assert ownership.released


def test_unexpected_worker_exception_keeps_sweeper_running_to_deadline(
    database_path: Path,
) -> None:
    attempted = Event()

    def explode(_request_id, _guard):
        attempted.set()
        raise RuntimeError("injected")

    runtime, factory, monotonic, _wall, ownership = make_runtime(
        database_path, explode
    )
    add_request(factory)
    assert runtime.start()
    runtime.register_accepted(request_id=REQUEST_ID, accepted_at=NOW)
    assert attempted.wait(1)
    for _ in range(100):
        snapshot = runtime.snapshot()
        if not snapshot.healthy and not snapshot.worker_alive:
            break
        Event().wait(0.005)

    snapshot = runtime.snapshot()
    assert snapshot.healthy is False
    assert snapshot.accepting is False
    assert snapshot.worker_alive is False
    assert snapshot.sweeper_alive is True
    assert runtime.register_accepted(
        request_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        accepted_at=NOW,
    ) is RegistrationOutcome.UNAVAILABLE

    monotonic.advance(300)
    runtime.wake()
    for _ in range(200):
        with factory() as session:
            stored = session.get(AssistantRequest, REQUEST_ID)
            if stored.status == "completed":
                break
        Event().wait(0.005)
    with factory() as session:
        stored = session.get(AssistantRequest, REQUEST_ID)
        assert stored.status == "completed"
        assert stored.result_json["code"] == "PROCESSING_TIMEOUT"
    assert runtime.snapshot().sweeper_alive is True
    assert runtime.snapshot().registered_count == 0
    assert runtime.shutdown()
    assert ownership.released


def test_worker_processes_registered_requests_one_at_a_time_in_order(
    database_path: Path,
) -> None:
    seen: list[UUID] = []
    both = Event()
    first_id = REQUEST_ID
    second_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

    def record(request_id, _guard):
        seen.append(request_id)
        if len(seen) == 2:
            both.set()
        return ProcessOneResult.SKIPPED

    runtime, _factory, _monotonic, _wall, ownership = make_runtime(
        database_path, record
    )
    assert runtime.start()
    runtime.register_accepted(request_id=first_id, accepted_at=NOW)
    runtime.register_accepted(
        request_id=second_id, accepted_at=NOW + timedelta(microseconds=1)
    )
    assert both.wait(1)
    assert seen == [first_id, second_id]
    assert runtime.snapshot().registered_count == 0
    assert runtime.shutdown()
    assert ownership.released
