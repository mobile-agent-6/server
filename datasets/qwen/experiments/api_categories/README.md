# API 카테고리 기준 Qwen 평가 실험

> 상태: DATA 영역 내부 제안·미검수 실험. AI 구조화 출력 계약이나 `v0.2` 확정본이 아니다.

`cases_100.jsonl`은 기존 초기 100문항을 기반으로 category 정답을 Schedule API의 5개 영문 enum(`INBOUND`, `INVENTORY`, `OUTBOUND`, `RETURN`, `OTHER`)에 맞춘 자료다. `피킹`은 API 문서의 출고 업무 예시에 따라 `OUTBOUND`로 분류했다. 4개 문항은 API의 기타 업무 예시로 바꿔 `OTHER`를 등록·조회·수정·삭제에 하나씩 포함했다. 제공된 [프로젝트 페르소나](../../../../docs/persona_and_qwen_review.md)의 작업 환경에 맞춰 C구역 발화 21건을 A·B구역으로 정리했다. 나머지 필드의 초기 정답은 미검수 상태로 유지했다.

2026-10-16 DATA 내부 검토에서 일반 조회 19건의 `content="일정 조회"`를 `null`로 고쳤다. `content`는 조회 행위명이 아니라 발화에 명시된 업무 내용으로 해석했다. `api-050`의 `팀 회의`도 업무 내용으로 반영했다. 19건의 `read_content_rule_missing` 플래그와 모든 사례의 `proposed_unreviewed` 상태는 유지한다. 이는 공유 출력 계약의 확정이나 사람의 정답 승인이 아니다. 수정 25건의 대상 정보와 변경값이 같은 6필드에 섞이는 문제, 모호한 삭제 5건의 확인 경로도 여전히 검토 대상이다.

새 기준으로 모델을 실행하기 전에는 문항과 정답을 데이터·AI 담당자가 검수해야 한다. 이번 `content` 정답 변경 전 실행과도 같은 기준의 점수로 합치지 않는다. 결과는 기존 16/100과 직접 비교할 수 없다.

저장소 루트에서 문항 검증 및 실행:

```bash
python -m unittest discover -s datasets/qwen/tests
python datasets/qwen/tools/run_qwen_eval.py --model qwen3:4b --output-dir datasets/qwen/runs/api-categories
python datasets/qwen/tools/qwen_eval.py --responses datasets/qwen/runs/api-categories/responses.jsonl --summary-only
```

실행 결과는 Git에서 제외되는 `datasets/qwen/runs/`에 저장된다. 이 평가는 여전히 6개 필드의 텍스트 출력만 다루며 실제 Schedule API 호출 성공 여부를 측정하지 않는다. 공유 AI 출력 형식과 연동 방식은 `TBD-AI` 상태다.
