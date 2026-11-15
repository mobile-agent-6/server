from __future__ import annotations

import json
import urllib.error
import urllib.request
from uuid import uuid4

from app.modules.voice.errors import (
    SpeechAudioTooLarge,
    SpeechNotRecognized,
    TtsUnavailable,
    VoiceUnavailable,
)


class LocalVoiceClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def transcribe(self, audio_bytes: bytes, content_type: str = "audio/wav") -> str:
        boundary = f"----VoiceBoundary{uuid4().hex}"
        ext = "m4a" if "mp4" in content_type or "m4a" in content_type else "wav"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="audio.{ext}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + audio_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/stt",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                text = str(payload.get("text", "")).strip()
                if not text:
                    raise SpeechNotRecognized()
                return text
        except urllib.error.HTTPError as exc:
            if exc.code == 413:
                raise SpeechAudioTooLarge() from exc
            if exc.code == 422:
                raise SpeechNotRecognized() from exc
            raise VoiceUnavailable() from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VoiceUnavailable() from exc

    def synthesize(self, text: str) -> bytes:
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/tts",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise TtsUnavailable() from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TtsUnavailable() from exc
