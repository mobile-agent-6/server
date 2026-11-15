from __future__ import annotations

import urllib.error
import urllib.request
from http.client import IncompleteRead

import pytest

from app.modules.voice.client import LocalVoiceClient
from app.modules.voice.errors import (
    SpeechAudioTooLarge,
    SpeechNotRecognized,
    TtsUnavailable,
    VoiceUnavailable,
)


class FakeResponse:
    def __init__(self, body: bytes = b"", *, read_error: Exception | None = None) -> None:
        self.body = body
        self.read_error = read_error

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        if self.read_error is not None:
            raise self.read_error
        return self.body


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (413, SpeechAudioTooLarge),
        (422, SpeechNotRecognized),
        (500, VoiceUnavailable),
    ],
)
def test_stt_http_errors_map_to_public_voice_errors(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: type[Exception],
) -> None:
    def fail(*_args, **_kwargs):
        raise urllib.error.HTTPError("http://voice/stt", status, "failed", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fail)

    with pytest.raises(expected):
        LocalVoiceClient("http://voice").transcribe(b"wav")


def test_stt_connection_failure_maps_to_voice_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(urllib.request, "urlopen", fail)

    with pytest.raises(VoiceUnavailable):
        LocalVoiceClient("http://voice").transcribe(b"wav")


def test_tts_failure_maps_to_tts_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(urllib.request, "urlopen", fail)

    with pytest.raises(TtsUnavailable):
        LocalVoiceClient("http://voice").synthesize("일정을 변경했어요.")


@pytest.mark.parametrize("body", [b"not-json", b"[]", b'{"text": null}'])
def test_malformed_stt_success_response_maps_to_voice_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(body),
    )

    with pytest.raises(VoiceUnavailable):
        LocalVoiceClient("http://voice").transcribe(b"wav")


def test_incomplete_tts_response_maps_to_tts_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: FakeResponse(read_error=IncompleteRead(b"RIFF")),
    )

    with pytest.raises(TtsUnavailable):
        LocalVoiceClient("http://voice").synthesize("일정을 변경했어요.")
