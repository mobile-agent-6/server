"""PROPOSED deterministic contracts for Issue #30 candidates A, B and C.

This module is research-only.  It has no transport, model, database, backend
server, or production import.  Candidate A deliberately mirrors DATA v2.6,
including its known semantic gaps; safety added after A lives in
``common_backend_gate`` and is reported separately.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping
from uuid import UUID

CONTRACT_VERSION = "issue30-contracts-v0.1"
MAX_PAYLOAD_BYTES = 16_384
CATEGORIES = {"INBOUND", "INVENTORY", "OUTBOUND", "RETURN", "OTHER"}
CAUSES = {
    "FORMAT",
    "MODEL_SEMANTIC",
    "NORMALIZER",
    "MAPPING",
    "ORACLE_ERROR",
    "CONTRACT_GAP",
}
OUTCOMES = {"correct", "safe_reject", "over_ask", "unsafe_accept"}
A_ACTIONS = {
    "ASK",
    "LIST",
    "CREATE",
    "SELECT_TARGET",
    "CONFIRM_UPDATE",
    "CONFIRM_DELETE",
    "ANSWER",
}
B_INTENTS = {"create", "list", "update", "delete", "unsupported", "unrelated"}
C_KINDS = {"create", "list", "update", "delete"}
R_INTENTS = {"CREATE", "LIST", "UPDATE", "DELETE", "UNSUPPORTED", "UNRELATED"}
MISSING_ITEMS = {
    "task_or_title",
    "date",
    "time",
    "ampm",
    "absolute_datetime_due_to_untrusted_time",
    "change_value",
    "candidate_choice",
    "query_scope",
    "user_consent",
}
DATETIME_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class ContractError(ValueError):
    """A contract or safety stage rejected untrusted data."""

    def __init__(self, cause: str, code: str, detail: str = "") -> None:
        if cause not in CAUSES:
            raise ValueError(f"unknown cause: {cause}")
        self.cause = cause
        self.code = code
        self.detail = detail
        super().__init__(f"{cause}:{code}" + (f": {detail}" if detail else ""))


@dataclass(frozen=True)
class GateResult:
    accepted: bool
    cause: str | None
    code: str | None


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("FORMAT", "DUPLICATE_KEY", key)
        result[key] = value
    return result


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_strings(item)


def strict_json_loads(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise ContractError("FORMAT", "NOT_JSON_TEXT")
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ContractError("FORMAT", "PAYLOAD_TOO_LARGE")
    try:
        value = json.loads(raw, object_pairs_hook=_duplicate_safe_object)
    except ContractError:
        raise
    except (json.JSONDecodeError, TypeError, UnicodeError) as exc:
        raise ContractError("FORMAT", "INVALID_JSON", str(exc)) from exc
    if not isinstance(value, dict):
        raise ContractError("FORMAT", "TOP_LEVEL_NOT_OBJECT")
    for text in _walk_strings(value):
        if any(unicodedata.category(ch) == "Cc" and ch not in "\t\n\r" for ch in text):
            raise ContractError("FORMAT", "CONTROL_CHARACTER")
    return value


def _exact_fields(
    value: Any,
    required: set[str],
    optional: set[str] = frozenset(),
    *,
    code: str = "INVALID_FIELDS",
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("FORMAT", code, "object required")
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise ContractError("FORMAT", "MISSING_FIELD", ",".join(sorted(missing)))
    if extra:
        raise ContractError("FORMAT", "EXTRA_FIELD", ",".join(sorted(extra)))
    return value


def _string(value: Any, *, minimum: int = 1, maximum: int = 240, code: str = "INVALID_STRING") -> str:
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ContractError("FORMAT", code)
    return value.strip()


def _nullable_string(value: Any, *, maximum: int = 100) -> str | None:
    if value is None:
        return None
    return _string(value, maximum=maximum)


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise ContractError("FORMAT", "INVALID_TYPE", "boolean or null required")
    return value


def _datetime(value: Any, *, require_seoul: bool = False) -> str:
    if not isinstance(value, str) or not DATETIME_RE.fullmatch(value):
        raise ContractError("FORMAT", "INVALID_DATETIME")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("FORMAT", "INVALID_DATETIME") from exc
    if require_seoul and parsed.utcoffset() != timedelta(hours=9):
        raise ContractError("MODEL_SEMANTIC", "WRONG_TIMEZONE")
    return value


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ContractError("FORMAT", "INVALID_SCHEDULE_ID")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ContractError("FORMAT", "INVALID_SCHEDULE_ID") from exc


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


# Candidate A: intentionally copied behavior, including the missing "half" grammar.
_A_CATEGORY_WORDS = {
    "INBOUND": r"입고",
    "INVENTORY": r"재고",
    "OUTBOUND": r"출고|피킹|포장",
    "RETURN": r"반품",
    "OTHER": r"기타|회의|인수인계|안전\s*점검|장비\s*점검",
}


def candidate_a_task_evidence(users: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"status": "missing", "category": None, "source": None}
    for text in users:
        source = re.split(r"말고|대신", text)[-1]
        categories = [key for key, pattern in _A_CATEGORY_WORDS.items() if re.search(pattern, source)]
        has_task = bool(
            re.search(r"확인|검수|실사|조사|준비|피킹|포장|정리|회의|인수인계|점검|상차|촬영|분류", source)
        )
        if has_task or categories:
            result = {
                "status": "confirmed" if has_task and len(categories) == 1 else "ambiguous",
                "category": categories[0] if len(categories) == 1 else None,
                "source": source,
            }
    return result


def candidate_a_evidence(users: list[str], now: datetime) -> dict[str, Any]:
    day = None
    clock = None
    ambiguous = False
    for text in users:
        source = re.sub(r'["“‘\'][^"”’\']*["”’\']', "", text)
        for match in re.finditer(r"(오늘|내일|모레)(?!\s*(?:배송분|입고분|할\s*일))", source):
            day = now.date() + timedelta(days={"오늘": 0, "내일": 1, "모레": 2}[match.group(1)])
        # DATA v2.6 has no "반" branch.  Preserve that defect.
        for match in re.finditer(r"(?:(오전|오후|아침|저녁)\s*)?(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", source):
            period, hour_text, minute_text = match.groups()
            hour = int(hour_text)
            minute = int(minute_text or 0)
            ambiguous = period is None and 1 <= hour <= 12
            clock = None
            if not ambiguous and 0 <= hour <= 23 and 0 <= minute < 60:
                if period in {"오후", "저녁"} and hour < 12:
                    hour += 12
                if period in {"오전", "아침"} and hour == 12:
                    hour = 0
                clock = (hour, minute)
    value = None
    if day is not None and clock is not None:
        value = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo).replace(
            hour=clock[0], minute=clock[1]
        )
    return {"day": day, "clock": clock, "ambiguous": ambiguous, "datetime": value}


def candidate_a_context(
    users: list[str],
    now: datetime,
    *,
    intent: str,
    lookup_completed_this_turn: bool = False,
    candidates: list[dict[str, Any]] | None = None,
    query_from: str | None = None,
    query_to: str | None = None,
    query_category: str | None = None,
) -> dict[str, Any]:
    facts = candidate_a_evidence(users, now)
    task = candidate_a_task_evidence(users)
    required_question = None
    if intent == "create":
        if task["status"] == "missing":
            required_question = "task"
        elif task["status"] != "confirmed":
            required_question = "category"
        elif facts["day"] is None:
            required_question = "date"
        elif facts["ambiguous"]:
            required_question = "ampm"
        elif facts["clock"] is None:
            required_question = "time"
        elif facts["datetime"] < now:
            required_question = "past_time"
    return {
        "intent": intent,
        "lookup_completed_this_turn": lookup_completed_this_turn,
        "schedule_candidates": list(candidates or []),
        "verified_user_datetime": (
            facts["datetime"].isoformat(timespec="seconds") if facts["datetime"] else None
        ),
        "user_time_in_past": bool(facts["datetime"] and facts["datetime"] < now),
        "date_missing": query_from is None if intent in {"query", "update", "delete"} else facts["day"] is None,
        "task_evidence": task,
        "required_question": required_question,
        "query_date_range": {
            "status": "known" if query_from and query_to else "missing",
            "from": query_from,
            "to": query_to,
        },
        "query_scope": {"known": query_category is not None, "category": query_category},
    }


def candidate_a_allowed_actions(context: Mapping[str, Any]) -> set[str]:
    choices = {"ASK", "ANSWER"}
    if not context["lookup_completed_this_turn"]:
        choices.add("LIST")
    if (
        context.get("verified_user_datetime")
        and not context.get("user_time_in_past")
        and context.get("task_evidence", {}).get("status") == "confirmed"
    ):
        choices.add("CREATE")
    count = len(context.get("schedule_candidates", []))
    if count == 1:
        choices.update({"CONFIRM_UPDATE", "CONFIRM_DELETE"})
    elif 2 <= count <= 10:
        choices.add("SELECT_TARGET")
    intent = context.get("intent")
    allowed: set[str] | None = None
    if intent == "create":
        allowed = {"ASK", "CREATE"}
    elif intent == "query":
        allowed = (
            {"ANSWER"}
            if context["lookup_completed_this_turn"]
            else ({"ASK"} if context["date_missing"] else {"LIST"})
        )
    elif intent in {"update", "delete"}:
        if count == 1:
            allowed = {"ASK", "CONFIRM_UPDATE" if intent == "update" else "CONFIRM_DELETE"}
        elif count > 1:
            allowed = {"ASK", "SELECT_TARGET"}
        elif context["lookup_completed_this_turn"]:
            allowed = {"ANSWER", "ASK"}
        else:
            allowed = {"ASK"} if context["date_missing"] else {"LIST"}
    return choices if allowed is None else choices & allowed


def _validate_a_changes(changes: Any) -> dict[str, Any]:
    changes = _exact_fields(changes, set(), {"title", "scheduledAt", "category", "completed"})
    if not changes:
        raise ContractError("FORMAT", "EMPTY_CHANGES")
    checked: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "title":
            checked[key] = _string(value, maximum=100, code="INVALID_TITLE")
        elif key == "scheduledAt":
            checked[key] = _datetime(value)
        elif key == "category":
            if value not in CATEGORIES:
                raise ContractError("FORMAT", "INVALID_ENUM", "category")
            checked[key] = value
        elif key == "completed":
            if type(value) is not bool:
                raise ContractError("FORMAT", "INVALID_TYPE", "completed")
            checked[key] = value
    return checked


def _candidate_a_write_guard(
    body: Mapping[str, Any], users: list[str], now: datetime, operation: str, current: Mapping[str, Any] | None
) -> None:
    facts = candidate_a_evidence(users, now)
    if operation == "create":
        task = candidate_a_task_evidence(users)
        if task["status"] != "confirmed":
            raise ContractError("MODEL_SEMANTIC", "A_TASK_EVIDENCE_MISSING")
        if body.get("category") != task["category"]:
            raise ContractError("MODEL_SEMANTIC", "A_CATEGORY_UNGROUNDED")
        title = re.sub(r"\s+", "", str(body.get("title", "")))
        title_task = candidate_a_task_evidence([str(body.get("title", ""))])
        if (
            not title
            or title not in re.sub(r"\s+", "", task["source"] or "")
            or title_task["status"] != "confirmed"
            or title_task["category"] != task["category"]
        ):
            raise ContractError("MODEL_SEMANTIC", "A_TITLE_UNGROUNDED")
    if operation == "update":
        if "title" in body and not any(
            "제목" in user and re.sub(r"\s+", "", str(body["title"])) in re.sub(r"\s+", "", user)
            for user in users
        ):
            raise ContractError("MODEL_SEMANTIC", "A_TITLE_UNGROUNDED")
        if "completed" in body:
            pattern = (
                r"완료(?:로|\s*처리|\s*표시)"
                if body["completed"]
                else r"미완료(?:로|\s*처리|\s*표시)|완료.*(?:취소|해제)"
            )
            if type(body["completed"]) is not bool or not any(re.search(pattern, user) for user in users):
                raise ContractError("MODEL_SEMANTIC", "A_COMPLETED_UNGROUNDED")
        if "category" in body:
            word = _A_CATEGORY_WORDS.get(str(body["category"]))
            if not word or not any(
                re.search(r"(?:분류|카테고리)(?:를|을)?[^.?!]*?(?:" + word + r")\s*(?:로|으로)", user)
                and re.search(r"바꿔|변경|수정", user)
                for user in users
            ):
                raise ContractError("MODEL_SEMANTIC", "A_CATEGORY_UNGROUNDED")
    if "scheduledAt" in body:
        expected = facts["datetime"]
        if operation == "update" and expected is None and facts["clock"] and current:
            base = datetime.fromisoformat(str(current["scheduledAt"]).replace("Z", "+00:00")).astimezone(now.tzinfo)
            expected = base.replace(hour=facts["clock"][0], minute=facts["clock"][1], second=0, microsecond=0)
        if expected is None:
            raise ContractError("MODEL_SEMANTIC", "A_DATETIME_UNGROUNDED")
        actual = datetime.fromisoformat(str(body["scheduledAt"]).replace("Z", "+00:00"))
        if actual != expected:
            raise ContractError("MODEL_SEMANTIC", "A_DATETIME_MISMATCH")
        if actual < now:
            raise ContractError("MODEL_SEMANTIC", "A_PAST_DATETIME")


def validate_candidate_a(
    raw: str,
    context: Mapping[str, Any],
    *,
    users: list[str],
    now: datetime,
    current: Mapping[str, Any] | None = None,
    run_write_guard: bool = True,
) -> dict[str, Any]:
    value = _exact_fields(strict_json_loads(raw), {"action", "message", "request"})
    action = value["action"]
    if not isinstance(action, str) or action not in A_ACTIONS:
        raise ContractError("FORMAT", "INVALID_ENUM", "action")
    if action not in candidate_a_allowed_actions(context):
        raise ContractError("FORMAT", "A_DYNAMIC_SCHEMA_ACTION", action)
    message = value["message"]
    if action == "ASK":
        _string(message, maximum=240, code="INVALID_MESSAGE")
    elif message is not None:
        raise ContractError("FORMAT", "UNEXPECTED_MESSAGE")
    request = value["request"]
    if action in {"ASK", "ANSWER"}:
        if request is not None:
            raise ContractError("FORMAT", "UNEXPECTED_REQUEST")
    elif action == "LIST":
        request = _exact_fields(request, {"from", "to"}, {"category", "completed"})
        start_text = _datetime(request["from"])
        end_text = _datetime(request["to"])
        start = datetime.fromisoformat(start_text.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_text.replace("Z", "+00:00"))
        if not start < end <= start + timedelta(days=92):
            raise ContractError("FORMAT", "INVALID_DATE_RANGE")
        bounds = context.get("query_date_range", {})
        if bounds.get("status") == "known" and (start_text != bounds["from"] or end_text != bounds["to"]):
            raise ContractError("FORMAT", "A_DYNAMIC_SCHEMA_RANGE")
        scope = context.get("query_scope", {})
        if scope.get("known"):
            if scope.get("category") is None and "category" in request:
                raise ContractError("FORMAT", "A_DYNAMIC_SCHEMA_CATEGORY")
            if scope.get("category") is not None and request.get("category") != scope["category"]:
                raise ContractError("FORMAT", "A_DYNAMIC_SCHEMA_CATEGORY")
        if "category" in request and request["category"] not in CATEGORIES:
            raise ContractError("FORMAT", "INVALID_ENUM", "category")
        if "completed" in request and type(request["completed"]) is not bool:
            raise ContractError("FORMAT", "INVALID_TYPE", "completed")
        # DATA v2.6 does not ground completed here.  Common gate owns that check.
    elif action == "CREATE":
        request = _exact_fields(request, {"title", "scheduledAt", "category"})
        request = {
            "title": _string(request["title"], maximum=100, code="INVALID_TITLE"),
            "scheduledAt": _datetime(request["scheduledAt"]),
            "category": request["category"],
        }
        if request["category"] not in CATEGORIES:
            raise ContractError("FORMAT", "INVALID_ENUM", "category")
        if run_write_guard:
            _candidate_a_write_guard(request, users, now, "create", current)
    elif action == "SELECT_TARGET":
        request = _exact_fields(request, {"operation"}, {"changes"})
        if request["operation"] not in {"update", "delete"}:
            raise ContractError("FORMAT", "INVALID_ENUM", "operation")
        if context.get("intent") != request["operation"]:
            raise ContractError("FORMAT", "A_DYNAMIC_SCHEMA_OPERATION")
        if len(context.get("schedule_candidates", [])) not in range(2, 11):
            raise ContractError("FORMAT", "A_DYNAMIC_SCHEMA_CANDIDATES")
        if request["operation"] == "update":
            if "changes" not in request:
                raise ContractError("FORMAT", "MISSING_FIELD", "changes")
            request["changes"] = _validate_a_changes(request["changes"])
            if run_write_guard:
                _candidate_a_write_guard(request["changes"], users, now, "update", current)
        elif "changes" in request:
            raise ContractError("FORMAT", "EXTRA_FIELD", "changes")
    else:
        request = _exact_fields(
            request,
            {"targetId", "changes"} if action == "CONFIRM_UPDATE" else {"targetId"},
        )
        request["targetId"] = _uuid(request["targetId"])
        candidates = context.get("schedule_candidates", [])
        allowed_ids = [item.get("id") for item in candidates]
        if len(allowed_ids) != 1 or request["targetId"] != allowed_ids[0]:
            raise ContractError("FORMAT", "A_DYNAMIC_SCHEMA_TARGET")
        if action == "CONFIRM_UPDATE":
            request["changes"] = _validate_a_changes(request["changes"])
            if run_write_guard:
                _candidate_a_write_guard(request["changes"], users, now, "update", current)
    return {"action": action, "message": message, "request": request}


def validate_candidate_b(
    stage1_raw: str,
    stage2_raw: str | None,
    *,
    grounded: Mapping[str, set[Any]] | None = None,
) -> dict[str, Any]:
    """Validate B's at-most-two-call, single-intent normalized contract."""
    stage1 = _exact_fields(strict_json_loads(stage1_raw), {"intent"})
    intent = stage1["intent"]
    if not isinstance(intent, str) or intent not in B_INTENTS:
        raise ContractError("FORMAT", "INVALID_ENUM", "intent")
    if intent in {"unsupported", "unrelated"}:
        if stage2_raw is not None:
            raise ContractError("FORMAT", "UNEXPECTED_STAGE2")
        return {"intent": intent, "extraction": None, "modelCallCount": 1}
    if stage2_raw is None:
        raise ContractError("FORMAT", "MISSING_STAGE2")
    data = strict_json_loads(stage2_raw)
    if data.get("operation") != intent:
        raise ContractError("MODEL_SEMANTIC", "B_STAGE_MISMATCH")
    if intent == "create":
        data = _exact_fields(data, {"operation", "title", "scheduledAt", "category", "missingInformation"})
        data["title"] = _nullable_string(data["title"])
        data["scheduledAt"] = None if data["scheduledAt"] is None else _datetime(data["scheduledAt"], require_seoul=True)
        if data["category"] is not None and data["category"] not in CATEGORIES:
            raise ContractError("FORMAT", "INVALID_ENUM", "category")
    elif intent == "list":
        data = _exact_fields(
            data,
            {"operation", "from", "to", "title", "category", "completed", "missingInformation"},
        )
        data["from"] = None if data["from"] is None else _datetime(data["from"], require_seoul=True)
        data["to"] = None if data["to"] is None else _datetime(data["to"], require_seoul=True)
        data["title"] = _nullable_string(data["title"])
        if data["category"] is not None and data["category"] not in CATEGORIES:
            raise ContractError("FORMAT", "INVALID_ENUM", "category")
        data["completed"] = _bool_or_none(data["completed"])
    else:
        required = {"operation", "target", "missingInformation"}
        if intent == "update":
            required.add("changes")
        data = _exact_fields(data, required)
        target = _exact_fields(data["target"], {"title", "from", "to"})
        target["title"] = _nullable_string(target["title"])
        target["from"] = None if target["from"] is None else _datetime(target["from"], require_seoul=True)
        target["to"] = None if target["to"] is None else _datetime(target["to"], require_seoul=True)
        data["target"] = target
        if intent == "update":
            data["changes"] = _validate_a_changes(data["changes"])
    if not isinstance(data["missingInformation"], list) or any(
        not isinstance(item, str) or item not in MISSING_ITEMS for item in data["missingInformation"]
    ):
        raise ContractError("FORMAT", "INVALID_MISSING_INFORMATION")
    if grounded is not None:
        values: list[tuple[str, Any]] = []
        if intent == "create":
            values.extend((key, data[key]) for key in ("title", "scheduledAt", "category") if data[key] is not None)
        elif intent == "list":
            values.extend((key, data[key]) for key in ("from", "to", "title", "category", "completed") if data[key] is not None)
        else:
            values.extend((f"target.{key}", value) for key, value in data["target"].items() if value is not None)
            if intent == "update":
                values.extend((f"changes.{key}", value) for key, value in data["changes"].items())
        for path, value in values:
            if value not in grounded.get(path, set()):
                raise ContractError("MODEL_SEMANTIC", "B_UNGROUNDED_VALUE", path)
    return {"intent": intent, "extraction": data, "modelCallCount": 2}


def _validate_span(span: Any, turns: Mapping[int, str]) -> dict[str, Any]:
    span = _exact_fields(span, {"turn", "text", "status"}, {"occurrence"})
    if type(span["turn"]) is not int or span["turn"] not in turns:
        raise ContractError("MODEL_SEMANTIC", "C_INVALID_TURN")
    text = _string(span["text"], maximum=500, code="C_INVALID_SPAN")
    if span["status"] not in {"adopted", "superseded", "correction_marker"}:
        raise ContractError("FORMAT", "INVALID_ENUM", "span.status")
    haystack = normalize_text(turns[span["turn"]])
    needle = normalize_text(text)
    occurrences = [match.start() for match in re.finditer(re.escape(needle), haystack)]
    if not occurrences:
        raise ContractError("MODEL_SEMANTIC", "C_SPAN_NOT_IN_TURN")
    if "occurrence" in span:
        if type(span["occurrence"]) is not int or not 1 <= span["occurrence"] <= len(occurrences):
            raise ContractError("MODEL_SEMANTIC", "C_INVALID_OCCURRENCE")
    return dict(span)


def _span_positions(span: Mapping[str, Any], turns: Mapping[int, str]) -> list[tuple[int, int]]:
    source = normalize_text(turns[span["turn"]])
    needle = normalize_text(span["text"])
    offsets = [match.start() for match in re.finditer(re.escape(needle), source)]
    if "occurrence" in span:
        offsets = [offsets[span["occurrence"] - 1]]
    return [(span["turn"], offset) for offset in offsets]


def _validate_evidence_map(value: Any, turns: Mapping[int, str], allowed_slots: set[str]) -> dict[str, Any]:
    value = _exact_fields(value, set(), allowed_slots)
    checked: dict[str, Any] = {}
    for role, spans in value.items():
        if not isinstance(spans, list) or not 1 <= len(spans) <= 8:
            raise ContractError("FORMAT", "C_INVALID_SPAN_LIST", role)
        checked_spans = [_validate_span(span, turns) for span in spans]
        adopted = [span for span in checked_spans if span["status"] == "adopted"]
        superseded = [span for span in checked_spans if span["status"] == "superseded"]
        markers = [span for span in checked_spans if span["status"] == "correction_marker"]
        if not adopted:
            raise ContractError("MODEL_SEMANTIC", "C_NO_ADOPTED_SPAN", role)
        if superseded and not markers:
            raise ContractError("MODEL_SEMANTIC", "C_CORRECTION_MARKER_MISSING", role)
        for old in superseded:
            ordered = any(
                old_position < marker_position < new_position
                for old_position in _span_positions(old, turns)
                for marker in markers
                for marker_position in _span_positions(marker, turns)
                for new in adopted
                for new_position in _span_positions(new, turns)
            )
            if not ordered:
                raise ContractError("MODEL_SEMANTIC", "C_CORRECTION_ORDER")
        checked[role] = checked_spans
    return checked


def validate_candidate_c(raw: str, *, user_turns: Mapping[int, str]) -> dict[str, Any]:
    value = _exact_fields(strict_json_loads(raw), {"operations", "unsupportedEvidence", "missingInformation"})
    if not isinstance(value["operations"], list) or len(value["operations"]) > 8:
        raise ContractError("FORMAT", "C_INVALID_OPERATIONS")
    operations = []
    for expected_index, operation in enumerate(value["operations"], 1):
        operation = _exact_fields(
            operation,
            {"index", "kind", "targetEvidence", "changeEvidence", "filterEvidence", "unsupportedEvidence", "missingInformation"},
        )
        if type(operation["index"]) is not int or operation["index"] != expected_index:
            raise ContractError("MODEL_SEMANTIC", "C_OPERATION_ORDER")
        if operation["kind"] not in C_KINDS:
            raise ContractError("FORMAT", "INVALID_ENUM", "operation.kind")
        operation["targetEvidence"] = _validate_evidence_map(
            operation["targetEvidence"], user_turns, {"title", "date", "time", "category", "completedState"}
        )
        operation["changeEvidence"] = _validate_evidence_map(
            operation["changeEvidence"], user_turns, {"title", "date", "time", "category", "completed"}
        )
        operation["filterEvidence"] = _validate_evidence_map(
            operation["filterEvidence"], user_turns, {"title", "date", "time", "category", "completed", "futureScope"}
        )
        if not isinstance(operation["unsupportedEvidence"], list):
            raise ContractError("FORMAT", "C_INVALID_UNSUPPORTED")
        operation["unsupportedEvidence"] = [
            _validate_span(span, user_turns) for span in operation["unsupportedEvidence"]
        ]
        if not isinstance(operation["missingInformation"], list) or any(
            not isinstance(item, str) or item not in MISSING_ITEMS for item in operation["missingInformation"]
        ):
            raise ContractError("FORMAT", "INVALID_MISSING_INFORMATION")
        operations.append(operation)
    if not isinstance(value["unsupportedEvidence"], list) or len(value["unsupportedEvidence"]) > 16:
        raise ContractError("FORMAT", "C_INVALID_UNSUPPORTED")
    unsupported = [_validate_span(span, user_turns) for span in value["unsupportedEvidence"]]
    if not isinstance(value["missingInformation"], list) or any(
        not isinstance(item, str) or item not in MISSING_ITEMS for item in value["missingInformation"]
    ):
        raise ContractError("FORMAT", "INVALID_MISSING_INFORMATION")
    for text in _walk_strings(value):
        if DATETIME_RE.fullmatch(text):
            raise ContractError("FORMAT", "C_FORBIDDEN_RFC3339")
        if UUID_RE.fullmatch(text):
            raise ContractError("FORMAT", "C_FORBIDDEN_SCHEDULE_ID")
    return {
        "operations": operations,
        "unsupportedEvidence": unsupported,
        "missingInformation": value["missingInformation"],
    }


def validate_rule_intent(raw: str) -> dict[str, str]:
    value = _exact_fields(strict_json_loads(raw), {"intent"})
    if not isinstance(value["intent"], str) or value["intent"] not in R_INTENTS:
        raise ContractError("FORMAT", "INVALID_ENUM", "intent")
    return {"intent": value["intent"]}


def common_backend_gate(proposal: Mapping[str, Any], context: Mapping[str, Any]) -> GateResult:
    """Candidate-neutral last gate over a proposed backend effect and trusted state."""

    def reject(cause: str, code: str) -> GateResult:
        return GateResult(False, cause, code)

    if proposal.get("modelCallsAfterCandidateLookup", 0) != 0:
        return reject("MAPPING", "MODEL_RECALL_AFTER_LOOKUP")
    if proposal.get("usesModelTextAsFact") and not proposal.get("backendFactVerified"):
        return reject("MAPPING", "UNVERIFIED_MODEL_FACT")
    if proposal.get("resultTruncated"):
        return reject("MAPPING", "QUERY_RESULT_TRUNCATED")
    backend_ids = set(context.get("backendResultIds", []))
    if "listedIds" in proposal:
        raw_listed_ids = proposal["listedIds"]
        if not isinstance(raw_listed_ids, list) or any(not isinstance(item, str) for item in raw_listed_ids):
            return reject("FORMAT", "INVALID_LISTED_IDS")
        if len(raw_listed_ids) != len(set(raw_listed_ids)) or set(raw_listed_ids) != backend_ids:
            return reject("MAPPING", "QUERY_ITEMS_MISMATCH")
    operation = proposal.get("operation")
    write = bool(proposal.get("write"))
    if operation == "list" and write:
        return reject("MAPPING", "LIST_WRITE")
    if operation in {"update", "delete"} and write and not context.get("confirmationGranted"):
        return reject("MAPPING", "WRITE_BEFORE_CONFIRMATION")
    if proposal.get("delegatedSearchTreatedAsApproval"):
        return reject("MAPPING", "DELEGATION_IS_NOT_APPROVAL")
    target_id = proposal.get("targetScheduleId")
    if target_id is not None:
        if target_id not in set(context.get("existingScheduleIds", [])):
            return reject("MAPPING", "SCHEDULE_ID_NOT_FOUND")
        if target_id not in set(context.get("candidateIds", [])):
            return reject("MAPPING", "SCHEDULE_ID_OUTSIDE_CANDIDATES")
        if target_id in set(context.get("deletedScheduleIds", [])):
            return reject("MAPPING", "DELETED_SCHEDULE_SELECTED")
    if proposal.get("autoSelectedFromMultiple"):
        return reject("MAPPING", "UNFOUNDED_AUTO_SELECTION")
    if proposal.get("relativeBecameAbsolute") and not context.get("serverTimeTrusted", False):
        return reject("NORMALIZER", "UNTRUSTED_RELATIVE_TIME")
    query_from = proposal.get("queryFrom")
    query_to = proposal.get("queryTo")
    if query_from is not None or query_to is not None:
        if query_from is None or query_to is None:
            return reject("MODEL_SEMANTIC", "INCOMPLETE_QUERY_RANGE")
        try:
            start = datetime.fromisoformat(str(query_from).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(query_to).replace("Z", "+00:00"))
        except ValueError:
            return reject("FORMAT", "INVALID_DATE_RANGE")
        if not start < end <= start + timedelta(days=92):
            return reject("MODEL_SEMANTIC", "QUERY_RANGE_OVER_92_DAYS")
    scheduled_at = proposal.get("scheduledAt")
    if scheduled_at is not None:
        try:
            scheduled = datetime.fromisoformat(str(scheduled_at).replace("Z", "+00:00"))
        except ValueError:
            return reject("FORMAT", "INVALID_DATETIME")
        if scheduled.utcoffset() != timedelta(hours=9):
            return reject("MODEL_SEMANTIC", "WRONG_TIMEZONE")
        reference = context.get("referenceTime")
        if operation == "create" and reference and scheduled < datetime.fromisoformat(str(reference)):
            return reject("MAPPING", "PAST_CREATE")
    changes = proposal.get("changes")
    if operation == "update":
        if not isinstance(changes, dict) or not changes:
            return reject("MODEL_SEMANTIC", "EMPTY_CHANGES")
        current = context.get("currentSchedule", {})
        if all(current.get(key) == value for key, value in changes.items()):
            return reject("MODEL_SEMANTIC", "NO_OP_CHANGES")
    grounded = context.get("grounded", {})
    for field in ("title", "scheduledAt", "category", "completed"):
        if field in proposal and proposal[field] not in set(grounded.get(field, [])):
            return reject("MODEL_SEMANTIC", f"UNGROUNDED_{field.upper()}")
    if isinstance(changes, dict):
        for field, value in changes.items():
            if value not in set(grounded.get(f"changes.{field}", grounded.get(field, []))):
                return reject("MODEL_SEMANTIC", f"UNGROUNDED_CHANGE_{field.upper()}")
    return GateResult(True, None, None)


CONTRACT_SPECS = {
    "A": {
        "version": "data-v2.6-observed",
        "modelCallsPerTextTurn": 2,
        "output": "action/message/request; seven existing actions; controller context + dynamic schema + write guard",
        "knownGaps": ["half-minute loss", "LIST completed is not grounded by dynamic schema", "single-action state machine"],
    },
    "B": {
        "version": "issue30-B-v0.1",
        "modelCallsPerTextTurn": 2,
        "output": "one intent object followed by one operation-specific normalized extraction object",
        "knownGaps": ["normalized values remain untrusted", "ordered multiple operations are structurally unsupported"],
    },
    "C": {
        "version": "issue30-C-v0.1",
        "modelCallsPerTextTurn": 1,
        "output": "ordered operations with role evidence spans; no normalized execution value",
        "knownGaps": ["normalization and final missing-information decision remain backend responsibilities"],
    },
}
