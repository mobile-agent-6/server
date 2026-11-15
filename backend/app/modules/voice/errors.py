from __future__ import annotations

from app.modules.assistant.errors import AssistantError


class VoiceError(AssistantError):
    """Base class for stable public voice pipeline errors."""


class VoiceUnavailable(VoiceError):
    status = 503
    code = "VOICE_UNAVAILABLE"
    detail = "Voice service is currently unavailable."


class VoiceBusy(VoiceError):
    status = 409
    code = "VOICE_BUSY"
    detail = "Another voice request is currently being processed."


class SpeechNotRecognized(VoiceError):
    status = 422
    code = "SPEECH_NOT_RECOGNIZED"
    detail = "Speech was not recognized or audio was empty."


class AudioUnsupported(VoiceError):
    status = 422
    code = "AUDIO_UNSUPPORTED"
    detail = "The audio must be a supported PCM WAV file."


class SpeechAudioTooLarge(VoiceError):
    status = 413
    code = "REQUEST_BODY_TOO_LARGE"
    detail = "The audio exceeds the maximum size or duration."


class SpeechNotReady(VoiceError):
    status = 409
    code = "SPEECH_NOT_READY"
    detail = "TTS result for the assistant request is not ready yet."


class TtsUnavailable(VoiceError):
    status = 503
    code = "TTS_UNAVAILABLE"
    detail = "Text-to-speech is currently unavailable."


class VoiceAnswerUnclear(VoiceError):
    status = 422
    code = "VOICE_ANSWER_UNCLEAR"
    detail = "Candidate selection number or confirmation answer is unclear."
