"""Issue #30 common backend safety gate v0.3 (research only).

v0.3 adds a candidate-neutral, backend-owned *trusted policy context* for
unsupported-feature requests and user consent (D5, BE-PROPOSED), and enforces it
before the unchanged v0.2 gate.

Trust boundary
--------------
* Inputs are only backend-owned conversation state: the user text of each turn,
  the question a turn answers (``answersQuestionOfTurn``, the public API
  ``questionId`` link) and ``startsNewTask``.  Candidate output, the model's own
  unsupported/consent judgement, the semantic oracle, case IDs and expected
  answers are never read.
* The unsupported-feature detector is a bounded deterministic grammar for the
  features the product currently excludes: reminders/notifications and text
  messages (알림, 문자, 리마인더/리마인드, 푸시, "N분 전에 알려" and "미리 알려").
  It does not claim to understand other unsupported features (for example
  sensor data or recommendations).  A cue word that matches none of the known
  request, exclusion or title-noun forms is an *unknown expression* and fails
  closed.
* Consent is only accepted from the user turn that answers the backend's own
  consent question in the same task.  An unrelated or earlier "네" is never
  reused, and a new request needs new consent.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

import common_gate_v0_2 as gate_v2
import contracts

GATE_VERSION = "issue30-common-gate-v0.3"
POLICY_ANALYZER_VERSION = "issue30-trusted-policy-analyzer-v0.3"
CONSENT_STATES = ("pending", "granted", "declined", "not_required")

# ---------------------------------------------------------------- grammar

_CUE = r"(?:알림|문자|리마인더|리마인드|푸시)"
_PARTICLE = r"(?:은|는|이|가|을|를|도|만|로|으로)?"
_ADVERB = r"(?:\s*(?:좀|따로|꼭|같이|함께|미리|바로))*"
_REQUEST_VERB = (
    r"(?:보내|발송\s*해|발송\s*할|전송\s*해|전송\s*할|켜|맞춰|설정\s*해|설정\s*할|설정해|달아|울려|"
    r"추가\s*해|넣어|띄워|날려)"
)
_NEGATED_AFTER_VERB = r"(?!\s*지\s*(?:마|말))"
_REQUEST = re.compile(_CUE + _PARTICLE + _ADVERB + r"\s*" + _REQUEST_VERB + _NEGATED_AFTER_VERB)
_OFFSET_REMINDER = re.compile(
    r"\d+\s*(?:분|시간)\s*전에?\s*(?:미리\s*)?(?:알려|알림|문자|연락|깨워|울려)"
)
_EARLY_TELL = re.compile(r"미리\s*알려")
_EXCLUSION = re.compile(
    _CUE + _PARTICLE + r"\s*(?:빼고|빼\s*줘|빼|말고|없이|필요\s*없|생략|"
    r"(?:보내|켜|설정하|달|추가하|하)지\s*(?:말|마)|안\s*(?:보내|켜|해도|해|달아))"
)
_CUE_WORD = re.compile(_CUE)
_SUPPORTED_OP = re.compile(
    r"(?:등록|넣어|넣고|넣자|잡아|잡고|추가해|만들어|옮겨|바꿔|변경해|수정해|지워|지우고|삭제|"
    r"보여\s*줘|보여줘|알려\s*줘|알려줘|조회|완료로|완료\s*처리|일정\s*있어|뭐\s*있어)"
)
_REQUEST_STEM = re.compile(r"(?:보내|발송\s*해|전송\s*해|켜\s*줘|켜$|맞춰|설정\s*해|설정해|달아|울려|띄워|날려)")
_TITLE_FOLLOWER = re.compile(r"^[가-힣A-Za-z0-9]")
_PARTICLE_ONLY = re.compile(r"^(?:은|는|이|가|을|를|도|만|로|으로)(?![가-힣])")

_AFFIRM = re.compile(r"^(?:응|어|네|예|그래|그래요|좋아|좋아요|오케이|ok|알겠어|알겠습니다|그렇게\s*해)(?![가-힣])", re.IGNORECASE)
_DECLINE = re.compile(r"(?:^|[\s,.!?])(?:아니|아뇨|아니요|아니오|싫어|됐어|됐어요|취소|그만)(?![가-힣])")
_DECLINE_VERB = re.compile(r"(?:등록|넣|진행|추가|하|잡)지\s*(?:마|말)|안\s*할래|하지\s*않|그만\s*둬|취소해")
_PROCEED = re.compile(r"(?:등록해|등록\s*해|넣어|진행해|잡아|그렇게\s*해|해\s*줘)")
_DATE_OR_TIME = re.compile(r"\d+\s*(?:시|분|월|일|년)|오늘|내일|모레|글피|요일")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


@dataclass(frozen=True)
class CueFinding:
    turn: int
    kind: str  # request | exclusion | title_noun | unknown
    feature: str  # reminder | text_message
    text: str


def _feature(word: str) -> str:
    return "text_message" if word == "문자" else "reminder"


_VERB_TOKEN = re.compile(r"(?:줘|주세요|줄래|부탁|해|해서|해요|하고|하는|할|하자|좀|꼭)$")


def _has_verb_token(segment: str) -> bool:
    """A title noun phrase between the cue word and the schedule verb has no verb-like token."""

    tokens = [re.sub(r"(?:을|를)$", "", token) for token in segment.split()]
    return any(_VERB_TOKEN.search(token) or token in {"좀", "꼭", "부탁"} for token in tokens if token)


def analyze_text(turn: int, text: str) -> dict[str, Any]:
    """Classify cue words and supported-operation verbs in one user text turn."""

    value = _norm(text)
    findings: list[CueFinding] = []
    covered: list[tuple[int, int]] = []
    for match in _EXCLUSION.finditer(value):
        word = _CUE_WORD.match(match.group(0)).group(0)
        findings.append(CueFinding(turn, "exclusion", _feature(word), match.group(0)))
        covered.append(match.span())
    for match in _REQUEST.finditer(value):
        if any(start <= match.start() < end for start, end in covered):
            continue
        word = _CUE_WORD.match(match.group(0)).group(0)
        findings.append(CueFinding(turn, "request", _feature(word), match.group(0)))
        covered.append(match.span())
    for pattern in (_OFFSET_REMINDER, _EARLY_TELL):
        for match in pattern.finditer(value):
            if any(start <= match.start() < end for start, end in covered):
                continue
            feature = "text_message" if "문자" in match.group(0) else "reminder"
            findings.append(CueFinding(turn, "request", feature, match.group(0)))
            covered.append(match.span())
    for match in _CUE_WORD.finditer(value):
        if any(start <= match.start() < end for start, end in covered):
            continue
        rest = value[match.end():]
        verb = _SUPPORTED_OP.search(rest)
        between = rest[: verb.start()] if verb else rest
        compound = bool(_TITLE_FOLLOWER.match(rest)) and not _PARTICLE_ONLY.match(rest)
        noun_phrase = rest.startswith(" ") and bool(re.match(r"^ [가-힣A-Za-z0-9]", rest)) and not _PARTICLE_ONLY.match(rest.lstrip())
        if verb and (compound or noun_phrase) and not _REQUEST_STEM.search(between) and not _has_verb_token(between):
            findings.append(CueFinding(turn, "title_noun", _feature(match.group(0)), value[match.start(): match.end() + verb.start()].strip()))
        else:
            findings.append(CueFinding(turn, "unknown", _feature(match.group(0)), value[match.start(): min(len(value), match.end() + 12)]))
    residual = value
    for start, end in sorted(covered, reverse=True):
        residual = residual[:start] + " " * (end - start) + residual[end:]
    supported = bool(_SUPPORTED_OP.search(residual))
    return {"findings": findings, "supportedOperation": supported}


def classify_consent_answer(text: str) -> str:
    """granted | declined | unknown for a reply to the backend consent question."""

    value = _norm(text)
    analysis = analyze_text(0, value)
    kinds = {finding.kind for finding in analysis["findings"]}
    if "request" in kinds or "unknown" in kinds:
        return "unknown"
    if _DATE_OR_TIME.search(value):
        return "unknown"
    affirm = bool(_AFFIRM.match(value))
    decline = bool(_DECLINE.search(value)) or bool(_DECLINE_VERB.search(value))
    exclusion = "exclusion" in kinds
    proceed = bool(_PROCEED.search(value))
    if exclusion and proceed and not _DECLINE_VERB.search(value):
        return "granted"
    if affirm and not decline:
        return "granted"
    if decline and not affirm:
        return "declined"
    if proceed and not decline and not affirm:
        return "granted"
    return "unknown"


@dataclass(frozen=True)
class TrustedPolicyContext:
    analyzerVersion: str
    throughTurn: int
    scopeStartTurn: int | None
    requestTurn: int | None
    unsupportedCues: tuple[CueFinding, ...] = field(default_factory=tuple)
    exclusionCues: tuple[CueFinding, ...] = field(default_factory=tuple)
    unknownCueExpressions: tuple[CueFinding, ...] = field(default_factory=tuple)
    supportedOperationPresent: bool = False
    unsupportedOnly: bool = False
    consentRequired: bool = False
    consentState: str = "not_required"
    consentQuestionTurn: int | None = None
    consentDecisionTurn: int | None = None
    source: str = "backend_conversation_state"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["fingerprint"] = fingerprint(value)
        return value


def fingerprint(value: Mapping[str, Any]) -> str:
    material = {key: item for key, item in value.items() if key != "fingerprint"}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _text_turns(turns: Sequence[Mapping[str, Any]], through_turn: int) -> list[Mapping[str, Any]]:
    allowed = {"turn", "inputType", "inputSource", "text", "answersQuestionOfTurn", "startsNewTask", "acceptedAt", "sourceNote", "selectedScheduleRef", "approved"}
    result = []
    for turn in turns:
        if not isinstance(turn, Mapping) or set(turn) - allowed:
            raise ValueError("untrusted turn shape")
        if turn["turn"] > through_turn or turn["inputType"] != "text":
            continue
        result.append(turn)
    return result


def analyze_policy(turns: Sequence[Mapping[str, Any]], through_turn: int) -> TrustedPolicyContext:
    """Derive the backend-owned unsupported/consent state through ``through_turn``."""

    scope: dict[str, Any] | None = None

    def fresh(turn_number: int) -> dict[str, Any]:
        return {"start": turn_number, "request": None, "cues": [], "exclusions": [], "unknown": [], "supported": False,
                "state": "not_required", "question": None, "decision": None, "unsupportedOnly": False}

    for turn in _text_turns(turns, through_turn):
        number = int(turn["turn"])
        text = str(turn["text"])
        answers = turn.get("answersQuestionOfTurn")
        if scope is None or turn.get("startsNewTask") is True:
            scope = fresh(number)
        elif answers is None and scope["state"] != "pending":
            # A new request that does not answer a backend question starts a new scope;
            # earlier consent or refusal is never reused for it.
            scope = fresh(number)
        if scope["state"] == "pending" and answers is not None and answers == scope["question"]:
            decision = classify_consent_answer(text)
            if decision == "granted":
                scope.update(state="granted", decision=number)
            elif decision == "declined":
                scope.update(state="declined", decision=number)
            else:
                scope.update(question=number)  # the backend asks again in this turn
            continue
        analysis = analyze_text(number, text)
        requests = [item for item in analysis["findings"] if item.kind == "request"]
        unknown = [item for item in analysis["findings"] if item.kind == "unknown"]
        exclusions = [item for item in analysis["findings"] if item.kind == "exclusion"]
        scope["cues"].extend(requests)
        scope["unknown"].extend(unknown)
        scope["exclusions"].extend(exclusions)
        scope["supported"] = scope["supported"] or analysis["supportedOperation"]
        if requests or unknown:
            scope["request"] = number
            if scope["supported"]:
                scope.update(state="pending", question=number, decision=None, unsupportedOnly=False)
            else:
                scope.update(state="not_required", unsupportedOnly=True)
        elif analysis["supportedOperation"] and scope["state"] == "not_required":
            scope["request"] = number if scope["request"] is None else scope["request"]
            scope["unsupportedOnly"] = False
        if scope["state"] == "pending":
            # Any other turn while consent is pending is answered by the backend's
            # consent question again; only a reply to that newest question counts.
            scope["question"] = number
    if scope is None:
        return TrustedPolicyContext(POLICY_ANALYZER_VERSION, through_turn, None, None)
    return TrustedPolicyContext(
        analyzerVersion=POLICY_ANALYZER_VERSION,
        throughTurn=through_turn,
        scopeStartTurn=scope["start"],
        requestTurn=scope["request"],
        unsupportedCues=tuple(scope["cues"]),
        exclusionCues=tuple(scope["exclusions"]),
        unknownCueExpressions=tuple(scope["unknown"]),
        supportedOperationPresent=scope["supported"],
        unsupportedOnly=scope["unsupportedOnly"],
        consentRequired=scope["state"] in {"pending", "granted", "declined"},
        consentState=scope["state"],
        consentQuestionTurn=scope["question"],
        consentDecisionTurn=scope["decision"],
    )


# ---------------------------------------------------------------- gate

_POLICY_CLAIM_KEY = re.compile(r"consent|unsupported|policy|reminder|notification|알림|동의", re.IGNORECASE)
_POLICY_KEYS = set(TrustedPolicyContext.__dataclass_fields__) | {"fingerprint"}


def _reject(cause: str, code: str) -> contracts.GateResult:
    return contracts.GateResult(False, cause, code)


def _canon(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=lambda item: asdict(item)))


def _coerce_policy(policy: Any) -> dict[str, Any] | None:
    if isinstance(policy, TrustedPolicyContext):
        return _canon(policy.to_dict())
    if isinstance(policy, Mapping):
        try:
            return _canon(policy)
        except (TypeError, ValueError):
            return None
    return None


def validate_policy_context(
    policy: Any,
    *,
    turns: Sequence[Mapping[str, Any]] | None,
    through_turn: int | None,
) -> contracts.GateResult:
    """Fail closed on a missing, malformed or tampered policy context."""

    if policy is None:
        return _reject("MAPPING", "POLICY_CONTEXT_MISSING")
    value = _coerce_policy(policy)
    if value is None or set(value) != _POLICY_KEYS:
        return _reject("MAPPING", "POLICY_CONTEXT_INVALID")
    if value.get("consentState") not in CONSENT_STATES or value.get("analyzerVersion") != POLICY_ANALYZER_VERSION:
        return _reject("MAPPING", "POLICY_CONTEXT_INVALID")
    if value.get("source") != "backend_conversation_state" or value.get("fingerprint") != fingerprint(value):
        return _reject("MAPPING", "POLICY_CONTEXT_TAMPERED")
    if turns is None or through_turn is None:
        return _reject("MAPPING", "POLICY_INPUTS_MISSING")
    try:
        recomputed = analyze_policy(turns, through_turn).to_dict()
    except (KeyError, TypeError, ValueError):
        return _reject("MAPPING", "POLICY_INPUTS_INVALID")
    if _canon(recomputed) != value:
        return _reject("MAPPING", "POLICY_CONTEXT_TAMPERED")
    return contracts.GateResult(True, None, None)


def policy_gate(
    proposal: Mapping[str, Any] | None,
    policy: Any,
    *,
    turns: Sequence[Mapping[str, Any]] | None,
    through_turn: int | None,
    executable: bool,
) -> contracts.GateResult:
    """D5 enforcement. ASK or other non-executable results are never blocked."""

    if proposal is not None:
        for key in proposal:
            if _POLICY_CLAIM_KEY.search(str(key)):
                return _reject("MAPPING", "CANDIDATE_POLICY_CLAIM")
    if not executable:
        return contracts.GateResult(True, None, None)
    checked = validate_policy_context(policy, turns=turns, through_turn=through_turn)
    if not checked.accepted:
        return checked
    value = _coerce_policy(policy)
    if value["unknownCueExpressions"]:
        return _reject("MAPPING", "UNSUPPORTED_EXPRESSION_UNKNOWN")
    if value["unsupportedOnly"]:
        return _reject("MAPPING", "UNSUPPORTED_ONLY_NO_SCHEDULE_EFFECT")
    if value["consentState"] == "pending":
        return _reject("MAPPING", "CONSENT_REQUIRED_BEFORE_EFFECT")
    if value["consentState"] == "declined":
        return _reject("MAPPING", "CONSENT_DECLINED")
    return contracts.GateResult(True, None, None)


def common_backend_gate_v0_3(
    proposal: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    policy: Any,
    policy_turns: Sequence[Mapping[str, Any]] | None,
    through_turn: int | None,
    executable: bool,
    candidate_c_output: Mapping[str, Any] | None = None,
    user_turns: Mapping[int, str] | None = None,
    reference_time: str | None = None,
    server_time_trusted: bool | None = None,
) -> contracts.GateResult:
    """Trusted-policy (D5) checks first, then the unchanged v0.2 gate."""

    result = policy_gate(proposal, policy, turns=policy_turns, through_turn=through_turn, executable=executable)
    if not result.accepted:
        return result
    return gate_v2.common_backend_gate_v0_2(
        proposal,
        context,
        candidate_c_output=candidate_c_output,
        user_turns=user_turns,
        reference_time=reference_time,
        server_time_trusted=server_time_trusted,
    )
