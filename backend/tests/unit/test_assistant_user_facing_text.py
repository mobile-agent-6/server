from __future__ import annotations

import ast
import re
from pathlib import Path

from app.modules.assistant.evidence import texts
from app.modules.assistant.processor import (
    LIST_NARROWING_QUESTION,
    RELATIVE_TIME_QUESTION,
)


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_USER_FACING_SOURCE_FILES = (
    "app/modules/assistant/evidence/texts.py",
    "app/modules/assistant/evidence/provider.py",
    "app/modules/assistant/processor.py",
    "app/modules/assistant/safe_write.py",
    "app/modules/assistant/state_service.py",
    "app/modules/assistant/runtime_failures.py",
)
_FORMAL_ENDING = re.compile(r"[가-힣]니다[.?]")
_KOREAN = re.compile(r"[가-힣]")


def _string_literals(relative_path: str) -> list[tuple[int, str]]:
    source = (_BACKEND_ROOT / relative_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_assistant_user_facing_text_does_not_use_formal_ending() -> None:
    matches: list[str] = []
    for relative_path in _USER_FACING_SOURCE_FILES:
        for line_number, value in _string_literals(relative_path):
            if _FORMAL_ENDING.search(value):
                matches.append(f"{relative_path}:{line_number}: {value}")

    assert matches == []


def test_korean_user_facing_text_does_not_use_english_category() -> None:
    matches: list[str] = []
    for relative_path in _USER_FACING_SOURCE_FILES:
        for line_number, value in _string_literals(relative_path):
            if "category" in value.lower() and _KOREAN.search(value):
                matches.append(f"{relative_path}:{line_number}: {value}")

    assert matches == []


def test_frontend_review_questions_are_exact() -> None:
    assert texts.ASK_NARROW == (
        "조건에 맞는 일정이 너무 많아요. 날짜를 함께 말씀해 주세요."
    )
    assert texts.ASK_LIST_SCOPE == (
        "확인할 일정의 기간을 말씀해 주세요. 예: 오늘, 이번 주, 다음 주."
    )
    assert texts.confirm_update("재고 확인") == (
        "'재고 확인' 일정을 다음 내용으로 변경할까요?"
    )
    assert texts.confirm_delete("재고 확인") == "'재고 확인' 일정을 삭제할까요?"
    assert RELATIVE_TIME_QUESTION == (
        "서버 시각을 신뢰할 수 없어요. 연도, 월, 일과 시간을 함께 알려 주세요."
    )
    assert LIST_NARROWING_QUESTION == (
        "일정이 100개를 넘어요. 기간, 카테고리 또는 완료 여부를 추가해 "
        "범위를 좁혀 주세요."
    )
