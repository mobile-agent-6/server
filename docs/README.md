# 문서 안내

서버 저장소의 API, 데이터베이스, 모델 연동과 검증 기록을 정리한다. 현재 구현 기준과 과거 개발 기록을 구분해 사용한다.

## 현재 기준 문서

| 문서 | 내용 |
|---|---|
| [API 문서](api.md) | System, Schedule, Assistant와 Voice 공개 API 계약 |
| [OpenAPI 명세](openapi.json) | FastAPI 공개 API 명세 |
| [데이터베이스 ERD](erd.md) | Schedule 및 Assistant 영속 데이터 구조 |
| [Qwen v37 인계 문서](qwen_logistics440_v37_handoff.md) | 440건 학습 모델, v37 후처리와 서버 연동 기준 |

## 개발 및 검증 기록

| 문서 | 내용 |
|---|---|
| [AI 구조화 출력 계약 검증](ai_structured_output_contract_proposal.md) | 후보 C 출력 계약을 선택하기 전 수행한 비교와 제안 |
| [후보 C 데이터 작성 기준](candidate_c_data_authoring_guide.md) | 후보 C 학습 데이터 작성과 검수 기준 |
| [후보 C 학습 검증 결과](candidate_c_create_training_evaluation.md) | 후보 C 데이터 구축과 MLX 학습 시험 결과 |
| [Qwen 시험 학습 결과](qwen_smoke_training_results_20261016.md) | 초기 MLX 시험 학습과 전후 비교 결과 |
| [Orange Pi 음성 연동 검증 기록](orangepi-voice-validation-20261117.md) | 2026-11-16부터 17일까지 수행한 배포 및 음성 시험 기록 |
| [최종 통합 검증 결과](final-integration-validation.md) | Orange Pi 음성 시나리오 30회와 iOS 음성 통합 8개 항목 검증 결과 |

## 과거 계획 및 초기 분석

아래 문서는 개발 당시의 판단과 변화를 보존한다. 현재 계약이나 최종 성능 근거로 사용하지 않는다.

| 문서 | 내용 |
|---|---|
| [초기 데이터셋 평가](dataset_evaluation.md) | 2026-09-27 단일 발화 평가 기록 |
| [초기 학습 데이터 문제 분석](llm_data_issues.md) | 초기 데이터와 라벨 규칙의 문제점 분석 |
| [페르소나와 Qwen 검토](persona_and_qwen_review.md) | 초기 페르소나와 단일 발화 평가셋 검토 |
| [멀티턴 파인튜닝 계획](qwen_multiturn_finetuning_plan.md) | 최종 모델과 API 확정 전 작성한 학습 및 평가 계획 |
