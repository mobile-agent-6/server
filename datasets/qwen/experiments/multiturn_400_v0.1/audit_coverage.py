"""Report evaluation coverage and review status without approving any labels."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from review_sources import load_reviewed
from runtime.replay_multiturn_pilot import load_cases

ROOT = Path(__file__).resolve().parent


def coverage(cases: list[dict]) -> dict[str, int]:
    kinds = Counter(turn["expect"]["reply_kind"] for case in cases for turn in case["turns"])
    methods = Counter(fault["method"] for case in cases for fault in case.get("faults", []))
    return {
        "cases": len(cases),
        "select_target_cases": sum(any(action["action"] == "SELECT_TARGET"
                                       for turn in case["turns"]
                                       for action in turn["model_outputs"]) for case in cases),
        "error_turns": kinds["error"],
        "stale_turns": kinds["stale"],
        "fault_cases": sum(bool(case.get("faults")) for case in cases),
        **{f"fault_{method.lower()}": methods[method]
           for method in ("GET", "POST", "PATCH", "DELETE")},
    }


def report(root: Path = ROOT) -> dict:
    splits = {split: load_cases(root / split / "scenarios_UNREVIEWED.jsonl")
              for split in ("train", "validation", "test")}
    diagnostics = load_cases(root / "diagnostic" / "scenarios_UNREVIEWED.jsonl")
    reviewed = load_reviewed(root)
    approved = Counter(record["split"] for record in reviewed
                       if record["scenario"]["label_status"] == "reviewed")
    data = {split: coverage(cases) for split, cases in splits.items()}
    data["diagnostic"] = coverage(diagnostics)
    gaps = []
    if data["validation"]["select_target_cases"] == 0:
        gaps.append("validation has no SELECT_TARGET case")
    if data["validation"]["error_turns"] + data["validation"]["stale_turns"] == 0:
        gaps.append("validation has no error or stale response")
    if data["validation"]["fault_cases"] == 0:
        gaps.append("validation has no injected API failure")
    if approved["test"] != len(splits["test"]):
        gaps.append(f"test review incomplete: {approved['test']}/{len(splits['test'])}")
    return {"status": "data_proposal_not_formal_score", "coverage": data,
            "reviewed_sources": {split: approved[split] for split in splits},
            "gaps": gaps}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-complete", action="store_true",
                        help="exit unsuccessfully while listed coverage or review gaps remain")
    args = parser.parse_args()
    try:
        result = report()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_complete and result["gaps"]:
        parser.exit(1, "Evaluation coverage and review are incomplete.\n")


if __name__ == "__main__":
    main()
