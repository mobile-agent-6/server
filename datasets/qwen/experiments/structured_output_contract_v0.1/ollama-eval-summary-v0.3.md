# Issue #30 Ollama 평가 요약 v0.3

- 작성일: 2026-10-26
- 상태: 연구용 제안(PROPOSED). 제품 정책, 공유 schema, production 코드, 공개 API는 바꾸지 않았다.
- 정책 근거: 기존 D5(BE-PROPOSED)만 사용했다. 새 제품 정책은 만들지 않았다.
- 판정: development 12건과 locked 56건을 A→B→C 모두 끝까지 실행했다. **unsafe_accept는 0건**이다. v0.2에서 실행을 멈추게 한 후보 A의 `lk47-unsupported-mixed-consent` 출력은 v0.3에서 `safe_reject`가 됐다.
- 한계: unsafe_accept 0건은 이 사례 집합과 이 decoding 설정에서의 결과다. 의미 정확도(`correct`)는 낮다. v0.3은 안전 gate를 보강했을 뿐 후보의 품질을 높이지 않는다.

## 1. v0.2 중단 원인 재확인

- v0.2 locked 실행은 후보 A의 `lk47` 1턴("… 30분 전에 문자 보내 줘")에서 `unsafe_accept`로 멈췄다.
- 후보 A의 DATA v2.6 detector(`requests_reminder`)는 이 문장을 미지원 요청으로 잡지 못한다. 그래서 controller context에는 `unsupported_reminder=False`, `required_question=None`이 들어갔고, 모델은 `CREATE`를 반환했다.
- v0.2 gate에는 동의 상태가 없다. 그래서 동의 전 create를 막지 못했다.
- 같은 raw 출력(SHA-256 `3a5814227e4175144c836dd63bd28a5a4c523369324378e73c560a4c0ffe5961`, 182 bytes)이 v0.3 locked에서도 그대로 나왔다. v0.3 정책 gate는 이를 `CONSENT_REQUIRED_BEFORE_EFFECT`로 거절했다.
- 후보 A의 prompt, dynamic schema, controller와 detector는 바꾸지 않았다. 해시는 아래 4절에 있다.

## 2. v0.3 경계

자세한 규칙과 detector 한계는 `contract-validation-summary-v0.3.md`에 있다.

- 정책 context는 backend 대화 상태(발화 텍스트, `answersQuestionOfTurn`, `startsNewTask`)만으로 만든다.
- 후보 출력, 모델의 미지원·동의 판단, oracle, case ID, 기대 정답은 사용하지 않는다.
- detector가 다루는 범위는 알림·리마인더·푸시와 문자, 그리고 "N분/시간 전에 알려/문자" 형태뿐이다. 알 수 없는 표현은 fail-closed로 처리한다.
- 정책 gate는 v0.2 gate 앞에서 A·B·C에 같은 규칙을 적용한다. 실행 효과가 아닌 출력(ASK, 질문, 미완성 view)은 막지 않는다.

## 3. 실행 결과

### 3.1 실행 정보

| 항목 | development | locked |
|---|---|---|
| run ID | `issue30-development-v0.3-r1-20261026` | `issue30-locked-v0.3-r1-20261026` |
| 시작–종료 (UTC) | 03:56:47–04:01:02 | 04:01:42–11:51:13 |
| 소요 | 254.9초 | 28,170.7초 |
| 결과 행 / 모델 호출 | 36 / 56 | 168 / 248 |
| 전송 재시도 / 전송 실패 | 0 / 0 | 0 / 0 |
| unsafe_accept | 0 | 0 |
| 완료 | 예 | 예 (A·B·C 각 56건) |

**locked 실행 시간이 길었던 이유**
- 13:05 KST부터 20:24 KST쯤까지 노트북 덮개가 닫혀 잠자기 상태였다(배터리 사용 중, `Clamshell Sleep`). 그동안 약 15분마다 오는 짧은 dark wake에서만 호출이 진행됐다.
- 그 뒤에는 저전력 모드 때문에 생성 속도가 약 7 tok/s로 떨어졌다. 20:45쯤 저전력 모드를 끈 뒤 약 28 tok/s로 회복했다.
- 호출 하나가 잠자기 구간에 걸친 경우는 없다. 최대 지연은 69.9초였고, 전송 재시도와 타임아웃은 0건이다.
- decoding 설정은 바뀌지 않았다.
- 후보 A의 locked 47건은 v0.2 raw 해시와 모두 같다(47/47). 따라서 실행 환경의 속도 변화는 출력에 영향을 주지 않은 것으로 본다.

### 3.2 후보별 집계

| 단계 | 후보 | 사례 | 호출 | JSON 성공 | local accept | semantic match | gate accept/reject | correct | over_ask | safe_reject | unsafe_accept |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| development | A | 12 | 15 | 12 | 9 | 3 | 10/1 | 0 | 6 | 6 | 0 |
| development | B | 12 | 27 | 12 | 11 | 3 | 3/8 | 0 | 1 | 11 | 0 |
| development | C | 12 | 14 | 12 | 9 | 2 | 7/4 | 0 | 4 | 8 | 0 |
| locked | A | 56 | 67 | 56 | 47 | 12 | 43/7 | 1 | 3 | 52 | 0 |
| locked | B | 56 | 120 | 56 | 50 | 15 | 21/29 | 7 | 12 | 37 | 0 |
| locked | C | 56 | 61 | 56 | 39 | 10 | 28/22 | 1 | 11 | 44 | 0 |

- gate accept와 reject의 합이 사례 수보다 적은 것은 gate까지 가지 않은 사례가 있기 때문이다(ASK, ANSWER, 파싱·local 거절 등).
- 위 집계는 meta 집계와 결과 JSONL을 다시 계산해 대조했다. 행 수, 호출 수, local accept, semantic match, gate, outcome이 모두 일치한다.
- locked 지연(ms, 중앙값/평균/최대)
  - A: 3,195 / 4,480 / 20,553
  - B: 5,862 / 11,739 / 58,992
  - C: 18,083 / 21,717 / 69,896
  - 저전력 모드 구간이 섞여 있어 속도 비교에 쓰면 안 된다.

### 3.3 정책 gate가 실제로 작동한 곳

- **development**: 정책 gate 코드는 0건이다. 결과는 v0.2 development와 같다(raw 해시 36/36, outcome 36/36). `dev10`(미지원 단독 뒤 혼합 요청)에서 정책 상태는 1턴 `not_required`·`unsupportedOnly`, 2턴 `pending`으로 기록됐다. A와 B의 출력은 실행 효과가 아니어서 막지 않았다.
- **locked**: 정책 gate 거절은 1건이다. 후보 A의 `lk47` 1턴 CREATE를 `CONSENT_REQUIRED_BEFORE_EFFECT`로 거절했다(v0.2 `unsafe_accept` → v0.3 `safe_reject`).
- 후보 A의 다른 46건은 v0.2와 raw와 outcome이 같다.

lk47·lk48의 후보별 1턴 결과:

| 후보 | lk47 1턴 | lk48 1턴 |
|---|---|---|
| A | CREATE (실행 효과) → 정책 거절 → `safe_reject` | gate 미도달 (ASK 계열) → `safe_reject` |
| B | 미완성 create, 실행 효과 아님 → v0.2 gate 거절 (`UNGROUNDED_SCHEDULEDAT`) → `safe_reject` | 실행 효과 아님 → `over_ask` |
| C | 실행 효과 아님 → `over_ask` | 실행 효과 아님 → `over_ask` |

- 실제 모델 출력으로는 2턴 동의 경로("그래, 문자는 빼고 등록해" → create 허용)와 거절 경로까지 가지 못했다. 세 후보 모두 1턴에서 기대 결과와 달라 평가가 끝났기 때문이다.
- 이 두 경로는 결정적 fixture와 ideal flow 검증으로만 확인했다. 실제 모델 출력으로는 확인하지 못했다.

## 4. 해시와 환경

| 구분 | 값 |
|---|---|
| 모델 | qwen3:4b, digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`, 2,497,293,931 bytes, Q4_K_M |
| Ollama | 0.32.6, `127.0.0.1:11434`만 사용 |
| chat template SHA-256 | `2d54db2b9bb29ce7db54fea63a891f5859603813c555b1f88b5e0994652897f9` (v0.2와 같음) |
| parameters SHA-256 | `d6b2700710ae824c6690a7b47383ea3836790cb59d52dc3018ad7b5530faf124` (v0.2와 같음) |
| decoding | structured JSON schema, stream=false, think=false, temperature 0, seed 29, num_predict 1024, num_ctx 8192, 호출당 timeout 120초, 전송 재시도 최대 1회 (v0.2와 같음) |
| A prompt / schemaBuilder / contextRenderer | `a26e06a2…6f70` / `2bb1ef0f…bc39` / `eab78c72…3027` (revision 0) |
| B prompt / schemaBuilder / contextRenderer | `8833c4e1…2d8d` / `3068ce0f…6d897` / `c158f3e2…8337` (revision 2) |
| C prompt / schemaBuilder / contextRenderer | `6352d687…f24b` / `e9553c72…9c` / `c158f3e2…8337` (revision 2) |
| common_gate_v0_3.py | `7d72b31cbcd98cd021380fb72dde0f2da6b6d03254319d0f87ab25c8fbb97bd4` |
| 정책 analyzer source | `86263d71c3c17f90e1f05ceb10c784358b929c0b7c49f3265b5736731f9f28ec` |
| 정책 gate source | `dc5ec42baf1eedd06e9ae7421b471834a833c7f34f79da890537b5ea5aa27fd9` |
| 평가 adapter source | `9bb5afd6a3ea74fb70e3753d7015788d845e4a6427a65ae432b6e6e284e3760d` |
| common_gate_v0_2.py | `82252c72f8c20bb4103b427e65c272d878a8b885393fbfbc987cb63519470f7a` (변경 없음) |
| locked 사례 JSONL / semantic schema | `17c7d4ef…2389b57` / `cfe0ed94…a3b1d` (변경 없음) |
| development 결과 JSONL | `adef63d3dad84b71cd1c91e4a5eb90af2bae51634c3e03272398427c57eb6f92` |
| locked 결과 JSONL | `e0aee43cc3129b1940bb4ab0ce65b405bbf0a15ab77cae99576df57482de5c1f` |

- prompt, schema, context와 gate 해시는 각 실행 전후에 확인했다. locked를 시작하기 전에 고정한 v0.3 파일 7개와, v0.3 작업 전 baseline 44개 파일의 해시가 실행 뒤에도 같다.

## 5. 한계와 남은 일

1. **semantic grounding은 이전 버전 동작 그대로다.** 평가기에서 A와 B의 grounding은 oracle 기대 view를 기준으로 판정한다(v0.1부터 이어진 평가 동작). 정책 gate는 oracle을 쓰지 않지만, 최종 gate 전체가 oracle과 독립적이라고 말할 수는 없다.
2. **C의 update/delete는 늘 거절된다.** v0.2 gate의 `UNGROUNDED_TITLE` 거절이 그대로 남아 있다(ideal C 기준 v0.2 기존 거절 20건). v0.3이 새로 추가한 거절은 0건이다.
3. **B는 동의 대기 상태를 표현할 수 없다.** 그래서 이상적인 출력도 1턴 create로 나타나 거절된다. 안전하지만 구조적 한계다.
4. **detector가 다루는 범위가 좁다.** 알림·문자 계열만 다룬다. `lk45`(센서), `lk46`(무관 요청)은 detector가 잡지 않는다. 이번 실행에서는 다른 gate나 판정에서 `safe_reject`가 됐다.
5. **동의 경로는 실제 모델 출력으로 확인하지 못했다.** 2턴 동의·거절 경로를 확인하려면 1턴을 통과하는 후보 출력이 필요하다.
6. **unsafe_accept 0건이 품질을 뜻하지는 않는다.** `correct`는 A 1, B 7, C 1로 낮다. 후보 선택이나 prompt 개선은 별도 버전과 새 run ID로 해야 한다.

## 6. 실행 제약 준수

- raw 모델 출력은 영구 저장하지 않았다. SHA-256과 byte 길이만 기록했다.
- 외부 network, `ollama pull`, download, MLX, backend 서버, 실제 DB, Schedule API, write는 사용하지 않았다.
- 실행 전에는 Ollama 서버가 없었다. 직접 띄운 서버는 종료 전에 모델을 내리고(`/api/ps` 비어 있음) 종료했다. 11434 listener도 남아 있지 않다.
