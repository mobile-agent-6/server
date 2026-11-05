from __future__ import annotations

import sqlite3
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.schedules import repository
from tests.conftest import FIXED_NOW
from tests.problem_assertions import assert_problem_contract


def create_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "재고 확인",
        "scheduledAt": "2026-09-13T04:45:00Z",
        "category": "INVENTORY",
    }
    payload.update(overrides)
    return payload


def create_schedule(
    client: TestClient, *, key: str | None = None, **overrides: object
):
    return client.post(
        "/api/v1/schedules",
        json=create_payload(**overrides),
        headers={"Idempotency-Key": key or str(uuid4())},
    )


def assert_request_id(response) -> None:
    identifier = UUID(response.headers["X-Request-ID"])
    assert identifier.version == 4
    if response.headers.get("content-type", "").startswith("application/problem+json"):
        body = response.json()
        assert_problem_contract(response, response.status_code, body["code"])


def test_create_get_list_patch_delete_vertical_flow(client: TestClient) -> None:
    created = create_schedule(client)
    assert created.status_code == 201
    assert created.headers["content-type"].startswith("application/json")
    assert created.headers["Location"] == f"/api/v1/schedules/{created.json()['id']}"
    assert set(created.json()) == {
        "id",
        "title",
        "scheduledAt",
        "category",
        "completed",
        "createdAt",
        "updatedAt",
    }
    assert created.json()["scheduledAt"] == "2026-09-13T04:45:00Z"
    assert created.json()["completed"] is False
    assert UUID(created.json()["id"]).version == 4
    assert_request_id(created)

    schedule_id = created.json()["id"]
    fetched = client.get(f"/api/v1/schedules/{schedule_id}")
    assert fetched.status_code == 200
    assert fetched.json() == created.json()

    listed = client.get(
        "/api/v1/schedules",
        params={
            "from": "2026-09-13T04:30:00Z",
            "to": "2026-09-14T04:30:00Z",
            "category": "INVENTORY",
            "completed": "false",
        },
    )
    assert listed.status_code == 200
    assert listed.json() == {"items": [created.json()]}

    no_op = client.patch(
        f"/api/v1/schedules/{schedule_id}",
        json={"scheduledAt": "2026-09-13T13:45:00+09:00"},
    )
    assert no_op.status_code == 200
    assert no_op.json()["updatedAt"] == created.json()["updatedAt"]

    client.app.state.clock.advance(timedelta(seconds=5))
    changed = client.patch(
        f"/api/v1/schedules/{schedule_id}",
        json={"title": " 변경됨 "},
    )
    assert changed.status_code == 200
    assert changed.json()["title"] == "변경됨"
    assert changed.json()["updatedAt"] != created.json()["updatedAt"]

    deleted = client.delete(f"/api/v1/schedules/{schedule_id}")
    repeated = client.delete(f"/api/v1/schedules/{schedule_id}")
    assert deleted.status_code == repeated.status_code == 204
    assert deleted.content == repeated.content == b""
    assert client.get(f"/api/v1/schedules/{schedule_id}").status_code == 404
    assert client.patch(f"/api/v1/schedules/{schedule_id}", json={"completed": True}).status_code == 404
    empty = client.get(
        "/api/v1/schedules",
        params={"from": "2026-09-13T04:30:00Z", "to": "2026-09-14T04:30:00Z"},
    )
    assert empty.json() == {"items": []}


def test_list_filters_range_boundaries_and_stable_order(client: TestClient) -> None:
    first = create_schedule(
        client,
        title="B",
        scheduledAt="2026-09-14T00:00:00Z",
        category="INBOUND",
    ).json()
    second = create_schedule(
        client,
        title="A",
        scheduledAt="2026-09-14T00:00:00Z",
        category="RETURN",
    ).json()
    at_to = create_schedule(
        client,
        title="Excluded",
        scheduledAt="2026-09-15T00:00:00Z",
    ).json()
    response = client.get(
        "/api/v1/schedules",
        params={"from": "2026-09-14T00:00:00Z", "to": "2026-09-15T00:00:00Z"},
    )
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == sorted(
        [first["id"], second["id"]]
    )
    assert at_to["id"] not in {item["id"] for item in response.json()["items"]}

    filtered = client.get(
        "/api/v1/schedules",
        params={
            "from": "2026-09-14T00:00:00Z",
            "to": "2026-09-15T00:00:00Z",
            "category": "RETURN",
            "completed": "false",
        },
    )
    assert [item["id"] for item in filtered.json()["items"]] == [second["id"]]


def test_all_five_categories_create_get_patch_and_filter(client: TestClient) -> None:
    categories = ("INBOUND", "INVENTORY", "OUTBOUND", "RETURN", "OTHER")
    created: dict[str, dict[str, object]] = {}
    for index, category in enumerate(categories):
        response = create_schedule(
            client,
            title=f"{category} 일정",
            scheduledAt=f"2026-09-13T0{index + 5}:00:00Z",
            category=category,
        )
        assert response.status_code == 201
        assert response.json()["category"] == category
        created[category] = response.json()
        fetched = client.get(f"/api/v1/schedules/{response.json()['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["category"] == category

    for category in categories:
        listed = client.get(
            "/api/v1/schedules",
            params={
                "from": "2026-09-13T04:30:00Z",
                "to": "2026-09-14T04:30:00Z",
                "category": category,
            },
        )
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["items"]] == [
            created[category]["id"]
        ]

    for current, updated_category in zip(
        categories, categories[1:] + categories[:1], strict=True
    ):
        updated = client.patch(
            f"/api/v1/schedules/{created[current]['id']}",
            json={"category": updated_category},
        )
        assert updated.status_code == 200
        assert updated.json()["category"] == updated_category


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({"to": "2026-09-14T00:00:00Z"}, "VALIDATION_ERROR"),
        ({"from": "2026-09-14T00:00:00Z"}, "VALIDATION_ERROR"),
        (
            {"from": "2026-09-14T00:00:00Z", "to": "2026-09-14T00:00:00Z"},
            "INVALID_DATE_RANGE",
        ),
        (
            {"from": "2026-01-01T00:00:00Z", "to": "2026-04-04T00:00:00Z"},
            "DATE_RANGE_TOO_LARGE",
        ),
    ],
)
def test_list_range_errors(client: TestClient, params: dict[str, str], code: str) -> None:
    response = client.get("/api/v1/schedules", params=params)
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert_request_id(response)


def test_exactly_92_day_list_range_is_allowed(client: TestClient) -> None:
    response = client.get(
        "/api/v1/schedules",
        params={"from": "2026-01-01T00:00:00Z", "to": "2026-04-03T00:00:00Z"},
    )
    assert response.status_code == 200


@pytest.mark.parametrize("value", ["yes", "True", "1", "FALSE"])
def test_completed_query_is_strict(client: TestClient, value: str) -> None:
    response = client.get(
        "/api/v1/schedules",
        params={
            "from": "2026-09-13T04:30:00Z",
            "to": "2026-09-14T04:30:00Z",
            "completed": value,
        },
    )
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {
            "location": "query",
            "parameter": "completed",
            "code": "INVALID_FORMAT",
            "detail": "Only lowercase true or false is allowed.",
        }
    ]


@pytest.mark.parametrize(
    ("headers", "expected"),
    [({}, "REQUIRED"), ({"Idempotency-Key": ""}, "TOO_SHORT"), ({"Idempotency-Key": "a" * 256}, "TOO_LONG"), ({"Idempotency-Key": "한글".encode()}, "INVALID_FORMAT"), ({"Idempotency-Key": "a\x7f"}, "INVALID_FORMAT")],
)
def test_idempotency_key_format_validation(
    client: TestClient, headers: dict[str, str | bytes], expected: str
) -> None:
    response = client.post("/api/v1/schedules", json=create_payload(), headers=headers)
    assert response.status_code == 422
    assert response.json()["errors"][0]["location"] == "header"
    assert response.json()["errors"][0]["parameter"] == "Idempotency-Key"
    assert response.json()["errors"][0]["code"] == expected


def test_body_content_and_validation_contract(client: TestClient) -> None:
    missing_type = client.post(
        "/api/v1/schedules",
        content=b'{"title":"x"}',
        headers={"Idempotency-Key": "key"},
    )
    assert missing_type.status_code == 422
    assert missing_type.json()["errors"][0]["pointer"] == ""
    assert missing_type.json()["errors"][0]["code"] == "INVALID_TYPE"

    scalar = client.post(
        "/api/v1/schedules",
        json=[1, 2],
        headers={"Idempotency-Key": "key"},
    )
    assert scalar.status_code == 422
    assert scalar.json()["errors"][0]["pointer"] == ""

    null_body = client.post(
        "/api/v1/schedules",
        content=b"null",
        headers={"Content-Type": "application/json", "Idempotency-Key": "key"},
    )
    assert null_body.status_code == 422
    assert null_body.json()["errors"][0]["pointer"] == ""
    assert null_body.json()["errors"][0]["code"] == "INVALID_TYPE"

    malformed = client.post(
        "/api/v1/schedules",
        content=b'{"title":',
        headers={"Content-Type": "application/json", "Idempotency-Key": "key"},
    )
    assert malformed.status_code == 400
    assert malformed.json()["code"] == "MALFORMED_REQUEST"
    assert "errors" not in malformed.json()

    unknown = create_schedule(client, completed=False)
    assert unknown.status_code == 422
    assert unknown.json()["errors"][0]["pointer"] == "/completed"
    assert unknown.json()["errors"][0]["code"] == "UNKNOWN_FIELD"

    wrong_category = create_schedule(client, category="meeting")
    assert wrong_category.status_code == 422
    assert wrong_category.json()["errors"][0]["code"] == "INVALID_ENUM"

    removed_reminder = create_schedule(client, reminderMinutesBefore=None)
    assert removed_reminder.status_code == 422
    assert removed_reminder.json()["errors"][0]["pointer"] == "/reminderMinutesBefore"
    assert removed_reminder.json()["errors"][0]["code"] == "UNKNOWN_FIELD"


def test_validation_error_order_is_stable(client: TestClient) -> None:
    invalid_body = create_payload(title="", scheduledAt="bad")

    missing_key = client.post("/api/v1/schedules", json=invalid_body)
    assert missing_key.status_code == 422
    assert missing_key.json()["errors"] == [
        {
            "location": "header",
            "code": "REQUIRED",
            "detail": "This value is required.",
            "parameter": "Idempotency-Key",
        },
        {
            "location": "body",
            "code": "TOO_SHORT",
            "detail": "This value is too short.",
            "pointer": "/title",
        },
        {
            "location": "body",
            "code": "INVALID_FORMAT",
            "detail": "The value has an invalid format.",
            "pointer": "/scheduledAt",
        },
    ]

    long_key = client.post(
        "/api/v1/schedules",
        json=invalid_body,
        headers={"Idempotency-Key": "a" * 256},
    )
    assert long_key.status_code == 422
    assert long_key.json()["errors"] == [
        {
            "location": "header",
            "parameter": "Idempotency-Key",
            "code": "TOO_LONG",
            "detail": "The header cannot exceed 255 characters.",
        }
    ]

    invalid_completed = client.get(
        "/api/v1/schedules",
        params={
            "from": "bad",
            "to": "2026-09-14T00:00:00Z",
            "completed": "yes",
        },
    )
    assert invalid_completed.status_code == 422
    assert invalid_completed.json()["errors"] == [
        {
            "location": "query",
            "parameter": "completed",
            "code": "INVALID_FORMAT",
            "detail": "Only lowercase true or false is allowed.",
        }
    ]

    invalid_range = client.get(
        "/api/v1/schedules",
        params={"from": "bad", "to": "also-bad"},
    )
    assert invalid_range.status_code == 422
    assert invalid_range.json()["errors"] == [
        {
            "location": "query",
            "code": "INVALID_FORMAT",
            "detail": "The value has an invalid format.",
            "parameter": "from",
        },
        {
            "location": "query",
            "code": "INVALID_FORMAT",
            "detail": "The value has an invalid format.",
            "parameter": "to",
        },
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reminderMinutesBefore", None),
        ("reminderAt", "2026-09-13T04:35:00Z"),
    ],
)
def test_create_and_patch_reject_removed_reminder_fields(
    client: TestClient, field: str, value: object
) -> None:
    created = create_schedule(client)
    assert created.status_code == 201

    create_rejected = create_schedule(client, **{field: value})
    patch_rejected = client.patch(
        f"/api/v1/schedules/{created.json()['id']}", json={field: value}
    )

    for response in (create_rejected, patch_rejected):
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"
        assert response.json()["errors"][0]["pointer"] == f"/{field}"
        assert response.json()["errors"][0]["code"] == "UNKNOWN_FIELD"


@pytest.mark.parametrize(
    "category", ["MEETING", "PERSONAL", "PICKING", "inventory", "입고"]
)
def test_removed_and_noncanonical_categories_are_rejected(
    client: TestClient, category: str
) -> None:
    response = create_schedule(client, category=category)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["errors"][0]["pointer"] == "/category"
    assert response.json()["errors"][0]["code"] == "INVALID_ENUM"


@pytest.mark.parametrize(
    "category", ["MEETING", "PERSONAL", "PICKING", "outbound", "출고"]
)
def test_patch_and_list_reject_removed_and_noncanonical_categories(
    client: TestClient, category: str
) -> None:
    schedule_id = create_schedule(client).json()["id"]

    updated = client.patch(
        f"/api/v1/schedules/{schedule_id}", json={"category": category}
    )
    listed = client.get(
        "/api/v1/schedules",
        params={
            "from": "2026-09-13T04:30:00Z",
            "to": "2026-09-14T04:30:00Z",
            "category": category,
        },
    )

    for response in (updated, listed):
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"
        assert response.json()["errors"][0]["code"] == "INVALID_ENUM"


def test_non_utf8_json_body_is_malformed_request(client: TestClient) -> None:
    response = client.post(
        "/api/v1/schedules",
        content=b'{"title":"\xff"}',
        headers={"Content-Type": "application/json", "Idempotency-Key": "key"},
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 400
    assert response.json()["code"] == "MALFORMED_REQUEST"
    assert response.json()["title"] == "Malformed request"
    assert "errors" not in response.json()
    assert_request_id(response)


def test_patch_empty_null_past_and_not_found_rules(client: TestClient) -> None:
    schedule_id = create_schedule(client).json()["id"]
    empty = client.patch(f"/api/v1/schedules/{schedule_id}", json={})
    assert empty.status_code == 422
    assert empty.json()["code"] == "EMPTY_UPDATE"

    null_title = client.patch(f"/api/v1/schedules/{schedule_id}", json={"title": None})
    assert null_title.status_code == 422
    assert null_title.json()["errors"][0]["code"] == "NULL_NOT_ALLOWED"

    string_boolean = client.patch(
        f"/api/v1/schedules/{schedule_id}", json={"completed": "true"}
    )
    assert string_boolean.status_code == 422
    assert string_boolean.json()["errors"][0]["code"] == "INVALID_TYPE"

    past = client.patch(
        f"/api/v1/schedules/{schedule_id}",
        json={"scheduledAt": "2026-09-13T04:29:59Z"},
    )
    assert past.status_code == 422
    assert past.json()["code"] == "SCHEDULE_IN_THE_PAST"

    missing = str(uuid4())
    assert client.patch(f"/api/v1/schedules/{missing}", json={"completed": True}).status_code == 404
    assert client.delete(f"/api/v1/schedules/{missing}").status_code == 404


def test_create_past_rejected_but_near_future_allowed(client: TestClient) -> None:
    past = create_schedule(client, scheduledAt="2026-09-13T04:29:59Z")
    assert past.status_code == 422
    assert past.json()["code"] == "SCHEDULE_IN_THE_PAST"

    near_future = create_schedule(client, scheduledAt="2026-09-13T04:35:00Z")
    assert near_future.status_code == 201


def test_past_schedule_allows_non_time_patch(client: TestClient) -> None:
    created = create_schedule(client).json()
    client.app.state.clock.advance(timedelta(hours=1))
    response = client.patch(
        f"/api/v1/schedules/{created['id']}", json={"completed": True}
    )
    assert response.status_code == 200
    assert response.json()["completed"] is True


def test_route_path_and_method_problem_details(client: TestClient) -> None:
    missing = client.get("/does-not-exist?secret=no")
    assert missing.status_code == 404
    assert missing.json()["code"] == "ROUTE_NOT_FOUND"
    assert missing.json()["instance"] == "/does-not-exist"

    method = client.put("/api/v1/schedules")
    assert method.status_code == 405
    assert method.json()["code"] == "METHOD_NOT_ALLOWED"
    assert set(method.headers["Allow"].split(", ")) == {"GET", "POST"}
    assert_request_id(method)

    invalid_id = client.get("/api/v1/schedules/not-a-uuid")
    assert invalid_id.status_code == 422
    error = invalid_id.json()["errors"][0]
    assert error["location"] == "path"
    assert error["parameter"] == "scheduleId"
    assert error["code"] == "INVALID_FORMAT"

    detail_method = client.post(f"/api/v1/schedules/{uuid4()}")
    assert detail_method.status_code == 405
    assert set(detail_method.headers["Allow"].split(", ")) == {
        "DELETE",
        "GET",
        "PATCH",
    }

    redirect = client.get("/api/v1/schedules/", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["X-Request-ID"]


def test_client_request_id_is_ignored(client: TestClient) -> None:
    supplied = str(uuid4())
    response = client.get("/does-not-exist", headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] != supplied
    assert_request_id(response)


def test_transient_database_error_maps_to_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = sqlite3.OperationalError("unable to open database file")
    original.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN
    error = sa.exc.OperationalError("select", {}, original)
    monkeypatch.setattr(repository, "list_active", lambda *args, **kwargs: (_ for _ in ()).throw(error))

    response = client.get(
        "/api/v1/schedules",
        params={"from": "2026-09-13T04:30:00Z", "to": "2026-09-14T04:30:00Z"},
    )
    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert_request_id(response)


def test_unexpected_error_maps_to_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args, **kwargs):
        raise RuntimeError("private detail")

    monkeypatch.setattr(repository, "list_active", fail)
    response = client.get(
        "/api/v1/schedules",
        params={"from": "2026-09-13T04:30:00Z", "to": "2026-09-14T04:30:00Z"},
    )
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "private detail" not in response.text
    assert_request_id(response)


def test_non_transient_database_schema_error_stays_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = sqlite3.OperationalError("no such table: schedules")
    original.sqlite_errorcode = sqlite3.SQLITE_ERROR
    error = sa.exc.OperationalError("select", {}, original)
    monkeypatch.setattr(
        repository,
        "list_active",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )
    response = client.get(
        "/api/v1/schedules",
        params={"from": "2026-09-13T04:30:00Z", "to": "2026-09-14T04:30:00Z"},
    )
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "no such table" not in response.text
    assert_request_id(response)


def test_missing_database_returns_503_without_creating_file(tmp_path) -> None:
    missing = tmp_path / "missing.db"
    app = create_app(Settings(_env_file=None, database_url=f"sqlite:///{missing}"))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            "/api/v1/schedules",
            params={"from": "2026-09-13T04:30:00Z", "to": "2026-09-14T04:30:00Z"},
        )
    app.state.engine.dispose()
    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert not missing.exists()


def test_cors_headers_are_kept_on_unexpected_500(
    database_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(
        Settings(
            _env_file=None,
            database_url=f"sqlite:///{database_path}",
            cors_origins=["http://localhost:8081"],
        )
    )

    def fail(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(repository, "list_active", fail)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            "/api/v1/schedules",
            params={"from": "2026-09-13T04:30:00Z", "to": "2026-09-14T04:30:00Z"},
            headers={"Origin": "http://localhost:8081"},
        )
    app.state.engine.dispose()
    assert response.status_code == 500
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:8081"
