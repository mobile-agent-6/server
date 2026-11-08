# 물류 일정 Qwen 모델 팀 공유 안내

작성일: 2026-11-08 · 전달 구성: **400건 학습 모델 + v17 후처리**

이 문서는 백엔드·프론트·모델 실행 담당자가 연동을 준비하기 위한 안내다. 현재는 학습과 개발 평가를 완료한 상태이며, 실제 서비스 연결이나 Orange Pi 배포를 완료했다는 의미는 아니다. 문서 확인 기준은 `develop`의 `88c6996bf0bc64795ad549b43aeb0d82cffe6c4f`다.

## 먼저 볼 자료

| 목적 | 자료 |
|---|---|
| 학습 구성과 평가 결과 확인 | [학습·평가 README](../datasets/qwen/experiments/logistics400_v17/README.md) |
| 백엔드 연결 준비 | [연동 안내](../datasets/qwen/experiments/logistics400_v17/INTEGRATION.md) |
| 프론트 연결 준비 | [기존 Assistant 공개 API](api.md#16-텍스트-assistant-api-공개-계약) |
| 후처리 코드 확인 | [v17 및 의존 모듈](../datasets/qwen/experiments/logistics400_v17/runtime/rules/) |
| 실제 모델 파일 식별 | [모델 파일 목록·SHA-256](../datasets/qwen/experiments/logistics400_v17/model-artifacts.json) |
| 학습 예시 확인 | [학습 400건 보기](../datasets/qwen/experiments/logistics400_v17/data/학습400건_눈으로보기.txt) |

## 무엇을 학습했는가

- 기본 모델: `mlx-community/Qwen3-4B-4bit`.
- 작업: 등록, 조회, 수정, 삭제, 완료 상태 변경 **각 80건, 총 400건**.
- 학습 순서: 기본 가중치에서 222건 학습 → 신규 178건 추가 학습.
- 학습 산출물: 최종 LoRA 가중치. 여기서 LoRA 어댑터는 백엔드 API 연결용 어댑터와 다른 의미다.
- v17: 모델 원출력의 원문 복사 오류 등을 보완하는 별도 Python 후처리. 모호한 정정은 확인 질문으로 전달한다.

MLX는 Mac에서 사용한 학습 도구다. MLX로 학습했다는 사실이 모바일 앱을 Apple 전용으로 만든다는 뜻은 아니다. 최종 목표는 Orange Pi의 모델·백엔드와 모바일 앱의 연결이며, 목표 실행기에 맞는 가중치 변환과 실기기 검증은 별도 단계다.

## 현재 평가 결과

| 개발 평가 | 순수 모델 정답 | v17 적용 후 정답 | v17 출력 규칙 통과 |
|---|---:|---:|---:|
| 기존 100건 | 46/100 | 94/100 | 96/100 |
| 추가 25건① | 10/25 | 24/25 | 25/25 |
| 추가 25건② | 12/25 | 19/25 | 22/25 |
| 합산 150건 | 68/150 (45.3%) | **137/150 (91.3%)** | 143/150 (95.3%) |

정답은 JSON 전체 엄격 일치다. 확인 질문 7건은 정답에 포함하지 않았다. 이 문항들은 후처리 개선에 사용한 개발 평가이며, 독립 Test나 실제 API 성공률이 아니다. 기본 모델의 동일 문항 평가가 없으므로 학습 전 기본 모델 대비 향상 폭으로 사용하지 않는다.

## 연동 흐름

모바일 앱 → 백엔드 Assistant API → 실행 중인 Qwen → v17 후처리 → 백엔드 검증·일정 처리 → 앱 응답.

| 연결 준비 영역 | 확인할 내용 |
|---|---|
| 모델 실행 | 정확한 기본 모델과 최종 LoRA, tokenizer/chat template, thinking 비활성화, 모델 서버 실행 |
| 백엔드 | 학습 prompt·user 메시지 형식, v17 적용, 확인 질문 예외 처리, 기존 Schedule 실행 흐름 연결 |
| 프론트 | 기존 Assistant API의 비동기 상태, 질문·후보·승인·결과·실패 표시 |
| 장비 수령 후 | Orange Pi용 형식 변환·로딩, 메모리·응답 시간, 실제 다섯 기능 연결 검증 |

위 표는 연결에 필요한 작업 구분이며 새 담당 배정을 뜻하지 않는다. 최신 [백엔드 문서](../backend/README.md#모델-연결)는 Orange Pi용 변환·실행·성능 측정을 별도 작업으로 명시한다. 실제 작업 분담은 팀 합의를 따른다.

## 모델 가중치 받기

Git에는 텍스트 자료와 코드만 포함한다. 실제 모델을 실행하는 담당자는 작성자가 별도로 전달하는 아래 두 파일을 모두 받아야 한다.

1. `01_review-and-trained-adapter.zip`: 최종 학습 가중치, 정확한 tokenizer/template, 후처리와 평가 자료.
2. `02_exact-base-model.zip`: 해당 학습 가중치가 적용되는 정확한 기본 모델 본체.

현재 저장소에 다운로드 파일이나 Release가 게시된 상태는 아니다. 두 파일의 정확한 크기·SHA-256은 [model-artifacts.json](../datasets/qwen/experiments/logistics400_v17/model-artifacts.json)에 있다. 기본 모델 ZIP은 약 2.26 GB이므로 GitHub Release에 첨부하려면 분할 또는 재포장이 필요하다. 팀 파일 저장소로 전달해도 된다.

전달된 ZIP의 모델 파일은 MLX 형식이다. Orange Pi용 GGUF/RKLLM 변환본으로 소개하지 않는다. 프론트 담당자는 모델 가중치를 앱에 넣을 필요가 없다.

## 지금 확인할 수 있는 것

저장소 루트에서 실행한다. Python 3.10 이상과 `Asia/Seoul` 시간대 자료가 필요하며 MLX나 GPU는 필요 없다.

```bash
python3 datasets/qwen/experiments/logistics400_v17/tools/verify.py
```

파일 무결성, 실제 학습 400건의 분포와 두 학습 단계의 일치, 정답 420건 보존, 저장된 출력 150건의 v17 재현을 검사한다. 실제 모델 추론이나 DB 변경은 수행하지 않는다.

현재 평가용 계약 코드는 이전 백엔드 `988235f` 기준의 고정 사본이다. 최신 백엔드는 그 이후 오류 처리와 새 작업 기준 시각 처리가 변경됐다. 평가 재현 PASS를 최신 백엔드 연결 PASS로 해석하지 않는다. 실제 백엔드에 연결할 때는 [연동 안내의 최신 변경 항목](../datasets/qwen/experiments/logistics400_v17/INTEGRATION.md#최신-백엔드에-연결할-때)을 확인한다.
