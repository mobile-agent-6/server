from fastapi.testclient import TestClient


def test_crud_openapi_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["openapi"] == "3.1.0"
    assert set(schema["paths"]) == {
        "/api/v1/schedules",
        "/api/v1/schedules/{scheduleId}",
    }
    operations = {
        operation["operationId"]
        for path in schema["paths"].values()
        for operation in path.values()
    }
    assert operations == {
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
        "Problem",
        "ValidationErrorItem",
    ):
        assert name in components
    assert "HTTPValidationError" not in components
    assert "ValidationError" not in components
    assert components["Category"]["enum"] == [
        "INVENTORY",
        "RETURN",
        "MEETING",
        "PERSONAL",
        "OTHER",
    ]
    assert components["ScheduleCreate"]["additionalProperties"] is False
    assert set(components["ScheduleCreate"]["required"]) == {
        "title",
        "scheduledAt",
        "category",
        "reminderMinutesBefore",
    }
    assert "completed" not in components["ScheduleCreate"]["properties"]
    assert "required" not in components["ScheduleUpdate"]
    for field in ("title", "scheduledAt", "category", "completed"):
        assert {branch.get("type") for branch in components["ScheduleUpdate"]["properties"][field].get("anyOf", [])} != {"null"}
        assert not any(
            branch.get("type") == "null"
            for branch in components["ScheduleUpdate"]["properties"][field].get("anyOf", [])
        )
    assert any(
        branch.get("type") == "null"
        for branch in components["ScheduleUpdate"]["properties"]["reminderMinutesBefore"]["anyOf"]
    )
    assert set(components["ScheduleResponse"]["required"]) == {
        "id",
        "title",
        "scheduledAt",
        "category",
        "completed",
        "reminderMinutesBefore",
        "reminderAt",
        "createdAt",
        "updatedAt",
    }


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
