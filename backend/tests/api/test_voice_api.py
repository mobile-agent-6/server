from __future__ import annotations

from uuid import uuid4
from fastapi.testclient import TestClient

from app.modules.voice.client import LocalVoiceClient


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
        content=b"RIFFfakeaudio",
        headers={"Content-Type": "audio/wav"},
    )
    assert res.status_code == 202
    assert "X-Transcript" in res.headers
    body = res.json()
    assert body["requestId"] == req_id
    assert body["status"] == "accepted"
