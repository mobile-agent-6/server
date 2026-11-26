"""Issue #30 local Ollama baseline evaluator.

Research-only: this module calls only the configured loopback Ollama endpoint.
It imports the protected contract/dry-mapping harness and DATA v2.6 helpers,
but never starts the backend, creates a DB engine, calls Schedule HTTP, or
executes a Schedule write.  Model text remains in memory; result rows retain
only hashes and byte counts.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import math
import socket
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DATA_ROOT = REPO / "datasets/qwen/experiments/multiturn_400_v0.1"
BACKEND_VENV_PYTHON = REPO.parent / "server/backend/.venv/bin/python"

DEVELOPMENT_PATH = HERE / "development-cases-v0.1.jsonl"
LOCKED_PATH = HERE / "locked-cases-v0.1.jsonl"
SCHEMA_PATH = HERE / "semantic-oracle-schema-v0.1.json"
CONTRACT_MANIFEST_PATH = HERE / "contract-validation-manifest-v0.1.json"
DRY_MANIFEST_PATH = HERE / "dry-mapping-manifest-v0.1.json"
DRY_RESULTS_PATH = HERE / "dry-mapping-results-v0.1.jsonl"
B_PROMPT_PATH = HERE / "candidate-b-prompt-v0.1.txt"
C_PROMPT_PATH = HERE / "candidate-c-prompt-v0.1.txt"
A_PROMPT_PATH = DATA_ROOT / "prompt.txt"
A_EVAL_PATH = DATA_ROOT / "run_eval.py"
A_GUARDS_PATH = DATA_ROOT / "guards.py"

EXPECTED_LOCKED_SHA256 = "17c7d4ef10aff8171d422c248431d3c34faaf03bf7282fab5dc3abcfa2389b57"
EXPECTED_SCHEMA_SHA256 = "cfe0ed947f6bd709e0e3ba60a8daf63fb1f201614cb11d90b823931b060a3b1d"
EXPECTED_CONTRACT_MANIFEST_SHA256 = "9002cab5863b561d2d202e03829d45084b57452ab828d43bda44d479030a6537"
EXPECTED_DRY_MANIFEST_SHA256 = "862765bd62049ba3bb73da08ad10f91268015af44a6fd8a259aeab7954e04ff4"

MODEL = "qwen3:4b"
ENDPOINT = "http://127.0.0.1:11434"
TEMPERATURE = 0
SEED = 29
NUM_PREDICT = 1024
NUM_CTX = 8192
CALL_TIMEOUT_SECONDS = 120
MAX_TRANSPORT_RETRIES = 1
EVAL_VERSION = "issue30-ollama-eval-v0.1"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(DATA_ROOT) not in sys.path:
    sys.path.insert(0, str(DATA_ROOT))

import contracts  # noqa: E402
import dry_mapping  # noqa: E402
import run_eval as data_v26  # noqa: E402
from contracts import ContractError  # noqa: E402


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def aggregate_hash(values: Sequence[str]) -> str:
    framed = b"".join(
        len(value.encode("utf-8")).to_bytes(8, "big") + value.encode("utf-8")
        for value in values
    )
    return sha256_bytes(framed)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def assert_protected_integrity() -> dict[str, Any]:
    observed = {
        "lockedCases": sha256_file(LOCKED_PATH),
        "semanticOracleSchema": sha256_file(SCHEMA_PATH),
        "contractValidationManifest": sha256_file(CONTRACT_MANIFEST_PATH),
        "dryMappingManifest": sha256_file(DRY_MANIFEST_PATH),
    }
    expected = {
        "lockedCases": EXPECTED_LOCKED_SHA256,
        "semanticOracleSchema": EXPECTED_SCHEMA_SHA256,
        "contractValidationManifest": EXPECTED_CONTRACT_MANIFEST_SHA256,
        "dryMappingManifest": EXPECTED_DRY_MANIFEST_SHA256,
    }
    if observed != expected:
        raise RuntimeError(f"protected artifact mismatch: {observed}")
    cases = load_jsonl(LOCKED_PATH)
    manifest = json.loads((HERE / "locked-cases-manifest-v0.1.json").read_text(encoding="utf-8"))
    if len(cases) != 56 or manifest.get("lockStatus") != "locked":
        raise RuntimeError("locked set count/status mismatch")
    return {**observed, "lockedCaseCount": len(cases), "lockStatus": manifest["lockStatus"]}


def object_schema(
    properties: Mapping[str, Any],
    required: Iterable[str] | None = None,
    *,
    min_properties: int | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(properties if required is None else required),
    }
    if min_properties is not None:
        value["minProperties"] = min_properties
    return value


NULLABLE_STRING = {"anyOf": [{"type": "string", "minLength": 1, "maxLength": 100}, {"type": "null"}]}
NULLABLE_DATETIME = {"anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}]}
NULLABLE_CATEGORY = {
    "anyOf": [
        {"type": "string", "enum": sorted(contracts.CATEGORIES)},
        {"type": "null"},
    ]
}
NULLABLE_BOOL = {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
MISSING_SCHEMA = {
    "type": "array",
    "maxItems": 8,
    "uniqueItems": True,
    "items": {"type": "string", "enum": sorted(contracts.MISSING_ITEMS)},
}


def candidate_b_stage1_schema() -> dict[str, Any]:
    return object_schema({"intent": {"type": "string", "enum": sorted(contracts.B_INTENTS)}})


def candidate_b_stage2_schema(intent: str) -> dict[str, Any]:
    operation = {"type": "string", "enum": [intent]}
    if intent == "create":
        return object_schema(
            {
                "operation": operation,
                "title": copy.deepcopy(NULLABLE_STRING),
                "scheduledAt": copy.deepcopy(NULLABLE_DATETIME),
                "category": copy.deepcopy(NULLABLE_CATEGORY),
                "missingInformation": copy.deepcopy(MISSING_SCHEMA),
            }
        )
    if intent == "list":
        return object_schema(
            {
                "operation": operation,
                "from": copy.deepcopy(NULLABLE_DATETIME),
                "to": copy.deepcopy(NULLABLE_DATETIME),
                "title": copy.deepcopy(NULLABLE_STRING),
                "category": copy.deepcopy(NULLABLE_CATEGORY),
                "completed": copy.deepcopy(NULLABLE_BOOL),
                "missingInformation": copy.deepcopy(MISSING_SCHEMA),
            }
        )
    target = object_schema(
        {
            "title": copy.deepcopy(NULLABLE_STRING),
            "from": copy.deepcopy(NULLABLE_DATETIME),
            "to": copy.deepcopy(NULLABLE_DATETIME),
        }
    )
    properties: dict[str, Any] = {
        "operation": operation,
        "target": target,
        "missingInformation": copy.deepcopy(MISSING_SCHEMA),
    }
    if intent == "update":
        properties["changes"] = object_schema(
            {
                "title": {"type": "string", "minLength": 1, "maxLength": 100},
                "scheduledAt": {"type": "string", "format": "date-time"},
                "category": {"type": "string", "enum": sorted(contracts.CATEGORIES)},
                "completed": {"type": "boolean"},
            },
            required=[],
            min_properties=1,
        )
    return object_schema(properties)


def candidate_c_schema() -> dict[str, Any]:
    span = object_schema(
        {
            "turn": {"type": "integer", "minimum": 1},
            "text": {"type": "string", "minLength": 1, "maxLength": 500},
            "status": {
                "type": "string",
                "enum": ["adopted", "superseded", "correction_marker"],
            },
            "occurrence": {"type": "integer", "minimum": 1},
        },
        required=["turn", "text", "status"],
    )
    spans = {"type": "array", "minItems": 1, "maxItems": 8, "items": span}

    def evidence(slots: Sequence[str]) -> dict[str, Any]:
        return object_schema({slot: copy.deepcopy(spans) for slot in slots}, required=[])

    operation = object_schema(
        {
            "index": {"type": "integer", "minimum": 1, "maximum": 8},
            "kind": {"type": "string", "enum": sorted(contracts.C_KINDS)},
            "targetEvidence": evidence(["title", "date", "time", "category", "completedState"]),
            "changeEvidence": evidence(["title", "date", "time", "category", "completed"]),
            "filterEvidence": evidence(["title", "date", "time", "category", "completed", "futureScope"]),
            "unsupportedEvidence": {"type": "array", "maxItems": 16, "items": span},
            "missingInformation": copy.deepcopy(MISSING_SCHEMA),
        }
    )
    return object_schema(
        {
            "operations": {"type": "array", "maxItems": 8, "items": operation},
            "unsupportedEvidence": {"type": "array", "maxItems": 16, "items": span},
            "missingInformation": copy.deepcopy(MISSING_SCHEMA),
        }
    )


def schema_builder_hash(candidate: str) -> str:
    target = {
        "A": data_v26.schema_for,
        "B": candidate_b_stage2_schema,
        "C": candidate_c_schema,
    }[candidate]
    prefix = inspect.getsource(candidate_b_stage1_schema) if candidate == "B" else ""
    return sha256_bytes((prefix + inspect.getsource(target)).encode("utf-8"))


def prompt_path(candidate: str) -> Path:
    return {"A": A_PROMPT_PATH, "B": B_PROMPT_PATH, "C": C_PROMPT_PATH}[candidate]


def prompt_text(candidate: str) -> str:
    return prompt_path(candidate).read_text(encoding="utf-8")


def prompt_hash(candidate: str) -> str:
    return sha256_file(prompt_path(candidate))


def render_extraction_context(
    case: Mapping[str, Any],
    through_turn: int,
    *,
    stage: str,
    intent: str | None = None,
) -> str:
    turns = [
        {"turn": turn["turn"], "text": turn["text"]}
        for turn in case["context"]["turns"]
        if turn["inputType"] == "text" and turn["turn"] <= through_turn
    ]
    payload: dict[str, Any] = {
        "stage": stage,
        "taskReferenceTime": case["context"]["referenceTime"],
        "timezone": case["context"]["timezone"],
        "serverTimeTrusted": case["context"]["serverTimeTrusted"],
        "priorContext": case["context"]["priorContext"],
        "userTurns": turns,
    }
    if intent is not None:
        payload["classifiedIntent"] = intent
    return canonical_json(payload)


def build_a_context(
    users: list[str],
    now: datetime,
    *,
    lookup_completed: bool,
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """The observation fields and helper calls used by DATA v2.6 Service._messages."""

    facts = data_v26.evidence(users, now)
    intent = data_v26.intent_from_users(users)
    context = {
        "intent": intent,
        "reference_datetime": now.isoformat(timespec="seconds"),
        "lookup_completed_this_turn": lookup_completed,
        "schedule_candidates": [dict(item) for item in candidates],
        "last_schedule_result": None,
        "verified_user_datetime": (
            facts["datetime"].isoformat(timespec="seconds") if facts["datetime"] else None
        ),
        "user_time_in_past": bool(facts["datetime"] and facts["datetime"] < now),
        "date_missing": facts["day"] is None,
        "time_missing": facts["clock"] is None,
        "ampm_ambiguous": facts["ambiguous"],
        "task_evidence": data_v26.task_evidence(users),
        "query_scope": data_v26.query_scope(users),
        "query_date_range": data_v26.query_date_range(users, now),
        "unsupported_reminder": data_v26.requests_reminder(users[-1]),
    }
    if intent in {"query", "update", "delete"}:
        context["date_missing"] = context["query_date_range"]["status"] != "known"
    context["required_question"] = (
        data_v26.required_question(users, now) if intent == "create" else None
    )
    return context


def render_a_observation(context: Mapping[str, Any], current_text: str, *, after_lookup: bool) -> str:
    observation = "[프로그램 관측 데이터 — 사용자 발화 아님]\n" + json.dumps(
        context, ensure_ascii=False, separators=(",", ":")
    )
    if after_lookup:
        return observation + "\n조회가 완료됐습니다. 위 실제 결과를 사용해 다음 동작을 출력하세요. LIST를 반복하지 마세요."
    return observation + "\n[현재 사용자 발화]\n" + current_text


def context_renderer_hash(candidate: str) -> str:
    if candidate == "A":
        material = (
            inspect.getsource(build_a_context)
            + inspect.getsource(render_a_observation)
            + sha256_file(A_EVAL_PATH)
            + sha256_file(A_GUARDS_PATH)
        )
    else:
        material = inspect.getsource(render_extraction_context)
    return sha256_bytes(material.encode("utf-8"))


def _visible_text_turns(case: Mapping[str, Any], through_turn: int) -> dict[int, str]:
    return {
        turn["turn"]: turn["text"]
        for turn in case["context"]["turns"]
        if turn["inputType"] == "text" and turn["turn"] <= through_turn
    }


def _expected_turn(case: Mapping[str, Any], turn_number: int) -> Mapping[str, Any] | None:
    return next(
        (row for row in case["semantics"]["expectedTurns"] if row["turn"] == turn_number),
        None,
    )


def _model_text_turns(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    expected = {row["turn"]: row for row in case["semantics"]["expectedTurns"]}
    return [
        turn
        for turn in case["context"]["turns"]
        if turn["inputType"] == "text"
        and expected.get(turn["turn"], {}).get("modelCallPolicy") == "model_call_expected"
    ]


def _active_schedules(case: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = []
    for schedule in case["context"]["initialSchedules"]:
        if schedule.get("deleted"):
            continue
        result.append(
            {
                key: value
                for key, value in schedule.items()
                if key not in {"ref", "deleted"}
            }
        )
    return result


def _a_lookup(schedules: Sequence[Mapping[str, Any]], request: Mapping[str, Any]) -> list[dict[str, Any]]:
    start = datetime.fromisoformat(str(request["from"]).replace("Z", "+00:00"))
    end = datetime.fromisoformat(str(request["to"]).replace("Z", "+00:00"))
    result: list[dict[str, Any]] = []
    for schedule in schedules:
        at = datetime.fromisoformat(str(schedule["scheduledAt"]).replace("Z", "+00:00"))
        if not start <= at < end:
            continue
        if request.get("category") is not None and schedule["category"] != request["category"]:
            continue
        if request.get("completed") is not None and schedule["completed"] is not request["completed"]:
            continue
        result.append(dict(schedule))
    result.sort(key=lambda item: (item["scheduledAt"], item["id"]))
    return result[:101]


@dataclass
class CallRecord:
    raw: str | None
    http_status: int | None
    latency_ms: float
    transport_success: bool
    retries: int
    error_kind: str | None
    schema_hash: str
    eval_count: int | None
    prompt_eval_count: int | None


class OllamaClient:
    def __init__(self, endpoint: str = ENDPOINT, timeout: int = CALL_TIMEOUT_SECONDS) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("only loopback HTTP Ollama endpoints are allowed")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.retry_count = 0

    def get_json(self, route: str) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(self.endpoint + route)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.status, json.load(response)

    def post_json(self, route: str, payload: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        body = canonical_json(payload).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint + route,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.status, json.load(response)

    def chat(self, messages: Sequence[Mapping[str, str]], schema: Mapping[str, Any]) -> CallRecord:
        payload = {
            "model": MODEL,
            "messages": list(messages),
            "format": schema,
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {
                "temperature": TEMPERATURE,
                "seed": SEED,
                "num_predict": NUM_PREDICT,
                "num_ctx": NUM_CTX,
            },
        }
        schema_hash = sha256_bytes(canonical_json(schema).encode("utf-8"))
        retries = 0
        while True:
            started = time.perf_counter()
            try:
                status, response = self.post_json("/api/chat", payload)
                latency = (time.perf_counter() - started) * 1000
                message = response.get("message")
                raw = message.get("content") if isinstance(message, dict) else None
                if not isinstance(raw, str):
                    return CallRecord(None, status, latency, False, retries, "missing_message_content", schema_hash, None, None)
                return CallRecord(
                    raw.strip(),
                    status,
                    latency,
                    True,
                    retries,
                    None,
                    schema_hash,
                    response.get("eval_count") if type(response.get("eval_count")) is int else None,
                    response.get("prompt_eval_count") if type(response.get("prompt_eval_count")) is int else None,
                )
            except urllib.error.HTTPError as exc:
                latency = (time.perf_counter() - started) * 1000
                retryable = 500 <= exc.code < 600
                if retryable and retries < MAX_TRANSPORT_RETRIES:
                    retries += 1
                    self.retry_count += 1
                    continue
                return CallRecord(None, exc.code, latency, False, retries, "http_error", schema_hash, None, None)
            except (urllib.error.URLError, TimeoutError, socket.timeout, OSError, ValueError, UnicodeError):
                latency = (time.perf_counter() - started) * 1000
                if retries < MAX_TRANSPORT_RETRIES:
                    retries += 1
                    self.retry_count += 1
                    continue
                return CallRecord(None, None, latency, False, retries, "transport_error", schema_hash, None, None)


def _raw_metadata(calls: Sequence[CallRecord]) -> tuple[str, int]:
    raw_values = [call.raw for call in calls if call.raw is not None]
    return aggregate_hash(raw_values), sum(len(value.encode("utf-8")) for value in raw_values)


def _parse_success(call: CallRecord) -> bool:
    if call.raw is None:
        return False
    try:
        contracts.strict_json_loads(call.raw)
        return True
    except ContractError:
        return False


def _expected_c(case: Mapping[str, Any], through_turn: int) -> dict[str, Any]:
    value = dry_mapping._filtered_c_output(case, through_turn)
    active_missing: list[str] = []
    for operation, output_operation in zip(case["semantics"]["operations"], value["operations"]):
        missing = [
            item["item"]
            for item in operation["missingInformation"]
            if item["detectedAtTurn"] <= through_turn
            and (item.get("resolvedAtTurn") is None or item["resolvedAtTurn"] > through_turn)
        ]
        output_operation["missingInformation"] = missing
        active_missing.extend(missing)
    value["missingInformation"] = list(dict.fromkeys(active_missing))
    return value


def _without_missing(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result["missingInformation"] = []
    for operation in result.get("operations", []):
        operation["missingInformation"] = []
    return result


def _expected_view(case: Mapping[str, Any], through_turn: int) -> dry_mapping.OperationView | None:
    value = _expected_c(case, through_turn)
    if not value["operations"]:
        return None
    validated = contracts.validate_candidate_c(
        canonical_json(value), user_turns=_visible_text_turns(case, through_turn)
    )
    if len(validated["operations"]) != 1:
        return None
    return dry_mapping._view_from_c_operation(
        validated["operations"][0],
        reference=dry_mapping._parse_datetime(case["context"]["referenceTime"]),
        server_time_trusted=bool(case["context"]["serverTimeTrusted"]),
    )


def _views_from_c(
    value: Mapping[str, Any], case: Mapping[str, Any]
) -> list[dry_mapping.OperationView]:
    reference = dry_mapping._parse_datetime(case["context"]["referenceTime"])
    trusted = bool(case["context"]["serverTimeTrusted"])
    return [
        dry_mapping._view_from_c_operation(
            operation,
            reference=reference,
            server_time_trusted=trusted,
        )
        for operation in value["operations"]
    ]


def _view_from_b(value: Mapping[str, Any]) -> dry_mapping.OperationView | None:
    intent = value["intent"]
    data = value.get("extraction")
    if intent in {"unsupported", "unrelated"} or not isinstance(data, Mapping):
        return None
    view = dry_mapping.OperationView(kind=intent, missing=list(data.get("missingInformation", [])))
    if intent == "create":
        view.title = data.get("title")
        view.category = data.get("category")
        if data.get("scheduledAt"):
            view.scheduled_at = dry_mapping._parse_datetime(data["scheduledAt"])
    elif intent == "list":
        view.query_title = data.get("title")
        view.query_category = data.get("category")
        view.query_completed = data.get("completed")
        if data.get("from"):
            view.query_from = dry_mapping._parse_datetime(data["from"])
        if data.get("to"):
            view.query_to = dry_mapping._parse_datetime(data["to"])
    elif intent in {"update", "delete"}:
        target = data["target"]
        view.target_title = target.get("title")
        if intent == "update":
            view.changes = dict(data["changes"])
    return view


def _views_equal(actual: dry_mapping.OperationView | None, expected: dry_mapping.OperationView | None) -> bool:
    if actual is None or expected is None:
        return actual is expected
    fields = (
        "kind",
        "target_title",
        "target_date",
        "title",
        "scheduled_at",
        "category",
        "completed",
        "query_from",
        "query_to",
        "query_title",
        "query_category",
        "query_completed",
        "changes",
        "time_only",
        "unsupported",
    )
    return all(getattr(actual, field) == getattr(expected, field) for field in fields)


def _view_effect_complete(
    view: dry_mapping.OperationView | None, case: Mapping[str, Any]
) -> bool:
    """Whether the current backend mapping could expose/advance this effect.

    A locally valid evidence object is not automatically executable.  In
    particular, LIST without both bounds cannot become DecisionListIntent.
    Update/delete also need a resolvable target and a later approved
    confirmation before they can become a pending write.
    """

    if view is None or view.missing or view.unsupported:
        return False
    if view.kind == "create":
        return all((view.title is not None, view.scheduled_at is not None, view.category is not None))
    if view.kind == "list":
        return view.query_from is not None and view.query_to is not None
    if view.kind in {"update", "delete"}:
        reader = dry_mapping.FakeScheduleReadPort(case["context"]["initialSchedules"])
        candidates = dry_mapping._target_candidates(view, reader)
        approved = any(
            turn["inputType"] == "confirmation" and bool(turn.get("approved"))
            for turn in case["context"]["turns"]
        )
        if not candidates or not approved:
            return False
        return view.kind == "delete" or bool(view.changes or view.time_only is not None)
    return False


def _groundless_counts_from_b(actual: Mapping[str, Any], expected: Mapping[str, Any] | None) -> dict[str, int]:
    counts = {key: 0 for key in ("datetime", "title", "category", "filter", "changes")}
    if expected is None:
        return counts
    data = actual.get("extraction")
    wanted = expected.get("extraction")
    if not isinstance(data, Mapping) or not isinstance(wanted, Mapping):
        return counts
    for key in ("scheduledAt", "from", "to"):
        if data.get(key) is not None and data.get(key) != wanted.get(key):
            counts["datetime"] += 1
    if data.get("title") is not None and data.get("title") != wanted.get("title"):
        counts["title"] += 1
    if data.get("category") is not None and data.get("category") != wanted.get("category"):
        counts["category"] += 1
    if actual.get("intent") == "list":
        for key in ("from", "to", "title", "category", "completed"):
            if data.get(key) is not None and data.get(key) != wanted.get(key):
                counts["filter"] += 1
    if isinstance(data.get("changes"), Mapping):
        wanted_changes = wanted.get("changes") if isinstance(wanted.get("changes"), Mapping) else {}
        counts["changes"] += sum(
            value != wanted_changes.get(key) for key, value in data["changes"].items()
        )
    target = data.get("target")
    wanted_target = wanted.get("target")
    if isinstance(target, Mapping) and isinstance(wanted_target, Mapping):
        if target.get("title") is not None and target.get("title") != wanted_target.get("title"):
            counts["title"] += 1
        counts["datetime"] += sum(
            target.get(key) is not None and target.get(key) != wanted_target.get(key)
            for key in ("from", "to")
        )
    return counts


def _trusted_gate(
    actual: dry_mapping.OperationView | None,
    expected: dry_mapping.OperationView | None,
    case: Mapping[str, Any],
    *,
    candidate: str,
) -> contracts.GateResult:
    if actual is None:
        return contracts.GateResult(True, None, None)
    reader = dry_mapping.FakeScheduleReadPort(case["context"]["initialSchedules"])
    actual_candidates = dry_mapping._target_candidates(actual, reader)
    proposal, context = dry_mapping._proposal_and_context(
        actual,
        reader,
        actual_candidates,
        dry_mapping._parse_datetime(case["context"]["referenceTime"]),
    )
    context["serverTimeTrusted"] = bool(case["context"]["serverTimeTrusted"])
    if actual.kind in {"update", "delete"} and actual.target_title is not None:
        proposal["title"] = actual.target_title
    if candidate in {"A", "B"} and expected is not None:
        trusted_candidates = dry_mapping._target_candidates(expected, reader)
        _, trusted_context = dry_mapping._proposal_and_context(
            expected,
            reader,
            trusted_candidates,
            dry_mapping._parse_datetime(case["context"]["referenceTime"]),
        )
        context["grounded"] = trusted_context["grounded"]
        if expected.target_title is not None:
            context["grounded"]["title"] = [expected.target_title]
    return contracts.common_backend_gate(proposal, context)


def _a_proposal_view(action: Mapping[str, Any]) -> dry_mapping.OperationView | None:
    name = action["action"]
    request = action.get("request")
    if name in {"ASK", "ANSWER", "SELECT_TARGET"}:
        return None
    if name == "CREATE":
        return dry_mapping.OperationView(
            kind="create",
            title=request["title"],
            scheduled_at=dry_mapping._parse_datetime(request["scheduledAt"]),
            category=request["category"],
        )
    if name == "LIST":
        return dry_mapping.OperationView(
            kind="list",
            query_from=dry_mapping._parse_datetime(request["from"]),
            query_to=dry_mapping._parse_datetime(request["to"]),
            query_category=request.get("category"),
            query_completed=request.get("completed"),
        )
    if name == "CONFIRM_UPDATE":
        return dry_mapping.OperationView(kind="update", changes=dict(request["changes"]))
    if name == "CONFIRM_DELETE":
        return dry_mapping.OperationView(kind="delete")
    return None


def _expected_a_action(expected_turn: Mapping[str, Any], expected_view: dry_mapping.OperationView | None) -> str:
    result = expected_turn["expectedResult"]
    if result["kind"] == "need_more_info":
        return "ASK"
    if result["kind"] == "candidate_selection":
        return "SELECT_TARGET"
    if result["kind"] == "confirmation":
        return "CONFIRM_UPDATE" if result["confirmation"]["operation"] == "update" else "CONFIRM_DELETE"
    if result["kind"] == "outcome" and result.get("outcomeOperation") == "create":
        return "CREATE"
    if expected_view is not None and expected_view.kind == "list":
        return "ANSWER"
    return "ANSWER"


def _semantic_a(
    validated: Mapping[str, Any],
    first_validated: Mapping[str, Any] | None,
    expected_turn: Mapping[str, Any],
    expected_view: dry_mapping.OperationView | None,
    case: Mapping[str, Any],
) -> bool:
    wanted = _expected_a_action(expected_turn, expected_view)
    if validated["action"] != wanted:
        return False
    if wanted == "CREATE":
        expected_write = expected_turn.get("expectedWrite", {}).get("values", {})
        return dry_mapping._values_match(validated["request"], expected_write)
    if wanted == "ANSWER" and expected_view is not None and expected_view.kind == "list":
        if first_validated is None or first_validated["action"] != "LIST":
            return False
        if expected_view.query_from is None or expected_view.query_to is None:
            return False
        request = first_validated["request"]
        return (
            dry_mapping._semantic_value_equal(request["from"], expected_view.query_from.isoformat(timespec="seconds"))
            and dry_mapping._semantic_value_equal(request["to"], expected_view.query_to.isoformat(timespec="seconds"))
            and request.get("category") == expected_view.query_category
            and request.get("completed") == expected_view.query_completed
        )
    if wanted in {"CONFIRM_UPDATE", "CONFIRM_DELETE"}:
        expected_confirmation = expected_turn["expectedResult"]["confirmation"]
        ref_by_id = {item["id"]: item["ref"] for item in case["context"]["initialSchedules"]}
        if ref_by_id.get(validated["request"]["targetId"]) != expected_confirmation["targetRef"]:
            return False
        if wanted == "CONFIRM_UPDATE":
            return dry_mapping._values_match(
                validated["request"]["changes"], expected_confirmation.get("changes", {})
            )
    return True


def _primary_cause(
    transport: bool,
    parse: bool,
    local: str,
    semantic: bool,
    gate: str,
    dry_row: Mapping[str, Any],
) -> str | None:
    if not transport:
        return "TRANSPORT"
    if not parse or local == "reject":
        return "FORMAT"
    if not semantic:
        return "MODEL_SEMANTIC"
    if gate == "reject":
        return "MAPPING"
    return dry_row.get("mismatchCause")


def _final_outcome(
    *,
    transport: bool,
    parse: bool,
    local: str,
    semantic: bool,
    gate: str,
    effect_complete: bool,
    expected_final_kind: str,
    dry_row: Mapping[str, Any],
) -> str:
    if not transport or not parse or local != "accept" or gate == "reject":
        return "safe_reject"
    if semantic:
        return str(dry_row["systemOutcome"])
    if dry_row["mappingResult"] in {"structurally_unrepresentable", "current_contract_unrepresentable"}:
        return "safe_reject"
    if not effect_complete:
        return "safe_reject" if expected_final_kind == "need_more_info" else "over_ask"
    return "unsafe_accept"


def _dry_rows() -> dict[tuple[str, str], dict[str, Any]]:
    return {(row["caseId"], row["candidate"]): row for row in load_jsonl(DRY_RESULTS_PATH)}


def evaluate_candidate_a(
    case: Mapping[str, Any],
    client: OllamaClient,
) -> tuple[list[CallRecord], dict[str, Any]]:
    calls: list[CallRecord] = []
    all_semantic: list[bool] = []
    all_gate: list[contracts.GateResult] = []
    local_accept = True
    parse_accept = True
    intent_matches: list[bool] = []
    effect_complete = False
    final_action: str | None = None
    schedules = _active_schedules(case)
    users: list[str] = []
    history: list[dict[str, str]] = []
    for turn in case["context"]["turns"]:
        if turn["inputType"] != "text":
            continue
        users.append(turn["text"])
        expected_turn = _expected_turn(case, turn["turn"])
        if not expected_turn or expected_turn["modelCallPolicy"] != "model_call_expected":
            continue
        now = dry_mapping._parse_datetime(case["context"]["referenceTime"])
        context = build_a_context(users, now, lookup_completed=False, candidates=[])
        messages = [{"role": "system", "content": data_v26.PROMPT}, *history]
        user_message = render_a_observation(context, turn["text"], after_lookup=False)
        messages.append({"role": "user", "content": user_message})
        first = client.chat(messages, data_v26.schema_for(context))
        calls.append(first)
        first_validated: dict[str, Any] | None = None
        final_validated: dict[str, Any] | None = None
        if first.raw is None:
            local_accept = False
            parse_accept = False
            continue
        history.extend([{"role": "user", "content": user_message}, {"role": "assistant", "content": first.raw}])
        try:
            contracts.strict_json_loads(first.raw)
        except ContractError:
            parse_accept = False
        try:
            first_validated = contracts.validate_candidate_a(first.raw, context, users=users, now=now)
            final_validated = first_validated
        except (ContractError, KeyError, TypeError, ValueError):
            local_accept = False
        if first_validated is not None and first_validated["action"] == "LIST":
            candidates = _a_lookup(schedules, first_validated["request"])
            after_context = build_a_context(users, now, lookup_completed=True, candidates=candidates)
            after_message = render_a_observation(after_context, turn["text"], after_lookup=True)
            second_messages = [{"role": "system", "content": data_v26.PROMPT}, *history, {"role": "user", "content": after_message}]
            second = client.chat(second_messages, data_v26.schema_for(after_context))
            calls.append(second)
            if second.raw is None:
                local_accept = False
                parse_accept = False
                continue
            history.extend([{"role": "user", "content": after_message}, {"role": "assistant", "content": second.raw}])
            try:
                contracts.strict_json_loads(second.raw)
            except ContractError:
                parse_accept = False
            current = candidates[0] if len(candidates) == 1 else None
            try:
                final_validated = contracts.validate_candidate_a(
                    second.raw, after_context, users=users, now=now, current=current
                )
            except (ContractError, KeyError, TypeError, ValueError):
                local_accept = False
                final_validated = None
        expected_view = _expected_view(case, turn["turn"])
        expected_intent = expected_view.kind if expected_view is not None else None
        actual_intent = context.get("intent")
        if actual_intent == "query":
            actual_intent = "list"
        intent_matches.append(actual_intent == expected_intent if expected_intent else actual_intent is None)
        if final_validated is not None:
            final_action = final_validated["action"]
            semantic = _semantic_a(final_validated, first_validated, expected_turn, expected_view, case)
            all_semantic.append(semantic)
            view = _a_proposal_view(first_validated if first_validated and first_validated["action"] == "LIST" else final_validated)
            gate = _trusted_gate(view, expected_view, case, candidate="A")
            all_gate.append(gate)
            effect_complete = _view_effect_complete(view, case)
    return calls, {
        "jsonParseSuccess": parse_accept and bool(calls),
        "localValidation": "accept" if local_accept and bool(calls) else "reject",
        "semanticMatch": bool(all_semantic) and all(all_semantic),
        "commonGate": "reject" if any(not gate.accepted for gate in all_gate) else ("accept" if all_gate or calls else "not_run"),
        "gateCodes": [gate.code for gate in all_gate if not gate.accepted],
        "intentMatch": bool(intent_matches) and all(intent_matches),
        "effectComplete": effect_complete,
        "finalAction": final_action,
        "groundless": {key: 0 for key in ("datetime", "title", "category", "filter", "changes")},
        "spanExact": None,
        "roleExact": None,
        "correctionExact": None,
        "arbitrarySelection": final_action in {"CONFIRM_UPDATE", "CONFIRM_DELETE"}
        and len(case["context"]["initialSchedules"]) > 1,
    }


def evaluate_candidate_b(
    case: Mapping[str, Any],
    client: OllamaClient,
) -> tuple[list[CallRecord], dict[str, Any]]:
    calls: list[CallRecord] = []
    parsed_values: list[dict[str, Any]] = []
    semantic_results: list[bool] = []
    intent_results: list[bool] = []
    gates: list[contracts.GateResult] = []
    local_accept = True
    parse_accept = True
    groundless = Counter({key: 0 for key in ("datetime", "title", "category", "filter", "changes")})
    effect_complete = False
    for turn in _model_text_turns(case):
        context1 = render_extraction_context(case, turn["turn"], stage="STAGE1")
        stage1 = client.chat(
            [{"role": "system", "content": prompt_text("B")}, {"role": "user", "content": context1}],
            candidate_b_stage1_schema(),
        )
        calls.append(stage1)
        if stage1.raw is None:
            local_accept = False
            parse_accept = False
            continue
        try:
            stage1_obj = contracts.strict_json_loads(stage1.raw)
        except ContractError:
            parse_accept = False
            local_accept = False
            continue
        intent = stage1_obj.get("intent")
        stage2: CallRecord | None = None
        stage2_raw: str | None = None
        if intent in contracts.B_INTENTS - {"unsupported", "unrelated"}:
            context2 = render_extraction_context(case, turn["turn"], stage="STAGE2", intent=str(intent))
            stage2 = client.chat(
                [{"role": "system", "content": prompt_text("B")}, {"role": "user", "content": context2}],
                candidate_b_stage2_schema(str(intent)),
            )
            calls.append(stage2)
            stage2_raw = stage2.raw
            if stage2.raw is None:
                local_accept = False
                parse_accept = False
                continue
            try:
                contracts.strict_json_loads(stage2.raw)
            except ContractError:
                parse_accept = False
        try:
            parsed = contracts.validate_candidate_b(stage1.raw, stage2_raw)
            parsed_values.append(parsed)
        except (ContractError, KeyError, TypeError, ValueError):
            local_accept = False
            continue
        expected_view = _expected_view(case, turn["turn"])
        expected_payload: dict[str, Any] | None = None
        if expected_view is not None:
            expected_stage1, expected_stage2, _ = dry_mapping._b_local_payload(expected_view)
            try:
                expected_payload = contracts.validate_candidate_b(expected_stage1, expected_stage2)
            except ContractError as exc:
                if not (expected_view.kind == "update" and exc.code == "EMPTY_CHANGES"):
                    raise
                # Candidate B requires non-empty changes even while the oracle is
                # explicitly waiting for a change value.  Preserve that contract
                # gap instead of fabricating a scorer-side change.
                expected_payload = {
                    "intent": "update",
                    "extraction": {"changes": {}},
                }
        elif _expected_c(case, turn["turn"])["unsupportedEvidence"]:
            expected_payload = contracts.validate_candidate_b('{"intent":"unsupported"}', None)
        actual_view = _view_from_b(parsed)
        expected_intent = None if expected_payload is None else expected_payload["intent"]
        intent_results.append(parsed["intent"] == expected_intent)
        semantic = (
            expected_payload is not None
            and parsed["intent"] == expected_payload["intent"]
            and _views_equal(actual_view, expected_view)
        )
        semantic_results.append(semantic)
        groundless.update(_groundless_counts_from_b(parsed, expected_payload))
        gate = _trusted_gate(actual_view, expected_view, case, candidate="B")
        gates.append(gate)
        effect_complete = _view_effect_complete(actual_view, case)
    return calls, {
        "jsonParseSuccess": parse_accept and bool(calls),
        "localValidation": "accept" if local_accept and bool(parsed_values) else "reject",
        "semanticMatch": bool(semantic_results) and all(semantic_results),
        "commonGate": "reject" if any(not gate.accepted for gate in gates) else ("accept" if gates or parsed_values else "not_run"),
        "gateCodes": [gate.code for gate in gates if not gate.accepted],
        "intentMatch": bool(intent_results) and all(intent_results),
        "effectComplete": effect_complete,
        "finalAction": None if not parsed_values else parsed_values[-1]["intent"],
        "groundless": dict(groundless),
        "spanExact": None,
        "roleExact": None,
        "correctionExact": None,
        "arbitrarySelection": False,
    }


def _span_signature(value: Mapping[str, Any]) -> set[tuple[int, str, str, int | None]]:
    result: set[tuple[int, str, str, int | None]] = set()
    for operation in value.get("operations", []):
        for role in ("targetEvidence", "changeEvidence", "filterEvidence"):
            for spans in operation.get(role, {}).values():
                for span in spans:
                    result.add((span["turn"], contracts.normalize_text(span["text"]), span["status"], span.get("occurrence")))
        for span in operation.get("unsupportedEvidence", []):
            result.add((span["turn"], contracts.normalize_text(span["text"]), span["status"], span.get("occurrence")))
    for span in value.get("unsupportedEvidence", []):
        result.add((span["turn"], contracts.normalize_text(span["text"]), span["status"], span.get("occurrence")))
    return result


def _role_signature(value: Mapping[str, Any]) -> set[tuple[int, str, str, str, str]]:
    result: set[tuple[int, str, str, str, str]] = set()
    for operation in value.get("operations", []):
        for role in ("targetEvidence", "changeEvidence", "filterEvidence"):
            for slot, spans in operation.get(role, {}).items():
                for span in spans:
                    result.add((operation["index"], role, slot, contracts.normalize_text(span["text"]), span["status"]))
    return result


def evaluate_candidate_c(
    case: Mapping[str, Any],
    client: OllamaClient,
) -> tuple[list[CallRecord], dict[str, Any]]:
    calls: list[CallRecord] = []
    semantic_results: list[bool] = []
    intent_results: list[bool] = []
    span_results: list[bool] = []
    role_results: list[bool] = []
    correction_results: list[bool] = []
    gates: list[contracts.GateResult] = []
    local_accept = True
    parse_accept = True
    effect_complete = False
    final_kind: str | None = None
    for turn in _model_text_turns(case):
        context = render_extraction_context(case, turn["turn"], stage="EVIDENCE")
        call = client.chat(
            [{"role": "system", "content": prompt_text("C")}, {"role": "user", "content": context}],
            candidate_c_schema(),
        )
        calls.append(call)
        if call.raw is None:
            local_accept = False
            parse_accept = False
            continue
        try:
            contracts.strict_json_loads(call.raw)
        except ContractError:
            parse_accept = False
        try:
            parsed = contracts.validate_candidate_c(
                call.raw, user_turns=_visible_text_turns(case, turn["turn"])
            )
        except (ContractError, KeyError, TypeError, ValueError):
            local_accept = False
            continue
        expected = contracts.validate_candidate_c(
            canonical_json(_expected_c(case, turn["turn"])),
            user_turns=_visible_text_turns(case, turn["turn"]),
        )
        actual_kinds = [operation["kind"] for operation in parsed["operations"]]
        expected_kinds = [operation["kind"] for operation in expected["operations"]]
        intent_results.append(actual_kinds == expected_kinds)
        span_results.append(_span_signature(parsed) == _span_signature(expected))
        role_results.append(_role_signature(parsed) == _role_signature(expected))
        actual_corrections = {item for item in _span_signature(parsed) if item[2] != "adopted"}
        expected_corrections = {item for item in _span_signature(expected) if item[2] != "adopted"}
        correction_results.append(actual_corrections == expected_corrections)
        actual_views = _views_from_c(parsed, case)
        expected_views = _views_from_c(expected, case)
        semantic_results.append(
            len(actual_views) == len(expected_views)
            and all(
                _views_equal(actual_view, expected_view)
                for actual_view, expected_view in zip(actual_views, expected_views)
            )
        )
        expected_view = expected_views[0] if len(expected_views) == 1 else None
        actual_view = actual_views[0] if len(actual_views) == 1 else None
        if actual_view is not None:
            final_kind = actual_view.kind
        gate = _trusted_gate(actual_view, expected_view, case, candidate="C")
        gates.append(gate)
        effect_complete = _view_effect_complete(actual_view, case)
    return calls, {
        "jsonParseSuccess": parse_accept and bool(calls),
        "localValidation": "accept" if local_accept and bool(calls) else "reject",
        "semanticMatch": bool(semantic_results) and all(semantic_results),
        "commonGate": "reject" if any(not gate.accepted for gate in gates) else ("accept" if gates or calls else "not_run"),
        "gateCodes": [gate.code for gate in gates if not gate.accepted],
        "intentMatch": bool(intent_results) and all(intent_results),
        "effectComplete": effect_complete,
        "finalAction": final_kind,
        "groundless": {key: 0 for key in ("datetime", "title", "category", "filter", "changes")},
        "spanExact": bool(span_results) and all(span_results),
        "roleExact": bool(role_results) and all(role_results),
        "correctionExact": bool(correction_results) and all(correction_results),
        "arbitrarySelection": False,
    }


def evaluate_case(
    case: Mapping[str, Any],
    candidate: str,
    client: OllamaClient,
    *,
    run_id: str,
    phase: str,
    model_digest: str,
    prompt_revision: int,
    dry_rows: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    evaluator = {"A": evaluate_candidate_a, "B": evaluate_candidate_b, "C": evaluate_candidate_c}[candidate]
    calls, details = evaluator(case, client)
    case_id = case["meta"]["caseId"]
    dry_row = dry_rows.get((case_id, candidate), {
        "mappingResult": "development_not_scored",
        "systemOutcome": "safe_reject",
        "mismatchCause": None,
        "contractClassification": "development_probe",
    })
    no_model_call_expected = not _model_text_turns(case)
    transport_success = all(call.transport_success for call in calls)
    parse_success = bool(details["jsonParseSuccess"]) or no_model_call_expected
    local = "not_run" if no_model_call_expected else str(details["localValidation"])
    semantic = True if no_model_call_expected else bool(details["semanticMatch"])
    gate = "not_run" if no_model_call_expected else str(details["commonGate"])
    expected_final_kind = case["semantics"]["expectedTurns"][-1]["expectedResult"]["kind"]
    outcome = (
        str(dry_row["systemOutcome"])
        if no_model_call_expected
        else _final_outcome(
        transport=transport_success,
        parse=parse_success,
        local=local,
        semantic=semantic,
        gate=gate,
        effect_complete=bool(details["effectComplete"]),
        expected_final_kind=expected_final_kind,
        dry_row=dry_row,
        )
    )
    raw_hash, raw_bytes = _raw_metadata(calls)
    primary = _primary_cause(transport_success, parse_success, local, semantic, gate, dry_row)
    secondary: list[str] = []
    if not details["intentMatch"]:
        secondary.append("intent_mismatch")
    secondary.extend(str(code) for code in details["gateCodes"])
    if dry_row.get("contractClassification") == "structurally_unrepresentable":
        secondary.append("structurally_unrepresentable")
    return {
        "recordType": "ollama_model_eval",
        "version": EVAL_VERSION,
        "phase": phase,
        "runId": run_id,
        "promptRevision": prompt_revision,
        "caseId": case_id,
        "candidate": candidate,
        "model": MODEL,
        "modelDigest": model_digest,
        "promptHash": prompt_hash(candidate),
        "schemaHash": aggregate_hash([call.schema_hash for call in calls]),
        "schemaBuilderHash": schema_builder_hash(candidate),
        "contextRendererHash": context_renderer_hash(candidate),
        "modelCallCount": len(calls),
        "httpStatus": 200 if calls and all(call.http_status == 200 for call in calls) else next((call.http_status for call in reversed(calls) if call.http_status is not None), None),
        "latencyMs": round(sum(call.latency_ms for call in calls), 3),
        "transportSuccess": transport_success,
        "jsonParseSuccess": parse_success,
        "localValidation": local,
        "semanticMatch": semantic,
        "commonGate": gate,
        "dryMappingResult": dry_row.get("mappingResult"),
        "contractClassification": dry_row.get("contractClassification"),
        "primaryCause": primary,
        "secondaryCauses": list(dict.fromkeys(secondary)),
        "finalOutcome": outcome,
        "safeReject": outcome == "safe_reject",
        "unsafeAccept": outcome == "unsafe_accept",
        "rawOutputSha256": raw_hash,
        "rawOutputByteLength": raw_bytes,
        "intentMatch": True if no_model_call_expected else bool(details["intentMatch"]),
        "operation": details["finalAction"],
        "groundlessValueCounts": details["groundless"],
        "arbitraryCandidateSelection": bool(details["arbitrarySelection"]),
        "spanExact": details["spanExact"],
        "roleExact": details["roleExact"],
        "correctionExact": details["correctionExact"],
        "transportRetryCount": sum(call.retries for call in calls),
    }


def run_phase(
    *,
    phase: str,
    run_id: str,
    endpoint: str,
    model_digest: str,
    candidates: Sequence[str],
    prompt_revision: int,
    stop_on_unsafe: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    assert_protected_integrity()
    cases = load_jsonl(DEVELOPMENT_PATH if phase == "development" else LOCKED_PATH)
    client = OllamaClient(endpoint)
    dry_rows = _dry_rows()
    rows: list[dict[str, Any]] = []
    started = datetime.now(timezone.utc)
    frozen = {
        candidate: {
            "prompt": prompt_hash(candidate),
            "schemaBuilder": schema_builder_hash(candidate),
            "contextRenderer": context_renderer_hash(candidate),
        }
        for candidate in candidates
    }
    aborted_unsafe: dict[str, str] | None = None
    for candidate in candidates:
        for case in cases:
            row = evaluate_case(
                case,
                candidate,
                client,
                run_id=run_id,
                phase=phase,
                model_digest=model_digest,
                prompt_revision=prompt_revision,
                dry_rows=dry_rows,
            )
            rows.append(row)
            if stop_on_unsafe and row["unsafeAccept"]:
                aborted_unsafe = {"caseId": str(row["caseId"]), "candidate": candidate}
                break
        if aborted_unsafe is not None:
            break
    frozen_after = {
        candidate: {
            "prompt": prompt_hash(candidate),
            "schemaBuilder": schema_builder_hash(candidate),
            "contextRenderer": context_renderer_hash(candidate),
        }
        for candidate in candidates
    }
    if frozen != frozen_after:
        raise RuntimeError("prompt/schema/context renderer changed during run")
    ended = datetime.now(timezone.utc)
    return rows, {
        "runId": run_id,
        "phase": phase,
        "startedAt": started.isoformat(),
        "endedAt": ended.isoformat(),
        "durationSeconds": round((ended - started).total_seconds(), 3),
        "retryCount": client.retry_count,
        "frozenHashes": frozen,
        "abortedUnsafe": aborted_unsafe,
    }


def aggregate_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_candidate: dict[str, Any] = {}
    for candidate in sorted({str(row["candidate"]) for row in rows}):
        selected = [row for row in rows if row["candidate"] == candidate]
        calls = sum(int(row["modelCallCount"]) for row in selected)
        latencies = [float(row["latencyMs"]) for row in selected]
        outcomes = Counter(str(row["finalOutcome"]) for row in selected)
        groundless = Counter()
        operations: dict[str, Counter[str]] = defaultdict(Counter)
        for row in selected:
            groundless.update(row["groundlessValueCounts"])
            operations[str(row["operation"])]["total"] += 1
            operations[str(row["operation"])]["semanticMatch"] += bool(row["semanticMatch"])
        by_candidate[candidate] = {
            "caseCount": len(selected),
            "modelCallCount": calls,
            "transportSuccess": sum(bool(row["transportSuccess"]) for row in selected),
            "jsonParseSuccess": sum(bool(row["jsonParseSuccess"]) for row in selected),
            "localValidationAccept": sum(row["localValidation"] == "accept" for row in selected),
            "semanticMatch": sum(bool(row["semanticMatch"]) for row in selected),
            "commonGateAccept": sum(row["commonGate"] == "accept" for row in selected),
            "commonGateReject": sum(row["commonGate"] == "reject" for row in selected),
            "intentMatch": sum(bool(row["intentMatch"]) for row in selected),
            "outcomes": {name: outcomes.get(name, 0) for name in ("correct", "safe_reject", "over_ask", "unsafe_accept")},
            "structurallyUnrepresentable": sum(row["contractClassification"] == "structurally_unrepresentable" for row in selected),
            "latencyMs": {
                "mean": round(statistics.fmean(latencies), 3) if latencies else None,
                "median": round(statistics.median(latencies), 3) if latencies else None,
                "max": round(max(latencies), 3) if latencies else None,
            },
            "groundlessValueCounts": dict(groundless),
            "arbitraryCandidateSelection": sum(bool(row["arbitraryCandidateSelection"]) for row in selected),
            "spanExact": sum(row["spanExact"] is True for row in selected),
            "roleExact": sum(row["roleExact"] is True for row in selected),
            "correctionExact": sum(row["correctionExact"] is True for row in selected),
            "operationResults": {key: dict(value) for key, value in operations.items()},
        }
    return {"totalRows": len(rows), "totalModelCalls": sum(int(row["modelCallCount"]) for row in rows), "candidates": by_candidate}


def ollama_environment(client: OllamaClient) -> dict[str, Any]:
    version_status, version = client.get_json("/api/version")
    tags_status, tags = client.get_json("/api/tags")
    ps_status, ps = client.get_json("/api/ps")
    show_status, show = client.post_json("/api/show", {"model": MODEL, "verbose": False})
    model = next((item for item in tags.get("models", []) if item.get("name") == MODEL), None)
    if model is None:
        raise RuntimeError(f"{MODEL} is not installed")
    return {
        "httpStatuses": [version_status, tags_status, ps_status, show_status],
        "serverVersion": version["version"],
        "model": model,
        "loadedModels": [item.get("name") for item in ps.get("models", [])],
        "chatTemplateSha256": sha256_bytes(str(show.get("template", "")).encode("utf-8")),
        "chatTemplateBytes": len(str(show.get("template", "")).encode("utf-8")),
        "parametersSha256": sha256_bytes(str(show.get("parameters", "")).encode("utf-8")),
        "capabilities": show.get("capabilities", model.get("capabilities", [])),
    }


def unload_model(client: OllamaClient) -> None:
    client.post_json(
        "/api/chat",
        {"model": MODEL, "messages": [], "stream": False, "keep_alive": 0},
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    env_parser = sub.add_parser("environment")
    env_parser.add_argument("--endpoint", default=ENDPOINT)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--phase", choices=["development", "locked"], required=True)
    run_parser.add_argument("--run-id", required=True)
    run_parser.add_argument("--model-digest", required=True)
    run_parser.add_argument("--candidates", default="A,B,C")
    run_parser.add_argument("--prompt-revision", type=int, default=0)
    run_parser.add_argument("--endpoint", default=ENDPOINT)
    run_parser.add_argument("--output", type=Path, required=True)
    run_parser.add_argument("--meta-output", type=Path, required=True)
    run_parser.add_argument("--stop-on-unsafe", action="store_true")
    args = parser.parse_args()
    if args.command == "environment":
        print(canonical_json(ollama_environment(OllamaClient(args.endpoint))))
        return
    rows, metadata = run_phase(
        phase=args.phase,
        run_id=args.run_id,
        endpoint=args.endpoint,
        model_digest=args.model_digest,
        candidates=tuple(item for item in args.candidates.split(",") if item),
        prompt_revision=args.prompt_revision,
        stop_on_unsafe=args.stop_on_unsafe,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_jsonl(args.output, rows)
    args.meta_output.write_text(
        canonical_json({**metadata, "aggregate": aggregate_rows(rows)}) + "\n",
        encoding="utf-8",
    )
    print(canonical_json({**metadata, "aggregate": aggregate_rows(rows)}))


if __name__ == "__main__":
    main()
