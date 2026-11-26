# Datasets

이 디렉터리는 **너만의 비서**의 Qwen 학습, 검증과 평가 자료를 관리합니다. 최종 모델에 사용한 데이터와 개발 과정의 실험 자료를 구분해 보존합니다.

## 최종 모델 기준

프로젝트의 최종 모델 기준은 **440개 학습 예시와 v37 후처리**입니다.

- 기존 400개 예시에 멀티턴 중간 시점 40개를 추가 학습했습니다.
- v37은 추가 학습이 아닌 모델 출력 후처리 개선입니다.
- 개발 자료의 고유 모델 입력 132건을 저장 출력으로 재평가해 132/132 일치를 확인했습니다.
- 이 결과는 실패 사례를 확인한 개발 자료의 재평가이며 독립 Test, 전체 서비스 정확도 또는 실제 API 성공률을 의미하지 않습니다.

상세 구성과 평가 범위는 다음 문서를 참고하세요.

- [440건 학습 모델과 v37 후처리 전달 안내](../docs/qwen_logistics440_v37_handoff.md)
- [logistics440_v37 실험 README](qwen/experiments/logistics440_v37/README.md)
- [v37 모델 Release](https://github.com/mobile-agent-6/server/releases/tag/qwen-logistics440-v37-20261111)

## 실험 자료

| 경로 | 구분 | 내용 |
| --- | --- | --- |
| [`logistics440_v37/`](qwen/experiments/logistics440_v37/README.md) | 최종 기준 | 실제 학습 440개, v37 후처리와 개발 자료 재평가 |
| [`logistics1000_v0.1/`](qwen/experiments/logistics1000_v0.1/README.md) | 후속 후보 | 기존 440개와 미검수 합성 변형 560개로 구성한 1,000개 학습 후보 |
| [`logistics400_v17/`](qwen/experiments/logistics400_v17/README.md) | 과거 기록 | 400개 학습과 v17 후처리 결과 |
| [`candidate_c_create_400_v0.1/`](qwen/experiments/candidate_c_create_400_v0.1/README.md) | 과거 기록 | 일정 등록 후보 C 데이터 구축과 MLX 비교 평가 |
| [`multiturn_400_v0.1/`](qwen/experiments/multiturn_400_v0.1/README.md) | 과거 기록 | 초기 멀티턴 400개 데이터셋과 평가 도구 |
| [`mlx_smoke_v0.1/`](qwen/experiments/mlx_smoke_v0.1/README.md) | 과거 기록 | MLX 20-step 시험 학습과 전후 비교 |
| [`structured_output_contract_v0.1/`](qwen/experiments/structured_output_contract_v0.1/README.md) | 과거 기록 | 구조화 출력 계약 후보와 검증 자료 |
| [`multiturn_pilot/`](qwen/experiments/multiturn_pilot/README.md) | 과거 기록 | 초기 멀티턴 시범 대화와 당시 제어부 |
| [`v0.1_initial/`](qwen/v0.1_initial/README.md) | 과거 기록 | 초기 단일 발화 평가 결과 |

`logistics1000_v0.1`의 추가 560개는 제목을 바꾼 합성 변형이며 사람 검수, 모델 추가 학습과 성능 평가를 수행하지 않았습니다. 최종 모델의 실제 학습 수량은 440개입니다.

## 검증

v37 자료의 무결성, 시나리오와 저장 모델 출력을 확인합니다.

```bash
python3 datasets/qwen/experiments/logistics440_v37/tools/verify.py
```

데이터 생성, 검수와 평가 도구의 자동 시험을 실행합니다.

```bash
python3 -m unittest discover -s datasets/qwen/tests -v
```

자동 시험은 파일 형식, 계보와 검증 도구의 동작을 확인합니다. 모델 정확도, 사람 검수 승인과 실제 API 통합 성공을 대신하지 않습니다. 각 실험의 재현 명령과 제한사항은 해당 README를 참고하세요.

## 모델 파일 관리

모델 가중치, adapter, tokenizer와 checkpoint는 Git에 포함하지 않습니다. 최종 v37 가중치는 Release로 전달하며 파일 크기와 SHA-256은 [`model-artifacts.json`](qwen/experiments/logistics440_v37/model-artifacts.json)에서 확인합니다.

실제 사용자 자료, 음성 원본, 로컬 실행 로그와 개인 검수 기록도 Git에 포함하지 않습니다. 저장소에는 합성 데이터, 검증 도구와 공개 가능한 집계 결과만 보존합니다.
