"""Human edits must reach training targets and history without leaking test data."""

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "multiturn_400_v0.1"
sys.path.insert(0, str(EXPERIMENT))
import export_training
import review_sources

spec = importlib.util.spec_from_file_location("mlx_prepare", EXPERIMENT.parent / "mlx_smoke_v0.1" / "prepare.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class ReviewedTrainingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / "export"
        self.cases = {}
        for split in review_sources.SPLITS:
            cases = [json.loads(line) for line in (EXPERIMENT / split / "scenarios_UNREVIEWED.jsonl").read_text(encoding="utf-8").splitlines()]
            self.cases[split] = cases[:3]
            directory = self.root / split
            directory.mkdir()
            (directory / "scenarios_UNREVIEWED.jsonl").write_text(
                "".join(json.dumps(c) + "\n" for c in cases[:3]), encoding="utf-8")
        (self.root / "question_rubric.json").write_bytes((EXPERIMENT / "question_rubric.json").read_bytes())

    def review(self, split, index=0, status="reviewed"):
        path = review_sources.start_review(self.cases[split][index]["id"], self.root)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["scenario"]["label_status"] = status
        review_sources.save_json(path, record)
        return path

    def ready(self):
        self.review("train")
        self.review("validation")

    def test_edited_answer_and_following_history_reach_training(self):
        path = self.review("train")
        record = json.loads(path.read_text(encoding="utf-8"))
        edited = "이미 지난 시각이에요. 언제로 등록할까요?"
        record["scenario"]["turns"][0]["model_outputs"][0]["message"] = edited
        review_sources.save_json(path, record)
        self.review("validation")
        before = path.read_bytes()
        manifest = export_training.export_training(self.output, self.root)
        inputs, loaded_manifest = prepare.load_inputs(self.output)
        train = inputs["train"][1]
        self.assertEqual(len(train), 2)
        self.assertEqual(json.loads(train[0]["messages"][-1]["content"])["message"], edited)
        self.assertTrue(any(edited in m["content"] for m in train[1]["messages"][:-1]))
        self.assertEqual(train[0]["messages"][0]["content"], export_training.evaluator.PROMPT)
        self.assertEqual(loaded_manifest, manifest)
        self.assertEqual(path.read_bytes(), before)
        with self.assertRaises(FileExistsError):
            export_training.export_training(self.output, self.root)

    def test_split_and_approval_filters_and_controller_only_turns(self):
        self.review("train", 0, "proposed_unreviewed")
        self.review("train", 1, "rejected")
        self.review("train", 2)  # LIST + CONFIRM_DELETE; approval does not call model.
        self.review("validation")
        self.review("test")
        manifest = export_training.export_training(self.output, self.root)
        self.assertEqual(manifest["scenario_counts"], {"train": 1, "validation": 1})
        self.assertEqual(manifest["excluded_test_sources"], 1)
        self.assertEqual(manifest["excluded_unapproved_sources"], 2)
        inputs, _ = prepare.load_inputs(self.output)
        train = inputs["train"][1]
        self.assertEqual([json.loads(r["messages"][-1]["content"])["action"] for r in train], ["LIST", "CONFIRM_DELETE"])
        maps = [json.loads(s) for s in (self.output / "train_row_map.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(m["turn"], m["call"]) for m in maps], [(1, 1), (1, 2)])
        self.assertTrue(all(m["case_id"] == self.cases["train"][2]["id"] for m in maps))
        self.assertIn('"lookup_completed_this_turn": true', train[1]["messages"][-2]["content"])
        self.assertFalse((self.output / "test.jsonl").exists())

    def test_no_automatic_approval_or_unreviewed_fallback(self):
        self.review("train")
        self.review("validation", status="proposed_unreviewed")
        with self.assertRaisesRegex(ValueError, "Approve at least one"):
            export_training.export_training(self.output, self.root)
        self.assertFalse(self.output.exists())

    def test_stale_draft_rejected_before_output(self):
        self.ready()
        path = self.root / "train" / "scenarios_UNREVIEWED.jsonl"
        cases = self.cases["train"]
        cases[0]["turns"][0]["user"] += "!"
        path.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "draft changed"):
            export_training.export_training(self.output, self.root)
        self.assertFalse(self.output.exists())

    def test_guard_or_question_failure_blocks_export(self):
        self.ready()
        path = self.root / "reviewed" / (self.cases["train"][0]["id"] + ".json")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["scenario"]["turns"][0]["model_outputs"][0]["message"] = "어떤 업무인가요?"
        review_sources.save_json(path, record)
        with self.assertRaisesRegex(ValueError, "reviewed replay failed"):
            export_training.export_training(self.output, self.root)
        self.assertFalse(self.output.exists())

    def test_input_overlap_and_conflicting_answers_are_blocked(self):
        self.ready()
        records = review_sources.load_reviewed(self.root)
        first = copy.deepcopy(records[0])
        same_input = copy.deepcopy(first)
        same_input["split"] = "validation" if first["split"] == "train" else "train"
        with patch.object(review_sources, "load_reviewed", return_value=[first, same_input]):
            with self.assertRaisesRegex(ValueError, "crosses splits"):
                export_training.export_training(self.output, self.root)
        same_input["split"] = first["split"]
        same_input["scenario"]["turns"][0]["model_outputs"][0]["message"] = "이미 지난 시각이에요. 언제로 등록할까요?"
        with patch.object(review_sources, "load_reviewed", return_value=[first, same_input, records[1]]):
            with self.assertRaisesRegex(ValueError, "Conflicting reviewed targets"):
                export_training.export_training(self.output, self.root)
        self.assertFalse(self.output.exists())

    def test_prepare_rejects_modified_export(self):
        self.ready()
        export_training.export_training(self.output, self.root)
        path = self.output / "train.jsonl"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Reviewed export changed"):
            prepare.load_inputs(self.output)

    def test_prepare_rejects_scenario_export(self):
        self.ready()
        review_sources.export_reviewed(self.output, self.root)
        with self.assertRaisesRegex(ValueError, "Expected reviewed training export"):
            prepare.load_inputs(self.output)

    def test_unsupported_and_mixed_reminder_cases_export_when_reviewed(self):
        selected = {"train": "inv-update-alarm-off",
                    "validation": "validation-inbound-update-title_alarm-01"}
        for split, case_id in selected.items():
            source = EXPERIMENT / split / "scenarios_UNREVIEWED.jsonl"
            case = next(json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()
                        if json.loads(line)["id"] == case_id)
            self.cases[split] = [case]
            (self.root / split / "scenarios_UNREVIEWED.jsonl").write_text(
                json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
            self.review(split)
        manifest = export_training.export_training(self.output, self.root)
        self.assertEqual(manifest["scenario_counts"], {"train": 1, "validation": 1})
        inputs, _ = prepare.load_inputs(self.output)
        self.assertEqual(len(inputs["train"][1]), 1)
        self.assertEqual(len(inputs["valid"][1]), 3)


if __name__ == "__main__":
    unittest.main()
