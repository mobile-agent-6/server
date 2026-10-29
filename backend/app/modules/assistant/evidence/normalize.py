"""Turn evidence spans into dates, times, categories and query ranges.

Rules follow one principle: when an expression is unknown or ambiguous, the
result says so and the caller asks the user.  Nothing here guesses.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum

SEOUL = timezone(timedelta(hours=9), "Asia/Seoul")

_WEEKDAYS = "월화수목금토일"
_RELATIVE_DAYS = (("글피", 3), ("모레", 2), ("내일", 1), ("오늘", 0))


def normalize_text(value: str) -> str:
    """NFKC and collapse whitespace, the same way spans are matched to turns."""

    return " ".join(unicodedata.normalize("NFKC", value).split())


def _joined(texts: Sequence[str]) -> str:
    return " ".join(normalize_text(text) for text in texts)


def _local_today(reference: datetime) -> date:
    return reference.astimezone(SEOUL).date()


# ------------------------------------------------------------------- dates


class DateStatus(StrEnum):
    RESOLVED = "resolved"
    # A real date expression the user must confirm or clarify.
    PAST_WITHOUT_YEAR = "past_without_year"
    WEEKDAY_IS_TODAY = "weekday_is_today"
    INVALID_DATE = "invalid_date"
    UNSUPPORTED = "unsupported"
    # The text is not a date expression at all.
    NOT_A_DATE = "not_a_date"


class DatePurpose(StrEnum):
    """What the date is for.

    ``SCHEDULE`` is a date to write (create or change).  ``SEARCH`` finds an
    existing schedule, so an omitted year may mean this year or next year.
    """

    SCHEDULE = "schedule"
    SEARCH = "search"


@dataclass(frozen=True)
class DateResult:
    status: DateStatus
    # One date for SCHEDULE.  SEARCH may hold two (this year and next year).
    dates: tuple[date, ...] = ()
    absolute: bool = False
    # For PAST_WITHOUT_YEAR: the next-year date to propose in the question.
    proposal: date | None = None

    @property
    def resolved(self) -> bool:
        return self.status is DateStatus.RESOLVED


_FULL_DATE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")
_MONTH_DAY = re.compile(r"(\d{1,2})월\s*(\d{1,2})일")
_THIS_WEEK_DAY = re.compile(r"이번\s*주\s*([월화수목금토일])요일")
_OTHER_WEEK_DAY = re.compile(r"(?:다음|다다음|지난|저번)\s*주\s*[월화수목금토일]요일")
# Recurring wording is not a single date.
_RECURRING = re.compile(r"매주|매일|매달|매월|격주|주마다|날마다")
_BARE_WEEKDAY = re.compile(r"(?<![가-힣])([월화수목금토일])요일")
# Date-like wording that this version does not resolve.  It makes the result
# UNSUPPORTED (ask the user) instead of NOT_A_DATE (a model role error).
_DATE_LIKE = re.compile(
    r"\d+\s*(?:월|일)|요일|주말|다음\s*(?:주|달)|이번\s*(?:주|달)|지난\s*(?:주|달)|"
    r"저번\s*(?:주|달)|월말|월초|연말|연초|\d+\s*/\s*\d+|(?:며칠|\d+\s*일)\s*(?:뒤|후)|"
    r"오늘|내일|모레|글피|어제|그제|그저께"
)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def resolve_date(
    texts: Sequence[str],
    reference: datetime,
    *,
    purpose: DatePurpose,
) -> DateResult:
    combined = _joined(texts)
    today = _local_today(reference)

    full = _FULL_DATE.search(combined)
    if full:
        value = _safe_date(*map(int, full.groups()))
        if value is None:
            return DateResult(DateStatus.INVALID_DATE)
        return DateResult(DateStatus.RESOLVED, (value,), absolute=True)

    month_day = _MONTH_DAY.search(combined)
    if month_day:
        month, day_value = map(int, month_day.groups())
        this_year = _safe_date(today.year, month, day_value)
        next_year = _safe_date(today.year + 1, month, day_value)
        if this_year is None and next_year is None:
            return DateResult(DateStatus.INVALID_DATE)
        if purpose is DatePurpose.SEARCH:
            found = tuple(value for value in (this_year, next_year) if value is not None)
            return DateResult(DateStatus.RESOLVED, found)
        if this_year is not None and this_year >= today:
            return DateResult(DateStatus.RESOLVED, (this_year,))
        # The date already passed this year (or exists only next year, such as
        # 2월 29일).  Never move it silently: ask about next year.
        return DateResult(DateStatus.PAST_WITHOUT_YEAR, proposal=next_year)

    if _OTHER_WEEK_DAY.search(combined) or _RECURRING.search(combined):
        return DateResult(DateStatus.UNSUPPORTED)

    for word, offset in _RELATIVE_DAYS:
        if word in combined:
            return DateResult(DateStatus.RESOLVED, (today + timedelta(days=offset),))

    this_week = _THIS_WEEK_DAY.search(combined)
    if this_week:
        index = _WEEKDAYS.index(this_week.group(1))
        monday = today - timedelta(days=today.weekday())
        return DateResult(DateStatus.RESOLVED, (monday + timedelta(days=index),))

    bare = _BARE_WEEKDAY.search(combined)
    if bare:
        index = _WEEKDAYS.index(bare.group(1))
        ahead = (index - today.weekday()) % 7
        if ahead == 0:
            return DateResult(DateStatus.WEEKDAY_IS_TODAY)
        return DateResult(DateStatus.RESOLVED, (today + timedelta(days=ahead),))

    if _DATE_LIKE.search(combined):
        return DateResult(DateStatus.UNSUPPORTED)
    return DateResult(DateStatus.NOT_A_DATE)


_MONTH_ONLY = re.compile(r"(?:(\d{4})년\s*)?(\d{1,2})월(?!\s*\d{1,2}일)")


def resolve_month_range(
    texts: Sequence[str],
    reference: datetime,
) -> tuple[datetime, datetime, bool] | None:
    """Return ``(start, end, absolute)`` for a month-only query such as "10월"."""

    match = _MONTH_ONLY.search(_joined(texts))
    if not match:
        return None
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    absolute = match.group(1) is not None
    year = int(match.group(1)) if absolute else _local_today(reference).year
    start = datetime(year, month, 1, tzinfo=SEOUL)
    end = datetime(year + (month == 12), 1 if month == 12 else month + 1, 1, tzinfo=SEOUL)
    return start, end, absolute


# ------------------------------------------------------------------- times


class TimeStatus(StrEnum):
    RESOLVED = "resolved"
    NEEDS_AMPM = "needs_ampm"
    # "저녁 12시", "밤 12시", "새벽 12시": midnight is read differently by people.
    MIDNIGHT_UNCLEAR = "midnight_unclear"
    # A period word that does not fit the hour, such as "밤 2시" or "낮 9시".
    PERIOD_UNCLEAR = "period_unclear"
    # Only a period such as "오후" without an hour.
    NEEDS_CLOCK = "needs_clock"
    INVALID_TIME = "invalid_time"
    # A real time expression this version does not resolve ("15:30", "정오").
    UNSUPPORTED = "unsupported"
    NOT_A_TIME = "not_a_time"


@dataclass(frozen=True)
class TimeResult:
    status: TimeStatus
    hour: int | None = None
    minute: int | None = None

    @property
    def resolved(self) -> bool:
        return self.status is TimeStatus.RESOLVED

    @property
    def clock(self) -> time | None:
        if self.hour is None or self.minute is None:
            return None
        return time(self.hour, self.minute)


_PERIOD = re.compile(r"오전|오후|아침|저녁|새벽|낮|밤")
_CLOCK = re.compile(r"(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분|\s*(반))?")
_TIME_LIKE = re.compile(
    r"\d{1,2}\s*:\s*\d{2}|정오|자정|점심|한\s*시|두\s*시|세\s*시|네\s*시|다섯\s*시|"
    r"여섯\s*시|일곱\s*시|여덟\s*시|아홉\s*시|열\s*시|열한\s*시|열두\s*시|\d+\s*시간\s*(?:뒤|후)"
)


def _apply_period(period: str, hour: int) -> int | TimeStatus:
    if period in {"오전", "아침"}:
        return 0 if hour == 12 else hour
    if period == "오후":
        return hour if hour == 12 else hour + 12
    if period == "저녁":
        if hour == 12:
            return TimeStatus.MIDNIGHT_UNCLEAR
        return hour + 12
    if period == "밤":
        if hour == 12:
            return TimeStatus.MIDNIGHT_UNCLEAR
        if 6 <= hour <= 11:
            return hour + 12
        return TimeStatus.PERIOD_UNCLEAR  # "밤 2시" may mean 02:00
    if period == "낮":
        if hour == 12:
            return 12
        if 1 <= hour <= 6:
            return hour + 12
        return TimeStatus.PERIOD_UNCLEAR
    if period == "새벽":
        if hour == 12:
            return TimeStatus.MIDNIGHT_UNCLEAR
        if 1 <= hour <= 6:
            return hour
        return TimeStatus.PERIOD_UNCLEAR
    raise ValueError(f"unknown period: {period}")


def resolve_time(texts: Sequence[str]) -> TimeResult:
    combined = _joined(texts)
    match = _CLOCK.search(combined)
    if not match:
        if _TIME_LIKE.search(combined):
            return TimeResult(TimeStatus.UNSUPPORTED)
        if _PERIOD.search(combined):
            return TimeResult(TimeStatus.NEEDS_CLOCK)
        return TimeResult(TimeStatus.NOT_A_TIME)
    hour = int(match.group(1))
    minute = 30 if match.group(3) else int(match.group(2) or 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return TimeResult(TimeStatus.INVALID_TIME)
    period_match = _PERIOD.search(combined)
    if period_match is None:
        if 1 <= hour <= 12:
            return TimeResult(TimeStatus.NEEDS_AMPM)
        return TimeResult(TimeStatus.RESOLVED, hour, minute)
    if hour > 12:
        # "오후 15시" is a typo-like mix; keep the 24-hour value only if it agrees.
        if period_match.group(0) in {"오후", "저녁", "밤", "낮"}:
            return TimeResult(TimeStatus.RESOLVED, hour, minute)
        return TimeResult(TimeStatus.PERIOD_UNCLEAR)
    applied = _apply_period(period_match.group(0), hour)
    if isinstance(applied, TimeStatus):
        return TimeResult(applied)
    return TimeResult(TimeStatus.RESOLVED, applied, minute)


# --------------------------------------------------------- category, state

_CATEGORY_CUES = (
    ("INBOUND", re.compile(r"입고")),
    ("INVENTORY", re.compile(r"재고")),
    ("OUTBOUND", re.compile(r"출고|피킹|포장")),
    ("RETURN", re.compile(r"반품")),
)


def resolve_category(texts: Sequence[str]) -> str | None:
    """Category from explicit category evidence or a title, else ``None``.

    A cue that starts a longer word still counts ("출고팀" is OUTBOUND), which
    matches the labelled training and evaluation data.
    """

    combined = _joined(texts)
    for category, pattern in _CATEGORY_CUES:
        if pattern.search(combined):
            return category
    return None


_NOT_COMPLETED = re.compile(
    r"미완료|완료\s*(?:안|못)\s*(?:된|한|했)|완료(?:되|하)지\s*(?:않|못)|"
    r"안\s*끝|끝나지\s*않|못\s*끝|완료.*(?:취소|해제)"
)
_COMPLETED = re.compile(r"완료|끝났|끝난")


def resolve_completed(texts: Sequence[str]) -> bool | None:
    combined = _joined(texts)
    if _NOT_COMPLETED.search(combined):
        return False
    if _COMPLETED.search(combined):
        return True
    return None


_FUTURE_SCOPE = re.compile(r"앞으로|향후|예정(?:된|인)?|(?:지금|오늘)\s*이후")


def is_future_scope(texts: Sequence[str]) -> bool:
    return bool(texts) and all(_FUTURE_SCOPE.search(normalize_text(text)) for text in texts)


# Words that never say what the schedule is (Issue #35, user decision 2026-10-29).
# Removal order matters: "일정" goes before "그일" so "그 일정" leaves only "그".
_UNCLEAR_WORDS = (
    "저번에", "스케줄", "일정", "그거", "그것", "이거", "이것", "저거", "저것", "그건",
    "할일", "말한", "그때", "아까", "하나", "그일", "뭐", "좀",
)
# Pointing words and particles that may be left over ("그 일정" leaves "그").
_LEFTOVER = set("그이저을를이가은는도만요의")


def is_unclear_title(value: str) -> bool:
    """True when nothing in the title says what the schedule is."""

    remaining = "".join(unicodedata.normalize("NFKC", value).split())
    remaining = re.sub(r"[^0-9A-Za-z가-힣]", "", remaining)
    for word in _UNCLEAR_WORDS:
        remaining = remaining.replace(word, "")
    return all(character in _LEFTOVER for character in remaining)


def _title_key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).split()).casefold()


def title_contains(query: str, title: str) -> bool:
    """True when ``query`` appears in ``title``, ignoring spaces and case."""

    key = _title_key(query)
    return bool(key) and key in _title_key(title)


def combine(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=SEOUL)
