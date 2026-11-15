from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from app.modules.voice.client import LocalVoiceClient
from app.modules.voice.errors import (
    SpeechAudioTooLarge,
    SpeechNotRecognized,
    TtsUnavailable,
    VoiceUnavailable,
)


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
