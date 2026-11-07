"""Deterministic cues for user-requested Schedule operations."""

from __future__ import annotations

import re

_ACTION_PATTERN = (
    r"등록|넣어|넣고|넣자|잡아|잡고|잡자(?!재)|잡을래|잡되(?![게지다])|"
    r"추가해|만들어|옮겨|바꿔|변경\s*해|수정\s*해|지워|지우고|삭제|"
    r"완료로|완료\s*처리|조회"
)
_CONTEXTLESS_QUERY_PATTERN = (
    r"보여\s*줘|보여줘|알려\s*줘|알려줘|일정\s*있어|뭐\s*있어"
)

_ACTION = re.compile(rf"(?:{_ACTION_PATTERN})")
_ACTION_OR_CONTEXTLESS_QUERY = re.compile(
    rf"(?:{_ACTION_PATTERN}|{_CONTEXTLESS_QUERY_PATTERN})"
)


def find_supported_operation(
    text: str, *, include_contextless_query: bool = False
) -> re.Match[str] | None:
    """Return the first supported operation cue in normalized user text.

    The unsupported-feature gate keeps its existing broad query detection so it
    can separate title nouns from reminder cues. The provider uses the stricter
    action-only form and applies its Schedule-context query rule separately.
    """

    pattern = (
        _ACTION_OR_CONTEXTLESS_QUERY if include_contextless_query else _ACTION
    )
    return pattern.search(text)
