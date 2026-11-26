# 물류 일정 Qwen 400건 학습 + v17 후처리

작성일: 2026-11-08. 상태: DATA 내부 학습·개발 평가 결과 공유. 공유 API·AI 출력 계약을 확정하거나 서비스에 배포하는 변경이 아니다.

물류 일정의 등록·조회·수정·삭제·완료 상태 변경을 각 80건, 총 400건 학습한 결과와 v17 후처리 코드를 공유한다. 기본 모델은 `mlx-community/Qwen3-4B-4bit`이며, 기본 가중치에서 222건을 새로 학습한 뒤 신규 178건을 이어 학습했다. 최종 모델이 본 학습 예시는 누적 400건이다. v17은 추가 학습이 아닌 후처리 개선이다.

이 자료는 과거 v17 학습 기록이다. 최종 모델 전달 기준은 [440건 학습 모델과 v37 후처리 안내](../../../../docs/qwen_logistics440_v37_handoff.md)를 확인한다. 당시 상세 연동 내용은 [연동 안내](INTEGRATION.md)에 보존한다. **v17 모델은 MLX 학습 형식이며 당시 Orange Pi 실행은 검증하지 않았다.** 전달 ZIP의 식별 정보는 [model-artifacts.json](model-artifacts.json)에 있다.

## 성능

정답 일치율은 JSON 전체 엄격 일치다. 확인 질문을 정답에 포함하지 않는다.

| 개발 평가 묶음 | 순수 모델 일치 | v17 적용 후 일치 | v17 출력 규칙 통과 | 확인 질문 |
|---|---:|---:|---:|---:|
| 기존 100건 | 46/100 | 94/100 | 96/100 | 4 |
| 추가 25건① | 10/25 | 24/25 | 25/25 | 0 |
| 추가 25건② | 12/25 | 19/25 | 22/25 | 3 |
| 합산 150건 | 68/150 (45.3%) | 137/150 (91.3%) | 143/150 (95.3%) | 7 |

평가 문항은 이미 열람하고 후처리 개선에 사용한 개발 자료다. 독립 Test 또는 실제 API 성공률이 아니다. 순수 모델의 학습 전후 비교에는 동일 문항과 실행 설정이 필요하며, 여기에는 학습 전 기본 모델의 같은 150건 평가가 없다. v17 개선은 저장된 원출력을 재처리해 검증했으며 모델 가중치는 변경하지 않았다.

## 파일

| 경로 | 내용 |
|---|---|
| `data/train400.jsonl` | 실제 학습한 누적 400건 |
| `data/train400-index.jsonl` | 작업 분류·예시 출처 |
| `data/stage1-train222.jsonl`, `data/stage2-train178.jsonl` | 실제 두 단계 학습 분할 |
| `data/valid20.jsonl` | 학습 건수에 포함하지 않은 검증 20건 |
| `data/학습400건_눈으로보기.txt` | 입력·정답을 사람이 읽기 쉬운 형태로 표시 |
| `training/` | 학습 설정, 완료 기록, 구조화된 학습 지표, 관측 환경 |
| `evaluation/` | 합성 물류 평가 입력·정답·저장된 모델 출력·후처리 결과 |
| `runtime/rules/` | v17 및 필요한 이전 모듈 전체 |
| `runtime/backend/` | 평가 시 사용한 evidence 계약 코드와 프롬프트의 고정 사본 |
| `tools/` | 모델 없이 실행하는 검증·후처리 도구 |
| `reports/` | 기존 오류 수정 및 회귀 검사 결과 |
| `reference/dataset-guide.md` | 작업 당시 참고한 가이드의 고정 사본 |
| `model-info.json` | 별도 전달할 모델 가중치·프롬프트·토크나이저의 식별 해시 |
| `source-provenance.json`, `manifest.json` | 공유용 변환 이력과 현재 파일 무결성 정보 |

완료 상태 변경은 별도 kind가 아니라 `kind=update`와 `changeEvidence.completed`로 표현한다. v17은 이전 모듈을 import하므로 v17 파일 하나만 복사하면 안 된다.

## CPU 검증

저장소 루트에서 Python 3.10 이상으로 실행한다. Python 환경에 `Asia/Seoul` 시간대 자료가 있어야 한다. MLX와 기본 모델은 필요 없다.

```bash
python3 datasets/qwen/experiments/logistics400_v17/tools/verify.py
```

현재 파일 해시, 400건 구성 및 222+178 일치, 학습·검증 420건 정답 보존, 렌더러 바이트 일치, 저장된 평가 출력 150건의 재현을 확인한다. GPU 추론·새 모델 평가·API/DB 호출은 수행하지 않는다. 출력 규칙을 통과해도 의미상 정답을 보장하지 않는다.

저장된 원출력에 후처리만 적용하는 예시:

```bash
python3 datasets/qwen/experiments/logistics400_v17/tools/postprocess.py \
  --input datasets/qwen/experiments/logistics400_v17/tools/example-input.json \
  --raw datasets/qwen/experiments/logistics400_v17/tools/example-model-raw.txt
```

`clarification_required`는 확인 질문으로 전달한다. `invalid_output`이나 확인 질문을 일정 변경 성공으로 처리하지 않는다. `contract_valid_evidence` 역시 DB 실행 성공 표시가 아니다.

## 모델 파일과 실행 범위

이 폴더에는 모델 가중치와 토크나이저 본체를 커밋하지 않는다. 별도 전달용 `01_review-and-trained-adapter.zip`에 최종 LoRA 가중치와 정확한 토크나이저·템플릿이 있고, `02_exact-base-model.zip`에 기본 모델 가중치가 있다. 이 문서는 해당 파일이 GitHub Release에 게시됐다는 뜻이 아니다.

실제 추론은 정확한 기본 모델에 최종 400건 LoRA만 적용한다. 중간 222건 LoRA를 추가로 겹쳐 적용하지 않는다. 같은 이름의 모델을 새로 다운로드하는 것만으로 학습 시의 토크나이저·채팅 템플릿과 바이트 일치가 보장되지는 않는다. 식별 해시는 `model-info.json`을 참고한다.

학습용 시스템 프롬프트는 `runtime/backend/app/modules/assistant/evidence/prompt.txt`, 사용자 메시지 렌더러는 `tools/common.py`의 `render_user_message`다. 생성 설정은 temperature 0, seed 29, 최대 1024 토큰, enable_thinking=False다. 생성 후 `evidence_postprocess_v17.apply_rules`와 `ClarificationRequired` 처리가 별도로 필요하다.

`runtime/backend`는 검증용 일부 사본이다. 저장소의 실제 `backend/`를 덮어쓰지 않는다. provider 연결에는 전체 백엔드 앱과 의존성이 필요하다. Orange Pi용 변환·실제 장치·API/DB 연결은 검증하지 않았다.

## 재현과 제한

- 이번 범위는 사용자 지정 총 400건이다. 가이드의 기능별 400건/총 2,000건, 독립 Test 500건, 독립 사람 검수 전체를 완료했다는 뜻이 아니다.
- 과거 비물류 400건 모델 가중치에서 이어 학습하지 않았다. 물류에 적합한 기존 122건 데이터와 신규 100건으로 처음 222건을 구성했다.
- 예시는 합성 데이터다. 기존 예시의 계보를 보존하며, 단순 제목 교체를 독립된 데이터 계열로 주장하지 않는다.
- `training`의 YAML은 실행 당시 설정 기록이다. `<SOURCE_WORKSPACE>`는 개인 절대경로를 제거한 표시이며 실행 경로가 아니다. 재학습 시 모델·데이터·출력·재개 경로를 새 환경에 맞춰 지정해야 한다.
- 입력·정답·후처리 핵심 코드는 전달본과 같다. 출처 메타데이터의 작성자 경로만 익명화했고, Git용 검증기와 안내는 가중치 없는 구성에 맞춰 수정했다.
- `reports/verification.json`의 350건 회귀 검사는 이전 222건 모델 출력도 포함한다. 현재 400건 모델의 평가 출력은 `evaluation/`의 150건이다. 합성 복사 오류 주입 성적은 실제 모델 정확도가 아니다.
- 이 자료 공유로 팀의 TBD 계약이나 최종 배포 방식이 확정되는 것은 아니다.
