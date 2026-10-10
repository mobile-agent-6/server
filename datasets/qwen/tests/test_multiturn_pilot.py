"""Checks for provisional scenario validation and scripted controller replay."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

QWEN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QWEN_ROOT / "tools"))

import replay_multiturn_pilot as pilot  # noqa: E402
import run_multiturn_pilot_eval as evaluator  # noqa: E402


class MultiturnPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = pilot.load_cases(QWEN_ROOT / "experiments" / "multiturn_pilot" /
                                     "scenarios_pilot_v0.1.jsonl")

    def test_seed_scenarios_replay_without_model_or_backend(self) -> None:
        self.assertEqual(len(self.cases), 5)
        for case in self.cases:
            with self.subTest(case=case["id"]):
                pilot.replay(case)

    def test_validator_rejects_non_null_answer_and_missing_group(self) -> None:
        case = copy.deepcopy(self.cases[1])
        case["turns"][0]["model_outputs"][1]["message"] = "모델이 지어낸 답"
        with self.assertRaises(pilot.ActionError):
            pilot.validate_scenario(case)
        case = copy.deepcopy(self.cases[1])
        del case["conversation_group_id"]
        with self.assertRaises(ValueError):
            pilot.validate_scenario(case)

    def test_replay_detects_wrong_write_target(self) -> None:
        case = copy.deepcopy(self.cases[2])
        case["turns"][1]["expect"]["target_id"] = "22222222-2222-4222-8222-222222222222"
        with self.assertRaisesRegex(AssertionError, "write target"):
            pilot.replay(case)

    def test_replay_detects_unexpected_model_call_even_on_error_reply(self) -> None:
        case = copy.deepcopy(self.cases[1])
        case["turns"][0]["model_outputs"] = []
        case["turns"][0]["expect"]["reply_kind"] = "error"
        case["turns"][0]["expect"]["reply_contains"] = []
        case["turns"][0]["expect"]["api_methods"] = []
        with self.assertRaisesRegex(AssertionError, "unexpected model call"):
            pilot.replay(case)

    def test_real_model_runner_records_scripted_equivalent_without_live_ollama(self) -> None:
        for case in self.cases:
            with self.subTest(case=case["id"]):
                model = pilot.ScriptedModel()
                model.outputs = [output for turn in case["turns"] for output in turn["model_outputs"]]
                result = evaluator.run_case(case, model)
                self.assertTrue(result["provisional_match"])
                self.assertEqual(result["completed_turns"], len(case["turns"]))
                self.assertEqual(sum(len(turn["model_calls"]) for turn in result["turns"]),
                                 sum(len(turn["model_outputs"]) for turn in case["turns"]))

    def test_runner_stops_followup_when_model_takes_wrong_branch(self) -> None:
        model = pilot.ScriptedModel()
        model.outputs = [{"action": "ANSWER", "message": None, "request": None}]
        result = evaluator.run_case(self.cases[0], model)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["completed_turns"], 1)
        self.assertEqual(result["turns"][0]["model_calls"][0]["action"], "ANSWER")
        self.assertFalse(result["turns"][0]["checks"][0]["match"])

    def test_ollama_adapter_records_raw_output_and_token_metrics(self) -> None:
        class Response:
            def __init__(self, action: dict[str, object]):
                self.payload = json.dumps({"message": {"content": json.dumps(action)},
                                           "prompt_eval_count": 12, "eval_count": 5}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self) -> bytes:
                return self.payload

        case = self.cases[0]
        responses = [Response(output) for turn in case["turns"] for output in turn["model_outputs"]]
        client = evaluator.OllamaClient("local-test")
        with patch("urllib.request.urlopen", side_effect=responses) as send:
            result = evaluator.run_case(case, client)
        self.assertTrue(result["provisional_match"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result["turns"][0]["model_calls"][0]["metrics"]["eval_count"], 5)
        self.assertIn('"action": "ASK"', result["turns"][0]["model_calls"][0]["raw"])

    def test_cli_writes_reviewable_run_files(self) -> None:
        model = pilot.ScriptedModel()
        model.outputs = [output for case in self.cases for turn in case["turns"]
                         for output in turn["model_outputs"]]
        scenarios = QWEN_ROOT / "experiments" / "multiturn_pilot" / "scenarios_pilot_v0.1.jsonl"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            argv = ["run_multiturn_pilot_eval.py", "--scenarios", str(scenarios),
                    "--model", "fake-local", "--output-dir", str(output)]
            with patch.object(sys, "argv", argv), patch.object(evaluator, "OllamaClient", return_value=model):
                evaluator.main()
            cases = [json.loads(line) for line in (output / "review_cases.jsonl").read_text(encoding="utf-8").splitlines()]
            turns = (output / "turn_results.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(cases), 5)
            self.assertEqual(len(turns), sum(len(case["turns"]) for case in self.cases))
            self.assertEqual(json.loads((output / "summary.json").read_text(encoding="utf-8"))["provisional_matches"], 5)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "provisional_unreviewed")
            self.assertEqual({case["run_id"] for case in cases}, {manifest["run_id"]})

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable for HTML script syntax check")
    def test_review_html_script_parses(self) -> None:
        page = (QWEN_ROOT / "tools" / "review_ui" / "index.html").read_text(encoding="utf-8")
        match = re.search(r"<script>(.*?)</script>", page, re.DOTALL)
        self.assertIsNotNone(match)
        result = subprocess.run(["node", "--check", "-"], input=match.group(1),
                                text=True, encoding="utf-8", capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
