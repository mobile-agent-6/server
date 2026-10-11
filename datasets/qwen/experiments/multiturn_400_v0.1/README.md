# Qwen 멀티턴 400개 — 미검수 초안

기존 30개와 추가 370개를 합한 **400개 대화**다. 한 대화는 여러 사용자 턴으로 구성된다. 파인튜닝 데이터 준비 및 기본 모델 평가 단계이며 가중치 학습은 아직 실행하지 않았다. 모든 정답은 `proposed_unreviewed`다. 공유 AI 출력·데이터 계약을 확정하지 않는다.

## 업로드 구성

| 파일 | 역할 |
|---|---|
| `prompt.txt` | 실제 평가에 사용한 v2.3 프롬프트 |
| `build_dataset.py` | 원본 대화·생성 규칙으로 400개 생성 및 정답 재생. 필요한 원본 30개는 코드에 포함 |
| `scenarios_all_400_UNREVIEWED.jsonl` | 전체 400개 대화 |
| `train/scenarios_UNREVIEWED.jsonl` | 학습 후보 260개 |
| `validation/scenarios_UNREVIEWED.jsonl` | 검증 후보 40개 |
| `test/scenarios_UNREVIEWED.jsonl` | 테스트 예약 후보 100개 |
| `run_eval.py` | 지정한 시나리오를 읽어 실제 Qwen 평가 |
| `review.html` | 실행 결과 JSONL을 첨부해 대화·모델 원문·판정 확인 |
| `guards.py` | 발화 근거·날짜·질문·쓰기 검사 |
| `question_rubric.json` | 대화별 기대 질문 종류 |
| `runtime/` | 평가기가 사용하는 action 검증·대화 제어부·메모리 일정 저장소 등 실행 의존 파일 |

`runtime/schedule_assistant/prompt.txt`는 내부 기반 클래스가 읽는 기본 프롬프트다. 실제 Qwen 평가에는 최상위 `prompt.txt`를 사용한다. 실행 의존 파일을 제외하면 import 또는 실행이 실패한다. 이 폴더는 DATA 내부 재현 실험이며 저장소의 `ai/`·`backend/` 구현을 변경하지 않는다.

학습 행·검수표·정답 재생 미리보기·실행 로그는 Git에 올리지 않고 필요할 때 생성한다. 기존 시범 파일은 별도로 복사하지 않는다. 실행 로그와 개인 검수 기록은 공유 대상에서 제외한다.

## 분할과 한계

| 분할 | 대화 | 그룹 | 생성되는 학습/참조 행 |
|---|---:|---:|---:|
| train | 260 | 27 | 651 |
| validation | 40 | 8 | 114 |
| test | 100 | 10 | 352 |

각 업무 카테고리(INBOUND·INVENTORY·OUTBOUND·RETURN·OTHER)는 train 52개·validation 8개·test 20개다. train의 등록/조회/수정/삭제는 72/61/64/63개로, 기존 계획의 각 65개와는 다르다. validation은 각 10개, test는 각 25개다.

원본 30개와 추가 train 230개는 같은 상황의 어휘·날짜·승인 표현 변형을 포함한다. validation/test는 새 대화 조합으로 작성했다. 같은 원본/조합의 변형은 같은 그룹과 분할에 배치했다. 그룹 ID와 동일 입력·정답 행의 분할 간 중복은 없으나 의미상 유사성과 실제 현장 대표성은 사람 검수가 필요하다. test는 검수 전 예약 초안이며 아직 잠근 독립 시험본이 아니다.

## 실행

Python 3.10 이상을 사용한다. 아래 명령은 **이 README가 있는 폴더**에서 실행한다.

### 모델 없이 실행·HTML 연결 먼저 확인

```bash
python3 run_eval.py --scripted --output-dir runs/smoke_001
```

`review.html`을 브라우저로 열고 `runs/smoke_001/review_cases.jsonl`을 선택한다. 기본 validation 40개의 **제안 정답 재생 미리보기**이며 실제 Qwen 응답이 아니다. Python 표준 라이브러리만 사용하며 별도 pip 설치는 필요 없다.

### 제안 정답 재생 및 산출물 생성

```bash
python3 build_dataset.py
```

모델과 운영 DB를 호출하지 않는다. 400개 시나리오를 재생하고 형식·그룹 분리·중복을 검사한다. 학습 후보 JSONL, 검수표, 정답 재생 미리보기, manifest는 로컬에 생성된다. 생성 파일을 다시 쓰므로 수동 검수 기록을 별도 보관하고 정답 수정은 생성 원본에도 반영한다.

### 실제 Qwen 평가

Ollama가 실행 중이고 로컬에 `qwen3:4b`가 있어야 한다. 기본 입력은 validation 40개다.

```bash
python3 run_eval.py --model qwen3:4b --scenarios validation/scenarios_UNREVIEWED.jsonl --rubric question_rubric.json --output-dir runs/validation_001
```

매번 새 출력 폴더명을 사용한다. 결과는 `runs/validation_001/review_cases.jsonl`이다. 함께 제공한 `review.html`을 브라우저로 열고 해당 파일을 첨부하면 된다. GitHub 파일 보기 화면이 아닌 내려받은 HTML을 연다. 로컬 생성된 `validation/review_cases.jsonl`은 정답 재생 미리보기이며 실제 모델 결과와 구분한다.

전체 400개도 입력 경로를 바꿔 실행할 수 있다.

```bash
python3 run_eval.py --model qwen3:4b --scenarios scenarios_all_400_UNREVIEWED.jsonl --rubric question_rubric.json --output-dir runs/all_400_001
```

단, 독립 테스트를 남겨두려면 개발 중에는 validation만 사용한다. test 결과를 보고 모델·프롬프트를 조정하면 새로운 독립 시험본이 필요하다.

## 확인된 결과

- 오프라인 제안 정답 재생: 400/400 통과. 모델 정확도 100%를 의미하지 않는다.
- 실제 Qwen은 **validation 40개만** 평가했다. 400개 전체를 실제 모델로 평가한 결과는 아직 없다.
- 실행 `validation_baseline_001`: 잠정 일치 31/40(77.5%), 검토 필요 9개, 모델 호출 114회.
- 파싱 오류·모델 요청 오류·제어부 crash·조회 필터 자동 보정은 각각 0건.
- 불일치: 완료 처리와 알림 동시 수정 시 알림 누락 5건, 날짜·시각 오류로 등록 차단 3건, 날짜 재질문 1건.

이 수치는 프롬프트·상태 schema·가드·미검수 기준을 포함한 시스템의 잠정 일치율이다. 모델 단독 정확도나 파인튜닝 후 성능으로 해석하지 않는다. 원문 로그는 로컬에 보관한다.

## 학습 전

정답 action과 최종 상태를 사람이 검수하고 승인한 train/validation만 사용한다. 모델 오답을 정답으로 복사하지 않는다. 생성된 `messages`에서 마지막 assistant action에만 loss를 적용하고 이전 문맥 전체를 학습 정답으로 처리하지 않는다. 번호 선택·승인·취소처럼 모델을 호출하지 않는 턴에는 가짜 action을 만들지 않는다.

학습 행으로 바꾼 뒤 행 단위로 다시 분할하지 않는다. 학습기의 chat template·loss mask·토큰 길이/잘림은 아직 확인하지 않았다. 모델 오류가 0건이어야 학습할 수 있는 것은 아니지만, 학습 정답과 평가 기준은 먼저 정확히 검수해야 한다.
