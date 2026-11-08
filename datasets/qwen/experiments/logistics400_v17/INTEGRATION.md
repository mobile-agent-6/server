# 백엔드·프론트 연동 준비용 전달 안내

상태: **학습 결과와 검증용 후처리 전달 가능. Orange Pi 실행 형식 변환 및 실제 연동은 미검증.**
이 문서는 2026-11-08 로컬에서 확인한 백엔드 commit `988235f202b73ba02a2954b20d6a16c93e54fc72`를 기준으로 한다. 원격 최신 코드는 연결 오류로 확인하지 못했다. 실제 반영 전 담당자가 현재 백엔드와 비교해야 한다.

## 전달물

- 이 Git 폴더: 데이터, 학습 기록, 평가 근거, 정확한 학습 프롬프트, v17 전체 후처리, CPU 검증 도구.
- `01_review-and-trained-adapter.zip`: **실제 최종 학습 가중치** `model/adapter/adapters.safetensors`와 설정, 정확한 tokenizer/chat template 포함.
- `02_exact-base-model.zip`: 위 학습 가중치가 적용되는 **정확한 기본 모델 가중치** 포함.
- `model-artifacts.json`: 두 전달 파일의 크기와 SHA-256. 아직 GitHub에 게시된 파일이나 다운로드 링크가 아니다.

Git에 커밋한 텍스트 자료만으로 모델이 실행되지는 않는다. 두 ZIP을 함께 받으면 학습 당시 MLX 모델 구성을 재구성할 수 있으나 이것이 Orange Pi 실행을 보장하지는 않는다. 02 파일은 약 2.26 GB로 GitHub Release의 단일 파일 2 GiB 제한을 넘는다. 게시할 때 분할/재포장하거나 별도의 팀 공유 파일 저장소를 이용해야 한다.

## 학습 형식과 장치 실행을 구분

현재 산출물은 MLX의 4bit 기본 모델과 MLX LoRA 가중치다. Orange Pi용 GGUF 또는 RKLLM 변환을 완료한 산출물이 아니다. MLX LoRA 파일을 다른 실행기의 LoRA로 그대로 읽을 수 있다고 가정하지 않는다.

필요한 다음 검증은 다음 순서다.

1. 장치 모델·RAM과 실제 모델 실행기를 확인한다. CPU 실행과 NPU 실행은 별도 경로다.
2. 해당 실행기가 Qwen3-4B 구조와 필요한 파일 형식을 지원하는지 확인한다.
3. 정확한 기본 모델에 최종 LoRA를 병합/변환하는 경로를 검증한다. MLX `fuse --export-gguf` 한 명령으로 Qwen3 변환이 된다고 가정하지 않는다.
4. 변환 전후 동일 입력으로 제목·날짜·시간·상태, JSON 파싱, v17 후처리 결과를 비교한다. 다시 양자화하면 수치와 출력이 달라질 수 있다.
5. 실제 장치에서 로딩, 최대 메모리, 요청 지연을 측정한다. 현재 기본 모델 파일 크기만으로 필요한 총 RAM을 판단하지 않는다.
6. 모델 서버를 백엔드에 연결하고 아래 다섯 기능을 실제 API 경로로 확인한다.

모델 학습과 후처리 검증 완료가 장치 배포 완료를 뜻하지 않는다. 현재 확인된 자료만으로 변환 실패나 재학습 필요 여부를 단정할 수 없다.

참고: [Qwen 공식 llama.cpp 실행 안내](https://qwen.readthedocs.io/en/latest/run_locally/llama.cpp.html), [MLX LM fuse 구현](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/fuse.py).

## 백엔드 연결 지점

현재 확인한 백엔드 모델 클라이언트는 모델 서버의 `GET /v1/models`와 `POST /v1/chat/completions`를 사용한다. 응답에서 `choices[0].message.content` 문자열을 읽는다. 이 문자열이 evidence JSON 원출력이다.

- 학습 프롬프트: `runtime/backend/app/modules/assistant/evidence/prompt.txt`. 기본 모델 클라이언트의 기본 prompt가 이 파일과 다를 수 있으므로 정확한 학습 prompt를 명시적으로 전달한다.
- user 메시지: `tools/common.py`의 `render_user_message`와 동일한 렌더링. 사용자 turn 번호, 기준 시각, `serverTimeTrusted`를 보존한다.
- 생성 설정: temperature 0, seed 29, max_tokens 1024, thinking 비활성화. thinking 비활성화 방법은 실제 서버/채팅 템플릿에서 확인한다.
- 순서: 모델 생성 → v17 후처리 → evidence 계약 검증 및 기존 planner/provider → 기존 Schedule API 처리.

`runtime/rules/correction_bridge_v17.py`의 `make_provider(model, schedule_reader)`는 연결 참고 구현이다. 모델 추출과 `ClarificationRequired` 처리를 함께 감싼다. 확인 질문이 필요하면 기존 task 상태를 비우고 전체 요청을 다시 묻는다. `PostprocessedEvidenceModel`만 감싸고 예외 처리를 누락하면 안 된다.

로컬 기준 `backend/app/main.py`의 `_model_provider`는 일반 `EvidenceDecisionProvider`를 생성한다. 이 공유 폴더를 추가하는 것만으로 v17이 활성화되지 않는다. 실제 반영은 백엔드 담당자가 prompt 전달과 provider 생성 지점을 함께 연결해야 한다. 전체 앱 의존성과 현재 provider의 내부 인터페이스를 확인해야 하며, 본 공유에서는 `backend/` 파일을 변경하지 않았다.

`runtime/backend/`는 검증용 사본이지 전체 서버가 아니다. 실제 앱의 `app` 패키지 대신 이 사본을 서비스 import 경로에 앞세우거나 서버 폴더를 덮어쓰지 않는다.

## 프론트 연결 지점

프론트는 모델 가중치를 로드하지 않고 백엔드의 기존 Assistant API를 사용한다. 현재 확인한 라우트는 다음과 같다.

- `POST /api/v1/assistant/requests`: 텍스트·후보 선택·확인 입력 제출.
- `GET /api/v1/assistant/requests/{requestId}`: 비동기 처리 상태와 결과 확인.
- `POST /api/v1/assistant/conversations/{conversationId}/cancel`: 취소.

요청/응답의 최신 기준은 저장소 `backend/app/modules/assistant/router.py`와 `schemas.py`다. 이 문서로 별도 프론트 계약을 만들지 않는다. 모델 evidence JSON을 프론트가 직접 일정 생성 값으로 처리하지 않는다. 백엔드가 반환하는 추가 정보 질문·후보 선택·확인·결과·실패 상태를 표시한다.

## 인수 확인

| 확인 항목 | 현재 상태 |
|---|---|
| 최종 물류 400건 학습 가중치 존재 | 확인, 별도 파일 전달 필요 |
| 학습 데이터/설정/prompt/가중치 식별 해시 | 공유 자료에 포함 |
| 현재 모델의 저장된 150건 v17 재처리 | CPU 검증 PASS |
| 새 입력의 변환 전후 모델 출력 비교 | 미실시 |
| Orange Pi용 모델 형식 변환 및 로딩 | 미실시 |
| Orange Pi 속도·메모리 확인 | 미실시 |
| 실제 백엔드 prompt + v17 provider 연결 | 미실시 |
| 등록/조회/수정/삭제/완료 변경 실제 API 동작 | 미실시 |
| 프론트 질문·확인·결과 표시 | 미실시 |

다섯 기능의 API 연결 결과와 모델의 엄격 JSON 일치율은 별도 지표다. 현재 개발 평가 150건의 후처리 일치율 91.3%는 실제 장치·API의 성공률이 아니다.
