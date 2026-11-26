# AI 구조화 출력 계약 검증 및 제안

- Issue: #30
- 상태: `BE-PROPOSED`
- 제안 주체: BE
- 확인 대기: AI, DATA, 관련 FE 항목, TEAM 합격 기준
- 작성 기준일: 2026-10-26
- 재현 산출물: [`datasets/qwen/experiments/structured_output_contract_v0.1`](../datasets/qwen/experiments/structured_output_contract_v0.1/README.md)

## 1. 결론

BE는 **후보 C(순서형 작업과 역할별 사용자 원문 근거)**를 채택 검토 대상으로 권장한다. 모델은 의도와 사용자 발화에 실제로 존재하는 근거만 구조화하고, 날짜·시각 정규화, 필수 정보 판정, Schedule 후보 ID, 후보 선택, 승인, write와 실제 결과 문구는 backend가 계산·검증한다.

이 권장은 팀 합의나 production 채택이 아니다. 현재 Qwen3 4B 실행에서 후보 C의 locked `correct`는 56건 중 1건에 불과했다. C가 56건의 의미 정답을 모두 **표현할 수 있다**는 결과와, 현재 모델이 그 출력을 **정확히 생성한다**는 결론은 분리해야 한다.

규칙 기준선 R은 안전·비용 비교용으로 유지한다. 후보 A는 기존 DATA v2.6/fresh20 재현 비교용으로만 유지하고, 후보 B는 정규화 실행값을 모델이 제안하는 구조와 다중 작업 제한 때문에 주 권장안으로 선택하지 않는다.

## 2. 범위와 불변 경계

이 검증은 출력 계약과 backend 안전 경계를 비교한다. 다음은 변경하지 않았다.

- 공개 Assistant API, Schedule API, OpenAPI, 오류 code
- production processor, DB·ORM, frontend, 기존 400개 데이터 원본
- 실제 Schedule write
- Qwen production DecisionProvider·Ollama transport 연결
- STT·TTS·Orange Pi 배포

모델과 transport에는 DB, ORM, Schedule write 권한을 제공하지 않는다. 어떤 후보를 쓰더라도 common backend gate와 기존 Assistant 안전 실행 계층을 통과해야 한다.

## 3. 책임 경계

| 영역 | 책임 |
|---|---|
| AI model adapter | 모델별 prompt·chat template·schema 적용, 의도와 원문 evidence 출력, transport 오류 분류 |
| 공통 semantic contract | 순서형 operation, evidence role·turn·span, 정정 전후, 지원하지 않는 요청, 참고용 missing 제안 |
| Backend normalizer | Asia/Seoul 날짜·시각 정규화, 조회 범위, category·completed 필터, 필수 정보 최종 판정 |
| Backend safety gate | 원문 grounding, trusted time, 후보 membership, 다중 후보 임의 선택 금지, 승인, no-op·write 차단 |
| Schedule layer | 실제 후보 조회, 단일 일정 write, 실제 결과 반환 |
| Backend response builder | 실제 조회·변경 결과를 사용한 `items`·성공·실패 사실 생성 |
| FE | backend 결과의 문구·카드·TTS 표현, 추가 질문·후보·승인 interaction 검토 |
| DATA | 후보 독립 의미 정답, evidence label, 분할·중복·오염 검사, 재현 정보 |
| TEAM | 계약 채택, 정책 확정, 안전·정확도·지연 합격 기준, 배포 모델 결정 |

정확한 자연어 문구는 모델이 DB 결과를 추측하게 하지 않는다. backend가 확정한 사실과 공개 API 구조를 FE가 표현하는 것이 기본 경계다.

## 4. 비교 후보

| 후보 | 구조 | 장점 | 핵심 한계 |
|---|---|---|---|
| A | DATA v2.6 7-action controller 보조 구조 | 기존 400개·fresh20을 같은 형식으로 재현 가능 | 단일 action, 제한된 날짜 문법, `반` 분 손실, LIST completed grounding 공백 |
| B | intent 분류 후 operation별 정규화 정보 | 단일 작업 표현력이 높음 | 다중 operation 4건 표현 불가, 모델 정규화 값을 다시 검증해야 함 |
| C | 순서형 operation + 역할별 원문 evidence | 고정 56건 모두 구조적 표현, 실행값·ID·성공 여부를 모델에서 분리 | 출력이 복잡하고 현재 Qwen3 4B 정확도가 매우 낮음 |
| R | 모델은 단일 intent, slot은 규칙 추출 | 저비용·결정적 안전 기준선 | 정정·유사 제목·일부 요일·다중 operation에서 fail-closed |

## 5. 권장 semantic contract

후보 C의 공통 계약은 모델·runtime에 독립적이어야 한다.

```text
intent
operations[]
  operationType
  targetEvidence[]
  changeEvidence[]
  filterEvidence[]
  unsupportedEvidence[]
  corrections[]
    superseded[]
    adopted[]
  suggestedMissingInformation[]
```

각 evidence는 user turn 번호, 해당 turn에 실제로 존재하는 문자열, 역할을 갖는다. local validator는 NFKC 정규화·공백 정리 후 substring을 확인하고 정정 순서와 occurrence를 검사한다.

모델 출력에는 다음을 금지한다.

- RFC3339 실행 시각과 backend 전용 조회 범위
- Schedule ID·candidate ID·question ID 생성
- 다중 후보의 최종 선택
- write 성공·실패·조회 결과 주장
- 사용자 원문에 없는 제목·category·필터·변경값

`suggestedMissingInformation`은 참고용이며 backend가 최종 누락 여부를 판정한다.

## 6. 공통 안전 gate

후보-local schema 수락은 실행 승인이 아니다. backend는 적어도 다음을 공통으로 검사한다.

- trusted server time과 사용자 근거에서 datetime·범위·filter·change 재계산
- evidence의 target·change·filter·unsupported role 적합성 검사
- 현재 user turn과 작업 prefix의 완전성·중복·순서 검사(v0.4)
- Schedule 후보 ID 생성 및 직전 후보 집합 membership 재검증
- 다중 후보 자동 선택·유사 매칭 자동 실행 금지
- 수정·삭제 승인, 한 번의 승인에 일정 한 건만 write
- 빈 수정·no-op·과거 등록·조회 write·승인 전 write 차단
- 지원·미지원 혼합 요청에서 동의 전 실행 차단
- 실제 backend 결과 없이 성공·실패·조회 사실 표시 금지
- 후보 조회 후 선택·승인·결정적 추천을 위한 모델 재호출 금지

v0.4는 v0.3의 정책·detector·모델 결과를 바꾸지 않고, 실행 효과에 사용되는 trusted turn 목록이 `1..through_turn`의 완전한 prefix인지를 보강한다. v0.4 gate의 전체 모델 재실행은 수행하지 않았다.

## 7. 평가 정답과 사례

평가 데이터는 다음 세 층으로 분리한다.

1. 사용자 원문, 필요한 이전 대화, 기준 시각, 초기 Schedule
2. 후보 독립 의미 정답, 역할별 evidence, 누락, 금지된 추론, 기대 Decision·write
3. 선택한 후보별 학습·평가 JSON

공유 패키지에는 development 12건과 locked 56건이 있다. locked의 핵심 무결성은 다음과 같다.

- locked JSONL: `17c7d4ef10aff8171d422c248431d3c34faaf03bf7282fab5dc3abcfa2389b57`
- semantic schema: `cfe0ed947f6bd709e0e3ba60a8daf63fb1f201614cb11d90b823931b060a3b1d`
- lock 상태: `locked`
- locked 사례: 56건

표현 문구·낙독 범위·카드 배치는 모델 의미 점수에서 제외한다. backend 사실과 안전 의미를 유지한 상태에서 FE가 다른 표현을 선택할 수 있어야 한다.

## 8. 검증 결과

### 8.1 계약 표현력

| 후보 | 표현 가능 | 안전한 거절 | 구조적 표현 불가 |
|---|---:|---:|---:|
| A | 9 | 4 | 43 |
| B | 52 | 0 | 4 |
| C | 56 | 0 | 0 |
| R | 42 | 10 | 4 |

42개 mutation을 후보별로 141회 실행했다. local validator가 39건을 거절했고, local을 통과한 102건은 common gate가 거절했다. 최종 `unsafe_accept`는 0건이다.

### 8.2 DecisionEnvelope·processor dry mapping

locked 56건 × A·B·C·R = 224건을 actual backend private planning 함수와 fake read port로 실행했다.

| 후보 | correct | safe_reject | over_ask | unsafe_accept |
|---|---:|---:|---:|---:|
| A | 5 | 47 | 4 | 0 |
| B | 26 | 27 | 3 | 0 |
| C | 26 | 27 | 3 | 0 |
| R | 14 | 33 | 9 | 0 |

pending write plan은 51건이고 모두 대상이 최대 1건이었다. 실제 write는 0건이다. fake Schedule read는 138회였고 예상 밖 read는 0건이다. 전체 보존기간 제목 검색은 production 경로가 없어 54개 행에서 research adapter를 사용했으며, production 지원으로 계산하지 않았다.

### 8.3 Qwen3 4B Ollama baseline v0.3

- 모델: `qwen3:4b`, digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`
- runtime: Ollama 0.32.6, loopback 전용
- decoding: structured schema, `think=false`, `temperature=0`, `seed=29`, `num_predict=1024`, `num_ctx=8192`
- development: 12건 × A·B·C, unsafe 0
- locked: 56건 × A·B·C, unsafe 0

| 후보 | correct | over_ask | safe_reject | unsafe_accept |
|---|---:|---:|---:|---:|
| A | 1 | 3 | 52 | 0 |
| B | 7 | 12 | 37 | 0 |
| C | 1 | 11 | 44 | 0 |

v0.2에서 후보 A의 지원·미지원 혼합 요청 `lk47`이 unsafe accept로 중단되었다. v0.3은 기존 D5 동의 정책을 candidate-neutral trusted policy context로 검사해 같은 raw 출력을 `CONSENT_REQUIRED_BEFORE_EFFECT`로 거절했다. 새 제품 정책은 만들지 않았다.

이 결과로 배포 후보를 선택할 수 없다. 안전 gate가 오답을 많이 거절했을 뿐, 의미 정확도는 배포 수준에 도달하지 않았다.

## 9. 오류 분류

원인과 시스템 결과를 분리한다.

| 원인 | 의미 |
|---|---|
| TRANSPORT | timeout·HTTP envelope·runtime 실패 |
| FORMAT | JSON·candidate schema 위반 |
| MODEL_SEMANTIC | 형식은 맞지만 사용자 의미와 다른 출력 |
| NORMALIZER | 원문 근거는 맞지만 backend 계산이 틀림 |
| MAPPING | 의미·계산은 맞지만 Decision 변환이 틀림 |
| ORACLE_ERROR | 고정 정답·정책 근거가 틀림 |
| CONTRACT_GAP | 현재 규칙으로 유일한 정답을 만들 수 없음 |

시스템 결과는 `correct`, `safe_reject`, `over_ask`, `unsafe_accept`, `transport_fail`로 집계한다. `CONTRACT_GAP`은 모델 점수에서 제외한다.

## 10. 기존 400개 데이터 재사용 판정

현재 저장소 기준 기계적 감사 결과다.

| 분할 | 대화 | 사용자 turn | 그룹 |
|---|---:|---:|---:|
| Train | 250 | 543 | 28 |
| Validation | 50 | 125 | 10 |
| Test 예약 후보 | 100 | 305 | 11 |
| 합계 | 400 | 973 | 49 |

이 데이터는 DATA v2.6 후보 A의 7-action 형식으로 작성됐다. `reviewed` 400건은 2026-10-22 사용자 지시에 따른 상태이며 독립 복수 검수, AI·DATA 공유 계약 채택, 독립 test lock을 의미하지 않는다. 이전 validation 40건 Qwen 수치는 현재 validation 50건의 결과가 아니다.

| 자료 | 판정 |
|---|---|
| 사용자 원문·대화 문맥·초기 일정 | 개인정보, 오염, 의미 중복, 현장 대표성 검토 후 조건부 재사용 |
| 후보 독립 의미 정답 | 현 schema로 변환하고 정책 근거를 다시 검수 |
| C 역할별 원문 evidence | 400개 대화 모두 신규 label이 필요 |
| 기존 `model_outputs`·`expect` | A 비교 외의 계약에서 재검수 |
| 폐기 대상 | 자동 수량을 확정하지 않음. 개별 의미·지원 정책 검수 후 결정 |

기능별 수량은 대화 수가 아니라 `operation × evidence role × 결정 단계 × 안전 시나리오` annotation 단위로 집계하는 것을 권장한다. 최종 목표 수량은 이 이슈에서 확정하지 않는다.

## 11. 모델·runtime 분리

모델을 교체해도 유지할 공통 항목:

- semantic contract·strict validator·common safety gate
- backend normalizer·candidate membership·confirmation·write 제어
- semantic oracle·locked 사례·오류 분류·점수 기준
- Decision mapping·processor 안전 불변식

모델·runtime에 따라 교체할 항목:

- model endpoint·transport·timeout·retry
- prompt·chat template·tool/schema 전달 방식
- decoding·quantization·context 길이·token 제한
- candidate output을 공통 semantic contract로 변환하는 adapter

Ollama와 MLX 차이를 LoRA 효과로 표현하지 않는다. fresh20 효과는 같은 MLX runtime의 base·adapter를 학습 형식인 A로 비교할 때만 판정한다. B·C에서의 fresh20 실행은 탐색 결과로만 기록한다.

## 12. production·공개 API 차이

dry mapping에서 다음을 확인했다. Issue #30에서 production 코드를 고치지 않고 후속 작업으로 분리한다.

### Backend 내부 보완

- `scheduledAt` 수정에서 이미 parse한 `datetime`을 문자열 전용 schema로 재검증하는 경로
- invalid/off-candidate saved selection의 provider fallback 차단
- 날짜 없는 수정·삭제의 전체 보존기간 제목 검색
- 후보 0건과 동일 표시 후보의 backend-owned 결과·추가 질문
- control state의 원 요청 연결·작업 경계·초기화
- 결정적 제목 유사도 기준
- 실제 LIST items·write 결과를 사용한 응답 생성

### 공개 API 검토

- candidate의 `completed` 표시 필드
- 성공 결과와 다음 작업 질문의 조합
- 미지원 기능만 요청한 경우의 결과 종류
- 순서형 다중 작업 queue

표시 문구·카드·TTS·한 번에 읽을 일정 수는 의미 정확도 정답으로 잠그지 않았다. 관련 기술 사실과 대안을 FE 검토 항목으로 제공하되, BE가 FE 표현 선택을 선행 확정하지 않는다.

## 13. 채택 전 확인 항목

| 영역 | 확인 항목 |
|---|---|
| AI | C 출력의 학습·추론 가능성, prompt/schema 복잡도, 지원할 모델·runtime |
| DATA | semantic oracle·evidence annotation 방식, 400개 변환·재라벨링, 오염·중복·test lock |
| FE | 후보 completed, 성공+다음 질문, 미지원 결과, queue, LIST·TTS 표현 가능성 |
| BE | production 보완 항목과 v0.4 trusted policy state 저장·연속성 구현 |
| TEAM | 최종 계약, 안전 gate 0 unsafe 필수 조건, 정확도·over-ask·latency 합격선, 배포 모델 |

## 14. Issue #29 재개 조건

Issue #29의 production DecisionProvider 연결·fine-tuning은 다음 조건 후 재개한다.

1. AI·DATA·관련 FE·TEAM이 출력 계약과 합격 기준을 확인한다.
2. 선택한 계약으로 학습·평가 데이터를 변환하고 의미 정답·evidence·오염을 검수한다.
3. 현재 validation 50건과 분리된 locked 세트로 새 run을 수행한다.
4. local validator·common gate·processor dry mapping에서 mutation `unsafe_accept=0`을 유지한다.
5. backend production 공백을 별도 이슈로 구현·테스한다.
6. 실제 runtime에서 의미 정확도·over-ask·transport 실패·latency·자원을 합의한 기준과 비교한다.
7. fresh20을 비교할 때는 같은 MLX runtime의 base·adapter와 학습 형식 A를 사용하고, 조건이 다른 결과를 LoRA 효과로 표현하지 않는다.
8. fake read·no-write 검증 후 대표 사례의 실제 app–backend–model 통합 시험을 별도로 수행한다.

## 15. 재현과 무결성

공유 패키지의 기본 검증:

```bash
cd datasets/qwen/experiments/structured_output_contract_v0.1
python3 verify_shared.py
python3 audit_existing_400.py --check existing-400-audit-v0.1.json
```

기본 검증은 외부 network, Ollama, backend server, DB, Schedule API, write를 실행하지 않는다. 결과·코드·fixture 해시는 `shared-manifest-v0.1.json`에 기록한다.

v0.3 모델 결과 핵심 해시:

- development results: `adef63d3dad84b71cd1c91e4a5eb90af2bae51634c3e03272398427c57eb6f92`
- locked results: `e0aee43cc3129b1940bb4ab0ce65b405bbf0a15ab77cae99576df57482de5c1f`
- contract validation v0.3 manifest: `383299544c257825aa3175ffc26981c3dbc5204ef0b015a3986b493bfe7c3d6d`
- Ollama eval v0.3 manifest: `8f4f2bb09d6eb7074f131e75474864295f95e7ae60a0e9cee2818585be339939`
- contract validation v0.4 manifest: `339f7cfda48dddfd719da622266cc5ddda0d940ac0421df61f893dc15c1f0d60`

## 16. 최종 제안 상태

- 후보 C: **BE 권장 검토안**, 미채택
- 규칙 기준선 R: 비교 기준선 유지
- 후보 A: 기존 DATA/fresh20 재현 비교용
- 후보 B: 탐색 후보, 정규화 값 신뢰 금지
- Qwen3 4B: 1차 baseline, 배포 모델 미확정
- D1–D12: `BE-PROPOSED` 유지
- 최종 계약·데이터 schema·합격선·FE 표현: 관련 담당자 확인 대기

이 문서와 실험 결과는 데이터 확충·fine-tuning·production 연결 전의 안전 경계 제안이다. 팀 공동 확정으로 승격하기 전에 제안 상태를 유지한다.
