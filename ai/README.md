# AI action → Schedule API 로컬 시범 구현

`TBD-AI` 공유 계약을 검토하기 위한 **AI 영역 내부 제안 구현**이다. 백엔드 공개 API나 DB를 변경하지 않는다. 한 프로세스가 대화 한 개를 메모리에 보관하고, Qwen의 JSON action을 검증한 뒤 기존 Schedule HTTP API에 연결한다.

```powershell
python ai/run_local.py --model qwen3:4b --schedule-url http://127.0.0.1:8000
```

Ollama와 백엔드가 먼저 실행돼 있어야 한다. 위 명령은 지정한 Schedule API에 실제로 등록·수정·삭제를 요청한다. 종료 시 대화 상태가 사라진다. `/reset`은 이력과 수정·삭제 확인 대기를 지우지만, 결과가 불확실한 등록의 키와 본문은 보관한다. 의존성은 Python 표준 라이브러리뿐이다.

| action             | 검증 뒤 처리                                                                            |
| ------------------ | --------------------------------------------------------------------------------------- |
| `ASK`            | 짧은 추가 질문만 표시                                                                   |
| `LIST`           | `GET /api/v1/schedules` 후 결과를 문맥으로 넣고 모델을 다시 호출                      |
| `CREATE`         | 유효한 필수값에 대해 `Idempotency-Key`를 붙여 `POST /api/v1/schedules`              |
| `SELECT_TARGET`  | 조회 후보가 2~10건이면 ID 없이 번호 선택을 요청; 선택한 대상·변경값을 확인하기 전에는 쓰지 않음 |
| `CONFIRM_UPDATE` | 조회 후보가 1건이면 해당 ID·변경값을 확인; 승인 시 재조회 후 `PATCH`                 |
| `CONFIRM_DELETE` | 조회 후보가 1건이면 해당 ID를 확인; 승인 시 재조회 후 `DELETE`                       |
| `ANSWER`         | `message=null`. 조회 결과가 있으면 API 응답에서 안내를 만들고, 그 외에는 고정 안내 표시 |

모델 출력은 `action`, `message`, `request`의 세 키만 허용한다. `ASK.message`에는 질문을 넣고 `SELECT_TARGET.message`와 `ANSWER.message`는 `null`이다. 상세 필드와 다섯 category는 [검증 코드](schedule_assistant/actions.py), 지시문은 [프롬프트](schedule_assistant/prompt.txt)에 있다. 위치 필드는 없다. 수정·삭제 조회 결과가 0건이면 없음을 안내하고, 1건이면 해당 UUID로 확인을 시작한다. 2~10건이면 `SELECT_TARGET`에 `targetId` 없이 번호 선택을 요구하며, 10건 초과면 날짜·제목으로 범위를 좁히도록 묻는다. 선택한 대상·변경값을 사용자에게 확인받기 전이나 거절 뒤에는 쓰지 않는다. 확인 문구에 모델의 `message`를 사용하지 않는다. 등록과 수정·삭제 성공 문구는 API 성공 뒤에만 표시한다. 확인 중 승인·거절 외 새 발화는 기존 확인을 취소하고 새 요청으로 처리한다.

등록 결과가 불확실하면 새 생성 요청을 막고, 사용자가 `재시도`라고 입력했을 때 같은 본문·키로만 다시 요청한다. 24시간이 지났으면 자동 재시도를 막고 서버 확인을 안내한다. 이 보관은 프로세스 메모리에서만 유효하므로 재시작 뒤 중복 등록 위험은 남는다.

등록·변경할 시각이 이미 지났으면 다음 날로 넘기지 않고 새 시각을 묻는다. 수정·삭제 응답이 불확실하면 성공이라고 말하지 않고 대상 일정을 다시 조회해 현재 상태를 안내한다. 이후 변경은 새 요청과 확인이 필요하다. [멀티턴 시범 자료](../datasets/qwen/experiments/multiturn_pilot/README.md)는 이 제어부를 모의 일정으로 재생한다.

```powershell
python -m unittest discover -s ai/tests -v
```

현재 한계: 대화 상태가 메모리에만 있고 프로세스별 한 세션이다. 조회와 PATCH/DELETE 사이의 동시 변경을 막는 조건부 쓰기 계약은 백엔드에 아직 없다. 네트워크 실패 후 변경 결과가 불확실하면 성공으로 표시하지 않는다. 모델 응답의 의미 정확도와 멀티턴 평가 지표는 별도 데이터셋/평가기에서 검증해야 한다. 공유 AI endpoint, 상태 보관, 오류·재시도와 action schema의 확정은 `TBD-AI`로 남는다.
