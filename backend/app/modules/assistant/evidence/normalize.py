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
_RELATIVE_DAYS = (
    ("내일모레", 2),
    ("글피", 3),
    ("모레", 2),
    ("내일", 1),
    ("오늘", 0),
)


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
_OTHER_WEEK_DAY = re.compile(r"(다다음|다음|지난|저번)\s*주\s*([월화수목금토일])요일")
_WEEK_SHIFT = {"다음": 1, "다다음": 2, "지난": -1, "저번": -1, "이번": 0}
_MONTH_SHIFT_DAY = re.compile(r"(이번|다다음|다음|지난|저번)\s*달\s*(\d{1,2})\s*일")
_DAYS_LATER = re.compile(r"(?<!\d)(\d{1,3})\s*일\s*(?:뒤|후)")
DATE_MODIFIER_PATTERN = (
    r"(?:내\s*년|내\s*후년|후년|작년|다음\s*해|이듬해)|"
    r"(?:\d{1,3}|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|몇)"
    r"\s*(?:주(?:일)?|개월|달|년)\s*(?:뒤|후|전)|"
    r"(?:일\s*주일|보름|이틀|사흘|나흘|닷새|열흘)\s*(?:뒤|후|전)|"
    r"\d{1,3}\s*일\s*전"
)
_UNSUPPORTED_DATE_MODIFIER = re.compile(DATE_MODIFIER_PATTERN)
# A day of the month alone ("15일에").  Not part of "N일 뒤", "N일간" or "M월 N일".
_DAY_ONLY = re.compile(r"(?<![\d월])(?<!월\s)(\d{1,2})\s*일(?!\s*(?:뒤|후|간|동안))")
# Recurring wording is not a single date.
_RECURRING = re.compile(r"매주|매일|매달|매월|격주|주마다|날마다")
_BARE_WEEKDAY = re.compile(r"(?<![가-힣])([월화수목금토일])요일")
# Date-like wording that this version does not resolve.  It makes the result
# UNSUPPORTED (ask the user) instead of NOT_A_DATE (a model role error).
_DATE_LIKE = re.compile(
    r"\d+\s*(?:월|일)|요일|주말|다음\s*(?:주|달)|이번\s*(?:주|달)|지난\s*(?:주|달)|"
    r"저번\s*(?:주|달)|월말|월초|연말|연초|\d+\s*/\s*\d+|(?:며칠|\d+\s*일)\s*(?:뒤|후)|"
    r"이틀\s*(?:뒤|후)|일\s*주일\s*(?:뒤|후)|한\s*달\s*(?:뒤|후)|"
    r"오늘|내일|모레|글피|어제|그제|그저께"
)
_DATE_REMAINDER_PUNCTUATION = re.compile(r"[\s,，.。!?！？'\"“”‘’()\[\]{}]+")
_DATE_ALLOWED_REMAINDER = re.compile(
    r"(?:에는|으로|부터|까지|에|은|는|이|가|을|를|로)?"
)


def _date_match_is_complete(value: str, match: re.Match[str]) -> bool:
    """Return whether one supported date expression consumed its evidence.

    Evidence may include a case particle such as ``에`` or ``부터``. Any
    other remaining word means that a supported suffix matched only part of
    an unknown expression, so the caller must ask instead of guessing.
    """

    remainder = value[: match.start()] + value[match.end() :]
    remainder = _DATE_REMAINDER_PUNCTUATION.sub("", remainder)
    return _DATE_ALLOWED_REMAINDER.fullmatch(remainder) is not None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _shift_month(day: date, months: int) -> tuple[int, int]:
    index = day.year * 12 + day.month - 1 + months
    return index // 12, index % 12 + 1


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def resolve_date(
    texts: Sequence[str],
    reference: datetime,
    *,
    purpose: DatePurpose,
) -> DateResult:
    combined = _joined(texts)
    today = _local_today(reference)

    # Do not let a supported suffix win while an unsupported modifier is
    # silently discarded (for example, reading "2주 뒤 금요일" as merely
    # this week's Friday or "내년 12월 5일" as this year's date).
    if _UNSUPPORTED_DATE_MODIFIER.search(combined):
        return DateResult(DateStatus.UNSUPPORTED)

    full = _FULL_DATE.search(combined)
    if full:
        if not _date_match_is_complete(combined, full):
            return DateResult(DateStatus.UNSUPPORTED)
        value = _safe_date(*map(int, full.groups()))
        if value is None:
            return DateResult(DateStatus.INVALID_DATE)
        return DateResult(DateStatus.RESOLVED, (value,), absolute=True)

    month_day = _MONTH_DAY.search(combined)
    if month_day:
        if not _date_match_is_complete(combined, month_day):
            return DateResult(DateStatus.UNSUPPORTED)
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

    if _RECURRING.search(combined):
        return DateResult(DateStatus.UNSUPPORTED)

    # Weeks run Monday to Sunday: "다음 주 화요일" is the Tuesday of next week.
    other_week = _OTHER_WEEK_DAY.search(combined)
    if other_week:
        if not _date_match_is_complete(combined, other_week):
            return DateResult(DateStatus.UNSUPPORTED)
        shift = _WEEK_SHIFT[other_week.group(1)]
        index = _WEEKDAYS.index(other_week.group(2))
        return DateResult(DateStatus.RESOLVED, (_monday(today) + timedelta(days=7 * shift + index),))

    month_shift = _MONTH_SHIFT_DAY.search(combined)
    if month_shift:
        if not _date_match_is_complete(combined, month_shift):
            return DateResult(DateStatus.UNSUPPORTED)
        year, month = _shift_month(today, _WEEK_SHIFT[month_shift.group(1)])
        value = _safe_date(year, month, int(month_shift.group(2)))
        if value is None:
            return DateResult(DateStatus.INVALID_DATE)
        return DateResult(DateStatus.RESOLVED, (value,))

    later = _DAYS_LATER.search(combined)
    if later:
        if not _date_match_is_complete(combined, later):
            return DateResult(DateStatus.UNSUPPORTED)
        return DateResult(DateStatus.RESOLVED, (today + timedelta(days=int(later.group(1))),))

    for word, offset in _RELATIVE_DAYS:
        match = re.search(re.escape(word), combined)
        if match:
            if not _date_match_is_complete(combined, match):
                return DateResult(DateStatus.UNSUPPORTED)
            return DateResult(DateStatus.RESOLVED, (today + timedelta(days=offset),))

    this_week = _THIS_WEEK_DAY.search(combined)
    if this_week:
        if not _date_match_is_complete(combined, this_week):
            return DateResult(DateStatus.UNSUPPORTED)
        index = _WEEKDAYS.index(this_week.group(1))
        monday = today - timedelta(days=today.weekday())
        return DateResult(DateStatus.RESOLVED, (monday + timedelta(days=index),))

    bare = _BARE_WEEKDAY.search(combined)
    if bare:
        if not _date_match_is_complete(combined, bare):
            return DateResult(DateStatus.UNSUPPORTED)
        index = _WEEKDAYS.index(bare.group(1))
        ahead = (index - today.weekday()) % 7
        if ahead == 0:
            return DateResult(DateStatus.WEEKDAY_IS_TODAY)
        return DateResult(DateStatus.RESOLVED, (today + timedelta(days=ahead),))

    day_only = _DAY_ONLY.search(combined)
    if day_only:
        if not _date_match_is_complete(combined, day_only):
            return DateResult(DateStatus.UNSUPPORTED)
        return _day_of_month(int(day_only.group(1)), today, purpose)

    if _DATE_LIKE.search(combined):
        return DateResult(DateStatus.UNSUPPORTED)
    return DateResult(DateStatus.NOT_A_DATE)


def _day_of_month(day_value: int, today: date, purpose: DatePurpose) -> DateResult:
    """ "15일" means the coming 15th (user decision, 2026-11-01).

    A search also looks at the 15th of next month, the way a date without a
    year looks at this year and next year.
    """

    if not 1 <= day_value <= 31:
        return DateResult(DateStatus.INVALID_DATE)
    # A day-only expression refers to the current month first. If the date does
    # not exist, do not silently move it to a later month where it does.
    if _safe_date(today.year, today.month, day_value) is None:
        return DateResult(DateStatus.INVALID_DATE)
    found: list[date] = []
    for months in range(0, 2):
        year, month = _shift_month(today, months)
        value = _safe_date(year, month, day_value)
        if value is not None and value >= today:
            found.append(value)
    if purpose is DatePurpose.SEARCH:
        this_month = _safe_date(today.year, today.month, day_value)
        searched = [this_month] if this_month is not None and this_month < today else []
        candidates = searched + found[:1]
        if candidates:
            return DateResult(DateStatus.RESOLVED, tuple(candidates))
        return DateResult(DateStatus.INVALID_DATE)
    if not found:
        return DateResult(DateStatus.INVALID_DATE)
    return DateResult(DateStatus.RESOLVED, (found[0],))


_PARTIAL_DAY = re.compile(r"(\d{1,2})\s*일")
_PARTIAL_WEEKDAY = re.compile(r"([월화수목금토일])요일")


_HAS_MONTH = re.compile(r"\d{1,2}\s*월|(?:이번|다다음|다음|지난|저번)\s*달")
_HAS_WEEK = re.compile(r"(?:이번|다다음|다음|지난|저번)\s*주")


class Inherit(StrEnum):
    NOT_PARTIAL = "not_partial"
    INHERITED = "inherited"
    # Inherited from a past date without a year: ask about next year first.
    INHERITED_NEXT_YEAR = "inherited_next_year"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InheritResult:
    status: Inherit
    day: date | None = None
    absolute: bool = False


_YEARLESS_MONTH = re.compile(r"(?<!년\s)(?<!년)\d{1,2}\s*월")


def inherit_partial_date(adopted: str, replaced: Sequence[str], reference: datetime) -> InheritResult:
    """A correction that leaves out the month or week keeps the replaced one.

    "12월 3일 말고 5일" is 12월 5일, and "다음 주 화요일 말고 수요일" is next
    week's Wednesday.  Only a replaced value that names a month or week is
    inherited ("내일 말고 5일" is still the coming 5th).  When the replaced
    value is not a readable date, the result is UNKNOWN so the caller asks
    again instead of guessing (review 2026-11-01).
    """

    text = normalize_text(adopted)
    day_match = _PARTIAL_DAY.fullmatch(text)
    weekday_match = _PARTIAL_WEEKDAY.fullmatch(text)
    if not day_match and not weekday_match or not replaced:
        return InheritResult(Inherit.NOT_PARTIAL)
    last = normalize_text(replaced[-1])
    previous = resolve_date([last], reference, purpose=DatePurpose.SCHEDULE)
    if previous.status in {DateStatus.NOT_A_DATE, DateStatus.UNSUPPORTED, DateStatus.INVALID_DATE}:
        return InheritResult(Inherit.UNKNOWN)
    marker = _HAS_MONTH if day_match else _HAS_WEEK
    if not marker.search(last):
        # "12월 3일 말고 5일, 아니 6일" or "다음 주 화요일 말고 수요일, 아니 목요일":
        # the month or week is two values back; ask.
        if any(marker.search(normalize_text(value)) for value in replaced[:-1]):
            return InheritResult(Inherit.UNKNOWN)
        # "12월 3일 말고 금요일": a month alone does not say which week.
        if weekday_match and _HAS_MONTH.search(last):
            return InheritResult(Inherit.UNKNOWN)
        return InheritResult(Inherit.NOT_PARTIAL)
    status = Inherit.INHERITED
    if previous.status is DateStatus.RESOLVED:
        base = previous.dates[0]
    elif previous.status is DateStatus.PAST_WITHOUT_YEAR and previous.proposal is not None:
        base = previous.proposal
    else:
        return InheritResult(Inherit.UNKNOWN)
    if day_match:
        day_value = int(day_match.group(1))
        today = _local_today(reference)
        if not previous.absolute and _YEARLESS_MONTH.search(last):
            # Same rule as a date without a year: this year if still ahead, else ask about next year.
            this_year = _safe_date(today.year, base.month, day_value)
            if this_year is not None and this_year >= today:
                return InheritResult(Inherit.INHERITED, this_year, False)
            next_year = _safe_date(today.year + 1, base.month, day_value)
            if next_year is None:
                return InheritResult(Inherit.UNKNOWN)
            return InheritResult(Inherit.INHERITED_NEXT_YEAR, next_year, False)
        value = _safe_date(base.year, base.month, day_value)
        if value is None:
            return InheritResult(Inherit.UNKNOWN)
        return InheritResult(status, value, previous.absolute)
    index = _WEEKDAYS.index(weekday_match.group(1))
    return InheritResult(status, _monday(base) + timedelta(days=index), previous.absolute)


_MONTH_ONLY = re.compile(r"(?:(\d{4})년\s*)?(\d{1,2})월(?!\s*\d{1,2}일)")


_MONTH_WORD = re.compile(r"(이번|다다음|다음|지난|저번)\s*달(?!\s*\d)")
_WEEK_WORD = re.compile(r"(이번|다다음|다음|지난|저번)\s*주(?!\s*[월화수목금토일]요일)(?!말)")


def resolve_week_range(texts: Sequence[str], reference: datetime) -> tuple[datetime, datetime] | None:
    """Monday to Sunday for "이번 주", "다음 주" or "지난주" (a list period)."""

    match = _WEEK_WORD.search(_joined(texts))
    if not match:
        return None
    monday = _monday(_local_today(reference)) + timedelta(days=7 * _WEEK_SHIFT[match.group(1)])
    start = datetime.combine(monday, time(0, 0), tzinfo=SEOUL)
    return start, start + timedelta(days=7)


def resolve_month_range(
    texts: Sequence[str],
    reference: datetime,
) -> tuple[datetime, datetime, bool] | None:
    """Return ``(start, end, absolute)`` for a month-only query such as "10월"
    or "다음 달"."""

    joined = _joined(texts)
    word = _MONTH_WORD.search(joined)
    if word:
        year, month = _shift_month(_local_today(reference), _WEEK_SHIFT[word.group(1)])
        start = datetime(year, month, 1, tzinfo=SEOUL)
        end_year, end_month = _shift_month(start.date(), 1)
        return start, datetime(end_year, end_month, 1, tzinfo=SEOUL), False
    match = _MONTH_ONLY.search(joined)
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
    # For a midnight confirmation, whether the proposed 00:xx belongs to the
    # requested date (0) or the following date (1).
    day_offset: int | None = None

    @property
    def resolved(self) -> bool:
        return self.status is TimeStatus.RESOLVED

    @property
    def clock(self) -> time | None:
        if self.hour is None or self.minute is None:
            return None
        return time(self.hour, self.minute)


_PERIOD = re.compile(r"오전|오후|아침|저녁|새벽|점심|낮|밤")
_CLOCK = re.compile(r"(\d{1,2})\s*시(?!간)(?:\s*(\d{1,2})\s*분|\s*(반))?")
_COLON_CLOCK = re.compile(r"(?<!\d)(\d{1,2})\s*:\s*(\d{2})(?!\d)")
# "세 시 반" is "3시 반".  Not inside a word ("부두 시설") and not "한 시간".
_KOREAN_HOUR = re.compile(r"(?<![가-힣])(열\s*한|열\s*두|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*시(?!간)")
# Minutes left after the clock that this version cannot read ("삼십 분", "3시 30").
_UNREAD_MINUTES = re.compile(r"\s*(?:\d|[일이삼사오육칠팔구십]+\s*분)")
_KOREAN_HOURS = {
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8,
    "아홉": 9, "열": 10, "열한": 11, "열두": 12,
}
# Relative hours need the clock; this version asks instead.
_TIME_LIKE = re.compile(r"(?:\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*시간\s*(?:뒤|후)")
# 24-hour Korean words ("열다섯 시", "열 세 시", "스물 한 시") are not read yet.
# Checked before the hour words become digits, so "열 세 시" is never read as 3시.
_KOREAN_24_HOUR = re.compile(
    r"(?<![가-힣])(?:열\s*(?:셋|세|넷|네|다섯|여섯|일곱|여덟|아홉)|스무|스물\s*(?:한|두|세|네)?)\s*시(?!간)"
)
_MIDNIGHT_DAY_OFFSET = {"아침": 0, "새벽": 0, "저녁": 1, "밤": 1}


def _apply_period(period: str, hour: int) -> int | TimeStatus:
    if period == "오전":
        return 0 if hour == 12 else hour
    if period == "아침":
        return TimeStatus.MIDNIGHT_UNCLEAR if hour == 12 else hour
    if period == "오후":
        return hour if hour == 12 else hour + 12
    if period == "저녁":
        if hour == 12:
            return TimeStatus.MIDNIGHT_UNCLEAR
        if 1 <= hour <= 5:
            return TimeStatus.PERIOD_UNCLEAR
        return hour + 12
    if period == "밤":
        if hour == 12:
            return TimeStatus.MIDNIGHT_UNCLEAR
        if 6 <= hour <= 11:
            return hour + 12
        return TimeStatus.PERIOD_UNCLEAR  # "밤 2시" may mean 02:00
    if period == "점심":
        # "점심 12시" is noon and "점심 1시" is 13:00; other hours are not lunch.
        if hour in {11, 12}:
            return hour
        if hour in {1, 2}:
            return hour + 12
        return TimeStatus.PERIOD_UNCLEAR
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


def _korean_hour_digits(match: re.Match[str]) -> str:
    word = "".join(match.group(1).split())  # "열 한" is "열한"
    return f"{_KOREAN_HOURS[word]}시"


def resolve_time(texts: Sequence[str]) -> TimeResult:
    joined = _joined(texts)
    if _KOREAN_24_HOUR.search(joined):
        return TimeResult(TimeStatus.UNSUPPORTED)
    combined = _KOREAN_HOUR.sub(_korean_hour_digits, joined)
    combined = _COLON_CLOCK.sub(lambda m: f"{int(m.group(1))}시 {int(m.group(2))}분", combined)
    if "정오" in combined and not _CLOCK.search(combined):
        return TimeResult(TimeStatus.RESOLVED, 12, 0)
    if "자정" in combined and not _CLOCK.search(combined):
        # Same question as "밤 12시": people read midnight differently.
        return TimeResult(TimeStatus.MIDNIGHT_UNCLEAR, 0, 0, 1)
    match = _CLOCK.search(combined)
    if not match:
        if _TIME_LIKE.search(combined):
            return TimeResult(TimeStatus.UNSUPPORTED)
        if _PERIOD.search(combined):
            return TimeResult(TimeStatus.NEEDS_CLOCK)
        return TimeResult(TimeStatus.NOT_A_TIME)
    if _UNREAD_MINUTES.match(combined, match.end()):
        # Never drop minutes: "두 시 삼십 분" is not 2:00.
        return TimeResult(TimeStatus.UNSUPPORTED)
    hour = int(match.group(1))
    minute = 30 if match.group(3) else int(match.group(2) or 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        return TimeResult(TimeStatus.INVALID_TIME)
    period_match = _PERIOD.search(combined)
    if period_match is None:
        if 1 <= hour <= 12:
            # Keep the parsed clock components so target matching can retain
            # both possible schedules without choosing morning or afternoon.
            return TimeResult(TimeStatus.NEEDS_AMPM, hour, minute)
        return TimeResult(TimeStatus.RESOLVED, hour, minute)
    if hour > 12:
        # "오후 15시" is a typo-like mix; keep the 24-hour value only if it agrees.
        if period_match.group(0) in {"오후", "저녁", "밤", "낮"}:
            return TimeResult(TimeStatus.RESOLVED, hour, minute)
        return TimeResult(TimeStatus.PERIOD_UNCLEAR)
    period = period_match.group(0)
    if hour == 12 and period in _MIDNIGHT_DAY_OFFSET:
        return TimeResult(
            TimeStatus.MIDNIGHT_UNCLEAR,
            0,
            minute,
            _MIDNIGHT_DAY_OFFSET[period],
        )
    applied = _apply_period(period, hour)
    if isinstance(applied, TimeStatus):
        return TimeResult(applied)
    return TimeResult(TimeStatus.RESOLVED, applied, minute)


# ------------------------------------------------- shared date/time checks

# Pieces of date and time wording, used to tell "only a date or time" apart
# from a title that mentions one ("다음 주 회의 준비").
_DATE_TIME_PARTS = re.compile(
    r"\d{4}\s*년|\d{1,2}\s*월|\d{1,2}\s*일|오늘|내일|모레|글피|"
    r"(?:이번|다다음|다음|지난|저번)\s*(?:주|달)|[월화수목금토일]요일|"
    r"오전|오후|아침|저녁|새벽|점심|낮|밤|정오|자정|\d{1,2}\s*:\s*\d{2}|\d{1,2}\s*시|\d{1,2}\s*분|"
    r"(?:열\s*한|열\s*두|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*시|반|뒤|후|에|으로|로|쯤|요"
)


def mentions_date_or_time(text: str) -> bool:
    """True when the text holds any date or time wording this module knows.

    gate.py and provider.py call this instead of keeping their own word lists,
    so a new expression here is known everywhere (review 2026-11-01).
    """

    return (
        resolve_date([text], datetime(2000, 1, 3, tzinfo=SEOUL), purpose=DatePurpose.SEARCH).status
        is not DateStatus.NOT_A_DATE
        or resolve_time([text]).status is not TimeStatus.NOT_A_TIME
    )


def is_date_or_time_only(text: str) -> bool:
    """True for "정오" or "다음 주 화요일", false for "다음 주 회의 준비"."""

    value = normalize_text(text)
    return mentions_date_or_time(value) and not _DATE_TIME_PARTS.sub("", value).strip(" ,.")


# --------------------------------------------------------- category, state

_CATEGORY_CUES = (
    ("INBOUND", re.compile(r"입고")),
    ("INVENTORY", re.compile(r"재고")),
    ("OUTBOUND", re.compile(r"출고|피킹|포장")),
    ("RETURN", re.compile(r"반품")),
    ("OTHER", re.compile(r"기타")),
)


def category_candidates(texts: Sequence[str]) -> frozenset[str]:
    """Return every explicit category meaning found in the evidence."""

    combined = _joined(texts)
    return frozenset(
        category for category, pattern in _CATEGORY_CUES if pattern.search(combined)
    )


def resolve_category(texts: Sequence[str]) -> str | None:
    """Category from explicit category evidence or a title, else ``None``.

    A cue that starts a longer word still counts ("출고팀" is OUTBOUND), which
    matches the labelled training and evaluation data.
    """

    candidates = category_candidates(texts)
    return next(iter(candidates)) if len(candidates) == 1 else None


_NOT_COMPLETED = re.compile(
    r"미완료|"
    r"완료\s*(?:된|되|처리|표시)?\s*(?:게|거|건|것)?\s*"
    r"(?:가|는|은|이|를|로)?\s*(?:아직\s*)?"
    r"(?:(?:안|못)(?=\s|되|돼|됐|됨|된|한|했|함|$)|아니|아닌|아님)|"
    r"완료(?:\s*(?:처리|표시))?\s*(?:가|는|를|로)?\s*(?:되|하)지\s*(?:않|못)|"
    r"완료\s*전|안\s*끝|끝나지\s*않|못\s*끝|완료.*(?:취소|해제)"
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


_CATEGORY_WORDS = re.compile(r"입고|재고|출고|피킹|포장|반품")
_DISTINCTIVE_TITLE_WORDS = re.compile(r"\d+|[a-z]+|오전|오후")


def _category_words(key: str) -> set[str]:
    return set(_CATEGORY_WORDS.findall(key))


# Shorter titles differ by one letter too easily ("회의" and "회식").
SIMILAR_TITLE_MIN_CHARS = 5


def title_similar(query: str, title: str) -> bool:
    """True when the titles differ by at most one letter (a speech recognition slip
    such as "상하차" heard as "상하자"), ignoring spaces and case."""

    a, b = _title_key(query), _title_key(title)
    if min(len(a), len(b)) < SIMILAR_TITLE_MIN_CHARS or abs(len(a) - len(b)) > 1:
        return False
    # Numbers, Latin words and AM/PM words identify the schedule rather than a
    # likely Korean speech-recognition typo. Never offer a different dock,
    # version or time period as a one-letter fallback candidate.
    if _DISTINCTIVE_TITLE_WORDS.findall(a) != _DISTINCTIVE_TITLE_WORDS.findall(b):
        return False
    # "입고 검품" and "출고 검품" differ by one letter but are different work.
    if _category_words(a) != _category_words(b):
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    short, long = (a, b) if len(a) < len(b) else (b, a)
    return any(long[:index] + long[index + 1 :] == short for index in range(len(long)))


def combine(day: date, clock: time) -> datetime:
    return datetime.combine(day, clock, tzinfo=SEOUL)
