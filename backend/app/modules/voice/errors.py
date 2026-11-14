from __future__ import annotations

from app.modules.assistant.errors import AssistantError

class VoiceUnavailable(AssistantError):
    status = 503
    code = "VOICE_UNAVAILABLE"
    detail = "Voice service is currently unavailable."

class VoiceBusy(AssistantError):
    status = 409
    code = "VOICE_BUSY"
    detail = "Another voice request is currently being processed."

class SpeechNotRecognized(AssistantError):
    status = 422
    code = "SPEECH_NOT_RECOGNIZED"
    detail = "Speech was not recognized or audio was empty."

class SpeechAudioTooLarge(AssistantError):
    status = 413
    code = "REQUEST_BODY_TOO_LARGE"
    detail = "Audio file size exceeds the maximum limit."

class VoiceSpeechNotReady(AssistantError):
    status = 409
    code = "VOICE_SPEECH_NOT_READY"
    detail = "TTS result for the assistant request is not ready yet."

    def __init__(self, *args, **kwargs) -> None:
        super().__init__()

class VoiceAnswerUnclear(AssistantError):
    status = 422
    code = "VOICE_ANSWER_UNCLEAR"
    detail = "Candidate selection number or confirmation answer is unclear."
