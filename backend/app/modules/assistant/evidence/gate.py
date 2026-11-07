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
    Span,
    SpanStatus,
    adopted_texts,
)
from app.modules.assistant.evidence.normalize import (
    DatePurpose,
    DateStatus,
    TimeStatus,
    is_date_or_time_only,
    is_future_scope,
    mentions_date_or_time,
    normalize_text,
    resolve_category,
    resolve_completed,
    resolve_date,
    resolve_month_range,
    resolve_time,
)
from app.modules.assistant.evidence.role_contract import ROLE_SLOTS

# Words that only name the request, never the schedule itself.
_OPERATION_ONLY = re.compile(
    r"^(?:(?:일정|목록)\s*)?(?:등록(?:하고|해\s*줘)?|넣어\s*줘|잡아\s*줘|"
    r"보여\s*줘|알려\s*줘|뭐\s*있어|있어|바꿔\s*줘|옮겨\s*줘|"
    r"삭제해\s*줘|지워\s*줘|지우고|다\s*보여\s*줘|전부\s*보여\s*줘)$"
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
        # A date or time alone ("정오", "다음 주 화요일") cannot be a title.
        if _OPERATION_ONLY.fullmatch(value) or is_date_or_time_only(value):
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
    allowed = ROLE_SLOTS[operation.kind]
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
    r"(?:등록|넣어|넣고|넣자|잡아|잡고|잡되|추가해|만들어|옮겨|바꿔|변경해|수정해|지워|지우고|삭제|"
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
#
# A reply to a yes/no question is split into words of four kinds.  Every word
# must be one of them or a neutral word; anything left over ("응 정오로",
# "응, 제목은 주간 회의로", "그건 좀") is new or unclear information, so the
# backend asks again instead of guessing or dropping it (review 2026-11-01).
# Keeping one table, not separate yes and no lists, stops a word from being
# allowed without also being judged.

# "알림 빼고" or "문자는 필요 없어요" in a consent reply: the user drops the
# unsupported part and goes on.
_DROPPED_FEATURE = re.compile(
    r"(?:알림|문자|리마인더|푸시|메시지|메세지|알람)\s*(?:은|는|도|이|가)?\s*"
    r"(?:없이|빼고|빼\s*주세요|빼\s*줘|빼|말고|생략하고|필요\s*없으니|필요\s*없어요|필요\s*없어|필요\s*없습니다|"
    r"안\s*해도\s*(?:돼요|돼|됩니다)|됐어요|됐어|괜찮아요|괜찮아)"
)
_ANSWER_WORD = re.compile(
    # No: refusing or stopping.
    r"(?P<no>(?:하|넣|잡|등록하|진행하|추가하)지\s*(?:마세요|마|말아\s*주세요|말아|않을게요|않을게|않아요|않아)|"
    r"안\s*(?:할게요|할게|할래요|할래|해요|해)|"
    r"취소할게요|취소할게|취소해\s*주세요|취소해\s*줘|취소해요|취소해|취소|"
    r"그만둘게요|그만둘게|그만할게요|그만할게|그만\s*둬|그만|"
    r"아니요|아니오|아뇨|아니야|아니|싫어요|싫어|됐어요|됐어|마세요)|"
    # A part of the requests: "나머지만", "첫 번째는 빼 줘".
    r"(?P<part>빼고|빼\s*주세요|빼\s*줘|빼|나머지)|"
    # Yes.
    r"(?P<yes>그렇게\s*해\s*주세요|그렇게\s*해\s*줘|그렇게\s*해요|그렇게\s*해|"
    r"알겠습니다|알겠어요|알겠어|좋습니다|좋아요|좋아|맞습니다|맞아요|맞아|그래요|그래|그럼요|"
    r"오케이|ok|넵|네|예|응|어)|"
    # Go ahead: "등록해 줘", "진행해 주세요".
    r"(?P<go>등록만|등록|넣어|잡아|진행|해\s*주세요|해\s*줘요|해\s*줘|해요|해|"
    r"부탁해요|부탁해|부탁드립니다|부탁드려요|부탁합니다|할게요|할게|주세요|줘)|"
    # Neutral: words that change nothing.  "좀" is left out ("그건 좀" is a soft no).
    r"(?P<neutral>첫\s*번째(?:\s*(?:것|거))?|그건|그거|그것|일정|전부|모두|이대로|그대로|그냥|일단|그럼|"
    r"괜찮아요|괜찮아|은|는|도|만|요)",
    re.IGNORECASE,
)
_ANSWER_FILLER = re.compile(r"[\s,.!?~…]+")


class Answer(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


def _answer_words(value: str) -> set[str] | None:
    """The kinds of words in a reply, or None when something else is left."""

    rest = _DROPPED_FEATURE.sub(" ", value)
    # Dropping the unsupported part ("알림은 필요 없어요") means going on without it.
    kinds: set[str] = {"go"} if rest != value else set()
    pieces: list[str] = []
    position = 0
    for match in _ANSWER_WORD.finditer(rest):
        pieces.append(rest[position:match.start()])
        position = match.end()
        kinds.add(match.lastgroup or "")
    pieces.append(rest[position:])
    if _ANSWER_FILLER.sub("", "".join(pieces)):
        return None
    return kinds


def classify_answer(text: str) -> Answer:
    """Read a reply to a backend yes/no question.

    1. A yes word with a no word ("네, 취소할게요") is unclear: ask again.
    2. Any no word is a no ("아니요, 등록하지 마세요").
    3. Picking a part ("나머지만 해 줘", "응 첫 번째는 빼 줘") is unclear: ask again.
    4. A yes or go-ahead word, or dropping the unsupported part ("알림은 필요
       없어요"), is a yes.
    5. Anything else, a new request, or a date or time is unclear: ask again.
    """

    value = normalize_text(text)
    analysis = analyze_text(value)
    cue_kinds = {finding.kind for finding in analysis.findings}
    if CueKind.REQUEST in cue_kinds or CueKind.UNKNOWN in cue_kinds or mentions_date_or_time(value):
        return Answer.UNKNOWN
    kinds = _answer_words(value)
    if kinds is None:
        return Answer.UNKNOWN
    if "no" in kinds:
        return Answer.UNKNOWN if "yes" in kinds else Answer.NO
    if "part" in kinds:
        return Answer.UNKNOWN
    # A pointing word alone does not identify what the user accepted. In
    # particular, "어 그건" must not turn a pending write into approval.
    if "neutral" in kinds and "yes" in kinds and "go" not in kinds:
        return Answer.UNKNOWN
    if "yes" in kinds or "go" in kinds:
        return Answer.YES
    return Answer.UNKNOWN
