# `schedule_assistant`

이 폴더는 **AI 영역의 로컬 일정 대화 시범 구현**이다. Qwen이 제안한 JSON action을 검증하고, 기존 Schedule HTTP API를 통해 일정을 조회·등록·수정·삭제하는 대화 제어부를 담고 있다. 자체 HTTP 서버나 음성 입출력 구현은 없으며, Schedule 데이터베이스에 직접 접근하지 않는다. 공유 AI endpoint와 action 계약은 아직 `TBD-AI`다.

## 파일별 역할

| 파일 | 역할 |
| --- | --- |
| [`actions.py`](actions.py) | 모델의 JSON 응답을 `ASK`, `LIST`, `CREATE`, `SELECT_TARGET`, `CONFIRM_UPDATE`, `CONFIRM_DELETE`, `ANSWER` 중 하나로 해석하고 필드·날짜·카테고리 등을 검증한다. |
| [`service.py`](service.py) | 한 세션의 대화 이력과 대상 선택·수정/삭제 확인 상태를 메모리에 보관하고, 검증된 action을 처리한다. |
| [`clients.py`](clients.py) | 로컬 Ollama `/api/chat`과 백엔드의 `/api/v1/schedules` HTTP API를 호출한다. |
| [`prompt.txt`](prompt.txt) | Qwen에 제공하는 시스템 지시문이다. |
| [`__init__.py`](__init__.py) | `ConversationService`와 `Reply`를 패키지에서 내보낸다. |

## 처리 흐름

1. `ConversationService.handle()`이 사용자 발화와 메모리의 대화 문맥을 `OllamaClient`에 전달한다.
2. `parse_action()`이 모델 응답의 세 키(`action`, `message`, `request`)와 action별 요청 형식을 검사한다. `ASK.message`는 표시할 질문이고 `ANSWER.message`는 `null`이어야 한다. 잘못된 응답은 Schedule API로 보내지 않는다.
3. `LIST`는 Schedule API에서 후보 일정을 읽고 모델을 한 번 더 호출한다. `CREATE`는 유효한 요청에 `Idempotency-Key`를 붙여 등록한다.
4. 수정·삭제의 조회 후보가 1건이면 `CONFIRM_UPDATE` 또는 `CONFIRM_DELETE`에 해당 ID를 넣는다. 2~10건이면 ID 없는 `SELECT_TARGET`으로 번호를 고르게 하고, 10건 초과면 범위를 좁혀 달라고 묻는다. 선택한 대상·변경 내용을 확인받은 뒤 재조회하여 변경 여부를 검사한다. 승인 뒤에만 PATCH 또는 DELETE를 호출한다.
5. 성공·실패 안내는 API 결과를 기준으로 만든다. 등록 결과가 불확실하면 같은 본문과 키를 보관하며, 사용자의 `재시도` 입력에만 재요청한다.

현재 실행 진입점은 상위 폴더의 [`run_local.py`](../run_local.py)이다. 저장소 루트에서 다음처럼 실행한다. 로컬 Ollama에 지정 모델이 설치되어 있고 Schedule 백엔드가 실행 중이어야 한다. 이 명령으로 입력한 등록·수정·삭제 요청은 지정한 백엔드에 실제 반영된다.

```powershell
python ai/run_local.py --model qwen3:4b --schedule-url http://127.0.0.1:8000
```

`--ollama-url`을 생략하면 `http://127.0.0.1:11434`를 사용한다. CLI에서 `/reset`은 대화와 확인 대기를 초기화하고, `/quit`은 종료한다. 의존성은 Python 표준 라이브러리뿐이다. 사용법과 action 요약은 [AI 영역 README](../README.md)에도 있다.

## 검증과 현재 한계

저장소 루트에서 다음 단위·모의 HTTP 테스트를 실행할 수 있다.

```powershell
python -m unittest discover -s ai/tests -v
```

대화 상태와 결과가 불확실한 등록의 재시도 정보는 프로세스 메모리에만 있다. 프로세스 재시작 뒤에는 해당 정보가 사라진다. 조회와 수정·삭제 사이의 동시 변경을 원자적으로 막는 백엔드 계약도 아직 없다. 이 코드는 AI 영역의 시범 구현이며, 공개 AI endpoint, 상태 보관 방식, 출력 schema와 연동·재시도 계약을 확정하지 않는다. 관련 기준은 [Schedule API 문서](../../docs/api.md)와 [팀 지침](../../TEAM_GUIDELINES.md)을 참조한다.
