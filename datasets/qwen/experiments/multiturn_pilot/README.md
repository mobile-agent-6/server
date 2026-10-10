# 멀티턴 일정 대화 시범 자료

> 상태: AI·DATA 내부 실험. `scenarios_pilot_v0.1.jsonl`의 5건은 재생 도구를 확인하기 위한 **미검수 초안**이며, 계획한 20~30건의 검수 완료 데이터가 아니다. 공유 AI 출력·DATA schema 계약도 확정하지 않는다.

이 자료는 실제 녹음 대신 인식이 끝난 텍스트 대화를 입력으로 사용한다. 짧은 음성식 발화와 인식 오류 정정은 시범 사례를 늘릴 때 텍스트로 표현한다. 실제 STT·TTS 성능은 별도로 평가한다.

시나리오 JSONL은 한 줄에 대화 한 건을 둔다. `schema_version`, `id`, `conversation_group_id`, `reference_datetime`, `initial_schedules`, `turns`, `label_status`, `expect_final`을 필수로 한다. 각 턴에는 사용자 발화, 시범 검수용 `model_outputs`, 기대 답변 종류·쓰기 시도 수를 둔다. `model_outputs`는 실제 모델 평가 결과가 아니라 제어부 동작을 재생하기 위한 **미검수 action 제안**이다. 승인·거절·번호 선택처럼 모델을 호출하지 않는 턴에는 빈 배열을 둔다. 등록 사례는 결정적인 결과 비교를 위해 `created_id`를 지정할 수 있다.

저장소 루트에서 표준 라이브러리만으로 검증·재생할 수 있다.

```powershell
python datasets/qwen/tools/replay_multiturn_pilot.py --scenarios datasets/qwen/experiments/multiturn_pilot/scenarios_pilot_v0.1.jsonl
python -m unittest discover -s datasets/qwen/tests -v
```

재생 도구는 [AI 대화 제어부](../../../../ai/schedule_assistant/service.py)에 지정된 action을 순서대로 넣고 메모리 일정 저장소로 API 호출 순서·쓰기 횟수·최종 상태를 확인한다. 이는 Qwen의 실제 출력 정확도나 음성 성능을 측정하지 않는다. HTTP 503·timeout·응답 유실의 클라이언트 동작은 [AI 모의 HTTP 테스트](../../../../ai/tests/test_schedule_assistant.py)에서 별도로 확인한다. 정답 검수 시에는 발화 근거와 허용 대화 경로를 별도 단계에서 확인하고 `label_status`를 갱신해야 한다. 검수 전 행을 학습이나 잠근 test에 사용하지 않는다.

## 실제 Qwen을 쓰는 시범 평가

로컬 Ollama에 모델이 준비돼 있으면 아래 명령으로 **실제 모델 응답**을 받아 같은 모의 일정 저장소에서 재생한다. 백엔드나 실제 DB는 사용하지 않는다. `--model`에는 로컬에 설치된 정확한 모델명을 넣는다. 결과 폴더는 매 실행마다 새 이름으로 지정한다.

```powershell
python datasets/qwen/tools/run_multiturn_pilot_eval.py --scenarios datasets/qwen/experiments/multiturn_pilot/scenarios_pilot_v0.1.jsonl --model qwen3:4b --output-dir runs/qwen_pilot_001
```

`runs/`는 Git에서 제외된다. 실행 결과의 `manifest.json`에는 실행 ID·모델명·입력·프롬프트 해시, `review_cases.jsonl`에는 대화별 응답·모델 원문·모의 API 호출·최종 상태, `turn_results.jsonl`에는 턴별 로그, `summary.json`에는 잠정 일치 건수가 담긴다. 예상 답변 종류와 달라지면 후속 사용자 발화의 의미가 달라질 수 있어 그 사례를 중단하고 `needs_review`로 기록한다. 일부 문구·API 순서 검사는 현재 미검수 시범 정답과 정확히 비교하므로 **잠정 일치 건수를 공식 성능 점수로 해석하지 않는다**. 모델 요청이 실패해도 해당 턴의 오류를 결과에 남긴다.

정성 검토는 브라우저에서 [로컬 HTML 평가 화면](../../tools/review_ui/index.html)을 열고 `review_cases.jsonl`을 선택한다. 사례별로 질문·확인·결과 안내를 판정하고, 발생하지 않은 항목은 `해당 없음`으로 표시한 뒤 판정 JSON을 내보낸다. 화면은 외부 서버에 파일을 업로드하지 않는다. 브라우저 임시 저장은 인수인계 수단이 아니므로 작업을 마치면 반드시 JSON을 내보낸다. 모델 원문과 실제 사용자 기록이 포함될 수 있는 실행 로그·개별 검토 파일은 Git에 추가하지 않는다. 문항 검수, 허용 대화 경로, 정성 판정 기준 버전은 정식 전후 평가 전에 별도로 확정한다.
