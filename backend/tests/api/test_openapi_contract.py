import re

from fastapi.testclient import TestClient


DATETIME_PATTERN = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
TITLE_PATTERN = r"^[^\u0000-\u001F\u007F-\u009F]*$"
IDEMPOTENCY_KEY_PATTERN = r"^[\x20-\x7E]*$"
DATETIME_DESCRIPTION = (
    "RFC 3339 datetime with seconds and uppercase Z or a colon-separated "
    "offset; fractional seconds may contain 1 to 6 digits."
)
TITLE_DESCRIPTION = (
    "Trimmed before validation; 1 to 100 Unicode code points; "
    "Cc control characters are not allowed."
)


def test_complete_openapi_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["openapi"] == "3.1.0"
    assert set(schema["paths"]) == {
        "/health",
        "/api/v1/system/time-sync",
        "/api/v1/schedules",
        "/api/v1/schedules/{scheduleId}",
        "/api/v1/assistant/requests",
        "/api/v1/assistant/requests/{requestId}",
        "/api/v1/assistant/conversations/{conversationId}/cancel",
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
        "createAssistantRequest",
        "getAssistantRequest",
        "cancelAssistantTarget",
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
        "AssistantRequestCreate",
        "AssistantRequestAcceptedResponse",
        "AssistantRequestStatusResponse",
        "AssistantCancelRequest",
        "AssistantCancelResponse",
        "AssistantResultNeedMoreInfo",
        "AssistantResultCandidateSelection",
        "AssistantResultConfirmation",
        "AssistantResultOutcome",
        "AssistantResultFailure",
        "AssistantResultUncertain",
        "AssistantCandidate",
        "AssistantScheduleSummary",
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
    for component in ("ScheduleCreate", "ScheduleUpdate"):
        assert components[component]["properties"]["title"] == {
            "description": TITLE_DESCRIPTION,
            "maxLength": 100,
            "minLength": 1,
            "pattern": TITLE_PATTERN,
            "title": "Title",
            "type": "string",
        }
        assert components[component]["properties"]["scheduledAt"] == {
            "description": DATETIME_DESCRIPTION,
            "format": "date-time",
            "pattern": DATETIME_PATTERN,
            "title": "Scheduledat",
            "type": "string",
        }
    assert "completed" not in components["ScheduleCreate"]["properties"]
    assert "required" not in components["ScheduleUpdate"]
    assert components["ScheduleUpdate"]["minProperties"] == 1
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
    problem_properties = components["Problem"]["properties"]
    assert {
        "assistantRequestId",
        "blockingRequestId",
        "activeQuestionId",
        "activeQuestionRequestId",
    } <= set(problem_properties)

    assistant_request = components["AssistantRequestCreate"]
    assert assistant_request["additionalProperties"] is False
    assert set(assistant_request["required"]) == {"requestId", "input"}
    assert assistant_request["properties"]["input"]["discriminator"]["propertyName"] == "type"
    assert set(assistant_request["properties"]["input"]["discriminator"]["mapping"]) == {
        "text",
        "selection",
        "confirmation",
    }
    for name in (
        "AssistantResultNeedMoreInfo",
        "AssistantResultCandidateSelection",
        "AssistantResultConfirmation",
        "AssistantResultOutcome",
        "AssistantResultFailure",
        "AssistantResultUncertain",
    ):
        assert components[name]["additionalProperties"] is False
    assert "expiresAt" not in components["AssistantResultNeedMoreInfo"]["properties"]
    candidate_result = components["AssistantResultCandidateSelection"]
    assert {"expiresAt", "candidates"} <= set(candidate_result["required"])
    assert candidate_result["properties"]["candidates"]["minItems"] == 2
    assert candidate_result["properties"]["candidates"]["maxItems"] == 10
    confirmation = components["AssistantResultConfirmation"]
    assert "expiresAt" in confirmation["required"]
    assert "allOf" in confirmation
    confirmation_changes = components["AssistantConfirmationChanges"]
    assert confirmation_changes["minProperties"] == 1
    assert confirmation_changes["properties"]["scheduledAt"] == {
        "format": "date-time",
        "title": "Scheduledat",
        "type": "string",
    }
    assert "scheduledAt" not in confirmation_changes.get("required", [])
    assert "default" not in confirmation_changes["properties"]["scheduledAt"]
    assert "anyOf" not in confirmation_changes["properties"]["scheduledAt"]
    assert "oneOf" in components["AssistantResultOutcome"]
    assert "oneOf" in components["AssistantResultFailure"]
    assert "schedulesChanged" not in components["AssistantResultUncertain"]["properties"]

    public_assistant_components = {
        "AssistantRequestCreate",
        "AssistantRequestAcceptedResponse",
        "AssistantRequestStatusResponse",
        "AssistantCancelRequest",
        "AssistantCancelResponse",
        "AssistantResultNeedMoreInfo",
        "AssistantResultCandidateSelection",
        "AssistantResultConfirmation",
        "AssistantResultOutcome",
        "AssistantResultFailure",
        "AssistantResultUncertain",
        "AssistantCandidate",
        "AssistantScheduleSummary",
    }
    existing_non_assistant_components = {
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
    }
    auxiliary = set(components) - public_assistant_components - existing_non_assistant_components
    assert auxiliary == {
        "AssistantCancelTarget",
        "AssistantCancelTargetState",
        "AssistantCancelTargetType",
        "AssistantConfirmationChanges",
        "AssistantConfirmationOperation",
        "AssistantFailureCode",
        "AssistantFailureNextAction",
        "AssistantInputConfirmation",
        "AssistantInputSelection",
        "AssistantInputText",
        "AssistantOutcome",
        "AssistantOutcomeOperation",
        "AssistantRequestStatus",
    }
    assert all(name.startswith("Assistant") for name in auxiliary)
    assert all(
        "reminderMinutesBefore" not in component.get("properties", {})
        and "reminderAt" not in component.get("properties", {})
        for name, component in components.items()
        if name.startswith("Assistant")
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
    listed = schema["paths"]["/api/v1/schedules"]["get"]
    list_parameters = {parameter["name"]: parameter for parameter in listed["parameters"]}
    for name, title in (("from", "From"), ("to", "To")):
        assert list_parameters[name]["schema"] == {
            "description": DATETIME_DESCRIPTION,
            "format": "date-time",
            "pattern": DATETIME_PATTERN,
            "title": title,
            "type": "string",
        }
        assert "(?P" not in list_parameters[name]["schema"]["pattern"]
    assert list_parameters["completed"]["required"] is False
    assert list_parameters["completed"]["schema"] == {
        "description": "When present, send lowercase true or false.",
        "title": "Completed",
        "type": "boolean",
    }
    assert list_parameters["category"]["required"] is False
    assert list_parameters["category"]["schema"] == {
        "$ref": "#/components/schemas/Category"
    }
    assert {parameter["name"] for parameter in create["parameters"]} == {"Idempotency-Key"}
    assert create["parameters"][0]["required"] is True
    assert create["parameters"][0]["schema"] == {
        "description": "1 to 255 control-free ASCII characters.",
        "maxLength": 255,
        "minLength": 1,
        "pattern": IDEMPOTENCY_KEY_PATTERN,
        "title": "Idempotency-Key",
        "type": "string",
    }
    assert {"Location", "Idempotent-Replayed", "X-Request-ID"} <= set(
        create["responses"]["201"]["headers"]
    )
    assert create["responses"]["201"]["headers"]["Location"] == {
        "description": "Relative path of the created schedule.",
        "schema": {"type": "string"},
    }
    assert create["responses"]["201"]["headers"]["Idempotent-Replayed"] == {
        "description": (
            "Present only when this 201 response replays an earlier successful create."
        ),
        "schema": {"enum": ["true"], "type": "string"},
    }
    assert "content" not in schema["paths"]["/api/v1/schedules/{scheduleId}"]["delete"]["responses"]["204"]

    expected_errors = {
        "healthCheck": {"500", "503"},
        "syncSystemTime": {"400", "409", "422", "500", "503"},
        "listSchedules": {"422", "500", "503"},
        "createSchedule": {"400", "409", "422", "500", "503"},
        "getSchedule": {"404", "422", "500", "503"},
        "updateSchedule": {"400", "404", "422", "500", "503"},
        "deleteSchedule": {"404", "422", "500", "503"},
        "createAssistantRequest": {
            "400",
            "404",
            "409",
            "413",
            "422",
            "500",
            "503",
        },
        "getAssistantRequest": {"404", "410", "422", "500", "503"},
        "cancelAssistantTarget": {"400", "404", "413", "422", "500", "503"},
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


def test_assistant_openapi_success_statuses(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    expected = {
        ("/api/v1/assistant/requests", "post"): {"200", "202"},
        ("/api/v1/assistant/requests/{requestId}", "get"): {"200"},
        (
            "/api/v1/assistant/conversations/{conversationId}/cancel",
            "post",
        ): {"200"},
    }

    for (path, method), expected_statuses in expected.items():
        actual_statuses = {
            status
            for status in schema["paths"][path][method]["responses"]
            if status.startswith(("2", "3"))
        }
        assert actual_statuses == expected_statuses


def test_schedule_openapi_patterns_match_http_contract(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    components = schema["components"]["schemas"]
    create_operation = schema["paths"]["/api/v1/schedules"]["post"]

    datetime_schema = components["ScheduleCreate"]["properties"]["scheduledAt"]
    datetime_cases = (
        ("2026-09-13T04:45:00Z", True),
        ("2026-09-13T13:45:00+09:00", True),
        ("2026-09-13T04:45:00.123456Z", True),
        ("2026-09-13T04:45:00.1234567Z", False),
        ("2026-09-13t04:45:00z", False),
        ("2026-09-13T04:45:00", False),
    )
    for index, (value, expected) in enumerate(datetime_cases):
        assert (re.fullmatch(datetime_schema["pattern"], value) is not None) is expected
        response = client.post(
            "/api/v1/schedules",
            json={"title": "datetime contract", "scheduledAt": value, "category": "OTHER"},
            headers={"Idempotency-Key": f"datetime-contract-{index}"},
        )
        assert (response.status_code == 201) is expected

    title_schema = components["ScheduleCreate"]["properties"]["title"]
    title_cases = (
        ("재고 확인", True),
        ("😀" * 100, True),
        ("😀" * 101, False),
        ("제\n목", False),
    )
    for index, (value, expected) in enumerate(title_cases):
        schema_accepts = (
            title_schema["minLength"] <= len(value) <= title_schema["maxLength"]
            and re.fullmatch(title_schema["pattern"], value) is not None
        )
        assert schema_accepts is expected
        response = client.post(
            "/api/v1/schedules",
            json={
                "title": value,
                "scheduledAt": "2026-09-13T04:50:00Z",
                "category": "OTHER",
            },
            headers={"Idempotency-Key": f"title-contract-{index}"},
        )
        assert (response.status_code == 201) is expected

    key_schema = create_operation["parameters"][0]["schema"]
    key_cases = (
        ("contract-key", True),
        ("", False),
        ("a" * 256, False),
        ("한글", False),
    )
    for index, (value, expected) in enumerate(key_cases):
        schema_accepts = (
            key_schema["minLength"] <= len(value) <= key_schema["maxLength"]
            and re.fullmatch(key_schema["pattern"], value) is not None
        )
        assert schema_accepts is expected
        header_value: str | bytes = value.encode() if value == "한글" else value
        response = client.post(
            "/api/v1/schedules",
            json={
                "title": f"key contract {index}",
                "scheduledAt": "2026-09-13T04:55:00Z",
                "category": "OTHER",
            },
            headers={"Idempotency-Key": header_value},
        )
        assert (response.status_code == 201) is expected
