"""Issue #30 common backend safety gate v0.2.

This research-only layer preserves the v0.1 effect gate and adds deterministic
semantic validation for candidate-C evidence roles.  It never consults the
locked oracle: acceptance depends only on the user turns, the documented
normalizer grammar, trusted backend state, and the proposed effect.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Mapping, Sequence

import contracts
import dry_mapping

GATE_VERSION = "issue30-common-gate-v0.2"
_BASE_GATE = contracts.common_backend_gate

_ROLE_SLOTS: dict[str, dict[str, set[str]]] = {
    "create": {
        "targetEvidence": set(),
        "changeEvidence": {"title", "date", "time", "category"},
        "filterEvidence": set(),
    },
    "list": {
        "targetEvidence": set(),
        "changeEvidence": set(),
        "filterEvidence": {"title", "date", "category", "completed", "futureScope"},
    },
    "update": {
        "targetEvidence": {"title", "date", "completedState"},
        "changeEvidence": {"title", "date", "time", "category", "completed"},
        "filterEvidence": set(),
    },
    "delete": {
        "targetEvidence": {"title", "date", "completedState"},
        "changeEvidence": set(),
        "filterEvidence": set(),
    },
}

_DATE_TOKEN = re.compile(
    r"(?:\d{4}년\s*\d{1,2}월\s*\d{1,2}일|"
    r"\d{1,2}월\s*\d{1,2}일|"
    r"\d{4}년\s*\d{1,2}월(?!\s*\d{1,2}일)|"
    r"\d{1,2}월(?!\s*\d{1,2}일)|"
    r"오늘|내일|모레|글피|이번\s*주\s*[월화수목금토일]요일)"
)
_TIME_COMPONENT = re.compile(
    r"^(?:(?:오전|오후|아침|저녁)\s*)?(?:\d{1,2})\s*시"
    r"(?:\s*(?:\d{1,2})\s*분|\s*반)?$|^(?:오전|오후|아침|저녁)$"
)
_FUTURE_SCOPE = re.compile(
    r"(?:앞으로|향후|예정(?:된|인)?|(?:지금|오늘)\s*이후)"
)
_OPERATION_ONLY = re.compile(
    r"^(?:(?:일정|목록)\s*)?(?:등록(?:하고|해\s*줘)?|넣어\s*줘|잡아\s*줘|"
    r"보여\s*줘|알려\s*줘|뭐\s*있어|있어|바꿔\s*줘|옮겨\s*줘|"
    r"삭제해\s*줘|지워\s*줘|지우고|다\s*보여\s*줘|전부\s*보여\s*줘)$"
)


def _reject(cause: str, code: str) -> contracts.GateResult:
    return contracts.GateResult(False, cause, code)


def _adopted(spans: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [span for span in spans if span.get("status") == "adopted"]


def _texts(spans: Sequence[Mapping[str, Any]]) -> list[str]:
    return [contracts.normalize_text(str(span["text"])) for span in _adopted(spans)]


def _span_identity(span: Mapping[str, Any]) -> tuple[int, str, int | None]:
    return (
        int(span["turn"]),
        contracts.normalize_text(str(span["text"])),
        span.get("occurrence"),
    )


def _is_supported_date(texts: Sequence[str], reference: datetime, *, allow_month: bool) -> bool:
    if not texts or any(_DATE_TOKEN.search(text) is None for text in texts):
        return False
    try:
        if allow_month and dry_mapping._month_range(list(texts), reference) is not None:
            return True
        parsed, _interpretation = dry_mapping._date_from_texts(list(texts), reference)
        return parsed is not None
    except (TypeError, ValueError, OverflowError):
        return False


def _is_supported_time(texts: Sequence[str]) -> bool:
    if not texts or any(_TIME_COMPONENT.fullmatch(text) is None for text in texts):
        return False
    try:
        clock, ambiguous = dry_mapping._clock_from_texts(list(texts))
    except (TypeError, ValueError, OverflowError):
        return False
    return clock is not None or ambiguous


def _is_supported_future_scope(texts: Sequence[str]) -> bool:
    return bool(texts) and all(_FUTURE_SCOPE.search(text) is not None for text in texts)


def _is_supported_title(texts: Sequence[str]) -> bool:
    if not texts:
        return False
    for text in texts:
        if _OPERATION_ONLY.fullmatch(text):
            return False
        if _DATE_TOKEN.fullmatch(text) or _TIME_COMPONENT.fullmatch(text):
            return False
        if not re.search(r"[0-9A-Za-z가-힣]", text):
            return False
    return True


def _validate_slot(
    role: str,
    slot: str,
    spans: Sequence[Mapping[str, Any]],
    *,
    reference: datetime,
) -> contracts.GateResult:
    texts = _texts(spans)
    if not texts:
        return _reject("MODEL_SEMANTIC", "C_ROLE_HAS_NO_ADOPTED_EVIDENCE")
    if slot == "date":
        valid = _is_supported_date(texts, reference, allow_month=role == "filterEvidence")
        code = "C_DATE_EVIDENCE_UNSUPPORTED"
    elif slot == "time":
        valid = _is_supported_time(texts)
        code = "C_TIME_EVIDENCE_UNSUPPORTED"
    elif slot == "futureScope":
        valid = _is_supported_future_scope(texts)
        code = "C_FUTURE_SCOPE_EVIDENCE_UNSUPPORTED"
    elif slot in {"completed", "completedState"}:
        meanings = {dry_mapping._completed_from_texts([text]) for text in texts}
        valid = None not in meanings and len(meanings) == 1
        code = "C_COMPLETED_EVIDENCE_UNSUPPORTED"
    elif slot == "category":
        meanings = {dry_mapping._category_from_texts([text]) for text in texts}
        valid = None not in meanings and len(meanings) == 1
        code = "C_CATEGORY_EVIDENCE_UNSUPPORTED"
    elif slot == "title":
        valid = _is_supported_title(texts)
        code = "C_TITLE_EVIDENCE_UNSUPPORTED"
    else:
        valid = False
        code = "C_UNKNOWN_EVIDENCE_ROLE"
    return contracts.GateResult(True, None, None) if valid else _reject("MODEL_SEMANTIC", code)


def validate_candidate_c_role_semantics(
    candidate_output: Mapping[str, Any],
    *,
    user_turns: Mapping[int, str],
    reference_time: str,
) -> contracts.GateResult:
    """Validate role meaning without consulting a case oracle."""

    try:
        parsed = contracts.validate_candidate_c(
            json.dumps(candidate_output, ensure_ascii=False, separators=(",", ":")),
            user_turns=user_turns,
        )
        reference = dry_mapping._parse_datetime(reference_time)
    except (contracts.ContractError, KeyError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, contracts.ContractError):
            return _reject(exc.cause, exc.code)
        return _reject("FORMAT", "C_ROLE_VALIDATION_INPUT_INVALID")

    used: dict[tuple[int, tuple[int, str, int | None]], tuple[str, str]] = {}
    for operation in parsed["operations"]:
        kind = str(operation["kind"])
        allowed = _ROLE_SLOTS[kind]
        for role in ("targetEvidence", "changeEvidence", "filterEvidence"):
            for slot, spans in operation[role].items():
                if slot not in allowed[role]:
                    return _reject("MODEL_SEMANTIC", "C_ROLE_NOT_ALLOWED_FOR_OPERATION")
                result = _validate_slot(role, slot, spans, reference=reference)
                if not result.accepted:
                    return result
                for span in _adopted(spans):
                    identity = (int(operation["index"]), _span_identity(span))
                    semantic_role = (role, slot)
                    previous = used.get(identity)
                    if previous is not None and previous != semantic_role:
                        return _reject("MODEL_SEMANTIC", "C_EVIDENCE_ROLE_CONFLICT")
                    used[identity] = semantic_role
    return contracts.GateResult(True, None, None)


def _same_value(actual: Any, expected: Any) -> bool:
    if actual == expected:
        return True
    if isinstance(actual, str) and isinstance(expected, str):
        try:
            return dry_mapping._parse_datetime(actual) == dry_mapping._parse_datetime(expected)
        except ValueError:
            return False
    return False


def _expected_effect(
    view: dry_mapping.OperationView,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    expected: dict[str, Any] = {"operation": view.kind}
    if view.kind == "create":
        if view.title is not None:
            expected["title"] = view.title
        if view.scheduled_at is not None:
            expected["scheduledAt"] = view.scheduled_at.isoformat(timespec="seconds")
        if view.category is not None:
            expected["category"] = view.category
    elif view.kind == "list":
        if view.query_from is not None:
            expected["queryFrom"] = view.query_from.isoformat(timespec="seconds")
        if view.query_to is not None:
            expected["queryTo"] = view.query_to.isoformat(timespec="seconds")
        if view.query_category is not None:
            expected["category"] = view.query_category
        if view.query_completed is not None:
            expected["completed"] = view.query_completed
    elif view.kind == "update":
        if view.target_title is not None:
            expected["title"] = view.target_title
        changes = dict(view.changes)
        if view.time_only is not None:
            current = context.get("currentSchedule")
            scheduled_at = current.get("scheduledAt") if isinstance(current, Mapping) else None
            if isinstance(scheduled_at, str):
                local = dry_mapping._parse_datetime(scheduled_at).astimezone(dry_mapping.SEOUL)
                changes["scheduledAt"] = local.replace(
                    hour=view.time_only[0],
                    minute=view.time_only[1],
                    second=0,
                    microsecond=0,
                ).isoformat(timespec="seconds")
        expected["changes"] = changes
    elif view.kind == "delete" and view.target_title is not None:
        expected["title"] = view.target_title
    return expected


def _validate_effect_matches_evidence(
    candidate_output: Mapping[str, Any],
    proposal: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    reference_time: str,
    server_time_trusted: bool,
) -> contracts.GateResult:
    operations = candidate_output.get("operations")
    if not isinstance(operations, list):
        return _reject("FORMAT", "C_ROLE_VALIDATION_INPUT_INVALID")
    if len(operations) != 1:
        if any(key in proposal for key in ("queryFrom", "queryTo", "scheduledAt", "changes", "targetScheduleId")):
            return _reject("MAPPING", "C_MULTI_OPERATION_EXECUTABLE_EFFECT")
        return contracts.GateResult(True, None, None)
    try:
        view = dry_mapping._view_from_c_operation(
            operations[0],
            reference=dry_mapping._parse_datetime(reference_time),
            server_time_trusted=server_time_trusted,
        )
        expected = _expected_effect(view, context)
    except (KeyError, TypeError, ValueError, OverflowError):
        return _reject("NORMALIZER", "C_EVIDENCE_NORMALIZATION_FAILED")
    controlled = {"operation", "title", "scheduledAt", "category", "completed", "queryFrom", "queryTo"}
    for field in controlled & proposal.keys():
        if field not in expected or not _same_value(proposal[field], expected[field]):
            return _reject("NORMALIZER", "C_EVIDENCE_EFFECT_MISMATCH")
    if "changes" in proposal:
        actual_changes = proposal["changes"]
        expected_changes = expected.get("changes")
        if not isinstance(actual_changes, Mapping) or not isinstance(expected_changes, Mapping):
            return _reject("NORMALIZER", "C_EVIDENCE_EFFECT_MISMATCH")
        for field, value in actual_changes.items():
            if field not in expected_changes or not _same_value(value, expected_changes[field]):
                return _reject("NORMALIZER", "C_EVIDENCE_EFFECT_MISMATCH")
    return contracts.GateResult(True, None, None)


def common_backend_gate_v0_2(
    proposal: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    candidate_c_output: Mapping[str, Any] | None = None,
    user_turns: Mapping[int, str] | None = None,
    reference_time: str | None = None,
    server_time_trusted: bool | None = None,
) -> contracts.GateResult:
    """Apply the unchanged v0.1 effect gate, then v0.2 evidence checks."""

    base = _BASE_GATE(proposal, context)
    if not base.accepted or candidate_c_output is None:
        return base
    if user_turns is None:
        return _reject("FORMAT", "C_USER_TURNS_REQUIRED")
    reference = reference_time or context.get("referenceTime")
    if not isinstance(reference, str):
        return _reject("FORMAT", "C_REFERENCE_TIME_REQUIRED")
    role_result = validate_candidate_c_role_semantics(
        candidate_c_output,
        user_turns=user_turns,
        reference_time=reference,
    )
    if not role_result.accepted:
        return role_result
    trusted = bool(context.get("serverTimeTrusted")) if server_time_trusted is None else server_time_trusted
    return _validate_effect_matches_evidence(
        candidate_c_output,
        proposal,
        context,
        reference_time=reference,
        server_time_trusted=trusted,
    )

