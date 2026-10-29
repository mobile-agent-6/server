"""Build the Candidate-C create Test 50 (new families, never used for training or tuning).

Status: PROPOSED_UNREVIEWED / BE-PROPOSED.  Written before run 003 results were
read, so case choice is independent of the trained model's failures.

Unlike batch 3, every scheduledAt is hand-computed here and check_specs compares
it with the span-based recomputation (review note: AUTO was a self-comparison).
Families are new (prefix t50_) with at most 3 conversations each (guide section 6).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for folder in ("pilot", "batch1", "batch2", "batch3"):
    sys.path.insert(0, str(ROOT / folder))
sys.path.insert(0, str(ROOT.parent / "structured_output_contract_v0.1"))

from build_batch1 import (  # noqa: E402
    AFTER, ASK_INFO, CONSENT_Q, CROSS, DECLINED, GRANTED_OK, OK, PAST,
    C, D, M, T, X, EM, LD, ME, WE, WK, WM, YE,
    SPECS as B1_SPECS, _category, _ev, ask, miss,
)
from build_batch2 import SPECS as B2_SPECS, _fix_tags  # noqa: E402
from build_batch3 import SPECS as B3_SPECS  # noqa: E402
from build_pilot import (  # noqa: E402
    SPECS as PILOT_SPECS, canonical, evidence, make_candidate_target, make_case, sha256, spec, turn,
)
from spec_checks import batch_quality_errors, check_specs  # noqa: E402
import common_gate_v0_3 as gate_v3  # noqa: E402

OUTPUT = HERE / "conversations-v0.1.jsonl"
TARGETS = HERE / "candidate-c-targets-v0.1.jsonl"
CASE_COUNT = 50
MAX_PER_FAMILY = 3
UNTRUSTED = "absolute_datetime_due_to_untrusted_time"
TITLE_ASK = ask("task_or_title_unclear")
E = evidence


def S(number: int, bucket: str, family: str, texts: list[str], ref: str, *,
      title: Any, date: Any, time: Any, cat: Any, at: str | None,
      expectations: list[dict[str, Any]] = OK, **kwargs: Any) -> dict[str, Any]:
    turns = [turn(text) if index == 0 else turn(text, answer=index) for index, text in enumerate(texts)]
    return spec(
        number, bucket, family, turns, reference=ref,
        title=_ev(title), date=_ev(date), time=_ev(time), category_evidence=_ev(cat),
        scheduled_at=at, category=_category(cat), expectations=expectations,
        batch="t50", dataset_role="locked_comparison", authored_on="2026-10-28", **kwargs,
    )


def TU(number, family, texts, ref, *, title, date, time, at, cat=None):
    return S(number, M, family, texts, ref, title=(2, title), date=date, time=time,
             cat=None if cat is None else (2, cat), at=at,
             missing=miss(("task_or_title", 1, 2)), expectations=[TITLE_ASK, *OK])


def UF(number, family, texts, ref, *, title, cat, t1_date, t1_time, date, time, at):
    overrides = {"date": E([(1, t1_date)])}
    if t1_time is not None:
        overrides["time"] = E([(1, t1_time)])
    return S(number, M, family, texts, ref, trusted=False, title=title, date=date, time=time, cat=cat, at=at,
             missing=miss((UNTRUSTED, 1, 2)), target_overrides={1: overrides},
             expectations=[ask("absolute_datetime_required"), *OK])


def UP(number, family, texts, ref, *, title, date, time, cat):
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
    # ---- Direct create: 10 ----
    S(1, D, "t50_direct_plain", ["모레 오후 3시에 냉동 탑차 점검 등록해줘"], WM,
      title=(1, "냉동 탑차 점검"), date=(1, "모레"), time=(1, "오후 3시"), cat=None, at="2028-02-12T15:00:00+09:00"),
    S(2, D, "t50_direct_plain", ["라벨 프린터 교체 작업, 내일 오전 10시에 넣어 줘"], WE,
      title=(1, "라벨 프린터 교체 작업"), date=(1, "내일"), time=(1, "오전 10시"), cat=None, at="2028-03-16T10:00:00+09:00"),
    S(3, D, "t50_direct_plain", ["2028년 4월 25일 오후 4시 30분에 반품 사진 대조 등록해 주세요"], EM,
      title=(1, "반품 사진 대조"), date=(1, "2028년 4월 25일"), time=(1, "오후 4시 30분"), cat=(1, "반품"),
      at="2028-04-25T16:30:00+09:00"),
    S(4, D, "t50_direct_cue", ["주말 입고 팔레트 정리 이번 주 일요일 오전 11시에 잡아줘"], WK,
      title=(1, "주말 입고 팔레트 정리"), date=(1, "이번 주 일요일"), time=(1, "오전 11시"), cat=(1, "입고"),
      at="2028-05-14T11:00:00+09:00"),
    S(5, D, "t50_direct_cue", ["고객사 재고 리포트 검토는 7월 5일 오전 9시로 넣어줘"], ME,
      title=(1, "고객사 재고 리포트 검토"), date=(1, "7월 5일"), time=(1, "오전 9시"), cat=(1, "재고"),
      at="2028-07-05T09:00:00+09:00"),
    S(6, D, "t50_direct_cue", ["글피 오후 1시 반에 냉장 출고 동선 점검 등록해줘"], LD,
      title=(1, "냉장 출고 동선 점검"), date=(1, "글피"), time=(1, "오후 1시 반"), cat=(1, "출고"),
      at="2028-03-03T13:30:00+09:00"),
    S(7, D, "t50_direct_24h", ["2029년 1월 11일 15시에 창고 전기 설비 점검 넣어줘"], YE,
      title=(1, "창고 전기 설비 점검"), date=(1, "2029년 1월 11일"), time=(1, "15시"), cat=None,
      at="2029-01-11T15:00:00+09:00"),
    S(8, D, "t50_direct_24h", ["내일 14시 20분에 협력 운송사 계약 미팅 잡아줘"], WM,
      title=(1, "협력 운송사 계약 미팅"), date=(1, "내일"), time=(1, "14시 20분"), cat=None,
      at="2028-02-11T14:20:00+09:00"),
    S(9, D, "t50_direct_filler", ["아 그리고 이번 주 금요일 오후 2시에 피킹 스캐너 충전 점검 등록해줘"], WE,
      title=(1, "피킹 스캐너 충전 점검"), date=(1, "이번 주 금요일"), time=(1, "오후 2시"), cat=(1, "피킹"),
      at="2028-03-17T14:00:00+09:00"),
    S(10, D, "t50_direct_filler", ["저 혹시 4월 9일 오전 9시 반에 신규 거래처 실사 넣어 줄래요?"], EM,
      title=(1, "신규 거래처 실사"), date=(1, "4월 9일"), time=(1, "오전 9시 반"), cat=None,
      at="2028-04-09T09:30:00+09:00"),

    # ---- Missing information and follow-up: 13 ----
    TU(11, "t50_title_unclear", ["내일 오후 2시에 그거 좀 등록해", "포장 검수대 수리야"], WK,
       title="포장 검수대 수리", date=(1, "내일"), time=(1, "오후 2시"), cat="포장", at="2028-05-14T14:00:00+09:00"),
    TU(12, "t50_title_unclear", ["7월 3일 오전 11시로 하나 넣어 줘", "거래처 반품 회수 협의요"], ME,
       title="거래처 반품 회수 협의", date=(1, "7월 3일"), time=(1, "오전 11시"), cat="반품",
       at="2028-07-03T11:00:00+09:00"),
    TU(13, "t50_title_unclear", ["모레 오후 4시에 뭐 하나 잡아 줘", "외부 감사 자료 정리"], LD,
       title="외부 감사 자료 정리", date=(1, "모레"), time=(1, "오후 4시"), at="2028-03-02T16:00:00+09:00"),
    TU(14, "t50_title_unclear_polite", ["2029년 1월 3일 오후 2시에 그 일정 좀 넣어 주세요", "재고 기준표 개정 회의예요"], YE,
       title="재고 기준표 개정 회의", date=(1, "2029년 1월 3일"), time=(1, "오후 2시"), cat="재고",
       at="2029-01-03T14:00:00+09:00"),
    S(15, M, "t50_missing_time", ["2028년 2월 20일 출고 마감 리포트 작성 넣어줘", "오후 5시"], WM,
      title=(1, "출고 마감 리포트 작성"), date=(1, "2028년 2월 20일"), time=(2, "오후 5시"), cat=(1, "출고"),
      at="2028-02-20T17:00:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(16, M, "t50_missing_time", ["모레 지게차 배터리 교체 등록해 주세요", "오전 11시 반으로 할게요"], EM,
      title=(1, "지게차 배터리 교체"), date=(1, "모레"), time=(2, "오전 11시 반"), cat=None,
      at="2028-04-06T11:30:00+09:00", missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(17, M, "t50_missing_date", ["오후 3시에 입고 지연 대책 회의 잡아줘", "내일 하자"], WE,
      title=(1, "입고 지연 대책 회의"), date=(2, "내일"), time=(1, "오후 3시"), cat=(1, "입고"),
      at="2028-03-16T15:00:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(18, M, "t50_missing_date", ["오전 10시에 창고 보험 갱신 상담 넣어 주세요", "5월 22일로 해 주세요"], WK,
      title=(1, "창고 보험 갱신 상담"), date=(2, "5월 22일"), time=(1, "오전 10시"), cat=None,
      at="2028-05-22T10:00:00+09:00", missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK]),
    S(19, M, "t50_missing_ampm", ["7월 6일 2시에 출고 차량 정산 넣어줘", "오후 2시요"], ME,
      title=(1, "출고 차량 정산"), date=(1, "7월 6일"), time=(2, "오후 2시"), cat=(1, "출고"),
      at="2028-07-06T14:00:00+09:00", missing=miss(("ampm", 1, 2)),
      target_overrides={1: {"time": E([(1, "2시")])}}, expectations=[ask("ampm"), *OK]),
    S(20, M, "t50_missing_ampm", ["3월 8일 7시 반에 외부 청소 업체 방문 넣어 주세요", "저녁 7시 반이요"], LD,
      title=(1, "외부 청소 업체 방문"), date=(1, "3월 8일"), time=(2, "저녁 7시 반"), cat=None,
      at="2028-03-08T19:30:00+09:00", missing=miss(("ampm", 1, 2)),
      target_overrides={1: {"time": E([(1, "7시 반")])}}, expectations=[ask("ampm"), *OK]),
    UF(21, "t50_untrusted_followup", ["오늘 오후 1시에 반품 검수 일지 정리 넣어줘", "2028년 4월 4일 오후 1시로 해 줘"], EM,
       title=(1, "반품 검수 일지 정리"), cat=(1, "반품"), t1_date="오늘", t1_time="오후 1시",
       date=(2, "2028년 4월 4일"), time=(2, "오후 1시"), at="2028-04-04T13:00:00+09:00"),
    UF(22, "t50_untrusted_followup", ["모레 오전 10시에 협력사 안전 점검 등록해 주세요", "2028년 2월 12일이요"], WM,
       title=(1, "협력사 안전 점검"), cat=None, t1_date="모레", t1_time=None,
       date=(2, "2028년 2월 12일"), time=(1, "오전 10시"), at="2028-02-12T10:00:00+09:00"),
    UF(23, "t50_untrusted_followup", ["오늘 오후 9시에 연간 재고 마감 확인 넣어줘", "2029년 1월 2일 오후 9시예요"], YE,
       title=(1, "연간 재고 마감 확인"), cat=(1, "재고"), t1_date="오늘", t1_time="오후 9시",
       date=(2, "2029년 1월 2일"), time=(2, "오후 9시"), at="2029-01-02T21:00:00+09:00"),

    # ---- Corrections: 10 ----
    S(24, C, "t50_corr_time", ["택배 출고 품목 대조는 내일 오후 5시 말고 오후 6시 반으로 넣어줘"], WE,
      title=(1, "택배 출고 품목 대조"), date=(1, "내일"), cat=(1, "출고"),
      time=E([(1, "오후 6시 반")], superseded=[(1, "오후 5시")], markers=[(1, "말고")]), at="2028-03-16T18:30:00+09:00"),
    S(25, C, "t50_corr_time", ["2028년 3월 9일 오후 2시 아니고 오후 3시에 거래처 신규 계약 등록해 주세요"], LD,
      title=(1, "거래처 신규 계약"), date=(1, "2028년 3월 9일"), cat=None,
      time=E([(1, "오후 3시")], superseded=[(1, "오후 2시")], markers=[(1, "아니고")]), at="2028-03-09T15:00:00+09:00"),
    S(26, C, "t50_corr_date", ["5월 20일 아니라 5월 21일 오후 1시에 입고 라벨 점검 잡아줘"], WK,
      title=(1, "입고 라벨 점검"), time=(1, "오후 1시"), cat=(1, "입고"),
      date=E([(1, "5월 21일")], superseded=[(1, "5월 20일")], markers=[(1, "아니라")]), at="2028-05-21T13:00:00+09:00"),
    S(27, C, "t50_corr_date", ["모레 대신 글피 오전 10시에 창고 계약 연장 검토 넣어 주세요"], ME,
      title=(1, "창고 계약 연장 검토"), time=(1, "오전 10시"), cat=None,
      date=E([(1, "글피")], superseded=[(1, "모레")], markers=[(1, "대신")]), at="2028-07-03T10:00:00+09:00"),
    S(28, C, "t50_corr_title", ["안전 교육 말고 품질 워크숍을 2028년 4월 12일 오후 3시에 등록해줘"], EM,
      date=(1, "2028년 4월 12일"), time=(1, "오후 3시"), cat=None,
      title=E([(1, "품질 워크숍")], superseded=[(1, "안전 교육")], markers=[(1, "말고")]), at="2028-04-12T15:00:00+09:00"),
    S(29, C, "t50_corr_title", ["분기 결산 아니고 사업 계획 회의를 2029년 1월 4일 오후 2시에 넣어줘"], YE,
      date=(1, "2029년 1월 4일"), time=(1, "오후 2시"), cat=None,
      title=E([(1, "사업 계획 회의")], superseded=[(1, "분기 결산")], markers=[(1, "아니고")]), at="2029-01-04T14:00:00+09:00"),
    S(30, C, "t50_corr_cue", ["재고 실사 보고 대신 반품 실사 보고를 내일 오후 4시에 넣어줘"], WM,
      date=(1, "내일"), time=(1, "오후 4시"),
      title=E([(1, "반품 실사 보고")], superseded=[(1, "재고 실사 보고")], markers=[(1, "대신")]),
      cat=E([(1, "반품")], superseded=[(1, "재고")], markers=[(1, "대신")]), at="2028-02-11T16:00:00+09:00"),
    S(31, C, "t50_corr_cue", ["입고 검수 교육 아니고 출고 검수 교육을 모레 오전 10시에 잡아 주세요"], WE,
      date=(1, "모레"), time=(1, "오전 10시"),
      title=E([(1, "출고 검수 교육")], superseded=[(1, "입고 검수 교육")], markers=[(1, "아니고")]),
      cat=E([(1, "출고")], superseded=[(1, "입고")], markers=[(1, "아니고")]), at="2028-03-17T10:00:00+09:00"),
    S(32, C, "t50_marker_after", ["모레 오후 3시에 냉장 설비 점검 넣어줘, 2시 말고"], LD,
      title=(1, "냉장 설비 점검"), date=(1, "모레"), time=(1, "오후 3시"), cat=None,
      at="2028-03-02T15:00:00+09:00", correction_provenance=AFTER),
    S(33, C, "t50_cross_title", ["이번 주 일요일 오후 2시로 하나만 잡아 둬", "피킹 바구니 세척이야, 근데 오후 4시가 좋겠다"], WK,
      title=(2, "피킹 바구니 세척"), date=(1, "이번 주 일요일"), time=(2, "오후 4시"), cat=(2, "피킹"),
      at="2028-05-14T16:00:00+09:00", missing=miss(("task_or_title", 1, 2)),
      target_overrides={1: {"time": E([(1, "오후 2시")])}}, correction_provenance=CROSS,
      expectations=[TITLE_ASK, *OK]),

    # ---- Time safety: 10 ----
    UP(34, "t50_untrusted_unresolved", ["이번 주 금요일 오후 3시에 입고 송장 대조 넣어 줘"], WM,
       title=(1, "입고 송장 대조"), date=(1, "이번 주 금요일"), time=(1, "오후 3시"), cat=(1, "입고")),
    UP(35, "t50_untrusted_unresolved", ["오늘 오후 6시에 현장 출고 마감 회의 넣어줘"], ME,
       title=(1, "현장 출고 마감 회의"), date=(1, "오늘"), time=(1, "오후 6시"), cat=(1, "출고")),
    UP(36, "t50_untrusted_unresolved", ["3월 30일 오전 10시에 반품 처리 기준 회의 잡아줘"], WE,
       title=(1, "반품 처리 기준 회의"), date=(1, "3월 30일"), time=(1, "오전 10시"), cat=(1, "반품")),
    S(37, T, "t50_untrusted_absolute", ["2028년 4월 27일 오후 2시에 재고 감사 대응 회의 등록해줘"], EM,
      trusted=False, title=(1, "재고 감사 대응 회의"), date=(1, "2028년 4월 27일"), time=(1, "오후 2시"), cat=(1, "재고"),
      at="2028-04-27T14:00:00+09:00"),
    S(38, T, "t50_untrusted_absolute", ["2029년 1월 12일 오전 10시에 입고 계획 검토 넣어 주세요"], YE,
      trusted=False, title=(1, "입고 계획 검토"), date=(1, "2029년 1월 12일"), time=(1, "오전 10시"), cat=(1, "입고"),
      at="2029-01-12T10:00:00+09:00"),
    S(39, T, "t50_past", ["2028년 2월 27일 오후 3시에 거래처 미수금 점검 등록해줘"], LD,
      title=(1, "거래처 미수금 점검"), date=(1, "2028년 2월 27일"), time=(1, "오후 3시"), cat=None,
      at="2028-02-27T15:00:00+09:00", final_status="schedule_rejected", expectations=PAST),
    S(40, T, "t50_past", ["오늘 오전 9시에 주말 출고 점검 넣어줘"], WK,
      title=(1, "주말 출고 점검"), date=(1, "오늘"), time=(1, "오전 9시"), cat=(1, "출고"),
      at="2028-05-13T09:00:00+09:00", final_status="schedule_rejected", expectations=PAST),
    S(41, T, "t50_noon_boundary", ["물류팀 간식 나눔 행사, 3월 21일 오후 12시 40분으로 넣어 줘"], WE,
      title=(1, "물류팀 간식 나눔 행사"), date=(1, "3월 21일"), time=(1, "오후 12시 40분"), cat=None,
      at="2028-03-21T12:40:00+09:00"),
    S(42, T, "t50_year_boundary", ["모레 오전 11시에 현장 운영 회의 등록해줘"], YE,
      title=(1, "현장 운영 회의"), date=(1, "모레"), time=(1, "오전 11시"), cat=None,
      at="2029-01-02T11:00:00+09:00"),
    S(43, T, "t50_ambiguous", ["내일 3시에 반품 적재 구역 점검 넣어줘"], EM,
      title=(1, "반품 적재 구역 점검"), date=(1, "내일"), time=(1, "3시"), cat=(1, "반품"), at=None,
      missing=miss(("ampm", 1, None)), final_status="not_executed_pending_info", expectations=[ask("ampm")]),

    # ---- Mixed unsupported + consent: 7 ----
    MX(44, "t50_mixed_grant", ["협력사 납기 점검 내일 오후 3시에 넣고 알림도 켜 줘", "알림은 필요 없으니 진행해 줘"], WM,
       title=(1, "협력사 납기 점검"), date=(1, "내일"), time=(1, "오후 3시"), cat=None,
       at="2028-02-11T15:00:00+09:00", cue="알림도 켜", feature="reminder", result="granted"),
    MX(45, "t50_mixed_grant", ["7월 7일 오전 10시에 재고 실사 결과 보고 등록하고 문자로 보내 줘", "문자 빼고 진행해 주세요"], ME,
       title=(1, "재고 실사 결과 보고"), date=(1, "7월 7일"), time=(1, "오전 10시"), cat=(1, "재고"),
       at="2028-07-07T10:00:00+09:00", cue="문자로 보내", feature="other", result="granted"),
    MX(46, "t50_mixed_grant", ["모레 오후 2시에 출고 차량 타이어 점검 넣고 1시간 전에 알려줘", "알림 없이 그냥 넣어"], LD,
       title=(1, "출고 차량 타이어 점검"), date=(1, "모레"), time=(1, "오후 2시"), cat=(1, "출고"),
       at="2028-03-02T14:00:00+09:00", cue="1시간 전에 알려", feature="reminder", result="granted"),
    MX(47, "t50_mixed_decline", ["물류 창고 견적 비교 회의 내일 오후 4시에 넣고 리마인더도 설정해 줘", "됐어, 일정도 넣지 마"], EM,
       title=(1, "물류 창고 견적 비교 회의"), date=(1, "내일"), time=(1, "오후 4시"), cat=None,
       at="2028-04-05T16:00:00+09:00", cue="리마인더도 설정해", feature="reminder", result="declined"),
    MX(48, "t50_mixed_decline", ["5월 24일 오후 3시에 반품 비용 검토 회의 넣고 푸시도 보내 줘", "아니요, 일정도 등록하지 마세요"], WK,
       title=(1, "반품 비용 검토 회의"), date=(1, "5월 24일"), time=(1, "오후 3시"), cat=(1, "반품"),
       at="2028-05-24T15:00:00+09:00", cue="푸시도 보내", feature="reminder", result="declined"),
    MX(49, "t50_mixed_pending", ["리마인더도 설정해 주고 내일 오전 9시에 창고 안전 순회 등록해줘"], WE,
       title=(1, "창고 안전 순회"), date=(1, "내일"), time=(1, "오전 9시"), cat=None,
       at="2028-03-16T09:00:00+09:00", cue="리마인더도 설정해", feature="reminder", result="pending"),
    MX(50, "t50_mixed_pending", ["2029년 1월 5일 오후 3시에 재고 정책 검토 넣고 미리 알려 주세요"], YE,
       title=(1, "재고 정책 검토"), date=(1, "2029년 1월 5일"), time=(1, "오후 3시"), cat=(1, "재고"),
       at="2029-01-05T15:00:00+09:00", cue="미리 알려", feature="reminder", result="pending"),
]


def authoring_errors(specifications: list[dict[str, Any]]) -> list[str]:
    errors = list(batch_quality_errors(specifications))
    earlier_specs = [*PILOT_SPECS, *B1_SPECS, *B2_SPECS, *B3_SPECS]
    earlier_titles = {item["titleEvidence"]["adoptedSpans"][-1]["text"] for item in earlier_specs}
    earlier_families = {item["family"] for item in earlier_specs}
    for item in specifications:
        title = item["titleEvidence"]["adoptedSpans"][-1]["text"]
        if title in earlier_titles:
            errors.append(f"{item['caseId']}: title {title!r} reused from training data")
        if item["family"] in earlier_families:
            errors.append(f"{item['caseId']}: family {item['family']!r} also used in training data")
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


def _mark_scored_test(case: dict[str, Any]) -> dict[str, Any]:
    """Test rows are scored like locked56; make_case writes the pilot's development_probe exclusion."""
    case["semantics"]["scoring"] = {
        "excludedFromModelScore": False,
        "primaryCauseOrder": case["semantics"]["scoring"]["primaryCauseOrder"],
    }
    case["meta"]["notes"] += ";split=test50"
    return case


def _add_contamination(cases: list[dict[str, Any]]) -> None:
    """locked_comparison rows must carry a contamination record (semantic oracle schema)."""
    sys.path.insert(0, str(ROOT))
    import validator  # noqa: PLC0415  (heavy backend imports; only needed here)

    sources = validator._source_conversations(validator.TEST50_CONFIG.extra_sources)
    checked = ["existing400", "dev12", "locked56", "pilot30", "batch1", "batch2", "batch3"]
    for case in cases:
        text = validator._conversation_text(case)
        best = max((validator._jaccard(text, source) for _id, source in sources), default=0.0)
        case["meta"]["contamination"] = {
            "checkedAgainst": checked,
            "exactMatchFound": any(text == source for _id, source in sources),
            "maxCharTrigramJaccard": round(best, 6),
            "nearDuplicatesReviewed": [],
        }


def main() -> None:
    check_specs(SPECS)
    errors = authoring_errors(SPECS)
    if errors:
        raise RuntimeError("\n".join(errors))
    cases = [_mark_scored_test(_fix_tags(make_case(item), item)) for item in SPECS]
    _add_contamination(cases)
    if len(cases) != CASE_COUNT or len({case["meta"]["caseId"] for case in cases}) != CASE_COUNT:
        raise RuntimeError(f"Test 50 must contain {CASE_COUNT} unique cases")
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
