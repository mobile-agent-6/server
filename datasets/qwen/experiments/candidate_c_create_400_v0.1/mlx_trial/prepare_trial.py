"""Prepare the batch 1 Candidate-C MLX LoRA trial in a new run directory.

Writes only outside the repository (--output).  Checks that batch 1 is the
reviewed revision, splits validation by family, verifies the final-assistant
mask and token lengths, and writes the mlx_lm.lora config.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BATCHES = ("batch1", "batch2", "batch3", "batch4", "batch5")
CONVERSATIONS = ROOT / "batch1" / "conversations-v0.1.jsonl"  # kept for tests on batch 1 alone
PREREG = HERE / "preregistration-v0.7.json"
KEY_ORDERS = ("canonical", "missingInformation_last", "schema")
# Issue #30 candidate_c_schema property order (test_mlx_trial checks it against the schema).
SCHEMA_ORDER = {
    "root": ("operations", "unsupportedEvidence", "missingInformation"),
    "operation": ("index", "kind", "targetEvidence", "changeEvidence", "filterEvidence",
                  "unsupportedEvidence", "missingInformation"),
    "roles": ("title", "date", "time", "category", "completedState", "completed", "futureScope"),
    "span": ("turn", "text", "status", "occurrence"),
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def notes(case: dict) -> dict[str, str]:
    return dict(item.split("=", 1) for item in case["meta"]["notes"].split(";") if "=" in item)


def check_reviewed_inputs() -> dict:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    for name, expected in prereg["trainingData"]["batches"].items():
        folder = ROOT / name
        actual = sha256_bytes((folder / "conversations-v0.1.jsonl").read_bytes())
        if actual != expected:
            raise SystemExit(f"{name} conversations changed since review: {actual}")
        final: dict[str, dict] = {}
        for event in sorted(load_jsonl(folder / "review-events-v0.1.jsonl"), key=lambda e: e["round"]):
            final[event["caseId"]] = event
        case_ids = {case["meta"]["caseId"] for case in load_jsonl(folder / "conversations-v0.1.jsonl")}
        if set(final) != case_ids or any(e["verdict"] != "ACCEPT" for e in final.values()):
            raise SystemExit(f"{name}: final review round must ACCEPT all {len(case_ids)} cases")
        if any(e["inputSha256"]["conversations"] != expected for e in final.values()):
            raise SystemExit(f"{name}: review events do not cover the current conversations file")
    return prereg


def skill(case: dict) -> tuple:
    """What a case teaches: bucket, missing item kinds, and whether a follow-up resolves them."""
    missing = case["semantics"]["operations"][0].get("missingInformation", [])
    return (
        notes(case)["bucket"],
        tuple(sorted({item["item"] for item in missing})),
        any(item.get("resolvedAtTurn") is not None for item in missing),
    )


def choose_valid_families(cases: dict, target_conversations: int | None = None) -> list[str]:
    """Hold out whole families only if training keeps every skill they carry.

    Without a target: one family per bucket (runs 001 to 004).  With a target: walk the buckets
    round-robin, each in sha256(familyId) order, until the held-out conversations reach the target.
    """
    families_by_bucket: dict[str, set[str]] = defaultdict(set)
    skills_of: dict[str, set[tuple]] = defaultdict(set)
    size_of: dict[str, int] = defaultdict(int)
    for case in cases.values():
        info = notes(case)
        families_by_bucket[info["bucket"]].add(info["familyId"])
        skills_of[info["familyId"]].add(skill(case))
        size_of[info["familyId"]] += 1
    ordered = {bucket: sorted(families, key=lambda name: sha256_bytes(name.encode("utf-8")))
               for bucket, families in sorted(families_by_bucket.items())}
    chosen: list[str] = []

    def can_hold_out(family: str) -> bool:
        kept = set().union(*(skills_of[f] for f in skills_of if f != family and f not in chosen))
        return family not in chosen and skills_of[family] <= kept

    if target_conversations is None:
        for families in families_by_bucket.values():
            for family in sorted(families, key=lambda name: sha256_bytes(name.encode("utf-8"))):
                if can_hold_out(family):
                    chosen.append(family)
                    break
        return sorted(chosen)
    progress = True
    while sum(size_of[f] for f in chosen) < target_conversations and progress:
        progress = False
        for bucket, families in ordered.items():
            if sum(size_of[f] for f in chosen) >= target_conversations:
                break
            for family in families:
                if can_hold_out(family):
                    chosen.append(family)
                    progress = True
                    break
    return sorted(chosen)


def _missing_last(value):
    if isinstance(value, dict):
        keys = [k for k in value if k != "missingInformation"] + (["missingInformation"] if "missingInformation" in value else [])
        return {k: _missing_last(value[k]) for k in keys}
    if isinstance(value, list):
        return [_missing_last(item) for item in value]
    return value


def _schema_ordered(value, level: str = "root"):
    if level == "span":
        unknown = set(value) - set(SCHEMA_ORDER["span"])
        assert not unknown, unknown
        return {k: value[k] for k in SCHEMA_ORDER["span"] if k in value}
    keys = SCHEMA_ORDER[level]
    unknown = set(value) - set(keys)
    assert not unknown, (level, unknown)
    out = {}
    for key in keys:
        if key not in value:
            continue
        item = value[key]
        if key == "operations":
            out[key] = [_schema_ordered(op, "operation") for op in item]
        elif key in {"targetEvidence", "changeEvidence", "filterEvidence"}:
            assert not set(item) - set(SCHEMA_ORDER["roles"]), set(item)
            out[key] = {role: [_schema_ordered(span, "span") for span in item[role]]
                        for role in SCHEMA_ORDER["roles"] if role in item}
        elif key == "unsupportedEvidence":
            out[key] = [_schema_ordered(span, "span") for span in item]
        else:
            out[key] = item
    return out


def order_target(content: str, key_order: str) -> str:
    """Serialize the assistant target so evidence comes before missingInformation (same parsed JSON).

    Run 003 and probe A showed the canonical (alphabetical) order makes the model decide
    missingInformation before writing any evidence; probe B with this order asked 33/38.
    """
    if key_order == "canonical":
        return content
    if key_order not in KEY_ORDERS:
        raise SystemExit(f"unknown targetKeyOrder {key_order!r}")
    target = json.loads(content)
    reorder = _missing_last if key_order == "missingInformation_last" else _schema_ordered
    ordered = json.dumps(reorder(target), ensure_ascii=False, separators=(",", ":"))
    assert json.loads(ordered) == target
    return ordered


def split_rows(batches: tuple[str, ...] = BATCHES, key_order: str = "canonical",
               validation_conversations: int | None = None) -> tuple[list[dict], list[dict], list[str]]:
    cases = {c["meta"]["caseId"]: c for name in batches for c in load_jsonl(ROOT / name / "conversations-v0.1.jsonl")}
    family_of = {case_id: notes(case)["familyId"] for case_id, case in cases.items()}
    valid_families = choose_valid_families(cases, validation_conversations)
    train, valid = [], []
    preview = [row for name in batches for row in load_jsonl(ROOT / name / "training-row-preview-v0.1.jsonl")]
    for row in preview:
        messages = [dict(message) for message in row["messages"]]
        messages[-1]["content"] = order_target(messages[-1]["content"], key_order)
        item = {"messages": messages}
        (valid if family_of[row["caseId"]] in valid_families else train).append(item)
    return train, valid, valid_families


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new directory outside the repo")
    args = parser.parse_args()
    prereg = check_reviewed_inputs()
    train, valid, valid_families = split_rows(
        key_order=prereg["training"].get("targetKeyOrder", "canonical"),
        validation_conversations=prereg["trainingData"].get("validationConversations"))

    from mlx_lm.tuner.datasets import ChatDataset
    from transformers import AutoTokenizer
    import yaml

    model = args.model.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if ROOT.parents[3] in output.parents:
        raise SystemExit("output must be outside the repository")
    output.mkdir(parents=True, exist_ok=False)

    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True)
    tokenizer.chat_template = "{% set enable_thinking = false %}" + tokenizer.chat_template
    tokenizer_dir = output / "tokenizer"
    tokenizer.save_pretrained(tokenizer_dir)
    view = output / "model"
    view.mkdir()
    for path in model.iterdir():
        if path.is_file() and not (tokenizer_dir / path.name).exists():
            (view / path.name).symlink_to(path)
    for path in tokenizer_dir.iterdir():
        (view / path.name).symlink_to(path)
    tokenizer = AutoTokenizer.from_pretrained(view, local_files_only=True)

    stats = {}
    maximum = 0
    for split, rows in (("train", train), ("valid", valid)):
        dataset = ChatDataset(rows, tokenizer, mask_prompt=True)
        lengths = []
        for index, row in enumerate(rows):
            messages = row["messages"]
            assert [m["role"] for m in messages] == ["system", "user", "assistant"], (split, index)
            json.loads(messages[-1]["content"])
            tokens, offset = dataset.process(row)
            prompt = tokenizer.apply_chat_template(messages[:-1], add_generation_prompt=True, return_dict=False)
            assert tokens[:offset] == prompt, ("mask prefix mismatch", split, index)
            supervised = tokenizer.decode(tokens[offset:], skip_special_tokens=True).strip()
            assert supervised == messages[-1]["content"].strip(), ("unexpected supervised text", split, index)
            lengths.append(len(tokens))
        content = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")
        (output / f"{split}.jsonl").write_bytes(content)
        maximum = max(maximum, max(lengths))
        stats[split] = {"rows": len(rows), "minTokens": min(lengths), "maxTokens": max(lengths),
                        "meanTokens": round(sum(lengths) / len(lengths)), "sha256": sha256_bytes(content)}
    limit = ((maximum + 255) // 256) * 256
    assert limit <= 8192, limit
    training = prereg["training"]
    settings = {
        "model": str(view), "data": str(output), "train": True, "fine_tune_type": "lora",
        "mask_prompt": True, "batch_size": training["batchSize"], "num_layers": training["numLayers"],
        "lora_parameters": {"rank": training["loraRank"], "scale": 20.0, "dropout": 0.0},
        "learning_rate": training["learningRate"], "iters": round(training.get("epochs", 2) * len(train)),
        "max_seq_length": limit, "grad_checkpoint": True, "steps_per_report": 10,
        "steps_per_eval": training.get("evalEvery", training["evalAndSaveEvery"]), "val_batches": -1,
        "save_every": training["evalAndSaveEvery"], "seed": training["seed"],
        "adapter_path": str(output / "adapters"),
    }
    (output / "trial.yaml").write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    inspection = {"status": "PREPARED", "maskCheck": "PASS", "maxSeqLength": limit,
                  "iterations": settings["iters"], "validationFamilies": valid_families,
                  "splits": stats, "batches": prereg["trainingData"]["batches"],
                  "targetKeyOrder": prereg["training"].get("targetKeyOrder", "canonical")}
    (output / "inspection.json").write_text(json.dumps(inspection, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(inspection, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
