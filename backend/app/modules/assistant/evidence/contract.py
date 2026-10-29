"""Strict parser for the evidence-only model output.

The shape follows the Issue #30 output contract.  Every span must be a real
substring of the user turn it names, after NFKC and whitespace normalization.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.modules.assistant.evidence.normalize import normalize_text

MAX_PAYLOAD_BYTES = 16_384
MAX_OPERATIONS = 8
MAX_SPANS_PER_SLOT = 8
MAX_UNSUPPORTED_SPANS = 16
MAX_SPAN_CHARS = 500

TARGET_SLOTS = frozenset({"title", "date", "time", "category", "completedState"})
CHANGE_SLOTS = frozenset({"title", "date", "time", "category", "completed"})
FILTER_SLOTS = frozenset({"title", "date", "time", "category", "completed", "futureScope"})
MISSING_ITEMS = frozenset(
    {
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
)

_DATETIME = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


class OperationKind(StrEnum):
    CREATE = "create"
    LIST = "list"
    UPDATE = "update"
    DELETE = "delete"


class SpanStatus(StrEnum):
    ADOPTED = "adopted"
    SUPERSEDED = "superseded"
    CORRECTION_MARKER = "correction_marker"


class EvidenceContractError(ValueError):
    """The model output does not follow the evidence contract."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code + (f": {detail}" if detail else ""))


@dataclass(frozen=True)
class Span:
    turn: int
    text: str
    status: SpanStatus
    occurrence: int | None = None


EvidenceMap = Mapping[str, tuple[Span, ...]]


@dataclass(frozen=True)
class EvidenceOperation:
    index: int
    kind: OperationKind
    target: EvidenceMap
    change: EvidenceMap
    filter: EvidenceMap
    unsupported: tuple[Span, ...]
    missing_information: tuple[str, ...]


@dataclass(frozen=True)
class EvidenceOutput:
    operations: tuple[EvidenceOperation, ...]
    unsupported: tuple[Span, ...]
    missing_information: tuple[str, ...]


def adopted_texts(spans: Iterable[Span]) -> list[str]:
    return [span.text for span in spans if span.status is SpanStatus.ADOPTED]


# ----------------------------------------------------------------- parsing


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceContractError("DUPLICATE_KEY", key)
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


def _load(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise EvidenceContractError("NOT_JSON_TEXT")
    if len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise EvidenceContractError("PAYLOAD_TOO_LARGE")
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except EvidenceContractError:
        raise
    except (json.JSONDecodeError, TypeError, UnicodeError) as exc:
        raise EvidenceContractError("INVALID_JSON") from exc
    if not isinstance(value, dict):
        raise EvidenceContractError("TOP_LEVEL_NOT_OBJECT")
    for text in _walk_strings(value):
        if any(unicodedata.category(ch) == "Cc" and ch not in "\t\n\r" for ch in text):
            raise EvidenceContractError("CONTROL_CHARACTER")
        # The model must never supply an execution datetime or a Schedule ID.
        if _DATETIME.fullmatch(text):
            raise EvidenceContractError("FORBIDDEN_DATETIME")
        if _UUID.fullmatch(text):
            raise EvidenceContractError("FORBIDDEN_SCHEDULE_ID")
    return value


def _exact_fields(value: Any, required: set[str], optional: frozenset[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceContractError("OBJECT_REQUIRED")
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise EvidenceContractError("MISSING_FIELD", ",".join(sorted(missing)))
    if extra:
        raise EvidenceContractError("EXTRA_FIELD", ",".join(sorted(extra)))
    return value


def _occurrence_offsets(text: str, turn_text: str) -> list[int]:
    haystack = normalize_text(turn_text)
    needle = normalize_text(text)
    return [match.start() for match in re.finditer(re.escape(needle), haystack)]


def _span(value: Any, turns: Mapping[int, str]) -> Span:
    value = _exact_fields(value, {"turn", "text", "status"}, frozenset({"occurrence"}))
    turn = value["turn"]
    if type(turn) is not int or turn not in turns:
        raise EvidenceContractError("INVALID_TURN")
    text = value["text"]
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_SPAN_CHARS:
        raise EvidenceContractError("INVALID_SPAN_TEXT")
    try:
        status = SpanStatus(value["status"])
    except ValueError as exc:
        raise EvidenceContractError("INVALID_SPAN_STATUS") from exc
    offsets = _occurrence_offsets(text, turns[turn])
    if not offsets:
        raise EvidenceContractError("SPAN_NOT_IN_TURN")
    occurrence = value.get("occurrence")
    if occurrence is not None and (type(occurrence) is not int or not 1 <= occurrence <= len(offsets)):
        raise EvidenceContractError("INVALID_OCCURRENCE")
    return Span(turn=turn, text=text.strip(), status=status, occurrence=occurrence)


def _positions(span: Span, turns: Mapping[int, str]) -> list[tuple[int, int]]:
    offsets = _occurrence_offsets(span.text, turns[span.turn])
    if span.occurrence is not None:
        offsets = [offsets[span.occurrence - 1]]
    return [(span.turn, offset) for offset in offsets]


def _evidence_map(value: Any, turns: Mapping[int, str], allowed: frozenset[str]) -> dict[str, tuple[Span, ...]]:
    value = _exact_fields(value, set(), allowed)
    checked: dict[str, tuple[Span, ...]] = {}
    for slot, raw_spans in value.items():
        if not isinstance(raw_spans, list) or not 1 <= len(raw_spans) <= MAX_SPANS_PER_SLOT:
            raise EvidenceContractError("INVALID_SPAN_LIST", slot)
        spans = tuple(_span(item, turns) for item in raw_spans)
        adopted = [span for span in spans if span.status is SpanStatus.ADOPTED]
        superseded = [span for span in spans if span.status is SpanStatus.SUPERSEDED]
        markers = [span for span in spans if span.status is SpanStatus.CORRECTION_MARKER]
        if not adopted:
            raise EvidenceContractError("NO_ADOPTED_SPAN", slot)
        if superseded and not markers:
            raise EvidenceContractError("CORRECTION_MARKER_MISSING", slot)
        for old in superseded:
            # A replaced value must come before its marker, and the marker before the new value.
            ordered = any(
                old_position < marker_position < new_position
                for old_position in _positions(old, turns)
                for marker in markers
                for marker_position in _positions(marker, turns)
                for new in adopted
                for new_position in _positions(new, turns)
            )
            if not ordered:
                raise EvidenceContractError("CORRECTION_ORDER", slot)
        checked[slot] = spans
    return checked


def _missing(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or item not in MISSING_ITEMS for item in value):
        raise EvidenceContractError("INVALID_MISSING_INFORMATION")
    return tuple(value)


def _unsupported(value: Any, turns: Mapping[int, str], limit: int) -> tuple[Span, ...]:
    if not isinstance(value, list) or len(value) > limit:
        raise EvidenceContractError("INVALID_UNSUPPORTED")
    return tuple(_span(item, turns) for item in value)


def parse_evidence_output(raw: str, *, user_turns: Mapping[int, str]) -> EvidenceOutput:
    """Parse and validate one raw model output against the visible user turns."""

    value = _exact_fields(_load(raw), {"operations", "unsupportedEvidence", "missingInformation"})
    raw_operations = value["operations"]
    if not isinstance(raw_operations, list) or len(raw_operations) > MAX_OPERATIONS:
        raise EvidenceContractError("INVALID_OPERATIONS")
    operations: list[EvidenceOperation] = []
    for expected_index, raw_operation in enumerate(raw_operations, 1):
        operation = _exact_fields(
            raw_operation,
            {
                "index",
                "kind",
                "targetEvidence",
                "changeEvidence",
                "filterEvidence",
                "unsupportedEvidence",
                "missingInformation",
            },
        )
        if type(operation["index"]) is not int or operation["index"] != expected_index:
            raise EvidenceContractError("OPERATION_ORDER")
        try:
            kind = OperationKind(operation["kind"])
        except ValueError as exc:
            raise EvidenceContractError("INVALID_OPERATION_KIND") from exc
        operations.append(
            EvidenceOperation(
                index=expected_index,
                kind=kind,
                target=_evidence_map(operation["targetEvidence"], user_turns, TARGET_SLOTS),
                change=_evidence_map(operation["changeEvidence"], user_turns, CHANGE_SLOTS),
                filter=_evidence_map(operation["filterEvidence"], user_turns, FILTER_SLOTS),
                unsupported=_unsupported(operation["unsupportedEvidence"], user_turns, MAX_UNSUPPORTED_SPANS),
                missing_information=_missing(operation["missingInformation"]),
            )
        )
    return EvidenceOutput(
        operations=tuple(operations),
        unsupported=_unsupported(value["unsupportedEvidence"], user_turns, MAX_UNSUPPORTED_SPANS),
        missing_information=_missing(value["missingInformation"]),
    )
