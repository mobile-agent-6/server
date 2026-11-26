"""Deterministically audit the current 400-conversation DATA v2.6 dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
DATASET_ROOT = REPO_ROOT / "datasets/qwen/experiments/multiturn_400_v0.1"
ALL_PATH = DATASET_ROOT / "scenarios_all_400_UNREVIEWED.jsonl"
SPLITS = {
    "train": DATASET_ROOT / "train/scenarios_UNREVIEWED.jsonl",
    "validation": DATASET_ROOT / "validation/scenarios_UNREVIEWED.jsonl",
    "test": DATASET_ROOT / "test/scenarios_UNREVIEWED.jsonl",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: object required")
        rows.append(value)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    action_counts: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    top_level_keys: Counter[tuple[str, ...]] = Counter()
    turn_keys: Counter[tuple[str, ...]] = Counter()
    user_turns = 0
    model_outputs = 0
    groups: set[str] = set()

    for row in rows:
        label_counts[str(row.get("label_status"))] += 1
        groups.add(str(row.get("conversation_group_id")))
        top_level_keys[tuple(sorted(row))] += 1
        turns = row.get("turns")
        if not isinstance(turns, list):
            raise ValueError(f"{row.get('id')}: turns must be a list")
        user_turns += len(turns)
        for turn in turns:
            if not isinstance(turn, dict):
                raise ValueError(f"{row.get('id')}: turn must be an object")
            turn_keys[tuple(sorted(turn))] += 1
            outputs = turn.get("model_outputs")
            if not isinstance(outputs, list):
                raise ValueError(f"{row.get('id')}: model_outputs must be a list")
            model_outputs += len(outputs)
            for output in outputs:
                action_counts[str(output.get("action"))] += 1

    return {
        "conversations": len(rows),
        "conversationGroups": len(groups),
        "userTurns": user_turns,
        "modelOutputs": model_outputs,
        "labelStatus": dict(sorted(label_counts.items())),
        "actionOccurrences": dict(sorted(action_counts.items())),
        "topLevelKeyShapes": [
            {"keys": list(keys), "count": count}
            for keys, count in sorted(top_level_keys.items())
        ],
        "turnKeyShapes": [
            {"keys": list(keys), "count": count}
            for keys, count in sorted(turn_keys.items())
        ],
    }


def build_report() -> dict[str, Any]:
    all_rows = load_jsonl(ALL_PATH)
    split_rows = {name: load_jsonl(path) for name, path in SPLITS.items()}
    all_ids = [str(row.get("id")) for row in all_rows]
    split_ids = [str(row.get("id")) for rows in split_rows.values() for row in rows]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("duplicate case ID in all-400 file")
    if len(split_ids) != len(set(split_ids)):
        raise ValueError("case ID appears in more than one split")
    if set(all_ids) != set(split_ids):
        raise ValueError("all-400 and split case IDs differ")

    return {
        "schemaVersion": "issue30-existing-400-audit-v0.1",
        "status": "BE-PROPOSED",
        "source": {
            "datasetVersion": "qwen-dataset-400-v0.1.4-proposed",
            "contractFormat": "DATA v2.6 candidate A seven-action format",
            "allPath": str(ALL_PATH.relative_to(REPO_ROOT)),
            "allSha256": sha256(ALL_PATH),
        },
        "all": summarize(all_rows),
        "splits": {
            name: {
                **summarize(rows),
                "path": str(SPLITS[name].relative_to(REPO_ROOT)),
                "sha256": sha256(SPLITS[name]),
            }
            for name, rows in split_rows.items()
        },
        "reuseAssessment": {
            "userTextAndContext": "conditionally_reusable_after_contamination_and_semantic_review",
            "candidateNeutralSemanticOracle": "requires_conversion_and_review",
            "candidateCRoleEvidence": "requires_new_labeling_for_all_conversations",
            "candidateSpecificModelOutputs": "requires_reinspection_for_any_contract_other_than_A",
            "automaticDiscardCount": None,
            "independentReviewComplete": False,
            "testSplitLockedIndependent": False,
        },
        "notes": [
            "reviewed is the 2026-10-22 user-directed status, not independent multi-review",
            "the current dataset contract is not a TEAM-confirmed shared AI/DATA contract",
            "the previous validation-40 Qwen score is not a result for the current validation-50 split",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(build_report(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if args.check.read_text(encoding="utf-8") != rendered:
            raise SystemExit(f"audit mismatch: {args.check}")
        print(f"PASS: {args.check}")
        return 0
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
