# Orange Pi 로컬 STT/TTS 실행 환경

공개 음성 계약은 [API 문서](../../docs/api.md)의 §16.14를 따른다. 이 파일은 AI 음성 프로세스의 실행 설정과 검증 명령을 설명한다. backend와 별도 Python 3.11 가상환경을 사용한다.

## 모델과 실행 조건

- 서버 진입점: `ai/voice_server/server.py`
- 필요한 실행 파일: `ffmpeg`
- STT: faster-whisper, CPU int8, 한국어, VAD 활성화, temperature 0, 이전 segment의 텍스트 문맥 사용 안 함
- STT 모델은 서비스 사용자의 Hugging Face 캐시에 미리 저장해야 한다. `local_files_only=True`이므로 부팅 중 외부 모델 다운로드를 시도하지 않는다.
- TTS: Piper, `ai/voice_server/models/piper-ko-std/*.onnx`와 대응 JSON. 모델은 Git 밖에서 별도 배포한다.
- 내부 서버는 `127.0.0.1:8081`에만 bind한다. 앱은 backend 8000 포트만 호출한다.

| 환경변수 | 코드 기본값 | 이번 Pi 시험 설정 |
|---|---:|---:|
| `STT_MODEL_SIZE` | `base` | `small` |
| `STT_THREADS` | 8 | 8 |
| `STT_BEAM_SIZE` | 3 | 3 |

`requirements.txt`는 Pi의 Python 3.11 음성 가상환경에서 확인한 직접 의존성 버전이다. backend 의존성과 섞어 설치하지 않는다. 내부 `/health`의 `stt_model`, `stt_beam_size`, 모델 준비 상태로 실제 설정을 확인한다.

## 검증

저장소 루트에서 음성 서버 가상환경으로 실행한다.

```bash
ai/voice_server/.venv/bin/python -m unittest discover -s ai/tests -v
```

음성 의존성이 없는 환경에서는 음성 엔진 테스트가 명시적으로 skip된다. 이 테스트는 decoder 설정 전달과 무인식 422 경계를 검증하기 위해 가짜 인식기를 사용한다. 실제 Pi 음원 시험과 구분한다.

2026-11-16 시험에서는 Microsoft Heami로 만든 고정 한국어 WAV를 실제 Pi STT에 업로드했다. 합성 음원이며 사람이 녹음한 음성 품질의 검증으로 취급하지 않는다. 짧은 답변과 날짜를 함께 포함한 프롬프트 및 물류 명사·명령 어휘의 hotwords를 사용하며, '안녕'을 '아니요'로 바꾸는 식의 의미 추측 보정은 하지 않는다.

2026-11-17에는 사용자가 녹음한 고정 문장 17개를 16 kHz mono PCM WAV로 변환해 재시험했다. `오후, 2시`처럼 STT가 시각 표현 안에 삽입한 쉼표는 `normalize_clock_separator`가 제거한다. 오전·오후 등 시간대와 바로 뒤 시각 사이의 쉼표만 대상으로 하며, 인용한 제목·다른 쉼표·소수점은 보존한다. 물류 hotwords에는 입고 검수 및 재고 실사를 추가했다. 도크·냉장창고 등 기존 인식 프롬프트 어휘를 hotwords에 중복 추가했을 때 삭제 발화의 세 시 인식이 나빠져 필요한 어휘만 유지했다. 시험 상태와 실제 transcript는 배포·시험 가이드의 실제 사람 녹음 절을 따른다.

## 서비스 설치 및 재배포

서비스 생성·설치는 [배포 도구](../deploy/README.md)를 따른다. STT/TTS 프로세스에는 Schedule DB 접근 수단을 추가하지 않는다. 모델·환경·음원과 비밀값은 Git에 추가하지 않는다.
