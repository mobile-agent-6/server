import urllib.request
import json
import uuid
import wave
import io
import time

def create_wav():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x10" * 16000)
    return buf.getvalue()

def find_question_id(obj):
    if isinstance(obj, dict):
        if "questionId" in obj and obj["questionId"]:
            return obj["questionId"]
        for v in obj.values():
            res = find_question_id(v)
            if res:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_question_id(item)
            if res:
                return res
    return None

wav_bytes = create_wav()

# ================= [Step 1: 등록 요청] ================
req_id_1 = str(uuid.uuid4())
url_1 = f"http://127.0.0.1:8000/api/v1/voice/requests?requestId={req_id_1}"
print(f"[Step 1] 요청 시작 (requestId={req_id_1})")

with urllib.request.urlopen(urllib.request.Request(url_1, data=wav_bytes, headers={"Content-Type": "audio/wav"}, method="POST")) as res:
    body = json.loads(res.read().decode("utf-8"))
    conv_id = body.get("conversationId")

get_speech_1 = f"http://127.0.0.1:8000/api/v1/voice/requests/{req_id_1}/speech"
for _ in range(15):
    time.sleep(3.0)
    try:
        with urllib.request.urlopen(urllib.request.Request(get_speech_1, method="GET")) as g_res:
            print(f"[+] Step 1 TTS 생성 완료 ({len(g_res.read())} bytes)")
            break
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise

status_url_1 = f"http://127.0.0.1:8000/api/v1/assistant/requests/{req_id_1}"
with urllib.request.urlopen(urllib.request.Request(status_url_1, method="GET")) as s_res:
    status_body = json.loads(s_res.read().decode("utf-8"))
    question_id = find_question_id(status_body)
    print(f"[+] 응답 텍스트: {(status_body.get('result') or {}).get('text')}")
    print(f"[+] 추출된 questionId: {question_id}\n")

# ================= [Step 2: 후속 답변 요청] ================
req_id_2 = str(uuid.uuid4())
url_2 = f"http://127.0.0.1:8000/api/v1/voice/requests?requestId={req_id_2}&conversationId={conv_id}&questionId={question_id}"
print(f"[Step 2] 후속 답변 요청 (questionId={question_id})")

with urllib.request.urlopen(urllib.request.Request(url_2, data=wav_bytes, headers={"Content-Type": "audio/wav"}, method="POST")) as res_2:
    print(f"[+] Step 2 접수 성공 ({res_2.status}): {res_2.read().decode('utf-8')}")

get_speech_2 = f"http://127.0.0.1:8000/api/v1/voice/requests/{req_id_2}/speech"
for _ in range(15):
    time.sleep(3.0)
    try:
        with urllib.request.urlopen(urllib.request.Request(get_speech_2, method="GET")) as g_res_2:
            print(f"[+] Step 2 TTS 생성 완료 ({len(g_res_2.read())} bytes)")
            break
    except urllib.error.HTTPError as e:
        if e.code != 409:
            raise

print("\n[완료] 전체 멀티턴 E2E 시나리오 통과!")
