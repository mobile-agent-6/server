"""PROPOSED rule baseline R for Issue #30.

The model output is exactly one intent.  Slot evidence is extracted from the
bounded user text by deterministic rules.  Consequently a request containing
ordered multiple operations is structurally unrepresentable in R v0.1; this
module does not silently widen the contract to an intent list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from contracts import normalize_text

RULE_BASELINE_VERSION = "issue30-rule-baseline-R-v0.1"


@dataclass(frozen=True)
class RuleAssessment:
    classification: str
    reason: str
    extracted: Mapping[str, Any]


_DATE = re.compile(
    r"(?P<absolute>[0-9]{4}년\s*[0-9]{1,2}월\s*[0-9]{1,2}일)"
    r"|(?P<month_day>[0-9]{1,2}월\s*[0-9]{1,2}일)"
    r"|(?P<relative>오늘|내일|모레)"
)
_MONTH = re.compile(r"(?:(?P<year>[0-9]{4})년\s*)?(?P<month>[0-9]{1,2})월")
_TIME = re.compile(r"(?:(오전|오후|아침|저녁)\s*)?([0-9]{1,2})시(?:\s*([0-9]{1,2})분|\s*(반))?")
_CATEGORY = {
    "INBOUND": re.compile(r"입고"),
    "INVENTORY": re.compile(r"재고"),
    "OUTBOUND": re.compile(r"출고|피킹"),
    "RETURN": re.compile(r"반품"),
}


def _date_values(text: str, reference: datetime) -> list[str]:
    values: list[str] = []
    for match in _DATE.finditer(text):
        if match.group("absolute"):
            year, month, day = map(int, re.findall(r"[0-9]+", match.group("absolute")))
            values.append(f"{year:04d}-{month:02d}-{day:02d}")
        elif match.group("month_day"):
            month, day = map(int, re.findall(r"[0-9]+", match.group("month_day")))
            values.append(f"{reference.year:04d}-{month:02d}-{day:02d}")
        else:
            offset = {"오늘": 0, "내일": 1, "모레": 2}[match.group("relative")]
            values.append((reference.date() + timedelta(days=offset)).isoformat())
    return values


def _time_values(text: str) -> list[str]:
    values: list[str] = []
    for period, hour_text, minute_text, half in _TIME.findall(text):
        hour = int(hour_text)
        minute = 30 if half else int(minute_text or 0)
        if period in {"오후", "저녁"} and hour < 12:
            hour += 12
        if period in {"오전", "아침"} and hour == 12:
            hour = 0
        if not period and 1 <= hour <= 12:
            values.append("AMBIGUOUS_AMPM")
        elif 0 <= hour <= 23 and 0 <= minute <= 59:
            values.append(f"{hour:02d}:{minute:02d}")
    return values


def _category_values(text: str) -> list[str]:
    return [category for category, pattern in _CATEGORY.items() if pattern.search(text)]


def _completed_value(text: str) -> bool | None:
    if re.search(r"미완료|아직\s*안\s*끝|완료.*(?:취소|해제)", text):
        return False
    if re.search(r"완료(?:된|로|\s*처리|\s*표시)|끝났", text):
        return True
    return None


def extract_rule_slots(turns: list[str], intent: str, reference: datetime) -> dict[str, Any]:
    """Extract a conservative, reviewable set of slots without oracle access."""
    combined = normalize_text(" ".join(turns))
    dates = _date_values(combined, reference)
    times = _time_values(combined)
    categories = _category_values(combined)
    completed = _completed_value(combined)
    title = None
    first = normalize_text(turns[0]) if turns else ""
    patterns = {
        "CREATE": r"^(.*?)(?:\s*(?:등록해|등록하고|넣어|잡아))",
        "LIST": r"^(.*?)(?:\s*일정)?\s*(?:언제|있어|보여|알려|뭐\s*있)",
        "UPDATE": r"^(.*?)(?:\s*일정)?\s*(?:제목|분류|카테고리|시간|시각|완료|내일|오늘|모레|[0-9]{1,4}년?)",
        "DELETE": r"^(.*?)(?:\s*일정)?\s*(?:지워|삭제)",
    }
    match = re.search(patterns.get(intent, r"a^"), first)
    if match:
        candidate = match.group(1)
        candidate = _DATE.sub("", candidate)
        candidate = _TIME.sub("", candidate)
        candidate = re.sub(r"^(?:앞으로|완료된|분류가\s*\S+이고|분류가\s*\S+인)\s*", "", candidate)
        candidate = normalize_text(candidate.strip(" ,?"))
        title = candidate or None
    return {
        "intent": intent,
        "dates": dates,
        "times": times,
        "categories": categories,
        "completed": completed,
        "title": title,
    }


def assess_rule_case(case: Mapping[str, Any]) -> RuleAssessment:
    operations = case["semantics"]["operations"]
    if len(operations) > 1:
        return RuleAssessment(
            "structurally_unrepresentable",
            "R v0.1 has one intent and cannot preserve ordered multiple operations",
            {},
        )
    text_turns = [
        turn["text"] for turn in case["context"]["turns"] if turn.get("inputType") == "text"
    ]
    if not operations:
        return RuleAssessment(
            "safe_reject",
            "bounded schedule rules do not execute unsupported or unrelated requests",
            {"intent": "UNSUPPORTED"},
        )
    operation = operations[0]
    intent = operation["kind"].upper()
    reference = datetime.fromisoformat(case["context"]["referenceTime"])
    extracted = extract_rule_slots(text_turns, intent, reference)
    tags = set(case["meta"]["scenarioTags"])
    if "correction" in tags:
        return RuleAssessment(
            "safe_reject",
            "R v0.1 does not choose between correction spans",
            extracted,
        )
    if "title_similar_match" in tags:
        return RuleAssessment(
            "safe_reject",
            "similar-title matching is outside the deterministic rule baseline",
            extracted,
        )
    if "same_display_candidates" in tags:
        return RuleAssessment(
            "safe_reject",
            "indistinguishable candidates remain a backend question",
            extracted,
        )
    if any("이번 주" in text or re.search(r"[월화수목금토일]요일", text) for text in text_turns):
        return RuleAssessment(
            "safe_reject",
            "weekday expressions are outside R v0.1's bounded date grammar",
            extracted,
        )
    if any(item == "AMBIGUOUS_AMPM" for item in extracted["times"]):
        return RuleAssessment(
            "safe_reject",
            "ambiguous AM/PM is asked rather than normalized",
            extracted,
        )
    if operation["kind"] in {"create", "update"}:
        change = operation["normalized"].get("change", {})
        if "scheduledAt" in change and not extracted["times"]:
            return RuleAssessment(
                "safe_reject",
                "required time is outside the bounded lexical extractor",
                extracted,
            )
    return RuleAssessment("representable", "bounded deterministic rules cover this case", extracted)


RULE_BASELINE_SPEC = {
    "version": RULE_BASELINE_VERSION,
    "modelOutput": {"intent": sorted({"CREATE", "LIST", "UPDATE", "DELETE", "UNSUPPORTED", "UNRELATED"})},
    "intentCardinality": "exactly one",
    "slotOwner": "deterministic backend rules",
    "unsupportedBehavior": "safe reject or ask; never fabricate a slot",
    "knownLimits": [
        "ordered multiple operations are structurally unrepresentable",
        "correction choice, similar-title matching, and weekday grammar fail closed",
    ],
}
