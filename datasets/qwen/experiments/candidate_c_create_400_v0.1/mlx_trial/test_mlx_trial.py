"""Model-free checks for the MLX trial harness (backend venv)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mlx_eval_c  # noqa: E402
from mlx_eval_c import v1  # noqa: E402


class GoldClient:
    """Answers each model turn with the Issue #30 oracle Candidate C output."""

    def __init__(self, cases):
        self.queue = [
            v1.canonical_json(v1._expected_c(case, turn["turn"]))
            for case in cases
            for turn in v1._model_text_turns(case)
        ]

    def chat(self, messages, schema):
        raw = self.queue.pop(0)
        return v1.CallRecord(raw, 200, 1.0, True, 0, None, "test", None, None)


class GarbageClient:
    def chat(self, messages, schema):
        return v1.CallRecord("not json", 200, 1.0, True, 0, None, "test", None, None)


class TrialHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = v1.load_jsonl(v1.LOCKED_PATH)

    def test_subsets_match_preregistration(self) -> None:
        names = [mlx_eval_c.subset(case) for case in self.cases]
        self.assertEqual(names.count("primary_create"), 20)
        self.assertEqual(names.count("regression_other"), 36)

    def test_gold_outputs_are_never_unsafe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = mlx_eval_c.run(GoldClient(self.cases), label="base", run_dir=Path(tmp),
                                     model_identity={"modelRevision": "test"})
        self.assertIsNone(summary["stoppedOnUnsafe"])
        self.assertEqual(summary["evaluatedCases"], 56)
        self.assertEqual(summary["summary"]["all"]["unsafeAccept"], 0)
        self.assertEqual(summary["summary"]["all"]["jsonParseFailures"], 0)

    def test_garbage_outputs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = mlx_eval_c.run(GarbageClient(), label="base", run_dir=Path(tmp),
                                     model_identity={"modelRevision": "test"})
        self.assertEqual(summary["summary"]["all"]["unsafeAccept"], 0)
        self.assertEqual(summary["summary"]["primary_create"]["correct"], 0)
        # Only backend-only cases (no model call) can be correct with garbage output.
        self.assertLessEqual(summary["summary"]["regression_other"]["correct"],
                             summary["summary"]["regression_other"]["modelFreeCases"])

    def test_validation_split_keeps_every_skill_in_training(self) -> None:
        import prepare_trial

        cases = {c["meta"]["caseId"]: c for c in prepare_trial.load_jsonl(prepare_trial.CONVERSATIONS)}
        valid = set(prepare_trial.choose_valid_families(cases))
        self.assertNotIn("untrusted_followup", valid)  # run 001 held this skill out entirely
        train_skills = {prepare_trial.skill(c) for c in cases.values()
                        if prepare_trial.notes(c)["familyId"] not in valid}
        for case in cases.values():
            if prepare_trial.notes(case)["familyId"] in valid:
                self.assertIn(prepare_trial.skill(case), train_skills, case["meta"]["caseId"])
        train, held, _ = prepare_trial.split_rows(("batch1",))
        self.assertEqual(len(train) + len(held), 131)
        train, held, _ = prepare_trial.split_rows(("batch1", "batch2"))
        self.assertEqual(len(train) + len(held), 131 + 128)
        train, held, _ = prepare_trial.split_rows(("batch1", "batch2", "batch3"))
        self.assertEqual(len(train) + len(held), 131 + 128 + 224)
        train, held, _ = prepare_trial.split_rows(("batch1", "batch2", "batch3", "batch4"))
        self.assertEqual(len(train) + len(held), 131 + 128 + 224 + 50)
        train, held, _ = prepare_trial.split_rows()
        self.assertEqual(len(train) + len(held), 131 + 128 + 224 + 50 + 37)

    def test_missing_last_order_keeps_target_and_moves_decision_after_evidence(self) -> None:
        import prepare_trial

        canonical, _, _ = prepare_trial.split_rows()
        ordered, _, _ = prepare_trial.split_rows(key_order="missingInformation_last")
        self.assertEqual(len(canonical), len(ordered))
        for before, after in zip(canonical, ordered):
            self.assertEqual(before["messages"][:-1], after["messages"][:-1])
            old, new = before["messages"][-1]["content"], after["messages"][-1]["content"]
            self.assertEqual(json.loads(old), json.loads(new))
            self.assertTrue(new.startswith('{"operations":'))
            self.assertTrue(new.endswith(']}') and '"missingInformation":' in new.rsplit("}", 2)[-1] + new[-200:])
        with self.assertRaises(SystemExit):
            prepare_trial.order_target(canonical[0]["messages"][-1]["content"], "alphabetical")

    def test_test50_gold_scores_all_correct_and_garbage_none(self) -> None:
        cases, _ = mlx_eval_c._test50_cases_and_dry_rows()
        self.assertEqual(len(cases), 50)
        with tempfile.TemporaryDirectory() as tmp:
            gold = mlx_eval_c.run(GoldClient(cases), label="base", run_dir=Path(tmp),
                                  model_identity={"modelRevision": "test"}, case_set="test50")
            self.assertTrue((Path(tmp) / "eval-base-test50.jsonl").exists())
            self.assertFalse((Path(tmp) / "eval-base.jsonl").exists())
        self.assertEqual(gold["caseSet"], "test50")
        # Past-time creates (039, 040) end in the common gate PAST_CREATE reject, which the v0.3
        # evaluator scores as safe_reject even for the gold output, so 48 is the ceiling.
        self.assertEqual(gold["summary"]["primary_create"]["correct"], 48)
        self.assertEqual(gold["summary"]["primary_create"]["outcomes"].get("safe_reject"), 2)
        self.assertEqual(gold["summary"]["all"]["unsafeAccept"], 0)
        with tempfile.TemporaryDirectory() as tmp:
            junk = mlx_eval_c.run(GarbageClient(), label="base", run_dir=Path(tmp),
                                  model_identity={"modelRevision": "test"}, case_set="test50")
        self.assertEqual(junk["summary"]["all"]["correct"], 0)
        self.assertEqual(junk["summary"]["all"]["unsafeAccept"], 0)

    def test_schema_order_matches_issue30_schema_and_keeps_targets(self) -> None:
        import prepare_trial

        schema = v1.candidate_c_schema()
        operation = schema["properties"]["operations"]["items"]
        self.assertEqual(tuple(schema["properties"]), prepare_trial.SCHEMA_ORDER["root"])
        self.assertEqual(tuple(operation["properties"]), prepare_trial.SCHEMA_ORDER["operation"])
        roles = [r for key in ("targetEvidence", "changeEvidence", "filterEvidence")
                 for r in operation["properties"][key]["properties"]]
        self.assertTrue(set(roles) <= set(prepare_trial.SCHEMA_ORDER["roles"]))
        span = operation["properties"]["changeEvidence"]["properties"]["title"]["items"]
        self.assertEqual(tuple(span["properties"]), prepare_trial.SCHEMA_ORDER["span"])
        canonical, _, _ = prepare_trial.split_rows()
        ordered, _, _ = prepare_trial.split_rows(key_order="schema")
        for before, after in zip(canonical, ordered):
            old, new = before["messages"][-1]["content"], after["messages"][-1]["content"]
            self.assertEqual(json.loads(old), json.loads(new))
            self.assertTrue(new.startswith('{"operations":[{"index":'))

    def test_validation_target_holds_out_about_50_conversations_without_losing_skills(self) -> None:
        import prepare_trial

        cases = {c["meta"]["caseId"]: c for name in prepare_trial.BATCHES
                 for c in prepare_trial.load_jsonl(prepare_trial.ROOT / name / "conversations-v0.1.jsonl")}
        valid = set(prepare_trial.choose_valid_families(cases, 50))
        held = [c for c in cases.values() if prepare_trial.notes(c)["familyId"] in valid]
        self.assertGreaterEqual(len(held), 50)
        self.assertLess(len(held), 70)
        train_skills = {prepare_trial.skill(c) for c in cases.values()
                        if prepare_trial.notes(c)["familyId"] not in valid}
        for case in held:
            self.assertIn(prepare_trial.skill(case), train_skills, case["meta"]["caseId"])
        self.assertEqual(prepare_trial.choose_valid_families(cases), prepare_trial.choose_valid_families(cases, None))

    def test_existing_result_is_not_resumed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "eval-base.jsonl").write_text("", encoding="utf-8")
            with self.assertRaises(SystemExit):
                mlx_eval_c.run(GarbageClient(), label="base", run_dir=Path(tmp),
                               model_identity={"modelRevision": "test"})


if __name__ == "__main__":
    unittest.main()
