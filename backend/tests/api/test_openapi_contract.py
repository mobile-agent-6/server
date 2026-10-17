from fastapi.testclient import TestClient


def test_complete_openapi_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["openapi"] == "3.1.0"
    assert set(schema["paths"]) == {
        "/health",
        "/api/v1/system/time-sync",
        "/api/v1/schedules",
        "/api/v1/schedules/{scheduleId}",
    }
    operations = {
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
    }
    assert operations == {
        "healthCheck",
        "syncSystemTime",
        "listSchedules",
        "createSchedule",
        "getSchedule",
        "updateSchedule",
        "deleteSchedule",
    }

    components = schema["components"]["schemas"]
    for name in (
        "Category",
        "ScheduleResponse",
        "ScheduleCreate",
        "ScheduleUpdate",
        "ScheduleListResponse",
        "HealthResponse",
        "TimeSyncRequest",
        "TimeSyncResponse",
        "Problem",
        "ValidationErrorItem",
    ):
        assert name in components
    assert "HTTPValidationError" not in components
    assert "ValidationError" not in components
    assert components["Category"]["enum"] == [
        "INBOUND",
        "INVENTORY",
        "OUTBOUND",
        "RETURN",
        "OTHER",
    ]
    assert components["ScheduleCreate"]["additionalProperties"] is False
    assert set(components["ScheduleCreate"]["required"]) == {
        "title",
        "scheduledAt",
        "category",
    }
    assert "completed" not in components["ScheduleCreate"]["properties"]
    assert "required" not in components["ScheduleUpdate"]
    for field in ("title", "scheduledAt", "category", "completed"):
        assert {branch.get("type") for branch in components["ScheduleUpdate"]["properties"][field].get("anyOf", [])} != {"null"}
        assert not any(
            branch.get("type") == "null"
            for branch in components["ScheduleUpdate"]["properties"][field].get("anyOf", [])
        )
    assert "reminderMinutesBefore" not in components["ScheduleCreate"]["properties"]
    assert "reminderMinutesBefore" not in components["ScheduleUpdate"]["properties"]
    assert "reminderMinutesBefore" not in components["ScheduleResponse"]["properties"]
    assert "reminderAt" not in components["ScheduleResponse"]["properties"]
    assert set(components["ScheduleResponse"]["required"]) == {
        "id",
        "title",
        "scheduledAt",
        "category",
        "completed",
        "createdAt",
        "updatedAt",
    }
    assert set(components["HealthResponse"]["required"]) == {
        "status",
        "apiVersion",
        "serverTime",
    }
    assert components["TimeSyncRequest"]["additionalProperties"] is False
    assert components["TimeSyncRequest"]["required"] == ["currentTime"]
    assert set(components["TimeSyncResponse"]["required"]) == {
        "adjusted",
        "previousServerTime",
        "serverTime",
    }
    for component, field in (
        ("HealthResponse", "serverTime"),
        ("TimeSyncRequest", "currentTime"),
        ("TimeSyncResponse", "previousServerTime"),
        ("TimeSyncResponse", "serverTime"),
    ):
        assert components[component]["properties"][field]["type"] == "string"
        assert components[component]["properties"][field]["format"] == "date-time"
    for field in ("scheduledAt", "createdAt", "updatedAt"):
        property_schema = components["ScheduleResponse"]["properties"][field]
        assert property_schema["type"] == "string"
        assert property_schema["format"] == "date-time"
    assert components["Problem"]["properties"]["status"]["type"] == "integer"
    assert components["Problem"]["properties"]["requestId"] == {
        "format": "uuid",
        "title": "Requestid",
        "type": "string",
    }
    validation_properties = components["ValidationErrorItem"]["properties"]
    assert {"location", "pointer", "parameter", "code", "detail"} == set(
        validation_properties
    )


def test_openapi_problem_content_and_headers(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    for path in schema["paths"].values():
        for operation in path.values():
            for status, response in operation["responses"].items():
                assert "X-Request-ID" in response["headers"]
                if status.startswith(("4", "5")):
                    assert set(response["content"]) == {"application/problem+json"}
                    assert response["content"]["application/problem+json"]["schema"] == {
                        "$ref": "#/components/schemas/Problem"
                    }
    create = schema["paths"]["/api/v1/schedules"]["post"]
    assert {parameter["name"] for parameter in create["parameters"]} == {"Idempotency-Key"}
    assert create["parameters"][0]["required"] is True
    assert {"Location", "Idempotent-Replayed", "X-Request-ID"} <= set(
        create["responses"]["201"]["headers"]
    )
    assert "content" not in schema["paths"]["/api/v1/schedules/{scheduleId}"]["delete"]["responses"]["204"]

    expected_errors = {
        "healthCheck": {"500", "503"},
        "syncSystemTime": {"400", "409", "422", "500", "503"},
        "listSchedules": {"422", "500", "503"},
        "createSchedule": {"400", "409", "422", "500", "503"},
        "getSchedule": {"404", "422", "500", "503"},
        "updateSchedule": {"400", "404", "422", "500", "503"},
        "deleteSchedule": {"404", "422", "500", "503"},
    }
    for path in schema["paths"].values():
        for operation in path.values():
            actual = {
                status
                for status in operation["responses"]
                if status.startswith(("4", "5"))
            }
            assert actual == expected_errors[operation["operationId"]]
            for status, response in operation["responses"].items():
                if status == "204":
                    assert "content" not in response
                elif not status.startswith(("4", "5")):
                    assert set(response["content"]) == {"application/json"}
                    assert "schema" in response["content"]["application/json"]
