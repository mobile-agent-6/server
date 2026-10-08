# Schedule API Backend

FastAPI와 SQLite로 구현한 일정 관리 API다.

- API 계약: [`docs/api.md`](../docs/api.md)
- 데이터베이스 구조: [`docs/erd.md`](../docs/erd.md)

## 로컬 실행

Python 3.11이 필요하다. 개발 기준 branch는 `develop`이다.

```bash
git switch develop
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
cp .env.example .env
mkdir -p data
python -m alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

실행 후 다음 주소를 확인한다.

- 상태 확인: <http://127.0.0.1:8000/health>
- Swagger UI: <http://127.0.0.1:8000/docs>

## 카테고리 migration 적용

`20261008_0002`는 카테고리 CHECK를 `INBOUND`, `INVENTORY`, `OUTBOUND`, `RETURN`,
`OTHER`로 교체한다. 실제 DB에는 다음 순서로 적용한다.

1. 서비스를 중지하고 대상 DB 경로와 현재 revision을 확인한다.
2. SQLite online backup을 만들고 `integrity_check`와 revision을 검증한다.
3. 기존 `schedules`의 카테고리와 ID를 확인한다.
4. `PERSONAL`, `PICKING`, 확인되지 않은 `MEETING` 또는 예상하지 못한 값이 있으면
   적용하지 않는다.
5. 기존 `MEETING`이 없으면 `python -m alembic upgrade head`를 실행한다.
6. 변환이 승인된 `MEETING`만 있고 그 ID가 모두 확인됐으면 쉼표로 연결한 정확한
   ID 전체를 `CONFIRMED_MEETING_IDS`에 넣어 전달한다.

```bash
python -m alembic -x "confirmed_meeting_ids=${CONFIRMED_MEETING_IDS}" upgrade head
```

migration은 전달한 ID 집합이 DB의 기존 `MEETING` ID 집합과 정확히 같을 때만
해당 행의 카테고리를 `OTHER`로 바꾼다. 일정의 다른 필드, soft-delete 행,
인덱스와 멱등성 기록은 보존한다. 적용 후 `/health`, 카테고리별 CRUD와
`alembic current`를 확인한다.

`MEETING`에서 변환된 `OTHER`는 원래 값으로 자동 복원할 수 없다. `INBOUND` 또는
`OUTBOUND` 행이 있으면 이전 revision으로 downgrade하지 않으며, migration 전
검증 backup 복구를 기본 복구 방법으로 사용한다. 두 새 카테고리가 없어 schema
downgrade가 가능하더라도 변환된 `OTHER`는 그대로 남는다. 구버전 코드는 새
CHECK와 호환되지 않으므로 구버전 코드만 다시 배포하지 않는다.

## 모바일 앱 연결

- iOS Simulator: 일반적으로 `http://127.0.0.1:8000`
- Android Emulator: 일반적으로 `http://10.0.2.2:8000`
- 실제 휴대폰: 서버 컴퓨터와 같은 네트워크에서 해당 컴퓨터의 로컬 주소와 포트 `8000` 사용

실제 장비·개인 네트워크 주소는 코드나 Git 문서에 기록하지 않는다. Orange Pi 접속 정보는 실기기 설정 후 공유한다.

## 테스트

```bash
python -m pytest --cov=app --cov-branch --cov-report=term-missing
```

## 참고

- 기본 로컬 설정에서는 실제 시스템 시간을 변경하는 time-sync 기능이 비활성화되어 있다.
- 실제 `.env`, SQLite 데이터베이스와 로그는 Git에 추가하지 않는다.
- 공유 가능한 환경변수 예시는 [`.env.example`](.env.example)을 사용한다.
