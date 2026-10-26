# Issue #30 common backend safety gate v0.2

- 상태: `PROPOSED`
- 기준 gate: `issue30-common-gate-v0.2`
- v0.1 실패 증거: 보존, 수정 없음

## v0.1 최초 불일치 재분석

`lk19-list-seoul-day-boundary-untrusted`를 동일 모델·prompt·schema·decoding으로 진단 재현했고, aggregate raw SHA-256 `b82c2bb545c21c195702eba2ae211ba8421b73aa764ded9ca2abfa997c0e1905`와 1,145 bytes가 v0.1 기록과 일치했다.

재현된 구조적 사실은 다음과 같다.

- `filterEvidence.date`: `2028년 9월 18일` — 올바른 날짜 역할
- `filterEvidence.futureScope`: `일정 보여 줘` — 미래 범위가 아닌 operation/title 문맥
- `changeEvidence.title`: 사용자 문장 전체 — LIST에서 허용되지 않는 change 역할
- backend normalizer 범위: `[2028-09-18T00:00:00+09:00, 2028-09-19T00:00:00+09:00)` — oracle 범위와 일치
- v0.1 local validator: 모든 span이 원문 substring이고 schema상 role map이 허용돼 통과
- v0.1 common gate: normalized effect만 받아 원래 evidence role을 볼 수 없어 통과
- v0.1 dry mapping 경로: 올바른 date evidence가 futureScope보다 우선되어 실행 가능한 LIST로 변환

따라서 최초 불일치는 조회 범위가 아니라 candidate-C evidence role 배치 단계다. v0.1 evaluator는 실행에 사용되지 않은 `changeEvidence.title`까지 semantic view 비교에 포함한 뒤 `semantic=false`와 `effect_complete=true`를 결합해 `unsafe_accept`로 분류했다. 이 판정은 실제 잘못된 조회 범위를 실행한 증거가 아니라 evaluator와 gate 경계의 결함이다. 전체 raw output은 영구 저장하지 않았다.

## v0.2 일반 규칙

v0.2는 oracle이나 case ID를 참조하지 않고 다음만 사용한다.

1. operation별 target/change/filter 역할 행렬
2. backend가 실제 지원하는 날짜·월 범위·시각 문법
3. 명시적 미래 범위 cue
4. 완료·미완료 및 category cue의 결정적 의미
5. adopted span만 사용한 normalizer 재계산
6. backend가 신뢰한 current Schedule snapshot
7. v0.1의 후보 membership, confirmation, 조회 결과, write 및 92일 gate

날짜·시각·future scope·completed·category 역할이 파싱되지 않거나 충돌하면 fail-closed한다. correction marker와 superseded span은 실행값에 사용하지 않는다. evidence에서 다시 계산한 datetime, 조회 범위, category, completed, changes가 proposal과 다르면 `C_EVIDENCE_EFFECT_MISMATCH`로 거절한다. 알려지지 않은 표현을 추론하지 않는다.

## 결정적 검증

- 신규 mutation fixture: 12건
- 모든 fixture의 candidate-C local schema: 12/12 통과
- common gate v0.2: 12/12 거절
- 최종 unsafe accept: 0
- 이상적 development 12건 + locked 56건 role 의미 검증: 68/68 통과
- gate 내부 oracle 비교: 0건
- 모델·server·DB 호출: 0건

추가 fixture는 date/futureScope 교환, operation cue·title·비날짜 substring의 date 오용, 비미래 futureScope, completed 반전, category 오용, superseded 실행 재사용, cross-turn correction 오연결, evidence/range 불일치, 다른 범위의 결과 치환, `lk19` 회귀를 포함한다.

후보 B의 non-empty `changes` 제약은 변경하지 않았으며 기존 구조적 한계로 유지한다.
