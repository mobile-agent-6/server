from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.clock import FixedClock
from app.core.config import Settings
from app.core.database import create_api_engine, create_session_factory
from app.main import create_app
from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
)
from app.modules.assistant.processor import ProcessOneResult
from app.modules.assistant.schedule_read import (
    ScheduleReadUnavailable,
    SqlAlchemyScheduleReader,
)
from app.modules.schedules import repository as schedule_repository
from app.modules.schedules.category import Category
from app.modules.schedules.model import Schedule
from app.modules.schedules.service import list_schedules
from tests.conftest import FIXED_NOW
from tests.fakes.assistant import ScriptedDecisionProvider

CONVERSATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SOURCE_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
QUESTION_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
TARGET_ID = UUID("77777777-7777-4777-8777-777777777777")
SECOND_TARGET_ID = UUID("88888888-8888-4888-8888-888888888888")


def make_app(
    database_path: Path,
    *,
    runtime_ready: bool,
    provider=None,
):
    application = create_app(
        Settings(_env_file=None, database_url=f"sqlite:///{database_path}"),
        _assistant_runtime_ready_for_tests=runtime_ready,
        _assistant_decision_provider_for_tests=provider,
        _assistant_process_one_for_tests=(
            lambda _request_id, _guard: ProcessOneResult.PENDING_WRITE
        ),
    )
    application.state.clock = FixedClock(FIXED_NOW)
    application.state.assistant_uuid_factory = lambda: UUID(
        "99999999-9999-4999-8999-999999999999"
    )
    return application


def add_question_state(
    application,
    *,
    kind: str,
    intents: object,
) -> None:
    if kind == "candidate_selection":
        result = {
            "kind": kind,
            "text": "선택하세요.",
            "questionId": str(QUESTION_ID),
            "expiresAt": "2026-09-13T04:40:00Z",
            "candidates": [
                {
                    "candidateId": "opaque-a",
                    "title": "재고 확인",
                    "scheduledAt": "2026-09-13T05:30:00Z",
                    "category": "INVENTORY",
                },
                {
                    "candidateId": "opaque-b",
                    "title": "반품 확인",
                    "scheduledAt": "2026-09-13T06:30:00Z",
                    "category": "RETURN",
                },
            ],
        }
        candidates = result["candidates"]
    else:
        result = {
            "kind": kind,
            "text": "삭제할까요?",
            "questionId": str(QUESTION_ID),
            "expiresAt": "2026-09-13T04:40:00Z",
            "operation": "delete",
            "target": {
                "id": str(TARGET_ID),
                "title": "재고 확인",
                "scheduledAt": "2026-09-13T05:30:00Z",
                "category": "INVENTORY",
                "completed": False,
                "updatedAt": "2026-09-13T04:00:00Z",
            },
        }
        candidates = None
    with application.state.assistant_session_factory.begin() as session:
        session.add(
            AssistantConversation(
                id=CONVERSATION_ID,
                created_at=FIXED_NOW - timedelta(minutes=30),
                last_valid_input_at=FIXED_NOW - timedelta(minutes=10),
                expired_at=None,
                control_state_version=None,
                control_state_json=None,
            )
        )
        session.add(
            AssistantRequest(
                id=SOURCE_ID,
                conversation_id=CONVERSATION_ID,
                fingerprint_version=1,
                fingerprint_hash="a" * 64,
                normalized_body_json={"requestId": str(SOURCE_ID)},
                status="completed",
                result_kind=kind,
                result_json=result,
                accepted_at=FIXED_NOW - timedelta(minutes=20),
                processing_at=FIXED_NOW - timedelta(minutes=19),
                completed_at=FIXED_NOW - timedelta(minutes=18),
                task_reference_accepted_at=FIXED_NOW - timedelta(minutes=20),
                write_started=False,
                write_started_at=None,
                write_operation=None,
                write_commit_mode=None,
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
        session.flush()
        session.add(
            AssistantQuestion(
                id=QUESTION_ID,
                conversation_id=CONVERSATION_ID,
                source_request_id=SOURCE_ID,
                kind=kind,
                payload_json=result,
                candidates_json=candidates,
                candidate_intents_json=intents,
                status=AssistantQuestionStatus.ACTIVE.value,
                created_at=FIXED_NOW - timedelta(minutes=18),
                expires_at=FIXED_NOW + timedelta(minutes=10),
                deactivated_at=None,
                answered_by_request_id=None,
            )
        )


def selection_payload(request_id: str, candidate_id: str = "opaque-a"):
    return {
        "requestId": request_id,
        "conversationId": str(CONVERSATION_ID),
        "input": {
            "type": "selection",
            "questionId": str(QUESTION_ID),
            "candidateId": candidate_id,
        },
    }


def confirmation_payload(request_id: str, approved: bool = True):
    return {
        "requestId": request_id,
        "conversationId": str(CONVERSATION_ID),
        "input": {
            "type": "confirmation",
            "questionId": str(QUESTION_ID),
            "approved": approved,
        },
    }


def request_count(application) -> int:
    with application.state.assistant_session_factory() as session:
        return int(
            session.scalar(sa.select(sa.func.count()).select_from(AssistantRequest))
            or 0
        )


def test_runtime_false_precedes_conversation_validation_and_writes_nothing(
    database_path: Path,
) -> None:
    application = make_app(
        database_path,
        runtime_ready=False,
        provider=ScriptedDecisionProvider([]),
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/assistant/requests",
            json={
                "requestId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "conversationId": str(CONVERSATION_ID),
                "input": {"type": "text", "text": "재고 확인"},
            },
        )
        assert response.status_code == 503
        assert response.json()["code"] == "ASSISTANT_UNAVAILABLE"
        assert request_count(application) == 0
    application.state.engine.dispose()


def test_text_requires_provider_before_insert(database_path: Path) -> None:
    application = make_app(database_path, runtime_ready=True, provider=None)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/assistant/requests",
            json={
                "requestId": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                "input": {"type": "text", "text": "재고 확인"},
            },
        )
        assert response.status_code == 503
        assert request_count(application) == 0
    application.state.engine.dispose()


def test_complete_selection_is_admitted_without_provider(database_path: Path) -> None:
    application = make_app(database_path, runtime_ready=True, provider=None)
    with TestClient(application, raise_server_exceptions=False) as client:
        add_question_state(
            application,
            kind="candidate_selection",
            intents={
                "version": 1,
                "candidates": {
                    "opaque-a": {
                        "version": 1,
                        "operation": "delete",
                        "targetScheduleId": str(TARGET_ID),
                        "expectedUpdatedAt": "2026-09-13T04:00:00Z",
                        "timeInterpretation": "absolute",
                    }
                },
            },
        )
        response = client.post(
            "/api/v1/assistant/requests",
            json=selection_payload("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        )
        assert response.status_code == 202
        assert request_count(application) == 2
    application.state.engine.dispose()


@pytest.mark.parametrize(
    "corruption",
    ["missing_version", "unsupported_version", "missing_intent"],
)
def test_issued_selection_requires_complete_versioned_intent_snapshot(
    database_path: Path, corruption: str
) -> None:
    provider = ScriptedDecisionProvider([])
    application = make_app(database_path, runtime_ready=True, provider=provider)
    snapshot = {
        "version": 1,
        "candidates": {
            "opaque-a": {
                "version": 1,
                "operation": "delete",
                "targetScheduleId": str(TARGET_ID),
                "expectedUpdatedAt": "2026-09-13T04:00:00Z",
                "timeInterpretation": "absolute",
            }
        },
    }
    if corruption == "missing_version":
        snapshot.pop("version")
    elif corruption == "unsupported_version":
        snapshot["version"] = 2
    else:
        snapshot["candidates"] = {}

    with TestClient(application, raise_server_exceptions=False) as client:
        add_question_state(
            application,
            kind="candidate_selection",
            intents=snapshot,
        )
        response = client.post(
            "/api/v1/assistant/requests",
            json=selection_payload("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        )
        assert response.status_code == 503
        assert response.json()["code"] == "ASSISTANT_UNAVAILABLE"
        assert request_count(application) == 1
        assert provider.call_count == 0
        with application.state.assistant_session_factory() as session:
            question = session.get(AssistantQuestion, QUESTION_ID)
            assert question is not None
            assert question.status == "active"
            assert question.answered_by_request_id is None
    application.state.engine.dispose()


def test_invalid_candidate_precedes_missing_provider_and_keeps_question_active(
    database_path: Path,
) -> None:
    application = make_app(database_path, runtime_ready=True, provider=None)
    with TestClient(application, raise_server_exceptions=False) as client:
        add_question_state(
            application,
            kind="candidate_selection",
            intents={
                "version": 1,
                "candidates": {
                    "opaque-a": {
                        "version": 1,
                        "operation": "delete",
                        "targetScheduleId": str(TARGET_ID),
                        "expectedUpdatedAt": "2026-09-13T04:00:00Z",
                        "timeInterpretation": "absolute",
                    }
                },
            },
        )
        response = client.post(
            "/api/v1/assistant/requests",
            json=selection_payload(
                "dddddddd-dddd-4ddd-8ddd-dddddddddddd", "not-issued"
            ),
        )
        assert response.status_code == 409
        assert response.json()["code"] == "STALE_QUESTION"
        assert request_count(application) == 1
        with application.state.assistant_session_factory() as session:
            question = session.get(AssistantQuestion, QUESTION_ID)
            assert question is not None and question.status == "active"
    application.state.engine.dispose()


def test_incomplete_selection_is_never_reinterpreted_by_provider(
    database_path: Path,
) -> None:
    provider = ScriptedDecisionProvider([])
    application = make_app(database_path, runtime_ready=True, provider=None)
    with TestClient(application, raise_server_exceptions=False) as client:
        add_question_state(
            application,
            kind="candidate_selection",
            intents={
                "version": 1,
                "candidates": {"opaque-a": {"operation": "update"}},
            },
        )
        first = client.post(
            "/api/v1/assistant/requests",
            json=selection_payload("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        )
        assert first.status_code == 503
        assert request_count(application) == 1
        application.state.assistant_decision_provider = provider
        application.state.assistant_provider_available = True
        second = client.post(
            "/api/v1/assistant/requests",
            json=selection_payload("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        )
        assert second.status_code == 503
        assert request_count(application) == 1
        assert provider.call_count == 0
        with application.state.assistant_session_factory() as session:
            question = session.get(AssistantQuestion, QUESTION_ID)
            assert question is not None
            assert question.status == "active"
            assert question.answered_by_request_id is None
    application.state.engine.dispose()


@pytest.mark.parametrize("provider_present", [False, True])
def test_incomplete_confirmation_is_never_reinterpreted_by_provider(
    database_path: Path, provider_present: bool
) -> None:
    provider = ScriptedDecisionProvider([]) if provider_present else None
    application = make_app(
        database_path,
        runtime_ready=True,
        provider=provider,
    )
    with TestClient(application, raise_server_exceptions=False) as client:
        add_question_state(
            application,
            kind="confirmation",
            intents={"version": 1, "intent": {"operation": "delete"}},
        )
        response = client.post(
            "/api/v1/assistant/requests",
            json=confirmation_payload("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        )
        assert response.status_code == 503
        assert request_count(application) == 1
        if provider is not None:
            assert provider.call_count == 0
    application.state.engine.dispose()


def test_complete_confirmation_is_admitted_without_provider(database_path: Path) -> None:
    application = make_app(database_path, runtime_ready=True, provider=None)
    with TestClient(application, raise_server_exceptions=False) as client:
        add_question_state(
            application,
            kind="confirmation",
            intents={
                "version": 1,
                "intent": {
                    "version": 1,
                    "operation": "delete",
                    "targetScheduleId": str(TARGET_ID),
                    "expectedUpdatedAt": "2026-09-13T04:00:00Z",
                    "timeInterpretation": "absolute",
                },
            },
        )
        response = client.post(
            "/api/v1/assistant/requests",
            json=confirmation_payload("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        )
        assert response.status_code == 202
        assert request_count(application) == 2
    application.state.engine.dispose()


def test_sqlalchemy_schedule_reader_limits_101_and_preserves_schedule_api_semantics(
    database_path: Path,
) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_api_engine(settings)
    factory = create_session_factory(engine)
    schedules = [
        Schedule(
            id=UUID(f"70000000-0000-4000-8000-{index:012x}"),
            title=f"일정 {index}",
            scheduled_at=FIXED_NOW + timedelta(minutes=index + 1),
            category=Category.INVENTORY,
            completed=index % 2 == 0,
            created_at=FIXED_NOW + timedelta(seconds=index),
            updated_at=FIXED_NOW + timedelta(seconds=index),
            deleted_at=None,
        )
        for index in range(105)
    ]
    with factory.begin() as session:
        session.add_all(schedules)

    reader = SqlAlchemyScheduleReader(factory)
    limited = reader.list(
        from_utc=FIXED_NOW,
        to_utc=FIXED_NOW + timedelta(days=1),
        category=None,
        completed=None,
        limit=101,
    )
    with factory() as session:
        public = list_schedules(
            session,
            from_utc=FIXED_NOW,
            to_utc=FIXED_NOW + timedelta(days=1),
            category=None,
            completed=None,
        )

    assert len(limited) == 101
    assert len(public.items) == 105
    assert [item.id for item in limited] == [item.id for item in schedules[:101]]
    assert reader.get(schedules[0].id) == limited[0]
    assert reader.get(TARGET_ID) is None
    assert not hasattr(reader, "create")
    assert not hasattr(reader, "update")
    assert not hasattr(reader, "delete")
    with pytest.raises(FrozenInstanceError):
        limited[0].title = "변경"  # type: ignore[misc]
    engine.dispose()


def test_sqlalchemy_schedule_reader_filters_title_before_limit(
    database_path: Path,
) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_api_engine(settings)
    factory = create_session_factory(engine)
    unrelated = [
        Schedule(
            id=UUID(f"71000000-0000-4000-8000-{index:012x}"),
            title=f"다른 일정 {index}",
            scheduled_at=FIXED_NOW + timedelta(minutes=index + 1),
            category=Category.OTHER,
            completed=False,
            created_at=FIXED_NOW + timedelta(seconds=index),
            updated_at=FIXED_NOW,
            deleted_at=None,
        )
        for index in range(101)
    ]
    matching = Schedule(
        id=TARGET_ID,
        title="출고 검량 확인",
        scheduled_at=FIXED_NOW + timedelta(hours=4),
        category=Category.OUTBOUND,
        completed=False,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
        deleted_at=None,
    )
    with factory.begin() as session:
        session.add_all([*unrelated, matching])

    reader = SqlAlchemyScheduleReader(factory)
    rows = reader.list(
        from_utc=FIXED_NOW,
        to_utc=FIXED_NOW + timedelta(days=1),
        category=None,
        completed=None,
        limit=101,
        title="출고검량 확인",
    )

    assert [row.id for row in rows] == [TARGET_ID]
    engine.dispose()


@pytest.mark.parametrize(
    "overrides",
    [
        {"from_utc": FIXED_NOW.replace(tzinfo=None)},
        {"to_utc": FIXED_NOW},
        {"to_utc": FIXED_NOW + timedelta(days=93)},
        {"limit": 0},
        {"limit": 102},
    ],
)
def test_schedule_reader_rejects_invalid_range_and_limit(
    database_path: Path, overrides: dict[str, object]
) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_api_engine(settings)
    reader = SqlAlchemyScheduleReader(create_session_factory(engine))
    arguments: dict[str, object] = {
        "from_utc": FIXED_NOW,
        "to_utc": FIXED_NOW + timedelta(days=1),
        "category": None,
        "completed": None,
        "limit": 101,
    }
    arguments.update(overrides)

    with pytest.raises(ValueError):
        reader.list(**arguments)  # type: ignore[arg-type]
    engine.dispose()


def test_schedule_reader_sanitizes_sqlalchemy_failures(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_api_engine(settings)
    reader = SqlAlchemyScheduleReader(create_session_factory(engine))

    def fail(*args, **kwargs):
        del args, kwargs
        raise sa.exc.OperationalError("private statement", {}, Exception("private"))

    monkeypatch.setattr(schedule_repository, "get_active_by_id", fail)
    with pytest.raises(ScheduleReadUnavailable, match="Schedule read failed"):
        reader.get(TARGET_ID)
    monkeypatch.setattr(schedule_repository, "list_active", fail)
    with pytest.raises(ScheduleReadUnavailable, match="Schedule read failed"):
        reader.list(
            from_utc=FIXED_NOW,
            to_utc=FIXED_NOW + timedelta(days=1),
            category=None,
            completed=None,
            limit=101,
        )
    engine.dispose()
