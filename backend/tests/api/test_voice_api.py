from __future__ import annotations

import hashlib
import io
import logging
import wave
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from app.modules.assistant.model import (
    AssistantConversation,
    AssistantQuestion,
    AssistantQuestionStatus,
    AssistantRequest,
)
from app.modules.assistant.schemas import AssistantRequestStatusResponse
from app.modules.voice import router as voice_router
from app.modules.voice.audio import MAX_VOICE_BYTES
from app.modules.voice.client import LocalVoiceClient


def valid_wav(*, frames: int = 1_600, sample_rate: int = 16_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(b"\0\0" * frames)
    return buffer.getvalue()


def store_assistant_request(
    client: TestClient,
    *,
    status: str,
    result: dict[str, object] | None = None,
    conversation_id: UUID | None = None,
) -> str:
    request_id = uuid4()
    conversation_id = conversation_id or uuid4()
    now = client.app.state.clock.now()
    completed = status == "completed"
    with client.app.state.assistant_session_factory.begin() as session:
        session.add(
            AssistantConversation(
                id=conversation_id,
                created_at=now,
                last_valid_input_at=now,
                expired_at=None,
                control_state_version=None,
                control_state_json=None,
            )
        )
        session.add(
            AssistantRequest(
                id=request_id,
                conversation_id=conversation_id,
                fingerprint_version=1,
                fingerprint_hash="a" * 64,
                normalized_body_json={"requestId": str(request_id)},
                status=status,
                result_kind=str(result["kind"]) if result is not None else None,
                result_json=result,
                accepted_at=now,
                processing_at=None,
                completed_at=now if completed else None,
                task_reference_accepted_at=now,
                write_started=False,
                write_started_at=None,
                write_operation=None,
                write_commit_mode=None,
                pending_write_version=None,
                pending_write_json=None,
                create_idempotency_key=None,
                create_idempotency_scope=None,
                had_uncertain=False,
                uncertain_at=None,
                uncertain_acknowledged_at=None,
                uncertain_acknowledged_by_request_id=None,
                uncertain_resolved_at=None,
                retention_started_at=now if completed else None,
                tombstone=False,
                tombstoned_at=None,
                cancelled_at=None,
            )
        )
    return str(request_id)


def store_active_question(
    client: TestClient,
    *,
    kind: str,
) -> tuple[str, str]:
    conversation_id = uuid4()
    question_id = uuid4()
    target_a = uuid4()
    target_b = uuid4()
    now = client.app.state.clock.now()
    expires_at = now + timedelta(minutes=10)
    target = {
        "id": str(target_a),
        "title": "입고 대조",
        "scheduledAt": "2026-11-16T14:00:00+09:00",
        "category": "INBOUND",
        "completed": False,
        "updatedAt": "2026-11-15T12:00:00+09:00",
    }
    if kind == "confirmation":
        result: dict[str, object] = {
            "kind": kind,
            "text": "이 일정을 삭제할까요?",
            "questionId": str(question_id),
            "expiresAt": expires_at.isoformat(),
            "operation": "delete",
            "target": target,
        }
        candidates = None
        intents: object = {
            "version": 1,
            "intent": {
                "version": 1,
                "operation": "delete",
                "targetScheduleId": str(target_a),
                "expectedUpdatedAt": "2026-11-15T03:00:00Z",
                "timeInterpretation": "absolute",
            },
        }
    else:
        candidates = [
            {
                "candidateId": "candidate-a",
                "title": "입고 대조",
                "scheduledAt": "2026-11-16T14:00:00+09:00",
                "category": "INBOUND",
            },
            {
                "candidateId": "candidate-b",
                "title": "반품 대조",
                "scheduledAt": "2026-11-16T15:00:00+09:00",
                "category": "RETURN",
            },
        ]
        result = {
            "kind": kind,
            "text": "하나를 선택해 주세요.",
            "questionId": str(question_id),
            "expiresAt": expires_at.isoformat(),
            "candidates": candidates,
        }
        intents = {
            "version": 1,
            "candidates": {
                "candidate-a": {
                    "version": 1,
                    "operation": "delete",
                    "targetScheduleId": str(target_a),
                    "expectedUpdatedAt": "2026-11-15T03:00:00Z",
                    "timeInterpretation": "absolute",
                },
                "candidate-b": {
                    "version": 1,
                    "operation": "delete",
                    "targetScheduleId": str(target_b),
                    "expectedUpdatedAt": "2026-11-15T03:00:00Z",
                    "timeInterpretation": "absolute",
                },
            },
        }

    source_request_id = UUID(
        store_assistant_request(
            client,
            status="completed",
            result=result,
            conversation_id=conversation_id,
        )
    )
    with client.app.state.assistant_session_factory.begin() as session:
        session.add(
            AssistantQuestion(
                id=question_id,
                conversation_id=conversation_id,
                source_request_id=source_request_id,
                kind=kind,
                payload_json=result,
                candidates_json=candidates,
                candidate_intents_json=intents,
                status=AssistantQuestionStatus.ACTIVE.value,
                created_at=now,
                expires_at=expires_at,
                deactivated_at=None,
                answered_by_request_id=None,
            )
        )
    return str(conversation_id), str(question_id)


def test_voice_disabled_returns_503_problem(client: TestClient) -> None:
    client.app.state.settings.voice_enabled = False
    res = client.post(
        f"/api/v1/voice/requests?requestId={uuid4()}",
        content=b"RIFFfakewav",
        headers={"Content-Type": "audio/wav"},
    )
    assert res.status_code == 503
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "VOICE_UNAVAILABLE"


def test_voice_empty_audio_returns_422_problem(client: TestClient) -> None:
    client.app.state.settings.voice_enabled = True
    client.app.state.assistant_provider_available = True
    res = client.post(
        f"/api/v1/voice/requests?requestId={uuid4()}",
        content=b"",
        headers={"Content-Type": "audio/wav"},
    )
    assert res.status_code == 422
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "SPEECH_NOT_RECOGNIZED"


def test_voice_roundtrip_with_mocked_local_server(client: TestClient, monkeypatch) -> None:
    client.app.state.settings.voice_enabled = True
    client.app.state.assistant_provider_available = True
    monkeypatch.setattr(
        LocalVoiceClient,
        "transcribe",
        lambda self, audio_bytes, content_type="audio/wav": "내일 오후 3시에 물류 입고 검수 일정 등록해줘",
    )
    monkeypatch.setattr(
        LocalVoiceClient,
        "synthesize",
        lambda self, text: b"RIFF\x24\x00\x00\x00WAVEfmt ",
    )
    req_id = str(uuid4())
    res = client.post(
        f"/api/v1/voice/requests?requestId={req_id}",
        content=valid_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    assert res.status_code == 202
    assert "X-Transcript" in res.headers
    body = res.json()
    assert body["requestId"] == req_id
    assert body["status"] == "accepted"
    assert body["transcript"] == "내일 오후 3시에 물류 입고 검수 일정 등록해줘"
    assert body["input"] == {
        "type": "text",
        "text": "내일 오후 3시에 물류 입고 검수 일정 등록해줘",
    }


def test_same_request_id_replays_before_audio_and_stt(
    client: TestClient, monkeypatch
) -> None:
    client.app.state.settings.voice_enabled = True
    client.app.state.assistant_provider_available = True
    calls = 0

    def transcribe(self, audio_bytes, content_type="audio/wav"):
        nonlocal calls
        calls += 1
        return "내일 오후 3시에 재고 확인 일정 등록해줘"

    monkeypatch.setattr(LocalVoiceClient, "transcribe", transcribe)
    request_id = str(uuid4())
    first = client.post(
        f"/api/v1/voice/requests?requestId={request_id}",
        content=valid_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    replay = client.post(
        f"/api/v1/voice/requests?requestId={request_id}",
        content=b"not a wav",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert first.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["requestId"] == request_id
    assert calls == 1


def test_voice_rejects_unsupported_audio_before_stt(
    client: TestClient, monkeypatch
) -> None:
    client.app.state.settings.voice_enabled = True
    calls = 0

    def transcribe(self, audio_bytes, content_type="audio/wav"):
        nonlocal calls
        calls += 1
        return "사용되면 안 됨"

    monkeypatch.setattr(LocalVoiceClient, "transcribe", transcribe)
    request_id = str(uuid4())
    response = client.post(
        f"/api/v1/voice/requests?requestId={request_id}",
        content=b"RIFFfakeaudio",
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "AUDIO_UNSUPPORTED"
    assert response.json()["assistantRequestId"] == request_id
    assert calls == 0


def test_voice_rejects_question_without_conversation_before_stt(
    client: TestClient, monkeypatch
) -> None:
    client.app.state.settings.voice_enabled = True
    calls = 0

    def transcribe(self, audio_bytes, content_type="audio/wav"):
        nonlocal calls
        calls += 1
        return "네"

    monkeypatch.setattr(LocalVoiceClient, "transcribe", transcribe)
    request_id = str(uuid4())
    response = client.post(
        f"/api/v1/voice/requests?requestId={request_id}&questionId={uuid4()}",
        content=valid_wav(),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["assistantRequestId"] == request_id
    assert calls == 0


def test_speech_not_ready_uses_stable_problem_code(client: TestClient) -> None:
    client.app.state.settings.voice_enabled = True
    request_id = store_assistant_request(client, status="accepted")

    response = client.get(f"/api/v1/voice/requests/{request_id}/speech")

    assert response.status_code == 409
    assert response.json()["code"] == "SPEECH_NOT_READY"
    assert response.json()["assistantRequestId"] == request_id


def test_speech_uses_structured_result_without_mutating_request(
    client: TestClient, monkeypatch
) -> None:
    client.app.state.settings.voice_enabled = True
    result = {
        "kind": "outcome",
        "text": "요청한 일정이에요.",
        "outcome": "success",
        "schedulesChanged": False,
        "operation": "list",
        "items": [
            {
                "id": str(uuid4()),
                "title": "입고 수량 최종 대조",
                "scheduledAt": "2026-11-16T14:00:00+09:00",
                "category": "INBOUND",
                "completed": False,
                "updatedAt": "2026-11-15T12:00:00+09:00",
            }
        ],
    }
    request_id = store_assistant_request(client, status="completed", result=result)
    before = client.get(f"/api/v1/assistant/requests/{request_id}").json()
    spoken: list[str] = []

    def synthesize(_self, text: str) -> bytes:
        spoken.append(text)
        return valid_wav(sample_rate=22_050)

    monkeypatch.setattr(LocalVoiceClient, "synthesize", synthesize)
    response = client.get(f"/api/v1/voice/requests/{request_id}/speech")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/wav")
    assert response.content.startswith(b"RIFF")
    assert spoken == [
        "요청한 일정은 '입고 수량 최종 대조', 11월 16일 오후 2시, 입고 일정입니다."
    ]
    stored = client.get(f"/api/v1/assistant/requests/{request_id}")
    assert stored.status_code == 200
    assert stored.json() == before


def test_invalid_tts_output_returns_tts_unavailable(
    client: TestClient, monkeypatch
) -> None:
    client.app.state.settings.voice_enabled = True
    result = {
        "kind": "outcome",
        "text": "일정을 변경했어요.",
        "outcome": "success",
        "schedulesChanged": True,
        "operation": "update",
        "affectedScheduleIds": [str(uuid4())],
    }
    request_id = store_assistant_request(client, status="completed", result=result)
    monkeypatch.setattr(LocalVoiceClient, "synthesize", lambda _self, _text: b"not-wav")

    response = client.get(f"/api/v1/voice/requests/{request_id}/speech")

    assert response.status_code == 503
    assert response.json()["code"] == "TTS_UNAVAILABLE"
    assert response.json()["assistantRequestId"] == request_id


@pytest.mark.parametrize(
    ("kind", "transcript", "expected_input"),
    [
        ("confirmation", "네", {"type": "confirmation", "approved": True}),
        (
            "candidate_selection",
            "2번",
            {"type": "selection", "candidateId": "candidate-b"},
        ),
    ],
)
def test_voice_followup_reaches_admission_with_structured_input(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    transcript: str,
    expected_input: dict[str, object],
) -> None:
    client.app.state.settings.voice_enabled = True
    conversation_id, question_id = store_active_question(client, kind=kind)
    monkeypatch.setattr(
        LocalVoiceClient,
        "transcribe",
        lambda _self, _audio, content_type="audio/wav": transcript,
    )
    request_id = str(uuid4())

    response = client.post(
        "/api/v1/voice/requests",
        params={
            "requestId": request_id,
            "conversationId": conversation_id,
            "questionId": question_id,
        },
        content=valid_wav(),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 202
    assert response.json()["input"] == {
        **expected_input,
        "questionId": question_id,
    }
    with client.app.state.assistant_session_factory() as session:
        stored = session.get(AssistantRequest, UUID(request_id))
        assert stored is not None
        assert stored.normalized_body_json["input"] == response.json()["input"]


def test_voice_slot_is_busy_and_released_after_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.app.state.settings.voice_enabled = True
    client.app.state.assistant_provider_available = True
    assert voice_router._voice_slot_lock.acquire(blocking=False)
    try:
        busy = client.post(
            "/api/v1/voice/requests",
            params={"requestId": str(uuid4())},
            content=valid_wav(),
            headers={"Content-Type": "audio/wav"},
        )
    finally:
        voice_router._voice_slot_lock.release()

    assert busy.status_code == 409
    assert busy.json()["code"] == "VOICE_BUSY"

    invalid = client.post(
        "/api/v1/voice/requests",
        params={"requestId": str(uuid4())},
        content=b"not-wav",
        headers={"Content-Type": "audio/wav"},
    )
    assert invalid.status_code == 422

    monkeypatch.setattr(
        LocalVoiceClient,
        "transcribe",
        lambda _self, _audio, content_type="audio/wav": "내일 재고 조사 등록해줘",
    )
    accepted = client.post(
        "/api/v1/voice/requests",
        params={"requestId": str(uuid4())},
        content=valid_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    assert accepted.status_code == 202


def test_replay_created_after_slot_skips_stt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_id = uuid4()
    conversation_id = uuid4()
    existing = AssistantRequestStatusResponse.model_validate(
        {
            "requestId": str(request_id),
            "conversationId": str(conversation_id),
            "status": "accepted",
        }
    )
    statuses = iter((None, existing))
    monkeypatch.setattr(
        voice_router,
        "_replay_status",
        lambda _factory, _request_id: next(statuses),
    )
    calls = 0

    def transcribe(_self, _audio, content_type="audio/wav"):
        nonlocal calls
        calls += 1
        return "호출되면 안 됨"

    monkeypatch.setattr(LocalVoiceClient, "transcribe", transcribe)
    response = client.post(
        "/api/v1/voice/requests",
        params={"requestId": str(request_id)},
        content=valid_wav(),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 200
    assert response.json()["requestId"] == str(request_id)
    assert calls == 0


def test_tombstone_replay_returns_request_expired_before_stt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = {
        "kind": "outcome",
        "text": "조건에 맞는 일정을 찾지 못했어요.",
        "outcome": "no_match",
        "schedulesChanged": False,
        "operation": "list",
        "items": [],
    }
    request_id = store_assistant_request(client, status="completed", result=result)
    now = client.app.state.clock.now()
    with client.app.state.assistant_session_factory.begin() as session:
        stored = session.get(AssistantRequest, UUID(request_id))
        assert stored is not None
        stored.result_kind = None
        stored.result_json = None
        stored.tombstone = True
        stored.tombstoned_at = now
        stored.retention_started_at = now

    calls = 0

    def transcribe(_self, _audio, content_type="audio/wav"):
        nonlocal calls
        calls += 1
        return "호출되면 안 됨"

    monkeypatch.setattr(LocalVoiceClient, "transcribe", transcribe)
    response = client.post(
        "/api/v1/voice/requests",
        params={"requestId": request_id},
        content=b"not-wav",
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "REQUEST_EXPIRED"
    assert calls == 0


def test_voice_rejects_body_over_one_mib_before_stt(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.app.state.settings.voice_enabled = True
    calls = 0

    def transcribe(_self, _audio, content_type="audio/wav"):
        nonlocal calls
        calls += 1
        return "호출되면 안 됨"

    monkeypatch.setattr(LocalVoiceClient, "transcribe", transcribe)
    request_id = str(uuid4())
    response = client.post(
        "/api/v1/voice/requests",
        params={"requestId": request_id},
        content=b"x" * (MAX_VOICE_BYTES + 1),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == "REQUEST_BODY_TOO_LARGE"
    assert response.json()["assistantRequestId"] == request_id
    assert calls == 0


def test_voice_log_records_transcript_hash_without_raw_text(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client.app.state.settings.voice_enabled = True
    client.app.state.assistant_provider_available = True
    transcript = "내일 오후 2시 입고 대조 등록해줘"
    monkeypatch.setattr(
        LocalVoiceClient,
        "transcribe",
        lambda _self, _audio, content_type="audio/wav": transcript,
    )
    caplog.set_level(logging.INFO, logger="app.modules.voice.router")

    response = client.post(
        "/api/v1/voice/requests",
        params={"requestId": str(uuid4())},
        content=valid_wav(),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 202
    stage_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "voice.stage"
    ]
    assert len(stage_records) == 1
    record = stage_records[0]
    assert record.transcriptLength == len(transcript)
    assert record.transcriptSha256 == hashlib.sha256(transcript.encode()).hexdigest()
    assert transcript not in record.getMessage()
    assert transcript not in repr(record.__dict__)
