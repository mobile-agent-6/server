"""Build Candidate-C create batch 2 (100 new training candidates) deterministically.

Status: PROPOSED_UNREVIEWED / BE-PROPOSED.  Adds the skills missing from run 001
(untrusted server time with 오늘, date-only follow-ups, yearless dates under
untrusted time) and the review bias fixes (category cue mid-title, title-first
word order, polite speech, varied corrections, unsupported request first).
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "pilot"))
sys.path.insert(0, str(ROOT / "batch1"))
sys.path.insert(0, str(ROOT.parent / "structured_output_contract_v0.1"))

from build_batch1 import (  # noqa: E402
    AFTER, ASK_INFO, CONSENT_Q, CROSS, DECLINED, GRANTED_OK, OK, PAST,
    C, D, M, T, X, EM, LD, ME, WE, WK, WM, YE,
    SPECS as B1_SPECS, _category, _ev, ask, miss,
)
from build_pilot import (  # noqa: E402
    SPECS as PILOT_SPECS, canonical, evidence, make_candidate_target, make_case, sha256, spec, turn,
)
from spec_checks import batch_quality_errors, check_specs  # noqa: E402
import common_gate_v0_3 as gate_v3  # noqa: E402

OUTPUT = HERE / "conversations-v0.1.jsonl"
TARGETS = HERE / "candidate-c-targets-v0.1.jsonl"
MAX_PER_FAMILY = 5
UNTRUSTED = "absolute_datetime_due_to_untrusted_time"
E = evidence


def S(number: int, bucket: str, family: str, texts: list[str], ref: str, *,
      title: Any, date: Any, time: Any, cat: Any, at: str | None,
      expectations: list[dict[str, Any]] = OK, **kwargs: Any) -> dict[str, Any]:
    turns = [turn(text) if index == 0 else turn(text, answer=index) for index, text in enumerate(texts)]
    return spec(
        number, bucket, family, turns, reference=ref,
        title=_ev(title), date=_ev(date), time=_ev(time), category_evidence=_ev(cat),
        scheduled_at=at, category=_category(cat), expectations=expectations,
        batch="b2", dataset_role="training_candidate", authored_on="2026-10-27", **kwargs,
    )


def UF(number, family, texts, ref, *, title, cat, t1_date, t1_time, date, time, at):
    """Untrusted server time: ask for an absolute date, then resolve on turn 2."""
    overrides = {"date": E([(1, t1_date)])}
    if t1_time is not None:
        overrides["time"] = E([(1, t1_time)])
    return S(number, M, family, texts, ref, trusted=False, title=title, date=date, time=time, cat=cat,
             at=at, missing=miss((UNTRUSTED, 1, 2)), target_overrides={1: overrides},
             expectations=[ask("absolute_datetime_required"), *OK])


def UP(number, texts, ref, *, title, date, time, cat, family):
    """Untrusted server time with a relative or yearless date and no follow-up yet."""
    return S(number, T, family, texts, ref, trusted=False, title=title, date=date, time=time, cat=cat,
             at=None, missing=miss((UNTRUSTED, 1, None)), final_status="not_executed_pending_info",
             expectations=[ask("absolute_datetime_required")])


def MX(number, family, texts, ref, *, title, date, time, cat, at, cue, feature, result):
    if result == "pending":
        return S(number, X, family, texts, ref, title=title, date=date, time=time, cat=cat, at=at,
                 missing=miss(("user_consent", 1, None)), unsupported=(1, cue, feature),
                 final_status="not_executed_pending_info", expectations=[CONSENT_Q])
    declined = result == "declined"
    return S(number, X, family, texts, ref, title=title, date=date, time=time, cat=cat, at=at,
             missing=miss(("user_consent", 1, 2)), unsupported=(1, cue, feature),
             final_status="rejected_by_user" if declined else "success",
             expectations=[CONSENT_Q, DECLINED if declined else GRANTED_OK])


SPECS = [
    # ---- Direct create: 22 ----
    S(1, D, "direct_title_first", ["협력사 견적 비교 회의, 내일 오후 2시에 등록해줘"], WE,
      title=(1, "협력사 견적 비교 회의"), date=(1, "내일"), time=(1, "오후 2시"), cat=None, at="2028-03-16T14:00:00+09:00"),
    S(2, D, "direct_title_first", ["주간 입고 일정 공유는 모레 오전 9시로 잡아줘"], EM,
      title=(1, "주간 입고 일정 공유"), date=(1, "모레"), time=(1, "오전 9시"), cat=(1, "입고"), at="2028-04-06T09:00:00+09:00"),
    S(3, D, "direct_title_first", ["긴급 반품 처리 건 2028년 5월 17일 오후 1시에 넣어줘"], WK,
      title=(1, "긴급 반품 처리"), date=(1, "2028년 5월 17일"), time=(1, "오후 1시"), cat=(1, "반품"), at="2028-05-17T13:00:00+09:00"),
    S(4, D, "direct_title_first", ["월말 재고 마감, 오늘 오후 7시에 등록해줘"], ME,
      title=(1, "월말 재고 마감"), date=(1, "오늘"), time=(1, "오후 7시"), cat=(1, "재고"), at="2028-06-30T19:00:00+09:00"),
    S(5, D, "direct_title_first", ["신규 거래처 계약서 검토 2028년 3월 3일 오전 11시에 잡아줘"], LD,
      title=(1, "신규 거래처 계약서 검토"), date=(1, "2028년 3월 3일"), time=(1, "오전 11시"), cat=None, at="2028-03-03T11:00:00+09:00"),
    S(6, D, "direct_polite", ["내일 오전 10시에 2층 창고 정리 등록해 주세요"], WM,
      title=(1, "2층 창고 정리"), date=(1, "내일"), time=(1, "오전 10시"), cat=None, at="2028-02-11T10:00:00+09:00"),
    S(7, D, "direct_polite", ["2028년 3월 20일 오후 3시에 냉장 입고 점검 넣어 주세요"], WE,
      title=(1, "냉장 입고 점검"), date=(1, "2028년 3월 20일"), time=(1, "오후 3시"), cat=(1, "입고"), at="2028-03-20T15:00:00+09:00"),
    S(8, D, "direct_polite", ["모레 오전 11시에 신년 재고 집계 잡아 주세요"], YE,
      title=(1, "신년 재고 집계"), date=(1, "모레"), time=(1, "오전 11시"), cat=(1, "재고"), at="2029-01-02T11:00:00+09:00"),
    S(9, D, "direct_polite", ["글피 오후 4시 반에 대량 출고 사전 점검 등록해 주실래요"], EM,
      title=(1, "대량 출고 사전 점검"), date=(1, "글피"), time=(1, "오후 4시 반"), cat=(1, "출고"), at="2028-04-07T16:30:00+09:00"),
    S(10, D, "direct_polite", ["2028년 5월 22일 오전 9시에 외부 감사 준비 회의 등록 부탁드려요"], WK,
      title=(1, "외부 감사 준비 회의"), date=(1, "2028년 5월 22일"), time=(1, "오전 9시"), cat=None, at="2028-05-22T09:00:00+09:00"),
    S(11, D, "direct_cue_mid", ["7월 7일 오후 2시에 해외 반품 통관 서류 확인 넣어줘"], ME,
      title=(1, "해외 반품 통관 서류 확인"), date=(1, "7월 7일"), time=(1, "오후 2시"), cat=(1, "반품"), at="2028-07-07T14:00:00+09:00"),
    S(12, D, "direct_cue_mid", ["모레 오후 1시에 소형 박스 재고 확인 등록해줘"], LD,
      title=(1, "소형 박스 재고 확인"), date=(1, "모레"), time=(1, "오후 1시"), cat=(1, "재고"), at="2028-03-02T13:00:00+09:00"),
    S(13, D, "direct_cue_mid", ["2028년 2월 16일 오후 5시에 지역 출고 현황 보고 넣어줘"], WM,
      title=(1, "지역 출고 현황 보고"), date=(1, "2028년 2월 16일"), time=(1, "오후 5시"), cat=(1, "출고"), at="2028-02-16T17:00:00+09:00"),
    S(14, D, "direct_cue_mid", ["이번 주 금요일 오전 11시에 협력 입고 업체 미팅 잡아줘"], WE,
      title=(1, "협력 입고 업체 미팅"), date=(1, "이번 주 금요일"), time=(1, "오전 11시"), cat=(1, "입고"), at="2028-03-17T11:00:00+09:00"),
    S(15, D, "direct_cue_mid", ["2028년 4월 12일 오전 8시 30분에 야간조 피킹 교육 등록해줘"], EM,
      title=(1, "야간조 피킹 교육"), date=(1, "2028년 4월 12일"), time=(1, "오전 8시 30분"), cat=(1, "피킹"), at="2028-04-12T08:30:00+09:00"),
    S(16, D, "direct_weekday_polite", ["이번 주 일요일 오후 3시에 창고 보안 점검 넣어 주세요"], WK,
      title=(1, "창고 보안 점검"), date=(1, "이번 주 일요일"), time=(1, "오후 3시"), cat=None, at="2028-05-14T15:00:00+09:00"),
    S(17, D, "direct_weekday_polite", ["이번 주 금요일 오전 10시에 품질 클레임 회의 등록해 주세요"], LD,
      title=(1, "품질 클레임 회의"), date=(1, "이번 주 금요일"), time=(1, "오전 10시"), cat=None, at="2028-03-03T10:00:00+09:00"),
    S(18, D, "direct_weekday_polite", ["이번 주 토요일 오전 9시에 반품 재검수 등록해 주세요"], ME,
      title=(1, "반품 재검수"), date=(1, "이번 주 토요일"), time=(1, "오전 9시"), cat=(1, "반품"), at="2028-07-01T09:00:00+09:00"),
    S(19, D, "direct_mixed_minutes", ["2028년 2월 14일 오후 2시 40분에 운송 계약 갱신 미팅 넣어줘"], WM,
      title=(1, "운송 계약 갱신 미팅"), date=(1, "2028년 2월 14일"), time=(1, "오후 2시 40분"), cat=None, at="2028-02-14T14:40:00+09:00"),
    S(20, D, "direct_mixed_minutes", ["모레 오전 7시 50분에 조기 출고 상차 확인 등록해줘"], WE,
      title=(1, "조기 출고 상차 확인"), date=(1, "모레"), time=(1, "오전 7시 50분"), cat=(1, "출고"), at="2028-03-17T07:50:00+09:00"),
    S(21, D, "direct_mixed_minutes", ["2029년 1월 2일 13시 20분에 연초 입고 계획 확정 등록해줘"], YE,
      title=(1, "연초 입고 계획 확정"), date=(1, "2029년 1월 2일"), time=(1, "13시 20분"), cat=(1, "입고"), at="2029-01-02T13:20:00+09:00"),
    S(22, D, "direct_mixed_minutes", ["내일 17시 45분에 설비 안전 순회 넣어줘"], EM,
      title=(1, "설비 안전 순회"), date=(1, "내일"), time=(1, "17시 45분"), cat=None, at="2028-04-05T17:45:00+09:00"),

    # ---- Missing information and follow-up: 25 ----
    UF(23, "untrusted_today_followup", ["오늘 오후 4시에 입고 수량 재확인 등록해 주세요", "2028년 4월 4일 오후 4시요"], EM,
       title=(1, "입고 수량 재확인"), cat=(1, "입고"), t1_date="오늘", t1_time="오후 4시",
       date=(2, "2028년 4월 4일"), time=(2, "오후 4시"), at="2028-04-04T16:00:00+09:00"),
    UF(24, "untrusted_today_followup", ["오늘 오후 8시에 협력사 정산 마감 넣어줘", "2028년 6월 30일 20시"], ME,
       title=(1, "협력사 정산 마감"), cat=None, t1_date="오늘", t1_time="오후 8시",
       date=(2, "2028년 6월 30일"), time=(2, "20시"), at="2028-06-30T20:00:00+09:00"),
    UF(25, "untrusted_today_followup", ["오늘 저녁 9시에 재고 이상 보고 등록해줘", "2028년 3월 15일 오후 9시예요"], WE,
       title=(1, "재고 이상 보고"), cat=(1, "재고"), t1_date="오늘", t1_time="저녁 9시",
       date=(2, "2028년 3월 15일"), time=(2, "오후 9시"), at="2028-03-15T21:00:00+09:00"),
    UF(26, "untrusted_today_followup", ["오늘 오후 1시 반에 반품 상담 일정 잡아줘", "2028년 5월 13일 오후 1시 30분이요"], WK,
       title=(1, "반품 상담"), cat=(1, "반품"), t1_date="오늘", t1_time="오후 1시 반",
       date=(2, "2028년 5월 13일"), time=(2, "오후 1시 30분"), at="2028-05-13T13:30:00+09:00"),
    UF(27, "untrusted_date_only_followup", ["출고 기사 점호, 오늘 오후 6시로 넣어줘", "날짜는 2028년 6월 30일이에요"], ME,
       title=(1, "출고 기사 점호"), cat=(1, "출고"), t1_date="오늘", t1_time=None,
       date=(2, "2028년 6월 30일"), time=(1, "오후 6시"), at="2028-06-30T18:00:00+09:00"),
    UF(28, "untrusted_date_only_followup", ["내일 오전 11시에 입고 서류 스캔 작업 등록해 주세요", "2028년 2월 11일입니다"], WM,
       title=(1, "입고 서류 스캔 작업"), cat=(1, "입고"), t1_date="내일", t1_time=None,
       date=(2, "2028년 2월 11일"), time=(1, "오전 11시"), at="2028-02-11T11:00:00+09:00"),
    UF(29, "untrusted_date_only_followup", ["모레 오후 2시 반에 재고 이력 정비 잡아줘", "2028년 4월 6일로 해줘"], EM,
       title=(1, "재고 이력 정비"), cat=(1, "재고"), t1_date="모레", t1_time=None,
       date=(2, "2028년 4월 6일"), time=(1, "오후 2시 반"), at="2028-04-06T14:30:00+09:00"),
    UF(30, "untrusted_date_only_followup", ["글피 오전 9시에 창고 출입 권한 점검 넣어줘", "2028년 3월 3일이야"], LD,
       title=(1, "창고 출입 권한 점검"), cat=None, t1_date="글피", t1_time=None,
       date=(2, "2028년 3월 3일"), time=(1, "오전 9시"), at="2028-03-03T09:00:00+09:00"),
    UF(31, "untrusted_date_only_followup", ["내일 오후 3시에 반품 연말 결산 보고 등록해줘", "2029년 1월 1일이요"], YE,
       title=(1, "반품 연말 결산 보고"), cat=(1, "반품"), t1_date="내일", t1_time=None,
       date=(2, "2029년 1월 1일"), time=(1, "오후 3시"), at="2029-01-01T15:00:00+09:00"),
    UF(32, "untrusted_yearless_followup", ["5월 20일 오후 4시에 협력사 교육 일정 등록해줘", "2028년 5월 20일이에요"], WK,
       title=(1, "협력사 교육"), cat=None, t1_date="5월 20일", t1_time=None,
       date=(2, "2028년 5월 20일"), time=(1, "오후 4시"), at="2028-05-20T16:00:00+09:00"),
    UF(33, "untrusted_yearless_followup", ["3월 28일 오전 10시에 출고 단가 검토 넣어줘", "2028년 3월 28일입니다"], WE,
       title=(1, "출고 단가 검토"), cat=(1, "출고"), t1_date="3월 28일", t1_time=None,
       date=(2, "2028년 3월 28일"), time=(1, "오전 10시"), at="2028-03-28T10:00:00+09:00"),
    UF(34, "untrusted_yearless_followup", ["7월 15일 오후 2시에 입고 검사 장비 점검 잡아줘", "2028년 7월 15일"], ME,
       title=(1, "입고 검사 장비 점검"), cat=(1, "입고"), t1_date="7월 15일", t1_time=None,
       date=(2, "2028년 7월 15일"), time=(1, "오후 2시"), at="2028-07-15T14:00:00+09:00"),
    S(35, M, "missing_time_polite", ["2028년 5월 18일 재고 실사 결과 공유 등록해 주세요", "오후 3시 20분으로 해 주세요"], WK,
      title=(1, "재고 실사 결과 공유"), date=(1, "2028년 5월 18일"), time=(2, "오후 3시 20분"), cat=(1, "재고"),
      at="2028-05-18T15:20:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(36, M, "missing_time_polite", ["모레 거래처 방문 상담 넣어 주세요", "오전 11시가 좋겠어요"], LD,
      title=(1, "거래처 방문 상담"), date=(1, "모레"), time=(2, "오전 11시"), cat=None,
      at="2028-03-02T11:00:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(37, M, "missing_time_polite", ["2029년 1월 6일 출고 일정 재편성 회의 잡아 주세요", "14시로 부탁드려요"], YE,
      title=(1, "출고 일정 재편성 회의"), date=(1, "2029년 1월 6일"), time=(2, "14시"), cat=(1, "출고"),
      at="2029-01-06T14:00:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(38, M, "missing_time_polite", ["내일 반품 상태 점검 등록해 주실래요", "오후 1시 반이요"], WM,
      title=(1, "반품 상태 점검"), date=(1, "내일"), time=(2, "오후 1시 반"), cat=(1, "반품"),
      at="2028-02-11T13:30:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(39, M, "missing_date_polite", ["오전 9시 30분에 입고 라인 정비 등록해 주세요", "2028년 3월 22일로 해 주세요"], WE,
      title=(1, "입고 라인 정비"), date=(2, "2028년 3월 22일"), time=(1, "오전 9시 30분"), cat=(1, "입고"),
      at="2028-03-22T09:30:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(40, M, "missing_date_polite", ["오후 5시에 직원 고충 상담 넣어 주세요", "모레 일정으로 넣어 주세요"], EM,
      title=(1, "직원 고충 상담"), date=(2, "모레"), time=(1, "오후 5시"), cat=None,
      at="2028-04-06T17:00:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(41, M, "missing_date_polite", ["오후 2시에 재고 회계 대사 잡아 주세요", "7월 3일로 할게요"], ME,
      title=(1, "재고 회계 대사"), date=(2, "7월 3일"), time=(1, "오후 2시"), cat=(1, "재고"),
      at="2028-07-03T14:00:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(42, M, "missing_date_polite", ["오전 10시 반에 출고 마감 리허설 등록해 주세요", "글피로 해 주세요"], WK,
      title=(1, "출고 마감 리허설"), date=(2, "글피"), time=(1, "오전 10시 반"), cat=(1, "출고"),
      at="2028-05-16T10:30:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(43, M, "missing_title_polite", ["내일 오후 4시에 하나 등록해 주세요", "협력사 실적 평가 회의예요"], WE,
      title=(2, "협력사 실적 평가 회의"), date=(1, "내일"), time=(1, "오후 4시"), cat=None,
      at="2028-03-16T16:00:00+09:00", missing=miss(("task_or_title", 1, 2)), expectations=[ask("task_or_title_unclear"), *OK]),
    S(44, M, "missing_title_polite", ["2028년 4월 9일 오전 10시에 일정 넣어 주실래요", "입고 지연 원인 분석이요"], EM,
      title=(2, "입고 지연 원인 분석"), date=(1, "2028년 4월 9일"), time=(1, "오전 10시"), cat=(2, "입고"),
      at="2028-04-09T10:00:00+09:00", missing=miss(("task_or_title", 1, 2)), expectations=[ask("task_or_title_unclear"), *OK]),
    S(45, M, "missing_title_polite", ["모레 오후 3시에 그거 잡아 주세요", "반품 회수 경로 점검이요"], ME,
      title=(2, "반품 회수 경로 점검"), date=(1, "모레"), time=(1, "오후 3시"), cat=(2, "반품"),
      at="2028-07-02T15:00:00+09:00", missing=miss(("task_or_title", 1, 2)), expectations=[ask("task_or_title_unclear"), *OK]),
    S(46, M, "missing_ampm_variants", ["2028년 5월 21일 6시에 출고 사전 회의 등록해줘", "오후 6시요"], WK,
      title=(1, "출고 사전 회의"), date=(1, "2028년 5월 21일"), time=(2, "오후 6시"), cat=(1, "출고"),
      at="2028-05-21T18:00:00+09:00", missing=miss(("ampm", 1, 2)),
      target_overrides={1: {"time": E([(1, "6시")])}}, expectations=[ask("ampm"), *OK]),
    S(47, M, "missing_ampm_variants", ["내일 9시에 재고 이동 승인 회의 넣어줘", "아침 9시"], LD,
      title=(1, "재고 이동 승인 회의"), date=(1, "내일"), time=(2, "아침 9시"), cat=(1, "재고"),
      at="2028-03-01T09:00:00+09:00", missing=miss(("ampm", 1, 2)),
      target_overrides={1: {"time": E([(1, "9시")])}}, expectations=[ask("ampm"), *OK]),

    # ---- Corrections: 20 ----
    S(48, C, "corr_time_rephrase", ["내일 오전 9시가 아니라 오후 2시에 협력사 회의 등록해줘"], WM,
      title=(1, "협력사 회의"), date=(1, "내일"), cat=None,
      time=E([(1, "오후 2시")], superseded=[(1, "오전 9시")], markers=[(1, "아니라")]), at="2028-02-11T14:00:00+09:00"),
    S(49, C, "corr_time_rephrase", ["2028년 3월 23일 오후 3시 말고 오후 4시 반으로 입고 마감 회의 잡아 주세요"], WE,
      title=(1, "입고 마감 회의"), date=(1, "2028년 3월 23일"), cat=(1, "입고"),
      time=E([(1, "오후 4시 반")], superseded=[(1, "오후 3시")], markers=[(1, "말고")]), at="2028-03-23T16:30:00+09:00"),
    S(50, C, "corr_time_rephrase", ["모레 오전 10시, 아니 오전 11시에 출고 자재 점검 넣어줘"], EM,
      title=(1, "출고 자재 점검"), date=(1, "모레"), cat=(1, "출고"),
      time=E([(1, "오전 11시")], superseded=[(1, "오전 10시")], markers=[(1, "아니")]), at="2028-04-06T11:00:00+09:00"),
    S(51, C, "corr_time_rephrase", ["2028년 5월 19일 15시 대신 17시로 재고 조정 회의 등록해 주세요"], WK,
      title=(1, "재고 조정 회의"), date=(1, "2028년 5월 19일"), cat=(1, "재고"),
      time=E([(1, "17시")], superseded=[(1, "15시")], markers=[(1, "대신")]), at="2028-05-19T17:00:00+09:00"),
    S(52, C, "corr_time_rephrase", ["글피 오후 1시 아니고 오후 2시에 반품 협의 넣어줘"], LD,
      title=(1, "반품 협의"), date=(1, "글피"), cat=(1, "반품"),
      time=E([(1, "오후 2시")], superseded=[(1, "오후 1시")], markers=[(1, "아니고")]), at="2028-03-03T14:00:00+09:00"),
    S(53, C, "corr_date_rephrase", ["7월 4일이 아니라 7월 5일 오전 10시에 신입 교육 등록해 주세요"], ME,
      title=(1, "신입 교육"), time=(1, "오전 10시"), cat=None,
      date=E([(1, "7월 5일")], superseded=[(1, "7월 4일")], markers=[(1, "아니라")]), at="2028-07-05T10:00:00+09:00"),
    S(54, C, "corr_date_rephrase", ["모레 말고 글피 오전 9시에 입고 신년 회의 넣어줘"], YE,
      title=(1, "입고 신년 회의"), time=(1, "오전 9시"), cat=(1, "입고"),
      date=E([(1, "글피")], superseded=[(1, "모레")], markers=[(1, "말고")]), at="2029-01-03T09:00:00+09:00"),
    S(55, C, "corr_date_rephrase", ["2028년 2월 20일 대신 2028년 2월 21일 오후 3시에 재고 보고서 검토 등록해줘"], WM,
      title=(1, "재고 보고서 검토"), time=(1, "오후 3시"), cat=(1, "재고"),
      date=E([(1, "2028년 2월 21일")], superseded=[(1, "2028년 2월 20일")], markers=[(1, "대신")]), at="2028-02-21T15:00:00+09:00"),
    S(56, C, "corr_date_rephrase", ["내일 아니고 이번 주 금요일 오후 2시에 출고 협력 회의 잡아줘"], WE,
      title=(1, "출고 협력 회의"), time=(1, "오후 2시"), cat=(1, "출고"),
      date=E([(1, "이번 주 금요일")], superseded=[(1, "내일")], markers=[(1, "아니고")]), at="2028-03-17T14:00:00+09:00"),
    S(57, C, "corr_title_rephrase", ["안전 교육이 아니라 장비 교육을 2028년 4월 10일 오후 2시에 등록해 주세요"], EM,
      date=(1, "2028년 4월 10일"), time=(1, "오후 2시"), cat=None,
      title=E([(1, "장비 교육")], superseded=[(1, "안전 교육")], markers=[(1, "아니라")]), at="2028-04-10T14:00:00+09:00"),
    S(58, C, "corr_title_rephrase", ["회의실 예약 말고 팀 회식을 2028년 5월 26일 오후 6시에 넣어줘"], WK,
      date=(1, "2028년 5월 26일"), time=(1, "오후 6시"), cat=None,
      title=E([(1, "팀 회식")], superseded=[(1, "회의실 예약")], markers=[(1, "말고")]), at="2028-05-26T18:00:00+09:00"),
    S(59, C, "corr_title_rephrase", ["업체 미팅 아니고 업체 정산을 내일 오전 11시에 잡아줘"], LD,
      date=(1, "내일"), time=(1, "오전 11시"), cat=None,
      title=E([(1, "업체 정산")], superseded=[(1, "업체 미팅")], markers=[(1, "아니고")]), at="2028-03-01T11:00:00+09:00"),
    S(60, C, "corr_cue_mid", ["주간 입고 점검 말고 주간 출고 점검을 내일 오후 3시에 등록해줘"], ME,
      date=(1, "내일"), time=(1, "오후 3시"),
      title=E([(1, "주간 출고 점검")], superseded=[(1, "주간 입고 점검")], markers=[(1, "말고")]),
      cat=E([(1, "출고")], superseded=[(1, "입고")], markers=[(1, "말고")]), at="2028-07-01T15:00:00+09:00"),
    S(61, C, "corr_cue_mid", ["긴급 재고 조사 아니라 긴급 반품 조사를 모레 오전 10시에 넣어 주세요"], WE,
      date=(1, "모레"), time=(1, "오전 10시"),
      title=E([(1, "긴급 반품 조사")], superseded=[(1, "긴급 재고 조사")], markers=[(1, "아니라")]),
      cat=E([(1, "반품")], superseded=[(1, "재고")], markers=[(1, "아니라")]), at="2028-03-17T10:00:00+09:00"),
    S(62, C, "corr_cue_mid", ["야간 반품 정리 대신 야간 입고 정리를 2028년 4월 11일 오후 8시에 등록해줘"], EM,
      date=(1, "2028년 4월 11일"), time=(1, "오후 8시"),
      title=E([(1, "야간 입고 정리")], superseded=[(1, "야간 반품 정리")], markers=[(1, "대신")]),
      cat=E([(1, "입고")], superseded=[(1, "반품")], markers=[(1, "대신")]), at="2028-04-11T20:00:00+09:00"),
    S(63, C, "marker_after_adopted_only", ["내일 오후 5시에 장비 반납 확인 넣어줘, 4시는 아니고"], WK,
      title=(1, "장비 반납 확인"), date=(1, "내일"), time=(1, "오후 5시"), cat=None,
      at="2028-05-14T17:00:00+09:00", correction_provenance=AFTER),
    S(64, C, "marker_after_adopted_only", ["2029년 1월 8일 오전 10시에 재고 시스템 점검 등록해 주세요, 9시는 말고요"], YE,
      title=(1, "재고 시스템 점검"), date=(1, "2029년 1월 8일"), time=(1, "오전 10시"), cat=(1, "재고"),
      at="2029-01-08T10:00:00+09:00", correction_provenance=AFTER),
    S(65, C, "pending_cross_turn_adopted_only", ["모레 오후 2시로 하나 넣어 주세요", "출고 품질 회의고요, 음 오후 3시가 낫겠어요"], WM,
      title=(2, "출고 품질 회의"), date=(1, "모레"), time=(2, "오후 3시"), cat=(2, "출고"),
      at="2028-02-12T15:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      target_overrides={1: {"time": E([(1, "오후 2시")])}}, correction_provenance=CROSS,
      expectations=[ask("task_or_title_unclear"), *OK]),
    S(66, C, "pending_cross_turn_adopted_only", ["2028년 4월 13일 오전 10시에 하나 잡아줘", "협력사 감사 준비야, 근데 2028년 4월 14일로"], EM,
      title=(2, "협력사 감사 준비"), date=(2, "2028년 4월 14일"), time=(1, "오전 10시"), cat=None,
      at="2028-04-14T10:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      target_overrides={1: {"date": E([(1, "2028년 4월 13일")])}}, correction_provenance=CROSS,
      expectations=[ask("task_or_title_unclear"), *OK]),
    S(67, C, "pending_cross_turn_adopted_only", ["내일 오전 9시 반으로 일정 하나 잡아 둬", "입고 서류 감사고, 시간은 오전 11시로"], ME,
      title=(2, "입고 서류 감사"), date=(1, "내일"), time=(2, "오전 11시"), cat=(2, "입고"),
      at="2028-07-01T11:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      target_overrides={1: {"time": E([(1, "오전 9시 반")])}}, correction_provenance=CROSS,
      expectations=[ask("task_or_title_unclear"), *OK]),

    # ---- Time safety: 20 ----
    UP(68, ["오늘 오후 10시에 재고 백업 확인 등록해 주세요"], WE, family="untrusted_today_unresolved",
       title=(1, "재고 백업 확인"), date=(1, "오늘"), time=(1, "오후 10시"), cat=(1, "재고")),
    UP(69, ["오늘 오후 3시에 반품 통계 정리 넣어줘"], EM, family="untrusted_today_unresolved",
       title=(1, "반품 통계 정리"), date=(1, "오늘"), time=(1, "오후 3시"), cat=(1, "반품")),
    UP(70, ["오늘 오후 5시 반에 출고 비상 연락망 점검 잡아줘"], WK, family="untrusted_today_unresolved",
       title=(1, "출고 비상 연락망 점검"), date=(1, "오늘"), time=(1, "오후 5시 반"), cat=(1, "출고")),
    UP(71, ["오늘 오후 2시에 창고 온습도 기록 확인 등록해줘"], LD, family="untrusted_today_unresolved",
       title=(1, "창고 온습도 기록 확인"), date=(1, "오늘"), time=(1, "오후 2시"), cat=None),
    UP(72, ["7월 9일 오전 10시에 입고 협력사 평가 등록해줘"], ME, family="untrusted_yearless_unresolved",
       title=(1, "입고 협력사 평가"), date=(1, "7월 9일"), time=(1, "오전 10시"), cat=(1, "입고")),
    UP(73, ["3월 2일 오후 2시에 재고 위험 품목 점검 넣어 주세요"], WM, family="untrusted_yearless_unresolved",
       title=(1, "재고 위험 품목 점검"), date=(1, "3월 2일"), time=(1, "오후 2시"), cat=(1, "재고")),
    UP(74, ["5월 30일 오전 10시에 출고 목표 점검 잡아줘"], WK, family="untrusted_yearless_unresolved",
       title=(1, "출고 목표 점검"), date=(1, "5월 30일"), time=(1, "오전 10시"), cat=(1, "출고")),
    S(75, T, "untrusted_absolute_polite", ["2028년 4월 15일 오후 3시에 반품 협력사 회의 등록해 주세요"], EM,
      trusted=False, title=(1, "반품 협력사 회의"), date=(1, "2028년 4월 15일"), time=(1, "오후 3시"), cat=(1, "반품"),
      at="2028-04-15T15:00:00+09:00"),
    S(76, T, "untrusted_absolute_polite", ["2028년 5월 24일 오전 9시 15분에 입고 검수 기준 교육 넣어 주세요"], WK,
      trusted=False, title=(1, "입고 검수 기준 교육"), date=(1, "2028년 5월 24일"), time=(1, "오전 9시 15분"), cat=(1, "입고"),
      at="2028-05-24T09:15:00+09:00"),
    S(77, T, "untrusted_absolute_polite", ["2029년 1월 4일 14시에 재고 연간 실사 계획 등록해 주세요"], YE,
      trusted=False, title=(1, "재고 연간 실사 계획"), date=(1, "2029년 1월 4일"), time=(1, "14시"), cat=(1, "재고"),
      at="2029-01-04T14:00:00+09:00"),
    S(78, T, "past_time_variants", ["2028년 2월 9일 오후 3시에 출고 실적 보고 등록해줘"], WM,
      title=(1, "출고 실적 보고"), date=(1, "2028년 2월 9일"), time=(1, "오후 3시"), cat=(1, "출고"),
      at="2028-02-09T15:00:00+09:00", final_status="schedule_rejected", expectations=PAST),
    S(79, T, "past_time_variants", ["오늘 오전 5시 30분에 조기 입고 확인 넣어줘"], EM,
      title=(1, "조기 입고 확인"), date=(1, "오늘"), time=(1, "오전 5시 30분"), cat=(1, "입고"),
      at="2028-04-04T05:30:00+09:00", final_status="schedule_rejected", expectations=PAST),
    S(80, T, "past_time_variants", ["오늘 오후 7시에 연말 행사 정리 등록해줘"], YE,
      title=(1, "연말 행사 정리"), date=(1, "오늘"), time=(1, "오후 7시"), cat=None,
      at="2028-12-31T19:00:00+09:00", final_status="schedule_rejected", expectations=PAST),
    S(81, T, "noon_midnight_explicit", ["2028년 5월 23일 오후 12시 30분에 협력사 오찬 미팅 등록해줘"], WK,
      title=(1, "협력사 오찬 미팅"), date=(1, "2028년 5월 23일"), time=(1, "오후 12시 30분"), cat=None,
      at="2028-05-23T12:30:00+09:00"),
    S(82, T, "noon_midnight_explicit", ["2028년 3월 4일 0시에 전산 재고 마감 작업 넣어줘"], LD,
      title=(1, "전산 재고 마감 작업"), date=(1, "2028년 3월 4일"), time=(1, "0시"), cat=(1, "재고"),
      at="2028-03-04T00:00:00+09:00"),
    S(83, T, "noon_midnight_explicit", ["내일 오전 12시 15분에 서버 패치 작업 등록해줘"], ME,
      title=(1, "서버 패치 작업"), date=(1, "내일"), time=(1, "오전 12시 15분"), cat=None,
      at="2028-07-01T00:15:00+09:00"),
    S(84, T, "year_boundary_variants", ["글피 오전 9시 30분에 반품 신년 점검 넣어줘"], YE,
      title=(1, "반품 신년 점검"), date=(1, "글피"), time=(1, "오전 9시 30분"), cat=(1, "반품"),
      at="2029-01-03T09:30:00+09:00"),
    S(85, T, "year_boundary_variants", ["내일 오후 2시에 신년 입고 첫 검수 등록해 주세요"], YE,
      title=(1, "신년 입고 첫 검수"), date=(1, "내일"), time=(1, "오후 2시"), cat=(1, "입고"),
      at="2029-01-01T14:00:00+09:00"),
    S(86, T, "ambiguous_unresolved_polite", ["2028년 3월 25일 8시에 출고 정산 마감 등록해 주세요"], WE,
      title=(1, "출고 정산 마감"), date=(1, "2028년 3월 25일"), time=(1, "8시"), cat=(1, "출고"),
      at=None, missing=miss(("ampm", 1, None)), final_status="not_executed_pending_info", expectations=[ask("ampm")]),
    S(87, T, "ambiguous_unresolved_polite", ["모레 10시 반에 반품 데이터 정리 넣어줘"], EM,
      title=(1, "반품 데이터 정리"), date=(1, "모레"), time=(1, "10시 반"), cat=(1, "반품"),
      at=None, missing=miss(("ampm", 1, None)), final_status="not_executed_pending_info", expectations=[ask("ampm")]),

    # ---- Mixed unsupported + consent: 13 ----
    MX(88, "mixed_unsupported_first", ["알림도 꼭 설정해 주고, 내일 오후 2시에 입고 계획 회의 등록해줘", "그럼 알림 없이 일정만 등록해"], WE,
       title=(1, "입고 계획 회의"), date=(1, "내일"), time=(1, "오후 2시"), cat=(1, "입고"),
       at="2028-03-16T14:00:00+09:00", cue="알림도 꼭 설정해", feature="reminder", result="granted"),
    MX(89, "mixed_unsupported_first", ["문자로 보내 주시고 모레 오전 10시에 재고 정책 회의 넣어 주세요", "네, 문자 없이 넣어 주세요"], EM,
       title=(1, "재고 정책 회의"), date=(1, "모레"), time=(1, "오전 10시"), cat=(1, "재고"),
       at="2028-04-06T10:00:00+09:00", cue="문자로 보내", feature="other", result="granted"),
    MX(90, "mixed_unsupported_first", ["푸시도 켜 주고, 2028년 3월 6일 오후 4시에 출고 일정 협의 등록해줘", "푸시는 생략하고 등록해 줘"], LD,
       title=(1, "출고 일정 협의"), date=(1, "2028년 3월 6일"), time=(1, "오후 4시"), cat=(1, "출고"),
       at="2028-03-06T16:00:00+09:00", cue="푸시도 켜", feature="reminder", result="granted"),
    MX(91, "mixed_polite_grant", ["내일 오전 10시에 반품 처리 교육 등록하고 리마인더도 설정해 주세요", "리마인더 빼고 등록만 해줘"], WK,
       title=(1, "반품 처리 교육"), date=(1, "내일"), time=(1, "오전 10시"), cat=(1, "반품"),
       at="2028-05-14T10:00:00+09:00", cue="리마인더도 설정해", feature="reminder", result="granted"),
    MX(92, "mixed_polite_grant", ["2028년 7월 10일 오후 3시에 재고 담당자 회의 넣고 1시간 전에 알려 주세요", "알림 없이 넣어 주세요"], ME,
       title=(1, "재고 담당자 회의"), date=(1, "2028년 7월 10일"), time=(1, "오후 3시"), cat=(1, "재고"),
       at="2028-07-10T15:00:00+09:00", cue="1시간 전에 알려", feature="reminder", result="granted"),
    MX(93, "mixed_polite_grant", ["모레 오후 1시에 출고 신년 계획 확정 등록하고 미리 알려 주세요", "좋아요, 그냥 등록해 주세요"], YE,
       title=(1, "출고 신년 계획 확정"), date=(1, "모레"), time=(1, "오후 1시"), cat=(1, "출고"),
       at="2029-01-02T13:00:00+09:00", cue="미리 알려", feature="reminder", result="granted"),
    MX(94, "mixed_decline_variants", ["내일 오후 3시에 입고 점검표 개정 회의 등록하고 알림도 보내줘", "아니, 취소할게"], WM,
       title=(1, "입고 점검표 개정 회의"), date=(1, "내일"), time=(1, "오후 3시"), cat=(1, "입고"),
       at="2028-02-11T15:00:00+09:00", cue="알림도 보내", feature="reminder", result="declined"),
    MX(95, "mixed_decline_variants", ["2028년 5월 27일 오전 11시에 재고 품질 회의 넣고 문자도 보내 주세요", "괜찮아요, 등록하지 마세요"], WK,
       title=(1, "재고 품질 회의"), date=(1, "2028년 5월 27일"), time=(1, "오전 11시"), cat=(1, "재고"),
       at="2028-05-27T11:00:00+09:00", cue="문자도 보내", feature="other", result="declined"),
    MX(96, "mixed_decline_variants", ["모레 오후 5시에 반품 비용 정산 등록하고 푸시도 보내줘", "그만, 하지 마"], LD,
       title=(1, "반품 비용 정산"), date=(1, "모레"), time=(1, "오후 5시"), cat=(1, "반품"),
       at="2028-03-02T17:00:00+09:00", cue="푸시도 보내", feature="reminder", result="declined"),
    MX(97, "mixed_pending_variants", ["리마인더도 설정해 주고 2028년 3월 24일 오전 10시에 협력사 보안 교육 등록해 주세요"], WE,
       title=(1, "협력사 보안 교육"), date=(1, "2028년 3월 24일"), time=(1, "오전 10시"), cat=None,
       at="2028-03-24T10:00:00+09:00", cue="리마인더도 설정해", feature="reminder", result="pending"),
    MX(98, "mixed_pending_variants", ["글피 오후 2시에 출고 장비 점검 넣고 30분 전에 알려줘"], EM,
       title=(1, "출고 장비 점검"), date=(1, "글피"), time=(1, "오후 2시"), cat=(1, "출고"),
       at="2028-04-07T14:00:00+09:00", cue="30분 전에 알려", feature="reminder", result="pending"),
    MX(99, "mixed_pending_variants", ["내일 오전 9시에 입고 협력 미팅 등록하고 문자로 보내 주세요"], ME,
       title=(1, "입고 협력 미팅"), date=(1, "내일"), time=(1, "오전 9시"), cat=(1, "입고"),
       at="2028-07-01T09:00:00+09:00", cue="문자로 보내", feature="other", result="pending"),
    MX(100, "mixed_pending_variants", ["2029년 1월 7일 오후 3시에 재고 신규 코드 교육 넣어 주시고 알림도 켜 주세요"], YE,
       title=(1, "재고 신규 코드 교육"), date=(1, "2029년 1월 7일"), time=(1, "오후 3시"), cat=(1, "재고"),
       at="2029-01-07T15:00:00+09:00", cue="알림도 켜", feature="reminder", result="pending"),
]


_YEARLESS_DATE = re.compile(r"(\d{4}년\s*)?\d{1,2}월\s*\d{1,2}일")


def _fix_tags(case: dict[str, Any], specification: dict[str, Any]) -> dict[str, Any]:
    """Batch tag fixes; relative_time looks only at adopted date evidence (064 follow-up)."""
    tags = case["meta"]["scenarioTags"]
    if len(specification["turns"]) == 1 and "follow_up_answer" in tags:
        tags.remove("follow_up_answer")
    sources = [specification["dateEvidence"] or {}]
    sources += [o["date"] for o in specification["targetOverrides"].values() if o.get("date")]
    if "relative_time" not in tags and any(
        match.group(1) is None
        for source in sources
        for span in source.get("adoptedSpans", [])
        for match in _YEARLESS_DATE.finditer(span["text"])
    ):
        tags.append("relative_time")
    return case


def authoring_errors(specifications: list[dict[str, Any]]) -> list[str]:
    errors = list(batch_quality_errors(specifications))
    earlier = {item["titleEvidence"]["adoptedSpans"][-1]["text"] for item in [*PILOT_SPECS, *B1_SPECS]}
    for item in specifications:
        title = item["titleEvidence"]["adoptedSpans"][-1]["text"]
        if title in earlier:
            errors.append(f"{item['caseId']}: title {title!r} reused from pilot or batch 1")
        if item["unsupported"] is not None and len(item["turns"]) > 1:
            outcome = item["expectations"][1]["outcome"]
            want = {"success": "granted", "cancelled": "declined"}[outcome]
            got = gate_v3.classify_consent_answer(item["turns"][1]["text"])
            if got != want:
                errors.append(f"{item['caseId']}: consent reply classified {got!r}, oracle needs {want!r}")
    for family, count in sorted(Counter(item["family"] for item in specifications).items()):
        if count > MAX_PER_FAMILY:
            errors.append(f"FAMILY_OVER_LIMIT: {family} {count} > {MAX_PER_FAMILY}")
    return errors


def main() -> None:
    check_specs(SPECS)
    errors = authoring_errors(SPECS)
    if errors:
        raise RuntimeError("; ".join(errors))
    cases = [_fix_tags(make_case(item), item) for item in SPECS]
    if len(cases) != 100 or len({case["meta"]["caseId"] for case in cases}) != 100:
        raise RuntimeError("batch 2 must contain 100 unique cases")
    OUTPUT.write_text("\n".join(canonical(case) for case in cases) + "\n", encoding="utf-8")
    targets = [
        {"caseId": s["caseId"], "turn": t, "target": make_candidate_target(s, t), "status": "PROPOSED_UNREVIEWED"}
        for s in SPECS for t in range(1, len(s["turns"]) + 1)
    ]
    TARGETS.write_text("\n".join(canonical(row) for row in targets) + "\n", encoding="utf-8")
    print(canonical({"status": "PROPOSED_UNREVIEWED", "caseCount": len(cases), "sha256": sha256(OUTPUT),
                     "targetCount": len(targets), "targetsSha256": sha256(TARGETS)}))


if __name__ == "__main__":
    main()
