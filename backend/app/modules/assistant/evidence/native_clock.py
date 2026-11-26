"""Split a source-verified Korean clock accidentally retained in a delete title."""
from __future__ import annotations

import re
from dataclasses import replace

from app.modules.assistant.evidence.contract import (
    EvidenceOutput, OperationKind, Span, SpanStatus,
)
from app.modules.assistant.evidence.normalize import (
    TimeStatus, mentions_date_or_time, resolve_time,
)
from app.modules.assistant.evidence.rules.evidence_postprocess_v15 import (
    MARK, OUTSIDE, UNCERTAIN, NoPlan, clean_title,
)

_CLOCK = (
    r"(?:(?:오전|오후|새벽|아침|저녁|밤)\s*)?"
    r"(?:열\s*한|열\s*두|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*시"
    r"(?:\s*(?:\d{1,2}\s*분|반))?"
)


def split_native_clock_target(output: EvidenceOutput, *, user_turns: dict[int, str]) -> EvidenceOutput:
    if set(user_turns) != {1} or len(output.operations) != 1 or output.unsupported or output.missing_information:
        return output
    operation = output.operations[0]
    if (operation.kind is not OperationKind.DELETE or operation.change or operation.filter
            or operation.unsupported or operation.missing_information
            or set(operation.target) != {"date", "title"}):
        return output
    dates, titles = operation.target["date"], operation.target["title"]
    if len(dates) != 1 or len(titles) != 1:
        return output
    if any(s.turn != 1 or s.status is not SpanStatus.ADOPTED or s.occurrence is not None
           for s in (dates[0], titles[0])):
        return output
    source = user_turns[1]
    if OUTSIDE.search(source) or UNCERTAIN.search(source) or MARK.search(source):
        return output
    match = re.fullmatch(
        rf"\s*{re.escape(dates[0].text)}(?:에 있는)?\s+"
        rf"(?P<clock>{_CLOCK})(?:에)?\s+(?P<title>.+?)\s+"
        r"일정(?:을)?\s+(?:삭제해|지워)\s*(?:줘|주세요)[.!]?\s*",
        source,
    )
    if match is None or source[match.start("clock"):match.end("title")] != titles[0].text:
        return output
    clock, title = match["clock"], match["title"]
    if source.count(clock) != 1 or source.count(title) != 1 or mentions_date_or_time(title):
        return output
    if resolve_time([clock]).status not in {TimeStatus.RESOLVED, TimeStatus.NEEDS_AMPM}:
        return output
    try:
        clean_title(title, explicit=True)
    except NoPlan:
        return output
    target = {
        "date": dates,
        "time": (Span(1, clock, SpanStatus.ADOPTED),),
        "title": (Span(1, title, SpanStatus.ADOPTED),),
    }
    return replace(output, operations=(replace(operation, target=target),))
