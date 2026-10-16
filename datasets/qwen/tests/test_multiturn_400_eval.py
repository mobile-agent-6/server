"""Checks for the DATA-only 400-dialogue evaluation proposal."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

EXPERIMENT = Path(__file__).resolve().parents[1] / "experiments" / "multiturn_400_v0.1"
sys.path.insert(0, str(EXPERIMENT))

import run_eval as evaluator  # noqa: E402
from guards import check_question, validate_write  # noqa: E402
from runtime.schedule_assistant.service import ConversationService, Reply  # noqa: E402


class Multiturn400EvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = {case["id"]: case for case in evaluator.load_cases(
            EXPERIMENT / "train" / "scenarios_UNREVIEWED.jsonl")}
        cls.rubric = json.loads((EXPERIMENT / "question_rubric.json").read_text(encoding="utf-8"))

    def test_first_utterances_do_not_overlap_across_splits(self) -> None:
        first_utterances = {
            split: {case["turns"][0]["user"] for case in evaluator.load_cases(
                EXPERIMENT / split / "scenarios_UNREVIEWED.jsonl")}
            for split in ("train", "validation", "test")
        }
        for left, right in (("train", "validation"), ("train", "test"),
                            ("validation", "test")):
            self.assertFalse(first_utterances[left] & first_utterances[right],
                             f"identical first utterances in {left} and {right}")

    def test_wrong_displayed_query_result_fails_even_when_get_and_state_match(self) -> None:
        case = self.cases["in-list-range"]
        original_service = evaluator.Service

        class WrongDisplay(original_service):
            def handle(self, utterance: str) -> Reply:
                reply = super().handle(utterance)
                if reply.kind == "answer":
                    return Reply("answer", "조회된 일정이 없어요.", reply.schedule)
                return reply

        client = evaluator.ReferenceClient(
            [action for turn in case["turns"] for action in turn["model_outputs"]])
        with patch.object(evaluator, "Service", WrongDisplay):
            result = evaluator.run_case(case, client, self.rubric)
        self.assertFalse(result["provisional_match"])
        self.assertEqual(result["stop_reason"], "semantic_path_mismatch")
        self.assertTrue(any(check["name"] == "displayed_answer" and not check["match"]
                            for turn in result["turns"] for check in turn["checks"]))

    def test_shared_controller_display_bug_fails_independent_fact_check(self) -> None:
        case = self.cases["in-list-range"]
        original_handle = ConversationService.handle

        def wrong_for_both(self, utterance):
            reply = original_handle(self, utterance)
            if reply.kind == "answer":
                return Reply("answer", "조회된 일정이 없어요.", reply.schedule)
            return reply

        outputs = [action for turn in case["turns"] for action in turn["model_outputs"]]
        with patch.object(ConversationService, "handle", wrong_for_both):
            result = evaluator.run_case(case, evaluator.ReferenceClient(outputs), self.rubric)
        self.assertFalse(result["provisional_match"])
        self.assertEqual(result["stop_reason"], "semantic_path_mismatch")
        self.assertTrue(any(check["name"].startswith("answer_title:") and not check["match"]
                            for turn in result["turns"] for check in turn["checks"]))

    def test_guarded_success_keeps_raw_model_error_visible(self) -> None:
        case = self.cases["in-list-range"]
        outputs = [dict(action) for turn in case["turns"] for action in turn["model_outputs"]]
        outputs[1] = {**outputs[1], "request": {**outputs[1]["request"], "category": "RETURN"}}
        result = evaluator.run_case(case, evaluator.ReferenceClient(outputs), self.rubric)
        self.assertTrue(result["provisional_match"])
        self.assertFalse(result["raw_json_path_exact"])
        self.assertFalse(result["turns"][1]["raw_model_checks"][0]["json_exact"])
        self.assertTrue(result["turns"][1]["filter_events"])

    def test_missing_and_extra_model_calls_fail_raw_path(self) -> None:
        expected = [{"action": "ASK", "message": "언제인가요?", "request": None}]
        self.assertFalse(evaluator.raw_model_checks(expected, [])[0]["action_exact"])
        extra = evaluator.raw_model_checks([], [{"raw": json.dumps(expected[0]),
                                                   "action": "ASK", "parse_error": None,
                                                   "error": None}])
        self.assertFalse(extra[0]["json_exact"])

    def test_statement_keywords_do_not_pass_question_rubric(self) -> None:
        statements = [
            ("selection", "번호를 제가 정하겠습니다.", True),
            ("change", "변경 내용을 확인했습니다.", False),
            ("date", "어느 날짜인지 기록했습니다.", False),
            ("task", "어떤 업무인지 모르겠습니다.", False),
            ("category", "입고 반품 처리하겠습니다.", False),
            ("time", "몇 시인지 기록했습니다.", False),
        ]
        for kind, message, selection_active in statements:
            with self.subTest(kind=kind):
                self.assertFalse(check_question(kind, message, selection_active))
        self.assertTrue(check_question("selection", "2번까지 번호를 말씀해 주세요.", True))
        self.assertTrue(check_question("selection", "1번부터 2번 중 번호를 말씀해 주세요. 취소할 수도 있어요.", True))
        self.assertTrue(check_question("date", "언제 일정으로 등록할까요?"))

    def test_update_guard_requires_explicit_changed_fields(self) -> None:
        now = evaluator.datetime.fromisoformat("2026-10-16T09:00:00+09:00")
        proposed = [
            ({"title": "새 제목"}, ["일정을 바꿔줘."]),
            ({"completed": True}, ["일정 상태를 바꿔줘."]),
            ({"category": "OTHER"}, ["일정을 바꿔줘."]),
        ]
        for body, users in proposed:
            with self.subTest(body=body):
                self.assertIsNotNone(validate_write(body, users, now, "update"))
        self.assertIsNone(validate_write({"title": "새 제목"},
                                         ["제목을 새 제목으로 바꿔줘."], now, "update"))
        self.assertIsNone(validate_write({"completed": True},
                                         ["완료로 표시해줘."], now, "update"))
        self.assertIsNone(validate_write({"category": "OTHER"},
                                         ["카테고리를 기타로 바꿔줘."], now, "update"))


if __name__ == "__main__":
    unittest.main()
