"""Public response models for voice requests."""

from __future__ import annotations

from app.modules.assistant.schemas import (
    AssistantInput,
    AssistantRequestAcceptedResponse,
    AssistantText,
)


class VoiceRequestAcceptedResponse(AssistantRequestAcceptedResponse):
    transcript: AssistantText
    input: AssistantInput
