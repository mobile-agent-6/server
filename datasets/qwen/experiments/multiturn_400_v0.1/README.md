# Qwen 멀티턴 400개 — 미검수 초안

기존 30개와 추가 370개를 합한 **400개 대화**다. 한 대화는 여러 사용자 턴으로 구성된다. 2026-10-15~16에 이 초안으로 별도의 [MLX 20-step 시험 학습](../mlx_smoke_v0.1/README.md)을 실행했다. 전체 데이터 학습은 아직 완료하지 않았다. 모든 정답은 `proposed_unreviewed`다. 공유 AI 출력·데이터 계약을 확정하지 않는다.

## 업로드 구성

| 파일 | 역할 |
|---|---|
| `prompt.txt` | 다음 실제 평가에 사용할 v2.5 제안 프롬프트. action별 적용 범위와 JSON 예시를 정리했으며 성능은 아직 측정하지 않음 |
| `build_dataset.py` | 원본 대화·생성 규칙으로 400개 생성 및 정답 재생. 필요한 원본 30개는 코드에 포함 |
| `review_sources.py` | 사례별 검수 원본 생성·검증 및 승인 사례만 별도 출력 |
| `export_training.py` | 승인된 train/validation을 재생해 현재 프롬프트의 학습 JSONL과 원본 추적표 생성 |
| `pilot_review_cases_v0.1.txt` | train 25건·validation 5건의 재현 가능한 시범 검수 표본. test는 포함하지 않음 |
| `scenarios_all_400_UNREVIEWED.jsonl` | 전체 400개 대화 |
| `scenarios_all_400_UNREVIEWED_turns.csv` | 전체 400개 대화의 979개 사용자 턴을 한 행씩 펼친 검토용 CSV |
| `train/scenarios_UNREVIEWED.jsonl` | 학습 후보 250개 |
| `validation/scenarios_UNREVIEWED.jsonl` | 검증 후보 50개 |
| `test/scenarios_UNREVIEWED.jsonl` | 테스트 예약 후보 100개 |
| `run_eval.py` | 지정한 시나리오를 읽어 실제 Qwen 평가 |
| `compare_runs.py` | 동일한 입력·코드·설정으로 실행한 모델 두 개의 전체 대화 결과를 사례별로 비교 |
| `build_fault_probes.py`, `diagnostic/` | 기존 400건과 분리한 미검수 대상 선택·API 장애 진단 사례 10건 |
| `audit_coverage.py` | 분할별 대상 선택·오류·장애 사례와 검수 원본 현황 보고 |
| `review.html` | 실행 결과 JSONL을 첨부해 대화·모델 원문·판정 확인 |
| `guards.py` | 발화 근거·날짜·질문·쓰기 검사 |
| `question_rubric.json` | 대화별 기대 질문 종류 |
| `runtime/` | 평가기가 사용하는 action 검증·대화 제어부·메모리 일정 저장소 등 실행 의존 파일 |

`runtime/schedule_assistant/prompt.txt`는 내부 기반 클래스가 읽는 기본 프롬프트다. 실제 Qwen 평가에는 최상위 `prompt.txt`를 사용한다. 실행 의존 파일을 제외하면 import 또는 실행이 실패한다. 이 폴더는 DATA 내부 재현 실험이며 저장소의 `ai/`·`backend/` 구현을 변경하지 않는다.

### 프롬프트 v2.5 제안 — 2026-10-16

등록의 `required_question=null` 조건을 `intent=create`와 확인된 업무·시각 조건으로 한정했다. 조회 범위 규칙도 조회·수정·삭제의 조회 전 단계에 한정했다. 후보가 1건일 때의 `CONFIRM_UPDATE`/`CONFIRM_DELETE`와 다중 후보의 `SELECT_TARGET`을 완전한 JSON 예시로 구분한다. action·필드 계약과 제어부는 변경하지 않았다.

기존 20-step 시험 및 23/114 → 43/114 비교는 **v2.4 프롬프트** 결과다. v2.5 개선 효과는 아직 측정하지 않았으며 기존 결과를 새 버전의 성능으로 표시하지 않는다. 평가기 버전 `eval-v2.7-proposed`는 프롬프트 버전과 별개이며 원출력 action·JSON 일치를 가드 보정 후 시스템 결과와 분리하고 주입된 장애 사건을 기록한다.

학습 JSONL에는 생성 당시 시스템 프롬프트가 포함된다. 미검수 재현 시험은 `build_dataset.py`, 승인본은 아래 `export_training.py`로 새 입력을 생성하고 별도 출력 폴더에서 토큰 길이·마스크를 다시 검사한다. 과거 학습 폴더·어댑터·결과는 보존한다. 학습 전후 비교는 동일한 v2.5 프롬프트를 사용해야 한다. 프롬프트 정리와 승인본 변환 도구 구현은 검수 승인·본 학습·평가기 개선의 완료를 뜻하지 않는다.

v2.5 JSON 예시 8개는 기존 action 검증기와 해당 상황의 동적 schema를 통과했다. 기존 651/114행의 시스템 프롬프트만 메모리에서 교체한 tokenizer 검사에서는 최대 train 4,183·validation 3,854토큰이며 마지막 응답 마스크가 일치했다. 기존 3,328 설정은 모든 행에 부족하다. 현재 준비 도구의 256토큰 단위 올림 방식으로 새 설정은 4,352가 필요하다. 이는 파일 재생성이나 GPU 학습 결과가 아닌 읽기 전용 검사이며, 예시 추가로 문맥 길이와 연산 부담이 증가한다. 새로 생성한 실제 입력에서도 다시 확인한다.

CSV는 JSONL 원본을 수정하지 않고 만든 검토용 사본이다. `scenario_id`와 `turn_number`로 원래 대화 순서를 확인할 수 있다. `model_outputs_json`, `expect_json`, 초기·최종 일정 열은 중첩 구조를 JSON 문자열로 보존한다. 학습·평가 입력과 검수 상태의 기준은 계속 JSONL 원본이다.

### 데이터 문구 개정 — 2026-10-16

이전 `qwen-dataset-400-v0.1.1-proposed`에서 제목 치환 후 잘못 붙은 조사(train 27건), `분야` 시각 표현(validation 3건·test 10건), `모레이야` 날짜 표현(test 10건), 제목 변경 문장의 인용부호 뒤 조사(validation 5건·test 15건), `분로` 시각 표현(test 21건)을 고쳤다. 일부 test 사례에서 수정이 겹치므로 당시 변경된 대화는 train 27건·validation 8건·test 39건, 총 74건이었다. 이 문구 교정은 현재 생성본에도 유지된다.

문구가 달라져 JSONL·CSV의 해시가 바뀌었다. 아래의 과거 실제 모델 평가와 20-step 시험 학습 수치는 개정 전 입력의 기록이다. 개정본의 전후 비교에는 두 모델을 같은 새 입력으로 다시 평가해야 한다. 고정 시범 표본 30건 중 `validation-outbound-update-title_alarm-03`의 문구가 바뀌었다. 이미 만든 `reviewed/` 검수 원본은 새 초안과 해시가 다를 수 있으므로 자동 승인하거나 덮어쓰지 말고 해당 사례를 대조한다.

### 기본 발화·검증 범위 개정 — 2026-10-16

현재 생성본은 `qwen-dataset-400-v0.1.2-proposed`다. 전체 400건은 유지하고 반복 train 변형 20건을 줄여 train 250·validation 50·test 예약 100건으로 재분할했다. 날짜·시각·업무가 모두 있는 직접 등록을 train 10·validation 5·test 5건 넣고, validation에는 별도 대화 가족의 다중 후보 삭제 선택 5건을 넣었다. train 변형의 첫 요청 동사를 일부 바꾸고 기준 시각 밤 9시 사례를 train 60·validation 20·test 47건 포함했다. train의 고유 첫 발화는 이전 88/260에서 109/250으로 늘었지만 실제 음성 표현의 대표성을 입증하지 않는다. 검토용 CSV는 400건 JSONL에서 매번 다시 만들며 학습·평가 기준은 계속 JSONL이다.

과거 validation 40건의 실제 Qwen 점수와 기존 651/114행의 tokenizer 검사는 수정된 validation 50건·학습 행 612/129건의 결과가 아니다. 시범 승인본도 새 초안과 해시·분할을 대조하기 전까지 자동 승계하지 않는다. 새 정답은 모두 `proposed_unreviewed`이며 실제 Qwen 기준선과 tokenizer 길이·마스크 검사는 재실행해야 한다.

### 다중 후보 대상 선택 규칙 — AI·DATA 로컬 시범

수정·삭제 조회 결과가 1건이면 기존 `CONFIRM_UPDATE`/`CONFIRM_DELETE`에 조회된 ID를 넣는다. 2~10건이면 `SELECT_TARGET`을 출력한다. 삭제 형식은 `{"action":"SELECT_TARGET","message":null,"request":{"operation":"delete"}}`, 수정 형식은 `request={"operation":"update","changes":{...}}`다. `SELECT_TARGET`에는 `targetId`가 없다. 제어부가 번호를 보여주고 사용자의 선택을 실제 ID로 바꿔 대상·변경값을 확인한 뒤 승인 시에만 쓴다. 잘못된 번호는 다시 묻고, 취소하면 쓰지 않는다. 10건 초과는 날짜·제목으로 범위를 좁혀 다시 조회하도록 묻는다.

이번 초안의 `SELECT_TARGET`은 62건(train 32·validation 5·test 예약 25)에 있다. `other-update-different-target`처럼 2번을 선택한 뒤 변경 시각을 다시 말하면 선택된 일정의 이름이 명시된 경우에만 그 후보를 재사용하고 변경값을 다시 확인한다. 400건 초안은 재생 통과했다. 이전 시범 30건은 사용자 위임 Codex 검토로 별도의 로컬 승인본을 구성했으며 독립 검수와 구분한다. AI 영역의 로컬 시범 제어부·검증기·프롬프트도 같은 다중 후보 action 형식을 지원한다. 공유 출력 계약은 여전히 `TBD-AI`이며, 학습 어댑터의 AI 실행기 연결이나 실제 모델 평가가 완료된 것은 아니다. 기존 단일 후보 흐름과 5건 구형 시범 데이터는 별개로 유지한다.

학습 행·검수표·정답 재생 미리보기·실행 로그는 Git에 올리지 않고 필요할 때 생성한다. 기존 시범 파일은 별도로 복사하지 않는다. 실행 로그와 개인 검수 기록은 공유 대상에서 제외한다.

## 분할과 한계

| 분할 | 대화 | 그룹 | 생성되는 학습/참조 행 |
|---|---:|---:|---:|
| train | 250 | 28 | 612 |
| validation | 50 | 10 | 129 |
| test | 100 | 11 | 327 |

각 업무 카테고리(INBOUND·INVENTORY·OUTBOUND·RETURN·OTHER)는 train 50개·validation 10개·test 20개다. train의 등록/조회/수정/삭제는 74/56/64/56개, validation은 15/10/10/15개, test는 30/20/25/25개다.

원본 30개와 추가 train 변형 210개는 같은 상황의 어휘·날짜·승인 표현 변형을 포함한다. validation/test는 새 대화 조합으로 작성했다. 같은 원본/조합의 변형은 같은 그룹과 분할에 배치했다. 그룹 ID와 동일 입력·정답 행의 분할 간 중복은 없으나 의미상 유사성과 실제 현장 대표성은 사람 검수가 필요하다. test는 검수 전 예약 초안이며 아직 잠근 독립 시험본이 아니다.

400건의 기대 응답 종류를 점검하면 `error`·`stale`은 0건이다. validation 50건에는 `SELECT_TARGET` 5건이 있다. 별도 `diagnostic/`에는 대상 선택 2건, 오류 6턴·stale 1턴, GET/POST/PATCH/DELETE 장애 주입 7건이 있다. 등록 응답 유실 후 같은 멱등키 재시도, 등록 거부 후 새 요청, 수정·삭제의 커밋 전 거부와 커밋 후 응답 유실, 확인 전 외부 변경을 포함한다. 10건 모두 **미검수 DATA 진단용**이며 validation/test 점수에 합치지 않는다. 장애 경로의 독립 평가 채택은 분할·누출 점검과 사람 검수 후 결정한다.

진단 10건 중 9건의 첫 사용자 발화가 train과 같다. 기존 train 사례에서 복사한 장애 진단을 validation으로 옮기면 독립 분할로 볼 수 없다. 공식 validation의 대상 선택 5건은 새 발화 가족으로 작성했으며, 장애 경로를 포함하려면 train과 겹치지 않는 새 가족을 별도로 작성하고 정답·채점 경로를 검수해야 한다. 기존 40건의 과거 실행 점수와 새 50건의 점수는 구분한다.

```bash
python3 build_fault_probes.py
python3 audit_coverage.py
python3 run_eval.py --scripted --scenarios diagnostic/scenarios_UNREVIEWED.jsonl --rubric diagnostic/question_rubric.json --output-dir runs/diagnostic_scripted_001
```

첫 명령은 진단 초안을 다시 만들고 정답 재생까지 확인한다. 두 번째 명령의 `gaps`는 공식 validation과 test에 남은 공백을 표시한다. `--require-complete`는 공백이 남으면 실패한다. 마지막 명령은 모델 없이 평가기의 장애 경로·원출력 지표를 확인한다. 실제 모델로 진단할 때는 `--scripted`를 빼고 별도 출력 폴더를 사용한다. 이 결과도 검수 전 탐색 결과다.

## 실행

Python 3.10 이상을 사용한다. 아래 명령은 **이 README가 있는 폴더**에서 실행한다.

### 모델 없이 실행·HTML 연결 먼저 확인

```bash
python3 run_eval.py --scripted --output-dir runs/smoke_001
```

`review.html`을 브라우저로 열고 `runs/smoke_001/review_cases.jsonl`을 선택한다. 기본 validation 50개의 **제안 정답 재생 미리보기**이며 실제 Qwen 응답이 아니다. Python 표준 라이브러리만 사용하며 별도 pip 설치는 필요 없다.

### 제안 정답 재생 및 산출물 생성

```bash
python3 build_dataset.py
```

모델과 운영 DB를 호출하지 않는다. 400개 시나리오를 재생하고 형식·그룹 분리·중복을 검사한다. 학습 후보 JSONL, 검수표, 정답 재생 미리보기, manifest는 로컬에 생성된다. 이 명령은 미검수 초안을 다시 쓴다. 사람의 정답 수정과 승인 상태는 다음 절의 `reviewed/` 원본에 기록한다.

### 검수한 정답 보존 및 승인본 출력

```bash
python3 review_sources.py start --case in-create-past
```

위 명령은 초안과 해당 질문 rubric을 `reviewed/in-create-past.json`에 한 번만 복사한다. 이미 있는 파일은 덮어쓰지 않는다. 검수자가 이 파일의 `scenario`와 `question_rubric`을 수정하고, 근거를 확인한 뒤 `scenario.label_status`를 `reviewed`로 바꾼다. 보류 중이면 `proposed_unreviewed`, 기각이면 `rejected`로 둔다. 개인 검수 메모와 실제 사용자 데이터는 이 원본에 넣지 않는다.

```bash
python3 review_sources.py check
python3 review_sources.py export --output-dir runs/reviewed_001
```

`check`는 모든 검수 원본의 형식·질문 rubric을 검사하고, `reviewed` 상태인 사례의 대화를 재생한다. 시작 당시 초안이 다시 생성되며 바뀐 경우에는 오류를 내고 수동 대조를 요구한다. 검수 원본 자체는 수정하지 않는다. `export`는 승인된 사례만 새 출력 폴더에 분할별 `scenarios_REVIEWED.jsonl`과 `question_rubric_REVIEWED.json`으로 복사한다. 출력 폴더가 이미 있으면 중단한다. 승인 사례가 없으면 내보낼 수 없다. 분할별 승인 건수는 다를 수 있고, 승인 0건인 분할 파일은 비어 있다.

승인본을 평가할 때는 해당 분할 파일과 rubric을 `run_eval.py --scenarios ... --rubric ...`에 지정한다. `reviewed/`에는 개인정보를 제거한 공유 가능 검수 원본만 보관한다. 현재 검수 schema와 보관 방식은 DATA 내부 실험이며 팀의 공유 계약으로 확정된 것은 아니다.

### 검수 정답을 학습 입력에 반영

`review.html`의 판정 JSON이나 모델의 before/after 응답을 학습 정답으로 자동 복사하지 않는다. 사람이 확인한 수정은 `reviewed/사례ID.json`의 `scenario.turns[].model_outputs`에 반영한다. 정답이 바뀌면 관련 `expect`, `expect_final`, `question_rubric`도 일치하도록 확인하고 그 뒤에만 `label_status`를 `reviewed`로 바꾼다.

이 폴더에서 실행한다. 먼저 train과 validation 각각 승인 사례가 있어야 한다.

```bash
python3 review_sources.py check
python3 export_training.py --output-dir "$HOME/qwen-reviewed-data-001"
python ../mlx_smoke_v0.1/prepare.py \
  --reviewed-data "$HOME/qwen-reviewed-data-001" \
  --model "$HOME/qwen-models/Qwen3-4B-4bit" \
  --output "$HOME/qwen-reviewed-smoke-001"
```

마지막 명령은 MLX 학습 가상환경에서 실행한다. 여기까지는 모델 학습·추론을 실행하지 않는다. `prepare.py`가 PASS를 출력하면 새 입력과 마지막 assistant 마스크 검사가 완료된 것이다. 생성 설정은 여전히 **20-step 시험용**이다. 전체 학습 횟수와 평가 계획은 별도로 정해야 한다.

변환 도구는 `reviewed/` 원본을 직접 읽어 초안 해시·기존 분할·그룹을 검증한다. `review_sources.py export`의 시나리오 출력은 평가용이며 학습 변환의 필수 중간 단계는 아니다. 승인된 정답을 메모리 내 제어부로 재생해 실제 모델 호출 시점의 관측 데이터·대화 이력과 마지막 assistant 정답을 묶는다. 수정된 앞선 정답은 이후 턴의 이력에도 반영된다. 승인·취소 등 모델 호출이 없는 턴은 행을 만들지 않는다. 한 턴에 LIST와 후속 판단이 있으면 각각 학습 행이 된다.

출력은 `train.jsonl`, `valid.jsonl`, 각 `*_row_map.jsonl`, `manifest.json`이다. 원본 추적표에는 사례·턴·호출 번호·검수 원본 해시·동적 schema가 들어간다. manifest는 프롬프트·코드·생성 파일의 해시와 승인 대화 수를 기록한다. 같은 분할의 동일 행은 하나로 합치되 모든 출처를 추적한다. 동일 입력의 상충 정답과 train/validation 간 동일 입력은 오류로 중단한다.

미승인·기각 사례와 test는 학습 출력에서 제외한다. 정답 재생·질문 기준·동적 schema 검사에 실패하거나 가드가 정답을 보정하는 사례는 출력을 중단하므로 해당 검수 원본이나 기준을 확인한다. 질문 기준은 기존의 제한된 단어 검사이므로 중단 자체가 사람의 정답이 틀렸다는 뜻은 아니다. 기존 출력 폴더는 덮어쓰지 않으며, 생성 파일이 나중에 바뀌면 `prepare.py --reviewed-data`가 해시 불일치로 거부한다. 정답 변경은 검수 원본에 적용한 뒤 새 폴더로 다시 변환한다.

실제 검수 승인 현황은 `python3 review_sources.py check`로 확인한다. 자동 테스트의 임시 승인 사례는 기능 검증용이며 실제 승인 데이터 수에 포함하지 않는다.

### 30건 시범 검수

`pilot_review_cases_v0.1.txt`는 업무 분류별 등록·조회·수정·삭제 20건, 확인 거절·알림·대상 정정 등 추가 5건, validation 5건을 고정한다. test 사례는 포함하지 않는다. 다음 명령은 정답 재생 결과를 새 로컬 폴더에 만들며 실제 Qwen을 호출하지 않는다.

```bash
python3 run_eval.py --scripted --scenarios train/scenarios_UNREVIEWED.jsonl --additional-scenarios validation/scenarios_UNREVIEWED.jsonl --case-file pilot_review_cases_v0.1.txt --output-dir runs/pilot_review_001
```

`review.html`에서 `runs/pilot_review_001/review_cases.jsonl`을 열어 사용자 발화, 실제 표시 답변, action, API 호출, 최종 상태를 확인한다. 브라우저에서 내보낸 판정 JSON은 개인 검수 기록으로 로컬에 보관한다. 정답 수정·승인 사례는 `review_sources.py start --case 사례ID`로 만든 해당 원본 JSON에 반영하고 `check`를 실행한다. 자동 재생의 `provisional_match`는 검수 승인이나 Qwen 정확도를 뜻하지 않는다.

시범 검수에서 채점 오판·허용 경로 차이를 기록하고 기준을 고친 뒤 같은 사례를 재실행한다. 현재 평가기는 `ANSWER` 조회 문장을 정답 재생의 표시 문장과 대조하고, 실제 조회 일정의 제목·시각이 사용자에게 표시됐는지도 별도로 확인한다. 자유로운 `ASK` 표현은 관련 단어와 질문·요청 형태를 함께 검사하지만 한국어 의미 판정은 아니므로 사람 판정을 병행한다. 시범 검수와 기준 고정이 끝나기 전에는 test를 독립 시험본으로 잠그지 않는다.

### 실제 Qwen 평가

Ollama가 실행 중이고 로컬에 `qwen3:4b`가 있어야 한다. 기본 입력은 수정된 validation 50개다.

```bash
python3 run_eval.py --model qwen3:4b --scenarios validation/scenarios_UNREVIEWED.jsonl --rubric question_rubric.json --output-dir runs/validation_001
```

매번 새 출력 폴더명을 사용한다. 결과는 `runs/validation_001/review_cases.jsonl`이다. 함께 제공한 `review.html`을 브라우저로 열고 해당 파일을 첨부하면 된다. GitHub 파일 보기 화면이 아닌 내려받은 HTML을 연다. 로컬 생성된 `validation/review_cases.jsonl`은 정답 재생 미리보기이며 실제 모델 결과와 구분한다.

전체 400개도 입력 경로를 바꿔 실행할 수 있다.

```bash
python3 run_eval.py --model qwen3:4b --scenarios scenarios_all_400_UNREVIEWED.jsonl --rubric question_rubric.json --output-dir runs/all_400_001
```

단, 독립 테스트를 남겨두려면 개발 중에는 validation만 사용한다. test 결과를 보고 모델·프롬프트를 조정하면 새로운 독립 시험본이 필요하다.

### 동일 조건의 전체 대화 전후 비교

로컬 `reviewed/` 원본에서 승인된 validation을 내보낸 뒤, 같은 시나리오·rubric·코드로 기본 모델과 어댑터 모델을 각각 실행한다. 두 모델은 Ollama에 별도 이름으로 설치되어야 한다. 전달받은 PEFT 파일을 `run_eval.py`가 직접 로드하지는 않는다. 아래 명령은 **이 README 폴더**에서 실행하며 실행 사이에 코드·입력·프롬프트·옵션을 변경하지 않는다.

```bash
python3 review_sources.py export --output-dir runs/reviewed_eval_001
python3 run_eval.py --model BASE_MODEL --scenarios runs/reviewed_eval_001/validation/scenarios_REVIEWED.jsonl --rubric runs/reviewed_eval_001/question_rubric_REVIEWED.json --output-dir runs/base_001
python3 run_eval.py --model ADAPTER_MODEL --scenarios runs/reviewed_eval_001/validation/scenarios_REVIEWED.jsonl --rubric runs/reviewed_eval_001/question_rubric_REVIEWED.json --output-dir runs/adapter_001
python3 compare_runs.py --before runs/base_001 --after runs/adapter_001 --output runs/paired_001.json
```

비교기는 두 실행이 실제 모델 응답인지, 사례 순서·상태와 입력 파일·프롬프트·코드 해시·생성 설정이 같은지, Ollama 모델 digest가 서로 다른지 검사한다. `provisional_match`는 가드가 적용된 DATA 시범 시스템의 대화 성공이다. `raw_action_path_exact`와 `raw_json_path_exact`는 가드 보정 전 모델 응답을 각 호출의 제안 정답과 순서대로 비교하며, 중간 실패로 호출하지 못한 정답도 대화 전체 불일치로 센다. `summary.json`의 원출력 호출 수·일치 수는 도달한 턴과 전체 기대 호출 수를 따로 기록한다. `--scripted` 결과는 비교 대상이 아니다.

미검수 자료로 탐색할 때만 `compare_runs.py --allow-unreviewed`를 사용한다. 승인본 비교도 사람의 정답 검수 범위에 한정한 잠정 결과다. test 100건은 의미상 중복·실패 사례·채점 기준을 별도로 검수하고 고정하기 전까지 최종 점수에 쓰지 않는다. 실제 백엔드, STT·TTS, Orange Pi 성능은 이 메모리 기반 평가 범위 밖이다.

## 확인된 결과

- 오프라인 제안 정답 재생: 400/400 통과. 모델 정확도 100%를 의미하지 않는다.
- 시범 표본 30건의 오프라인 제안 정답 재생: 30/30 통과. 이후 사용자 위임 Codex 검토로 30건을 로컬 승인했으며 독립적인 복수 사람 검수는 아니다.
- 실제 Qwen은 **validation 40개만** 평가했다. 400개 전체를 실제 모델로 평가한 결과는 아직 없다.
- 이전 규칙의 실행 `validation_baseline_001`: 잠정 일치 31/40(77.5%), 검토 필요 9개, 모델 호출 114회. `SELECT_TARGET` 제안 도입 전 결과라 현재 v2.4 프롬프트·제어부의 점수로 재사용하지 않는다.
- 파싱 오류·모델 요청 오류·제어부 crash·조회 필터 자동 보정은 각각 0건.
- 불일치: 완료 처리와 알림 동시 수정 시 알림 누락 5건, 날짜·시각 오류로 등록 차단 3건, 날짜 재질문 1건.

이 수치는 프롬프트·상태 schema·가드·미검수 기준을 포함한 시스템의 잠정 일치율이다. 모델 단독 정확도나 파인튜닝 후 성능으로 해석하지 않는다. 원문 로그는 로컬에 보관한다.

## 학습 전

미검수 자료의 초기 20-step 시험과 시범 승인본 63/13행의 후속 20-step 시험을 별도 기록했다. 후속 비교의 JSON 전체 일치는 5/13→5/13으로 개선이 확인되지 않았다. 다음 검수 원칙을 충족한 본 학습이나 공식 성능 평가로 취급하지 않는다.

정답 action과 최종 상태를 사람이 검수하고 승인한 train/validation만 사용한다. 모델 오답을 정답으로 복사하지 않는다. 생성된 `messages`에서 마지막 assistant action에만 loss를 적용하고 이전 문맥 전체를 학습 정답으로 처리하지 않는다. 번호 선택·승인·취소처럼 모델을 호출하지 않는 턴에는 가짜 action을 만들지 않는다.

학습 행으로 바꾼 뒤 행 단위로 다시 분할하지 않는다. MLX 시험에서 chat template·loss mask·토큰 길이/잘림을 확인했으며, 범위와 결과는 [시험 보고서](../../../../docs/qwen_smoke_training_results_20261016.md)를 따른다. 다른 모델·토크나이저·데이터 버전에는 별도 확인이 필요하다. 모델 오류가 0건이어야 학습할 수 있는 것은 아니지만, 학습 정답과 평가 기준은 먼저 정확히 검수해야 한다.
