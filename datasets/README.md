# Qwen 멀티턴 평가 자료

> 상태: DATA 영역 내부 평가 실험. AI 출력 계약·평가 데이터 schema를 확정하지 않는다.

현재 작업은 [멀티턴 400건 데이터셋](qwen/experiments/multiturn_400_v0.1/README.md)을 기준으로 한다. 기존 단일 발화 100문항과 전용 프롬프트·실행기·채점기·테스트는 2026-10-22에 제거했다. 과거 평가 보고서의 근거인 초기 결과 CSV는 보존한다.

## 물류 400건 학습 + v17 공유 자료 (2026-11-08)

백엔드·프론트 팀원은 [모델 팀 공유 안내](../docs/qwen_logistics400_v17_handoff.md)부터 확인한다.

[물류 400건 학습·평가·후처리 자료](qwen/experiments/logistics400_v17/README.md)를 별도 실험 폴더에 추가했다. 다섯 기능 각 80건의 실제 학습 데이터와 개발 평가 150건을 포함한다. 기존 멀티턴 400건 초안과 구분하며, 모델 가중치·서비스 배포·공유 계약 확정은 포함하지 않는다. 저장된 합성 평가 출력은 이 실험의 후처리 재현용 자료다.

## 폴더 안내

| 경로 | 용도 |
|---|---|
| [`qwen/experiments/multiturn_400_v0.1/`](qwen/experiments/multiturn_400_v0.1/README.md) | 현재 멀티턴 데이터셋·검수·평가 도구 |
| [`qwen/experiments/mlx_smoke_v0.1/`](qwen/experiments/mlx_smoke_v0.1/README.md) | MLX 시험 학습·전후 비교 도구와 과거 집계 |
| [`qwen/experiments/multiturn_pilot/`](qwen/experiments/multiturn_pilot/README.md) | 구형 시범 5건과 당시 제어부 보존본 |
| `qwen/tools/` | 구형 시범의 재생·실제 모델 평가·검토 화면 |
| `qwen/tests/` | 데이터·검수·평가 도구 검사 |
| [`qwen/v0.1_initial/`](qwen/v0.1_initial/README.md) | 과거 보고서가 참조하는 초기 결과 CSV |

프로젝트 페르소나와 제거된 단일 발화 평가의 검토 기록은 [`docs/persona_and_qwen_review.md`](../docs/persona_and_qwen_review.md)에 있다.

## 검증과 실행

저장소 루트에서 도구 검사를 실행한다.

```bash
python -m unittest discover -s datasets/qwen/tests
python -m unittest discover -s datasets/qwen/experiments/mlx_smoke_v0.1 -p 'test_*.py'
```

실제 모델 평가·검수·학습 명령은 각 실험의 README를 따른다. 실행마다 새 출력 폴더를 사용한다. 자동 검사는 모델 정확도나 검수 승인을 뜻하지 않는다. 원본 음성·실제 사용자 자료·모델 체크포인트·비밀값은 Git에 추가하지 않는다.

## 멀티턴 400개 데이터셋

[현재 데이터셋](qwen/experiments/multiturn_400_v0.1/README.md)은 train 250건·validation 50건·test 예약 100건이다. 2026-10-22 사용자 지시에 따라 400건 모두 `reviewed`(검수 완료)로 처리했다. 별도의 독립 검수를 수행한 기록은 아니며, test는 잠근 독립 시험본이 아니다. 별도 `diagnostic/`의 API 장애·재시도 진단 10건은 본 400건의 점수에 합치지 않는다. 공유 AI 출력·DATA schema는 여전히 `TBD-AI`·`TBD-DATA`다.

현재 생성본은 알림 필드를 제거한 `qwen-dataset-400-v0.1.4-proposed`다. 이전 검수 승인과 MLX 시험 수치는 현재 초안에 자동 승계하지 않는다. 실제 Qwen 기준선·독립 검수·재학습은 새 입력에서 확인해야 한다. 과거 측정 근거는 [시험 학습 보고서](../docs/qwen_smoke_training_results_20261016.md)에 보존한다.

학습 변환본·정답 재생 미리보기·검수 템플릿은 필요할 때 실험 폴더의 `build_dataset.py`로 생성한다. 이 명령은 생성본을 덮어쓰며, `review_status.json`에 승인된 내용·분할·질문 기준이 같을 때만 `reviewed`를 유지한다. 내용이 달라지면 `proposed_unreviewed`로 돌아간다. 개인 검수 기록과 과거 실행 결과는 구분해 보관한다. 실제 모델 원문·로컬 실행 로그·개인 검수 기록은 Git에 포함하지 않는다.

## 새 20-step MLX 모델 전달본 (2026-10-22)

[전달용 Release: qwen-fresh20-20261022](https://github.com/mobile-agent-6/server/releases/tag/qwen-fresh20-20261022)
에서 다음 두 첨부 파일을 받는다. Release 게시 전에는 링크가 열리지 않을 수 있다.
GitHub가 자동 생성하는 Source code ZIP 대신 아래 전달용 ZIP을 선택한다.

- `qwen-handoff-fresh20-20261022.zip` (2,070,457,207 bytes)
- `qwen-handoff-fresh20-20261022.zip.sha256`

Apple Silicon Mac용 MLX 패키지다. 기본 Qwen3-4B-4bit, 새 LoRA 20-step 어댑터,
평가 때 사용한 토크나이저·프롬프트·로컬 제어부, 실행 코드와 평가 요약을 포함한다.
기존 40-step 어댑터를 이어 학습한 모델이 아니다. 모델 가중치는 Git 이력에 넣지 않는다.

두 파일을 받은 폴더에서 무결성을 확인하고 압축을 해제한다.

```bash
shasum -a 256 -c qwen-handoff-fresh20-20261022.zip.sha256
unzip qwen-handoff-fresh20-20261022.zip
cd qwen-handoff-fresh20-20261022
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python verify.py
bash run.sh sample
```

원 평가 환경은 macOS ARM64 Python 3.14다. 설치에는 인터넷이 필요하며 기본 모델은
ZIP에 포함돼 있다. 상세 안내는 ZIP 내부 `사용안내.txt`를 따른다.
`sample`은 모델 원본 출력만 확인하고 DB/API에 쓰지 않는다.

```bash
bash run.sh eval10
bash run.sh eval40
```

두 평가는 서로 겹치지 않는 개발 검증 대화 50개이며, 메모리 모의 API를 사용한다.
Ctrl+C 후 같은 명령을 실행하면 완료한 대화는 건너뛴다. 진행 중이던 대화는 다시 실행한다.

고정 기준 문맥 27개에서 기본 모델 대비 새 모델은 action 14/27 → 20/27,
JSON 전체 일치 10/27 → 14/27, JSON 형식 통과 25/27 → 25/27이었다.
별도의 새 모델+수정 제어부 대화 평가에서는 10/10 및 40/40이 통과했다.
이는 모델 단독 정확도나 실제 서비스 성공률이 아니다.

제어부 수정본은 전달 ZIP에 들어 있는 DATA 내부 실험 버전이며, 이 저장소의
백엔드에 통합됐다는 의미가 아니다. `DecisionProvider` 연결, 실제 Schedule API,
수신자 Mac 및 목표 장비 검증은 별도로 필요하다. Ollama/Orange Pi용 변환본이 아니며,
공유 인터페이스 계약을 변경하거나 확정하지 않는다.

패키징 시 원본 가중치·제어부 일치, CPU 모의 평가 50개, 재개 동작 및 ZIP 무결성을
확인했다. 포장 후 GPU 추론과 수신 장비 실행은 확인하지 않았다.
