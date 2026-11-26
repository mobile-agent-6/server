"""Build Candidate-C create batch 3 (170 new training candidates) deterministically.

Status: PROPOSED_UNREVIEWED / BE-PROPOSED.  Adds the skill run 002 stopped on
(lk09: a vague placeholder such as "그거 하나" must not become a title), so
title-unclear cases go from 15 to 41 across the 400.  Also covers the review
bias notes: category cue late in the title, no "신년", fewer date-first
utterances, untrusted follow-ups whose answer differs from the server date.

scheduledAt is computed from the final adopted date/time spans with
spec_checks.computed_scheduled_at (AUTO); the validator then checks it against
the production Decision stage.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "pilot"))
sys.path.insert(0, str(ROOT / "batch1"))
sys.path.insert(0, str(ROOT / "batch2"))
sys.path.insert(0, str(ROOT.parent / "structured_output_contract_v0.1"))

from build_batch1 import (  # noqa: E402
    AFTER, ASK_INFO, CONSENT_Q, CROSS, DECLINED, GRANTED_OK, OK, PAST,
    C, D, M, T, X, EM, LD, ME, WE, WK, WM, YE,
    SPECS as B1_SPECS, _category, _ev, ask, miss,
)
from build_batch2 import SPECS as B2_SPECS, _fix_tags  # noqa: E402
from build_pilot import (  # noqa: E402
    SPECS as PILOT_SPECS, canonical, evidence, make_candidate_target, make_case, sha256, spec, turn,
)
from spec_checks import batch_quality_errors, check_specs, computed_scheduled_at  # noqa: E402
import common_gate_v0_3 as gate_v3  # noqa: E402

OUTPUT = HERE / "conversations-v0.1.jsonl"
TARGETS = HERE / "candidate-c-targets-v0.1.jsonl"
CASE_COUNT = 170
MAX_PER_FAMILY = 5
UNTRUSTED = "absolute_datetime_due_to_untrusted_time"
TITLE_ASK = ask("task_or_title_unclear")
AUTO = "auto"
E = evidence


def S(number: int, bucket: str, family: str, texts: list[str], ref: str, *,
      title: Any, date: Any, time: Any, cat: Any, at: str | None = AUTO,
      expectations: list[dict[str, Any]] = OK, **kwargs: Any) -> dict[str, Any]:
    turns = [turn(text) if index == 0 else turn(text, answer=index) for index, text in enumerate(texts)]
    item = spec(
        number, bucket, family, turns, reference=ref,
        title=_ev(title), date=_ev(date), time=_ev(time), category_evidence=_ev(cat),
        scheduled_at=None if at == AUTO else at, category=_category(cat), expectations=expectations,
        batch="b3", dataset_role="training_candidate", authored_on="2026-10-28", **kwargs,
    )
    if at == AUTO:
        item["scheduledAt"] = computed_scheduled_at(item)
    return item


def TU(number: int, family: str, texts: list[str], ref: str, *, title: str, date: Any, time: Any,
       cat: str | None = None) -> dict[str, Any]:
    """Title unclear on turn 1 (placeholder such as 그거/하나), title answered on turn 2."""
    return S(number, M, family, texts, ref, title=(2, title), date=date, time=time,
             cat=None if cat is None else (2, cat),
             missing=miss(("task_or_title", 1, 2)), expectations=[TITLE_ASK, *OK])


def UF(number, family, texts, ref, *, title, cat, t1_date, t1_time, date, time):
    """Untrusted server time: ask for an absolute date, then resolve on turn 2."""
    overrides = {"date": E([(1, t1_date)])}
    if t1_time is not None:
        overrides["time"] = E([(1, t1_time)])
    return S(number, M, family, texts, ref, trusted=False, title=title, date=date, time=time, cat=cat,
             missing=miss((UNTRUSTED, 1, 2)), target_overrides={1: overrides},
             expectations=[ask("absolute_datetime_required"), *OK])


def UP(number, texts, ref, *, title, date, time, cat, family):
    """Untrusted server time with a relative or yearless date and no follow-up yet."""
    return S(number, T, family, texts, ref, trusted=False, title=title, date=date, time=time, cat=cat,
             at=None, missing=miss((UNTRUSTED, 1, None)), final_status="not_executed_pending_info",
             expectations=[ask("absolute_datetime_required")])


def MX(number, family, texts, ref, *, title, date, time, cat, cue, feature, result):
    if result == "pending":
        return S(number, X, family, texts, ref, title=title, date=date, time=time, cat=cat,
                 missing=miss(("user_consent", 1, None)), unsupported=(1, cue, feature),
                 final_status="not_executed_pending_info", expectations=[CONSENT_Q])
    declined = result == "declined"
    return S(number, X, family, texts, ref, title=title, date=date, time=time, cat=cat,
             missing=miss(("user_consent", 1, 2)), unsupported=(1, cue, feature),
             final_status="rejected_by_user" if declined else "success",
             expectations=[CONSENT_Q, DECLINED if declined else GRANTED_OK])


def AMB(number, family, texts, ref, *, title, date, time, cat):
    return S(number, T, family, texts, ref, title=title, date=date, time=time, cat=cat, at=None,
             missing=miss(("ampm", 1, None)), final_status="not_executed_pending_info",
             expectations=[ask("ampm")])


def CROSS_TITLE(number, texts, ref, *, title, date, time, cat, t1):
    return S(number, C, "pending_cross_turn_title_unclear", texts, ref, title=(2, title), date=date, time=time,
             cat=None if cat is None else (2, cat), missing=miss(("task_or_title", 1, 2)),
             target_overrides={1: t1}, correction_provenance=CROSS, expectations=[TITLE_ASK, *OK])


SPECS = [
    # ---- Direct create: 34 ----
    S(1, D, "direct_cue_late", ["협력사 납품분 입고 확인, 내일 오후 3시에 넣어줘"], WE,
      title=(1, "협력사 납품분 입고 확인"), date=(1, "내일"), time=(1, "오후 3시"), cat=(1, "입고")),
    S(2, D, "direct_cue_late", ["사무용품 재고 정리는 모레 오전 10시로 해줘"], EM,
      title=(1, "사무용품 재고 정리"), date=(1, "모레"), time=(1, "오전 10시"), cat=(1, "재고")),
    S(3, D, "direct_cue_late", ["거래처 샘플 반품 회수 건, 2028년 5월 18일 오후 2시에 등록해줘"], WK,
      title=(1, "거래처 샘플 반품 회수"), date=(1, "2028년 5월 18일"), time=(1, "오후 2시"), cat=(1, "반품")),
    S(4, D, "direct_cue_late", ["택배사 출고 마감 조율 내일 오후 4시에 잡아줘"], LD,
      title=(1, "택배사 출고 마감 조율"), date=(1, "내일"), time=(1, "오후 4시"), cat=(1, "출고")),
    S(5, D, "direct_cue_late", ["의류 포장 검수 7월 3일 오전 10시에 넣어줘"], ME,
      title=(1, "의류 포장 검수"), date=(1, "7월 3일"), time=(1, "오전 10시"), cat=(1, "포장")),
    S(6, D, "direct_filler_start", ["저기, 내일 오후 2시에 전동 지게차 정기 점검 넣어줘"], WM,
      title=(1, "전동 지게차 정기 점검"), date=(1, "내일"), time=(1, "오후 2시"), cat=None),
    S(7, D, "direct_filler_start", ["음 2029년 1월 3일 오전 10시에 연초 업무 계획 회의 등록해줘"], YE,
      title=(1, "연초 업무 계획 회의"), date=(1, "2029년 1월 3일"), time=(1, "오전 10시"), cat=None),
    S(8, D, "direct_filler_start", ["혹시 모레 오후 4시에 냉장 재고 실사 넣어 줄 수 있어?"], WE,
      title=(1, "냉장 재고 실사"), date=(1, "모레"), time=(1, "오후 4시"), cat=(1, "재고")),
    S(9, D, "direct_filler_start", ["아 맞다, 글피 오전 11시에 반품 바코드 라벨 교체 잡아줘"], EM,
      title=(1, "반품 바코드 라벨 교체"), date=(1, "글피"), time=(1, "오전 11시"), cat=(1, "반품")),
    S(10, D, "direct_filler_start", ["그리고 7월 6일 오후 3시에 출고 차량 배차 회의 넣어줘"], ME,
      title=(1, "출고 차량 배차 회의"), date=(1, "7월 6일"), time=(1, "오후 3시"), cat=(1, "출고")),
    S(11, D, "direct_title_first_polite", ["창고 소방 설비 점검은 이번 주 일요일 오후 2시로 등록해 주세요"], WK,
      title=(1, "창고 소방 설비 점검"), date=(1, "이번 주 일요일"), time=(1, "오후 2시"), cat=None),
    S(12, D, "direct_title_first_polite", ["물류 센터 견학 안내를 2028년 3월 8일 오전 10시에 넣어 주세요"], LD,
      title=(1, "물류 센터 견학 안내"), date=(1, "2028년 3월 8일"), time=(1, "오전 10시"), cat=None),
    S(13, D, "direct_title_first_polite", ["분기 피킹 정확도 리뷰, 2028년 2월 17일 오후 3시에 잡아 주세요"], WM,
      title=(1, "분기 피킹 정확도 리뷰"), date=(1, "2028년 2월 17일"), time=(1, "오후 3시"), cat=(1, "피킹")),
    S(14, D, "direct_title_first_polite", ["연간 거래처 평가 보고는 2029년 1월 5일 오후 2시에 등록해 주세요"], YE,
      title=(1, "연간 거래처 평가 보고"), date=(1, "2029년 1월 5일"), time=(1, "오후 2시"), cat=None),
    S(15, D, "direct_title_first_polite", ["신규 직원 안전 오리엔테이션, 이번 주 금요일 오전 9시에 넣어 주세요"], WE,
      title=(1, "신규 직원 안전 오리엔테이션"), date=(1, "이번 주 금요일"), time=(1, "오전 9시"), cat=None),
    S(16, D, "direct_weekday_mix", ["이번 주 토요일 오전 10시에 창고 대청소 등록해줘"], WM,
      title=(1, "창고 대청소"), date=(1, "이번 주 토요일"), time=(1, "오전 10시"), cat=None),
    S(17, D, "direct_weekday_mix", ["이번 주 목요일 오후 1시에 박스 자재 발주 회의 넣어줘"], EM,
      title=(1, "박스 자재 발주 회의"), date=(1, "이번 주 목요일"), time=(1, "오후 1시"), cat=None),
    S(18, D, "direct_weekday_mix", ["이번 주 금요일 오후 5시에 운영팀 주간 회고 잡아줘"], LD,
      title=(1, "운영팀 주간 회고"), date=(1, "이번 주 금요일"), time=(1, "오후 5시"), cat=None),
    S(19, D, "direct_weekday_mix", ["이번 주 토요일 오전 11시에 하역장 바닥 보수 등록해줘"], WE,
      title=(1, "하역장 바닥 보수"), date=(1, "이번 주 토요일"), time=(1, "오전 11시"), cat=None),
    S(20, D, "direct_weekday_mix", ["이번 주 일요일 오후 1시에 재고 시스템 백업 점검 넣어줘"], ME,
      title=(1, "재고 시스템 백업 점검"), date=(1, "이번 주 일요일"), time=(1, "오후 1시"), cat=(1, "재고")),
    S(21, D, "direct_minutes_b3", ["2028년 5월 16일 오전 8시 40분에 주간 출고 조회 미팅 넣어줘"], WK,
      title=(1, "주간 출고 조회 미팅"), date=(1, "2028년 5월 16일"), time=(1, "오전 8시 40분"), cat=(1, "출고")),
    S(22, D, "direct_minutes_b3", ["내일 오후 4시 15분에 협력사 단가 협상 등록해줘"], LD,
      title=(1, "협력사 단가 협상"), date=(1, "내일"), time=(1, "오후 4시 15분"), cat=None),
    S(23, D, "direct_minutes_b3", ["모레 오전 11시 45분에 고객 반품 사유 분석 잡아줘"], WM,
      title=(1, "고객 반품 사유 분석"), date=(1, "모레"), time=(1, "오전 11시 45분"), cat=(1, "반품")),
    S(24, D, "direct_minutes_b3", ["7월 4일 오후 2시 10분에 월간 재고 회전율 점검 넣어 주세요"], ME,
      title=(1, "월간 재고 회전율 점검"), date=(1, "7월 4일"), time=(1, "오후 2시 10분"), cat=(1, "재고")),
    S(25, D, "direct_minutes_b3", ["글피 오후 3시 50분에 컨베이어 정비 등록해줘"], EM,
      title=(1, "컨베이어 정비"), date=(1, "글피"), time=(1, "오후 3시 50분"), cat=None),
    S(26, D, "direct_24h_b3", ["2028년 3월 21일 15시 30분에 협력사 입고 검품 교육 넣어줘"], WE,
      title=(1, "협력사 입고 검품 교육"), date=(1, "2028년 3월 21일"), time=(1, "15시 30분"), cat=(1, "입고")),
    S(27, D, "direct_24h_b3", ["내일 16시에 창고 피킹 동선 개선 회의 잡아줘"], WK,
      title=(1, "창고 피킹 동선 개선 회의"), date=(1, "내일"), time=(1, "16시"), cat=(1, "피킹")),
    S(28, D, "direct_24h_b3", ["2029년 1월 9일 13시 20분에 보안 카드 갱신 등록해줘"], YE,
      title=(1, "보안 카드 갱신"), date=(1, "2029년 1월 9일"), time=(1, "13시 20분"), cat=None),
    S(29, D, "direct_24h_b3", ["모레 18시에 야간 출고 인수인계 넣어줘"], LD,
      title=(1, "야간 출고 인수인계"), date=(1, "모레"), time=(1, "18시"), cat=(1, "출고")),
    S(30, D, "direct_24h_b3", ["7월 10일 14시 40분에 분기 재고 차이 원인 회의 등록해 주세요"], ME,
      title=(1, "분기 재고 차이 원인 회의"), date=(1, "7월 10일"), time=(1, "14시 40분"), cat=(1, "재고")),
    S(31, D, "direct_yearless_b3", ["2월 23일 오후 3시에 거래처 신용 점검 넣어줘"], WM,
      title=(1, "거래처 신용 점검"), date=(1, "2월 23일"), time=(1, "오후 3시"), cat=None),
    S(32, D, "direct_yearless_b3", ["4월 18일 오전 10시에 고객 반품 창구 운영 회의 잡아 주세요"], EM,
      title=(1, "고객 반품 창구 운영 회의"), date=(1, "4월 18일"), time=(1, "오전 10시"), cat=(1, "반품")),
    S(33, D, "direct_yearless_b3", ["5월 25일 오후 1시 반에 출고 협력사 간담회 등록해줘"], WK,
      title=(1, "출고 협력사 간담회"), date=(1, "5월 25일"), time=(1, "오후 1시 반"), cat=(1, "출고")),
    S(34, D, "direct_yearless_b3", ["3월 14일 오후 2시에 창고 임대 계약 검토 넣어줘"], LD,
      title=(1, "창고 임대 계약 검토"), date=(1, "3월 14일"), time=(1, "오후 2시"), cat=None),

    # ---- Missing information and follow-up: 50 (22 title unclear) ----
    TU(35, "title_unclear_pronoun", ["내일 오후 2시에 그것 좀 넣어줘", "협력사 납기 회의야"], WE,
       title="협력사 납기 회의", date=(1, "내일"), time=(1, "오후 2시")),
    TU(36, "title_unclear_pronoun", ["이번 주 일요일 오전 10시에 이거 등록해줘", "창고 조명 교체 작업이요"], WK,
       title="창고 조명 교체 작업", date=(1, "이번 주 일요일"), time=(1, "오전 10시")),
    TU(37, "title_unclear_pronoun", ["2028년 3월 9일 오후 4시에 그 일 잡아 줘", "재고 위치 표지 재부착이야"], LD,
       title="재고 위치 표지 재부착", date=(1, "2028년 3월 9일"), time=(1, "오후 4시"), cat="재고"),
    TU(38, "title_unclear_pronoun", ["저번에 말한 그 건, 7월 5일 오후 1시에 넣어줘", "반품 운송 계약 갱신 건이요"], ME,
       title="반품 운송 계약 갱신", date=(1, "7월 5일"), time=(1, "오후 1시"), cat="반품"),
    TU(39, "title_unclear_pronoun", ["모레 오후 5시에 그거 등록 좀", "출고 검수 체크리스트 정리"], EM,
       title="출고 검수 체크리스트 정리", date=(1, "모레"), time=(1, "오후 5시"), cat="출고"),
    TU(40, "title_unclear_count", ["내일 오전 10시 반에 뭐 하나 넣어줘", "인사팀 면담이야"], WM,
       title="인사팀 면담", date=(1, "내일"), time=(1, "오전 10시 반")),
    TU(41, "title_unclear_count", ["2029년 1월 4일 오후 3시에 스케줄 하나 잡아줘", "연초 재고 조정 회의요"], YE,
       title="연초 재고 조정 회의", date=(1, "2029년 1월 4일"), time=(1, "오후 3시"), cat="재고"),
    TU(42, "title_unclear_count", ["글피 오전 11시에 할 일 하나 등록해줘", "포장 라인 속도 점검이야"], WE,
       title="포장 라인 속도 점검", date=(1, "글피"), time=(1, "오전 11시"), cat="포장"),
    TU(43, "title_unclear_count", ["2028년 5월 20일 오후 3시에 일정 하나만 넣어줘", "거래처 결제 조건 협의"], WK,
       title="거래처 결제 조건 협의", date=(1, "2028년 5월 20일"), time=(1, "오후 3시")),
    TU(44, "title_unclear_count", ["내일 오후 1시에 하나 넣어 둬", "입고 대기 차량 정리야"], LD,
       title="입고 대기 차량 정리", date=(1, "내일"), time=(1, "오후 1시"), cat="입고"),
    TU(45, "title_unclear_polite", ["모레 오전 9시에 그거 하나 등록해 주실래요", "반품 검수 기준 회의예요"], EM,
       title="반품 검수 기준 회의", date=(1, "모레"), time=(1, "오전 9시"), cat="반품"),
    TU(46, "title_unclear_polite", ["7월 7일 오후 4시에 일정 좀 잡아 주세요", "택배 출고 품질 점검이에요"], ME,
       title="택배 출고 품질 점검", date=(1, "7월 7일"), time=(1, "오후 4시"), cat="출고"),
    TU(47, "title_unclear_polite", ["2028년 2월 15일 오후 2시에 하나 넣어 주세요", "물류 보험 갱신 상담이요"], WM,
       title="물류 보험 갱신 상담", date=(1, "2028년 2월 15일"), time=(1, "오후 2시")),
    TU(48, "title_unclear_polite", ["2029년 1월 2일 오전 11시에 이것 좀 등록해 주세요", "연초 출고 목표 공유예요"], YE,
       title="연초 출고 목표 공유", date=(1, "2029년 1월 2일"), time=(1, "오전 11시"), cat="출고"),
    TU(49, "title_unclear_polite", ["내일 오후 5시에 그 건 넣어 주시겠어요", "재고 부족 품목 회의입니다"], WE,
       title="재고 부족 품목 회의", date=(1, "내일"), time=(1, "오후 5시"), cat="재고"),
    TU(50, "title_unclear_answer_phrase", ["2028년 5월 17일 오전 10시에 그거 넣어줘", "아, 피킹 카트 수리하는 거"], WK,
       title="피킹 카트 수리", date=(1, "2028년 5월 17일"), time=(1, "오전 10시"), cat="피킹"),
    TU(51, "title_unclear_answer_phrase", ["글피 오후 3시에 하나 잡아줘", "음 화장품 신규 거래처 미팅이요"], LD,
       title="화장품 신규 거래처 미팅", date=(1, "글피"), time=(1, "오후 3시")),
    TU(52, "title_unclear_answer_phrase", ["7월 12일 오전 9시 반에 그것 좀 넣어 줘", "입고 예정표 확정 건이야"], ME,
       title="입고 예정표 확정", date=(1, "7월 12일"), time=(1, "오전 9시 반"), cat="입고"),
    TU(53, "title_unclear_answer_phrase", ["4월 20일 오후 2시에 일정 하나 넣자", "창고 방역 작업이요"], EM,
       title="창고 방역 작업", date=(1, "4월 20일"), time=(1, "오후 2시")),
    TU(54, "title_unclear_time_first", ["오후 3시, 모레로 그거 하나 넣어줘", "협력사 반품 정산이요"], WE,
       title="협력사 반품 정산", date=(1, "모레"), time=(1, "오후 3시"), cat="반품"),
    TU(55, "title_unclear_time_first", ["오전 11시에 내일 뭐 좀 넣어 줘", "출고 지연 대응 회의야"], WM,
       title="출고 지연 대응 회의", date=(1, "내일"), time=(1, "오전 11시"), cat="출고"),
    TU(56, "title_unclear_time_first", ["오후 2시 반, 2029년 1월 6일로 하나 잡아 주세요", "연간 안전 교육 계획 수립이요"], YE,
       title="연간 안전 교육 계획 수립", date=(1, "2029년 1월 6일"), time=(1, "오후 2시 반")),
    S(57, M, "missing_time_b3", ["2028년 2월 18일 협력사 품질 개선 회의 넣어줘", "오후 4시"], WM,
      title=(1, "협력사 품질 개선 회의"), date=(1, "2028년 2월 18일"), time=(2, "오후 4시"), cat=None,
      missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(58, M, "missing_time_b3", ["반품 회수 차량 점검 모레로 넣어 줘", "아침 10시로 해 줘"], EM,
      title=(1, "반품 회수 차량 점검"), date=(1, "모레"), time=(2, "아침 10시"), cat=(1, "반품"),
      missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(59, M, "missing_time_b3", ["이번 주 일요일에 재고 진열 정리 등록해줘", "오후 3시 반"], WK,
      title=(1, "재고 진열 정리"), date=(1, "이번 주 일요일"), time=(2, "오후 3시 반"), cat=(1, "재고"),
      missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(60, M, "missing_time_b3", ["7월 8일에 출고 운임 정산 잡아줘", "14시"], ME,
      title=(1, "출고 운임 정산"), date=(1, "7월 8일"), time=(2, "14시"), cat=(1, "출고"),
      missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(61, M, "missing_time_polite_b3", ["2028년 3월 10일 거래처 설명회 등록해 주세요", "오후 2시로 해 주세요"], LD,
      title=(1, "거래처 설명회"), date=(1, "2028년 3월 10일"), time=(2, "오후 2시"), cat=None,
      missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(62, M, "missing_time_polite_b3", ["2029년 1월 5일 입고 담당 교대 회의 넣어 주세요", "오전 9시요"], YE,
      title=(1, "입고 담당 교대 회의"), date=(1, "2029년 1월 5일"), time=(2, "오전 9시"), cat=(1, "입고"),
      missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(63, M, "missing_time_polite_b3", ["모레 사내 물류 세미나 잡아 주세요", "오후 1시 반이 좋아요"], WE,
      title=(1, "사내 물류 세미나"), date=(1, "모레"), time=(2, "오후 1시 반"), cat=None,
      missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(64, M, "missing_date_b3", ["오후 3시에 박스 납품 업체 입찰 회의 넣어줘", "모레"], WM,
      title=(1, "박스 납품 업체 입찰 회의"), date=(2, "모레"), time=(1, "오후 3시"), cat=None,
      missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(65, M, "missing_date_b3", ["오전 11시에 반기 재고 평가 회의 등록해줘", "4월 11일로"], EM,
      title=(1, "반기 재고 평가 회의"), date=(2, "4월 11일"), time=(1, "오전 11시"), cat=(1, "재고"),
      missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(66, M, "missing_date_b3", ["오후 4시에 출고 기사 안전 교육 잡아줘", "내일로 해"], WK,
      title=(1, "출고 기사 안전 교육"), date=(2, "내일"), time=(1, "오후 4시"), cat=(1, "출고"),
      missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(67, M, "missing_date_b3", ["피킹 오류 개선 회의, 오전 10시에 넣어줘", "2028년 3월 7일"], LD,
      title=(1, "피킹 오류 개선 회의"), date=(2, "2028년 3월 7일"), time=(1, "오전 10시"), cat=(1, "피킹"),
      missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(68, M, "missing_date_polite_b3", ["오후 5시에 반품 콜센터 응대 교육 등록해 주세요", "7월 11일이요"], ME,
      title=(1, "반품 콜센터 응대 교육"), date=(2, "7월 11일"), time=(1, "오후 5시"), cat=(1, "반품"),
      missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(69, M, "missing_date_polite_b3", ["오전 10시 반에 연간 계약 갱신 회의 넣어 주세요", "2029년 1월 3일로 해 주세요"], YE,
      title=(1, "연간 계약 갱신 회의"), date=(2, "2029년 1월 3일"), time=(1, "오전 10시 반"), cat=None,
      missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(70, M, "missing_date_polite_b3", ["오후 2시에 창고 동선 개선 워크숍 잡아 주세요", "글피로요"], WE,
      title=(1, "창고 동선 개선 워크숍"), date=(2, "글피"), time=(1, "오후 2시"), cat=None,
      missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(71, M, "missing_ampm_b3", ["2028년 2월 22일 3시에 입고 차량 예약 확인 넣어줘", "오후 3시"], WM,
      title=(1, "입고 차량 예약 확인"), date=(1, "2028년 2월 22일"), time=(2, "오후 3시"), cat=(1, "입고"),
      missing=miss(("ampm", 1, 2)), target_overrides={1: {"time": E([(1, "3시")])}}, expectations=[ask("ampm"), *OK]),
    S(72, M, "missing_ampm_b3", ["모레 11시에 재고 분류 기준 회의 등록해줘", "오전 11시요"], EM,
      title=(1, "재고 분류 기준 회의"), date=(1, "모레"), time=(2, "오전 11시"), cat=(1, "재고"),
      missing=miss(("ampm", 1, 2)), target_overrides={1: {"time": E([(1, "11시")])}}, expectations=[ask("ampm"), *OK]),
    S(73, M, "missing_ampm_b3", ["내일 7시에 출고 상차 인원 점검 잡아줘", "저녁 7시"], WK,
      title=(1, "출고 상차 인원 점검"), date=(1, "내일"), time=(2, "저녁 7시"), cat=(1, "출고"),
      missing=miss(("ampm", 1, 2)), target_overrides={1: {"time": E([(1, "7시")])}}, expectations=[ask("ampm"), *OK]),
    S(74, M, "missing_ampm_b3", ["7월 9일 10시 반에 협력사 계약 서명 넣어줘", "오전 10시 반이요"], ME,
      title=(1, "협력사 계약 서명"), date=(1, "7월 9일"), time=(2, "오전 10시 반"), cat=None,
      missing=miss(("ampm", 1, 2)), target_overrides={1: {"time": E([(1, "10시 반")])}}, expectations=[ask("ampm"), *OK]),
    S(75, M, "missing_ampm_b3", ["2028년 3월 6일 4시에 반품 처리 매뉴얼 회의 넣어 주세요", "오후 4시로 부탁해요"], LD,
      title=(1, "반품 처리 매뉴얼 회의"), date=(1, "2028년 3월 6일"), time=(2, "오후 4시"), cat=(1, "반품"),
      missing=miss(("ampm", 1, 2)), target_overrides={1: {"time": E([(1, "4시")])}}, expectations=[ask("ampm"), *OK]),
    UF(76, "untrusted_today_followup_b3", ["오늘 오후 2시에 출고 차량 정비 확인 넣어줘", "2028년 4월 4일 오후 2시"], EM,
       title=(1, "출고 차량 정비 확인"), cat=(1, "출고"), t1_date="오늘", t1_time="오후 2시",
       date=(2, "2028년 4월 4일"), time=(2, "오후 2시")),
    UF(77, "untrusted_today_followup_b3", ["오늘 오후 6시에 주말 재고 마감 보고 등록해 주세요", "2028년 5월 13일 18시예요"], WK,
       title=(1, "주말 재고 마감 보고"), cat=(1, "재고"), t1_date="오늘", t1_time="오후 6시",
       date=(2, "2028년 5월 13일"), time=(2, "18시")),
    UF(78, "untrusted_today_followup_b3", ["오늘 오후 3시 반에 거래처 클레임 대응 잡아줘", "2028년 2월 29일 오후 3시 30분"], LD,
       title=(1, "거래처 클레임 대응"), cat=None, t1_date="오늘", t1_time="오후 3시 반",
       date=(2, "2028년 2월 29일"), time=(2, "오후 3시 30분")),
    UF(79, "untrusted_today_followup_b3", ["내일 오전 10시에 입고 검수 장비 교정 넣어줘", "날짜는 2028년 2월 11일이야"], WM,
       title=(1, "입고 검수 장비 교정"), cat=(1, "입고"), t1_date="내일", t1_time=None,
       date=(2, "2028년 2월 11일"), time=(1, "오전 10시")),
    UF(80, "untrusted_today_followup_b3", ["모레 오후 1시에 연초 반품 집계 등록해 줘", "2029년 1월 2일이요"], YE,
       title=(1, "연초 반품 집계"), cat=(1, "반품"), t1_date="모레", t1_time=None,
       date=(2, "2029년 1월 2일"), time=(1, "오후 1시")),
    UF(81, "untrusted_conflict_followup", ["오늘 오후 3시에 협력사 설비 점검 회의 넣어줘", "2028년 4월 6일 오후 3시예요"], EM,
       title=(1, "협력사 설비 점검 회의"), cat=None, t1_date="오늘", t1_time="오후 3시",
       date=(2, "2028년 4월 6일"), time=(2, "오후 3시")),
    UF(82, "untrusted_conflict_followup", ["내일 오전 10시에 출고 대기 물량 점검 등록해줘", "2028년 3월 20일이요"], WE,
       title=(1, "출고 대기 물량 점검"), cat=(1, "출고"), t1_date="내일", t1_time=None,
       date=(2, "2028년 3월 20일"), time=(1, "오전 10시")),
    UF(83, "untrusted_conflict_followup", ["모레 오후 2시에 재고 실사 준비 회의 넣어 주세요", "2028년 5월 19일입니다"], WK,
       title=(1, "재고 실사 준비 회의"), cat=(1, "재고"), t1_date="모레", t1_time=None,
       date=(2, "2028년 5월 19일"), time=(1, "오후 2시")),
    UF(84, "untrusted_conflict_followup", ["오늘 오후 7시에 월말 반품 마감 등록해줘", "2028년 7월 31일 오후 7시"], ME,
       title=(1, "월말 반품 마감"), cat=(1, "반품"), t1_date="오늘", t1_time="오후 7시",
       date=(2, "2028년 7월 31일"), time=(2, "오후 7시")),

    # ---- Corrections: 34 (4 title unclear) ----
    S(85, C, "corr_time_b3", ["내일 오후 3시 아니고 오후 4시에 협력사 감사 대응 등록해줘"], WM,
      title=(1, "협력사 감사 대응"), date=(1, "내일"), cat=None,
      time=E([(1, "오후 4시")], superseded=[(1, "오후 3시")], markers=[(1, "아니고")])),
    S(86, C, "corr_time_b3", ["2028년 4월 13일 오전 9시 말고 오전 10시에 재고 창고 이관 회의 넣어줘"], EM,
      title=(1, "재고 창고 이관 회의"), date=(1, "2028년 4월 13일"), cat=(1, "재고"),
      time=E([(1, "오전 10시")], superseded=[(1, "오전 9시")], markers=[(1, "말고")])),
    S(87, C, "corr_time_b3", ["글피 오후 2시, 아니 오후 4시로 출하 예정 점검 넣어줘"], WK,
      title=(1, "출하 예정 점검"), date=(1, "글피"), cat=None,
      time=E([(1, "오후 4시")], superseded=[(1, "오후 2시")], markers=[(1, "아니")])),
    S(88, C, "corr_time_b3", ["모레 15시 대신 16시 30분에 반품 협력사 미팅 잡아줘"], LD,
      title=(1, "반품 협력사 미팅"), date=(1, "모레"), cat=(1, "반품"),
      time=E([(1, "16시 30분")], superseded=[(1, "15시")], markers=[(1, "대신")])),
    S(89, C, "corr_time_b3", ["2029년 1월 5일 오후 1시가 아니라 오후 3시에 연초 피킹 교육 등록해줘"], YE,
      title=(1, "연초 피킹 교육"), date=(1, "2029년 1월 5일"), cat=(1, "피킹"),
      time=E([(1, "오후 3시")], superseded=[(1, "오후 1시")], markers=[(1, "아니라")])),
    S(90, C, "corr_time_polite_b3", ["모레 오전 10시 말고 오전 11시에 입고 품질 회의 넣어 주세요"], WE,
      title=(1, "입고 품질 회의"), date=(1, "모레"), cat=(1, "입고"),
      time=E([(1, "오전 11시")], superseded=[(1, "오전 10시")], markers=[(1, "말고")])),
    S(91, C, "corr_time_polite_b3", ["7월 6일 오후 2시 아니고 오후 3시 반에 부산 거래처 방문 등록해 주세요"], ME,
      title=(1, "부산 거래처 방문"), date=(1, "7월 6일"), cat=None,
      time=E([(1, "오후 3시 반")], superseded=[(1, "오후 2시")], markers=[(1, "아니고")])),
    S(92, C, "corr_time_polite_b3", ["2028년 2월 24일 오전 8시 대신 오전 9시 30분으로 창고 정기 순찰 넣어 주세요"], WM,
      title=(1, "창고 정기 순찰"), date=(1, "2028년 2월 24일"), cat=None,
      time=E([(1, "오전 9시 30분")], superseded=[(1, "오전 8시")], markers=[(1, "대신")])),
    S(93, C, "corr_time_polite_b3", ["내일 오후 4시가 아니라 오후 5시에 재고 정산 회의 잡아 주세요"], LD,
      title=(1, "재고 정산 회의"), date=(1, "내일"), cat=(1, "재고"),
      time=E([(1, "오후 5시")], superseded=[(1, "오후 4시")], markers=[(1, "아니라")])),
    S(94, C, "corr_date_b3", ["5월 16일 말고 5월 17일 오후 3시에 출고 실적 회의 넣어줘"], WK,
      title=(1, "출고 실적 회의"), time=(1, "오후 3시"), cat=(1, "출고"),
      date=E([(1, "5월 17일")], superseded=[(1, "5월 16일")], markers=[(1, "말고")])),
    S(95, C, "corr_date_b3", ["내일 아니라 모레 오전 11시에 반품 환불 정책 회의 등록해줘"], EM,
      title=(1, "반품 환불 정책 회의"), time=(1, "오전 11시"), cat=(1, "반품"),
      date=E([(1, "모레")], superseded=[(1, "내일")], markers=[(1, "아니라")])),
    S(96, C, "corr_date_b3", ["2028년 3월 22일 대신 2028년 3월 23일 오후 2시에 입고 협력 점검 잡아줘"], WE,
      title=(1, "입고 협력 점검"), time=(1, "오후 2시"), cat=(1, "입고"),
      date=E([(1, "2028년 3월 23일")], superseded=[(1, "2028년 3월 22일")], markers=[(1, "대신")])),
    S(97, C, "corr_date_b3", ["7월 3일 아니고 7월 4일 오전 10시에 운송사 평가 회의 넣어줘"], ME,
      title=(1, "운송사 평가 회의"), time=(1, "오전 10시"), cat=None,
      date=E([(1, "7월 4일")], superseded=[(1, "7월 3일")], markers=[(1, "아니고")])),
    S(98, C, "corr_date_b3", ["연초 거래처 인사, 모레 말고 글피 오후 3시에 등록해줘"], YE,
      title=(1, "연초 거래처 인사"), time=(1, "오후 3시"), cat=None,
      date=E([(1, "글피")], superseded=[(1, "모레")], markers=[(1, "말고")])),
    S(99, C, "corr_date_polite_b3", ["모레가 아니라 글피 오후 2시에 피킹 장비 교육 넣어 주세요"], WM,
      title=(1, "피킹 장비 교육"), time=(1, "오후 2시"), cat=(1, "피킹"),
      date=E([(1, "글피")], superseded=[(1, "모레")], markers=[(1, "아니라")])),
    S(100, C, "corr_date_polite_b3", ["2028년 3월 2일 말고 2028년 3월 3일 오전 11시에 협력사 인증 심사 등록해 주세요"], LD,
      title=(1, "협력사 인증 심사"), time=(1, "오전 11시"), cat=None,
      date=E([(1, "2028년 3월 3일")], superseded=[(1, "2028년 3월 2일")], markers=[(1, "말고")])),
    S(101, C, "corr_date_polite_b3", ["5월 18일이 아니라 5월 19일 오후 1시에 신입 재고 교육 넣어 주세요"], WK,
      title=(1, "신입 재고 교육"), time=(1, "오후 1시"), cat=(1, "재고"),
      date=E([(1, "5월 19일")], superseded=[(1, "5월 18일")], markers=[(1, "아니라")])),
    S(102, C, "corr_date_polite_b3", ["4월 7일 아니고 4월 8일 오후 4시에 출고 공정 회의 잡아 주세요"], EM,
      title=(1, "출고 공정 회의"), time=(1, "오후 4시"), cat=(1, "출고"),
      date=E([(1, "4월 8일")], superseded=[(1, "4월 7일")], markers=[(1, "아니고")])),
    S(103, C, "corr_title_b3", ["하역장 점검 말고 화물 차량 점검을 내일 오전 10시에 등록해줘"], WE,
      date=(1, "내일"), time=(1, "오전 10시"), cat=None,
      title=E([(1, "화물 차량 점검")], superseded=[(1, "하역장 점검")], markers=[(1, "말고")])),
    S(104, C, "corr_title_b3", ["거래처 미팅 아니고 거래처 정산 회의를 7월 5일 오후 2시에 넣어줘"], ME,
      date=(1, "7월 5일"), time=(1, "오후 2시"), cat=None,
      title=E([(1, "거래처 정산 회의")], superseded=[(1, "거래처 미팅")], markers=[(1, "아니고")])),
    S(105, C, "corr_title_b3", ["송년 회식이 아니라 연초 회식을 2029년 1월 6일 오후 6시에 잡아 주세요"], YE,
      date=(1, "2029년 1월 6일"), time=(1, "오후 6시"), cat=None,
      title=E([(1, "연초 회식")], superseded=[(1, "송년 회식")], markers=[(1, "아니라")])),
    S(106, C, "corr_title_b3", ["보안 교육 대신 화재 대피 훈련을 모레 오후 3시에 넣어줘"], LD,
      date=(1, "모레"), time=(1, "오후 3시"), cat=None,
      title=E([(1, "화재 대피 훈련")], superseded=[(1, "보안 교육")], markers=[(1, "대신")])),
    S(107, C, "corr_title_b3", ["면접 말고 인턴 면담을 2028년 2월 16일 오전 11시에 등록해 주세요"], WM,
      date=(1, "2028년 2월 16일"), time=(1, "오전 11시"), cat=None,
      title=E([(1, "인턴 면담")], superseded=[(1, "면접")], markers=[(1, "말고")])),
    S(108, C, "corr_cue_mid_b3", ["주간 재고 회의 말고 주간 반품 회의를 모레 오후 2시에 등록해줘"], EM,
      date=(1, "모레"), time=(1, "오후 2시"),
      title=E([(1, "주간 반품 회의")], superseded=[(1, "주간 재고 회의")], markers=[(1, "말고")]),
      cat=E([(1, "반품")], superseded=[(1, "재고")], markers=[(1, "말고")])),
    S(109, C, "corr_cue_mid_b3", ["협력 입고 브리핑 아니라 협력 출고 브리핑을 내일 오전 9시에 넣어 주세요"], WK,
      date=(1, "내일"), time=(1, "오전 9시"),
      title=E([(1, "협력 출고 브리핑")], superseded=[(1, "협력 입고 브리핑")], markers=[(1, "아니라")]),
      cat=E([(1, "출고")], superseded=[(1, "입고")], markers=[(1, "아니라")])),
    S(110, C, "corr_cue_mid_b3", ["긴급 출고 회의 대신 긴급 재고 회의를 7월 3일 오후 1시에 넣어줘"], ME,
      date=(1, "7월 3일"), time=(1, "오후 1시"),
      title=E([(1, "긴급 재고 회의")], superseded=[(1, "긴급 출고 회의")], markers=[(1, "대신")]),
      cat=E([(1, "재고")], superseded=[(1, "출고")], markers=[(1, "대신")])),
    S(111, C, "corr_cue_mid_b3", ["연초 반품 점검 아니고 연초 입고 점검을 2029년 1월 4일 오전 10시에 잡아줘"], YE,
      date=(1, "2029년 1월 4일"), time=(1, "오전 10시"),
      title=E([(1, "연초 입고 점검")], superseded=[(1, "연초 반품 점검")], markers=[(1, "아니고")]),
      cat=E([(1, "입고")], superseded=[(1, "반품")], markers=[(1, "아니고")])),
    S(112, C, "marker_after_adopted_b3", ["모레 오후 4시에 협력사 납품 단가 회의 넣어줘, 3시는 아니고"], WE,
      title=(1, "협력사 납품 단가 회의"), date=(1, "모레"), time=(1, "오후 4시"), cat=None, correction_provenance=AFTER),
    S(113, C, "marker_after_adopted_b3", ["2028년 3월 7일 오전 10시에 재고 회계 마감 등록해 주세요, 오전 9시는 말고요"], LD,
      title=(1, "재고 회계 마감"), date=(1, "2028년 3월 7일"), time=(1, "오전 10시"), cat=(1, "재고"),
      correction_provenance=AFTER),
    S(114, C, "marker_after_adopted_b3", ["내일 오후 2시에 출고 라벨 교육 잡아줘, 1시 말고"], WM,
      title=(1, "출고 라벨 교육"), date=(1, "내일"), time=(1, "오후 2시"), cat=(1, "출고"), correction_provenance=AFTER),
    CROSS_TITLE(115, ["내일 오후 1시로 하나 잡아 줘", "협력사 정기 회의고, 오후 2시로 해 줘"], WK,
                title="협력사 정기 회의", date=(1, "내일"), time=(2, "오후 2시"), cat=None,
                t1={"time": E([(1, "오후 1시")])}),
    CROSS_TITLE(116, ["모레 오전 10시에 그거 넣어 주세요", "반품 정리 회의예요, 시간은 오전 11시로요"], EM,
                title="반품 정리 회의", date=(1, "모레"), time=(2, "오전 11시"), cat="반품",
                t1={"time": E([(1, "오전 10시")])}),
    CROSS_TITLE(117, ["7월 4일 오후 3시로 뭐 하나 등록해 줘", "재고 이동 계획이야, 날짜는 7월 5일로"], ME,
                title="재고 이동 계획", date=(2, "7월 5일"), time=(1, "오후 3시"), cat="재고",
                t1={"date": E([(1, "7월 4일")])}),
    CROSS_TITLE(118, ["2029년 1월 3일 오전 10시에 이거 등록해 줘", "연초 입고 물량 협의고, 2029년 1월 4일로 해 줘"], YE,
                title="연초 입고 물량 협의", date=(2, "2029년 1월 4일"), time=(1, "오전 10시"), cat="입고",
                t1={"date": E([(1, "2029년 1월 3일")])}),

    # ---- Time safety: 32 ----
    UP(119, ["오늘 오후 5시에 평일 출고 마감 점검 등록해줘"], WM, family="untrusted_today_unresolved_b3",
       title=(1, "평일 출고 마감 점검"), date=(1, "오늘"), time=(1, "오후 5시"), cat=(1, "출고")),
    UP(120, ["오늘 오후 4시에 창고 셔터 수리 넣어 주세요"], WK, family="untrusted_today_unresolved_b3",
       title=(1, "창고 셔터 수리"), date=(1, "오늘"), time=(1, "오후 4시"), cat=None),
    UP(121, ["오늘 오후 2시 반에 입고 거래명세서 확인 잡아줘"], LD, family="untrusted_today_unresolved_b3",
       title=(1, "입고 거래명세서 확인"), date=(1, "오늘"), time=(1, "오후 2시 반"), cat=(1, "입고")),
    UP(122, ["오늘 오후 9시에 연말 재고 스냅샷 등록해줘"], YE, family="untrusted_today_unresolved_b3",
       title=(1, "연말 재고 스냅샷"), date=(1, "오늘"), time=(1, "오후 9시"), cat=(1, "재고")),
    UP(123, ["오늘 오전 11시에 반품 접수 현황 공유 넣어줘"], EM, family="untrusted_today_unresolved_b3",
       title=(1, "반품 접수 현황 공유"), date=(1, "오늘"), time=(1, "오전 11시"), cat=(1, "반품")),
    UP(124, ["3월 29일 오후 3시에 출고 요율 협상 등록해 주세요"], WE, family="untrusted_yearless_unresolved_b3",
       title=(1, "출고 요율 협상"), date=(1, "3월 29일"), time=(1, "오후 3시"), cat=(1, "출고")),
    UP(125, ["7월 14일 오전 10시에 재고 실사 리허설 넣어줘"], ME, family="untrusted_yearless_unresolved_b3",
       title=(1, "재고 실사 리허설"), date=(1, "7월 14일"), time=(1, "오전 10시"), cat=(1, "재고")),
    UP(126, ["5월 28일 오후 2시에 협력사 교육 평가 잡아줘"], WK, family="untrusted_yearless_unresolved_b3",
       title=(1, "협력사 교육 평가"), date=(1, "5월 28일"), time=(1, "오후 2시"), cat=None),
    UP(127, ["3월 16일 오전 9시 반에 입고 라인 증설 회의 등록해줘"], LD, family="untrusted_yearless_unresolved_b3",
       title=(1, "입고 라인 증설 회의"), date=(1, "3월 16일"), time=(1, "오전 9시 반"), cat=(1, "입고")),
    S(128, T, "untrusted_absolute_b3", ["2028년 2월 19일 오후 3시에 반품 감사 준비 등록해줘"], WM,
      trusted=False, title=(1, "반품 감사 준비"), date=(1, "2028년 2월 19일"), time=(1, "오후 3시"), cat=(1, "반품")),
    S(129, T, "untrusted_absolute_b3", ["2028년 4월 21일 오전 10시 30분에 출고 계약 검토 넣어 주세요"], EM,
      trusted=False, title=(1, "출고 계약 검토"), date=(1, "2028년 4월 21일"), time=(1, "오전 10시 30분"), cat=(1, "출고")),
    S(130, T, "untrusted_absolute_b3", ["2028년 7월 13일 13시에 재고 이상 품목 회의 잡아줘"], ME,
      trusted=False, title=(1, "재고 이상 품목 회의"), date=(1, "2028년 7월 13일"), time=(1, "13시"), cat=(1, "재고")),
    S(131, T, "untrusted_absolute_b3", ["2029년 1월 10일 오후 4시에 연간 물류 전략 회의 등록해 주세요"], YE,
      trusted=False, title=(1, "연간 물류 전략 회의"), date=(1, "2029년 1월 10일"), time=(1, "오후 4시"), cat=None),
    S(132, T, "untrusted_absolute_b3", ["2028년 3월 27일 오전 9시에 피킹 존 재배치 넣어줘"], WE,
      trusted=False, title=(1, "피킹 존 재배치"), date=(1, "2028년 3월 27일"), time=(1, "오전 9시"), cat=(1, "피킹")),
    S(133, T, "past_time_b3", ["2028년 3월 14일 오후 2시에 협력사 정기 보고 넣어줘"], WE,
      title=(1, "협력사 정기 보고"), date=(1, "2028년 3월 14일"), time=(1, "오후 2시"), cat=None,
      final_status="schedule_rejected", expectations=PAST),
    S(134, T, "past_time_b3", ["오늘 오후 3시에 월말 출고 집계 등록해줘"], ME,
      title=(1, "월말 출고 집계"), date=(1, "오늘"), time=(1, "오후 3시"), cat=(1, "출고"),
      final_status="schedule_rejected", expectations=PAST),
    S(135, T, "past_time_b3", ["오늘 오전 10시에 주말 재고 순찰 잡아줘"], WK,
      title=(1, "주말 재고 순찰"), date=(1, "오늘"), time=(1, "오전 10시"), cat=(1, "재고"),
      final_status="schedule_rejected", expectations=PAST),
    S(136, T, "past_time_b3", ["2028년 2월 28일 오후 5시에 반품 마감 보고 등록해 주세요"], LD,
      title=(1, "반품 마감 보고"), date=(1, "2028년 2월 28일"), time=(1, "오후 5시"), cat=(1, "반품"),
      final_status="schedule_rejected", expectations=PAST),
    S(137, T, "past_time_b3", ["오늘 오후 6시에 연말 입고 정리 넣어줘"], YE,
      title=(1, "연말 입고 정리"), date=(1, "오늘"), time=(1, "오후 6시"), cat=(1, "입고"),
      final_status="schedule_rejected", expectations=PAST),
    S(138, T, "noon_midnight_b3", ["2028년 4월 19일 오후 12시에 협력사 오찬 간담회 등록해줘"], EM,
      title=(1, "협력사 오찬 간담회"), date=(1, "2028년 4월 19일"), time=(1, "오후 12시"), cat=None),
    S(139, T, "noon_midnight_b3", ["모레 0시 30분에 전산 시스템 점검 넣어줘"], WK,
      title=(1, "전산 시스템 점검"), date=(1, "모레"), time=(1, "0시 30분"), cat=None),
    S(140, T, "noon_midnight_b3", ["2028년 2월 13일 오전 12시에 재고 데이터 이관 등록해줘"], WM,
      title=(1, "재고 데이터 이관"), date=(1, "2028년 2월 13일"), time=(1, "오전 12시"), cat=(1, "재고")),
    S(141, T, "noon_midnight_b3", ["7월 2일 오후 12시 반에 출고팀 오찬 회의 잡아 주세요"], ME,
      title=(1, "출고팀 오찬 회의"), date=(1, "7월 2일"), time=(1, "오후 12시 반"), cat=(1, "출고")),
    S(142, T, "year_boundary_b3", ["내일 오전 10시에 연초 입고 계획 공유 넣어줘"], YE,
      title=(1, "연초 입고 계획 공유"), date=(1, "내일"), time=(1, "오전 10시"), cat=(1, "입고")),
    S(143, T, "year_boundary_b3", ["모레 오후 3시에 새해 재고 목표 회의 등록해 주세요"], YE,
      title=(1, "새해 재고 목표 회의"), date=(1, "모레"), time=(1, "오후 3시"), cat=(1, "재고")),
    S(144, T, "year_boundary_b3", ["글피 오후 2시에 연간 반품 결산 잡아줘"], YE,
      title=(1, "연간 반품 결산"), date=(1, "글피"), time=(1, "오후 2시"), cat=(1, "반품")),
    S(145, T, "year_boundary_b3", ["2029년 1월 2일 오전 9시에 시무식 준비 회의 넣어줘"], YE,
      title=(1, "시무식 준비 회의"), date=(1, "2029년 1월 2일"), time=(1, "오전 9시"), cat=None),
    AMB(146, "ambiguous_unresolved_b3", ["모레 2시에 협력사 납품 일정 조정 넣어줘"], WM,
        title=(1, "협력사 납품 일정 조정"), date=(1, "모레"), time=(1, "2시"), cat=None),
    AMB(147, "ambiguous_unresolved_b3", ["2028년 5월 22일 9시에 반품 검사 교육 등록해 주세요"], WK,
        title=(1, "반품 검사 교육"), date=(1, "2028년 5월 22일"), time=(1, "9시"), cat=(1, "반품")),
    AMB(148, "ambiguous_unresolved_b3", ["내일 5시 반에 주간 출고 마감 회의 잡아줘"], LD,
        title=(1, "주간 출고 마감 회의"), date=(1, "내일"), time=(1, "5시 반"), cat=(1, "출고")),
    AMB(149, "ambiguous_unresolved_b3", ["7월 12일 11시에 분기 재고 정리 회의 넣어 주세요"], ME,
        title=(1, "분기 재고 정리 회의"), date=(1, "7월 12일"), time=(1, "11시"), cat=(1, "재고")),
    AMB(150, "ambiguous_unresolved_b3", ["글피 8시에 지게차 안전 교육 등록해줘"], EM,
        title=(1, "지게차 안전 교육"), date=(1, "글피"), time=(1, "8시"), cat=None),

    # ---- Mixed unsupported + consent: 20 ----
    MX(151, "mixed_unsupported_first_b3", ["알림도 설정해 주고, 모레 오후 3시에 출고 협의 회의 넣어줘", "알림 빼고 일정만 넣어줘"], WM,
       title=(1, "출고 협의 회의"), date=(1, "모레"), time=(1, "오후 3시"), cat=(1, "출고"),
       cue="알림도 설정해", feature="reminder", result="granted"),
    MX(152, "mixed_unsupported_first_b3", ["문자도 보내 주고 2028년 4월 14일 오전 10시에 재고 감사 회의 등록해 줘", "문자 없이 등록해 줘"], EM,
       title=(1, "재고 감사 회의"), date=(1, "2028년 4월 14일"), time=(1, "오전 10시"), cat=(1, "재고"),
       cue="문자도 보내", feature="other", result="granted"),
    MX(153, "mixed_unsupported_first_b3", ["푸시도 켜 주고 내일 오후 2시에 협력사 교류회 넣어줘", "푸시 없이 그냥 등록해 줘"], WK,
       title=(1, "협력사 교류회"), date=(1, "내일"), time=(1, "오후 2시"), cat=None,
       cue="푸시도 켜", feature="reminder", result="granted"),
    MX(154, "mixed_unsupported_first_b3", ["미리 알려 주고, 2028년 3월 13일 오후 1시에 반품 프로세스 개선 회의 잡아줘", "알림 없이 진행해 줘"], LD,
       title=(1, "반품 프로세스 개선 회의"), date=(1, "2028년 3월 13일"), time=(1, "오후 1시"), cat=(1, "반품"),
       cue="미리 알려", feature="reminder", result="granted"),
    MX(155, "mixed_unsupported_first_b3", ["리마인더도 설정해 주고 2029년 1월 8일 오전 11시에 입고 담당자 미팅 등록해줘", "그럼 리마인더 없이 등록해"], YE,
       title=(1, "입고 담당자 미팅"), date=(1, "2029년 1월 8일"), time=(1, "오전 11시"), cat=(1, "입고"),
       cue="리마인더도 설정해", feature="reminder", result="granted"),
    MX(156, "mixed_polite_grant_b3", ["내일 오후 4시에 피킹 리스트 점검 넣고 1시간 전에 알려 주세요", "알림 없이 등록해 주세요"], WE,
       title=(1, "피킹 리스트 점검"), date=(1, "내일"), time=(1, "오후 4시"), cat=(1, "피킹"),
       cue="1시간 전에 알려", feature="reminder", result="granted"),
    MX(157, "mixed_polite_grant_b3", ["7월 6일 오전 10시에 출고 대기 회의 등록하고 문자로 보내 주세요", "네, 문자는 빼고 등록해 주세요"], ME,
       title=(1, "출고 대기 회의"), date=(1, "7월 6일"), time=(1, "오전 10시"), cat=(1, "출고"),
       cue="문자로 보내", feature="other", result="granted"),
    MX(158, "mixed_polite_grant_b3", ["2028년 2월 21일 오후 2시에 재고 감모 분석 넣고 알림도 켜 주세요", "알림 없이 그냥 넣어 주세요"], WM,
       title=(1, "재고 감모 분석"), date=(1, "2028년 2월 21일"), time=(1, "오후 2시"), cat=(1, "재고"),
       cue="알림도 켜", feature="reminder", result="granted"),
    MX(159, "mixed_polite_grant_b3", ["모레 오전 11시에 반품 보관대 정돈 등록하고 30분 전에 알려 주세요", "그냥 일정만 등록해 주세요"], LD,
       title=(1, "반품 보관대 정돈"), date=(1, "모레"), time=(1, "오전 11시"), cat=(1, "반품"),
       cue="30분 전에 알려", feature="reminder", result="granted"),
    MX(160, "mixed_polite_grant_b3", ["5월 23일 오후 3시에 거래처 감사 미팅 넣고 리마인더도 설정해 주세요", "좋아요, 리마인더 없이 넣어 주세요"], WK,
       title=(1, "거래처 감사 미팅"), date=(1, "5월 23일"), time=(1, "오후 3시"), cat=None,
       cue="리마인더도 설정해", feature="reminder", result="granted"),
    MX(161, "mixed_decline_b3", ["모레 오후 2시에 입고 단가 회의 등록하고 알림도 보내 주세요", "아니요, 등록하지 마세요"], EM,
       title=(1, "입고 단가 회의"), date=(1, "모레"), time=(1, "오후 2시"), cat=(1, "입고"),
       cue="알림도 보내", feature="reminder", result="declined"),
    MX(162, "mixed_decline_b3", ["내일 오전 10시에 재고 보고 회의 넣고 문자도 보내줘", "아니, 그냥 취소할게"], WE,
       title=(1, "재고 보고 회의"), date=(1, "내일"), time=(1, "오전 10시"), cat=(1, "재고"),
       cue="문자도 보내", feature="other", result="declined"),
    MX(163, "mixed_decline_b3", ["7월 10일 오후 4시에 출고 운송 점검 등록하고 푸시도 보내 줘", "괜찮아, 하지 마"], ME,
       title=(1, "출고 운송 점검"), date=(1, "7월 10일"), time=(1, "오후 4시"), cat=(1, "출고"),
       cue="푸시도 보내", feature="reminder", result="declined"),
    MX(164, "mixed_decline_b3", ["2029년 1월 7일 오후 2시에 반품 담당 회의 넣고 미리 알려줘", "아니요, 등록 안 할게요"], YE,
       title=(1, "반품 담당 회의"), date=(1, "2029년 1월 7일"), time=(1, "오후 2시"), cat=(1, "반품"),
       cue="미리 알려", feature="reminder", result="declined"),
    MX(165, "mixed_decline_b3", ["내일 오후 5시에 창고 키 반납 확인 등록하고 알림도 꼭 보내줘", "됐어요, 등록하지 마세요"], LD,
       title=(1, "창고 키 반납 확인"), date=(1, "내일"), time=(1, "오후 5시"), cat=None,
       cue="알림도 꼭 보내", feature="reminder", result="declined"),
    MX(166, "mixed_pending_b3", ["리마인더도 설정해 주고 2028년 2월 25일 오전 10시에 출고 송장 서류 점검 등록해 주세요"], WM,
       title=(1, "출고 송장 서류 점검"), date=(1, "2028년 2월 25일"), time=(1, "오전 10시"), cat=(1, "출고"),
       cue="리마인더도 설정해", feature="reminder", result="pending"),
    MX(167, "mixed_pending_b3", ["글피 오후 3시에 반품 운송장 정리 넣고 1시간 전에 알려줘"], WE,
       title=(1, "반품 운송장 정리"), date=(1, "글피"), time=(1, "오후 3시"), cat=(1, "반품"),
       cue="1시간 전에 알려", feature="reminder", result="pending"),
    MX(168, "mixed_pending_b3", ["내일 오후 1시에 재고 표준 교육 등록하고 문자로 보내 줘"], EM,
       title=(1, "재고 표준 교육"), date=(1, "내일"), time=(1, "오후 1시"), cat=(1, "재고"),
       cue="문자로 보내", feature="other", result="pending"),
    MX(169, "mixed_pending_b3", ["알림도 켜 주고 2028년 5월 29일 오후 2시에 협력사 상생 회의 넣어 주세요"], WK,
       title=(1, "협력사 상생 회의"), date=(1, "2028년 5월 29일"), time=(1, "오후 2시"), cat=None,
       cue="알림도 켜", feature="reminder", result="pending"),
    MX(170, "mixed_pending_b3", ["7월 11일 오전 9시에 입고 보안 점검 잡고 푸시도 보내줘"], ME,
       title=(1, "입고 보안 점검"), date=(1, "7월 11일"), time=(1, "오전 9시"), cat=(1, "입고"),
       cue="푸시도 보내", feature="reminder", result="pending"),
]


def authoring_errors(specifications: list[dict[str, Any]]) -> list[str]:
    errors = list(batch_quality_errors(specifications))
    earlier = {item["titleEvidence"]["adoptedSpans"][-1]["text"] for item in [*PILOT_SPECS, *B1_SPECS, *B2_SPECS]}
    for item in specifications:
        title = item["titleEvidence"]["adoptedSpans"][-1]["text"]
        if title in earlier:
            errors.append(f"{item['caseId']}: title {title!r} reused from pilot, batch 1 or batch 2")
        if "신년" in " ".join(t["text"] for t in item["turns"]):
            errors.append(f"{item['caseId']}: '신년' avoided in batch 3 (review bias note)")
        if item["unsupported"] is not None and len(item["turns"]) > 1:
            outcome = item["expectations"][1]["outcome"]
            want = {"success": "granted", "cancelled": "declined"}[outcome]
            got = gate_v3.classify_consent_answer(item["turns"][1]["text"])
            if got != want:
                errors.append(f"{item['caseId']}: consent reply classified {got!r}, oracle needs {want!r}")
        if item["expectations"][-1].get("outcome") == "success" and item["scheduledAt"] is None:
            errors.append(f"{item['caseId']}: success case without scheduledAt")
    for family, count in sorted(Counter(item["family"] for item in specifications).items()):
        if count > MAX_PER_FAMILY:
            errors.append(f"FAMILY_OVER_LIMIT: {family} {count} > {MAX_PER_FAMILY}")
    return errors


def main() -> None:
    check_specs(SPECS)
    errors = authoring_errors(SPECS)
    if errors:
        raise RuntimeError("\n".join(errors))
    cases = [_fix_tags(make_case(item), item) for item in SPECS]
    if len(cases) != CASE_COUNT or len({case["meta"]["caseId"] for case in cases}) != CASE_COUNT:
        raise RuntimeError(f"batch 3 must contain {CASE_COUNT} unique cases")
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
