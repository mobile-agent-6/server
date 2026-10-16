"""Compare two completed DATA-prototype conversation runs under identical inputs.

This compares saved actual-model runs; it does not call a model or certify a
human-reviewed test set as a locked benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


SAME_INPUT = ("tool_version", "ollama_version", "options", "think",
              "prompt", "case_ids", "file_sha256")
METRICS = ("provisional_match", "raw_action_path_exact", "raw_json_path_exact")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_run(directory: Path, allow_unreviewed: bool):
    manifest_path = directory / "manifest.json"
    manifest = read_json(manifest_path)
    summary = read_json(directory / "summary.json")
    cases = read_jsonl(directory / "review_cases.jsonl")
    if manifest.get("source_kind") != "actual_model" or summary.get("source_kind") != "actual_model":
        raise ValueError(f"{directory}: scripted reference is not a model run")
    ids = manifest.get("case_ids")
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise ValueError(f"{directory}: invalid case list")
    if [case.get("case_id") for case in cases] != ids:
        raise ValueError(f"{directory}: missing, duplicate, or reordered case results")
    if (summary.get("run_id") != manifest.get("run_id")
            or summary.get("cases_run") != len(cases)
            or any(case.get("run_id") != manifest.get("run_id")
                   or case.get("source_kind") != "actual_model" for case in cases)):
        raise ValueError(f"{directory}: run identity or result count differs")
    label_counts = dict(Counter(case.get("label_status") for case in cases))
    if manifest.get("label_status_counts") != label_counts or summary.get("label_status_counts") != label_counts:
        raise ValueError(f"{directory}: review-state counts differ")
    if not allow_unreviewed and any(case.get("label_status") != "reviewed" for case in cases):
        raise ValueError(f"{directory}: unreviewed labels require --allow-unreviewed")
    if any(any(type(case.get(key)) is not bool for key in METRICS) for case in cases):
        raise ValueError(f"{directory}: run lacks separate raw and guarded metrics")
    for key, field in (("provisional_matches", "provisional_match"),
                       ("raw_action_exact_dialogues", "raw_action_path_exact"),
                       ("raw_json_exact_dialogues", "raw_json_path_exact")):
        if summary.get(key) != sum(case[field] for case in cases):
            raise ValueError(f"{directory}: inconsistent {key}")
    identity = manifest.get("model_metadata")
    if not isinstance(identity, list) or len(identity) != 1 or not identity[0].get("digest"):
        raise ValueError(f"{directory}: Ollama model digest is required for a paired comparison")
    return manifest, cases, sha256(manifest_path)


def compare(before_dir: Path, after_dir: Path, allow_unreviewed: bool = False):
    before, first, before_hash = load_run(before_dir, allow_unreviewed)
    after, second, after_hash = load_run(after_dir, allow_unreviewed)
    for field in SAME_INPUT:
        if field not in before or before[field] != after.get(field):
            raise ValueError(f"Different evaluation input or code: {field}")
    if [case.get("label_status") for case in first] != [case.get("label_status") for case in second]:
        raise ValueError("Case review states differ")
    if before["model_metadata"][0]["digest"] == after["model_metadata"][0]["digest"]:
        raise ValueError("Before and after use the same Ollama model digest")
    metrics = {}
    for name in METRICS:
        a = [case[name] for case in first]
        b = [case[name] for case in second]
        metrics[name] = {
            "before": sum(a), "after": sum(b),
            "improved": sum(not x and y for x, y in zip(a, b)),
            "regressed": sum(x and not y for x, y in zip(a, b)),
            "unchanged_pass": sum(x and y for x, y in zip(a, b)),
            "unchanged_fail": sum(not x and not y for x, y in zip(a, b)),
        }
    return {
        "status": ("reviewed_provisional_not_locked_test"
                   if all(case["label_status"] == "reviewed" for case in first)
                   else "exploratory_unreviewed"),
        "scope": "Paired full-conversation DATA prototype with identical input, code, and generation settings",
        "cases": len(first),
        "before": {"run_id": before["run_id"], "model": before["model"],
                   "model_digest": before["model_metadata"][0]["digest"],
                   "manifest_sha256": before_hash},
        "after": {"run_id": after["run_id"], "model": after["model"],
                  "model_digest": after["model_metadata"][0]["digest"],
                  "manifest_sha256": after_hash},
        "metrics": metrics,
        "case_results": [
            {"case_id": old["case_id"],
             **{key: {"before": old[key], "after": new[key]} for key in METRICS}}
            for old, new in zip(first, second)
        ],
        "limitations": "Review approval, test locking, real backend, speech, and device performance require separate evidence.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True, help="run_eval.py output directory")
    parser.add_argument("--after", type=Path, required=True, help="run_eval.py output directory")
    parser.add_argument("--output", type=Path, required=True, help="new JSON report path")
    parser.add_argument("--allow-unreviewed", action="store_true",
                        help="development diagnostics only; labels remain unreviewed")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; preserve the previous comparison")
    try:
        report = compare(args.before, args.after, args.allow_unreviewed)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "cases", "metrics")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
