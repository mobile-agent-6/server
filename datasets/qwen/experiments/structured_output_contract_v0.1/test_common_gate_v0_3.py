from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import common_gate_v0_3 as gate
import ollama_eval_v0_3 as evaluator
import test_common_gate_v0_2 as v02_tests
import validate_contracts_v0_3 as validation


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


LOCKED = {row["meta"]["caseId"]: row for row in load_jsonl(HERE / "locked-cases-v0.1.jsonl")}
LK47 = LOCKED["lk47-unsupported-mixed-consent"]["context"]["turns"]
LK48 = LOCKED["lk48-unsupported-mixed-decline"]["context"]["turns"]
REQUEST47 = LK47[0]["text"]


def text_turns(*items):
    turns = []
    for number, item in enumerate(items, 1):
        text, answers = item if isinstance(item, tuple) else (item, None)
        turn = {"turn": number, "inputType": "text", "inputSource": "typed_text", "text": text}
        if answers:
            turn["answersQuestionOfTurn"] = answers
        turns.append(turn)
    return turns


def policy_result(turns, through, *, executable=True, policy="auto", proposal=None):
    value = gate.analyze_policy(turns, through) if policy == "auto" else policy
    return gate.policy_gate(proposal if proposal is not None else {"operation": "create"}, value, turns=turns, through_turn=through, executable=executable)


class TrustedPolicyAnalyzerTests(unittest.TestCase):
    def test_v01_and_v02_protected_artifacts_unchanged(self) -> None:
        for name, expected in v02_tests.V01_HASHES.items():
            self.assertEqual(hashlib.sha256((HERE / name).read_bytes()).hexdigest(), expected, name)
        self.assertEqual(evaluator.assert_v02_protected(), evaluator.V02_PROTECTED)

    def test_lk47_create_before_consent_rejected_and_explicit_consent_granted(self) -> None:
        first = gate.analyze_policy(LK47, 1)
        self.assertEqual((first.consentRequired, first.consentState, first.consentQuestionTurn), (True, "pending", 1))
        self.assertEqual({cue.feature for cue in first.unsupportedCues}, {"text_message"})
        self.assertEqual(policy_result(LK47, 1).code, "CONSENT_REQUIRED_BEFORE_EFFECT")
        second = gate.analyze_policy(LK47, 2)
        self.assertEqual((second.consentState, second.consentDecisionTurn), ("granted", 2))
        self.assertTrue(policy_result(LK47, 2).accepted)
        self.assertEqual(gate.classify_consent_answer("그래, 문자는 빼고 등록해"), "granted")

    def test_lk48_decline_blocks_any_effect(self) -> None:
        self.assertEqual(policy_result(LK48, 1).code, "CONSENT_REQUIRED_BEFORE_EFFECT")
        self.assertEqual(gate.analyze_policy(LK48, 2).consentState, "declined")
        self.assertEqual(policy_result(LK48, 2).code, "CONSENT_DECLINED")
        self.assertTrue(policy_result(LK48, 2, executable=False).accepted)

    def test_reminder_on_request_requires_consent(self) -> None:
        turns = text_turns("내일 오후 2시 입고 라벨 점검 등록하고 알림도 켜 줘")
        self.assertEqual(gate.analyze_policy(turns, 1).consentState, "pending")
        self.assertEqual(policy_result(turns, 1).code, "CONSENT_REQUIRED_BEFORE_EFFECT")

    def test_unsupported_only_creates_no_schedule(self) -> None:
        turns = text_turns("30분 전에 문자 보내 줘")
        value = gate.analyze_policy(turns, 1)
        self.assertTrue(value.unsupportedOnly)
        self.assertFalse(value.consentRequired)
        self.assertEqual(policy_result(turns, 1).code, "UNSUPPORTED_ONLY_NO_SCHEDULE_EFFECT")

    def test_supported_only_unchanged(self) -> None:
        turns = text_turns("모레 오전 8시 반에 출고 송장 대조 넣어 줘")
        value = gate.analyze_policy(turns, 1)
        self.assertEqual((value.consentState, value.unsupportedOnly, value.consentRequired), ("not_required", False, False))
        self.assertTrue(policy_result(turns, 1).accepted)

    def test_title_word_is_not_a_message_command(self) -> None:
        turns = text_turns("내일 오전 10시 문자 발송 대장 점검 등록해 줘")
        value = gate.analyze_policy(turns, 1)
        self.assertEqual(value.unsupportedCues, ())
        self.assertEqual(value.unknownCueExpressions, ())
        self.assertTrue(policy_result(turns, 1).accepted)

    def test_exclusion_inside_the_request_needs_no_consent(self) -> None:
        for text in ("내일 오후 3시 반품 회수 확인 등록하는데 문자는 보내지 말고 일정만 등록해", "내일 오후 1시 재고 위치 점검 알림 없이 일정만 넣어 줘"):
            turns = text_turns(text)
            value = gate.analyze_policy(turns, 1)
            self.assertEqual(value.consentState, "not_required", text)
            self.assertTrue(value.exclusionCues, text)
            self.assertTrue(policy_result(turns, 1).accepted, text)

    def test_previous_consent_is_not_reused_for_a_new_task(self) -> None:
        turns = text_turns(REQUEST47, ("응", 1), "내일 오후 2시 입고 라벨 점검 등록하고 알림도 켜 줘")
        self.assertEqual(gate.analyze_policy(turns, 2).consentState, "granted")
        value = gate.analyze_policy(turns, 3)
        self.assertEqual((value.consentState, value.consentQuestionTurn), ("pending", 3))
        self.assertEqual(policy_result(turns, 3).code, "CONSENT_REQUIRED_BEFORE_EFFECT")

    def test_consent_must_answer_the_backend_question(self) -> None:
        unlinked = text_turns(REQUEST47, "그래, 문자는 빼고 등록해")
        self.assertEqual(gate.analyze_policy(unlinked, 2).consentState, "pending")
        stale = text_turns(REQUEST47, "뭐라고?", ("그래, 문자는 빼고 등록해", 1))
        self.assertEqual(gate.analyze_policy(stale, 3).consentState, "pending")
        fresh = text_turns(REQUEST47, "뭐라고?", ("그래, 문자는 빼고 등록해", 2))
        self.assertEqual(gate.analyze_policy(fresh, 3).consentState, "granted")

    def test_unknown_expressions_and_answers_fail_closed(self) -> None:
        turns = text_turns("내일 오전 9시 입고 검수 등록하고 문자 좀 부탁해")
        self.assertEqual(policy_result(turns, 1).code, "UNSUPPORTED_EXPRESSION_UNKNOWN")
        for answer in ("음, 글쎄", "응, 그리고 알림도 꼭 켜 줘", "응 아니"):
            turns = text_turns(REQUEST47, (answer, 1))
            self.assertEqual(gate.analyze_policy(turns, 2).consentState, "pending", answer)
            self.assertEqual(policy_result(turns, 2).code, "CONSENT_REQUIRED_BEFORE_EFFECT", answer)

    def test_missing_invalid_tampered_and_model_claimed_policy_fail_closed(self) -> None:
        self.assertEqual(policy_result(LK47, 2, policy=None).code, "POLICY_CONTEXT_MISSING")
        value = gate.analyze_policy(LK47, 1).to_dict()
        invalid = dict(value, consentState="maybe")
        self.assertEqual(policy_result(LK47, 1, policy=invalid).code, "POLICY_CONTEXT_INVALID")
        tampered = dict(value, consentState="granted")
        self.assertEqual(policy_result(LK47, 1, policy=tampered).code, "POLICY_CONTEXT_TAMPERED")
        forged = dict(value, consentState="granted", consentDecisionTurn=1)
        forged["fingerprint"] = gate.fingerprint(forged)
        self.assertEqual(policy_result(LK47, 1, policy=forged).code, "POLICY_CONTEXT_TAMPERED")
        self.assertEqual(policy_result(LK47, 1, policy="not a policy").code, "POLICY_CONTEXT_INVALID")
        claimed = policy_result(LK47, 2, proposal={"operation": "create", "userConsentGranted": True})
        self.assertEqual(claimed.code, "CANDIDATE_POLICY_CLAIM")
        self.assertEqual(policy_result(LK47, 2, policy=gate.analyze_policy(LK47, 2), proposal=None).code, None)

    def test_ask_or_safe_question_is_never_an_effect(self) -> None:
        for turns, through in ((LK47, 1), (LK48, 2), (text_turns("30분 전에 문자 보내 줘"), 1)):
            self.assertTrue(policy_result(turns, through, executable=False).accepted)
        self.assertTrue(gate.policy_gate(None, None, turns=None, through_turn=None, executable=False).accepted)

    def test_policy_depends_only_on_backend_conversation_state(self) -> None:
        case = copy.deepcopy(LOCKED["lk47-unsupported-mixed-consent"])
        stripped = copy.deepcopy(case["context"]["turns"])
        self.assertEqual(gate.analyze_policy(stripped, 1).to_dict(), gate.analyze_policy(case["context"]["turns"], 1).to_dict())
        with self.assertRaises(ValueError):
            gate.analyze_policy([dict(case["context"]["turns"][0], expectedResult={"kind": "outcome"})], 1)
        source = (HERE / "common_gate_v0_3.py").read_text(encoding="utf-8")
        for forbidden in ('["semantics"]', "['semantics']", '["meta"]', "caseId", "expectedTurns", "expectedResult", "locked-cases"):
            self.assertNotIn(forbidden, source)

    def test_deterministic_fixtures_and_ideal_flows(self) -> None:
        rows = validation.fixture_rows()
        self.assertEqual(len(rows), 31)
        mutations = [row for row in rows if row["fixtureKind"] == "mutation"]
        self.assertTrue(all(row["localValidation"] == "accept" and row["commonGate"] == "reject" for row in mutations))
        self.assertEqual(sum(row["unsafeAccept"] for row in rows), 0)
        self.assertEqual(sum(row["falseReject"] for row in rows), 0)
        lk47_a = next(row for row in rows if row["fixtureId"] == "M3-01-lk47-A-create-before-consent")
        self.assertEqual((lk47_a["commonGateV02"], lk47_a["commonCode"]), ("accept", "CONSENT_REQUIRED_BEFORE_EFFECT"))
        ideal = validation.ideal_policy_rows()
        self.assertEqual(len(ideal), 68)
        self.assertEqual(sum(row["falseReject"] for row in ideal), 0)


if __name__ == "__main__":
    unittest.main()
