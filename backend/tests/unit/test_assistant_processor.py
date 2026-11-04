from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
import sqlalchemy as sa

from app.core.clock import FixedClock, FixedMonotonicClock
from app.core.config import Settings
from app.core.database import create_assistant_engine, create_session_factory
from app.modules.assistant import repository
from app.modules.assistant.admission import admit_request, request_fingerprint
from app.modules.assistant.decisions import (
    DecisionContext,
    DecisionEnvelope,
    DecisionProviderInvalidOutput,
    DecisionProviderTimeout,
)
from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
)
from app.modules.assistant.evidence.normalize import title_contains
from app.modules.assistant.processor import ProcessOneResult, process_one
from app.modules.assistant.runtime_guard import ProcessDeadlineGuard
from app.modules.assistant.schedule_read import (
    ScheduleRead,
    ScheduleReadPort,
    ScheduleReadUnavailable,
)
from app.modules.assistant.schemas import AssistantCancelTarget, AssistantRequestCreate
from app.modules.assistant.state_service import cancel_assistant_target
from app.modules.assistant.serialization import SNAPSHOT_MAX_BYTES, serialize_snapshot
from app.modules.schedules.category import Category
from tests.fakes.assistant import ScriptedDecisionProvider

NOW = datetime(2026, 10, 18, 5, 0, tzinfo=timezone.utc)
REQUEST_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CONVERSATION_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SCHEDULE_A = UUID("77777777-7777-4777-8777-777777777777")
SCHEDULE_B = UUID("88888888-8888-4888-8888-888888888888")


class TrustSource:
    def __init__(self, trusted: bool) -> None:
        self.trusted = trusted

    def is_trusted(self) -> bool:
        return self.trusted


class UuidSequence:
    def __init__(self) -> None:
        self.counter = 1

    def __call__(self) -> UUID:
        value = UUID(f"99999999-9999-4999-8999-{self.counter:012x}")
        self.counter += 1
        return value


class AdvancingClock:
    def __init__(
        self,
        start: datetime,
        step: timedelta,
        events: list[str] | None = None,
    ) -> None:
        self.current = start
        self.step = step
        self.calls: list[datetime] = []
        self.events = events

    def now(self) -> datetime:
        value = self.current
        self.calls.append(value)
        if self.events is not None:
            self.events.append("clock")
        self.current += self.step
        return value


class MemoryScheduleReader:
    def __init__(self, schedules: list[ScheduleRead] | None = None) -> None:
        self.schedules = {item.id: item for item in schedules or []}
        self.get_calls: list[UUID] = []
        self.list_limits: list[int] = []
        self.list_titles: list[str | None] = []

    def get(self, schedule_id: UUID) -> ScheduleRead | None:
        self.get_calls.append(schedule_id)
        return self.schedules.get(schedule_id)

    def list(
        self,
        *,
        from_utc: datetime,
        to_utc: datetime,
        category: Category | None,
        completed: bool | None,
        limit: int,
        title: str | None = None,
    ) -> list[ScheduleRead]:
        self.list_limits.append(limit)
        self.list_titles.append(title)
        values = [
            item
            for item in self.schedules.values()
            if from_utc <= item.scheduled_at < to_utc
            and (category is None or item.category is category)
            and (completed is None or item.completed is completed)
            and (title is None or title_contains(title, item.title))
        ]
        return sorted(values, key=lambda item: (item.scheduled_at, item.id))[:limit]


class FailingScheduleReader(MemoryScheduleReader):
    def get(self, schedule_id: UUID) -> ScheduleRead | None:
        del schedule_id
        raise ScheduleReadUnavailable("private database detail")


class BlockingScheduleReader(MemoryScheduleReader):
    def __init__(self, schedule: ScheduleRead, entered: Event, release: Event) -> None:
        super().__init__([schedule])
        self.entered = entered
        self.release = release

    def get(self, schedule_id: UUID) -> ScheduleRead | None:
        self.entered.set()
        assert self.release.wait(timeout=3)
        return super().get(schedule_id)


class PendingSafeWriter:
    def execute(self, *, request_id, runtime_guard):
        del request_id, runtime_guard
        return ProcessOneResult.PENDING_WRITE


@pytest.fixture
def processor_store(database_path: Path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    engine = create_assistant_engine(settings)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def schedule(
    identifier: UUID = SCHEDULE_A,
    *,
    title: str = "재고 확인",
    at: datetime = NOW + timedelta(hours=1),
    category: Category = Category.INVENTORY,
    completed: bool = False,
    updated_at: datetime = NOW - timedelta(minutes=5),
) -> ScheduleRead:
    return ScheduleRead(
        id=identifier,
        title=title,
        scheduled_at=at,
        category=category,
        completed=completed,
        updated_at=updated_at,
    )


def envelope(decision: dict[str, object], *, state: object | None = None):
    payload: dict[str, object] = {"decision": decision}
    if state is not None:
        payload.update({"controlStateVersion": 1, "controlState": state})
    return DecisionEnvelope.model_validate(payload)


def request_data(
    request_id: UUID = REQUEST_ID,
    *,
    conversation_id: UUID | None = None,
    input_payload: dict[str, object] | None = None,
) -> AssistantRequestCreate:
    payload: dict[str, object] = {
        "requestId": str(request_id),
        "input": input_payload or {"type": "text", "text": "재고 확인"},
    }
    if conversation_id is not None:
        payload["conversationId"] = str(conversation_id)
    return AssistantRequestCreate.model_validate(payload)


def admit(
    factory,
    data: AssistantRequestCreate,
    *,
    provider_available: bool = True,
    accepted_at: datetime = NOW,
) -> None:
    result = admit_request(
        factory,
        data=data,
        fingerprint=request_fingerprint(data),
        accepted_at=accepted_at,
        new_conversation_id=CONVERSATION_ID,
        runtime_ready=True,
        provider_available=provider_available,
    )
    assert result.kind.value == "accepted"


def run(
    factory,
    provider,
    reader: ScheduleReadPort,
    *,
    request_id: UUID = REQUEST_ID,
    trusted: bool = True,
    uuid_factory=None,
    clock=None,
    monotonic_clock=None,
    stop_signal=None,
    deadline: float = 300.0,
) -> ProcessOneResult:
    runtime_guard = ProcessDeadlineGuard(
        monotonic_clock=(
            FixedMonotonicClock()
            if monotonic_clock is None
            else monotonic_clock
        ),
        deadline=deadline,
        stop_signal=Event() if stop_signal is None else stop_signal,
    )
    return process_one(
        factory,
        request_id=request_id,
        provider=provider,
        schedule_reader=reader,
        clock=FixedClock(NOW) if clock is None else clock,
        time_trust_source=TrustSource(trusted),
        uuid_factory=uuid_factory or UuidSequence(),
        runtime_guard=runtime_guard,
        safe_writer=PendingSafeWriter(),
    )


@pytest.mark.parametrize(
    ("monotonic_now", "expected_timeout"),
    [(299.999, False), (300.0, True)],
)
def test_claim_deadline_guard_uses_exact_300_second_boundary(
    processor_store, monotonic_now: float, expected_timeout: bool
) -> None:
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [envelope({"kind": "ask", "text": "몇 시인가요?"})]
    )
    reader = MemoryScheduleReader([schedule()])

    result = run(
        processor_store,
        provider,
        reader,
        monotonic_clock=FixedMonotonicClock(monotonic_now),
    )

    assert result is ProcessOneResult.COMPLETED
    stored = stored_request(processor_store)
    assert stored.status == "completed"
    assert stored.pending_write_json is None
    if expected_timeout:
        assert stored.result_json["code"] == "PROCESSING_TIMEOUT"
    else:
        # Claimed before the deadline, but too late to call the model safely.
        assert stored.result_json["code"] == "MODEL_TIMEOUT"
    assert provider.call_count == 0
    assert reader.get_calls == []
    assert reader.list_limits == []


def test_provider_gets_the_time_left_before_the_deadline(processor_store) -> None:
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider([envelope({"kind": "ask", "text": "몇 시인가요?"})])

    run(processor_store, provider, MemoryScheduleReader([schedule()]), monotonic_clock=FixedMonotonicClock(100.0))

    # 300 s deadline - 100 s waited - 10 s safety margin.
    assert provider.contexts[0].time_budget_seconds == 190.0
    assert stored_request(processor_store).result_json["kind"] == "need_more_info"


@pytest.mark.parametrize("monotonic_now", [290.0, 295.0])
def test_request_without_enough_time_left_fails_without_calling_the_model(
    processor_store, monotonic_now: float
) -> None:
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider([])

    result = run(
        processor_store, provider, MemoryScheduleReader([schedule()]), monotonic_clock=FixedMonotonicClock(monotonic_now)
    )

    assert result is ProcessOneResult.COMPLETED
    stored = stored_request(processor_store)
    assert (stored.result_json["code"], stored.result_json["nextAction"]) == ("MODEL_TIMEOUT", "retry_later")
    assert stored.pending_write_json is None
    assert provider.call_count == 0


def test_finalize_guard_makes_timeout_win_after_late_provider_result(
    processor_store,
) -> None:
    admit(processor_store, request_data())
    # Just inside the safety margin, then the provider answers past the deadline.
    monotonic = FixedMonotonicClock(289.999)

    class DeadlineCrossingProvider:
        def __init__(self) -> None:
            self.call_count = 0

        def decide(self, _context):
            self.call_count += 1
            monotonic.advance(10.001)
            return envelope({"kind": "ask", "text": "늦은 질문"})

    provider = DeadlineCrossingProvider()
    result = run(
        processor_store,
        provider,
        MemoryScheduleReader(),
        monotonic_clock=monotonic,
    )

    assert result is ProcessOneResult.COMPLETED
    stored = stored_request(processor_store)
    assert stored.result_json["code"] == "PROCESSING_TIMEOUT"
    assert provider.call_count == 1
    with processor_store() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(AssistantQuestion)) == 0


def test_shutdown_guard_before_claim_leaves_request_for_recovery(
    processor_store,
) -> None:
    admit(processor_store, request_data())
    stopped = Event()
    stopped.set()
    provider = ScriptedDecisionProvider(
        [envelope({"kind": "ask", "text": "호출되면 안 됨"})]
    )

    result = run(
        processor_store,
        provider,
        MemoryScheduleReader(),
        stop_signal=stopped,
    )

    assert result is ProcessOneResult.SKIPPED
    assert stored_request(processor_store).status == "accepted"
    assert provider.call_count == 0


def test_shutdown_guard_after_provider_discards_late_result(
    processor_store,
) -> None:
    admit(processor_store, request_data())
    stopped = Event()

    class StoppingProvider:
        def decide(self, _context):
            stopped.set()
            return envelope({"kind": "ask", "text": "늦은 질문"})

    result = run(
        processor_store,
        StoppingProvider(),
        MemoryScheduleReader(),
        stop_signal=stopped,
    )

    assert result is ProcessOneResult.LOST_FINALIZE
    stored = stored_request(processor_store)
    assert stored.status == "processing"
    assert stored.result_json is None
    with processor_store() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(AssistantQuestion)) == 0


def stored_request(factory, request_id: UUID = REQUEST_ID) -> AssistantRequest:
    with factory() as session:
        value = session.get(AssistantRequest, request_id)
        assert value is not None
        session.expunge(value)
        return value


def active_question(factory) -> AssistantQuestion:
    with factory() as session:
        value = session.scalar(
            sa.select(AssistantQuestion).where(
                AssistantQuestion.status == AssistantQuestionStatus.ACTIVE.value
            )
        )
        assert value is not None
        session.expunge(value)
        return value


def test_ask_claims_and_atomically_persists_question_and_control_state(
    processor_store,
) -> None:
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [envelope({"kind": "ask", "text": "몇 시인가요?"}, state={"step": 2})]
    )

    result = run(processor_store, provider, MemoryScheduleReader())

    assert result is ProcessOneResult.COMPLETED
    request = stored_request(processor_store)
    assert request.processing_at == NOW
    assert request.result_kind == "need_more_info"
    assert request.retention_started_at is None
    question = active_question(processor_store)
    assert request.completed_at == question.created_at == NOW
    assert question.expires_at is None
    assert question.payload_json == request.result_json
    with processor_store() as session:
        conversation = session.get(AssistantConversation, CONVERSATION_ID)
        assert conversation is not None
        assert conversation.control_state_version == 1
        assert conversation.control_state_json == {"step": 2}
    assert provider.contexts[0].server_time_trusted is True
    assert provider.contexts[0].timezone == "Asia/Seoul"
    assert provider.contexts[0].task_reference_accepted_at == NOW


@pytest.mark.parametrize(
    ("step", "code"),
    [
        (DecisionProviderTimeout("timeout"), "MODEL_TIMEOUT"),
        (DecisionProviderInvalidOutput("bad evidence"), "MODEL_OUTPUT_INVALID"),
        (ValueError("backend planner bug"), "INTERNAL_ERROR"),
        (TypeError("backend planner type bug"), "INTERNAL_ERROR"),
        (RuntimeError("private provider detail"), "INTERNAL_ERROR"),
    ],
)
def test_provider_failures_are_sanitized(
    processor_store, step, code: str, caplog
) -> None:
    caplog.set_level("ERROR", logger="app.modules.assistant.processor")
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider([step])

    assert run(processor_store, provider, MemoryScheduleReader()) is ProcessOneResult.COMPLETED

    request = stored_request(processor_store)
    assert request.result_json["code"] == code
    assert "private" not in request.result_json["text"]
    internal_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "assistant.processor_internal_error"
    ]
    assert bool(internal_records) is (code == "INTERNAL_ERROR")
    if internal_records:
        (record,) = internal_records
        assert record.exc_info is None
        assert record.exceptionClass == type(step).__name__
        assert "private" not in record.getMessage()


@pytest.mark.parametrize(
    ("code", "next_action"),
    [("TARGET_AMBIGUOUS", "revise_input"), ("INTERNAL_ERROR", "none")],
)
def test_allowed_provider_failure_is_persisted(
    processor_store, code: str, next_action: str
) -> None:
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "fail",
                    "text": "처리 실패",
                    "code": code,
                    "nextAction": next_action,
                }
            )
        ]
    )

    run(processor_store, provider, MemoryScheduleReader())

    assert stored_request(processor_store).result_json["code"] == code


def test_malformed_provider_output_becomes_model_output_invalid(
    processor_store,
) -> None:
    class InvalidProvider:
        def decide(self, context: DecisionContext):
            del context
            return {"decision": {"kind": "fail", "text": "x", "code": "MODEL_TIMEOUT", "nextAction": "retry_later"}}

    admit(processor_store, request_data())
    assert run(processor_store, InvalidProvider(), MemoryScheduleReader()) is ProcessOneResult.COMPLETED
    assert stored_request(processor_store).result_json["code"] == "MODEL_OUTPUT_INVALID"


def test_oversized_control_state_becomes_model_output_invalid_without_state_write(
    processor_store,
) -> None:
    class InvalidProvider:
        def decide(self, context: DecisionContext):
            del context
            return {
                "decision": {"kind": "ask", "text": "질문"},
                "controlStateVersion": 1,
                "controlState": {"large": "x" * SNAPSHOT_MAX_BYTES},
            }

    admit(processor_store, request_data())
    run(processor_store, InvalidProvider(), MemoryScheduleReader())

    assert stored_request(processor_store).result_json["code"] == "MODEL_OUTPUT_INVALID"
    with processor_store() as session:
        conversation = session.get(AssistantConversation, CONVERSATION_ID)
        assert conversation is not None
        assert conversation.control_state_json is None


def test_provider_base_exception_is_not_swallowed(processor_store) -> None:
    class StopProvider:
        def decide(self, context: DecisionContext):
            del context
            raise KeyboardInterrupt

    admit(processor_store, request_data())
    with pytest.raises(KeyboardInterrupt):
        run(processor_store, StopProvider(), MemoryScheduleReader())
    assert stored_request(processor_store).status == "processing"


def test_confirm_uses_current_database_snapshot_and_removes_noop_fields(
    processor_store,
) -> None:
    current = schedule(title="현재 제목")
    reader = MemoryScheduleReader([current])
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "수정할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "update",
                    "changes": {"title": "현재 제목", "completed": True},
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    assert run(processor_store, provider, reader) is ProcessOneResult.COMPLETED

    request = stored_request(processor_store)
    assert request.result_json["target"]["updatedAt"] == "2026-10-18T04:55:00Z"
    assert request.result_json["changes"] == {"completed": True}
    question = active_question(processor_store)
    assert question.expires_at == NOW + timedelta(minutes=10)
    assert question.candidate_intents_json["intent"]["expectedUpdatedAt"] == (
        "2026-10-18T04:55:00Z"
    )


def test_confirm_scheduled_at_change_serializes_offset_and_fraction(
    processor_store,
) -> None:
    current = schedule()
    reader = MemoryScheduleReader([current])
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "수정할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "update",
                    "changes": {
                        "scheduledAt": "2026-10-19T09:00:00.123456+09:00"
                    },
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    assert run(processor_store, provider, reader) is ProcessOneResult.COMPLETED

    expected = {"scheduledAt": "2026-10-19T00:00:00.123456Z"}
    request = stored_request(processor_store)
    assert request.result_kind == "confirmation"
    assert request.result_json["changes"] == expected
    question = active_question(processor_store)
    assert question.candidate_intents_json["intent"]["changes"] == expected
    assert request.pending_write_json is None
    assert request.write_started is False


def test_scheduled_at_candidate_changes_are_serialized_before_selection(
    processor_store,
) -> None:
    first = schedule()
    second = schedule(
        SCHEDULE_B,
        title="반품 확인",
        at=NOW + timedelta(hours=2),
        category=Category.RETURN,
    )
    reader = MemoryScheduleReader([first, second])
    admit(processor_store, request_data())
    change = {"scheduledAt": "2026-10-19T09:00:00.123456+09:00"}
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "select_candidate",
                    "text": "대상을 선택하세요.",
                    "candidates": [
                        {
                            "scheduleId": str(SCHEDULE_A),
                            "operation": "update",
                            "changes": change,
                            "timeInterpretation": "absolute",
                        },
                        {
                            "scheduleId": str(SCHEDULE_B),
                            "operation": "update",
                            "changes": change,
                            "timeInterpretation": "absolute",
                        },
                    ],
                }
            )
        ]
    )

    assert run(processor_store, provider, reader) is ProcessOneResult.COMPLETED

    request = stored_request(processor_store)
    assert request.result_kind == "candidate_selection"
    question = active_question(processor_store)
    expected = {"scheduledAt": "2026-10-19T00:00:00.123456Z"}
    assert {
        tuple(intent["changes"].items())
        for intent in question.candidate_intents_json["candidates"].values()
    } == {tuple(expected.items())}


def test_all_noop_update_completes_no_match_without_question(processor_store) -> None:
    current = schedule(title="현재 제목", completed=True)
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "수정할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "update",
                    "changes": {"title": "현재 제목", "completed": True},
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    run(processor_store, provider, MemoryScheduleReader([current]))

    request = stored_request(processor_store)
    assert request.result_json == {
        "kind": "outcome",
        "text": "이미 요청한 상태입니다.",
        "outcome": "no_match",
        "schedulesChanged": False,
        "operation": "update",
    }
    with processor_store() as session:
        assert session.scalar(sa.select(sa.func.count()).select_from(AssistantQuestion)) == 0


@pytest.mark.parametrize(
    "second_exists",
    [True, False],
    ids=["all_existing_noop", "noop_and_deleted"],
)
def test_select_candidate_noop_only_results_are_already_applied(
    processor_store, second_exists: bool
) -> None:
    schedules = [schedule(completed=True)]
    if second_exists:
        schedules.append(
            schedule(
                SCHEDULE_B,
                title="반품 확인",
                at=NOW + timedelta(hours=2),
                category=Category.RETURN,
                completed=True,
            )
        )
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "select_candidate",
                    "text": "대상을 선택하세요.",
                    "candidates": [
                        {
                            "scheduleId": str(SCHEDULE_A),
                            "operation": "update",
                            "changes": {"completed": True},
                            "timeInterpretation": "absolute",
                        },
                        {
                            "scheduleId": str(SCHEDULE_B),
                            "operation": "update",
                            "changes": {"completed": True},
                            "timeInterpretation": "absolute",
                        },
                    ],
                },
                state={"step": "noop"},
            )
        ]
    )

    assert (
        run(processor_store, provider, MemoryScheduleReader(schedules))
        is ProcessOneResult.COMPLETED
    )

    request = stored_request(processor_store)
    assert request.result_json == {
        "kind": "outcome",
        "text": "이미 요청한 상태입니다.",
        "outcome": "no_match",
        "schedulesChanged": False,
        "operation": "update",
    }
    assert request.pending_write_json is None
    assert request.write_started is False
    with processor_store() as session:
        conversation = session.get(AssistantConversation, CONVERSATION_ID)
        assert conversation is not None
        assert conversation.control_state_json == {"step": "noop"}
        assert (
            session.scalar(
                sa.select(sa.func.count()).select_from(AssistantQuestion)
            )
            == 0
        )


def test_confirm_missing_target_completes_no_match(processor_store) -> None:
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "삭제할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "delete",
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    run(processor_store, provider, MemoryScheduleReader())

    result = stored_request(processor_store).result_json
    assert result["outcome"] == "no_match"
    assert result["operation"] == "delete"


def test_candidate_selection_uses_opaque_ids_and_current_snapshots(
    processor_store,
) -> None:
    first = schedule()
    second = schedule(
        SCHEDULE_B,
        title="반품 확인",
        at=NOW + timedelta(hours=2),
        category=Category.RETURN,
    )
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "select_candidate",
                    "text": "대상을 선택하세요.",
                    "candidates": [
                        {
                            "scheduleId": str(SCHEDULE_A),
                            "operation": "delete",
                            "timeInterpretation": "absolute",
                        },
                        {
                            "scheduleId": str(SCHEDULE_B),
                            "operation": "update",
                            "changes": {"completed": True},
                            "timeInterpretation": "absolute",
                        },
                    ],
                }
            )
        ]
    )

    run(processor_store, provider, MemoryScheduleReader([first, second]))

    request = stored_request(processor_store)
    assert request.result_kind == "candidate_selection"
    candidate_ids = [item["candidateId"] for item in request.result_json["candidates"]]
    assert set(candidate_ids).isdisjoint({str(SCHEDULE_A), str(SCHEDULE_B)})
    question = active_question(processor_store)
    assert set(question.candidate_intents_json["candidates"]) == set(candidate_ids)
    assert question.candidates_json == request.result_json["candidates"]


def test_candidate_question_uses_issue_time_after_provider_and_all_reads(
    processor_store,
) -> None:
    events: list[str] = []
    first = schedule()
    second = schedule(
        SCHEDULE_B,
        title="반품 확인",
        at=NOW + timedelta(hours=2),
        category=Category.RETURN,
    )

    class RecordingProvider:
        def decide(self, context: DecisionContext):
            del context
            events.append("provider")
            return envelope(
                {
                    "kind": "select_candidate",
                    "text": "대상을 선택하세요.",
                    "candidates": [
                        {
                            "scheduleId": str(SCHEDULE_A),
                            "operation": "delete",
                            "timeInterpretation": "absolute",
                        },
                        {
                            "scheduleId": str(SCHEDULE_B),
                            "operation": "delete",
                            "timeInterpretation": "absolute",
                        },
                    ],
                }
            )

    class RecordingReader(MemoryScheduleReader):
        def get(self, schedule_id: UUID) -> ScheduleRead | None:
            events.append("read")
            return super().get(schedule_id)

    clock = AdvancingClock(NOW, timedelta(minutes=4), events)
    admit(processor_store, request_data())

    assert (
        run(
            processor_store,
            RecordingProvider(),
            RecordingReader([first, second]),
            clock=clock,
        )
        is ProcessOneResult.COMPLETED
    )

    issue_time = NOW + timedelta(minutes=4)
    request = stored_request(processor_store)
    question = active_question(processor_store)
    assert events == ["clock", "provider", "read", "read", "clock"]
    assert clock.calls == [NOW, issue_time]
    assert request.completed_at == question.created_at == issue_time
    assert question.expires_at - question.created_at == timedelta(minutes=10)
    assert request.result_json["expiresAt"] == question.payload_json["expiresAt"]


def test_provider_confirmation_uses_issue_time_after_schedule_read(
    processor_store,
) -> None:
    current = schedule()
    clock = AdvancingClock(NOW, timedelta(minutes=4))
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "삭제할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "delete",
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    assert (
        run(
            processor_store,
            provider,
            MemoryScheduleReader([current]),
            clock=clock,
        )
        is ProcessOneResult.COMPLETED
    )

    issue_time = NOW + timedelta(minutes=4)
    request = stored_request(processor_store)
    question = active_question(processor_store)
    assert clock.calls == [NOW, issue_time]
    assert request.completed_at == question.created_at == issue_time
    assert question.expires_at - question.created_at == timedelta(minutes=10)
    assert request.result_json["expiresAt"] == question.payload_json["expiresAt"]


@pytest.mark.parametrize(("available_ids", "expected_kind"), [([], "outcome"), ([SCHEDULE_A], "confirmation")])
def test_candidate_resolution_handles_zero_or_one_remaining_target(
    processor_store, available_ids: list[UUID], expected_kind: str
) -> None:
    available = [
        schedule(identifier)
        for identifier in available_ids
    ]
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "select_candidate",
                    "text": "대상을 선택하세요.",
                    "candidates": [
                        {"scheduleId": str(SCHEDULE_A), "operation": "delete", "timeInterpretation": "absolute"},
                        {"scheduleId": str(SCHEDULE_B), "operation": "delete", "timeInterpretation": "absolute"},
                    ],
                }
            )
        ]
    )

    run(processor_store, provider, MemoryScheduleReader(available))

    assert stored_request(processor_store).result_kind == expected_kind


def test_candidate_noop_is_removed_before_cardinality_decision(processor_store) -> None:
    first = schedule(completed=True)
    second = schedule(
        SCHEDULE_B,
        title="반품 확인",
        at=NOW + timedelta(hours=2),
        category=Category.RETURN,
    )
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "select_candidate",
                    "text": "선택하세요.",
                    "candidates": [
                        {
                            "scheduleId": str(SCHEDULE_A),
                            "operation": "update",
                            "changes": {"completed": True},
                            "timeInterpretation": "absolute",
                        },
                        {
                            "scheduleId": str(SCHEDULE_B),
                            "operation": "delete",
                            "timeInterpretation": "absolute",
                        },
                    ],
                }
            )
        ]
    )

    run(processor_store, provider, MemoryScheduleReader([first, second]))

    result = stored_request(processor_store).result_json
    assert result["kind"] == "confirmation"
    assert result["target"]["id"] == str(SCHEDULE_B)
    request = stored_request(processor_store)
    assert request.pending_write_json is None
    assert request.write_started is False
    question = active_question(processor_store)
    assert question.candidate_intents_json["intent"]["targetScheduleId"] == str(
        SCHEDULE_B
    )


def test_identical_public_candidates_fail_ambiguous(processor_store) -> None:
    first = schedule()
    second = schedule(SCHEDULE_B)
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "select_candidate",
                    "text": "구분할 수 없습니다.",
                    "candidates": [
                        {"scheduleId": str(SCHEDULE_A), "operation": "delete", "timeInterpretation": "absolute"},
                        {"scheduleId": str(SCHEDULE_B), "operation": "delete", "timeInterpretation": "absolute"},
                    ],
                }
            )
        ]
    )

    run(processor_store, provider, MemoryScheduleReader([first, second]))
    assert stored_request(processor_store).result_json["code"] == "TARGET_AMBIGUOUS"


def test_valid_selection_and_confirmation_are_provider_free(processor_store) -> None:
    first = schedule()
    second = schedule(
        SCHEDULE_B,
        title="반품 확인",
        at=NOW + timedelta(hours=2),
        category=Category.RETURN,
    )
    reader = MemoryScheduleReader([first, second])
    uuids = UuidSequence()
    admit(processor_store, request_data())
    initial_provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "select_candidate",
                    "text": "선택하세요.",
                    "candidates": [
                        {"scheduleId": str(SCHEDULE_A), "operation": "delete", "timeInterpretation": "absolute"},
                        {"scheduleId": str(SCHEDULE_B), "operation": "delete", "timeInterpretation": "absolute"},
                    ],
                }
            )
        ]
    )
    run(processor_store, initial_provider, reader, uuid_factory=uuids)
    candidate_question = active_question(processor_store)
    candidate_id = candidate_question.candidates_json[0]["candidateId"]
    reader.schedules[SCHEDULE_A] = schedule(
        title="현재 변경된 제목",
        updated_at=NOW + timedelta(minutes=1),
    )

    selection_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    selection = request_data(
        selection_id,
        conversation_id=CONVERSATION_ID,
        input_payload={
            "type": "selection",
            "questionId": str(candidate_question.id),
            "candidateId": candidate_id,
        },
    )
    admit(processor_store, selection, provider_available=False)
    no_provider = ScriptedDecisionProvider([])
    assert run(
        processor_store,
        no_provider,
        reader,
        request_id=selection_id,
        uuid_factory=uuids,
    ) is ProcessOneResult.COMPLETED
    assert no_provider.call_count == 0
    confirmation = active_question(processor_store)
    assert confirmation.kind == "confirmation"
    assert confirmation.payload_json["target"]["title"] == "현재 변경된 제목"
    assert confirmation.candidate_intents_json["intent"]["expectedUpdatedAt"] == (
        "2026-10-18T05:01:00Z"
    )

    reject_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    rejection = request_data(
        reject_id,
        conversation_id=CONVERSATION_ID,
        input_payload={
            "type": "confirmation",
            "questionId": str(confirmation.id),
            "approved": False,
        },
    )
    admit(processor_store, rejection, provider_available=False)
    assert run(
        processor_store,
        no_provider,
        reader,
        request_id=reject_id,
        uuid_factory=uuids,
    ) is ProcessOneResult.COMPLETED
    assert stored_request(processor_store, reject_id).result_json["outcome"] == "cancelled"
    assert no_provider.call_count == 0


def _evidence_state(turn: str) -> dict[str, object]:
    return {
        "evidence": 4,
        "turns": [turn],
        "consentGranted": False,
        "pending": None,
        "changeDate": None,
        "changeDateSource": None,
        "midnightValue": None,
        "midnightSource": None,
        "createConfirmed": None,
        "validatedOutput": None,
    }


def test_saved_confirmation_keeps_original_state_for_time_trust_question(
    processor_store,
) -> None:
    turn = "내일 재고 확인 일정을 오후 3시로 옮겨 줘"
    state = _evidence_state(turn)
    reader = MemoryScheduleReader([schedule()])
    uuid_factory = UuidSequence()
    admit(processor_store, request_data())
    initial = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "바꿀까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "update",
                    "changes": {"scheduledAt": "2026-10-19T06:00:00Z"},
                    "timeInterpretation": "relative",
                },
                state=state,
            )
        ]
    )
    run(processor_store, initial, reader, uuid_factory=uuid_factory)
    confirmation = active_question(processor_store)
    approval_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    admit(
        processor_store,
        request_data(
            approval_id,
            conversation_id=CONVERSATION_ID,
            input_payload={
                "type": "confirmation",
                "questionId": str(confirmation.id),
                "approved": True,
            },
        ),
        provider_available=False,
    )

    no_provider = ScriptedDecisionProvider([])
    assert run(
        processor_store,
        no_provider,
        reader,
        request_id=approval_id,
        trusted=False,
        uuid_factory=uuid_factory,
    ) is ProcessOneResult.COMPLETED

    assert no_provider.call_count == 0
    assert stored_request(processor_store, approval_id).result_json["kind"] == "need_more_info"
    with processor_store() as session:
        conversation = session.get(AssistantConversation, CONVERSATION_ID)
        assert conversation is not None
        assert conversation.control_state_json == state


def test_saved_selection_keeps_original_state_for_time_trust_question(
    processor_store,
) -> None:
    state = _evidence_state("내일 재고 확인 일정 지워 줘")
    first = schedule()
    second = schedule(SCHEDULE_B, title="재고 확인 두 번째")
    reader = MemoryScheduleReader([first, second])
    uuid_factory = UuidSequence()
    admit(processor_store, request_data())
    initial = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "select_candidate",
                    "text": "선택하세요.",
                    "candidates": [
                        {
                            "scheduleId": str(SCHEDULE_A),
                            "operation": "delete",
                            "timeInterpretation": "relative",
                        },
                        {
                            "scheduleId": str(SCHEDULE_B),
                            "operation": "delete",
                            "timeInterpretation": "relative",
                        },
                    ],
                },
                state=state,
            )
        ]
    )
    run(processor_store, initial, reader, uuid_factory=uuid_factory)
    selection_question = active_question(processor_store)
    selection_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    admit(
        processor_store,
        request_data(
            selection_id,
            conversation_id=CONVERSATION_ID,
            input_payload={
                "type": "selection",
                "questionId": str(selection_question.id),
                "candidateId": selection_question.candidates_json[0]["candidateId"],
            },
        ),
        provider_available=False,
    )

    no_provider = ScriptedDecisionProvider([])
    assert run(
        processor_store,
        no_provider,
        reader,
        request_id=selection_id,
        trusted=False,
        uuid_factory=uuid_factory,
    ) is ProcessOneResult.COMPLETED

    assert no_provider.call_count == 0
    assert stored_request(processor_store, selection_id).result_json["kind"] == "need_more_info"
    with processor_store() as session:
        conversation = session.get(AssistantConversation, CONVERSATION_ID)
        assert conversation is not None
        assert conversation.control_state_json == state


def test_saved_confirmation_clears_provider_state_before_write(
    processor_store,
) -> None:
    state = _evidence_state("재고 확인 일정 지워 줘")
    reader = MemoryScheduleReader([schedule()])
    admit(processor_store, request_data())
    initial = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "삭제할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "delete",
                    "timeInterpretation": "absolute",
                },
                state=state,
            )
        ]
    )
    run(processor_store, initial, reader)
    confirmation = active_question(processor_store)
    approval_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    admit(
        processor_store,
        request_data(
            approval_id,
            conversation_id=CONVERSATION_ID,
            input_payload={
                "type": "confirmation",
                "questionId": str(confirmation.id),
                "approved": True,
            },
        ),
        provider_available=False,
    )

    assert run(
        processor_store,
        ScriptedDecisionProvider([]),
        reader,
        request_id=approval_id,
    ) is ProcessOneResult.PENDING_WRITE
    with processor_store() as session:
        conversation = session.get(AssistantConversation, CONVERSATION_ID)
        assert conversation is not None
        assert conversation.control_state_version is None
        assert conversation.control_state_json is None


def test_saved_selection_confirmation_uses_post_read_issue_time(
    processor_store,
) -> None:
    first = schedule()
    second = schedule(
        SCHEDULE_B,
        title="반품 확인",
        at=NOW + timedelta(hours=2),
        category=Category.RETURN,
    )
    reader = MemoryScheduleReader([first, second])
    uuids = UuidSequence()
    admit(processor_store, request_data())
    run(
        processor_store,
        ScriptedDecisionProvider(
            [
                envelope(
                    {
                        "kind": "select_candidate",
                        "text": "선택하세요.",
                        "candidates": [
                            {
                                "scheduleId": str(SCHEDULE_A),
                                "operation": "delete",
                                "timeInterpretation": "absolute",
                            },
                            {
                                "scheduleId": str(SCHEDULE_B),
                                "operation": "delete",
                                "timeInterpretation": "absolute",
                            },
                        ],
                    }
                )
            ]
        ),
        reader,
        uuid_factory=uuids,
    )
    candidate_question = active_question(processor_store)
    selection_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    admit(
        processor_store,
        request_data(
            selection_id,
            conversation_id=CONVERSATION_ID,
            input_payload={
                "type": "selection",
                "questionId": str(candidate_question.id),
                "candidateId": candidate_question.candidates_json[0]["candidateId"],
            },
        ),
        provider_available=False,
    )
    reader.get_calls.clear()
    clock_start = NOW + timedelta(minutes=1)
    clock = AdvancingClock(clock_start, timedelta(minutes=4))
    no_provider = ScriptedDecisionProvider([])

    assert (
        run(
            processor_store,
            no_provider,
            reader,
            request_id=selection_id,
            uuid_factory=uuids,
            clock=clock,
        )
        is ProcessOneResult.COMPLETED
    )

    issue_time = clock_start + timedelta(minutes=4)
    request = stored_request(processor_store, selection_id)
    confirmation = active_question(processor_store)
    assert no_provider.call_count == 0
    assert reader.get_calls == [SCHEDULE_A]
    assert clock.calls == [clock_start, issue_time]
    assert request.completed_at == confirmation.created_at == issue_time
    assert confirmation.expires_at - confirmation.created_at == timedelta(minutes=10)
    assert request.result_json["expiresAt"] == confirmation.payload_json["expiresAt"]


def test_provider_free_selection_handles_deleted_target_and_read_failure(
    processor_store,
) -> None:
    first = schedule()
    second = schedule(
        SCHEDULE_B,
        title="반품 확인",
        at=NOW + timedelta(hours=2),
        category=Category.RETURN,
    )
    uuids = UuidSequence()
    reader = MemoryScheduleReader([first, second])
    admit(processor_store, request_data())
    run(
        processor_store,
        ScriptedDecisionProvider(
            [
                envelope(
                    {
                        "kind": "select_candidate",
                        "text": "선택하세요.",
                        "candidates": [
                            {"scheduleId": str(SCHEDULE_A), "operation": "delete", "timeInterpretation": "absolute"},
                            {"scheduleId": str(SCHEDULE_B), "operation": "delete", "timeInterpretation": "absolute"},
                        ],
                    }
                )
            ]
        ),
        reader,
        uuid_factory=uuids,
    )
    question = active_question(processor_store)
    selected = question.candidates_json[0]["candidateId"]
    reader.schedules.clear()
    follow_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    follow = request_data(
        follow_id,
        conversation_id=CONVERSATION_ID,
        input_payload={
            "type": "selection",
            "questionId": str(question.id),
            "candidateId": selected,
        },
    )
    admit(processor_store, follow, provider_available=False)

    no_provider = ScriptedDecisionProvider([])
    run(
        processor_store,
        no_provider,
        reader,
        request_id=follow_id,
        uuid_factory=uuids,
    )
    assert stored_request(processor_store, follow_id).result_json["outcome"] == "no_match"
    assert no_provider.call_count == 0


@pytest.mark.parametrize(
    "corruption",
    ["version_missing", "version", "intent", "candidate_map"],
)
def test_selection_snapshot_corrupted_after_admission_fails_without_provider(
    processor_store, corruption: str
) -> None:
    first = schedule()
    second = schedule(
        SCHEDULE_B,
        title="반품 확인",
        at=NOW + timedelta(hours=2),
        category=Category.RETURN,
    )
    uuids = UuidSequence()
    reader = MemoryScheduleReader([first, second])
    admit(processor_store, request_data())
    run(
        processor_store,
        ScriptedDecisionProvider(
            [
                envelope(
                    {
                        "kind": "select_candidate",
                        "text": "선택하세요.",
                        "candidates": [
                            {"scheduleId": str(SCHEDULE_A), "operation": "delete", "timeInterpretation": "absolute"},
                            {"scheduleId": str(SCHEDULE_B), "operation": "delete", "timeInterpretation": "absolute"},
                        ],
                    }
                )
            ]
        ),
        reader,
        uuid_factory=uuids,
    )
    question = active_question(processor_store)
    selected = question.candidates_json[0]["candidateId"]
    follow_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    follow = request_data(
        follow_id,
        conversation_id=CONVERSATION_ID,
        input_payload={
            "type": "selection",
            "questionId": str(question.id),
            "candidateId": selected,
        },
    )
    admit(processor_store, follow, provider_available=False)
    with processor_store.begin() as session:
        stored = session.get(AssistantQuestion, question.id)
        assert stored is not None
        if corruption == "version_missing":
            snapshot = dict(stored.candidate_intents_json)
            snapshot.pop("version")
            stored.candidate_intents_json = snapshot
        elif corruption == "version":
            snapshot = dict(stored.candidate_intents_json)
            snapshot["version"] = 2
            stored.candidate_intents_json = snapshot
        elif corruption == "intent":
            stored.candidate_intents_json = {
                "version": 1,
                "candidates": {selected: {"operation": "delete"}},
            }
        else:
            stored.candidate_intents_json = {"version": 1, "candidates": {}}
    fallback = ScriptedDecisionProvider(
        [envelope({"kind": "ask", "text": "다시 설명해 주세요."})]
    )

    run(
        processor_store,
        fallback,
        reader,
        request_id=follow_id,
        uuid_factory=uuids,
    )

    assert fallback.call_count == 0
    request = stored_request(processor_store, follow_id)
    assert request.result_kind == "failure"
    assert request.result_json["code"] == "INTERNAL_ERROR"
    assert request.pending_write_json is None
    assert request.write_started is False


def test_approved_confirmation_and_create_stop_at_pending_write(processor_store) -> None:
    current = schedule()
    reader = MemoryScheduleReader([current])
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "삭제할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "delete",
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )
    run(processor_store, provider, reader)
    confirmation = active_question(processor_store)
    approval_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    approval = request_data(
        approval_id,
        conversation_id=CONVERSATION_ID,
        input_payload={
            "type": "confirmation",
            "questionId": str(confirmation.id),
            "approved": True,
        },
    )
    admit(processor_store, approval, provider_available=False)

    no_provider = ScriptedDecisionProvider([])
    assert run(
        processor_store,
        no_provider,
        reader,
        request_id=approval_id,
    ) is ProcessOneResult.PENDING_WRITE
    approved = stored_request(processor_store, approval_id)
    assert approved.status == "processing"
    assert approved.pending_write_json["operation"] == "delete"
    assert approved.write_started is False
    assert approved.write_operation is None
    assert approved.create_idempotency_key is None
    assert no_provider.call_count == 0
    assert not hasattr(reader, "create")
    assert not hasattr(reader, "update")
    assert not hasattr(reader, "delete")

    cancelled = cancel_assistant_target(
        processor_store,
        conversation_id=CONVERSATION_ID,
        target=AssistantCancelTarget.model_validate(
            {"type": "request", "id": str(approval_id)}
        ),
        now=NOW + timedelta(seconds=1),
    )
    assert cancelled.target_state.value == "cancelled"
    after_cancel = stored_request(processor_store, approval_id)
    assert after_cancel.result_json["outcome"] == "cancelled"
    assert after_cancel.pending_write_json is None


def test_create_intent_is_validated_and_saved_without_schedule_write(
    processor_store,
) -> None:
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "create_intent",
                    "text": "등록합니다.",
                    "title": "반품 검수",
                    "scheduledAt": "2026-10-19T05:00:00Z",
                    "category": "RETURN",
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    assert run(processor_store, provider, MemoryScheduleReader()) is ProcessOneResult.PENDING_WRITE
    request = stored_request(processor_store)
    assert request.status == "processing"
    assert request.pending_write_version == 1
    assert request.pending_write_json == {
        "version": 1,
        "operation": "create",
        "title": "반품 검수",
        "scheduledAt": "2026-10-19T05:00:00Z",
        "category": "RETURN",
        "timeInterpretation": "absolute",
    }
    assert request.write_started is False


@pytest.mark.parametrize(("count", "kind"), [(0, "outcome"), (100, "outcome"), (101, "need_more_info")])
def test_list_reads_at_most_101_and_never_returns_partial_success(
    processor_store, count: int, kind: str
) -> None:
    schedules = [
        schedule(
            UUID(f"70000000-0000-4000-8000-{index:012x}"),
            title=f"일정 {index}",
            at=NOW + timedelta(minutes=index + 1),
        )
        for index in range(count)
    ]
    reader = MemoryScheduleReader(schedules)
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "list_intent",
                    "text": "조회 결과입니다.",
                    "from": NOW.isoformat(),
                    "to": (NOW + timedelta(days=1)).isoformat(),
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    run(processor_store, provider, reader)

    result = stored_request(processor_store).result_json
    assert reader.list_limits == [101]
    assert result["kind"] == kind
    if count <= 100:
        assert len(result["items"]) == count
    else:
        assert "items" not in result


def test_list_time_filter_is_applied_after_bounded_read(processor_store) -> None:
    matching = schedule(
        SCHEDULE_A,
        title="오후 점검",
        at=datetime(2026, 10, 19, 6, 0, tzinfo=timezone.utc),
    )
    other = schedule(
        SCHEDULE_B,
        title="저녁 점검",
        at=datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc),
    )
    reader = MemoryScheduleReader([other, matching])
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "list_intent",
                    "text": "조회 결과입니다.",
                    "from": "2026-10-19T00:00:00+09:00",
                    "to": "2026-10-20T00:00:00+09:00",
                    "timeMinutes": 15 * 60,
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    run(processor_store, provider, reader)

    result = stored_request(processor_store).result_json
    assert reader.list_limits == [101]
    assert result["kind"] == "outcome"
    assert [item["id"] for item in result["items"]] == [str(matching.id)]


def test_list_title_prefilter_is_applied_before_the_101_row_limit(
    processor_store,
) -> None:
    unrelated = [
        schedule(
            UUID(f"71000000-0000-4000-8000-{index:012x}"),
            title=f"다른 일정 {index}",
            at=NOW + timedelta(minutes=index + 1),
        )
        for index in range(101)
    ]
    matching = schedule(
        SCHEDULE_A,
        title="출고 검량 확인",
        at=NOW + timedelta(hours=4),
    )
    reader = MemoryScheduleReader([*unrelated, matching])
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "list_intent",
                    "text": "조회 결과입니다.",
                    "from": NOW.isoformat(),
                    "to": (NOW + timedelta(days=1)).isoformat(),
                    "title": "출고 검량 확인",
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    run(processor_store, provider, reader)

    result = stored_request(processor_store).result_json
    assert reader.list_limits == [101]
    assert reader.list_titles == ["출고 검량 확인"]
    assert result["kind"] == "outcome"
    assert [item["id"] for item in result["items"]] == [str(matching.id)]


def test_worst_case_100_item_list_snapshot_stays_within_64_kib(
    processor_store,
) -> None:
    near_max = datetime(9999, 12, 31, 23, 59, 58, 999999, tzinfo=timezone.utc)
    schedules = [
        schedule(
            UUID(f"70000000-0000-4000-8000-{index:012x}"),
            title="😀" * 100,
            at=near_max,
            category=Category.INVENTORY,
            updated_at=near_max,
        )
        for index in range(100)
    ]
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "list_intent",
                    "text": "조회 결과입니다.",
                    "from": "9999-12-30T23:59:59.999999Z",
                    "to": "9999-12-31T23:59:59.999999Z",
                    "category": "INVENTORY",
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    run(processor_store, provider, MemoryScheduleReader(schedules))

    result = stored_request(processor_store).result_json
    assert result["kind"] == "outcome"
    assert len(result["items"]) == 100
    assert len(serialize_snapshot(result).encode("utf-8")) <= SNAPSHOT_MAX_BYTES


def test_provider_is_not_called_for_nonaccepted_request_states(processor_store) -> None:
    admit(processor_store, request_data())
    with processor_store.begin() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        assert request is not None
        request.status = "completed"
        request.processing_at = NOW
        request.completed_at = NOW
        request.result_kind = "outcome"
        request.result_json = {
            "kind": "outcome",
            "text": "완료",
            "outcome": "no_match",
            "schedulesChanged": False,
        }
        request.retention_started_at = NOW
    provider = ScriptedDecisionProvider([])

    assert run(processor_store, provider, MemoryScheduleReader()) is ProcessOneResult.SKIPPED
    assert provider.call_count == 0


def test_untrusted_relative_intent_asks_for_absolute_time_without_read_or_pending(
    processor_store,
) -> None:
    admit(processor_store, request_data())
    reader = MemoryScheduleReader([schedule()])
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "수정할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "delete",
                    "timeInterpretation": "relative",
                }
            )
        ]
    )

    run(processor_store, provider, reader, trusted=False)

    request = stored_request(processor_store)
    assert request.result_kind == "need_more_info"
    assert request.pending_write_json is None
    assert reader.get_calls == []
    assert provider.contexts[0].server_time_trusted is False


def test_question_followup_keeps_original_task_reference_time(processor_store) -> None:
    uuids = UuidSequence()
    admit(processor_store, request_data())
    run(
        processor_store,
        ScriptedDecisionProvider(
            [envelope({"kind": "ask", "text": "몇 시인가요?"})]
        ),
        MemoryScheduleReader(),
        uuid_factory=uuids,
    )
    question = active_question(processor_store)
    follow_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    follow = request_data(
        follow_id,
        conversation_id=CONVERSATION_ID,
        input_payload={
            "type": "text",
            "questionId": str(question.id),
            "text": "오후 3시",
        },
    )
    admit(
        processor_store,
        follow,
        accepted_at=NOW + timedelta(hours=2),
    )
    provider = ScriptedDecisionProvider(
        [envelope({"kind": "ask", "text": "날짜도 알려 주세요."})]
    )

    run(
        processor_store,
        provider,
        MemoryScheduleReader(),
        request_id=follow_id,
        uuid_factory=uuids,
    )

    context = provider.contexts[0]
    assert context.is_question_follow_up is True
    assert context.answered_question_kind == "need_more_info"
    assert context.task_reference_accepted_at == NOW


def test_schedule_read_failure_maps_to_schedule_unavailable(processor_store) -> None:
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "삭제할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "delete",
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )

    run(processor_store, provider, FailingScheduleReader())
    assert stored_request(processor_store).result_json["code"] == "SCHEDULE_UNAVAILABLE"


def test_claim_allows_only_one_provider_call(processor_store) -> None:
    admit(processor_store, request_data())
    entered = Event()
    release = Event()

    class BlockingProvider:
        def __init__(self) -> None:
            self.calls = 0

        def decide(self, context: DecisionContext):
            del context
            self.calls += 1
            entered.set()
            assert release.wait(timeout=3)
            return envelope({"kind": "ask", "text": "질문"})

    provider = BlockingProvider()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(run, processor_store, provider, MemoryScheduleReader())
        assert entered.wait(timeout=3)
        second = executor.submit(run, processor_store, provider, MemoryScheduleReader())
        assert second.result(timeout=3) is ProcessOneResult.SKIPPED
        release.set()
        assert first.result(timeout=3) is ProcessOneResult.COMPLETED
    assert provider.calls == 1


def test_cancel_winner_discards_late_provider_result_and_control_state(
    processor_store,
) -> None:
    admit(processor_store, request_data())
    entered = Event()
    release = Event()

    class BlockingProvider:
        def decide(self, context: DecisionContext):
            del context
            entered.set()
            assert release.wait(timeout=3)
            return envelope(
                {"kind": "ask", "text": "늦은 질문"}, state={"late": True}
            )

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            run, processor_store, BlockingProvider(), MemoryScheduleReader()
        )
        assert entered.wait(timeout=3)
        response = cancel_assistant_target(
            processor_store,
            conversation_id=CONVERSATION_ID,
            target=AssistantCancelTarget.model_validate(
                {"type": "request", "id": str(REQUEST_ID)}
            ),
            now=NOW + timedelta(seconds=1),
        )
        assert response.target_state.value == "cancelled"
        release.set()
        assert future.result(timeout=3) is ProcessOneResult.LOST_FINALIZE

    with processor_store() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        conversation = session.get(AssistantConversation, CONVERSATION_ID)
        assert request is not None and request.result_json["outcome"] == "cancelled"
        assert conversation is not None and conversation.control_state_json is None
        assert session.scalar(sa.select(sa.func.count()).select_from(AssistantQuestion)) == 0


def test_schedule_read_holds_no_immediate_lock_and_cancel_wins(processor_store) -> None:
    admit(processor_store, request_data())
    entered = Event()
    release = Event()
    reader = BlockingScheduleReader(schedule(), entered, release)
    provider = ScriptedDecisionProvider(
        [
            envelope(
                {
                    "kind": "confirm",
                    "text": "삭제할까요?",
                    "scheduleId": str(SCHEDULE_A),
                    "operation": "delete",
                    "timeInterpretation": "absolute",
                }
            )
        ]
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run, processor_store, provider, reader)
        assert entered.wait(timeout=3)
        cancelled = cancel_assistant_target(
            processor_store,
            conversation_id=CONVERSATION_ID,
            target=AssistantCancelTarget.model_validate(
                {"type": "request", "id": str(REQUEST_ID)}
            ),
            now=NOW + timedelta(seconds=1),
        )
        assert cancelled.target_state.value == "cancelled"
        release.set()
        assert future.result(timeout=3) is ProcessOneResult.LOST_FINALIZE


def test_question_and_result_roll_back_together_on_finalize_failure(
    processor_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    admit(processor_store, request_data())
    provider = ScriptedDecisionProvider(
        [envelope({"kind": "ask", "text": "질문"}, state={"step": 2})]
    )
    original = repository.add_question

    def fail_after_add(session, question):
        original(session, question)
        raise RuntimeError("injected finalize failure")

    monkeypatch.setattr(repository, "add_question", fail_after_add)

    with pytest.raises(RuntimeError, match="injected finalize failure"):
        run(processor_store, provider, MemoryScheduleReader())

    with processor_store() as session:
        request = session.get(AssistantRequest, REQUEST_ID)
        conversation = session.get(AssistantConversation, CONVERSATION_ID)
        assert request is not None and request.status == "processing"
        assert request.result_json is None
        assert conversation is not None and conversation.control_state_json is None
        assert session.scalar(sa.select(sa.func.count()).select_from(AssistantQuestion)) == 0
