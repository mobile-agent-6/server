# MLX Qwen3-4B 시험 학습

미검수 데이터로 학습 도구와 입출력을 확인하는 DATA/AI 내부 실험이다. 본 학습용 승인 데이터 생성이나 전체 대화 성능 평가를 대신하지 않는다. [2026-10-16 결과 보고서](../../../../docs/qwen_smoke_training_results_20261016.md)에 측정 범위와 한계를 기록했다.

## 파일

- `prepare.py`: 마지막 assistant 학습 마스크·토큰 길이 검사, non-thinking tokenizer 사본과 학습 설정 생성.
- `compare.py`: 같은 검증 입력의 기본 모델·최종 어댑터 응답 비교 및 중단 후 재개.
- `requirements.txt`: 시험 당시 주요 패키지 버전. 전체 의존성 lockfile은 아니다.
- `results_summary.json`: 실제 완료한 시험의 집계와 입력 해시. 원문 응답은 제외.
- `test_compare.py`: GPU 없이 실행하는 선택·재개 보호 검사.

## 준비와 20-step 학습

Apple Silicon macOS에서 저장소 루트를 기준으로 실행한다. 이 절은 **미검수 데이터 사용을 명시한 파이프라인 시험 재현용**이다. 본 학습은 기존 검수 절차로 승인된 자료를 별도로 구성한다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r datasets/qwen/experiments/mlx_smoke_v0.1/requirements.txt
hf download mlx-community/Qwen3-4B-4bit --local-dir "$HOME/qwen-models/Qwen3-4B-4bit"
```

모델 revision을 고정하려면 다운로드 명령에 검증한 `--revision` 값을 지정하고 별도로 기록한다. 역사적 시험에서는 revision을 보존하지 않았다.

원본 생성 명령은 미검수 생성물을 덮어쓴다. 개인 검수 기록은 `reviewed/` 등 원본 README의 절차로 보존한다.

```bash
python datasets/qwen/experiments/multiturn_400_v0.1/build_dataset.py
python datasets/qwen/experiments/mlx_smoke_v0.1/prepare.py \
  --model "$HOME/qwen-models/Qwen3-4B-4bit" \
  --output "$HOME/qwen-smoke-run-001"
mlx_lm.lora --config "$HOME/qwen-smoke-run-001/smoke.yaml"
```

`prepare.py`가 PASS로 종료된 뒤에만 학습한다. 출력 폴더는 새 경로여야 하며 오류 후 재시도할 때도 새 경로를 사용한다. `--source`로 별도 원본 실험 폴더를 지정할 수 있다. 생성된 `smoke.yaml`이 실제 학습 설정이며 경로를 담으므로 커밋하지 않는다. 기본값은 20 iterations, batch 1, LoRA 4 layers, learning rate 1e-5, seed 42, gradient checkpoint 활성화다. max sequence length는 데이터에서 계산한다. 모델 view는 원본 가중치에 대한 심볼릭 링크를 사용하므로 다운로드 폴더를 이동·삭제하지 않는다.

## 비교와 재개

```bash
python datasets/qwen/experiments/mlx_smoke_v0.1/compare.py \
  --run-dir "$HOME/qwen-smoke-run-001" --limit 10
```

확인 후 `--limit 114`로 전체 validation을 별도 비교한다. 모델 호출 시점 114개이며 대화 114개가 아니다. 질문의 표현 차이도 전체 JSON 불일치로 잡히며, `action_match`는 action 종류만 같은 것이 아니라 검증기 통과 조건까지 포함한다. `SELECT_TARGET` 정답은 현재 validation에 없다. 동적 JSON schema·실제 API·대화 제어부를 사용하지 않는다.

쉬어 가려면 `--stage before`로 시작한다. before 완료 후 출력된 비교 폴더를 아래 `COMPARISON_DIR` 위치에 넣는다.

```bash
python datasets/qwen/experiments/mlx_smoke_v0.1/compare.py \
  --run-dir "$HOME/qwen-smoke-run-001" --limit 114 --stage before
python datasets/qwen/experiments/mlx_smoke_v0.1/compare.py \
  --run-dir "$HOME/qwen-smoke-run-001" --resume COMPARISON_DIR --stage after
```

Control+C로 중단하면 완료한 행을 보존한다. 같은 `--resume`과 `--stage`로 남은 행을 실행한다. 재개 시 `--limit` 대신 기존 manifest의 선택 행을 사용한다. 데이터·어댑터·모델 가중치와 tokenizer·실험 런타임의 해시가 바뀌면 중단한다. 시작 시 가중치 해시 검사로 잠시 시간이 걸릴 수 있다. 비교 도중 파일이나 Python 환경을 바꾸지 않는다.

기존 개인 스크립트의 manifest에는 추가 식별 정보가 없어 이 버전으로 재개할 수 없다. 완료된 역사적 결과는 그대로 보관하고 새 비교는 새 디렉터리에서 시작한다. 강제 종료로 JSONL 마지막 행이 손상되면 자동 삭제하지 않고 오류를 내므로 파일을 보존한 뒤 확인한다.

## 검증과 Git 제외

```bash
python -m unittest discover -s datasets/qwen/experiments/mlx_smoke_v0.1 -p 'test_*.py' -v
```

이 검사는 GPU 학습·추론 실행 검증이 아니다. 공유 버전은 경로 일반화와 재개 보호를 검사했으며 전체 GPU 실험을 재실행하지 않았다. 학습 결과 보고서의 수치는 기존 로컬 실행에서 나온 것이다.

모델, 어댑터, 가상환경, generated JSONL, 개인 검수 기록과 원문 비교 로그는 Git에 넣지 않는다. 위 명령처럼 실행 산출물은 저장소 밖에 둔다. Git 공유 대상은 코드·실행 안내·검증 코드·주요 버전·익명화된 집계와 보고서뿐이다.
