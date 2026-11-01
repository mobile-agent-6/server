"""DecisionProvider that uses the evidence-only model output.

The model only returns evidence spans.  This provider keeps the conversation
turns, asks consent for unsupported requests, checks the output, and lets the
planner build the decision.  The model never sees or picks Schedule IDs.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from app.modules.assistant.decisions import DecisionContext, DecisionEnvelope
from app.modules.assistant.evidence import texts
from app.modules.assistant.evidence.contract import (
    EvidenceOperation,
    EvidenceOutput,
    SpanStatus,
    parse_evidence_output,
)
from app.modules.assistant.evidence.gate import (
    Answer,
    analyze_text,
    check_roles,
    check_turn_prefix,
    classify_answer,
)
from app.modules.assistant.evidence.normalize import mentions_date_or_time
from app.modules.assistant.evidence.planner import Ask, Overrides, plan_operation
from app.modules.assistant.schedule_read import ScheduleReadPort

logger = logging.getLogger(__name__)

STATE_KEY = "evidence"
# Shape of the provider's own JSON; an older shape starts the task fresh.
STATE_SHAPE = 2
STATE_VERSION = 1
MAX_TURNS = 8
MAX_TURN_CHARS = 500


class EvidenceModel(Protocol):
    """Returns the raw evidence JSON text for the user turns so far."""

    def extract(
        self,
        *,
        turns: Sequence[str],
        reference_time: datetime,
        server_time_trusted: bool,
        timeout_seconds: float | None = None,
    ) -> str: ...


@dataclass
class _State:
    turns: list[str] = field(default_factory=list)
    consent_granted: bool = False
    # {"kind": "consent"} or {"kind": "confirm", "slot": ..., "value": ..., "text": ...}
    pending: dict[str, str] | None = None
    # Confirmed values keep the evidence they were asked about (see Overrides).
    change_date: str | None = None
    change_date_source: str | None = None
    midnight_source: str | None = None
    # The create the user said yes to (title, time, category), so a changed create is checked again.
    create_confirmed: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            STATE_KEY: STATE_SHAPE,
            "turns": list(self.turns),
            "consentGranted": self.consent_granted,
            "pending": self.pending,
            "changeDate": self.change_date,
            "changeDateSource": self.change_date_source,
            "midnightSource": self.midnight_source,
            "createConfirmed": self.create_confirmed,
        }

    @property
    def overrides(self) -> Overrides:
        return Overrides(
            change_date=date.fromisoformat(self.change_date) if self.change_date else None,
            change_date_source=self.change_date_source,
            midnight_source=self.midnight_source,
        )


def _load_state(value: object) -> _State | None:
    """Accept only the exact shape this provider writes; anything else starts fresh."""

    if not isinstance(value, dict) or value.get(STATE_KEY) != STATE_SHAPE:
        return None
    turns = value.get("turns")
    pending = value.get("pending")
    change_date = value.get("changeDate")
    optional_text = ("changeDate", "changeDateSource", "midnightSource", "createConfirmed")
    if (
        not isinstance(turns, list)
        or not all(isinstance(turn, str) for turn in turns)
        or len(turns) > MAX_TURNS
        or type(value.get("consentGranted")) is not bool
        or not all(value.get(key) is None or isinstance(value.get(key), str) for key in optional_text)
        or not (pending is None or (isinstance(pending, dict) and all(isinstance(v, str) for v in pending.values())))
    ):
        return None
    try:
        if change_date is not None:
            date.fromisoformat(change_date)
    except ValueError:
        return None
    return _State(
        turns=list(turns),
        consent_granted=value["consentGranted"],
        pending=dict(pending) if pending is not None else None,
        change_date=change_date,
        change_date_source=value.get("changeDateSource"),
        midnight_source=value.get("midnightSource"),
        create_confirmed=value.get("createConfirmed"),
    )


def _envelope(decision: dict[str, Any], state: _State) -> DecisionEnvelope:
    return DecisionEnvelope.model_validate(
        {"decision": decision, "controlStateVersion": STATE_VERSION, "controlState": state.to_json()}
    )


def _cleared() -> _State:
    # Overwrite stored turns once a task ends, so user text is not kept longer than needed.
    return _State()


class EvidenceDecisionProvider:
    def __init__(self, model: EvidenceModel, schedule_reader: ScheduleReadPort) -> None:
        self._model = model
        self._reader = schedule_reader

    # ------------------------------------------------------------ helpers

    def _ask(self, text: str, state: _State) -> DecisionEnvelope:
        return _envelope({"kind": "ask", "text": text}, state)

    def _starting_state(self, context: DecisionContext, text: str) -> _State:
        previous = None
        if context.is_question_follow_up and context.answered_question_kind == "need_more_info":
            previous = _load_state(context.control_state)
        if previous is None or len(previous.turns) >= MAX_TURNS:
            return _State(turns=[text])
        previous.turns.append(text)
        return previous

    # ------------------------------------------------------------- decide

    def decide(self, context: DecisionContext) -> DecisionEnvelope:
        if context.input.get("type") != "text" or not isinstance(context.input.get("text"), str):
            raise ValueError("evidence provider accepts text input only")
        text = context.input["text"][:MAX_TURN_CHARS]
        state = self._starting_state(context, text)
        reference = context.task_reference_accepted_at

        pending = state.pending
        if pending is not None:
            state.pending = None
            answer = classify_answer(text)
            # A yes or no ("응, 알림 없이 등록해") is not part of the request.  The
            # model was never trained on it and misreads the request when it is there.
            state.turns.pop()
            if pending.get("kind") == "consent":
                if answer is Answer.NO:
                    return _envelope({"kind": "cancel", "text": texts.CONSENT_DECLINED}, _cleared())
                if answer is Answer.UNKNOWN:
                    state.pending = pending
                    return self._ask(texts.CONSENT_QUESTION, state)
                state.consent_granted = True
            elif pending.get("kind") == "confirm":
                if answer is Answer.UNKNOWN:
                    state.pending = pending
                    return self._ask(pending.get("text", texts.ASK_DATE_AGAIN), state)
                slot = pending.get("slot")
                if answer is Answer.NO:
                    if slot == "first_operation":
                        return _envelope({"kind": "cancel", "text": texts.FIRST_DECLINED}, _cleared())
                    if slot == "new_create":
                        # Probably a change to an existing schedule: start over.
                        return _envelope({"kind": "ask", "text": texts.ASK_RESTATE}, _cleared())
                    if slot == "values":
                        return self._ask(texts.ASK_DATETIME_AGAIN, state)
                    again = texts.ASK_TIME_AGAIN if slot == "midnight" else texts.ASK_DATE_AGAIN
                    return self._ask(again, state)
                if slot == "change_date":
                    state.change_date = pending.get("value")
                    state.change_date_source = pending.get("source")
                elif slot == "midnight":
                    state.midnight_source = pending.get("source")
                elif slot in {"first_operation", "values", "new_create"}:
                    state.create_confirmed = pending.get("value")

        if not state.consent_granted:
            analyses = [analyze_text(turn) for turn in state.turns]
            if any(item.has_unsupported_request for item in analyses):
                if any(item.supported_operation for item in analyses):
                    state.pending = {"kind": "consent"}
                    return self._ask(texts.CONSENT_QUESTION, state)
                return _envelope({"kind": "ask", "text": texts.GUIDANCE}, _cleared())

        output = self._extract(state, context, reference)
        return self._plan(output, state, context, reference)

    def _extract(self, state: _State, context: DecisionContext, reference: datetime) -> EvidenceOutput:
        """Call the model once, check the output, and log the call (Issue #29)."""

        turns = {index: turn for index, turn in enumerate(state.turns, 1)}
        started = time.monotonic()
        raw: str | None = None
        validation = "ok"
        try:
            raw = self._model.extract(
                turns=list(state.turns),
                reference_time=reference,
                server_time_trusted=context.server_time_trusted,
                timeout_seconds=context.time_budget_seconds,
            )
            output = parse_evidence_output(raw, user_turns=turns)
            check_roles(output, reference=reference)
            check_turn_prefix(list(turns), len(turns))
            return output
        except Exception as exc:
            validation = str(getattr(exc, "code", None) or type(exc).__name__)
            raise
        finally:
            duration_ms = round((time.monotonic() - started) * 1000)
            logger.info(
                "Assistant model call %s in %d ms",
                validation,
                duration_ms,
                extra={
                    "event": "assistant.model_call",
                    "requestId": str(context.request_id),
                    "durationMs": duration_ms,
                    "model": getattr(self._model, "name", None),
                    "promptSha256": getattr(self._model, "prompt_sha256", None),
                    "userTurns": list(state.turns),
                    "modelOutput": raw,
                    "validation": validation,
                },
            )

    def _plan(
        self, output: EvidenceOutput, state: _State, context: DecisionContext, reference: datetime
    ) -> DecisionEnvelope:
        if not output.operations:
            return _envelope({"kind": "ask", "text": texts.GUIDANCE}, _cleared())
        operation = output.operations[0]
        model_saw_unsupported = bool(output.unsupported) or bool(operation.unsupported)
        if model_saw_unsupported and not state.consent_granted:
            state.pending = {"kind": "consent"}
            return self._ask(texts.CONSENT_QUESTION, state)

        decision, ask = plan_operation(
            operation,
            reference=reference,
            server_time_trusted=context.server_time_trusted,
            overrides=state.overrides,
            reader=self._reader,
        )
        if ask is not None:
            if ask.confirm is not None:
                state.pending = {"kind": "confirm", **ask.confirm, "text": ask.text}
            return self._ask(ask.text, state)
        if decision is None:
            raise ValueError("planner returned neither a decision nor a question")
        several = len(output.operations) > 1
        if decision["kind"] == "create_intent" and state.create_confirmed != _create_signature(decision):
            # A create runs at once with no confirmation step, so these checks
            # stop a model mistake before it becomes a wrong schedule.
            check = _create_check(decision, operation, state.turns, several=several)
            if check is not None:
                slot, question = check
                state.pending = {
                    "kind": "confirm",
                    "slot": slot,
                    "value": _create_signature(decision),
                    "text": question,
                }
                return self._ask(question, state)
        if several and decision["kind"] in {"confirm", "select_candidate"}:
            decision["text"] = decision["text"] + texts.MORE_REQUESTS_NOTE
        if not context.server_time_trusted and _is_relative(decision):
            # The processor will ask for an absolute date; keep the turns for that answer.
            return _envelope(decision, state)
        return _envelope(decision, _cleared())


# "말고", "대신", "아니" mark a correction; "아니요" and "아니오" are answers, not corrections.
_CORRECTION_MARKER = re.compile(r"말고|대신|아니(?!요|오)")
_CHANGE_VERB = re.compile(r"옮겨|바꿔|변경|수정|취소|삭제|지워")
_REMOVE_VERB = re.compile(r"취소|삭제|지워")
# "날짜는 글피로 바꿔 줘" in an answer changes the create being asked about.
_SLOT_WORD = re.compile(r"(?:날짜|시간|시각|제목)\s*(?:은|는|을|를)")


def _is_change_request(turn: str, *, first: bool) -> bool:
    if first or _REMOVE_VERB.search(turn):
        return bool(_CHANGE_VERB.search(turn))
    return bool(_CHANGE_VERB.search(turn)) and not _SLOT_WORD.search(turn)


def _has_correction_evidence(operation: EvidenceOperation) -> bool:
    return any(
        span.status is not SpanStatus.ADOPTED
        for evidence in (operation.target, operation.change, operation.filter)
        for spans in evidence.values()
        for span in spans
    )


def _create_check(
    decision: dict[str, Any],
    operation: EvidenceOperation,
    turns: Sequence[str],
    *,
    several: bool,
) -> tuple[str, str] | None:
    """Return ``(slot, question)`` when a create needs a yes before it runs."""

    title = str(decision["title"])
    if several:
        return "first_operation", texts.confirm_first_create(title)
    if _has_correction_evidence(operation):
        return None
    latest = turns[-1] if turns else ""
    if _CORRECTION_MARKER.search(latest) and mentions_date_or_time(latest):
        # The user corrected a value but the model marked no correction (run 006 lk52).
        scheduled_at = datetime.fromisoformat(str(decision["scheduledAt"]))
        return "values", texts.confirm_create_values(title, scheduled_at)
    if any(_is_change_request(turn, first=index == 0) for index, turn in enumerate(turns)):
        # Change or delete wording read as a new schedule (run 004 lk28).
        return "new_create", texts.confirm_new_create(title)
    return None


def _create_signature(decision: dict[str, Any]) -> str:
    return json.dumps(
        [decision.get("title"), decision.get("scheduledAt"), decision.get("category")], ensure_ascii=False
    )


def _is_relative(decision: dict[str, Any]) -> bool:
    if decision.get("timeInterpretation") == "relative":
        return True
    return any(item.get("timeInterpretation") == "relative" for item in decision.get("candidates", []))
