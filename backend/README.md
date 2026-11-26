# 너만의 비서 Backend

물류창고 작업 비서 **너만의 비서**의 백엔드. FastAPI와 SQLite를 기반으로 일정 관리, 자연어 비서, 음성 요청 기능을 처리하며, Orange Pi 5 Plus에서 모델 서버 및 음성 서버와 함께 동작.

> `server` 저장소의 백엔드 영역. 모델 및 음성 서버는 [`ai/`](../ai), 학습 및 평가 데이터는 [`datasets/`](../datasets), API 명세와 DB 구조는 [`docs/`](../docs)에서 관리.
>
> 모바일 앱은 별도의 [`frontend`](https://github.com/mobile-agent-6/frontend) 저장소에서 관리.

## 주요 기능

공개 API는 4개 영역, 12개 호출로 구성. 요청 및 응답 형식은 [`docs/api.md`](../docs/api.md) 참고.

| 영역 | 호출 | 역할 |
|---|---|---|
| System | `GET /health`<br>`POST /api/v1/system/time-sync` | 서버 및 DB 상태 확인, 장비 시각 동기화 |
| Schedule | `GET /api/v1/schedules`<br>`POST /api/v1/schedules`<br>`GET /api/v1/schedules/{scheduleId}`<br>`PATCH /api/v1/schedules/{scheduleId}`<br>`DELETE /api/v1/schedules/{scheduleId}` | 일정 등록, 조회, 수정, 완료 상태 변경, 삭제 |
| Assistant | `POST /api/v1/assistant/requests`<br>`GET /api/v1/assistant/requests/{requestId}`<br>`POST /api/v1/assistant/conversations/{conversationId}/cancel` | 자연어 요청 접수 및 처리 상태 조회. 정보 보완, 후보 선택, 변경 확인 대화와 취소 |
| Voice | `POST /api/v1/voice/requests`<br>`GET /api/v1/voice/requests/{requestId}/speech` | WAV 음성을 텍스트로 변환해 Assistant로 전달, 처리 결과를 음성(WAV)으로 반환 |

업무 카테고리는 입고(`INBOUND`), 재고(`INVENTORY`), 출고(`OUTBOUND`), 반품(`RETURN`), 기타(`OTHER`)의 5종.

## 안전장치

자연어 및 음성 입력은 해석 오류 가능성이 있으므로, 일정 변경은 백엔드의 최종 검사를 통과한 경우에만 실행.

| 구분 | 동작 |
|---|---|
| 모델 출력 검증 | 모델은 사용자 원문의 근거 구절만 출력하고, 값 계산과 판단은 백엔드(`app/modules/assistant/evidence/`)가 담당. 원문에 없는 근거나 규격 밖 출력은 `MODEL_OUTPUT_INVALID`로 종료하고 일정에 반영하지 않음 |
| 변경 전 확인 | 수정, 삭제, 완료 상태 변경은 대상과 변경 내용을 보여 주고 승인 후 실행. 후보가 여러 건이면 먼저 선택 요청 |
| 저장 직전 재검사 | safe writer가 과거 시각, 대상 ID, `updated_at` 충돌, 멱등성, 최대 1건 쓰기를 저장 직전에 다시 확인 |
| 중복 요청 방지 | 일정 등록은 `Idempotency-Key`로 같은 요청을 한 번만 반영하고, 24시간 동안 최초 결과 재사용 |
| 장애 격리 | 모델이나 음성 서버가 멈추면 해당 기능만 `503` 반환. 수동 일정 관리와 `/health`는 정상 유지 |
| 요청 복구 | 대화, 요청, 질문 상태를 SQLite에 저장해 통신 단절이나 서버 재시작 후에도 원래 요청 상태 확인 가능 |
| 로그 보호 | 사용자 원문과 모델 원출력 대신 길이와 SHA-256만 기록 |

## 요구 사항

| 항목 | 조건 |
|---|---|
| Python | 3.11 |
| 운영체제 | Linux 또는 macOS (POSIX `fcntl` 사용). Windows 네이티브 실행은 미지원 |
| DB | SQLite (별도 설치 불필요) |
| 의존성 | `requirements.lock` 기준 설치 |
| 선택 | Assistant 사용 시 OpenAI 호환 모델 서버, Voice 사용 시 로컬 STT/TTS 음성 서버 필요 |

모델 서버와 음성 서버 없이도 일정 관리와 `/health`는 동작.

## 빠른 시작

개발 기준 브랜치는 `develop`. `backend/` 폴더에서 실행.

```bash
git switch develop
cd backend

# 1. 가상 환경 생성 및 의존성 설치
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock

# 2. 환경 변수 파일과 DB 폴더 준비
cp .env.example .env
mkdir -p data

# 3. DB를 최신 구조로 변경
python -m alembic upgrade head

# 4. 서버 실행
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

실행 후 다음 주소에서 확인.

- 상태 확인: <http://127.0.0.1:8000/health>
- API 문서(Swagger UI): <http://127.0.0.1:8000/docs>

## 환경 변수

[`.env.example`](.env.example)을 `.env`로 복사한 뒤 필요한 값만 변경. 모든 변수는 `SCHEDULE_` 접두사 사용.

| 묶음 | 주요 변수 | 기본값 |
|---|---|---|
| 서버 | `SCHEDULE_API_HOST`, `SCHEDULE_API_PORT`, `SCHEDULE_DOCS_ENABLED`, `SCHEDULE_CORS_ORIGINS` | `0.0.0.0`, `8000`, `true`, `[]` |
| DB | `SCHEDULE_DATABASE_URL`, `SCHEDULE_BACKUP_DIR`, `SCHEDULE_BACKUP_KEEP` | `sqlite:///data/app.db`, `data/backups`, `5` |
| 로그 | `SCHEDULE_LOG_LEVEL`, `SCHEDULE_LOG_FORMAT`, `SCHEDULE_LOG_FILE` | `INFO`, `console`, 비움 |
| 시각 동기화 | `SCHEDULE_TIME_SYNC_ENABLED` | `false` |
| 모델 | `SCHEDULE_ASSISTANT_MODEL_ENABLED`, `SCHEDULE_ASSISTANT_MODEL_BASE_URL` | `false`, `http://127.0.0.1:8088` |
| 음성 | `SCHEDULE_VOICE_ENABLED`, `SCHEDULE_VOICE_SERVER_BASE_URL` | `false`, `http://127.0.0.1:8081` |

모델과 음성 기능은 기본적으로 꺼져 있음. 켜는 방법과 세부 변수는 [다른 구성 요소와 연결](#다른-구성-요소와-연결) 참고.
실제 `.env` 파일은 Git에 추가하지 않음.

## 폴더 구조

```text
backend/
├── app/
│   ├── main.py              # FastAPI 앱 생성 및 라우터 등록
│   ├── core/                # 설정, DB 연결, 시각, 로깅, 미들웨어, 오류 형식
│   └── modules/
│       ├── system/          # 상태 확인, 시각 동기화
│       ├── schedules/       # 일정 CRUD, 카테고리, 중복 요청 방지
│       ├── assistant/       # 대화 상태, 처리 runtime, 요청 복구, safe writer
│       │   └── evidence/    # 모델 호출, 출력 검증, 후처리 규칙(v37), 실행 계획
│       └── voice/           # WAV 검사, 음성 서버 연결
├── alembic/                 # DB migration (현재 head: 20261017_0004)
├── tests/                   # unit, api, integration 테스트
├── scripts/                 # API 계약 smoke, 배포용 lock, wheelhouse 생성
├── test_scenario_e2e.py     # 시나리오 종단 간 시험
├── test_voice_e2e.py        # 음성 종단 간 시험
├── pyproject.toml
├── requirements.lock                       # 개발 환경 의존성
├── requirements-runtime-linux-aarch64.lock # Orange Pi(ARM64) 실행 환경 의존성
├── alembic.ini
└── .env.example             # 공유용 환경 변수 예시
```

## 테스트

### 전체 실행

```bash
python -m pytest --cov=app --cov-branch --cov-report=term-missing
```

2026-11-17 Pi 보고 기준 1,515건 통과, 1건 건너뜀. 건너뛴 1건은 모델 서버 주소가 없을 때 실행하지 않는 실제 모델 smoke test.

### 영역별 실행

| 대상 | 명령 |
|---|---|
| Assistant 안전 검증 | `python -m pytest -q -p no:cacheprovider tests/integration/test_assistant_model_output_safety.py tests/integration/test_assistant_evidence_locked56.py tests/integration/test_assistant_evidence_gold_replay.py` |
| v37 후처리 회귀 | `python -m pytest -q tests/unit/test_assistant_v37_rules.py tests/integration/test_assistant_v37_replay.py tests/integration/test_assistant_model_wiring.py` |
| 음성 계약 (가짜 엔진) | `python -m pytest -q tests/unit/test_voice_audio.py tests/unit/test_voice_client.py tests/unit/test_voice_router.py tests/api/test_voice_api.py tests/api/test_openapi_contract.py` |
| prompt 줄바꿈과 해시 | `python -m pytest -q tests/unit/test_assistant_evidence_model_client.py` |

### Assistant 안전 검증 기준

| 묶음 | 고정 기준 |
|---|---|
| 모델 오답 안전 행렬 | API부터 임시 SQLite까지 단일 turn 안전 사례 84개에서 확인 전 쓰기 0건 또는 사람이 고정한 정확한 DB 행 |
| 정상 대조군 | 등록 2개와 완료 부정 조회 4개가 질문으로 바뀌지 않고 기대 DB 행 또는 조회 결과와 일치 |
| 멀티 turn 확인 경계 | 수정 승인, 등록 확인 승인, 자정 확인 승인과 거절, state 초기화, 잘못된 미지원 근거 뒤 날짜 재질문 6개 사례의 모델 호출 수와 전체 DB 행 일치 |
| locked56 | correct 49 이상, unsafe 0 |
| 기존 정답 재생 | pilot, batch 1부터 6, Test 50의 정답 849행에서 승인된 확인 추가 2건만 허용하고 `3b0f937` 기준선 밖 차이 0 |

- Test 50 lock, locked56 원본, 기존 연구 manifest의 SHA-256은 재생 테스트에서 고정.
- run 006 평가 파일에는 원출력 본문 없이 SHA-256과 byte 길이만 있어, 같은 모델 출력을 이전과 현재 백엔드에 다시 적용하는 비교는 불가. 기존 run의 모델 점수와 현재 백엔드 재생 결과는 별도 지표로 기록.
- 저장 출력 재생은 실제 모델 추론, 앱 HTTP 통합, Orange Pi 실행을 대신하지 않음.

## 다른 구성 요소와 연결

백엔드는 모바일 앱의 요청을 받아, 같은 장비 안의 모델 서버와 음성 서버를 호출하는 구조.

| 구성 요소 | 기본 주소 | 연결 방식 |
|---|---|---|
| 모바일 앱 | → 백엔드 `:8000` | 공개 HTTP API |
| 모델 서버 | `127.0.0.1:8088` (개발 Mac), `:8080` (Orange Pi) | OpenAI 호환 `/v1/chat/completions` |
| 음성 서버 | `127.0.0.1:8081` | 내부 `/stt`, `/tts` (loopback 전용) |

### 모델 서버

- OpenAI 호환 `/v1/chat/completions` endpoint면 연결 가능. Orange Pi에서는 RKLLM 웹 런타임, 개발 Mac에서는 `mlx_lm.server` 사용.
- 시스템 prompt는 학습 때와 같은 `app/modules/assistant/evidence/prompt.txt` 사용. 시작과 재연결 시 학습 prompt의 고정 SHA-256을 검사하고, 파일이 없거나 해시가 다르면 모델을 호출하지 않고 Assistant만 `503 ASSISTANT_UNAVAILABLE` 유지. Schedule API와 `/health`는 영향 없음.
- `backend/.gitattributes`로 prompt의 LF 줄바꿈 고정. Windows `core.autocrlf=true` 환경에서도 학습 바이트와 SHA-256을 보존하기 위한 설정. 이미 받은 작업본은 자동 변환되지 않을 수 있으므로, 로컬 수정이 없는지 확인 후 LF로 저장하고 테스트로 확인. 실행 중 자동 줄바꿈 보정이나 잘못된 해시 허용은 하지 않음.

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `SCHEDULE_ASSISTANT_MODEL_ENABLED` | `false` | `true`면 시작 시 모델 서버 연결. 준비 전이면 첫 텍스트 요청에서 재확인하고, 계속 연결되지 않으면 Assistant만 `503` |
| `SCHEDULE_ASSISTANT_MODEL_BASE_URL` | `http://127.0.0.1:8088` | 모델 서버 주소 |
| `SCHEDULE_ASSISTANT_MODEL_NAME` | 비움 | 요청의 `model` 값. 비우면 전송하지 않음 |
| `SCHEDULE_ASSISTANT_MODEL_TIMEOUT_SECONDS` | `60` | 모델 호출 1회 제한 시간 (최대 240) |
| `SCHEDULE_ASSISTANT_TRUST_SERVER_TIME` | `false` | `true`면 시각 동기화 없이 서버 시각을 신뢰해 "내일" 같은 상대 날짜를 바로 계산. 시계가 정확한 개발 Mac에서만 사용. `false`면 앱의 `POST /api/v1/system/time-sync` 성공 후부터 신뢰하고, 그전에는 절대 날짜를 다시 질문 |

개발 Mac에서 학습한 어댑터로 실행하는 예:

```bash
mlx_lm.server --model <기본 모델> --adapter-path <어댑터> --host 127.0.0.1 --port 8088 \
  --temp 0.0 --chat-template-args '{"enable_thinking": false}'
SCHEDULE_ASSISTANT_MODEL_ENABLED=true SCHEDULE_ASSISTANT_TRUST_SERVER_TIME=true \
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

실제 모델 smoke test는 CI에서 건너뛰고, 모델 서버가 있을 때만 실행.

```bash
ASSISTANT_SMOKE_MODEL_URL=http://127.0.0.1:8088 python -m pytest tests/integration/test_assistant_model_smoke.py -s
```

#### 모델 출력 처리

- 모델 연결 시 Qwen 440예시 모델의 v37 후처리를 적용한 뒤 evidence parser, 역할 및 원문 검사, planner로 전달.
- 후처리가 `ClarificationRequired`를 반환하면 `need_more_info` 질문으로 전달하고 이전 작업의 evidence와 확인 상태 초기화.
- 후처리 결과만으로 일정 쓰기를 승인하지 않으며, 후보 선택, 변경 승인, safe writer 검사는 그대로 유지.
- 후처리 후에도 검사를 통과하지 못하면 모델을 다시 호출하지 않고 `MODEL_OUTPUT_INVALID`로 종료. decoding이 고정(temperature 0)이라 같은 입력은 같은 출력을 내기 때문.
- 규칙은 `app/modules/assistant/evidence/rules/`에 포함. 출처는 [`logistics440_v37/runtime/rules`](../datasets/qwen/experiments/logistics440_v37/runtime/rules)이며, `correction_bridge_v37`에 필요한 38개 모듈을 패키지 상대 import로만 바꾼 사본. 실행과 배포는 `datasets/`나 별도 `PYTHONPATH`에 의존하지 않음. 규칙 변경 시 출처 비교와 저장 출력, 대화 회귀 테스트를 함께 확인.
- 실제 Pi STT가 출력한 `내일 세 시 입고 납품서 대조 일정 삭제해 줘`에서 v37 후처리가 `세 시`를 제목에 합치는 경우는 `evidence/native_clock.py`에서 보정. 단일 삭제, 단일 turn의 원문 전체가 정해진 형태와 일치하고 기존 date/title 근거도 원문과 일치할 때만 원문의 시간과 제목을 분리. 인용된 제목, 정정과 불확실 표현, 복수 작업은 보정하지 않음. 기존 38개 출처 규칙과 prompt는 수정하지 않고 역할과 겹침 검사, 후보 선택과 삭제 승인을 유지. 이 보정과 실제 두 후보 삭제 시나리오는 합성 WAV로 두 번 검증.
- 모델 호출 제한 시간은 `SCHEDULE_ASSISTANT_MODEL_TIMEOUT_SECONDS`와 "요청 처리 마감(300초)까지 남은 시간에서 안전 여유 10초를 뺀 값" 중 짧은 쪽. 대기열에서 남은 시간이 10초 이하가 되면 모델을 부르지 않고 `MODEL_TIMEOUT`으로 종료. 마감을 넘긴 모델 호출이 남으면 runtime이 unhealthy가 되기 때문.

### 음성 서버

- 백엔드는 기본적으로 음성 기능을 끈 상태로 시작. 로컬 STT/TTS 음성 서버를 `127.0.0.1:8081`에 준비한 뒤 아래 설정으로 활성화.

```bash
SCHEDULE_VOICE_ENABLED=true
SCHEDULE_VOICE_SERVER_BASE_URL=http://127.0.0.1:8081
SCHEDULE_VOICE_SERVER_TIMEOUT_SECONDS=30
```

#### 음성 입력 규격

| 항목 | 조건 |
|---|---|
| 형식 | `audio/wav`, RIFF/WAVE, PCM S16LE, 16 kHz, mono, 16-bit |
| 제한 | 1회 최대 15초, 최대 1,048,576 bytes |
| 응답 | 신규 접수 `202` (인식된 `transcript`와 실제 접수된 `input` 포함). 같은 `requestId` 재전송 시 다시 처리하지 않고 기존 상태 `200` |

#### 처리 규칙

- 후보 질문에는 유효한 번호 하나만 선택으로 인정. 승인 질문에는 명확한 긍정이나 부정만 인정하고, 불명확한 답은 `422 VOICE_ANSWER_UNCLEAR`.
- 무음이나 빈 인식 결과를 임의의 일정 명령으로 바꾸지 않음.
- STT와 TTS는 하나의 처리 슬롯을 공유. TTS가 실패해도 이미 완료된 Assistant 요청이나 일정 쓰기를 다시 실행하지 않음.
- 실제 Whisper, Piper 모델 파일, Orange Pi 성능, iPhone 전체 연동은 이 저장소의 백엔드 테스트만으로 검증되지 않음. 백엔드 테스트는 가짜 엔진으로 계약만 확인([테스트](#테스트) 참고).
- 모델 파일과 실제 음성, DB는 Git에 추가하지 않음.

### 모바일 앱

앱은 백엔드의 공개 API(포트 `8000`)에 연결.

| 실행 환경 | 백엔드 주소 |
|---|---|
| iOS Simulator | 일반적으로 `http://127.0.0.1:8000` |
| Android Emulator | 일반적으로 `http://10.0.2.2:8000` |
| 실제 휴대폰 | 서버 컴퓨터와 같은 네트워크에서 해당 컴퓨터의 로컬 주소와 포트 `8000` |

- 앱은 연결 시 `GET /health`로 서버와 DB 상태를 확인하고, `POST /api/v1/system/time-sync`로 장비 시각을 맞춘 뒤 "내일" 같은 상대 날짜를 사용.
- 실제 장비와 개인 네트워크 주소는 코드나 Git 문서에 기록하지 않음. Orange Pi 접속 정보는 실기기 설정 후 팀 내에서 공유.

## 운영

### Assistant runtime

- Schedule DB 파일마다 POSIX advisory lock 1개 사용. lock 경로는 같은 폴더의 `{DB 파일명}.assistant-runtime.lock`.
- lock 파일이 있다고 현재 소유 중이라는 뜻은 아니므로, 실행 중이거나 장애를 조사할 때 수동으로 삭제하지 않음.
- 같은 DB를 쓰는 서버는 `uvicorn` worker 1개로 운영.
- runtime 소유자는 단일 worker와 deadline sweeper를 실행. provider 응답 없음, worker 또는 sweeper 이상, 시작 시 복구 실패로 Assistant가 비활성화되면 서버 프로세스를 재시작해 lock 획득과 근거 기반 복구를 다시 수행. 재시작해도 모델 호출이나 일정 쓰기를 다시 실행하지 않음.
- 모델이 연결되지 않은 기본 상태에서는 새 텍스트와 자연어 후속 입력을 저장하지 않고 `503 ASSISTANT_UNAVAILABLE` 반환. 저장된 질문의 대상과 변경 내용이 완전한 선택이나 승인은 모델 없이 접수해 safe writer까지 처리 가능.
- 테스트 전용 scripted provider, 시각, UUID 주입은 demo 환경에서 사용 불가.

### 확인 질문과 실행

- 백엔드가 직접 만든 등록 확인 질문은 저장할 절대 연도, 날짜, 시각을 표시. 표시한 값은 title, scheduledAt, category와 함께 확인 상태에 고정.
- 사용자가 동의하면 모델을 다시 부르지 않고, 검증된 모델 출력과 확인받은 값으로 planner 재개.
- 자정 관련 확인은 확인받은 로컬 시각과 근거 원문으로 다시 계산한 값이 같을 때만 사용.
- 이전 형식이거나 손상된 상태는 쓰기 근거로 사용하지 않고 새로 시작.
- 수정, 삭제 확인은 화면에 고정된 변경 내용을 실행하며, safe writer가 과거 시각, 대상 ID, `updated_at` 충돌, 중복 요청, 최대 1건 쓰기를 저장 직전에 다시 검사.

### 로그

- 모델을 부를 때마다 `assistant.model_call` 로그 1줄 기록. 요청 ID, 모델명, prompt 해시, turn 수, 입력 글자 수, 입력 SHA-256, 출력 byte 수, 출력 SHA-256, 검사 결과(`ok` 또는 오류 코드), 처리 시간 포함.
- 사용자 turn 원문과 모델 원출력은 기록하지 않음.
- 위 필드는 `SCHEDULE_LOG_FORMAT=json`일 때만 기록 (console 형식은 결과와 시간 요약 1줄). SQLite나 오류 응답에는 포함하지 않음.
- 해시와 길이도 운영 정보이므로 로그 파일은 Git이나 팀 공유 위치에 올리지 않음.
- Assistant processor의 예기치 않은 내부 오류는 event, 요청 ID, 예외 class만 기록. 예외 메시지, traceback, 입력 원문은 남기지 않음.
- 긴 제목처럼 예상 가능한 제약 위반은 planner가 재입력 질문으로 처리해 내부 오류로 보내지 않음.

## DB migration

### revision 이력

| revision | 내용 |
|---|---|
| `20260927_0001` | 최초 일정 schema |
| `20261008_0002` | 카테고리를 `INBOUND`, `INVENTORY`, `OUTBOUND`, `RETURN`, `OTHER`로 교체 |
| `20261017_0003` | 제품 범위에서 제외된 알림 컬럼 `schedules.reminder_minutes_before` 삭제 |
| `20261017_0004` | Assistant 대화, 요청, 질문, write gate 테이블 추가 (현재 `head`) |

- 최신 서버 코드를 쓸 DB는 반드시 `upgrade head`까지 적용. `20260927_0001` DB에서 실행하면 카테고리 변경, 알림 컬럼 제거, Assistant schema가 차례로 적용됨.
- `/health`는 revision이 `head`와 다르면 `503`을 반환하지만, 이것만으로 모든 Schedule CRUD가 막힌다고 보장하지 않음. migration 적용을 대신하는 보호장치로 쓰지 않음.

### 공통 적용 절차

1. 서비스를 중지하고 대상 DB 경로와 현재 revision 확인.
2. SQLite online backup을 만들고 `integrity_check`와 revision 검증.
3. `python -m alembic upgrade head` 실행.
4. 적용 후 `/health`, 일정 CRUD, 기존 일정 수와 필드, `alembic current` 확인.

복구는 **migration 전 검증 backup과 그 revision에 맞는 코드를 함께 복원**하는 것이 기본. downgrade는 schema만 되돌릴 뿐 데이터와 구버전 코드 호환을 보장하지 않음.

### `20261008_0002` 카테고리 변경

- 적용 전 기존 `schedules`의 카테고리와 ID 확인. `PERSONAL`, `PICKING`, 확인되지 않은 `MEETING`이나 예상하지 못한 값이 있으면 적용하지 않음.
- 기존 `MEETING`이 없으면 공통 절차대로 `upgrade head`.
- 변환이 승인된 `MEETING`만 있고 ID가 모두 확인됐으면, 정확한 ID 전체를 쉼표로 이어 `CONFIRMED_MEETING_IDS`로 전달.

```bash
python -m alembic -x "confirmed_meeting_ids=${CONFIRMED_MEETING_IDS}" upgrade head
```

- 전달한 ID 집합이 DB의 기존 `MEETING` ID 집합과 정확히 같을 때만 해당 행을 `OTHER`로 변경. 다른 필드, soft-delete 행, 인덱스, 멱등성 기록은 보존. 적용 후 카테고리별 CRUD도 확인.
- 카테고리 migration만 따로 점검할 때는 `upgrade 20261008_0002`를 명시하고, 그 상태에서는 최신 서버를 쓰지 않음.
- `MEETING`에서 바뀐 `OTHER`는 자동 복원 불가. `INBOUND`나 `OUTBOUND` 행이 있으면 downgrade하지 않고 backup으로 복구. 두 카테고리가 없어 downgrade가 가능해도 바뀐 `OTHER`는 그대로 남음.
- 구버전 코드는 새 CHECK와 호환되지 않으므로 구버전 코드만 다시 배포하지 않음.

### `20261017_0003` 알림 컬럼 삭제

- 알림 컬럼만 삭제하고 나머지 필드, soft-delete 행, 인덱스, `idempotency_records`의 fingerprint, 응답 snapshot, 만료 시각은 보존. 제거 전 알림 값은 폐기됨.
- 실패 후 `_alembic_tmp_schedules`가 남아 있으면 자동 삭제하거나 바로 재시도하지 않음.
  1. 서비스를 중지하고 대상 DB 경로와 현재 revision을 다시 확인.
  2. 현 상태를 보존한 사본이나 backup을 만들고, 원본 `schedules`와 잔여 테이블의 schema, 인덱스, 행 수, 일정 ID, `integrity_check` 확인.
  3. 원본 일정과 무결성 보존을 입증할 수 없거나 상태가 불명확하면 migration 전 backup 복구를 우선.
  4. 잔여 테이블이 비어 있다는 사실만으로 삭제가 안전하다고 판단하지 않음. 정확한 대상과 원본 보존을 확인하고 별도 승인을 받은 경우에만 잔여 테이블 처리를 결정한 뒤 backup과 무결성을 다시 확인하고 재시도.
- schema만 되돌릴 때:

```bash
python -m alembic downgrade 20261008_0002
```

- downgrade는 알림 컬럼을 nullable로 다시 만들지만 모든 값은 `null`. 구버전 코드로 안전하게 돌아간다는 보장이 아니며, 제거 전 알림 값도 복원하지 않음.
- 현행 일정 생성 fingerprint는 version `2`. 알림 필드가 있는 구형 요청은 `422 VALIDATION_ERROR`, 아직 유효한 version `1` key를 새 요청에 재사용하면 `409 IDEMPOTENCY_KEY_CONFLICT`. 기존 기록을 수정하거나 충돌 후 새 key로 자동 등록하지 않음.

### `20261017_0004` Assistant 테이블 downgrade

- 반드시 Assistant runtime을 포함한 서버를 먼저 중지하고, 대상 DB, revision, 검증 backup을 확인한 상태에서 수행.

```bash
python -m alembic downgrade 20261017_0003
```

- accepted나 processing 요청, 또는 outcome이나 failure로 확정되지 않은 uncertain 기록이 있으면 첫 DDL 전에 거절됨.
- 안전 검사를 통과해도 Assistant 테이블과 tombstone을 모두 삭제하므로 `REQUEST_EXPIRED` 최소 기록 보장이 사라지는 파괴적 작업. `docs/api.md` §16.8의 DB 초기화 같은 운영 예외로만 취급.
- Schedule과 멱등성 데이터를 보존하더라도, 구버전 코드와 검증 backup 없이 일반 rollback 수단으로 쓰지 않음.

## 참고 문서

| 문서 | 내용 |
|---|---|
| [`docs/api.md`](../docs/api.md) | 공개 API 계약 (요청, 응답, 오류 코드) |
| [`docs/erd.md`](../docs/erd.md) | DB 구조 |
| [`.env.example`](.env.example) | 공유용 환경 변수 예시 |
| [`ai/README.md`](../ai/README.md) | 모델 및 음성 서버 |
| [`datasets/README.md`](../datasets/README.md) | 학습 및 평가 데이터 |
| [`TEAM_GUIDELINES.md`](../TEAM_GUIDELINES.md) | 저장소 협업 기준 |
| [`frontend`](https://github.com/mobile-agent-6/frontend) | 모바일 앱 저장소 |

- 기본 로컬 설정에서는 실제 시스템 시각을 변경하는 time-sync 기능이 비활성화되어 있음 (`SCHEDULE_TIME_SYNC_ENABLED=false`).
- 실제 `.env`, SQLite DB, 로그는 Git에 추가하지 않음.
