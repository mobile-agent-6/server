from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import contracts
import ollama_eval as evaluator


class OllamaEvaluatorTests(unittest.TestCase):
    def test_protected_inputs_match_required_hashes(self) -> None:
        result = evaluator.assert_protected_integrity()
        self.assertEqual(result["lockedCaseCount"], 56)
        self.assertEqual(result["lockStatus"], "locked")

    def test_development_and_locked_sets_are_disjoint(self) -> None:
        development = evaluator.load_jsonl(evaluator.DEVELOPMENT_PATH)
        locked = evaluator.load_jsonl(evaluator.LOCKED_PATH)
        dev_ids = {row["meta"]["caseId"] for row in development}
        locked_ids = {row["meta"]["caseId"] for row in locked}
        self.assertEqual(len(development), 12)
        self.assertEqual(len(locked), 56)
        self.assertFalse(dev_ids & locked_ids)
        self.assertTrue(all(row["meta"]["datasetRole"] == "development_probe" for row in development))
        self.assertTrue(all(row["meta"]["datasetRole"] == "locked_comparison" for row in locked))

    def test_candidate_b_schemas_are_strict_and_json_serializable(self) -> None:
        json.dumps(evaluator.candidate_b_stage1_schema())
        for intent in ("create", "list", "update", "delete"):
            schema = evaluator.candidate_b_stage2_schema(intent)
            json.dumps(schema)
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(schema["properties"]["operation"]["enum"], [intent])
        self.assertEqual(
            set(evaluator.candidate_b_stage1_schema()["properties"]["intent"]["enum"]),
            contracts.B_INTENTS,
        )
        update_changes = evaluator.candidate_b_stage2_schema("update")["properties"]["changes"]
        self.assertEqual(update_changes["minProperties"], 1)

    def test_candidate_c_schema_is_strict_and_has_no_execution_values(self) -> None:
        schema = evaluator.candidate_c_schema()
        encoded = json.dumps(schema, ensure_ascii=False)
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("scheduledAt", encoded)
        self.assertNotIn("targetId", encoded)
        self.assertNotIn("scheduleId", encoded)
        self.assertIn("superseded", encoded)
        self.assertIn("correction_marker", encoded)

    def test_context_renderer_does_not_expose_oracle_or_schedules(self) -> None:
        case = evaluator.load_jsonl(evaluator.DEVELOPMENT_PATH)[0]
        rendered = evaluator.render_extraction_context(case, 1, stage="EVIDENCE")
        self.assertNotIn("semantics", rendered)
        self.assertNotIn("forbiddenValues", rendered)
        self.assertNotIn("initialSchedules", rendered)
        self.assertIn(case["context"]["turns"][0]["text"], rendered)

    def test_candidate_a_uses_exact_data_prompt_and_dynamic_schema(self) -> None:
        self.assertEqual(evaluator.prompt_text("A"), evaluator.data_v26.PROMPT)
        case = evaluator.load_jsonl(evaluator.DEVELOPMENT_PATH)[0]
        now = evaluator.dry_mapping._parse_datetime(case["context"]["referenceTime"])
        context = evaluator.build_a_context(
            [case["context"]["turns"][0]["text"]],
            now,
            lookup_completed=False,
            candidates=[],
        )
        schema = evaluator.data_v26.schema_for(context)
        actions = {
            branch["properties"]["action"]["enum"][0] for branch in schema["anyOf"]
        }
        self.assertEqual(actions, {"ASK"})

    def test_all_ideal_candidate_c_outputs_validate_as_real_spans(self) -> None:
        for case in evaluator.load_jsonl(evaluator.LOCKED_PATH):
            for turn in case["context"]["turns"]:
                if turn["inputType"] != "text":
                    continue
                value = evaluator._expected_c(case, turn["turn"])
                contracts.validate_candidate_c(
                    evaluator.canonical_json(value),
                    user_turns=evaluator._visible_text_turns(case, turn["turn"]),
                )

    def test_candidate_c_semantics_are_view_based_not_span_text_identity(self) -> None:
        case = evaluator.load_jsonl(evaluator.DEVELOPMENT_PATH)[0]
        expected = contracts.validate_candidate_c(
            evaluator.canonical_json(evaluator._expected_c(case, 1)),
            user_turns=evaluator._visible_text_turns(case, 1),
        )
        views = evaluator._views_from_c(expected, case)
        self.assertEqual(len(views), 1)
        self.assertEqual(views[0].kind, "create")
        self.assertEqual(views[0].title, "입고 팔레트 수량 점검")

    def test_range_less_list_is_not_an_executable_effect(self) -> None:
        case = evaluator.load_jsonl(evaluator.LOCKED_PATH)[18]
        self.assertEqual(case["meta"]["caseId"], "lk19-list-seoul-day-boundary-untrusted")
        self.assertFalse(
            evaluator._view_effect_complete(
                evaluator.dry_mapping.OperationView(kind="list"), case
            )
        )
        self.assertTrue(
            evaluator._view_effect_complete(
                evaluator.dry_mapping.OperationView(
                    kind="list",
                    query_from=evaluator.dry_mapping._parse_datetime(
                        "2028-09-18T00:00:00+09:00"
                    ),
                    query_to=evaluator.dry_mapping._parse_datetime(
                        "2028-09-19T00:00:00+09:00"
                    ),
                ),
                case,
            )
        )

    def test_client_rejects_non_loopback_endpoint(self) -> None:
        with self.assertRaises(ValueError):
            evaluator.OllamaClient("https://example.com")

    def test_raw_metadata_uses_only_hash_and_length(self) -> None:
        calls = [
            evaluator.CallRecord(
                '{"intent":"create"}', 200, 1.0, True, 0, None, "a" * 64, 1, 1
            )
        ]
        digest, length = evaluator._raw_metadata(calls)
        self.assertEqual(len(digest), 64)
        self.assertEqual(length, len(calls[0].raw.encode("utf-8")))
        self.assertNotIn(calls[0].raw, digest)

    def test_candidate_a_range_less_list_is_semantic_mismatch_not_crash(self) -> None:
        expected = evaluator.dry_mapping.OperationView(kind="list")
        result = evaluator._semantic_a(
            {"action": "ANSWER", "message": None, "request": None},
            {
                "action": "LIST",
                "message": None,
                "request": {
                    "from": "2028-01-01T00:00:00+09:00",
                    "to": "2028-01-02T00:00:00+09:00",
                },
            },
            {"expectedResult": {"kind": "outcome", "outcomeOperation": "list"}},
            expected,
            {"context": {"initialSchedules": []}},
        )
        self.assertFalse(result)

    def test_locked_matrix_generation_can_be_audited_without_transport(self) -> None:
        fake_details = {
            "jsonParseSuccess": True,
            "localValidation": "accept",
            "semanticMatch": True,
            "commonGate": "accept",
            "gateCodes": [],
            "intentMatch": True,
            "effectComplete": False,
            "finalAction": None,
            "groundless": {key: 0 for key in ("datetime", "title", "category", "filter", "changes")},
            "spanExact": True,
            "roleExact": True,
            "correctionExact": True,
            "arbitrarySelection": False,
        }
        fake_call = evaluator.CallRecord("{}", 200, 1.0, True, 0, None, "b" * 64, 1, 1)
        with patch.object(evaluator, "evaluate_candidate_c", return_value=([fake_call], fake_details)):
            rows, metadata = evaluator.run_phase(
                phase="locked",
                run_id="unit-test",
                endpoint=evaluator.ENDPOINT,
                model_digest="digest",
                candidates=("C",),
                prompt_revision=0,
                stop_on_unsafe=False,
            )
        self.assertEqual(len(rows), 56)
        self.assertEqual(len({(row["caseId"], row["candidate"]) for row in rows}), 56)
        self.assertEqual(metadata["frozenHashes"], metadata["frozenHashes"])
        self.assertIsNone(metadata["abortedUnsafe"])

    def test_aggregate_matches_rows_and_has_no_nan(self) -> None:
        rows = [
            {
                "candidate": "C",
                "modelCallCount": 1,
                "transportSuccess": True,
                "jsonParseSuccess": True,
                "localValidation": "accept",
                "semanticMatch": False,
                "commonGate": "reject",
                "intentMatch": True,
                "finalOutcome": "safe_reject",
                "contractClassification": "representable",
                "latencyMs": 10.0,
                "groundlessValueCounts": {},
                "arbitraryCandidateSelection": False,
                "spanExact": True,
                "roleExact": False,
                "correctionExact": True,
                "operation": "create",
            }
        ]
        aggregate = evaluator.aggregate_rows(rows)
        self.assertEqual(aggregate["totalRows"], 1)
        self.assertEqual(aggregate["totalModelCalls"], 1)
        self.assertNotIn("NaN", json.dumps(aggregate))

    def test_zero_call_control_case_is_not_a_transport_failure(self) -> None:
        case = next(
            row
            for row in evaluator.load_jsonl(evaluator.DEVELOPMENT_PATH)
            if row["meta"]["caseId"] == "dev06-complete-title-search-zero-candidates"
        )
        with patch.object(
            evaluator,
            "evaluate_candidate_c",
            return_value=(
                [],
                {
                    "jsonParseSuccess": False,
                    "localValidation": "reject",
                    "semanticMatch": False,
                    "commonGate": "not_run",
                    "gateCodes": [],
                    "intentMatch": False,
                    "effectComplete": False,
                    "finalAction": None,
                    "groundless": {},
                    "spanExact": False,
                    "roleExact": False,
                    "correctionExact": False,
                    "arbitrarySelection": False,
                },
            ),
        ):
            row = evaluator.evaluate_case(
                case,
                "C",
                evaluator.OllamaClient(),
                run_id="unit",
                phase="development",
                model_digest="digest",
                prompt_revision=2,
                dry_rows={},
            )
        self.assertTrue(row["transportSuccess"])
        self.assertTrue(row["jsonParseSuccess"])
        self.assertEqual(row["localValidation"], "not_run")
        self.assertEqual(row["modelCallCount"], 0)

    def test_research_module_has_no_backend_db_or_schedule_http_client(self) -> None:
        source = evaluator.Path(evaluator.__file__).read_text(encoding="utf-8")
        self.assertNotIn("sqlalchemy", source.lower())
        self.assertNotIn("create_engine", source)
        self.assertNotIn("/api/schedules", source)
        self.assertNotIn("ollama pull", source.lower())


if __name__ == "__main__":
    unittest.main()
