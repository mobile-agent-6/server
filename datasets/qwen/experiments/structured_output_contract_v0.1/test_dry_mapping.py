"""Deterministic tests for the Issue #30 processor dry-mapping harness."""

from __future__ import annotations

import json
import unittest
from unittest import mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import dry_mapping as dm


class DryMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = dm.load_jsonl(dm.LOCKED_PATH)
        cls.rows, cls.summary = dm.run_dry_mapping()

    def case(self, prefix: str):
        return next(case for case in self.cases if case["meta"]["caseId"].startswith(prefix))

    def row(self, prefix: str, candidate: str):
        return next(
            row
            for row in self.rows
            if row["caseId"].startswith(prefix) and row["candidate"] == candidate
        )

    def test_fixed_inputs_and_contract_artifacts_are_unchanged(self) -> None:
        integrity = dm.assert_fixed_inputs()
        self.assertEqual(integrity["caseCount"], 56)
        self.assertEqual(integrity["lockStatus"], "locked")
        self.assertEqual(integrity["lockedSha256"], dm.EXPECTED_LOCKED_SHA256)
        self.assertEqual(integrity["schemaSha256"], dm.EXPECTED_SCHEMA_SHA256)
        self.assertEqual(integrity["contractArtifactCount"], 7)

    def test_matrix_has_56_by_4_records_and_is_deterministic(self) -> None:
        self.assertEqual(len(self.rows), 224)
        self.assertEqual(
            {(row["caseId"], row["candidate"]) for row in self.rows},
            {
                (case["meta"]["caseId"], candidate)
                for case in self.cases
                for candidate in ("A", "B", "C", "R")
            },
        )
        rerun, rerun_summary = dm.run_dry_mapping()
        self.assertEqual(
            json.dumps(self.rows, ensure_ascii=False, sort_keys=True),
            json.dumps(rerun, ensure_ascii=False, sort_keys=True),
        )
        self.assertEqual(self.summary, rerun_summary)

    def test_fake_schedule_port_is_read_only_and_filters_deterministically(self) -> None:
        case = self.case("lk40")
        port = dm.FakeScheduleReadPort(case["context"]["initialSchedules"])
        self.assertFalse(hasattr(port, "create"))
        self.assertFalse(hasattr(port, "update"))
        self.assertFalse(hasattr(port, "delete"))
        active = [item for item in case["context"]["initialSchedules"] if not item["deleted"]]
        active_times = [dm._parse_datetime(item["scheduledAt"]).astimezone(timezone.utc) for item in active]
        start = min(active_times) - timedelta(seconds=1)
        end = max(active_times) + timedelta(seconds=1)
        values = port.list(
            from_utc=start, to_utc=end, category=None, completed=None, limit=101
        )
        self.assertEqual(
            [item.scheduled_at for item in values],
            sorted(active_times),
        )
        self.assertNotIn(
            next(item["id"] for item in case["context"]["initialSchedules"] if item["deleted"]),
            {str(item.id) for item in values},
        )
        filtered = port.list(
            from_utc=start,
            to_utc=end,
            category=dm.Category("RETURN"),
            completed=True,
            limit=2,
        )
        self.assertLessEqual(len(filtered), 2)
        self.assertTrue(all(item.category.value == "RETURN" and item.completed for item in filtered))
        empty_start = datetime(2000, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(
            port.list(
                from_utc=empty_start,
                to_utc=empty_start + timedelta(days=92),
                category=None,
                completed=None,
                limit=10,
            ),
            [],
        )
        deleted = next(item for item in case["context"]["initialSchedules"] if item["deleted"])
        self.assertIsNone(port.get(UUID(deleted["id"])))
        self.assertEqual(
            [call["method"] for call in port.calls],
            ["list", "list", "list", "get"],
        )

    def test_no_external_or_write_calls_and_no_unsafe_accept(self) -> None:
        self.assertEqual(dm.ACTUAL_EXTERNAL_CALLS, {
            "model": 0,
            "server": 0,
            "database": 0,
            "scheduleApi": 0,
            "scheduleWrite": 0,
        })
        self.assertFalse(any(row["systemOutcome"] == "unsafe_accept" for row in self.rows))
        self.assertTrue(all(row["pendingWriteTargetCount"] <= 1 for row in self.rows))
        self.assertTrue(all(row["uuidCollisionFree"] for row in self.rows))
        self.assertTrue(all(row["questionCandidateIdCollisionFree"] for row in self.rows))
        self.assertTrue(all(not row["unexpectedScheduleReadCalls"] for row in self.rows))

    def test_matrix_generation_has_no_network_or_db_engine_path(self) -> None:
        with (
            mock.patch("socket.socket", side_effect=AssertionError("network access")),
            mock.patch("sqlalchemy.create_engine", side_effect=AssertionError("DB engine access")),
        ):
            rows, summary = dm.run_dry_mapping()
        self.assertEqual(len(rows), 224)
        self.assertEqual(summary["actualExternalCalls"], dm.ACTUAL_EXTERNAL_CALLS)

    def test_relative_untrusted_time_is_asked_by_actual_processor(self) -> None:
        for candidate in ("B", "C", "R"):
            trace = self.row("lk38", candidate)["turnTrace"][0]
            self.assertEqual(trace["decisionKind"], "confirm")
            self.assertEqual(trace["planKind"], "need_more_info")
            self.assertTrue(trace["oracleMatch"])

    def test_create_only_becomes_pending_after_validation_and_gate(self) -> None:
        for candidate in ("B", "C"):
            row = self.row("lk01", candidate)
            self.assertEqual(row["localValidation"], "accept")
            self.assertEqual(row["commonGate"], "accept")
            self.assertEqual(row["processorFinalPlanKind"], "pending_write")
            self.assertEqual(row["pendingWriteOperation"], "create")

    def test_confirmation_is_required_and_saved_inputs_do_not_call_model(self) -> None:
        for row in self.rows:
            for trace in row["turnTrace"]:
                if trace["inputType"] in {"selection", "confirmation"}:
                    self.assertFalse(trace["logicalProviderDecision"])
            if row["pendingWrite"]:
                self.assertEqual(row["expectedWriteCount"], 1)
            self.assertEqual(row["selectionConfirmationModelCalls"], 0)
        for case in self.cases:
            operation_kinds = {item["kind"] for item in case["semantics"]["operations"]}
            if not operation_kinds & {"update", "delete"}:
                continue
            for candidate in ("A", "B", "C", "R"):
                row = self.row(case["meta"]["caseId"], candidate)
                for trace, expected in zip(
                    row["turnTrace"], case["semantics"]["expectedTurns"], strict=False
                ):
                    if expected["expectedWriteCount"] == 0:
                        self.assertNotEqual(trace["planKind"], "pending_write")

    def test_off_candidate_existing_schedule_id_is_not_resolved(self) -> None:
        case = self.case("lk31")
        reader = dm.FakeScheduleReadPort(case["context"]["initialSchedules"])
        expected = dm._expected_for_turn(case, 1)
        assert expected is not None
        mapping = dm._text_mapping("C", case, 1, reader, expected)
        assert mapping.envelope is not None
        factory = dm.DeterministicUuidFactory("off-candidate")
        plan = dm._provider_decision_plan(
            mapping.envelope,
            schedule_reader=reader,
            issue_time_factory=dm.FixedIssueClock(dm._parse_datetime(case["context"]["referenceTime"])).now,
            uuid_factory=factory,
            server_time_trusted=True,
        )
        assert plan.question is not None
        off_candidate = reader.id_by_ref["Q3"]
        stored = dm._stored_input(
            case,
            input_payload={"type": "selection", "candidateId": str(off_candidate)},
            pending_context=plan.question.intents,
        )
        resolved = dm._from_saved_selection(
            stored=stored,
            schedule_reader=reader,
            issue_time_factory=dm.FixedIssueClock(dm._parse_datetime(case["context"]["referenceTime"])).now,
            uuid_factory=factory,
            server_time_trusted=True,
        )
        self.assertIsNone(resolved)

    def test_more_than_100_list_items_asks_to_narrow(self) -> None:
        schedules = []
        base = datetime(2030, 1, 1, tzinfo=timezone.utc)
        for index in range(101):
            identifier = UUID(int=index + 1, version=4)
            schedules.append({
                "ref": f"S{index + 1}",
                "id": str(identifier),
                "title": f"schedule {index + 1}",
                "scheduledAt": (base + timedelta(minutes=index)).isoformat(),
                "category": "OTHER",
                "completed": False,
                "deleted": False,
                "createdAt": base.isoformat(),
                "updatedAt": base.isoformat(),
            })
        reader = dm.FakeScheduleReadPort(schedules)
        envelope = dm.DecisionEnvelope.model_validate({
            "decision": {
                "kind": "list_intent",
                "text": "list",
                "from": base.isoformat(),
                "to": (base + timedelta(days=1)).isoformat(),
                "timeInterpretation": "absolute",
            }
        })
        plan = dm._provider_decision_plan(
            envelope,
            schedule_reader=reader,
            issue_time_factory=dm.FixedIssueClock(base).now,
            uuid_factory=dm.DeterministicUuidFactory("list-101"),
            server_time_trusted=True,
        )
        self.assertEqual(dm._plan_kind(plan), "need_more_info")
        self.assertEqual(reader.calls[0]["limit"], 101)
        self.assertIsNone(plan.pending_write)

    def test_same_display_candidates_are_not_auto_selected(self) -> None:
        row = self.row("lk35", "C")
        self.assertEqual(row["processorFinalPlanKind"], "failure")
        self.assertEqual(row["publicResultKind"], "failure")
        self.assertFalse(row["pendingWrite"])

    def test_delegated_search_is_not_confirmation_or_model_recall(self) -> None:
        for candidate in ("B", "C", "R"):
            row = self.row("lk54", candidate)
            self.assertFalse(row["pendingWrite"])
            self.assertFalse(row["modelAdditionalCallAllowed"])
            self.assertEqual(row["selectionConfirmationModelCalls"], 0)
            self.assertEqual(row["publicResultKind"], "need_more_info")

    def test_noop_update_is_removed_by_actual_processor(self) -> None:
        case = self.case("lk31")
        reader = dm.FakeScheduleReadPort(case["context"]["initialSchedules"])
        schedule = reader.get(reader.id_by_ref["Q3"])
        assert schedule is not None
        envelope = dm.DecisionEnvelope.model_validate({
            "decision": {
                "kind": "confirm",
                "text": "confirm",
                "scheduleId": str(schedule.id),
                "operation": "update",
                "changes": {"completed": schedule.completed},
                "timeInterpretation": "absolute",
            }
        })
        plan = dm._provider_decision_plan(
            envelope,
            schedule_reader=reader,
            issue_time_factory=dm.FixedIssueClock(dm._parse_datetime(case["context"]["referenceTime"])).now,
            uuid_factory=dm.DeterministicUuidFactory("noop"),
            server_time_trusted=True,
        )
        self.assertEqual(dm._plan_kind(plan), "outcome")
        self.assertEqual(plan.result.outcome, "no_match")
        self.assertIsNone(plan.pending_write)

    def test_provider_validation_error_is_public_failure(self) -> None:
        self.assertEqual(
            dm.provider_validation_error_probe(),
            {
                "providerValidationRejected": True,
                "publicResultKind": "failure",
                "publicFailureCode": "MODEL_OUTPUT_INVALID",
            },
        )

    def test_scheduled_update_processor_defect_is_fail_closed(self) -> None:
        trace = self.row("lk28", "C")["turnTrace"][0]
        self.assertEqual(trace["mappingResult"], "processor_internal_error")
        self.assertEqual(trace["publicResultKind"], "failure")
        self.assertIn("datetime must be an RFC 3339 string", trace["processorException"])

    def test_candidate_a_all_seven_actions_have_explicit_mapping_outcome(self) -> None:
        schedule_ids = [
            "10000000-0000-4000-8000-000000000001",
            "10000000-0000-4000-8000-000000000002",
        ]
        candidates = [{"id": value} for value in schedule_ids]
        fixtures = {
            "ASK": {"action": "ASK", "message": "more", "request": None},
            "LIST": {"action": "LIST", "message": None, "request": {
                "from": "2030-01-01T00:00:00+09:00", "to": "2030-01-02T00:00:00+09:00"
            }},
            "CREATE": {"action": "CREATE", "message": None, "request": {
                "title": "task", "scheduledAt": "2030-01-01T09:00:00+09:00", "category": "OTHER"
            }},
            "SELECT_TARGET": {"action": "SELECT_TARGET", "message": None, "request": {
                "operation": "update", "changes": {"completed": True}
            }},
            "CONFIRM_UPDATE": {"action": "CONFIRM_UPDATE", "message": None, "request": {
                "targetId": schedule_ids[0], "changes": {"completed": True}
            }},
            "CONFIRM_DELETE": {"action": "CONFIRM_DELETE", "message": None, "request": {
                "targetId": schedule_ids[0]
            }},
        }
        kinds = {
            action: dm.candidate_a_to_decision(
                value,
                schedule_candidates=candidates if action == "SELECT_TARGET" else candidates[:1],
                time_interpretation="absolute",
            ).decision.kind
            for action, value in fixtures.items()
        }
        self.assertEqual(kinds, {
            "ASK": "ask",
            "LIST": "list_intent",
            "CREATE": "create_intent",
            "SELECT_TARGET": "select_candidate",
            "CONFIRM_UPDATE": "confirm",
            "CONFIRM_DELETE": "confirm",
        })
        with self.assertRaises(dm.CurrentContractUnrepresentable):
            dm.candidate_a_to_decision(
                {"action": "ANSWER", "message": None, "request": None},
                schedule_candidates=[],
                time_interpretation="absolute",
            )

    def test_c_evidence_spans_are_real_normalized_substrings(self) -> None:
        for case in self.cases:
            text_turns = {
                turn["turn"]: turn["text"]
                for turn in case["context"]["turns"]
                if turn["inputType"] == "text"
            }
            if not text_turns:
                continue
            payload = dm._filtered_c_output(case, max(text_turns))
            dm.validate_candidate_c(
                json.dumps(payload, ensure_ascii=False), user_turns=text_turns
            )

    def test_list_paths_never_create_pending_write_and_public_results_serialize(self) -> None:
        list_case_ids = {
            case["meta"]["caseId"]
            for case in self.cases
            if case["semantics"]["operations"]
            and case["semantics"]["operations"][0]["kind"] == "list"
        }
        self.assertFalse(
            any(row["pendingWrite"] for row in self.rows if row["caseId"] in list_case_ids)
        )
        self.assertTrue(all(row["publicSchemaSerializable"] for row in self.rows))

    def test_no_bytecode_database_or_log_artifacts_in_research_directory(self) -> None:
        forbidden_suffixes = {".db", ".sqlite", ".sqlite3", ".log", ".pyc"}
        artifacts = [
            path
            for path in Path(dm.HERE).rglob("*")
            if path.is_file() and path.suffix in forbidden_suffixes
        ]
        pycache = [path for path in Path(dm.HERE).rglob("__pycache__")]
        self.assertEqual(artifacts, [])
        self.assertEqual(pycache, [])


if __name__ == "__main__":
    unittest.main()
