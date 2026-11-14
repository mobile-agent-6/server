"""Stored model outputs through public schemas, admission, processing and SQLite.

No live model, HTTP server, POSIX ownership lock, or developer database is used.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.core.clock import FixedClock, FixedMonotonicClock
from app.core.config import Settings
from app.core.database import create_api_engine, create_assistant_engine, create_session_factory
from app.modules.assistant.errors import AssistantStaleQuestion
from app.modules.assistant.evidence import texts
from app.modules.assistant.evidence.model_client import render_user_message
from app.modules.assistant.evidence.rules.correction_bridge_v37 import make_provider
from app.modules.assistant.processor import process_one
from app.modules.assistant.runtime_guard import ProcessDeadlineGuard
from app.modules.assistant.safe_write import AssistantSafeWriter
from app.modules.assistant.schedule_read import SqlAlchemyScheduleReader
from app.modules.assistant.schemas import AssistantRequestCreate
from app.modules.assistant.state_service import admit_assistant_request, get_assistant_request
from app.modules.schedules.category import Category
from app.modules.schedules.model import Schedule

BACKEND = Path(__file__).resolve().parents[2]
BUNDLE = BACKEND.parent / "datasets/qwen/experiments/logistics440_v37"
CASES = json.loads((BUNDLE / "data/multiturn60/scenarios60.json").read_text(encoding="utf-8"))["cases"]


@pytest.fixture(scope="module")
def v37_cache():
    base = BUNDLE / "evaluation/full60"
    result = {}
    for index, line in enumerate((base / "inputs.jsonl").read_text(encoding="utf-8").splitlines(), 1):
        item = json.loads(line)
        result[item["messages"][1]["content"]] = json.loads(
            (base / "raw" / f"{index:03}.json").read_text(encoding="utf-8")
        )["raw"]
    assert len(result) == 132
    return result


@pytest.fixture(scope="module")
def v37_empty_database(tmp_path_factory):
    path = tmp_path_factory.mktemp("v37-template") / "empty.db"
    config = Config(str(BACKEND / "alembic.ini"))
    config.attributes["database_url"] = f"sqlite:///{path.as_posix()}"
    command.upgrade(config, "head")
    return path


class CachedModel:
    name = "stored440"
    prompt_sha256 = "stored-output-replay"

    def __init__(self, cache):
        self.cache = cache
        self.calls = []

    def extract(self, *, turns, reference_time, server_time_trusted, timeout_seconds=None):
        self.calls.append(list(turns))
        key = render_user_message(turns, reference_time=reference_time, server_time_trusted=server_time_trusted)
        # Never substitute gold or a different turn history for an unavailable output.
        return self.cache[key]


class TrustedTime:
    def is_trusted(self):
        return True


class Replay:
    def __init__(self, case, assistant_sessions, schedule_sessions, cache):
        self.case = case
        self.af, self.sf = assistant_sessions, schedule_sessions
        self.now = datetime.fromisoformat(case["referenceTime"])
        self.clock = FixedClock(self.now)
        self.reader = SqlAlchemyScheduleReader(self.sf)
        self.model = CachedModel(cache)
        self.provider = make_provider(self.model, self.reader)
        self.writer = AssistantSafeWriter(
            assistant_session_factory=self.af, schedule_session_factory=self.sf,
            clock=self.clock, uuid_factory=uuid4,
        )
        self.conversation_id = None
        self.previous = None
        with self.sf.begin() as session:
            for row in case["initialSchedules"]:
                session.add(Schedule(
                    id=UUID(row["id"]), title=row["title"],
                    scheduled_at=datetime.fromisoformat(row["scheduledAt"].replace("Z", "+00:00")),
                    category=Category(row["category"]), completed=row["completed"],
                    created_at=self.now, updated_at=self.now, deleted_at=None,
                ))

    def snapshot(self):
        with self.sf() as session:
            return {str(r.id): (r.title, r.scheduled_at, r.category, r.completed, r.updated_at, r.deleted_at)
                    for r in session.scalars(sa.select(Schedule)).all()}

    def rows(self):
        with self.sf() as session:
            return ordered([{
                "title": r.title,
                "scheduledAt": r.scheduled_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "category": r.category.value, "completed": r.completed,
            } for r in session.scalars(sa.select(Schedule)).all() if r.deleted_at is None])

    def send(self, input):
        request_id = uuid4()
        payload = {"requestId": str(request_id), "input": input}
        if self.conversation_id is not None:
            payload["conversationId"] = str(self.conversation_id)
        data = AssistantRequestCreate.model_validate(payload)
        accepted = admit_assistant_request(
            self.af, data=data, accepted_at=self.now, new_conversation_id=uuid4(),
            runtime_ready=True, provider_available=True,
        )
        self.conversation_id = accepted.conversation_id
        process_one(
            self.af, request_id=request_id, provider=self.provider, schedule_reader=self.reader,
            clock=self.clock, time_trust_source=TrustedTime(), uuid_factory=uuid4,
            runtime_guard=ProcessDeadlineGuard(FixedMonotonicClock(0), 300, Event()),
            safe_writer=self.writer,
        )
        status = get_assistant_request(self.af, request_id=request_id).model_dump(
            mode="json", by_alias=True, exclude_none=True,
        )
        assert status["status"] == "completed"
        self.previous = status["result"]
        return self.previous

    def turn(self, turn):
        input = {"type": turn["inputType"]}
        if turn["inputType"] == "text":
            input["text"] = turn["text"]
            if turn.get("answersPreviousQuestion"):
                input["questionId"] = self.previous["questionId"]
        else:
            input["questionId"] = self.previous["questionId"]
            if turn["inputType"] == "confirmation":
                input["approved"] = turn["approved"]
            else:
                input["candidateId"] = self.previous["candidates"][turn["candidateIndex"]]["candidateId"]
        return self.send(input)

    def until(self, kind):
        for turn in self.case["turns"]:
            result = self.turn(turn)
            if result["kind"] == kind:
                return result
        raise AssertionError(f"Question {kind} was not reached")


def ordered(rows):
    return sorted(rows, key=lambda row: (row["scheduledAt"], row["title"]))


@pytest.fixture
def v37_replay(tmp_path, v37_cache, v37_empty_database):
    @contextmanager
    def make(case):
        path = tmp_path / f"{uuid4()}.db"
        with closing(sqlite3.connect(v37_empty_database)) as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
        settings = Settings(_env_file=None, database_url=f"sqlite:///{path.as_posix()}")
        assistant_engine, schedule_engine = create_assistant_engine(settings), create_api_engine(settings)
        try:
            yield Replay(case, create_session_factory(assistant_engine), create_session_factory(schedule_engine), v37_cache)
        finally:
            assistant_engine.dispose()
            schedule_engine.dispose()
    return make


@pytest.mark.parametrize("case", CASES, ids=[case["caseId"] for case in CASES])
def test_v37_dialogue_results_and_database(case, v37_replay):
    with v37_replay(case) as replay:
        for turn in case["turns"]:
            before, calls = replay.snapshot(), len(replay.model.calls)
            result = replay.turn(turn)
            for key, expected in turn["expectedResult"].items():
                assert result.get(key) == expected, (key, result)
            assert replay.rows() == ordered(turn["expectedSchedules"])
            assert len(replay.model.calls) == turn["expectedModelCalls"]
            if turn["inputType"] != "text":
                assert len(replay.model.calls) == calls
            after = replay.snapshot()
            changed = [key for key in before.keys() | after.keys() if before.get(key) != after.get(key)]
            assert len(changed) <= 1
            if changed and result.get("operation") != "create":
                assert turn["inputType"] == "confirmation" and turn["approved"] is True
        assert replay.rows() == ordered(case["expectedFinalSchedules"])


CONFIRM_CASES = [next(case for case in CASES if case["feature"] == feature
                     and any(t["inputType"] == "confirmation" for t in case["turns"]))
                 for feature in ("update", "delete", "completed_update")]


@pytest.mark.parametrize("case", CONFIRM_CASES, ids=[c["feature"] for c in CONFIRM_CASES])
def test_v37_rejection_and_stale_approval_never_write(case, v37_replay):
    with v37_replay(case) as replay:
        question = replay.until("confirmation")
        before, calls = replay.snapshot(), len(replay.model.calls)
        result = replay.send({"type": "confirmation", "questionId": question["questionId"], "approved": False})
        assert result["outcome"] == "cancelled"
        assert replay.snapshot() == before
        with pytest.raises(AssistantStaleQuestion):
            replay.send({"type": "confirmation", "questionId": question["questionId"], "approved": True})
        assert replay.snapshot() == before
        assert len(replay.model.calls) == calls


@pytest.mark.parametrize("case", CONFIRM_CASES, ids=[c["feature"] for c in CONFIRM_CASES])
def test_v37_concurrent_edit_requires_new_approval(case, v37_replay):
    with v37_replay(case) as replay:
        question = replay.until("confirmation")
        with replay.sf.begin() as session:
            target = session.get(Schedule, UUID(question["target"]["id"]))
            target.title += " 외부수정"
            target.updated_at = replay.now + timedelta(seconds=1)
        before, calls = replay.snapshot(), len(replay.model.calls)
        result = replay.send({"type": "confirmation", "questionId": question["questionId"], "approved": True})
        assert result["kind"] == "confirmation"
        assert result["questionId"] != question["questionId"]
        assert replay.snapshot() == before
        assert len(replay.model.calls) == calls


def test_v37_unissued_candidate_is_rejected(v37_replay):
    case = next(case for case in CASES if any(t["inputType"] == "selection" for t in case["turns"]))
    with v37_replay(case) as replay:
        question = replay.until("candidate_selection")
        before, calls = replay.snapshot(), len(replay.model.calls)
        with pytest.raises(AssistantStaleQuestion):
            replay.send({"type": "selection", "questionId": question["questionId"], "candidateId": "unissued-candidate"})
        assert replay.snapshot() == before
        assert len(replay.model.calls) == calls


def test_v37_missing_query_scope_asks_before_listing(v37_replay):
    loading_id = "c6acb391-4ae7-4d78-bb17-719ea4d187e5"
    case = {
        "referenceTime": "2026-11-14T10:00:00+09:00",
        "initialSchedules": [
            {
                "id": loading_id,
                "title": "상차 화물 무게 배분 확인",
                "scheduledAt": "2026-11-15T05:00:00Z",
                "category": "OTHER",
                "completed": False,
            },
            {
                "id": "49a1f0e0-c5ea-4775-9fd8-e29afb7dfa05",
                "title": "입고 검수",
                "scheduledAt": "2026-11-15T01:00:00Z",
                "category": "INBOUND",
                "completed": False,
            },
        ],
    }
    first_turn = "일정 좀 찾아 줘"
    second_turn = "제목이 상차 화물 무게 배분 확인인 걸 찾고 있어"
    first_raw = json.dumps(
        {
            "operations": [
                {
                    "index": 1,
                    "kind": "list",
                    "targetEvidence": {},
                    "changeEvidence": {},
                    "filterEvidence": {
                        "title": [
                            {
                                "turn": 1,
                                "text": first_turn,
                                "status": "adopted",
                            }
                        ]
                    },
                    "unsupportedEvidence": [],
                    "missingInformation": [],
                }
            ],
            "unsupportedEvidence": [],
            "missingInformation": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    second_raw = json.dumps(
        {
            "operations": [
                {
                    "index": 1,
                    "kind": "list",
                    "targetEvidence": {},
                    "changeEvidence": {},
                    "filterEvidence": {
                        "title": [
                            {
                                "turn": 2,
                                "text": "상차 화물 무게 배분 확인",
                                "status": "adopted",
                            }
                        ]
                    },
                    "unsupportedEvidence": [],
                    "missingInformation": [],
                }
            ],
            "unsupportedEvidence": [],
            "missingInformation": [],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with v37_replay(case) as replay:
        replay.model.cache.update(
            {
                render_user_message(
                    [first_turn],
                    reference_time=replay.now,
                    server_time_trusted=True,
                ): first_raw,
                render_user_message(
                    [first_turn, second_turn],
                    reference_time=replay.now,
                    server_time_trusted=True,
                ): second_raw,
            }
        )
        before = replay.snapshot()

        question = replay.send({"type": "text", "text": first_turn})

        assert question == {
            "kind": "need_more_info",
            "text": texts.ASK_TARGET_TITLE,
            "questionId": question["questionId"],
        }
        assert replay.snapshot() == before

        result = replay.send(
            {
                "type": "text",
                "text": second_turn,
                "questionId": question["questionId"],
            }
        )

        assert result["kind"] == "outcome"
        assert result["operation"] == "list"
        assert result["schedulesChanged"] is False
        assert [item["id"] for item in result["items"]] == [loading_id]
        assert replay.snapshot() == before
        assert replay.model.calls == [[first_turn], [first_turn, second_turn]]
