from __future__ import annotations

from datetime import date, datetime

import pytest

from app.modules.assistant.evidence.normalize import (
    SEOUL,
    DatePurpose,
    DateStatus,
    TimeResult,
    TimeStatus,
    is_future_scope,
    resolve_category,
    resolve_completed,
    resolve_date,
    resolve_month_range,
    resolve_time,
    resolve_week_range,
)

# Thursday 2026-10-29 10:00 in Seoul.
REFERENCE = datetime(2026, 10, 29, 10, 0, tzinfo=SEOUL)


def schedule_date(text: str):
    return resolve_date([text], REFERENCE, purpose=DatePurpose.SCHEDULE)


def search_date(text: str):
    return resolve_date([text], REFERENCE, purpose=DatePurpose.SEARCH)


# ---------------------------------------------------------------- dates


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("오늘", date(2026, 10, 29)),
        ("내일", date(2026, 10, 30)),
        ("모레", date(2026, 10, 31)),
        ("글피", date(2026, 11, 1)),
        ("내일모레", date(2026, 10, 31)),
        ("11월 3일", date(2026, 11, 3)),
        ("10월 29일", date(2026, 10, 29)),
        ("이번 주 월요일", date(2026, 10, 26)),
        ("이번 주 금요일", date(2026, 10, 30)),
    ],
)
def test_schedule_dates_resolve(text: str, expected: date) -> None:
    result = schedule_date(text)
    assert result.status is DateStatus.RESOLVED
    assert result.dates == (expected,)
    assert result.absolute is False


def test_full_date_is_absolute() -> None:
    result = schedule_date("2027년 3월 5일")
    assert result.status is DateStatus.RESOLVED
    assert result.dates == (date(2027, 3, 5),)
    assert result.absolute is True


def test_past_date_without_year_asks_about_next_year_when_scheduling() -> None:
    result = schedule_date("3월 5일")
    assert result.status is DateStatus.PAST_WITHOUT_YEAR
    assert result.dates == ()
    assert result.proposal == date(2027, 3, 5)


def test_search_date_without_year_checks_this_year_and_next_year() -> None:
    assert search_date("3월 5일").dates == (date(2026, 3, 5), date(2027, 3, 5))
    assert search_date("11월 3일").dates == (date(2026, 11, 3), date(2027, 11, 3))


def test_search_date_with_year_checks_only_that_date() -> None:
    assert search_date("2026년 3월 5일").dates == (date(2026, 3, 5),)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("금요일", date(2026, 10, 30)),
        ("토요일", date(2026, 10, 31)),
        ("월요일", date(2026, 11, 2)),
        ("수요일", date(2026, 11, 4)),
    ],
)
def test_weekday_only_means_the_coming_weekday(text: str, expected: date) -> None:
    result = schedule_date(text)
    assert result.status is DateStatus.RESOLVED
    assert result.dates == (expected,)


def test_weekday_that_is_today_asks() -> None:
    assert schedule_date("목요일").status is DateStatus.WEEKDAY_IS_TODAY


@pytest.mark.parametrize(
    "text",
    [
        "매주 금요일",
        "다음 달",
        "주말",
        "이번 주말",
        "3/5",
        "15일간",
        "월말",
        "이틀 뒤",
        "일주일 후",
        "한 달 뒤",
    ],
)
def test_unsupported_date_expressions_ask_instead_of_guessing(text: str) -> None:
    assert schedule_date(text).status is DateStatus.UNSUPPORTED


# Weeks run Monday to Sunday; 2026-10-29 is a Thursday.
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("다음 주 금요일", date(2026, 11, 6)),
        ("다음주 월요일", date(2026, 11, 2)),
        ("다다음 주 화요일", date(2026, 11, 10)),
        ("지난주 월요일", date(2026, 10, 19)),
        ("저번 주 수요일", date(2026, 10, 21)),
        ("이번 달 30일", date(2026, 10, 30)),
        ("다음 달 3일", date(2026, 11, 3)),
        ("지난달 3일", date(2026, 9, 3)),
        ("3일 뒤", date(2026, 11, 1)),
        ("10일 후", date(2026, 11, 8)),
    ],
)
def test_week_month_and_day_count_expressions(text: str, expected: date) -> None:
    result = schedule_date(text)
    assert (result.status, result.dates, result.absolute) == (DateStatus.RESOLVED, (expected,), False)


def test_day_of_next_month_that_does_not_exist() -> None:
    assert schedule_date("다음 달 31일").status is DateStatus.INVALID_DATE


@pytest.mark.parametrize(
    ("text", "expected"),
    [("15일", date(2026, 11, 15)), ("29일", date(2026, 10, 29)), ("31일", date(2026, 10, 31)), ("1일", date(2026, 11, 1))],
)
def test_day_only_means_the_coming_day(text: str, expected: date) -> None:
    result = schedule_date(text)
    assert (result.status, result.dates) == (DateStatus.RESOLVED, (expected,))


def test_day_only_search_looks_back_this_month_and_ahead() -> None:
    assert search_date("15일").dates == (date(2026, 10, 15), date(2026, 11, 15))
    assert search_date("30일").dates == (date(2026, 10, 30),)


@pytest.mark.parametrize(
    ("text", "start"),
    [("이번 주", date(2026, 10, 26)), ("다음 주", date(2026, 11, 2)), ("지난주", date(2026, 10, 19))],
)
def test_week_range_runs_monday_to_sunday(text: str, start: date) -> None:
    begin, end = resolve_week_range([text], REFERENCE)
    assert begin.date() == start and (end - begin).days == 7


@pytest.mark.parametrize("text", ["다음 주 화요일", "이번 주말", "주말"])
def test_week_range_ignores_a_weekday_or_weekend(text: str) -> None:
    assert resolve_week_range([text], REFERENCE) is None


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [("이번 달", date(2026, 10, 1), date(2026, 11, 1)), ("다음 달", date(2026, 11, 1), date(2026, 12, 1))],
)
def test_month_words_cover_the_whole_month(text: str, start: date, end: date) -> None:
    begin, finish, absolute = resolve_month_range([text], REFERENCE)
    assert (begin.date(), finish.date(), absolute) == (start, end, False)


def test_next_month_in_december_rolls_into_january() -> None:
    december = datetime(2026, 12, 10, 9, 0, tzinfo=SEOUL)
    begin, finish, _ = resolve_month_range(["다음 달"], december)
    assert (begin.date(), finish.date()) == (date(2027, 1, 1), date(2027, 2, 1))


@pytest.mark.parametrize("text", ["2월 30일", "13월 1일", "2026년 2월 29일"])
def test_impossible_dates(text: str) -> None:
    assert schedule_date(text).status is DateStatus.INVALID_DATE


@pytest.mark.parametrize("text", ["회의", "입고 검수", "잡아 줘"])
def test_non_date_words(text: str) -> None:
    assert schedule_date(text).status is DateStatus.NOT_A_DATE


def test_month_range() -> None:
    start, end, absolute = resolve_month_range(["10월"], REFERENCE)
    assert (start, end, absolute) == (
        datetime(2026, 10, 1, tzinfo=SEOUL),
        datetime(2026, 11, 1, tzinfo=SEOUL),
        False,
    )
    start, end, absolute = resolve_month_range(["2027년 12월"], REFERENCE)
    assert (start, end, absolute) == (
        datetime(2027, 12, 1, tzinfo=SEOUL),
        datetime(2028, 1, 1, tzinfo=SEOUL),
        True,
    )
    assert resolve_month_range(["10월 3일"], REFERENCE) is None
    assert resolve_month_range(["13월"], REFERENCE) is None


# ---------------------------------------------------------------- times


@pytest.mark.parametrize(
    ("texts", "hour", "minute"),
    [
        (["오후 3시"], 15, 0),
        (["오후 3시 반"], 15, 30),
        (["오전 9시 20분"], 9, 20),
        (["오전 12시"], 0, 0),
        (["오후 12시"], 12, 0),
        (["아침 7시"], 7, 0),
        (["저녁 7시"], 19, 0),
        (["15시"], 15, 0),
        (["0시"], 0, 0),
        (["3시", "오후"], 15, 0),
        (["밤 9시"], 21, 0),
        (["밤 11시 반"], 23, 30),
        (["낮 2시"], 14, 0),
        (["낮 12시"], 12, 0),
        (["새벽 5시"], 5, 0),
    ],
)
def test_times_resolve(texts: list[str], hour: int, minute: int) -> None:
    result = resolve_time(texts)
    assert result.status is TimeStatus.RESOLVED
    assert (result.hour, result.minute) == (hour, minute)


@pytest.mark.parametrize("text", ["저녁 12시", "밤 12시", "새벽 12시"])
def test_evening_and_night_twelve_ask(text: str) -> None:
    # Bug fix: "저녁 12시" used to become 12:00 noon.
    assert resolve_time([text]).status is TimeStatus.MIDNIGHT_UNCLEAR


@pytest.mark.parametrize("text", ["밤 2시", "낮 9시", "새벽 9시"])
def test_period_that_does_not_fit_the_hour_asks(text: str) -> None:
    assert resolve_time([text]).status is TimeStatus.PERIOD_UNCLEAR


@pytest.mark.parametrize("text", ["3시", "3시 반", "12시"])
def test_hour_without_period_asks_am_or_pm(text: str) -> None:
    assert resolve_time([text]).status is TimeStatus.NEEDS_AMPM


def test_period_without_hour_asks_for_time() -> None:
    assert resolve_time(["오후"]).status is TimeStatus.NEEDS_CLOCK


@pytest.mark.parametrize("text", ["2시간 뒤", "한 시간 후"])
def test_unsupported_time_expressions(text: str) -> None:
    assert resolve_time([text]).status is TimeStatus.UNSUPPORTED


@pytest.mark.parametrize(
    ("text", "hour", "minute"),
    [
        ("15:30", 15, 30),
        ("오후 3:30", 15, 30),
        ("정오", 12, 0),
        ("점심 12시", 12, 0),
        ("점심 1시", 13, 0),
        ("점심 11시 반", 11, 30),
        ("오후 세 시 반", 15, 30),
        ("오전 열한 시 20분", 11, 20),
        ("저녁 일곱 시", 19, 0),
    ],
)
def test_more_time_expressions(text: str, hour: int, minute: int) -> None:
    assert resolve_time([text]) == TimeResult(TimeStatus.RESOLVED, hour, minute)


@pytest.mark.parametrize("text", ["3:30", "세 시", "열두 시"])
def test_colon_and_korean_hours_without_period_ask_am_or_pm(text: str) -> None:
    assert resolve_time([text]).status is TimeStatus.NEEDS_AMPM


def test_midnight_word_asks_like_night_twelve() -> None:
    assert resolve_time(["자정"]).status is TimeStatus.MIDNIGHT_UNCLEAR


@pytest.mark.parametrize(("text", "status"), [("점심", TimeStatus.NEEDS_CLOCK), ("점심 5시", TimeStatus.PERIOD_UNCLEAR)])
def test_lunch_needs_a_lunch_hour(text: str, status: TimeStatus) -> None:
    assert resolve_time([text]).status is status


@pytest.mark.parametrize("text", ["부두 시설", "한 시간"])
def test_korean_hour_words_inside_other_words_are_not_times(text: str) -> None:
    assert resolve_time([text]).status is TimeStatus.NOT_A_TIME


@pytest.mark.parametrize("text", ["25시", "3시 70분"])
def test_impossible_times(text: str) -> None:
    assert resolve_time([text]).status is TimeStatus.INVALID_TIME


def test_24_hour_value_with_period() -> None:
    result = resolve_time(["오후 15시"])
    assert (result.status, result.hour) == (TimeStatus.RESOLVED, 15)
    assert resolve_time(["오전 15시"]).status is TimeStatus.PERIOD_UNCLEAR


def test_non_time_words() -> None:
    assert resolve_time(["회의"]).status is TimeStatus.NOT_A_TIME


# ------------------------------------------------------- category, state


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("입고", "INBOUND"),
        ("분류가 입고인", "INBOUND"),
        ("재고 실사", "INVENTORY"),
        ("출고", "OUTBOUND"),
        ("피킹 점검", "OUTBOUND"),
        ("포장", "OUTBOUND"),
        ("반품 상자 검수", "RETURN"),
        ("출고팀 회의", "OUTBOUND"),
        ("출하 예정 점검", None),
        ("협력사 납품 회의", None),
        ("운영 회의", None),
    ],
)
def test_category(text: str, expected: str | None) -> None:
    assert resolve_category([text]) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("완료", True),
        ("완료된", True),
        ("완료로 표시해 줘", True),
        ("끝난", True),
        ("끝났어", True),
        ("미완료", False),
        ("아직 안 끝난", False),
        ("안 끝났어", False),
        ("완료 안 된", False),
        ("완료 안 한", False),
        ("완료되지 않은", False),
        ("완료하지 못한", False),
        ("끝나지 않은", False),
        ("못 끝낸", False),
        ("완료 표시 취소", False),
        ("회의", None),
    ],
)
def test_completed(text: str, expected: bool | None) -> None:
    # Bug fix: "완료 안 된" used to mean completed.
    assert resolve_completed([text]) is expected


def test_future_scope() -> None:
    assert is_future_scope(["앞으로 남은"]) is True
    assert is_future_scope(["예정된"]) is True
    assert is_future_scope(["오늘 이후"]) is True
    assert is_future_scope(["지난"]) is False
    assert is_future_scope([]) is False


@pytest.mark.parametrize(
    ("title", "unclear"),
    [
        ("그거 하나", True),
        ("일정 좀", True),
        ("뭐 하나", True),
        ("이것", True),
        ("저번에 말한 그 건", True),
        ("그 일정", True),
        ("할 일", True),
        ("", True),
        ("입고 검수", False),
        ("반품 창고 정리", False),
        ("일정 조율 회의", False),
        ("하나로 마트 납품", False),
        ("이 회의", False),
    ],
)
def test_unclear_title(title: str, unclear: bool) -> None:
    from app.modules.assistant.evidence.normalize import is_unclear_title

    assert is_unclear_title(title) is unclear


@pytest.mark.parametrize(("text", "hour"), [("오후 열 한 시", 23), ("오전 열 두 시", 0), ("오후 열 두 시", 12)])
def test_spaced_korean_eleven_and_twelve(text: str, hour: int) -> None:
    assert resolve_time([text]) == TimeResult(TimeStatus.RESOLVED, hour, 0)


@pytest.mark.parametrize("text", ["오후 두 시 삼십 분", "3시 30", "3시 십오 분", "오후 3시 5"])
def test_minutes_that_cannot_be_read_are_asked_not_dropped(text: str) -> None:
    assert resolve_time([text]).status is TimeStatus.UNSUPPORTED


@pytest.mark.parametrize(
    ("adopted", "replaced", "status", "day", "absolute"),
    [
        ("5일", "12월 3일", "inherited", date(2026, 12, 5), False),
        ("5일", "2026년 12월 3일", "inherited", date(2026, 12, 5), True),
        ("5일", "다음 달 3일", "inherited", date(2026, 11, 5), False),
        ("수요일", "다음 주 화요일", "inherited", date(2026, 11, 4), False),
        # A past date without a year: the caller asks about next year.
        ("5일", "2월 3일", "inherited_next_year", date(2027, 2, 5), False),
        # No month or week to keep: read the new value on its own.
        ("5일", "내일", "not_partial", None, False),
        ("수요일", "오늘", "not_partial", None, False),
        ("모레", "내일", "not_partial", None, False),
        ("31일", "11월 3일", "unknown", None, False),
        ("5일", "그날", "unknown", None, False),
        # A month alone does not say which week.
        ("금요일", "12월 3일", "unknown", None, False),
    ],
)
def test_partial_correction_inherits_month_or_week(adopted, replaced, status, day, absolute) -> None:
    from app.modules.assistant.evidence.normalize import inherit_partial_date

    result = inherit_partial_date(adopted, [replaced], REFERENCE)
    assert (result.status.value, result.day, result.absolute) == (status, day, absolute)


def test_korean_24_hour_words_are_asked_again() -> None:
    assert resolve_time(["열다섯 시"]).status is TimeStatus.UNSUPPORTED
    assert resolve_time(["스무 시"]).status is TimeStatus.UNSUPPORTED


def test_second_correction_without_a_month_asks_again() -> None:
    from app.modules.assistant.evidence.normalize import Inherit, inherit_partial_date

    # "12월 3일 말고 5일, 아니 6일": the month is two values back.
    assert inherit_partial_date("6일", ["12월 3일", "5일"], REFERENCE).status is Inherit.UNKNOWN


@pytest.mark.parametrize(
    ("adopted", "replaced", "status", "day"),
    [
        ("25일", "11월 3일", "inherited", date(2026, 11, 25)),  # still ahead this year
        ("1일", "11월 3일", "inherited_next_year", date(2027, 11, 1)),  # already passed
    ],
)
def test_inherited_day_uses_this_year_when_still_ahead(adopted, replaced, status, day) -> None:
    from app.modules.assistant.evidence.normalize import inherit_partial_date

    november_20 = datetime(2026, 11, 20, 10, 0, tzinfo=SEOUL)
    result = inherit_partial_date(adopted, [replaced], november_20)
    assert (result.status.value, result.day) == (status, day)


@pytest.mark.parametrize("text", ["열 세 시", "오후 열 세 시", "스물 한 시", "열 다섯 시"])
def test_spaced_korean_24_hour_words_are_asked_not_misread(text: str) -> None:
    assert resolve_time([text]).status is TimeStatus.UNSUPPORTED


def test_second_weekday_correction_without_a_week_asks_again() -> None:
    from app.modules.assistant.evidence.normalize import Inherit, inherit_partial_date

    result = inherit_partial_date("목요일", ["다음 주 화요일", "수요일"], REFERENCE)
    assert result.status is Inherit.UNKNOWN
