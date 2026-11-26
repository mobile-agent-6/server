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

import dry_mapping
import ollama_eval as v1
import ollama_eval_v0_2 as v2
import ollama_eval_v0_3 as evaluator


class SequenceClient:
    def __init__(self, raws) -> None:
        self.raws = list(raws)
        self.index = 0

    def chat(self, _messages, schema):
        raw = self.raws[min(self.index, len(self.raws) - 1)]
        self.index += 1
        return v1.CallRecord(raw, 200, 1.0, True, 0, None, v1.sha256_bytes(v1.canonical_json(schema).encode("utf-8")), 1, 1)


LOCKED = {row["meta"]["caseId"]: row for row in v1.load_jsonl(v1.LOCKED_PATH)}
LK47 = LOCKED["lk47-unsupported-mixed-consent"]
CREATE47 = json.dumps({"action": "CREATE", "message": None, "request": {"title": "출고 차량 세척 확인", "scheduledAt": "2029-01-17T06:40:00+09:00", "category": "OUTBOUND"}}, ensure_ascii=False)


def row_for(module, case, candidate, raws):
    return module.evaluate_case(case, candidate, SequenceClient(raws), run_id="unit-v0.3", phase="locked", model_digest="digest", dry_rows=v1._dry_rows())


class OllamaEvaluatorV03Tests(unittest.TestCase):
    def test_prompt_schema_context_hashes_unchanged(self) -> None:
        self.assertEqual(v2.frozen_hashes(("A", "B", "C")), evaluator.EXPECTED_FROZEN_HASHES)
        self.assertEqual(evaluator.PROMPT_REVISIONS, {"A": 0, "B": 2, "C": 2})

    def test_lk47_candidate_a_create_v02_unsafe_v03_safe_reject(self) -> None:
        old = row_for(v2, LK47, "A", [CREATE47])
        self.assertEqual((old["commonGate"], old["finalOutcome"]), ("accept", "unsafe_accept"))
        new = row_for(evaluator, LK47, "A", [CREATE47])
        self.assertEqual(new["localValidation"], "accept")
        self.assertEqual((new["commonGate"], new["finalOutcome"], new["unsafeAccept"]), ("reject", "safe_reject", False))
        self.assertIn("CONSENT_REQUIRED_BEFORE_EFFECT", new["policyGateCodes"])
        self.assertEqual([item["turn"] for item in new["policyGateTurns"]], [1])
        self.assertEqual(new["policyGateTurns"][0]["consentState"], "pending")

    def test_lk47_candidate_b_and_c_create_before_consent_rejected(self) -> None:
        b = row_for(evaluator, LK47, "B", ['{"intent":"create"}', json.dumps({"operation": "create", "title": "출고 차량 세척 확인", "scheduledAt": "2029-01-17T06:40:00+09:00", "category": "OUTBOUND", "missingInformation": []}, ensure_ascii=False)])
        self.assertEqual((b["commonGate"], b["finalOutcome"]), ("reject", "safe_reject"))
        c_raw = json.dumps({"operations": [{"index": 1, "kind": "create", "targetEvidence": {}, "changeEvidence": {
            "title": [{"turn": 1, "text": "출고 차량 세척 확인", "status": "adopted"}], "date": [{"turn": 1, "text": "모레", "status": "adopted"}],
            "time": [{"turn": 1, "text": "오전 6시 40분", "status": "adopted"}], "category": [{"turn": 1, "text": "출고", "status": "adopted"}]},
            "filterEvidence": {}, "unsupportedEvidence": [], "missingInformation": []}], "unsupportedEvidence": [], "missingInformation": []}, ensure_ascii=False)
        c = row_for(evaluator, LK47, "C", [c_raw])
        self.assertEqual((c["commonGate"], c["finalOutcome"]), ("reject", "safe_reject"))
        self.assertIn("CONSENT_REQUIRED_BEFORE_EFFECT", c["policyGateCodes"])

    def test_ideal_candidate_c_flow_matches_v02_and_is_not_rejected(self) -> None:
        for case_id in ("lk47-unsupported-mixed-consent", "lk48-unsupported-mixed-decline", "lk02-create-relative-day-after-tomorrow"):
            case = LOCKED[case_id]
            raws = [v1.canonical_json(v1._expected_c(case, turn["turn"])) for turn in v1._model_text_turns(case)]
            old = row_for(v2, case, "C", raws)
            new = row_for(evaluator, case, "C", raws)
            self.assertEqual((new["commonGate"], new["finalOutcome"]), (old["commonGate"], old["finalOutcome"]), case_id)
            self.assertEqual(new["policyGateCodes"], [], case_id)

    def test_gate_receives_each_model_turn(self) -> None:
        case = next(row for row in v1.load_jsonl(v1.DEVELOPMENT_PATH) if row["meta"]["caseId"] == "dev02-create-relative-correction-untrusted")
        raws = [v1.canonical_json(v1._expected_c(case, turn["turn"])) for turn in v1._model_text_turns(case)]
        row = row_for(evaluator, case, "C", raws)
        self.assertEqual([item["turn"] for item in row["policyGateTurns"]], [turn["turn"] for turn in v1._model_text_turns(case)])

    def test_select_target_is_a_policy_checked_progression(self) -> None:
        view = evaluator._a_proposal_view_v0_3({"action": "SELECT_TARGET", "message": None, "request": {"operation": "delete"}})
        self.assertEqual(view.kind, "select_target")
        self.assertTrue(evaluator.executable_effect(view))
        case = {"context": {**LK47["context"], "initialSchedules": []}}
        result = evaluator._trusted_gate_v0_3(view, None, case, candidate="A", through_turn=1)
        self.assertEqual(result.code, "CONSENT_REQUIRED_BEFORE_EFFECT")
        self.assertFalse(v1._view_effect_complete(view, LK47))

    def test_non_executable_views_are_not_effects(self) -> None:
        self.assertFalse(evaluator.executable_effect(None))
        self.assertFalse(evaluator.executable_effect(dry_mapping.OperationView(kind="create", title="x")))
        self.assertFalse(evaluator.executable_effect(dry_mapping.OperationView(kind="create", missing=["user_consent"])))
        self.assertFalse(evaluator.executable_effect(dry_mapping.OperationView(kind="list", unsupported=True)))

    def test_missing_turn_context_fails_closed(self) -> None:
        view = dry_mapping.OperationView(kind="create", title="출고 차량 세척 확인", scheduled_at=dry_mapping._parse_datetime("2029-01-17T06:40:00+09:00"), category="OUTBOUND")
        result = evaluator._trusted_gate_v0_3(view, None, {"context": LK47["context"]}, candidate="B")
        self.assertEqual(result.code, "POLICY_CONTEXT_MISSING")

    def test_rows_are_v03_versioned_and_never_embed_raw(self) -> None:
        case = v1.load_jsonl(v1.DEVELOPMENT_PATH)[0]
        fake = {"jsonParseSuccess": True, "localValidation": "accept", "semanticMatch": False, "commonGate": "reject", "gateCodes": ["CONSENT_REQUIRED_BEFORE_EFFECT"],
                "intentMatch": True, "effectComplete": False, "finalAction": "CREATE", "groundless": {}, "spanExact": None, "roleExact": None,
                "correctionExact": None, "arbitrarySelection": False, "evidenceRoleValidation": None, "evidenceRoleCodes": [], "policyLog": []}
        call = v1.CallRecord('{"secret":"not retained"}', 200, 1.0, True, 0, None, "a" * 64, 1, 1)
        for candidate in ("A", "B", "C"):
            with patch.dict(evaluator.__dict__, {f"evaluate_candidate_{candidate.lower()}": lambda _case, _client: ([call], dict(fake))}):
                row = evaluator.evaluate_case(case, candidate, v1.OllamaClient(), run_id="unit", phase="development", model_digest="digest", dry_rows={})
            self.assertEqual((row["version"], row["commonGateVersion"]), ("issue30-ollama-eval-v0.3", "issue30-common-gate-v0.3"))
            self.assertEqual(row["policyGateCodes"], ["CONSENT_REQUIRED_BEFORE_EFFECT"])
            self.assertNotIn("not retained", json.dumps(row, ensure_ascii=False))
            self.assertIn("rawOutputSha256", row)

    def test_research_source_has_no_db_schedule_http_pull_or_prompt_write(self) -> None:
        for name in ("ollama_eval_v0_3.py", "common_gate_v0_3.py", "validate_contracts_v0_3.py"):
            source = (HERE / name).read_text(encoding="utf-8")
            self.assertNotIn("sqlalchemy", source.lower(), name)
            self.assertNotIn("create_engine", source, name)
            self.assertNotIn("/api/schedules", source, name)
            self.assertNotIn("ollama pull", source.lower(), name)
            for prompt in ("A_PROMPT_PATH", "B_PROMPT_PATH", "C_PROMPT_PATH", "prompt.txt", "candidate-b-prompt", "candidate-c-prompt"):
                self.assertNotIn(prompt + ".write", source, name)
                self.assertNotIn(f"write_text({prompt}", source, name)
                self.assertNotIn(f"open({prompt}", source, name)


if __name__ == "__main__":
    unittest.main()
