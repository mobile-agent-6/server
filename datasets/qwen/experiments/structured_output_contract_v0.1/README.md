# Structured output contract v0.1

Issue #30의 AI 구조화 출력 계약 비교·안전 gate·dry mapping·Ollama 평가를 재현하기 위한 Git 추적 실험 패키지다. 권장안과 책임 경계는 [제안서](../../../../docs/ai_structured_output_contract_proposal.md)를 기준으로 한다.

## 상태와 주의사항

- 상태: `BE-PROPOSED`
- 확정되지 않은 항목: 최종 출력 계약, 배포 모델, 합격 수치, FE 표현 방식
- production 코드, 공개 API, DB, 기존 400개 데이터 원본을 변경하지 않았다.
- `unsafe_accept=0`은 고정 사례와 해당 gate 버전의 안전 결과이며, 모델 정확도나 production 안전성의 일반적 증명이 아니다.
- v0.3 Ollama 실행의 지연 시간은 잠자기·저전력 구간이 섞여 있어 후보 성능 비교에 사용하지 않는다.
- 원 모델 출력은 보존하지 않았고 SHA-256과 byte 길이만 기록했다.

## 구성

| 범위 | 주요 파일 |
|---|---|
| 의미 정답 | `semantic-oracle-schema-v0.1.json`, `development-cases-v0.1.jsonl`, `locked-cases-v0.1.jsonl` |
| 후보 A·B·C·R | `contracts.py`, `rule_baseline.py`, `candidate-*-prompt-v0.1.txt` |
| 결정적 안전 검증 | `mutation-fixtures-v0.*.jsonl`, `validate_contracts*.py`, `common_gate_v0_*.py` |
| processor dry mapping | `dry_mapping.py`, `dry-mapping-*.jsonl|json|md` |
| Ollama 평가 | `ollama_eval*.py`, `ollama-*-results-v0.*.jsonl`, `ollama-eval-*.json|md` |
| 기존 400개 감사 | `audit_existing_400.py`, `existing-400-audit-v0.1.json` |
| 공유 무결성 | `shared-manifest-v0.1.json`, `verify_shared.py` |

연구 중산출물의 v0.1~v0.4는 덮어쓰지 않고 별도 버전으로 보존했다. v0.4는 trusted policy turn prefix 완전성을 검사하는 결정적 gate 보완이며, 모델을 다시 실행하지 않았다.

### 고정 원본 안의 이전 로컬 경로

locked JSONL과 이전 manifest의 SHA-256을 유지하기 위해 연구 당시의 `.omx/...` 경로 문자열을 변경하지 않았다. 이 경로는 재현 시 읽는 실행 의존성이 아니라 이전 provenance 기록이다. 팀이 접근할 현재 근거는 `historical-path-map-v0.1.json`을 따르며, 정책 상태와 권장안의 기준은 본 README와 제안서다. 고정 원본의 경로 문자열을 현재 공유 문서 경로로 해석하지 않는다.

## 재현

Python 3.11과 sibling backend 가상환경을 사용한다. 아래 명령은 이 폴더에서 실행한다.

```bash
python3 verify_shared.py
```

`verify_shared.py`는 다음을 수행한다.

1. `shared-manifest-v0.1.json`의 파일 해시를 대조한다.
2. 추적 파일을 임시 `.omx/research/issue30` 레이아웃으로 복사한다.
3. backend·dataset은 읽기 전용 연구 의존으로 연결한다.
4. 외부 network·Ollama·DB·write 없이 회귀 테스트를 실행한다.

모델 평가는 기본 검증에 포함하지 않은다. 모델·runtime·prompt·schema·decoding을 고정한 새 run ID와 승인된 실행 범위가 있을 때만 다시 실행한다.

## 기존 400개 데이터 감사 재생성

```bash
python3 audit_existing_400.py --check existing-400-audit-v0.1.json
```

감사 결과는 개인 검수 기록이 아니라 현재 저장소의 수량·schema·action 분포를 기계적으로 집계한 것이다. `reviewed`는 2026-10-22 사용자 지시에 따른 상태이며 독립 복수 검수나 공유 계약 채택을 의미하지 않는다.

## 공유 규칙

- 수치를 인용할 때는 반드시 같은 버전의 summary·manifest·JSONL을 함께 확인한다.
- 권장 계약과 정책 상태는 제안서의 `BE-PROPOSED`를 유지한다.
- 새 모델 실행은 기존 result/manifest를 덮어쓰지 않고 새 버전·run ID로 보존한다.
- frontend 문구·카드·TTS 표현은 모델 의미 점수에 혼합하지 않고, dry mapping 후 별도 검토 항목으로 다룬다.
