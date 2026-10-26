# Issue #30 DecisionEnvelope / Assistant processor dry mapping v0.1

- 상태: `PROPOSED`
- 작성일: 2026-10-25
- 평가 단위: locked 56건 × 후보 A·B·C·R = 224건
- 실제 모델·서버·DB·Schedule write: 0건
- 최종 `unsafe_accept`: 0건

이 문서는 후보 출력 계약 검증 뒤 현재 backend `DecisionEnvelope`와 Assistant processor의 실제 private plan 함수를 연결한 연구 결과다. public API나 production 구현을 변경하지 않았으며, 문구·낭독·카드 배치는 후보 의미 점수에서 제외했다.

## 1. 실행 경계

실제로 호출한 production 함수는 다음과 같다.

- `app.modules.assistant.processor._provider_decision_plan`
- `app.modules.assistant.processor._prepare`
- `app.modules.assistant.processor._from_saved_selection`
- `app.modules.assistant.processor._from_saved_confirmation`
- provider validation probe의 `_call_provider`, `_failure`

private 함수 의존은 연구용 검증 경계다. `process_one`, `_finalize`, `SafeWritePort.execute`, DB session/engine, HTTP client, 모델 transport는 호출하지 않았다. production `process_one`의 외부 예외 경계만 동일한 `_failure(INTERNAL_ERROR)` plan으로 재현했다.

`FakeScheduleReadPort`는 각 locked 사례의 `initialSchedules`만 사용한다. `get`과 `[from,to)`·category·completed·scheduledAt 오름차순·limit을 구현하고 삭제 항목을 제외한다. update/delete용 전체 보존기간 제목 검색은 production port 기능이 아니므로 `adapter_title_search`로 호출 기록을 남기며 I5 내부 구현 공백으로 분류했다. 이 matcher의 exact/substring 규칙은 fixture 연결을 위한 연구 adapter일 뿐 production 지원이나 제품 정책이 아니다. 이를 사용한 54행은 `researchAdapterDependencies`와 `currentBackendPathComplete=false`로 표시했다. write 메서드는 없다.

시계는 각 사례의 `referenceTime`과 `serverTimeTrusted`를 사용하고, UUID는 `(case ID, candidate, counter)`의 SHA-256 기반 결정적 UUIDv4다. wall clock은 평가에 사용하지 않았다.

## 2. 후보 정의와 mapping

| 후보 | 후보 출력 → Decision mapping | 제한 |
|---|---|---|
| A | DATA v2.6의 7 action을 그대로 해석한다. `ASK→ask`, `LIST→list_intent`, `CREATE→create_intent`, `SELECT_TARGET→select_candidate`, `CONFIRM_UPDATE/DELETE→confirm`; `ANSWER`는 대응 Decision이 없다. controller context, dynamic schema, 후보 ID 제한과 기존 write guard를 먼저 적용한다. | 기존 half-minute 손실, LIST completed grounding 공백, 단일 action 상태기를 개선하지 않았다. |
| B | 1단계 intent와 2단계 정규화 실행 정보를 local validation 및 common gate 뒤 `DecisionEnvelope`로 변환한다. | 정규화 값은 신뢰하지 않는다. 다중 작업 4건과 time-only 다중 후보의 per-target 정규화는 현행 B 구조로 표현하지 않는다. |
| C | 사용자 원문의 역할별 evidence span만 local validation한다. 연구 backend normalizer가 날짜·시각·category·completed를 계산하고, fake read 결과로 target별 실행값을 만든 뒤 Decision으로 변환한다. | 모델은 Schedule ID·선택·성공 여부를 만들지 않는다. 모든 span은 지정 turn의 NFKC/공백 정리 substring 검증을 통과했다. |
| R | 모델은 intent만 제공하고, 지원되는 문법의 slot/evidence를 결정적 규칙으로 추출한 뒤 Decision으로 변환한다. | 기존 10건 safe reject와 4건 구조적 표현 불가를 성공으로 승격하지 않았다. |

후보 출력은 항상 candidate local validator → common backend gate → Decision mapping 순서다. selection·confirmation은 저장된 opaque candidate/question state를 actual private 함수로 처리하며 추가 모델 호출을 허용하지 않았다.

## 3. 224건 결과

### 3.1 시스템 결과

| 후보 | correct | safe_reject | over_ask | unsafe_accept |
|---|---:|---:|---:|---:|
| A | 5 | 47 | 4 | 0 |
| B | 26 | 27 | 3 | 0 |
| C | 26 | 27 | 3 | 0 |
| R | 14 | 33 | 9 | 0 |

이 수치는 모델 품질 점수가 아니다. 현행 Decision/API/processor로 연결되지 않는 사례, fail-closed 결과와 production 내부 결함을 포함한 end-to-end dry mapping 결과다.

### 3.2 local validation과 common gate

| 후보 | local accept | local reject | local not-run | gate accept | gate reject | gate not-run |
|---|---:|---:|---:|---:|---:|---:|
| A | 10 | 3 | 43 | 9 | 1 | 46 |
| B | 49 | 3 | 4 | 48 | 1 | 7 |
| C | 56 | 0 | 0 | 55 | 1 | 0 |
| R | 52 | 0 | 4 | 50 | 2 | 4 |

A의 local accept 뒤 gate reject 1건은 DATA v2.6의 `3시 반` 분 손실을 그대로 재현한 것이다. local 결과와 common gate 결과를 합치지 않았다.

최종 mapping 결과는 `decision_envelope` 84, `structurally_unrepresentable` 51, `current_contract_unrepresentable` 54, `provider_validation_exception` 3, `processor_internal_error` 18, 그 밖의 `safe_reject` 14건이다. processor exception은 turn 기준 18회다.

### 3.3 processor plan / public result / pending write

모든 turn의 processor plan은 `need_more_info` 66, `candidate_selection` 9, `confirmation` 19, `outcome` 19, `failure` 20, `pending_write` 51, plan 없음 109건이다. public result는 `need_more_info` 66, `candidate_selection` 9, `confirmation` 19, `outcome` 19, `failure` 20, result 없음 160건이다.

사례별 최종 plan 기준 pending write는 51건(A 4, B 18, C 18, R 11)이다. 이는 write 실행이 아니라 검증된 write 가능 plan이다. 모든 pending intent의 대상 수는 1 이하였고, final turn의 pending 값은 모두 oracle expected write와 일치했다. confirmation 이전 update/delete pending write와 LIST pending write는 0건이다.

Schedule read 호출은 총 138회다. LIST는 actual `ScheduleReadPort.list(limit=101)` 경로를 탔고, 별도 101건 fixture에서 범위 축소 `need_more_info`를 확인했다. update/delete는 후보 재조회와 selection 재조회를 기록했다. 각 행에는 예상 read method와 실제 호출을 함께 저장했으며 unexpected read는 0건이다.

## 4. 확인한 processor 동작

- `serverTimeTrusted=false`의 relative Decision은 actual processor에서 `need_more_info`로 바뀐다.
- create Decision은 즉시 write가 아니라 `PendingCreateIntent`가 된다.
- LIST는 fake port를 실제 호출하고 0건·여러 건·100건 초과를 구분한다.
- update/delete는 target을 다시 읽고, confirmation 전 write plan을 만들지 않는다.
- candidate selection은 actual Schedule ID와 다른 opaque candidate ID를 사용한다.
- 저장 selection·confirmation은 모델 호출 0건으로 처리했다.
- 존재하지 않거나 직전 candidate map 밖에 있는 실제 Schedule ID는 saved selection으로 resolve되지 않는다.
- 표시가 완전히 같은 후보는 자동 선택하지 않고 현재 `TARGET_AMBIGUOUS` failure로 끝난다.
- no-op update와 이미 적용된 completed 변경은 제거되어 pending write를 만들지 않는다.
- invalid provider 출력은 `MODEL_OUTPUT_INVALID` public failure로 변환된다.
- 모든 public result object는 현행 `AssistantResult` union으로 재검증됐다.

주의: invalid/off-candidate saved selection에 `_from_saved_selection`이 `None`을 반환하면 현재 `process_one`은 provider fallback으로 진행할 수 있다. 이 연구 harness는 D10에 따라 재호출하지 않고 safe reject로 멈췄으며, 이를 backend 내부 보완 항목으로 기록한다.

## 5. 현재 계약 표현 가능성

| ID | 판정 대상 | 분류 |
|---|---|---|
| E01 | 등록 진행 질문을 `need_more_info`로 표현 | 현행 코드와 공개 API로 표현 가능 |
| E02 | 후보 선택 응답에 completed 상태 표시 | 공개 API 변경 없이는 표현 불가 |
| E03 | 한 작업 성공과 다음 작업 질문을 한 결과에 결합 | 공개 API 변경 없이는 표현 불가 |
| E04 | 실제 변경 결과에 근거한 상세 성공 문구 | backend 내부 구현 보완 필요 |
| E05 | 미지원 기능만 요청했을 때의 전용 결과 종류 | 공개 API 변경 없이는 표현 불가 |
| E06 | 조회 완료 후 actual items에 맞는 안내 문구 | backend 내부 구현 보완 필요 |
| E07 | 날짜 없는 update/delete의 전체 보존기간 제목 검색 | backend 내부 구현 보완 필요 |
| E08 | update/delete 후보 0건의 자연스러운 결과 | backend 내부 구현 보완 필요 |
| E09 | 구분 가능한 동일 표시 후보를 먼저 질문 | backend 내부 구현 보완 필요 |
| E10 | control state를 원 질문과 연결하고 종료 시 초기화 | backend 내부 구현 보완 필요 |
| E11 | adapter의 결정적 제목 유사도 기준 | backend 내부 구현 보완 필요 |
| E12 | 여러 작업 queue와 성공+다음 질문 상태 | 공개 API 변경 없이는 표현 불가 |

E06의 정확한 문구·TTS·카드 표현, E02의 화면 표시 방식 등 표현 정책은 관련 영역 확인이 필요하지만 이번 연구에서 새 정책으로 정하지 않았다.

## 6. 발견 결함과 계약 공백

1. **scheduledAt update processor defect**: `DecisionConfirm`/`DecisionSelectCandidate`의 `AssistantConfirmationChanges.scheduled_at`은 최초 validation 뒤 `datetime`이지만 `_changes_without_noops`가 이를 문자열 전용 `ApiDateTime` schema에 다시 넣는다. actual `_provider_decision_plan`이 `ValidationError`를 내며 production의 outer boundary에서는 `INTERNAL_ERROR`로 fail-closed 된다. 18 turn에서 관찰됐다.
2. **saved selection fallback**: invalid/off-candidate opaque ID가 `_from_saved_selection`에서 `None`이면 provider fallback 가능성이 있어 “후보 조회 뒤 모델 재호출 없음”과 맞지 않는다.
3. **title lookup / zero target**: `ScheduleReadPort`와 Decision에는 전체 보존기간 title search 및 backend-owned no-match Decision이 없다.
4. **identical display**: 현재 processor는 표시가 같은 후보를 `TARGET_AMBIGUOUS`로 종결한다. 구분 가능한 추가 필드를 먼저 묻는 경로는 없다.
5. **list text timing**: Decision의 provider text는 `list()` 이전에 확정되므로 actual items 기반 안내 문구를 만들 수 없다.
6. **A 그대로의 결함**: half-minute 손실과 LIST completed local grounding 공백을 mapping에서 보정하지 않았다. common gate가 위험 effect를 막는다.

semantic oracle schema를 변경해야 하는 결함은 발견하지 않았다. 다만 위 항목은 production 내부 구현 또는 공개 API 판단이 필요한 계약 공백이며, 이 연구 산출물만으로 확정하지 않는다.

## 7. 프론트 영향 기술 사실

상세 목록은 `dry-mapping-frontend-impact-v0.1.md`에 분리했다. 핵심은 `AssistantCandidate`에 completed가 없고, result union이 성공과 다음 질문을 동시에 담지 못하며, LIST text가 items 조회 전에 정해지고, TTS 입력은 `text`뿐이라는 점이다.

## 8. 검증과 환경

- Python: 기존 sibling backend venv의 CPython 3.11.16
- Pydantic: 2.13.5
- SQLAlchemy: 2.0.54
- 자동 테스트: 19개 통과
- 새 package 설치: 없음
- network: 사용 안 함; matrix 재생성 중 socket과 SQLAlchemy engine 생성을 실패하도록 patch한 동적 검증 통과
- bytecode/DB/log 부산물: 없음
- locked 및 기존 contract-validation 산출물: 시작·종료 해시 확인

실행 명령:

```text
../server/backend/.venv/bin/python -B -m unittest discover -s .omx/research/issue30 -p 'test_dry_mapping.py' -v
```

실행하지 않은 항목: 실제 모델(Ollama/MLX/Qwen), model transport, backend app startup, 실제 server, DB engine/session, ORM, Schedule API HTTP, Schedule write, frontend, GitHub 변경.
