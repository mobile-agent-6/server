"""Keep human-reviewed scenario sources separate from generated draft files.

The review source files are never written by build_dataset.py. This tool creates
one draft review file exclusively, validates edited files, and exports only
approved scenarios to a new, local output directory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from runtime.replay_multiturn_pilot import ActionError, load_cases, replay, validate_scenario  # noqa: E402


SOURCE_VERSION = "review-source-v0.1"
SPLITS = ("train", "validation", "test")
QUESTION_TYPES = {"selection", "change", "ampm", "date", "task", "category", "time", "past_time"}
CASE_ID = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


def save_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_drafts(root: Path = ROOT) -> tuple[dict[str, tuple[str, dict[str, Any]]], dict[str, Any]]:
    drafts: dict[str, tuple[str, dict[str, Any]]] = {}
    groups: dict[str, str] = {}
    for split in SPLITS:
        for case in load_cases(root / split / "scenarios_UNREVIEWED.jsonl"):
            case_id = case["id"]
            if case_id in drafts:
                raise ValueError(f"duplicate draft case ID: {case_id}")
            group = case["conversation_group_id"]
            if group in groups and groups[group] != split:
                raise ValueError(f"conversation group crosses splits: {group}")
            groups[group] = split
            drafts[case_id] = (split, case)
    rubric = json.loads((root / "question_rubric.json").read_text(encoding="utf-8"))
    if not isinstance(rubric, dict):
        raise ValueError("question rubric must be an object")
    return drafts, rubric


def start_review(case_id: str, root: Path = ROOT) -> Path:
    if not CASE_ID.fullmatch(case_id):
        raise ValueError("invalid case ID")
    drafts, rubric = load_drafts(root)
    if case_id not in drafts:
        raise ValueError(f"unknown case ID: {case_id}")
    _, draft = drafts[case_id]
    source_dir = root / "reviewed"
    source_dir.mkdir(exist_ok=True)
    path = source_dir / f"{case_id}.json"
    record = {"source_version": SOURCE_VERSION, "draft_sha256": fingerprint(draft),
              "scenario": copy.deepcopy(draft),
              "question_rubric": copy.deepcopy(rubric.get(case_id, {}))}
    # Exclusive creation protects a reviewer's edits from accidental re-runs.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path


def _validate_rubric(case: dict[str, Any], rubric: Any) -> None:
    if not isinstance(rubric, dict):
        raise ValueError("question_rubric must be an object")
    needed = {str(index) for index, turn in enumerate(case["turns"], 1)
              if turn["expect"]["reply_kind"] == "ask"}
    if set(rubric) != needed:
        raise ValueError(f"question_rubric needs exactly the ASK turns {sorted(needed)}")
    if any(kind not in QUESTION_TYPES for kind in rubric.values()):
        raise ValueError("unknown question type")


def load_reviewed(root: Path = ROOT) -> list[dict[str, Any]]:
    drafts, _ = load_drafts(root)
    records: list[dict[str, Any]] = []
    for path in sorted((root / "reviewed").glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or set(record) != {
                "source_version", "draft_sha256", "scenario", "question_rubric"
            } or record["source_version"] != SOURCE_VERSION:
                raise ValueError("invalid review source fields or version")
            case = validate_scenario(record["scenario"])
            case_id = case["id"]
            if path.stem != case_id or case_id not in drafts:
                raise ValueError("file name and known case ID must match")
            split, draft = drafts[case_id]
            if record["draft_sha256"] != fingerprint(draft):
                raise ValueError("draft changed after review started; reconcile this source manually")
            if case["conversation_group_id"] != draft["conversation_group_id"]:
                raise ValueError("conversation group cannot change in a review source")
            _validate_rubric(case, record["question_rubric"])
            if case["label_status"] == "reviewed":
                replay(case)
            records.append({"split": split, "path": path, **record})
        except (ActionError, ValueError, TypeError, KeyError, AssertionError) as exc:
            raise ValueError(f"{path}: {exc}") from exc
    return records


def export_reviewed(output_dir: Path, root: Path = ROOT) -> dict[str, int]:
    records = load_reviewed(root)
    approved = [record for record in records if record["scenario"]["label_status"] == "reviewed"]
    if not approved:
        raise ValueError("no reviewed scenarios to export")
    if output_dir.exists():
        raise FileExistsError(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    counts = Counter(record["split"] for record in approved)
    rubric: dict[str, Any] = {}
    for split in SPLITS:
        cases = [record["scenario"] for record in approved if record["split"] == split]
        destination = output_dir / split
        destination.mkdir()
        (destination / "scenarios_REVIEWED.jsonl").write_text(
            "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases), encoding="utf-8")
    for record in approved:
        rubric[record["scenario"]["id"]] = record["question_rubric"]
    save_json(output_dir / "question_rubric_REVIEWED.json", rubric)
    save_json(output_dir / "manifest.json", {
        "source_version": SOURCE_VERSION,
        "status": "reviewed_sources_only_not_locked_test",
        "counts": {split: counts[split] for split in SPLITS},
        "sources": {record["scenario"]["id"]: {
            "split": record["split"], "sha256": hashlib.sha256(record["path"].read_bytes()).hexdigest()
        } for record in approved},
    })
    return {split: counts[split] for split in SPLITS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="create one review source without overwriting it")
    start.add_argument("--case", required=True)
    commands.add_parser("check", help="validate all review sources; do not change files")
    export = commands.add_parser("export", help="export only reviewed cases to a new local directory")
    export.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "start":
            print(start_review(args.case))
        elif args.command == "check":
            records = load_reviewed()
            counts = Counter(record["scenario"]["label_status"] for record in records)
            print(f"review sources: {len(records)}; reviewed: {counts['reviewed']}; "
                  f"unreviewed: {counts['proposed_unreviewed']}; rejected: {counts['rejected']}")
        else:
            print(export_reviewed(args.output_dir))
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
