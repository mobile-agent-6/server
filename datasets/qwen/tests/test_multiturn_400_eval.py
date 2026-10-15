"""Checks for the DATA-only 400-dialogue evaluation proposal."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "multiturn_400_v0.1"
sys.path.insert(0, str(EXPERIMENT))

import run_eval as evaluator  # noqa: E402
from runtime.schedule_assistant.service import Reply  # noqa: E402


class Multiturn400EvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = {case["id"]: case for case in evaluator.load_cases(
            EXPERIMENT / "train" / "scenarios_UNREVIEWED.jsonl")}
        cls.rubric = json.loads((EXPERIMENT / "question_rubric.json").read_text(encoding="utf-8"))

    def test_first_utterances_do_not_overlap_across_splits(self) -> None:
        first_utterances = {
            split: {case["turns"][0]["user"] for case in evaluator.load_cases(
                EXPERIMENT / split / "scenarios_UNREVIEWED.jsonl")}
            for split in ("train", "validation", "test")
        }
        for left, right in (("train", "validation"), ("train", "test"),
                            ("validation", "test")):
            self.assertFalse(first_utterances[left] & first_utterances[right],
                             f"identical first utterances in {left} and {right}")

    def test_wrong_displayed_query_result_fails_even_when_get_and_state_match(self) -> None:
        case = self.cases["in-list-range"]
        original_service = evaluator.Service

        class WrongDisplay(original_service):
            def handle(self, utterance: str) -> Reply:
                reply = super().handle(utterance)
                if reply.kind == "answer":
                    return Reply("answer", "조회된 일정이 없어요.", reply.schedule)
                return reply

        client = evaluator.ReferenceClient(
            [action for turn in case["turns"] for action in turn["model_outputs"]])
        with patch.object(evaluator, "Service", WrongDisplay):
            result = evaluator.run_case(case, client, self.rubric)
        self.assertFalse(result["provisional_match"])
        self.assertEqual(result["stop_reason"], "semantic_path_mismatch")
        self.assertTrue(any(check["name"] == "displayed_answer" and not check["match"]
                            for turn in result["turns"] for check in turn["checks"]))


if __name__ == "__main__":
    unittest.main()
