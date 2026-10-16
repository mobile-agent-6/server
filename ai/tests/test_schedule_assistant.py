"""Safety and HTTP-contract checks for the provisional AI adapter."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schedule_assistant.actions import ActionError, parse_action
from schedule_assistant.clients import ScheduleClient, ScheduleError
from schedule_assistant.service import ConversationService


ID = "11111111-1111-4111-8111-111111111111"
NOW = datetime.fromisoformat("2026-10-10T09:00:00+09:00")
WHEN = "2026-10-11T10:00:00+09:00"
LATER = "2026-10-11T15:00:00+09:00"
ITEM = {"id": ID, "title": "재고 실사", "scheduledAt": WHEN,
        "category": "INVENTORY", "updatedAt": "v1", "reminderMinutesBefore": 10}


def action(name, request=None, message=None):
    return json.dumps({"action": name, "message": message, "request": request}, ensure_ascii=False)


class FakeModel:
    def __init__(self, *outputs):
        self.outputs = iter(outputs)

    def generate(self, messages):
        return next(self.outputs)


class FakeSchedules:
    def __init__(self):
        self.item = dict(ITEM)
        self.writes = []
        self.keys = []

    def list(self, filters):
        return [dict(self.item)]

    def get(self, schedule_id):
        assert schedule_id == ID
        return dict(self.item)

    def create(self, body, key):
        self.writes.append(("POST", body))
        self.keys.append(key)
        return {"id": ID, **body}

    def update(self, schedule_id, changes):
        self.writes.append(("PATCH", schedule_id, changes))
        self.item.update(changes)
        return dict(self.item)

    def delete(self, schedule_id):
        self.writes.append(("DELETE", schedule_id))


class ActionTests(unittest.TestCase):
    def test_rejects_extra_fields_invalid_category_and_long_list(self):
        for raw in (
            '{"action":"ASK","message":"언제요?","request":null,"location":"B"}',
            action("CREATE", {"title": "실사", "scheduledAt": WHEN, "category": "재고", "reminderMinutesBefore": 10}),
            action("LIST", {"from": WHEN, "to": "2027-02-01T10:00:00+09:00"}),
        ):
            with self.subTest(raw=raw), self.assertRaises(ActionError):
                parse_action(raw)

    def test_select_target_requires_id_free_request_and_valid_changes(self):
        request = {"operation": "update", "changes": {"scheduledAt": LATER}}
        self.assertEqual(parse_action(action("SELECT_TARGET", request)).request, request)
        self.assertEqual(parse_action(action("SELECT_TARGET", {"operation": "delete"})).request,
                         {"operation": "delete"})
        for raw in (
            action("SELECT_TARGET", request, "1번인가요?"),
            action("SELECT_TARGET", {"operation": "delete", "targetId": ID}),
            action("SELECT_TARGET", {"operation": "update", "changes": {}}),
            action("SELECT_TARGET", {"operation": "update", "changes": {"completed": "yes"}}),
            action("SELECT_TARGET", {"operation": "other"}),
        ):
            with self.subTest(raw=raw), self.assertRaises(ActionError):
                parse_action(raw)

    def test_create_after_valid_action_only(self):
        body = {"title": "재고 실사", "scheduledAt": WHEN, "category": "INVENTORY", "reminderMinutesBefore": 10}
        schedules = FakeSchedules()
        service = ConversationService(FakeModel(action("CREATE", body)), schedules,
                                      lambda: datetime.fromisoformat("2026-10-10T09:00:00+09:00"))
        reply = service.handle("내일 10시 재고 실사")
        self.assertEqual(reply.kind, "created")
        self.assertEqual(schedules.writes, [("POST", body)])
        self.assertEqual(len(schedules.keys[0]), 36)

    def test_past_create_asks_for_new_time_before_api(self):
        schedules = FakeSchedules()
        body = {"title": "재고 실사", "scheduledAt": WHEN, "category": "INVENTORY", "reminderMinutesBefore": None}
        service = ConversationService(FakeModel(action("CREATE", body)), schedules,
                                      lambda: datetime.fromisoformat("2026-10-12T09:00:00+09:00"))
        reply = service.handle("등록해")
        self.assertEqual(reply.kind, "ask")
        self.assertIn("언제로 등록", reply.message)
        self.assertEqual(schedules.writes, [])

    def test_past_update_at_confirmation_asks_again_without_write(self):
        schedules = FakeSchedules()
        now = [datetime.fromisoformat("2026-10-10T09:00:00+09:00")]
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("CONFIRM_UPDATE", {"targetId": ID, "changes": {"scheduledAt": LATER}})),
            schedules, lambda: now[0])
        self.assertEqual(service.handle("3시로 변경해").kind, "confirm")
        now[0] = datetime.fromisoformat("2026-10-12T09:00:00+09:00")
        reply = service.handle("응")
        self.assertEqual(reply.kind, "ask")
        self.assertIn("언제로 변경", reply.message)
        self.assertEqual(schedules.writes, [])

    def test_update_needs_explicit_confirmation_and_uses_real_changes(self):
        schedules = FakeSchedules()
        model = FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("CONFIRM_UPDATE", {"targetId": ID, "changes": {"scheduledAt": LATER}}, "이미 변경했어요"),
        )
        service = ConversationService(model, schedules, lambda: NOW)
        reply = service.handle("재고 실사 3시로 바꿔줘")
        self.assertEqual(reply.kind, "confirm")
        self.assertIn(LATER, reply.message)
        self.assertNotIn("이미 변경", reply.message)
        self.assertEqual(schedules.writes, [])
        self.assertEqual(service.handle("응").kind, "updated")
        self.assertEqual(schedules.writes, [("PATCH", ID, {"scheduledAt": LATER})])

    def test_reject_and_stale_never_write(self):
        for answer in ("아니", "응"):
            with self.subTest(answer=answer):
                schedules = FakeSchedules()
                service = ConversationService(FakeModel(
                    action("LIST", {"from": WHEN, "to": LATER}),
                    action("CONFIRM_DELETE", {"targetId": ID})), schedules)
                self.assertEqual(service.handle("재고 실사 삭제해").kind, "confirm")
                if answer == "응":
                    schedules.item["updatedAt"] = "v2"
                self.assertIn(service.handle(answer).kind, {"cancelled", "stale"})
                self.assertEqual(schedules.writes, [])

    def test_unknown_target_never_writes(self):
        schedules = FakeSchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("CONFIRM_DELETE", {"targetId": "22222222-2222-4222-8222-222222222222"})), schedules)
        self.assertEqual(service.handle("삭제해").kind, "error")
        self.assertEqual(schedules.writes, [])

    def test_multiple_candidates_require_user_selection_before_confirmation(self):
        second_id = "22222222-2222-4222-8222-222222222222"

        class TwoSchedules(FakeSchedules):
            def list(self, filters):
                return [dict(self.item), {**self.item, "id": second_id, "title": "반품 확인"}]

            def get(self, schedule_id):
                return {**self.item, "id": schedule_id,
                        "title": "반품 확인" if schedule_id == second_id else self.item["title"]}

        schedules = TwoSchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("SELECT_TARGET", {"operation": "delete"})), schedules)
        self.assertEqual(service.handle("일정 삭제해").kind, "ask")
        self.assertEqual(schedules.writes, [])
        self.assertEqual(service.handle("3번").kind, "ask")
        self.assertEqual(schedules.writes, [])
        confirmation = service.handle("2번")
        self.assertEqual(confirmation.kind, "confirm")
        self.assertIn("반품 확인", confirmation.message)
        self.assertEqual(schedules.writes, [])
        self.assertEqual(service.handle("응").kind, "deleted")
        self.assertEqual(schedules.writes, [("DELETE", second_id)])

    def test_multiple_candidates_reject_model_target_id(self):
        second_id = "22222222-2222-4222-8222-222222222222"

        class TwoSchedules(FakeSchedules):
            def list(self, filters):
                return [dict(self.item), {**self.item, "id": second_id}]

        schedules = TwoSchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("CONFIRM_DELETE", {"targetId": ID})), schedules)
        self.assertEqual(service.handle("일정 삭제해").kind, "error")
        self.assertIsNone(service.state.selection)
        self.assertEqual(schedules.writes, [])

    def test_select_target_without_lookup_and_cancel_never_write(self):
        schedules = FakeSchedules()
        service = ConversationService(FakeModel(action("SELECT_TARGET", {"operation": "delete"})), schedules)
        self.assertEqual(service.handle("삭제해").kind, "error")
        self.assertEqual(schedules.writes, [])

        class TwoSchedules(FakeSchedules):
            def list(self, filters):
                return [dict(self.item), {**self.item, "id": "22222222-2222-4222-8222-222222222222"}]

        schedules = TwoSchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("SELECT_TARGET", {"operation": "delete"})), schedules)
        self.assertEqual(service.handle("일정 삭제해").kind, "ask")
        self.assertEqual(service.handle("취소").kind, "cancelled")
        self.assertEqual(schedules.writes, [])

    def test_update_selection_confirms_selected_target_and_changes(self):
        second_id = "22222222-2222-4222-8222-222222222222"

        class TwoSchedules(FakeSchedules):
            def list(self, filters):
                return [dict(self.item), {**self.item, "id": second_id, "title": "반품 확인"}]

            def get(self, schedule_id):
                return {**self.item, "id": schedule_id,
                        "title": "반품 확인" if schedule_id == second_id else self.item["title"]}

            def update(self, schedule_id, changes):
                self.writes.append(("PATCH", schedule_id, changes))
                return {**self.item, "id": schedule_id, **changes}

        schedules = TwoSchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("SELECT_TARGET", {"operation": "update", "changes": {"scheduledAt": LATER}})),
            schedules, lambda: NOW)
        self.assertEqual(service.handle("일정 한 시간 늦춰줘").kind, "ask")
        confirmation = service.handle("2번")
        self.assertEqual(confirmation.kind, "confirm")
        self.assertIn("반품 확인", confirmation.message)
        self.assertIn(LATER, confirmation.message)
        self.assertEqual(schedules.writes, [])
        self.assertEqual(service.handle("응").kind, "updated")
        self.assertEqual(schedules.writes[0][1], second_id)
        self.assertEqual(schedules.writes[0][2]["scheduledAt"], LATER)

    def test_selected_update_correction_requires_new_confirmation(self):
        second_id = "22222222-2222-4222-8222-222222222222"
        corrected_time = "2026-10-11T16:00:00+09:00"

        class TwoSchedules(FakeSchedules):
            def list(self, filters):
                return [dict(self.item), {**self.item, "id": second_id, "title": "반품 확인"}]

            def get(self, schedule_id):
                return {**self.item, "id": schedule_id,
                        "title": "반품 확인" if schedule_id == second_id else self.item["title"]}

        schedules = TwoSchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("SELECT_TARGET", {"operation": "update", "changes": {"scheduledAt": LATER}}),
            action("CONFIRM_UPDATE", {"targetId": second_id, "changes": {"scheduledAt": corrected_time}})),
            schedules, lambda: NOW)
        self.assertEqual(service.handle("일정 시간을 바꿔줘").kind, "ask")
        self.assertEqual(service.handle("2번").kind, "confirm")
        corrected = service.handle("아니, 반품 확인을 4시로 바꿔줘")
        self.assertEqual(corrected.kind, "confirm")
        self.assertIn(corrected_time, corrected.message)
        self.assertEqual(schedules.writes, [])
        self.assertEqual(service.handle("응").kind, "updated")
        self.assertEqual(schedules.writes, [("PATCH", second_id, {"scheduledAt": corrected_time})])

    def test_more_than_ten_candidates_require_narrower_lookup(self):
        class ManySchedules(FakeSchedules):
            def list(self, filters):
                return [{**self.item, "id": f"00000000-0000-4000-8000-{index:012d}"}
                        for index in range(11)]

        schedules = ManySchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("SELECT_TARGET", {"operation": "delete"})), schedules)
        reply = service.handle("일정 삭제해")
        self.assertEqual(reply.kind, "ask")
        self.assertIn("좁혀", reply.message)
        self.assertIsNone(service.state.selection)
        self.assertEqual(schedules.writes, [])

    def test_correction_cancels_pending_write_and_processes_new_request(self):
        schedules = FakeSchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("CONFIRM_DELETE", {"targetId": ID}),
            action("ASK", message="몇 시인가요?")), schedules)
        self.assertEqual(service.handle("재고 실사 삭제해").kind, "confirm")
        self.assertEqual(service.handle("아니, 새 일정을 등록해").kind, "ask")
        self.assertEqual(schedules.writes, [])

    def test_uncertain_create_reuses_key_and_blocks_new_model_request(self):
        body = {"title": "재고 실사", "scheduledAt": WHEN,
                "category": "INVENTORY", "reminderMinutesBefore": None}

        class UncertainSchedules(FakeSchedules):
            def create(self, payload, key):
                self.writes.append(("POST", dict(payload)))
                self.keys.append(key)
                if len(self.keys) == 1:
                    raise ScheduleError("CONNECTION_UNCERTAIN", uncertain=True)
                return {"id": ID, **payload}

        schedules = UncertainSchedules()
        service = ConversationService(FakeModel(action("CREATE", body)), schedules,
                                      lambda: datetime.fromisoformat("2026-10-10T09:00:00+09:00"))
        self.assertEqual(service.handle("내일 실사 등록해").kind, "error")
        service.reset()
        self.assertEqual(service.handle("다른 일정 등록해").kind, "error")
        self.assertEqual(len(schedules.writes), 1)
        self.assertEqual(service.handle("재시도").kind, "created")
        self.assertEqual(schedules.writes, [("POST", body), ("POST", body)])
        self.assertEqual(schedules.keys[0], schedules.keys[1])

    def test_uncertain_create_does_not_retry_after_key_retention(self):
        body = {"title": "재고 실사", "scheduledAt": WHEN,
                "category": "INVENTORY", "reminderMinutesBefore": None}

        class UncertainSchedules(FakeSchedules):
            def create(self, payload, key):
                self.writes.append(("POST", payload))
                raise ScheduleError("CONNECTION_UNCERTAIN", uncertain=True)

        now = [datetime.fromisoformat("2026-10-10T09:00:00+09:00")]
        schedules = UncertainSchedules()
        service = ConversationService(FakeModel(action("CREATE", body)), schedules, lambda: now[0])
        self.assertEqual(service.handle("등록해").kind, "error")
        now[0] += timedelta(hours=24)
        self.assertEqual(service.handle("재시도").kind, "error")
        self.assertEqual(len(schedules.writes), 1)

    def test_answer_requires_null_message(self):
        with self.assertRaises(ActionError):
            parse_action(action("ANSWER", message="일정을 삭제했어요."))
        self.assertIsNone(parse_action(action("ANSWER")).message)

    def test_answer_still_displays_controller_message(self):
        schedules = FakeSchedules()
        service = ConversationService(FakeModel(action("ANSWER")), schedules)
        reply = service.handle("고마워")
        self.assertEqual(reply.kind, "answer")
        self.assertEqual(reply.message, "네, 일정 요청을 말씀해 주세요.")
        self.assertEqual(schedules.writes, [])

    def test_answer_after_list_uses_schedule_result(self):
        schedules = FakeSchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("ANSWER")), schedules)
        reply = service.handle("재고 실사 일정 알려줘")
        self.assertEqual(reply.kind, "answer")
        self.assertIn("재고 실사", reply.message)
        self.assertIn(WHEN, reply.message)
        self.assertEqual(schedules.writes, [])

    def test_failed_update_never_reports_success(self):
        class FailingSchedules(FakeSchedules):
            def __init__(self):
                super().__init__()
                self.reads = 0

            def get(self, schedule_id):
                self.reads += 1
                return super().get(schedule_id)

            def update(self, schedule_id, changes):
                raise ScheduleError("DATABASE_UNAVAILABLE", uncertain=True)

        schedules = FailingSchedules()
        service = ConversationService(FakeModel(
            action("LIST", {"from": WHEN, "to": LATER}),
            action("CONFIRM_UPDATE", {"targetId": ID, "changes": {"scheduledAt": LATER}})),
            schedules, lambda: NOW)
        self.assertEqual(service.handle("3시로 바꿔줘").kind, "confirm")
        reply = service.handle("응")
        self.assertEqual(reply.kind, "error")
        self.assertNotIn("변경했", reply.message)
        self.assertIn("현재 일정은", reply.message)
        self.assertEqual(schedules.reads, 3)
        self.assertEqual(schedules.writes, [])


class HttpTests(unittest.TestCase):
    def test_post_retry_reuses_body_and_idempotency_key(self):
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps({"id": ID}).encode()

        def urlopen(request, timeout):
            requests.append(request)
            if len(requests) == 1:
                raise HTTPError(request.full_url, 503, "unavailable", {}, BytesIO(b'{}'))
            return Response()

        with patch("urllib.request.urlopen", side_effect=urlopen), patch("time.sleep") as sleep:
            result = ScheduleClient("http://127.0.0.1:8000").create(
                {"title": "실사"}, "00000000-0000-4000-8000-000000000000")
        sleep.assert_called_once_with(1)
        self.assertEqual(result["id"], ID)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].get_method(), "POST")
        self.assertEqual(requests[0].full_url, "http://127.0.0.1:8000/api/v1/schedules")
        self.assertEqual(requests[0].get_header("Idempotency-key"), requests[1].get_header("Idempotency-key"))
        self.assertEqual(requests[0].data, requests[1].data)

    def test_post_timeout_retry_keeps_key_and_reports_uncertainty(self):
        requests = []

        def urlopen(request, timeout):
            requests.append(request)
            raise TimeoutError("response timed out")

        with patch("urllib.request.urlopen", side_effect=urlopen), patch("time.sleep"):
            with self.assertRaises(ScheduleError) as caught:
                ScheduleClient("http://127.0.0.1:8000").create(
                    {"title": "실사"}, "00000000-0000-4000-8000-000000000000")
        self.assertTrue(caught.exception.uncertain)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].get_header("Idempotency-key"), requests[1].get_header("Idempotency-key"))
        self.assertEqual(requests[0].data, requests[1].data)

    def test_invalid_success_body_is_uncertain_write_error(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b"not-json"

        with patch("urllib.request.urlopen", return_value=Response()):
            with self.assertRaises(ScheduleError) as caught:
                ScheduleClient("http://127.0.0.1:8000").create({"title": "실사"}, "test-key")
        self.assertTrue(caught.exception.uncertain)

    def test_lost_delete_response_then_not_found_stays_uncertain(self):
        attempts = []

        def urlopen(request, timeout):
            attempts.append(request)
            if len(attempts) == 1:
                raise URLError("response lost")
            raise HTTPError(request.full_url, 404, "not found", {}, BytesIO(b'{}'))

        with patch("urllib.request.urlopen", side_effect=urlopen), patch("time.sleep"):
            with self.assertRaises(ScheduleError) as caught:
                ScheduleClient("http://127.0.0.1:8000").delete(ID)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(caught.exception.uncertain)


if __name__ == "__main__":
    unittest.main()
