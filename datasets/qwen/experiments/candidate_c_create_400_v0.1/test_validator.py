from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import validator


class PilotValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = validator.load_jsonl(validator.PILOT)
        cls.summary = validator.run_validation(write_artifacts=False)

    def test_exactly_30_cases_and_six_per_bucket(self) -> None:
        self.assertEqual(len(self.cases), 30)
        counts = Counter(validator._notes(case)["bucket"] for case in self.cases)
        self.assertEqual(set(counts), validator.BUCKETS)
        self.assertEqual(set(counts.values()), {6})

    def test_pilot_is_proposed_unreviewed_development_only(self) -> None:
        for case in self.cases:
            self.assertEqual(case["meta"]["status"], "PROPOSED")
            self.assertEqual(case["meta"]["reviewState"], "single-review proposed")
            self.assertEqual(case["meta"]["datasetRole"], "development_probe")
            self.assertEqual(validator._notes(case)["state"], "PROPOSED_UNREVIEWED")

    def test_out_of_order_corrections_use_adopted_only_policy(self) -> None:
        gaps = [
            gap
            for case in self.cases
            for gap in case["semantics"]["contractGaps"]
        ]
        self.assertEqual(gaps, [])
        decisions = self.summary["annotationDecisions"]
        self.assertEqual({item["caseId"] for item in decisions}, {
            "c3-create-pilot-17", "c3-create-pilot-18",
        })
        self.assertTrue(all(not item["correctionProvenanceScored"] for item in decisions))
        self.assertEqual(self.summary["expansionDecision"], "READY_FOR_INDEPENDENT_REVIEW")

    def test_gold_is_safe_and_mutation_detection_has_no_miss(self) -> None:
        self.assertEqual(self.summary["unsafeAccepts"], 0)
        self.assertEqual(self.summary["wrongWriteValueAccepts"], 0)
        self.assertEqual(self.summary["supersededExecutedAccepts"], 0)
        self.assertEqual(self.summary["mappingExceptionCount"], 0)
        self.assertEqual(self.summary["mutationFixtureCount"], 20)
        self.assertEqual(self.summary["mutationUnsafeAccepts"], 2)
        self.assertEqual(self.summary["mutationFinalAccepts"], 0)
        self.assertEqual(self.summary["mutationDetectionFailures"], 0)
        self.assertGreaterEqual(self.summary["mutationSafetyPathCount"], 5)
        self.assertEqual(self.summary["semanticSchema"]["errorCount"], 0)
        self.assertEqual(self.summary["contamination"]["exactMatches"], 0)
        self.assertEqual(self.summary["contamination"]["jaccardAtLeast0_7"], 0)
        self.assertEqual(self.summary["contamination"]["utteranceExactMatches"], 0)
        self.assertEqual(self.summary["contamination"]["utteranceJaccardAtLeast0_7"], 0)

    def test_mixed_unsupported_is_not_duplicated_at_top_level(self) -> None:
        for case in self.cases:
            if not case["semantics"]["unsupportedRequests"]:
                continue
            output = validator.candidate_c_output(case, 1)
            self.assertEqual(output["unsupportedEvidence"], [])
            self.assertTrue(output["operations"][0]["unsupportedEvidence"])

    def test_training_preview_excludes_contract_gaps_and_no_model_call_turns(self) -> None:
        expected = 0
        for case in self.cases:
            expected += sum(
                item["modelCallPolicy"] == "model_call_expected"
                for item in case["semantics"]["expectedTurns"]
            )
        self.assertEqual(self.summary["trainingPreviewRows"], expected)
        self.assertEqual(self.summary["trainingInputTargetConflicts"], 0)

    def test_training_preview_uses_evaluator_evidence_stage_bytes(self) -> None:
        cases = {case["meta"]["caseId"]: case for case in self.cases}
        rows = validator.load_jsonl(validator.PREVIEW)
        self.assertEqual(len(rows), self.summary["trainingPreviewRows"])
        for row in rows:
            expected = validator.ollama_eval.render_extraction_context(
                cases[row["caseId"]], int(row["turn"]), stage="EVIDENCE"
            )
            self.assertEqual(row["input"].encode("utf-8"), expected.encode("utf-8"))
            self.assertEqual(json.loads(row["input"])["stage"], "EVIDENCE")
            self.assertEqual(row["messages"][0]["content"], validator.ollama_eval.prompt_text("C"))
            self.assertEqual(row["messages"][1], {"role": "user", "content": expected})
            self.assertEqual(row["messages"][2], {"role": "assistant", "content": row["target"]})

    def test_turn_targets_keep_values_visible_at_that_turn(self) -> None:
        cases = {case["meta"]["caseId"]: case for case in self.cases}
        targets = validator.load_target_map()
        checks = {
            "c3-create-pilot-09": {"date": "내일", "time": "3시"},
            "c3-create-pilot-11": {"date": "내일", "time": "오전 9시"},
            "c3-create-pilot-18": {"date": "모레", "time": "오전 10시 반"},
            "c3-create-pilot-19": {"date": "내일", "time": "오전 9시"},
            "c3-create-pilot-24": {"date": "2028년 2월 18일", "time": "7시"},
        }
        for case_id, expected in checks.items():
            output = validator.candidate_c_output(cases[case_id], 1, targets)
            evidence = output["operations"][0]["changeEvidence"]
            for slot, text in expected.items():
                self.assertIn(text, [item["text"] for item in evidence[slot]])

    def test_case18_is_pending_create_before_cross_turn_replacement(self) -> None:
        case = next(case for case in self.cases if case["meta"]["caseId"] == "c3-create-pilot-18")
        first = case["semantics"]["expectedTurns"][0]
        self.assertEqual(first["expectedResult"]["kind"], "need_more_info")
        self.assertEqual(first["expectedWriteCount"], 0)
        self.assertIn("task_or_title", [
            item["item"] for item in case["semantics"]["operations"][0]["missingInformation"]
        ])
        self.assertEqual(
            first["expectedResult"]["questionPurpose"],
            "task_or_title_unclear",
        )

    def test_failure_and_question_response_oracles_are_consistent(self) -> None:
        by_id = {case["meta"]["caseId"]: case for case in self.cases}
        failure = by_id["c3-create-pilot-21"]["semantics"]["expectedTurns"][0]
        self.assertEqual(failure["expectedResult"]["kind"], "failure")
        self.assertEqual(failure["requiredResponseFacts"], [])

        bad_question = json.loads(json.dumps(by_id["c3-create-pilot-18"]))
        bad_question["semantics"]["expectedTurns"][0]["expectedResult"][
            "questionPurpose"
        ] = "missing_information"
        errors = validator._lint_semantic_subset([
            bad_question,
            *[
                case
                for case in self.cases
                if case["meta"]["caseId"] != "c3-create-pilot-18"
            ],
        ])
        self.assertTrue(any("questionPurpose" in item for item in errors))

        bad_failure = json.loads(json.dumps(by_id["c3-create-pilot-21"]))
        bad_failure["semantics"]["expectedTurns"][0]["requiredResponseFacts"] = [
            {"fact": "missing_item_question", "source": "oracle_constant", "operationIndex": 1}
        ]
        errors = validator._lint_semantic_subset([
            bad_failure,
            *[
                case
                for case in self.cases
                if case["meta"]["caseId"] != "c3-create-pilot-21"
            ],
        ])
        self.assertTrue(any("failure result" in item for item in errors))

    def test_conflicting_adopted_values_fail_closed_in_profile(self) -> None:
        rows = validator.load_jsonl(validator.MUTATION_RESULTS)
        selected = [
            item for item in rows if item["fixtureId"].startswith("m-18-conflicting-adopted-time")
        ]
        self.assertEqual(len(selected), 3)
        for row in selected:
            self.assertEqual(row["safetyClass"], "profile_reject")
            self.assertIn("C_PROFILE_CONFLICTING_ADOPTED_VALUES", row["local"])
            self.assertFalse(row["writePossible"])
        fixture = next(
            item
            for item in validator.load_jsonl(validator.MUTATIONS)
            if item["fixtureId"] == "m-18-conflicting-adopted-time"
        )
        case = next(
            item for item in self.cases if item["meta"]["caseId"] == fixture["caseId"]
        )
        for output in (fixture["output"], json.loads(json.dumps(fixture["output"]))):
            if output is not fixture["output"]:
                output["operations"][0]["changeEvidence"]["time"].reverse()
            parsed = validator.contracts.validate_candidate_c(
                validator.canonical(output),
                user_turns=validator._text_turns(case, int(fixture["turn"])),
            )
            with self.assertRaisesRegex(
                validator.CreateProfileError,
                "changeEvidence.time",
            ):
                validator.validate_create_profile(
                    parsed,
                    reference_time=case["context"]["referenceTime"],
                )

    def test_fully_normalizable_equivalent_adopted_values_are_allowed(self) -> None:
        case = next(case for case in self.cases if case["meta"]["caseId"] == "c3-create-pilot-11")
        output = validator.candidate_c_output(case, 2)
        change = output["operations"][0]["changeEvidence"]
        change["date"].insert(0, {"turn": 1, "text": "내일", "status": "adopted"})
        change["time"].insert(0, {"turn": 1, "text": "오전 9시", "status": "adopted"})
        parsed = validator.contracts.validate_candidate_c(
            validator.canonical(output),
            user_turns=validator._text_turns(case, 2),
        )
        validator.validate_create_profile(
            parsed,
            reference_time=case["context"]["referenceTime"],
        )

    def test_superseded_execution_mutations_are_detected_as_unsafe(self) -> None:
        rows = validator.load_jsonl(validator.MUTATION_RESULTS)
        selected = {
            item["fixtureId"]: item
            for item in rows
            if item["fixtureId"] in {
                "m-14-superseded-time-executed",
                "m-16-superseded-title-category-executed",
            }
        }
        self.assertEqual(len(selected), 2)
        for row in selected.values():
            self.assertTrue(row["writePossible"])
            self.assertTrue(row["unsafeAccept"])
            self.assertEqual(row["safetyClass"], "unsafe_superseded_executed")
            self.assertTrue(row["writeValueMismatches"])
            self.assertFalse(row["finalAccept"])

    def test_category_correction_uses_all_three_roles(self) -> None:
        case = next(case for case in self.cases if case["meta"]["caseId"] == "c3-create-pilot-16")
        output = validator.candidate_c_output(case, 1)
        spans = output["operations"][0]["changeEvidence"]["category"]
        self.assertEqual(
            {(item["text"], item["status"]) for item in spans},
            {("출고", "adopted"), ("입고", "superseded"), ("말고", "correction_marker")},
        )

    def test_scenario_tags_cover_time_axes(self) -> None:
        by_id = {case["meta"]["caseId"]: set(case["meta"]["scenarioTags"]) for case in self.cases}
        self.assertIn("untrusted_server_time", by_id["c3-create-pilot-11"])
        self.assertIn("relative_time", by_id["c3-create-pilot-11"])
        self.assertIn("year_boundary", by_id["c3-create-pilot-22"])
        self.assertIn("clock_24h", by_id["c3-create-pilot-03"])
        self.assertIn("clock_24h", by_id["c3-create-pilot-23"])

    def test_source_registry_protects_issue30_development_and_locked(self) -> None:
        rows = validator.load_jsonl(validator.SOURCE_REGISTRY)
        policies = {row["sourceId"]: row["policy"] for row in rows}
        self.assertEqual(policies["issue30_development_12"], "forbidden_for_training")
        self.assertEqual(policies["issue30_locked_56"], "forbidden_for_training")

    def test_profile_adds_no_fields(self) -> None:
        profile = json.loads(validator.PROFILE.read_text(encoding="utf-8"))
        self.assertFalse(profile["addsFields"])
        self.assertEqual(profile["allowedKinds"], ["create"])


PILOT_DIR = HERE / "pilot"
if str(PILOT_DIR) not in sys.path:
    sys.path.insert(0, str(PILOT_DIR))

import build_pilot  # noqa: E402
import spec_checks  # noqa: E402


def _spec(**overrides):
    base = dict(
        number=90,
        bucket="direct_create",
        family="guard_probe",
        text_turns=[build_pilot.turn("내일 오후 2시에 출고 점검 등록해줘")],
        title=build_pilot.evidence([(1, "출고 점검")]),
        date=build_pilot.evidence([(1, "내일")]),
        time=build_pilot.evidence([(1, "오후 2시")]),
        category_evidence=build_pilot.evidence([(1, "출고")]),
        scheduled_at="2028-02-11T14:00:00+09:00",
        category="OUTBOUND",
        expectations=[build_pilot.expected("outcome", write=1, outcome="success")],
    )
    base.update(overrides)
    number = base.pop("number")
    bucket = base.pop("bucket")
    family = base.pop("family")
    text_turns = base.pop("text_turns")
    return build_pilot.spec(number, bucket, family, text_turns, **base)


class GeneratorGuardTests(unittest.TestCase):
    def test_question_after_consent_asks_the_remaining_item(self) -> None:
        specification = _spec(
            number=25,
            bucket="mixed_unsupported_consent",
            family="mixed_reminder_grant_then_time",
            text_turns=[
                build_pilot.turn("내일 입고 회의 등록하고 알림도 설정해줘"),
                build_pilot.turn("알림은 빼고 등록해줘", answer=1),
                build_pilot.turn("오전 9시", answer=2),
            ],
            title=build_pilot.evidence([(1, "입고 회의")]),
            time=build_pilot.evidence([(3, "오전 9시")]),
            category_evidence=build_pilot.evidence([(1, "입고")]),
            scheduled_at="2028-02-11T09:00:00+09:00",
            category="INBOUND",
            missing=[
                {"item": "user_consent", "detectedAtTurn": 1, "resolvedAtTurn": 2},
                {"item": "time", "detectedAtTurn": 1, "resolvedAtTurn": 3},
            ],
            unsupported=(1, "알림도 설정해줘", "reminder"),
            expectations=[
                build_pilot.expected("need_more_info", purpose="consent_without_unsupported"),
                build_pilot.expected("need_more_info", model_call="no_model_call", purpose="missing_information"),
                build_pilot.expected("outcome", write=1, outcome="success"),
            ],
        )
        case = build_pilot.make_case(specification)
        turns = case["semantics"]["expectedTurns"]
        self.assertEqual(
            [item["fact"] for item in turns[0]["requiredResponseFacts"]],
            ["unsupported_feature_notice"],
        )
        self.assertEqual(
            [item["fact"] for item in turns[1]["requiredResponseFacts"]],
            ["missing_item_question"],
        )
        operation = case["semantics"]["operations"][0]
        self.assertEqual(
            validator._expected_question_purpose(case, operation, turns[0]),
            "consent_without_unsupported",
        )
        self.assertEqual(
            validator._expected_question_purpose(case, operation, turns[1]),
            "missing_information",
        )
        others = [
            item
            for item in validator.load_jsonl(validator.PILOT)
            if item["meta"]["caseId"] != "c3-create-pilot-25"
        ]
        errors = validator._lint_semantic_subset([case, *others])
        self.assertEqual(
            [item for item in errors if "questionPurpose" in item or "requiredResponseFacts" in item],
            [],
        )

    def test_pilot_specs_pass_and_scheduled_at_is_recomputed(self) -> None:
        spec_checks.check_specs(build_pilot.SPECS)
        recomputed = [
            item for item in build_pilot.SPECS
            if item["scheduledAt"] is not None
            and spec_checks.computed_scheduled_at(item) == item["scheduledAt"]
        ]
        self.assertEqual(len(recomputed), 28)

    def test_noon_and_midnight_with_period(self) -> None:
        noon = _spec(time=build_pilot.evidence([(1, "오후 12시")]), scheduled_at="2028-02-11T12:00:00+09:00")
        midnight = _spec(time=build_pilot.evidence([(1, "오전 12시 30분")]), scheduled_at="2028-02-11T00:30:00+09:00")
        spec_checks.check_spec(noon)
        spec_checks.check_spec(midnight)

    def test_future_yearless_date_is_allowed(self) -> None:
        spec_checks.check_spec(_spec(
            date=build_pilot.evidence([(1, "3월 5일")]),
            scheduled_at="2028-03-05T14:00:00+09:00",
        ))

    def test_excluded_expressions_are_rejected(self) -> None:
        cases = {
            "EXCLUDED_YEARLESS_PAST_DATE": _spec(
                reference=spec_checks.REFERENCE_PRESETS["year_end"],
                date=build_pilot.evidence([(1, "1월 5일")]),
                scheduled_at="2029-01-05T14:00:00+09:00",
            ),
            "EXCLUDED_TIME_PERIOD_WORD": _spec(
                time=build_pilot.evidence([(1, "밤 10시")]),
                scheduled_at="2028-02-11T22:00:00+09:00",
            ),
            "EXCLUDED_KOREAN_NUMERAL_HOUR": _spec(
                time=build_pilot.evidence([(1, "오후 세 시")]),
                scheduled_at="2028-02-11T15:00:00+09:00",
            ),
        }
        for code, specification in cases.items():
            with self.subTest(code=code):
                with self.assertRaisesRegex(spec_checks.SpecError, code):
                    spec_checks.check_spec(specification)

    def test_wrong_scheduled_at_is_rejected(self) -> None:
        with self.assertRaisesRegex(spec_checks.SpecError, "recomputed"):
            spec_checks.check_spec(_spec(scheduled_at="2028-02-11T02:00:00+09:00"))

    def test_consent_reply_must_match_backend_classifier(self) -> None:
        cases = {
            item["meta"]["caseId"]: item
            for item in validator.load_jsonl(validator.BATCH1_CONFIG.conversations)
        }
        targets = validator.load_target_map(validator.BATCH1_CONFIG.targets)
        case = json.loads(json.dumps(cases["c3-create-b1-091"]))
        expectation = case["semantics"]["expectedTurns"][1]

        def state(current: dict) -> str | None:
            output = validator.candidate_c_output(current, 2, targets)
            parsed = validator.contracts.validate_candidate_c(
                validator.canonical(output), user_turns=validator._text_turns(current, 2)
            )
            return validator._gate_check(current, 2, parsed)[2]

        self.assertEqual(state(case), "granted")
        self.assertIsNone(validator._consent_answer_mismatch(case, expectation, "granted"))
        case["context"]["turns"][1]["text"] = "문자 말고 등록만 부탁해"
        self.assertEqual(state(case), "pending")
        self.assertIn(
            "oracle expects 'granted'",
            validator._consent_answer_mismatch(case, expectation, state(case)),
        )

    def test_batch_quality_flags_pilot_concentration(self) -> None:
        errors = spec_checks.batch_quality_errors(build_pilot.SPECS)
        self.assertIn("DUPLICATE_TITLE: '입고 검수' x2", errors)
        self.assertIn("DUPLICATE_TITLE: '재고 실사' x2", errors)
        self.assertTrue(any(item.startswith("REFERENCE_TIME_CONCENTRATED") for item in errors))
        self.assertEqual(
            spec_checks.batch_quality_errors([_spec(reference=value) for value in spec_checks.REFERENCE_PRESETS.values()][:4]),
            ["DUPLICATE_TITLE: '출고 점검' x4"],
        )


if __name__ == "__main__":
    unittest.main()
