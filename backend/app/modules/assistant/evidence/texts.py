"""User-facing questions and notices for the evidence planner.

All wording lives here so it can be reviewed with the frontend in one place.
These are drafts; the shown and spoken wording is not part of model scoring.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

ASK_TITLE = "어떤 일정인가요? 일정 이름을 알려 주세요."
ASK_DATE = "며칠로 할까요?"
ASK_TIME = "몇 시로 할까요?"
ASK_AMPM = "오전인가요, 오후인가요?"
ASK_DATE_AGAIN = "날짜를 '11월 3일'이나 '내일'처럼 다시 말씀해 주세요."
ASK_TIME_AGAIN = "시간을 '오후 3시'처럼 다시 말씀해 주세요."
ASK_ABSOLUTE_DATE = "서버 시각을 확인할 수 없어요. 연도와 월, 일을 함께 알려 주세요."
ASK_CHANGE_VALUE = "무엇으로 바꿀까요?"
ASK_TARGET_TITLE = "어떤 일정인지 이름을 알려 주세요."
ASK_NARROW = "해당하는 일정이 너무 많아요. 날짜를 함께 알려 주세요."
ASK_LIST_SCOPE = "언제 일정을 볼까요?"
SELECT_TARGET = "어떤 일정인가요? 하나를 골라 주세요."
CONSENT_QUESTION = "알림과 문자 기능은 지원하지 않아요. 알림 없이 일정만 진행할까요?"
CONSENT_DECLINED = "요청을 취소했어요."
GUIDANCE = "그 요청은 도와드릴 수 없어요. 일정 등록, 조회, 변경, 삭제를 말씀해 주세요."
LIST_RESULT = "요청한 일정이에요."
MORE_REQUESTS_NOTE = " 나머지 요청은 이어서 말씀해 주세요."
FIRST_DECLINED = "요청을 취소했어요. 필요한 일정은 하나씩 말씀해 주세요."
ASK_DATETIME_AGAIN = "날짜와 시간을 다시 말씀해 주세요."
ASK_RESTATE = "원하시는 내용을 다시 말씀해 주세요."

_WEEKDAY_NAMES = "월화수목금토일"


def confirm_next_year(day: date) -> str:
    return f"{day.month}월 {day.day}일은 이미 지났어요. {day.year}년 {day.month}월 {day.day}일 말씀이세요?"


def confirm_today(day: date) -> str:
    return f"오늘({_WEEKDAY_NAMES[day.weekday()]}요일) 말씀이세요?"


def confirm_midnight(next_day: date) -> str:
    return f"{next_day.month}월 {next_day.day}일 0시(자정) 말씀이세요?"


def confirm_first_create(title: str) -> str:
    return f"여러 일정을 말씀하셨어요. '{title}' 일정부터 등록할까요? 나머지 요청은 이어서 말씀해 주세요."


def _spoken_time(value: datetime) -> str:
    local = value.astimezone(timezone(timedelta(hours=9)))
    period = "오전" if local.hour < 12 else "오후"
    hour = local.hour % 12 or 12
    minute = f" {local.minute}분" if local.minute else ""
    return f"{local.month}월 {local.day}일 {period} {hour}시{minute}"


def confirm_create_values(title: str, scheduled_at: datetime) -> str:
    return f"{_spoken_time(scheduled_at)}에 '{title}' 일정을 등록할까요?"


def confirm_new_create(title: str) -> str:
    return f"'{title}' 일정을 새로 등록할까요? 기존 일정을 바꾸시려면 '아니'라고 말씀해 주세요."


def confirm_update(title: str) -> str:
    return f"'{title}' 일정을 바꿀까요?"


def confirm_delete(title: str) -> str:
    return f"'{title}' 일정을 삭제할까요?"
