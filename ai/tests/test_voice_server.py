"""Voice engine checks; run in the voice server environment."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import shutil
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_REQUIRED = ("fastapi", "faster_whisper", "piper")
_AVAILABLE = all(importlib.util.find_spec(name) is not None for name in _REQUIRED)


@unittest.skipUnless(_AVAILABLE, "requires the STT/TTS service environment")
class VoiceServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).resolve().parents[1] / "voice_server" / "server.py"
        spec = importlib.util.spec_from_file_location("voice_server_under_test", source)
        cls.server = importlib.util.module_from_spec(spec)
        with patch("glob.glob", return_value=["unused.onnx"]):
            spec.loader.exec_module(cls.server)

    @staticmethod
    def audio():
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\x00\x00" * 8000)
        return output.getvalue()

    def transcribe(self, segments):
        from fastapi import UploadFile

        calls = []
        def recognize(path, **kwargs):
            calls.append(kwargs)
            return [SimpleNamespace(text=text) for text in segments], None
        def normalize(args, **kwargs):
            shutil.copyfile(args[args.index("-i") + 1], args[-1])
            return SimpleNamespace(returncode=0)
        model = SimpleNamespace(transcribe=recognize)
        upload = UploadFile(filename="test.wav", file=io.BytesIO(self.audio()))
        with patch.object(self.server, "stt_model", model), patch.object(self.server.subprocess, "run", side_effect=normalize):
            result = asyncio.run(self.server.transcribe(file=upload))
        return result, calls

    def test_decoder_is_deterministic_and_uses_configured_beam(self):
        result, calls = self.transcribe([" 모레 입고 수량 대조 등록해줘 "])
        self.assertEqual(result["text"], "모레 입고 수량 대조 등록해줘")
        self.assertEqual(calls[0]["beam_size"], self.server.STT_BEAM_SIZE)
        self.assertEqual(calls[0]["temperature"], 0.0)
        self.assertEqual(calls[0]["hotwords"], self.server.LOGISTICS_HOTWORDS)
        self.assertIs(calls[0]["condition_on_previous_text"], False)
        self.assertIs(calls[0]["vad_filter"], True)

    def test_no_recognized_speech_returns_422_without_fallback(self):
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as caught:
            self.transcribe([])
        self.assertEqual(caught.exception.status_code, 422)

    def test_health_reports_decoder_configuration(self):
        self.assertEqual(self.server.health()["stt_beam_size"], self.server.STT_BEAM_SIZE)

    def test_clock_separator_is_normalized_in_recognized_text(self):
        for original, expected in [
            ("오후, 2시로 잡아줘.", "오후 2시로 잡아줘."),
            ("오전，열두 시에 조회해 줘.", "오전 열두 시에 조회해 줘."),
        ]:
            with self.subTest(original=original):
                result, _ = self.transcribe([original])
                self.assertEqual(result["text"], expected)

    def test_clock_cleanup_preserves_other_punctuation_and_quoted_titles(self):
        for original in [
            "2번, 거.", "첫 번째, 두 번째.", "오후, 일정 알려 줘.",
            "3.5톤 차량 점검", "'오후, 2시' 제목을 바꿔 줘.",
        ]:
            with self.subTest(original=original):
                self.assertEqual(self.server.normalize_clock_separator(original), original)


if __name__ == "__main__":
    unittest.main()
