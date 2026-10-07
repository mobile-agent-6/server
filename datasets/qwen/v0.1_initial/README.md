# Qwen 초기 100건 평가 (v0.1)

> 상태: 2026-09-25 기준 날짜로 수행한 **초기 평가 기록**. 정답은 검수 전이며 최종 AI 출력 계약이 아니다.

- `qwen_batch_test_100_scored.py`: 당시 `TESTS`, 프롬프트, Ollama 호출과 정규화 채점 코드
- `qwen_100_scored_results.csv`: 당시 문항·정답·Qwen 응답 원문·채점 결과
- `qwen_100_scored_summary.csv`: 당시 집계 (완전 일치 16/100)
- `qwen_legacy_eval_100.jsonl`: 같은 문항·정답을 ID와 검토 표기와 함께 분리한 자료

원래 실행 코드는 결과 파일을 **현재 작업 디렉터리**에 쓰므로 이 폴더에서 재실행하면 보존된 CSV를 덮어쓸 수 있다. 재실행은 별도 출력 디렉터리에서 수행한다. 기존 응답의 엄격한 재채점은 `../tools/qwen_eval.py`를 사용한다. 세부 실행 명령과 한계는 `datasets/README.md`에 있다.
