"""Run provisional multiturn cases with a real local Ollama model and mock schedules."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "ai"))

from schedule_assistant.actions import ActionError, parse_action  # noqa: E402
from schedule_assistant.clients import OllamaClient  # noqa: E402
from schedule_assistant.service import ConversationService, PROMPT  # noqa: E402

from replay_multiturn_pilot import MemorySchedules, load_cases  # noqa: E402


VERSION = "pilot-eval-v0.1"
WRITES = {"POST", "PATCH", "DELETE"}


class RecordingModel:
    def __init__(self, client: Any):
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def generate(self, messages: list[dict[str, str]]) -> str:
        started = time.perf_counter()
        call: dict[str, Any] = {"messages": messages, "raw": None, "action": None,
                                "parse_error": None, "error": None, "metrics": {}}
        try:
            raw = self.client.generate(messages)
            call["raw"] = raw
            metrics = getattr(self.client, "last_metrics", {})
            if isinstance(metrics, dict):
                call["metrics"] = dict(metrics)
            try:
                call["action"] = parse_action(raw).name
            except (ActionError, ValueError, TypeError) as exc:
                call["parse_error"] = str(exc)
            return raw
        except RuntimeError as exc:
            call["error"] = str(exc)
            raise
        finally:
            call["wall_time_ms"] = round((time.perf_counter() - started) * 1000, 1)
            self.calls.append(call)


def _check(name: str, expected: Any, actual: Any) -> dict[str, Any]:
    return {"name": name, "expected": expected, "actual": actual, "match": expected == actual}


def _final_checks(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks = [_check("final_ids", sorted(item["id"] for item in expected),
                     sorted(item["id"] for item in actual))]
    by_id = {item["id"]: item for item in actual}
    for item in expected:
        observed = by_id.get(item["id"], {})
        for key, value in item.items():
            if key != "id":
                checks.append(_check(f"final:{item['id']}:{key}", value, observed.get(key)))
    return checks


def run_case(case: dict[str, Any], client: Any) -> dict[str, Any]:
    """Execute one case. A reply-kind branch mismatch stops scripted follow-up turns."""
    schedules = MemorySchedules(case["initial_schedules"], case.get("created_id"))
    model = RecordingModel(client)
    now = datetime.fromisoformat(case["reference_datetime"])
    service = ConversationService(model, schedules, lambda: now)
    turns: list[dict[str, Any]] = []
    for index, turn in enumerate(case["turns"], 1):
        call_start, model_start = len(schedules.calls), len(model.calls)
        started = time.perf_counter()
        crash = None
        try:
            reply = service.handle(turn["user"])
        except Exception as exc:
            # Preserve partial traces; a controller crash is an evaluation finding.
            crash = f"{type(exc).__name__}: {exc}"
            reply = None
        calls = schedules.calls[call_start:]
        writes = [call for call in calls if call["method"] in WRITES]
        expected = turn["expect"]
        kind = reply.kind if reply else None
        message = reply.message if reply else None
        checks = [_check("reply_kind", expected["reply_kind"], kind),
                  _check("write_attempts", expected["writes"], len(writes))]
        for phrase in expected.get("reply_contains", []):
            checks.append(_check("reply_contains", True, phrase in (message or "")))
        if "api_methods" in expected:
            checks.append(_check("api_methods", expected["api_methods"],
                                 [call["method"] for call in calls]))
        if "post_body" in expected:
            checks.append(_check("post_body", True, any(call["method"] == "POST" and
                                 call.get("body") == expected["post_body"] for call in calls)))
        if "target_id" in expected:
            checks.append(_check("write_target", True, all(call.get("id") == expected["target_id"]
                                                           for call in writes)))
        turns.append({"index": index, "user": turn["user"], "expected": expected,
                      "expected_model_outputs": turn["model_outputs"],
                      "reply": {"kind": kind, "message": message},
                      "model_calls": model.calls[model_start:], "api_calls": calls,
                      "wall_time_ms": round((time.perf_counter() - started) * 1000, 1),
                      "checks": checks, "crash": crash})
        if kind != expected["reply_kind"]:
            break
    completed = len(turns) == len(case["turns"])
    final_items = list(schedules.items.values())
    final_checks = _final_checks(case["expect_final"], final_items) if completed else []
    all_checks = [check for turn in turns for check in turn["checks"]] + final_checks
    matched = completed and all(check["match"] for check in all_checks)
    return {"case_id": case["id"], "conversation_group_id": case["conversation_group_id"],
            "label_status": case["label_status"], "reference_datetime": case["reference_datetime"],
            "initial_schedules": case["initial_schedules"], "expected_final": case["expect_final"],
            "actual_final": final_items, "turns": turns, "final_checks": final_checks,
            "completed_turns": len(turns), "total_turns": len(case["turns"]),
            "provisional_match": matched, "status": "provisional_match" if matched else "needs_review"}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values),
                    encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", type=Path, required=True)
    parser.add_argument("--model", required=True, help="installed local Ollama model name")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="new local directory for logs; runs/ is Git-ignored")
    args = parser.parse_args()
    cases = load_cases(args.scenarios)
    client = OllamaClient(args.model, args.ollama_url)
    if args.output_dir.exists():
        parser.error("output directory already exists; choose a new run directory")
    args.output_dir.mkdir(parents=True)
    run_id = str(uuid4())
    manifest = {"tool_version": VERSION, "run_id": run_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "model": args.model, "ollama_url": args.ollama_url,
                "scenario_file": str(args.scenarios),
                "scenario_sha256": hashlib.sha256(args.scenarios.read_bytes()).hexdigest(),
                "prompt_sha256": hashlib.sha256(PROMPT.encode("utf-8")).hexdigest(),
                "case_count": len(cases), "status": "provisional_unreviewed",
                "note": "Exact pilot expectations are provisional; this is not a formal model score."}
    _write_json(args.output_dir / "manifest.json", manifest)
    results: list[dict[str, Any]] = []
    for case in cases:
        result = run_case(case, client)
        result["run_id"] = run_id
        results.append(result)
        print(f"{case['id']}: {result['status']} ({result['completed_turns']}/{result['total_turns']} turns)",
              flush=True)
        # Keep partial progress if a later Ollama call or process fails.
        _write_jsonl(args.output_dir / "review_cases.jsonl", results)
        _write_jsonl(args.output_dir / "turn_results.jsonl", [
            {"run_id": run_id, "case_id": item["case_id"], **turn}
            for item in results for turn in item["turns"]])
    summary = {"run_id": run_id, "status": "provisional_unreviewed", "cases_run": len(results),
               "provisional_matches": sum(item["provisional_match"] for item in results),
               "needs_review": sum(not item["provisional_match"] for item in results),
               "note": "Counts show matches to unreviewed pilot expectations, not Qwen performance."}
    _write_json(args.output_dir / "summary.json", summary)
    print(f"Saved provisional results to {args.output_dir}")


if __name__ == "__main__":
    main()
