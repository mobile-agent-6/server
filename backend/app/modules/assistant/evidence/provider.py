"""DecisionProvider that uses the evidence-only model output.

The model only returns evidence spans.  This provider keeps the conversation
turns, asks consent for unsupported requests, checks the output, and lets the
planner build the decision.  The model never sees or picks Schedule IDs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from app.modules.assistant.decisions import DecisionContext, DecisionEnvelope
from app.modules.assistant.evidence import texts
from app.modules.assistant.evidence.contract import EvidenceOutput, parse_evidence_output
from app.modules.assistant.evidence.gate import (
    Answer,
    analyze_text,
    check_roles,
    check_turn_prefix,
    classify_answer,
)
from app.modules.assistant.evidence.planner import Ask, Overrides, plan_operation
from app.modules.assistant.schedule_read import ScheduleReadPort

STATE_KEY = "evidence"
STATE_VERSION = 1
MAX_TURNS = 8
MAX_TURN_CHARS = 500


class EvidenceModel(Protocol):
    """Returns the raw evidence JSON text for the user turns so far."""

    def extract(self, *, turns: Sequence[str], reference_time: datetime, server_time_trusted: bool) -> str: ...


@dataclass
class _State:
    turns: list[str] = field(default_factory=list)
    consent_granted: bool = False
    # {"kind": "consent"} or {"kind": "confirm", "slot": ..., "value": ..., "text": ...}
    pending: dict[str, str] | None = None
    change_date: str | None = None
    midnight: bool = False
    # Several requests at once: the first create runs only after a yes.
    first_confirmed: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            STATE_KEY: 1,
            "turns": list(self.turns),
            "consentGranted": self.consent_granted,
            "pending": self.pending,
            "changeDate": self.change_date,
            "midnight": self.midnight,
            "firstConfirmed": self.first_confirmed,
        }

    @property
    def overrides(self) -> Overrides:
        return Overrides(
            change_date=date.fromisoformat(self.change_date) if self.change_date else None,
            midnight=self.midnight,
        )


def _load_state(value: object) -> _State | None:
    """Accept only the exact shape this provider writes; anything else starts fresh."""

    if not isinstance(value, dict) or value.get(STATE_KEY) != 1:
        return None
    turns = value.get("turns")
    pending = value.get("pending")
    change_date = value.get("changeDate")
    if (
        not isinstance(turns, list)
        or not all(isinstance(turn, str) for turn in turns)
        or len(turns) > MAX_TURNS
        or type(value.get("consentGranted")) is not bool
        or type(value.get("midnight")) is not bool
        or type(value.get("firstConfirmed")) is not bool
        or not (pending is None or (isinstance(pending, dict) and all(isinstance(v, str) for v in pending.values())))
        or not (change_date is None or isinstance(change_date, str))
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
        midnight=value["midnight"],
        first_confirmed=value["firstConfirmed"],
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
                    again = texts.ASK_TIME_AGAIN if slot == "midnight" else texts.ASK_DATE_AGAIN
                    return self._ask(again, state)
                if slot == "change_date":
                    state.change_date = pending.get("value")
                elif slot == "midnight":
                    state.midnight = True
                elif slot == "first_operation":
                    state.first_confirmed = True

        if not state.consent_granted:
            analyses = [analyze_text(turn) for turn in state.turns]
            if any(item.has_unsupported_request for item in analyses):
                if any(item.supported_operation for item in analyses):
                    state.pending = {"kind": "consent"}
                    return self._ask(texts.CONSENT_QUESTION, state)
                return _envelope({"kind": "ask", "text": texts.GUIDANCE}, _cleared())

        turns = {index: turn for index, turn in enumerate(state.turns, 1)}
        raw = self._model.extract(
            turns=list(state.turns), reference_time=reference, server_time_trusted=context.server_time_trusted
        )
        output = parse_evidence_output(raw, user_turns=turns)
        check_roles(output, reference=reference)
        check_turn_prefix(list(turns), len(turns))
        return self._plan(output, state, context, reference)

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
        if several and decision["kind"] == "create_intent" and not state.first_confirmed:
            # A create runs at once, so with several requests ask before the first one.
            question = texts.confirm_first_create(decision["title"])
            state.pending = {"kind": "confirm", "slot": "first_operation", "value": "yes", "text": question}
            return self._ask(question, state)
        if several and decision["kind"] in {"confirm", "select_candidate"}:
            decision["text"] = decision["text"] + texts.MORE_REQUESTS_NOTE
        if not context.server_time_trusted and _is_relative(decision):
            # The processor will ask for an absolute date; keep the turns for that answer.
            return _envelope(decision, state)
        return _envelope(decision, _cleared())


def _is_relative(decision: dict[str, Any]) -> bool:
    if decision.get("timeInterpretation") == "relative":
        return True
    return any(item.get("timeInterpretation") == "relative" for item in decision.get("candidates", []))
