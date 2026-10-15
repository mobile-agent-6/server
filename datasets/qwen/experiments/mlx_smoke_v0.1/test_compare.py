"""CPU-only checks for sample coverage and safe comparison resumption."""
import importlib.util
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('smoke_compare', Path(__file__).with_name('compare.py'))
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


class CompareTests(unittest.TestCase):
    def test_full_selection_has_no_missing_or_duplicate_rows(self):
        rows = [{'messages': [{'content': '{"action":"' + action + '"}'}]}
                for action in ['ASK', 'LIST', 'ASK', 'CREATE', 'LIST']]
        selected = compare.select_rows(rows, 114)
        self.assertEqual(sorted(selected), list(range(len(rows))))
        self.assertEqual(len(set(selected)), len(rows))

    def test_partial_rows_resume_but_duplicates_and_reordering_fail(self):
        compare.validate_saved_rows([{'row': 3}, {'row': 1}], [2, 0, 1])
        for records in [[{'row': 3}, {'row': 3}], [{'row': 1}],
                        [{'row': 3}, {'row': 1}, {'row': 2}, {'row': 2}]]:
            with self.assertRaises(ValueError):
                compare.validate_saved_rows(records, [2, 0, 1])

    def test_resume_rejects_changed_inputs_and_legacy_manifest(self):
        manifest = dict(data_sha256='data', adapter_sha256='adapter', temperature=0,
                        max_tokens=512, input_identity={'model/config.json': 'hash'})
        compare.validate_resume(manifest, manifest.copy())
        for key in manifest:
            changed = manifest | {key: 'changed'}
            with self.assertRaises(ValueError):
                compare.validate_resume(manifest, changed)
        with self.assertRaises(ValueError):
            compare.validate_resume({}, manifest)

    def test_invalid_sample_limit(self):
        with self.assertRaises(ValueError):
            compare.select_rows([], 0)

    def test_interrupted_save_preserves_last_complete_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'before.jsonl'
            first = [{'row': 3, 'output': '질문'}]
            compare.save_records(path, first)
            more = first + [{'row': 1, 'output': '정답'}]
            with patch.object(Path, 'replace', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    compare.save_records(path, more)
            saved = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(saved, first)
            compare.validate_saved_rows(saved, [2, 0])
            compare.save_records(path, more)
            self.assertEqual([json.loads(line) for line in path.read_text().splitlines()], more)

    def test_resume_command_quotes_paths_and_finishes_both_stages(self):
        args = shlex.split(compare.resume_command(Path('/run with spaces'), Path('/runtime'), Path('/compare dir')))
        self.assertEqual(args[args.index('--run-dir') + 1], '/run with spaces')
        self.assertEqual(args[args.index('--resume') + 1], '/compare dir')
        self.assertEqual(args[args.index('--stage') + 1], 'both')

    def test_scope_uses_prepared_data_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertTrue(compare.diagnostic_scope(root).startswith('UNSPECIFIED '))
            for status, label in [('REVIEWED_SMOKE_ONLY', 'REVIEWED'), ('UNREVIEWED_SMOKE_ONLY', 'UNREVIEWED')]:
                (root / 'inspection.json').write_text(json.dumps({'status': status}))
                self.assertTrue(compare.diagnostic_scope(root).startswith(label + ' '))


if __name__ == '__main__':
    unittest.main()
