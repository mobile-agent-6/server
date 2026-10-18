from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.database import create_assistant_engine, create_session_factory
from app.main import create_app
from app.modules.assistant import state_service
from app.modules.assistant.admission import AdmissionKind, AdmissionResult
from app.modules.assistant.model import AssistantConversation, AssistantRequest
from app.modules.assistant.processor import ProcessOneResult
from app.modules.assistant.schemas import AssistantRequestCreate
from app.modules.schedules.model import Schedule
from tests.fakes.assistant import ScriptedDecisionProvider
from tests.conftest import FIXED_NOW, alembic_config

REQUEST_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SECOND_REQUEST_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
CONVERSATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

GET_RESULTS = [
    {
        "kind": "need_more_info",
        "text": "Provide more information.",
        "questionId": "44444444-4444-4444-8444-444444444444",
    },
    {
        "kind": "candidate_selection",
        "text": "Choose one.",
        "questionId": "44444444-4444-4444-8444-444444444444",
        "expiresAt": "2026-09-13T05:00:00Z",
        "candidates": [
            {
                "candidateId": "A",
                "title": "A",
                "scheduledAt": "2026-09-13T06:00:00Z",
                "category": "INVENTORY",
            },
            {
                "candidateId": "B",
                "title": "B",
                "scheduledAt": "2026-09-13T07:00:00Z",
                "category": "RETURN",
            },
        ],
    },
    {
        "kind": "confirmation",
        "text": "Delete it?",
        "questionId": "44444444-4444-4444-8444-444444444444",
        "expiresAt": "2026-09-13T05:00:00Z",
        "operation": "delete",
        "target": {
            "id": "77777777-7777-4777-8777-777777777777",
            "title": "Target",
            "scheduledAt": "2026-09-13T06:00:00Z",
            "category": "RETURN",
            "completed": False,
            "updatedAt": "2026-09-13T04:00:00Z",
        },
    },
    {
        "kind": "outcome",
        "text": "Nothing matched.",
        "outcome": "no_match",
        "schedulesChanged": False,
    },
    {
        "kind": "failure",
        "text": "Try again later.",
        "code": "PROCESSING_INTERRUPTED",
        "nextAction": "retry_later",
    },
    {
        "kind": "uncertain",
        "text": "The result is unknown.",
        "code": "EXECUTION_RESULT_UNKNOWN",
        "nextAction": "check_status",
    },
]


def payload(*, request_id: str = REQUEST_ID, text: str = "재고 확인") -> dict[str, object]:
    return {
        "requestId": request_id,
        "input": {"type": "text", "text": text},
    }


@pytest.fixture
def ready_application(database_path: Path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    application = create_app(
        settings,
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=ScriptedDecisionProvider([]),
        _assistant_process_one_for_tests=(
            lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE
        ),
    )
    application.state.clock = FixedClock(FIXED_NOW)
    application.state.assistant_uuid_factory = lambda: CONVERSATION_ID
    yield application
    application.state.engine.dispose()


@pytest.fixture
def ready_client(ready_application):
    with TestClient(ready_application, raise_server_exceptions=False) as client:
        yield client


def assert_problem(response, status: int, code: str) -> dict[str, object]:
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["requestId"] == response.headers["X-Request-ID"]
    return body


def assistant_counts(client: TestClient) -> tuple[int, int]:
    with Session(client.app.state.engine) as session:
        return (
            int(
                session.scalar(
                    sa.select(sa.func.count()).select_from(AssistantConversation)
                )
                or 0
            ),
            int(
                session.scalar(sa.select(sa.func.count()).select_from(AssistantRequest))
                or 0
            ),
        )


def insert_completed_request(
    client: TestClient,
    *,
    request_id: UUID,
    conversation_id: UUID,
    result: dict[str, object],
) -> None:
    with client.app.state.assistant_session_factory.begin() as session:
        session.add(
            AssistantConversation(
                id=conversation_id,
                created_at=FIXED_NOW,
                last_valid_input_at=FIXED_NOW,
                expired_at=None,
                control_state_version=None,
                control_state_json=None,
            )
        )
        session.flush()
        uncertain = result["kind"] == "uncertain"
        session.add(
            AssistantRequest(
                id=request_id,
                conversation_id=conversation_id,
                fingerprint_version=1,
                fingerprint_hash="a" * 64,
                normalized_body_json={"requestId": str(request_id)},
                status="completed",
                result_kind=str(result["kind"]),
                result_json=result,
                accepted_at=FIXED_NOW,
                processing_at=FIXED_NOW,
                completed_at=FIXED_NOW,
                task_reference_accepted_at=FIXED_NOW,
                write_started=False,
                write_started_at=None,
                write_operation=None,
                write_commit_mode=None,
                pending_write_version=None,
                pending_write_json=None,
                create_idempotency_key=None,
                create_idempotency_scope=None,
                had_uncertain=uncertain,
                uncertain_at=(FIXED_NOW if uncertain else None),
                uncertain_acknowledged_at=None,
                uncertain_acknowledged_by_request_id=None,
                uncertain_resolved_at=None,
                retention_started_at=(None if uncertain else FIXED_NOW),
                tombstone=False,
                tombstoned_at=None,
                cancelled_at=None,
            )
        )


def test_ready_post_persists_accepted_and_get_is_read_only(
    ready_client: TestClient,
) -> None:
    accepted = ready_client.post("/api/v1/assistant/requests", json=payload())

    assert accepted.status_code == 202
    assert accepted.json() == {
        "requestId": REQUEST_ID,
        "conversationId": str(CONVERSATION_ID),
        "status": "accepted",
    }
    assert "Location" not in accepted.headers
    assert "Idempotent-Replayed" not in accepted.headers
    assert assistant_counts(ready_client) == (1, 1)

    with ready_client.app.state.engine.connect() as connection:
        before = tuple(
            connection.execute(
                sa.text("SELECT * FROM assistant_requests WHERE id = :id"),
                {"id": UUID(REQUEST_ID).hex},
            ).one()
        )
    status = ready_client.get(f"/api/v1/assistant/requests/{REQUEST_ID.upper()}")
    repeated = ready_client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
    with ready_client.app.state.engine.connect() as connection:
        after = tuple(
            connection.execute(
                sa.text("SELECT * FROM assistant_requests WHERE id = :id"),
                {"id": UUID(REQUEST_ID).hex},
            ).one()
        )

    assert status.status_code == repeated.status_code == 200
    assert status.json() == accepted.json()
    assert before == after


def test_post_fingerprint_is_computed_from_validated_request_dto(
    ready_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = state_service.request_fingerprint
    observed: list[AssistantRequestCreate] = []

    def inspect_fingerprint(data: AssistantRequestCreate):
        observed.append(data)
        return original(data)

    monkeypatch.setattr(state_service, "request_fingerprint", inspect_fingerprint)

    response = ready_client.post("/api/v1/assistant/requests", json=payload())

    assert response.status_code == 202
    assert len(observed) == 1
    assert isinstance(observed[0], AssistantRequestCreate)
    assert observed[0].request_id == UUID(REQUEST_ID)


def test_processing_get_omits_result(ready_client: TestClient) -> None:
    assert ready_client.post(
        "/api/v1/assistant/requests", json=payload()
    ).status_code == 202
    with ready_client.app.state.assistant_session_factory.begin() as session:
        request = session.get(AssistantRequest, UUID(REQUEST_ID))
        assert request is not None
        request.status = "processing"
        request.processing_at = FIXED_NOW

    response = ready_client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")

    assert response.status_code == 200
    assert response.json()["status"] == "processing"
    assert "result" not in response.json()


@pytest.mark.parametrize(("index", "result"), list(enumerate(GET_RESULTS, start=1)))
def test_completed_get_returns_each_strict_public_result(
    ready_client: TestClient,
    index: int,
    result: dict[str, object],
) -> None:
    request_id = UUID(f"00000000-0000-4000-8000-{index:012x}")
    conversation_id = UUID(f"10000000-0000-4000-8000-{index:012x}")
    insert_completed_request(
        ready_client,
        request_id=request_id,
        conversation_id=conversation_id,
        result=result,
    )

    response = ready_client.get(f"/api/v1/assistant/requests/{request_id}")

    assert response.status_code == 200
    assert response.json() == {
        "requestId": str(request_id),
        "conversationId": str(conversation_id),
        "status": "completed",
        "result": result,
    }


def test_corrupt_get_is_sanitized_internal_error(
    ready_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    request_id = UUID("00000000-0000-4000-8000-000000000099")
    conversation_id = UUID("10000000-0000-4000-8000-000000000099")
    insert_completed_request(
        ready_client,
        request_id=request_id,
        conversation_id=conversation_id,
        result=GET_RESULTS[3],
    )
    private_value = "private-corrupt-value"
    with ready_client.app.state.engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE assistant_requests SET result_json = :result WHERE id = :id"
            ),
            {
                "id": request_id.hex,
                "result": (
                    '{"kind":"failure","text":"'
                    + private_value
                    + '","code":"MODEL_TIMEOUT","nextAction":"retry_later"}'
                ),
            },
        )

    response = ready_client.get(f"/api/v1/assistant/requests/{request_id}")

    assert_problem(response, 500, "INTERNAL_ERROR")
    assert private_value not in response.text
    assert private_value not in caplog.text


def test_get_database_unavailable_is_sanitized_and_keeps_logical_id(
    ready_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_value = "private-database-failure"

    def unavailable_factory():
        original = sqlite3.OperationalError(private_value)
        original.sqlite_errorcode = sqlite3.SQLITE_BUSY
        raise sa.exc.OperationalError("select", {}, original)

    monkeypatch.setattr(
        ready_client.app.state, "session_factory", unavailable_factory
    )

    response = ready_client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")

    body = assert_problem(response, 503, "DATABASE_UNAVAILABLE")
    assert body["assistantRequestId"] == REQUEST_ID
    assert private_value not in response.text
    assert private_value not in caplog.text


def test_replay_and_conflict_precede_disabled_readiness_and_conversation_state(
    ready_client: TestClient,
) -> None:
    assert ready_client.post(
        "/api/v1/assistant/requests", json=payload()
    ).status_code == 202
    with ready_client.app.state.assistant_session_factory.begin() as session:
        conversation = session.get(AssistantConversation, CONVERSATION_ID)
        assert conversation is not None
        conversation.expired_at = FIXED_NOW
    ready_client.app.state.assistant_runtime_ready = False

    replay = ready_client.post("/api/v1/assistant/requests", json=payload())
    conflict = ready_client.post(
        "/api/v1/assistant/requests",
        json=payload(text="different"),
    )
    new_request = ready_client.post(
        "/api/v1/assistant/requests",
        json=payload(request_id=SECOND_REQUEST_ID),
    )

    assert replay.status_code == 200
    assert replay.json()["status"] == "accepted"
    assert "Location" not in replay.headers
    assert "Idempotent-Replayed" not in replay.headers
    conflict_body = assert_problem(conflict, 409, "REQUEST_ID_CONFLICT")
    assert conflict_body["assistantRequestId"] == REQUEST_ID
    unavailable = assert_problem(new_request, 503, "ASSISTANT_UNAVAILABLE")
    assert unavailable["assistantRequestId"] == SECOND_REQUEST_ID
    assert assistant_counts(ready_client) == (1, 1)


@pytest.mark.parametrize(
    ("kind", "status", "code", "extra"),
    [
        (AdmissionKind.ASSISTANT_UNAVAILABLE, 503, "ASSISTANT_UNAVAILABLE", {}),
        (AdmissionKind.REQUEST_ID_CONFLICT, 409, "REQUEST_ID_CONFLICT", {}),
        (AdmissionKind.REQUEST_EXPIRED, 409, "REQUEST_EXPIRED", {}),
        (
            AdmissionKind.CONVERSATION_NOT_FOUND,
            404,
            "ASSISTANT_CONVERSATION_NOT_FOUND",
            {},
        ),
        (
            AdmissionKind.CONVERSATION_EXPIRED,
            409,
            "CONVERSATION_EXPIRED",
            {},
        ),
        (
            AdmissionKind.CONVERSATION_BUSY,
            409,
            "CONVERSATION_BUSY",
            {"blockingRequestId": SECOND_REQUEST_ID},
        ),
        (
            AdmissionKind.STALE_QUESTION,
            409,
            "STALE_QUESTION",
            {
                "activeQuestionId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "activeQuestionRequestId": SECOND_REQUEST_ID,
            },
        ),
        (AdmissionKind.QUESTION_EXPIRED, 409, "QUESTION_EXPIRED", {}),
        (
            AdmissionKind.UNRESOLVED_UNCERTAIN,
            409,
            "UNRESOLVED_UNCERTAIN",
            {"blockingRequestId": SECOND_REQUEST_ID},
        ),
        (
            AdmissionKind.ACKNOWLEDGEMENT_NOT_APPLICABLE,
            409,
            "ACKNOWLEDGEMENT_NOT_APPLICABLE",
            {},
        ),
    ],
)
def test_all_admission_failures_map_to_exact_http_problem(
    ready_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    kind: AdmissionKind,
    status: int,
    code: str,
    extra: dict[str, str],
) -> None:
    def fake_admission(*args, **kwargs):
        del args, kwargs
        return AdmissionResult(
            kind=kind,
            request_id=UUID(REQUEST_ID),
            conversation_id=CONVERSATION_ID,
            blocking_request_id=(
                UUID(SECOND_REQUEST_ID)
                if "blockingRequestId" in extra
                else None
            ),
            active_question_id=(
                UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
                if "activeQuestionId" in extra
                else None
            ),
            active_question_request_id=(
                UUID(SECOND_REQUEST_ID)
                if "activeQuestionRequestId" in extra
                else None
            ),
        )

    monkeypatch.setattr(state_service, "admit_request", fake_admission)
    response = ready_client.post("/api/v1/assistant/requests", json=payload())

    body = assert_problem(response, status, code)
    assert body["assistantRequestId"] == REQUEST_ID
    for field, value in extra.items():
        assert body[field] == value
    optional = {
        "blockingRequestId",
        "activeQuestionId",
        "activeQuestionRequestId",
    }
    assert optional.intersection(body) == set(extra)


def test_tombstone_post_and_get_use_distinct_request_expired_statuses(
    ready_client: TestClient,
) -> None:
    assert ready_client.post(
        "/api/v1/assistant/requests", json=payload()
    ).status_code == 202
    cancel = ready_client.post(
        f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel",
        json={"target": {"type": "request", "id": REQUEST_ID}},
    )
    assert cancel.status_code == 200
    with ready_client.app.state.assistant_session_factory.begin() as session:
        request = session.get(AssistantRequest, UUID(REQUEST_ID))
        assert request is not None
        request.tombstone = True
        request.tombstoned_at = FIXED_NOW
        request.result_kind = None
        request.result_json = None

    post = ready_client.post("/api/v1/assistant/requests", json=payload(text="changed"))
    get = ready_client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")

    assert_problem(post, 409, "REQUEST_EXPIRED")
    assert_problem(get, 410, "REQUEST_EXPIRED")
    assert post.json()["assistantRequestId"] == REQUEST_ID
    assert get.json()["assistantRequestId"] == REQUEST_ID


def test_cancelled_request_is_visible_through_original_get(
    ready_client: TestClient,
) -> None:
    assert ready_client.post(
        "/api/v1/assistant/requests", json=payload()
    ).status_code == 202

    response = ready_client.post(
        f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel",
        json={"target": {"type": "request", "id": REQUEST_ID}},
    )
    status = ready_client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")

    assert response.status_code == 200
    assert response.json()["targetState"] == "cancelled"
    assert response.json()["text"]
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["result"] == {
        "kind": "outcome",
        "text": "The Assistant request was cancelled.",
        "outcome": "cancelled",
        "schedulesChanged": False,
    }


def test_missing_get_conversation_and_target_have_exact_problem_codes(
    ready_client: TestClient,
) -> None:
    missing_get = ready_client.get(
        "/api/v1/assistant/requests/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    )
    missing_conversation = ready_client.post(
        "/api/v1/assistant/conversations/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/cancel",
        json={"target": {"type": "request", "id": REQUEST_ID}},
    )
    assert_problem(missing_get, 404, "ASSISTANT_REQUEST_NOT_FOUND")
    assert_problem(
        missing_conversation, 404, "ASSISTANT_CONVERSATION_NOT_FOUND"
    )

    assert ready_client.post(
        "/api/v1/assistant/requests", json=payload()
    ).status_code == 202
    missing_target = ready_client.post(
        f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel",
        json={
            "target": {
                "type": "request",
                "id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            }
        },
    )
    assert_problem(missing_target, 404, "ASSISTANT_TARGET_NOT_FOUND")


def test_concurrent_same_id_api_post_is_one_accept_and_one_replay(
    ready_client: TestClient,
) -> None:
    barrier = Barrier(2)

    def post_once() -> int:
        barrier.wait(timeout=5)
        return ready_client.post(
            "/api/v1/assistant/requests", json=payload()
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _: post_once(), range(2)))

    assert sorted(statuses) == [200, 202]
    assert assistant_counts(ready_client) == (1, 1)


def test_api_database_lock_is_503_and_leaves_no_rows(
    ready_client: TestClient,
) -> None:
    settings = ready_client.app.state.settings
    locked_connection = ready_client.app.state.assistant_engine.connect()
    transaction = locked_connection.begin()
    fast_engine = create_assistant_engine(settings, busy_timeout_ms=0)
    ready_client.app.state.assistant_session_factory = create_session_factory(fast_engine)
    try:
        response = ready_client.post(
            "/api/v1/assistant/requests", json=payload()
        )
    finally:
        transaction.rollback()
        locked_connection.close()
        fast_engine.dispose()

    body = assert_problem(response, 503, "DATABASE_UNAVAILABLE")
    assert body["assistantRequestId"] == REQUEST_ID
    assert assistant_counts(ready_client) == (0, 0)


@pytest.mark.parametrize("database_kind", ["missing", "empty", "old"])
def test_schema_not_ready_does_not_block_app_and_isolates_assistant(
    tmp_path: Path, database_kind: str
) -> None:
    database = tmp_path / f"{database_kind}.db"
    if database_kind == "empty":
        sqlite3.connect(database).close()
    elif database_kind == "old":
        command.upgrade(alembic_config(database), "20261017_0003")
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database}")
    application = create_app(settings)
    application.state.clock = FixedClock(FIXED_NOW)

    with TestClient(application, raise_server_exceptions=False) as client:
        post = client.post("/api/v1/assistant/requests", json=payload())
        get = client.get(f"/api/v1/assistant/requests/{REQUEST_ID}")
        cancel = client.post(
            f"/api/v1/assistant/conversations/{CONVERSATION_ID}/cancel",
            json={"target": {"type": "request", "id": REQUEST_ID}},
        )
        health = client.get("/health")
        assert_problem(post, 503, "ASSISTANT_UNAVAILABLE")
        assert_problem(get, 503, "ASSISTANT_UNAVAILABLE")
        assert_problem(cancel, 503, "ASSISTANT_UNAVAILABLE")
        assert_problem(health, 503, "DATABASE_UNAVAILABLE")
        assert application.state.assistant_schema_ready is False
        assert application.state.assistant_runtime_ready is False
        assert application.state.assistant_provider_available is False
        assert not hasattr(application.state, "assistant_worker")
        if database_kind == "old":
            schedule = client.post(
                "/api/v1/schedules",
                headers={"Idempotency-Key": "old-schema-schedule-write"},
                json={
                    "title": "Old schema still serves Schedule",
                    "scheduledAt": "2026-09-13T05:00:00Z",
                    "category": "INVENTORY",
                },
            )
            assert schedule.status_code == 201
            with Session(application.state.engine) as session:
                assert session.scalar(sa.select(sa.func.count()).select_from(Schedule)) == 1
    application.state.engine.dispose()


def test_lifespan_starts_runtime_and_disposes_assistant_engine_on_shutdown(
    ready_application, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispose_calls: list[bool] = []
    with TestClient(ready_application) as client:
        engine = client.app.state.assistant_engine
        original_dispose = engine.dispose

        def dispose(*args, **kwargs):
            dispose_calls.append(True)
            return original_dispose(*args, **kwargs)

        monkeypatch.setattr(engine, "dispose", dispose)
        assert client.app.state.assistant_schema_ready is True
        assert client.app.state.assistant_runtime_ready is True
        assert client.app.state.assistant_provider_available is True
        snapshot = client.app.state.assistant_runtime.snapshot()
        assert snapshot.owner is True
        assert snapshot.healthy is True
        assert snapshot.worker_alive is True
        assert snapshot.sweeper_alive is True
        for name in ("assistant_worker", "assistant_thread", "assistant_file_lock"):
            assert not hasattr(client.app.state, name)

    assert dispose_calls == [True]
    assert ready_application.state.assistant_engine is None
    assert ready_application.state.assistant_session_factory is None
    assert ready_application.state.assistant_schema_ready is False
    assert ready_application.state.assistant_runtime_ready is False
    assert ready_application.state.assistant_provider_available is False
