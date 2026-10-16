# Qwen 평가 자료

> 상태: DATA 영역 내부 평가 실험. AI 출력 계약·평가 데이터 schema를 확정하지 않는다.

단일 발화 평가셋은 [`qwen/experiments/api_categories/`](qwen/experiments/api_categories/README.md)의 API 카테고리 5개 기준 100문항이다. 별도 멀티턴 평가 초안은 아래의 400건 실험을 따른다. 초기 v0.1의 한글 카테고리 문항·프롬프트·실행 코드는 현재 작업 트리에서 제거했다. `qwen/v0.1_initial/`에는 당시 측정한 결과 CSV만 보존한다. 과거 수치와 새 기준의 수치를 직접 비교하지 않는다.

단일 발화 문항은 `proposed_unreviewed` 상태이며 `v0.2` 확정본이 아니다. AI 출력 형식과 세부 정답은 AI·데이터 담당자의 검수가 필요하다. 단일 발화 모델 응답과 실행 기록은 Git에서 제외되는 `qwen/runs/`에 저장한다.

프로젝트 페르소나와 현재 문항·프롬프트의 대화 흐름 검토는 [`docs/persona_and_qwen_review.md`](../docs/persona_and_qwen_review.md)에 기록한다.

## 실행과 채점

저장소 루트에서 다음 명령을 실행한다. `--output-dir`은 매번 새 경로를 사용한다.

```bash
python -m unittest discover -s datasets/qwen/tests
python datasets/qwen/tools/run_qwen_eval.py --model qwen3:4b --output-dir datasets/qwen/runs/api-categories
python datasets/qwen/tools/qwen_eval.py --responses datasets/qwen/runs/api-categories/responses.jsonl --summary-only
```

실행기는 로컬 Ollama의 `/api/generate`를 호출하고 응답·요청 시간·Ollama 지표를 기록한다. `manifest.json`에는 문항과 프롬프트 해시, 모델 이름, 실행 환경과 옵션이 들어간다. 채점기는 JSON 형식, 6개 필드의 엄격한 일치, 카테고리 enum 준수, 응답 시간과 토큰 속도를 구분해 보고한다. 필요하면 `--compare-responses`로 동일 문항의 두 실행 결과를 비교할 수 있다.

이 평가는 텍스트를 6개 필드로 구조화하는 실험이며 실제 Schedule API 호출 성공 여부를 측정하지 않는다. 원본 음성·실제 사용자 자료·모델 체크포인트·비밀값은 Git에 추가하지 않는다.

## 멀티턴 400개 초안

[멀티턴 400건 실험](qwen/experiments/multiturn_400_v0.1/README.md)에 train 260건·validation 40건·test 예약 초안 100건과 잠정 평가기가 있다. Git에 보존한 초안 원본은 모두 `proposed_unreviewed`다. 2026-10-15~16에 별도의 [MLX 20-step 시험 학습](qwen/experiments/mlx_smoke_v0.1/README.md)을 실행했으며, 전체 데이터 학습·검수 완료를 뜻하지 않는다. 제안 정답의 오프라인 재생은 400/400건이다. 다중 후보 60건은 ID 없이 번호를 묻는 `SELECT_TARGET`으로 작성했고 AI 로컬 시범 구현도 같은 action 형식을 지원한다. 공유 출력 계약은 여전히 `TBD-AI`다. 이전 규칙에서 측정한 실제 Qwen의 미검수 validation 잠정 일치 31/40건은 새 규칙의 점수가 아니다. train 25건·validation 5건 시범 표본은 사용자 위임에 따른 Codex 검토·판정 기록 후 로컬 승인본으로 반영했다. 독립적인 복수 사람 검수는 아니다. 승인본 63/13행으로 후속 20-step 학습과 비교를 완료했으며 JSON 전체 일치는 5/13→5/13으로 개선이 확인되지 않았다. 검수 원본 보존 도구와 로컬 HTML 검토 화면이 있으며, API 실패·응답 유실·재시도 사례는 아직 없다. test는 잠근 독립 시험본이 아니다. 실행 방법·검수 절차와 결과 해석은 실험 README를 따른다. 실제 모델 원문·로컬 실행 로그와 개인 검수 기록은 Git에 포함하지 않는다.
