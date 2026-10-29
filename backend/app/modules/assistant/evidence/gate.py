"""Safety checks on parsed evidence before any value is used.

* Role check: each operation kind may only use certain evidence slots, and each
  slot's words must really mean that slot (a title is not a date, and so on).
* Turn check: the user turns behind a decision must be the complete ordered
  prefix ``1..n``.
* Unsupported request detection: reminder and text-message requests are not a
  supported feature, so they must be found from the user's own text, not from
  the model.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.modules.assistant.evidence.contract import (
    EvidenceOperation,
    EvidenceOutput,
    OperationKind,
    Span,
    SpanStatus,
    adopted_texts,
)
from app.modules.assistant.evidence.normalize import (
    DatePurpose,
    DateStatus,
    TimeStatus,
    is_future_scope,
    normalize_text,
    resolve_category,
    resolve_completed,
    resolve_date,
    resolve_month_range,
    resolve_time,
)

_ROLE_SLOTS: dict[OperationKind, dict[str, frozenset[str]]] = {
    OperationKind.CREATE: {
        "target": frozenset(),
        "change": frozenset({"title", "date", "time", "category"}),
        "filter": frozenset(),
    },
    OperationKind.LIST: {
        "target": frozenset(),
        "change": frozenset(),
        "filter": frozenset({"title", "date", "category", "completed", "futureScope"}),
    },
    OperationKind.UPDATE: {
        "target": frozenset({"title", "date", "completedState"}),
        "change": frozenset({"title", "date", "time", "category", "completed"}),
        "filter": frozenset(),
    },
    OperationKind.DELETE: {
        "target": frozenset({"title", "date", "completedState"}),
        "change": frozenset(),
        "filter": frozenset(),
    },
}

# Words that only name the request, never the schedule itself.
_OPERATION_ONLY = re.compile(
    r"^(?:(?:일정|목록)\s*)?(?:등록(?:하고|해\s*줘)?|넣어\s*줘|잡아\s*줘|"
    r"보여\s*줘|알려\s*줘|뭐\s*있어|있어|바꿔\s*줘|옮겨\s*줘|"
    r"삭제해\s*줘|지워\s*줘|지우고|다\s*보여\s*줘|전부\s*보여\s*줘)$"
)
# A date or time token alone cannot be a title.
_DATE_OR_TIME_ONLY = re.compile(
    r"^(?:\d{4}년\s*)?\d{1,2}월(?:\s*\d{1,2}일)?$|^(?:오늘|내일|모레|글피)$|"
    r"^(?:이번\s*주\s*)?[월화수목금토일]요일$|"
    r"^(?:(?:오전|오후|아침|저녁|새벽|낮|밤)\s*)?\d{1,2}\s*시(?:\s*\d{1,2}\s*분|\s*반)?$"
)

# Statuses that are real expressions the user can clarify.  Only NOT_A_* means
# the model put words in the wrong slot.
_DATE_EXPRESSIONS = frozenset(set(DateStatus) - {DateStatus.NOT_A_DATE})
_TIME_EXPRESSIONS = frozenset(set(TimeStatus) - {TimeStatus.NOT_A_TIME})


class EvidenceGateError(ValueError):
    """Parsed evidence is unsafe or inconsistent and must not be used."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code + (f": {detail}" if detail else ""))


def _is_title(texts: Sequence[str]) -> bool:
    for text in texts:
        value = normalize_text(text)
        if _OPERATION_ONLY.fullmatch(value) or _DATE_OR_TIME_ONLY.fullmatch(value):
            return False
        if not re.search(r"[0-9A-Za-z가-힣]", value):
            return False
    return bool(texts)


def _is_date_expression(role: str, text: str, reference: datetime) -> bool:
    if role == "filter" and resolve_month_range([text], reference) is not None:
        return True
    purpose = DatePurpose.SCHEDULE if role == "change" else DatePurpose.SEARCH
    return resolve_date([text], reference, purpose=purpose).status in _DATE_EXPRESSIONS


def _slot_has_meaning(role: str, slot: str, texts: list[str], reference: datetime) -> bool:
    # Date and time are checked span by span: every span must be a date or time
    # expression on its own.  Combining spans ("3시" + "오후") happens later.
    if slot == "date":
        return all(_is_date_expression(role, text, reference) for text in texts)
    if slot == "time":
        return all(resolve_time([text]).status in _TIME_EXPRESSIONS for text in texts)
    if slot == "futureScope":
        return is_future_scope(texts)
    if slot in {"completed", "completedState"}:
        meanings = {resolve_completed([text]) for text in texts}
        return None not in meanings and len(meanings) == 1
    if slot == "category":
        meanings = {resolve_category([text]) for text in texts}
        return None not in meanings and len(meanings) == 1
    if slot == "title":
        return _is_title(texts)
    return False


def _span_identity(span: Span) -> tuple[int, str, int | None]:
    return span.turn, normalize_text(span.text), span.occurrence


def check_operation_roles(operation: EvidenceOperation, *, reference: datetime) -> None:
    allowed = _ROLE_SLOTS[operation.kind]
    used: dict[tuple[int, str, int | None], tuple[str, str]] = {}
    for role, evidence in (("target", operation.target), ("change", operation.change), ("filter", operation.filter)):
        for slot, spans in evidence.items():
            if slot not in allowed[role]:
                raise EvidenceGateError("ROLE_NOT_ALLOWED", f"{operation.kind}.{role}.{slot}")
            texts = adopted_texts(spans)
            if not texts:
                raise EvidenceGateError("ROLE_HAS_NO_ADOPTED_EVIDENCE", f"{role}.{slot}")
            if not _slot_has_meaning(role, slot, texts, reference):
                raise EvidenceGateError("EVIDENCE_MEANING_MISMATCH", f"{role}.{slot}")
            for span in spans:
                if span.status is not SpanStatus.ADOPTED:
                    continue
                identity = _span_identity(span)
                previous = used.get(identity)
                if previous is not None and previous != (role, slot):
                    raise EvidenceGateError("EVIDENCE_ROLE_CONFLICT", f"{role}.{slot}")
                used[identity] = (role, slot)


def check_roles(output: EvidenceOutput, *, reference: datetime) -> None:
    for operation in output.operations:
        check_operation_roles(operation, reference=reference)


def check_turn_prefix(turn_numbers: Sequence[int], through_turn: int) -> None:
    """Require the turns ``1..through_turn`` exactly once and in order."""

    if type(through_turn) is not int or through_turn < 1:
        raise EvidenceGateError("TURNS_INCOMPLETE")
    numbers = list(turn_numbers)
    if any(type(number) is not int or number < 1 for number in numbers):
        raise EvidenceGateError("TURNS_INVALID")
    if len(numbers) != len(set(numbers)):
        raise EvidenceGateError("TURNS_DUPLICATE")
    if numbers != sorted(numbers):
        raise EvidenceGateError("TURNS_OUT_OF_ORDER")
    if [number for number in numbers if number <= through_turn] != list(range(1, through_turn + 1)):
        raise EvidenceGateError("TURNS_INCOMPLETE")


# ------------------------------------------------ unsupported request finder

_CUE = r"(?:알림|문자|리마인더|리마인드|푸시)"
_PARTICLE = r"(?:은|는|이|가|을|를|도|만|로|으로)?"
_ADVERB = r"(?:\s*(?:좀|따로|꼭|같이|함께|미리|바로))*"
_REQUEST_VERB = (
    r"(?:보내|발송\s*해|발송\s*할|전송\s*해|전송\s*할|켜|맞춰|설정\s*해|설정\s*할|설정해|달아|울려|"
    r"추가\s*해|넣어|띄워|날려)"
)
_NEGATED_AFTER_VERB = r"(?!\s*지\s*(?:마|말))"
_REQUEST = re.compile(_CUE + _PARTICLE + _ADVERB + r"\s*" + _REQUEST_VERB + _NEGATED_AFTER_VERB)
_OFFSET_REMINDER = re.compile(r"\d+\s*(?:분|시간)\s*전에?\s*(?:미리\s*)?(?:알려|알림|문자|연락|깨워|울려)")
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
_VERB_TOKEN = re.compile(r"(?:줘|주세요|줄래|부탁|해|해서|해요|하고|하는|할|하자|좀|꼭)$")


class CueKind(StrEnum):
    REQUEST = "request"  # asks for an unsupported feature
    EXCLUSION = "exclusion"  # explicitly declines it ("알림은 빼고")
    TITLE_NOUN = "title_noun"  # part of a schedule title ("알림판 점검")
    UNKNOWN = "unknown"  # cannot tell; treat as a request


@dataclass(frozen=True)
class CueFinding:
    kind: CueKind
    feature: str  # "reminder" or "text_message"
    text: str


@dataclass(frozen=True)
class TextAnalysis:
    findings: tuple[CueFinding, ...]
    supported_operation: bool

    @property
    def has_unsupported_request(self) -> bool:
        return any(finding.kind in {CueKind.REQUEST, CueKind.UNKNOWN} for finding in self.findings)


def _feature(word: str) -> str:
    return "text_message" if word == "문자" else "reminder"


def _has_verb_token(segment: str) -> bool:
    tokens = [re.sub(r"(?:을|를)$", "", token) for token in segment.split()]
    return any(_VERB_TOKEN.search(token) or token in {"좀", "꼭", "부탁"} for token in tokens if token)


def analyze_text(text: str) -> TextAnalysis:
    """Find reminder and text-message requests in one user text turn."""

    value = normalize_text(text)
    findings: list[CueFinding] = []
    covered: list[tuple[int, int]] = []

    def is_covered(position: int) -> bool:
        return any(start <= position < end for start, end in covered)

    # Both patterns start with a cue word, so the cue is the leading match.
    for match in _EXCLUSION.finditer(value):
        word = _CUE_WORD.match(match.group(0))
        feature = _feature(word.group(0)) if word else "reminder"
        findings.append(CueFinding(CueKind.EXCLUSION, feature, match.group(0)))
        covered.append(match.span())
    for match in _REQUEST.finditer(value):
        if is_covered(match.start()):
            continue
        word = _CUE_WORD.match(match.group(0))
        feature = _feature(word.group(0)) if word else "reminder"
        findings.append(CueFinding(CueKind.REQUEST, feature, match.group(0)))
        covered.append(match.span())
    for pattern in (_OFFSET_REMINDER, _EARLY_TELL):
        for match in pattern.finditer(value):
            if is_covered(match.start()):
                continue
            feature = "text_message" if "문자" in match.group(0) else "reminder"
            findings.append(CueFinding(CueKind.REQUEST, feature, match.group(0)))
            covered.append(match.span())
    for match in _CUE_WORD.finditer(value):
        if is_covered(match.start()):
            continue
        rest = value[match.end():]
        verb = _SUPPORTED_OP.search(rest)
        between = rest[: verb.start()] if verb else rest
        compound = bool(_TITLE_FOLLOWER.match(rest)) and not _PARTICLE_ONLY.match(rest)
        noun_phrase = (
            rest.startswith(" ")
            and bool(re.match(r"^ [가-힣A-Za-z0-9]", rest))
            and not _PARTICLE_ONLY.match(rest.lstrip())
        )
        if verb and (compound or noun_phrase) and not _REQUEST_STEM.search(between) and not _has_verb_token(between):
            findings.append(
                CueFinding(
                    CueKind.TITLE_NOUN,
                    _feature(match.group(0)),
                    value[match.start(): match.end() + verb.start()].strip(),
                )
            )
        else:
            findings.append(
                CueFinding(
                    CueKind.UNKNOWN,
                    _feature(match.group(0)),
                    value[match.start(): min(len(value), match.end() + 12)],
                )
            )
    residual = value
    for start, end in sorted(covered, reverse=True):
        residual = residual[:start] + " " * (end - start) + residual[end:]
    return TextAnalysis(findings=tuple(findings), supported_operation=bool(_SUPPORTED_OP.search(residual)))


# ------------------------------------------------------ yes or no answers

_AFFIRM = re.compile(
    r"^(?:응|어|네|넵|예|그래|그래요|좋아|좋아요|오케이|ok|알겠어|알겠습니다|맞아|맞아요|그렇게\s*해)(?![가-힣])",
    re.IGNORECASE,
)
_DECLINE = re.compile(r"(?:^|[\s,.!?])(?:아니|아뇨|아니요|아니오|싫어|됐어|됐어요|취소|그만)(?![가-힣])")
_DECLINE_VERB = re.compile(r"(?:등록|넣|진행|추가|하|잡)지\s*(?:마|말)|안\s*할래|하지\s*않|그만\s*둬|취소해")
# Issue #35: polite and everyday forms ("해 주세요", "부탁", "필요 없어요") also proceed.
_PROCEED = re.compile(r"(?:등록해|등록\s*해|넣어|진행해|잡아|그렇게\s*해|해\s*줘|해\s*주세요|부탁|필요\s*없)")
_DATE_OR_TIME = re.compile(r"\d+\s*(?:시|분|월|일|년)|오늘|내일|모레|글피|요일")


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


def classify_answer(text: str) -> Answer:
    """Read a reply to a backend yes/no question.

    Anything unclear, any new unsupported request, and any reply that carries a
    date or time is UNKNOWN, so the backend asks again instead of guessing.
    """

    value = normalize_text(text)
    analysis = analyze_text(value)
    kinds = {finding.kind for finding in analysis.findings}
    if CueKind.REQUEST in kinds or CueKind.UNKNOWN in kinds:
        return Answer.UNKNOWN
    if _DATE_OR_TIME.search(value):
        return Answer.UNKNOWN
    affirm = bool(_AFFIRM.match(value))
    decline = bool(_DECLINE.search(value)) or bool(_DECLINE_VERB.search(value))
    proceed = bool(_PROCEED.search(value))
    if CueKind.EXCLUSION in kinds and proceed and not _DECLINE_VERB.search(value):
        return Answer.YES
    if affirm and not decline:
        return Answer.YES
    if decline and not affirm:
        return Answer.NO
    if proceed and not decline:
        return Answer.YES
    return Answer.UNKNOWN
