"""Validate and replay provisional multiturn scenarios without a live model or DB."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.schedule_assistant.actions import ActionError, parse_action  # noqa: E402
from runtime.schedule_assistant.clients import ScheduleError  # noqa: E402
from runtime.schedule_assistant.service import ConversationService  # noqa: E402

SCHEMA_VERSION = "pilot-v0.2"
STATUSES = {"proposed_unreviewed", "reviewed", "rejected"}
REQUIRED_SCHEDULE = {"id", "title", "scheduledAt", "category", "completed",
                     "createdAt", "updatedAt"}


def _utc_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("schedule timestamp needs an offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _fields(value: Any, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise ValueError(f"expected fields {sorted(required)}; got {sorted(value) if isinstance(value, dict) else type(value).__name__}")
    return value


def validate_scenario(value: Any) -> dict[str, Any]:
    case = _fields(value, {"schema_version", "id", "conversation_group_id", "reference_datetime",
                           "initial_schedules", "turns", "label_status", "expect_final"}, {"created_id", "faults"})
    if case["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported schema version")
    for key in ("id", "conversation_group_id"):
        if not isinstance(case[key], str) or not case[key].strip():
            raise ValueError(f"invalid {key}")
    clock = datetime.fromisoformat(case["reference_datetime"])
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("reference_datetime needs an offset")
    if case["label_status"] not in STATUSES:
        raise ValueError("invalid label_status")
    if not isinstance(case["initial_schedules"], list) or not isinstance(case["expect_final"], list):
        raise ValueError("schedules must be lists")
    if "created_id" in case:
        UUID(case["created_id"])
    for item in case["initial_schedules"]:
        _fields(item, REQUIRED_SCHEDULE)
        UUID(item["id"])
    if len({item["id"] for item in case["initial_schedules"]}) != len(case["initial_schedules"]):
        raise ValueError("duplicate initial schedule ID")
    for item in case["expect_final"]:
        _fields(item, {"id"}, {"title", "scheduledAt", "category", "completed"})
        UUID(item["id"])
    if len({item["id"] for item in case["expect_final"]}) != len(case["expect_final"]):
        raise ValueError("duplicate final schedule ID")
    if not isinstance(case["turns"], list) or not case["turns"]:
        raise ValueError("turns must be a nonempty list")
    faults = case.get("faults", [])
    if not isinstance(faults, list):
        raise ValueError("faults must be a list")
    for fault in faults:
        _fields(fault, {"method", "occurrence", "effect"})
        supported = {(method, effect) for method in ("POST", "PATCH", "DELETE")
                     for effect in ("commit_then_timeout", "reject_before_commit")}
        supported.add(("GET", "stale_before_read"))
        if (type(fault["occurrence"]) is not int or fault["occurrence"] < 1
                or (fault["method"], fault["effect"]) not in supported):
            raise ValueError("unsupported fault injection")
    if len({(fault["method"], fault["occurrence"]) for fault in faults}) != len(faults):
        raise ValueError("duplicate fault occurrence")
    for turn in case["turns"]:
        _fields(turn, {"user", "model_outputs", "expect"})
        if not isinstance(turn["user"], str) or not turn["user"].strip():
            raise ValueError("invalid user utterance")
        if not isinstance(turn["model_outputs"], list):
            raise ValueError("model_outputs must be a list")
        for output in turn["model_outputs"]:
            parse_action(json.dumps(output, ensure_ascii=False))
        expected = _fields(turn["expect"], {"reply_kind", "writes"},
                           {"reply_contains", "api_methods", "post_body", "target_id"})
        if not isinstance(expected["reply_kind"], str) or type(expected["writes"]) is not int or expected["writes"] < 0:
            raise ValueError("invalid expected reply or write count")
        if "reply_contains" in expected and (not isinstance(expected["reply_contains"], list)
                                            or any(not isinstance(x, str) for x in expected["reply_contains"])):
            raise ValueError("invalid reply_contains")
        if "api_methods" in expected and (not isinstance(expected["api_methods"], list)
                                          or any(x not in {"GET", "POST", "PATCH", "DELETE"} for x in expected["api_methods"])):
            raise ValueError("invalid api_methods")
    return case


class ScriptedModel:
    def __init__(self) -> None:
        self.outputs: list[dict[str, Any]] = []
        self.unexpected_calls = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        if not self.outputs:
            self.unexpected_calls += 1
            raise RuntimeError("unexpected model call")
        return json.dumps(self.outputs.pop(0), ensure_ascii=False)


class MemorySchedules:
    def __init__(self, initial: list[dict[str, Any]], created_id: str | None = None,
                 reference_datetime: str | None = None) -> None:
        self.items = {item["id"]: copy.deepcopy(item) for item in initial}
        self.created_id = created_id
        self.reference_datetime = (_utc_timestamp(reference_datetime)
                                   if reference_datetime is not None else None)
        self.calls: list[dict[str, Any]] = []
        self.created_keys: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    def list(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        self.calls.append({"method": "GET", "filters": copy.deepcopy(filters)})
        def included(item: dict[str, Any]) -> bool:
            when = datetime.fromisoformat(item["scheduledAt"].replace("Z", "+00:00"))
            start = datetime.fromisoformat(filters["from"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(filters["to"].replace("Z", "+00:00"))
            return (start <= when < end and
                    ("category" not in filters or item["category"] == filters["category"]) and
                    ("completed" not in filters or item["completed"] == filters["completed"]))
        return [copy.deepcopy(item) for item in self.items.values() if included(item)]

    def get(self, schedule_id: str) -> dict[str, Any]:
        self.calls.append({"method": "GET", "id": schedule_id})
        if schedule_id not in self.items:
            raise ScheduleError("NOT_FOUND")
        return copy.deepcopy(self.items[schedule_id])

    def create(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        _fields(body, {"title", "scheduledAt", "category"})
        self.calls.append({"method": "POST", "body": copy.deepcopy(body), "key": key})
        if key in self.created_keys:
            original_body, result = self.created_keys[key]
            if original_body != body:
                raise ScheduleError("IDEMPOTENCY_CONFLICT")
            return copy.deepcopy(result)
        if self.created_id is None or self.created_id in self.items:
            raise ScheduleError("NO_CREATED_ID")
        if self.reference_datetime is None:
            raise ValueError("reference_datetime required for write replay")
        result = {"id": self.created_id, **copy.deepcopy(body), "completed": False,
                  "createdAt": self.reference_datetime, "updatedAt": self.reference_datetime}
        result["scheduledAt"] = _utc_timestamp(result["scheduledAt"])
        self.items[self.created_id] = copy.deepcopy(result)
        self.created_keys[key] = (copy.deepcopy(body), copy.deepcopy(result))
        return result

    def update(self, schedule_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        _fields(changes, set(), {"title", "scheduledAt", "category", "completed"})
        if not changes or any(value is None for value in changes.values()):
            raise ValueError("invalid schedule update")
        self.calls.append({"method": "PATCH", "id": schedule_id, "body": copy.deepcopy(changes)})
        if schedule_id not in self.items:
            raise ScheduleError("NOT_FOUND")
        if self.reference_datetime is None:
            raise ValueError("reference_datetime required for write replay")
        self.items[schedule_id].update(changes)
        if "scheduledAt" in changes:
            self.items[schedule_id]["scheduledAt"] = _utc_timestamp(changes["scheduledAt"])
        self.items[schedule_id]["updatedAt"] = self.reference_datetime
        return copy.deepcopy(self.items[schedule_id])

    def delete(self, schedule_id: str) -> None:
        self.calls.append({"method": "DELETE", "id": schedule_id})
        if schedule_id not in self.items:
            raise ScheduleError("NOT_FOUND")
        del self.items[schedule_id]


class FaultingMemorySchedules(MemorySchedules):
    """DATA-only deterministic write failures and pre-read stale state."""

    def __init__(self, initial: list[dict[str, Any]], created_id: str | None = None,
                 faults: list[dict[str, Any]] | None = None,
                 reference_datetime: str | None = None) -> None:
        super().__init__(initial, created_id, reference_datetime)
        self.faults = faults or []
        self.post_count = 0
        self.patch_count = 0
        self.delete_count = 0
        self.get_count = 0
        self.fault_events: list[dict[str, Any]] = []

    def get(self, schedule_id: str) -> dict[str, Any]:
        self.get_count += 1
        if any(fault == {"method": "GET", "occurrence": self.get_count,
                         "effect": "stale_before_read"} for fault in self.faults):
            if schedule_id in self.items:
                if self.reference_datetime is None:
                    raise ValueError("reference_datetime required for stale replay")
                changed = datetime.fromisoformat(self.reference_datetime.replace("Z", "+00:00")) + timedelta(seconds=1)
                self.items[schedule_id]["updatedAt"] = changed.isoformat().replace("+00:00", "Z")
            self.fault_events.append({"method": "GET", "occurrence": self.get_count,
                                      "effect": "stale_before_read"})
        return super().get(schedule_id)

    def create(self, body: dict[str, Any], key: str) -> dict[str, Any]:
        self.post_count += 1
        if any(fault == {"method": "POST", "occurrence": self.post_count,
                         "effect": "reject_before_commit"} for fault in self.faults):
            self.calls.append({"method": "POST", "body": copy.deepcopy(body), "key": key})
            self.fault_events.append({"method": "POST", "occurrence": self.post_count,
                                      "effect": "reject_before_commit"})
            raise ScheduleError("SERVICE_UNAVAILABLE")
        result = super().create(body, key)
        if any(fault == {"method": "POST", "occurrence": self.post_count,
                         "effect": "commit_then_timeout"} for fault in self.faults):
            self.fault_events.append({"method": "POST", "occurrence": self.post_count,
                                      "effect": "commit_then_timeout"})
            raise ScheduleError("CONNECTION_UNCERTAIN", uncertain=True)
        return result

    def _write_fault(self, method: str, occurrence: int, effect: str) -> bool:
        if any(fault == {"method": method, "occurrence": occurrence,
                         "effect": effect} for fault in self.faults):
            self.fault_events.append({"method": method, "occurrence": occurrence,
                                      "effect": effect})
            return True
        return False

    def update(self, schedule_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        self.patch_count += 1
        if self._write_fault("PATCH", self.patch_count, "reject_before_commit"):
            self.calls.append({"method": "PATCH", "id": schedule_id, "body": copy.deepcopy(changes)})
            raise ScheduleError("SERVICE_UNAVAILABLE")
        result = super().update(schedule_id, changes)
        if self._write_fault("PATCH", self.patch_count, "commit_then_timeout"):
            raise ScheduleError("CONNECTION_UNCERTAIN", uncertain=True)
        return result

    def delete(self, schedule_id: str) -> None:
        self.delete_count += 1
        if self._write_fault("DELETE", self.delete_count, "reject_before_commit"):
            self.calls.append({"method": "DELETE", "id": schedule_id})
            raise ScheduleError("SERVICE_UNAVAILABLE")
        super().delete(schedule_id)
        if self._write_fault("DELETE", self.delete_count, "commit_then_timeout"):
            raise ScheduleError("CONNECTION_UNCERTAIN", uncertain=True)


def replay(case: dict[str, Any]) -> None:
    validate_scenario(case)
    model = ScriptedModel()
    schedules = FaultingMemorySchedules(case["initial_schedules"], case.get("created_id"),
                                        case.get("faults"), case["reference_datetime"])
    now = datetime.fromisoformat(case["reference_datetime"])
    service = ConversationService(model, schedules, lambda: now)
    for index, turn in enumerate(case["turns"], 1):
        model.outputs = list(turn["model_outputs"])
        model.unexpected_calls = 0
        before = len(schedules.calls)
        reply = service.handle(turn["user"])
        expected = turn["expect"]
        calls = schedules.calls[before:]
        writes = [call for call in calls if call["method"] in {"POST", "PATCH", "DELETE"}]
        def require(condition: bool, detail: str) -> None:
            if not condition:
                raise AssertionError(f"{case['id']} turn {index}: {detail}")
        require(not model.outputs, "expected model calls were not consumed")
        require(model.unexpected_calls == 0, "unexpected model call")
        require(reply.kind == expected["reply_kind"], f"reply kind {reply.kind!r}")
        require(len(writes) == expected["writes"], f"write attempts {len(writes)}")
        for phrase in expected.get("reply_contains", []):
            require(phrase in reply.message, f"reply missing {phrase!r}")
        if "api_methods" in expected:
            require([call["method"] for call in calls] == expected["api_methods"], "API call order")
        if "post_body" in expected:
            require(any(call["method"] == "POST" and call["body"] == expected["post_body"] for call in calls),
                    "POST body")
        if "target_id" in expected:
            require(all(call.get("id") == expected["target_id"] for call in writes), "write target")
    require_ids = {item["id"] for item in case["expect_final"]}
    if set(schedules.items) != require_ids:
        raise AssertionError(f"{case['id']}: final schedule IDs differ")
    for expected in case["expect_final"]:
        current = schedules.items[expected["id"]]
        for key, value in expected.items():
            if current.get(key) != value:
                raise AssertionError(f"{case['id']}: final {expected['id']} {key} differs")
    if sorted(schedules.fault_events, key=lambda event: (event["method"], event["occurrence"])) != sorted(
            case.get("faults", []), key=lambda event: (event["method"], event["occurrence"])):
        raise AssertionError(f"{case['id']}: declared faults were not triggered")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    ids: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = validate_scenario(json.loads(line))
        except (ActionError, ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"{path}:{number}: {exc}") from exc
        if case["id"] in ids:
            raise ValueError(f"{path}:{number}: duplicate scenario ID")
        ids.add(case["id"])
        cases.append(case)
    if not cases:
        raise ValueError("no scenarios")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, required=True)
    args = parser.parse_args()
    cases = load_cases(args.scenarios)
    for case in cases:
        replay(case)
    print(f"Validated and replayed {len(cases)} provisional scenarios.")


if __name__ == "__main__":
    main()
