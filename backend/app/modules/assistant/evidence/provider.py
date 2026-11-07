"""DecisionProvider that uses the evidence-only model output.

The model only returns evidence spans.  This provider keeps the conversation
turns, asks consent for unsupported requests, checks the output, and lets the
planner build the decision.  The model never sees or picks Schedule IDs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

from app.modules.assistant.decisions import (
    DecisionContext,
    DecisionEnvelope,
    DecisionProviderInvalidOutput,
)
from app.modules.assistant.evidence import texts
from app.modules.assistant.evidence.contract import (
    EvidenceContractError,
    EvidenceOperation,
    EvidenceOutput,
    SpanStatus,
    parse_evidence_output,
)
from app.modules.assistant.evidence.gate import (
    Answer,
    EvidenceGateError,
    analyze_text,
    check_roles,
    check_turn_prefix,
    classify_answer,
)
from app.modules.assistant.evidence.normalize import mentions_date_or_time, normalize_text
from app.modules.assistant.evidence.operation_cues import find_supported_operation
from app.modules.assistant.evidence.planner import Ask, Overrides, plan_operation
from app.modules.assistant.evidence.safety import (
    EvidenceQuestion,
    check_role_span_overlaps,
    clarification_answer_matches,
    evidence_question,
    pre_write_question,
)
from app.modules.assistant.schedule_read import ScheduleReadPort

logger = logging.getLogger(__name__)

STATE_KEY = "evidence"
# Shape of the provider's own JSON; an older shape starts the task fresh.
STATE_SHAPE = 4
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
    # Runtime-only marker. It is deliberately not serialized into control state.
    starts_new_task: bool = False
    consent_granted: bool = False
    # {"kind": "consent"} or {"kind": "confirm", "slot": ..., "value": ..., "text": ...}
    pending: dict[str, str] | None = None
    # Confirmed values keep the evidence they were asked about (see Overrides).
    change_date: str | None = None
    change_date_source: str | None = None
    midnight_value: str | None = None
    midnight_source: str | None = None
    # The create the user said yes to (title, time, category), so a changed create is checked again.
    create_confirmed: str | None = None
    # Stored only after contract, role and overlap validation. A pure yes answer
    # can resume at the planner without another model call.
    validated_output: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            STATE_KEY: STATE_SHAPE,
            "turns": list(self.turns),
            "consentGranted": self.consent_granted,
            "pending": self.pending,
            "changeDate": self.change_date,
            "changeDateSource": self.change_date_source,
            "midnightValue": self.midnight_value,
            "midnightSource": self.midnight_source,
            "createConfirmed": self.create_confirmed,
            "validatedOutput": self.validated_output,
        }

    @property
    def overrides(self) -> Overrides:
        return Overrides(
            change_date=date.fromisoformat(self.change_date) if self.change_date else None,
            change_date_source=self.change_date_source,
            midnight_value=(
                datetime.fromisoformat(self.midnight_value)
                if self.midnight_value
                else None
            ),
            midnight_source=self.midnight_source,
        )


def _load_state(value: object) -> _State | None:
    """Accept only the exact shape this provider writes; anything else starts fresh."""

    if not isinstance(value, dict) or value.get(STATE_KEY) != STATE_SHAPE:
        return None
    turns = value.get("turns")
    pending = value.get("pending")
    change_date = value.get("changeDate")
    optional_text = (
        "changeDate",
        "changeDateSource",
        "midnightValue",
        "midnightSource",
        "createConfirmed",
        "validatedOutput",
    )
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
        midnight_value = value.get("midnightValue")
        if midnight_value is not None:
            parsed_midnight = datetime.fromisoformat(midnight_value)
            if parsed_midnight.tzinfo is None:
                return None
    except ValueError:
        return None
    return _State(
        turns=list(turns),
        consent_granted=value["consentGranted"],
        pending=dict(pending) if pending is not None else None,
        change_date=change_date,
        change_date_source=value.get("changeDateSource"),
        midnight_value=value.get("midnightValue"),
        midnight_source=value.get("midnightSource"),
        create_confirmed=value.get("createConfirmed"),
        validated_output=value.get("validatedOutput"),
    )


def _envelope(decision: dict[str, Any], state: _State) -> DecisionEnvelope:
    return DecisionEnvelope.model_validate(
        {
            "decision": decision,
            "controlStateVersion": STATE_VERSION,
            "controlState": state.to_json(),
            "startsNewTask": state.starts_new_task,
        }
    )


def _cleared(state: _State) -> _State:
    # Overwrite stored turns once a task ends, so user text is not kept longer than needed.
    return _State(starts_new_task=state.starts_new_task)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_output(
    raw: str,
    *,
    turns: dict[int, str],
    reference: datetime,
) -> EvidenceOutput:
    output = parse_evidence_output(raw, user_turns=turns)
    check_roles(output, reference=reference)
    check_role_span_overlaps(output, user_turns=turns)
    check_turn_prefix(list(turns), len(turns))
    return output


class EvidenceDecisionProvider:
    def __init__(self, model: EvidenceModel, schedule_reader: ScheduleReadPort) -> None:
        self._model = model
        self._reader = schedule_reader

    # ------------------------------------------------------------ helpers

    def _ask(self, text: str, state: _State) -> DecisionEnvelope:
        return _envelope({"kind": "ask", "text": text}, state)

    def _ask_for_evidence(
        self, question: EvidenceQuestion, state: _State
    ) -> DecisionEnvelope:
        state.validated_output = None
        state.pending = {
            "kind": "clarify",
            "slot": question.value,
            "text": question.text,
        }
        return self._ask(question.text, state)

    def _starting_state(self, context: DecisionContext, text: str) -> _State:
        previous = None
        if context.is_question_follow_up and context.answered_question_kind == "need_more_info":
            previous = _load_state(context.control_state)
        if previous is None or not previous.turns or len(previous.turns) >= MAX_TURNS:
            return _State(turns=[text], starts_new_task=True)
        previous.turns.append(text)
        if previous.pending is None:
            # A processor-owned question supplied new evidence. Re-run model
            # extraction with the original turns and this answer.
            previous.validated_output = None
        return previous

    # ------------------------------------------------------------- decide

    def decide(self, context: DecisionContext) -> DecisionEnvelope:
        if context.input.get("type") != "text" or not isinstance(context.input.get("text"), str):
            raise ValueError("evidence provider accepts text input only")
        text = context.input["text"][:MAX_TURN_CHARS]
        state = self._starting_state(context, text)
        reference = (
            context.request_accepted_at
            if state.starts_new_task
            else context.task_reference_accepted_at
        )

        pending = state.pending
        if pending is not None:
            state.pending = None
            if pending.get("kind") == "clarify":
                try:
                    question = EvidenceQuestion(
                        pending.get("slot", EvidenceQuestion.VALUE)
                    )
                except (TypeError, ValueError):
                    return _envelope(
                        {"kind": "ask", "text": texts.ASK_RESTATE}, _cleared(state)
                    )
                if not clarification_answer_matches(question, text):
                    state.turns.pop()
                    state.pending = pending
                    return self._ask(pending.get("text", question.text), state)
                state.validated_output = None
            else:
                answer = classify_answer(text)
                if pending.get("kind") == "consent":
                    # A yes or no ("응, 알림 없이 등록해") is not part of the request.
                    # The model was never trained on it and misreads the request when it is there.
                    state.turns.pop()
                    if answer is Answer.NO:
                        return _envelope(
                            {"kind": "cancel", "text": texts.CONSENT_DECLINED},
                            _cleared(state),
                        )
                    if answer is Answer.UNKNOWN:
                        state.pending = pending
                        return self._ask(texts.CONSENT_QUESTION, state)
                    state.consent_granted = True
                elif pending.get("kind") == "confirm":
                    if answer is Answer.UNKNOWN:
                        if _is_confirmation_revision(text):
                            state.validated_output = None
                        else:
                            state.turns.pop()
                            state.pending = pending
                            return self._ask(
                                pending.get("text", texts.ASK_DATE_AGAIN), state
                            )
                    else:
                        state.turns.pop()
                        slot = pending.get("slot")
                        if answer is Answer.NO:
                            state.validated_output = None
                            if slot == "first_operation":
                                return _envelope(
                                    {"kind": "cancel", "text": texts.FIRST_DECLINED},
                                    _cleared(state),
                                )
                            if slot == "new_create":
                                # Probably a change to an existing schedule: start over.
                                return _envelope(
                                    {"kind": "ask", "text": texts.ASK_RESTATE},
                                    _cleared(state),
                                )
                            if slot == "values":
                                return self._ask(texts.ASK_DATETIME_AGAIN, state)
                            again = (
                                texts.ASK_TIME_AGAIN
                                if slot == "midnight"
                                else texts.ASK_DATE_AGAIN
                            )
                            return self._ask(again, state)
                        if slot == "change_date":
                            state.change_date = pending.get("value")
                            state.change_date_source = pending.get("source")
                        elif slot == "midnight":
                            state.midnight_value = pending.get("value")
                            state.midnight_source = pending.get("source")
                        elif slot in {"first_operation", "values", "new_create"}:
                            state.create_confirmed = pending.get("value")
                else:
                    return _envelope(
                        {"kind": "ask", "text": texts.ASK_RESTATE}, _cleared(state)
                    )

        if not state.consent_granted:
            analyses = [analyze_text(turn) for turn in state.turns]
            if any(item.has_unsupported_request for item in analyses):
                if any(item.supported_operation for item in analyses):
                    state.pending = {"kind": "consent"}
                    return self._ask(texts.CONSENT_QUESTION, state)
                return _envelope(
                    {"kind": "ask", "text": texts.GUIDANCE}, _cleared(state)
                )

        output = (
            self._restore_output(state, reference)
            if state.validated_output is not None
            else self._extract(state, context, reference)
        )
        try:
            return self._plan(output, state, context, reference)
        except EvidenceGateError as exc:
            raise DecisionProviderInvalidOutput(exc.code) from exc

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
            output = _validate_output(raw, turns=turns, reference=reference)
            state.validated_output = raw
            return output
        except (EvidenceContractError, EvidenceGateError) as exc:
            validation = str(getattr(exc, "code", None) or type(exc).__name__)
            raise DecisionProviderInvalidOutput(validation) from exc
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
                    "turnCount": len(state.turns),
                    "inputChars": sum(len(turn) for turn in state.turns),
                    "inputSha256": _digest(
                        json.dumps(
                            state.turns,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    ),
                    "outputBytes": len(raw.encode("utf-8")) if raw is not None else 0,
                    "outputSha256": _digest(raw) if raw is not None else None,
                    "validation": validation,
                },
            )

    def _restore_output(
        self, state: _State, reference: datetime
    ) -> EvidenceOutput:
        assert state.validated_output is not None
        turns = {index: turn for index, turn in enumerate(state.turns, 1)}
        try:
            return _validate_output(
                state.validated_output,
                turns=turns,
                reference=reference,
            )
        except (EvidenceContractError, EvidenceGateError) as exc:
            code = str(getattr(exc, "code", None) or type(exc).__name__)
            raise DecisionProviderInvalidOutput(code) from exc

    def _plan(
        self, output: EvidenceOutput, state: _State, context: DecisionContext, reference: datetime
    ) -> DecisionEnvelope:
        if not output.operations:
            if state.consent_granted:
                raise EvidenceGateError("SUPPORTED_OPERATION_MISSING")
            if output.unsupported:
                return _envelope(
                    {"kind": "ask", "text": texts.GUIDANCE}, _cleared(state)
                )
            if any(_has_clear_supported_request(turn) for turn in state.turns):
                raise EvidenceGateError("SUPPORTED_OPERATION_MISSING")
            return _envelope(
                {"kind": "ask", "text": texts.GUIDANCE}, _cleared(state)
            )
        operation = output.operations[0]
        several = len(output.operations) > 1
        model_saw_unsupported = bool(output.unsupported) or any(
            item.unsupported for item in output.operations
        )
        if model_saw_unsupported and not state.consent_granted:
            state.pending = {"kind": "consent"}
            return self._ask(texts.CONSENT_QUESTION, state)

        question = evidence_question(
            operation,
            turns=state.turns,
            reference=reference,
            coverage_operations=output.operations,
            coverage_spans=output.unsupported,
        )
        if question is not None:
            return self._ask_for_evidence(question, state)

        decision, ask = plan_operation(
            operation,
            reference=reference,
            server_time_trusted=context.server_time_trusted,
            overrides=state.overrides,
            reader=self._reader,
        )
        if ask is not None:
            question = _with_more_requests_note(ask.text, several=several)
            if ask.confirm is not None:
                state.pending = {"kind": "confirm", **ask.confirm, "text": question}
            else:
                state.validated_output = None
            return self._ask(question, state)
        if decision is None:
            raise ValueError("planner returned neither a decision nor a question")
        question = pre_write_question(
            decision,
            operation,
            turns=state.turns,
            reference=reference,
            server_time_trusted=context.server_time_trusted,
            coverage_operations=output.operations,
            coverage_spans=output.unsupported,
        )
        if question is not None:
            return self._ask_for_evidence(question, state)
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
        if several:
            if decision["kind"] == "no_match":
                decision["text"] = texts.NO_MATCH + texts.MORE_REQUESTS_NOTE
            elif decision["kind"] in {"confirm", "select_candidate", "list_intent", "cancel"}:
                decision["text"] = _with_more_requests_note(
                    decision["text"], several=True
                )
        if not context.server_time_trusted and _is_relative(decision):
            # The processor will ask for an absolute date; keep the turns for that answer.
            state.validated_output = None
            return _envelope(decision, state)
        if decision["kind"] in {"confirm", "select_candidate"}:
            # A public selection or approval can later encounter a time-trust
            # question. Keep the original turns, but not the model payload.
            state.validated_output = None
            return _envelope(decision, state)
        return _envelope(decision, _cleared(state))


# "말고", "대신", "아니" mark a correction; "아니요" and "아니오" are answers, not corrections.
_CORRECTION_MARKER = re.compile(r"말고|대신|아니(?!요|오)")
_CHANGE_VERB = re.compile(r"옮겨|바꿔|변경|수정|미뤄|당겨|취소|삭제|지워")
_REMOVE_VERB = re.compile(r"취소|삭제|지워")
# A question about schedules must not become a create just because the model
# returned create evidence.  Capability-style create requests are exempt.
_QUERY_REQUEST = re.compile(
    r"(?:보여\s*줘|알려\s*줘|조회(?:해\s*줘)?|확인(?:해)?\s*줘|"
    r"뭐\s*있(?:어|나요|습니까|니|나)|일정\s*있(?:어|나요|습니까|니|나)|"
    r"있는지\s*(?:확인(?:해)?\s*줘)?|있(?:어|나요|습니까|니|나))\s*[?？]?\s*$"
)
_CREATE_CAPABILITY = re.compile(
    r"(?:등록(?:해)?|넣어|잡아|추가해|만들어|생성해)\s*"
    r"(?:줘|주세요|줄\s*(?:수\s*있(?:어|나요|습니까)|래(?:요)?)|"
    r"주(?:시겠(?:어요|습니까)|실래(?:요)?))\s*[?？]?\s*$"
)
_CREATE_ACTION = re.compile(r"등록|넣어|잡아|추가|만들어|생성")
_QUESTION_ENDING = re.compile(
    r"(?:[?？]|(?:나|니|냐|까|던가|나요|까요|죠)\s*[.。]?)\s*$"
)
_CREATE_PASSIVE = re.compile(
    r"(?:등록|추가|생성)\s*(?:돼|되어|되었|되는|된|됐|됨)|"
    r"(?:넣어|만들어)\s*(?:져|진|졌)|잡힌"
)
_CREATE_NEGATED = re.compile(
    r"(?:등록|추가|생성)\s*(?:하지|안\s*해|지\s*(?:마|말|않))|"
    r"(?:넣|잡|만들)\s*지\s*(?:마|말|않)|"
    r"안\s*(?:등록|추가|생성|넣어|잡아|만들어)"
)
# A past status or a question about whether creation is necessary is not an
# instruction to create. Keep this narrower than a generic question suffix so
# direct commands such as "넣어" remain valid create actions.
_CREATE_STATUS_OR_OBLIGATION = re.compile(
    r"(?:등록|추가|생성)\s*(?:했(?:었)?|해\s*(?:뒀|두었|둔|놨|놓았|놓은)|해야\s*하)|"
    r"(?:넣어|잡아|만들어)\s*(?:뒀|두었|둔|놨|놓았|놓은)"
)
_CREATE_STATUS_PREFIX = re.compile(
    r"^\s*(?:(?:이|은|도|가|는)\s*|완료\s*)*"
)
_CREATE_ATTRIBUTIVE_STATUS = re.compile(
    r"^(?:한|된|하는|하신)(?=\s*(?:거|것|건|회의|일정|상태|내용|항목)|\s|$)"
)
_CREATE_PASSIVE_OR_OBLIGATION_STATUS = re.compile(
    r"^(?:(?:돼|되어|되었|되는|된|됐|됨)|해야\s*(?:하|돼|되|$))"
)
_COMPLETION_REQUEST = re.compile(r"완료(?:로|\s*(?:처리|표시))")
_SCHEDULE_LIST_REQUEST = re.compile(
    r"(?:(?:일정|스케줄|할\s*일|회의|점검|검수|업무|작업).*"
    r"(?:보여|알려|있(?:어|나요|습니까))|뭐\s*있(?:어|나요|습니까))"
)
# "날짜는 글피로 바꿔 줘" in an answer changes the create being asked about.
_SLOT_WORD = re.compile(r"(?:날짜|시간|시각|제목)\s*(?:은|는|을|를)")
_REVISION_WORD = re.compile(r"날짜|시간|시각|제목|분류|완료")


def _is_confirmation_revision(text: str) -> bool:
    value = normalize_text(text)
    return bool(
        mentions_date_or_time(value)
        or _REVISION_WORD.search(value)
        or (
            _CORRECTION_MARKER.search(value)
            and re.search(r"\S+(?:으)?로", value)
        )
    )


def _is_change_request(turn: str, *, first: bool) -> bool:
    if first or _REMOVE_VERB.search(turn):
        return bool(_CHANGE_VERB.search(turn))
    return bool(_CHANGE_VERB.search(turn)) and not _SLOT_WORD.search(turn)


def _has_clear_supported_request(turn: str) -> bool:
    value = normalize_text(turn)
    return bool(
        find_supported_operation(value)
        or _SCHEDULE_LIST_REQUEST.search(value)
    )


def _has_replaced_evidence(operation: EvidenceOperation) -> bool:
    return any(
        span.status is SpanStatus.SUPERSEDED
        for evidence in (operation.target, operation.change, operation.filter)
        for spans in evidence.values()
        for span in spans
    )


def _mask_text(value: str, text: str, occurrence: int | None = None) -> str:
    needle = normalize_text(text)
    matches = list(re.finditer(re.escape(needle), value))
    if occurrence is not None:
        matches = matches[occurrence - 1:occurrence]
    for match in reversed(matches):
        value = value[:match.start()] + " " * (match.end() - match.start()) + value[match.end():]
    return value


def _has_query_request(operation: EvidenceOperation, turns: Sequence[str]) -> bool:
    """Find a query cue only in text the model did not claim as evidence.

    Unsupported-feature phrases are removed too.  This keeps requests such as
    "register it and tell me an hour before" from being mistaken for a list
    request after the unsupported part has already been handled by consent.
    """

    for value in _residual_turns(operation, turns):
        if _CREATE_CAPABILITY.search(value):
            continue
        if (
            _QUERY_REQUEST.search(value)
            or _has_create_status_or_obligation(value)
            or (
                _CREATE_ACTION.search(value)
                and _QUESTION_ENDING.search(value)
            )
        ):
            return True
    return False


def _residual_turns(
    operation: EvidenceOperation, turns: Sequence[str]
) -> tuple[str, ...]:
    """Return normalized request text after removing every claimed cue."""

    spans = [
        span
        for evidence in (operation.target, operation.change, operation.filter)
        for slot_spans in evidence.values()
        for span in slot_spans
    ] + list(operation.unsupported)
    residuals: list[str] = []
    for turn_number, turn in enumerate(turns, 1):
        residual = normalize_text(turn)
        for span in spans:
            if span.turn == turn_number:
                residual = _mask_text(residual, span.text, span.occurrence)
        for finding in analyze_text(turn).findings:
            residual = _mask_text(residual, finding.text)
        residuals.append(residual)
    return tuple(residuals)


def _has_ss_batchim(value: str) -> bool:
    """Return whether a Hangul syllable carries the past-tense ㅆ batchim."""

    return any(
        0xAC00 <= ord(character) <= 0xD7A3
        and (ord(character) - 0xAC00) % 28 == 20
        for character in value
    )


def _has_past_ss_batchim(value: str) -> bool:
    """Find a past-tense ㅆ after an action, ignoring non-past auxiliaries."""

    return any(
        character not in {"겠", "있", "없"}
        and 0xAC00 <= ord(character) <= 0xD7A3
        and (ord(character) - 0xAC00) % 28 == 20
        for character in value
    )


def _has_create_status_or_obligation(value: str) -> bool:
    """Distinguish a creation status statement from an execution request."""

    if _CREATE_STATUS_OR_OBLIGATION.search(value):
        return True
    for action in _CREATE_ACTION.finditer(value):
        tail = value[action.end() :]
        tail = _CREATE_STATUS_PREFIX.sub("", tail, count=1)
        first_word = tail.split(maxsplit=1)[0] if tail else ""
        if first_word.startswith(("하", "되", "돼", "됐")) and _has_ss_batchim(
            first_word
        ):
            return True
        if _has_past_ss_batchim(tail):
            return True
        if (
            _CREATE_ATTRIBUTIVE_STATUS.search(tail)
            or _CREATE_PASSIVE_OR_OBLIGATION_STATUS.search(tail)
        ):
            return True
    return False


def _has_explicit_create_action(
    operation: EvidenceOperation, turns: Sequence[str]
) -> bool:
    for residual in _residual_turns(operation, turns):
        if _CREATE_CAPABILITY.search(residual):
            return True
        if _CREATE_ACTION.search(residual) and _QUESTION_ENDING.search(residual):
            continue
        if (
            _CREATE_ACTION.search(residual)
            and not _CREATE_PASSIVE.search(residual)
            and not _CREATE_NEGATED.search(residual)
            and not _has_create_status_or_obligation(residual)
        ):
            return True
    return False


def _with_more_requests_note(text: str, *, several: bool) -> str:
    if not several or text.endswith(texts.MORE_REQUESTS_NOTE):
        return text
    return text + texts.MORE_REQUESTS_NOTE


def _create_check(
    decision: dict[str, Any],
    operation: EvidenceOperation,
    turns: Sequence[str],
    *,
    several: bool,
) -> tuple[str, str] | None:
    """Return ``(slot, question)`` when a create needs a yes before it runs."""

    title = str(decision["title"])
    scheduled_at = datetime.fromisoformat(str(decision["scheduledAt"]))
    if several:
        return "first_operation", texts.confirm_first_create(title, scheduled_at)
    latest = turns[-1] if turns else ""
    if any(_is_change_request(turn, first=index == 0) for index, turn in enumerate(turns)):
        # Change or delete wording read as a new schedule (run 004 lk28).
        return "new_create", texts.confirm_new_create(title, scheduled_at)
    if any(_COMPLETION_REQUEST.search(normalize_text(turn)) for turn in turns):
        return "new_create", texts.confirm_new_create(title, scheduled_at)
    if _has_query_request(operation, turns):
        # A list question read as a new schedule must never write immediately.
        return "new_create", texts.confirm_new_create(title, scheduled_at)
    if not _has_explicit_create_action(operation, turns):
        return "new_create", texts.confirm_new_create(title, scheduled_at)
    if _has_replaced_evidence(operation):
        return None
    if _CORRECTION_MARKER.search(latest) and mentions_date_or_time(latest):
        # The user corrected a value but the model marked no replaced value (run 006 lk52).
        return "values", texts.confirm_create_values(title, scheduled_at)
    return None


def _create_signature(decision: dict[str, Any]) -> str:
    return json.dumps(
        [decision.get("title"), decision.get("scheduledAt"), decision.get("category")], ensure_ascii=False
    )


def _is_relative(decision: dict[str, Any]) -> bool:
    if decision.get("timeInterpretation") == "relative":
        return True
    return any(item.get("timeInterpretation") == "relative" for item in decision.get("candidates", []))
