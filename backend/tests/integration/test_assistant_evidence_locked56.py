"""Issue #30 locked56 replayed through the real Assistant API with gold evidence.

The model is replaced by the gold evidence output of each case, so this checks
the backend part only: evidence checks, value conversion, decisions, questions
and Schedule writes.  The bar (Issue #37 M1): no unsafe write, and at least the
26 correct cases the Issue #30 dry mapping reached.
"""

from __future__ import annotations

import copy
import json
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.clock import FixedClock
from app.core.config import Settings
from app.main import create_app
from app.modules.assistant.evidence.provider import EvidenceDecisionProvider
from app.modules.assistant.schedule_read import ScheduleReadPort, SqlAlchemyScheduleReader
from app.modules.schedules.category import Category
from app.modules.schedules.model import Schedule
from tests.conftest import migrate

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "evidence_locked56.json").read_text(encoding="utf-8")
)
CASES = FIXTURE["cases"]
MIN_CORRECT = 26


class _Trust:
    def __init__(self, trusted: bool) -> None:
        self._trusted = trusted

    def is_trusted(self) -> bool:
        return self._trusted


class _LazyReader:
    """The app creates its session factory at startup; bind the reader after that."""

    def __init__(self) -> None:
        self.inner: ScheduleReadPort | None = None

    def get(self, schedule_id):
        assert self.inner is not None
        return self.inner.get(schedule_id)

    def list(self, **kwargs):
        assert self.inner is not None
        return self.inner.list(**kwargs)


class _GoldModel:
    """Return the gold evidence for the case turn being replayed, renumbered to the
    provider's own turn list (it only keeps the text turns of the current task)."""

    def __init__(self, case: dict) -> None:
        self.case = case
        self.text_turns = [t["turn"] for t in case["turns"] if t["inputType"] == "text"]
        self.current_turn: int | None = None
        self.calls = 0

    def extract(self, *, turns, reference_time, server_time_trusted) -> str:
        self.calls += 1
        assert self.current_turn is not None
        seen = [number for number in self.text_turns if number <= self.current_turn]
        window = seen[-len(turns):]
        mapping = {case_turn: index for index, case_turn in enumerate(window, 1)}
        gold = copy.deepcopy(self.case["goldByTurn"][str(self.current_turn)])

        def renumber(value):
            if isinstance(value, dict):
                if {"turn", "text", "status"} <= value.keys():
                    value["turn"] = mapping.get(value["turn"], 999)
                for item in value.values():
                    renumber(item)
            elif isinstance(value, list):
                for item in value:
                    renumber(item)

        renumber(gold)
        return json.dumps(gold, ensure_ascii=False)


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _row_key(title: str, scheduled_at: datetime, category: str, completed: bool) -> tuple:
    return (title, scheduled_at.astimezone(timezone.utc), category, completed)


def _expected_final(case: dict) -> Counter:
    by_ref = {s["ref"]: dict(s) for s in case["initialSchedules"] if not s.get("deleted")}
    created = []
    for turn in case["expectedTurns"]:
        write = turn.get("write")
        if not write:
            continue
        values = write.get("values", {})
        if write["operation"] == "create":
            created.append(
                {
                    "title": values["title"],
                    "scheduledAt": values["scheduledAt"],
                    "category": values["category"],
                    "completed": False,
                }
            )
        elif write["operation"] == "update":
            target = by_ref[write["targetRef"]]
            for key in ("title", "scheduledAt", "category", "completed"):
                if key in values:
                    target[key] = values[key]
        elif write["operation"] == "delete":
            by_ref.pop(write["targetRef"], None)
    rows = list(by_ref.values()) + created
    return Counter(_row_key(r["title"], _instant(r["scheduledAt"]), r["category"], r["completed"]) for r in rows)


def _initial(case: dict) -> Counter:
    return Counter(
        _row_key(s["title"], _instant(s["scheduledAt"]), s["category"], s["completed"])
        for s in case["initialSchedules"]
        if not s.get("deleted")
    )


def _actual(application) -> Counter:
    with application.state.session_factory() as session:
        rows = session.scalars(sa.select(Schedule).where(Schedule.deleted_at.is_(None))).all()
        return Counter(_row_key(r.title, r.scheduled_at, r.category.value, r.completed) for r in rows)


def _seed(application, case: dict) -> None:
    with application.state.session_factory() as session, session.begin():
        for s in case["initialSchedules"]:
            session.add(
                Schedule(
                    id=UUID(s["id"]),
                    title=s["title"],
                    scheduled_at=_instant(s["scheduledAt"]),
                    category=Category(s["category"]),
                    completed=s["completed"],
                    created_at=_instant(s["createdAt"]),
                    updated_at=_instant(s["updatedAt"]),
                    deleted_at=_instant(s["updatedAt"]) if s.get("deleted") else None,
                )
            )


def _wait_completed(client: TestClient, request_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/assistant/requests/{request_id}").json()
        if body.get("status") == "completed":
            return body
        Event().wait(0.005)
    raise AssertionError("request did not complete")


@pytest.fixture(scope="module")
def template_db(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("locked56") / "template.db"
    migrate(path)
    return path


def replay(case: dict, template_db: Path, tmp_path: Path) -> tuple[str, list[str]]:
    """Return ("correct" | "safe" | "unsafe", per-turn notes)."""

    database = tmp_path / "case.db"
    shutil.copy(template_db, database)
    model = _GoldModel(case)
    reader = _LazyReader()
    provider = EvidenceDecisionProvider(model, reader)
    application = create_app(
        Settings(_env_file=None, database_url=f"sqlite:///{database}"),
        _assistant_runtime_ready_for_tests=True,
        _assistant_decision_provider_for_tests=provider,
        _assistant_time_trust_source_for_tests=_Trust(case["serverTimeTrusted"]),
        _assistant_uuid_factory_for_tests=uuid4,
    )
    application.state.clock = FixedClock(_instant(case["referenceTime"]))
    notes: list[str] = []
    turns_match = True
    try:
        with TestClient(application, raise_server_exceptions=False) as client:
            reader.inner = SqlAlchemyScheduleReader(application.state.session_factory)
            _seed(application, case)
            expected = {e["turn"]: e for e in case["expectedTurns"]}
            conversation_id: str | None = None
            last_result: dict | None = None
            by_ref = {s["ref"]: s for s in case["initialSchedules"]}
            for turn in case["turns"]:
                request_id = str(uuid4())
                payload: dict = {"requestId": request_id}
                if conversation_id is not None:
                    payload["conversationId"] = conversation_id
                kind = turn["inputType"]
                question_id = (last_result or {}).get("questionId")
                if kind == "text":
                    model.current_turn = turn["turn"]
                    payload["input"] = {"type": "text", "text": turn["text"]}
                    if turn.get("answersQuestionOfTurn") is not None and question_id:
                        payload["input"]["questionId"] = question_id
                elif kind == "selection":
                    target = by_ref[turn["selectedScheduleRef"]]
                    wanted = (target["title"], _instant(target["scheduledAt"]))
                    candidates = (last_result or {}).get("candidates") or []
                    chosen = [
                        c
                        for c in candidates
                        if (c["title"], _instant(c["scheduledAt"])) == wanted
                    ]
                    if not chosen or not question_id:
                        notes.append(f"t{turn['turn']}: no matching candidate to select")
                        turns_match = False
                        break
                    payload["input"] = {
                        "type": "selection",
                        "questionId": question_id,
                        "candidateId": chosen[0]["candidateId"],
                    }
                elif kind == "confirmation":
                    if not question_id or (last_result or {}).get("kind") != "confirmation":
                        notes.append(f"t{turn['turn']}: no confirmation question")
                        turns_match = False
                        break
                    payload["input"] = {
                        "type": "confirmation",
                        "questionId": question_id,
                        "approved": turn["approved"],
                    }
                else:
                    notes.append(f"t{turn['turn']}: unsupported input {kind}")
                    turns_match = False
                    break
                response = client.post("/api/v1/assistant/requests", json=payload)
                if response.status_code != 202:
                    notes.append(f"t{turn['turn']}: HTTP {response.status_code}")
                    turns_match = False
                    break
                body = _wait_completed(client, request_id)
                conversation_id = body["conversationId"]
                last_result = body["result"]
                want = expected.get(turn["turn"])
                got = (last_result.get("kind"), last_result.get("outcome"))
                if want is not None and (want["kind"], want.get("outcome")) != got:
                    turns_match = False
                    notes.append(f"t{turn['turn']}: got {got} want {(want['kind'], want.get('outcome'))}")
            actual = _actual(application)
    finally:
        application.state.engine.dispose()
    initial = _initial(case)
    expected_final = _expected_final(case)
    # Unsafe means a write the case never asked for.  Doing only part of the
    # expected writes (for example the first of two creates) is safe.
    if (actual - initial) - (expected_final - initial) or (initial - actual) - (initial - expected_final):
        return "unsafe", notes + ["unexpected schedule change"]
    if turns_match and actual == expected_final:
        return "correct", notes
    return "safe", notes


RESULTS: dict[str, tuple[str, list[str]]] = {}


@pytest.mark.slow
@pytest.mark.parametrize("case", CASES, ids=[c["caseId"][:4] for c in CASES])
def test_locked_case_never_writes_unsafely(case: dict, template_db: Path, tmp_path: Path) -> None:
    verdict, notes = replay(case, template_db, tmp_path)
    RESULTS[case["caseId"]] = (verdict, notes)
    assert verdict != "unsafe", notes


@pytest.mark.slow
def test_locked56_reaches_dry_mapping_level() -> None:
    if len(RESULTS) != len(CASES):
        pytest.skip("run together with the per-case replay tests")
    correct = sum(1 for verdict, _ in RESULTS.values() if verdict == "correct")
    assert correct >= MIN_CORRECT, sorted((k[:4], v) for k, (v, _) in RESULTS.items() if v != "correct")
