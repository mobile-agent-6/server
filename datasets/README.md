# Qwen 평가 자료

> 상태: DATA 영역 내부 평가 실험. AI 출력 계약·평가 데이터 schema를 확정하지 않는다.

```text
datasets/
├── README.md
└── qwen/
    ├── v0.1_initial/  # 초기 100건 문항·실행 코드·당시 결과
    ├── tools/         # 평가 실행·재채점 도구
    └── tests/         # 평가 도구 검증
```

`v0.2/`는 정답 규칙을 합의하고 문항을 검수한 뒤 만든다. 현재 새 채점 결과는 `v0.1_initial` 문항의 **재분석**이며 새 평가셋 버전이 아니다. `qwen/runs/`는 로컬 실행 결과 저장 위치이며 Git에서 제외한다.

## 보존한 초기 평가

- `qwen/v0.1_initial/qwen_batch_test_100_scored.py`: 2026-09-25 기준 날짜의 원래 100문항·프롬프트·모델 호출·채점 코드
- `qwen/v0.1_initial/prompt_v01.txt`: 당시 프롬프트와 동일한 내용을 문항 파일에서 분리해 새 실행 도구에 제공하는 템플릿
- `qwen/v0.1_initial/qwen_100_scored_results.csv`, `qwen/v0.1_initial/qwen_100_scored_summary.csv`: 당시 응답과 기존 정규화 채점 결과

위 파일은 초기 측정 기록으로 보존한다. 기존 완전 일치 16/100은 문자열 `"null"`을 실제 `null`과 같게 처리한 결과다.

원래 배치 스크립트의 `TESTS`에는 문항과 정답이 하드코딩되어 있다. 당시 실행 내용을 보존하기 위한 파일이며, 새 실행 도구는 아래 JSONL 문항 파일과 별도 프롬프트 템플릿을 입력으로 읽는다. 초기 CSV에는 문항별 소요 시간과 토큰 지표가 없어 학습 전 속도를 복원할 수 없다.

## 재채점 실험

- `qwen/v0.1_initial/qwen_legacy_eval_100.jsonl`: 원래 코드의 `TESTS`를 문항 ID·기준 날짜·정답과 함께 추출한 복사본. **모든 라벨은 `legacy_unreviewed`**이며 새 합의 규칙으로 검수한 평가셋이 아니다.
- `qwen/tools/qwen_eval.py`: 모델을 호출하지 않는 오프라인 채점기. 기존 CSV 또는 ID별 `raw_response`가 들어 있는 JSONL을 읽는다.
- `qwen/tools/run_legacy_eval.py`: 기존 프롬프트와 문항을 사용해 로컬 Ollama 모델을 다시 실행하고 응답, 요청 소요 시간, Ollama 지표와 실행 조건을 기록한다.

문항의 `review_flags`는 정답 수정 없이 우선 검토할 문제를 표시한다. 현재 `category_mapping_tbd` 51건, `read_content_rule_missing` 19건, `target_change_not_separated` 25건, `ambiguous_delete_needs_clarification_review` 5건이다. 한 문항에 여러 표기가 붙을 수 있다. 이 표기는 검수 완료나 새 정답 결정을 의미하지 않는다.

저장소 루트에서 다음 명령으로 초기 결과를 재집계할 수 있다.

```bash
python datasets/qwen/tools/qwen_eval.py --legacy-results datasets/qwen/v0.1_initial/qwen_100_scored_results.csv --summary-only
python -m unittest discover -s datasets/qwen/tests
```

다음 실행 결과를 채점할 때는 문항 ID마다 JSON 객체 한 줄을 갖는 UTF-8 JSONL을 사용한다.

```json
{"id":"legacy-001","raw_response":"{\"intent\":\"create\",\"date\":\"2026-09-25\",\"time\":\"15:00\",\"location\":\"B구역\",\"category\":\"반품\",\"content\":\"확인\"}"}
```

```bash
python datasets/qwen/tools/qwen_eval.py --responses PATH_TO_RESPONSES.jsonl --summary-only
```

## 학습 전후 품질·속도 측정

같은 장비와 Ollama 환경에서 기준 모델과 학습 모델을 각각 실행한다. 학습 전 기준 모델도 **다시 실행**해야 한다. 실행 폴더는 매번 새 이름을 사용하며, 도구가 기존 폴더를 덮어쓰지 않는다.

```bash
python datasets/qwen/tools/run_legacy_eval.py --model qwen3:4b --output-dir datasets/qwen/runs/before
python datasets/qwen/tools/run_legacy_eval.py --model YOUR_FINE_TUNED_MODEL --output-dir datasets/qwen/runs/after
python datasets/qwen/tools/qwen_eval.py --responses datasets/qwen/runs/before/responses.jsonl --compare-responses datasets/qwen/runs/after/responses.jsonl --summary-only
```

각 폴더의 `responses.jsonl`에는 문항 ID, 원문 응답, 요청 시작부터 완료까지의 `wall_time_ms`, Ollama의 나노초 단위 지속 시간과 토큰 수, 오류 종류가 기록된다. 오류 요청의 시간은 응답 속도 통계에서 제외하고 오류 건수로 별도 집계한다. `manifest.json`에는 문항·프롬프트 템플릿 SHA-256, 모델 이름, 실행 시각, 장비 플랫폼, Python 버전과 생성 옵션을 저장한다. 모델 digest와 양자화 정보를 알고 있다면 `--model-digest`, `--quantization`으로 함께 기록한다. 실제 IP나 비밀값은 저장하지 않는다.

채점 결과의 `speed`는 정상 요청의 전체 응답 시간 중앙값, 최근접 순위 방식 p95, Ollama 로드 시간 중앙값과 생성 토큰/초 중앙값을 보여준다. 토큰/초는 `eval_count / (eval_duration / 1e9)`로 계산하며 해당 지표가 있는 요청만 포함한다. 두 실행을 비교할 때는 같은 문항 ID의 엄격한 완전 일치 변화와 문항별 시간 차이의 중앙값을 제시한다. **음수 시간 차이는 학습 모델이 더 빠르다는 뜻**이다. `timed_count`, `token_rate_count`, `paired_timing_count`로 사용된 문항 수를 확인한다.

전후 비교 전에는 두 manifest의 문항·프롬프트 해시, 모델 digest·양자화, 장비, Ollama 버전과 실행 조건을 확인한다. 현재 manifest는 Ollama 버전을 자동 수집하지 않으므로 별도 기록이 필요하다. 반복 실행하여 중앙값을 비교하고, 첫 호출의 모델 로드 시간은 별도 해석한다. 응답 길이가 바뀌면 전체 지연과 토큰/초가 다르게 움직일 수 있어 두 지표를 함께 본다. 실행한 적 없는 모델의 속도 향상을 추정할 수 없다.

이 도구는 **기존 v0.1 프롬프트와 기준 날짜만** 실행한다. 평가 라벨은 여전히 `legacy_unreviewed`이므로 결과는 임시 실험치다. 합의된 `v0.2` 문항·출력 규칙을 만들 때는 그 버전에 맞는 실행기와 채점 규칙을 추가해야 한다. 학습 데이터와 평가 문항의 중복 검사도 데이터 준비 시 수행해야 한다.

채점 결과는 JSON 파싱, 6개 키·타입·값 형식 준수, 원문 완전 일치, 기존 정규화 완전 일치를 별도로 보고한다. 필드별로 전체 정확 일치와 정답이 null이 아닌 문항의 정확 일치를 구분한다. `issues`는 오류 유형별 **발생 횟수**이므로 합계가 오류 문항 수와 다를 수 있다. 전체 문항·응답 파일의 ID 중복과 누락, 이전 CSV의 문항·정답 불일치는 오류로 중단한다.

이 채점기는 여전히 초기 평가의 6개 필드와 임시 카테고리를 사용한다. 완료·재질문, 수정 대상 식별, 시간 범위와 실제 Schedule API 작업 성공 여부를 측정하지 않는다. 새로운 공유 출력 규칙은 AI·데이터·백엔드 등 관련 영역의 합의 후 별도 버전으로 추가한다. 학습·검증 자료가 저장소에 없어 현재 문항과의 중복 검사는 수행하지 못했다. 모델 digest·실행 장비·프롬프트 버전·생성 옵션·지연 시간도 기존 CSV에는 없으므로 재채점으로 복원할 수 없다.

원본 음성, 실제 사용자 자료, 대형 모델·checkpoint와 비밀값은 Git에 추가하지 않는다. 관리 범위는 `TEAM_GUIDELINES.md` §7을 따른다.
