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
