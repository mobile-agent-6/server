from __future__ import annotations

import io
import wave
from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.modules.assistant.model import AssistantConversation, AssistantRequest
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
) -> str:
    request_id = uuid4()
    conversation_id = uuid4()
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
