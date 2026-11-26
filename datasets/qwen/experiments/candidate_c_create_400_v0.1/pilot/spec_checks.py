"""Authoring guards for hand-written Candidate-C create specs.

These checks only read specs and raise or return errors.  They write no
artifacts, so running them on the pilot SPECS leaves the pilot output unchanged.

Status: BE-PROPOSED.  The excluded expressions below are not product policy;
they are kept out of new batches until the backend normalizer handles them
(see README "100건 생성 보조 기능").
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Iterable

# Reference times for new batches.  The pilot mostly used 2028-02-10 08:20.
REFERENCE_PRESETS = {
    "weekday_morning": "2028-02-10T08:20:00+09:00",   # Thursday
    "weekday_evening": "2028-03-15T19:40:00+09:00",   # Wednesday
    "early_morning": "2028-04-04T06:10:00+09:00",     # Tuesday
    "weekend": "2028-05-13T11:05:00+09:00",           # Saturday
    "month_end": "2028-06-30T16:30:00+09:00",         # Friday
    "leap_day": "2028-02-29T09:00:00+09:00",          # Tuesday
    "year_end": "2028-12-31T20:00:00+09:00",          # Sunday
}

# Expressions the Issue #30 normalizer cannot turn into the intended value.
EXCLUDED_TIME_WORDS = re.compile(r"밤|새벽|자정|정오|점심|낮")
EXCLUDED_KOREAN_HOUR = re.compile(r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|열한|열두)\s*시")

_FULL_DATE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")
_MONTH_DAY = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")
_WEEKDAY = re.compile(r"이번\s*주\s*([월화수목금토일])요일")
_CLOCK = re.compile(r"(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분|\s*(반))?")
_PERIOD = re.compile(r"오전|오후|아침|저녁")
_RELATIVE_WORDS = (("글피", 3), ("모레", 2), ("내일", 1), ("오늘", 0))


class SpecError(ValueError):
    """A spec that must not enter a batch as written."""


def _adopted_texts(value: dict[str, Any] | None) -> list[str]:
    return [span["text"] for span in (value or {}).get("adoptedSpans", [])]


def _date_from(texts: list[str], reference: datetime) -> date | None:
    combined = " ".join(texts)
    if not combined:
        return None
    local = reference.date()
    full = _FULL_DATE.search(combined)
    if full:
        return date(*map(int, full.groups()))
    month_day = _MONTH_DAY.search(combined)
    if month_day:
        value = date(local.year, *map(int, month_day.groups()))
        if value < local:
            raise SpecError(
                f"EXCLUDED_YEARLESS_PAST_DATE: {combined!r} is before {local} "
                "and the normalizer does not roll over to next year"
            )
        return value
    for word, offset in _RELATIVE_WORDS:
        if word in combined:
            return local + timedelta(days=offset)
    weekday = _WEEKDAY.search(combined)
    if weekday:
        index = "월화수목금토일".index(weekday.group(1))
        return local - timedelta(days=local.weekday()) + timedelta(days=index)
    raise SpecError(f"UNSUPPORTED_DATE_EXPRESSION: {combined!r}")


def _clock_from(texts: list[str]) -> tuple[int, int] | None:
    """Return (hour, minute), or None when AM/PM is ambiguous."""
    combined = " ".join(texts)
    if not combined:
        return None
    if EXCLUDED_TIME_WORDS.search(combined):
        raise SpecError(f"EXCLUDED_TIME_PERIOD_WORD: {combined!r}")
    if EXCLUDED_KOREAN_HOUR.search(combined):
        raise SpecError(f"EXCLUDED_KOREAN_NUMERAL_HOUR: {combined!r}")
    match = _CLOCK.search(combined)
    if not match:
        raise SpecError(f"UNSUPPORTED_TIME_EXPRESSION: {combined!r}")
    hour = int(match.group(1))
    minute = 30 if match.group(3) else int(match.group(2) or 0)
    period_match = _PERIOD.search(combined)
    period = period_match.group(0) if period_match else None
    if period in {"오후", "저녁"} and hour < 12:
        hour += 12
    if period in {"오전", "아침"} and hour == 12:
        hour = 0
    if period is None and 1 <= hour <= 12:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SpecError(f"INVALID_CLOCK: {combined!r}")
    return hour, minute


def computed_scheduled_at(specification: dict[str, Any]) -> str | None:
    """Recompute scheduledAt from the final adopted date/time spans."""
    reference = datetime.fromisoformat(specification["referenceTime"])
    day = _date_from(_adopted_texts(specification["dateEvidence"]), reference)
    clock = _clock_from(_adopted_texts(specification["timeEvidence"]))
    if day is None or clock is None:
        return None
    value = datetime(day.year, day.month, day.day, *clock, tzinfo=reference.tzinfo)
    return value.isoformat()


def check_spec(specification: dict[str, Any]) -> None:
    """Raise SpecError when a spec uses excluded expressions or a wrong scheduledAt."""
    case_id = specification["caseId"]
    try:
        computed = computed_scheduled_at(specification)
    except SpecError as exc:
        raise SpecError(f"{case_id}: {exc}") from None
    expected = specification["scheduledAt"]
    if expected is not None and computed != expected:
        raise SpecError(f"{case_id}: scheduledAt {expected!r} != recomputed {computed!r}")


def check_specs(specifications: Iterable[dict[str, Any]]) -> None:
    errors = []
    for specification in specifications:
        try:
            check_spec(specification)
        except SpecError as exc:
            errors.append(str(exc))
    if errors:
        raise SpecError("; ".join(errors))


def batch_quality_errors(
    specifications: list[dict[str, Any]],
    *,
    max_reference_share: float = 0.25,
) -> list[str]:
    """Diversity checks for new batches (not applied to the pilot)."""
    errors = []
    titles = Counter(
        spec["titleEvidence"]["adoptedSpans"][-1]["text"]
        for spec in specifications
        if spec["titleEvidence"]
    )
    for title, count in sorted(titles.items()):
        if count > 1:
            errors.append(f"DUPLICATE_TITLE: {title!r} x{count}")
    references = Counter(spec["referenceTime"] for spec in specifications)
    total = len(specifications)
    for reference, count in sorted(references.items()):
        if total and count / total > max_reference_share:
            errors.append(
                f"REFERENCE_TIME_CONCENTRATED: {reference} {count}/{total} > {max_reference_share:.0%}"
            )
    return errors
