"""Validation of paired, full-conversation DATA evaluation reports."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "multiturn_400_v0.1"
sys.path.insert(0, str(EXPERIMENT))
import compare_runs  # noqa: E402


class CompareRunsTests(unittest.TestCase):
    def make_run(self, directory, run_id, digest, results, label="reviewed"):
        directory.mkdir()
        manifest = {
            "run_id": run_id, "source_kind": "actual_model",
            "model": run_id, "model_metadata": [{"digest": digest}],
            "tool_version": "eval-v2.6-proposed", "ollama_version": {"version": "test"},
            "options": {"temperature": 0},
            "think": False, "prompt": "fixed", "case_ids": ["a", "b"],
            "file_sha256": {"scenarios.jsonl": "same", "run_eval.py": "same"},
            "label_status_counts": {label: 2},
        }
        cases = [
            {"run_id": run_id, "source_kind": "actual_model", "case_id": name,
             "label_status": label, "provisional_match": values[0],
             "raw_action_path_exact": values[1], "raw_json_path_exact": values[2]}
            for name, values in zip(("a", "b"), results)
        ]
        summary = {"run_id": run_id, "source_kind": "actual_model", "cases_run": 2,
                   "label_status_counts": {label: 2},
                   "provisional_matches": sum(case["provisional_match"] for case in cases),
                   "raw_action_exact_dialogues": sum(case["raw_action_path_exact"] for case in cases),
                   "raw_json_exact_dialogues": sum(case["raw_json_path_exact"] for case in cases)}
        for name, data in (("manifest.json", manifest), ("summary.json", summary)):
            (directory / name).write_text(json.dumps(data), encoding="utf-8")
        (directory / "review_cases.jsonl").write_text(
            "".join(json.dumps(case) + "\n" for case in cases), encoding="utf-8")

    def test_pair_reports_improvement_and_regression_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            before, after = Path(temp) / "before", Path(temp) / "after"
            self.make_run(before, "base", "digest-base",
                          [(True, True, False), (False, False, False)])
            self.make_run(after, "adapter", "digest-adapter",
                          [(True, True, True), (True, False, False)])
            report = compare_runs.compare(before, after)
            self.assertEqual(report["cases"], 2)
            self.assertEqual(report["metrics"]["provisional_match"]["improved"], 1)
            self.assertEqual(report["metrics"]["raw_json_path_exact"]["improved"], 1)
            self.assertEqual(report["metrics"]["raw_action_path_exact"]["after"], 1)

    def test_mismatched_code_and_unreviewed_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            before, after = Path(temp) / "before", Path(temp) / "after"
            values = [(True, True, True), (False, False, False)]
            self.make_run(before, "base", "digest-base", values, label="proposed_unreviewed")
            self.make_run(after, "adapter", "digest-adapter", values, label="proposed_unreviewed")
            with self.assertRaisesRegex(ValueError, "unreviewed"):
                compare_runs.compare(before, after)
            report = compare_runs.compare(before, after, allow_unreviewed=True)
            self.assertEqual(report["status"], "exploratory_unreviewed")
            manifest_path = after / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["file_sha256"]["run_eval.py"] = "changed"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "file_sha256"):
                compare_runs.compare(before, after, allow_unreviewed=True)

    def test_scripted_or_incomplete_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            before, after = Path(temp) / "before", Path(temp) / "after"
            values = [(True, True, True), (False, False, False)]
            self.make_run(before, "base", "digest-base", values)
            self.make_run(after, "adapter", "digest-adapter", values)
            summary_path = after / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["provisional_matches"] = 2
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inconsistent"):
                compare_runs.compare(before, after)
            summary["provisional_matches"] = 1
            summary["source_kind"] = "scripted_reference"
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "scripted reference"):
                compare_runs.compare(before, after)


if __name__ == "__main__":
    unittest.main()
