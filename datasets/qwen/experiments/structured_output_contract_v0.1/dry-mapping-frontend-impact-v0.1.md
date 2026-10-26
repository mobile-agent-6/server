# Issue #30 dry mapping — 프론트 영향 기술 사실

- 상태: `PROPOSED`
- 범위: 현행 Assistant 공개 응답 schema와 processor 생성 시점의 기술 사실만 기록한다.

1. `AssistantCandidate`는 `candidateId`, `title`, `scheduledAt`, `category`만 제공한다. `completed`는 제공하지 않는다.
2. `AssistantResult`는 단일 kind union이다. 한 write 성공 결과와 다음 작업 질문을 한 응답에 동시에 담을 수 없다.
3. LIST의 `text`는 provider Decision에 들어 있고 `ScheduleReadPort.list` 호출보다 먼저 정해진다. 실제 `items`는 backend가 조회 뒤 추가한다.
4. candidate 화면에는 실제 Schedule ID가 아니라 opaque `candidateId`가 제공된다. selection 입력도 이 ID를 사용한다.
5. confirmation은 target summary와 update changes를 제공하지만 이 시점에는 write가 발생하지 않았다.
6. write 성공 outcome은 한 개의 `affectedScheduleIds`를 제공한다. 실제 committed before/after 상세값은 결과에 없다.
7. candidate selection과 confirmation은 각각 별도 result kind다. 이전/다음 결과와 결합하는 public queue 필드는 없다.
8. TTS가 직접 사용할 구조화 데이터는 없고 결과의 `text`만 있다. 카드 표현은 result kind별 typed fields에 의존한다.
9. 미지원 기능 전용 public result kind는 없다. 현행 종류로 표현할 정책을 이 연구에서 새로 정하지 않았다.

표현 문구, 카드 배치, 낭독 건수와 TTS 방식은 이번 후보 의미 점수에 포함하지 않았다. 이 파일은 검토 요청서나 정책 제안서가 아니다.
