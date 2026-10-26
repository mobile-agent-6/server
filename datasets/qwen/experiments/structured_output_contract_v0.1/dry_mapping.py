"""PROPOSED Issue #30 DecisionEnvelope and processor dry-mapping harness.

The harness deliberately imports production private planning functions.  It is
research-only evidence, not a supported backend import boundary.  It never
calls ``process_one``, ``_finalize``, ``SafeWritePort.execute``, a DB/session
factory, an HTTP client, or a model transport.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(BACKEND_ROOT))

from pydantic import TypeAdapter, ValidationError  # noqa: E402

from app.modules.assistant.decisions import (  # noqa: E402
    DecisionContext,
    DecisionEnvelope,
)
from app.modules.assistant.enums import (  # noqa: E402
    AssistantFailureCode,
    AssistantFailureNextAction,
)
from app.modules.assistant.processor import (  # noqa: E402
    INTERNAL_ERROR_TEXT,
    MODEL_OUTPUT_INVALID_TEXT,
    _InvalidDecision,
    _StoredInput,
    _call_provider,
    _failure,
    _from_saved_confirmation,
    _from_saved_selection,
    _prepare,
    _provider_decision_plan,
)
from app.modules.assistant.schedule_read import ScheduleRead  # noqa: E402
from app.modules.assistant.schemas import AssistantResult  # noqa: E402
from app.modules.schedules.category import Category  # noqa: E402
from contracts import (  # noqa: E402
    ContractError,
    candidate_a_context,
    common_backend_gate,
    normalize_text,
    validate_candidate_a,
    validate_candidate_b,
    validate_candidate_c,
    validate_rule_intent,
)
from rule_baseline import assess_rule_case, extract_rule_slots  # noqa: E402
from validate_contracts import (  # noqa: E402
    EXPECTED_LOCKED_SHA256,
    EXPECTED_SCHEMA_SHA256,
    LOCKED_PATH,
    classify_candidate_a,
    classify_candidate_b,
    classify_candidate_c,
    load_jsonl,
)

DRY_MAPPING_VERSION = "issue30-dry-mapping-v0.1"
CONTRACT_MANIFEST_PATH = HERE / "contract-validation-manifest-v0.1.json"
RESULT_ADAPTER = TypeAdapter(AssistantResult)
SEOUL = timezone(timedelta(hours=9))

ACTUAL_EXTERNAL_CALLS = {
    "model": 0,
    "server": 0,
    "database": 0,
    "scheduleApi": 0,
    "scheduleWrite": 0,
}

PRODUCTION_FUNCTIONS_USED = [
    "app.modules.assistant.processor._provider_decision_plan",
    "app.modules.assistant.processor._prepare",
    "app.modules.assistant.processor._from_saved_selection",
    "app.modules.assistant.processor._from_saved_confirmation",
    "app.modules.assistant.processor._call_provider",
    "app.modules.assistant.processor._failure",
]

EXPRESSIBILITY_FINDINGS = [
    {
        "id": "E01",
        "question": "create progress question as need_more_info",
        "classification": "current_code_and_public_api",
    },
    {
        "id": "E02",
        "question": "completed state on candidate selection response",
        "classification": "public_api_change_required",
    },
    {
        "id": "E03",
        "question": "one write success plus next-operation question in one result",
        "classification": "public_api_change_required",
    },
    {
        "id": "E04",
        "question": "detailed success text derived from the committed change",
        "classification": "backend_internal_implementation_required",
    },
    {
        "id": "E05",
        "question": "result kind for unsupported-only request",
        "classification": "public_api_change_required",
    },
    {
        "id": "E06",
        "question": "post-list text derived from actual returned items",
        "classification": "backend_internal_implementation_required",
    },
    {
        "id": "E07",
        "question": "all-retention title search for undated update/delete",
        "classification": "backend_internal_implementation_required",
    },
    {
        "id": "E08",
        "question": "natural zero-target update/delete result",
        "classification": "backend_internal_implementation_required",
    },
    {
        "id": "E09",
        "question": "ask before choosing distinguishable identical-display candidates",
        "classification": "backend_internal_implementation_required",
    },
    {
        "id": "E10",
        "question": "link control state to the original question and clear it on completion",
        "classification": "backend_internal_implementation_required",
    },
    {
        "id": "E11",
        "question": "deterministic title-similarity policy in the adapter",
        "classification": "backend_internal_implementation_required",
    },
    {
        "id": "E12",
        "question": "ordered multi-operation queue in current control state/result union",
        "classification": "public_api_change_required",
    },
]

FRONTEND_TECHNICAL_IMPACTS = [
    "AssistantCandidate has no completed field.",
    "AssistantResult is a single-kind union and cannot combine success with a next question.",
    "LIST text is supplied before ScheduleReadPort.list returns actual items.",
    "candidate_selection exposes opaque candidateId, title, scheduledAt, and category only.",
    "confirmation exposes the concrete target and update changes; no write has occurred yet.",
    "write success exposes one affected Schedule ID but no committed before/after detail.",
    "TTS has only result.text; cards use typed result fields.",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_fixed_inputs() -> dict[str, Any]:
    locked_manifest = json.loads((HERE / "locked-cases-manifest-v0.1.json").read_text())
    contract_manifest = json.loads(CONTRACT_MANIFEST_PATH.read_text())
    cases = load_jsonl(LOCKED_PATH)
    if locked_manifest.get("lockStatus") != "locked":
        raise RuntimeError("LOCK_STATUS_CHANGED")
    if len(cases) != 56 or locked_manifest.get("caseCount") != 56:
        raise RuntimeError("LOCKED_CASE_COUNT_CHANGED")
    if sha256(LOCKED_PATH) != EXPECTED_LOCKED_SHA256:
        raise RuntimeError("LOCKED_JSONL_HASH_CHANGED")
    schema_path = HERE / "semantic-oracle-schema-v0.1.json"
    if sha256(schema_path) != EXPECTED_SCHEMA_SHA256:
        raise RuntimeError("SEMANTIC_SCHEMA_HASH_CHANGED")
    mismatches: dict[str, dict[str, str]] = {}
    for name, metadata in contract_manifest["artifacts"].items():
        actual = sha256(HERE / name)
        if actual != metadata["sha256"]:
            mismatches[name] = {"expected": metadata["sha256"], "actual": actual}
    if mismatches:
        raise RuntimeError(f"CONTRACT_ARTIFACT_CHANGED:{mismatches}")
    return {
        "lockStatus": "locked",
        "caseCount": 56,
        "lockedSha256": EXPECTED_LOCKED_SHA256,
        "schemaSha256": EXPECTED_SCHEMA_SHA256,
        "contractManifestSha256": sha256(CONTRACT_MANIFEST_PATH),
        "contractArtifactCount": len(contract_manifest["artifacts"]),
    }


class DeterministicUuidFactory:
    """Stable RFC-variant UUIDv4 values derived from a case/candidate seed."""

    def __init__(self, seed: str) -> None:
        self.seed = seed
        self.counter = 0
        self.values: list[UUID] = []

    def __call__(self) -> UUID:
        self.counter += 1
        digest = hashlib.sha256(f"{self.seed}:{self.counter}".encode()).digest()
        value = UUID(bytes=digest[:16], version=4)
        self.values.append(value)
        return value


class FixedIssueClock:
    """A fixed fixture clock; it never reads the wall clock."""

    def __init__(self, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fixed clock must be timezone-aware")
        self.value = value.astimezone(timezone.utc)
        self.calls = 0

    def now(self) -> datetime:
        self.calls += 1
        return self.value


class FakeScheduleReadPort:
    """Read-only port backed only by one locked case's initialSchedules."""

    def __init__(self, schedules: list[Mapping[str, Any]]) -> None:
        self._all = tuple(dict(item) for item in schedules)
        self._active: dict[UUID, ScheduleRead] = {}
        self.ref_by_id: dict[UUID, str] = {}
        self.id_by_ref: dict[str, UUID] = {}
        self.calls: list[dict[str, Any]] = []
        for item in self._all:
            identifier = UUID(str(item["id"]))
            self.ref_by_id[identifier] = str(item["ref"])
            self.id_by_ref[str(item["ref"])] = identifier
            if item.get("deleted") is True:
                continue
            self._active[identifier] = ScheduleRead(
                id=identifier,
                title=str(item["title"]),
                scheduled_at=_parse_datetime(str(item["scheduledAt"])).astimezone(timezone.utc),
                category=Category(str(item["category"])),
                completed=bool(item["completed"]),
                updated_at=_parse_datetime(str(item["updatedAt"])).astimezone(timezone.utc),
            )

    @property
    def active_ids(self) -> set[str]:
        return {str(value) for value in self._active}

    @property
    def deleted_ids(self) -> set[str]:
        return {
            str(item["id"])
            for item in self._all
            if item.get("deleted") is True
        }

    def get(self, schedule_id: UUID) -> ScheduleRead | None:
        self.calls.append({"method": "get", "scheduleId": str(schedule_id)})
        return self._active.get(schedule_id)

    def list(
        self,
        *,
        from_utc: datetime,
        to_utc: datetime,
        category: Category | None,
        completed: bool | None,
        limit: int,
    ) -> list[ScheduleRead]:
        if from_utc.tzinfo is None or to_utc.tzinfo is None:
            raise ValueError("fake read bounds must be timezone-aware")
        if not from_utc < to_utc <= from_utc + timedelta(days=92):
            raise ValueError("fake read range must be positive and at most 92 days")
        if not 1 <= limit <= 101:
            raise ValueError("fake read limit must be 1..101")
        self.calls.append(
            {
                "method": "list",
                "from": from_utc.astimezone(timezone.utc).isoformat(),
                "to": to_utc.astimezone(timezone.utc).isoformat(),
                "category": None if category is None else category.value,
                "completed": completed,
                "limit": limit,
            }
        )
        values = [
            item
            for item in self._active.values()
            if from_utc <= item.scheduled_at < to_utc
            and (category is None or item.category is category)
            and (completed is None or item.completed is completed)
        ]
        return sorted(values, key=lambda item: (item.scheduled_at, item.id))[:limit]

    def adapter_title_search(
        self,
        title: str,
        *,
        target_date: date | None = None,
    ) -> list[ScheduleRead]:
        """Research adapter evidence for missing production all-period title search (I5)."""
        normalized_query = _match_text(title)
        self.calls.append(
            {
                "method": "adapter_title_search",
                "title": normalized_query,
                "targetDate": None if target_date is None else target_date.isoformat(),
            }
        )
        exact: list[ScheduleRead] = []
        partial: list[ScheduleRead] = []
        for item in self._active.values():
            if target_date is not None and item.scheduled_at.astimezone(SEOUL).date() != target_date:
                continue
            normalized_title = _match_text(item.title)
            if normalized_title == normalized_query:
                exact.append(item)
            elif normalized_query in normalized_title or normalized_title in normalized_query:
                partial.append(item)
        key = lambda item: (item.scheduled_at, item.id)
        return sorted(exact, key=key) + sorted(partial, key=key)


@dataclass
class OperationView:
    kind: str
    target_title: str | None = None
    target_date: date | None = None
    title: str | None = None
    scheduled_at: datetime | None = None
    category: str | None = None
    completed: bool | None = None
    query_from: datetime | None = None
    query_to: datetime | None = None
    query_title: str | None = None
    query_category: str | None = None
    query_completed: bool | None = None
    time_interpretation: str = "absolute"
    time_only: tuple[int, int] | None = None
    changes: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    unsupported: bool = False


@dataclass
class TextMapping:
    local_validation: str
    common_gate: str
    mapping_result: str
    envelope: DecisionEnvelope | None
    decision_kind: str | None
    cause: str | None = None
    reason: str | None = None
    contract_expressibility: str = "current_code_and_public_api"


class CurrentContractUnrepresentable(ValueError):
    """A validated candidate output has no equivalent DecisionEnvelope variant."""


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _match_text(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _span_texts(payload: Mapping[str, Any], role: str, slot: str) -> list[str]:
    return [
        str(span["text"])
        for span in payload.get(role, {}).get(slot, [])
        if span.get("status") == "adopted"
    ]


def _date_from_texts(texts: list[str], reference: datetime) -> tuple[date | None, str]:
    combined = " ".join(texts)
    full = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", combined)
    if full:
        return date(*map(int, full.groups())), "absolute"
    month_day = re.search(r"(\d{1,2})월\s*(\d{1,2})일", combined)
    if month_day:
        month, day_value = map(int, month_day.groups())
        return date(reference.year, month, day_value), "relative"
    for word, offset in (("글피", 3), ("모레", 2), ("내일", 1), ("오늘", 0)):
        if word in combined:
            return reference.astimezone(SEOUL).date() + timedelta(days=offset), "relative"
    weekday = re.search(r"이번\s*주\s*([월화수목금토일])요일", combined)
    if weekday:
        index = "월화수목금토일".index(weekday.group(1))
        local = reference.astimezone(SEOUL).date()
        return local - timedelta(days=local.weekday()) + timedelta(days=index), "relative"
    return None, "absolute"


def _month_range(texts: list[str], reference: datetime) -> tuple[datetime, datetime] | None:
    combined = " ".join(texts)
    match = re.search(r"(?:(\d{4})년\s*)?(\d{1,2})월(?!\s*\d{1,2}일)", combined)
    if not match:
        return None
    year = int(match.group(1) or reference.astimezone(SEOUL).year)
    month = int(match.group(2))
    start = datetime(year, month, 1, tzinfo=SEOUL)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=SEOUL)
    return start, end


def _clock_from_texts(texts: list[str]) -> tuple[tuple[int, int] | None, bool]:
    combined = " ".join(texts)
    period_match = re.search(r"오전|오후|아침|저녁", combined)
    match = re.search(r"(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분|\s*(반))?", combined)
    if not match:
        return None, False
    hour = int(match.group(1))
    minute = 30 if match.group(3) else int(match.group(2) or 0)
    period = None if period_match is None else period_match.group(0)
    if period in {"오후", "저녁"} and hour < 12:
        hour += 12
    if period in {"오전", "아침"} and hour == 12:
        hour = 0
    ambiguous = period is None and 1 <= hour <= 12
    if ambiguous or not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, ambiguous
    return (hour, minute), False


def _category_from_texts(texts: list[str]) -> str | None:
    combined = " ".join(texts)
    for category, pattern in (
        ("INBOUND", r"입고"),
        ("INVENTORY", r"재고"),
        ("OUTBOUND", r"출고|피킹|포장"),
        ("RETURN", r"반품"),
    ):
        if re.search(pattern, combined):
            return category
    return None


def _completed_from_texts(texts: list[str]) -> bool | None:
    combined = " ".join(texts)
    if re.search(r"미완료|아직\s*안\s*끝|완료.*(?:취소|해제)", combined):
        return False
    if re.search(r"완료|끝났", combined):
        return True
    return None


def _filtered_c_output(case: Mapping[str, Any], through_turn: int) -> dict[str, Any]:
    """Build an ideal evidence-only fixture; normalized oracle values are never copied."""
    operations: list[dict[str, Any]] = []
    for operation in case["semantics"]["operations"]:
        converted: dict[str, Any] = {}
        for output_role, oracle_role in (
            ("targetEvidence", "target"),
            ("changeEvidence", "change"),
            ("filterEvidence", "filter"),
        ):
            slots: dict[str, list[dict[str, Any]]] = {}
            for slot, evidence in operation["evidence"][oracle_role].items():
                spans: list[dict[str, Any]] = []
                for key, status in (
                    ("adoptedSpans", "adopted"),
                    ("supersededSpans", "superseded"),
                    ("correctionMarkerSpans", "correction_marker"),
                ):
                    for span in evidence.get(key, []):
                        if span["turn"] <= through_turn:
                            value = {"turn": span["turn"], "text": span["text"], "status": status}
                            if "occurrence" in span:
                                value["occurrence"] = span["occurrence"]
                            spans.append(value)
                if spans and any(span["status"] == "adopted" for span in spans):
                    slots[slot] = spans
            converted[output_role] = slots
        date_role = {
            "create": "changeEvidence",
            "update": "changeEvidence",
            "list": "filterEvidence",
            "delete": "targetEvidence",
        }.get(str(operation["kind"]))
        if date_role is not None and "date" not in converted[date_role]:
            date_pattern = re.compile(
                r"\d{4}\uB144\s*\d{1,2}\uC6D4\s*\d{1,2}\uC77C|"
                r"\d{1,2}\uC6D4\s*\d{1,2}\uC77C|"
                r"\uC624\uB298|\uB0B4\uC77C|\uBAA8\uB808|\uAE00\uD53C|"
                r"\uC774\uBC88\s*\uC8FC\s*[\uC6D4\uD654\uC218\uBAA9\uAE08\uD1A0\uC77C]\uC694\uC77C"
            )
            visible_turns = [
                turn
                for turn in case["context"]["turns"]
                if turn["inputType"] == "text" and turn["turn"] <= through_turn
            ]
            for source_turn in reversed(visible_turns):
                match = date_pattern.search(source_turn["text"])
                if match is not None:
                    converted[date_role]["date"] = [
                        {
                            "turn": source_turn["turn"],
                            "text": match.group(0),
                            "status": "adopted",
                        }
                    ]
                    break
        unsupported: list[dict[str, Any]] = []
        for request in case["semantics"]["unsupportedRequests"]:
            if operation["index"] not in request.get("relatedOperationIndexes", []):
                continue
            for span in request["cue"].get("adoptedSpans", []):
                if span["turn"] <= through_turn:
                    unsupported.append({"turn": span["turn"], "text": span["text"], "status": "adopted"})
        operations.append(
            {
                "index": operation["index"],
                "kind": operation["kind"],
                **converted,
                "unsupportedEvidence": unsupported,
                "missingInformation": [],
            }
        )
    top_unsupported: list[dict[str, Any]] = []
    for request in case["semantics"]["unsupportedRequests"]:
        for span in request["cue"].get("adoptedSpans", []):
            if span["turn"] <= through_turn:
                top_unsupported.append({"turn": span["turn"], "text": span["text"], "status": "adopted"})
    return {"operations": operations, "unsupportedEvidence": top_unsupported, "missingInformation": []}


def _view_from_c_operation(
    operation: Mapping[str, Any],
    *,
    reference: datetime,
    server_time_trusted: bool,
) -> OperationView:
    kind = str(operation["kind"])
    target_title_texts = _span_texts(operation, "targetEvidence", "title")
    target_date_texts = _span_texts(operation, "targetEvidence", "date")
    change_title_texts = _span_texts(operation, "changeEvidence", "title")
    change_date_texts = _span_texts(operation, "changeEvidence", "date")
    change_time_texts = _span_texts(operation, "changeEvidence", "time")
    change_category_texts = _span_texts(operation, "changeEvidence", "category")
    change_completed_texts = _span_texts(operation, "changeEvidence", "completed")
    filter_date_texts = _span_texts(operation, "filterEvidence", "date")
    filter_title_texts = _span_texts(operation, "filterEvidence", "title")
    filter_category_texts = _span_texts(operation, "filterEvidence", "category")
    filter_completed_texts = _span_texts(operation, "filterEvidence", "completed")
    future_texts = _span_texts(operation, "filterEvidence", "futureScope")
    target_date, _ = _date_from_texts(target_date_texts, reference)
    change_date, date_interpretation = _date_from_texts(change_date_texts, reference)
    clock, ambiguous = _clock_from_texts(change_time_texts)
    view = OperationView(
        kind=kind,
        target_title=target_title_texts[-1] if target_title_texts else None,
        target_date=target_date,
        title=change_title_texts[-1] if change_title_texts else None,
        category=_category_from_texts(change_category_texts),
        completed=_completed_from_texts(change_completed_texts),
        unsupported=bool(operation.get("unsupportedEvidence")),
    )
    if kind == "create":
        if view.title is None:
            view.missing.append("task_or_title")
        if change_date is None:
            view.missing.append("date")
        if ambiguous:
            view.missing.append("ampm")
        elif clock is None:
            view.missing.append("time")
        if view.category is None and view.title is not None:
            view.category = _category_from_texts([view.title]) or "OTHER"
        if change_date is not None and clock is not None:
            view.scheduled_at = datetime.combine(change_date, time(*clock), tzinfo=SEOUL)
            view.time_interpretation = date_interpretation
    elif kind == "list":
        view.query_title = filter_title_texts[-1] if filter_title_texts else None
        view.query_category = _category_from_texts(filter_category_texts)
        view.query_completed = _completed_from_texts(filter_completed_texts)
        month_range = _month_range(filter_date_texts, reference)
        query_date, query_interpretation = _date_from_texts(filter_date_texts, reference)
        if month_range is not None:
            view.query_from, view.query_to = month_range
            view.time_interpretation = "absolute" if re.search(r"\d{4}년", " ".join(filter_date_texts)) else "relative"
        elif query_date is not None:
            view.query_from = datetime.combine(query_date, time.min, tzinfo=SEOUL)
            view.query_to = view.query_from + timedelta(days=1)
            view.time_interpretation = query_interpretation
        elif future_texts:
            view.query_from = reference.astimezone(SEOUL)
            view.query_to = view.query_from + timedelta(days=92)
            view.time_interpretation = "relative"
    elif kind == "update":
        if not any((view.title is not None, view.category is not None, view.completed is not None, clock is not None)):
            view.missing.append("change_value")
        if view.title is not None:
            view.changes["title"] = view.title
        if view.category is not None:
            view.changes["category"] = view.category
        if view.completed is not None:
            view.changes["completed"] = view.completed
        if clock is not None and change_date is not None:
            view.scheduled_at = datetime.combine(change_date, time(*clock), tzinfo=SEOUL)
            view.changes["scheduledAt"] = view.scheduled_at.isoformat(timespec="seconds")
            view.time_interpretation = date_interpretation
        elif clock is not None:
            view.time_only = clock
            view.time_interpretation = "absolute"
    return view


def _view_from_rule(case: Mapping[str, Any], through_turn: int) -> OperationView | None:
    operations = case["semantics"]["operations"]
    if len(operations) != 1:
        return None
    kind = operations[0]["kind"]
    turns = [
        turn["text"]
        for turn in case["context"]["turns"]
        if turn["inputType"] == "text" and turn["turn"] <= through_turn
    ]
    reference = _parse_datetime(case["context"]["referenceTime"])
    extracted = extract_rule_slots(turns, kind.upper(), reference)
    view = OperationView(kind=kind)
    dates = [date.fromisoformat(value) for value in extracted["dates"]]
    clocks = [value for value in extracted["times"] if value != "AMBIGUOUS_AMPM"]
    clock = None
    if clocks:
        hour, minute = map(int, clocks[-1].split(":"))
        clock = (hour, minute)
    combined = normalize_text(" ".join(turns))
    view.unsupported = bool(re.search(r"알림|문자|전화", combined))
    if kind == "create":
        raw_title = extracted["title"]
        view.title = None if raw_title is None else re.sub(r"^\uC5D0\s+", "", raw_title)
        if view.title is not None and re.search(
            r"(?:\uADF8\uAC70|\uC774\uAC70|\uC800\uAC70)\s*(?:\uD558\uB098)?$",
            view.title,
        ):
            view.title = None
        view.category = extracted["categories"][-1] if extracted["categories"] else (
            "OTHER" if view.title else None
        )
        if view.title is None:
            view.missing.append("task_or_title")
        if not dates:
            view.missing.append("date")
        if "AMBIGUOUS_AMPM" in extracted["times"]:
            view.missing.append("ampm")
        elif clock is None:
            view.missing.append("time")
        if dates and clock:
            view.scheduled_at = datetime.combine(dates[-1], time(*clock), tzinfo=SEOUL)
            view.time_interpretation = "absolute" if re.search(r"\d{4}년", combined) else "relative"
    elif kind == "list":
        view.query_title = extracted["title"]
        view.query_category = extracted["categories"][-1] if extracted["categories"] else None
        view.query_completed = extracted["completed"]
        month = _month_range(turns, reference)
        if month:
            view.query_from, view.query_to = month
        elif dates:
            view.query_from = datetime.combine(dates[-1], time.min, tzinfo=SEOUL)
            view.query_to = view.query_from + timedelta(days=1)
        view.time_interpretation = "absolute" if re.search(r"\d{4}년", combined) else "relative"
    else:
        title_change = re.search(r"제목을\s*(.+?)로\s*(?:바꿔|변경|수정)", combined)
        view.target_title = extracted["title"]
        if title_change and kind == "update":
            view.changes["title"] = normalize_text(title_change.group(1))
        category_change = re.search(r"분류를\s*(입고|재고|출고|반품)로", combined)
        if category_change and kind == "update":
            view.changes["category"] = _category_from_texts([category_change.group(1)])
        if extracted["completed"] is not None and kind == "update":
            view.changes["completed"] = extracted["completed"]
        if clock and kind == "update":
            if dates:
                scheduled_date = dates[-1]
                view.scheduled_at = datetime.combine(scheduled_date, time(*clock), tzinfo=SEOUL)
                view.changes["scheduledAt"] = view.scheduled_at.isoformat(timespec="seconds")
                view.time_interpretation = "absolute" if re.search(r"\d{4}년", combined) else "relative"
            else:
                view.time_only = clock
        if kind == "update" and not view.changes and view.time_only is None:
            view.missing.append("change_value")
    return view


def _candidate_classification(candidate: str, case: Mapping[str, Any]) -> tuple[str, str]:
    if candidate == "A":
        return classify_candidate_a(case)
    if candidate == "B":
        return classify_candidate_b(case)
    if candidate == "C":
        return classify_candidate_c(case)
    assessed = assess_rule_case(case)
    return assessed.classification, assessed.reason


def _target_candidates(view: OperationView, reader: FakeScheduleReadPort) -> list[ScheduleRead]:
    if view.kind not in {"update", "delete"} or view.target_title is None:
        return []
    return reader.adapter_title_search(view.target_title, target_date=view.target_date)


def _changes_for_schedule(view: OperationView, schedule: ScheduleRead) -> dict[str, Any]:
    changes = dict(view.changes)
    if view.time_only is not None:
        local = schedule.scheduled_at.astimezone(SEOUL)
        changes["scheduledAt"] = local.replace(
            hour=view.time_only[0], minute=view.time_only[1], second=0, microsecond=0
        ).isoformat(timespec="seconds")
    return changes


def _proposal_and_context(
    view: OperationView,
    reader: FakeScheduleReadPort,
    candidates: list[ScheduleRead],
    reference: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    proposal: dict[str, Any] = {"operation": view.kind, "write": False}
    grounded: dict[str, list[Any]] = {}
    if view.kind == "create":
        if view.title is not None:
            proposal["title"] = view.title
            grounded["title"] = [view.title]
        if view.scheduled_at is not None:
            value = view.scheduled_at.isoformat(timespec="seconds")
            proposal["scheduledAt"] = value
            grounded["scheduledAt"] = [value]
        if view.category is not None:
            proposal["category"] = view.category
            grounded["category"] = [view.category]
    elif view.kind == "list":
        if view.query_from is not None:
            proposal["queryFrom"] = view.query_from.isoformat(timespec="seconds")
        if view.query_to is not None:
            proposal["queryTo"] = view.query_to.isoformat(timespec="seconds")
        if view.query_category is not None:
            proposal["category"] = view.query_category
            grounded["category"] = [view.query_category]
        if view.query_completed is not None:
            proposal["completed"] = view.query_completed
            grounded["completed"] = [view.query_completed]
    elif view.kind == "update":
        if len(candidates) == 1:
            proposal["targetScheduleId"] = str(candidates[0].id)
        changes = _changes_for_schedule(view, candidates[0]) if len(candidates) == 1 else dict(view.changes)
        proposal["changes"] = changes
        for key, value in changes.items():
            grounded[f"changes.{key}"] = [value]
    elif view.kind == "delete" and len(candidates) == 1:
        proposal["targetScheduleId"] = str(candidates[0].id)
    return proposal, {
        "referenceTime": reference.isoformat(),
        "serverTimeTrusted": True,
        "existingScheduleIds": sorted(reader.active_ids),
        "candidateIds": [str(item.id) for item in candidates],
        "deletedScheduleIds": sorted(reader.deleted_ids),
        "grounded": grounded,
        "currentSchedule": (
            {} if len(candidates) != 1 else {
                "title": candidates[0].title,
                "scheduledAt": candidates[0].scheduled_at.astimezone(SEOUL).isoformat(timespec="seconds"),
                "category": candidates[0].category.value,
                "completed": candidates[0].completed,
            }
        ),
    }


def _envelope_for_view(
    view: OperationView,
    candidates: list[ScheduleRead],
    *,
    control_state: object | None = None,
) -> DecisionEnvelope:
    state: dict[str, Any] = {}
    if control_state is not None:
        state = {"controlStateVersion": 1, "controlState": control_state}
    interpretation = "relative" if view.time_interpretation == "relative" else "absolute"
    if view.missing or view.unsupported:
        return DecisionEnvelope.model_validate(
            {"decision": {"kind": "ask", "text": "추가 정보가 필요합니다."}, **state}
        )
    if view.kind == "create":
        assert view.title is not None and view.scheduled_at is not None and view.category is not None
        return DecisionEnvelope.model_validate(
            {
                "decision": {
                    "kind": "create_intent",
                    "text": "일정을 등록합니다.",
                    "title": view.title,
                    "scheduledAt": view.scheduled_at.isoformat(timespec="seconds"),
                    "category": view.category,
                    "timeInterpretation": interpretation,
                },
                **state,
            }
        )
    if view.kind == "list":
        assert view.query_from is not None and view.query_to is not None
        decision: dict[str, Any] = {
            "kind": "list_intent",
            "text": "조회 결과입니다.",
            "from": view.query_from.isoformat(timespec="seconds"),
            "to": view.query_to.isoformat(timespec="seconds"),
            "timeInterpretation": interpretation,
        }
        if view.query_category is not None:
            decision["category"] = view.query_category
        if view.query_completed is not None:
            decision["completed"] = view.query_completed
        return DecisionEnvelope.model_validate({"decision": decision, **state})
    operation = view.kind
    if len(candidates) == 1:
        decision = {
            "kind": "confirm",
            "text": "이 일정에 요청한 변경을 적용할까요?",
            "scheduleId": str(candidates[0].id),
            "operation": operation,
            "timeInterpretation": interpretation,
        }
        if operation == "update":
            decision["changes"] = _changes_for_schedule(view, candidates[0])
        return DecisionEnvelope.model_validate({"decision": decision, **state})
    decision_candidates = []
    for schedule in candidates:
        item: dict[str, Any] = {
            "scheduleId": str(schedule.id),
            "operation": operation,
            "timeInterpretation": interpretation,
        }
        if operation == "update":
            item["changes"] = _changes_for_schedule(view, schedule)
        decision_candidates.append(item)
    return DecisionEnvelope.model_validate(
        {
            "decision": {
                "kind": "select_candidate",
                "text": "대상을 선택해 주세요.",
                "candidates": decision_candidates,
            },
            **state,
        }
    )


def _b_local_payload(view: OperationView) -> tuple[str, str | None, dict[str, set[Any]]]:
    stage1 = json.dumps({"intent": view.kind}, ensure_ascii=False)
    missing = list(dict.fromkeys(view.missing))
    grounded: dict[str, set[Any]] = {}
    if view.kind == "create":
        payload = {
            "operation": "create",
            "title": view.title,
            "scheduledAt": None if view.scheduled_at is None else view.scheduled_at.isoformat(timespec="seconds"),
            "category": view.category,
            "missingInformation": missing,
        }
        for key in ("title", "scheduledAt", "category"):
            if payload[key] is not None:
                grounded[key] = {payload[key]}
    elif view.kind == "list":
        payload = {
            "operation": "list",
            "from": None if view.query_from is None else view.query_from.isoformat(timespec="seconds"),
            "to": None if view.query_to is None else view.query_to.isoformat(timespec="seconds"),
            "title": view.query_title,
            "category": view.query_category,
            "completed": view.query_completed,
            "missingInformation": missing,
        }
        for key in ("from", "to", "title", "category", "completed"):
            if payload[key] is not None:
                grounded[key] = {payload[key]}
    else:
        target = {"title": view.target_title, "from": None, "to": None}
        payload = {"operation": view.kind, "target": target, "missingInformation": missing}
        if view.kind == "update":
            payload["changes"] = dict(view.changes)
        for key, value in target.items():
            if value is not None:
                grounded[f"target.{key}"] = {value}
        if view.kind == "update":
            for key, value in payload["changes"].items():
                grounded[f"changes.{key}"] = {value}
    return stage1, json.dumps(payload, ensure_ascii=False), grounded


def candidate_a_to_decision(
    validated: Mapping[str, Any],
    *,
    schedule_candidates: list[Mapping[str, Any]],
    time_interpretation: str,
) -> DecisionEnvelope:
    """Map all seven DATA v2.6 actions without changing validated values.

    ``ANSWER`` has no equivalent DecisionEnvelope variant. ``SELECT_TARGET``
    obtains target IDs only from the controller-owned candidate set.
    """

    action = str(validated["action"])
    request = validated.get("request")
    if action == "ASK":
        return DecisionEnvelope.model_validate(
            {"decision": {"kind": "ask", "text": validated["message"]}}
        )
    if action == "ANSWER":
        raise CurrentContractUnrepresentable("A ANSWER has no DecisionEnvelope variant")
    if action == "CREATE":
        assert isinstance(request, Mapping)
        return DecisionEnvelope.model_validate(
            {
                "decision": {
                    "kind": "create_intent",
                    "text": "Create the schedule.",
                    "title": request["title"],
                    "scheduledAt": request["scheduledAt"],
                    "category": request["category"],
                    "timeInterpretation": time_interpretation,
                }
            }
        )
    if action == "LIST":
        assert isinstance(request, Mapping)
        return DecisionEnvelope.model_validate(
            {
                "decision": {
                    "kind": "list_intent",
                    "text": "Schedule lookup result.",
                    "from": request["from"],
                    "to": request["to"],
                    "timeInterpretation": time_interpretation,
                    **({"category": request["category"]} if "category" in request else {}),
                    **({"completed": request["completed"]} if "completed" in request else {}),
                }
            }
        )
    if action == "SELECT_TARGET":
        assert isinstance(request, Mapping)
        candidates: list[dict[str, Any]] = []
        for candidate in schedule_candidates:
            item = {
                "scheduleId": candidate["id"],
                "operation": request["operation"],
                "timeInterpretation": time_interpretation,
            }
            if request["operation"] == "update":
                item["changes"] = request["changes"]
            candidates.append(item)
        return DecisionEnvelope.model_validate(
            {
                "decision": {
                    "kind": "select_candidate",
                    "text": "Select a target.",
                    "candidates": candidates,
                }
            }
        )
    if action in {"CONFIRM_UPDATE", "CONFIRM_DELETE"}:
        assert isinstance(request, Mapping)
        decision: dict[str, Any] = {
            "kind": "confirm",
            "text": "Apply this operation?",
            "scheduleId": request["targetId"],
            "operation": "update" if action == "CONFIRM_UPDATE" else "delete",
            "timeInterpretation": time_interpretation,
        }
        if action == "CONFIRM_UPDATE":
            decision["changes"] = request["changes"]
        return DecisionEnvelope.model_validate({"decision": decision})
    raise ValueError(f"unknown validated A action: {action}")


def _map_candidate_a(
    case: Mapping[str, Any],
    through_turn: int,
    view: OperationView | None,
    expected: Mapping[str, Any],
) -> tuple[str, OperationView | None, DecisionEnvelope | None]:
    users = [
        turn["text"]
        for turn in case["context"]["turns"]
        if turn["inputType"] == "text" and turn["turn"] <= through_turn
    ]
    reference = _parse_datetime(case["context"]["referenceTime"])
    intent = None if view is None else ("query" if view.kind == "list" else view.kind)
    query_from = None if view is None or view.query_from is None else view.query_from.isoformat(timespec="seconds")
    query_to = None if view is None or view.query_to is None else view.query_to.isoformat(timespec="seconds")
    context = candidate_a_context(
        users,
        reference,
        intent=intent or "unsupported",
        query_from=query_from,
        query_to=query_to,
        query_category=None if view is None else view.query_category,
    )
    expected_kind = expected["expectedResult"]["kind"]
    if view is None or expected_kind == "need_more_info" or view.unsupported:
        raw_value = {"action": "ASK", "message": "추가 정보가 필요합니다.", "request": None}
    elif view.kind == "create":
        raw_value = {
            "action": "CREATE",
            "message": None,
            "request": {
                "title": view.title,
                "scheduledAt": context["verified_user_datetime"],
                "category": view.category,
            },
        }
    elif view.kind == "list":
        raw_value = {
            "action": "LIST",
            "message": None,
            "request": {
                "from": query_from,
                "to": query_to,
                **({"category": view.query_category} if view.query_category else {}),
                **({"completed": view.query_completed} if view.query_completed is not None else {}),
            },
        }
    else:
        raise ContractError("CONTRACT_GAP", "A_LOCKED_MAPPING_UNSUPPORTED")
    validated = validate_candidate_a(
        json.dumps(raw_value, ensure_ascii=False),
        context,
        users=users,
        now=reference,
    )
    action = validated["action"]
    try:
        envelope = candidate_a_to_decision(
            validated,
            schedule_candidates=context["schedule_candidates"],
            time_interpretation=(
                "relative"
                if view is not None and view.time_interpretation == "relative"
                else "absolute"
            ),
        )
    except CurrentContractUnrepresentable:
        envelope = None
    return action, view, envelope


def _text_mapping(
    candidate: str,
    case: Mapping[str, Any],
    through_turn: int,
    reader: FakeScheduleReadPort,
    expected: Mapping[str, Any],
) -> TextMapping:
    classification, classification_reason = _candidate_classification(candidate, case)
    if classification == "structurally_unrepresentable":
        return TextMapping(
            "not_applicable", "not_run", "structurally_unrepresentable", None, None,
            reason=classification_reason,
            contract_expressibility="public_api_change_required" if len(case["semantics"]["operations"]) > 1 else "backend_internal_implementation_required",
        )
    reference = _parse_datetime(case["context"]["referenceTime"])
    server_trusted = bool(case["context"]["serverTimeTrusted"])
    user_turns = {
        turn["turn"]: turn["text"]
        for turn in case["context"]["turns"]
        if turn["inputType"] == "text" and turn["turn"] <= through_turn
    }
    c_payload = _filtered_c_output(case, through_turn)
    validated_c = validate_candidate_c(json.dumps(c_payload, ensure_ascii=False), user_turns=user_turns)
    operations = validated_c["operations"]
    view = None if not operations else _view_from_c_operation(
        operations[0], reference=reference, server_time_trusted=server_trusted
    )
    if validated_c["unsupportedEvidence"] and view is None:
        if candidate == "B":
            validate_candidate_b('{"intent":"unsupported"}', None, grounded={})
        elif candidate == "R":
            validate_rule_intent('{"intent":"UNSUPPORTED"}')
        return TextMapping(
            "accept", "accept", "current_contract_unrepresentable", None, None,
            cause="CONTRACT_GAP",
            reason="DecisionEnvelope has no unsupported-only result variant",
            contract_expressibility="public_api_change_required",
        )
    if len(operations) > 1:
        return TextMapping(
            "accept", "accept", "current_contract_unrepresentable", None, None,
            cause="CONTRACT_GAP",
            reason="one DecisionEnvelope/public result cannot carry an ordered operation queue",
            contract_expressibility="public_api_change_required",
        )
    if candidate == "R":
        validate_rule_intent(json.dumps({"intent": "UNSUPPORTED" if view is None else view.kind.upper()}))
        if classification == "safe_reject":
            envelope = DecisionEnvelope.model_validate(
                {"decision": {"kind": "ask", "text": "입력을 안전하게 처리할 수 없습니다."}}
            )
            return TextMapping("accept", "accept", "safe_reject", envelope, "ask", reason=classification_reason)
        rule_view = _view_from_rule(case, through_turn)
        view = rule_view
    if candidate == "A":
        try:
            action, view, envelope = _map_candidate_a(case, through_turn, view, expected)
        except (ContractError, ValidationError) as exc:
            cause = exc.cause if isinstance(exc, ContractError) else "FORMAT"
            return TextMapping("reject", "not_run", "provider_validation_exception", None, None, cause=cause, reason=str(exc))
        if envelope is None:
            return TextMapping(
                "accept", "accept", "current_contract_unrepresentable", None, None,
                cause="CONTRACT_GAP", reason=f"A action {action} has no safe current Decision mapping",
                contract_expressibility="backend_internal_implementation_required",
            )
        if (
            action == "CREATE"
            and view is not None
            and view.scheduled_at is not None
            and getattr(envelope.decision, "scheduled_at", None)
            != view.scheduled_at.astimezone(timezone.utc)
        ):
            return TextMapping(
                "accept",
                "reject",
                "safe_reject",
                None,
                None,
                cause="NORMALIZER",
                reason="A_VALIDATED_DATETIME_DIFFERS_FROM_EVIDENCE_NORMALIZER",
            )
    elif candidate == "B":
        if view is None:
            return TextMapping("accept", "accept", "safe_reject", None, None, reason="no supported operation")
        try:
            stage1, stage2, grounded = _b_local_payload(view)
            validate_candidate_b(stage1, stage2, grounded=grounded)
        except ContractError as exc:
            return TextMapping(
                "reject", "not_run", "provider_validation_exception", None, None,
                cause=exc.cause, reason=str(exc),
                contract_expressibility="backend_internal_implementation_required",
            )
        envelope = None
    else:
        envelope = None
    if view is None:
        return TextMapping("accept", "accept", "safe_reject", None, None, reason="no supported operation")
    if view.unsupported:
        envelope = DecisionEnvelope.model_validate(
            {
                "decision": {"kind": "ask", "text": "지원하지 않는 기능을 제외하고 진행할까요?"},
                "controlStateVersion": 1,
                "controlState": {"version": 1, "purpose": "unsupported_consent"},
            }
        )
        return TextMapping(
            "accept", "accept", "decision_envelope", envelope, "ask",
            contract_expressibility="backend_internal_implementation_required",
            reason="consent question is expressible; continuation/reset implementation is missing",
        )
    candidates = _target_candidates(view, reader)
    if view.kind == "update" and view.time_only is not None and len(candidates) > 1:
        candidate_gates = []
        for schedule in candidates:
            changes = _changes_for_schedule(view, schedule)
            proposal = {
                "operation": "update",
                "write": False,
                "targetScheduleId": str(schedule.id),
                "changes": changes,
            }
            gate_context = {
                "referenceTime": reference.isoformat(),
                "serverTimeTrusted": server_trusted,
                "existingScheduleIds": sorted(reader.active_ids),
                "candidateIds": [str(item.id) for item in candidates],
                "deletedScheduleIds": sorted(reader.deleted_ids),
                "grounded": {
                    f"changes.{key}": [value] for key, value in changes.items()
                },
                "currentSchedule": {
                    "title": schedule.title,
                    "scheduledAt": schedule.scheduled_at.astimezone(SEOUL).isoformat(timespec="seconds"),
                    "category": schedule.category.value,
                    "completed": schedule.completed,
                },
            }
            candidate_gates.append(common_backend_gate(proposal, gate_context))
        gate = next((item for item in candidate_gates if not item.accepted), candidate_gates[0])
    else:
        proposal, gate_context = _proposal_and_context(view, reader, candidates, reference)
        gate_context["serverTimeTrusted"] = server_trusted
        gate = common_backend_gate(proposal, gate_context)
    if not gate.accepted:
        return TextMapping(
            "accept", "reject", "safe_reject", None, None,
            cause=gate.cause, reason=gate.code,
        )
    if view.missing:
        envelope = DecisionEnvelope.model_validate(
            {"decision": {"kind": "ask", "text": "추가 정보가 필요합니다."}}
        )
        return TextMapping("accept", "accept", "decision_envelope", envelope, "ask")
    if view.kind == "list" and (view.query_title is not None or view.query_from is None or view.query_to is None):
        return TextMapping(
            "accept", "accept", "current_contract_unrepresentable", None, None,
            cause="CONTRACT_GAP",
            reason="DecisionListIntent has no title filter or all-period range",
            contract_expressibility="backend_internal_implementation_required",
        )
    if view.kind in {"update", "delete"}:
        if not candidates:
            return TextMapping(
                "accept", "accept", "current_contract_unrepresentable", None, None,
                cause="CONTRACT_GAP",
                reason="DecisionEnvelope has no target-search no-match decision",
                contract_expressibility="backend_internal_implementation_required",
            )
        if len(candidates) > 10:
            envelope = DecisionEnvelope.model_validate(
                {"decision": {"kind": "ask", "text": "대상 범위를 더 좁혀 주세요."}}
            )
            return TextMapping(
                "accept", "accept", "decision_envelope", envelope, "ask",
                contract_expressibility="backend_internal_implementation_required",
                reason="I5 title search is supplied only by the research adapter",
            )
    if envelope is None:
        try:
            envelope = _envelope_for_view(view, candidates)
        except (AssertionError, ValidationError, ValueError) as exc:
            return TextMapping(
                "accept", "accept", "provider_validation_exception", None, None,
                cause="MAPPING", reason=str(exc),
            )
    expressibility = (
        "backend_internal_implementation_required"
        if view.kind in {"update", "delete"}
        else "current_code_and_public_api"
    )
    return TextMapping(
        "accept", "accept", "decision_envelope", envelope,
        str(envelope.decision.kind),
        contract_expressibility=expressibility,
    )


def _plan_kind(plan: Any | None) -> str | None:
    if plan is None:
        return None
    if plan.pending_write is not None:
        return "pending_write"
    if plan.result is not None:
        return str(plan.result.kind)
    return "empty"


def _public_kind(plan: Any | None) -> str | None:
    if plan is None or plan.result is None:
        return None
    return str(plan.result.kind)


def _stored_input(
    case: Mapping[str, Any],
    *,
    input_payload: dict[str, object],
    pending_context: object | None,
) -> _StoredInput:
    seed = case["meta"]["caseId"]
    return _StoredInput(
        request_id=UUID(bytes=hashlib.sha256(f"request:{seed}".encode()).digest()[:16], version=4),
        conversation_id=UUID(bytes=hashlib.sha256(f"conversation:{seed}".encode()).digest()[:16], version=4),
        input=input_payload,
        task_reference_accepted_at=_parse_datetime(case["context"]["referenceTime"]).astimezone(timezone.utc),
        control_state_version=None,
        control_state=None,
        answered_question_kind=None,
        answered_question_payload=None,
        pending_context=pending_context,
    )


def _selected_candidate_id(plan: Any, selected_schedule_id: UUID) -> str | None:
    if plan is None or plan.question is None or not isinstance(plan.question.intents, dict):
        return None
    candidates = plan.question.intents.get("candidates")
    if not isinstance(candidates, dict):
        return None
    for candidate_id, intent in candidates.items():
        if isinstance(intent, dict) and intent.get("targetScheduleId") == str(selected_schedule_id):
            return str(candidate_id)
    return None


def _expected_for_turn(case: Mapping[str, Any], turn: int) -> Mapping[str, Any] | None:
    return next((item for item in case["semantics"]["expectedTurns"] if item["turn"] == turn), None)


def _semantic_value_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        if "T" in actual and "T" in expected:
            try:
                return _parse_datetime(actual) == _parse_datetime(expected)
            except ValueError:
                pass
    return actual == expected


def _values_match(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    checked = {key: value for key, value in expected.items() if key != "dateSource"}
    return all(
        key in actual and _semantic_value_equal(actual[key], value)
        for key, value in checked.items()
    )


def _pending_matches_expected(
    pending: Any,
    expected_write: Mapping[str, Any],
    reader: FakeScheduleReadPort,
) -> bool:
    payload = pending.model_dump(mode="json", by_alias=True, exclude_none=True)
    if payload.get("operation") != expected_write.get("operation"):
        return False
    target_ref = expected_write.get("targetRef")
    if target_ref is not None:
        target_id = payload.get("targetScheduleId")
        if target_id is None or reader.ref_by_id.get(UUID(target_id)) != target_ref:
            return False
    expected_values = expected_write.get("values", {})
    actual_values = payload.get("changes", payload)
    return _values_match(actual_values, expected_values)


def _plan_matches_expected(plan: Any | None, expected: Mapping[str, Any], reader: FakeScheduleReadPort) -> bool:
    result = expected["expectedResult"]
    expected_kind = result["kind"]
    if plan is None:
        return False
    if plan.pending_write is not None:
        expected_write = expected.get("expectedWrite")
        return (
            expected_kind == "outcome"
            and result.get("outcome") == "success"
            and expected["expectedWriteCount"] == 1
            and isinstance(expected_write, Mapping)
            and _pending_matches_expected(plan.pending_write, expected_write, reader)
        )
    if plan.result is None or str(plan.result.kind) != expected_kind:
        return False
    payload = plan.result.model_dump(mode="json", by_alias=True, exclude_none=True)
    if expected_kind == "outcome":
        if payload.get("outcome") != result.get("outcome"):
            return False
        if result.get("outcomeOperation") and payload.get("operation") != result.get("outcomeOperation"):
            return False
        if "listedRefs" in result:
            actual_refs = {
                reader.ref_by_id.get(UUID(item["id"]))
                for item in payload.get("items", [])
            }
            return actual_refs == set(result["listedRefs"])
    if expected_kind == "failure":
        return payload.get("code") == result.get("failureCode")
    if expected_kind == "candidate_selection":
        if plan.question is None or not isinstance(plan.question.intents, dict):
            return False
        intents = plan.question.intents.get("candidates", {})
        refs = {
            reader.ref_by_id.get(UUID(intent["targetScheduleId"]))
            for intent in intents.values()
        }
        return refs == set(result.get("candidateRefs", []))
    if expected_kind == "confirmation":
        target = payload.get("target", {})
        target_id = target.get("id")
        confirmation = result["confirmation"]
        if target_id is None or reader.ref_by_id.get(UUID(target_id)) != confirmation["targetRef"]:
            return False
        if payload.get("operation") != confirmation["operation"]:
            return False
        expected_changes = confirmation.get("changes")
        if expected_changes is not None:
            return _values_match(payload.get("changes", {}), expected_changes)
        return "changes" not in payload
    return True


def _internal_error_plan(exc: Exception) -> tuple[Any, str]:
    """Mirror process_one's outer fail-closed exception boundary without DB finalize."""

    return (
        _failure(
            AssistantFailureCode.INTERNAL_ERROR,
            AssistantFailureNextAction.NONE,
            INTERNAL_ERROR_TEXT,
        ),
        f"{type(exc).__module__}.{type(exc).__name__}: {exc}",
    )


def evaluate_case_candidate(case: Mapping[str, Any], candidate: str) -> dict[str, Any]:
    case_id = case["meta"]["caseId"]
    classification, classification_reason = _candidate_classification(candidate, case)
    reader = FakeScheduleReadPort(case["context"]["initialSchedules"])
    clock = FixedIssueClock(_parse_datetime(case["context"]["referenceTime"]))
    uuid_factory = DeterministicUuidFactory(f"{case_id}:{candidate}")
    traces: list[dict[str, Any]] = []
    decisions: list[str] = []
    local_results: list[str] = []
    gate_results: list[str] = []
    logical_provider_decisions = 0
    current_plan = None
    mapping_result = "structurally_unrepresentable" if classification == "structurally_unrepresentable" else "safe_reject"
    mismatch_cause: str | None = None
    mismatch_reason: str | None = classification_reason
    expressibility = "current_code_and_public_api"

    if classification != "structurally_unrepresentable":
        for turn in case["context"]["turns"]:
            expected = _expected_for_turn(case, turn["turn"])
            if expected is None:
                continue
            input_type = turn["inputType"]
            plan = None
            processor_exception: str | None = None
            if input_type == "text":
                if (
                    turn["turn"] > 1
                    and "candidates_over_10" in case["meta"]["scenarioTags"]
                    and any(phrase in turn["text"] for phrase in ("찾아봐", "알아서"))
                ):
                    mapping_result = "current_contract_unrepresentable"
                    expressibility = "backend_internal_implementation_required"
                    mismatch_cause = "CONTRACT_GAP"
                    mismatch_reason = "saved narrowing question has no production backend-only delegation resolver"
                    traces.append(
                        {
                            "turn": turn["turn"],
                            "inputType": input_type,
                            "localValidation": "not_run",
                            "commonGate": "not_run",
                            "mappingResult": mapping_result,
                            "decisionKind": None,
                            "planKind": None,
                            "publicResultKind": None,
                            "oracleMatch": False,
                            "logicalProviderDecision": False,
                        }
                    )
                    break
                mapping = _text_mapping(candidate, case, turn["turn"], reader, expected)
                logical_provider_decisions += 1
                local_results.append(mapping.local_validation)
                gate_results.append(mapping.common_gate)
                mapping_result = mapping.mapping_result
                expressibility = mapping.contract_expressibility
                mismatch_cause = mapping.cause
                mismatch_reason = mapping.reason
                if mapping.envelope is not None:
                    decisions.append(str(mapping.envelope.decision.kind))
                    try:
                        plan = _provider_decision_plan(
                            mapping.envelope,
                            schedule_reader=reader,
                            issue_time_factory=clock.now,
                            uuid_factory=uuid_factory,
                            server_time_trusted=bool(case["context"]["serverTimeTrusted"]),
                        )
                    except Exception as exc:  # production process_one maps this to INTERNAL_ERROR
                        plan, processor_exception = _internal_error_plan(exc)
                        mapping_result = "processor_internal_error"
                        expressibility = "backend_internal_implementation_required"
                        mismatch_cause = "CONTRACT_GAP"
                        mismatch_reason = processor_exception
                    _prepare(plan)
            elif input_type == "selection":
                selected_id = reader.id_by_ref[str(turn["selectedScheduleRef"])]
                candidate_id = _selected_candidate_id(current_plan, selected_id)
                if candidate_id is not None:
                    stored = _stored_input(
                        case,
                        input_payload={"type": "selection", "candidateId": candidate_id},
                        pending_context=current_plan.question.intents,
                    )
                    try:
                        plan = _from_saved_selection(
                            stored=stored,
                            schedule_reader=reader,
                            issue_time_factory=clock.now,
                            uuid_factory=uuid_factory,
                            server_time_trusted=bool(case["context"]["serverTimeTrusted"]),
                        )
                    except Exception as exc:
                        plan, processor_exception = _internal_error_plan(exc)
                        mapping_result = "processor_internal_error"
                        expressibility = "backend_internal_implementation_required"
                        mismatch_cause = "CONTRACT_GAP"
                        mismatch_reason = processor_exception
                    if plan is not None:
                        _prepare(plan)
                if plan is None:
                    mismatch_cause = mismatch_cause or "MAPPING"
                    mismatch_reason = mismatch_reason or "stored selection could not be resolved without provider fallback"
                    if mapping_result != "processor_internal_error":
                        mapping_result = "safe_reject"
            elif input_type == "confirmation":
                if current_plan is not None and current_plan.question is not None:
                    stored = _stored_input(
                        case,
                        input_payload={"type": "confirmation", "approved": bool(turn["approved"])},
                        pending_context=current_plan.question.intents,
                    )
                    try:
                        plan = _from_saved_confirmation(
                            stored=stored,
                            issue_time_factory=clock.now,
                            uuid_factory=uuid_factory,
                            server_time_trusted=bool(case["context"]["serverTimeTrusted"]),
                        )
                    except Exception as exc:
                        plan, processor_exception = _internal_error_plan(exc)
                        mapping_result = "processor_internal_error"
                        expressibility = "backend_internal_implementation_required"
                        mismatch_cause = "CONTRACT_GAP"
                        mismatch_reason = processor_exception
                    _prepare(plan)
                else:
                    mismatch_cause = mismatch_cause or "MAPPING"
                    mismatch_reason = mismatch_reason or "confirmation has no stored production question intent"
                    if mapping_result != "processor_internal_error":
                        mapping_result = "safe_reject"
            matched = _plan_matches_expected(plan, expected, reader)
            traces.append(
                {
                    "turn": turn["turn"],
                    "inputType": input_type,
                    "localValidation": local_results[-1] if input_type == "text" and local_results else "not_applicable",
                    "commonGate": gate_results[-1] if input_type == "text" and gate_results else "not_applicable",
                    "mappingResult": mapping_result,
                    "decisionKind": decisions[-1] if input_type == "text" and decisions else None,
                    "planKind": _plan_kind(plan),
                    "publicResultKind": _public_kind(plan),
                    "oracleMatch": matched,
                    "logicalProviderDecision": input_type == "text",
                    "processorException": processor_exception,
                }
            )
            if plan is not None:
                current_plan = plan

    expected_write_count_total = sum(
        item["expectedWriteCount"] for item in case["semantics"]["expectedTurns"]
    )
    expected_write_count = case["semantics"]["expectedTurns"][-1]["expectedWriteCount"]
    pending = current_plan is not None and current_plan.pending_write is not None
    pending_targets = 1 if pending else 0
    final_match = bool(traces) and all(trace["oracleMatch"] for trace in traces)
    final_turn_matches = bool(traces) and traces[-1]["oracleMatch"]
    if pending and (expected_write_count == 0 or not final_turn_matches):
        system_outcome = "unsafe_accept"
    elif final_match:
        system_outcome = "correct"
    elif current_plan is not None and _public_kind(current_plan) == "need_more_info":
        final_expected = case["semantics"]["expectedTurns"][-1]["expectedResult"]["kind"]
        system_outcome = "over_ask" if final_expected != "need_more_info" else "safe_reject"
    else:
        system_outcome = "safe_reject"
    if system_outcome == "unsafe_accept":
        mismatch_cause = mismatch_cause or "MAPPING"
        mismatch_reason = mismatch_reason or "pending write contradicts the oracle"
    public_serializable = True
    if current_plan is not None and current_plan.result is not None:
        try:
            RESULT_ADAPTER.validate_python(
                current_plan.result.model_dump(mode="json", by_alias=True, exclude_none=True)
            )
        except ValidationError:
            public_serializable = False
    generated_ids = {str(value) for value in uuid_factory.values}
    real_ids = {str(item["id"]) for item in case["context"]["initialSchedules"]}
    operation_kinds = {str(item["kind"]) for item in case["semantics"]["operations"]}
    expected_read_methods: set[str] = set()
    if "list" in operation_kinds:
        expected_read_methods.add("list")
    if operation_kinds & {"update", "delete"}:
        expected_read_methods.update({"adapter_title_search", "get"})
    unexpected_read_calls = [
        call for call in reader.calls if call["method"] not in expected_read_methods
    ]
    research_adapter_used = any(
        call["method"] == "adapter_title_search" for call in reader.calls
    )
    question_ids = {
        str(trace_plan.question.id)
        for trace_plan in ([current_plan] if current_plan is not None else [])
        if trace_plan.question is not None
    }
    return {
        "recordType": "dry_mapping",
        "version": DRY_MAPPING_VERSION,
        "caseId": case_id,
        "candidate": candidate,
        "contractClassification": classification,
        "localValidation": local_results[-1] if local_results else "not_run",
        "commonGate": gate_results[-1] if gate_results else "not_run",
        "mappingResult": mapping_result,
        "decisionKind": decisions[-1] if decisions else None,
        "decisionKinds": decisions,
        "processorFinalPlanKind": _plan_kind(current_plan),
        "publicResultKind": _public_kind(current_plan),
        "scheduleReadCalls": reader.calls,
        "expectedScheduleReadMethods": sorted(expected_read_methods),
        "unexpectedScheduleReadCalls": unexpected_read_calls,
        "researchAdapterDependencies": (
            ["I5 fixture-only exact/substring title matcher; not a production policy"]
            if research_adapter_used
            else []
        ),
        "usesOnlyProductionScheduleReadPort": not research_adapter_used,
        "pendingWrite": pending,
        "pendingWriteOperation": (
            None if not pending else str(current_plan.pending_write.operation)
        ),
        "pendingWriteTargetCount": pending_targets,
        "immediateWritePossible": pending,
        "expectedWriteCount": expected_write_count,
        "expectedWriteCountTotal": expected_write_count_total,
        "actualModelCalls": 0,
        "logicalProviderDecisions": logical_provider_decisions,
        "modelAdditionalCallAllowed": (
            _public_kind(current_plan) == "need_more_info"
            and not any(call["method"] == "adapter_title_search" for call in reader.calls)
        ),
        "selectionConfirmationModelCalls": 0,
        "oracleExpectedFinalKind": case["semantics"]["expectedTurns"][-1]["expectedResult"]["kind"],
        "oracleMatch": final_match,
        "mismatchCause": None if final_match else (mismatch_cause or "MAPPING"),
        "mismatchReason": None if final_match else (mismatch_reason or "processor plan differs from oracle"),
        "contractExpressibility": expressibility,
        "publicSchemaSerializable": public_serializable,
        "uuidCollisionFree": not bool(generated_ids & real_ids) and len(generated_ids) == len(uuid_factory.values),
        "questionCandidateIdCollisionFree": not bool(question_ids & (generated_ids - question_ids)),
        "systemOutcome": system_outcome,
        "turnTrace": traces,
    }


def provider_validation_error_probe() -> dict[str, Any]:
    class InvalidProvider:
        def decide(self, context: DecisionContext) -> object:
            del context
            return {"decision": {"kind": "fail", "text": "x", "code": "MODEL_TIMEOUT", "nextAction": "retry_later"}}

    fixed = datetime(2030, 1, 1, tzinfo=timezone.utc)
    context = DecisionContext(
        request_id=UUID("10000000-0000-4000-8000-000000000001"),
        conversation_id=UUID("10000000-0000-4000-8000-000000000002"),
        input={"type": "text", "text": "invalid fixture"},
        task_reference_accepted_at=fixed,
        server_time_trusted=True,
        is_question_follow_up=False,
    )
    try:
        _call_provider(InvalidProvider(), context)
    except _InvalidDecision:
        prepared = _prepare(
            _failure(
                AssistantFailureCode.MODEL_OUTPUT_INVALID,
                AssistantFailureNextAction.REVISE_INPUT,
                MODEL_OUTPUT_INVALID_TEXT,
            )
        )
        return {
            "providerValidationRejected": True,
            "publicResultKind": prepared.result_kind,
            "publicFailureCode": prepared.result["code"] if prepared.result else None,
        }
    raise AssertionError("invalid provider output was accepted")


def run_dry_mapping() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    integrity = assert_fixed_inputs()
    cases = load_jsonl(LOCKED_PATH)
    rows = [
        evaluate_case_candidate(case, candidate)
        for case in cases
        for candidate in ("A", "B", "C", "R")
    ]
    if len(rows) != 224:
        raise AssertionError("dry mapping must contain 56 x 4 rows")
    if any(row["systemOutcome"] == "unsafe_accept" for row in rows):
        unsafe = [(row["caseId"], row["candidate"]) for row in rows if row["systemOutcome"] == "unsafe_accept"]
        raise AssertionError(f"unsafe dry mapping accepted: {unsafe}")
    probe = provider_validation_error_probe()
    outcome_counts = {
        candidate: dict(Counter(row["systemOutcome"] for row in rows if row["candidate"] == candidate))
        for candidate in ("A", "B", "C", "R")
    }
    summary = {
        "version": DRY_MAPPING_VERSION,
        "status": "PROPOSED",
        "integrity": integrity,
        "resultCount": len(rows),
        "outcomes": outcome_counts,
        "localValidationByCandidate": {
            candidate: dict(
                Counter(row["localValidation"] for row in rows if row["candidate"] == candidate)
            )
            for candidate in ("A", "B", "C", "R")
        },
        "commonGateByCandidate": {
            candidate: dict(
                Counter(row["commonGate"] for row in rows if row["candidate"] == candidate)
            )
            for candidate in ("A", "B", "C", "R")
        },
        "mappingResultsByCandidate": {
            candidate: dict(
                Counter(row["mappingResult"] for row in rows if row["candidate"] == candidate)
            )
            for candidate in ("A", "B", "C", "R")
        },
        "mappingResults": dict(Counter(row["mappingResult"] for row in rows)),
        "processorFinalPlanKinds": dict(Counter(str(row["processorFinalPlanKind"]) for row in rows)),
        "publicResultKinds": dict(Counter(str(row["publicResultKind"]) for row in rows)),
        "processorPlanKindsAllTurns": dict(
            Counter(str(trace["planKind"]) for row in rows for trace in row["turnTrace"])
        ),
        "publicResultKindsAllTurns": dict(
            Counter(str(trace["publicResultKind"]) for row in rows for trace in row["turnTrace"])
        ),
        "pendingWriteCount": sum(row["pendingWrite"] for row in rows),
        "pendingWriteByCandidate": {
            candidate: sum(row["pendingWrite"] for row in rows if row["candidate"] == candidate)
            for candidate in ("A", "B", "C", "R")
        },
        "processorExceptionTurnCount": sum(
            trace.get("processorException") is not None
            for row in rows
            for trace in row["turnTrace"]
        ),
        "scheduleReadCallCount": sum(len(row["scheduleReadCalls"]) for row in rows),
        "unsafeAccept": 0,
        "providerValidationProbe": probe,
        "actualExternalCalls": dict(ACTUAL_EXTERNAL_CALLS),
        "productionFunctionsUsed": PRODUCTION_FUNCTIONS_USED,
        "privateProductionDependency": True,
        "expressibilityFindings": EXPRESSIBILITY_FINDINGS,
        "frontendTechnicalImpacts": FRONTEND_TECHNICAL_IMPACTS,
    }
    return rows, summary


if __name__ == "__main__":
    _rows, _summary = run_dry_mapping()
    print(json.dumps(_summary, ensure_ascii=False, sort_keys=True))
