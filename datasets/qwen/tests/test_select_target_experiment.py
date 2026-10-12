"""Safety checks for the DATA-only target-selection contract proposal."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "multiturn_400_v0.1"
sys.path.insert(0, str(EXPERIMENT))

import run_eval as evaluator  # noqa: E402
from runtime.schedule_assistant.actions import ActionError, parse_action  # noqa: E402


class SelectTargetExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = {case["id"]: case for case in evaluator.load_cases(
            EXPERIMENT / "train" / "scenarios_UNREVIEWED.jsonl")}
        cls.rubric = json.loads((EXPERIMENT / "question_rubric.json").read_text(encoding="utf-8"))

    def test_selection_action_has_operation_and_no_target_id(self) -> None:
        case = self.cases["other-update-different-target"]
        selection = case["turns"][0]["model_outputs"][1]
        self.assertEqual(selection["action"], "SELECT_TARGET")
        self.assertEqual(selection["request"]["operation"], "update")
        self.assertNotIn("targetId", selection["request"])
        parsed = parse_action(json.dumps(selection, ensure_ascii=False))
        self.assertEqual(parsed.request["changes"], {"scheduledAt": "2026-10-11T17:00:00+09:00"})

    def test_selection_rejects_target_id_and_empty_changes(self) -> None:
        for request in (
            {"operation": "delete", "targetId": "6c99a773-24d3-523e-8149-bd2b6b79d16b"},
            {"operation": "update", "changes": {}},
        ):
            with self.subTest(request=request), self.assertRaises(ActionError):
                parse_action(json.dumps({"action": "SELECT_TARGET", "message": None, "request": request}))

    def test_selected_second_candidate_is_confirmed_before_write(self) -> None:
        case = self.cases["other-update-different-target"]
        result = evaluator.run_case(case, evaluator.ReferenceClient(
            [action for turn in case["turns"] for action in turn["model_outputs"]]), self.rubric)
        self.assertTrue(result["provisional_match"])
        self.assertEqual([turn["reply"]["kind"] for turn in result["turns"]],
                         ["ask", "confirm", "confirm", "updated"])
        self.assertEqual([turn["expected"]["writes"] for turn in result["turns"]], [0, 0, 0, 1])
        self.assertEqual(result["turns"][-1]["api_calls"][-1]["id"], case["initial_schedules"][1]["id"])

    def test_confirm_action_cannot_skip_selection_when_two_candidates_exist(self) -> None:
        case = self.cases["other-update-different-target"]
        actions = case["turns"][0]["model_outputs"]
        wrong = {"action": "CONFIRM_UPDATE", "message": None,
                 "request": {"targetId": case["initial_schedules"][0]["id"],
                             "changes": {"scheduledAt": "2026-10-11T17:00:00+09:00"}}}
        model = evaluator.ReferenceClient([actions[0], wrong])
        schedules = evaluator.MemorySchedules(case["initial_schedules"])
        service = evaluator.ConversationService(model, schedules,
                                                lambda: datetime.fromisoformat(case["reference_datetime"]))
        reply = service.handle(case["turns"][0]["user"])
        self.assertEqual(reply.kind, "error")
        self.assertIsNone(service.state.pending)
        self.assertFalse(any(call["method"] in {"POST", "PATCH", "DELETE"} for call in schedules.calls))


if __name__ == "__main__":
    unittest.main()
