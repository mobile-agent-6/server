from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.assistant.decisions import DecisionContext
from app.modules.assistant.evidence import texts
from app.modules.assistant.evidence.contract import EvidenceContractError
from app.modules.assistant.evidence.gate import Answer, classify_answer
from app.modules.assistant.evidence.normalize import SEOUL
from app.modules.assistant.evidence.provider import EvidenceDecisionProvider
from app.modules.assistant.schedule_read import ScheduleRead
from app.modules.schedules.category import Category

# Thursday 2026-10-29 10:00 in Seoul.
REFERENCE = datetime(2026, 10, 29, 10, 0, tzinfo=SEOUL)


class FakeReader:
    def __init__(self, schedules: list[ScheduleRead] | None = None) -> None:
        self.schedules = schedules or []
        self.reads: list[dict] = []

    def get(self, schedule_id: UUID) -> ScheduleRead | None:
        return next((s for s in self.schedules if s.id == schedule_id), None)

    def list(self, *, from_utc, to_utc, category, completed, limit):
        self.reads.append({"from": from_utc, "to": to_utc, "completed": completed, "limit": limit})
        rows = [
            s
            for s in self.schedules
            if from_utc <= s.scheduled_at < to_utc and (completed is None or s.completed is completed)
        ]
        return rows[:limit]


class ScriptedModel:
    def __init__(self, *outputs: dict) -> None:
        self.outputs = list(outputs)
        self.seen: list[list[str]] = []

    def extract(self, *, turns, reference_time, server_time_trusted) -> str:
        self.seen.append(list(turns))
        return json.dumps(self.outputs.pop(0), ensure_ascii=False)


def schedule(title: str, when: datetime, *, completed: bool = False) -> ScheduleRead:
    return ScheduleRead(
        id=uuid4(),
        title=title,
        scheduled_at=when.astimezone(timezone.utc),
        category=Category.OTHER,
        completed=completed,
        updated_at=REFERENCE.astimezone(timezone.utc),
    )


def span(text: str, turn: int = 1, status: str = "adopted") -> dict:
    return {"turn": turn, "text": text, "status": status}


def output(*operations: dict, unsupported: list | None = None) -> dict:
    return {"operations": list(operations), "unsupportedEvidence": unsupported or [], "missingInformation": []}


def op(kind: str, index: int = 1, **evidence: dict) -> dict:
    return {
        "index": index,
        "kind": kind,
        "targetEvidence": evidence.get("target", {}),
        "changeEvidence": evidence.get("change", {}),
        "filterEvidence": evidence.get("filter", {}),
        "unsupportedEvidence": [],
        "missingInformation": [],
    }


def context(text: str, *, follow_up: bool = False, state: object | None = None, trusted: bool = True) -> DecisionContext:
    return DecisionContext(
        request_id=uuid4(),
        conversation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        input={"type": "text", "text": text},
        task_reference_accepted_at=REFERENCE.astimezone(timezone.utc),
        server_time_trusted=trusted,
        is_question_follow_up=follow_up,
        answered_question_kind="need_more_info" if follow_up else None,
        control_state_version=1 if state is not None else None,
        control_state=state,
    )


def decide(provider, text, previous=None, **kwargs):
    state = previous.control_state if previous is not None else None
    envelope = provider.decide(context(text, follow_up=previous is not None, state=state, **kwargs))
    return envelope, envelope.decision.model_dump(mode="json", by_alias=True, exclude_none=True)


# ------------------------------------------------------------------ create


def test_complete_create() -> None:
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span("입고 검수")],
                    "date": [span("내일")],
                    "time": [span("오후 3시 반")],
                    "category": [span("입고")],
                },
            )
        )
    )
    envelope, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "내일 오후 3시 반에 입고 검수 잡아 줘")
    assert decision["kind"] == "create_intent"
    assert decision["title"] == "입고 검수"
    assert decision["category"] == "INBOUND"
    assert datetime.fromisoformat(decision["scheduledAt"]) == datetime(2026, 10, 30, 15, 30, tzinfo=SEOUL)
    assert decision["timeInterpretation"] == "relative"
    assert envelope.control_state["turns"] == []  # cleared once the task ends


def test_category_from_title_and_default_other() -> None:
    model = ScriptedModel(
        output(op("create", change={"title": [span("반품 상자 검수")], "date": [span("내일")], "time": [span("오후 2시")]})),
        output(op("create", change={"title": [span("운영 회의")], "date": [span("내일")], "time": [span("오후 2시")]})),
    )
    provider = EvidenceDecisionProvider(model, FakeReader())
    assert decide(provider, "내일 오후 2시에 반품 상자 검수 잡아 줘")[1]["category"] == "RETURN"
    assert decide(provider, "내일 오후 2시에 운영 회의 잡아 줘")[1]["category"] == "OTHER"


def test_missing_time_then_follow_up() -> None:
    model = ScriptedModel(
        output(op("create", change={"title": [span("회의")], "date": [span("내일")]})),
        output(op("create", change={"title": [span("회의")], "date": [span("내일")], "time": [span("오후 4시", turn=2)]})),
    )
    provider = EvidenceDecisionProvider(model, FakeReader())
    first, decision = decide(provider, "내일 회의 잡아 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_TIME}
    _, decision = decide(provider, "오후 4시", previous=first)
    assert decision["kind"] == "create_intent"
    assert model.seen[1] == ["내일 회의 잡아 줘", "오후 4시"]


def test_past_date_without_year_is_confirmed_before_using_next_year() -> None:
    evidence = output(op("create", change={"title": [span("회의")], "date": [span("3월 5일")], "time": [span("오후 2시")]}))
    model = ScriptedModel(evidence, evidence)
    provider = EvidenceDecisionProvider(model, FakeReader())
    first, decision = decide(provider, "3월 5일 오후 2시 회의 잡아 줘")
    assert decision["text"] == texts.confirm_next_year(datetime(2027, 3, 5).date())
    _, decision = decide(provider, "응", previous=first)
    assert datetime.fromisoformat(decision["scheduledAt"]) == datetime(2027, 3, 5, 14, 0, tzinfo=SEOUL)


def test_declined_date_confirmation_asks_for_the_date_again() -> None:
    evidence = output(op("create", change={"title": [span("회의")], "date": [span("3월 5일")], "time": [span("오후 2시")]}))
    provider = EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader())
    first, _ = decide(provider, "3월 5일 오후 2시 회의 잡아 줘")
    _, decision = decide(provider, "아니", previous=first)
    assert decision == {"kind": "ask", "text": texts.ASK_DATE_AGAIN}


def test_evening_twelve_asks_about_midnight() -> None:
    evidence = output(op("create", change={"title": [span("재고 실사")], "date": [span("내일")], "time": [span("밤 12시")]}))
    provider = EvidenceDecisionProvider(ScriptedModel(evidence, evidence), FakeReader())
    first, decision = decide(provider, "내일 밤 12시에 재고 실사 잡아 줘")
    assert decision["text"] == texts.confirm_midnight(datetime(2026, 10, 31).date())
    _, decision = decide(provider, "응", previous=first)
    assert datetime.fromisoformat(decision["scheduledAt"]) == datetime(2026, 10, 31, 0, 0, tzinfo=SEOUL)


def test_untrusted_time_keeps_turns_for_the_absolute_date_answer() -> None:
    evidence = output(op("create", change={"title": [span("회의")], "date": [span("내일")], "time": [span("오후 2시")]}))
    provider = EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader())
    envelope, decision = decide(provider, "내일 오후 2시 회의 잡아 줘", trusted=False)
    assert decision["timeInterpretation"] == "relative"
    assert envelope.control_state["turns"] == ["내일 오후 2시 회의 잡아 줘"]


# ----------------------------------------------- unsupported and consent


def test_unsupported_request_asks_consent_then_creates() -> None:
    model = ScriptedModel(
        output(op("create", change={"title": [span("회의")], "date": [span("내일")], "time": [span("오후 4시")]}))
    )
    provider = EvidenceDecisionProvider(model, FakeReader())
    first, decision = decide(provider, "내일 오후 4시에 회의 등록하고 알림도 켜 줘")
    assert decision == {"kind": "ask", "text": texts.CONSENT_QUESTION}
    assert model.seen == []  # no model call before consent
    _, decision = decide(provider, "문자 없이 등록만 해 주세요", previous=first)
    assert decision["kind"] == "create_intent"


def test_declined_consent_cancels() -> None:
    provider = EvidenceDecisionProvider(ScriptedModel(), FakeReader())
    first, _ = decide(provider, "내일 오후 4시에 회의 등록하고 알림도 켜 줘")
    _, decision = decide(provider, "아니, 그럼 등록하지 마", previous=first)
    assert decision == {"kind": "cancel", "text": texts.CONSENT_DECLINED}


def test_unclear_consent_answer_asks_again() -> None:
    provider = EvidenceDecisionProvider(ScriptedModel(), FakeReader())
    first, _ = decide(provider, "내일 오후 4시에 회의 등록하고 알림도 켜 줘")
    _, decision = decide(provider, "글쎄", previous=first)
    assert decision == {"kind": "ask", "text": texts.CONSENT_QUESTION}


def test_unsupported_only_gets_guidance_without_model_call() -> None:
    model = ScriptedModel()
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "알림 켜 줘")
    assert decision == {"kind": "ask", "text": texts.GUIDANCE}
    assert model.seen == []


def test_unrelated_request_gets_guidance() -> None:
    _, decision = decide(EvidenceDecisionProvider(ScriptedModel(output()), FakeReader()), "점심 메뉴 추천해 줘")
    assert decision == {"kind": "ask", "text": texts.GUIDANCE}


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("응", Answer.YES),
        ("네", Answer.YES),
        ("그래, 문자는 빼고 등록해", Answer.YES),
        ("알림은 필요 없어요", Answer.YES),
        ("문자 말고 등록만 부탁해", Answer.YES),
        ("아니", Answer.NO),
        ("아니요, 등록하지 마세요", Answer.NO),
        ("글쎄", Answer.UNKNOWN),
        ("내일로 해 줘", Answer.UNKNOWN),
        ("알림도 켜 줘", Answer.UNKNOWN),
    ],
)
def test_classify_answer(text: str, answer: Answer) -> None:
    assert classify_answer(text) is answer


# ---------------------------------------------------------------- targets


def test_delete_one_target_confirms() -> None:
    target = schedule("재고 실물 대조", REFERENCE + timedelta(days=2))
    model = ScriptedModel(output(op("delete", target={"title": [span("재고 실물 대조")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader([target])), "재고 실물 대조 일정 삭제해 줘")
    assert decision == {
        "kind": "confirm",
        "text": texts.confirm_delete("재고 실물 대조"),
        "scheduleId": str(target.id),
        "operation": "delete",
        "timeInterpretation": "relative",
    }


def test_target_title_match_ignores_spaces_and_picks_among_several() -> None:
    first = schedule("주간 회의", REFERENCE + timedelta(days=1))
    second = schedule("운영회의", REFERENCE + timedelta(days=3))
    other = schedule("입고 검수", REFERENCE + timedelta(days=2))
    model = ScriptedModel(output(op("delete", target={"title": [span("회의")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader([first, second, other])), "회의 지워 줘")
    assert decision["kind"] == "select_candidate"
    assert [c["scheduleId"] for c in decision["candidates"]] == [str(first.id), str(second.id)]


def test_no_target_found() -> None:
    model = ScriptedModel(output(op("delete", target={"title": [span("출고 검량 기록")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "출고 검량 기록 지워 줘")
    assert decision == {"kind": "no_match", "operation": "delete"}


def test_more_than_ten_targets_asks_to_narrow() -> None:
    rows = [schedule("출고 검량 확인", REFERENCE + timedelta(days=day)) for day in range(1, 12)]
    model = ScriptedModel(output(op("delete", target={"title": [span("출고 검량 확인")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader(rows)), "출고 검량 확인 지워 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_NARROW}


def test_target_without_date_searches_the_coming_92_days() -> None:
    reader = FakeReader()
    model = ScriptedModel(output(op("delete", target={"title": [span("점검")]})))
    decide(EvidenceDecisionProvider(model, reader), "점검 일정 지워 줘")
    assert len(reader.reads) == 1
    assert reader.reads[0]["to"] - reader.reads[0]["from"] == timedelta(days=92)


def test_target_date_without_year_searches_this_and_next_year() -> None:
    reader = FakeReader()
    model = ScriptedModel(output(op("delete", target={"title": [span("회의")], "date": [span("3월 5일")]})))
    decide(EvidenceDecisionProvider(model, reader), "3월 5일 회의 지워 줘")
    starts = [read["from"].astimezone(SEOUL).date().isoformat() for read in reader.reads]
    assert starts == ["2026-03-05", "2027-03-05"]


def test_update_date_only_keeps_the_time() -> None:
    target = schedule("회의", datetime(2026, 11, 2, 15, 0, tzinfo=SEOUL))
    model = ScriptedModel(output(op("update", target={"title": [span("회의")]}, change={"date": [span("금요일")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader([target])), "회의 금요일로 옮겨 줘")
    assert decision["kind"] == "confirm"
    assert datetime.fromisoformat(decision["changes"]["scheduledAt"]) == datetime(2026, 10, 30, 15, 0, tzinfo=SEOUL)


def test_update_time_only_keeps_the_date() -> None:
    target = schedule("회의", datetime(2026, 11, 2, 15, 0, tzinfo=SEOUL))
    model = ScriptedModel(output(op("update", target={"title": [span("회의")]}, change={"time": [span("오후 5시")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader([target])), "회의 오후 5시로 옮겨 줘")
    assert datetime.fromisoformat(decision["changes"]["scheduledAt"]) == datetime(2026, 11, 2, 17, 0, tzinfo=SEOUL)


def test_update_without_change_value_asks() -> None:
    model = ScriptedModel(output(op("update", target={"title": [span("회의")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "회의 좀 바꿔 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_CHANGE_VALUE}


def test_update_completed_state() -> None:
    target = schedule("포장 라인 점검", REFERENCE + timedelta(days=1))
    model = ScriptedModel(
        output(op("update", target={"title": [span("포장 라인 점검")]}, change={"completed": [span("완료로 표시해 줘")]}))
    )
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader([target])), "포장 라인 점검 완료로 표시해 줘")
    assert decision["changes"] == {"completed": True}


# -------------------------------------------------------------------- list


def test_list_without_period_shows_the_coming_92_days() -> None:
    model = ScriptedModel(output(op("list", filter={"category": [span("입고")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "입고 일정 보여 줘")
    assert decision["kind"] == "list_intent"
    assert decision["category"] == "INBOUND"
    start = datetime.fromisoformat(decision["from"])
    assert datetime.fromisoformat(decision["to"]) - start == timedelta(days=92)


def test_list_month_title_and_completed_filters() -> None:
    model = ScriptedModel(
        output(op("list", filter={"date": [span("11월")], "completed": [span("미완료")], "title": [span("회의")]}))
    )
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "11월 미완료 회의 일정 보여 줘")
    def instant(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    assert instant(decision["from"]) == datetime(2026, 11, 1, tzinfo=SEOUL)
    assert instant(decision["to"]) == datetime(2026, 12, 1, tzinfo=SEOUL)
    assert decision["completed"] is False
    assert decision["title"] == "회의"


# ------------------------------------------------------- several requests


def test_several_requests_confirm_the_first_create() -> None:
    evidence = output(
        op("create", change={"title": [span("입고 서류 스캔")], "date": [span("내일")], "time": [span("오전 8시")]}),
        op("create", 2, change={"title": [span("반품 회수 박스 집계")], "date": [span("내일")], "time": [span("오후 1시")]}),
    )
    turn = "내일 오전 8시 입고 서류 스캔이랑 오후 1시 반품 회수 박스 집계 두 개 등록해 줘"
    provider = EvidenceDecisionProvider(ScriptedModel(evidence, evidence), FakeReader())
    first, decision = decide(provider, turn)
    assert decision["text"] == texts.confirm_first_create("입고 서류 스캔")
    _, decision = decide(provider, "응, 첫 번째 등록해", previous=first)
    assert decision["kind"] == "create_intent"
    assert decision["title"] == "입고 서류 스캔"


def test_several_requests_declining_the_first_cancels() -> None:
    evidence = output(
        op("create", change={"title": [span("입고 서류 스캔")], "date": [span("내일")], "time": [span("오전 8시")]}),
        op("create", 2, change={"title": [span("반품 회수 박스 집계")], "date": [span("내일")], "time": [span("오후 1시")]}),
    )
    turn = "내일 오전 8시 입고 서류 스캔이랑 오후 1시 반품 회수 박스 집계 두 개 등록해 줘"
    provider = EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader())
    first, _ = decide(provider, turn)
    _, decision = decide(provider, "아니요, 첫 번째는 빼 줘", previous=first)
    assert decision == {"kind": "cancel", "text": texts.FIRST_DECLINED}


def test_several_requests_note_on_target_question() -> None:
    target = schedule("출고 박스 수량 집계", REFERENCE + timedelta(days=2))
    evidence = output(
        op("delete", target={"title": [span("출고 박스 수량 집계")]}),
        op("create", 2, change={"title": [span("재고 이월 회의")], "date": [span("내일")], "time": [span("오후 3시")]}),
    )
    turn = "출고 박스 수량 집계 일정은 지우고, 재고 이월 회의는 내일 오후 3시로 잡아 줘"
    _, decision = decide(EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader([target])), turn)
    assert decision["kind"] == "confirm"
    assert decision["text"].endswith(texts.MORE_REQUESTS_NOTE)


# ------------------------------------------------------------ bad output


def test_invalid_model_output_is_rejected() -> None:
    model = ScriptedModel(output(op("create", change={"title": [span("출고 검수")]})))
    with pytest.raises(EvidenceContractError):
        decide(EvidenceDecisionProvider(model, FakeReader()), "내일 입고 검수 잡아 줘")


def test_stored_state_from_another_shape_starts_fresh() -> None:
    model = ScriptedModel(output(op("create", change={"title": [span("회의")]})))
    provider = EvidenceDecisionProvider(model, FakeReader())
    envelope = provider.decide(context("회의 잡아 줘", follow_up=True, state={"something": "else"}))
    assert envelope.control_state["turns"] == ["회의 잡아 줘"]
    assert model.seen == [["회의 잡아 줘"]]
