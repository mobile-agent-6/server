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
import ollama_eval as v1
import ollama_eval_v0_2 as evaluator


class StaticClient:
    def __init__(self, raw: str) -> None:
        self.raw = raw

    def chat(self, _messages, schema):
        return v1.CallRecord(
            self.raw,
            200,
            1.0,
            True,
            0,
            None,
            v1.sha256_bytes(v1.canonical_json(schema).encode("utf-8")),
            1,
            1,
        )


class OllamaEvaluatorV02Tests(unittest.TestCase):
    def test_prompt_schema_and_context_hashes_are_identical_to_v01(self) -> None:
        self.assertEqual(evaluator.frozen_hashes(("A", "B", "C")), evaluator.EXPECTED_FROZEN_HASHES)
        self.assertEqual(evaluator.PROMPT_REVISIONS, {"A": 0, "B": 2, "C": 2})

    def test_lk19_role_regression_is_safe_rejected(self) -> None:
        case = next(
            row for row in v1.load_jsonl(v1.LOCKED_PATH)
            if row["meta"]["caseId"] == "lk19-list-seoul-day-boundary-untrusted"
        )
        raw = json.dumps({
            "operations": [{
                "index": 1,
                "kind": "list",
                "targetEvidence": {},
                "changeEvidence": {
                    "title": [{"turn": 1, "text": "2028년 9월 18일 일정 보여 줘", "status": "adopted"}],
                },
                "filterEvidence": {
                    "date": [{"turn": 1, "text": "2028년 9월 18일", "status": "adopted"}],
                    "futureScope": [{"turn": 1, "text": "일정 보여 줘", "status": "adopted"}],
                },
                "unsupportedEvidence": [],
                "missingInformation": [],
            }],
            "unsupportedEvidence": [],
            "missingInformation": [],
        }, ensure_ascii=False)
        calls, details = evaluator.evaluate_candidate_c(case, StaticClient(raw))
        self.assertEqual(len(calls), 1)
        self.assertEqual(details["localValidation"], "accept")
        self.assertEqual(details["evidenceRoleValidation"], "reject")
        self.assertEqual(details["commonGate"], "reject")
        self.assertIn("C_ROLE_NOT_ALLOWED_FOR_OPERATION", details["gateCodes"])
        row = evaluator.evaluate_case(
            case,
            "C",
            StaticClient(raw),
            run_id="unit-v0.2",
            phase="locked",
            model_digest="digest",
            dry_rows=v1._dry_rows(),
        )
        self.assertEqual(row["finalOutcome"], "safe_reject")
        self.assertFalse(row["unsafeAccept"])

    def test_all_three_candidates_use_v02_versioned_results(self) -> None:
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
            "spanExact": None,
            "roleExact": None,
            "correctionExact": None,
            "arbitrarySelection": False,
            "evidenceRoleValidation": None,
            "evidenceRoleCodes": [],
        }
        fake_call = v1.CallRecord("{}", 200, 1.0, True, 0, None, "a" * 64, 1, 1)
        case = v1.load_jsonl(v1.DEVELOPMENT_PATH)[0]
        for candidate in ("A", "B", "C"):
            with patch.dict(
                evaluator.__dict__,
                {f"evaluate_candidate_{candidate.lower()}": lambda _case, _client: ([fake_call], dict(fake_details))},
            ):
                # evaluate_case resolves its dispatch table at call time.
                row = evaluator.evaluate_case(
                    case,
                    candidate,
                    v1.OllamaClient(),
                    run_id="unit",
                    phase="development",
                    model_digest="digest",
                    dry_rows={},
                )
            self.assertEqual(row["version"], evaluator.EVAL_VERSION)
            self.assertEqual(row["commonGateVersion"], "issue30-common-gate-v0.2")
            self.assertEqual(row["promptRevision"], evaluator.PROMPT_REVISIONS[candidate])

    def test_v02_role_rejection_is_model_semantic_not_transport(self) -> None:
        row = {
            "transportSuccess": True,
            "jsonParseSuccess": True,
            "localValidation": "accept",
            "semanticMatch": False,
            "commonGate": "reject",
        }
        cause = v1._primary_cause(
            row["transportSuccess"], row["jsonParseSuccess"], row["localValidation"],
            row["semanticMatch"], row["commonGate"], {"mismatchCause": None},
        )
        self.assertEqual(cause, "MODEL_SEMANTIC")

    def test_result_rows_never_embed_raw_output(self) -> None:
        case = v1.load_jsonl(v1.DEVELOPMENT_PATH)[0]
        with patch.object(
            evaluator,
            "evaluate_candidate_c",
            return_value=(
                [v1.CallRecord('{"secret":"not retained"}', 200, 1.0, True, 0, None, "a" * 64, 1, 1)],
                {
                    "jsonParseSuccess": True,
                    "localValidation": "accept",
                    "semanticMatch": False,
                    "commonGate": "reject",
                    "gateCodes": ["C_ROLE_NOT_ALLOWED_FOR_OPERATION"],
                    "intentMatch": True,
                    "effectComplete": False,
                    "finalAction": "create",
                    "groundless": {},
                    "spanExact": False,
                    "roleExact": False,
                    "correctionExact": True,
                    "arbitrarySelection": False,
                    "evidenceRoleValidation": "reject",
                    "evidenceRoleCodes": ["C_ROLE_NOT_ALLOWED_FOR_OPERATION"],
                },
            ),
        ):
            row = evaluator.evaluate_case(
                case, "C", v1.OllamaClient(), run_id="unit", phase="development",
                model_digest="digest", dry_rows={},
            )
        encoded = json.dumps(row, ensure_ascii=False)
        self.assertNotIn("not retained", encoded)
        self.assertIn("rawOutputSha256", row)
        self.assertIn("rawOutputByteLength", row)

    def test_research_source_has_no_db_schedule_http_or_prompt_mutation(self) -> None:
        source = (HERE / "ollama_eval_v0_2.py").read_text(encoding="utf-8")
        self.assertNotIn("sqlalchemy", source.lower())
        self.assertNotIn("create_engine", source)
        self.assertNotIn("/api/schedules", source)
        self.assertNotIn("ollama pull", source.lower())
        self.assertNotIn("write_text(v1.B_PROMPT_PATH", source)
        self.assertNotIn("write_text(v1.C_PROMPT_PATH", source)


if __name__ == "__main__":
    unittest.main()
