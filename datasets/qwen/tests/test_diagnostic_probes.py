"""Exercise the supplemental DATA evaluation paths without calling a model."""

import copy
import json
import sys
import unittest
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "multiturn_400_v0.1"
sys.path.insert(0, str(EXPERIMENT))

import run_eval  # noqa: E402
from runtime.replay_multiturn_pilot import replay, validate_scenario  # noqa: E402


class DiagnosticProbeTests(unittest.TestCase):
    def test_each_probe_replays_through_evaluator(self) -> None:
        folder = EXPERIMENT / "diagnostic"
        cases = run_eval.load_cases(folder / "scenarios_UNREVIEWED.jsonl")
        rubric = json.loads((folder / "question_rubric.json").read_text(encoding="utf-8"))
        self.assertEqual(len(cases), 10)
        self.assertTrue(all(case["label_status"] == "proposed_unreviewed" for case in cases))
        self.assertTrue(any("select-two-candidates" in case["id"] for case in cases))
        for case in cases:
            with self.subTest(case=case["id"]):
                replay(case)
                outputs = [action for turn in case["turns"]
                           for action in turn["model_outputs"]]
                result = run_eval.run_case(case, run_eval.ReferenceClient(outputs), rubric)
                self.assertTrue(result["provisional_match"])
                self.assertTrue(result["raw_action_path_exact"])
                self.assertTrue(result["raw_json_path_exact"])
                self.assertEqual(result["fault_events"], case.get("faults", []))

    def test_write_failure_probes_distinguish_commit_from_rejection(self) -> None:
        cases = run_eval.load_cases(EXPERIMENT / "diagnostic" / "scenarios_UNREVIEWED.jsonl")
        for case in cases:
            faults = case.get("faults", [])
            if not faults or faults[0]["method"] not in {"PATCH", "DELETE"}:
                continue
            effect = faults[0]["effect"]
            initial_ids = {item["id"] for item in case["initial_schedules"]}
            final_ids = {item["id"] for item in case["expect_final"]}
            with self.subTest(case=case["id"]):
                self.assertEqual(case["turns"][-1]["expect"]["reply_kind"], "error")
                if effect == "reject_before_commit":
                    self.assertEqual(final_ids, initial_ids)
                elif faults[0]["method"] == "DELETE":
                    self.assertLess(len(final_ids), len(initial_ids))
                else:
                    self.assertEqual(final_ids, initial_ids)
                    initial = {item["id"]: item for item in case["initial_schedules"]}
                    self.assertTrue(any(item.get("scheduledAt") != initial[item["id"]]["scheduledAt"]
                                        for item in case["expect_final"]))

    def test_declared_but_unreached_fault_cannot_pass(self) -> None:
        case = copy.deepcopy(run_eval.load_cases(
            EXPERIMENT / "diagnostic" / "scenarios_UNREVIEWED.jsonl")[0])
        case["faults"].append({"method": "POST", "occurrence": 99,
                               "effect": "reject_before_commit"})
        with self.assertRaisesRegex(AssertionError, "declared faults were not triggered"):
            replay(case)
        outputs = [action for turn in case["turns"] for action in turn["model_outputs"]]
        result = run_eval.run_case(case, run_eval.ReferenceClient(outputs), {case["id"]: {"1": "time"}})
        self.assertFalse(result["provisional_match"])
        self.assertTrue(any(check["name"] == "injected_faults_triggered" and not check["match"]
                            for check in result["final_checks"]))

    def test_duplicate_fault_occurrence_is_rejected(self) -> None:
        case = copy.deepcopy(run_eval.load_cases(
            EXPERIMENT / "diagnostic" / "scenarios_UNREVIEWED.jsonl")[0])
        case["faults"].append({"method": "POST", "occurrence": 1,
                               "effect": "reject_before_commit"})
        with self.assertRaisesRegex(ValueError, "duplicate fault occurrence"):
            validate_scenario(case)


if __name__ == "__main__":
    unittest.main()
