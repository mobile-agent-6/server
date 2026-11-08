from __future__ import annotations

import re
from pathlib import Path


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


def test_assistant_user_facing_text_does_not_use_formal_ending() -> None:
    matches: list[str] = []
    for relative_path in _USER_FACING_SOURCE_FILES:
        source = (_BACKEND_ROOT / relative_path).read_text(encoding="utf-8")
        for line_number, line in enumerate(source.splitlines(), start=1):
            if _FORMAL_ENDING.search(line):
                matches.append(f"{relative_path}:{line_number}: {line.strip()}")

    assert matches == []
