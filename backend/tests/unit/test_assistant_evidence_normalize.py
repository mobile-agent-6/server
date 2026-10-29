from __future__ import annotations

from datetime import date, datetime

import pytest

from app.modules.assistant.evidence.normalize import (
    SEOUL,
    DatePurpose,
    DateStatus,
    TimeStatus,
    is_future_scope,
    resolve_category,
    resolve_completed,
    resolve_date,
    resolve_month_range,
    resolve_time,
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


@pytest.mark.parametrize("text", ["다음 주 금요일", "지난주 월요일", "매주 금요일", "다음 달", "3일 뒤", "주말", "3/5"])
def test_unsupported_date_expressions_ask_instead_of_guessing(text: str) -> None:
    assert schedule_date(text).status is DateStatus.UNSUPPORTED


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


@pytest.mark.parametrize("text", ["15:30", "정오", "자정", "점심", "세 시"])
def test_unsupported_time_expressions(text: str) -> None:
    assert resolve_time([text]).status is TimeStatus.UNSUPPORTED


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
