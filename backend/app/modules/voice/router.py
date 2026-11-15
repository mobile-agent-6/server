from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Path, Query, Request, Response, status
from pydantic import TypeAdapter, ValidationError
from starlette.responses import JSONResponse

from app.core.exceptions import ApplicationError, ValidationFailure
from app.modules.assistant import repository
from app.modules.assistant.enums import AssistantRequestStatus
from app.modules.assistant.errors import (
    AssistantRequestExpiredForGet,
    AssistantRequestExpiredForPost,
    AssistantRequestNotFound,
    AssistantUnavailable,
)
from app.modules.assistant.evidence.gate import Answer, classify_answer
from app.modules.assistant.router import (
    REQUEST_ID_HEADER,
    _assistant_session_factory,
    _compensate_registration_failure,
    _problem_responses,
    _provider_available,
    _runtime,
    _runtime_ready,
)
from app.modules.assistant.runtime import RegistrationOutcome
from app.modules.assistant.schemas import (
    AssistantRequestCreate,
    AssistantRequestStatusResponse,
    AssistantText,
    AssistantUuid,
)
from app.modules.assistant.state_service import (
    _raise_storage_error,
    admit_assistant_request,
    get_assistant_request as get_request_status,
)
from app.modules.voice.audio import (
    MAX_VOICE_BYTES,
    validate_stt_wav,
    validate_tts_wav,
)
from app.modules.voice.client import LocalVoiceClient
from app.modules.voice.errors import (
    SpeechAudioTooLarge,
    SpeechNotReady,
    SpeechNotRecognized,
    TtsUnavailable,
    VoiceAnswerUnclear,
    VoiceBusy,
    VoiceError,
    VoiceUnavailable,
)
from app.modules.voice.schemas import VoiceRequestAcceptedResponse
from app.modules.voice.tts import generate_tts_sentence

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

_voice_slot_lock = threading.Lock()
_assistant_text = TypeAdapter(AssistantText)

_ORDINAL_WORDS = {
    "첫": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
}


def _voice_client(
    request: Request,
    *,
    request_id: AssistantUuid,
    unavailable_error: type[VoiceError] = VoiceUnavailable,
) -> LocalVoiceClient:
    settings = request.app.state.settings
    if not settings.voice_enabled:
        raise unavailable_error(assistant_request_id=request_id)
    return LocalVoiceClient(
        base_url=settings.voice_server_base_url,
        timeout_seconds=settings.voice_server_timeout_seconds,
    )


def _bind_request_id(exc: ApplicationError, request_id: AssistantUuid) -> None:
    if getattr(exc, "assistant_request_id", None) is None:
        exc.assistant_request_id = request_id  # type: ignore[attr-defined]


def _log_stage(
    *,
    request_id: AssistantUuid,
    stage: str,
    started_at: float,
    status_code: int,
    code: str,
    transcript: str | None = None,
) -> None:
    extra: dict[str, object] = {
        "event": "voice.stage",
        "assistantRequestId": str(request_id),
        "stage": stage,
        "durationMs": round((time.monotonic() - started_at) * 1000, 3),
        "status": status_code,
        "errorCode": code,
    }
    if transcript is not None:
        extra["transcriptLength"] = len(transcript)
        extra["transcriptSha256"] = hashlib.sha256(transcript.encode("utf-8")).hexdigest()
    logger.info("voice pipeline stage completed", extra=extra)


def _parse_candidate_selection(transcript: str, candidates: list[object]) -> str | None:
    """Map one unambiguous spoken ordinal to a persisted candidate ID."""

    found: set[int] = {
        int(match.group(1))
        for match in re.finditer(r"(?<!\d)(10|[1-9])\s*번(?!\d)", transcript)
    }
    for word, number in _ORDINAL_WORDS.items():
        pattern = rf"(?<![가-힣]){word}\s*번째"
        if re.search(pattern, transcript):
            found.add(number)

    if len(found) != 1:
        return None
    number = next(iter(found))
    if not 1 <= number <= len(candidates):
        return None
    candidate = candidates[number - 1]
    if not isinstance(candidate, dict):
        return None
    candidate_id = candidate.get("candidateId")
    return candidate_id if isinstance(candidate_id, str) and candidate_id else None


def _query_validation_error(request_id: AssistantUuid) -> ValidationFailure:
    exc = ValidationFailure(
        [
            {
                "location": "query",
                "parameter": "conversationId",
                "code": "REQUIRED",
                "detail": "conversationId is required when questionId is provided.",
            }
        ]
    )
    exc.assistant_request_id = request_id  # type: ignore[attr-defined]
    return exc


def _replay_status(session_factory, request_id: AssistantUuid) -> AssistantRequestStatusResponse | None:
    try:
        return get_request_status(session_factory, request_id=request_id)
    except AssistantRequestNotFound:
        return None
    except AssistantRequestExpiredForGet as exc:
        raise AssistantRequestExpiredForPost(assistant_request_id=request_id) from exc


def _status_response(response: AssistantRequestStatusResponse) -> JSONResponse:
    return JSONResponse(
        response.model_dump(mode="json", by_alias=True, exclude_none=True),
        status_code=status.HTTP_200_OK,
    )


async def _read_audio_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_VOICE_BYTES:
            raise SpeechAudioTooLarge()
        chunks.append(chunk)
    audio_bytes = b"".join(chunks)
    if not audio_bytes:
        raise SpeechNotRecognized()
    return audio_bytes


def _active_question(session_factory, conversation_id, request_id):
    try:
        with session_factory() as session:
            return repository.find_active_question(session, conversation_id)
    except Exception as exc:
        _raise_storage_error(exc, assistant_request_id=request_id)
        raise AssertionError("unreachable")


def _input_payload(
    *,
    transcript: str,
    active_question: object | None,
    question_id: AssistantUuid | None,
    now,
) -> dict[str, object]:
    if (
        active_question is not None
        and question_id == getattr(active_question, "id")
        and (
            getattr(active_question, "expires_at") is None
            or getattr(active_question, "expires_at") > now
        )
    ):
        kind = getattr(active_question, "kind")
        if kind == "candidate_selection":
            candidates = getattr(active_question, "candidates_json") or []
            candidate_id = _parse_candidate_selection(transcript, candidates)
            if candidate_id is None:
                raise VoiceAnswerUnclear()
            return {
                "type": "selection",
                "questionId": str(question_id),
                "candidateId": candidate_id,
            }
        if kind == "confirmation":
            answer = classify_answer(transcript)
            if answer is Answer.UNKNOWN:
                raise VoiceAnswerUnclear()
            return {
                "type": "confirmation",
                "questionId": str(question_id),
                "approved": answer is Answer.YES,
            }

    payload: dict[str, object] = {"type": "text", "text": transcript}
    if question_id is not None:
        payload["questionId"] = str(question_id)
    return payload


@router.post(
    "/requests",
    response_model=VoiceRequestAcceptedResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="createVoiceRequest",
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "audio/wav": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
    responses={
        202: {
            "description": "Voice request accepted with transcript",
            "headers": {
                **REQUEST_ID_HEADER,
                "X-Transcript": {
                    "description": "URL-encoded recognized Korean transcript",
                    "schema": {"type": "string"},
                },
            },
        },
        200: {
            "model": AssistantRequestStatusResponse,
            "description": "Existing request status (Idempotency)",
            "headers": REQUEST_ID_HEADER,
        },
        **_problem_responses(400, 404, 409, 413, 422, 500, 503),
    },
)
async def create_voice_request(
    request: Request,
    request_id: Annotated[AssistantUuid, Query(alias="requestId")],
    conversation_id: Annotated[AssistantUuid | None, Query(alias="conversationId")] = None,
    question_id: Annotated[AssistantUuid | None, Query(alias="questionId")] = None,
) -> JSONResponse:
    started_at = time.monotonic()
    transcript: str | None = None
    slot_acquired = False
    try:
        session_factory = _assistant_session_factory(request)
        if session_factory is None:
            raise AssistantUnavailable(assistant_request_id=request_id)

        runtime = _runtime(request)
        if runtime is not None:
            runtime.try_lazy_timeout(request_id)
        replay = _replay_status(session_factory, request_id)
        if replay is not None:
            _log_stage(
                request_id=request_id,
                stage="replay",
                started_at=started_at,
                status_code=200,
                code="OK",
            )
            return _status_response(replay)

        if not _voice_slot_lock.acquire(blocking=False):
            raise VoiceBusy(assistant_request_id=request_id)
        slot_acquired = True

        replay = _replay_status(session_factory, request_id)
        if replay is not None:
            _log_stage(
                request_id=request_id,
                stage="replay_after_slot",
                started_at=started_at,
                status_code=200,
                code="OK",
            )
            return _status_response(replay)

        if question_id is not None and conversation_id is None:
            raise _query_validation_error(request_id)

        client = _voice_client(request, request_id=request_id)
        audio_bytes = await _read_audio_body(request)
        content_type = request.headers.get("content-type")
        validate_stt_wav(audio_bytes, content_type=content_type)
        transcript = client.transcribe(audio_bytes, content_type="audio/wav")
        try:
            transcript = _assistant_text.validate_python(transcript)
        except ValidationError as exc:
            raise SpeechNotRecognized(assistant_request_id=request_id) from exc

        active_question = (
            _active_question(session_factory, conversation_id, request_id)
            if conversation_id is not None
            else None
        )
        input_payload = _input_payload(
            transcript=transcript,
            active_question=active_question,
            question_id=question_id,
            now=request.app.state.clock.now(),
        )
        create_payload: dict[str, object] = {
            "requestId": request_id,
            "input": input_payload,
        }
        if conversation_id is not None:
            create_payload["conversationId"] = conversation_id
        try:
            data = AssistantRequestCreate.model_validate(create_payload)
        except ValidationError as exc:
            raise SpeechNotRecognized(assistant_request_id=request_id) from exc

        accepted_at = request.app.state.clock.now()
        if runtime is not None:
            runtime.try_lazy_timeout()
        response = admit_assistant_request(
            session_factory,
            data=data,
            accepted_at=accepted_at,
            new_conversation_id=request.app.state.assistant_uuid_factory(),
            runtime_ready=_runtime_ready(request),
            provider_available=_provider_available(request, data),
        )
        if isinstance(response, AssistantRequestStatusResponse):
            return _status_response(response)

        if runtime is None:
            _compensate_registration_failure(request, request_id=data.request_id)
        else:
            try:
                registration = runtime.register_accepted(
                    request_id=data.request_id,
                    accepted_at=accepted_at,
                )
            except Exception as exc:
                logger.error(
                    "voice assistant deadline registration failed",
                    extra={
                        "event": "voice.deadline_registration_failed",
                        "assistantRequestId": str(request_id),
                        "exceptionClass": type(exc).__name__,
                    },
                )
                _compensate_registration_failure(request, request_id=data.request_id)
            else:
                if registration is RegistrationOutcome.UNAVAILABLE:
                    _compensate_registration_failure(request, request_id=data.request_id)

        accepted = VoiceRequestAcceptedResponse(
            request_id=response.request_id,
            conversation_id=response.conversation_id,
            status=response.status,
            transcript=transcript,
            input=data.input,
        )
        _log_stage(
            request_id=request_id,
            stage="accepted",
            started_at=started_at,
            status_code=202,
            code="OK",
            transcript=transcript,
        )
        return JSONResponse(
            accepted.model_dump(mode="json", by_alias=True, exclude_none=True),
            status_code=status.HTTP_202_ACCEPTED,
            headers={"X-Transcript": quote(transcript)},
        )
    except ApplicationError as exc:
        if isinstance(exc, VoiceError):
            _bind_request_id(exc, request_id)
        _log_stage(
            request_id=request_id,
            stage="failed",
            started_at=started_at,
            status_code=exc.status,
            code=exc.code,
            transcript=transcript,
        )
        raise
    finally:
        if slot_acquired:
            _voice_slot_lock.release()


@router.get(
    "/requests/{requestId}/speech",
    operation_id="getVoiceRequestSpeech",
    responses={
        200: {
            "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}},
            "description": "Synthesized WAV speech for completed assistant result",
            "headers": REQUEST_ID_HEADER,
        },
        **_problem_responses(404, 409, 410, 422, 500, 503),
    },
)
def get_voice_request_speech(
    request: Request,
    request_id: Annotated[AssistantUuid, Path(alias="requestId")],
) -> Response:
    started_at = time.monotonic()
    slot_acquired = False
    try:
        session_factory = _assistant_session_factory(request)
        if session_factory is None:
            raise AssistantUnavailable(assistant_request_id=request_id)
        runtime = _runtime(request)
        if runtime is not None:
            runtime.try_lazy_timeout(request_id)
        request_status = get_request_status(session_factory, request_id=request_id)
        if (
            request_status.status is not AssistantRequestStatus.COMPLETED
            or request_status.result is None
        ):
            raise SpeechNotReady(assistant_request_id=request_id)

        if not _voice_slot_lock.acquire(blocking=False):
            raise VoiceBusy(assistant_request_id=request_id)
        slot_acquired = True
        client = _voice_client(
            request,
            request_id=request_id,
            unavailable_error=TtsUnavailable,
        )
        speech_text = generate_tts_sentence(request_status)
        wav_bytes = client.synthesize(speech_text)
        validate_tts_wav(wav_bytes)
        _log_stage(
            request_id=request_id,
            stage="tts_completed",
            started_at=started_at,
            status_code=200,
            code="OK",
        )
        return Response(content=wav_bytes, media_type="audio/wav")
    except ApplicationError as exc:
        if isinstance(exc, VoiceError):
            _bind_request_id(exc, request_id)
        _log_stage(
            request_id=request_id,
            stage="tts_failed",
            started_at=started_at,
            status_code=exc.status,
            code=exc.code,
        )
        raise
    finally:
        if slot_acquired:
            _voice_slot_lock.release()
