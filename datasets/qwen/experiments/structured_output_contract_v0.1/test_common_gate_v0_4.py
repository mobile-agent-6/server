from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common_gate_v0_3 as gate_v3
import common_gate_v0_4 as gate_v4


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


LOCKED = {row["meta"]["caseId"]: row for row in load_jsonl(HERE / "locked-cases-v0.1.jsonl")}
LK47 = LOCKED["lk47-unsupported-mixed-consent"]["context"]["turns"]
V03_HASHES = {
    "common_gate_v0_3.py": "7d72b31cbcd98cd021380fb72dde0f2da6b6d03254319d0f87ab25c8fbb97bd4",
    "contract-validation-manifest-v0.3.json": "383299544c257825aa3175ffc26981c3dbc5204ef0b015a3986b493bfe7c3d6d",
    "ollama-eval-manifest-v0.3.json": "8f4f2bb09d6eb7074f131e75474864295f95e7ae60a0e9cee2818585be339939",
}


def text_turn(number: int, text: str) -> dict:
    return {"turn": number, "inputType": "text", "inputSource": "typed_text", "text": text}


class CompletePolicyInputTests(unittest.TestCase):
    def test_v03_artifacts_are_unchanged(self) -> None:
        for name, expected in V03_HASHES.items():
            self.assertEqual(hashlib.sha256((HERE / name).read_bytes()).hexdigest(), expected, name)

    def test_current_turn_must_be_present(self) -> None:
        result = gate_v4.validate_complete_turn_prefix(LK47[:1], 2)
        self.assertFalse(result.accepted)
        self.assertEqual(result.code, "POLICY_INPUTS_INCOMPLETE")

    def test_duplicate_out_of_order_and_gap_are_rejected(self) -> None:
        duplicate = [text_turn(1, "첫 요청"), text_turn(2, "현재 요청"), text_turn(2, "중복")]
        self.assertEqual(gate_v4.validate_complete_turn_prefix(duplicate, 2).code, "POLICY_INPUTS_DUPLICATE_TURN")
        out_of_order = [text_turn(2, "현재 요청"), text_turn(1, "첫 요청")]
        self.assertEqual(gate_v4.validate_complete_turn_prefix(out_of_order, 2).code, "POLICY_INPUTS_OUT_OF_ORDER")
        gap = [text_turn(1, "첫 요청"), text_turn(3, "현재 요청")]
        self.assertEqual(gate_v4.validate_complete_turn_prefix(gap, 3).code, "POLICY_INPUTS_INCOMPLETE")

    def test_latest_unsupported_turn_cannot_be_omitted(self) -> None:
        full = [
            text_turn(1, "내일 오전 9시 입고 확인 등록해 줘"),
            text_turn(2, "모레 오전 10시 출고 확인 등록하고 문자도 보내 줘"),
        ]
        policy = gate_v3.analyze_policy(full, 2)
        proposal = {"operation": "create"}
        result = gate_v4.common_backend_gate_v0_4(
            proposal,
            {},
            policy=policy,
            policy_turns=full[:1],
            through_turn=2,
            executable=True,
        )
        self.assertFalse(result.accepted)
        self.assertEqual(result.code, "POLICY_INPUTS_INCOMPLETE")

    def test_valid_complete_prefixes_pass_for_all_locked_and_development_text_turns(self) -> None:
        checked = 0
        for name in ("development-cases-v0.1.jsonl", "locked-cases-v0.1.jsonl"):
            for case in load_jsonl(HERE / name):
                turns = case["context"]["turns"]
                for turn in turns:
                    if turn["inputType"] != "text":
                        continue
                    result = gate_v4.validate_complete_turn_prefix(turns, turn["turn"])
                    self.assertTrue(result.accepted, (case["meta"]["caseId"], turn["turn"], result.code))
                    checked += 1
        self.assertGreater(checked, 68)

    def test_non_executable_output_keeps_v03_behavior(self) -> None:
        result = gate_v4.common_backend_gate_v0_4(
            {},
            {},
            policy=None,
            policy_turns=None,
            through_turn=None,
            executable=False,
        )
        self.assertTrue(result.accepted)

    def test_source_has_no_oracle_or_case_dependency(self) -> None:
        source = (HERE / "common_gate_v0_4.py").read_text(encoding="utf-8")
        for forbidden in ("semantic", "oracle", "caseId", "expectedResult", "locked-cases"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
