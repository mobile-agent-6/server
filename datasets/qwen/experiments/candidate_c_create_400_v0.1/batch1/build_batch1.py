"""Build Candidate-C create batch 1 (100 new training candidates) deterministically.

Status: PROPOSED_UNREVIEWED / BE-PROPOSED.  This writes only proposed,
unreviewed rows.  It does not open a Test split, reuse the existing 400,
call a model, or touch backend state.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
PILOT_DIR = HERE.parent / "pilot"
sys.path.insert(0, str(PILOT_DIR))

from build_pilot import (  # noqa: E402
    SPECS as PILOT_SPECS,
    canonical,
    evidence,
    expected,
    make_candidate_target,
    make_case,
    sha256,
    spec,
    turn,
)
from spec_checks import REFERENCE_PRESETS, batch_quality_errors, check_specs  # noqa: E402

OUTPUT = HERE / "conversations-v0.1.jsonl"
TARGETS = HERE / "candidate-c-targets-v0.1.jsonl"
MAX_PER_FAMILY = 5

WM = REFERENCE_PRESETS["weekday_morning"]   # 2028-02-10 Thu 08:20
WE = REFERENCE_PRESETS["weekday_evening"]   # 2028-03-15 Wed 19:40
EM = REFERENCE_PRESETS["early_morning"]     # 2028-04-04 Tue 06:10
WK = REFERENCE_PRESETS["weekend"]           # 2028-05-13 Sat 11:05
ME = REFERENCE_PRESETS["month_end"]         # 2028-06-30 Fri 16:30
LD = REFERENCE_PRESETS["leap_day"]          # 2028-02-29 Tue 09:00
YE = REFERENCE_PRESETS["year_end"]          # 2028-12-31 Sun 20:00

CATEGORY_CUES = (("입고", "INBOUND"), ("재고", "INVENTORY"), ("출고", "OUTBOUND"),
                 ("피킹", "OUTBOUND"), ("포장", "OUTBOUND"), ("반품", "RETURN"))

OK = [expected("outcome", write=1, outcome="success")]


def _ev(value: Any) -> dict[str, Any] | None:
    if value is None or isinstance(value, dict):
        return value
    return evidence([value])


def _category(cue: Any) -> str:
    if cue is None:
        return "OTHER"
    text = cue[1] if isinstance(cue, tuple) else cue["adoptedSpans"][-1]["text"]
    return dict(CATEGORY_CUES)[text]


def S(number: int, bucket: str, family: str, texts: list[str], ref: str, *,
      title: Any, date: Any, time: Any, cat: Any, at: str | None,
      expectations: list[dict[str, Any]] = OK, **kwargs: Any) -> dict[str, Any]:
    turns = [turn(text) if index == 0 else turn(text, answer=index) for index, text in enumerate(texts)]
    return spec(
        number, bucket, family, turns, reference=ref,
        title=_ev(title), date=_ev(date), time=_ev(time), category_evidence=_ev(cat),
        scheduled_at=at, category=_category(cat), expectations=expectations,
        batch="b1", dataset_role="training_candidate", authored_on="2026-10-27", **kwargs,
    )


def miss(*items: tuple[str, int, int | None]) -> list[dict[str, Any]]:
    rows = []
    for item, detected, resolved in items:
        row: dict[str, Any] = {"item": item, "detectedAtTurn": detected}
        if resolved is not None:
            row["resolvedAtTurn"] = resolved
        rows.append(row)
    return rows


def ask(purpose: str, **kwargs: Any) -> dict[str, Any]:
    return expected("need_more_info", purpose=purpose, **kwargs)


ASK_INFO = ask("missing_information")
CONSENT_Q = ask("consent_without_unsupported")
GRANTED_OK = expected("outcome", write=1, model_call="no_model_call", outcome="success")
DECLINED = expected("outcome", model_call="no_model_call", outcome="cancelled")
PAST = [expected("failure", failure="SCHEDULE_REJECTED")]
D, M, C, T, X = (
    "direct_create", "missing_and_followup", "correction", "time_safety", "mixed_unsupported_consent",
)
AFTER = "excluded_out_of_order_marker_after_adopted"
CROSS = "excluded_cross_turn_without_marker"

SPECS = [
    # ---- Direct create: 22 ----
    S(1, D, "direct_absolute", ["2028년 3월 20일 오전 10시에 입고 라벨 확인 등록해줘"], WE,
      title=(1, "입고 라벨 확인"), date=(1, "2028년 3월 20일"), time=(1, "오전 10시"), cat=(1, "입고"),
      at="2028-03-20T10:00:00+09:00"),
    S(2, D, "direct_absolute", ["2028년 4월 7일 오후 1시에 지게차 정기 점검 넣어줘"], EM,
      title=(1, "지게차 정기 점검"), date=(1, "2028년 4월 7일"), time=(1, "오후 1시"), cat=None,
      at="2028-04-07T13:00:00+09:00"),
    S(3, D, "direct_absolute", ["2028년 5월 16일 오전 11시에 반품 사유 정리 일정 잡아줘"], WK,
      title=(1, "반품 사유 정리"), date=(1, "2028년 5월 16일"), time=(1, "오전 11시"), cat=(1, "반품"),
      at="2028-05-16T11:00:00+09:00"),
    S(4, D, "direct_absolute", ["2028년 7월 3일 오후 5시에 출고 송장 대조 등록해줘"], ME,
      title=(1, "출고 송장 대조"), date=(1, "2028년 7월 3일"), time=(1, "오후 5시"), cat=(1, "출고"),
      at="2028-07-03T17:00:00+09:00"),
    S(5, D, "direct_relative", ["내일 오전 8시에 재고 위치 정리 등록해줘"], WE,
      title=(1, "재고 위치 정리"), date=(1, "내일"), time=(1, "오전 8시"), cat=(1, "재고"),
      at="2028-03-16T08:00:00+09:00"),
    S(6, D, "direct_relative", ["모레 오후 3시에 소방 설비 점검 잡아줘"], EM,
      title=(1, "소방 설비 점검"), date=(1, "모레"), time=(1, "오후 3시"), cat=None,
      at="2028-04-06T15:00:00+09:00"),
    S(7, D, "direct_relative", ["글피 오전 9시에 피킹 동선 점검 넣어줘"], WK,
      title=(1, "피킹 동선 점검"), date=(1, "글피"), time=(1, "오전 9시"), cat=(1, "피킹"),
      at="2028-05-16T09:00:00+09:00"),
    S(8, D, "direct_relative", ["내일 오후 4시에 입고 팔레트 정리 등록해줘"], LD,
      title=(1, "입고 팔레트 정리"), date=(1, "내일"), time=(1, "오후 4시"), cat=(1, "입고"),
      at="2028-03-01T16:00:00+09:00"),
    S(9, D, "direct_relative", ["오늘 오후 6시에 주간 실적 보고 일정 잡아줘"], ME,
      title=(1, "주간 실적 보고"), date=(1, "오늘"), time=(1, "오후 6시"), cat=None,
      at="2028-06-30T18:00:00+09:00"),
    S(10, D, "direct_weekday", ["이번 주 금요일 오후 2시에 반품 창고 정돈 등록해줘"], WM,
      title=(1, "반품 창고 정돈"), date=(1, "이번 주 금요일"), time=(1, "오후 2시"), cat=(1, "반품"),
      at="2028-02-11T14:00:00+09:00"),
    S(11, D, "direct_weekday", ["이번 주 금요일 오전 10시에 신입 직원 면담 넣어줘"], WE,
      title=(1, "신입 직원 면담"), date=(1, "이번 주 금요일"), time=(1, "오전 10시"), cat=None,
      at="2028-03-17T10:00:00+09:00"),
    S(12, D, "direct_weekday", ["이번 주 목요일 오후 2시 반에 재고 이동 계획 회의 등록해줘"], EM,
      title=(1, "재고 이동 계획 회의"), date=(1, "이번 주 목요일"), time=(1, "오후 2시 반"), cat=(1, "재고"),
      at="2028-04-06T14:30:00+09:00"),
    S(13, D, "direct_yearless_future", ["3월 3일 오전 9시에 출고 일정 점검 등록해줘"], WM,
      title=(1, "출고 일정 점검"), date=(1, "3월 3일"), time=(1, "오전 9시"), cat=(1, "출고"),
      at="2028-03-03T09:00:00+09:00"),
    S(14, D, "direct_yearless_future", ["5월 25일 오후 2시에 거래처 계약 검토 잡아줘"], WK,
      title=(1, "거래처 계약 검토"), date=(1, "5월 25일"), time=(1, "오후 2시"), cat=None,
      at="2028-05-25T14:00:00+09:00"),
    S(15, D, "direct_yearless_future", ["7월 12일 오전 10시 반에 입고 업체 미팅 등록해줘"], ME,
      title=(1, "입고 업체 미팅"), date=(1, "7월 12일"), time=(1, "오전 10시 반"), cat=(1, "입고"),
      at="2028-07-12T10:30:00+09:00"),
    S(16, D, "direct_noon_midnight", ["내일 오후 12시에 물류 동선 개선 회의 등록해줘"], WE,
      title=(1, "물류 동선 개선 회의"), date=(1, "내일"), time=(1, "오후 12시"), cat=None,
      at="2028-03-16T12:00:00+09:00"),
    S(17, D, "direct_noon_midnight", ["2028년 4월 8일 오전 12시에 전산 시스템 교체 작업 넣어줘"], EM,
      title=(1, "전산 시스템 교체 작업"), date=(1, "2028년 4월 8일"), time=(1, "오전 12시"), cat=None,
      at="2028-04-08T00:00:00+09:00"),
    S(18, D, "direct_minutes", ["2028년 5월 15일 오후 3시 15분에 포장 작업 점검 등록해줘"], WK,
      title=(1, "포장 작업 점검"), date=(1, "2028년 5월 15일"), time=(1, "오후 3시 15분"), cat=(1, "포장"),
      at="2028-05-15T15:15:00+09:00"),
    S(19, D, "direct_minutes", ["모레 오전 9시 45분에 냉동 창고 온도 점검 잡아줘"], LD,
      title=(1, "냉동 창고 온도 점검"), date=(1, "모레"), time=(1, "오전 9시 45분"), cat=None,
      at="2028-03-02T09:45:00+09:00"),
    S(20, D, "direct_minutes", ["2029년 1월 3일 오후 2시 20분에 재고 감사 준비 등록해줘"], YE,
      title=(1, "재고 감사 준비"), date=(1, "2029년 1월 3일"), time=(1, "오후 2시 20분"), cat=(1, "재고"),
      at="2029-01-03T14:20:00+09:00"),
    S(21, D, "direct_24h", ["2028년 2월 13일 15시에 반품 검품 일정 넣어줘"], WM,
      title=(1, "반품 검품"), date=(1, "2028년 2월 13일"), time=(1, "15시"), cat=(1, "반품"),
      at="2028-02-13T15:00:00+09:00"),
    S(22, D, "direct_24h", ["내일 21시 30분에 야간 출고 준비 등록해줘"], ME,
      title=(1, "야간 출고 준비"), date=(1, "내일"), time=(1, "21시 30분"), cat=(1, "출고"),
      at="2028-07-01T21:30:00+09:00"),

    # ---- Missing information and follow-up: 25 ----
    S(23, M, "missing_time", ["모레 입고 수량 대조 등록해줘", "오후 2시로 잡아줘"], WE,
      title=(1, "입고 수량 대조"), date=(1, "모레"), time=(2, "오후 2시"), cat=(1, "입고"),
      at="2028-03-17T14:00:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(24, M, "missing_time", ["2028년 4월 10일 협력사 납품 회의 넣어줘", "오전 11시 반이요"], EM,
      title=(1, "협력사 납품 회의"), date=(1, "2028년 4월 10일"), time=(2, "오전 11시 반"), cat=None,
      at="2028-04-10T11:30:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(25, M, "missing_time", ["내일 반품 라벨 교체 등록해줘", "16시에 해줘"], WK,
      title=(1, "반품 라벨 교체"), date=(1, "내일"), time=(2, "16시"), cat=(1, "반품"),
      at="2028-05-14T16:00:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(26, M, "missing_time", ["글피 재고 회전율 검토 잡아줘", "오후 1시 10분"], LD,
      title=(1, "재고 회전율 검토"), date=(1, "글피"), time=(2, "오후 1시 10분"), cat=(1, "재고"),
      at="2028-03-03T13:10:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(27, M, "missing_date", ["오후 4시에 출고 라인 점검 넣어줘", "2028년 2월 15일이야"], WM,
      title=(1, "출고 라인 점검"), date=(2, "2028년 2월 15일"), time=(1, "오후 4시"), cat=(1, "출고"),
      at="2028-02-15T16:00:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(28, M, "missing_date", ["오전 10시에 보안 카메라 점검 등록해줘", "날짜는 내일이야"], ME,
      title=(1, "보안 카메라 점검"), date=(2, "내일"), time=(1, "오전 10시"), cat=None,
      at="2028-07-01T10:00:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(29, M, "missing_date", ["오후 3시 30분에 입고 검품 교육 잡아줘", "4월 12일"], EM,
      title=(1, "입고 검품 교육"), date=(2, "4월 12일"), time=(1, "오후 3시 30분"), cat=(1, "입고"),
      at="2028-04-12T15:30:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(30, M, "missing_date", ["오전 9시에 반품 처리 실적 정리 등록해줘", "모레로 부탁해"], YE,
      title=(1, "반품 처리 실적 정리"), date=(2, "모레"), time=(1, "오전 9시"), cat=(1, "반품"),
      at="2029-01-02T09:00:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(31, M, "missing_date_time", ["입고 예정표 공유 일정 등록해줘", "내일 오후 3시"], WE,
      title=(1, "입고 예정표 공유"), date=(2, "내일"), time=(2, "오후 3시"), cat=(1, "입고"),
      at="2028-03-16T15:00:00+09:00", missing=miss(("date", 1, 2), ("time", 1, 2)),
      expectations=[ASK_INFO, *OK]),
    S(32, M, "missing_date_time", ["거래처 단가 협의 일정 하나 넣어줘", "2028년 5월 19일 오전 10시로"], WK,
      title=(1, "거래처 단가 협의"), date=(2, "2028년 5월 19일"), time=(2, "오전 10시"), cat=None,
      at="2028-05-19T10:00:00+09:00", missing=miss(("date", 1, 2), ("time", 1, 2)),
      expectations=[ASK_INFO, *OK]),
    S(33, M, "missing_date_time", ["재고 실물 대조 잡아줘", "모레 오후 2시 반"], LD,
      title=(1, "재고 실물 대조"), date=(2, "모레"), time=(2, "오후 2시 반"), cat=(1, "재고"),
      at="2028-03-02T14:30:00+09:00", missing=miss(("date", 1, 2), ("time", 1, 2)),
      expectations=[ASK_INFO, *OK]),
    S(34, M, "missing_date_time", ["포장 라인 정비 등록해줘", "7월 5일 13시"], ME,
      title=(1, "포장 라인 정비"), date=(2, "7월 5일"), time=(2, "13시"), cat=(1, "포장"),
      at="2028-07-05T13:00:00+09:00", missing=miss(("date", 1, 2), ("time", 1, 2)),
      expectations=[ASK_INFO, *OK]),
    S(35, M, "missing_date_then_time", ["출고 우선순위 회의 넣어줘", "2028년 3월 17일로 해줘", "오전 9시 반"], WE,
      title=(1, "출고 우선순위 회의"), date=(2, "2028년 3월 17일"), time=(3, "오전 9시 반"), cat=(1, "출고"),
      at="2028-03-17T09:30:00+09:00", missing=miss(("date", 1, 2), ("time", 1, 3)),
      expectations=[ASK_INFO, ASK_INFO, *OK]),
    S(36, M, "missing_date_then_time", ["창고 조명 수리 일정 등록해줘", "내일", "오후 4시로"], EM,
      title=(1, "창고 조명 수리"), date=(2, "내일"), time=(3, "오후 4시"), cat=None,
      at="2028-04-05T16:00:00+09:00", missing=miss(("date", 1, 2), ("time", 1, 3)),
      expectations=[ASK_INFO, ASK_INFO, *OK]),
    S(37, M, "missing_date_then_time", ["반품 고객 응대 교육 잡아줘", "모레야", "오전 10시 20분"], WK,
      title=(1, "반품 고객 응대 교육"), date=(2, "모레"), time=(3, "오전 10시 20분"), cat=(1, "반품"),
      at="2028-05-15T10:20:00+09:00", missing=miss(("date", 1, 2), ("time", 1, 3)),
      expectations=[ASK_INFO, ASK_INFO, *OK]),
    S(38, M, "missing_ampm", ["내일 4시에 입고 차량 배차 확인 등록해줘", "오후 4시예요"], WM,
      title=(1, "입고 차량 배차 확인"), date=(1, "내일"), time=(2, "오후 4시"), cat=(1, "입고"),
      at="2028-02-11T16:00:00+09:00", missing=miss(("ampm", 1, 2)),
      target_overrides={1: {"time": evidence([(1, "4시")])}}, expectations=[ask("ampm"), *OK]),
    S(39, M, "missing_ampm", ["2028년 3월 18일 10시에 재고 라벨 재부착 넣어줘", "아침 10시야"], WE,
      title=(1, "재고 라벨 재부착"), date=(1, "2028년 3월 18일"), time=(2, "아침 10시"), cat=(1, "재고"),
      at="2028-03-18T10:00:00+09:00", missing=miss(("ampm", 1, 2)),
      target_overrides={1: {"time": evidence([(1, "10시")])}}, expectations=[ask("ampm"), *OK]),
    S(40, M, "missing_ampm", ["모레 2시 반에 설비 업체 미팅 잡아줘", "오후 2시 반으로"], EM,
      title=(1, "설비 업체 미팅"), date=(1, "모레"), time=(2, "오후 2시 반"), cat=None,
      at="2028-04-06T14:30:00+09:00", missing=miss(("ampm", 1, 2)),
      target_overrides={1: {"time": evidence([(1, "2시 반")])}}, expectations=[ask("ampm"), *OK]),
    S(41, M, "missing_ampm", ["7월 4일 8시에 출고 상차 점검 등록해줘", "저녁 8시"], ME,
      title=(1, "출고 상차 점검"), date=(1, "7월 4일"), time=(2, "저녁 8시"), cat=(1, "출고"),
      at="2028-07-04T20:00:00+09:00", missing=miss(("ampm", 1, 2)),
      target_overrides={1: {"time": evidence([(1, "8시")])}}, expectations=[ask("ampm"), *OK]),
    S(42, M, "missing_title", ["내일 오후 3시에 일정 하나 넣어줘", "택배사 단가 협의야"], WK,
      title=(2, "택배사 단가 협의"), date=(1, "내일"), time=(1, "오후 3시"), cat=None,
      at="2028-05-14T15:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      expectations=[ask("task_or_title_unclear"), *OK]),
    S(43, M, "missing_title", ["2028년 3월 6일 오전 10시로 하나 잡아줘", "반품 기준 개정 회의"], LD,
      title=(2, "반품 기준 개정 회의"), date=(1, "2028년 3월 6일"), time=(1, "오전 10시"), cat=(2, "반품"),
      at="2028-03-06T10:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      expectations=[ask("task_or_title_unclear"), *OK]),
    S(44, M, "missing_title", ["모레 오후 1시에 그것 좀 등록해줘", "재고 마감 보고야"], YE,
      title=(2, "재고 마감 보고"), date=(1, "모레"), time=(1, "오후 1시"), cat=(2, "재고"),
      at="2029-01-02T13:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      expectations=[ask("task_or_title_unclear"), *OK]),
    S(45, M, "untrusted_followup", ["내일 오후 2시에 입고 지연 대응 회의 등록해줘", "2028년 3월 16일 오후 2시"], WE,
      trusted=False, title=(1, "입고 지연 대응 회의"), date=(2, "2028년 3월 16일"), time=(2, "오후 2시"),
      cat=(1, "입고"), at="2028-03-16T14:00:00+09:00",
      missing=miss(("absolute_datetime_due_to_untrusted_time", 1, 2)),
      target_overrides={1: {"date": evidence([(1, "내일")]), "time": evidence([(1, "오후 2시")])}},
      expectations=[ask("absolute_datetime_required"), *OK]),
    S(46, M, "untrusted_followup", ["오늘 오후 5시에 본사 화상 회의 넣어줘", "2028년 4월 4일 17시야"], EM,
      trusted=False, title=(1, "본사 화상 회의"), date=(2, "2028년 4월 4일"), time=(2, "17시"),
      cat=None, at="2028-04-04T17:00:00+09:00",
      missing=miss(("absolute_datetime_due_to_untrusted_time", 1, 2)),
      target_overrides={1: {"date": evidence([(1, "오늘")]), "time": evidence([(1, "오후 5시")])}},
      expectations=[ask("absolute_datetime_required"), *OK]),
    S(47, M, "untrusted_followup", ["7월 10일 오전 9시에 출고 증빙 정리 잡아줘", "2028년 7월 10일 오전 9시로"], ME,
      trusted=False, title=(1, "출고 증빙 정리"), date=(2, "2028년 7월 10일"), time=(2, "오전 9시"),
      cat=(1, "출고"), at="2028-07-10T09:00:00+09:00",
      missing=miss(("absolute_datetime_due_to_untrusted_time", 1, 2)),
      target_overrides={1: {"date": evidence([(1, "7월 10일")]), "time": evidence([(1, "오전 9시")])}},
      expectations=[ask("absolute_datetime_required"), *OK]),

    # ---- Corrections: 20 ----
    S(48, C, "correction_date", ["2028년 2월 16일 말고 2028년 2월 17일 오후 2시에 입고 교육 등록해줘"], WM,
      title=(1, "입고 교육"), time=(1, "오후 2시"), cat=(1, "입고"),
      date=evidence([(1, "2028년 2월 17일")], superseded=[(1, "2028년 2월 16일")], markers=[(1, "말고")]),
      at="2028-02-17T14:00:00+09:00"),
    S(49, C, "correction_date", ["내일 말고 모레 오전 10시에 재고 이관 회의 넣어줘"], WE,
      title=(1, "재고 이관 회의"), time=(1, "오전 10시"), cat=(1, "재고"),
      date=evidence([(1, "모레")], superseded=[(1, "내일")], markers=[(1, "말고")]),
      at="2028-03-17T10:00:00+09:00"),
    S(50, C, "correction_date", ["5월 20일 아니고 5월 22일 오후 3시에 소모품 구매 회의 잡아줘"], WK,
      title=(1, "소모품 구매 회의"), time=(1, "오후 3시"), cat=None,
      date=evidence([(1, "5월 22일")], superseded=[(1, "5월 20일")], markers=[(1, "아니고")]),
      at="2028-05-22T15:00:00+09:00"),
    S(51, C, "correction_date", ["모레 대신 글피 오후 4시에 반품 회수 차량 확인 등록해줘"], ME,
      title=(1, "반품 회수 차량 확인"), time=(1, "오후 4시"), cat=(1, "반품"),
      date=evidence([(1, "글피")], superseded=[(1, "모레")], markers=[(1, "대신")]),
      at="2028-07-03T16:00:00+09:00"),
    S(52, C, "correction_time", ["2028년 4월 11일 오전 10시 말고 오후 2시에 출고 계획 회의 등록해줘"], EM,
      title=(1, "출고 계획 회의"), date=(1, "2028년 4월 11일"), cat=(1, "출고"),
      time=evidence([(1, "오후 2시")], superseded=[(1, "오전 10시")], markers=[(1, "말고")]),
      at="2028-04-11T14:00:00+09:00"),
    S(53, C, "correction_time", ["내일 오후 1시 아니고 오후 3시 반에 협력업체 정산 회의 넣어줘"], LD,
      title=(1, "협력업체 정산 회의"), date=(1, "내일"), cat=None,
      time=evidence([(1, "오후 3시 반")], superseded=[(1, "오후 1시")], markers=[(1, "아니고")]),
      at="2028-03-01T15:30:00+09:00"),
    S(54, C, "correction_time", ["모레 오전 9시 말고 오전 11시에 재고 정산 보고 등록해줘"], YE,
      title=(1, "재고 정산 보고"), date=(1, "모레"), cat=(1, "재고"),
      time=evidence([(1, "오전 11시")], superseded=[(1, "오전 9시")], markers=[(1, "말고")]),
      at="2029-01-02T11:00:00+09:00"),
    S(55, C, "correction_time", ["2028년 5월 17일 14시 말고 16시에 피킹 오류 점검 넣어줘"], WK,
      title=(1, "피킹 오류 점검"), date=(1, "2028년 5월 17일"), cat=(1, "피킹"),
      time=evidence([(1, "16시")], superseded=[(1, "14시")], markers=[(1, "말고")]),
      at="2028-05-17T16:00:00+09:00"),
    S(56, C, "correction_time", ["내일 오후 2시 대신 오후 5시에 입고 지연 보고 등록해줘"], WM,
      title=(1, "입고 지연 보고"), date=(1, "내일"), cat=(1, "입고"),
      time=evidence([(1, "오후 5시")], superseded=[(1, "오후 2시")], markers=[(1, "대신")]),
      at="2028-02-11T17:00:00+09:00"),
    S(57, C, "correction_title_other", ["안전 점검 말고 소방 훈련을 2028년 3월 21일 오후 2시에 등록해줘"], WE,
      date=(1, "2028년 3월 21일"), time=(1, "오후 2시"), cat=None,
      title=evidence([(1, "소방 훈련")], superseded=[(1, "안전 점검")], markers=[(1, "말고")]),
      at="2028-03-21T14:00:00+09:00"),
    S(58, C, "correction_title_other", ["예산 회의 아니고 인력 배치 회의를 내일 오전 10시에 넣어줘"], EM,
      date=(1, "내일"), time=(1, "오전 10시"), cat=None,
      title=evidence([(1, "인력 배치 회의")], superseded=[(1, "예산 회의")], markers=[(1, "아니고")]),
      at="2028-04-05T10:00:00+09:00"),
    S(59, C, "correction_title_category", ["재고 정리 말고 반품 정리를 2028년 5월 18일 오전 11시에 등록해줘"], WK,
      date=(1, "2028년 5월 18일"), time=(1, "오전 11시"),
      title=evidence([(1, "반품 정리")], superseded=[(1, "재고 정리")], markers=[(1, "말고")]),
      cat=evidence([(1, "반품")], superseded=[(1, "재고")], markers=[(1, "말고")]),
      at="2028-05-18T11:00:00+09:00"),
    S(60, C, "correction_title_category", ["입고 대기열 점검 아니고 출고 대기열 점검을 내일 오후 2시에 넣어줘"], ME,
      date=(1, "내일"), time=(1, "오후 2시"),
      title=evidence([(1, "출고 대기열 점검")], superseded=[(1, "입고 대기열 점검")], markers=[(1, "아니고")]),
      cat=evidence([(1, "출고")], superseded=[(1, "입고")], markers=[(1, "아니고")]),
      at="2028-07-01T14:00:00+09:00"),
    S(61, C, "correction_title_category", ["반품 검수 말고 입고 검수 준비를 모레 오후 4시에 등록해줘"], LD,
      date=(1, "모레"), time=(1, "오후 4시"),
      title=evidence([(1, "입고 검수 준비")], superseded=[(1, "반품 검수")], markers=[(1, "말고")]),
      cat=evidence([(1, "입고")], superseded=[(1, "반품")], markers=[(1, "말고")]),
      at="2028-03-02T16:00:00+09:00"),
    S(62, C, "marker_after_adopted_only", ["모레 오후 4시에 재고 보충 요청 등록해줘, 3시는 말고"], WE,
      title=(1, "재고 보충 요청"), date=(1, "모레"), time=(1, "오후 4시"), cat=(1, "재고"),
      at="2028-03-17T16:00:00+09:00", correction_provenance=AFTER),
    S(63, C, "marker_after_adopted_only", ["2028년 4월 13일 오전 11시에 출고 서류 점검 넣어줘, 10시는 아니고"], EM,
      title=(1, "출고 서류 점검"), date=(1, "2028년 4월 13일"), time=(1, "오전 11시"), cat=(1, "출고"),
      at="2028-04-13T11:00:00+09:00", correction_provenance=AFTER),
    S(64, C, "marker_after_adopted_only", ["2029년 1월 4일 오후 3시에 연간 계획 회의 잡아줘, 1월 3일은 말고"], YE,
      title=(1, "연간 계획 회의"), date=(1, "2029년 1월 4일"), time=(1, "오후 3시"), cat=None,
      at="2029-01-04T15:00:00+09:00", correction_provenance=AFTER),
    S(65, C, "pending_cross_turn_adopted_only", ["내일 오후 2시로 하나 잡아줄래", "협력사 품질 회의야, 아 오후 4시로"], WK,
      title=(2, "협력사 품질 회의"), date=(1, "내일"), time=(2, "오후 4시"), cat=None,
      at="2028-05-14T16:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      target_overrides={1: {"time": evidence([(1, "오후 2시")])}}, correction_provenance=CROSS,
      expectations=[ask("task_or_title_unclear"), *OK]),
    S(66, C, "pending_cross_turn_adopted_only", ["2028년 3월 7일 오전 9시에 하나 넣어 둬", "입고 일정 조율이고, 날짜는 2028년 3월 8일"], LD,
      title=(2, "입고 일정 조율"), date=(2, "2028년 3월 8일"), time=(1, "오전 9시"), cat=(2, "입고"),
      at="2028-03-08T09:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      target_overrides={1: {"date": evidence([(1, "2028년 3월 7일")])}}, correction_provenance=CROSS,
      expectations=[ask("task_or_title_unclear"), *OK]),
    S(67, C, "pending_cross_turn_adopted_only", ["모레 오전 11시 반쯤 하나 넣어 놔 줘", "반품 운송사 미팅이고, 음 오후 1시"], ME,
      title=(2, "반품 운송사 미팅"), date=(1, "모레"), time=(2, "오후 1시"), cat=(2, "반품"),
      at="2028-07-02T13:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      target_overrides={1: {"time": evidence([(1, "오전 11시 반")])}}, correction_provenance=CROSS,
      expectations=[ask("task_or_title_unclear"), *OK]),

    # ---- Time safety: 20 ----
    S(68, T, "untrusted_relative_unresolved", ["모레 오후 3시에 재고 적재 점검 등록해줘"], WE,
      trusted=False, title=(1, "재고 적재 점검"), date=(1, "모레"), time=(1, "오후 3시"), cat=(1, "재고"),
      at=None, missing=miss(("absolute_datetime_due_to_untrusted_time", 1, None)),
      final_status="not_executed_pending_info", expectations=[ask("absolute_datetime_required")]),
    S(69, T, "untrusted_relative_unresolved", ["이번 주 일요일 오전 10시에 시설 보수 회의 넣어줘"], WK,
      trusted=False, title=(1, "시설 보수 회의"), date=(1, "이번 주 일요일"), time=(1, "오전 10시"), cat=None,
      at=None, missing=miss(("absolute_datetime_due_to_untrusted_time", 1, None)),
      final_status="not_executed_pending_info", expectations=[ask("absolute_datetime_required")]),
    S(70, T, "untrusted_relative_unresolved", ["내일 오후 2시에 반품 정책 회의 잡아줘"], YE,
      trusted=False, title=(1, "반품 정책 회의"), date=(1, "내일"), time=(1, "오후 2시"), cat=(1, "반품"),
      at=None, missing=miss(("absolute_datetime_due_to_untrusted_time", 1, None)),
      final_status="not_executed_pending_info", expectations=[ask("absolute_datetime_required")]),
    S(71, T, "untrusted_absolute", ["2028년 4월 9일 오전 8시에 입고 전표 마감 등록해줘"], EM,
      trusted=False, title=(1, "입고 전표 마감"), date=(1, "2028년 4월 9일"), time=(1, "오전 8시"),
      cat=(1, "입고"), at="2028-04-09T08:00:00+09:00"),
    S(72, T, "untrusted_absolute", ["2028년 7월 6일 15시 30분에 출고 정산 회의 넣어줘"], ME,
      trusted=False, title=(1, "출고 정산 회의"), date=(1, "2028년 7월 6일"), time=(1, "15시 30분"),
      cat=(1, "출고"), at="2028-07-06T15:30:00+09:00"),
    S(73, T, "untrusted_absolute", ["2028년 3월 9일 오후 2시에 전기 설비 점검 잡아줘"], LD,
      trusted=False, title=(1, "전기 설비 점검"), date=(1, "2028년 3월 9일"), time=(1, "오후 2시"),
      cat=None, at="2028-03-09T14:00:00+09:00"),
    S(74, T, "past_time_rejected", ["오늘 오전 11시에 재고 차이 분석 등록해줘"], ME,
      title=(1, "재고 차이 분석"), date=(1, "오늘"), time=(1, "오전 11시"), cat=(1, "재고"),
      at="2028-06-30T11:00:00+09:00", final_status="schedule_rejected", expectations=PAST),
    S(75, T, "past_time_rejected", ["오늘 오후 6시에 입고 마감 확인 넣어줘"], WE,
      title=(1, "입고 마감 확인"), date=(1, "오늘"), time=(1, "오후 6시"), cat=(1, "입고"),
      at="2028-03-15T18:00:00+09:00", final_status="schedule_rejected", expectations=PAST),
    S(76, T, "past_time_rejected", ["2028년 5월 10일 오후 2시에 협력사 감사 대응 회의 잡아줘"], WK,
      title=(1, "협력사 감사 대응 회의"), date=(1, "2028년 5월 10일"), time=(1, "오후 2시"), cat=None,
      at="2028-05-10T14:00:00+09:00", final_status="schedule_rejected", expectations=PAST),
    S(77, T, "year_boundary_relative", ["모레 오전 10시에 출고 연초 계획 회의 등록해줘"], YE,
      title=(1, "출고 연초 계획 회의"), date=(1, "모레"), time=(1, "오전 10시"), cat=(1, "출고"),
      at="2029-01-02T10:00:00+09:00"),
    S(78, T, "year_boundary_relative", ["글피 오후 3시에 창고 재배치 회의 넣어줘"], YE,
      title=(1, "창고 재배치 회의"), date=(1, "글피"), time=(1, "오후 3시"), cat=None,
      at="2029-01-03T15:00:00+09:00"),
    S(79, T, "year_boundary_relative", ["내일 오후 4시에 입고 신년 일정 공유 등록해줘"], YE,
      title=(1, "입고 신년 일정 공유"), date=(1, "내일"), time=(1, "오후 4시"), cat=(1, "입고"),
      at="2029-01-01T16:00:00+09:00"),
    S(80, T, "leap_day", ["내일 오전 10시에 재고 월초 점검 등록해줘"], LD,
      title=(1, "재고 월초 점검"), date=(1, "내일"), time=(1, "오전 10시"), cat=(1, "재고"),
      at="2028-03-01T10:00:00+09:00"),
    S(81, T, "leap_day", ["2028년 2월 29일 오후 3시에 반품 월말 정산 넣어줘"], WM,
      title=(1, "반품 월말 정산"), date=(1, "2028년 2월 29일"), time=(1, "오후 3시"), cat=(1, "반품"),
      at="2028-02-29T15:00:00+09:00"),
    S(82, T, "midnight_24h", ["2028년 4월 6일 0시 15분에 서버 점검 등록해줘"], EM,
      title=(1, "서버 점검"), date=(1, "2028년 4월 6일"), time=(1, "0시 15분"), cat=None,
      at="2028-04-06T00:15:00+09:00"),
    S(83, T, "midnight_24h", ["내일 23시에 출고 야간 상차 확인 넣어줘"], WE,
      title=(1, "출고 야간 상차 확인"), date=(1, "내일"), time=(1, "23시"), cat=(1, "출고"),
      at="2028-03-16T23:00:00+09:00"),
    S(84, T, "midnight_24h", ["2028년 5월 20일 오전 12시 반에 재고 시스템 백업 잡아줘"], WK,
      title=(1, "재고 시스템 백업"), date=(1, "2028년 5월 20일"), time=(1, "오전 12시 반"), cat=(1, "재고"),
      at="2028-05-20T00:30:00+09:00"),
    S(85, T, "ambiguous_ampm_unresolved", ["2028년 7월 7일 9시에 입고 검품 회의 등록해줘"], ME,
      title=(1, "입고 검품 회의"), date=(1, "2028년 7월 7일"), time=(1, "9시"), cat=(1, "입고"),
      at=None, missing=miss(("ampm", 1, None)), final_status="not_executed_pending_info",
      expectations=[ask("ampm")]),
    S(86, T, "ambiguous_ampm_unresolved", ["모레 5시에 소모품 재고 확인 넣어줘"], LD,
      title=(1, "소모품 재고 확인"), date=(1, "모레"), time=(1, "5시"), cat=(1, "재고"),
      at=None, missing=miss(("ampm", 1, None)), final_status="not_executed_pending_info",
      expectations=[ask("ampm")]),
    S(87, T, "ambiguous_ampm_unresolved", ["2028년 2월 18일 11시 반에 직원 안전 면담 잡아줘"], WM,
      title=(1, "직원 안전 면담"), date=(1, "2028년 2월 18일"), time=(1, "11시 반"), cat=None,
      at=None, missing=miss(("ampm", 1, None)), final_status="not_executed_pending_info",
      expectations=[ask("ampm")]),

    # ---- Mixed unsupported + consent: 13 ----
    S(88, X, "mixed_grant", ["2028년 2월 14일 오전 10시에 출고 일정 회의 등록하고 알림 좀 설정해줘", "알림 빼고 등록만 해줘"], WM,
      title=(1, "출고 일정 회의"), date=(1, "2028년 2월 14일"), time=(1, "오전 10시"), cat=(1, "출고"),
      at="2028-02-14T10:00:00+09:00", missing=miss(("user_consent", 1, 2)),
      unsupported=(1, "알림 좀 설정해줘", "reminder"), expectations=[CONSENT_Q, GRANTED_OK]),
    S(89, X, "mixed_grant", ["내일 오후 3시에 재고 소진 점검 넣고 푸시도 보내줘", "푸시는 필요 없어, 일정만 넣어줘"], EM,
      title=(1, "재고 소진 점검"), date=(1, "내일"), time=(1, "오후 3시"), cat=(1, "재고"),
      at="2028-04-05T15:00:00+09:00", missing=miss(("user_consent", 1, 2)),
      unsupported=(1, "푸시도 보내줘", "reminder"), expectations=[CONSENT_Q, GRANTED_OK]),
    S(90, X, "mixed_grant", ["모레 오전 11시에 반품 접수 회의 등록하고 리마인더도 맞춰줘", "응, 리마인더 없이 진행해"], WK,
      title=(1, "반품 접수 회의"), date=(1, "모레"), time=(1, "오전 11시"), cat=(1, "반품"),
      at="2028-05-15T11:00:00+09:00", missing=miss(("user_consent", 1, 2)),
      unsupported=(1, "리마인더도 맞춰줘", "reminder"), expectations=[CONSENT_Q, GRANTED_OK]),
    S(91, X, "mixed_grant", ["2028년 7월 8일 오후 2시에 장비 대여 미팅 등록하고 문자도 보내줘", "문자 말고 등록만 해줘"], ME,
      title=(1, "장비 대여 미팅"), date=(1, "2028년 7월 8일"), time=(1, "오후 2시"), cat=None,
      at="2028-07-08T14:00:00+09:00", missing=miss(("user_consent", 1, 2)),
      unsupported=(1, "문자도 보내줘", "other"), expectations=[CONSENT_Q, GRANTED_OK]),
    S(92, X, "mixed_decline", ["내일 오전 9시에 입고 협의 회의 등록하고 1시간 전에 알려줘", "아니요, 그냥 취소해"], WE,
      title=(1, "입고 협의 회의"), date=(1, "내일"), time=(1, "오전 9시"), cat=(1, "입고"),
      at="2028-03-16T09:00:00+09:00", missing=miss(("user_consent", 1, 2)),
      unsupported=(1, "1시간 전에 알려줘", "reminder"), final_status="rejected_by_user",
      expectations=[CONSENT_Q, DECLINED]),
    S(93, X, "mixed_decline", ["모레 오후 2시에 재고 검증 회의 넣고 알림도 켜줘", "됐어, 등록하지 마"], LD,
      title=(1, "재고 검증 회의"), date=(1, "모레"), time=(1, "오후 2시"), cat=(1, "재고"),
      at="2028-03-02T14:00:00+09:00", missing=miss(("user_consent", 1, 2)),
      unsupported=(1, "알림도 켜줘", "reminder"), final_status="rejected_by_user",
      expectations=[CONSENT_Q, DECLINED]),
    S(94, X, "mixed_decline", ["2029년 1월 5일 오전 10시에 출고 신년 점검 등록하고 푸시도 보내줘", "싫어, 넣지 마"], YE,
      title=(1, "출고 신년 점검"), date=(1, "2029년 1월 5일"), time=(1, "오전 10시"), cat=(1, "출고"),
      at="2029-01-05T10:00:00+09:00", missing=miss(("user_consent", 1, 2)),
      unsupported=(1, "푸시도 보내줘", "reminder"), final_status="rejected_by_user",
      expectations=[CONSENT_Q, DECLINED]),
    S(95, X, "mixed_pending", ["2028년 3월 22일 오후 4시에 정기 소독 등록하고 리마인더 설정해줘"], WE,
      title=(1, "정기 소독"), date=(1, "2028년 3월 22일"), time=(1, "오후 4시"), cat=None,
      at="2028-03-22T16:00:00+09:00", missing=miss(("user_consent", 1, None)),
      unsupported=(1, "리마인더 설정해줘", "reminder"), final_status="not_executed_pending_info",
      expectations=[CONSENT_Q]),
    S(96, X, "mixed_pending", ["모레 오전 9시에 입고 검사 장비 교정 넣고 문자로 보내줘"], EM,
      title=(1, "입고 검사 장비 교정"), date=(1, "모레"), time=(1, "오전 9시"), cat=(1, "입고"),
      at="2028-04-06T09:00:00+09:00", missing=miss(("user_consent", 1, None)),
      unsupported=(1, "문자로 보내줘", "other"), final_status="not_executed_pending_info",
      expectations=[CONSENT_Q]),
    S(97, X, "mixed_pending", ["내일 오후 3시에 반품 창고 청소 등록하고 30분 전에 미리 알려줘"], ME,
      title=(1, "반품 창고 청소"), date=(1, "내일"), time=(1, "오후 3시"), cat=(1, "반품"),
      at="2028-07-01T15:00:00+09:00", missing=miss(("user_consent", 1, None)),
      unsupported=(1, "30분 전에 미리 알려줘", "reminder"), final_status="not_executed_pending_info",
      expectations=[CONSENT_Q]),
    S(98, X, "mixed_grant", ["내일 오후 2시 반에 재고 순환 점검 등록하고 미리 알려줘", "좋아, 알림 없이 등록해"], WK,
      title=(1, "재고 순환 점검"), date=(1, "내일"), time=(1, "오후 2시 반"), cat=(1, "재고"),
      at="2028-05-14T14:30:00+09:00", missing=miss(("user_consent", 1, 2)),
      unsupported=(1, "미리 알려줘", "reminder"), expectations=[CONSENT_Q, GRANTED_OK]),
    S(99, X, "mixed_decline", ["2028년 3월 10일 오전 10시 40분에 출고 차량 배정 회의 넣고 푸시 좀 보내줘", "아뇨, 그만둘게요"], LD,
      title=(1, "출고 차량 배정 회의"), date=(1, "2028년 3월 10일"), time=(1, "오전 10시 40분"), cat=(1, "출고"),
      at="2028-03-10T10:40:00+09:00", missing=miss(("user_consent", 1, 2)),
      unsupported=(1, "푸시 좀 보내줘", "reminder"), final_status="rejected_by_user",
      expectations=[CONSENT_Q, DECLINED]),
    S(100, X, "mixed_pending", ["모레 오후 5시에 협력사 안전 회의 등록하고 리마인더도 달아줘"], ME,
      title=(1, "협력사 안전 회의"), date=(1, "모레"), time=(1, "오후 5시"), cat=None,
      at="2028-07-02T17:00:00+09:00", missing=miss(("user_consent", 1, None)),
      unsupported=(1, "리마인더도 달아줘", "reminder"), final_status="not_executed_pending_info",
      expectations=[CONSENT_Q]),
]


def authoring_errors(specifications: list[dict[str, Any]]) -> list[str]:
    errors = list(batch_quality_errors(specifications))
    pilot_titles = {item["titleEvidence"]["adoptedSpans"][-1]["text"] for item in PILOT_SPECS}
    for item in specifications:
        title = item["titleEvidence"]["adoptedSpans"][-1]["text"]
        if title in pilot_titles:
            errors.append(f"{item['caseId']}: title {title!r} reused from pilot")
    for family, count in sorted(Counter(item["family"] for item in specifications).items()):
        if count > MAX_PER_FAMILY:
            errors.append(f"FAMILY_OVER_LIMIT: {family} {count} > {MAX_PER_FAMILY}")
    return errors


_YEARLESS_DATE = re.compile(r"(\d{4}년\s*)?\d{1,2}월\s*\d{1,2}일")


def _fix_tags(case: dict[str, Any], specification: dict[str, Any]) -> dict[str, Any]:
    """Batch-only tag fixes; the shared pilot tagger stays unchanged."""
    tags = case["meta"]["scenarioTags"]
    if len(specification["turns"]) == 1 and "follow_up_answer" in tags:
        tags.remove("follow_up_answer")
    if "relative_time" not in tags and any(
        match.group(1) is None
        for item in specification["turns"]
        for match in _YEARLESS_DATE.finditer(str(item["text"]))
    ):
        tags.append("relative_time")
    return case


def main() -> None:
    check_specs(SPECS)
    errors = authoring_errors(SPECS)
    if errors:
        raise RuntimeError("; ".join(errors))
    cases = [_fix_tags(make_case(item), item) for item in SPECS]
    if len(cases) != 100 or len({case["meta"]["caseId"] for case in cases}) != 100:
        raise RuntimeError("batch 1 must contain 100 unique cases")
    OUTPUT.write_text("\n".join(canonical(case) for case in cases) + "\n", encoding="utf-8")
    targets = [
        {
            "caseId": specification["caseId"],
            "turn": through_turn,
            "target": make_candidate_target(specification, through_turn),
            "status": "PROPOSED_UNREVIEWED",
        }
        for specification in SPECS
        for through_turn in range(1, len(specification["turns"]) + 1)
    ]
    TARGETS.write_text("\n".join(canonical(row) for row in targets) + "\n", encoding="utf-8")
    print(canonical({
        "status": "PROPOSED_UNREVIEWED",
        "caseCount": len(cases),
        "output": str(OUTPUT.relative_to(HERE.parent)),
        "sha256": sha256(OUTPUT),
        "targetCount": len(targets),
        "targetsSha256": sha256(TARGETS),
    }))


if __name__ == "__main__":
    main()
