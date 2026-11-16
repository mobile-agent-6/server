from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.assistant.decisions import DecisionContext, DecisionProviderInvalidOutput
from app.modules.assistant.evidence import texts
from app.modules.assistant.evidence.gate import Answer, classify_answer
from app.modules.assistant.evidence.normalize import SEOUL, title_contains
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

    def list(self, *, from_utc, to_utc, category, completed, limit, title=None):
        self.reads.append(
            {
                "from": from_utc,
                "to": to_utc,
                "category": category,
                "completed": completed,
                "limit": limit,
                "title": title,
            }
        )
        rows = [
            s
            for s in self.schedules
            if from_utc <= s.scheduled_at < to_utc
            and (category is None or s.category is category)
            and (completed is None or s.completed is completed)
            and (title is None or title_contains(title, s.title))
        ]
        return rows[:limit]


class ScriptedModel:
    def __init__(self, *outputs: dict) -> None:
        self.outputs = list(outputs)
        self.seen: list[list[str]] = []
        self.references: list[datetime] = []

    def extract(self, *, turns, reference_time, server_time_trusted, timeout_seconds=None) -> str:
        self.seen.append(list(turns))
        self.references.append(reference_time)
        return json.dumps(self.outputs.pop(0), ensure_ascii=False)


def schedule(
    title: str,
    when: datetime,
    *,
    category: Category = Category.OTHER,
    completed: bool = False,
) -> ScheduleRead:
    return ScheduleRead(
        id=uuid4(),
        title=title,
        scheduled_at=when.astimezone(timezone.utc),
        category=category,
        completed=completed,
        updated_at=REFERENCE.astimezone(timezone.utc),
    )


def span(
    text: str,
    turn: int = 1,
    status: str = "adopted",
    occurrence: int | None = None,
) -> dict:
    result = {"turn": turn, "text": text, "status": status}
    if occurrence is not None:
        result["occurrence"] = occurrence
    return result


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


def context(
    text: str,
    *,
    follow_up: bool = False,
    state: object | None = None,
    trusted: bool = True,
    request_accepted_at: datetime | None = None,
    task_reference_accepted_at: datetime | None = None,
) -> DecisionContext:
    return DecisionContext(
        request_id=uuid4(),
        conversation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        input={"type": "text", "text": text},
        request_accepted_at=(request_accepted_at or REFERENCE).astimezone(
            timezone.utc
        ),
        task_reference_accepted_at=(
            task_reference_accepted_at or REFERENCE
        ).astimezone(timezone.utc),
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


@pytest.mark.parametrize("title", ["출하 예정 점검", "협력사 납품 회의"])
def test_create_uses_other_for_ambiguous_logistics_terms(title: str) -> None:
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span(title)],
                    "date": [span("내일")],
                    "time": [span("오전 9시")],
                },
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader()),
        f"내일 오전 9시에 {title} 등록해 줘",
    )
    assert decision["kind"] == "create_intent"
    assert decision["category"] == "OTHER"


def test_create_maps_explicit_other_category() -> None:
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span("운영 회의")],
                    "date": [span("내일")],
                    "time": [span("오후 3시")],
                    "category": [span("기타")],
                },
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader()),
        "내일 오후 3시에 운영 회의를 기타 분류로 등록해 줘",
    )
    assert decision["kind"] == "create_intent"
    assert decision["category"] == "OTHER"


def test_create_with_multiple_category_cues_asks_instead_of_using_pattern_order() -> None:
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span("반품 입고 회의")],
                    "date": [span("내일")],
                    "time": [span("오후 3시")],
                },
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader()),
        "내일 오후 3시에 반품 입고 회의 등록해 줘",
    )
    assert decision == {"kind": "ask", "text": texts.ASK_CATEGORY}


@pytest.mark.parametrize(
    ("said_time", "expected_text"),
    [
        (
            "아침 12시",
            texts.confirm_midnight(datetime(2026, 10, 30, 0, 0, tzinfo=SEOUL)),
        ),
        (
            "새벽 12시 30분",
            texts.confirm_midnight(datetime(2026, 10, 30, 0, 30, tzinfo=SEOUL)),
        ),
        ("저녁 3시", texts.ASK_TIME_AGAIN),
    ],
)
def test_create_asks_for_ambiguous_clock_periods(
    said_time: str, expected_text: str
) -> None:
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span("운영 회의")],
                    "date": [span("내일")],
                    "time": [span(said_time)],
                },
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader()),
        f"내일 {said_time}에 운영 회의 등록해 줘",
    )
    assert decision == {"kind": "ask", "text": expected_text}


@pytest.mark.parametrize(
    ("title", "category_text", "expected"),
    [
        ("납품분 입고 확인", "입고", "INBOUND"),
        ("출하 출고 점검", "출고", "OUTBOUND"),
    ],
)
def test_explicit_category_cue_wins_over_ambiguous_logistics_terms(
    title: str, category_text: str, expected: str
) -> None:
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span(title)],
                    "date": [span("내일")],
                    "time": [span("오전 9시")],
                    "category": [span(category_text)],
                },
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader()),
        f"내일 오전 9시에 {title} 등록해 줘",
    )
    assert decision["kind"] == "create_intent"
    assert decision["category"] == expected


def test_past_create_asks_for_a_new_date_and_time_before_write() -> None:
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span("입고 검수")],
                    "date": [span("오늘")],
                    "time": [span("오전 9시")],
                },
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader()),
        "오늘 오전 9시에 입고 검수 잡아 줘",
    )
    assert decision == {"kind": "ask", "text": texts.ASK_DATETIME_AGAIN}


@pytest.mark.parametrize("date_text", ["이틀 뒤", "일주일 후", "한 달 뒤"])
def test_unsupported_relative_date_asks_instead_of_rejecting_model_output(
    date_text: str,
) -> None:
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span("입고 검수")],
                    "date": [span(date_text)],
                    "time": [span("오후 3시")],
                },
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader()),
        f"{date_text} 오후 3시에 입고 검수 잡아 줘",
    )
    assert decision == {"kind": "ask", "text": texts.ASK_DATE_AGAIN}


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
    assert model.seen == [["3월 5일 오후 2시 회의 잡아 줘"]]


def test_declined_date_confirmation_asks_for_the_date_again() -> None:
    evidence = output(op("create", change={"title": [span("회의")], "date": [span("3월 5일")], "time": [span("오후 2시")]}))
    provider = EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader())
    first, _ = decide(provider, "3월 5일 오후 2시 회의 잡아 줘")
    _, decision = decide(provider, "아니", previous=first)
    assert decision == {"kind": "ask", "text": texts.ASK_DATE_AGAIN}


def test_evening_twelve_asks_about_midnight() -> None:
    evidence = output(op("create", change={"title": [span("재고 실사")], "date": [span("내일")], "time": [span("밤 12시")]}))
    model = ScriptedModel(evidence)
    provider = EvidenceDecisionProvider(model, FakeReader())
    first, decision = decide(provider, "내일 밤 12시에 재고 실사 잡아 줘")
    assert decision["text"] == texts.confirm_midnight(
        datetime(2026, 10, 31, 0, 0, tzinfo=SEOUL)
    )
    _, decision = decide(provider, "응", previous=first)
    assert datetime.fromisoformat(decision["scheduledAt"]) == datetime(2026, 10, 31, 0, 0, tzinfo=SEOUL)
    assert model.seen == [["내일 밤 12시에 재고 실사 잡아 줘"]]


@pytest.mark.parametrize(
    ("said_time", "expected"),
    [
        ("아침 12시", datetime(2026, 10, 30, 0, 0, tzinfo=SEOUL)),
        ("아침 12시 30분", datetime(2026, 10, 30, 0, 30, tzinfo=SEOUL)),
        ("새벽 12시", datetime(2026, 10, 30, 0, 0, tzinfo=SEOUL)),
        ("새벽 12시 반", datetime(2026, 10, 30, 0, 30, tzinfo=SEOUL)),
        ("밤 12시 반", datetime(2026, 10, 31, 0, 30, tzinfo=SEOUL)),
    ],
)
def test_confirmed_midnight_uses_the_exact_proposed_datetime(
    said_time: str, expected: datetime
) -> None:
    evidence = output(
        op(
            "create",
            change={
                "title": [span("재고 실사")],
                "date": [span("내일")],
                "time": [span(said_time)],
            },
        )
    )
    model = ScriptedModel(evidence)
    provider = EvidenceDecisionProvider(model, FakeReader())
    first, decision = decide(
        provider, f"내일 {said_time}에 재고 실사 잡아 줘"
    )
    assert decision["text"] == texts.confirm_midnight(expected)
    _, decision = decide(provider, "응", previous=first)
    assert datetime.fromisoformat(decision["scheduledAt"]) == expected
    assert model.seen == [[f"내일 {said_time}에 재고 실사 잡아 줘"]]


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
    # The yes is not sent to the model; only the request is.
    assert model.seen == [["내일 오후 4시에 회의 등록하고 알림도 켜 줘"]]


def test_supported_create_with_connective_asks_consent() -> None:
    model = ScriptedModel()
    provider = EvidenceDecisionProvider(model, FakeReader())
    _, decision = decide(
        provider, "내일 오후 3시에 출고 검수 일정을 잡되 알림도 켜 줘"
    )
    assert decision == {"kind": "ask", "text": texts.CONSENT_QUESTION}
    assert model.seen == []


@pytest.mark.parametrize(
    "user_text",
    [
        "내일 오후 3시에 출고 검수 일정 잡자 알림도 켜 줘",
        "내일 오후 3시에 출고 검수 일정 잡을래 알림도 켜 줘",
    ],
)
def test_colloquial_create_with_unsupported_feature_asks_consent(
    user_text: str,
) -> None:
    model = ScriptedModel()
    provider = EvidenceDecisionProvider(model, FakeReader())
    _, decision = decide(provider, user_text)
    assert decision == {"kind": "ask", "text": texts.CONSENT_QUESTION}
    assert model.seen == []


@pytest.mark.parametrize(
    "user_text",
    [
        "잡되게 굴지 말고 알림 켜 줘",
        "잡되지 않게 알림 켜 줘",
        "일정 변경되면 알림 보내 줘",
        "수정된 일정 있으면 문자 보내 줘",
        "잡자재 입고 알림 켜 줘",
    ],
)
def test_non_operation_phrase_with_unsupported_feature_keeps_guidance(
    user_text: str,
) -> None:
    model = ScriptedModel()
    provider = EvidenceDecisionProvider(model, FakeReader())
    _, decision = decide(provider, user_text)
    assert decision == {"kind": "ask", "text": texts.GUIDANCE}
    assert model.seen == []


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


def test_generic_tell_me_request_is_not_treated_as_a_missing_schedule_operation() -> None:
    provider = EvidenceDecisionProvider(ScriptedModel(output()), FakeReader())
    _, decision = decide(provider, "내일 날씨 알려 줘")
    assert decision == {"kind": "ask", "text": texts.GUIDANCE}


def test_supported_request_missing_from_model_output_is_rejected() -> None:
    provider = EvidenceDecisionProvider(ScriptedModel(output()), FakeReader())
    with pytest.raises(DecisionProviderInvalidOutput, match="SUPPORTED_OPERATION_MISSING"):
        decide(provider, "내일 오후 3시에 회의 등록해 줘")


def test_japdoe_request_missing_from_model_output_is_rejected() -> None:
    provider = EvidenceDecisionProvider(ScriptedModel(output()), FakeReader())
    with pytest.raises(DecisionProviderInvalidOutput, match="SUPPORTED_OPERATION_MISSING"):
        decide(provider, "내일 오후 3시에 회의 잡되 장소는 2층으로")


@pytest.mark.parametrize(
    "user_text",
    [
        "내일 오후 3시에 회의 잡자",
        "내일 오후 3시에 회의 잡을래",
        "내일 오후 3시에 회의 넣자",
        "내일 오후 3시에 회의 잡고 장소는 2층으로",
        "내일 오후 3시 회의를 변경해 줘",
        "내일 오후 3시 회의를 수정해 줘",
    ],
)
def test_shared_supported_action_missing_from_model_output_is_rejected(
    user_text: str,
) -> None:
    provider = EvidenceDecisionProvider(ScriptedModel(output()), FakeReader())
    with pytest.raises(DecisionProviderInvalidOutput, match="SUPPORTED_OPERATION_MISSING"):
        decide(provider, user_text)


def test_consented_supported_request_missing_from_unsupported_output_is_rejected() -> None:
    model = ScriptedModel(output(unsupported=[span("알림도 보내 줘")]))
    provider = EvidenceDecisionProvider(model, FakeReader())
    first, decision = decide(
        provider, "내일 오후 3시에 회의 등록하고 알림도 보내 줘"
    )
    assert decision == {"kind": "ask", "text": texts.CONSENT_QUESTION}

    with pytest.raises(DecisionProviderInvalidOutput, match="SUPPORTED_OPERATION_MISSING"):
        decide(provider, "응", previous=first)


def test_unsupported_only_request_with_registration_word_keeps_guidance() -> None:
    model = ScriptedModel(output(unsupported=[span("알림 등록해 줘")]))
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader()), "알림 등록해 줘"
    )
    assert decision == {"kind": "ask", "text": texts.GUIDANCE}


def test_unsupported_evidence_in_later_operation_still_asks_consent() -> None:
    second = op("list", 2, filter={"title": [span("회의")]})
    second["unsupportedEvidence"] = [span("보여 줘")]
    evidence = output(
        op(
            "create",
            change={
                "title": [span("회의")],
                "date": [span("내일")],
                "time": [span("오후 3시")],
            },
        ),
        second,
    )
    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()),
        "내일 오후 3시 회의를 등록하고 회의도 보여 줘",
    )
    assert decision == {"kind": "ask", "text": texts.CONSENT_QUESTION}


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
        # Real consent replies from the training and locked data.
        ("그냥 일정만 등록해 주세요", Answer.YES),
        ("푸시는 생략하고 등록해 줘", Answer.YES),
        ("알림은 필요 없으니 진행해 줘", Answer.YES),
        ("응, 첫 번째 등록해", Answer.YES),
        ("됐어, 일정도 넣지 마", Answer.NO),
        ("아뇨, 그만둘게요", Answer.NO),
        ("전부 취소해, 나머지는 하지 마", Answer.NO),
        # A yes with a refusal is unclear; picking a part without a yes too (fourth review).
        ("네, 취소할게요", Answer.UNKNOWN),
        ("네 그만할게요", Answer.UNKNOWN),
        ("응 안 할게", Answer.UNKNOWN),
        ("응 그만둘게", Answer.UNKNOWN),
        ("어 그건 좀", Answer.UNKNOWN),
        ("나머지만 해 줘", Answer.UNKNOWN),
        ("나머지 진행해 주세요", Answer.UNKNOWN),
        ("첫 번째는 빼고 등록해 줘", Answer.UNKNOWN),
        ("싫어, 넣지 마", Answer.NO),
        ("응 다 해", Answer.UNKNOWN),
        # Putting it off, yes with a refusal, no with a part (fifth review).
        ("응 나중에", Answer.UNKNOWN),
        ("네 잠깐만", Answer.UNKNOWN),
        ("응 싫어", Answer.UNKNOWN),
        ("네 됐어요", Answer.UNKNOWN),
        ("나머지는 하지 마", Answer.NO),
        ("아뇨 괜찮아요", Answer.NO),
        # A yes with a new value is not a plain yes: ask again, never drop it (review).
        ("응 정오로", Answer.UNKNOWN),
        ("응 열한 시로", Answer.UNKNOWN),
        ("응, 15:30으로", Answer.UNKNOWN),
        ("응 자정으로", Answer.UNKNOWN),
        ("네 다음 달로", Answer.UNKNOWN),
        ("좋아요 근데 오후로", Answer.UNKNOWN),
        ("응, 제목은 주간 회의로", Answer.UNKNOWN),
        ("어 그건", Answer.UNKNOWN),
        # A yes that drops something is not a plain yes (re-review).
        ("응 첫 번째는 빼 줘", Answer.UNKNOWN),
        ("네 그건 빼 주세요", Answer.UNKNOWN),
        ("리마인더 빼고 등록만 해줘", Answer.YES),
        ("응, 알림은 빼 줘", Answer.YES),
        ("응 나머지만", Answer.UNKNOWN),
        ("네 나머지만 해 줘", Answer.UNKNOWN),
        ("전부 취소해, 나머지는 하지 마", Answer.NO),
        ("아니요, 첫 번째는 빼 줘", Answer.NO),
        # Everyday yes forms.
        ("네네", Answer.YES),
        ("응응", Answer.YES),
        ("맞습니다", Answer.YES),
        ("부탁드립니다", Answer.YES),
        ("괜찮아요", Answer.UNKNOWN),
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
    assert decision["text"] == "어떤 일정인가요? 하나를 선택해 주세요."
    assert [c["scheduleId"] for c in decision["candidates"]] == [str(first.id), str(second.id)]


def test_no_target_found() -> None:
    model = ScriptedModel(output(op("delete", target={"title": [span("출고 검량 기록")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "출고 검량 기록 지워 줘")
    assert decision == {"kind": "no_match", "operation": "delete"}


@pytest.mark.parametrize("query", ["2번 도크", "dock b", "오후 입고 점검"])
def test_distinctive_title_change_is_not_a_typo_candidate(query: str) -> None:
    rows = [
        schedule("1번 도크", REFERENCE + timedelta(days=1)),
        schedule("dock a", REFERENCE + timedelta(days=2)),
        schedule("오전 입고 점검", REFERENCE + timedelta(days=3)),
    ]
    model = ScriptedModel(output(op("delete", target={"title": [span(query)]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader(rows)), f"{query} 지워 줘")
    assert decision == {"kind": "no_match", "operation": "delete"}


def test_more_than_ten_targets_asks_to_narrow() -> None:
    rows = [schedule("출고 검량 확인", REFERENCE + timedelta(days=day)) for day in range(1, 12)]
    model = ScriptedModel(output(op("delete", target={"title": [span("출고 검량 확인")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader(rows)), "출고 검량 확인 지워 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_NARROW}


def test_unrelated_rows_do_not_hide_one_title_match() -> None:
    unrelated = [
        schedule(f"다른 일정 {index}", REFERENCE + timedelta(minutes=index + 1))
        for index in range(101)
    ]
    target = schedule("출고 검량 확인", REFERENCE + timedelta(days=2))
    reader = FakeReader([*unrelated, target])
    model = ScriptedModel(output(op("delete", target={"title": [span("출고 검량 확인")]})))
    _, decision = decide(
        EvidenceDecisionProvider(model, reader),
        "출고 검량 확인 지워 줘",
    )
    assert decision["kind"] == "confirm"
    assert decision["scheduleId"] == str(target.id)
    assert [read["title"] for read in reader.reads] == [
        "출고 검량 확인",
        "출고 검량 확인",
    ]


def test_target_without_date_searches_92_days_back_and_ahead() -> None:
    reader = FakeReader()
    model = ScriptedModel(output(op("delete", target={"title": [span("점검")]})))
    decide(EvidenceDecisionProvider(model, reader), "점검 일정 지워 줘")
    today = datetime(2026, 10, 29, tzinfo=SEOUL)
    ranges = [
        (today - timedelta(days=92), today),
        (today, today + timedelta(days=92)),
    ]
    assert [(read["from"], read["to"]) for read in reader.reads] == ranges * 2
    assert [read["title"] for read in reader.reads] == ["점검", "점검", None, None]


def test_target_without_date_finds_a_past_schedule() -> None:
    past = schedule("냉장 창고 온도 기록", datetime(2026, 10, 20, 8, 0, tzinfo=SEOUL), completed=True)
    model = ScriptedModel(output(op("delete", target={"title": [span("냉장 창고 온도 기록")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader([past])), "냉장 창고 온도 기록 지워 줘")
    assert decision["kind"] == "confirm"
    assert decision["scheduleId"] == str(past.id)


def _delete_target(title: str, schedules: list[ScheduleRead]) -> dict:
    model = ScriptedModel(output(op("delete", target={"title": [span(title)]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader(schedules)), f"{title} 지워 줘")
    return decision


def test_one_letter_slip_is_found_when_nothing_matches() -> None:
    stored = schedule("출고 상하자 대기 확인", datetime(2026, 10, 30, 10, 0, tzinfo=SEOUL))
    decision = _delete_target("출고 상하차 대기 확인", [stored])
    # The confirmation shows the stored title, so the user sees the slip.
    assert decision["kind"] == "confirm"
    assert decision["scheduleId"] == str(stored.id)


def test_one_letter_slip_is_not_mixed_in_when_the_title_matches() -> None:
    exact = schedule("출고 상하차 대기 확인", datetime(2026, 10, 30, 10, 0, tzinfo=SEOUL))
    slip = schedule("출고 상하자 대기 확인", datetime(2026, 10, 31, 10, 0, tzinfo=SEOUL))
    decision = _delete_target("출고 상하차 대기 확인", [exact, slip])
    assert (decision["kind"], decision["scheduleId"]) == ("confirm", str(exact.id))


@pytest.mark.parametrize(
    ("said", "stored"),
    [
        ("입고 검품 라인 정리", "출고 검품 라인 정리"),  # different category word
        ("주간 회의", "주간 회식"),  # too short to guess
        ("출고 상하차 대기 확인", "출고 대기 차량 확인"),  # more than one letter
    ],
)
def test_titles_that_only_look_alike_are_not_targets(said: str, stored: str) -> None:
    decision = _delete_target(said, [schedule(stored, datetime(2026, 10, 30, 10, 0, tzinfo=SEOUL))])
    assert decision["kind"] == "no_match"


def test_target_date_without_year_searches_this_and_next_year() -> None:
    reader = FakeReader()
    model = ScriptedModel(output(op("delete", target={"title": [span("회의")], "date": [span("3월 5일")]})))
    decide(EvidenceDecisionProvider(model, reader), "3월 5일 회의 지워 줘")
    starts = [read["from"].astimezone(SEOUL).date().isoformat() for read in reader.reads]
    assert starts == ["2026-03-05", "2027-03-05"] * 2
    assert [read["title"] for read in reader.reads] == ["회의", "회의", None, None]


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


def test_update_to_a_past_time_asks_before_confirmation() -> None:
    target = schedule("회의", datetime(2026, 10, 29, 11, 0, tzinfo=SEOUL))
    model = ScriptedModel(
        output(
            op(
                "update",
                target={"title": [span("회의")]},
                change={"time": [span("오전 9시")]},
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader([target])),
        "회의 오전 9시로 옮겨 줘",
    )
    assert decision == {"kind": "ask", "text": texts.ASK_DATETIME_AGAIN}


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


@pytest.mark.parametrize(
    ("period", "start"),
    [("3월 5일", "2027-03-04T15:00:00Z"), ("15일", "2026-11-14T15:00:00Z")],
)
def test_list_yearless_date_uses_the_coming_occurrence(period: str, start: str) -> None:
    model = ScriptedModel(output(op("list", filter={"date": [span(period)]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), f"{period} 일정 보여 줘")
    assert decision["from"] == start
    expected_end = datetime.fromisoformat(start.replace("Z", "+00:00")) + timedelta(days=1)
    assert decision["to"] == expected_end.isoformat().replace("+00:00", "Z")


def test_list_explicit_past_year_keeps_the_exact_date() -> None:
    model = ScriptedModel(output(op("list", filter={"date": [span("2026년 3월 5일")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "2026년 3월 5일 일정 보여 줘")
    assert decision["from"] == "2026-03-04T15:00:00Z"


# ------------------------------------------------------- several requests


def test_several_requests_confirm_the_first_create() -> None:
    evidence = output(
        op("create", change={"title": [span("입고 서류 스캔")], "date": [span("내일")], "time": [span("오전 8시")]}),
        op("create", 2, change={"title": [span("반품 회수 박스 집계")], "date": [span("내일")], "time": [span("오후 1시")]}),
    )
    turn = "내일 오전 8시 입고 서류 스캔이랑 오후 1시 반품 회수 박스 집계 두 개 등록해 줘"
    provider = EvidenceDecisionProvider(ScriptedModel(evidence, evidence), FakeReader())
    first, decision = decide(provider, turn)
    assert decision["text"] == texts.confirm_first_create(
        "입고 서류 스캔", datetime(2026, 10, 30, 8, 0, tzinfo=SEOUL)
    )
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


def test_several_requests_note_on_missing_value_question() -> None:
    evidence = output(
        op("create", change={"title": [span("회의")], "date": [span("내일")]}),
        op("list", 2, filter={"date": [span("내일")]}),
    )
    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()),
        "내일 회의 등록하고 일정도 보여 줘",
    )
    assert decision == {"kind": "ask", "text": texts.ASK_TIME + texts.MORE_REQUESTS_NOTE}


def test_several_requests_note_on_list_result() -> None:
    evidence = output(
        op("list", filter={"date": [span("내일")]}),
        op(
            "create",
            2,
            change={
                "title": [span("회의")],
                "date": [span("내일")],
                "time": [span("오후 3시")],
            },
        ),
    )
    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()),
        "내일 일정 보여 주고 오후 3시 회의도 등록해 줘",
    )
    assert decision["kind"] == "list_intent"
    assert decision["text"] == texts.LIST_RESULT + texts.MORE_REQUESTS_NOTE


def test_several_requests_note_on_no_match() -> None:
    evidence = output(
        op("delete", target={"title": [span("없는 회의")]}),
        op(
            "create",
            2,
            change={
                "title": [span("재고 회의")],
                "date": [span("내일")],
                "time": [span("오후 3시")],
            },
        ),
    )
    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()),
        "없는 회의는 지우고 내일 오후 3시 재고 회의 등록해 줘",
    )
    assert decision == {
        "kind": "no_match",
        "operation": "delete",
        "text": texts.NO_MATCH + texts.MORE_REQUESTS_NOTE,
    }


def test_update_target_time_and_category_narrow_candidates() -> None:
    inbound = schedule(
        "정기 회의",
        datetime(2026, 10, 30, 15, 0, tzinfo=SEOUL),
        category=Category.INBOUND,
    )
    outbound = schedule(
        "정기 회의",
        datetime(2026, 10, 30, 15, 0, tzinfo=SEOUL),
        category=Category.OUTBOUND,
    )
    wrong_time = schedule(
        "정기 회의",
        datetime(2026, 10, 30, 17, 0, tzinfo=SEOUL),
        category=Category.INBOUND,
    )
    evidence = output(
        op(
            "update",
            target={
                "title": [span("정기 회의")],
                "date": [span("내일")],
                "time": [span("오후 3시")],
                "category": [span("입고")],
            },
            change={"title": [span("주간 회의")]},
        )
    )
    reader = FakeReader([outbound, wrong_time, inbound])

    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), reader),
        "내일 오후 3시 입고 정기 회의 제목을 주간 회의로 바꿔 줘",
    )

    assert decision["kind"] == "confirm"
    assert decision["scheduleId"] == str(inbound.id)
    assert reader.reads[0]["category"] is Category.INBOUND


def test_ambiguous_target_time_keeps_morning_and_afternoon_candidates() -> None:
    morning = schedule("정기 회의", datetime(2026, 10, 30, 3, 0, tzinfo=SEOUL))
    afternoon = schedule("정기 회의", datetime(2026, 10, 30, 15, 0, tzinfo=SEOUL))
    other = schedule("정기 회의", datetime(2026, 10, 30, 17, 0, tzinfo=SEOUL))
    evidence = output(
        op(
            "delete",
            target={
                "title": [span("정기 회의")],
                "date": [span("내일")],
                "time": [span("3시")],
            },
        )
    )

    _, decision = decide(
        EvidenceDecisionProvider(
            ScriptedModel(evidence), FakeReader([other, afternoon, morning])
        ),
        "내일 3시 정기 회의 지워 줘",
    )

    assert decision["kind"] == "select_candidate"
    assert {item["scheduleId"] for item in decision["candidates"]} == {
        str(morning.id),
        str(afternoon.id),
    }


@pytest.mark.parametrize(("clock", "hour", "minute"), [("세 시", 3, 0), ("두 시 반", 2, 30), ("열두 시", 0, 0)])
def test_native_clock_in_postprocessed_delete_title_keeps_both_candidates(clock, hour, minute) -> None:
    title = "입고 납품서 대조"
    morning = schedule(title, datetime(2026, 10, 30, hour, minute, tzinfo=SEOUL))
    afternoon = schedule(title, datetime(2026, 10, 30, hour + 12, minute, tzinfo=SEOUL))
    other = schedule(title, datetime(2026, 10, 30, 17, 0, tzinfo=SEOUL))
    evidence = output(op("delete", target={
        "date": [span("내일")], "title": [span(f"{clock} {title}")],
    }))
    model = ScriptedModel(evidence)
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader([other, afternoon, morning])),
        f"내일 {clock} {title} 일정 삭제해 줘.",
    )
    assert decision["kind"] == "select_candidate"
    assert {c["scheduleId"] for c in decision["candidates"]} == {str(morning.id), str(afternoon.id)}
    assert len(model.seen) == 1


def test_quoted_native_clock_title_is_not_reinterpreted_as_time() -> None:
    from app.modules.assistant.evidence.provider import _validate_output

    title = "세 시 입고 납품서 대조"
    evidence = output(op("delete", target={"date": [span("내일")], "title": [span(title)]}))
    parsed = _validate_output(
        json.dumps(evidence, ensure_ascii=False),
        turns={1: f"내일 '{title}' 일정 삭제해 줘."}, reference=REFERENCE,
    )
    assert "time" not in parsed.operations[0].target
    assert parsed.operations[0].target["title"][0].text == title


def test_list_time_is_converted_to_backend_clock_filter() -> None:
    evidence = output(
        op(
            "list",
            filter={"date": [span("내일")], "time": [span("오후 3시")]},
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()),
        "내일 오후 3시 일정 보여 줘",
    )
    assert decision["kind"] == "list_intent"
    assert decision["timeMinutes"] == 15 * 60


def test_ambiguous_list_time_asks_for_ampm() -> None:
    evidence = output(op("list", filter={"time": [span("3시")]}))
    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()),
        "3시 일정 보여 줘",
    )
    assert decision == {"kind": "ask", "text": texts.ASK_AMPM}


# ------------------------------------------------------------ bad output


def test_invalid_model_output_is_rejected() -> None:
    model = ScriptedModel(output(op("create", change={"title": [span("출고 검수")]})))
    with pytest.raises(DecisionProviderInvalidOutput):
        decide(EvidenceDecisionProvider(model, FakeReader()), "내일 입고 검수 잡아 줘")


def test_stored_state_from_another_shape_starts_fresh() -> None:
    model = ScriptedModel(output(op("create", change={"title": [span("회의")]})))
    provider = EvidenceDecisionProvider(model, FakeReader())
    current = REFERENCE + timedelta(hours=1)
    envelope = provider.decide(
        context(
            "회의 잡아 줘",
            follow_up=True,
            state={"something": "else"},
            request_accepted_at=current,
        )
    )
    assert envelope.control_state["turns"] == ["회의 잡아 줘"]
    assert envelope.starts_new_task is True
    assert model.seen == [["회의 잡아 줘"]]
    assert model.references == [current.astimezone(timezone.utc)]


def test_cleared_state_uses_current_request_time_after_midnight() -> None:
    previous_reference = datetime(2026, 11, 7, 23, 50, tzinfo=SEOUL)
    current_request = datetime(2026, 11, 8, 0, 10, tzinfo=SEOUL)
    model = ScriptedModel(
        output(),
        output(
            op(
                "create",
                change={
                    "title": [span("회의")],
                    "date": [span("내일")],
                    "time": [span("오후 3시")],
                },
            )
        ),
    )
    provider = EvidenceDecisionProvider(model, FakeReader())

    guidance = provider.decide(
        context(
            "음 뭐였더라",
            request_accepted_at=previous_reference,
            task_reference_accepted_at=previous_reference,
        )
    )
    assert guidance.decision.model_dump(mode="json", by_alias=True) == {
        "kind": "ask",
        "text": texts.GUIDANCE,
    }
    assert guidance.control_state["turns"] == []

    created = provider.decide(
        context(
            "내일 오후 3시에 회의 잡아 줘",
            follow_up=True,
            state=guidance.control_state,
            request_accepted_at=current_request,
            task_reference_accepted_at=previous_reference,
        )
    )

    decision = created.decision.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    assert created.starts_new_task is True
    assert decision["kind"] == "create_intent"
    assert decision["scheduledAt"] == "2026-11-09T06:00:00Z"
    assert model.references == [
        previous_reference.astimezone(timezone.utc),
        current_request.astimezone(timezone.utc),
    ]


def test_active_task_keeps_original_reference_after_midnight() -> None:
    previous_reference = datetime(2026, 11, 7, 23, 50, tzinfo=SEOUL)
    current_request = datetime(2026, 11, 8, 0, 10, tzinfo=SEOUL)
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span("회의")],
                    "date": [span("내일")],
                },
            )
        ),
        output(
            op(
                "create",
                change={
                    "title": [span("회의")],
                    "date": [span("내일")],
                    "time": [span("오후 3시", turn=2)],
                },
            )
        ),
    )
    provider = EvidenceDecisionProvider(model, FakeReader())

    question = provider.decide(
        context(
            "내일 회의 잡아 줘",
            request_accepted_at=previous_reference,
            task_reference_accepted_at=previous_reference,
        )
    )
    assert question.decision.model_dump(mode="json", by_alias=True) == {
        "kind": "ask",
        "text": texts.ASK_TIME,
    }

    created = provider.decide(
        context(
            "오후 3시",
            follow_up=True,
            state=question.control_state,
            request_accepted_at=current_request,
            task_reference_accepted_at=previous_reference,
        )
    )

    decision = created.decision.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    assert created.starts_new_task is False
    assert decision["kind"] == "create_intent"
    assert decision["scheduledAt"] == "2026-11-08T06:00:00Z"
    assert model.references == [
        previous_reference.astimezone(timezone.utc),
        previous_reference.astimezone(timezone.utc),
    ]


def test_turn_limit_starts_new_task_with_current_request_time() -> None:
    previous_reference = REFERENCE
    current_request = REFERENCE + timedelta(hours=2)
    state = {
        "evidence": 4,
        "turns": [f"이전 입력 {index}" for index in range(8)],
        "consentGranted": False,
        "pending": None,
        "changeDate": None,
        "changeDateSource": None,
        "midnightValue": None,
        "midnightSource": None,
        "createConfirmed": None,
        "validatedOutput": None,
    }
    model = ScriptedModel(output(op("create", change={"title": [span("회의")]})))
    provider = EvidenceDecisionProvider(model, FakeReader())

    envelope = provider.decide(
        context(
            "회의 잡아 줘",
            follow_up=True,
            state=state,
            request_accepted_at=current_request,
            task_reference_accepted_at=previous_reference,
        )
    )

    assert envelope.starts_new_task is True
    assert envelope.control_state["turns"] == ["회의 잡아 줘"]
    assert model.seen == [["회의 잡아 줘"]]
    assert model.references == [current_request.astimezone(timezone.utc)]


# ------------------------------------------- stage 3: model mistake guards


@pytest.mark.parametrize("title", ["그거 하나", "일정 좀", "뭐 하나", "이것", "저번에 말한 그 건", "그 일정"])
def test_unclear_create_title_asks_for_the_title(title: str) -> None:
    turn = f"모레 오전 10시에 {title} 넣어 줘"
    model = ScriptedModel(
        output(op("create", change={"title": [span(title)], "date": [span("모레")], "time": [span("오전 10시")]}))
    )
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), turn)
    assert decision == {"kind": "ask", "text": texts.ASK_TITLE}


def test_unclear_target_title_asks_which_schedule() -> None:
    model = ScriptedModel(output(op("delete", target={"title": [span("그거")]})))
    reader = FakeReader([schedule("그거 정리", REFERENCE + timedelta(days=1))])
    _, decision = decide(EvidenceDecisionProvider(model, reader), "그거 지워 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_TARGET_TITLE}
    assert reader.reads == []  # nothing is searched with an unclear title


def test_unclear_list_title_asks_which_schedule() -> None:
    model = ScriptedModel(output(op("list", filter={"title": [span("그 일정")]})))
    reader = FakeReader([schedule("그 일정 정리", REFERENCE + timedelta(days=1))])
    _, decision = decide(EvidenceDecisionProvider(model, reader), "그 일정 보여 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_TARGET_TITLE}
    assert reader.reads == []


def test_unclear_update_target_asks_which_schedule() -> None:
    model = ScriptedModel(
        output(
            op(
                "update",
                target={"title": [span("그거")]},
                change={"completed": [span("완료")]},
            )
        )
    )
    reader = FakeReader([schedule("그거 정리", REFERENCE + timedelta(days=1))])
    _, decision = decide(EvidenceDecisionProvider(model, reader), "그거 완료로 바꿔 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_TARGET_TITLE}
    assert reader.reads == []


def test_unclear_new_title_asks_for_the_change() -> None:
    model = ScriptedModel(output(op("update", target={"title": [span("회의")]}, change={"title": [span("그거")]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "회의 제목을 그거로 바꿔 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_CHANGE_VALUE}


def test_create_title_over_schedule_limit_asks_for_the_title() -> None:
    title = "가" * 101
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span(title)],
                    "date": [span("내일")],
                    "time": [span("오후 3시")],
                },
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader()),
        f"내일 오후 3시에 {title} 등록해 줘",
    )
    assert decision == {"kind": "ask", "text": texts.ASK_TITLE}


def test_update_title_over_schedule_limit_asks_for_a_change_value() -> None:
    title = "가" * 101
    model = ScriptedModel(
        output(
            op(
                "update",
                target={"title": [span("회의")]},
                change={"title": [span(title)]},
            )
        )
    )
    reader = FakeReader([schedule("회의", REFERENCE + timedelta(days=1))])
    _, decision = decide(
        EvidenceDecisionProvider(model, reader),
        f"회의 제목을 {title}로 바꿔 줘",
    )
    assert decision == {"kind": "ask", "text": texts.ASK_CHANGE_VALUE}
    assert reader.reads == []


LK52_TURNS = ["반품 사유 분류 잡아 줘", "모레요", "오전 10시로 하고, 날짜는 모레 말고 글피로 바꿔 줘"]
# The run 006 mistake: the correction "모레 말고 글피" is ignored and "모레" is adopted.
LK52_MISSED = output(
    op(
        "create",
        change={
            "title": [span("반품 사유 분류")],
            "date": [span("모레", turn=2)],
            "time": [span("오전 10시", turn=3)],
            "category": [span("반품")],
        },
    )
)


def _lk52_provider(*extra: dict) -> tuple[EvidenceDecisionProvider, object]:
    model = ScriptedModel(
        output(op("create", change={"title": [span("반품 사유 분류")], "category": [span("반품")]})),
        output(op("create", change={"title": [span("반품 사유 분류")], "date": [span("모레", turn=2)]})),
        LK52_MISSED,
        *extra,
    )
    provider = EvidenceDecisionProvider(model, FakeReader())
    previous, _ = decide(provider, LK52_TURNS[0])
    previous, _ = decide(provider, LK52_TURNS[1], previous=previous)
    return provider, previous


def test_missed_correction_asks_for_the_omitted_date() -> None:
    provider, previous = _lk52_provider()
    _, decision = decide(provider, LK52_TURNS[2], previous=previous)
    assert decision == {"kind": "ask", "text": texts.ASK_DATE_AGAIN}


def test_missed_correction_clarification_rejects_a_yes_no_answer() -> None:
    provider, previous = _lk52_provider()
    third, _ = decide(provider, LK52_TURNS[2], previous=previous)
    _, decision = decide(provider, "아니", previous=third)
    assert decision == {"kind": "ask", "text": texts.ASK_DATE_AGAIN}


def test_missed_correction_cannot_be_authorized_with_yes() -> None:
    provider, previous = _lk52_provider(LK52_MISSED)
    third, _ = decide(provider, LK52_TURNS[2], previous=previous)
    _, decision = decide(provider, "응", previous=third)
    assert decision == {"kind": "ask", "text": texts.ASK_DATE_AGAIN}


def test_date_clarification_is_rechecked_before_create() -> None:
    wrong = output(
        op(
            "create",
            change={
                "title": [span("회의")],
                "date": [span("12월 5일")],
                "time": [span("오후 3시")],
            },
        )
    )
    corrected = output(
        op(
            "create",
            change={
                "title": [span("회의")],
                "date": [span("2027년 12월 5일", turn=2)],
                "time": [span("오후 3시")],
            },
        )
    )
    model = ScriptedModel(wrong, corrected)
    provider = EvidenceDecisionProvider(model, FakeReader())

    first, decision = decide(provider, "내년 12월 5일 오후 3시에 회의 등록해 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_DATE_AGAIN}

    _, decision = decide(provider, "2027년 12월 5일", previous=first)
    assert decision["kind"] == "create_intent"
    assert decision["scheduledAt"] == "2027-12-05T06:00:00Z"
    assert len(model.seen) == 2


def test_marked_correction_creates_without_extra_question() -> None:
    turns = {1: "내일 오전 11시 아니 오후 1시에 출고 박스 테이핑 확인 등록해 줘"}
    model = ScriptedModel(
        output(
            op(
                "create",
                change={
                    "title": [span("출고 박스 테이핑 확인")],
                    "date": [span("내일")],
                    "time": [
                        span("오전 11시", status="superseded"),
                        span("아니", status="correction_marker"),
                        span("오후 1시"),
                    ],
                },
            )
        )
    )
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), turns[1])
    assert decision["kind"] == "create_intent"
    assert datetime.fromisoformat(decision["scheduledAt"]) == datetime(2026, 10, 30, 13, 0, tzinfo=SEOUL)


LK28_TURN = "출고 적재 순서 확인 일정을 2027년 11월 9일 오후 3시 20분으로 옮겨 줘"
# The run 004 mistake: a move request read as a new schedule.
LK28_AS_CREATE = output(
    op(
        "create",
        change={
            "title": [span("출고 적재 순서 확인")],
            "date": [span("2027년 11월 9일")],
            "time": [span("오후 3시 20분")],
        },
    )
)


def test_change_words_read_as_create_ask_before_creating() -> None:
    provider = EvidenceDecisionProvider(ScriptedModel(LK28_AS_CREATE), FakeReader())
    _, decision = decide(provider, LK28_TURN)
    assert decision == {
        "kind": "ask",
        "text": texts.confirm_new_create(
            "출고 적재 순서 확인",
            datetime(2027, 11, 9, 15, 20, tzinfo=SEOUL),
        ),
    }


def test_change_words_with_correction_evidence_still_ask_before_creating() -> None:
    turn = "내일 오후 3시 말고 오후 4시로 회의 옮겨 줘"
    evidence = output(
        op(
            "create",
            change={
                "title": [span("회의")],
                "date": [span("내일")],
                "time": [
                    span("오후 3시", status="superseded"),
                    span("말고", status="correction_marker"),
                    span("오후 4시"),
                ],
            },
        )
    )
    _, decision = decide(EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()), turn)
    assert decision == {
        "kind": "ask",
        "text": texts.confirm_new_create(
            "회의", datetime(2026, 10, 30, 16, 0, tzinfo=SEOUL)
        ),
    }


@pytest.mark.parametrize(
    "turn",
    [
        "내일 오후 3시 회의 있어?",
        "내일 오후 3시 회의 있나요?",
        "내일 오후 3시 회의 보여 줘",
        "내일 오후 3시 회의 알려 줘",
        "내일 오후 3시 회의 뭐 있어?",
        "내일 오후 3시 회의 조회해 줘",
    ],
)
def test_query_words_read_as_create_ask_before_creating(turn: str) -> None:
    evidence = output(
        op(
            "create",
            change={
                "title": [span("회의")],
                "date": [span("내일")],
                "time": [span("오후 3시")],
            },
        )
    )
    _, decision = decide(EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()), turn)
    assert decision == {
        "kind": "ask",
        "text": texts.confirm_new_create(
            "회의", datetime(2026, 10, 30, 15, 0, tzinfo=SEOUL)
        ),
    }


@pytest.mark.parametrize(
    "turn",
    [
        "내일 오후 3시 회의 있어요?",
        "내일 오후 3시 회의 있니?",
        "내일 오후 3시 회의 있나?",
        "내일 오후 3시 회의 있는지 확인해 줘",
        "내일 회의 오후 4시로 미뤄 줘",
        "내일 회의 오후 2시로 당겨 줘",
        "오늘 오후 5시 회의 완료 처리해 줘",
        "내일 오후 3시 회의 등록돼 있나?",
        "내일 오후 3시 회의 등록하지 마",
        "내일 오후 3시 회의 추가된 거 있나?",
        "내일 오후 3시 회의 등록했어?",
        "내일 오후 3시 회의 등록했나?",
        "내일 오후 3시 회의 잡아 뒀나?",
        "내일 오후 3시 회의 넣어 놨어?",
        "내일 오후 3시 회의 추가했었나?",
        "내일 오후 3시 회의 등록해야 하나?",
        "내일 오후 3시 회의 등록하셨어요?",
        "내일 오후 3시 회의 등록하셨나요?",
        "내일 오후 3시 회의 추가하셨어요?",
        "내일 오후 3시 회의 등록하였나?",
        "내일 오후 3시 회의 등록하는 거야?",
        "내일 오후 3시 회의 등록 완료됐어?",
        "내일 오후 3시 회의 등록이 됐나?",
        "내일 오후 3시 회의 등록은 했나?",
        "내일 오후 3시 회의 등록 안 됐나?",
        "내일 오후 3시 회의 등록할까?",
        "내일 오후 3시 회의 등록해 놓을까?",
        "내일 오후 3시 회의 등록한 거 맞지?",
        "내일 오후 3시 회의 등록하셨어요",
        "내일 오후 3시 회의 등록하는 거야",
        "내일 오후 3시 회의 등록 완료됐어",
        "내일 오후 3시 회의 등록이 됐어",
        "내일 오후 3시 회의 등록한 거 맞지",
        "내일 오후 3시 회의 등록하신 거 맞으세요",
        "내일 오후 3시 이미 등록한 회의야",
        "내일 오후 3시 회의 등록해야 돼",
        "내일 오후 3시 회의 등록 다 했어",
        "내일 오후 3시 회의 등록 이미 됐어",
        "내일 오후 3시 회의 등록은 벌써 했지",
        "내일 오후 3시 회의 등록 잘 됐어",
        "내일 오후 3시 회의 등록해 줬어",
        "내일 오후 3시 회의 등록해 주셨어요",
        "내일 오후 3시 회의 추가해 줬지",
        "내일 오후 3시 회의 넣어 줬어",
        "내일 오후 3시 회의 만들어 주셨네",
    ],
)
def test_non_create_wording_read_as_create_requires_confirmation(turn: str) -> None:
    title = "회의 있어" if "회의 있어" in turn else "회의"
    day = "오늘" if "오늘" in turn else "내일"
    clock = next(
        value
        for value in ("오후 2시", "오후 3시", "오후 4시", "오후 5시")
        if value in turn
    )
    evidence = output(
        op(
            "create",
            change={
                "title": [span(title)],
                "date": [span(day)],
                "time": [span(clock)],
            },
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()), turn
    )
    assert decision["kind"] == "ask"
    assert all(token in decision["text"] for token in ("년", "월", "일", "시"))


@pytest.mark.parametrize(
    "request_phrase",
    [
        "등록해 줄 수 있어?",
        "넣어 줄 수 있어?",
        "잡아 줄 수 있어?",
        "넣어 줄래요?",
        "등록해 주시겠어요?",
        "추가해 주실래요?",
        "등록 부탁드리겠습니다",
        "등록해 줘",
        "등록하고 싶어",
        "등록해 주면 좋겠어",
    ],
)
def test_create_capability_question_is_still_a_create_request(request_phrase: str) -> None:
    evidence = output(
        op(
            "create",
            change={
                "title": [span("회의")],
                "date": [span("내일")],
                "time": [span("오후 3시")],
            },
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()),
        f"내일 오후 3시 회의 {request_phrase}",
    )
    assert decision["kind"] == "create_intent"


def test_marker_without_replaced_value_does_not_bypass_create_confirmation() -> None:
    evidence = output(
        op(
            "create",
            change={
                "title": [span("회의")],
                "date": [
                    span("말고", status="correction_marker"),
                    span("5일"),
                ],
                "time": [span("오후 2시")],
            },
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(ScriptedModel(evidence), FakeReader()),
        "내일 말고 5일 오후 2시에 회의 잡아 줘",
    )
    assert decision == {
        "kind": "ask",
        "text": texts.confirm_create_values(
            "회의", datetime(2026, 11, 5, 14, 0, tzinfo=SEOUL)
        ),
    }


def test_change_words_answered_no_starts_over() -> None:
    provider = EvidenceDecisionProvider(ScriptedModel(LK28_AS_CREATE), FakeReader())
    first, _ = decide(provider, LK28_TURN)
    envelope, decision = decide(provider, "아니", previous=first)
    assert decision == {"kind": "ask", "text": texts.ASK_RESTATE}
    assert envelope.control_state["turns"] == []


def test_change_words_answered_yes_creates() -> None:
    provider = EvidenceDecisionProvider(ScriptedModel(LK28_AS_CREATE), FakeReader())
    first, _ = decide(provider, LK28_TURN)
    _, decision = decide(provider, "응", previous=first)
    assert decision["kind"] == "create_intent"
    assert decision["timeInterpretation"] == "absolute"
    assert model_turns(provider) == [[LK28_TURN]]


def model_turns(provider: EvidenceDecisionProvider) -> list[list[str]]:
    return provider._model.seen


MOVE_PENDING_FIRST = output(
    op("create", change={"title": [span("재고 회의")], "date": [span("모레")]})
)


def _pending_create(answer: str, answer_output: dict) -> dict:
    provider = EvidenceDecisionProvider(ScriptedModel(MOVE_PENDING_FIRST, answer_output), FakeReader())
    first, decision = decide(provider, "모레 재고 회의 넣어 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_TIME}
    _, decision = decide(provider, answer, previous=first)
    return decision


def test_slot_change_in_an_answer_creates_without_new_create_question() -> None:
    answer = "오후 4시로 하고 날짜는 글피로 바꿔 줘"
    decision = _pending_create(
        answer,
        output(op("create", change={
            "title": [span("재고 회의")], "date": [span("글피", turn=2)], "time": [span("오후 4시", turn=2)],
        })),
    )
    assert decision["kind"] == "create_intent"
    assert decision["scheduledAt"] == "2026-11-01T07:00:00Z"  # 11월 1일 오후 4시 (Seoul)


@pytest.mark.parametrize("answer", ["오후 4시, 그리고 기존 회의는 옮겨 줘", "오후 4시, 근데 그건 취소해 줘"])
def test_other_change_words_in_an_answer_still_ask(answer: str) -> None:
    decision = _pending_create(
        answer,
        output(op("create", change={
            "title": [span("재고 회의")], "date": [span("모레")], "time": [span("오후 4시", turn=2)],
        })),
    )
    assert decision == {
        "kind": "ask",
        "text": texts.confirm_new_create(
            "재고 회의", datetime(2026, 10, 31, 16, 0, tzinfo=SEOUL)
        ),
    }


def test_category_word_from_the_target_title_is_not_a_category_change() -> None:
    # Real run 006 output for "입고 검수 일정을 오후 5시로 옮겨 줘".
    target = schedule("입고 검수", datetime(2026, 10, 30, 15, 30, tzinfo=SEOUL))
    model = ScriptedModel(
        output(
            op(
                "update",
                target={"title": [span("입고 검수")]},
                change={"time": [span("오후 5시")], "category": [span("입고")]},
            )
        )
    )
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader([target])), "입고 검수 일정을 오후 5시로 옮겨 줘")
    assert decision["kind"] == "confirm"
    assert set(decision["changes"]) == {"scheduledAt"}


def test_category_word_from_target_title_alone_asks_for_a_change_value() -> None:
    target = schedule("입고 검수", REFERENCE + timedelta(days=1))
    model = ScriptedModel(
        output(
            op(
                "update",
                target={"title": [span("입고 검수")]},
                change={"category": [span("입고")]},
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader([target])),
        "입고 검수 분류를 바꿔 줘",
    )
    assert decision == {"kind": "ask", "text": texts.ASK_CHANGE_VALUE}


def test_repeated_category_word_outside_target_title_is_a_change() -> None:
    target = schedule(
        "입고 검수",
        REFERENCE + timedelta(days=1),
        category=Category.OTHER,
    )
    model = ScriptedModel(
        output(
            op(
                "update",
                target={"title": [span("입고 검수")]},
                change={"category": [span("입고", occurrence=2)]},
            )
        )
    )
    _, decision = decide(
        EvidenceDecisionProvider(model, FakeReader([target])),
        "입고 검수 분류를 입고로 바꿔 줘",
    )
    assert decision["kind"] == "confirm"
    assert decision["changes"] == {"category": "INBOUND"}


def test_category_change_outside_the_title_is_kept() -> None:
    target = schedule("입고 검수", REFERENCE + timedelta(days=1))
    model = ScriptedModel(
        output(op("update", target={"title": [span("입고 검수")]}, change={"category": [span("출고")]}))
    )
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader([target])), "입고 검수 분류를 출고로 바꿔 줘")
    assert decision["changes"] == {"category": "OUTBOUND"}


# ------------------------------------------------------------- model call log


def _model_call_records(caplog) -> list:
    return [r for r in caplog.records if getattr(r, "event", None) == "assistant.model_call"]


def test_model_call_log_keeps_metrics_without_user_or_model_text(caplog) -> None:
    caplog.set_level("INFO", logger="app.modules.assistant.evidence.provider")
    created = output(op("create", change={
        "title": [span("입고 검수")], "date": [span("내일")], "time": [span("오후 3시")],
    }))
    model = ScriptedModel(created)
    model.name = "run-006"
    model.prompt_sha256 = "abc"
    decide(EvidenceDecisionProvider(model, FakeReader()), "내일 오후 3시에 입고 검수 잡아 줘")
    (record,) = _model_call_records(caplog)
    assert record.validation == "ok"
    assert (record.model, record.promptSha256) == ("run-006", "abc")
    assert record.turnCount == 1
    assert record.inputChars == len("내일 오후 3시에 입고 검수 잡아 줘")
    assert len(record.inputSha256) == len(record.outputSha256) == 64
    assert record.outputBytes == len(
        json.dumps(created, ensure_ascii=False).encode("utf-8")
    )
    assert not hasattr(record, "userTurns")
    assert not hasattr(record, "modelOutput")
    assert record.durationMs >= 0


def test_invalid_model_output_is_logged_with_the_error_code(caplog) -> None:
    caplog.set_level("INFO", logger="app.modules.assistant.evidence.provider")
    model = ScriptedModel(output(op("create", change={"title": [span("없는 제목")]})))
    with pytest.raises(DecisionProviderInvalidOutput):
        decide(EvidenceDecisionProvider(model, FakeReader()), "내일 회의 잡아 줘")
    (record,) = _model_call_records(caplog)
    assert record.validation == "SPAN_NOT_IN_TURN"
    assert record.outputBytes > 0
    assert len(record.outputSha256) == 64
    assert not hasattr(record, "modelOutput")


def test_model_timeout_is_logged_without_output(caplog) -> None:
    from app.modules.assistant.decisions import DecisionProviderTimeout

    class SlowModel:
        def extract(self, **_: object) -> str:
            raise DecisionProviderTimeout("model server timed out")

    caplog.set_level("INFO", logger="app.modules.assistant.evidence.provider")
    with pytest.raises(DecisionProviderTimeout):
        decide(EvidenceDecisionProvider(SlowModel(), FakeReader()), "내일 회의 잡아 줘")
    (record,) = _model_call_records(caplog)
    assert (record.validation, record.outputBytes) == ("DecisionProviderTimeout", 0)
    assert getattr(record, "outputSha256", None) is None
    assert not hasattr(record, "modelOutput")


# ------------------------------------------------------- more expressions (#37 stage 8)


@pytest.mark.parametrize(
    ("date_text", "time_text", "expected"),
    [
        ("다음 주 화요일", "오후 3시", "2026-11-03T06:00:00Z"),
        ("다음 달 3일", "15:30", "2026-11-03T06:30:00Z"),
        ("15일", "점심 1시", "2026-11-15T04:00:00Z"),
        ("3일 뒤", "정오", "2026-11-01T03:00:00Z"),
        ("모레", "오후 세 시 반", "2026-10-31T06:30:00Z"),
    ],
)
def test_create_with_new_expressions(date_text: str, time_text: str, expected: str) -> None:
    text = f"{date_text} {time_text}에 회의 잡아 줘"
    model = ScriptedModel(output(op("create", change={
        "title": [span("회의")], "date": [span(date_text)], "time": [span(time_text)],
    })))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), text)
    assert (decision["kind"], decision["scheduledAt"]) == ("create_intent", expected)
    assert decision["timeInterpretation"] == "relative"


def test_create_at_midnight_word_asks_first() -> None:
    model = ScriptedModel(output(op("create", change={
        "title": [span("서버 점검")], "date": [span("모레")], "time": [span("자정")],
    })))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "모레 자정에 서버 점검 잡아 줘")
    assert decision == {
        "kind": "ask",
        "text": texts.confirm_midnight(
            datetime(2026, 11, 1, 0, 0, tzinfo=SEOUL)
        ),
    }


@pytest.mark.parametrize(
    ("period", "start", "end"),
    [
        ("다음 주", "2026-11-01T15:00:00Z", "2026-11-08T15:00:00Z"),
        ("이번 달", "2026-09-30T15:00:00Z", "2026-10-31T15:00:00Z"),
    ],
)
def test_list_by_week_or_month_word(period: str, start: str, end: str) -> None:
    model = ScriptedModel(output(op("list", filter={"date": [span(period)]})))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), f"{period} 일정 보여 줘")
    assert (decision["kind"], decision["from"], decision["to"]) == ("list_intent", start, end)


def test_confirmation_answer_with_a_new_time_is_reinterpreted() -> None:
    original = output(op("create", change={
        "title": [span("회의")], "date": [span("내일")], "time": [span("오후 3시")],
    }), op("create", index=2, change={"title": [span("점검")], "date": [span("내일")], "time": [span("오후 4시")]}))
    corrected = output(op("create", change={
        "title": [span("회의")],
        "date": [span("내일")],
        "time": [
            span("오후 3시", status="superseded"),
            span("아니", turn=2, status="correction_marker"),
            span("정오", turn=2),
        ],
    }), op("create", index=2, change={"title": [span("점검")], "date": [span("내일")], "time": [span("오후 4시")]}))
    model = ScriptedModel(original, corrected)
    provider = EvidenceDecisionProvider(model, FakeReader())
    first, decision = decide(provider, "내일 오후 3시 회의랑 오후 4시 점검 잡아 줘")
    assert decision["kind"] == "ask"
    _, decision = decide(provider, "아니 정오로", previous=first)
    assert decision == {
        "kind": "ask",
        "text": texts.confirm_first_create(
            "회의", datetime(2026, 10, 30, 12, 0, tzinfo=SEOUL)
        ),
    }
    assert model.seen == [
        ["내일 오후 3시 회의랑 오후 4시 점검 잡아 줘"],
        ["내일 오후 3시 회의랑 오후 4시 점검 잡아 줘", "아니 정오로"],
    ]


# ------------------------------------------- confirmed values follow their evidence (review)


def test_confirmed_next_year_date_does_not_override_a_later_correction() -> None:
    first_output = output(op("create", change={
        "title": [span("회의")], "date": [span("2월 3일")], "time": [span("3시")],
    }))
    corrected = output(op("create", change={
        "title": [span("회의")],
        "date": [span("2월 3일", 1, "superseded"), span("아니", 2, "correction_marker"), span("다음 주 금요일", 2)],
        "time": [span("3시", 1, "superseded"), span("아니", 2, "correction_marker"), span("오후 3시", 2)],
    }))
    provider = EvidenceDecisionProvider(ScriptedModel(first_output, corrected), FakeReader())
    first, decision = decide(provider, "2월 3일 3시 회의 잡아 줘")
    assert decision["text"] == texts.confirm_next_year(date(2027, 2, 3))
    second, decision = decide(provider, "응", previous=first)
    assert decision == {"kind": "ask", "text": texts.ASK_AMPM}
    _, decision = decide(provider, "아니 다음 주 금요일 오후 3시로", previous=second)
    assert (decision["kind"], decision["scheduledAt"]) == ("create_intent", "2026-11-06T06:00:00Z")


def test_confirmed_create_is_checked_again_when_the_values_change() -> None:
    from app.modules.assistant.evidence.provider import _State, _create_signature

    state = _State(turns=["내일 오후 3시 회의 잡아 줘"], create_confirmed=_create_signature(
        {"title": "회의", "scheduledAt": "2026-10-30T06:00:00Z", "category": "OTHER"}
    ))
    changed = {"title": "회의", "scheduledAt": "2026-10-30T07:00:00Z", "category": "OTHER"}
    assert state.create_confirmed != _create_signature(changed)


def test_old_state_shape_starts_fresh() -> None:
    from app.modules.assistant.evidence.provider import _load_state

    old = {"evidence": 1, "turns": ["회의"], "consentGranted": False, "pending": None,
           "changeDate": "2027-02-03", "midnight": True, "createConfirmed": True}
    assert _load_state(old) is None
    assert _load_state({"evidence": 2, "turns": []}) is None
    assert _load_state({"evidence": 3, "turns": []}) is None


@pytest.mark.parametrize(
    ("text", "replaced", "adopted", "time_text", "expected"),
    [
        ("12월 3일 말고 5일 오후 2시에 회의 잡아 줘", "12월 3일", "5일", "오후 2시", "2026-12-05T05:00:00Z"),
        ("다음 주 화요일 말고 수요일 오후 2시에 회의 잡아 줘", "다음 주 화요일", "수요일", "오후 2시", "2026-11-04T05:00:00Z"),
    ],
)
def test_correction_without_month_or_week_keeps_the_replaced_one(
    text: str, replaced: str, adopted: str, time_text: str, expected: str
) -> None:
    model = ScriptedModel(output(op("create", change={
        "title": [span("회의")],
        "date": [span(replaced, 1, "superseded"), span("말고", 1, "correction_marker"), span(adopted)],
        "time": [span(time_text)],
    })))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), text)
    assert (decision["kind"], decision["scheduledAt"]) == ("create_intent", expected)


def test_correction_without_month_asks_when_the_replaced_date_is_unreadable() -> None:
    model = ScriptedModel(output(op("create", change={
        "title": [span("회의")],
        "date": [span("그날", 1, "superseded"), span("말고", 1, "correction_marker"), span("5일")],
        "time": [span("오후 2시")],
    })))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "그날 말고 5일 오후 2시에 회의 잡아 줘")
    assert decision == {"kind": "ask", "text": texts.ASK_DATE_AGAIN}


@pytest.mark.parametrize(
    ("latest", "question"),
    [
        ("아니 정오로", texts.ASK_TIME_AGAIN),
        ("아니 15:30으로", texts.ASK_TIME_AGAIN),
        ("아니 다음 달 3일로", texts.ASK_DATE_AGAIN),
        ("아니 열한 시 반으로", texts.ASK_TIME_AGAIN),
    ],
)
def test_missed_correction_with_new_expressions_asks_for_that_value(
    latest: str, question: str
) -> None:
    # The model kept the old values and marked no correction.
    missed = output(op("create", change={
        "title": [span("회의")], "date": [span("내일")], "time": [span("오후 3시")],
    }))
    turns = ["내일 오후 3시에 회의 잡아 줘", latest]
    model = ScriptedModel(missed)
    provider = EvidenceDecisionProvider(model, FakeReader())
    state = {"evidence": 4, "turns": [turns[0]], "consentGranted": False, "pending": None,
             "changeDate": None, "changeDateSource": None, "midnightValue": None,
             "midnightSource": None,
             "createConfirmed": None, "validatedOutput": None}
    envelope = provider.decide(context(latest, follow_up=True, state=state))
    decision = envelope.decision.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert decision == {"kind": "ask", "text": question}


@pytest.mark.parametrize("title", ["정오", "다음 주 화요일", "15:30"])
def test_date_or_time_alone_is_not_a_title(title: str) -> None:
    model = ScriptedModel(output(op("create", change={"title": [span(title)], "date": [span("내일")]})))
    with pytest.raises(DecisionProviderInvalidOutput):
        decide(EvidenceDecisionProvider(model, FakeReader()), f"내일 {title} 잡아 줘")


def test_inherited_past_date_without_year_asks_about_next_year() -> None:
    created = output(op("create", change={
        "title": [span("회의")],
        "date": [span("2월 3일", 1, "superseded"), span("말고", 1, "correction_marker"), span("5일")],
        "time": [span("오후 2시")],
    }))
    provider = EvidenceDecisionProvider(ScriptedModel(created, created), FakeReader())
    first, decision = decide(provider, "2월 3일 말고 5일 오후 2시에 회의 잡아 줘")
    assert decision == {"kind": "ask", "text": texts.confirm_next_year(date(2027, 2, 5))}
    _, decision = decide(provider, "응", previous=first)
    assert (decision["kind"], decision["scheduledAt"]) == ("create_intent", "2027-02-05T05:00:00Z")


def test_correction_from_tomorrow_to_a_day_reads_the_day_on_its_own() -> None:
    model = ScriptedModel(output(op("create", change={
        "title": [span("회의")],
        "date": [span("내일", 1, "superseded"), span("말고", 1, "correction_marker"), span("5일")],
        "time": [span("오후 2시")],
    })))
    _, decision = decide(EvidenceDecisionProvider(model, FakeReader()), "내일 말고 5일 오후 2시에 회의 잡아 줘")
    # The coming 5th (user decision for a day alone), not a date in the past.
    assert decision["scheduledAt"] == "2026-11-05T05:00:00Z"
