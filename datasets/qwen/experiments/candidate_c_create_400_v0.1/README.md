# Candidate C 일정 등록 400 실험 v0.1

상태: `BE-PROPOSED / DATA·AI·TEAM 확인 전`

이 디렉터리는 Issue #32의 후보 C 일정 등록 데이터 구축과 MLX 비교 평가를 위한
실험 공간이다. 현재 산출물은 일정 등록 400건(시범 30, batch 1~3), 등록이 아닌
요청 87건(batch 4, 5), 잠금 Test 50, 검증 도구와 MLX 학습 6회의 평가 기록이다.
결과 요약은 [`docs/candidate_c_create_training_evaluation.md`](../../../../docs/candidate_c_create_training_evaluation.md)에 있다.

## 현재 범위

- Issue #30의 후보 C 추적 계약을 그대로 재사용한다.
- 일정 등록 시나리오 5개 버킷에서 각 6개, 총 30개를 시범 작성한다.
- 정정 provenance를 v0.1로 유일하게 표현할 수 없는 형태는 최종 adopted 값만 라벨하고
  정정 전 값 식별 지표의 분모에서 분리한다.
- 기존 development 12건과 locked 56건은 학습 데이터로 사용하지 않는다.
- 기존 400개 데이터는 향후 train/validation에서 사례별 재검수 후 최대 83개까지만 재사용할 수 있다.
- 새 Test 50과 locked boundary는 신규 family로 작성하고 같은 lock·개봉·paired run으로 보호한다.

## 상태 표기

- `PROPOSED_UNREVIEWED`: Codex가 생성하고 자동검증만 통과한 상태
- `ADVISORY_REVIEWED`: Claude가 읽기 전용 자문 검토한 상태. DATA 승인 아님
- `CLAUDE_INDEPENDENT_REVIEW`: 사용자가 지정한 Claude 독립 검수 세션이 원문과 라벨을
  전수 확인해 통과한 상태. DATA 담당 확인 전이며 `INDEPENDENT_REVIEWED`가 아님
- `INDEPENDENT_REVIEWED`: 확정된 독립 검수자가 원문과 라벨을 확인한 상태
- `TEAM_CONFIRMED`: 팀 합의가 명시적으로 기록된 상태

시범 데이터는 현재 `PROPOSED_UNREVIEWED`다. 파일명이나 메타데이터에 `reviewed`를
사용하지 않는다.

`ADVISORY_REVIEWED`는 검토 보고서의 상태로만 사용할 수 있으며 데이터 행의
`reviewState`로 쓰지 않는다. Claude 검토 뒤에도 데이터 행은 독립 검수 전까지
`single-review proposed`를 유지한다.

## 실행

```bash
cd <server-repository-root>
PYTHONDONTWRITEBYTECODE=1 python3 \
  datasets/qwen/experiments/candidate_c_create_400_v0.1/pilot/build_pilot.py
PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python \
  datasets/qwen/experiments/candidate_c_create_400_v0.1/validator.py
PYTHONPATH=backend PYTHONDONTWRITEBYTECODE=1 backend/.venv/bin/python -m unittest discover \
  -s datasets/qwen/experiments/candidate_c_create_400_v0.1 \
  -p 'test_validator.py' -v
```

`validator.py`는 production private Decision 경로를 읽기 전용으로 호출하므로
`pydantic` 등 backend 개발 의존성이 설치된 `backend/.venv`와 `PYTHONPATH=backend`가
필요하다. 별도 worktree에는 `.venv`가 자동 복사되지 않으므로 기존 검증 venv를
사용하거나 해당 worktree에서 lock 파일 기준으로 venv를 먼저 구성한다.

검증은 모델·서버·DB·네트워크를 사용하지 않는다. `jsonschema` 패키지가 현재
고정 환경에 없으므로 semantic oracle은 기존 schema의 해시를 고정하고 해당 schema가
사용하는 키워드 전체를 표준 라이브러리 기반 보조 검사기로 검증한다. 후보 C 출력은
Issue #30의 실제 validator와 common gate v0.4를 직접 호출한다. 보조 검사 결과는
오류 0건이지만 공식 JSON Schema 구현을 사용한 검증과 동일하다고 과장하지 않는다.

## 100건 생성 보조 기능

상태: `BE-PROPOSED`. 100건 데이터는 아직 생성하지 않았다.

`pilot/spec_checks.py`는 손으로 작성한 spec을 읽기만 하고 파일을 쓰지 않는다.
`build_pilot.py`는 파일을 쓰기 전에 `check_specs`를 실행한다.

- `scheduledAt` 재계산: 최종 adopted 날짜·시각 span과 `referenceTime`으로 다시 계산해
  spec 값과 다르면 중단한다. 시범 30건 중 값이 있는 28건이 모두 일치한다.
- 제외 표현: 아래 표현은 Issue #30 normalizer가 의도한 값으로 바꾸지 못하므로 새 batch에
  넣지 않는다. 위험한 실행이 아니라 불필요한 재질문·거절이 생기는 공백이다.
  - `EXCLUDED_TIME_PERIOD_WORD`: 밤, 새벽, 자정, 정오, 점심, 낮
  - `EXCLUDED_KOREAN_NUMERAL_HOUR`: `세 시`처럼 한글 숫자 시각
  - `EXCLUDED_YEARLESS_PAST_DATE`: 기준일보다 이른 연도 없는 `M월 D일`(다음 해로 넘기지 않음)
- `batch_quality_errors`: 새 batch의 제목 중복과 기준 시각 쏠림(기본 25% 초과)을 보고한다.
  시범 30건에는 적용하지 않는다(`입고 검수`·`재고 실사` 중복, 기준 시각 28/30).
- `REFERENCE_PRESETS`: 평일 아침·저녁, 이른 아침, 주말, 월말, 윤일, 연말 기준 시각.

제외 표현의 normalizer 보완은 Issue #29 Qwen adapter 구현 때 다룰 BE 후속 항목이다.
보완 전까지 이 표현의 의미 정답(예: `밤 10시`=22:00)은 확정하지 않는다.

동의 뒤에 다른 누락 항목을 묻는 turn은 `missing_information` 질문과
`missing_item_question` fact를 기대한다. 동의 질문과 미지원 안내는 `user_consent`가
해소되기 전에만 기대한다. 거절·결과 turn의 기존 규칙은 바꾸지 않았다.

## Batch 1 (100건)

상태: 데이터 행 `PROPOSED_UNREVIEWED / BE-PROPOSED`, `datasetRole=training_candidate`.
검수 상태 `CLAUDE_INDEPENDENT_REVIEW`(2차 100/100 `ACCEPT`, DATA 담당 확인 전),
검수 이벤트 200건은 `batch1/review-events-v0.1.jsonl`.

- 생성: `batch1/build_batch1.py` → `batch1/conversations-v0.1.jsonl`, `batch1/candidate-c-targets-v0.1.jsonl`
- 검증: `validator.py batch1` → `batch1/training-row-preview-v0.1.jsonl`, `batch1/validation-results-v0.1.json`
- 구성: 직접 등록 22, 누락 후 질문 25, 정정 20, 시간 안전 20, 미지원 동의 13. 신규 작성만 사용했고
  기존 400 재사용·Test는 포함하지 않았다.
- 자동검증: PASS, local accept 140 turn, gate 137 + 예상 거절 3(과거 시각), unsafe 0,
  oracle과 다른 write 값 0, mapping exception 0, 학습 row 미리보기 131행, 입력·target 충돌 0,
  기존 400·dev12·locked56·시범 30 대비 대화·발화 정확 중복 0, Jaccard 0.7 이상 0.
- mutation fixture는 시범 사례 ID에 고정돼 있어 batch에서는 재생성하지 않는다(`mutationScope=pilot_only`).
  gold turn의 안전·값 판정은 시범과 같은 경로로 수행한다.

계약 공백(`TBD-BE/AI`): 미지원 기능 동의를 받은 뒤 다른 누락 항목에 답하는 turn은 모델을 다시
호출한다. 이때 Candidate C target에 이전 turn의 미지원 cue가 남아 있으면 Decision 단계가 동의
질문을 다시 만든다. 위험한 실행은 아니지만 재질문이 반복된다. target에서 cue를 빼야 하는지, backend가
동의 상태를 Decision에 넘겨야 하는지 결정 전까지 이 흐름은 batch에 넣지 않는다. 검증기·생성기의
질문 목적 규칙은 이 흐름을 이미 표현할 수 있다.

### Batch 1 독립 검수 반영

`claude-data-review-b1-a`(001~050), `claude-data-review-b1-b`(051~100)가 100건·target 140개를 전수
검수해 98 `ACCEPT`, 2 `REVISE`로 판정했다. 반영 내용은 다음과 같다.

- 018: `포장재`의 일부를 출고 cue로 잡던 제목을 `포장 작업 점검`으로 교체
- 091: backend 동의 분류기가 `unknown`으로 보던 답변 `등록만 부탁해`를 `등록만 해줘`로 교체
- validator: 모델을 호출하지 않는 동의 답변 turn에서 backend 분류(granted·declined)가
  oracle 결과(success·cancelled)와 다르면 STOP
- batch 전용 태그 보정: 1-turn 대화의 `follow_up_answer` 제거, 연도 없는 날짜의 `relative_time` 추가

후속(`TBD`):
- `training_candidate`인데 `scoring.exclusionReason=development_probe`가 남아 있다. Issue #30 schema
  enum에 학습용 값이 없어 DATA 결정이 필요하다.
- 동의 분류기가 `등록만 부탁해` 같은 동의 표현을 놓친다(재질문 쪽 실패, BE 후속).
- 064 `relative_time` 태그가 정정 전 값(`1월 3일은 말고`)에서 붙었다. 태그 규칙을 adopted 날짜 근거만 보도록
  고치고 다음 재생성 때 태그만 바뀌는지 diff로 확인한다.
- 동의 검사 보강: oracle이 동의 재질문(pending)을 기대하는 경우와 결과·write 수 일치(success↔1,
  cancelled↔0)는 아직 검사하지 않는다. 해당 사례를 넣는 batch 전에 추가한다.
- 다음 batch 편향 보완: category 단어가 제목 맨 앞에 몰림(33/34), 첫 발화 어순 고정, 존댓말 부족,
  정정 문형 반복, 미지원 요청이 항상 뒤에 옴, 어색한 제목(077·079·094), `오전 12시` 비중 제한.

## Batch 2 (100건)

상태: 데이터 행 `PROPOSED_UNREVIEWED / BE-PROPOSED`, `datasetRole=training_candidate`.
검수 상태 `CLAUDE_INDEPENDENT_REVIEW`(`claude-data-review-b2-a`·`b2-b` 100/100 `ACCEPT`, 수정 0, DATA 담당 확인 전),
검수 이벤트 100건은 `batch2/review-events-v0.1.jsonl`.

- 생성: `batch2/build_batch2.py`, 검증: `validator.py batch2`(오염 비교 대상에 시범 30·batch 1 포함)
- 보강: 시험 학습 run 001 실패 원인인 서버 시각 불신 후속(`오늘`, T2 날짜만 답하기, 연도 없는 날짜),
  batch 1 검수 편향(제목 먼저 어순, 존댓말, cue 제목 중간, 미지원 요청 앞, 정정 표지 다양화)
- 생성기가 동의 답변을 backend `classify_consent_answer`로 사전 검사하고, `relative_time` 태그는 adopted 날짜 근거만 본다
- 자동검증: PASS, unsafe 0, 값 불일치 0, mapping 예외 0, 학습 row 미리보기 128행, 오염 정확 중복 0·Jaccard 0.7 이상 0
- 다음 batch 품질 메모: cue가 제목 맨 앞 쏠림(051~100에서 33/39), 연말 기준 `신년` 반복, 첫 발화 날짜 시작 다수,
  불신 후속 T2가 기준 시각과 어긋나는 사례 없음, 동의 granted turn의 write 값·결과-write 수 자동 대조 미구현(TBD)

## Batch 3 (170건)

상태: 데이터 행 `PROPOSED_UNREVIEWED / BE-PROPOSED`, `datasetRole=training_candidate`.
검수 상태 `CLAUDE_INDEPENDENT_REVIEW`(`claude-data-review-b3-a`·`b3-b` 170/170 `ACCEPT`, 수정 0, DATA 담당 확인 전),
검수 이벤트 170건은 `batch3/review-events-v0.1.jsonl`(reviewedAt은 주 세션의 보고 수신 시각).

- 생성: `batch3/build_batch3.py`, 검증: `validator.py batch3`(오염 비교 대상에 시범 30·batch 1·batch 2 포함)
- 구성: 직접 34 / 누락 후 질문 50 / 정정 34 / 시간 안전 32 / 미지원 동의 20
- 보강: run 002 중단 원인(lk09, `그거 하나`를 제목으로 채택). 제목 불분명 사례 26건(누락 22·turn 간 정정 4)을
  지시어(그거·이거·그 건), 수량어(하나·뭐 하나·일정 하나), 존댓말, 시각 먼저 어순, 답변 군말로 다양화.
  누적 제목 불분명 15 → 41건. lk09 원문은 쓰지 않았고 locked 56건은 기존처럼 오염 비교 대상이다
- 검수 편향 보완: cue가 제목 뒤·중간, `신년` 금지(생성기 검사), 군말·제목 먼저 첫 발화,
  불신 후속 T2가 서버 기준 날짜와 다른 사례(`untrusted_conflict_followup`)
- scheduledAt은 최종 adopted 날짜·시각 span에서 `spec_checks.computed_scheduled_at`로 계산하고,
  validator가 production Decision 경로 결과와 대조한다
- 자동검증: PASS, unsafe 0, contract gap 0, 학습 row 미리보기 224행, 앞선 산출물 불변
- 검수 지적(수정 대상 아님, 후속):
  - AUTO scheduledAt은 생성기 검사가 같은 함수로 재계산하는 자기 비교다. batch 3은 검수자 손 계산 170/170으로 보완했고,
    이후 데이터는 사람 계산값을 따로 둔다
  - 연말 단어(연초·연말·새해·시무식)가 연말 기준 시각에만 나옴, cue가 제목 맨 뒤인 사례 0
  - `출하`(087)·`납품`은 cue 목록에 없어 OTHER. 동의어 정책은 BE·DATA 후속(TBD)
  - `오전 12시`(140)=00:00은 normalizer와 같지만 사용자 오해 여지, 002 문형이 수정 요청처럼 읽힐 여지,
    021 제목의 `조회`, 084가 b2-004와 문형 근접
  - 동의 granted turn write 값·결과-write 수 자동 대조 미구현(batch 2와 같은 TBD)

## Batch 4 (50건, 등록이 아닌 요청)

상태: 데이터 행 `PROPOSED_UNREVIEWED / BE-PROPOSED`, `datasetRole=training_candidate`.
검수 `CLAUDE_INDEPENDENT_REVIEW`(`claude-data-review-b4` 1차 ACCEPT 41, REVISE 2, CONTRACT_GAP 7 / 2차 50 ACCEPT, DATA 담당 확인 전),
검수 이벤트 100건은 `batch4/review-events-v0.1.jsonl`.

- 목적: run 004 중간 저장본이 lk28(수정 요청)을 등록으로 출력한 unsafe_accept 대응. 학습 데이터가 모두 등록이었다
- 생성: `batch4/build_batch4.py`. 구성: 수정 20, 삭제 15, 조회 15
- 생성 단계 검사: `contracts.validate_candidate_c`, locked56과 Test 50 대비 문장 유사도 0.3 미만, 제목 완전 일치 금지
- 검수 반영: locked 제목과 같거나 비슷한 제목 교체, 조회 category span은 "분류가 X인" 관례(lk20, lk24)로 통일
- BE TBD: 날짜만 바꾸는 수정(기존 시각 유지)의 매핑 없음(006~010), 수정과 삭제 대상 time, 조회 조건 time 허용 여부(028),
  "피킹", "포장"을 조회 category로 볼지(050)

## Batch 5 (37건, 조회와 수정 보강)

상태: 데이터 행 `PROPOSED_UNREVIEWED / BE-PROPOSED`, `datasetRole=training_candidate`.
검수 `CLAUDE_INDEPENDENT_REVIEW`(`claude-data-review-b5` 1차 ACCEPT 27, REVISE 8, CONTRACT_GAP 1 / 2차 37 ACCEPT, DATA 담당 확인 전),
검수 이벤트 73건은 `batch5/review-events-v0.1.jsonl`.

- 목적: run 005가 lk23(조회의 완료 조건 "아직 안 끝난" 누락)에서 멈춘 unsafe_accept 대응
- 생성: `batch5/build_batch5.py`. 구성: 조회 22(완료 조건, 앞으로 남은, cue가 든 제목, 분류 조건), 수정 15(대상 날짜와 바꿀 날짜, category 변경, 완료 표시, 바꿀 값 없음)
- 생성 단계 검사: batch 4 검사에 더해 `common_gate_v0_2` 역할 검사, 문장 유사도 0.35, 제목 포함 관계와 Jaccard 0.35
- 검수 반영: 제목 교체, "분류가 X인" 관례, 004 "완료 안 된"을 "미완료"로 변경(아래 BE 버그 때문)
- 검수자 경고: 001, 009, 012, 013, 019, 020, 022~026, 034~036, 037은 locked 문형에 제목만 바꾼 형태라 locked 점수가 문형 암기로 높아질 수 있다
- BE 버그(TBD): `dry_mapping._completed_from_texts("완료 안 된")`이 True(완료됨)로 반대 해석

## Batch 6 (65건, 다음 턴 정정)

상태: 데이터 행 `PROPOSED_UNREVIEWED / BE-PROPOSED`, `datasetRole=training_candidate`.
검수 `CLAUDE_INDEPENDENT_REVIEW`(`claude-data-review-b6` 1차 ACCEPT 58, REVISE 7 / 2차 65 ACCEPT, DATA 담당 확인 전),
검수 이벤트 130건은 `batch6/review-events-v0.1.jsonl`.

- 목적: run 006이 멈춘 lk52(후속 턴 "모레 말고 글피로 바꿔 줘"의 정정 누락) 대응. 기존 학습 509행에는
  다음 턴에서 정정 표지로 앞 값을 고치는 사례가 0건이었다. 2026-10-29 run 006 모델에 되묻는 중 정정하는
  문형 13개를 넣어 보니 1개만 맞았다
- 전제: 모든 사례는 첫 턴에서 시간, 날짜 또는 제목을 되묻는 상태(등록 보류)에서 정정한다. 등록이 끝난 뒤의
  정정은 새 수정 요청이므로 넣지 않았다(annotation guide 5절)
- 생성: `batch6/build_batch6.py`, 검증: `validator.py batch6`(오염 비교 대상에 시범 30, batch 1~3, Test 50, locked 56 포함)
- 구성(모두 `correction` 버킷, 묶음당 5건):
  - 시간을 답하며 날짜 정정: 말고, 아니(앞 값 반복 없음), 아니라(존댓말), 대신, 말고(존댓말) 25
  - 날짜를 답하며 시간 정정: 말고, 아니 10
  - 제목 되묻기에 답하며 시간 또는 날짜 정정 10
  - 시간을 답하며 제목 정정: 말고, 아니 10
  - 3턴(날짜, 시간 순으로 되묻기)에서 날짜 정정 5
  - 표지 없는 정정("날짜는 글피로 바꿔 줘") 5: 새 값만 근거로 두고 `excluded_cross_turn_without_marker`
- scheduledAt은 사례마다 손으로 계산해 적고, 생성기가 `spec_checks.computed_scheduled_at`과 다르면 멈춘다(65/65 일치)
- 자동검증: PASS, unsafe 0, contract gap 0, 학습 row 미리보기 135행, 대화와 발화 정확 중복 0, Jaccard 0.7 이상 0
- 새 backend 재생(정답 출력을 근거 처리 경로에 넣음): 62건은 정답 출력 그대로 등록. 표지 없는 정정 3건은
  "바꿔/변경/옮겨" 때문에 "새로 등록할까요?" 확인을 한 번 더 묻는다(BE 후속, 위험 없음)
- 검수 반영: locked lk52와 같은 구절이나 값 쌍("모레 말고 글피로")과 문형을 쓰던 001, 053, 056, 061, Test 50 t50-027
  구절("모레 대신 글피")을 쓰던 016을 다른 날짜 쌍과 문형으로 교체. 029, 030은 "아니" 뒤에 "시간은"을 넣어
  바로 앞 날짜 답을 취소하는 말로 들리지 않게 함
- 검수자 메모(후속): 날짜 정정이 대부분 하루 차이, 두 번째 턴의 약 40/65가 "답 + 말고/아니라/대신 + 새 값" 모양,
  정정이 답보다 앞에 오는 사례와 두 값을 함께 고치는 사례, 분류가 바뀌는 제목 정정이 적거나 없음.
  "4월 6일. 아니다, 오후 5시로"처럼 다른 slot 답 뒤의 단독 "아니"(027, 028, 047~050)는 의미가 풀려 통과했지만
  모델이 방금 답한 값을 버리는 쪽으로 배울 위험이 있어 짧은 확인 학습에서 본다

## MLX 시험 학습

상태: `BE-PROPOSED` 탐색 시험. 기준과 판정 규칙은 실행 전에 `mlx_trial/preregistration-v0.*.json`에 고정했다.

상태: `BE-PROPOSED` 탐색 시험. 기준과 판정 규칙은 실행 전에 `mlx_trial/preregistration-v0.1.json`에 고정했다.

- `mlx_trial/prepare_trial.py`: 검수 이벤트와 conversations 해시를 확인한 뒤 family 단위로
  train 112·valid 19행을 나누고, 마지막 assistant mask와 토큰 길이를 검사해 `trial.yaml`을 만든다.
- `mlx_trial/mlx_eval_c.py`: 로컬 `mlx_lm.server`를 호출해 Issue #30 locked 56건을
  `ollama_eval_v0_3.evaluate_case` 후보 C 경로 그대로 판정한다. unsafe_accept가 나오면 즉시 멈추고 재개하지 않는다.
- `mlx_trial/test_mlx_trial.py`: 모델 없이 정답 출력은 unsafe 0, 무의미 출력은 create 정답 0인지 검사한다.
- 실행 산출물(모델 view, adapter, 평가 행)은 저장소 밖 run 디렉터리에 둔다. 평가 행은 원문 대신 해시만 남긴다.
- MLX 결과는 Issue #30 Ollama 결과와 비교하지 않는다(runtime·양자화·출력 형식 강제가 다름).
  locked 56건 중 6건은 모델을 호출하지 않는 backend 단독 사례라 base·adapter가 같다.

회차별 결과(locked56, 첫 unsafe_accept에서 중단):

| run | 사전 등록 | 학습 행 | 주요 변경 | 멈춘 사례 | 진행 | 등록 정답 |
|---|---|---:|---|---|---:|---:|
| 001 | v0.1 | 112 | batch 1 | lk04 | 4 | 0/4 |
| 002 | v0.2 | 241 | batch 2 추가 | lk09 | 9 | 5/9 |
| 003 | v0.3 | 465 | batch 3 추가 | lk09 | 9 | 5/9 |
| 004 (600번째 저장본) | v0.4 | 465 | 16 layers, lr 1e-4 | lk28 | 28 | 7/14 |
| 005 | v0.5 | 472 | schema 키 순서, 검증 family 확대 | lk23 | 23 | 10/14 |
| 006 | v0.7 | 509 | batch 4, 5 추가 | lk52 | 52 | 11/20 |

- run 001~003의 공통 원인은 정답 JSON의 알파벳 키 순서였다. `prepare_trial.py`의 `targetKeyOrder: schema`로 Issue #30 schema 순서를 따른다
- run 004 본 학습은 배터리 부족으로 중단되어 600번째 저장본을 평가했다
- run 006은 등록이 아닌 32건에서 unsafe 0이었고, lk52(후속 턴 "모레 말고 글피로 바꿔 줘"의 정정 누락)에서 멈췄다
- 모든 run이 locked56을 완주하지 못해 학습 모델의 Test 50 평가는 없다
- v0.6은 run 006 첫 시작분에 쓰였고 운영자 재시작으로 중단되어 판정에 쓰지 않았다. v0.7은 중간 검증을 끄는 설정만 다르다

## 중단 조건

- 시범 데이터에서 새 `CONTRACT_GAP`이 발견됨
- 후보 C local validator 또는 common gate 결과가 기대와 다름
- 기존 400·development 12·locked 56과 정확 중복 또는 미검토 고유사 발견
- training row와 evaluator renderer 입력 bytes가 다름
- mutation의 실제 안전 분류가 사전 기록한 기대값과 하나라도 다름
- gold turn에서 oracle과 다른 write 값, superseded 실행 또는 mapping exception이 1건 이상 발생

위 조건이면 400개 확장 전에 멈추고 계약 또는 guide 결정을 먼저 받는다.

현재 시범 결과는 `pilot/validation-summary-v0.1.md`에 정리돼 있다. Claude 1차
검토의 필수 수정 4건을 반영한 자동검증 결과는 `READY_FOR_INDEPENDENT_REVIEW`다.
이는 DATA 독립 검수 완료나 400건 확장 승인을 뜻하지 않는다.

2차 재검수에서 발견한 값 수준 오류도 반영했다. gold pending write는
oracle의 title·scheduledAt·category와 비교하고, 정정 전 값 실행 mutation은
unsafe로 분류한다. 같은 slot에 adopted 값을 둘 이상 두면, 서로 다른
정규화 값이거나 하나라도 정규화 불가·모호한 경우 create profile이 fail-closed 한다.
Issue #30 v0.1 normalizer의 배열 순서 의존은
production 수정없이 BE 후속 항목으로만 기록한다.

검증 JSON의 `mutationUnsafeAccepts`는 의도적 unsafe 변형을 harness가 잡아낸
건수이고, `mutationFinalAccepts`는 mutation을 정상·정답 accept로 판정한 건수다.
두 지표의 의미는 `validation-results-v0.1.json` 안의 `metricDefinitions`에도 기록한다.

DATA 독립 검수자 `claude-data-review-01`은 30/30대화·42/42 target을 전수
검수하고 28 `ACCEPT`, 2 `REVISE`로 판정했다. case 18의
`questionPurpose`와 case 21의 실패 `requiredResponseFacts`를 수정했고, 생성기와
validator에 동일 회귀 규칙을 추가했다. 같은 검수자가 2차 재확인에서 30건 모두
`ACCEPT`로 판정했고, 대화별 검수 이벤트 60건(1·2차)을
`pilot/review-events-v0.1.jsonl`에 기록했다. 시범 30건의 검수 상태는
`CLAUDE_INDEPENDENT_REVIEW`(DATA 담당 확인 전)이며, 데이터 행의 `reviewState`는
계속 `single-review proposed`다.

## Test 50 (신규 family, 학습·조정 금지)

상태: 데이터 행 `PROPOSED_UNREVIEWED / BE-PROPOSED`, `datasetRole=locked_comparison`(Issue #30 schema enum 재사용).
검수 `CLAUDE_INDEPENDENT_REVIEW`(`claude-data-review-t50` 1차 42 ACCEPT, 8 REVISE / 2차 50 ACCEPT, DATA 담당 확인 전),
검수 이벤트 100건은 `test50/review-events-v0.1.jsonl`. **잠금**: `test50/lock-manifest-v0.1.json`(2026-10-28 12:42).
평가 대상은 role이 아니라 잠금 파일의 경로와 해시로 고르고, 결과는 locked56과 따로 보고한다.

- 생성: `test50/build_test50.py`, 검증: `validator.py test50`(오염 비교 기존 400·dev12·locked56·시범·batch 1~3)
- run 003 결과를 읽기 전에 작성했다. family는 모두 신규(`t50_` 접두, family당 최대 3), 학습 제목·family 재사용 금지를 생성기가 검사
- scheduledAt은 사람이 계산한 값을 적고 `check_specs`가 span 재계산과 대조(batch 3 검수 지적 반영)
- 구성: 직접 10 / 누락 후 질문 13(제목 불분명 4) / 정정 10(turn 간 제목 불분명 1) / 시간 안전 10 / 미지원 동의 7
- `eval-row-preview-v0.1.jsonl`은 평가 입력 확인용이며 학습에 쓰지 않는다(`prepare_trial.py`는 batch 1~3만 읽음)
- 자동검증: PASS, unsafe 0, contract gap 0, 앞선 산출물 불변
- 1차 검수 반영: 모든 행이 `excludedFromModelScore=true`(시범 생성 함수 고정값)라 평가에서 빠질 위험 → 채점 포함으로 바꾸고
  notes에 `split=test50` 추가, validator가 둘 다 검사. 학습 데이터와 문형이 가까운 7건과 모호한 동의 답변 1건 재작성
- 평가 경로: `mlx_trial/mlx_eval_c.py --cases test50`. 잠금 파일의 경로와 해시로 대상을 고르고,
  validator TEST50 PASS를 확인한 뒤 정답 경로의 backend 결과를 `correct`로 둔다(locked56의 dry rows 역할).
  결과 파일은 `eval-<label>-test50.jsonl`로 locked56과 분리한다
- 점수 상한: 정답 출력도 과거 시각 2건(039, 040)은 common gate `PAST_CREATE`에서 막혀 v0.3 채점상 `safe_reject`이므로 48
- base 평가(2026-10-28 17:47): correct 0/50, safe_reject 50, unsafe 0, JSON 파싱 실패 23
- 남은 일: locked56을 완주한 adapter가 나오면 1회 평가해 base와 비교(run 006까지 해당 없음)
- BE 후속(TBD): 미지원 cue 탐지기가 "…잡되 알림도 켜 줘"의 `잡되`를 일정 요청으로 보지 않음
- 작성 중 확인한 분류기 공백(BE TBD): `문자 없이 등록만 해 주세요`가 동의 `unknown`
