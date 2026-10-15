"""Replay human-approved sources into chat training rows, without running an LLM."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import review_sources
import run_eval as evaluator

ROOT = Path(__file__).resolve().parent
FORMAT = "reviewed-chat-v1"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def schema_matches(value, schema):
    """Validate only the JSON Schema vocabulary emitted by schema_for; fail closed."""
    supported = {"anyOf", "type", "enum", "minLength", "maxLength", "properties",
                 "required", "additionalProperties", "minProperties"}
    if schema.keys() - supported:
        raise ValueError(f"Unsupported schema keywords: {schema.keys() - supported}")
    if "anyOf" in schema:
        return any(schema_matches(value, branch) for branch in schema["anyOf"])
    types = {"null": lambda v: v is None, "string": lambda v: isinstance(v, str),
             "object": lambda v: isinstance(v, dict), "integer": lambda v: type(v) is int,
             "boolean": lambda v: type(v) is bool}
    if "type" in schema and not types[schema["type"]](value):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, str) and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 10**9):
        return False
    if schema.get("type") == "object":
        props = schema.get("properties", {})
        if len(value) < schema.get("minProperties", 0) or set(schema.get("required", [])) - value.keys():
            return False
        if schema.get("additionalProperties") is False and value.keys() - props.keys():
            return False
        if any(not schema_matches(v, props[k]) for k, v in value.items() if k in props):
            return False
    return True


class ReviewedClient(evaluator.ReferenceClient):
    def generate(self, messages):
        self.last_schema = evaluator.schema_for(self.context)
        raw = super().generate(messages)
        if not schema_matches(json.loads(raw), self.last_schema):
            raise RuntimeError("Reviewed action violates the current context schema")
        return raw


def replay_rows(record):
    case = record["scenario"]
    client = ReviewedClient([action for turn in case["turns"] for action in turn["model_outputs"]])
    result = evaluator.run_case(case, client, {case["id"]: record["question_rubric"]})
    failed = [check["name"] for turn in result["turns"] for check in turn["checks"] if not check["match"]]
    failed += [check["name"] for check in result["final_checks"] if not check["match"]]
    if (not result["provisional_match"] or client.outputs
            or any(t["filter_events"] or t["guard_events"] or t["crash"] for t in result["turns"])):
        raise ValueError(f"{case['id']}: reviewed replay failed: {result['stop_reason']}; {failed}")
    for turn in result["turns"]:
        calls = turn["model_calls"]
        if len(calls) != len(turn["expected_model_outputs"]):
            raise ValueError(f"{case['id']}:{turn['index']}: model-call count differs from reviewed actions")
        for number, (call, expected) in enumerate(zip(calls, turn["expected_model_outputs"]), 1):
            if call["error"] or call["parse_error"] or json.loads(call["raw"]) != expected:
                raise ValueError(f"{case['id']}:{turn['index']}:{number}: invalid reviewed target")
            row = {"messages": call["messages"] + [{"role": "assistant", "content": call["raw"]}]}
            origin = {"case_id": case["id"], "conversation_group_id": case["conversation_group_id"],
                      "turn": turn["index"], "call": number, "label_status": "reviewed",
                      "source_sha256": sha256(record["path"]), "schema": call["schema"]}
            yield row, origin


def export_training(output_dir: Path, root: Path = ROOT):
    if output_dir.exists():
        raise FileExistsError(f"Output directory already exists: {output_dir}")
    records = review_sources.load_reviewed(root)
    approved = [r for r in records if r["scenario"]["label_status"] == "reviewed" and r["split"] != "test"]
    counts = Counter(r["split"] for r in approved)
    if not counts["train"] or not counts["validation"]:
        raise ValueError("Approve at least one train and one validation scenario first; no unreviewed fallback")
    rows = {"train": [], "valid": []}
    maps = {"train": [], "valid": []}
    seen = {"train": {}, "valid": {}}
    inputs = {}
    for record in approved:
        split = "valid" if record["split"] == "validation" else "train"
        for row, origin in replay_rows(record):
            input_hash = review_sources.fingerprint(row["messages"][:-1])
            target_hash = review_sources.fingerprint(json.loads(row["messages"][-1]["content"]))
            if input_hash in inputs:
                old_split, old_target = inputs[input_hash]
                if old_split != split:
                    raise ValueError(f"Identical model input crosses splits: {origin['case_id']}")
                if old_target != target_hash:
                    raise ValueError(f"Conflicting reviewed targets for the same input: {origin['case_id']}")
            inputs[input_hash] = (split, target_hash)
            key = review_sources.fingerprint(row)
            if key not in seen[split]:
                rows[split].append(row)
                seen[split][key] = len(rows[split])
            maps[split].append({"row": seen[split][key], "row_sha256": key, **origin})
    if any(not values for values in rows.values()):
        raise ValueError("Both train and validation must contain supervised model calls")
    # Validate everything before creating output. Never overwrite a previous export.
    output_dir.mkdir(parents=True, exist_ok=False)
    files = {}
    for split in rows:
        for name, values in ((f"{split}.jsonl", rows[split]), (f"{split}_row_map.jsonl", maps[split])):
            path = output_dir / name
            path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8")
            files[name] = {"rows": len(values), "sha256": sha256(path)}
    manifest = {
        "format": FORMAT, "status": "REVIEWED_TRAIN_VALID_ONLY", "files": files,
        "prompt_sha256": hashlib.sha256(evaluator.PROMPT.encode("utf-8")).hexdigest(),
        "scenario_counts": {split: counts[split] for split in ("train", "validation")},
        "excluded_test_sources": sum(r["split"] == "test" for r in records),
        "excluded_unapproved_sources": sum(r["scenario"]["label_status"] != "reviewed" for r in records),
        "sources": {r["scenario"]["id"]: {"split": r["split"], "sha256": sha256(r["path"])} for r in approved},
        "code_sha256": {str(p.relative_to(ROOT)): sha256(p) for p in
                        [Path(__file__), ROOT / "review_sources.py", ROOT / "run_eval.py", ROOT / "guards.py",
                         *sorted((ROOT / "runtime").rglob("*.py"))]},
        "note": "Scripted replay of human-approved targets; not model predictions or an accuracy measurement.",
    }
    review_sources.save_json(output_dir / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = export_training(args.output_dir.expanduser().resolve())
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(json.dumps({"scenarios": result["scenario_counts"], "files": result["files"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
