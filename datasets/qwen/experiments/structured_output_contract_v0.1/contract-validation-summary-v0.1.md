# Issue #30 후보 계약 검증 요약 v0.1

- 상태: `PROPOSED`
- 검증 방식: 실제 모델·transport·backend server·Schedule API·DB 호출 없이 Python 표준 라이브러리로 결정적 검증
- 기준 집합: lock 상태 `locked`, locked case 56건
- 범위: 후보 A·B·C와 규칙 기준선 R의 출력 계약, local validator, 공통 backend 안전 gate, mutation fixture

이 문서는 후보 선택을 확정하지 않는다. D1–D12는 기존 책임 경계 문서의 `BE-PROPOSED` 상태를 유지하며, 이 결과만으로 production 계약이나 공개 API를 변경하지 않는다.

## 후보 정의

### 후보 A — DATA v2.6 controller 보조 action

기존 DATA v2.6 구현을 그대로 재현한 단일 action 상태 기계다. controller context는 질문 필요 여부, 검증된 사용자 datetime, 조회 범위, 업무 근거, 현재 turn의 후보 조회 수행 여부와 후보 집합을 제공한다. dynamic schema는 현재 단계에서 가능한 action과 값, 알려진 category·조회 범위, 후보 ID를 제한한다.

출력 action은 기존 7개 `ASK`, `LIST`, `CREATE`, `SELECT_TARGET`, `CONFIRM_UPDATE`, `CONFIRM_DELETE`, `ANSWER`다. write guard는 create/update의 사용자 근거를 다시 확인하며, 후보가 1건이면 해당 ID만 허용하고 2–10건이면 모델이 ID를 고르지 못하게 한다. 후보가 10건을 넘으면 선택 action을 허용하지 않는다.

관찰된 실제 결함도 보존했다.

- `3시 반`, `7시 반`의 `반`을 기존 정규식이 소비하지 않아 분이 `00`으로 남는다. A local validator는 이 결과를 허용하지만 공통 gate는 grounded `:30`과 불일치하여 거절한다.
- LIST의 `completed` boolean은 기존 dynamic schema와 context에서 사용자 근거로 제한되지 않는다. A local validator는 근거 없는 값을 허용하지만 공통 gate는 거절한다.
- 단일 action과 제한된 날짜 문법 때문에 locked set의 다수 의미를 구조적으로 표현하지 못한다.

### 후보 B — 단계형 intent + 정규화 실행 정보

1단계는 정확히 하나의 intent를 분류하고, 2단계는 `create`, `list`, `update`, `delete`별 정규화 실행 정보를 출력한다. JSON 구조·필드·타입·enum·timezone을 엄격히 검사하고, 형식이 맞더라도 사용자 입력에서 결정적으로 도출되지 않은 datetime, filter, change 값은 의미 검증에서 거절한다. 모델의 정규화 값은 실행 근거로 그대로 신뢰하지 않는다.

v0.1은 단일 intent와 최대 2회 호출만 정의한다. 순서형 다중 operation 4건은 의미를 임의로 발명하지 않고 구조적 표현 불가로 분류했다. Issue #29의 58회 결과를 재실행·재현하지 않았고 fresh20의 학습 효과를 B의 결과로 주장하지 않는다.

### 후보 C — 역할별 원문 evidence

모델은 순서형 operations와 operation 종류, target/change/filter/unsupported evidence, 같은 역할의 여러 원문 조각, 정정 전 `superseded` 값과 채택한 `adopted` 값, user turn 번호, 참고용 missing information만 출력한다. backend가 정규화·후보 선택·최종 missing 판단·실행을 담당한다.

local validator는 NFKC 정규화와 공백 정리 뒤 각 span이 지정된 text user turn의 실제 substring인지, 정정 순서가 유효한지 검사한다. RFC3339 datetime, Schedule ID, 최종 후보 선택, 성공·실패 주장, DB 전용 값과 원문에 없는 span은 금지한다. locked 56건에서 모든 C span을 검증했다.

### 기준선 R — intent만 모델, slot은 규칙

모델은 정확히 하나의 intent만 출력하고, 나머지 evidence와 slot은 bounded deterministic backend 규칙이 추출한다. 지원하지 않는 문법은 slot을 꾸며내지 않고 안전하게 거절하거나 질문한다. 순서형 다중 operation은 구조적으로 표현할 수 없다. 정정 선택, 유사 제목 매칭과 일부 요일 문법도 fail-closed한다. R은 비교 기준선이며 최종 채택 후보로 가정하지 않는다.

## Locked 56건 표현 가능성

| 후보 | 표현 가능 | 안전한 거절 | 구조적 표현 불가 |
|---|---:|---:|---:|
| A | 9 | 4 | 43 |
| B | 52 | 0 | 4 |
| C | 56 | 0 | 0 |
| R | 42 | 10 | 4 |

`safe_reject`는 계약 구조는 안전한 ASK/거절을 표현할 수 있지만 실행 의미를 완성할 수 없는 경우다. `structurally_unrepresentable`은 해당 후보의 출력 cardinality 또는 field 구조로 oracle 의미 자체를 보존할 수 없는 경우다.

## Mutation 검증 결과

- fixture: 42개
- 후보별 실행: 141건
- local validator: accept 102건, reject 39건
- common gate: reject 102건, local reject로 미실행 39건
- 최종 결과: `safe_reject` 141건, `unsafe_accept` 0건

후보별 분리는 다음과 같다.

| 후보 | local accept | local reject | common reject | common 미실행 |
|---|---:|---:|---:|---:|
| A | 27 | 8 | 27 | 8 |
| B | 25 | 9 | 25 | 9 |
| C | 25 | 14 | 25 | 14 |
| R | 25 | 8 | 25 | 8 |

local accept는 최종 실행 승인을 뜻하지 않는다. local validator를 통과한 모든 위험 mutation은 common gate에서 거절됐다. common gate는 다음 조건을 후보 공통으로 검사한다.

- 근거 없는 datetime·title·category·completed·change와 신뢰하지 않은 상대시간 절대화
- 잘못된 timezone, 과거 create, 92일 초과 조회 범위
- 존재하지 않는 ID, 후보 밖의 실제 ID, 삭제된 일정과 다중 후보 자동 선택
- 빈/no-op update, confirmation 전 update/delete write, LIST write
- backend 확인 없는 성공·실패·조회 문구, 명시적 빈 목록을 포함한 실제 조회 결과 불일치
- 10건 초과 후보의 임의 절단, 검색 위임을 승인으로 해석하는 동작
- 후보 조회 후 질문·추천·selection·confirmation을 위한 모델 재호출

fixture는 duplicate key, extra/missing field, 잘못된 type·enum, 길이와 payload 초과, control character, 원문에 없는 span, 잘못된 turn, 같은 turn 및 turn 간 정정 전후 뒤바꿈, 금지된 datetime·Schedule ID와 위 공통 안전 조건을 포함한다.

## 원인과 결과 축

오류 원인은 `FORMAT`, `MODEL_SEMANTIC`, `NORMALIZER`, `MAPPING`, `ORACLE_ERROR`, `CONTRACT_GAP`으로 기록하고, 시스템 결과는 `correct`, `safe_reject`, `over_ask`, `unsafe_accept`로 별도 기록했다. 실제 transport를 실행하지 않았으므로 `TRANSPORT` 원인이나 결과는 만들지 않았다.

## 구조적 한계와 정책·schema 판정

- A의 half-hour 손실과 LIST completed grounding 누락은 기존 DATA v2.6의 후보-local 결함이다. 재현을 위해 production 코드를 변경하거나 조용히 개선하지 않았다.
- B의 정규화 값은 local semantic grounding을 통과해도 common gate와 backend 재계산 대상이다. B v0.1의 단일 intent 제한은 연구 계약의 명시적 한계다.
- C는 evidence 표현에는 충분하지만 실행값 정규화, 후보 집합 조회, final missing 판단과 write 권한을 갖지 않는다.
- R은 규칙으로 명백한 표현만 지원하며, unsupported grammar를 억지로 맞추지 않는다.
- 새 공유 정책 결정이 필요한 공백이나 locked semantic oracle schema 자체의 결함은 발견하지 않았다. A01의 `CONTRACT_GAP`은 후보 A local contract의 completed grounding 결함을 뜻한다.
- semantic oracle schema는 해시로 보호했으며 외부 JSON Schema 패키지를 새로 설치하지 않았다. 이 검증 한계는 manifest에 기록했다.

## 무결성 및 실행하지 않은 항목

- locked JSONL SHA-256: `17c7d4ef10aff8171d422c248431d3c34faaf03bf7282fab5dc3abcfa2389b57`
- semantic oracle schema SHA-256: `cfe0ed947f6bd709e0e3ba60a8daf63fb1f201614cb11d90b823931b060a3b1d`
- lock 상태: `locked`
- locked 사례 수: 56건
- 모델 호출: 0건
- backend server·Schedule API·DB 호출: 0건

Ollama, MLX, Qwen, model transport, backend server, Schedule DB, Schedule API와 production write를 실행하지 않았다. DecisionEnvelope와 processor dry mapping도 시작하지 않았다.

## 산출물

- `contracts.py`: A·B·C·R strict validator와 공통 backend gate
- `rule_baseline.py`: R의 bounded deterministic extractor와 지원 범위 분류
- `validate_contracts.py`: 무결성·locked coverage·fixture 실행 및 결과 생성기
- `mutation-fixtures-v0.1.jsonl`: 결정적 mutation 42개
- `contract-validation-results-v0.1.jsonl`: 최소 정상 출력 4건, locked 분류 224건, mutation 실행 141건
- `contract-validation-manifest-v0.1.json`: 입력·출력 해시, 환경, 제약과 집계
- `test_contracts.py`: 계약·coverage·mutation·무결성 단위 테스트

