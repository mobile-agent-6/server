"""Build Candidate-C create batch 6 (65 cross-turn corrections) deterministically.

Status: PROPOSED_UNREVIEWED / BE-PROPOSED.  The 509 training rows had no case
where a later turn corrects an earlier value with a marker ("모레 말고 글피로").
run 006 stopped on that shape (lk52), and a probe on 2026-10-29 got 1 of 13
right.  Every case here keeps the create pending (a missing time, date or
title is asked first) so the correction is still part of the same create;
after a create finishes, a correction is a new update request instead.

scheduledAt values are computed by hand and written per case; the builder
checks them against spec_checks.computed_scheduled_at and stops on any
difference.
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
sys.path.insert(0, str(ROOT / "batch3"))
sys.path.insert(0, str(ROOT.parent / "structured_output_contract_v0.1"))

from build_batch1 import (  # noqa: E402
    ASK_INFO, C, CROSS, OK, EM, LD, ME, WE, WK, WM, YE,
    SPECS as B1_SPECS, _category, _ev, ask, miss,
)
from build_batch2 import SPECS as B2_SPECS, _fix_tags  # noqa: E402
from build_batch3 import SPECS as B3_SPECS  # noqa: E402
from build_pilot import (  # noqa: E402
    SPECS as PILOT_SPECS, canonical, evidence, make_candidate_target, make_case, sha256, spec, turn,
)
from spec_checks import batch_quality_errors, check_specs, computed_scheduled_at  # noqa: E402

OUTPUT = HERE / "conversations-v0.1.jsonl"
TARGETS = HERE / "candidate-c-targets-v0.1.jsonl"
CASE_COUNT = 65
MAX_PER_FAMILY = 5
TITLE_ASK = ask("task_or_title_unclear")
E = evidence


def S(number: int, family: str, texts: list[str], ref: str, *, title: Any, date: Any, time: Any,
      cat: Any, at: str, missing: list[dict[str, Any]], expectations: list[dict[str, Any]],
      t1: dict[str, Any] | None = None, t2: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    turns = [turn(text) if index == 0 else turn(text, answer=index) for index, text in enumerate(texts)]
    overrides = {k: v for k, v in ((1, t1), (2, t2)) if v}
    return spec(
        number, C, family, turns, reference=ref,
        title=_ev(title), date=_ev(date), time=_ev(time), category_evidence=_ev(cat),
        scheduled_at=at, category=_category(cat), missing=missing, expectations=expectations,
        target_overrides=overrides, batch="b6", dataset_role="training_candidate",
        authored_on="2026-10-29", **kwargs,
    )


def NT(number, family, texts, ref, *, title, date, time, cat=None, at, t1):
    """Time missing on turn 1; turn 2 gives the time and corrects the date or title."""
    return S(number, family, texts, ref, title=title, date=date, time=time, cat=cat, at=at,
             missing=miss(("time", 1, 2)), expectations=[ASK_INFO, *OK], t1=t1)


def ND(number, family, texts, ref, *, title, date, time, cat=None, at, t1):
    """Date missing on turn 1; turn 2 gives the date and corrects the time."""
    return S(number, family, texts, ref, title=title, date=date, time=time, cat=cat, at=at,
             missing=miss(("date", 1, 2)), expectations=[ASK_INFO, *OK], t1=t1)


def NTITLE(number, family, texts, ref, *, title, date, time, cat=None, at, t1):
    """Title unclear on turn 1; turn 2 gives the title and corrects the date or time."""
    return S(number, family, texts, ref, title=(2, title), date=date, time=time,
             cat=None if cat is None else (2, cat), at=at,
             missing=miss(("task_or_title", 1, 2)), expectations=[TITLE_ASK, *OK], t1=t1)


def N3(number, family, texts, ref, *, title, date, time, cat=None, at, t2):
    """Date asked, then time asked; turn 3 gives the time and corrects the date."""
    return S(number, family, texts, ref, title=title, date=date, time=time, cat=cat, at=at,
             missing=miss(("date", 1, 2), ("time", 1, 3)), expectations=[ASK_INFO, ASK_INFO, *OK], t2=t2)


SPECS = [
    # ---- Date corrected with "말고" while answering the time: 5 ----
    NT(1, "xt_date_malgo", ["모레 냉동 창고 문 패킹 교체 넣어줘", "오후 2시, 아 모레 말고 2월 17일로"], WM,
       title=(1, "냉동 창고 문 패킹 교체"), time=(2, "오후 2시"),
       date=E([(2, "2월 17일")], superseded=[(2, "모레")], markers=[(2, "말고")]),
       at="2028-02-17T14:00:00+09:00", t1={"date": E([(1, "모레")])}),
    NT(2, "xt_date_malgo", ["내일 반품 검수 인력 회의 잡아줘", "오전 11시요. 근데 내일 말고 모레로 해 주세요"], WE,
       title=(1, "반품 검수 인력 회의"), time=(2, "오전 11시"), cat=(1, "반품"),
       date=E([(2, "모레")], superseded=[(2, "내일")], markers=[(2, "말고")]),
       at="2028-03-17T11:00:00+09:00", t1={"date": E([(1, "내일")])}),
    NT(3, "xt_date_malgo", ["4월 10일에 포장 자재 단가 협의 등록해줘", "오후 3시. 4월 10일 말고 4월 11일로"], EM,
       title=(1, "포장 자재 단가 협의"), time=(2, "오후 3시"), cat=(1, "포장"),
       date=E([(2, "4월 11일")], superseded=[(2, "4월 10일")], markers=[(2, "말고")]),
       at="2028-04-11T15:00:00+09:00", t1={"date": E([(1, "4월 10일")])}),
    NT(4, "xt_date_malgo", ["5월 20일에 출고 동선 재배치 회의 넣어 줘", "오전 10시 반, 그리고 5월 20일 말고 5월 22일"], WK,
       title=(1, "출고 동선 재배치 회의"), time=(2, "오전 10시 반"), cat=(1, "출고"),
       date=E([(2, "5월 22일")], superseded=[(2, "5월 20일")], markers=[(2, "말고")]),
       at="2028-05-22T10:30:00+09:00", t1={"date": E([(1, "5월 20일")])}),
    NT(5, "xt_date_malgo", ["7월 7일에 협력사 계약 갱신 미팅 잡아줘", "오후 4시로 하고 7월 7일 말고 7월 10일로 해 줘"], ME,
       title=(1, "협력사 계약 갱신 미팅"), time=(2, "오후 4시"),
       date=E([(2, "7월 10일")], superseded=[(2, "7월 7일")], markers=[(2, "말고")]),
       at="2028-07-10T16:00:00+09:00", t1={"date": E([(1, "7월 7일")])}),

    # ---- Date corrected with "아니" without repeating the old value: 5 ----
    NT(6, "xt_date_ani", ["모레 창고 배수로 청소 등록해줘", "아니 글피로 해 줘, 오전 9시"], LD,
       title=(1, "창고 배수로 청소"), time=(2, "오전 9시"),
       date=E([(2, "글피")], superseded=[(1, "모레")], markers=[(2, "아니")]),
       at="2028-03-03T09:00:00+09:00", t1={"date": E([(1, "모레")])}),
    NT(7, "xt_date_ani", ["내일 입고 차량 대기열 점검 넣어줘", "아니다, 모레로 하고 오후 1시"], YE,
       title=(1, "입고 차량 대기열 점검"), time=(2, "오후 1시"), cat=(1, "입고"),
       date=E([(2, "모레")], superseded=[(1, "내일")], markers=[(2, "아니다")]),
       at="2029-01-02T13:00:00+09:00", t1={"date": E([(1, "내일")])}),
    NT(8, "xt_date_ani", ["3월 20일 재고 바코드 재발행 잡아 줘", "아니 3월 21일이야. 오후 2시"], WE,
       title=(1, "재고 바코드 재발행"), time=(2, "오후 2시"), cat=(1, "재고"),
       date=E([(2, "3월 21일")], superseded=[(1, "3월 20일")], markers=[(2, "아니")]),
       at="2028-03-21T14:00:00+09:00", t1={"date": E([(1, "3월 20일")])}),
    NT(9, "xt_date_ani", ["2월 14일에 사내 소방 훈련 계획 회의 넣어 줘", "아 아니, 2월 15일이요. 오전 10시요"], WM,
       title=(1, "사내 소방 훈련 계획 회의"), time=(2, "오전 10시"),
       date=E([(2, "2월 15일")], superseded=[(1, "2월 14일")], markers=[(2, "아니")]),
       at="2028-02-15T10:00:00+09:00", t1={"date": E([(1, "2월 14일")])}),
    NT(10, "xt_date_ani", ["모레 피킹 카트 바퀴 교체 등록해 줘", "아니 글피. 시간은 오후 5시"], ME,
       title=(1, "피킹 카트 바퀴 교체"), time=(2, "오후 5시"), cat=(1, "피킹"),
       date=E([(2, "글피")], superseded=[(1, "모레")], markers=[(2, "아니")]),
       at="2028-07-03T17:00:00+09:00", t1={"date": E([(1, "모레")])}),

    # ---- Date corrected with "아니라", polite: 5 ----
    NT(11, "xt_date_anira_polite", ["내일 협력사 공장 방문 등록해 주세요", "오후 3시요. 내일이 아니라 모레예요"], WK,
       title=(1, "협력사 공장 방문"), time=(2, "오후 3시"),
       date=E([(2, "모레")], superseded=[(2, "내일")], markers=[(2, "아니라")]),
       at="2028-05-15T15:00:00+09:00", t1={"date": E([(1, "내일")])}),
    NT(12, "xt_date_anira_polite", ["3월 6일 출고 라벨 프린터 점검 넣어 주세요", "오전 10시요, 3월 6일이 아니라 3월 7일이에요"], LD,
       title=(1, "출고 라벨 프린터 점검"), time=(2, "오전 10시"), cat=(1, "출고"),
       date=E([(2, "3월 7일")], superseded=[(2, "3월 6일")], markers=[(2, "아니라")]),
       at="2028-03-07T10:00:00+09:00", t1={"date": E([(1, "3월 6일")])}),
    NT(13, "xt_date_anira_polite", ["4월 17일 재고 감사 사전 회의 잡아 주세요", "오후 2시요. 4월 17일 아니라 4월 18일이요"], EM,
       title=(1, "재고 감사 사전 회의"), time=(2, "오후 2시"), cat=(1, "재고"),
       date=E([(2, "4월 18일")], superseded=[(2, "4월 17일")], markers=[(2, "아니라")]),
       at="2028-04-18T14:00:00+09:00", t1={"date": E([(1, "4월 17일")])}),
    NT(14, "xt_date_anira_polite", ["글피 하역 장비 대여 협의 넣어 주세요", "오전 11시요, 글피가 아니라 모레예요"], WE,
       title=(1, "하역 장비 대여 협의"), time=(2, "오전 11시"),
       date=E([(2, "모레")], superseded=[(2, "글피")], markers=[(2, "아니라")]),
       at="2028-03-17T11:00:00+09:00", t1={"date": E([(1, "글피")])}),
    NT(15, "xt_date_anira_polite", ["모레 연간 물류 예산 회의 등록해 주세요", "오후 3시요. 모레가 아니라 글피예요"], YE,
       title=(1, "연간 물류 예산 회의"), time=(2, "오후 3시"),
       date=E([(2, "글피")], superseded=[(2, "모레")], markers=[(2, "아니라")]),
       at="2029-01-03T15:00:00+09:00", t1={"date": E([(1, "모레")])}),

    # ---- Date corrected with "대신": 5 ----
    NT(16, "xt_date_daesin", ["내일 컨테이너 하차 일정 회의 넣어줘", "내일 대신 모레, 오후 1시"], WM,
       title=(1, "컨테이너 하차 일정 회의"), time=(2, "오후 1시"),
       date=E([(2, "모레")], superseded=[(2, "내일")], markers=[(2, "대신")]),
       at="2028-02-12T13:00:00+09:00", t1={"date": E([(1, "내일")])}),
    NT(17, "xt_date_daesin", ["5월 22일 반품 회수 기사 미팅 잡아줘", "오후 4시, 5월 22일 대신 5월 23일로"], WK,
       title=(1, "반품 회수 기사 미팅"), time=(2, "오후 4시"), cat=(1, "반품"),
       date=E([(2, "5월 23일")], superseded=[(2, "5월 22일")], markers=[(2, "대신")]),
       at="2028-05-23T16:00:00+09:00", t1={"date": E([(1, "5월 22일")])}),
    NT(18, "xt_date_daesin", ["내일 입고 서류 전산화 회의 등록해줘", "오전 9시 반, 내일 대신 모레"], ME,
       title=(1, "입고 서류 전산화 회의"), time=(2, "오전 9시 반"), cat=(1, "입고"),
       date=E([(2, "모레")], superseded=[(2, "내일")], markers=[(2, "대신")]),
       at="2028-07-02T09:30:00+09:00", t1={"date": E([(1, "내일")])}),
    NT(19, "xt_date_daesin", ["3월 9일 지게차 면허 교육 넣어 줘", "3월 9일 대신 3월 10일로 하고 오후 2시"], LD,
       title=(1, "지게차 면허 교육"), time=(2, "오후 2시"),
       date=E([(2, "3월 10일")], superseded=[(2, "3월 9일")], markers=[(2, "대신")]),
       at="2028-03-10T14:00:00+09:00", t1={"date": E([(1, "3월 9일")])}),
    NT(20, "xt_date_daesin", ["4월 12일 포장 라인 증설 검토 잡아 줘", "오전 10시. 4월 12일 대신 4월 13일"], EM,
       title=(1, "포장 라인 증설 검토"), time=(2, "오전 10시"), cat=(1, "포장"),
       date=E([(2, "4월 13일")], superseded=[(2, "4월 12일")], markers=[(2, "대신")]),
       at="2028-04-13T10:00:00+09:00", t1={"date": E([(1, "4월 12일")])}),

    # ---- Time corrected with "말고" while answering the date: 5 ----
    ND(21, "xt_time_malgo", ["오후 3시에 재고 이관 승인 회의 넣어줘", "모레. 오후 3시 말고 오후 4시로"], WE,
       title=(1, "재고 이관 승인 회의"), date=(2, "모레"), cat=(1, "재고"),
       time=E([(2, "오후 4시")], superseded=[(2, "오후 3시")], markers=[(2, "말고")]),
       at="2028-03-17T16:00:00+09:00", t1={"time": E([(1, "오후 3시")])}),
    ND(22, "xt_time_malgo", ["오전 10시에 협력사 품질 감사 대응 회의 잡아줘", "내일, 그리고 오전 10시 말고 오전 11시"], LD,
       title=(1, "협력사 품질 감사 대응 회의"), date=(2, "내일"),
       time=E([(2, "오전 11시")], superseded=[(2, "오전 10시")], markers=[(2, "말고")]),
       at="2028-03-01T11:00:00+09:00", t1={"time": E([(1, "오전 10시")])}),
    ND(23, "xt_time_malgo", ["오후 2시에 출고 차량 정비 점검 등록해줘", "5월 24일요. 오후 2시 말고 오후 3시 반으로요"], WK,
       title=(1, "출고 차량 정비 점검"), date=(2, "5월 24일"), cat=(1, "출고"),
       time=E([(2, "오후 3시 반")], superseded=[(2, "오후 2시")], markers=[(2, "말고")]),
       at="2028-05-24T15:30:00+09:00", t1={"time": E([(1, "오후 2시")])}),
    ND(24, "xt_time_malgo", ["오전 9시에 창고 보안 카메라 교체 넣어 줘", "글피로 하고, 오전 9시 말고 오전 8시"], ME,
       title=(1, "창고 보안 카메라 교체"), date=(2, "글피"),
       time=E([(2, "오전 8시")], superseded=[(2, "오전 9시")], markers=[(2, "말고")]),
       at="2028-07-03T08:00:00+09:00", t1={"time": E([(1, "오전 9시")])}),
    ND(25, "xt_time_malgo", ["오후 1시에 입고 검품 기준 개정 회의 잡아 줘", "2029년 1월 4일, 오후 1시 말고 오후 2시"], YE,
       title=(1, "입고 검품 기준 개정 회의"), date=(2, "2029년 1월 4일"), cat=(1, "입고"),
       time=E([(2, "오후 2시")], superseded=[(2, "오후 1시")], markers=[(2, "말고")]),
       at="2029-01-04T14:00:00+09:00", t1={"time": E([(1, "오후 1시")])}),

    # ---- Time corrected with "아니" without repeating the old value: 5 ----
    ND(26, "xt_time_ani", ["오전 10시에 피킹 리스트 개선 회의 넣어줘", "모레, 아니 시간은 오전 11시로"], WM,
       title=(1, "피킹 리스트 개선 회의"), date=(2, "모레"), cat=(1, "피킹"),
       time=E([(2, "오전 11시")], superseded=[(1, "오전 10시")], markers=[(2, "아니")]),
       at="2028-02-12T11:00:00+09:00", t1={"time": E([(1, "오전 10시")])}),
    ND(27, "xt_time_ani", ["오후 4시에 반품 사유 코드 정비 잡아줘", "4월 6일. 아니다, 오후 5시로 해 줘"], EM,
       title=(1, "반품 사유 코드 정비"), date=(2, "4월 6일"), cat=(1, "반품"),
       time=E([(2, "오후 5시")], superseded=[(1, "오후 4시")], markers=[(2, "아니다")]),
       at="2028-04-06T17:00:00+09:00", t1={"time": E([(1, "오후 4시")])}),
    ND(28, "xt_time_ani", ["오후 2시에 화물 보험 갱신 상담 등록해줘", "내일이요. 아 아니, 오후 3시로요"], WE,
       title=(1, "화물 보험 갱신 상담"), date=(2, "내일"),
       time=E([(2, "오후 3시")], superseded=[(1, "오후 2시")], markers=[(2, "아니")]),
       at="2028-03-16T15:00:00+09:00", t1={"time": E([(1, "오후 2시")])}),
    ND(29, "xt_time_ani", ["오전 8시에 하역장 출입문 점검 넣어 줘", "글피. 아니 시간은 오전 9시"], WK,
       title=(1, "하역장 출입문 점검"), date=(2, "글피"),
       time=E([(2, "오전 9시")], superseded=[(1, "오전 8시")], markers=[(2, "아니")]),
       at="2028-05-16T09:00:00+09:00", t1={"time": E([(1, "오전 8시")])}),
    ND(30, "xt_time_ani", ["오후 5시에 재고 실사 결과 보고 잡아 줘", "7월 3일, 아니 시간은 오후 4시로"], ME,
       title=(1, "재고 실사 결과 보고"), date=(2, "7월 3일"), cat=(1, "재고"),
       time=E([(2, "오후 4시")], superseded=[(1, "오후 5시")], markers=[(2, "아니")]),
       at="2028-07-03T16:00:00+09:00", t1={"time": E([(1, "오후 5시")])}),

    # ---- Title unclear; turn 2 gives the title and corrects the time: 5 ----
    NTITLE(31, "xt_title_unclear_time", ["내일 오후 2시에 그거 잡아 줘", "출고 일정 조율 회의, 오후 2시 말고 오후 3시로"], WK,
           title="출고 일정 조율 회의", date=(1, "내일"), cat="출고",
           time=E([(2, "오후 3시")], superseded=[(2, "오후 2시")], markers=[(2, "말고")]),
           at="2028-05-14T15:00:00+09:00", t1={"time": E([(1, "오후 2시")])}),
    NTITLE(32, "xt_title_unclear_time", ["모레 오전 10시에 그 일 좀 등록해 줘", "협력사 정산 검토야. 아니 시간은 오전 11시"], LD,
           title="협력사 정산 검토", date=(1, "모레"),
           time=E([(2, "오전 11시")], superseded=[(1, "오전 10시")], markers=[(2, "아니")]),
           at="2028-03-02T11:00:00+09:00", t1={"time": E([(1, "오전 10시")])}),
    NTITLE(33, "xt_title_unclear_time", ["4월 7일 오후 1시에 이거 등록해 주세요", "포장 박스 규격 회의요, 오후 1시 대신 오후 2시요"], EM,
           title="포장 박스 규격 회의", date=(1, "4월 7일"), cat="포장",
           time=E([(2, "오후 2시")], superseded=[(2, "오후 1시")], markers=[(2, "대신")]),
           at="2028-04-07T14:00:00+09:00", t1={"time": E([(1, "오후 1시")])}),
    NTITLE(34, "xt_title_unclear_time", ["글피 오후 4시로 뭐 하나 잡아줘", "재고 조정 승인 회의고 오후 4시 아니라 오후 5시"], WE,
           title="재고 조정 승인 회의", date=(1, "글피"), cat="재고",
           time=E([(2, "오후 5시")], superseded=[(2, "오후 4시")], markers=[(2, "아니라")]),
           at="2028-03-18T17:00:00+09:00", t1={"time": E([(1, "오후 4시")])}),
    NTITLE(35, "xt_title_unclear_time", ["7월 5일 오전 9시에 그 건 넣어줘", "입고 예약 시스템 교육, 근데 오전 9시 말고 오전 10시"], ME,
           title="입고 예약 시스템 교육", date=(1, "7월 5일"), cat="입고",
           time=E([(2, "오전 10시")], superseded=[(2, "오전 9시")], markers=[(2, "말고")]),
           at="2028-07-05T10:00:00+09:00", t1={"time": E([(1, "오전 9시")])}),

    # ---- Title unclear; turn 2 gives the title and corrects the date: 5 ----
    NTITLE(36, "xt_title_unclear_date", ["내일 오후 3시에 그거 넣어줘", "반품 창고 정리, 날짜는 내일 말고 모레"], WM,
           title="반품 창고 정리", time=(1, "오후 3시"), cat="반품",
           date=E([(2, "모레")], superseded=[(2, "내일")], markers=[(2, "말고")]),
           at="2028-02-12T15:00:00+09:00", t1={"date": E([(1, "내일")])}),
    NTITLE(37, "xt_title_unclear_date", ["모레 오전 11시에 하나 잡아 주세요", "거래처 신규 계약 설명회예요. 모레가 아니라 글피예요"], YE,
           title="거래처 신규 계약 설명회", time=(1, "오전 11시"),
           date=E([(2, "글피")], superseded=[(2, "모레")], markers=[(2, "아니라")]),
           at="2029-01-03T11:00:00+09:00", t1={"date": E([(1, "모레")])}),
    NTITLE(38, "xt_title_unclear_date", ["5월 19일 오후 2시로 이거 등록해 줘", "피킹 존 재배치 회의, 아니 날짜는 5월 20일"], WK,
           title="피킹 존 재배치 회의", time=(1, "오후 2시"), cat="피킹",
           date=E([(2, "5월 20일")], superseded=[(1, "5월 19일")], markers=[(2, "아니")]),
           at="2028-05-20T14:00:00+09:00", t1={"date": E([(1, "5월 19일")])}),
    NTITLE(39, "xt_title_unclear_date", ["3월 8일 오전 10시에 그것 좀 넣어줘", "출고 검수 인력 충원 회의야. 3월 8일 대신 3월 9일로"], LD,
           title="출고 검수 인력 충원 회의", time=(1, "오전 10시"), cat="출고",
           date=E([(2, "3월 9일")], superseded=[(2, "3월 8일")], markers=[(2, "대신")]),
           at="2028-03-09T10:00:00+09:00", t1={"date": E([(1, "3월 8일")])}),
    NTITLE(40, "xt_title_unclear_date", ["4월 20일 오후 3시에 뭐 좀 잡아 줘", "창고 임대료 협상, 4월 20일 말고 4월 21일로 해 줘"], EM,
           title="창고 임대료 협상", time=(1, "오후 3시"),
           date=E([(2, "4월 21일")], superseded=[(2, "4월 20일")], markers=[(2, "말고")]),
           at="2028-04-21T15:00:00+09:00", t1={"date": E([(1, "4월 20일")])}),

    # ---- Title corrected with "말고" while answering the time: 5 ----
    NT(41, "xt_title_malgo", ["모레 협력사 견적 회의 넣어줘", "오후 2시. 그리고 협력사 견적 회의 말고 협력사 단가 조정 회의로"], WE,
       date=(1, "모레"), time=(2, "오후 2시"),
       title=E([(2, "협력사 단가 조정 회의")], superseded=[(2, "협력사 견적 회의")], markers=[(2, "말고")]),
       at="2028-03-17T14:00:00+09:00", t1={"title": E([(1, "협력사 견적 회의")])}),
    NT(42, "xt_title_malgo", ["내일 창고 소독 작업 잡아줘", "오전 9시요. 창고 소독 작업 말고 창고 방역 점검이요"], ME,
       date=(1, "내일"), time=(2, "오전 9시"),
       title=E([(2, "창고 방역 점검")], superseded=[(2, "창고 소독 작업")], markers=[(2, "말고")]),
       at="2028-07-01T09:00:00+09:00", t1={"title": E([(1, "창고 소독 작업")])}),
    NT(43, "xt_title_malgo", ["글피 거래처 감사 준비 회의 등록해줘", "오후 3시, 거래처 감사 준비 회의 말고 거래처 감사 결과 공유로 해 줘"], WM,
       date=(1, "글피"), time=(2, "오후 3시"),
       title=E([(2, "거래처 감사 결과 공유")], superseded=[(2, "거래처 감사 준비 회의")], markers=[(2, "말고")]),
       at="2028-02-13T15:00:00+09:00", t1={"title": E([(1, "거래처 감사 준비 회의")])}),
    NT(44, "xt_title_malgo", ["5월 25일 운송사 단가 비교 넣어줘", "오전 11시, 운송사 단가 비교 말고 운송사 계약 검토로"], WK,
       date=(1, "5월 25일"), time=(2, "오전 11시"),
       title=E([(2, "운송사 계약 검토")], superseded=[(2, "운송사 단가 비교")], markers=[(2, "말고")]),
       at="2028-05-25T11:00:00+09:00", t1={"title": E([(1, "운송사 단가 비교")])}),
    NT(45, "xt_title_malgo", ["4월 14일 사무실 이전 회의 잡아 줘", "오후 4시로. 사무실 이전 회의 말고 사무실 배치 회의"], EM,
       date=(1, "4월 14일"), time=(2, "오후 4시"),
       title=E([(2, "사무실 배치 회의")], superseded=[(2, "사무실 이전 회의")], markers=[(2, "말고")]),
       at="2028-04-14T16:00:00+09:00", t1={"title": E([(1, "사무실 이전 회의")])}),

    # ---- Title corrected with "아니" without repeating the old title: 5 ----
    NT(46, "xt_title_ani", ["내일 직원 건강 검진 안내 등록해 줘", "오후 1시. 아 아니, 제목은 직원 독감 접종 안내로"], LD,
       date=(1, "내일"), time=(2, "오후 1시"),
       title=E([(2, "직원 독감 접종 안내")], superseded=[(1, "직원 건강 검진 안내")], markers=[(2, "아니")]),
       at="2028-03-01T13:00:00+09:00", t1={"title": E([(1, "직원 건강 검진 안내")])}),
    NT(47, "xt_title_ani", ["모레 거래처 저녁 모임 넣어 줘", "오후 6시. 아니다, 거래처 간담회로 해 줘"], WE,
       date=(1, "모레"), time=(2, "오후 6시"),
       title=E([(2, "거래처 간담회")], superseded=[(1, "거래처 저녁 모임")], markers=[(2, "아니다")]),
       at="2028-03-17T18:00:00+09:00", t1={"title": E([(1, "거래처 저녁 모임")])}),
    NT(48, "xt_title_ani", ["글피 전산 장비 점검 잡아줘", "오전 10시, 아니 서버 장비 점검이야"], WK,
       date=(1, "글피"), time=(2, "오전 10시"),
       title=E([(2, "서버 장비 점검")], superseded=[(1, "전산 장비 점검")], markers=[(2, "아니")]),
       at="2028-05-16T10:00:00+09:00", t1={"title": E([(1, "전산 장비 점검")])}),
    NT(49, "xt_title_ani", ["7월 6일 물류 세미나 준비 등록해 주세요", "오후 2시요. 아니 물류 세미나 리허설이에요"], ME,
       date=(1, "7월 6일"), time=(2, "오후 2시"),
       title=E([(2, "물류 세미나 리허설")], superseded=[(1, "물류 세미나 준비")], markers=[(2, "아니")]),
       at="2028-07-06T14:00:00+09:00", t1={"title": E([(1, "물류 세미나 준비")])}),
    NT(50, "xt_title_ani", ["3월 22일 협력사 만족도 조사 넣어줘", "오전 11시. 아니, 협력사 만족도 결과 회의로"], WE,
       date=(1, "3월 22일"), time=(2, "오전 11시"),
       title=E([(2, "협력사 만족도 결과 회의")], superseded=[(1, "협력사 만족도 조사")], markers=[(2, "아니")]),
       at="2028-03-22T11:00:00+09:00", t1={"title": E([(1, "협력사 만족도 조사")])}),

    # ---- Date asked, time asked, turn 3 corrects the date: 5 ----
    N3(51, "xt3_date_then_time", ["창고 조명 배선 점검 넣어줘", "내일로 잡아", "오후 2시, 아 내일 말고 모레"], WM,
       title=(1, "창고 조명 배선 점검"), time=(3, "오후 2시"),
       date=E([(3, "모레")], superseded=[(3, "내일")], markers=[(3, "말고")]),
       at="2028-02-12T14:00:00+09:00", t2={"date": E([(2, "내일")])}),
    N3(52, "xt3_date_then_time", ["출고 운송장 양식 회의 잡아줘", "4월 10일로", "오전 10시. 4월 10일 말고 4월 11일"], EM,
       title=(1, "출고 운송장 양식 회의"), time=(3, "오전 10시"), cat=(1, "출고"),
       date=E([(3, "4월 11일")], superseded=[(3, "4월 10일")], markers=[(3, "말고")]),
       at="2028-04-11T10:00:00+09:00", t2={"date": E([(2, "4월 10일")])}),
    N3(53, "xt3_date_then_time", ["사내 물류 공모전 심사 등록해 줘", "5월 30일이 좋겠어", "오후 3시요, 아니 날짜는 6월 2일로 해 주세요"], WK,
       title=(1, "사내 물류 공모전 심사"), time=(3, "오후 3시"),
       date=E([(3, "6월 2일")], superseded=[(2, "5월 30일")], markers=[(3, "아니")]),
       at="2028-06-02T15:00:00+09:00", t2={"date": E([(2, "5월 30일")])}),
    N3(54, "xt3_date_then_time", ["거래처 정기 점검 미팅 넣어 줘", "7월 4일", "오후 2시, 7월 4일 대신 7월 5일"], ME,
       title=(1, "거래처 정기 점검 미팅"), time=(3, "오후 2시"),
       date=E([(3, "7월 5일")], superseded=[(3, "7월 4일")], markers=[(3, "대신")]),
       at="2028-07-05T14:00:00+09:00", t2={"date": E([(2, "7월 4일")])}),
    N3(55, "xt3_date_then_time", ["창고 지붕 방수 공사 잡아줘", "글피", "오전 10시. 아니다, 날짜는 모레로"], LD,
       title=(1, "창고 지붕 방수 공사"), time=(3, "오전 10시"),
       date=E([(3, "모레")], superseded=[(2, "글피")], markers=[(3, "아니다")]),
       at="2028-03-02T10:00:00+09:00", t2={"date": E([(2, "글피")])}),

    # ---- Correction without a marker ("날짜는 X로 바꿔 줘"): only the new value is evidence: 5 ----
    NT(56, "xt_markerless", ["모레 출고 오류 재발 방지 회의 넣어줘", "날짜는 3월 21일로 해 주고 시간은 오후 2시"], WE,
       title=(1, "출고 오류 재발 방지 회의"), date=(2, "3월 21일"), time=(2, "오후 2시"), cat=(1, "출고"),
       at="2028-03-21T14:00:00+09:00", t1={"date": E([(1, "모레")])}),
    NT(57, "xt_markerless", ["내일 거래처 샘플 검토 잡아줘", "오전 10시요. 날짜는 모레로 해 주세요"], LD,
       title=(1, "거래처 샘플 검토"), date=(2, "모레"), time=(2, "오전 10시"),
       at="2028-03-02T10:00:00+09:00", t1={"date": E([(1, "내일")])}),
    ND(58, "xt_markerless", ["오후 3시에 재고 위치 코드 정비 등록해 줘", "모레, 시간은 오후 4시로 바꿔 줘"], WK,
       title=(1, "재고 위치 코드 정비"), date=(2, "모레"), time=(2, "오후 4시"), cat=(1, "재고"),
       at="2028-05-15T16:00:00+09:00", t1={"time": E([(1, "오후 3시")])}),
    NT(59, "xt_markerless", ["4월 26일 반품 검수 절차 교육 넣어 줘", "오후 1시. 날짜는 4월 27일로 변경해 줘"], EM,
       title=(1, "반품 검수 절차 교육"), date=(2, "4월 27일"), time=(2, "오후 1시"), cat=(1, "반품"),
       at="2028-04-27T13:00:00+09:00", t1={"date": E([(1, "4월 26일")])}),
    NT(60, "xt_markerless", ["글피 하역장 출입 통제 회의 잡아 주세요", "오전 11시요. 날짜는 모레로 옮겨 주세요"], ME,
       title=(1, "하역장 출입 통제 회의"), date=(2, "모레"), time=(2, "오전 11시"),
       at="2028-07-02T11:00:00+09:00", t1={"date": E([(1, "글피")])}),

    # ---- Date corrected with "말고", polite: 5 ----
    NT(61, "xt_date_malgo_polite", ["모레 신규 거래처 등록 심사 넣어 주세요", "오후 2시요. 그런데 모레 말고 2월 22일로 해 주세요"], WM,
       title=(1, "신규 거래처 등록 심사"), time=(2, "오후 2시"),
       date=E([(2, "2월 22일")], superseded=[(2, "모레")], markers=[(2, "말고")]),
       at="2028-02-22T14:00:00+09:00", t1={"date": E([(1, "모레")])}),
    NT(62, "xt_date_malgo_polite", ["3월 23일 입고 저울 교정 등록해 주세요", "오전 10시로요. 3월 23일 말고 3월 24일이요"], WE,
       title=(1, "입고 저울 교정"), time=(2, "오전 10시"), cat=(1, "입고"),
       date=E([(2, "3월 24일")], superseded=[(2, "3월 23일")], markers=[(2, "말고")]),
       at="2028-03-24T10:00:00+09:00", t1={"date": E([(1, "3월 23일")])}),
    NT(63, "xt_date_malgo_polite", ["내일 출고 지연 고객 응대 회의 잡아 주세요", "오후 4시요, 내일 말고 모레로 부탁해요"], EM,
       title=(1, "출고 지연 고객 응대 회의"), time=(2, "오후 4시"), cat=(1, "출고"),
       date=E([(2, "모레")], superseded=[(2, "내일")], markers=[(2, "말고")]),
       at="2028-04-06T16:00:00+09:00", t1={"date": E([(1, "내일")])}),
    NT(64, "xt_date_malgo_polite", ["6월 2일 창고 방충 작업 넣어 주세요", "오전 9시요. 6월 2일 말고 6월 3일로요"], WK,
       title=(1, "창고 방충 작업"), time=(2, "오전 9시"),
       date=E([(2, "6월 3일")], superseded=[(2, "6월 2일")], markers=[(2, "말고")]),
       at="2028-06-03T09:00:00+09:00", t1={"date": E([(1, "6월 2일")])}),
    NT(65, "xt_date_malgo_polite", ["글피 재고 평가 기준 회의 등록해 주세요", "오후 3시요. 글피 말고 모레로 해 주세요"], YE,
       title=(1, "재고 평가 기준 회의"), time=(2, "오후 3시"), cat=(1, "재고"),
       date=E([(2, "모레")], superseded=[(2, "글피")], markers=[(2, "말고")]),
       at="2029-01-02T15:00:00+09:00", t1={"date": E([(1, "글피")])}),
]

for _item in SPECS:
    if _item["family"] == "xt_markerless":
        _item["correctionProvenance"] = CROSS


def authoring_errors(specifications: list[dict[str, Any]]) -> list[str]:
    errors = list(batch_quality_errors(specifications))
    earlier = {
        item["titleEvidence"]["adoptedSpans"][-1]["text"]
        for item in [*PILOT_SPECS, *B1_SPECS, *B2_SPECS, *B3_SPECS]
    }
    for item in specifications:
        title = item["titleEvidence"]["adoptedSpans"][-1]["text"]
        if title in earlier:
            errors.append(f"{item['caseId']}: title {title!r} reused from an earlier batch")
        # Hand-computed scheduledAt must match the shared calculator.
        computed = computed_scheduled_at(item)
        if computed != item["scheduledAt"]:
            errors.append(f"{item['caseId']}: scheduledAt {item['scheduledAt']} != computed {computed}")
        # Every case keeps the create pending until the correcting turn.
        if len(item["turns"]) < 2 or not item["missing"]:
            errors.append(f"{item['caseId']}: correction must happen while the create is pending")
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
        raise RuntimeError(f"batch 6 must contain {CASE_COUNT} unique cases")
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
