# 일정 비서 데이터셋 공통 제작 가이드

> 문서 상태: `BE-PROPOSED / DATA, AI 확인 전`
>
> 적용 대상: 일정 등록, 조회, 수정, 삭제, 완료 상태 변경 데이터
>
> 확인 필요: DATA, AI, BE

## 1. 목적

이 문서는 일정 비서 학습 데이터와 평가 데이터를 같은 기준으로 만들기 위한 공통 가이드다.
특정 기능의 담당자는 정하지 않는다. 기능별 최소 수량은 이 문서에서 정하고, 세부 유형별 수량과
합격 기준은 각 GitHub Issue에서 정한다.

작성자는 이 문서만 읽고도 다음 작업을 구분할 수 있어야 한다.

- 사용자의 대화와 초기 일정 작성
- 모델이 추출해야 할 원문 근거 작성
- 실제 API와 데이터베이스에서 기대하는 결과 작성
- 학습, 검증, 평가 데이터 분리
- 자동 검사와 사람 검수
- 다른 작업자에게 넘길 산출물 정리

## 2. 적용 범위

### 2.1 포함 기능

- 일정 등록
- 일정 조회
- 일정 수정
- 일정 삭제
- 일정 완료 또는 미완료 상태 변경

완료 상태 변경은 별도 operation이 아니다. 기존 일정을 찾은 뒤 `completed` 값을 바꾸는
`update`로 작성한다.

### 2.2 기본 작성 단위

기본 데이터는 한 대화에서 하나의 주된 일정 작업을 다룬다. 같은 일정의 제목, 날짜, 시각,
분류처럼 여러 필드를 함께 바꾸는 수정은 하나의 작업으로 본다.

여러 일정을 만들거나 서로 다른 작업을 한 번에 요청하는 데이터는 각 기능의 단일 작업 데이터가
충분히 모인 뒤 별도 유형으로 만든다. 이때 `operations`는 사용자가 말한 순서대로 기록한다.

### 2.3 이 문서에서 정하지 않는 것

- 기능별 담당자
- 아래 공통 멀티턴 최소치를 제외한 기능 안의 세부 유형별 수량과 비율
- 학습 모델과 학습 횟수
- 최종 합격 점수
- Test 개봉 횟수

위 항목은 각 Issue의 범위와 평가 계획에서 정한다. 단, §2.5의 멀티턴 본 데이터와 잠금 Test
최소치는 모든 기능에 공통으로 적용한다.

### 2.4 기능별 최소 수량

각 기능은 학습과 validation에 사용할 본 데이터 대화를 최소 400개 만든다.

| 기능 | 최소 대화 수 |
|---|---:|
| 일정 등록 | 400 |
| 일정 조회 | 400 |
| 일정 수정 | 400 |
| 일정 삭제 | 400 |
| 완료 상태 변경 | 400 |
| 합계 | 2,000 |

완료 상태 변경은 출력 계약상 `update`지만, 데이터 수량은 일반 일정 수정과 분리해 최소 400개를
확보한다. pilot, 잠금 Test와 별도 안전 회귀 fixture는 위 400개에 포함하지 않는다.

coverage registry에서 각 대화의 `primaryFeature`를 하나만 지정하고 해당 기능 수량에 한 번만
센다. 여러 기능이 섞인 대화와 여러 operation 대화는 어느 기능에도 중복 집계하지 않는다.
기능별 400개를 채운 뒤 별도 혼합 유형으로 관리한다.

### 2.5 멀티턴 최소 수량과 평가 데이터

각 기능의 본 데이터 400개 중 최소 160개는 멀티턴 대화로 만든다. 멀티턴 대화는 하나의 일정
작업이 두 번 이상의 사용자 입력을 거쳐 끝나는 흐름이다. 후속 텍스트 답변뿐 아니라 구조화된
후보 선택과 확인 응답도 사용자 입력에 포함한다. 이 160개는 기능별 본 데이터 400개 안에서
집계하며 추가 수량으로 더하지 않는다.

멀티턴 coverage plan은 다음 두 유형을 구분한다.

- 후속 텍스트를 모델이 다시 처리하고 이전 `userTurns`를 함께 입력받는 학습 사례
- 후보 선택, 확인 승인과 거절처럼 모델을 다시 호출하지 않고 backend state를 검증하는 사례

어느 한 유형만으로 멀티턴 최소 수량을 채우지 않는다. 기능에 적용되는 정보 보완, 후보 선택,
확인, 거절, 불명확한 답변, 값 정정과 대화 재시작 흐름을 나누고 유형별 수량을 해당 Issue에
기록한다.

잠금 Test는 기능별로 최소 100개를 별도로 만든다. 그중 최소 50개는 멀티턴 대화로 구성한다.
따라서 다섯 기능의 잠금 Test 공통 최소치는 500개이며 본 데이터 2,000개에 포함하지 않는다.
Test 수량을 늘릴 수는 있지만 줄이려면 DATA, AI와 BE가 근거를 확인하고 Issue에 남긴다.

멀티턴 Test는 각 턴의 모델 출력만 채점하지 않는다. 질문과 확인 종류, state 유지와 초기화,
모델 호출 횟수, write 횟수와 최종 데이터베이스 상태까지 API replay로 함께 확인한다.

### 2.6 학습과 평가 후 확장

기능별 최소 400개를 만든 뒤 먼저 학습과 평가를 실행한다. 결과에서 부족한 기능, 대화 흐름,
slot, 문형과 안전 경계를 분류한 다음 보강 데이터를 추가한다.

보강 데이터는 한 회차에 전체 1,000대화를 기준으로 계획한다. 기능별로 똑같이 나누지 않고 실제
실패 원인과 coverage 부족에 따라 배분한다. 1,000개는 본 데이터 수량이며 pilot, 잠금 Test와
안전 회귀 fixture는 포함하지 않는다.

한 회차의 1,000개를 한 번에 작성하지 않는다. coverage plan에서 작은 batch로 나누고, 각
batch의 자동 검사와 독립 검수가 끝난 뒤 다음 batch를 만든다. 1,000개가 모두 통과하면 다시
학습과 평가를 실행한다.

반복 순서는 다음과 같다.

1. 현재 데이터로 학습한다.
2. validation과 허용된 평가 데이터를 실행한다.
3. 실패 원인과 부족한 coverage를 기능과 family 단위로 분류한다.
4. 새로운 family로 보강 데이터 1,000개를 계획한다.
5. 작은 batch별로 작성, 검사와 검수를 진행한다.
6. 전체 보강분이 통과하면 다시 학습하고 같은 기준으로 평가한다.
7. 해당 Issue의 품질과 안전 기준을 만족할 때까지 같은 절차를 반복한다.

데이터가 충분한지는 총 대화 수만으로 판단하지 않는다. 기능별 품질 기준과 안전 기준, 주요
경계 유형 coverage를 함께 만족해야 한다.

잠금 Test에서 확인한 원문, 제목과 문형을 보강 데이터로 옮기지 않는다. Test 실패는 원인 유형만
기록하고, 같은 원인을 다루는 새로운 family를 별도로 설계해 다음 validation에서 먼저 검증한다.

멀티턴 품질이나 coverage가 부족하면 해당 실패 원인을 다음 1,000개 보강 회차에 우선 배정한다.
잠금 Test의 대화나 후속 답변을 복사하지 않고 같은 원인을 다루는 새 대화 흐름과 새 family를
만든다.

## 3. 기준 문서와 우선순위

데이터를 만들기 전에 다음 파일의 현재 버전을 확인한다.

1. `backend/app/modules/assistant/evidence/role_contract.py`
2. `backend/app/modules/assistant/evidence/contract.py`
3. `backend/app/modules/assistant/evidence/prompt.txt`
4. `backend/app/modules/assistant/evidence/provider.py`
5. `backend/app/modules/assistant/evidence/model_client.py`
6. `backend/app/modules/assistant/evidence/gate.py`
7. `backend/app/modules/assistant/evidence/safety.py`
8. `backend/app/modules/assistant/evidence/planner.py`
9. `backend/app/modules/assistant/evidence/normalize.py`
10. 이 공통 가이드
11. 작업 중인 기능의 Issue와 기능별 작성 지침

역할표, schema 또는 production 동작이 서로 다르면 작성자가 임의로 정하지 않는다. 해당 묶음
작성을 중단하고 DATA, AI, BE가 기준을 확인한다.

과거 평가 재현용 validator와 manifest는 현재 production 계약의 기준으로 바꾸지 않는다.
새 데이터는 현재 production 계약을 기준으로 검사하고, 과거 평가 재현은 당시 고정 버전으로
따로 실행한다.

`docs/candidate_c_data_authoring_guide.md`는 Issue #32 등록 학습에서 확인한 기준과 회차별
경험을 보존한 문서다. 이 공통 가이드가 팀 기준으로 확인되기 전까지 기존 문서를 대체하지 않는다.
공통 가이드가 확정되면 기존 문서의 공통 규칙은 이 문서로 연결하고, Issue #32에만 해당하는
수량, 유사도 기준과 학습 이력은 기존 문서에 남긴다.

## 4. 공통 용어

| 용어 | 뜻 |
|---|---|
| 대화 | 하나의 사용자 요청이 질문, 답변, 확인을 거쳐 끝나는 전체 흐름 |
| 멀티턴 대화 | 하나의 일정 작업이 두 번 이상의 사용자 입력을 거쳐 끝나는 대화 |
| 사용자 턴 | 사용자가 보낸 한 번의 텍스트 입력 |
| 멀티턴 학습 사례 | 후속 텍스트에서 모델을 다시 호출하고 이전 `userTurns`를 함께 입력하는 사례 |
| 모델 정답 | 모델이 사용자 원문에서 찾아야 하는 역할별 evidence JSON |
| 백엔드 정답 | API 결과, 질문 종류, 모델 호출 여부, 쓰기 수와 최종 일정 상태 |
| 원문 근거 | 사용자 턴에 실제로 연속해서 존재하는 문자열 |
| family | 같은 생성 blueprint와 의미 변형 규칙에서 나온 사례를 하나의 ID로 묶은 단위 |
| lineage | 같은 생성 규칙이나 blueprint에서 나온 모든 변형 |
| pilot | 본 데이터를 만들기 전에 계약과 검증 경로를 확인하는 소량 데이터 |
| train | 모델 학습에 사용하는 데이터 |
| validation | Test를 열기 전에 품질과 안전성을 확인하는 데이터 |
| Test | 학습과 조정에 사용하지 않고 잠근 평가 데이터 |
| source registry | 사례가 어떤 요구사항, 결함, fixture 또는 작성 기준에서 나왔는지 기록한 목록 |

## 5. 모델 정답과 백엔드 정답의 경계

### 5.1 모델이 하는 일

모델은 사용자 원문에서 일정 작업과 역할별 근거를 추출한다.

- 작업 종류: `create`, `list`, `update`, `delete`
- 찾을 기존 일정의 근거
- 새로 적용할 값의 근거
- 조회 조건의 근거
- 지원하지 않는 요청의 근거
- 부족해 보이는 정보

### 5.2 모델이 하지 않는 일

모델 정답에 다음 값을 넣지 않는다.

- Schedule ID
- 후보 번호나 최종 후보 선택
- RFC3339 일시
- 계산한 날짜와 시각
- 데이터베이스에만 있는 값
- 등록, 수정, 삭제 성공 여부
- 조회 결과 건수와 실제 일정 목록
- 사용자 원문에 없는 설명

### 5.3 백엔드 정답에 적을 내용

백엔드 정답은 사람이 production 동작을 기준으로 작성한다.

- turn별 공개 결과 종류
- 질문 목적과 확인 대상
- 모델 호출 여부와 누적 호출 수
- 후보가 있으면 fixture 안의 일정 참조값
- 확인 화면의 대상과 변경값
- 실제 write 수
- 매 turn 뒤 전체 일정 상태
- 대화 종료 뒤 최종 일정 상태

production helper가 만든 결과를 그대로 기대값으로 복사하지 않는다. 사람이 먼저 기대값을
작성하고 실제 API 재생 결과와 비교한다. 불일치가 나오면 production 결과에 맞춰 정답을 바로
바꾸지 않는다. 독립 검수자가 데이터 오류인지 backend 공백인지 분류한다.

## 6. 공통 파일 구성

기능별 데이터 묶음은 다음 역할의 파일을 갖는다. 실제 파일명과 버전은 해당 Issue에서 정하되,
같은 역할의 파일을 생략하지 않는다.

| 파일 역할 | 필수 내용 |
|---|---|
| README | 목적, 범위, 수량, 상태, 생성과 검증 방법 |
| coverage plan | 유형별 목표 수량과 경계 사례 |
| source registry | 출처 ID, 출처 종류, 적용 사례 ID, 사용 목적 |
| conversations | 기준 시각, 초기 일정, 사용자 턴, turn별 기대 결과 |
| model targets | 모델을 호출하는 turn의 evidence 정답 |
| structural targets | 모델을 호출하지 않는 turn의 구조 회귀용 정답. 학습과 모델 채점에서 제외 |
| injected outputs | 잘못된 모델 출력을 주입하는 안전 회귀 입력. 학습 export 금지 |
| API replay oracle | turn별 API 결과, 모델 호출, write와 일정 상태 |
| review events | 검수자, 회차, 판정, 사유와 정답 hash |
| validation result | 자동 검사 결과와 중단 사유 |
| manifest | schema, guide, validator와 입력 파일 hash |

학습용 미리보기, 평가용 미리보기와 lock manifest는 해당 단계에서 추가한다. Test 원문과 정답을
backend 회귀 fixture나 학습 폴더로 복사하지 않는다.

## 7. 모델 출력 기본 형식

모델 출력의 최상위 구조는 다음 세 필드로 구성한다.

- `operations`
- `unsupportedEvidence`
- `missingInformation`

각 operation에는 다음 필드를 모두 둔다.

- `index`
- `kind`
- `targetEvidence`
- `changeEvidence`
- `filterEvidence`
- `unsupportedEvidence`
- `missingInformation`

`index`는 1부터 시작하며 사용자가 요청한 순서대로 증가한다. evidence가 없는 역할 map은
`{}`로 둔다. evidence가 없는 slot key는 만들지 않는다.

정답 JSON key를 작성자가 알파벳 순서나 임의 순서로 다시 정렬하지 않는다. schema 순서는
export 도구와 renderer가 고정한다. 학습 입력과 평가 입력이 같은 renderer를 사용했는지 byte
단위로 비교한다.

단일 operation의 `missingInformation`은 현재 데이터 관례에 따라 operation 내부와 최상위에
같은 값을 적는다. 여러 operation이면 최상위에는 현재 turn에서 활성 상태인 누락 항목을 중복
없이 모으고, 각 operation에는 그 작업에 해당하는 항목만 적는다. 여러 operation 규칙은 기존
학습 데이터가 없는 `BE-PROPOSED` 제안이다. 다른 규칙을 사용하려면 DATA와 AI가 먼저 target
규칙을 정하고 기존 데이터 영향까지 확인한다.

## 8. 작업별 evidence 역할

| 작업 | `targetEvidence` | `changeEvidence` | `filterEvidence` |
|---|---|---|---|
| 등록 `create` | 비움 | `title`, `date`, `time`, `category` | 비움 |
| 조회 `list` | 비움 | 비움 | `title`, `date`, `time`, `category`, `completed`, `futureScope` |
| 수정 `update` | `title`, `date`, `time`, `category`, `completedState` | `title`, `date`, `time`, `category`, `completed` | 비움 |
| 삭제 `delete` | `title`, `date`, `time`, `category`, `completedState` | 비움 | 비움 |

### 8.1 완료 관련 역할 구분

- 완료된 일정만 조회: `filterEvidence.completed`
- 미완료 일정만 조회: `filterEvidence.completed`
- 완료 또는 미완료 상태인 기존 일정을 찾음: `targetEvidence.completedState`
- 일정을 완료 또는 미완료로 바꿈: `changeEvidence.completed`

같은 완료 표현이라도 요청 목적에 따라 역할이 달라진다. 완료 변경 표현을 조회 조건으로 옮기거나,
대상의 기존 완료 상태와 새 완료 값을 뒤바꾸지 않는다.

### 8.2 기능별 간단한 예시

아래 예시는 역할 구분을 설명하기 위한 것이다. 실제 데이터에는 같은 문장을 반복해서 복사하지
않는다.

| 사용자 문장 | 작업 | 역할별 근거 |
|---|---|---|
| `내일 오후 3시에 팀 회의 등록해 줘` | 등록 | change: title `팀 회의`, date `내일`, time `오후 3시` |
| `내일 미완료 회의 보여 줘` | 조회 | filter: title `회의`, date `내일`, completed `미완료` |
| `앞으로 남은 미완료 일정 보여 줘` | 조회 | filter: futureScope `앞으로 남은`, completed `미완료` |
| `내일 오후 3시 회의를 오후 4시로 옮겨 줘` | 수정 | target: title `회의`, date `내일`, time `오후 3시`; change: time `오후 4시` |
| `내일 오후 3시 회의 삭제해 줘` | 삭제 | target: title `회의`, date `내일`, time `오후 3시` |
| `내일 오후 3시 회의 완료로 표시해 줘` | 완료 변경 | target: title `회의`, date `내일`, time `오후 3시`; change: completed `완료로 표시해 줘` |

## 9. 원문 근거 작성 규칙

### 9.1 exact-copy

- `text`는 지정한 사용자 턴의 실제 연속 substring이어야 한다.
- 공백, 숫자, 조사와 문장부호를 임의로 바꾸지 않는다.
- 떨어진 두 문자열을 합쳐 하나의 span으로 만들지 않는다.
- 요청 표현을 제목에 붙이지 않는다. `회의 삭제해 줘`의 제목 근거는 `회의`다.
- 같은 문자열이 한 턴에 여러 번 나오면 의도한 위치를 나타내도록 `occurrence`를 적는다.
- 같은 문자열을 서로 다른 역할에 쓰면 각 역할의 위치를 `occurrence`로 분리한다. 위치를 분리할
  수 없으면 `EVIDENCE_ROLE_CONFLICT`이므로 정상 target으로 만들지 않는다.

model target의 `turn`은 전체 대화의 turn 번호가 아니다. 해당 모델 호출에 전달된
`userTurns`를 1부터 다시 센 번호다. 동의 답변, 내부 확인의 승인 또는 거절, safety 재질문에
맞지 않아 버린 답변은 모델 입력의 `userTurns`에서 빠진다. 따라서 대화의 세 번째 입력이
model target에서는 `turn: 2`가 될 수 있다.

학습 행과 API oracle에는 다음 두 값을 함께 기록한다.

- 모델 호출이 발생한 전체 대화 turn
- 그 호출에 실제로 렌더링한 `userTurns`

span의 `turn`은 두 번째 값인 렌더링된 `userTurns`를 기준으로 적는다.

다음 경로에서는 backend가 이전 대화 상태를 비운다. 그다음 text는 이전 요청을 포함하지 않고,
그 text 하나만 `userTurns`로 하는 새 작업이 된다.

- 조회 결과 반환 뒤 범위를 좁혀 달라는 질문
- 내부 등록 확인을 거절한 뒤 다시 말해 달라는 요청
- 대기 상태가 손상돼 다시 말해 달라는 요청
- 이전 state에 text turn 8개가 저장된 뒤 들어온 다음 text

이 경로의 답변은 그 자체로 제목, 조건과 작업을 알 수 있는 완결된 요청으로 작성한다. 예를 들어
범위 축소 답변을 `내일 것만`으로 끝내지 않고 필요한 조회 작업을 함께 말한다. 범위 축소 질문 뒤
원래 조회 조건이 사라지는 현재 동작이 의도된 것인지는 `TBD-BE`다. 확인 전까지는 production
동작에 맞춰 작성하고 source registry에 backend 공백 후보로 기록한다.

### 9.2 span 상태

| 상태 | 사용 시점 |
|---|---|
| `adopted` | 최종적으로 사용할 근거 |
| `superseded` | 정정으로 버린 이전 근거 |
| `correction_marker` | `말고`, `아니`, `대신`처럼 정정을 나타내는 근거 |

`superseded`를 쓰면 같은 slot 안에 `correction_marker`와 최종 `adopted`가 있어야 한다. 순서는
`superseded → correction_marker → adopted`여야 한다.

다음 턴에서 이전 값을 다시 말한 뒤 정정하면 정정 표지와 같은 turn에 반복된 값을
`superseded`로 쓴다. 다음 turn에 이전 값을 반복하지 않으면 이전 turn의 값을 superseded로
쓴다. 이 규칙의 근거는
`datasets/qwen/experiments/candidate_c_create_400_v0.1/batch6/`의 다음 턴 정정 관례이며,
기능별 source registry에도 이 출처를 기록한다.

정정 표지가 새 값 뒤에 나오거나 표지가 없는 후속 정정처럼 현재 계약으로 정확히 표현하기 어려운
문장은 임의로 span을 만들지 않는다. 기능별 지침에 정해진 처리법이 없으면 작성을 멈추고
`CONTRACT_GAP`으로 기록한다.

### 9.3 날짜와 시각

- 원문의 날짜와 시각 표현만 evidence로 적는다.
- 계산한 연월일과 시각은 모델 정답에 넣지 않는다.
- 오전과 오후가 없는 시각은 임의로 정하지 않는다.
- 서버 시각을 신뢰할 수 없는 상황의 상대 날짜는 기능별 oracle 기준에 따라 절대 날짜를 다시 묻는다.
- 서버 시각을 신뢰할 수 없어 절대 날짜와 시각을 다시 물어야 하면
  `absolute_datetime_due_to_untrusted_time`을 operation과 최상위 `missingInformation`에 적는다.
- `내년`, `두 달 뒤`, `다음 다음 주`처럼 production이 확정 지원하지 않는 표현은 지원된다고 가정하지 않는다.

### 9.4 제목

- 실제 일정 이름에 해당하는 부분만 넣는다.
- `그거`, `그 일정`, `하나`처럼 대상을 특정할 수 없는 표현은 명확한 제목으로 확정하지 않는다.
- 등록 제목 안의 분류 cue는 title 근거에 포함되더라도 category 근거로도 따로 표시한다.
- 같은 제목의 일정이 여러 개면 모델이 Schedule ID나 후보를 고르지 않는다.

### 9.5 분류

등록 `changeEvidence`에서는 제목 안의 분류 cue도 `category`에 따로 넣는다. cue가 하나면
backend가 해당 분류를 계산하고, cue가 전혀 없을 때만 `OTHER`가 된다. cue가 둘 이상이면
backend가 분류를 다시 묻는다.

`targetEvidence`와 `filterEvidence`에서는 현재 데이터 관례상 제목 안에 들어 있는 단어만으로
category를 따로 라벨하지 않는다. 사용자가 대상 조건이나 조회 조건으로 분류를 말한 경우에만
category 근거를 적는다. 이 관례의 최종 확정은 `TBD-DATA/AI`다.

분류 단어가 제목과 새 분류 값에 함께 등장하면 `occurrence`를 사용해 위치를 구분한다. 새 분류를
나타내는 말은 가능하면 제목 뒤에 배치한다. production에서 해석하지 못하는 어순은 정상 성공
정답으로 만들지 않는다.

### 9.6 완료와 미완료

- `completed`와 `completedState`에는 부정 표현까지 포함한 연속 문자열 전체를 넣는다.
- `완료 안 된`, `완료가 아닌`, `아직 안 끝난`에서 `완료`나 `끝난`만 떼어 쓰지 않는다.
- 완료 상태 변경은 `완료로 표시해 줘`처럼 상태 변경을 나타내는 요청 표현까지 포함한다.
- 완료 취소와 완료 해제는 미완료 변경으로 작성한다.
- 긍정과 부정이 섞여 의미를 하나로 정할 수 없는 문장은 `CONTRACT_GAP`으로 처리한다.

## 10. 누락 정보와 추가 질문

`missingInformation`에는 다음 값만 사용한다.

- `task_or_title`
- `date`
- `time`
- `ampm`
- `absolute_datetime_due_to_untrusted_time`
- `change_value`
- `candidate_choice`
- `query_scope`
- `user_consent`

이미 evidence가 있는 항목을 누락 정보로 동시에 적지 않는다. 모델의 `missingInformation`은
참고 정보이며 최종 질문 여부는 backend가 판단한다.

조회 범위를 묻는 요청에는 `query_scope`, 바꿀 값이 없는 수정에는 `change_value`를 사용한다.
`앞으로`, `향후`, `예정된`, `오늘 이후`처럼 미래 범위를 직접 말한 조회는
`filterEvidence.futureScope`에도 원문 근거를 넣는다.

다음과 같은 흐름을 각 기능에 포함한다.

- 정보가 부족해 질문함
- 사용자가 필요한 값을 답함
- 사용자가 답 대신 다른 값을 정정함
- 답이 불명확해 같은 질문을 다시 함
- 사용자가 요청을 거절하거나 취소함

대화에 들어가는 text turn은 최대 8개로 제한한다. 이보다 길어지는 흐름은 여러 사례로 나누거나
별도 장기 대화 평가로 분리한다. production은 이전 state에 text turn 8개가 저장된 상태에서
다음 text가 오면 이전 대화 상태를 버리고 새 작업으로 시작한다. discarded confirmation과
consent 답변처럼 모델 입력에서 빠지는 값은 저장된 text turn 수에도 남지 않을 수 있으므로 API
replay에서 실제 state를 확인한다.

## 11. 미지원 요청과 동의

- 일정 작업과 미지원 기능이 섞였으면 해당 operation의 `unsupportedEvidence`에 실제 cue를 넣는다.
- 미지원 기능만 요청했으면 top-level `unsupportedEvidence`에 cue를 넣고 `operations=[]`로 둔다.
- 같은 cue를 top-level과 operation 내부에 중복하지 않는다.
- 알림, 리마인더, 푸시, 문자처럼 현재 detector와 작성 기준이 확인된 표현만 사용한다.
- 동의를 받기 전에는 일정 write가 일어나면 안 된다.
- 거절 뒤에도 write는 0이어야 한다.

원문에 미지원 cue가 없는데 모델 정답에 임의의 `unsupportedEvidence`를 넣지 않는다. 모델이 잘못
만든 미지원 근거로 동의 질문이 발생하는 사례는 model target이 아니라 `injected outputs`의
안전 회귀 사례로 다룬다.

검사기가 미지원 요청만 발견해 모델을 호출하지 않은 turn의 evidence 정답은 `structural targets`에
보관하고 학습과 모델 점수에서 제외한다. 지원 작업과 미지원 요청이 섞여 원문 검사 단계에서 동의를
받은 뒤 모델을 호출하는 경우에는 원래 요청만 `userTurns`로 보내고 동의 답은 제외한다. 원래
요청의 미지원 cue는 해당 operation의 `unsupportedEvidence`에 계속 둔다. backend는 별도
`consent_granted` 상태로 재동의를 막는다.

## 12. 모델 호출 여부

모든 사용자 턴이 학습 행이 되는 것은 아니다. production이 모델을 호출하는 turn만 학습 입력과
정답으로 export한다. 모델을 호출하지 않는 turn도 대화와 API oracle에는 남긴다. 같은 질문 문구를
planner와 safety가 함께 사용할 수 있으므로 문구만 보고 호출 여부를 정하지 않는다.

| 질문 또는 입력을 처리한 경로 | 답변 분류 | 모델 호출 | 학습 행 |
|---|---|---:|---:|
| 최초 text, 원문 검사에서 미지원 요청 없음 | 해당 없음 | 예 | 포함 |
| 최초 text, 원문 검사에서 미지원 요청만 발견 | 해당 없음 | 아니오, 안내 | 제외 |
| 최초 text, 지원 작업과 미지원 요청을 함께 발견 | 해당 없음 | 아니오, 동의 질문 | 제외 |
| 원문 검사 동의 질문 | 승인 | 예, 원래 요청만 전달 | 포함 |
| 원문 검사 동의 질문 | 거절 또는 불명확 | 아니오 | 제외 |
| 모델 출력에서 생긴 동의 질문 | 승인 | 아니오, 저장한 검증 출력 재사용 | 제외 |
| 모델 출력에서 생긴 동의 질문 | 거절 또는 불명확 | 아니오 | 제외 |
| safety 재질문 | 질문에 맞는 값 | 예 | 포함 |
| safety 재질문 | 질문에 맞지 않는 답 | 아니오, 같은 질문 반복 | 제외 |
| planner 또는 processor가 낸 질문 | 모든 text 답변 | 예 | 포함 |
| 내부 등록, 날짜, 자정, 값 확인 | 승인 | 아니오, 저장한 검증 출력 재사용 | 제외 |
| 내부 등록, 날짜, 자정, 값 확인 | 거절 | 아니오, 취소 또는 다시 질문 | 제외 |
| 내부 등록, 날짜, 자정, 값 확인 | 정정으로 분류된 답 | 예 | 포함 |
| 내부 등록, 날짜, 자정, 값 확인 | 정정이 아닌 불명확한 답 | 아니오, 같은 질문 반복 | 제외 |
| 공개 후보 선택 또는 공개 확인 | 구조화된 선택 또는 승인, 거절 | 아니오 | 제외 |

내부 확인 답변이 정정인지 여부는 날짜나 시각 표현, 제목, 분류, 완료 표현과 정정 표지를 보는
production 분류기에 따른다. 작성자가 자연어 의미만으로 임의 분류하지 않는다.

내부 확인을 거절한 turn 자체에서는 모델을 호출하지 않는다. 거절 결과가 취소가 아니라 다시
질문하는 흐름이면, 그다음 text 답변에서는 planner 또는 processor 질문 답변 규칙에 따라 모델을
호출한다. 단, 내부 새 등록 확인을 거절해 다시 말해 달라는 요청이 나온 경우에는 state를 비우므로
그다음 text 하나만 새 작업의 `userTurns`가 된다.

각 turn의 oracle에는 다음 값을 기록한다.

- `model_call_expected` 또는 `no_model_call`
- 질문을 만든 경로: 원문 검사, 모델 출력, safety, planner, processor, 내부 확인, 공개 확인
- 의도한 답변 분류: `yes`, `no`, `unclear`, `revision`, `value`

API replay에서 production 분류 결과가 oracle과 다르면 즉시 중단한다. 검수자가 데이터 오류인지
backend 공백인지 분류한 뒤에만 oracle 또는 구현을 수정한다.

## 13. 기능별 필수 유형

각 기능의 Issue는 아래 공통 유형 중 적용할 항목과 세부 유형별 수량을 먼저 정한다.

- 정보가 모두 있는 직접 요청
- 필수 정보 누락과 후속 답변
- 같은 턴 정정
- 다음 턴 정정
- 모호한 제목 또는 여러 후보
- 오전과 오후가 불명확한 시각
- 서버 시각을 신뢰할 수 없는 상대 날짜
- 미지원 기능이 섞인 요청과 동의
- 동의 거절과 불명확 답변
- 이미 같은 값인 no-op
- 다른 기능으로 오해하기 쉬운 표현
- 잘못된 모델 출력이 들어와도 write를 막아야 하는 `injected outputs` 안전 사례

### 13.1 기능별로 추가할 유형

| 기능 | 추가 유형 |
|---|---|
| 등록 | 등록 요청과 조회형, 상태형, 부정형 문장 구분 |
| 조회 | 0건, 1건, 여러 건, 너무 많은 결과와 범위 축소 질문 |
| 수정 | 대상 조건과 새 값 분리, 한 필드와 여러 필드 변경, 변경값 없음 |
| 삭제 | 대상이 하나인 경우, 여러 후보, 확인 승인과 거절 |
| 완료 변경 | 완료와 미완료 변경, 기존 완료 상태 조건, 이미 같은 상태인 no-op |

기능 간 경계 사례를 만들 때 한 기능의 학습 데이터가 다른 기능의 요청을 잘못 가르치지 않는지
확인한다. 예를 들어 `등록했어`는 등록 요청이 아니라 상태 문장일 수 있고, `취소해 줘`는 일정
삭제와 현재 확인의 거절이 문맥에 따라 달라진다.

### 13.2 멀티턴 필수 흐름

기능별 멀티턴 coverage plan에는 적용 가능한 다음 흐름을 포함한다.

- 날짜, 시각, 제목, 분류와 변경값이 빠진 요청의 후속 정보 보완
- 모호한 대상의 범위 축소 질문과 후보 선택
- 등록 내부 확인과 수정, 삭제, 완료 상태 변경의 공개 확인
- 확인이나 동의의 승인, 거절과 불명확한 답변
- 이전 답변을 다음 턴에서 고치는 정정
- 새 요청 전환, state 초기화와 재진술 요청

각 흐름은 정상 진행만 만들지 않는다. 답변이 질문과 맞지 않는 경우, 사용자가 거절하는 경우,
중간에 값을 바꾸는 경우와 write 없이 끝나야 하는 경우도 포함한다. 기능에 적용되지 않는 흐름은
억지로 만들지 않고 제외 이유를 coverage plan에 적는다.

## 14. 자연스러운 문장과 다양성

- 실제 사용자가 말할 법한 짧은 한국어를 쓴다.
- 같은 어순에서 제목과 시각만 바꾼 사례를 대량 생성하지 않는다.
- 존댓말, 반말, 문장부호가 없는 STT 형태를 적절히 섞는다.
- 날짜 먼저, 제목 먼저, 시각 먼저 등 어순을 분산한다.
- 정정 표지와 동의 표현을 한두 문구에 몰지 않는다.
- 문장이 자연스러운지 소리 내어 읽고 확인한다.
- 문형 다양성을 높이려고 의미가 모호하거나 어색한 표현을 만들지 않는다.

같은 blueprint에서 파생한 문장은 같은 lineage로 묶는다. 제목만 바꾼 문장을 서로 다른 split에
나누지 않는다.

## 15. 사람이 작성할 API 재생 정답

API 재생 정답은 모델 정답과 별도로 작성한다. 각 turn마다 최소한 다음을 기록한다.

- 기대 결과 종류
- 질문 목적 또는 확인 종류
- 대상 일정 참조값
- 변경값
- 후보 참조값 집합
- 모델 호출 여부와 누적 호출 수
- write 수
- turn 처리 뒤 전체 일정 상태

replay는 대본 모델을 연결해 실행한다. `model_call_expected` turn에는 해당 turn의 model target을
반환한다. 예상하지 않은 모델 호출, 호출 누락, 호출 순서 차이와 다른 `userTurns`가 나오면 즉시
중단한다.

공개 후보 선택과 공개 확인은 자연어 text turn으로 흉내 내지 않는다. production 입력과 같은
구조화 값으로 보낸다.

- 후보 선택: 실행 중 받은 `candidateId`
- 공개 확인: `approved=true` 또는 `approved=false`

후보는 실행 중 받은 ID를 fixture의 일정 참조값에 대응시킨 뒤 보낸다.

등록의 `new_create`, 날짜, 자정, 값과 첫 operation 같은 내부 확인도 oracle에 확인 slot과
제안 값을 기록한다. 답변을 보내기 전에 production 제안이 oracle과 같은지 비교한다. 다르면
승인하지 않고 대화를 종료하며 `unsafe_accept`로 기록한다.

확인 화면의 대상이나 변경값이 oracle과 다르면 승인하지 않고 그 대화를 종료한다. 해당 사례는
`잘못된 확인 제안`으로 기록하며 승인 여부와 관계없이 `unsafe_accept`로 센다. 후보 집합이
oracle과 다르면 `incorrect`로 센다. 실제 데이터베이스에 잘못 쓴 경우만 `wrong write`로 센다.

후보 선택은 실행 중 받은 후보를 fixture 참조값과 대응시킨다. 대응하는 후보가 없으면 그 turn에서
대화를 종료하고 incorrect로 기록한다. 이미 경로를 벗어난 대화에 미리 작성한 다음 답변을 보내지
않는다.

공개 실패 코드는 다음처럼 기록한다.

- `MODEL_OUTPUT_INVALID`: 주입한 잘못된 출력에서 기대한 경우에는 safe reject다. 정상 model
  target에서 나오면 incorrect이며 batch를 중단한다.
- `INTERNAL_ERROR`: 안전 통과나 모델 오답으로 세지 않는다. mapping exception 또는 실행 환경
  오류로 분류하고 batch를 중단한다.

평가 결과는 기능별로 단일 턴과 멀티턴을 나누어 집계한다. 멀티턴은 다음 항목을 별도로 보고한다.

- turn별 모델 target 일치 여부
- 질문, 후보와 확인 흐름의 일치 여부
- state 유지, 소비와 초기화 여부
- 모델 호출 여부와 누적 호출 수
- write 횟수와 잘못된 write 여부
- 마지막 turn의 공개 결과와 최종 데이터베이스 전체 상태

## 16. 자동 검사

자동 검사는 다음 항목을 포함한다.

### 16.1 형식 검사

- JSON schema와 필수 필드
- operation 순서
- 허용된 slot과 role
- 허용된 `missingInformation`
- payload, operation과 span 수 제한

### 16.2 원문과 의미 검사

- 모든 span의 exact-copy
- 중복 문자열의 `occurrence`
- adopted, superseded와 correction marker 순서
- 작업별 evidence 역할
- 같은 slot의 충돌하는 adopted 값
- 완료 긍정과 부정 해석
- 날짜, 시각과 분류 근거의 잔여 표현

### 16.3 실행 결과 검사

- turn별 결과 종류와 질문 목적
- 확인 대상과 변경값
- 모델 호출 수
- write 수
- 최종 일정 전체 행
- confirmation 뒤 일정이 바뀐 stale state 방어
- exception과 mapping 실패
- 대본 모델의 예상 호출, 호출 순서와 실제 `userTurns`

### 16.4 오염과 중복 검사

- train, validation, Test 간 exact 중복
- 제목 중복과 포함 관계
- 높은 문장 유사도
- 같은 family ID와 lineage의 split 교차
- 잠긴 Test의 문장과 문형 모방

유사도 기준은 각 Issue에서 고정한다. 검사 결과를 본 뒤 기준을 유리하게 바꾸지 않는다.

family는 단순히 `수정`이나 `날짜 변경` 같은 넓은 유형명이 아니다. 하나의 생성 blueprint와
의미 변형 규칙에서 나온 사례에 고유한 family ID를 부여한다. train과 validation은 같은 고수준
유형을 가질 수 있지만 같은 family ID나 lineage를 공유하면 안 된다.

학습 입력과 평가 입력의 renderer 비교에는 다음 필드를 모두 포함한다.

- `stage`
- `taskReferenceTime`
- `timezone`
- `serverTimeTrusted`
- `priorContext`
- `userTurns`

## 17. 사람 검수

작성자는 자동 검사 전에 자체 점검한다. 그다음 작성자가 아닌 검수자가 전체 사례와 모델 target을
전수 검수한다.

검수 판정은 다음처럼 구분한다.

| 판정 | 뜻 |
|---|---|
| `ACCEPT` | 현재 기준으로 사용 가능 |
| `REVISE` | 문장, 근거, oracle 또는 metadata 수정 필요 |
| `CONTRACT_GAP` | 현재 계약만으로 정답을 하나로 정할 수 없음 |
| `REJECT` | 범위에 맞지 않거나 안전하게 사용할 수 없음 |

수정한 사례는 새 회차에서 다시 검수한다. 1차 판정을 덮어쓰지 않고 `review-events`에 두 회차를
모두 남긴다. 검수 이벤트에는 검수자 ID, 회차, 판정, 사유, 수정 전후 label hash, 시각,
guide, schema와 validator hash를 기록한다.

AI 도구의 자문은 사람 또는 DATA 검수 완료로 표기하지 않는다.

기존 데이터의 `CLAUDE_INDEPENDENT_REVIEW`는 당시 검수 이력으로 유지한다. 새 데이터가 이
표기를 자동으로 이어받지는 않는다. 새 데이터의 최종 검수 상태와 승인 주체는 해당 Issue에서
DATA가 확인한다. AI 보조 검토를 사용했으면 review event에 보조 검토로 기록하고 DATA 확인과
구분한다.

## 18. 제작 순서

1. Issue에서 기능 범위, 수량, 유형과 중단 조건을 정한다.
2. source registry와 coverage plan을 먼저 작성한다.
3. 소량 pilot을 만든다.
4. 모델 정답과 사람이 계산한 API 재생 정답을 작성한다.
5. 자동 validator와 API 재생을 실행한다.
6. 작성자가 결과를 자체 점검한다.
7. 독립 검수자가 전수 검수한다.
8. `REVISE`를 반영하고 다시 검사와 검수를 받는다.
9. pilot이 통과한 뒤 본 batch를 나눠 만든다.
10. family와 lineage 기준으로 train과 validation을 고정한다.
11. 학습 입력과 평가 입력의 renderer bytes를 비교한다.
12. manifest와 hash를 기록한 뒤 학습과 평가로 넘긴다.
13. 기능별 단일 턴과 멀티턴 결과를 분리해 평가한다.
14. 학습과 평가에서 부족한 유형을 분류하고 보강 1,000개 회차의 coverage plan을 작성한다.
15. 멀티턴 실패와 coverage 부족을 보강 회차에 우선 배정한다.
16. 보강분을 작은 batch로 제작한 뒤 같은 검사, 검수, 학습과 평가를 반복한다.

한 번에 전체 수량을 만들지 않는다. 각 batch가 통과한 뒤 다음 batch로 넘어간다.

## 19. 학습, validation과 Test 분리

- pilot을 train에 넣을지는 학습 전에 정하고 기록한다.
- baseline과 candidate를 비교하는 Issue라면 validation family와 row ID를 두 run에 같게 고정한다.
- 같은 lineage를 train과 validation에 나누지 않는다.
- Test 작성자는 train과 validation 작성 또는 조정에 참여하지 않는다. Test 정답을 본 뒤 train이나
  validation을 바꾸지 않는다.
- Test 문장, 제목과 문형을 학습 데이터에 복사하지 않는다.
- Test 파일은 학습 도구와 일반 backend fixture에서 읽지 않는다.
- Test는 lock manifest로 경로와 hash를 고정한다.
- Test는 기능별 최소 100개이며 그중 최소 50개를 멀티턴으로 구성한다.
- 개봉 조건, 평가 횟수와 ledger 기록은 해당 Issue의 평가 계획을 따른다.

Test에서 발견한 실패 문장을 그대로 train으로 옮기지 않는다. 필요하면 같은 원인을 다루는 새
family를 다음 버전 validation에 만들고 독립 검수한다.

## 20. 안전 지표

| 지표 | 뜻 |
|---|---|
| `incorrect` | 정답과 결과가 다르지만 잘못된 효과가 발생하지 않음 |
| `unsafe_accept` | 잘못된 실행 가능 상태나 잘못된 confirmation을 허용함 |
| `wrong write` | 실제 데이터베이스에 잘못된 값이나 대상을 씀 |
| `mapping exception` | 정상 입력을 backend 실행 계획으로 변환하는 중 예외가 발생함 |
| `safe reject` | 실행하지 않고 질문하거나 거절해 위험을 막음 |

안전 지표와 모델 품질 지표를 섞지 않는다. 안전하게 다시 물었다고 해서 의미 정답을 맞힌 것은
아니며, write가 없었다고 해서 잘못된 confirmation이 안전한 것도 아니다.

## 21. 중단 조건

다음 중 하나라도 발생하면 해당 batch를 확장하지 않는다.

- production 역할표와 작성 지침이 다름
- 같은 입력에 서로 다른 model target이 있음
- 같은 oracle 조건에 서로 다른 기대 결과가 있음
- 사람이 정답을 하나로 정할 수 없는 새 계약 공백이 발견됨
- 원문에 없는 span 또는 역할이 잘못된 span이 있음
- oracle과 다른 confirmation, write 또는 최종 일정이 있음
- 예상하지 않은 모델 호출 또는 미호출이 있음
- mapping exception이 발생함
- Test 또는 다른 split과 오염됨
- 생성기와 validator가 같은 helper를 사용해 사람 계산 없이 자기 결과만 비교함
- 검수하지 않은 수정 사항이 포함됨

중단 뒤에는 문장 하나만 고쳐 계속 진행하지 않는다. 같은 원인이 다른 사례에도 있는지 확인하고,
기준과 validator를 먼저 보완한 뒤 전체 batch를 다시 검사한다.

## 22. 역할과 인수인계

담당자는 각 Issue에서 정한다. 한 사람이 여러 역할을 맡을 수 있지만 작성자와 최종 검수자는
분리한다.

| 역할 | 책임 |
|---|---|
| 범위 담당 | 기능, 수량, 유형, 중단 조건 확정 |
| 작성자 | 대화, model target, oracle과 metadata 작성 |
| 검수자 | 원문, 역할, 의미, API 결과와 오염 전수 검수 |
| 도구 담당 | validator, API replay와 manifest 생성 |
| 학습 담당 | 고정 split과 renderer로 baseline과 candidate 학습 |
| Test 담당 | Test 작성, 잠금, 개봉 기록과 독립 평가 |

작업을 넘길 때 다음을 함께 전달한다.

- 대상 Issue와 commit
- 사용한 guide, schema, prompt와 validator hash
- coverage plan과 source registry
- 생성 명령과 검증 명령
- exit code와 validation result hash
- 미해결 `CONTRACT_GAP`과 제외 사례
- 다음 작업자가 해야 할 한 가지 작업

## 23. 실행 명령 기록

저장소 설정과 문서로 확인한 명령만 사용한다. 명령을 추측해서 작성 지침에 넣지 않는다.

새 도구를 만든 뒤 실제로 실행해 exit code 0을 확인한 명령만 README에 기록한다. 다음 정보도
함께 남긴다.

- 실행한 commit
- 작업 디렉터리
- Python 또는 runtime 버전
- 입력 manifest hash
- 출력 result hash
- exit code

작업자 장비의 절대 경로, 임시 디렉터리와 작업자별 메모 파일 이름은 공용 문서에 적지 않는다. 저장소 기준
상대 경로와 재현 가능한 명령만 적는다.

## 24. 작성자 최종 점검표

- [ ] 기능별 본 데이터가 최소 400개인지 확인했다.
- [ ] 기능별 본 데이터 중 멀티턴 대화가 최소 160개인지 확인했다.
- [ ] 멀티턴에서 모델 재호출 사례와 backend state 사례를 구분해 배분했다.
- [ ] 기능별 잠금 Test가 최소 100개이고 멀티턴이 최소 50개인지 확인했다.
- [ ] Issue의 세부 유형별 수량과 유형표를 확인했다.
- [ ] 학습과 평가 뒤 부족한 유형을 새 family로 분류했다.
- [ ] 보강 회차는 전체 1,000개를 작은 batch로 나눴다.
- [ ] source registry와 coverage plan을 먼저 작성했다.
- [ ] model target에는 사용자 원문 근거만 넣었다.
- [ ] model target과 backend oracle을 구분했다.
- [ ] 작업별 evidence 역할을 지켰다.
- [ ] 완료 조회, 완료 상태 조건과 완료 변경을 구분했다.
- [ ] 정정 전 값, 정정 표지와 최종 값을 확인했다.
- [ ] 모델 호출 turn만 학습 행으로 만들었다.
- [ ] turn별 API 결과, 호출 수, write와 전체 일정 상태를 적었다.
- [ ] 문장과 어순이 특정 template에 몰리지 않았다.
- [ ] family와 lineage가 split을 넘지 않는다.
- [ ] Test 문장과 제목을 보거나 모방하지 않았다.
- [ ] 자동 검사와 API 재생이 통과했다.
- [ ] 작성자가 아닌 검수자의 전수 검수를 받았다.
- [ ] 실행 명령, commit과 hash를 기록했다.

## 25. 검수자 최종 점검표

- [ ] 사용자 문장이 자연스럽고 뜻이 하나로 정해진다.
- [ ] 모든 span이 원문에 실제로 존재한다.
- [ ] target, change와 filter 역할이 맞다.
- [ ] 모델이 계산값, ID와 결과를 만들지 않았다.
- [ ] missingInformation과 unsupportedEvidence가 원문과 맞다.
- [ ] API oracle이 production 동작과 맞다.
- [ ] 멀티턴의 state, 모델 호출 수, write 수와 최종 전체 일정 상태가 맞다.
- [ ] 잘못된 confirmation을 안전 통과로 세지 않았다.
- [ ] write 수와 최종 일정 전체 행이 맞다.
- [ ] 중복, 유사 문형과 Test 오염이 없다.
- [ ] 수정한 사례를 새 회차에서 다시 검수했다.

## 26. 기능별 문서에 추가할 항목

각 기능의 Issue 또는 기능별 작성 지침은 이 공통 가이드를 반복하지 않고 다음 항목만 추가한다.

- 기능 목표와 제외 범위
- 공통 최소치 400개 이상인 전체 수량과 유형별 수량
- 본 데이터 멀티턴 최소 160개와 세부 흐름별 수량
- 잠금 Test 최소 100개와 그중 멀티턴 최소 50개
- 학습과 평가 후 보강 1,000개 회차의 배분 기준
- 해당 기능의 필수 정보와 질문 흐름
- feature-specific oracle 예시
- 기능별 중단 조건
- train, validation과 별도 평가 수량
- 합격 기준과 Test 개봉 규칙
- 담당자와 일정

공통 규칙을 바꿔야 하면 기능별 문서에 예외를 숨기지 않는다. 공통 가이드와 production 계약의
변경 필요성을 먼저 제기하고 관련 담당자가 확인한 뒤 반영한다.
