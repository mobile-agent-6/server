"""Deterministic tests for Issue #30 research contracts."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import validate_contracts as validator  # noqa: E402
from contracts import ContractError, strict_json_loads, validate_candidate_c  # noqa: E402
from rule_baseline import assess_rule_case  # noqa: E402


class ContractValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.integrity = validator.assert_locked_integrity()
        cls.cases = validator.load_jsonl(validator.LOCKED_PATH)
        cls.fixtures = validator.load_jsonl(validator.FIXTURE_PATH)

    def test_locked_inputs_are_unchanged(self) -> None:
        self.assertEqual(self.integrity["lockStatus"], "locked")
        self.assertEqual(self.integrity["caseCount"], 56)
        self.assertEqual(self.integrity["lockedSha256"], validator.EXPECTED_LOCKED_SHA256)
        self.assertEqual(self.integrity["schemaSha256"], validator.EXPECTED_SCHEMA_SHA256)

    def test_minimum_valid_output_for_every_candidate(self) -> None:
        rows = validator.valid_minimum_rows()
        self.assertEqual([row["candidate"] for row in rows], ["A", "B", "C", "R"])
        self.assertTrue(all(row["localValidation"] == "accept" for row in rows))

    def test_duplicate_keys_are_rejected_at_every_depth(self) -> None:
        with self.assertRaisesRegex(ContractError, "DUPLICATE_KEY"):
            strict_json_loads('{"outer":{"value":1,"value":2}}')

    def test_candidate_c_validates_all_locked_spans(self) -> None:
        checked = 0
        for case in self.cases:
            turns = {
                turn["turn"]: turn["text"]
                for turn in case["context"]["turns"]
                if turn["inputType"] == "text"
            }
            output = validator.candidate_c_from_case(case)
            validate_candidate_c(json.dumps(output, ensure_ascii=False), user_turns=turns)
            checked += 1
        self.assertEqual(checked, 56)

    def test_candidate_c_rejects_bool_index_and_non_string_missing_item(self) -> None:
        turns = {1: "일정 등록해 줘"}
        base = {
            "operations": [{
                "index": True,
                "kind": "create",
                "targetEvidence": {},
                "changeEvidence": {},
                "filterEvidence": {},
                "unsupportedEvidence": [],
                "missingInformation": [],
            }],
            "unsupportedEvidence": [],
            "missingInformation": [],
        }
        with self.assertRaisesRegex(ContractError, "C_OPERATION_ORDER"):
            validate_candidate_c(json.dumps(base, ensure_ascii=False), user_turns=turns)
        base["operations"] = []
        base["missingInformation"] = [{}]
        with self.assertRaisesRegex(ContractError, "INVALID_MISSING_INFORMATION"):
            validate_candidate_c(json.dumps(base, ensure_ascii=False), user_turns=turns)

    def test_candidate_a_half_hour_defect_is_not_hidden(self) -> None:
        local = validator._special_local_input("A", "a_half_minute_gap")
        self.assertEqual(local["context"]["verified_user_datetime"], "2028-07-03T19:00:00+09:00")
        validator.validate_local("A", local)

    def test_candidate_a_completed_filter_gap_is_not_hidden(self) -> None:
        local = validator._special_local_input("A", "a_completed_filter_gap")
        parsed = validator.validate_local("A", local)
        self.assertTrue(parsed["request"]["completed"])

    def test_locked_representability_counts(self) -> None:
        rows = validator.representation_rows(self.cases)
        counts: dict[str, dict[str, int]] = {}
        for candidate in ("A", "B", "C", "R"):
            subset = [row for row in rows if row["candidate"] == candidate]
            counts[candidate] = {
                key: sum(row["classification"] == key for row in subset)
                for key in ("representable", "safe_reject", "structurally_unrepresentable")
            }
            self.assertEqual(len(subset), 56)
        self.assertEqual(counts["A"], {"representable": 9, "safe_reject": 4, "structurally_unrepresentable": 43})
        self.assertEqual(counts["B"], {"representable": 52, "safe_reject": 0, "structurally_unrepresentable": 4})
        self.assertEqual(counts["C"], {"representable": 56, "safe_reject": 0, "structurally_unrepresentable": 0})
        self.assertEqual(counts["R"], {"representable": 42, "safe_reject": 10, "structurally_unrepresentable": 4})

    def test_rule_baseline_fails_closed_for_multi_operation(self) -> None:
        case = next(case for case in self.cases if case["meta"]["caseId"] == "lk50-multi-skip-current-then-continue")
        self.assertEqual(assess_rule_case(case).classification, "structurally_unrepresentable")

    def test_fixture_metadata_and_atomic_safety_coverage(self) -> None:
        self.assertEqual(len(self.fixtures), 42)
        required = {
            "fixtureId", "candidates", "sourceCaseId", "mutationPath", "mutation",
            "expectedLocalResult", "expectedCommonGateResult", "expectedFinalResult",
            "cause", "errorReason", "safetyConditions",
        }
        self.assertEqual(len({fixture["fixtureId"] for fixture in self.fixtures}), 42)
        for fixture in self.fixtures:
            self.assertFalse(required - fixture.keys(), fixture["fixtureId"])
            self.assertEqual(fixture["expectedFinalResult"], "safe_reject")
        conditions = {condition for fixture in self.fixtures for condition in fixture["safetyConditions"]}
        expected = {
            "strict_duplicate_key_rejection", "additional_properties_false", "required_fields",
            "strict_types", "closed_enums", "length_limits", "evidence_substring",
            "evidence_turn_membership", "correction_order", "no_fabricated_datetime",
            "schedule_id_existence", "candidate_membership", "no_unfounded_auto_selection",
            "untrusted_relative_time", "grounded_title", "grounded_category",
            "grounded_completed_filter", "grounded_changes", "nonempty_changes",
            "no_noop_update", "past_create_rejected", "query_range_max_92_days",
            "deleted_schedule_exclusion", "backend_verified_result_text", "query_items_exact",
            "confirmation_before_update", "confirmation_before_delete", "list_never_writes",
            "no_candidate_truncation", "delegation_is_not_approval",
            "no_model_call_after_candidate_lookup", "asia_seoul_timezone",
        }
        self.assertFalse(expected - conditions)

    def test_all_mutations_match_two_stage_expectations(self) -> None:
        rows = validator.mutation_rows(self.fixtures)
        self.assertEqual(len(rows), 141)
        self.assertEqual(sum(row["unsafeAccept"] for row in rows), 0)
        self.assertEqual(sum(row["localValidation"] == "accept" for row in rows), 102)
        self.assertEqual(sum(row["localValidation"] == "reject" for row in rows), 39)
        self.assertEqual(sum(row["commonGate"] == "reject" for row in rows), 102)
        self.assertEqual(sum(row["commonGate"] == "not_run" for row in rows), 39)
        self.assertTrue(all(row["commonGate"] == "not_run" for row in rows if row["localValidation"] == "reject"))

    def test_common_gate_rejects_false_empty_query_result(self) -> None:
        result = validator.common_backend_gate(
            {"operation": "list", "listedIds": [], "write": False},
            {"backendResultIds": ["id-1"]},
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.code, "QUERY_ITEMS_MISMATCH")

    def test_full_validation_has_no_transport_axis_or_external_calls(self) -> None:
        _rows, summary, manifest = validator.run_validation()
        self.assertEqual(summary["finalUnsafeAccept"], 0)
        self.assertEqual(summary["modelCalls"], 0)
        self.assertEqual(summary["serverCalls"], 0)
        self.assertEqual(summary["databaseCalls"], 0)
        self.assertNotIn("TRANSPORT", manifest["classificationAxes"]["causes"])
        self.assertNotIn("transport_fail", manifest["classificationAxes"]["outcomes"])


if __name__ == "__main__":
    unittest.main()
