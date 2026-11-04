"""Independent safety checks around evidence planning and pending writes.

The model may point at text, but it may not choose silently between conflicting
values or omit a modifier that changes a date or time. These checks compare the
model spans with the original turns before planning, then verify the
backend-built write values once more after planning.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import StrEnum
from typing import Any

from app.modules.assistant.evidence import texts
from app.modules.assistant.evidence.contract import (
    EvidenceOperation,
    EvidenceOutput,
    Span,
    SpanStatus,
    adopted_texts,
)
from app.modules.assistant.evidence.gate import EvidenceGateError
from app.modules.assistant.evidence.normalize import (
    DATE_MODIFIER_PATTERN,
    SEOUL,
    DatePurpose,
    DateStatus,
    TimeStatus,
    combine,
    normalize_text,
    resolve_category,
    resolve_completed,
    resolve_date,
    resolve_time,
)


class EvidenceQuestion(StrEnum):
    DATE = "date"
    TIME = "time"
    DATETIME = "datetime"
    TITLE = "title"
    VALUE = "value"

    @property
    def text(self) -> str:
        return {
            EvidenceQuestion.DATE: texts.ASK_DATE_AGAIN,
            EvidenceQuestion.TIME: texts.ASK_TIME_AGAIN,
            EvidenceQuestion.DATETIME: texts.ASK_DATETIME_AGAIN,
            EvidenceQuestion.TITLE: texts.ASK_TITLE,
            EvidenceQuestion.VALUE: texts.ASK_RESTATE,
        }[self]


class PreWriteSafetyError(RuntimeError):
    """A backend-built write value no longer agrees with its evidence."""


@dataclass(frozen=True)
class _LocatedSpan:
    role: str
    slot: str
    span: Span
    ranges: tuple[tuple[int, int], ...]


_DATE_EXPRESSION = re.compile(
    rf"(?:{DATE_MODIFIER_PATTERN})|"
    r"(?:\d{1,3}|한)\s*(?:주|개월|달)\s*(?:뒤|후|전)"
    r"(?:\s*(?:\d{1,2}\s*일|[월화수목금토일]요일))?|"
    r"(?:일\s*주일|보름)\s*(?:뒤|후|전)"
    r"(?:\s*(?:\d{1,2}\s*일|[월화수목금토일]요일))?|"
    r"(?:담주|차주)\s*(?:[월화수목금토일]요일)?|"
    r"(?:다다음|다음|이번|지난|저번)\s*주(?:에)?\s*[월화수목금토일]요일|"
    r"(?:다다음|다음|이번|지난|저번)\s*달(?:에)?\s*\d{1,2}\s*일|"
    r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일|"
    r"\d{1,2}\s*월\s*\d{1,2}\s*일|"
    r"\d{1,3}\s*일\s*(?:뒤|후|전)|"
    r"오늘|내일|모레|글피|어제|그제|그저께|"
    r"[월화수목금토일]요일|(?<![\d월])\d{1,2}\s*일(?!\s*(?:간|동안))"
)
_TIME_EXPRESSION = re.compile(
    r"(?:오전|오후|아침|저녁|새벽|점심|낮|밤)\s*"
    r"(?:\d{1,2}|열\s*한|열\s*두|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*시(?!간)"
    r"(?:\s*(?:\d{1,2}\s*분|반))?|"
    r"\d{1,2}\s*:\s*\d{2}|정오|자정|"
    r"(?:\d{1,2}|열\s*한|열\s*두|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*시(?!간)"
    r"(?:\s*(?:\d{1,2}\s*분|반))?"
)
_REJECTED_PHRASE = re.compile(r"^\s*(?:은|는)?\s*(?:말고|아니고)")
_SIGNAL_CHARACTER = re.compile(r"[0-9A-Za-z가-힣]")


def clarification_answer_matches(question: EvidenceQuestion, text: str) -> bool:
    """Return whether a follow-up contains the value just requested."""

    value = normalize_text(text)
    if question is EvidenceQuestion.DATE:
        return _DATE_EXPRESSION.search(value) is not None
    if question is EvidenceQuestion.TIME:
        return _TIME_EXPRESSION.search(value) is not None
    if question is EvidenceQuestion.DATETIME:
        return (
            _DATE_EXPRESSION.search(value) is not None
            and _TIME_EXPRESSION.search(value) is not None
        )
    return _SIGNAL_CHARACTER.search(value) is not None


def _occurrence_ranges(span: Span, turn: str) -> tuple[tuple[int, int], ...]:
    value = normalize_text(turn)
    needle = normalize_text(span.text)
    matches = tuple((match.start(), match.end()) for match in re.finditer(re.escape(needle), value))
    if span.occurrence is not None:
        return matches[span.occurrence - 1 : span.occurrence]
    return matches


def _semantic_spans(operation: EvidenceOperation, turns: Mapping[int, str]) -> list[_LocatedSpan]:
    result: list[_LocatedSpan] = []
    for role, evidence in (
        ("target", operation.target),
        ("change", operation.change),
        ("filter", operation.filter),
    ):
        for slot, spans in evidence.items():
            for span in spans:
                if span.status is SpanStatus.CORRECTION_MARKER:
                    continue
                result.append(
                    _LocatedSpan(
                        role=role,
                        slot=slot,
                        span=span,
                        ranges=_occurrence_ranges(span, turns[span.turn]),
                    )
                )
    return result


def check_role_span_overlaps(
    output: EvidenceOutput, *, user_turns: Mapping[int, str]
) -> None:
    """Reject unambiguous partial overlap between different semantic roles.

    A category cue may be part of a title ("입고 검수"), which is an intentional
    contract rule. Other nesting, such as using "내일 오후 3시 회의" as the
    title while also using its date and time, makes the execution evidence
    ambiguous and is rejected.
    """

    for operation in output.operations:
        located = _semantic_spans(operation, user_turns)
        for index, left in enumerate(located):
            for right in located[index + 1 :]:
                if left.span.turn != right.span.turn or (left.role, left.slot) == (
                    right.role,
                    right.slot,
                ):
                    continue
                if {left.slot, right.slot} == {"title", "category"}:
                    continue
                # Without occurrence numbers, repeated text can have more than
                # one valid assignment. Reject only an unambiguous overlap.
                if not left.ranges or not right.ranges:
                    continue
                overlaps = [
                    max(a_start, b_start) < min(a_end, b_end)
                    for a_start, a_end in left.ranges
                    for b_start, b_end in right.ranges
                ]
                if overlaps and all(overlaps):
                    raise EvidenceGateError(
                        "EVIDENCE_ROLE_OVERLAP",
                        f"{left.role}.{left.slot}:{right.role}.{right.slot}",
                    )


def _slot_question(slot: str) -> EvidenceQuestion:
    if slot == "date":
        return EvidenceQuestion.DATE
    if slot == "time":
        return EvidenceQuestion.TIME
    if slot == "title":
        return EvidenceQuestion.TITLE
    return EvidenceQuestion.VALUE


def _date_value(text: str, *, role: str, reference: datetime) -> tuple[date, ...] | None:
    purpose = DatePurpose.SCHEDULE if role == "change" else DatePurpose.SEARCH
    result = resolve_date([text], reference, purpose=purpose)
    if result.status is DateStatus.RESOLVED:
        return result.dates
    if result.status is DateStatus.WEEKDAY_IS_TODAY:
        return (reference.astimezone(SEOUL).date(),)
    return None


def _time_value(text: str) -> tuple[int, int] | None:
    result = resolve_time([text])
    if result.resolved and result.hour is not None and result.minute is not None:
        return result.hour, result.minute
    return None


def _has_conflicting_adopted(
    role: str, slot: str, spans: Sequence[Span], reference: datetime
) -> bool:
    adopted = [span.text for span in spans if span.status is SpanStatus.ADOPTED]
    if len(adopted) < 2:
        return False
    normalized = {normalize_text(value) for value in adopted}
    if len(normalized) == 1:
        return False
    if slot == "date":
        values = [_date_value(value, role=role, reference=reference) for value in adopted]
        return any(value is None for value in values) or len(set(values)) > 1
    if slot == "time":
        values = [_time_value(value) for value in adopted]
        resolved = {value for value in values if value is not None}
        if len(resolved) == len(values):
            return len(resolved) > 1
        combined = resolve_time(adopted)
        fragment_statuses = {resolve_time([value]).status for value in adopted}
        return not (
            combined.resolved
            and fragment_statuses <= {TimeStatus.NEEDS_AMPM, TimeStatus.NEEDS_CLOCK}
            and TimeStatus.NEEDS_AMPM in fragment_statuses
            and TimeStatus.NEEDS_CLOCK in fragment_statuses
        )
    if slot in {"category"}:
        values = {resolve_category([value]) for value in adopted}
        return None in values or len(values) > 1
    if slot in {"completed", "completedState"}:
        values = {resolve_completed([value]) for value in adopted}
        return None in values or len(values) > 1
    return True


def _all_operation_spans(operation: EvidenceOperation) -> list[Span]:
    return [
        span
        for evidence in (operation.target, operation.change, operation.filter)
        for spans in evidence.values()
        for span in spans
    ] + list(operation.unsupported)


def _covered_positions(
    operations: Sequence[EvidenceOperation],
    extra_spans: Sequence[Span],
    turn_number: int,
    turn: str,
) -> set[int]:
    positions: set[int] = set()
    spans = [span for operation in operations for span in _all_operation_spans(operation)]
    spans.extend(extra_spans)
    for span in spans:
        if span.turn != turn_number:
            continue
        for start, end in _occurrence_ranges(span, turn):
            positions.update(range(start, end))
    return positions


def _uncaptured_expression(
    pattern: re.Pattern[str],
    operations: Sequence[EvidenceOperation],
    extra_spans: Sequence[Span],
    turn_number: int,
    turn: str,
) -> bool:
    value = normalize_text(turn)
    covered = _covered_positions(operations, extra_spans, turn_number, value)
    for match in pattern.finditer(value):
        if _REJECTED_PHRASE.match(value[match.end() :]):
            continue
        required = {
            index
            for index in range(match.start(), match.end())
            if _SIGNAL_CHARACTER.fullmatch(value[index])
        }
        if required and not required <= covered:
            return True
    return False


def evidence_question(
    operation: EvidenceOperation,
    *,
    turns: Sequence[str],
    reference: datetime,
    coverage_operations: Sequence[EvidenceOperation] | None = None,
    coverage_spans: Sequence[Span] = (),
) -> EvidenceQuestion | None:
    """Return a clarification question for conflicting or incomplete evidence."""

    for role, evidence in (
        ("target", operation.target),
        ("change", operation.change),
        ("filter", operation.filter),
    ):
        for slot, spans in evidence.items():
            if _has_conflicting_adopted(role, slot, spans, reference):
                return _slot_question(slot)

    last_turn = len(turns)
    adopted_date_turns = {
        span.turn
        for evidence in (operation.target, operation.change, operation.filter)
        for span in evidence.get("date", ())
        if span.status is SpanStatus.ADOPTED
    }
    adopted_time_turns = {
        span.turn
        for evidence in (operation.target, operation.change, operation.filter)
        for span in evidence.get("time", ())
        if span.status is SpanStatus.ADOPTED
    }
    operations = tuple(coverage_operations or (operation,))
    for turn_number, turn in enumerate(turns, 1):
        if turn_number == last_turn or turn_number in adopted_date_turns:
            if _uncaptured_expression(
                _DATE_EXPRESSION, operations, coverage_spans, turn_number, turn
            ):
                return EvidenceQuestion.DATE
        if turn_number == last_turn or turn_number in adopted_time_turns:
            if _uncaptured_expression(
                _TIME_EXPRESSION, operations, coverage_spans, turn_number, turn
            ):
                return EvidenceQuestion.TIME
    return None


def _decision_write_times(decision: Mapping[str, Any]) -> list[tuple[datetime, str]]:
    values: list[tuple[datetime, str]] = []

    def append(value: object, interpretation: object) -> None:
        if not isinstance(value, str) or not isinstance(interpretation, str):
            return
        try:
            values.append((datetime.fromisoformat(value.replace("Z", "+00:00")), interpretation))
        except ValueError as exc:
            raise PreWriteSafetyError("invalid planned datetime") from exc

    kind = decision.get("kind")
    if kind == "create_intent":
        append(decision.get("scheduledAt"), decision.get("timeInterpretation"))
    elif kind == "confirm" and decision.get("operation") == "update":
        changes = decision.get("changes")
        if isinstance(changes, Mapping):
            append(changes.get("scheduledAt"), decision.get("timeInterpretation"))
    # Candidate selection is not yet a write. A selected candidate is checked
    # again when it becomes one concrete confirmation in the processor.
    return values


def _verify_create_datetime(
    decision: Mapping[str, Any], operation: EvidenceOperation, reference: datetime
) -> None:
    date_spans = operation.change.get("date", ())
    time_spans = operation.change.get("time", ())
    if any(
        span.status is SpanStatus.SUPERSEDED for span in (*date_spans, *time_spans)
    ):
        # Partial corrections inherit context in the planner. They are checked
        # by the conflict and completeness gates above, then by the write layer.
        return
    date_result = resolve_date(
        adopted_texts(date_spans), reference, purpose=DatePurpose.SCHEDULE
    )
    time_result = resolve_time(adopted_texts(time_spans))
    if (
        date_result.status is not DateStatus.RESOLVED
        or not date_result.dates
        or not time_result.resolved
        or time_result.clock is None
    ):
        return
    actual_values = _decision_write_times(decision)
    if len(actual_values) != 1:
        raise PreWriteSafetyError("create decision has no single planned datetime")
    actual, interpretation = actual_values[0]
    expected = combine(date_result.dates[0], time_result.clock)
    if actual != expected:
        raise PreWriteSafetyError("planned datetime does not match adopted evidence")
    expected_interpretation = "absolute" if date_result.absolute else "relative"
    if interpretation != expected_interpretation:
        raise PreWriteSafetyError("planned time interpretation does not match evidence")


def pre_write_question(
    decision: Mapping[str, Any],
    operation: EvidenceOperation,
    *,
    turns: Sequence[str],
    reference: datetime,
    server_time_trusted: bool,
    coverage_operations: Sequence[EvidenceOperation] | None = None,
    coverage_spans: Sequence[Span] = (),
) -> EvidenceQuestion | None:
    """Verify one backend-built create or update before it reaches pending state."""

    question = evidence_question(
        operation,
        turns=turns,
        reference=reference,
        coverage_operations=coverage_operations,
        coverage_spans=coverage_spans,
    )
    if question is not None:
        return question
    if decision.get("kind") == "create_intent":
        expected_title = adopted_texts(operation.change.get("title", ()))
        if not expected_title or decision.get("title") != expected_title[-1].strip():
            raise PreWriteSafetyError("planned title does not match adopted evidence")
        expected_category = (
            resolve_category(adopted_texts(operation.change.get("category", ())))
            or resolve_category([expected_title[-1]])
            or "OTHER"
        )
        if decision.get("category") != expected_category:
            raise PreWriteSafetyError("planned category does not match adopted evidence")
        _verify_create_datetime(decision, operation, reference)
    for value, interpretation in _decision_write_times(decision):
        if interpretation == "relative" and value <= reference:
            return EvidenceQuestion.DATETIME
        if not server_time_trusted and interpretation not in {"relative", "absolute"}:
            raise PreWriteSafetyError("planned time trust marker is missing")
    return None
