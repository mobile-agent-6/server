# Issue #30 trusted-policy 입력 완전성 보완 v0.4

- 작성일: 2026-10-26
- 상태: `PROPOSED` 연구용 보완
- 범위: v0.3의 제품 정책·detector·모델 결과는 변경하지 않고, 실행 효과에 사용되는 trusted turn 입력의 완전성만 검증한다.
- 모델 실행: 없음

## 발견 원인

v0.3은 전달받은 turn 목록에서 policy context를 다시 계산해 변조를 검사하지만, 호출자가 현재 사용자 turn이나 이전 turn을 목록에서 누락한 경우에는 누락 자체를 판별하지 못했다. 실제 evaluation은 전체 turn 목록을 전달했으므로 v0.3 locked 결과의 집계에는 영향을 주지 않지만, production 안전 계층의 입력 계약으로는 fail-closed 조건이 부족했다.

## v0.4 규칙

실행 가능한 효과를 검사할 때 다음 조건을 모두 요구한다.

- `through_turn`에 해당하는 현재 사용자 text turn이 정확히 하나 존재
- turn 번호 중복 없음
- turn 목록이 오름차순
- `1..through_turn` event prefix에 누락 없음
- 조건 위반 시 각각 `POLICY_INPUTS_INCOMPLETE`, `POLICY_INPUTS_DUPLICATE_TURN`, `POLICY_INPUTS_OUT_OF_ORDER` 또는 `POLICY_INPUTS_INVALID`로 거절

ASK·질문·미완성 view처럼 실행 효과가 없는 출력에는 기존 v0.3 동작을 유지한다. production이 전체 대화를 전달하지 않으려면 임의로 잘린 목록을 재분석하지 말고 동등한 trusted policy state와 event 연속성 근거를 영속해야 한다.

## 검증

- 현재 turn 누락: 거절
- 최신 미지원 요청 turn 생략: 거절
- 중복 turn: 거절
- 순서가 뒤바뀐 turn: 거절
- 중간 turn 누락: 거절
- development 12건과 locked 56건의 모든 text turn prefix: 통과
- v0.1~v0.3 회귀 포함 전체 테스트: 92/92 통과
- v0.3 보호 파일 해시: 불변
- 모델·Ollama·backend server·DB·Schedule API·write: 실행하지 않음

## 판정과 한계

v0.3의 `unsafe_accept=0`은 전체 turn 목록을 사용한 고정 평가 결과로 유지된다. v0.4는 코드 검토에서 발견된 입력 누락 우회 가능성을 결정적으로 차단했지만, v0.4 gate를 사용한 전체 모델 평가는 수행하지 않았다. v0.4는 모델 출력이나 prompt를 바꾸지 않으며, production 반영 전에는 실제 Assistant control state가 현재 turn·질문 연결·작업 경계를 같은 수준으로 제공하는지 별도 검증해야 한다.
