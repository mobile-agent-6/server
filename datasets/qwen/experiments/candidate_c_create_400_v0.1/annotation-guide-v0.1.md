# Candidate C create annotation guide v0.1

상태: `BE-PROPOSED / DATA·AI 확인 전`

## 1. 기준 계약

새 모델 출력 계약을 만들지 않는다. 다음 Issue #30 산출물을 하나의 contract bundle로
고정한다.

1. `contracts.validate_candidate_c`
2. `common_gate_v0_4.common_backend_gate_v0_4`
3. `dry_mapping._view_from_c_operation`
4. `semantic-oracle-schema-v0.1.json`
5. `candidate-c-prompt-v0.1.txt`
6. 학습 입력 동등성 확인용 `ollama_eval.render_extraction_context`

create profile은 위 계약에 필드를 추가하지 않고 `kind=create`와 역할 배치만 제한한다.

| Issue 표현 | 추적 계약 표현 |
|---|---|
| intent / operationType | `operations[].kind` |
| operationCue | 후보 C 출력 필드 없음. semantic oracle에만 기록 |
| corrections | span의 `adopted`, `superseded`, `correction_marker` |
| suggestedMissingInformation | `missingInformation` |

## 2. create 역할 규칙

- `targetEvidence`: 항상 `{}`
- `changeEvidence.title`: 사용자가 말한 일정 제목의 원문 근거
- `changeEvidence.date`: 날짜 근거
- `changeEvidence.time`: 시각 근거
- `changeEvidence.category`: INBOUND·INVENTORY·OUTBOUND·RETURN을 직접 가리키는 원문 근거
- `filterEvidence`: 항상 `{}`
- 모델 출력에는 RFC3339, Schedule ID, 최종 성공 문구를 넣지 않는다.
- category cue가 없지만 업무 제목이 명확하면 backend가 `OTHER`로 계산한다. category를 누락 정보로 묻지 않는다.

## 3. 미지원 기능과 동의

- 일정 등록과 미지원 기능이 섞였으면 cue는 해당 operation의 `unsupportedEvidence`에만 둔다.
- 미지원 기능만 있으면 top-level `unsupportedEvidence`에 두고 `operations=[]`로 둔다.
- 같은 cue를 top-level과 operation 내부에 중복하지 않는다.
- 동의 전 create 효과, 거절 뒤 create 효과, 미지원 단독 create 효과는 common gate가 거절해야 한다.
- 현재 detector 범위인 알림·리마인더·푸시·문자 표현만 시범 데이터에 사용한다.

이 create profile은 mixed unsupported cue를 operation 내부에만 두는 규칙을 사용한다.
Issue #30의 일부 이상적 fixture helper가 같은 cue를 top-level에도 복제하는 동작은
실행 안전성과 별개의 채점 일관성 `TBD-AI/DATA`로 남긴다. 이 시범에서는
profile에 명시한 operation-only를 고정 정답으로 사용한다.

## 4. 누락과 후속 답변

- `task_or_title`, `date`, `time`, `ampm`, `absolute_datetime_due_to_untrusted_time`만 create 시범에서 사용한다.
- `detectedAtTurn <= 현재 turn < resolvedAtTurn`인 항목만 현재 후보 C 출력의 `missingInformation`에 둔다.
- 후속 답변은 기존 task reference time과 이미 확정한 근거를 유지한다.
- `serverTimeTrusted=false`일 때 상대 날짜가 필요하면 절대 날짜·시각을 다시 묻는다.
- `modelCallPolicy=no_model_call`인 turn의 Candidate C target은 구조 회귀 검사에는
  포함하지만 학습 row와 모델 채점 분모에서는 제외한다. 동의·거절 결과는
  backend-owned control state로 판정한다.

## 5. 정정

현 v0.1 validator가 표현 가능한 정정은 같은 role에서 다음 순서를 만족해야 한다.

`superseded < correction_marker < adopted`

다음 형태는 v0.1 계약을 바꾸지 않는 create pilot 규칙을 적용한다.

- 새 값 뒤에 정정 표지가 오는 표현: `오후 3시에 등록해줘, 2시는 말고`
- 명시적 정정 표지 없는 후속 답변: `아, 오후 3시`

이 두 형태에서는 최종 adopted 값만 Candidate C target에 둔다. superseded 값과
correction marker는 넣지 않는다. 다만 정정 전 값 식별·correction exact 지표에서는 해당 사례를
별도 분모로 보고하고 점수에 포함하지 않는다. 이 규칙은 create pilot의
`BE-PROPOSED` annotation 규칙이며 v0.1 공개 계약이나 validator를 변경하지 않는다.

실행 안전성은 adopted 표시 자체를 신뢰해 주어지지 않는다. create profile은
같은 change slot에 서로 다른 값으로 정규화되는 adopted span이 두 개 이상이면
`C_PROFILE_CONFLICTING_ADOPTED_VALUES`로 fail-closed 한다. adopted span이 두 개 이상인 slot에서
하나라도 정규화할 수 없거나 오전·오후가 없는 `3시`처럼 모호하면 같은 코드로
fail-closed 한다. 둘 다 정규화 가능하고 같은 값이면 과잉 거절하지 않는다.
Issue #30 v0.1 normalizer의
배열 순서 선택 동작은 수정하지 않고 BE 후속 항목으로 기록한다.

완전히 정규화 가능한 동치 adopted span이 여러 개인 출력을 exact-match에서
동일 정답으로 보는 방식은 `TBD-AI/DATA`다. 현재 시범 학습 target은 slot당
필요한 adopted 근거만 두고, 모델 평가에서는 semantic 값 일치와 span exact를
별도 보고한다.

등록이 이미 끝난 뒤의 후속 발화는 create 정정이 아니라 새 update 작업이다. 따라서
turn 간 marker 없는 정정 시범은 제목 누락 등으로 create가 아직 보류된 상태에서만
작성한다.

제목 정정이 category도 바꾸고 `old < marker < new` 순서가 성립하면 title과 category
각 role에 자신의 adopted·superseded 근거를 기록한다. 동일 marker의 재사용은 허용하되
실행 category는 최종 adopted cue로만 계산한다.

## 6. family·lineage·분할

- family는 같은 대화 구조와 slot 조합을 공유하는 사례 묶음이다.
- lineage는 같은 blueprint 또는 생성 규칙에서 나온 모든 변형이다.
- 같은 lineage는 split을 가로지르지 않는다.
- 새 Test 50은 기존 400 출처 0건, 신규 family만 사용한다.
- Validation과 Test는 family당 최대 3대화다.
- 기존 validation 출처는 train으로 이동하지 않는다.
- Test 실패 사례는 다음 버전 validation으로만 사용하며 train으로 이동하지 않는다.

## 7. 검수 기록

대화별 검수 이벤트에 reviewer ID, 판정, 사유 코드, 수정 전후 label hash,
timestamp, guide/schema/validator hash를 남긴다. Claude 자문은 DATA 독립 검수나 팀
승인으로 표기하지 않는다.
