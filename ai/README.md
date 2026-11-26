# AI

이 디렉터리는 **너만의 비서**의 온디바이스 AI 실행 환경을 관리합니다. Orange Pi에서 사용하는 RKLLM 모델 서버, STT와 TTS 음성 서버, 배포 도구와 로컬 시범 구현을 포함합니다.

## 구성

| 경로 | 역할 |
| --- | --- |
| [`deploy/`](deploy/README.md) | Orange Pi 실행 환경 구성, systemd 서비스 설치와 재배포 |
| [`rkllm_runtime/`](rkllm_runtime/README.md) | RKLLM NPU 모델 서버의 Python 실행 환경 |
| [`voice_server/`](voice_server/README.md) | faster-whisper 기반 STT와 Piper 기반 TTS 실행 환경 |
| [`schedule_assistant/`](schedule_assistant/README.md) | Qwen action과 Schedule API 연동을 검증하기 위해 사용한 로컬 시범 구현 |
| [`tests/`](tests/) | 배포 도구, 로컬 시범 구현과 음성 서버의 자동 시험 |
| [`run_local.py`](run_local.py) | 로컬 시범 구현 실행 진입점 |

## 최종 실행 구조

모바일 앱은 백엔드의 공개 API만 호출합니다. 백엔드는 같은 Orange Pi 안에서 모델 서버와 음성 서버를 호출합니다.

1. 음성 요청은 백엔드가 음성 서버에 전달하고 STT 결과를 받습니다.
2. 백엔드는 텍스트 요청을 RKLLM 모델 서버에 전달합니다.
3. 모델 출력에 v37 후처리와 구조 검사를 적용합니다.
4. 백엔드가 후보 조회, 사용자 확인과 안전 검사를 거쳐 Schedule API 작업을 수행합니다.
5. 음성 응답이 필요하면 음성 서버에서 TTS WAV를 생성하고 백엔드가 앱에 제공합니다.

RKLLM 모델 서버와 음성 서버는 Orange Pi 내부 주소로만 연결하고, 앱에는 백엔드의 포트 `8000`만 공개합니다. 상세 연결과 환경 변수는 [백엔드 README](../backend/README.md#다른-구성-요소와-연결)를 참고하세요.

`schedule_assistant/`는 현재 백엔드 Assistant 구현 이전에 action 형식과 Schedule API 연결을 확인하기 위해 만든 로컬 시범 구현입니다. 최종 공개 Assistant API와 대화 상태 관리는 `backend/`에서 담당합니다.

## 모델과 데이터

최종 모델은 440개 학습 예시와 v37 후처리를 사용합니다. 구성과 평가 결과는 [v37 전달 안내](../docs/qwen_logistics440_v37_handoff.md)와 [데이터셋 README](../datasets/README.md)를 참고하세요.

모델 가중치, tokenizer와 장비별 native runtime은 Git에 포함하지 않습니다. 전달받은 파일의 식별자와 해시를 확인한 뒤 장비에 별도로 설치합니다.

## 검증

저장소 루트에서 AI 영역의 자동 시험을 실행합니다.

```bash
python -m unittest discover -s ai/tests -v
```

음성 의존성이 없는 환경에서는 실제 STT와 TTS 엔진 시험이 건너뛰어질 수 있습니다. 자동 시험 결과는 실제 모델 추론, Orange Pi 배포와 모바일 앱 통합시험 결과와 구분합니다.

실행 환경 설치, 서비스 상태 확인과 재배포 명령은 각 구성요소의 README를 참고하세요.

## Git에 포함하지 않는 파일

- 모델 가중치, adapter, tokenizer와 checkpoint
- STT와 TTS 모델 파일 및 다운로드 cache
- 실제 음성 파일과 생성된 WAV
- 장비별 절대 경로, IP, 계정과 인증정보
- 가상환경, 실행 로그와 임시 시험 결과

공개 예시에는 비밀값과 실제 접속정보 대신 안전한 예시 값을 사용합니다.
