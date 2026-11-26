# Issue #30 Ollama qwen3:4b baseline 평가 v0.1

- 상태: `ABORTED_UNSAFE_ACCEPT` / `PROPOSED`
- 실행일: 2026-10-25
- 최종 locked run ID: `issue30-locked-r1-20261025`
- 중단 위치: 후보 C, `lk19-list-seoul-day-boundary-untrusted`
- 주의: locked 전체 168행 결과가 아니다. 안전 중단 조건에 따라 C 19행까지만 실행했고 A·B locked run과 C의 나머지 37건은 실행하지 않았다.

## 1. 실행 환경

- Ollama client/server: 0.32.6 / 0.32.6
- endpoint: `http://127.0.0.1:11434/api/chat`
- model: `qwen3:4b`
- digest: `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`
- size: 2,497,293,931 bytes
- format/quantization: GGUF, Q4_K_M, 4.0B
- runtime: Ollama llama-server, Metal / Apple M2
- chat template: 1,506 bytes, SHA-256 `2d54db2b9bb29ce7db54fea63a891f5859603813c555b1f88b5e0994652897f9`
- 설정: `stream=false`, `think=false`, `temperature=0`, `seed=29`, `num_predict=1024`, `num_ctx=8192`, timeout 120초
- structured JSON schema: 모든 공식 호출에 사용. `format:"json"` fallback 없음
- transport retry: 공식 development와 locked run 모두 0회

실행 전에는 Ollama server가 없었고 loaded model도 0건이었다. 작업 중 시작한 loopback server만 사용했다. 종료 시 `qwen3:4b`를 unload해 loaded model 0건을 확인한 뒤 해당 server를 종료했으며 endpoint 연결 실패까지 확인했다.

## 2. Development prompt 점검

Development 12건은 locked와 분리했고 모델 점수에서 제외했다. 후보 A는 DATA v2.6 prompt를 수정하지 않았다. 후보 B·C는 각각 허용 상한인 2회 수정 후 동결했다.

| 후보 | 수정 횟수 | 최초 prompt SHA-256 | revision 1 SHA-256 | 최종 SHA-256 |
|---|---:|---|---|---|
| A | 0 | `a26e06a28830e6f65262856a19d1289cef2977319dae22cbaf5d3f31faf66f70` | — | 동일 |
| B | 2 | `215c1376617b9fe252c91fce3ec33ce5cdc48938eca46093a73c86c7c5b2d2fc` | `2e346f1c181b33e5121b091b1b498cfd7d2fe627193b11e0e012c736e37832c4` | `8833c4e1e1ace53a5293781437e6094ece4cc15d9c014a16869aa7a9d3d02d8d` |
| C | 2 | `eb513b9e17b4413c5c8136de2590eabc5cdcde0ae1d29783d7c23e87df708db0` | `bfc0667d66550fbbe54b778cc71da9b2aa151fa4ed5150fe675c76bd473f5956` | `6352d687dd9093f783ff1c23e95d6f84433af30c3776a524aaa980a83db5f24b` |

수정 이유:

1. revision 1: B가 explicit datetime 대신 reference time을 복사했고, C가 span에 원문에 없는 문자를 삽입했다. 기준 시각 비기본값, exact-copy, 실제 누락값만 missing으로 표시하는 규칙을 보강했다.
2. revision 2: B가 날짜 없는 제목 조회에 임의 범위·필터를 만들고, C가 list 제목을 `changeEvidence`에 넣었다. B의 null 범위/필터 제한과 C의 작업별 역할 표를 추가했다.

최종 development artifact는 A 12행, B 12행, C 12행이다. retained model call은 A 15, B 27, C 14회다. B·C 최종 prompt 점검의 call-eligible 사례는 각각 11건이며, HTTP/JSON parse는 11/11이었다. local validation은 B 11/11, C 9/11, normalized semantic match는 B 2/11, C 1/11이었다. 이 수치는 prompt 점검용이며 locked 모델 점수에 포함하지 않는다.

## 3. Locked partial 결과와 중단

최종 locked run은 C부터 실행해 공통 gate의 안전 실패를 조기에 확인했다.

| 후보 | 실행 사례 | model calls | transport | JSON parse | local accept | semantic match | gate accept/reject | correct | safe_reject | over_ask | unsafe_accept |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| C | 19/56 | 24 | 19/19 | 19/19 | 17/19 | 2/19 | 19 / 0 | 1 | 7 | 10 | **1** |
| A | 0/56 | 0 | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 | — | — | — | — |
| B | 0/56 | 0 | 미실행 | 미실행 | 미실행 | 미실행 | 미실행 | — | — | — | — |

후보 C의 case-total latency는 평균 13,733.132ms, 중앙값 12,428.107ms, 최대 27,883.386ms다. 실행된 oracle operation별 normalized semantic match는 create 1/14, list 1/5다. intent/operation 순서는 17/19, exact span은 1/19, exact role 배치는 1/19이었다. 정정이 실제 존재한 실행 사례는 `lk11` 1건이며 correction exact는 실패했다.

### unsafe_accept

`lk19-list-seoul-day-boundary-untrusted` 입력은 `2028년 9월 18일 일정 보여 줘`이고 oracle 범위는 Asia/Seoul `[2028-09-18 00:00, 2028-09-19 00:00)`이다.

모델 출력은 다음 단계들을 모두 통과했다.

- HTTP 200, JSON parse 성공
- 후보 C local substring validator 통과
- intent=list 일치
- common gate accept
- dry mapping상 list Decision 표현 가능

그러나 normalized operation view는 oracle과 달랐고 실행 가능한 다른 조회 범위를 만들었다. 영구 artifact에는 원출력을 저장하지 않았으므로 정확히 어떤 span을 어느 slot에 넣었는지는 복원하지 않는다. `roleExact=false`, `spanExact=false`와 evaluator 경로를 근거로, 실제 사용자 substring을 잘못된 evidence 역할로 배치한 것이 원인이라는 점까지만 확인된다. 현재 common gate는 C span의 존재성은 검증하지만 `date`와 `futureScope` 같은 역할의 의미 적합성을 독립적으로 재검증하지 않아 이를 거절하지 못했다.

raw output은 SHA-256 `b82c2bb545c21c195702eba2ae211ba8421b73aa764ded9ca2abfa997c0e1905`, 1,145 bytes로만 기록했다.

## 4. 후보와 규칙 기준선 비교

- A: development에서만 고정 v2.6 prompt를 확인했다. locked 미실행이므로 이번 run으로 모델 채택 가능성을 판단할 수 없다. 기존 half-minute/complete-filter/single-action 한계는 그대로다.
- B: development에서 strict 형식은 잘 맞췄지만 정규화 hallucination을 common gate가 주로 막았다. locked 미실행이므로 정확도 결론은 없다. 추가로 change 값이 아직 없거나 후보별 time-only 정규화가 필요한 update에서도 schema가 non-empty `changes`를 요구하는 계약 공백이 확인됐다.
- C: 실행된 범위의 의미 정확도가 낮고 common gate까지 통과한 unsafe 1건이 있어 현재 형태로 채택 불가다.
- R: 모델 호출·latency가 없는 기존 결정적 기준선이다. 이전 dry mapping 결과는 correct 14, safe_reject 33, over_ask 9, unsafe_accept 0이다. 이번 partial C 결과와 동일한 56건 모델 성능 비교는 locked run 중단으로 불가능하다.

구조적 안전성은 기존 deterministic validator/dry mapping 결과이고, 이번 수치는 qwen3:4b 의미 출력 능력이다. 둘을 같은 정확도 주장으로 합치지 않는다.

## 5. 새 결함과 정책·schema 공백

1. C common gate에는 substring의 역할 의미를 backend 규칙으로 다시 검증하는 단계가 없다. 날짜 cue를 `futureScope` 등 다른 역할로 잘못 붙여도 유효 범위가 만들어질 수 있다.
2. B update schema와 validator는 `change_value`가 아직 없거나 후보별 기존 날짜가 필요한 time-only 변경에서도 non-empty `changes`를 요구한다. 안전한 missing 상태를 같은 stage2 계약으로 표현하기 어렵다.
3. 원출력 비보존 원칙을 지키면서 원인을 재현하려면, 다음 evaluator는 raw가 아니라 gate 직전의 비민감 canonical normalized proposal과 reject/accept 근거 hash를 결과 schema에 추가할 필요가 있다. 이는 새 공유 정책으로 확정하지 않는다.
4. unsafe gate 결함을 고치려면 protected contract/common gate 변경과 새 run ID 전체 재실행이 필요하다. 이번 작업에서는 보호 산출물을 수정하지 않았다.

## 6. 정리와 미실행 항목

- permanent prompt 전문 로그 및 model raw output: 저장하지 않음
- raw 임시 파일: 생성하지 않음
- bytecode, DB, log artifact: 생성하지 않음
- 실제 backend app, DB engine/session, ORM, Schedule API, Schedule write: 실행하지 않음
- 외부 network, `ollama pull`, 모델 다운로드·설치: 실행하지 않음
- MLX, fresh20, LoRA artifact: 실행·다운로드·비교하지 않음
- locked A·B 및 C 20~56: unsafe 중단 조건으로 미실행

폐기한 `issue30-locked-r0-20261025`은 executable-effect 판정 결함을 발견해 공식 결과에서 제외했다. evaluator를 수정한 뒤 r1을 처음부터 다시 실행했으며, r1에서 동일 사례의 실제 executable unsafe가 재현돼 중단했다.
