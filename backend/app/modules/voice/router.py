from __future__ import annotations

import logging
import re
import threading
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Path, Query, Request, Response, status
from starlette.responses import JSONResponse

from app.modules.assistant.errors import AssistantUnavailable
from app.modules.assistant.evidence.gate import classify_answer
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
    AssistantInputConfirmation,
    AssistantInputSelection,
    AssistantInputText,
    AssistantRequestAcceptedResponse,
    AssistantRequestCreate,
    AssistantRequestStatusResponse,
    AssistantUuid,
)
from app.modules.assistant.state_service import (
    admit_assistant_request,
    get_assistant_request as get_request_status,
)
from app.modules.assistant import repository
from app.modules.voice.client import LocalVoiceClient
from app.modules.voice.errors import (
    SpeechAudioTooLarge,
    SpeechNotRecognized,
    VoiceAnswerUnclear,
    VoiceBusy,
    VoiceSpeechNotReady,
    VoiceUnavailable,
)

router = APIRouter(prefix="/api/v1/voice", tags=["voice"])

MAX_VOICE_BYTES = 10 * 1024 * 1024  # 10 MB
_voice_slot_lock = threading.Lock()


def _voice_client(request: Request) -> LocalVoiceClient:
    settings = request.app.state.settings
    if not settings.voice_enabled:
        raise VoiceUnavailable("Voice endpoints are disabled in configuration.")
    return LocalVoiceClient(
        base_url=settings.voice_server_base_url,
        timeout_seconds=settings.voice_server_timeout_seconds,
    )


def _parse_candidate_selection(transcript: str, candidates: list) -> str | None:
    """규칙 기반으로 '첫 번째', '2번' 등의 음성 표현을 현재 후보의 candidateId로 결정적 매핑"""
    text = transcript.replace(" ", "")
    ordinal_map = {
        "첫": 1, "첫번": 1, "첫번째": 1, "1": 1, "일": 1,
        "두": 2, "두번": 2, "두번째": 2, "2": 2, "이번": 2,
        "세": 3, "세번": 3, "세번째": 3, "3": 3, "삼": 3,
        "네": 4, "네번": 4, "네번째": 4, "4": 4, "사": 4,
        "다섯": 5, "다섯번": 5, "다섯번째": 5, "5": 5, "오": 5,
        "여섯": 6, "여섯번": 6, "여섯번째": 6, "6": 6, "육": 6,
        "일곱": 7, "일곱번": 7, "일곱번째": 7, "7": 7, "칠": 7,
        "여덟": 8, "여덟번": 8, "여덟번째": 8, "8": 8, "팔": 8,
        "아홉": 9, "아홉번": 9, "아홉번째": 9, "9": 9, "구": 9,
        "열": 10, "열번": 10, "열번째": 10, "10": 10, "십": 10
    }
    
    found_nums = []
    match = re.search(r'([1-9]|10)번', transcript)
    if match:
        found_nums.append(int(match.group(1)))
    else:
        for key, val in ordinal_map.items():
            if f"{key}번째" in text or f"{key}번" in text:
                found_nums.append(val)
                break

    if len(found_nums) != 1:
        return None
    
    idx = found_nums[0] - 1
    if candidates and 0 <= idx < len(candidates):
        cand = candidates[idx]
        return getattr(cand, "candidate_id", None) or getattr(cand, "candidateId", None) or (cand.get("candidateId") if isinstance(cand, dict) else None)
    return None


def _generate_tts_sentence(st) -> str:
    """6.3절 기준 서버 측 표준 TTS 안내 문장 템플릿 생성기"""
    res = getattr(st, "result", None)
    if not res:
        return "처리를 완료했습니다."

    # 1. 정보 보충 또는 일반 텍스트 결과
    if hasattr(res, "text") and res.text and not getattr(res, "items", None) and not getattr(res, "candidates", None):
        return res.text

    # 2. 일정 조회 결과 (1건, 2~3건, 4건 이상)
    items = getattr(res, "items", None) or getattr(res, "schedules", None)
    if items is not None:
        count = len(items)
        if count == 0:
            return "조건에 맞는 일정을 찾지 못했어요."
        elif count == 1:
            item = items[0]
            title = getattr(item, "title", "일정")
            dt = getattr(item, "dateTime", None) or getattr(item, "date_time", "")
            return f"요청한 일정은 '{title}'이며, {dt}입니다."
        elif count <= 3:
            ordinals = ["첫 번째", "두 번째", "세 번째"]
            parts = [f"요청한 일정은 총 {count}건입니다."]
            for i, item in enumerate(items):
                title = getattr(item, "title", "일정")
                dt = getattr(item, "dateTime", None) or getattr(item, "date_time", "")
                parts.append(f"{ordinals[i]}는 '{title}', {dt}입니다.")
            return " ".join(parts)
        else:
            item = items[0]
            title = getattr(item, "title", "일정")
            dt = getattr(item, "dateTime", None) or getattr(item, "date_time", "")
            return f"요청한 일정은 총 {count}건입니다. 먼저 세 건을 안내할게요. 첫 번째는 '{title}', {dt}입니다. 나머지는 화면에서 확인하거나 조회 조건을 좁혀 주세요."

    # 3. 후보 목록 (candidate_selection)
    candidates = getattr(res, "candidates", None)
    if candidates is not None:
        count = len(candidates)
        ordinals = ["첫 번째", "두 번째", "세 번째", "네 번째", "다섯 번째", "여섯 번째", "일곱 번째", "여덟 번째", "아홉 번째", "열 번째"]
        parts = [f"일정 후보는 총 {count}건입니다."]
        for i, c in enumerate(candidates[:10]):
            title = getattr(c, "title", "일정")
            dt = getattr(c, "dateTime", None) or getattr(c, "date_time", "")
            cat = getattr(c, "category", "")
            parts.append(f"{ordinals[i]}는 '{title}', {dt}, {cat}입니다.")
        parts.append("몇 번째 일정을 선택하시겠어요?")
        return " ".join(parts)

    return getattr(res, "text", None) or "처리를 완료했습니다."


@router.post(
    "/requests",
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="createVoiceRequest",
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
    client = _voice_client(request)
    session_factory = _assistant_session_factory(request)
    if session_factory is None:
        raise AssistantUnavailable(assistant_request_id=request_id)

    if not _voice_slot_lock.acquire(blocking=False):
        raise VoiceBusy()

    try:
        audio_bytes = await request.body()
        if not audio_bytes:
            raise SpeechNotRecognized("Empty audio body received.")
        if len(audio_bytes) > MAX_VOICE_BYTES:
            raise SpeechAudioTooLarge()

        content_type = request.headers.get("content-type", "audio/wav")
        transcript = client.transcribe(audio_bytes, content_type=content_type)

        input_payload: dict[str, object] = {}
        if conversation_id is not None:
            with session_factory() as session:
                active_q = repository.find_active_question(session, conversation_id)
                if active_q is not None:
                    q_type = getattr(active_q, "question_type", None) or getattr(active_q, "type", None)
                    
                    # 1. 후보 선택 질문 처리 (6절 및 7절 반영)
                    if q_type == "selection" or "candidate" in str(q_type).lower():
                        candidates = getattr(active_q, "candidates", None) or []
                        cand_id = _parse_candidate_selection(transcript, candidates)
                        if not cand_id:
                            raise VoiceAnswerUnclear("Ambiguous or out-of-range candidate number.")
                        input_payload = {
                            "type": "selection",
                            "candidateId": cand_id,
                        }
                        if question_id is not None:
                            input_payload["questionId"] = str(question_id)

                    # 2. 승인/거절 질문 처리
                    elif q_type == "confirmation":
                        answer_enum = classify_answer(transcript)
                        approved = (answer_enum.value == "approved") if hasattr(answer_enum, "value") else bool(answer_enum)
                        input_payload = {
                            "type": "confirmation",
                            "approved": approved,
                        }
                        if question_id is not None:
                            input_payload["questionId"] = str(question_id)

        # 3. 일반 텍스트 (정보 보충 등) 처리
        if not input_payload:
            input_payload = {"type": "text", "text": transcript}
            if question_id is not None:
                input_payload["questionId"] = str(question_id)

        create_payload: dict[str, object] = {
            "requestId": request_id,
            "input": input_payload,
        }
        if conversation_id is not None:
            create_payload["conversationId"] = conversation_id

        data = AssistantRequestCreate.model_validate(create_payload)

        runtime = _runtime(request)
        if runtime is not None:
            runtime.try_lazy_timeout()
        accepted_at = request.app.state.clock.now()
        response = admit_assistant_request(
            session_factory,
            data=data,
            accepted_at=accepted_at,
            new_conversation_id=request.app.state.assistant_uuid_factory(),
            runtime_ready=_runtime_ready(request),
            provider_available=_provider_available(request, data),
        )
        if isinstance(response, AssistantRequestStatusResponse):
            return JSONResponse(
                response.model_dump(mode="json", by_alias=True, exclude_none=True),
                status_code=status.HTTP_200_OK,
            )

        if runtime is None:
            _compensate_registration_failure(request, request_id=data.request_id)
            return JSONResponse(
                response.model_dump(mode="json", by_alias=True, exclude_none=True),
                status_code=status.HTTP_202_ACCEPTED,
                headers={"X-Transcript": quote(transcript)},
            )

        try:
            registration = runtime.register_accepted(
                request_id=data.request_id,
                accepted_at=accepted_at,
            )
        except Exception as exc:
            logging.getLogger(__name__).error(
                "Voice assistant deadline registration failed: %s",
                type(exc).__name__,
                extra={"event": "voice.deadline_registration_failed"},
            )
            _compensate_registration_failure(request, request_id=data.request_id)
        else:
            if registration is RegistrationOutcome.UNAVAILABLE:
                _compensate_registration_failure(request, request_id=data.request_id)

        resp_dict = response.model_dump(mode="json", by_alias=True, exclude_none=True)
        resp_dict["transcript"] = transcript
        resp_dict["input"] = input_payload

        return JSONResponse(
            resp_dict,
            status_code=status.HTTP_202_ACCEPTED,
            headers={"X-Transcript": quote(transcript)},
        )
    finally:
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
    client = _voice_client(request)
    if not request.app.state.assistant_schema_ready:
        raise AssistantUnavailable(assistant_request_id=request_id)
    runtime = _runtime(request)
    if runtime is not None:
        runtime.try_lazy_timeout(request_id)
    st = get_request_status(
        request.app.state.session_factory,
        request_id=request_id,
    )
    if st.status != "completed" or st.result is None:
        raise VoiceSpeechNotReady(request_id)
    
    # 6.3절 기준 표준 템플릿 TTS 문장 생성 적용
    speech_text = _generate_tts_sentence(st)
    wav_bytes = client.synthesize(speech_text)
    return Response(content=wav_bytes, media_type="audio/wav")
