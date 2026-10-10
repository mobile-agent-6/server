"""Checks for provisional scenario validation and scripted controller replay."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

QWEN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(QWEN_ROOT / "tools"))

import replay_multiturn_pilot as pilot  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
