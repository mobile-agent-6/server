"""Turn one parsed evidence operation into a backend decision.

The planner owns every judgement the model used to make: what is missing,
which schedule is the target, and which question to ask.  It reads Schedule
data only through the read-only port and never writes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from app.modules.assistant.decisions import TimeInterpretation
from app.modules.assistant.evidence import texts
from app.modules.assistant.evidence.contract import (
    EvidenceOperation,
    OperationKind,
    SpanStatus,
    adopted_texts,
    evidence_key,
)
from app.modules.assistant.evidence.normalize import (
    SEOUL,
    DatePurpose,
    DateStatus,
    Inherit,
    InheritResult,
    TimeStatus,
    category_candidates,
    combine,
    is_future_scope,
    is_unclear_title,
    inherit_partial_date,
    resolve_category,
    resolve_completed,
    resolve_date,
    resolve_month_range,
    resolve_week_range,
    resolve_time,
    title_contains,
    title_similar,
)
from app.modules.assistant.schedule_read import ScheduleRead, ScheduleReadPort
from app.modules.schedules.category import Category

SEARCH_WINDOW = timedelta(days=92)
TARGET_SCAN_LIMIT = 100  # the read port allows at most 101 rows per read
MAX_CANDIDATES = 10


@dataclass(frozen=True)
class Ask:
    """Ask the user.  ``confirm`` is set for a yes/no confirmation of a value."""

    text: str
    confirm: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Overrides:
    """Values the user already confirmed with a yes/no answer.

    Each one is tied to the evidence it was asked about.  When a later turn
    changes that evidence ("아니 다음 주 금요일로"), the old yes no longer applies.
    """

    change_date: date | None = None
    change_date_source: str | None = None
    midnight_source: str | None = None


@dataclass
class _Moment:
    day: date | None = None
    clock: time | None = None
    absolute: bool = False
    # "저녁 12시" confirmed as midnight: add one day to ``day``.
    next_day_midnight: bool = False


@dataclass
class _Plan:
    """A decision as a JSON-ready dict, or an Ask."""

    decision: dict[str, Any] | None = None
    ask: Ask | None = None
    notes: list[str] = field(default_factory=list)


def _clock_minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def _target_time_minutes(values: list[str]) -> tuple[frozenset[int] | None, Ask | None]:
    """Return safe target clock candidates without guessing an AM or PM."""

    if not values:
        return None, None
    result = resolve_time(values)
    if result.resolved and result.clock is not None:
        return frozenset({_clock_minutes(result.clock)}), None
    if (
        result.status is TimeStatus.NEEDS_AMPM
        and result.hour is not None
        and result.minute is not None
    ):
        morning = 0 if result.hour == 12 else result.hour
        afternoon = 12 if result.hour == 12 else result.hour + 12
        return frozenset({morning * 60 + result.minute, afternoon * 60 + result.minute}), None
    return None, Ask(texts.ASK_TIME_AGAIN)


def _today(reference: datetime) -> date:
    return reference.astimezone(SEOUL).date()


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=SEOUL)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _last(values: list[str]) -> str | None:
    return values[-1].strip() if values else None


def _interpretation(absolute: bool) -> str:
    return (TimeInterpretation.ABSOLUTE if absolute else TimeInterpretation.RELATIVE).value


def _write_time(decision: Mapping[str, Any]) -> datetime | None:
    """The one schedule time this decision would write, when already known."""

    if decision.get("kind") == "create_intent":
        value = decision.get("scheduledAt")
    elif decision.get("kind") == "confirm" and decision.get("operation") == "update":
        changes = decision.get("changes")
        value = changes.get("scheduledAt") if isinstance(changes, Mapping) else None
    else:
        return None
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pre_write_ask(
    decision: Mapping[str, Any], *, reference: datetime, server_time_trusted: bool
) -> Ask | None:
    """Ask before persistence when one relative write time is already past.

    The Schedule write boundary still performs its authoritative validation.
    This earlier check only improves a trusted-clock conversation by asking for
    a replacement while the evidence context is still available. An explicit
    absolute past time keeps the established Schedule rejection result.
    """

    value = _write_time(decision)
    if (
        server_time_trusted
        and decision.get("timeInterpretation") == TimeInterpretation.RELATIVE.value
        and value is not None
        and value <= reference
    ):
        return Ask(texts.ASK_DATETIME_AGAIN)
    return None


# ------------------------------------------------------------ date + time


def _change_moment(
    operation: EvidenceOperation,
    *,
    reference: datetime,
    server_time_trusted: bool,
    overrides: Overrides,
) -> _Moment | Ask:
    moment = _Moment()
    date_texts = adopted_texts(operation.change.get("date", ()))
    time_texts = adopted_texts(operation.change.get("time", ()))
    date_key = evidence_key(operation.change.get("date", ()))
    midnight_key = date_key + "#" + evidence_key(operation.change.get("time", ()))
    today = _today(reference)

    replaced = [
        span.text for span in operation.change.get("date", ()) if span.status is SpanStatus.SUPERSEDED
    ]
    inherited = (
        inherit_partial_date(date_texts[-1], replaced, reference)
        if date_texts and replaced
        else InheritResult(Inherit.NOT_PARTIAL)
    )
    if overrides.change_date is not None and overrides.change_date_source == date_key:
        moment.day = overrides.change_date
    elif inherited.status is Inherit.INHERITED:
        moment.day = inherited.day
        moment.absolute = inherited.absolute
    elif inherited.status is Inherit.INHERITED_NEXT_YEAR and inherited.day is not None:
        # "2월 3일 말고 5일" in October: the same next-year question as "2월 5일".
        if not server_time_trusted:
            return Ask(texts.ASK_ABSOLUTE_DATE)
        return Ask(
            texts.confirm_next_year(inherited.day),
            confirm={"slot": "change_date", "value": inherited.day.isoformat(), "source": date_key},
        )
    elif inherited.status is Inherit.UNKNOWN:
        return Ask(texts.ASK_DATE_AGAIN)
    elif date_texts:
        result = resolve_date(date_texts, reference, purpose=DatePurpose.SCHEDULE)
        if result.status is DateStatus.RESOLVED:
            moment.day = result.dates[0]
            moment.absolute = result.absolute
        elif result.status is DateStatus.PAST_WITHOUT_YEAR:
            if not server_time_trusted:
                return Ask(texts.ASK_ABSOLUTE_DATE)
            if result.proposal is None:
                return Ask(texts.ASK_DATE_AGAIN)
            return Ask(
                texts.confirm_next_year(result.proposal),
                confirm={"slot": "change_date", "value": result.proposal.isoformat(), "source": date_key},
            )
        elif result.status is DateStatus.WEEKDAY_IS_TODAY:
            if not server_time_trusted:
                return Ask(texts.ASK_ABSOLUTE_DATE)
            return Ask(
                texts.confirm_today(today),
                confirm={"slot": "change_date", "value": today.isoformat(), "source": date_key},
            )
        else:
            return Ask(texts.ASK_DATE_AGAIN)

    if time_texts:
        result_time = resolve_time(time_texts)
        if result_time.resolved:
            moment.clock = result_time.clock
        elif result_time.status is TimeStatus.NEEDS_AMPM:
            return Ask(texts.ASK_AMPM)
        elif result_time.status is TimeStatus.NEEDS_CLOCK:
            return Ask(texts.ASK_TIME)
        elif result_time.status is TimeStatus.MIDNIGHT_UNCLEAR:
            if overrides.midnight_source == midnight_key:
                moment.clock = time(0, 0)
                moment.next_day_midnight = True
            elif moment.day is not None:
                return Ask(
                    texts.confirm_midnight(moment.day + timedelta(days=1)),
                    confirm={"slot": "midnight", "value": "yes", "source": midnight_key},
                )
            else:
                # The date is still unknown; ask for it first.
                return Ask(texts.ASK_DATE)
        else:
            return Ask(texts.ASK_TIME_AGAIN)
    return moment


def _scheduled_at(day: date, clock: time, next_day_midnight: bool) -> datetime:
    value = combine(day, clock)
    return value + timedelta(days=1) if next_day_midnight else value


# ------------------------------------------------------------------ create


def _plan_create(
    operation: EvidenceOperation,
    *,
    reference: datetime,
    server_time_trusted: bool,
    overrides: Overrides,
) -> _Plan:
    title = _last(adopted_texts(operation.change.get("title", ())))
    if title is None or is_unclear_title(title):
        return _Plan(ask=Ask(texts.ASK_TITLE))
    moment = _change_moment(
        operation, reference=reference, server_time_trusted=server_time_trusted, overrides=overrides
    )
    if isinstance(moment, Ask):
        return _Plan(ask=moment)
    if moment.day is None:
        return _Plan(ask=Ask(texts.ASK_DATE))
    if moment.clock is None:
        return _Plan(ask=Ask(texts.ASK_TIME))
    category_texts = adopted_texts(operation.change.get("category", ()))
    if category_texts:
        categories = category_candidates(category_texts)
    else:
        categories = category_candidates([title])
    if len(categories) > 1:
        return _Plan(ask=Ask(texts.ASK_CATEGORY))
    category = next(iter(categories)) if len(categories) == 1 else "OTHER"
    return _Plan(
        decision={
            "kind": "create_intent",
            "text": "일정을 등록할게요.",
            "title": title,
            "scheduledAt": _iso(_scheduled_at(moment.day, moment.clock, moment.next_day_midnight)),
            "category": category,
            "timeInterpretation": _interpretation(moment.absolute),
        }
    )


# -------------------------------------------------------------------- list


def _plan_list(
    operation: EvidenceOperation,
    *,
    reference: datetime,
) -> _Plan:
    date_texts = adopted_texts(operation.filter.get("date", ()))
    future = is_future_scope(adopted_texts(operation.filter.get("futureScope", ())))
    now = reference.astimezone(SEOUL)
    absolute = False
    if date_texts:
        month = resolve_month_range(date_texts, reference)
        week = resolve_week_range(date_texts, reference)
        if month is not None:
            start, end, absolute = month
        elif week is not None:
            start, end = week
        else:
            result = resolve_date(date_texts, reference, purpose=DatePurpose.SEARCH)
            if result.status is DateStatus.RESOLVED:
                # A list needs one range. A yearless date means the coming
                # occurrence, while an explicit year keeps the exact date.
                selected = result.dates[0]
                if not result.absolute:
                    selected = next(
                        (day for day in result.dates if day >= _today(reference)),
                        selected,
                    )
                start = _day_start(selected)
                absolute = result.absolute
            elif result.status is DateStatus.WEEKDAY_IS_TODAY:
                start = _day_start(_today(reference))
            else:
                return _Plan(ask=Ask(texts.ASK_DATE_AGAIN))
            end = start + timedelta(days=1)
        if future:
            start = max(start, now)
            if end <= start:
                return _Plan(ask=Ask(texts.ASK_LIST_SCOPE))
    else:
        # No period: show what is coming up (user decision, 2026-10-29).
        start, end = now, now + SEARCH_WINDOW
    decision: dict[str, Any] = {
        "kind": "list_intent",
        "text": texts.LIST_RESULT,
        "from": _iso(start),
        "to": _iso(end),
        "timeInterpretation": _interpretation(absolute),
    }
    category = resolve_category(adopted_texts(operation.filter.get("category", ())))
    if category is not None:
        decision["category"] = category
    completed = resolve_completed(adopted_texts(operation.filter.get("completed", ())))
    if completed is not None:
        decision["completed"] = completed
    time_texts = adopted_texts(operation.filter.get("time", ()))
    if time_texts:
        result_time = resolve_time(time_texts)
        if result_time.status is TimeStatus.NEEDS_AMPM:
            return _Plan(ask=Ask(texts.ASK_AMPM))
        if not result_time.resolved or result_time.clock is None:
            return _Plan(ask=Ask(texts.ASK_TIME_AGAIN))
        decision["timeMinutes"] = _clock_minutes(result_time.clock)
    title = _last(adopted_texts(operation.filter.get("title", ())))
    if title is not None:
        if is_unclear_title(title):
            return _Plan(ask=Ask(texts.ASK_TARGET_TITLE))
        decision["title"] = title
    return _Plan(decision=decision)


# ---------------------------------------------------------- update, delete


@dataclass
class _Targets:
    schedules: list[ScheduleRead]
    absolute: bool


def _find_targets(
    operation: EvidenceOperation,
    *,
    reference: datetime,
    reader: ScheduleReadPort,
) -> _Targets | Ask:
    title = _last(adopted_texts(operation.target.get("title", ())))
    if title is None or is_unclear_title(title):
        return Ask(texts.ASK_TARGET_TITLE)
    date_texts = adopted_texts(operation.target.get("date", ()))
    absolute = False
    if date_texts:
        result = resolve_date(date_texts, reference, purpose=DatePurpose.SEARCH)
        if result.status is DateStatus.RESOLVED:
            days = result.dates
            absolute = result.absolute
        elif result.status is DateStatus.WEEKDAY_IS_TODAY:
            days = (_today(reference),)
        else:
            return Ask(texts.ASK_DATE_AGAIN)
        ranges = [(_day_start(day), _day_start(day) + timedelta(days=1)) for day in days]
    else:
        # No date: search 92 days back and 92 days ahead by title (#37 stage 5).
        # The read port allows at most 92 days per read, so read each side.
        start = _day_start(_today(reference))
        ranges = [(start - SEARCH_WINDOW, start), (start, start + SEARCH_WINDOW)]
    completed = None
    state_texts = adopted_texts(operation.target.get("completedState", ()))
    if state_texts:
        completed = resolve_completed(state_texts)
    category_value = resolve_category(adopted_texts(operation.target.get("category", ())))
    category = Category(category_value) if category_value is not None else None
    target_minutes, time_ask = _target_time_minutes(
        adopted_texts(operation.target.get("time", ()))
    )
    if time_ask is not None:
        return time_ask
    found: dict[str, ScheduleRead] = {}
    for start, end in ranges:
        rows = reader.list(
            from_utc=start,
            to_utc=end,
            category=category,
            completed=completed,
            limit=TARGET_SCAN_LIMIT + 1,
            title=title,
        )
        if len(rows) > TARGET_SCAN_LIMIT:
            return Ask(texts.ASK_NARROW)
        for row in rows:
            if target_minutes is not None:
                local = row.scheduled_at.astimezone(SEOUL)
                if local.hour * 60 + local.minute not in target_minutes:
                    continue
            if title_contains(title, row.title):
                found[str(row.id)] = row
    if not found:
        # Only when nothing matches: a one-letter slip ("상하자"), shown with its
        # stored title in the confirmation or the candidate list. This fallback
        # still uses the bounded scan because SQL substring filtering cannot
        # express the one-letter similarity rule safely.
        similar: dict[str, ScheduleRead] = {}
        for start, end in ranges:
            rows = reader.list(
                from_utc=start,
                to_utc=end,
                category=category,
                completed=completed,
                limit=TARGET_SCAN_LIMIT + 1,
            )
            if len(rows) > TARGET_SCAN_LIMIT:
                return Ask(texts.ASK_NARROW)
            for row in rows:
                if target_minutes is not None:
                    local = row.scheduled_at.astimezone(SEOUL)
                    if local.hour * 60 + local.minute not in target_minutes:
                        continue
                if title_similar(title, row.title):
                    similar[str(row.id)] = row
        found = similar
    ordered = sorted(found.values(), key=lambda row: (row.scheduled_at, str(row.id)))
    return _Targets(schedules=ordered, absolute=absolute)


def _changes_for(
    operation: EvidenceOperation,
    schedule: ScheduleRead,
    moment: _Moment,
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    title = _last(adopted_texts(operation.change.get("title", ())))
    if title is not None:
        changes["title"] = title
    category_texts = _separate_category_texts(operation)
    # A category word taken from inside the target title ("입고" in "입고 검수 일정을
    # 옮겨 줘") is part of the name, not a request to change the category.
    if category_texts:
        category = resolve_category(category_texts)
        if category is not None:
            changes["category"] = category
    completed = resolve_completed(adopted_texts(operation.change.get("completed", ())))
    if completed is not None:
        changes["completed"] = completed
    if moment.day is not None or moment.clock is not None:
        current = schedule.scheduled_at.astimezone(SEOUL)
        # Date only keeps the time; time only keeps the date (user decision).
        day = moment.day if moment.day is not None else current.date()
        clock = moment.clock if moment.clock is not None else current.time().replace(second=0, microsecond=0)
        changes["scheduledAt"] = _iso(_scheduled_at(day, clock, moment.next_day_midnight))
    return changes


def _separate_category_texts(operation: EvidenceOperation) -> list[str]:
    """Keep category evidence that is not merely a word inside the target title."""

    titles = [
        span
        for span in operation.target.get("title", ())
        if span.status is SpanStatus.ADOPTED
    ]
    result: list[str] = []
    for category in operation.change.get("category", ()):
        if category.status is not SpanStatus.ADOPTED:
            continue
        nested = any(
            category.turn == title.turn
            and title_contains(category.text, title.text)
            and not (
                category.occurrence is not None
                and (
                    category.occurrence > 1
                    or (
                        title.occurrence is not None
                        and category.occurrence != title.occurrence
                    )
                )
            )
            for title in titles
        )
        if not nested:
            result.append(category.text)
    return result


def _has_change_value(operation: EvidenceOperation) -> bool:
    return any(slot in operation.change for slot in ("title", "date", "time", "category", "completed"))


def _plan_target_operation(
    operation: EvidenceOperation,
    *,
    reference: datetime,
    server_time_trusted: bool,
    overrides: Overrides,
    reader: ScheduleReadPort,
) -> _Plan:
    is_update = operation.kind is OperationKind.UPDATE
    moment = _Moment()
    if is_update:
        if not _has_change_value(operation):
            return _Plan(ask=Ask(texts.ASK_CHANGE_VALUE))
        new_title = _last(adopted_texts(operation.change.get("title", ())))
        if new_title is not None and is_unclear_title(new_title):
            return _Plan(ask=Ask(texts.ASK_CHANGE_VALUE))
        resolved = _change_moment(
            operation, reference=reference, server_time_trusted=server_time_trusted, overrides=overrides
        )
        if isinstance(resolved, Ask):
            return _Plan(ask=resolved)
        moment = resolved
    targets = _find_targets(operation, reference=reference, reader=reader)
    if isinstance(targets, Ask):
        return _Plan(ask=targets)
    operation_name = "update" if is_update else "delete"
    if not targets.schedules:
        return _Plan(decision={"kind": "no_match", "operation": operation_name})
    if len(targets.schedules) > MAX_CANDIDATES:
        return _Plan(ask=Ask(texts.ASK_NARROW))
    interpretation = _interpretation(targets.absolute and (moment.day is None or moment.absolute))

    def candidate(schedule: ScheduleRead) -> dict[str, Any]:
        item: dict[str, Any] = {
            "scheduleId": str(schedule.id),
            "operation": operation_name,
            "timeInterpretation": interpretation,
        }
        if is_update:
            item["changes"] = _changes_for(operation, schedule, moment)
        return item

    candidates = [candidate(schedule) for schedule in targets.schedules]
    if is_update and all(not item.get("changes") for item in candidates):
        return _Plan(ask=Ask(texts.ASK_CHANGE_VALUE))
    if len(targets.schedules) == 1:
        schedule = targets.schedules[0]
        text = texts.confirm_update(schedule.title) if is_update else texts.confirm_delete(schedule.title)
        return _Plan(decision={"kind": "confirm", "text": text, **candidates[0]})
    return _Plan(
        decision={
            "kind": "select_candidate",
            "text": texts.SELECT_TARGET,
            "candidates": candidates,
        }
    )


# ------------------------------------------------------------------ entry


def plan_operation(
    operation: EvidenceOperation,
    *,
    reference: datetime,
    server_time_trusted: bool,
    overrides: Overrides,
    reader: ScheduleReadPort,
) -> tuple[dict[str, Any] | None, Ask | None]:
    """Return ``(decision, None)`` or ``(None, ask)`` for one operation."""

    if operation.kind is OperationKind.CREATE:
        plan = _plan_create(
            operation, reference=reference, server_time_trusted=server_time_trusted, overrides=overrides
        )
    elif operation.kind is OperationKind.LIST:
        plan = _plan_list(operation, reference=reference)
    else:
        plan = _plan_target_operation(
            operation,
            reference=reference,
            server_time_trusted=server_time_trusted,
            overrides=overrides,
            reader=reader,
        )
    if plan.decision is not None:
        ask = _pre_write_ask(
            plan.decision, reference=reference, server_time_trusted=server_time_trusted
        )
        if ask is not None:
            return None, ask
    return plan.decision, plan.ask
