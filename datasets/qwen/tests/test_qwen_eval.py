"""Regression checks for archived results and strict output handling."""

from __future__ import annotations

import sys
import json
import importlib.util
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

QWEN_ROOT = Path(__file__).resolve().parents[1]
LEGACY = QWEN_ROOT / "v0.1_initial"
sys.path.insert(0, str(QWEN_ROOT / "tools"))

import qwen_eval  # noqa: E402
import run_legacy_eval  # noqa: E402


class QwenEvaluationTests(unittest.TestCase):
    def test_archived_results_keep_original_and_strict_counts_separate(self) -> None:
        cases = qwen_eval.load_cases(LEGACY / "qwen_legacy_eval_100.jsonl")
        responses = qwen_eval.load_legacy_csv(
            LEGACY / "qwen_100_scored_results.csv", cases
        )
        report = qwen_eval.score(cases, responses)

        self.assertEqual(report["total"], 100)
        self.assertEqual(report["valid_json"], 100)
        self.assertEqual(report["valid_format"], 64)
        self.assertEqual(report["string_null_rows"], 35)
        self.assertEqual(report["legacy_normalized_full"], 16)
        self.assertEqual(report["exact_full"], 12)
        self.assertEqual(report["speed"]["timed_count"], 0)

    def test_missing_field_and_string_null_do_not_pass_strict_scoring(self) -> None:
        expected = {
            "intent": "read", "date": None, "time": None,
            "location": None, "category": None, "content": None,
        }
        cases = [{"id": "one", "expected": expected}]
        responses = {"one": {"raw_response": '{"intent":"read","date":"null","time":null,"location":null,"category":null}'}}
        report = qwen_eval.score(cases, responses)

        self.assertEqual(report["exact_full"], 0)
        self.assertEqual(report["legacy_normalized_full"], 1)
        self.assertEqual(report["valid_format"], 0)
        self.assertIn("missing:content", report["cases"][0]["issues"])
        self.assertIn("string_null:date", report["cases"][0]["issues"])

    def test_invalid_calendar_date_and_time_are_rejected(self) -> None:
        actual = {
            "intent": "create", "date": "2026-02-30", "time": "25:61",
            "location": None, "category": "반품", "content": "확인",
        }
        self.assertIn("value:date", qwen_eval.format_issues(actual))
        self.assertIn("format:time", qwen_eval.format_issues(actual))

    def test_response_jsonl_requires_one_known_id_per_case(self) -> None:
        cases = qwen_eval.load_cases(LEGACY / "qwen_legacy_eval_100.jsonl")
        archived = qwen_eval.load_legacy_csv(
            LEGACY / "qwen_100_scored_results.csv", cases
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "responses.jsonl"
            with path.open("w", encoding="utf-8") as stream:
                for case in cases:
                    stream.write(json.dumps({
                        "id": case["id"],
                        "raw_response": archived[case["id"]]["raw_response"],
                    }, ensure_ascii=False) + "\n")
            loaded = qwen_eval.load_responses(path, cases)
            self.assertEqual(qwen_eval.score(cases, loaded)["exact_full"], 12)

            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "id": cases[0]["id"], "raw_response": "{}",
                }) + "\n")
            with self.assertRaisesRegex(ValueError, "duplicate response id"):
                qwen_eval.load_responses(path, cases)

    def test_runner_records_wall_time_and_ollama_metrics(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps({
                    "response": " {} ", "eval_count": 20,
                    "eval_duration": 2_000_000_000,
                    "load_duration": 500_000_000,
                }).encode("utf-8")

        times = iter((0, 3_000_000_000))
        def opener(request, timeout):
            self.assertEqual(timeout, 10)
            self.assertIn(b'"stream": false', request.data)
            return Response()

        result = run_legacy_eval.request_one(
            "prompt", "qwen3:4b", "http://127.0.0.1:11434", 10,
            opener=opener, clock=lambda: next(times),
        )
        self.assertEqual(result["raw_response"], "{}")
        self.assertEqual(result["wall_time_ms"], 3000)
        self.assertEqual(result["ollama_metrics"]["eval_count"], 20)
        self.assertIsNone(result["error_type"])

    def test_runner_records_request_failure(self) -> None:
        times = iter((0, 250_000_000))
        def opener(*_args, **_kwargs):
            raise urllib.error.URLError("unavailable")
        result = run_legacy_eval.request_one(
            "prompt", "qwen3:4b", "http://127.0.0.1:11434", 10,
            opener=opener, clock=lambda: next(times),
        )
        self.assertEqual(result["wall_time_ms"], 250)
        self.assertEqual(result["error_type"], "URLError")

    def test_speed_and_paired_quality_comparison(self) -> None:
        expected = {"intent": "read", "date": None, "time": None,
                    "location": None, "category": None, "content": None}
        cases = [{"id": "one", "expected": expected}, {"id": "two", "expected": expected}]
        correct = json.dumps(expected)
        before_responses = {
            "one": {"raw_response": "{}", "wall_time_ms": 1000,
                    "ollama_metrics": {"eval_count": 10, "eval_duration": 1_000_000_000}},
            "two": {"raw_response": correct, "wall_time_ms": 2000},
        }
        after_responses = {
            "one": {"raw_response": correct, "wall_time_ms": 700,
                    "ollama_metrics": {"eval_count": 20, "eval_duration": 1_000_000_000}},
            "two": {"raw_response": "{}", "wall_time_ms": 2200},
        }
        before = qwen_eval.score(cases, before_responses)
        after = qwen_eval.score(cases, after_responses)
        comparison = qwen_eval.compare_reports(before, after, before_responses, after_responses)
        self.assertEqual(before["speed"]["wall_time_ms_median"], 1500)
        self.assertEqual(before["speed"]["wall_time_ms_p95_nearest_rank"], 2000)
        self.assertEqual(after["speed"]["output_tokens_per_second_median"], 20)
        self.assertEqual(comparison["quality_gained_cases"], 1)
        self.assertEqual(comparison["quality_lost_cases"], 1)
        self.assertEqual(comparison["paired_timing_count"], 2)
        self.assertEqual(comparison["after_minus_before_wall_ms_median"], -50)

    def test_response_rejects_invalid_timing(self) -> None:
        cases = [{"id": "one"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "responses.jsonl"
            path.write_text(json.dumps({"id": "one", "raw_response": "{}",
                                        "wall_time_ms": -1}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "wall_time_ms"):
                qwen_eval.load_responses(path, cases)

    def test_run_reads_jsonl_and_writes_manifest_without_overwrite(self) -> None:
        archived_cases = qwen_eval.load_cases(LEGACY / "qwen_legacy_eval_100.jsonl")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases_path = root / "cases.jsonl"
            cases_path.write_text(json.dumps(archived_cases[0], ensure_ascii=False) + "\n", encoding="utf-8")
            output = root / "run"
            fake = {"raw_response": "{}", "wall_time_ms": 10.0,
                    "ollama_metrics": {"eval_count": 1}, "error_type": None}
            with patch.object(run_legacy_eval, "request_one", return_value=fake) as request:
                run_legacy_eval.run(cases_path, output, "qwen3:4b", "http://127.0.0.1:11434", 10)
            self.assertIn(archived_cases[0]["utterance"], request.call_args.args[0])
            response = qwen_eval.read_jsonl(output / "responses.jsonl")[0]
            self.assertEqual(response["id"], archived_cases[0]["id"])
            self.assertEqual(len(response["prompt_sha256"]), 64)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(manifest["cases_sha256"], run_legacy_eval.sha256(cases_path))
            with self.assertRaises(FileExistsError):
                run_legacy_eval.run(cases_path, output, "qwen3:4b", "http://127.0.0.1:11434", 10)

    def test_prompt_template_matches_archived_script_exactly(self) -> None:
        script = LEGACY / "qwen_batch_test_100_scored.py"
        spec = importlib.util.spec_from_file_location("archived_qwen_prompt", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        utterance = "다음 주 입고 일정 확인"
        self.assertEqual(run_legacy_eval.legacy_prompt(utterance, module.BASE_DATE),
                         module.make_prompt(utterance))


if __name__ == "__main__":
    unittest.main()
