from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

from app.modules.assistant.evidence.role_contract import serializable_role_slots

REPOSITORY = Path(__file__).resolve().parents[3]
PRODUCTION = REPOSITORY / "backend/app/modules/assistant/evidence"
RESEARCH = REPOSITORY / "datasets/qwen/experiments/structured_output_contract_v0.1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _data_role_gate(monkeypatch):
    monkeypatch.syspath_prepend(str(RESEARCH))
    path = RESEARCH / "candidate_c_role_gate_v0_5.py"
    spec = importlib.util.spec_from_file_location("candidate_c_role_gate_v0_5_drift", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_and_data_role_matrices_do_not_drift(monkeypatch) -> None:
    data_gate = _data_role_gate(monkeypatch)
    assert data_gate.serializable_role_slots() == serializable_role_slots()


def test_prompt_and_authoring_guide_describe_the_implemented_roles() -> None:
    prompt = (PRODUCTION / "prompt.txt").read_text(encoding="utf-8")
    guide = (REPOSITORY / "docs/candidate_c_data_authoring_guide.md").read_text(
        encoding="utf-8"
    )
    assert _sha256(PRODUCTION / "prompt.txt") == (
        "6352d687dd9093f783ff1c23e95d6f84433af30c3776a524aaa980a83db5f24b"
    )
    assert (
        "- update: 기존 일정을 찾는 title/date/time/category/completedState는 "
        "targetEvidence," in prompt
    )
    assert (
        "- list: 찾을 title/date/time/category/completed/futureScope는 filterEvidence."
        in prompt
    )
    assert (
        "| update | 제목, 날짜, 시간, 분류, 완료 상태 |"
        in guide
    )
    assert "`candidate_c_role_gate_v0_5`의 역할 검사" in guide


def test_legacy_research_gate_hashes_stay_immutable() -> None:
    expected = {
        "common_gate_v0_2.py": "82252c72f8c20bb4103b427e65c272d878a8b885393fbfbc987cb63519470f7a",
        "common_gate_v0_3.py": "7d72b31cbcd98cd021380fb72dde0f2da6b6d03254319d0f87ab25c8fbb97bd4",
        "common_gate_v0_4.py": "7185baf7f7bde15359d17d76b3aa659587c0127f1e792549d2c11d873291963b",
    }
    assert {name: _sha256(RESEARCH / name) for name in expected} == expected
