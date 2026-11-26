# Candidate C 일정 등록 30건 시범 검증 요약

- 작성일: 2026-10-27
- 데이터 행 상태: `PROPOSED_UNREVIEWED` (`reviewState=single-review proposed`)
- 검수 상태: `CLAUDE_INDEPENDENT_REVIEW` — 2차 30/30 `ACCEPT`, DATA 담당 확인 전
- 자동검증: `PASS`
- 자동검증 판정값: `READY_FOR_INDEPENDENT_REVIEW` (validator 출력 고정값)

이 판정은 400건 확장 승인, DATA 담당 검수 완료 또는 팀 채택을 뜻하지 않는다.

## 결과

| 항목 | 결과 |
|---|---:|
| 시범 대화 | 30건 |
| text turn | 42건 |
| 버킷 | 5개 × 6건 |
| 후보 C local accept | 42 turn |
| common gate accept | 41 turn |
| 예상된 gate 거절 | 1 turn (`PAST_CREATE`) |
| oracle expectedWrite=0인데 Decision write 가능 | 0건 |
| oracle과 다른 write 값 수락 | 0건 |
| superseded 값 실행 | 0건 |
| mapping exception | 0건 |
| mutation fixture | 20건 |
| mutation 안전 분류 경로 | 8개 |
| 의도적 unsafe mutation 검출 | 2건 |
| mutation 분류 누락·오분류 | 0건 |
| mutation을 정상 accept로 판정 | 0건 |
| 학습 row 미리보기 | 37행 |
| 동일 evaluator 입력의 target 충돌 | 0건 |
| semantic schema 보조 검사 오류 | 0건 |
| 기존 자료 대화 정확 중복 | 0건 |
| 기존 자료 발화 정확 중복 | 0건 |
| 최대 대화 3-gram Jaccard | 0.407407 |
| 최대 발화 3-gram Jaccard | 0.6 |

## Claude 1차 검토 필수 수정 반영

1. evaluator와 동일하게 renderer stage를 대문자 `EVIDENCE`로 고정했다.
2. 최종 oracle evidence를 이전 turn에 역투영하지 않고, 42개 text turn마다 명시적
   Candidate C target을 저장한다.
3. case 18은 제목이 누락돼 create가 보류된 상태에서 후속 답변이 시각을 대체하는
   시나리오로 다시 작성했다. 이미 등록된 일정을 create 정정으로 취급하지 않는다.
4. Issue #30의 `executable_effect`를 재사용하고 Candidate C 출력의 missing 자기 주장으로
   실행 가능성을 낮추지 않는다. Decision/processor plan 단계까지 dry mapping해
   oracle expectedWriteCount와 비교한다.
5. untrusted missing 삭제, AM/PM missing 삭제, 동의 cue 삭제·거절 뒤 실행, correction
   marker 삭제, 근거 없는 시각, role 오류 등 mutation 18개를 8개 안전 경로로
   분류했다.

## Claude 2차 재검수 필수 수정 반영

1. write 여부만 보지 않고 pending write의 `title`, `scheduledAt`, `category`를
   oracle `expectedWrite.values`와 비교한다.
2. 정정 전 시각을 실행하는 mutation과 정정 전 제목·category를 실행하는
   mutation 2건은 실제 pending write까지 진행하지만 harness가
   `unsafe_superseded_executed`로 정확히 검출했다.
3. 같은 change slot의 서로 다른 정규화 adopted 값은 create profile이
   `C_PROFILE_CONFLICTING_ADOPTED_VALUES`로 거절한다. 배열 순서를 바꿔도 같다.
4. mapping exception이 gold나 mutation에서 한 건이라도 발생하면 run을
   `STOP`으로 판정한다.
5. adopted span이 둘 이상인 slot에서 `3시`처럼 하나라도 정규화 불가·모호하면
   같은 profile 코드로 fail-closed 한다. 모호한 span의 두 배열 순서 mutation을
   모두 거절했고, 둘 다 정규화 가능하고 동치인 표현은 통과했다.

## 정정 annotation 결정

기존 Candidate C v0.1은 변경하지 않는다.

- `old < marker < new` 순서가 성립하면 adopted·superseded·marker를 모두 라벨한다.
- marker가 새 값 뒤에 있거나, 보류 중인 turn 간 대체에 marker가 없으면 최종 adopted
  값만 라벨한다.
- 후자의 두 형태는 correction provenance 지표에서 제외하고 별도 분모로 보고한다.
- 일정 생성이 끝난 뒤의 후속 변경은 update 작업이다.

이 규칙은 Claude가 권장한 A안이며 create pilot의 `BE-PROPOSED` annotation 결정이다.
DATA·AI 확인 전에는 팀 확정 규칙으로 표기하지 않는다.
같은 slot에 서로 다른 adopted 값을 둘 이상 두는 출력은 profile에서 fail-closed
하며, Issue #30 v0.1 normalizer의 배열 순서 한계는 BE 후속 항목으로 남긴다.

## 안전 검증

- 미지원 cue를 누락하도록 변조하면 backend-owned policy state가 동의 전 실행을
  `CONSENT_REQUIRED_BEFORE_EFFECT`로 거절한다.
- 사용자가 거절한 뒤 실행하도록 변조하면 `CONSENT_DECLINED`로 거절한다.
- 신뢰할 수 없는 상대 시간에서 missing label을 삭제해도 processor Decision 단계가
  질문으로 전환하고 pending write를 만들지 않는다.
- 모호한 1~12시에서 AM/PM missing label을 삭제해도 normalizer가 질문 경로를 유지한다.
- 과거 시각 등록은 `PAST_CREATE`로 거절된다.
- 정정 전 값으로 pending write가 만들어지는 오류는 oracle 값 비교로
  unsafe로 분류하며 안전한 거절로 잘못 세지 않는다.

`mutationUnsafeAccepts`는 의도적 unsafe 변형이 실제 write에 도달했고 harness가
unsafe로 검출한 건수다. `mutationFinalAccepts`는 mutation을 정상·정답 accept로
판정한 건수이므로, unsafe로 정확히 검출된 변형은 여기에 포함하지 않는다.

## 검증 한계

- 고정 환경에 `jsonschema`와 `fastjsonschema`가 없어 semantic oracle을 공식 제3자
  구현으로 검증하지 못했다.
- schema 파일 해시를 Issue #30 고정값과 대조하고, schema가 사용하는 키워드 전체를
  표준 라이브러리 보조 검사기로 실행했다. 30건 오류는 0건이다.
- 모델 실행·학습, 실제 서버·DB·Schedule write, Test 개봉은 수행하지 않았다.
- Issue #30 v0.1은 model output이 이전 값을 adopted로 잘못 표시하는 경우를
  독립적으로 차단하지 못한다. 이 시범에서는 값 수준 oracle 비교로 평가하며,
  production 안전 gate 보완은 별도 BE 후속 범위다.
- `claude-data-review-01`이 30/30대화·42/42 target을 전수 검수해
  28 `ACCEPT`, 2 `REVISE`(case 18, 21)로 판정했다. 두 oracle 수정 반영 후 같은
  검수자가 2차 재확인에서 30건 모두 `ACCEPT`로 판정했다.
- 검수자는 사용자가 지정한 Claude 세션이다. DATA 담당 확인은 아직 받지 않았다.

## 검수 기록

`pilot/review-events-v0.1.jsonl`에 guide §7 형식의 대화별 이벤트 60건을 남겼다.

| 차수 | 시각(KST) | 판정 | 입력 conversations 해시 |
|---|---|---|---|
| 1차 | 2026-10-27 08:30 | 28 `ACCEPT` / 2 `REVISE` | `37c4…963b` |
| 2차 | 2026-10-27 12:08 | 30 `ACCEPT` | `bf7d…351a` |

1차 입력 파일은 보존되지 않았다. 현재 파일에서 case 18·21의 두 필드만 이전 값으로
되돌리면 1차 해시 `37c4…963b`가 정확히 재현되므로, 1·2차 사이 변경은 이 두 필드뿐이다.
1차 이벤트의 `labelSha256`은 이 재현본에서 계산했다.

## 다음 단계

1. 원격 develop을 재확인하고(base provenance `REMOTE_UNVERIFIED`) 시범 산출물을 commit·PR한다.
2. 100건 확장 전에 검수자 권장 사항을 반영한다: 동의 완료 뒤 다른 누락을 묻는 turn의
   questionPurpose 판정 보완, 기준 시각 다양화, 연도 없는 `M월 D일`, 밤·새벽,
   `오후 12시`, 제목 중복 해소.
3. DATA 담당 확인과 모델 정답률 합격 기준 합의는 별도로 받는다.
