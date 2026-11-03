"""Read-only replay of all 849 human-authored evidence targets.

The frozen baseline was produced from commit 3b0f937 with this same harness.
Intentional behavior changes must be listed case by case in the separate,
human-authored approval fixture. Neither this test nor the production
validator writes dataset artifacts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from uuid import UUID

from app.modules.assistant.decisions import DecisionContext
from app.modules.assistant.evidence.provider import EvidenceDecisionProvider

REPOSITORY = Path(__file__).resolve().parents[3]
DATASET = REPOSITORY / "datasets/qwen/experiments/candidate_c_create_400_v0.1"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
BASELINE_PATH = FIXTURES / "assistant_evidence_gold_replay_baseline_v1.json"
APPROVAL_PATH = FIXTURES / "assistant_evidence_replay_approved_differences.json"
GROUPS = ("pilot", "batch1", "batch2", "batch3", "batch4", "batch5", "batch6", "test50")


class _EmptyReader:
    def get(self, schedule_id):
        return None

    def list(self, **kwargs):
        return []


class _FixedModel:
    def __init__(self, output: dict) -> None:
        self.output = output

    def extract(self, **kwargs) -> str:
        return _canonical(self.output)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _state(previous_turns: list[str]) -> dict[str, object]:
    return {
        "evidence": 3,
        "turns": previous_turns,
        "consentGranted": False,
        "pending": None,
        "changeDate": None,
        "changeDateSource": None,
        "midnightSource": None,
        "createConfirmed": None,
        "validatedOutput": None,
    }


def _signature(target: dict, conversation: dict, turn_number: int) -> str:
    turns = [
        turn
        for turn in conversation["context"]["turns"]
        if turn["inputType"] == "text" and turn["turn"] <= turn_number
    ]
    assert turns and turns[-1]["turn"] == turn_number
    previous = [turn["text"] for turn in turns[:-1]]
    context = conversation["context"]
    decision_context = DecisionContext(
        request_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        conversation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        input={"type": "text", "text": turns[-1]["text"]},
        task_reference_accepted_at=datetime.fromisoformat(context["referenceTime"]),
        server_time_trusted=context["serverTimeTrusted"],
        is_question_follow_up=bool(previous),
        answered_question_kind="need_more_info" if previous else None,
        control_state_version=1 if previous else None,
        control_state=_state(previous) if previous else None,
    )
    provider = EvidenceDecisionProvider(_FixedModel(target), _EmptyReader())
    try:
        envelope = provider.decide(decision_context)
    except Exception as exc:
        return _canonical({"error": str(getattr(exc, "code", None) or type(exc).__name__)})
    decision = envelope.decision.model_dump(mode="json", by_alias=True, exclude_none=True)
    return _canonical(decision)


def replay_groups() -> Iterator[tuple[str, list[dict[str, object]]]]:
    for group in GROUPS:
        directory = DATASET / group
        conversations = {
            row["meta"]["caseId"]: row
            for row in _read_jsonl(directory / "conversations-v0.1.jsonl")
        }
        results: list[dict[str, object]] = []
        for target_row in _read_jsonl(directory / "candidate-c-targets-v0.1.jsonl"):
            identity = (target_row["caseId"], target_row["turn"])
            results.append(
                {
                    "case": identity[0],
                    "turn": identity[1],
                    "signature": _signature(
                        target_row["target"],
                        conversations[identity[0]],
                        identity[1],
                    ),
                }
            )
        yield group, results


def build_manifest() -> dict[str, object]:
    groups: dict[str, dict[str, object]] = {}
    total = 0
    for name, rows in replay_groups():
        payload = "\n".join(
            _canonical({"case": row["case"], "turn": row["turn"], "signature": row["signature"]})
            for row in rows
        )
        groups[name] = {
            "count": len(rows),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }
        total += len(rows)
    return {"version": 1, "baselineCommit": "3b0f937", "total": total, "groups": groups}


def test_all_gold_targets_match_frozen_baseline_or_explicit_approval() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    approval = json.loads(APPROVAL_PATH.read_text(encoding="utf-8"))
    approved = {(item["case"], item["turn"]): item for item in approval["differences"]}
    seen: set[tuple[str, int]] = set()
    actual_groups: dict[str, dict[str, object]] = {}
    total = 0

    for name, rows in replay_groups():
        normalized: list[str] = []
        for row in rows:
            identity = (str(row["case"]), int(row["turn"]))
            signature = str(row["signature"])
            item = approved.get(identity)
            if item is not None:
                assert signature == _canonical(json.loads(item["new"])), identity
                signature = _canonical(json.loads(item["baseline"]))
                seen.add(identity)
            normalized.append(
                _canonical({"case": identity[0], "turn": identity[1], "signature": signature})
            )
        payload = "\n".join(normalized)
        actual_groups[name] = {
            "count": len(rows),
            "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        }
        total += len(rows)

    assert total == baseline["total"] == 849
    assert actual_groups == baseline["groups"]
    assert seen == set(approved)


def test_baseline_manifest_shape_is_fixed() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert baseline["version"] == 1
    assert baseline["baselineCommit"] == "3b0f937"
    assert baseline["total"] == 849
    assert tuple(baseline["groups"]) == GROUPS
