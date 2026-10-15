# MLX Qwen3-4B 시험 학습

DATA/AI 내부 시험 학습 도구와 결과다. 미검수 자료의 초기 시험에 이어 시범 검수 승인본의 20-step 학습과 전후 비교를 완료했다. 승인본 변환 도구는 검수 원본을 입력으로 사용하며 검수 자체를 대신하지 않는다. 전체 데이터 학습이나 전체 대화 성능 평가를 완료한 것은 아니다. [2026-10-16 결과 보고서](../../../../docs/qwen_smoke_training_results_20261016.md)에 두 시험의 측정 범위와 한계를 기록했다.

## 파일

- `prepare.py`: 승인본 또는 명시적으로 선택한 미검수 입력의 마지막 assistant 학습 마스크·토큰 길이 검사, non-thinking tokenizer 사본과 학습 설정 생성.
- `compare.py`: 같은 검증 입력의 기본 모델·최종 어댑터 응답 비교 및 중단 후 재개.
- `requirements.txt`: 시험 당시 주요 패키지 버전. 전체 의존성 lockfile은 아니다.
- `results_summary.json`: 실제 완료한 시험의 집계와 입력 해시. 원문 응답은 제외.
- `results_summary_reviewed.json`: 검수 승인본의 후속 시험 집계·검수 방식·입력 해시. 초기 시험과 직접 비교하지 않는다.
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
  --allow-unreviewed \
  --model "$HOME/qwen-models/Qwen3-4B-4bit" \
  --output "$HOME/qwen-smoke-run-001"
mlx_lm.lora --config "$HOME/qwen-smoke-run-001/smoke.yaml"
```

`prepare.py`가 PASS로 종료된 뒤에만 학습한다. 출력 폴더는 새 경로여야 하며 오류 후 재시도할 때도 새 경로를 사용한다. `--source`로 별도 원본 실험 폴더를 지정할 수 있다. 생성된 `smoke.yaml`이 실제 학습 설정이며 경로를 담으므로 커밋하지 않는다. 기본값은 20 iterations, batch 1, LoRA 4 layers, learning rate 1e-5, seed 42, gradient checkpoint 활성화다. max sequence length는 데이터에서 계산한다. 모델 view는 원본 가중치에 대한 심볼릭 링크를 사용하므로 다운로드 폴더를 이동·삭제하지 않는다.

## 승인본 연결

검수 원본을 [승인본 변환 절차](../multiturn_400_v0.1/README.md#검수-정답을-학습-입력에-반영)에 따라 수정·승인한 뒤 실행한다. train과 validation에 각각 승인된 모델 호출이 필요하다. 생성된 모델 응답이나 검수 화면의 판정 파일을 직접 넣지 않는다.

```bash
python datasets/qwen/experiments/multiturn_400_v0.1/export_training.py \
  --output-dir "$HOME/qwen-reviewed-data-001"
python datasets/qwen/experiments/mlx_smoke_v0.1/prepare.py \
  --reviewed-data "$HOME/qwen-reviewed-data-001" \
  --model "$HOME/qwen-models/Qwen3-4B-4bit" \
  --output "$HOME/qwen-reviewed-smoke-001"
```

`--reviewed-data`와 `--allow-unreviewed` 중 하나를 반드시 지정한다. 승인본을 지정하면 `--source`는 사용하지 않으며, manifest의 상태·파일 해시·행 수·프롬프트 해시를 검사한다. 승인본이 없거나 잘못됐을 때 미검수 자료로 대체하지 않는다. 실행 폴더의 `dataset_manifest.json`에 출처를 보존하고 `inspection.json`에 `REVIEWED_SMOKE_ONLY`를 기록한다. 승인본이어도 설정은 20-step 시험용이며 본 학습 완료를 의미하지 않는다. 이 준비 명령만으로 학습이 시작되지는 않는다.

로컬 검수 원본·판정 기록은 Git에 포함하지 않는다. 새 checkout에는 승인 사례가 없으므로 검수 원본을 별도로 준비해야 한다. 자동 테스트의 임시 승인은 재현용 데이터 승인이 아니다. 2026-10-16 시범은 사용자의 전체 검토 진술과 위임에 따라 Codex가 30건을 점검·기록했으며 독립적인 복수 사람 검수가 아니다.

승인본 시험과 비교는 다음과 같이 실행한다. `--limit 13`은 이번 시범 validation 전체 행 수이며 다른 데이터에는 실제 행 수를 사용한다.

```bash
mlx_lm.lora --config "$HOME/qwen-reviewed-smoke-001/smoke.yaml"
python datasets/qwen/experiments/mlx_smoke_v0.1/compare.py \
  --run-dir "$HOME/qwen-reviewed-smoke-001" --limit 13
```

새 비교의 `scope`는 준비된 `inspection.json`의 검수 상태를 따른다. 후속 시험의 기존 로컬 summary에는 예전 고정 문구 `UNREVIEWED`가 남아 있다. 공유 집계에는 승인본 데이터 해시와 일치함을 확인한 정정 설명을 포함했고 원본 결과는 보존했다.

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

Control+C로 중단하면 완료한 행을 보존한다. 시작 시와 중단 시 표시하는 재개 명령을 복사하면 저장된 before/after 행을 건너뛰고 남은 두 단계를 완료한다. 완료 행은 임시 파일에 쓴 뒤 교체하므로 저장 중 중단되어도 기존 JSONL은 보존되며, 저장되지 않은 행만 다시 계산한다. before만 마치고 쉬려면 재개 명령의 `--stage both`를 `--stage before`로 바꾼다. 재개 시 `--limit` 대신 기존 manifest의 선택 행을 사용한다. 데이터·어댑터·모델 가중치와 tokenizer·실험 런타임의 해시가 바뀌면 중단한다. 시작 시 가중치 해시 검사로 잠시 시간이 걸릴 수 있다. 비교 도중 파일이나 Python 환경을 바꾸지 않는다. 이미 실행 중인 프로세스에는 코드 변경이 반영되지 않으므로 중단 후 새 명령으로 재개한다.

기존 개인 스크립트의 manifest에는 추가 식별 정보가 없어 이 버전으로 재개할 수 없다. 완료된 역사적 결과는 그대로 보관하고 새 비교는 새 디렉터리에서 시작한다. 강제 종료로 JSONL 마지막 행이 손상되면 자동 삭제하지 않고 오류를 내므로 파일을 보존한 뒤 확인한다.

## 검증과 Git 제외

```bash
python -m unittest discover -s datasets/qwen/tests -v
python -m unittest discover -s datasets/qwen/experiments/mlx_smoke_v0.1 -p 'test_*.py' -v
```

이 검사는 GPU 학습·추론 실행 검증이 아니다. 공유 버전은 경로 일반화와 재개 보호를 검사했으며 전체 GPU 실험을 재실행하지 않았다. 학습 결과 보고서의 수치는 기존 로컬 실행에서 나온 것이다.

승인본 연결·재개 보호·검수 상태 표시에 대한 DATA 테스트 37개와 비교 테스트 7개를 제공한다. 임시 fixture와 실제 승인본 63/13행의 마지막 정답 구간은 CPU tokenizer로 확인했다. 에이전트 작업 환경에서는 Metal에 접근할 수 없지만 사용자의 터미널에서 실제 승인본 준비·20-step 학습·13행 전후 비교를 완료했고, 저장된 inspection·어댑터·비교 결과를 대조했다. 이 자동 테스트를 검수 승인이나 GPU 성능 측정으로 해석하지 않는다.

모델, 어댑터, 가상환경, generated JSONL, 개인 검수 기록과 원문 비교 로그는 Git에 넣지 않는다. 위 명령처럼 실행 산출물은 저장소 밖에 둔다. Git 공유 대상은 코드·실행 안내·검증 코드·주요 버전·익명화된 집계와 보고서뿐이다.
