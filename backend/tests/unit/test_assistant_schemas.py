from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
import unicodedata

import pytest
from pydantic import TypeAdapter, ValidationError

from app.modules.assistant.enums import AssistantResultKind
from app.modules.assistant.fingerprint import (
    FINGERPRINT_VERSION,
    canonical_fingerprint,
    canonical_request_json,
)
from app.modules.assistant.schemas import (
    ASSISTANT_WHITE_SPACE,
    AssistantRequestCreate,
    AssistantRequestStatusResponse,
    AssistantResult,
)

REQUEST_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CONVERSATION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
QUESTION_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
SCHEDULE_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
ACKNOWLEDGED_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
DATETIME = "2026-10-18T01:00:00Z"
RESULT_ADAPTER = TypeAdapter(AssistantResult)


def text_request(text: str = "재고 확인", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "requestId": REQUEST_ID,
        "input": {"type": "text", "text": text},
    }
    payload.update(overrides)
    return payload


def validation_error(payload: dict[str, object]) -> dict[str, Any]:
    with pytest.raises(ValidationError) as caught:
        AssistantRequestCreate.model_validate(payload)
    return caught.value.errors()[0]


def schedule_summary() -> dict[str, object]:
    return {
        "id": SCHEDULE_ID,
        "title": "반품 검수",
        "scheduledAt": DATETIME,
        "category": "RETURN",
        "completed": False,
        "updatedAt": DATETIME,
    }


def candidate(identifier: str = "Case-Sensitive") -> dict[str, object]:
    return {
        "candidateId": identifier,
        "title": "반품 검수",
        "scheduledAt": DATETIME,
        "category": "RETURN",
    }


def result_payload(kind: str) -> dict[str, object]:
    common: dict[str, object] = {"kind": kind, "text": "안내"}
    if kind == "need_more_info":
        return {**common, "questionId": QUESTION_ID}
    if kind == "candidate_selection":
        return {
            **common,
            "questionId": QUESTION_ID,
            "expiresAt": DATETIME,
            "candidates": [candidate("A"), candidate("a")],
        }
    if kind == "confirmation":
        return {
            **common,
            "questionId": QUESTION_ID,
            "expiresAt": DATETIME,
            "operation": "update",
            "target": schedule_summary(),
            "changes": {"completed": True},
        }
    if kind == "outcome":
        return {
            **common,
            "outcome": "success",
            "operation": "create",
            "schedulesChanged": True,
            "affectedScheduleIds": [SCHEDULE_ID],
        }
    if kind == "failure":
        return {
            **common,
            "code": "MODEL_OUTPUT_INVALID",
            "nextAction": "revise_input",
        }
    if kind == "uncertain":
        return {
            **common,
            "code": "EXECUTION_RESULT_UNKNOWN",
            "nextAction": "check_status",
        }
    raise AssertionError(kind)


def test_request_dto_is_strict_and_discriminated() -> None:
    parsed = AssistantRequestCreate.model_validate(text_request())
    assert parsed.input.type.value == "text"

    assert validation_error(text_request(unexpected=True))["type"] == "extra_forbidden"
    assert validation_error(text_request(reminderMinutesBefore=10))["type"] == "extra_forbidden"
    assert validation_error(
        text_request(input={"type": "confirmation", "questionId": QUESTION_ID, "approved": 1}, conversationId=CONVERSATION_ID)
    )["type"] == "bool_type"
    assert validation_error(text_request(input={"type": "unknown"}))["type"] == "union_tag_invalid"
    assert validation_error(text_request(requestId=123))["type"] == "is_instance_of"
    assert validation_error(text_request(input={"type": "text", "text": 123}))["type"] == "string_type"
    assert validation_error(text_request(conversationId=None))["type"] == "is_instance_of"


def test_request_cross_field_context_rules() -> None:
    with pytest.raises(ValidationError):
        AssistantRequestCreate.model_validate(
            text_request(input={"type": "text", "text": "답변", "questionId": QUESTION_ID})
        )
    with pytest.raises(ValidationError):
        AssistantRequestCreate.model_validate(
            text_request(
                conversationId=CONVERSATION_ID,
                acknowledgedUncertainRequestId=ACKNOWLEDGED_ID,
                input={"type": "text", "text": "답변", "questionId": QUESTION_ID},
            )
        )
    with pytest.raises(ValidationError):
        AssistantRequestCreate.model_validate(
            text_request(
                input={
                    "type": "selection",
                    "questionId": QUESTION_ID,
                    "candidateId": "candidate",
                }
            )
        )
    with pytest.raises(ValidationError):
        AssistantRequestCreate.model_validate(
            text_request(
                conversationId=CONVERSATION_ID,
                acknowledgedUncertainRequestId=ACKNOWLEDGED_ID,
                input={
                    "type": "selection",
                    "questionId": QUESTION_ID,
                    "candidateId": "candidate",
                },
            )
        )


@pytest.mark.parametrize("kind", [member.value for member in AssistantResultKind])
def test_result_union_accepts_all_six_discriminators(kind: str) -> None:
    parsed = RESULT_ADAPTER.validate_python(result_payload(kind))
    assert parsed.kind.value == kind


def test_status_response_requires_result_only_when_completed() -> None:
    base = {
        "requestId": REQUEST_ID,
        "conversationId": CONVERSATION_ID,
    }
    AssistantRequestStatusResponse.model_validate({**base, "status": "accepted"})
    AssistantRequestStatusResponse.model_validate(
        {**base, "status": "completed", "result": result_payload("failure")}
    )
    with pytest.raises(ValidationError):
        AssistantRequestStatusResponse.model_validate({**base, "status": "completed"})
    with pytest.raises(ValidationError):
        AssistantRequestStatusResponse.model_validate(
            {**base, "status": "processing", "result": result_payload("failure")}
        )


def test_result_expiration_and_candidate_count_contract() -> None:
    need_more = result_payload("need_more_info")
    need_more["expiresAt"] = DATETIME
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(need_more)

    for kind in ("candidate_selection", "confirmation"):
        missing = result_payload(kind)
        del missing["expiresAt"]
        with pytest.raises(ValidationError):
            RESULT_ADAPTER.validate_python(missing)

    for count in (1, 11):
        invalid = result_payload("candidate_selection")
        invalid["candidates"] = [candidate(str(index)) for index in range(count)]
        with pytest.raises(ValidationError):
            RESULT_ADAPTER.validate_python(invalid)


def test_candidate_preserves_opaque_identifier_case() -> None:
    parsed = RESULT_ADAPTER.validate_python(result_payload("candidate_selection"))
    assert [item.candidate_id for item in parsed.candidates] == ["A", "a"]


def test_confirmation_operation_and_changes_contract() -> None:
    normalized = result_payload("confirmation")
    normalized["changes"] = {"scheduledAt": "2026-10-18T10:00:00+09:00"}
    assert RESULT_ADAPTER.validate_python(normalized).model_dump(
        mode="json", by_alias=True
    )["changes"]["scheduledAt"] == "2026-10-18T01:00:00Z"

    update_without_changes = result_payload("confirmation")
    del update_without_changes["changes"]
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(update_without_changes)

    empty_changes = result_payload("confirmation")
    empty_changes["changes"] = {}
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(empty_changes)

    reminder = result_payload("confirmation")
    reminder["changes"] = {"reminderMinutesBefore": 10}
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(reminder)

    delete = result_payload("confirmation")
    delete["operation"] = "delete"
    del delete["changes"]
    RESULT_ADAPTER.validate_python(delete)
    delete["changes"] = {"completed": True}
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(delete)


@pytest.mark.parametrize(
    ("code", "next_action"),
    [
        ("MODEL_OUTPUT_INVALID", "revise_input"),
        ("SCHEDULE_REJECTED", "revise_input"),
        ("TARGET_AMBIGUOUS", "revise_input"),
        ("MODEL_TIMEOUT", "retry_later"),
        ("SCHEDULE_UNAVAILABLE", "retry_later"),
        ("PROCESSING_INTERRUPTED", "retry_later"),
        ("PROCESSING_TIMEOUT", "retry_later"),
        ("INTERNAL_ERROR", "none"),
    ],
)
def test_failure_code_and_next_action_pairs(code: str, next_action: str) -> None:
    payload = result_payload("failure")
    payload.update(code=code, nextAction=next_action)
    RESULT_ADAPTER.validate_python(payload)
    payload["nextAction"] = "none" if next_action != "none" else "retry_later"
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(payload)


def test_uncertain_contract_has_no_schedules_changed() -> None:
    valid = result_payload("uncertain")
    RESULT_ADAPTER.validate_python(valid)
    valid["schedulesChanged"] = False
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(valid)


def test_outcome_cross_field_contract() -> None:
    RESULT_ADAPTER.validate_python(result_payload("outcome"))
    RESULT_ADAPTER.validate_python(
        {
            "kind": "outcome",
            "text": "조회 완료",
            "outcome": "success",
            "operation": "list",
            "schedulesChanged": False,
            "items": [],
        }
    )
    RESULT_ADAPTER.validate_python(
        {
            "kind": "outcome",
            "text": "취소",
            "outcome": "cancelled",
            "schedulesChanged": False,
        }
    )
    invalid = result_payload("outcome")
    invalid["affectedScheduleIds"] = [SCHEDULE_ID, REQUEST_ID]
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(invalid)
    invalid = result_payload("outcome")
    invalid["schedulesChanged"] = False
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(invalid)

    invalid_cases = [
        {
            "kind": "outcome",
            "text": "누락",
            "outcome": "success",
            "schedulesChanged": False,
        },
        {
            "kind": "outcome",
            "text": "조회",
            "outcome": "success",
            "operation": "list",
            "schedulesChanged": True,
            "items": [],
        },
        {
            "kind": "outcome",
            "text": "조회",
            "outcome": "success",
            "operation": "list",
            "schedulesChanged": False,
            "items": [],
            "affectedScheduleIds": [SCHEDULE_ID],
        },
        {
            **result_payload("outcome"),
            "items": [],
        },
        {
            "kind": "outcome",
            "text": "취소",
            "outcome": "cancelled",
            "schedulesChanged": True,
        },
        {
            "kind": "outcome",
            "text": "없음",
            "outcome": "no_match",
            "schedulesChanged": False,
            "items": [],
        },
    ]
    for invalid_case in invalid_cases:
        with pytest.raises(ValidationError):
            RESULT_ADAPTER.validate_python(invalid_case)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", REQUEST_ID),
        ("{aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa}", "invalid_format"),
        ("urn:uuid:aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "invalid_format"),
        ("aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa", "invalid_format"),
        ("1-1-1-1-1", "invalid_format"),
        ("aaaaaaaa-aaaa-1aaa-8aaa-aaaaaaaaaaaa", "invalid_value"),
        ("aaaaaaaa-aaaa-4aaa-0aaa-aaaaaaaaaaaa", "invalid_value"),
    ],
)
def test_uuid_contract(value: str, expected: str) -> None:
    payload = text_request(requestId=value)
    if expected == REQUEST_ID:
        assert str(AssistantRequestCreate.model_validate(payload).request_id) == expected
    else:
        assert validation_error(payload)["type"] == expected


def test_text_length_uses_original_unicode_code_points() -> None:
    assert len(AssistantRequestCreate.model_validate(text_request("가" * 500)).input.text) == 500
    assert len(AssistantRequestCreate.model_validate(text_request("😀" * 500)).input.text) == 500
    assert validation_error(text_request("가" * 501))["type"] == "string_too_long"
    assert validation_error(text_request("😀" * 501))["type"] == "string_too_long"
    assert validation_error(text_request(""))["type"] == "string_too_short"


def test_text_whitespace_controls_and_normalization_contract() -> None:
    parsed = AssistantRequestCreate.model_validate(text_request(" \tA\nB\r "))
    assert parsed.input.text == "A\nB"
    assert validation_error(text_request(" \t\r\n "))["type"] == "string_too_short"

    nfd = unicodedata.normalize("NFD", "é")
    assert AssistantRequestCreate.model_validate(text_request(nfd)).input.text == nfd
    assert unicodedata.normalize("NFC", nfd) != nfd

    fixed_non_control_whitespace = "".join(
        character
        for character in sorted(ASSISTANT_WHITE_SPACE)
        if unicodedata.category(character) != "Cc"
    )
    assert AssistantRequestCreate.model_validate(
        text_request(fixed_non_control_whitespace + "값" + fixed_non_control_whitespace)
    ).input.text == "값"
    assert AssistantRequestCreate.model_validate(text_request("\u200b값\ufeff")).input.text == "\u200b값\ufeff"


@pytest.mark.parametrize("character", ["\x00", "\x0b", "\x7f", "\x80", "\x9f", "\x85"])
def test_text_rejects_forbidden_control_characters(character: str) -> None:
    assert validation_error(text_request("값" + character))["type"] == "invalid_value"


def test_text_rejects_unmatched_surrogate() -> None:
    assert validation_error(text_request("\ud800"))["type"] == "invalid_value"
    assert validation_error(text_request("\udc00"))["type"] == "invalid_value"
    assert validation_error(text_request("\ud800A"))["type"] == "invalid_value"
    assert AssistantRequestCreate.model_validate(text_request("\ud83d\ude00")).input.text == "😀"


def test_result_datetime_and_enum_validation_paths() -> None:
    value = result_payload("candidate_selection")
    value["expiresAt"] = datetime(2026, 10, 18, 10, 0, tzinfo=timezone.utc)
    parsed = RESULT_ADAPTER.validate_python(value)
    assert parsed.model_dump(mode="json", by_alias=True)["expiresAt"] == "2026-10-18T10:00:00Z"

    value["expiresAt"] = datetime(2026, 10, 18, 10, 0)
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(value)

    failure = result_payload("failure")
    failure["code"] = "NOT_A_CODE"
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(failure)
    failure["code"] = 1
    with pytest.raises(ValidationError):
        RESULT_ADAPTER.validate_python(failure)


def test_fingerprint_is_versioned_deterministic_and_does_not_mutate_input() -> None:
    original = {
        "input": {"text": "\u3000재고 확인\u00a0", "type": "text"},
        "requestId": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
        "acknowledgedUncertainRequestId": "EEEEEEEE-EEEE-4EEE-8EEE-EEEEEEEEEEEE",
    }
    before = deepcopy(original)
    first = AssistantRequestCreate.model_validate(original)
    equivalent = AssistantRequestCreate.model_validate(
        {
            "acknowledgedUncertainRequestId": ACKNOWLEDGED_ID,
            "requestId": REQUEST_ID,
            "input": {"type": "text", "text": "재고 확인"},
        }
    )
    assert original == before
    assert FINGERPRINT_VERSION == 1
    assert canonical_fingerprint(first) == canonical_fingerprint(equivalent)
    assert f'"version":{FINGERPRINT_VERSION}' in canonical_request_json(first)


def test_fingerprint_changes_for_meaning_input_variant_ids_and_candidate_case() -> None:
    text = AssistantRequestCreate.model_validate(text_request())
    changed_text = AssistantRequestCreate.model_validate(text_request("재고 변경"))
    selection_upper = AssistantRequestCreate.model_validate(
        {
            "requestId": REQUEST_ID,
            "conversationId": CONVERSATION_ID,
            "input": {
                "type": "selection",
                "questionId": QUESTION_ID,
                "candidateId": "Candidate-A",
            },
        }
    )
    selection_lower = AssistantRequestCreate.model_validate(
        {
            "requestId": REQUEST_ID,
            "conversationId": CONVERSATION_ID,
            "input": {
                "type": "selection",
                "questionId": QUESTION_ID,
                "candidateId": "candidate-a",
            },
        }
    )
    confirmation = AssistantRequestCreate.model_validate(
        {
            "requestId": REQUEST_ID,
            "conversationId": CONVERSATION_ID,
            "input": {
                "type": "confirmation",
                "questionId": QUESTION_ID,
                "approved": True,
            },
        }
    )
    acknowledged = AssistantRequestCreate.model_validate(
        text_request(acknowledgedUncertainRequestId=ACKNOWLEDGED_ID)
    )

    fingerprints = {
        canonical_fingerprint(value)
        for value in (
            text,
            changed_text,
            selection_upper,
            selection_lower,
            confirmation,
            acknowledged,
        )
    }
    assert len(fingerprints) == 6
