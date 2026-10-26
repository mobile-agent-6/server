from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common_gate_v0_2 as gate_v2
import contracts
import validate_contracts


V01_HASHES = {
    "ollama_eval.py": "aeb43250fdcfd0e5ec38687b39b78b902c52489bba4b16f5888e8e8f69e98b70",
    "test_ollama_eval.py": "936dc8f6d29b128daf954366814919a2106b66beceaf522df63bc7b53b5d507c",
    "candidate-b-prompt-v0.1.txt": "8833c4e1e1ace53a5293781437e6094ece4cc15d9c014a16869aa7a9d3d02d8d",
    "candidate-c-prompt-v0.1.txt": "6352d687dd9093f783ff1c23e95d6f84433af30c3776a524aaa980a83db5f24b",
    "ollama-development-results-v0.1.jsonl": "e15fb91c1c4516b41e14bacad1a0c072c6795c262cf34bbf0d540bbe3a27b292",
    "ollama-locked-results-v0.1.jsonl": "8a49cd4c2b7b7da781bbaeea7fdca3017c95239fe2df964c3ee99116ff369e12",
    "ollama-eval-summary-v0.1.md": "c9768b193cd049631894eb5e96b86a5597e20b4cfa4b81d27acf88b6e41f5009",
    "ollama-eval-manifest-v0.1.json": "1e082d16eb8c33b92bac43d7784d864fc905eb660b4b1b6558cac7a1cd54ed5d",
    "contracts.py": "d68469a7e5a71ebdeb3ea693d895d1faecc8aa959125cfc94e15766dc82b5f62",
    "validate_contracts.py": "31fc4faac3f950cb03a21b01f9fac94edd69ac286d9ff7355b4c0aeb252ef7e2",
    "mutation-fixtures-v0.1.jsonl": "4f333dd03d55e1f53211cc1f76114f0125acc983967ff7c9395b8324268c8355",
    "contract-validation-results-v0.1.jsonl": "2a2810a2475263d7e1b014a688b094421acda8c9d30b178c8672f57282bf0916",
    "contract-validation-summary-v0.1.md": "b1936ffd117d83f5fc3293583d8413a105b2927400da4071f6e019564a783814",
    "contract-validation-manifest-v0.1.json": "9002cab5863b561d2d202e03829d45084b57452ab828d43bda44d479030a6537",
    "dry_mapping.py": "ecf7f23d8248c2562973ab8b4c98cf7506bbf287cd454f6d814cd128d9322bb0",
    "dry-mapping-results-v0.1.jsonl": "e8a202762b4d9f288e7da01b2b8e4c3c14cb119dbfdf8f65e4fc22ac1b78388f",
    "dry-mapping-summary-v0.1.md": "9955178c050cdef505cc1303bddd233cf05679bab19c14d58b4904a9fa1b6981",
    "dry-mapping-manifest-v0.1.json": "862765bd62049ba3bb73da08ad10f91268015af44a6fd8a259aeab7954e04ff4",
    "locked-cases-v0.1.jsonl": "17c7d4ef10aff8171d422c248431d3c34faaf03bf7282fab5dc3abcfa2389b57",
    "semantic-oracle-schema-v0.1.json": "cfe0ed947f6bd709e0e3ba60a8daf63fb1f201614cb11d90b823931b060a3b1d",
}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class CommonGateV02Tests(unittest.TestCase):
    def test_all_v01_protected_artifacts_are_unchanged(self) -> None:
        for name, expected in V01_HASHES.items():
            observed = hashlib.sha256((HERE / name).read_bytes()).hexdigest()
            self.assertEqual(observed, expected, name)

    def test_all_v02_mutations_pass_local_and_fail_common_gate(self) -> None:
        fixtures = load_jsonl(HERE / "mutation-fixtures-v0.2.jsonl")
        self.assertEqual(len(fixtures), 12)
        for fixture in fixtures:
            turns = {int(key): value for key, value in fixture["userTurns"].items()}
            parsed = contracts.validate_candidate_c(
                json.dumps(fixture["candidateOutput"], ensure_ascii=False),
                user_turns=turns,
            )
            result = gate_v2.common_backend_gate_v0_2(
                fixture["proposal"],
                fixture["gateContext"],
                candidate_c_output=parsed,
                user_turns=turns,
            )
            self.assertFalse(result.accepted, fixture["fixtureId"])
            self.assertEqual(result.code, fixture["expectedGateCode"], fixture["fixtureId"])

    def test_ideal_locked_and_development_roles_are_accepted_without_oracle_comparison(self) -> None:
        checked = 0
        for name in ("development-cases-v0.1.jsonl", "locked-cases-v0.1.jsonl"):
            for case in load_jsonl(HERE / name):
                output = validate_contracts.candidate_c_from_case(case)
                turns = {
                    turn["turn"]: turn["text"]
                    for turn in case["context"]["turns"]
                    if turn["inputType"] == "text"
                }
                parsed = contracts.validate_candidate_c(
                    json.dumps(output, ensure_ascii=False), user_turns=turns
                )
                result = gate_v2.validate_candidate_c_role_semantics(
                    parsed,
                    user_turns=turns,
                    reference_time=case["context"]["referenceTime"],
                )
                self.assertTrue(result.accepted, (case["meta"]["caseId"], result.code))
                checked += 1
        self.assertEqual(checked, 68)

    def test_valid_role_examples_and_same_role_multi_span_are_accepted(self) -> None:
        turns = {1: "모레 3시 반 말고 글피 오후로 입고 확인 등록해 줘"}
        value = {
            "operations": [{
                "index": 1,
                "kind": "create",
                "targetEvidence": {},
                "changeEvidence": {
                    "title": [{"turn": 1, "text": "입고 확인", "status": "adopted"}],
                    "date": [
                        {"turn": 1, "text": "모레", "status": "superseded"},
                        {"turn": 1, "text": "말고", "status": "correction_marker"},
                        {"turn": 1, "text": "글피", "status": "adopted"},
                    ],
                    "time": [
                        {"turn": 1, "text": "3시 반", "status": "adopted"},
                        {"turn": 1, "text": "오후", "status": "adopted"},
                    ],
                    "category": [{"turn": 1, "text": "입고", "status": "adopted"}],
                },
                "filterEvidence": {},
                "unsupportedEvidence": [],
                "missingInformation": [],
            }],
            "unsupportedEvidence": [],
            "missingInformation": [],
        }
        parsed = contracts.validate_candidate_c(json.dumps(value, ensure_ascii=False), user_turns=turns)
        result = gate_v2.validate_candidate_c_role_semantics(
            parsed,
            user_turns=turns,
            reference_time="2028-09-14T20:26:00+09:00",
        )
        self.assertTrue(result.accepted, result.code)

    def test_lk19_minimized_regression_was_accepted_by_v01_but_rejected_by_v02(self) -> None:
        fixture = next(
            item for item in load_jsonl(HERE / "mutation-fixtures-v0.2.jsonl")
            if item["fixtureId"] == "C2-12-lk19-role-regression"
        )
        turns = {int(key): value for key, value in fixture["userTurns"].items()}
        parsed = contracts.validate_candidate_c(
            json.dumps(fixture["candidateOutput"], ensure_ascii=False), user_turns=turns
        )
        self.assertTrue(contracts.common_backend_gate(fixture["proposal"], fixture["gateContext"]).accepted)
        result = gate_v2.common_backend_gate_v0_2(
            fixture["proposal"], fixture["gateContext"],
            candidate_c_output=parsed, user_turns=turns,
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.code, "C_ROLE_NOT_ALLOWED_FOR_OPERATION")

    def test_gate_source_has_no_locked_case_or_oracle_dependency(self) -> None:
        source = (HERE / "common_gate_v0_2.py").read_text(encoding="utf-8")
        self.assertNotIn("lk19", source)
        self.assertNotIn("locked-cases", source)
        self.assertNotIn("semantic-oracle", source)
        self.assertNotIn("expected_view", source)


if __name__ == "__main__":
    unittest.main()
