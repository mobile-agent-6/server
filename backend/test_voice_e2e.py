import urllib.request
import json
import uuid
import wave
import io
import time

# 유효한 1초짜리 16kHz mono PCM WAV 데이터 생성
buf = io.BytesIO()
with wave.open(buf, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(16000)
    wf.writeframes(b"\x00\x10" * 16000)
valid_wav_bytes = buf.getvalue()

request_id = str(uuid.uuid4())
post_url = f"http://127.0.0.1:8000/api/v1/voice/requests?requestId={request_id}"

print(f"[*] 테스트 시작 - 생성된 requestId: {request_id}")
print(f"[*] 1단계: 음성 접수 요청 중 -> {post_url}")

req = urllib.request.Request(
    post_url,
    data=valid_wav_bytes,
    headers={"Content-Type": "audio/wav"},
    method="POST"
)

try:
    with urllib.request.urlopen(req) as response:
        print(f"[*] 접수 응답 코드: {response.status}")
        body_text = response.read().decode('utf-8')
        print(f"[*] 접수 응답 본문: {body_text}")
except urllib.error.HTTPError as e:
    print(f"[-] 접수 실패 (HTTP Error {e.code}): {e.read().decode('utf-8')}")
    exit(1)

# 2단계: 모델 추론 시간(~28초)을 고려하여 폴링 대기 시간 연장 (최대 15회, 3초 간격 = 총 45초)
get_url = f"http://127.0.0.1:8000/api/v1/voice/requests/{request_id}/speech"
print(f"[*] 2단계: 생성된 TTS 음성 결과 조회 대기 및 폴링 시작 (최대 45초 소요)...")

success = False
for attempt in range(1, 16):
    time.sleep(3.0)  # 3초 대기
    print(f"[*] 시도 {attempt}/15 - TTS 조회 요청 중 -> {get_url}")
    get_req = urllib.request.Request(get_url, method="GET")
    try:
        with urllib.request.urlopen(get_req) as get_response:
            print(f"[*] 성공! TTS 조회 응답 코드: {get_response.status}")
            audio_content = get_response.read()
            print(f"[*] 수신된 TTS 오디오 바이트 크기: {len(audio_content)} bytes")
            success = True
            break
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        if e.code == 409:
            print(f"[-] 아직 준비되지 않음 (409 VOICE_SPEECH_NOT_READY), 재시도 대기 중...")
        else:
            print(f"[-] TTS 조회 실패 (HTTP Error {e.code}): {error_body}")
            break
    except Exception as e:
        print(f"[-] 예외 발생: {e}")
        break

if not success:
    print("[-] 제한 시간 내에 TTS 결과가 준비되지 않았습니다.")
