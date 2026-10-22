# Qwen 멀티턴 평가 자료

> 상태: DATA 영역 내부 평가 실험. AI 출력 계약·평가 데이터 schema를 확정하지 않는다.

현재 작업은 [멀티턴 400건 데이터셋](qwen/experiments/multiturn_400_v0.1/README.md)을 기준으로 한다. 기존 단일 발화 100문항과 전용 프롬프트·실행기·채점기·테스트는 2026-10-22에 제거했다. 과거 평가 보고서의 근거인 초기 결과 CSV는 보존한다.

## 폴더 안내

| 경로 | 용도 |
|---|---|
| [`qwen/experiments/multiturn_400_v0.1/`](qwen/experiments/multiturn_400_v0.1/README.md) | 현재 멀티턴 데이터셋·검수·평가 도구 |
| [`qwen/experiments/mlx_smoke_v0.1/`](qwen/experiments/mlx_smoke_v0.1/README.md) | MLX 시험 학습·전후 비교 도구와 과거 집계 |
| [`qwen/experiments/multiturn_pilot/`](qwen/experiments/multiturn_pilot/README.md) | 구형 시범 5건과 당시 제어부 보존본 |
| `qwen/tools/` | 구형 시범의 재생·실제 모델 평가·검토 화면 |
| `qwen/tests/` | 데이터·검수·평가 도구 검사 |
| [`qwen/v0.1_initial/`](qwen/v0.1_initial/README.md) | 과거 보고서가 참조하는 초기 결과 CSV |

프로젝트 페르소나와 제거된 단일 발화 평가의 검토 기록은 [`docs/persona_and_qwen_review.md`](../docs/persona_and_qwen_review.md)에 있다.

## 검증과 실행

저장소 루트에서 도구 검사를 실행한다.

```bash
python -m unittest discover -s datasets/qwen/tests
python -m unittest discover -s datasets/qwen/experiments/mlx_smoke_v0.1 -p 'test_*.py'
```

실제 모델 평가·검수·학습 명령은 각 실험의 README를 따른다. 실행마다 새 출력 폴더를 사용한다. 자동 검사는 모델 정확도나 검수 승인을 뜻하지 않는다. 원본 음성·실제 사용자 자료·모델 체크포인트·비밀값은 Git에 추가하지 않는다.

## 멀티턴 400개 데이터셋

[현재 데이터셋](qwen/experiments/multiturn_400_v0.1/README.md)은 train 250건·validation 50건·test 예약 100건이다. 2026-10-22 사용자 지시에 따라 400건 모두 `reviewed`(검수 완료)로 처리했다. 별도의 독립 검수를 수행한 기록은 아니며, test는 잠근 독립 시험본이 아니다. 별도 `diagnostic/`의 API 장애·재시도 진단 10건은 본 400건의 점수에 합치지 않는다. 공유 AI 출력·DATA schema는 여전히 `TBD-AI`·`TBD-DATA`다.

현재 생성본은 알림 필드를 제거한 `qwen-dataset-400-v0.1.4-proposed`다. 이전 검수 승인과 MLX 시험 수치는 현재 초안에 자동 승계하지 않는다. 실제 Qwen 기준선·독립 검수·재학습은 새 입력에서 확인해야 한다. 과거 측정 근거는 [시험 학습 보고서](../docs/qwen_smoke_training_results_20261016.md)에 보존한다.

학습 변환본·정답 재생 미리보기·검수 템플릿은 필요할 때 실험 폴더의 `build_dataset.py`로 생성한다. 이 명령은 생성본을 덮어쓰며, `review_status.json`에 승인된 내용·분할·질문 기준이 같을 때만 `reviewed`를 유지한다. 내용이 달라지면 `proposed_unreviewed`로 돌아간다. 개인 검수 기록과 과거 실행 결과는 구분해 보관한다. 실제 모델 원문·로컬 실행 로그·개인 검수 기록은 Git에 포함하지 않는다.
