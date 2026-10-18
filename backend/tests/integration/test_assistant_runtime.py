from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, current_thread, enumerate as enumerate_threads
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.core.clock import FixedClock, FixedMonotonicClock
from app.core.config import Settings
from app.core.database import (
    create_assistant_engine,
    create_assistant_probe_engine,
    create_session_factory,
)
from app.main import create_app
from app.modules.assistant.decisions import DecisionEnvelope
from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantRequest,
)
from app.modules.assistant.processor import ProcessOneResult
from app.modules.assistant.readiness import probe_assistant_schema
from app.modules.assistant.runtime import RegistrationOutcome
from app.modules.assistant import repository as assistant_repository
from app.modules.assistant.runtime_lock import (
    AssistantRuntimeFileLock,
    AssistantRuntimeLockContended,
    AssistantRuntimeLockUnavailable,
)
from app.modules.schedules.model import Schedule
from app.modules.system.service import resolve_expected_head
from tests.conftest import FIXED_NOW
from tests.fakes.assistant import ScriptedDecisionProvider

REQUEST_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SECOND_REQUEST_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
CONVERSATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SECOND_CONVERSATION_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
QUESTION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def settings_for(database_path: Path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{database_path}")


def wait_until(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        Event().wait(0.005)
    raise AssertionError("condition was not reached before timeout")


def request_payload(
    request_id: UUID = REQUEST_ID,
    *,
    conversation_id: UUID | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "requestId": str(request_id),
        "input": {"type": "text", "text": "재고를 확인해 줘"},
    }
    if conversation_id is not None:
        payload["conversationId"] = str(conversation_id)
    return payload


def add_request(
    factory,
    *,
    request_id: UUID,
    conversation_id: UUID,
    status: str,
    pending: bool = False,
    write_started: bool = False,
    completed_result: dict[str, object] | None = None,
) -> None:
    accepted_at = FIXED_NOW - timedelta(minutes=5)
    with factory.begin() as session:
        session.add(
            AssistantConversation(
                id=conversation_id,
                created_at=accepted_at,
                last_valid_input_at=accepted_at,
                expired_at=None,
                control_state_version=None,
                control_state_json=None,
            )
        )
        completed = status == "completed"
        session.add(
            AssistantRequest(
                id=request_id,
                conversation_id=conversation_id,
                fingerprint_version=1,
                fingerprint_hash="a" * 64,
                normalized_body_json={"requestId": str(request_id)},
                status=status,
                result_kind=(
                    str(completed_result["kind"])
                    if completed_result is not None
                    else None
                ),
                result_json=completed_result,
                accepted_at=accepted_at,
                processing_at=(
                    accepted_at + timedelta(seconds=1)
                    if status == "processing"
                    else None
                ),
                completed_at=FIXED_NOW if completed else None,
                task_reference_accepted_at=accepted_at,
                write_started=write_started,
                write_started_at=FIXED_NOW if write_started else None,
                write_operation="update" if write_started else None,
                write_commit_mode="atomic" if write_started else None,
                pending_write_version=1 if pending else None,
                pending_write_json=(
                    {
                        "version": 1,
                        "operation": "create",
                        "title": "재고 확인",
                    }
                    if pending
                    else None
                ),
                create_idempotency_key=None,
                create_idempotency_scope=None,
                had_uncertain=False,
                uncertain_at=None,
                uncertain_acknowledged_at=None,
                uncertain_acknowledged_by_request_id=None,
                uncertain_resolved_at=None,
                retention_started_at=FIXED_NOW if completed else None,
                tombstone=False,
                tombstoned_at=None,
                cancelled_at=None,
            )
        )


def standalone_factory(database_path: Path):
    engine = create_assistant_engine(settings_for(database_path))
    return engine, create_session_factory(engine)


def test_probe_is_query_only_and_not_blocked_by_runtime_writer(
    database_path: Path,
) -> None:
    settings = settings_for(database_path)
    writer = create_assistant_engine(settings, busy_timeout_ms=0)
    connection = writer.connect()
    transaction = connection.begin()
    probe = create_assistant_probe_engine(settings)
    statements: list[str] = []
    event.listen(
        probe,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _many: (
            statements.append(statement)
        ),
    )
    try:
        probe_assistant_schema(probe, expected_head=resolve_expected_head())
    finally:
        transaction.rollback()
        connection.close()
        writer.dispose()
        probe.dispose()

    assert statements
    assert all("BEGIN IMMEDIATE" not in statement.upper() for statement in statements)


def test_schema_failure_does_not_create_runtime_lock(tmp_path: Path) -> None:
    database = tmp_path / "missing.db"
    application = create_app(settings_for(database))
    with TestClient(application, raise_server_exceptions=False) as client:
        assert client.get("/health").status_code == 503
        assert application.state.assistant_schema_ready is False
    assert not database.with_name(
        f"{database.name}.assistant-runtime.lock"
    ).exists()
    application.state.engine.dispose()


def test_two_apps_share_schema_but_only_one_owns_runtime(
    database_path: Path,
) -> None:
    first = create_app(settings_for(database_path))
    second = create_app(settings_for(database_path))
    third = create_app(settings_for(database_path))
    with TestClient(first) as first_client:
        assert first.state.assistant_runtime_owner is True
        assert first.state.assistant_runtime_healthy is True
        with TestClient(second, raise_server_exceptions=False) as second_client:
            assert second.state.assistant_schema_ready is True
            assert second.state.assistant_runtime_owner is False
            assert second.state.assistant_runtime is None
            assert second_client.get("/health").status_code == 200
            response = second_client.post(
                "/api/v1/assistant/requests", json=request_payload()
            )
            assert response.status_code == 503
        assert first_client.get("/health").status_code == 200

    with TestClient(third):
        assert third.state.assistant_runtime_owner is True
        assert third.state.assistant_runtime_healthy is True

    for application in (first, second, third):
        application.state.engine.dispose()


def test_lock_io_failure_isolates_runtime_but_keeps_schema_reads(
    database_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    engine, factory = standalone_factory(database_path)
    completed = {
        "kind": "failure",
        "text": "retry",
        "code": "PROCESSING_INTERRUPTED",
        "nextAction": "retry_later",
    }
    add_request(
        factory,
        request_id=REQUEST_ID,
        conversation_id=CONVERSATION_ID,
        status="completed",
        completed_result=completed,
    )
    engine.dispose()

    def fail_lock(_path: Path):
        raise AssistantRuntimeLockUnavailable("injected")

    application = create_app(
        settings_for(database_path),
        _assistant_runtime_lock_factory_for_tests=fail_lock,
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        assert application.state.assistant_schema_ready is True
        assert application.state.assistant_runtime_owner is False
        assert client.get("/health").status_code == 200
        assert client.get(
            f"/api/v1/assistant/requests/{REQUEST_ID}"
        ).status_code == 200
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload(SECOND_REQUEST_ID)
        ).status_code == 503
    assert str(database_path) not in caplog.text
    assert "재고를 확인해 줘" not in caplog.text
    application.state.engine.dispose()


def test_startup_recovery_atomically_interrupts_only_prewrite_requests(
    database_path: Path,
) -> None:
    engine, factory = standalone_factory(database_path)
    third_request = UUID("11111111-1111-4111-8111-111111111111")
    third_conversation = UUID("22222222-2222-4222-8222-222222222222")
    completed_request = UUID("33333333-3333-4333-8333-333333333333")
    completed_conversation = UUID("44444444-4444-4444-8444-444444444444")
    original_result = {
        "kind": "failure",
        "text": "original",
        "code": "INTERNAL_ERROR",
        "nextAction": "none",
    }
    add_request(
        factory,
        request_id=REQUEST_ID,
        conversation_id=CONVERSATION_ID,
        status="accepted",
    )
    add_request(
        factory,
        request_id=SECOND_REQUEST_ID,
        conversation_id=SECOND_CONVERSATION_ID,
        status="processing",
    )
    add_request(
        factory,
        request_id=third_request,
        conversation_id=third_conversation,
        status="processing",
        pending=True,
    )
    add_request(
        factory,
        request_id=completed_request,
        conversation_id=completed_conversation,
        status="completed",
        completed_result=original_result,
    )
    engine.dispose()

    application = create_app(settings_for(database_path))
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application):
        assert application.state.assistant_runtime_owner is True
        assert application.state.assistant_runtime.snapshot().registered_count == 0
        with application.state.session_factory() as session:
            for request_id in (REQUEST_ID, SECOND_REQUEST_ID, third_request):
                stored = session.get(AssistantRequest, request_id)
                assert stored.status == "completed"
                assert stored.result_kind == "failure"
                assert stored.result_json["code"] == "PROCESSING_INTERRUPTED"
                assert stored.result_json["nextAction"] == "retry_later"
                assert stored.completed_at == FIXED_NOW
                assert stored.retention_started_at == FIXED_NOW
                assert stored.pending_write_version is None
                assert stored.pending_write_json is None
            processing = session.get(AssistantRequest, SECOND_REQUEST_ID)
            assert processing.processing_at == FIXED_NOW - timedelta(minutes=5) + timedelta(seconds=1)
            untouched = session.get(AssistantRequest, completed_request)
            assert untouched.result_json == original_result
    application.state.engine.dispose()


def test_active_write_marker_blocks_all_recovery_and_background_start(
    database_path: Path,
) -> None:
    engine, factory = standalone_factory(database_path)
    add_request(
        factory,
        request_id=REQUEST_ID,
        conversation_id=CONVERSATION_ID,
        status="accepted",
    )
    add_request(
        factory,
        request_id=SECOND_REQUEST_ID,
        conversation_id=SECOND_CONVERSATION_ID,
        status="processing",
        write_started=True,
    )
    engine.dispose()

    application = create_app(settings_for(database_path))
    with TestClient(application, raise_server_exceptions=False) as client:
        assert application.state.assistant_schema_ready is True
        assert application.state.assistant_runtime_owner is False
        assert application.state.assistant_runtime is None
        assert client.get("/health").status_code == 200
        assert client.get(
            f"/api/v1/assistant/requests/{REQUEST_ID}"
        ).json()["status"] == "accepted"
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload(
                UUID("55555555-5555-4555-8555-555555555555")
            )
        ).status_code == 503
        with application.state.session_factory() as session:
            assert session.get(AssistantRequest, REQUEST_ID).status == "accepted"
            marker = session.get(AssistantRequest, SECOND_REQUEST_ID)
            assert marker.status == "processing"
            assert marker.write_started is True
    application.state.engine.dispose()


def test_recovery_failure_rolls_back_and_releases_runtime_ownership(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, factory = standalone_factory(database_path)
    add_request(
        factory,
        request_id=REQUEST_ID,
        conversation_id=CONVERSATION_ID,
        status="accepted",
    )
    engine.dispose()
    original = assistant_repository.recover_prewrite_requests

    def fail_after_update(session, *, result_json, at):
        original(session, result_json=result_json, at=at)
        raise RuntimeError("injected recovery failure")

    monkeypatch.setattr(
        assistant_repository, "recover_prewrite_requests", fail_after_update
    )
    application = create_app(settings_for(database_path))
    with TestClient(application) as client:
        assert application.state.assistant_schema_ready is True
        assert application.state.assistant_runtime_owner is False
        assert client.get(
            f"/api/v1/assistant/requests/{REQUEST_ID}"
        ).json()["status"] == "accepted"
        assert client.get("/health").status_code == 200

    ownership = AssistantRuntimeFileLock.acquire(database_path)
    ownership.release()
    application.state.engine.dispose()


def test_nonowner_never_runs_recovery(database_path: Path) -> None:
    owner = create_app(settings_for(database_path))
    nonowner = create_app(settings_for(database_path))
    with TestClient(owner):
        add_request(
            owner.state.assistant_session_factory,
            request_id=REQUEST_ID,
            conversation_id=CONVERSATION_ID,
            status="accepted",
        )
        with TestClient(nonowner) as client:
            assert nonowner.state.assistant_runtime_owner is False
            response = client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
            assert response.json()["status"] == "accepted"
    owner.state.engine.dispose()
    nonowner.state.engine.dispose()


def test_private_runtime_runs_existing_process_one_and_never_writes_schedule(
    database_path: Path,
) -> None:
    provider = ScriptedDecisionProvider(
        [
            DecisionEnvelope.model_validate(
                {"decision": {"kind": "ask", "text": "몇 시인가요?"}}
            )
        ]
    )
    uuids = iter((CONVERSATION_ID, QUESTION_ID))
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=provider,
        _assistant_uuid_factory_for_tests=lambda: next(uuids),
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application) as client:
        accepted = client.post(
            "/api/v1/assistant/requests", json=request_payload()
        )
        assert accepted.status_code == 202

        def completed() -> bool:
            response = client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
            return response.json().get("status") == "completed"

        wait_until(completed)
        result = client.get(
            f"/api/v1/assistant/requests/{REQUEST_ID}"
        ).json()
        assert result["result"]["kind"] == "need_more_info"
        assert provider.call_count == 1
        assert application.state.assistant_runtime.snapshot().registered_count == 0
        with application.state.session_factory() as session:
            assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0
    application.state.engine.dispose()


class BlockingProvider:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.call_count = 0

    def decide(self, _context):
        self.call_count += 1
        self.entered.set()
        if not self.release.wait(5):
            raise AssertionError("test did not release provider")
        return DecisionEnvelope.model_validate(
            {"decision": {"kind": "ask", "text": "늦은 질문"}}
        )


def test_independent_sweeper_times_out_hung_provider_and_late_finalize_loses(
    database_path: Path,
) -> None:
    provider = BlockingProvider()
    monotonic = FixedMonotonicClock()
    uuids = iter((CONVERSATION_ID, QUESTION_ID))
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=provider,
        _assistant_uuid_factory_for_tests=lambda: next(uuids),
        _assistant_monotonic_clock_for_tests=monotonic,
        _assistant_shutdown_grace_seconds_for_tests=0.1,
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application, raise_server_exceptions=False) as client:
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload()
        ).status_code == 202
        assert provider.entered.wait(1)
        runtime = application.state.assistant_runtime
        monotonic.advance(300)
        runtime.wake()

        def timed_out() -> bool:
            with application.state.session_factory() as session:
                stored = session.get(AssistantRequest, REQUEST_ID)
                return (
                    stored is not None
                    and stored.status == "completed"
                    and stored.result_json["code"] == "PROCESSING_TIMEOUT"
                )

        wait_until(timed_out)
        snapshot = runtime.snapshot()
        assert snapshot.healthy is False
        assert snapshot.worker_alive is True
        assert snapshot.current_request_id == REQUEST_ID
        assert application.state.assistant_runtime_ready is False
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload(SECOND_REQUEST_ID)
        ).status_code == 503

        provider.release.set()
        wait_until(lambda: runtime.snapshot().current_request_id is None)
        assert runtime.snapshot().worker_alive is True
        assert runtime.snapshot().sweeper_alive is True
        assert sum(
            thread.name == "assistant-runtime-worker"
            for thread in enumerate_threads()
        ) == 1
        with application.state.session_factory() as session:
            stored = session.get(AssistantRequest, REQUEST_ID)
            assert stored.result_json["code"] == "PROCESSING_TIMEOUT"
            assert session.scalar(sa.select(sa.func.count()).select_from(AssistantQuestion)) == 0
            assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0
        assert provider.call_count == 1
    application.state.engine.dispose()


def test_sweeper_survives_unhealthy_and_enforces_staggered_deadlines(
    database_path: Path,
) -> None:
    provider = BlockingProvider()
    monotonic = FixedMonotonicClock()
    uuids = iter((CONVERSATION_ID, SECOND_CONVERSATION_ID, QUESTION_ID))
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=provider,
        _assistant_uuid_factory_for_tests=lambda: next(uuids),
        _assistant_monotonic_clock_for_tests=monotonic,
        _assistant_shutdown_grace_seconds_for_tests=0.1,
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application) as client:
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload()
        ).status_code == 202
        assert provider.entered.wait(1)
        monotonic.advance(100)
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload(SECOND_REQUEST_ID)
        ).status_code == 202
        runtime = application.state.assistant_runtime
        worker_thread = runtime._worker_thread
        wait_until(lambda: runtime.snapshot().registered_count == 2)

        monotonic.advance(199.999)
        runtime.wake()
        Event().wait(0.02)
        with application.state.session_factory() as session:
            assert session.get(AssistantRequest, REQUEST_ID).status == "processing"
            assert session.get(AssistantRequest, SECOND_REQUEST_ID).status == "accepted"

        monotonic.advance(0.001)
        runtime.wake()

        def first_timed_out() -> bool:
            with application.state.session_factory() as session:
                first = session.get(AssistantRequest, REQUEST_ID)
                second = session.get(AssistantRequest, SECOND_REQUEST_ID)
                return (
                    first.status == "completed"
                    and first.result_json["code"] == "PROCESSING_TIMEOUT"
                    and second.status == "accepted"
                )

        wait_until(first_timed_out)
        snapshot = runtime.snapshot()
        assert snapshot.healthy is False
        assert snapshot.sweeper_alive is True
        assert snapshot.worker_alive is True
        assert snapshot.current_request_id == REQUEST_ID
        assert snapshot.registered_count == 1
        assert runtime._worker_thread is worker_thread
        assert sum(
            thread.name == "assistant-runtime-worker"
            for thread in enumerate_threads()
        ) == 1

        monotonic.advance(99.999)
        runtime.wake()
        Event().wait(0.02)
        with application.state.assistant_session_factory() as session:
            assert session.get(AssistantRequest, SECOND_REQUEST_ID).status == "accepted"

        monotonic.advance(0.001)
        runtime.wake()

        def second_timed_out() -> bool:
            with application.state.session_factory() as session:
                value = session.get(AssistantRequest, SECOND_REQUEST_ID)
                return (
                    value is not None
                    and value.status == "completed"
                    and value.result_json["code"] == "PROCESSING_TIMEOUT"
                )

        wait_until(second_timed_out)
        assert runtime.snapshot().sweeper_alive is True
        assert runtime.snapshot().registered_count == 0
        assert provider.call_count == 1
        provider.release.set()
        wait_until(lambda: runtime.snapshot().current_request_id is None)
        assert runtime.snapshot().worker_alive is True
        assert runtime._worker_thread is worker_thread
        assert sum(
            thread.name == "assistant-runtime-worker"
            for thread in enumerate_threads()
        ) == 1
    application.state.engine.dispose()


def test_sweeper_failure_stops_new_claims_without_discarding_inflight_finalize(
    database_path: Path,
) -> None:
    provider = BlockingProvider()
    fail_sweeper = Event()

    def condition_waiter(condition, timeout):
        if (
            current_thread().name == "assistant-runtime-sweeper"
            and fail_sweeper.is_set()
        ):
            raise RuntimeError("injected sweeper failure")
        condition.wait(timeout=timeout)

    uuids = iter((CONVERSATION_ID, QUESTION_ID))
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=provider,
        _assistant_uuid_factory_for_tests=lambda: next(uuids),
        _assistant_condition_waiter_for_tests=condition_waiter,
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application) as client:
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload()
        ).status_code == 202
        assert provider.entered.wait(1)
        runtime = application.state.assistant_runtime

        fail_sweeper.set()
        runtime.wake()
        wait_until(lambda: not runtime.snapshot().sweeper_alive)
        assert runtime.snapshot().healthy is False
        assert runtime.snapshot().worker_alive is True

        provider.release.set()

        def normally_completed() -> bool:
            with application.state.assistant_session_factory() as session:
                stored = session.get(AssistantRequest, REQUEST_ID)
                return (
                    stored is not None
                    and stored.status == "completed"
                    and stored.result_json["kind"] == "need_more_info"
                )

        wait_until(normally_completed)
        wait_until(lambda: not runtime.snapshot().worker_alive)
        with application.state.assistant_session_factory() as session:
            assert session.scalar(
                sa.select(sa.func.count()).select_from(AssistantQuestion)
            ) == 1
        assert provider.call_count == 1
    application.state.engine.dispose()


def test_owner_get_lazy_timeout_but_nonowner_get_is_read_only(
    database_path: Path,
) -> None:
    monotonic = FixedMonotonicClock()
    owner = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=ScriptedDecisionProvider([]),
        _assistant_process_one_for_tests=(
            lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE
        ),
        _assistant_monotonic_clock_for_tests=monotonic,
        _assistant_uuid_factory_for_tests=lambda: CONVERSATION_ID,
    )
    owner.state.clock = FixedClock(FIXED_NOW)
    nonowner = create_app(settings_for(database_path))
    with TestClient(owner) as owner_client:
        assert owner_client.post(
            "/api/v1/assistant/requests", json=request_payload()
        ).status_code == 202
        wait_until(
            lambda: owner.state.assistant_runtime.snapshot().registered_count == 1
        )
        monotonic.advance(299.999)
        with TestClient(nonowner) as nonowner_client:
            response = nonowner_client.get(
                f"/api/v1/assistant/requests/{REQUEST_ID}"
            )
            assert response.status_code == 200
            assert response.json()["status"] == "accepted"

        before_deadline = owner_client.get(
            f"/api/v1/assistant/requests/{REQUEST_ID}"
        )
        assert before_deadline.status_code == 200
        assert before_deadline.json()["status"] == "accepted"

        monotonic.advance(0.001)
        response = owner_client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        assert response.json()["result"]["code"] == "PROCESSING_TIMEOUT"
    owner.state.engine.dispose()
    nonowner.state.engine.dispose()


def test_post_lazy_timeout_clears_blocking_request_before_admission(
    database_path: Path,
) -> None:
    monotonic = FixedMonotonicClock()
    uuids = iter((CONVERSATION_ID, SECOND_CONVERSATION_ID))
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=ScriptedDecisionProvider([]),
        _assistant_uuid_factory_for_tests=lambda: next(uuids),
        _assistant_process_one_for_tests=(
            lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE
        ),
        _assistant_monotonic_clock_for_tests=monotonic,
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application) as client:
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload()
        ).status_code == 202
        wait_until(
            lambda: application.state.assistant_runtime.snapshot().registered_count
            == 1
        )
        monotonic.advance(300)

        second = client.post(
            "/api/v1/assistant/requests",
            json=request_payload(
                SECOND_REQUEST_ID, conversation_id=CONVERSATION_ID
            ),
        )
        assert second.status_code == 202
        first = client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
        assert first.json()["result"]["code"] == "PROCESSING_TIMEOUT"
        assert application.state.assistant_runtime.snapshot().registered_count == 1
    application.state.engine.dispose()


def test_lazy_timeout_busy_does_not_replace_get_response(database_path: Path) -> None:
    monotonic = FixedMonotonicClock()
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=ScriptedDecisionProvider([]),
        _assistant_process_one_for_tests=(
            lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE
        ),
        _assistant_monotonic_clock_for_tests=monotonic,
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application) as client:
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload()
        ).status_code == 202
        wait_until(
            lambda: application.state.assistant_runtime.snapshot().registered_count
            == 1
        )
        monotonic.advance(300)
        locked = application.state.assistant_engine.connect()
        transaction = locked.begin()
        try:
            response = client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
        finally:
            transaction.rollback()
            locked.close()
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"

        completed = client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
        assert completed.json()["status"] == "completed"
        assert completed.json()["result"]["code"] == "PROCESSING_TIMEOUT"
    application.state.engine.dispose()


def test_sweeper_retries_busy_without_extending_deadline(database_path: Path) -> None:
    monotonic = FixedMonotonicClock()
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=ScriptedDecisionProvider([]),
        _assistant_process_one_for_tests=(
            lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE
        ),
        _assistant_monotonic_clock_for_tests=monotonic,
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application) as client:
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload()
        ).status_code == 202
        runtime = application.state.assistant_runtime
        wait_until(lambda: runtime.snapshot().registered_count == 1)
        locked = application.state.assistant_engine.connect()
        transaction = locked.begin()
        try:
            monotonic.advance(300)
            runtime.wake()
            Event().wait(0.05)
            with application.state.session_factory() as session:
                assert session.get(AssistantRequest, REQUEST_ID).status == "accepted"
            assert runtime.snapshot().registered_count == 1
        finally:
            transaction.rollback()
            locked.close()

        runtime.wake()

        def completed() -> bool:
            with application.state.session_factory() as session:
                return session.get(AssistantRequest, REQUEST_ID).status == "completed"

        wait_until(completed)
        with application.state.session_factory() as session:
            stored = session.get(AssistantRequest, REQUEST_ID)
            assert stored.result_json["code"] == "PROCESSING_TIMEOUT"
    application.state.engine.dispose()


def test_cancel_lazy_timeout_wins_before_cancel_judgement(database_path: Path) -> None:
    monotonic = FixedMonotonicClock()
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=ScriptedDecisionProvider([]),
        _assistant_process_one_for_tests=(
            lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE
        ),
        _assistant_monotonic_clock_for_tests=monotonic,
        _assistant_uuid_factory_for_tests=lambda: CONVERSATION_ID,
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application) as client:
        assert client.post(
            "/api/v1/assistant/requests", json=request_payload()
        ).status_code == 202
        wait_until(
            lambda: application.state.assistant_runtime.snapshot().registered_count
            == 1
        )
        monotonic.advance(300)
        response = client.post(
            f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel",
            json={"target": {"type": "request", "id": str(REQUEST_ID)}},
        )
        assert response.status_code == 200
        assert response.json()["targetState"] == "already_completed"
        status = client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
        assert status.json()["result"]["code"] == "PROCESSING_TIMEOUT"
    application.state.engine.dispose()


def test_registration_failure_returns_202_then_compensates_interrupted(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=ScriptedDecisionProvider([]),
        _assistant_process_one_for_tests=(
            lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE
        ),
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application) as client:
        runtime = application.state.assistant_runtime

        def fail_registration(**_kwargs):
            raise RuntimeError("injected")

        monkeypatch.setattr(runtime, "register_accepted", fail_registration)
        response = client.post(
            "/api/v1/assistant/requests", json=request_payload()
        )
        assert response.status_code == 202
        status = client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
        assert status.json()["status"] == "completed"
        assert status.json()["result"]["code"] == "PROCESSING_INTERRUPTED"
        assert runtime.snapshot().healthy is False
        assert application.state.assistant_runtime_ready is False
    application.state.engine.dispose()


def test_shutdown_registration_rejection_leaves_startup_recovery_target(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=ScriptedDecisionProvider([]),
        _assistant_process_one_for_tests=(
            lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE
        ),
    )
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application) as client:
        runtime = application.state.assistant_runtime
        monkeypatch.setattr(
            runtime,
            "register_accepted",
            lambda **_kwargs: RegistrationOutcome.SHUTTING_DOWN,
        )
        response = client.post(
            "/api/v1/assistant/requests", json=request_payload()
        )
        assert response.status_code == 202
        assert client.get(
            f"/api/v1/assistant/requests/{REQUEST_ID}"
        ).json()["status"] == "accepted"

    restarted = create_app(settings_for(database_path))
    restarted.state.clock = FixedClock(FIXED_NOW + timedelta(minutes=1))
    with TestClient(restarted) as client:
        status = client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
        assert status.json()["result"]["code"] == "PROCESSING_INTERRUPTED"
    application.state.engine.dispose()
    restarted.state.engine.dispose()


def test_blocked_provider_shutdown_is_bounded_and_defers_lock_release(
    database_path: Path,
) -> None:
    provider = BlockingProvider()
    uuids = iter((CONVERSATION_ID, QUESTION_ID))
    application = create_app(
        settings_for(database_path),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=provider,
        _assistant_uuid_factory_for_tests=lambda: next(uuids),
        _assistant_shutdown_grace_seconds_for_tests=0.05,
    )
    application.state.clock = FixedClock(FIXED_NOW)
    client = TestClient(application)
    client.__enter__()
    assert client.post(
        "/api/v1/assistant/requests", json=request_payload()
    ).status_code == 202
    assert provider.entered.wait(1)
    runtime = application.state.assistant_runtime

    started = time.monotonic()
    client.__exit__(None, None, None)
    elapsed = time.monotonic() - started
    assert elapsed < 0.5
    assert runtime.snapshot().worker_alive is True
    with pytest.raises(AssistantRuntimeLockContended):
        AssistantRuntimeFileLock.acquire(database_path)

    provider.release.set()
    wait_until(lambda: not runtime.snapshot().worker_alive)

    def lock_is_released() -> bool:
        try:
            ownership = AssistantRuntimeFileLock.acquire(database_path)
        except AssistantRuntimeLockContended:
            return False
        ownership.release()
        return True

    wait_until(lock_is_released)

    restarted = create_app(settings_for(database_path))
    restarted.state.clock = FixedClock(FIXED_NOW + timedelta(minutes=1))
    with TestClient(restarted) as restarted_client:
        assert restarted.state.assistant_runtime_owner is True
        status = restarted_client.get(
            f"/api/v1/assistant/requests/{REQUEST_ID}"
        )
        assert status.json()["result"]["code"] == "PROCESSING_INTERRUPTED"
        with restarted.state.session_factory() as session:
            assert session.scalar(sa.select(sa.func.count()).select_from(AssistantQuestion)) == 0
            assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0
    application.state.engine.dispose()
    restarted.state.engine.dispose()


def test_production_runtime_owner_still_rejects_new_post_without_insert(
    database_path: Path,
) -> None:
    application = create_app(settings_for(database_path))
    application.state.clock = FixedClock(FIXED_NOW)
    with TestClient(application, raise_server_exceptions=False) as client:
        assert application.state.assistant_runtime_owner is True
        assert application.state.assistant_runtime_healthy is True
        assert application.state.assistant_runtime_ready is False
        response = client.post(
            "/api/v1/assistant/requests", json=request_payload()
        )
        assert response.status_code == 503
        with application.state.session_factory() as session:
            assert session.scalar(sa.select(sa.func.count()).select_from(AssistantRequest)) == 0
            assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 0
    application.state.engine.dispose()
