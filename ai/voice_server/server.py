import glob
import io
import os
import re
import subprocess
import tempfile
import threading
import wave

from faster_whisper import WhisperModel
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from piper.voice import PiperVoice
from pydantic import BaseModel

app = FastAPI(title="OrangePi Local STT/TTS Loopback Server")

STT_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "base")
STT_THREADS = int(os.getenv("STT_THREADS", "8"))
STT_BEAM_SIZE = int(os.getenv("STT_BEAM_SIZE", "3"))
TTS_ONNX_PATH = glob.glob(
    os.path.join(os.path.dirname(__file__), "models/piper-ko-std/*.onnx")
)[0]

stt_model: WhisperModel | None = None
tts_voice: PiperVoice | None = None
infer_lock = threading.Lock()

LOGISTICS_PROMPT = (
    "물류 입고 검수, 출고 차량 점검, 냉장창고 온도 점검, 재고 실사, "
    "안전 교육, 일정 등록, 조회, 변경, 수정, 삭제, 취소, 승인, 거절, "
    "오전, 오후, 내일, 모레, 다음 주. "
    "네. 아니요. 첫 번째. 두 번째. 1번, 2번, 3번, 4번, 5번."
)

LOGISTICS_HOTWORDS = (
    "납품서 수량 대조 최종 제목 일정 삭제해 줘 바꿔 줘 세 시 "
    "입고 검수 및 재고 실사"
)


def normalize_clock_separator(text: str) -> str:
    """Remove an ASR comma between a day period and its following hour."""
    if any(quote in text for quote in ("'", '"', "‘", "’", "“", "”")):
        return text
    return re.sub(
        r"(오전|오후|새벽|아침|저녁|밤)\s*[,，]\s*"
        r"(?=(?:[0-9]{1,2}|열\s*한|열\s*두|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*시)",
        r"\1 ",
        text,
    )


@app.on_event("startup")
def load_models():
    global stt_model, tts_voice
    tts_voice = PiperVoice.load(TTS_ONNX_PATH)
    stt_model = WhisperModel(
        STT_MODEL_SIZE,
        device="cpu",
        compute_type="int8",
        cpu_threads=STT_THREADS,
        local_files_only=True,
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "stt_loaded": stt_model is not None,
        "tts_loaded": tts_voice is not None,
        "stt_model": STT_MODEL_SIZE,
        "stt_beam_size": STT_BEAM_SIZE,
    }


@app.post("/stt")
async def transcribe(file: UploadFile = File(...)):
    if stt_model is None:
        raise HTTPException(status_code=503, detail="STT model not ready")

    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=422, detail="Empty audio payload")

    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = os.path.join(tmpdir, "input_audio")
        wav_path = os.path.join(tmpdir, "norm_16k.wav")
        with open(in_path, "wb") as f:
            f.write(raw_bytes)

        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                in_path,
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                wav_path,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not os.path.exists(wav_path):
            raise HTTPException(
                status_code=422,
                detail="Invalid or unreadable audio file",
            )

        with wave.open(wav_path, "rb") as wf:
            duration = wf.getnframes() / float(wf.getframerate() or 16000)
        if duration > 15.0:
            raise HTTPException(status_code=413, detail=f"Audio too long ({duration:.1f}s > 15s)")
        if duration < 0.2:
            raise HTTPException(status_code=422, detail="Audio too short")

        with infer_lock:
            segments, _ = stt_model.transcribe(
                wav_path,
                language="ko",
                beam_size=STT_BEAM_SIZE,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                initial_prompt=LOGISTICS_PROMPT,
                hotwords=LOGISTICS_HOTWORDS,
            )
            text = normalize_clock_separator("".join(s.text for s in segments).strip())

    if not text:
        raise HTTPException(status_code=422, detail="Speech was not recognized")

    return {"text": text, "durationSec": round(duration, 2)}


class TTSRequest(BaseModel):
    text: str


@app.post("/tts")
def synthesize(req: TTSRequest):
    if tts_voice is None:
        raise HTTPException(status_code=503, detail="TTS model not ready")
    clean_text = (req.text or "").strip()
    if not clean_text:
        raise HTTPException(status_code=422, detail="Empty text")

    buf = io.BytesIO()
    with infer_lock:
        with wave.open(buf, "wb") as wf:
            tts_voice.synthesize_wav(clean_text, wf)

    return Response(content=buf.getvalue(), media_type="audio/wav")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8081)
