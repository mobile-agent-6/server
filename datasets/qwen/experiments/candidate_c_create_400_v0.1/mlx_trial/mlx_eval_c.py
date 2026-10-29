"""Evaluate Candidate C on Issue #30 locked cases through a local mlx_lm.server.

Run with the backend venv (the Issue #30 evaluator imports backend modules).
The model runs in a separate MLX venv behind a loopback HTTP endpoint.
Judgement reuses ollama_eval_v0_3.evaluate_case unchanged; only the client differs.
Writes only to --run-dir (outside the repository).  Raw model text is not
stored; evaluate_case records its hash and byte length.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ISSUE30 = ROOT.parent / "structured_output_contract_v0.1"
OLD_DATA_ROOT = ROOT.parent / "multiturn_400_v0.1"  # DATA v2.6 run_eval, same as validator.py
for path in (ISSUE30, OLD_DATA_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
sys.dont_write_bytecode = True

import ollama_eval as v1  # noqa: E402
import ollama_eval_v0_3 as v3  # noqa: E402

LOCKED_SHA256 = "17c7d4ef10aff8171d422c248431d3c34faaf03bf7282fab5dc3abcfa2389b57"
TEST50_LOCK = ROOT / "test50" / "lock-manifest-v0.1.json"
MAX_TOKENS = v1.NUM_PREDICT


class MlxServerClient:
    """Same chat() contract as v1.OllamaClient; no schema-constrained decoding."""

    def __init__(self, endpoint: str, timeout: int = 600) -> None:
        parsed = urllib.parse.urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("only loopback HTTP endpoints are allowed")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout

    def chat(self, messages: Sequence[Mapping[str, str]], schema: Mapping[str, Any]) -> v1.CallRecord:
        schema_hash = v1.sha256_bytes(v1.canonical_json(schema).encode("utf-8"))
        body = json.dumps({
            "messages": list(messages),
            "temperature": 0.0,
            "max_tokens": MAX_TOKENS,
            "seed": v1.SEED,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
                status = response.status
        except Exception:  # transport failure is recorded, never retried silently
            latency = (time.perf_counter() - started) * 1000
            return v1.CallRecord(None, None, latency, False, 0, "transport_error", schema_hash, None, None)
        latency = (time.perf_counter() - started) * 1000
        try:
            raw = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return v1.CallRecord(None, status, latency, False, 0, "missing_message_content", schema_hash, None, None)
        usage = payload.get("usage") or {}
        return v1.CallRecord(
            str(raw).strip(), status, latency, True, 0, None, schema_hash,
            usage.get("completion_tokens"), usage.get("prompt_tokens"),
        )


def subset(case: Mapping[str, Any]) -> str:
    kinds = {operation["kind"] for operation in case["semantics"]["operations"]}
    return "primary_create" if "create" in kinds else "regression_other"


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("primary_create", "regression_other", "all"):
        chosen = [row for row in rows if name == "all" or row["subset"] == name]
        outcomes = Counter(row["finalOutcome"] for row in chosen)
        result[name] = {
            "cases": len(chosen),
            "outcomes": dict(sorted(outcomes.items())),
            "correct": outcomes.get("correct", 0),
            "modelFreeCases": sum(row["modelCallCount"] == 0 for row in chosen),
            "unsafeAccept": outcomes.get("unsafe_accept", 0),
            "jsonParseFailures": sum(not row["jsonParseSuccess"] for row in chosen),
            "localValidationFailures": sum(row["localValidation"] not in {"accept", "not_run"} for row in chosen),
        }
    return result


def _test50_cases_and_dry_rows() -> tuple[list[dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    """Test 50 is selected by its lock manifest path and hash, never by datasetRole (same as locked56).

    Issue #30 dry rows exist only for locked56.  For Test 50 the gold Candidate C path through the
    production Decision stage is checked by validator TEST50_CONFIG (PASS required here), so a
    semantically correct model output maps to the expected backend outcome.
    """
    lock = json.loads(TEST50_LOCK.read_text(encoding="utf-8"))
    for item in lock["files"].values():
        path = ROOT / item["path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise SystemExit(f"Test 50 locked file changed: {item['path']}")
    sys.path.insert(0, str(ROOT))
    import validator  # noqa: PLC0415

    check = validator.run_validation(write_artifacts=False, config=validator.TEST50_CONFIG)
    if check["status"] != "PASS" or check["unsafeAccepts"] or check["contractGapCount"]:
        raise SystemExit("Test 50 gold path did not pass the validator")
    cases = v1.load_jsonl(ROOT / lock["files"]["conversations"]["path"])
    dry = {(case["meta"]["caseId"], "C"): {"mappingResult": "decision_envelope", "systemOutcome": "correct",
                                            "mismatchCause": None, "contractClassification": "representable"}
           for case in cases}
    return cases, dry


def run(client: Any, *, label: str, run_dir: Path, model_identity: Mapping[str, Any],
        case_set: str = "locked56") -> dict[str, Any]:
    if hashlib.sha256(v1.LOCKED_PATH.read_bytes()).hexdigest() != LOCKED_SHA256:
        raise SystemExit("locked cases changed")
    v1.assert_protected_integrity()
    if case_set == "locked56":
        cases, dry_rows, phase, suffix = v1.load_jsonl(v1.LOCKED_PATH), v1._dry_rows(), "locked", ""
    elif case_set == "test50":
        (cases, dry_rows), phase, suffix = _test50_cases_and_dry_rows(), "test50", "-test50"
    else:
        raise SystemExit(f"unknown case set {case_set!r}")
    label = label + suffix
    out = run_dir / f"eval-{label}.jsonl"
    if out.exists():
        raise SystemExit(f"{out} exists; use a new label instead of resuming")
    rows: list[dict[str, Any]] = []
    stopped = None
    with out.open("w", encoding="utf-8") as stream:
        for case in cases:
            row = v3.evaluate_case(case, "C", client, run_id=f"mlx-trial-{label}", phase=phase,
                                   model_digest=str(model_identity.get("modelRevision")), dry_rows=dry_rows)
            row = {**row, "recordType": "mlx_model_eval", "model": "mlx-community/Qwen3-4B-4bit",
                   "runtime": "mlx_lm.server", "label": label, "subset": subset(case)}
            rows.append(row)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            if row["unsafeAccept"]:
                stopped = row["caseId"]
                break
    summary = {"label": label, "caseSet": case_set, "stoppedOnUnsafe": stopped, "evaluatedCases": len(rows),
               "modelIdentity": dict(model_identity), "summary": summarize(rows),
               "resultsSha256": hashlib.sha256(out.read_bytes()).hexdigest()}
    (run_dir / f"eval-{label}-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8088")
    parser.add_argument("--label", required=True, choices=["base", "adapter"])
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True, help="JSON with model revision and adapter hash")
    parser.add_argument("--cases", choices=["locked56", "test50"], default="locked56")
    args = parser.parse_args()
    identity = json.loads(args.identity.read_text(encoding="utf-8"))
    summary = run(MlxServerClient(args.endpoint), label=args.label,
                  run_dir=args.run_dir.expanduser().resolve(), model_identity=identity, case_set=args.cases)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["stoppedOnUnsafe"]:
        raise SystemExit(f"STOP: unsafe_accept at {summary['stoppedOnUnsafe']}")


if __name__ == "__main__":
    main()
