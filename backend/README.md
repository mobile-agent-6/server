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
`OTHER`로 교체한다. 현재 `head`는 알림 컬럼 제거 revision인 `20261017_0003`까지
포함한다. 따라서 `20260927_0001` DB에서 `upgrade head`를 실행하면 카테고리
변경에 이어 알림 컬럼 제거도 적용된다. 최신 서버 코드를 사용할 DB는 전체
`upgrade head`를 적용해야 한다. 카테고리 migration만 별도로 점검해야 할 때는
`upgrade 20261008_0002`를 명시하고, 그 상태에서 최신 서버를 사용하지 않는다.

실제 DB에는 다음 순서로 적용한다.

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

## 알림 제거 migration 적용과 복구

`20261017_0003`은 제품 범위에서 제외된 알림 기능의
`schedules.reminder_minutes_before` 컬럼만 삭제한다. 기존 일정의 나머지 필드,
soft-delete 행, 인덱스와 `idempotency_records`의 fingerprint·응답 snapshot·만료
시각은 그대로 보존한다.

실제 DB에 적용할 때는 서비스를 중지하고 대상 DB와 현재 revision을 확인한 뒤,
SQLite online backup과 `integrity_check`를 완료하고 `python -m alembic upgrade head`를
실행한다. 적용 후 `/health`, 일정 CRUD, 기존 일정 수·필드와 `alembic current`를
확인한다. migration 적용 과정에서 제거 전 알림 값은 폐기된다.

새 서버 코드를 사용하기 전에 대상 DB를 반드시 현재 `head`까지 올린다. `/health`는
revision이 예상 `head`와 다르면 `503`을 반환하지만, 이 응답만으로 모든 Schedule
CRUD가 차단된다고 보장하지 않는다. `/health`를 migration 적용을 대신하는 보호장치로
사용하지 않는다.

실패 후 `_alembic_tmp_schedules`가 남아 있으면 자동 삭제하거나 곧바로 migration을
재시도하지 않는다.

1. 서비스를 중지하고 정확한 대상 DB 경로와 현재 revision을 다시 확인한다.
2. 현 상태를 보존한 사본 또는 backup을 만들고 원본 `schedules`와 잔여 테이블의
   schema·인덱스·행 수·일정 ID 및 `integrity_check`를 확인한다.
3. 원본 일정과 무결성이 보존됐음을 입증할 수 없거나 상태가 불명확하면 검증된
   migration 전 backup 복구를 우선한다.
4. 잔여 테이블이 비어 있다는 사실만으로 삭제가 안전하다고 판단하지 않는다.
   정확한 대상과 원본 보존을 확인하고 별도 승인을 받은 경우에만 잔여 테이블 처리를
   결정한 뒤 backup·무결성을 다시 확인하고 재시도한다.

schema만 이전 revision으로 되돌려야 하면 다음 명령을 사용한다.

```bash
python -m alembic downgrade 20261008_0002
```

downgrade는 `reminder_minutes_before`를 nullable 컬럼으로 다시 만들지만 모든 행의
값은 `null`이다. 이는 구버전 코드로 안전하게 돌아갈 수 있다는 보장이 아니며 제거 전
알림 설정값도 복원하지 않는다. 완전 복구는 서비스를 중지한 상태에서 migration 전
검증 backup과 그 revision에 맞는 코드를 함께 복원한다.

현행 일정 생성 fingerprint는 version `2`다. 알림 필드가 포함된 구형 요청은
`422 VALIDATION_ERROR`로 거절되며, 아직 유효한 version `1` key를 새 요청에 재사용하면
`409 IDEMPOTENCY_KEY_CONFLICT`다. 기존 기록을 수정하거나 충돌 뒤 새 key로 자동
등록하지 않는다.

## Assistant 영속 migration downgrade

`20261017_0004`는 Assistant 대화·요청·질문·write gate 테이블을 추가한다.
downgrade는 반드시 서버를 중지하고 대상 DB·revision·검증 backup을 확인한 상태에서
수행한다.

```bash
python -m alembic downgrade 20261017_0003
```

accepted 또는 processing 요청이나 아직 outcome/failure로 확정되지 않은 uncertain
기록이 있으면 downgrade는 첫 DDL 전에 거절된다. 안전 검사를 통과한 downgrade도
Assistant table과 tombstone을 모두 삭제하므로 `REQUEST_EXPIRED` 최소 기록 보장이
사라지는 파괴적 관리 작업이다. 이는 `docs/api.md` §16.8의 DB 초기화 같은 운영
예외로만 취급하고, Schedule·idempotency 데이터를 보존하더라도 구버전 코드와 검증
backup 없이 일반 rollback 수단으로 사용하지 않는다.

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
