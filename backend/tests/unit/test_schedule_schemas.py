from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.modules.schedules.category import Category
from app.modules.schedules.schemas import ScheduleCreate, ScheduleUpdate


def valid_create(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "title": "재고 확인",
        "scheduledAt": "2026-09-13T13:45:00+09:00",
        "category": "INVENTORY",
    }
    values.update(overrides)
    return values


def error_type(values: dict[str, object]) -> str:
    with pytest.raises(ValidationError) as caught:
        ScheduleCreate.model_validate(values)
    return caught.value.errors()[0]["type"]


def test_create_normalizes_title_datetime_and_category() -> None:
    schema = ScheduleCreate.model_validate(valid_create(title="  재고 확인  "))

    assert schema.title == "재고 확인"
    assert schema.scheduled_at == datetime(2026, 9, 13, 4, 45, tzinfo=timezone.utc)
    assert schema.category is Category.INVENTORY


@pytest.mark.parametrize("category", list(Category))
def test_create_accepts_each_confirmed_category(category: Category) -> None:
    schema = ScheduleCreate.model_validate(valid_create(category=category.value))

    assert schema.category is category


@pytest.mark.parametrize(
    "category", ["MEETING", "PERSONAL", "PICKING", "inbound", "입고"]
)
def test_create_rejects_removed_and_noncanonical_categories(category: str) -> None:
    assert error_type(valid_create(category=category)) == "enum"


@pytest.mark.parametrize("title", ["a\n", "\t제목", "제\x00목"])
def test_title_rejects_control_characters_before_trimming(title: str) -> None:
    assert error_type(valid_create(title=title)) == "control_character"


def test_title_uses_unicode_code_point_length_after_trim() -> None:
    assert len(ScheduleCreate.model_validate(valid_create(title="😀" * 100)).title) == 100
    assert error_type(valid_create(title=" ")) == "string_too_short"
    assert error_type(valid_create(title="😀" * 101)) == "string_too_long"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-13T04:30:00.1234567Z", "invalid_format"),
        ("2026-09-13T04:30:00", "timezone_required"),
        ("2026-09-13 04:30:00Z", "invalid_format"),
        ("2026-09-13T13:30:00+0900", "invalid_format"),
        ("0001-01-01T00:00:00+14:00", "invalid_value"),
        ("9999-12-31T23:59:59-14:00", "invalid_value"),
    ],
)
def test_datetime_contract_boundaries(value: str, expected: str) -> None:
    assert error_type(valid_create(scheduledAt=value)) == expected


def test_create_rejects_unknown_snake_case_and_read_only_fields() -> None:
    assert error_type(valid_create(scheduled_at="2026-09-13T04:45:00Z")) == "extra_forbidden"
    assert error_type(valid_create(completed=False)) == "extra_forbidden"
    assert error_type(valid_create(id="c8ca93cc-e8e5-48f0-9381-46817759912e")) == "extra_forbidden"


def test_update_distinguishes_omitted_null_and_empty() -> None:
    empty = ScheduleUpdate.model_validate({})
    changed = ScheduleUpdate.model_validate({"completed": True})

    assert empty.model_fields_set == set()
    assert changed.model_fields_set == {"completed"}
    with pytest.raises(ValidationError):
        ScheduleUpdate.model_validate({"title": None})


@pytest.mark.parametrize(
    ("schema", "values"),
    [
        (ScheduleCreate, valid_create(reminderMinutesBefore=None)),
        (ScheduleCreate, valid_create(reminderAt="2026-09-13T04:35:00Z")),
        (ScheduleUpdate, {"reminderMinutesBefore": None}),
        (ScheduleUpdate, {"reminderAt": "2026-09-13T04:35:00Z"}),
    ],
)
def test_removed_reminder_fields_are_unknown(schema, values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(values)
