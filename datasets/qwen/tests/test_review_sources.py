"""Review source files must survive draft generation and export only approved cases."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "multiturn_400_v0.1"
sys.path.insert(0, str(EXPERIMENT))

import review_sources  # noqa: E402


class ReviewSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        original_rubric = json.loads((EXPERIMENT / "question_rubric.json").read_text(encoding="utf-8"))
        rubric = {}
        self.cases = {}
        for split in review_sources.SPLITS:
            case = json.loads((EXPERIMENT / split / "scenarios_UNREVIEWED.jsonl")
                              .read_text(encoding="utf-8").splitlines()[0])
            self.cases[split] = case
            rubric[case["id"]] = original_rubric.get(case["id"], {})
            target = self.root / split
            target.mkdir()
            (target / "scenarios_UNREVIEWED.jsonl").write_text(
                json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
        review_sources.save_json(self.root / "question_rubric.json", rubric)

    def _approve(self, split: str) -> Path:
        path = review_sources.start_review(self.cases[split]["id"], self.root)
        record = json.loads(path.read_text(encoding="utf-8"))
        record["scenario"]["label_status"] = "reviewed"
        review_sources.save_json(path, record)
        return path

    def test_start_never_overwrites_human_edits(self) -> None:
        path = self._approve("train")
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            review_sources.start_review(self.cases["train"]["id"], self.root)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(review_sources.load_reviewed(self.root)), 1)

    def test_export_includes_only_reviewed_sources_and_refuses_overwrite(self) -> None:
        self._approve("train")
        review_sources.start_review(self.cases["validation"]["id"], self.root)
        output = self.root / "runs" / "approved_001"
        counts = review_sources.export_reviewed(output, self.root)
        self.assertEqual(counts, {"train": 1, "validation": 0, "test": 0})
        exported = json.loads((output / "train" / "scenarios_REVIEWED.jsonl")
                              .read_text(encoding="utf-8").strip())
        self.assertEqual(exported["label_status"], "reviewed")
        self.assertEqual((output / "validation" / "scenarios_REVIEWED.jsonl")
                         .read_text(encoding="utf-8"), "")
        with self.assertRaises(FileExistsError):
            review_sources.export_reviewed(output, self.root)

    def test_changed_draft_requires_reconciliation_without_losing_review(self) -> None:
        path = self._approve("train")
        before = path.read_bytes()
        draft_path = self.root / "train" / "scenarios_UNREVIEWED.jsonl"
        draft = self.cases["train"]
        draft["turns"][0]["user"] += "!"
        draft_path.write_text(json.dumps(draft, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "draft changed after review started"):
            review_sources.load_reviewed(self.root)
        self.assertEqual(path.read_bytes(), before)

    def test_invalid_approved_action_is_rejected(self) -> None:
        path = self._approve("train")
        record = json.loads(path.read_text(encoding="utf-8"))
        record["scenario"]["turns"][0]["model_outputs"][0]["action"] = "INVALID"
        review_sources.save_json(path, record)
        with self.assertRaises(ValueError):
            review_sources.load_reviewed(self.root)

    def test_unsupported_reminder_question_can_be_reviewed(self) -> None:
        source = EXPERIMENT / "train" / "scenarios_UNREVIEWED.jsonl"
        case = next(json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()
                    if json.loads(line)["id"] == "inv-update-alarm-off")
        self.cases["train"] = case
        (self.root / "train" / "scenarios_UNREVIEWED.jsonl").write_text(
            json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
        rubric = json.loads((self.root / "question_rubric.json").read_text(encoding="utf-8"))
        rubric[case["id"]] = {"1": "unsupported_reminder"}
        review_sources.save_json(self.root / "question_rubric.json", rubric)
        self._approve("train")
        self.assertEqual(len(review_sources.load_reviewed(self.root)), 1)


if __name__ == "__main__":
    unittest.main()
