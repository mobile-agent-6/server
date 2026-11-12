"""Source provenance, stored-output parity, and the v37 provider boundary."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.assistant.decisions import DecisionContext, DecisionProviderInvalidOutput
from app.modules.assistant.evidence.provider import _State, _validate_output
from app.modules.assistant.evidence.rules import correction_bridge_v37 as bridge
from app.modules.assistant.evidence.rules.evidence_postprocess_v37 import (
    ClarificationRequired,
    apply_rules,
)

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "datasets/qwen/experiments/logistics440_v37"
COHORTS = json.loads((BUNDLE / "evaluation/cohorts.json").read_text(encoding="utf-8"))


def test_packaged_rules_match_verified_data_sources() -> None:
    packaged = Path(bridge.__file__).parent
    files = {p.stem: p for p in packaged.glob("*.py") if p.name != "__init__.py"}
    assert len(files) == 38
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))["files"]
    local_imports = "|".join(re.escape(name) for name in files)
    for name, path in files.items():
        source = (BUNDLE / "runtime/rules" / path.name).read_text(encoding="utf-8")
        assert hashlib.sha256(source.encode("utf-8")).hexdigest() == manifest[
            f"runtime/rules/{path.name}"
        ]["sha256"]
        expected = re.sub(
            rf"(?m)^(\s*from )({local_imports})( import\b)",
            lambda match: match[1] + "." + match[2] + match[3],
            source,
        )
        assert path.read_text(encoding="utf-8") == expected, name


@pytest.mark.parametrize("cohort", [c["name"] for c in COHORTS])
def test_packaged_rules_reproduce_saved_outputs(cohort: str) -> None:
    base = BUNDLE / "evaluation" / cohort
    inputs = [json.loads(line) for line in (base / "inputs.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = json.loads((base / "expected-v37.json").read_text(encoding="utf-8"))
    assert len(inputs) == len(expected)
    for index, (item, saved) in enumerate(zip(inputs, expected), 1):
        user = json.loads(item["messages"][1]["content"])
        turns = {turn["turn"]: turn["text"] for turn in user["userTurns"]}
        reference = datetime.fromisoformat(user["taskReferenceTime"])
        cached = json.loads((base / "raw" / f"{index:03}.json").read_text(encoding="utf-8"))
        assert hashlib.sha256(cached["raw"].encode("utf-8")).hexdigest() == cached["rawSha256"]
        if saved["clarification"] is not None:
            with pytest.raises(ClarificationRequired) as captured:
                apply_rules(cached["raw"], user_turns=turns, reference=reference,
                            server_time_trusted=user["serverTimeTrusted"])
            assert captured.value.reason == saved["clarification"]["reason"]
            assert captured.value.question == saved["clarification"]["question"]
        else:
            processed = apply_rules(cached["raw"], user_turns=turns, reference=reference,
                                    server_time_trusted=user["serverTimeTrusted"])
            assert processed.raw == saved["postRaw"], (cohort, index)
            _validate_output(processed.raw, turns=turns, reference=reference)


class NoReads:
    def get(self, *args, **kwargs):
        raise AssertionError("Unexpected Schedule read")

    def list(self, **kwargs):
        raise AssertionError("Unexpected Schedule read")


def context(text, reference, state=None):
    return DecisionContext(
        request_id=uuid4(), conversation_id=uuid4(), input={"type": "text", "text": text},
        request_accepted_at=reference, task_reference_accepted_at=reference,
        server_time_trusted=True, is_question_follow_up=state is not None,
        answered_question_kind="need_more_info" if state is not None else None,
        control_state_version=1 if state is not None else None, control_state=state,
    )


def test_ambiguous_operation_clears_state_before_bare_yes() -> None:
    base = BUNDLE / "evaluation/ambiguity24"
    item = json.loads((base / "inputs.jsonl").read_text(encoding="utf-8").splitlines()[12])
    user = json.loads(item["messages"][1]["content"])
    turns = [turn["text"] for turn in user["userTurns"]]
    raw = json.loads((base / "raw/013.json").read_text(encoding="utf-8"))["raw"]
    reference = datetime.fromisoformat(user["taskReferenceTime"])
    seen = []

    class Model:
        def extract(self, *, turns, **kwargs):
            seen.append(list(turns))
            # The yes response is a controlled neutral model output, not new inference.
            return raw if len(seen) == 1 else (
                '{"operations":[],"unsupportedEvidence":[],"missingInformation":[]}'
            )

    provider = bridge.make_provider(Model(), NoReads())
    result = provider.decide(context(turns[-1], reference, _State(turns=turns[:-1]).to_json()))
    assert result.decision.kind == "ask"
    assert result.control_state["turns"] == []
    assert result.control_state["pending"] is None
    yes = provider.decide(context("네", reference, result.control_state))
    assert yes.decision.kind == "ask"
    assert seen == [turns, ["네"]]


def test_postprocessed_output_still_passes_backend_validation(monkeypatch) -> None:
    reference = datetime.fromisoformat("2028-01-10T09:00:00+09:00")
    monkeypatch.setattr(bridge, "apply_rules", lambda *a, **kw: SimpleNamespace(raw='{"extra":1}'))

    class Model:
        def extract(self, **kwargs):
            return "{}"

    with pytest.raises(DecisionProviderInvalidOutput):
        bridge.make_provider(Model(), NoReads()).decide(context("내일 입고 검수 등록해 줘", reference))
