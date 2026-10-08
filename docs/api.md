# Schedule API 설계

> 계약 상태: `BE-CONFIRMED`, 프론트 연동 항목은 `BE-FE-AGREED`
>
> 범위: MVP Schedule CRUD, 상태 확인, Orange Pi 시각 동기화

## 1. 문서 목적과 범위

이 문서는 모바일 앱이 Orange Pi의 Schedule 서버와 연동할 때 사용하는 HTTP API를 정의한다. FastAPI 구현과 자동 생성되는 `/openapi.json`은 이 계약을 따라야 한다.

- 일반 일정 화면은 이 문서의 Schedule REST API를 직접 호출한다.
- 일정 비서 화면이 호출할 AI 서비스의 endpoint와 요청·응답 형식은 아직 확정하지 않았다.
- AI 서비스 계약을 추측해 Schedule API에 필드나 endpoint를 추가하지 않는다.
- MVP에는 계정, 사용자별 일정 소유권과 API 인증을 포함하지 않는다.
- 데이터베이스 구조는 [`erd.md`](erd.md)를 참고한다.

## 2. 서버와 공통 HTTP 규칙

### 2.1 주소

```text
Base URL: http://{PI_IP}:8000
API Base: http://{PI_IP}:8000/api/v1
```

- 서버는 Orange Pi에서 `0.0.0.0:8000`에 bind한다.
- 개발 중에는 앱 설정 화면에서 Pi IP를 입력한다.
- 시연에서는 미리 정한 고정 Pi IP를 사용한다.
- MVP 통신은 Orange Pi 핫스팟 내부의 HTTP를 사용한다.

### 2.2 Content-Type과 JSON

- JSON 요청은 `Content-Type: application/json`을 사용한다.
- 성공 JSON 응답은 `application/json`이다.
- 오류 응답은 `application/problem+json`이다.
- JSON 필드명은 camelCase만 허용한다.
- snake_case, 알 수 없는 필드와 읽기 전용 필드는 검증 오류로 처리한다.
- 단건 성공 응답은 별도 wrapper 없이 리소스를 직접 반환한다.
- 목록 응답은 `{ "items": [...] }` 형태다.
- 깨진 JSON 문법은 `400 MALFORMED_REQUEST`다.
- JSON 객체가 아닌 body나 잘못된 Content-Type은 `422 VALIDATION_ERROR`의 `INVALID_TYPE`으로 처리한다.

### 2.3 Request ID

- 서버는 요청마다 새로운 UUID 형식의 request ID를 생성한다.
- 모든 응답에 `X-Request-ID` 헤더를 포함한다.
- 오류 본문의 `requestId`와 `X-Request-ID` 헤더는 같다.
- 클라이언트가 보낸 `X-Request-ID`는 MVP에서 사용하지 않는다.

### 2.4 경로

- 이 문서에 적힌 마지막 `/`가 없는 경로가 canonical path다.
- 마지막 `/`만 추가된 요청은 `307 Temporary Redirect`가 될 수 있으므로 클라이언트는 canonical path를 호출한다.
- 정의되지 않은 경로는 `404 ROUTE_NOT_FOUND`다.
- UUID 형식이 아닌 `{scheduleId}`는 `422 VALIDATION_ERROR`의 path 오류다.

### 2.5 날짜와 시간

요청 datetime은 다음 문법만 허용한다.

```text
YYYY-MM-DDTHH:MM:SS[.fraction](Z|±HH:MM)
```

허용 예시:

```text
2026-09-14T15:00:00+09:00
2026-09-14T06:00:00Z
2026-09-14T06:00:00.123456Z
```

규칙:

- `T` 구분자와 UTC `Z` 또는 콜론이 포함된 offset이 필수다.
- offset 없는 값, 공백 구분자, `+0900`, Unix timestamp는 허용하지 않는다.
- 소수초는 최대 6자리까지 허용하며 7자리 이상은 `INVALID_FORMAT`이다.
- UTC로 표현할 수 없는 경계 시각은 `INVALID_VALUE`다.
- 서버는 입력을 UTC instant로 정규화해 저장하고 비교한다.
- API datetime 응답은 UTC `Z`로 통일한다.
- 앱은 `Asia/Seoul` 기준으로 변환해 표시한다.
- query의 `+09:00`에서 `+`는 `%2B`로 URL 인코딩해야 한다. 가능하면 UTC `Z`를 사용한다.
- 과거 일정 여부 등 서버 업무 규칙은 Orange Pi 시스템 시각을 기준으로 한다.

## 3. 데이터 계약

### 3.1 Category

MVP 카테고리는 재고, 피킹, 반품, 기타 4개로 확정한다(`TEAM-CONFIRMED`). API 값은 아래 표를 따른다(`BE-CONFIRMED`).

| 화면 표시 | API 값 | 포함할 일정의 대표 예시 |
|---|---|---|
| 재고 | `INVENTORY` | 재고 실사, 재확인, 부족 수량 확인 |
| 피킹 | `PICKING` | 주문 상품 피킹, 피킹 목록 확인 |
| 반품 | `RETURN` | 반품 상품 확인, 검수 |
| 기타 | `OTHER` | 재고, 피킹, 반품 분류에 해당하지 않는 일정 |

- 생성과 수정의 `category`, 목록 조회의 `category` 필터에는 위 대문자 enum만 전송한다. 응답의 `category`도 같은 4개 값을 사용한다.
- `MEETING`, `PERSONAL`, 소문자와 한글 값은 허용하지 않는다. 요청하면 `422 VALIDATION_ERROR`의 `INVALID_ENUM`으로 거절한다.
- 대표 예시는 카테고리의 의미를 설명하며, 허용하는 일정 제목을 예시 목록으로 제한하지 않는다. 모델의 상세 업무 분류 규칙은 AI와 데이터 담당 영역에서 정리한다.
- 표의 행 순서는 프론트의 화면 표시 순서를 강제하지 않는다.
- MVP 이후 필요에 따라 관련 담당자와 협의해 카테고리를 추가할 수 있다. 추가가 확정되면 API 문서와 관련 구현을 함께 갱신한다.

### 3.2 Schedule 응답

| 필드 | 타입 | nullable | 설명 |
|---|---|---:|---|
| `id` | UUID v4 string | 아니요 | 서버 생성 |
| `title` | string | 아니요 | trim 후 1~100자 |
| `scheduledAt` | datetime string | 아니요 | 일정 시각 |
| `category` | Category | 아니요 | 위 4개 enum 중 하나 |
| `completed` | boolean | 아니요 | 완료 여부 |
| `reminderMinutesBefore` | integer | 예 | `10` 또는 `null` |
| `reminderAt` | datetime string | 예 | 서버가 계산한 알림 시각 |
| `createdAt` | datetime string | 아니요 | 서버 생성 시각 |
| `updatedAt` | datetime string | 아니요 | 마지막 실제 변경 시각 |

```json
{
  "id": "5b3e9a7c-2f1d-4c8e-9a6b-1d2e3f4a5b6c",
  "title": "3분기 재고 실사",
  "scheduledAt": "2026-09-14T06:00:00Z",
  "category": "INVENTORY",
  "completed": false,
  "reminderMinutesBefore": 10,
  "reminderAt": "2026-09-14T05:50:00Z",
  "createdAt": "2026-09-13T02:10:00Z",
  "updatedAt": "2026-09-13T02:10:00Z"
}
```

`reminderAt` 계산:

```text
reminderMinutesBefore == 10   → scheduledAt - 10분
reminderMinutesBefore == null → null
```

- `title`에는 일반 Unicode 텍스트를 허용한다.
- NUL, 줄바꿈, 탭 등 제어문자는 `INVALID_VALUE`로 거부한다.
- `reminderAt`은 읽기 전용이며 현재 시각이나 완료 여부와 무관하게 계산한다.
- soft delete용 `deletedAt`은 API에 노출하지 않는다.

### 3.3 일정 생성 요청

| 필드 | 필수 | 허용값 |
|---|---:|---|
| `title` | 예 | trim 후 1~100자 |
| `scheduledAt` | 예 | 공통 datetime 문법 |
| `category` | 예 | Category enum |
| `reminderMinutesBefore` | 예 | `10` 또는 `null` |

- 생성 요청에 `completed`를 보내지 않는다.
- 서버가 새 일정을 항상 `completed: false`로 생성한다.
- `completed`나 다른 읽기 전용 필드를 보내면 `UNKNOWN_FIELD`다.

### 3.4 일정 수정 요청

| 필드 | 요청에서 필수 | 명시적 null |
|---|---:|---|
| `title` | 아니요 | 금지 |
| `scheduledAt` | 아니요 | 금지 |
| `category` | 아니요 | 금지 |
| `completed` | 아니요 | 금지 |
| `reminderMinutesBefore` | 아니요 | 허용 |

- 전송된 필드만 수정한다.
- 한 개 이상의 수정 필드가 있어야 한다.
- `reminderMinutesBefore: null`은 알림 해제를 뜻한다.
- 이 API는 RFC 7396이 아닌 프로젝트 자체 부분 수정 규칙이다.

## 4. Endpoint 요약

| operationId | Method | Path | 성공 | 용도 |
|---|---|---|---:|---|
| `healthCheck` | GET | `/health` | 200 | 서버·DB·schema와 Pi 시각 확인 |
| `syncSystemTime` | POST | `/api/v1/system/time-sync` | 200 | Pi 시스템 시각 동기화 |
| `listSchedules` | GET | `/api/v1/schedules` | 200 | 일정 목록 조회 |
| `createSchedule` | POST | `/api/v1/schedules` | 201 | 일정 생성 |
| `getSchedule` | GET | `/api/v1/schedules/{scheduleId}` | 200 | 일정 단건 조회 |
| `updateSchedule` | PATCH | `/api/v1/schedules/{scheduleId}` | 200 | 일정 부분 수정 |
| `deleteSchedule` | DELETE | `/api/v1/schedules/{scheduleId}` | 204 | 일정 soft delete |

## 5. 상태 확인

### `GET /health`

요청 본문과 인증은 없다.

정상 응답:

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-Request-ID: 3f2a85a9-cc84-4ec9-b98d-63edc4d46e30
```

```json
{
  "status": "ok",
  "apiVersion": "v1",
  "serverTime": "2026-09-13T04:30:00Z"
}
```

| 상황 | 응답 |
|---|---|
| API·DB·schema 정상 | `200` Health 응답 |
| DB 파일을 열 수 없거나 schema revision이 준비되지 않음 | `503 DATABASE_UNAVAILABLE` |

프론트는 다음 조건을 모두 만족할 때만 입력한 Pi IP를 저장할 수 있다.

1. HTTP `200`
2. `status === "ok"`
3. `apiVersion === "v1"`
4. RTT midpoint 기준 `serverTime`과 휴대폰 시각의 차이가 60초 이내

RTT midpoint는 다음 방식으로 계산한다.

```text
RTT = 응답 수신 monotonic 시각 - 요청 시작 monotonic 시각
기기 기준 중간 시각 = 요청 시작 시 기록한 휴대폰 시각 + RTT / 2
시각 차이 = |serverTime - 기기 기준 중간 시각|
```

## 6. Pi 시스템 시각 동기화

### `POST /api/v1/system/time-sync`

사용자가 설정 화면에서 동기화에 동의한 경우에만 호출한다.

요청:

```http
POST /api/v1/system/time-sync
Content-Type: application/json
```

```json
{
  "currentTime": "2026-09-13T13:30:15+09:00"
}
```

- `currentTime`은 사용자가 동기화를 확인한 시점에 휴대폰에서 새로 읽는다.
- 이 endpoint에는 `Idempotency-Key`를 사용하지 않는다.
- 기본 허용 범위는 `2025-01-01T00:00:00Z` 이상, `2035-01-01T00:00:00Z` 미만이다.
- 서버가 다시 계산한 차이가 60초 이하면 시각을 변경하지 않는다.

응답:

| 필드 | 타입 | 설명 |
|---|---|---|
| `adjusted` | boolean | 시각 변경 명령 실행 여부 |
| `previousServerTime` | UTC `Z` datetime | 비교에 사용한 변경 전 Pi 시각 |
| `serverTime` | UTC `Z` datetime | 변경하지 않은 판독 시각 또는 변경 후 다시 읽은 시각 |

```json
{
  "adjusted": true,
  "previousServerTime": "2026-09-13T04:20:00Z",
  "serverTime": "2026-09-13T04:30:15Z"
}
```

| 상황 | 응답 |
|---|---|
| 차이 60초 이하 | `200`, `adjusted: false` |
| 시각 변경 명령 성공 | `200`, `adjusted: true` |
| 요청 형식 오류 | `422 VALIDATION_ERROR` |
| 허용 범위 밖 | `422 CLOCK_TIME_OUT_OF_RANGE` |
| NTP 활성으로 수동 변경 불가 | `409 CLOCK_AUTO_SYNC_ACTIVE` |
| lock, 권한 또는 시각 변경 명령 실패 | `503 TIME_SYNC_UNAVAILABLE` |

프론트 처리:

1. 성공 응답 후 `/health`를 다시 호출한다.
2. 재확인한 차이가 60초 이내일 때만 연결됨과 저장 가능 상태로 전환한다.
3. timeout이나 응답 유실 시 같은 값을 바로 재전송하지 않고 `/health`로 적용 여부부터 확인한다.
4. `adjusted: false` 이후에도 차이가 60초를 넘으면 자동 반복하지 않고 수동 설정을 안내한다.

## 7. 일정 목록 조회

### `GET /api/v1/schedules`

| Query | 필수 | 타입 | 설명 |
|---|---:|---|---|
| `from` | 예 | datetime | 포함 경계 |
| `to` | 예 | datetime | 제외 경계 |
| `category` | 아니요 | Category | 단일 카테고리 필터 |
| `completed` | 아니요 | boolean | 완료 여부 필터 |

```http
GET /api/v1/schedules?from=2026-09-01T00:00:00%2B09:00&to=2026-10-01T00:00:00%2B09:00&category=PICKING&completed=false
```

규칙:

- 삭제되지 않았고 `scheduledAt`이 `[from, to)`에 속하는 일정을 반환한다.
- `to <= from`이면 `422 INVALID_DATE_RANGE`다.
- `to - from`이 92일을 초과하면 `422 DATE_RANGE_TOO_LARGE`다.
- 정렬은 `scheduledAt ASC`, `createdAt ASC`, `id ASC`다.
- MVP에는 정렬 parameter와 pagination이 없다.
- 결과가 없으면 `items`는 빈 배열이다.

```json
{
  "items": []
}
```

프론트의 메인 목록 기본 범위는 `Asia/Seoul` 기준 이전 달 1일 00:00부터 다음 달의 다음 달 1일 00:00 전까지다. 예를 들어 현재가 9월이면 `[8월 1일 00:00, 11월 1일 00:00)`을 요청한다. 더 오래된 일정은 캘린더에서 해당 월로 이동했을 때 조회한다.

## 8. 일정 생성

### `POST /api/v1/schedules`

```http
POST /api/v1/schedules
Content-Type: application/json
Idempotency-Key: 7031e930-5089-45ff-a54d-4b7ab77917c5
```

```json
{
  "title": "3분기 재고 실사",
  "scheduledAt": "2026-09-14T15:00:00+09:00",
  "category": "INVENTORY",
  "reminderMinutesBefore": 10
}
```

규칙:

- `Idempotency-Key`와 네 body 필드는 모두 필수다.
- `scheduledAt`이 요청 처리 시점의 Pi 시각보다 과거이면 `422 SCHEDULE_IN_THE_PAST`다.
- 일정은 미래지만 `reminderAt`만 이미 지났다면 일정 생성은 허용한다.
- 같은 제목과 같은 시각의 일정도 별개의 일정으로 생성할 수 있다.
- 알림 없음은 `reminderMinutesBefore: null`로 명시한다.

성공:

```http
HTTP/1.1 201 Created
Content-Type: application/json
Location: /api/v1/schedules/5b3e9a7c-2f1d-4c8e-9a6b-1d2e3f4a5b6c
X-Request-ID: 3f2a85a9-cc84-4ec9-b98d-63edc4d46e30
```

본문은 생성된 Schedule이다.

## 9. 일정 단건 조회

### `GET /api/v1/schedules/{scheduleId}`

| 상황 | 응답 |
|---|---|
| 삭제되지 않은 일정 존재 | `200` Schedule |
| UUID는 유효하지만 일정이 없거나 삭제됨 | `404 SCHEDULE_NOT_FOUND` |
| UUID 형식 오류 | `422 VALIDATION_ERROR` |

## 10. 일정 수정

### `PATCH /api/v1/schedules/{scheduleId}`

```http
PATCH /api/v1/schedules/5b3e9a7c-2f1d-4c8e-9a6b-1d2e3f4a5b6c
Content-Type: application/json
```

```json
{
  "completed": true
}
```

규칙:

- 프론트는 사용자가 실제로 변경한 필드만 전송한다.
- `{}`는 `422 EMPTY_UPDATE`다.
- `reminderMinutesBefore: null`은 알림 해제다.
- `title`, `scheduledAt`, `category`, `completed`의 명시적 null은 `NULL_NOT_ALLOWED`다.
- 읽기 전용 필드, snake_case와 알 수 없는 필드는 `UNKNOWN_FIELD`다.
- `scheduledAt`이 실제로 변경됐을 때만 과거 여부를 검사한다.
- 같은 instant를 다른 offset으로 표현한 값은 시각 변경이 아니다.
- 새 `scheduledAt`이 Pi 현재 시각보다 과거이면 `422 SCHEDULE_IN_THE_PAST`다.
- 일정 시각을 변경하지 않는 제목·카테고리·완료·알림 수정은 기존 일정이 과거여도 허용한다.
- 실제 변경이 없으면 현재 Schedule을 `200`으로 반환하고 `updatedAt`은 바꾸지 않는다.
- 일정이 없거나 삭제됐으면 `404 SCHEDULE_NOT_FOUND`다.

## 11. 일정 삭제

### `DELETE /api/v1/schedules/{scheduleId}`

| 상황 | 응답 |
|---|---|
| 최초 soft delete | `204 No Content` |
| 이미 삭제된 일정 재삭제 | `204 No Content` |
| UUID는 유효하지만 처음부터 존재하지 않음 | `404 SCHEDULE_NOT_FOUND` |
| UUID 형식 오류 | `422 VALIDATION_ERROR` |

- `204` 응답에는 본문이 없다.
- 삭제된 일정은 목록에서 제외하며 GET과 PATCH에서도 존재하지 않는 것처럼 처리한다.

### 변경 후 목록 동기화

> 상태: `BE-FE-AGREED`

- 일정 생성·수정·완료 상태 변경·삭제 요청이 성공하면 프론트는 현재 사용 중인 `from`, `to`, `category`, `completed` 조건으로 일정 목록 API를 다시 호출한다.
- `POST`, `PATCH`, `DELETE`의 성공 응답을 받은 뒤 목록 재조회만 실패한 경우, 이미 성공한 변경 요청을 다시 전송하지 않는다.
- 이 경우 변경 작업 자체는 성공한 상태로 유지하며, 프론트는 목록 갱신에 실패했음을 사용자에게 안내하고 일정 목록 `GET` 요청만 다시 시도한다.
- 구체적인 안내 문구와 표시 방식은 프론트에서 결정한다.

## 12. Idempotency-Key

적용 범위는 `POST /api/v1/schedules`뿐이다.

- 1~255자의 제어문자 없는 ASCII 문자열만 허용한다.
- 프론트는 논리적인 생성 작업마다 UUID 형태의 새 키를 만든다.
- timeout, 503 또는 응답 유실처럼 결과가 불확실한 같은 요청은 payload를 바꾸지 않고 같은 키로 재시도한다.
- `201`을 받거나 이전 작업을 끝내고 새 생성을 시작할 때 새 키를 사용한다.
- 키가 없으면 header 위치의 `REQUIRED` 검증 오류다.
- AI의 키 생성·재사용 방식은 아직 확정하지 않았다.

같은 key와 같은 생성 요청을 재시도하면 최초 성공 응답을 다시 반환한다.

```http
HTTP/1.1 201 Created
Location: /api/v1/schedules/{최초 생성 ID}
Idempotent-Replayed: true
X-Request-ID: {재시도 요청의 새 ID}
```

- replay 본문은 최초 생성 당시의 Schedule snapshot이다.
- 같은 key에 다른 생성 요청을 보내면 `409 IDEMPOTENCY_KEY_CONFLICT`다.
- 성공한 `201` 기록은 24시간 유지한다.

## 13. 오류 응답

클라이언트는 변경될 수 있는 `detail` 문구가 아니라 안정적인 `code`로 분기한다.

### 13.1 Problem Details

```json
{
  "type": "https://example.invalid/problems/validation-error",
  "title": "Request validation failed",
  "status": 422,
  "detail": "One or more fields are invalid.",
  "instance": "/api/v1/schedules",
  "code": "VALIDATION_ERROR",
  "requestId": "3f2a85a9-cc84-4ec9-b98d-63edc4d46e30",
  "errors": [
    {
      "location": "body",
      "pointer": "/category",
      "code": "INVALID_ENUM",
      "detail": "Allowed: INVENTORY, PICKING, RETURN, OTHER"
    }
  ]
}
```

`errors`는 `VALIDATION_ERROR`일 때 한 개 이상 포함한다.

### 13.2 Validation error item

| 필드 | 조건 | 설명 |
|---|---|---|
| `location` | 항상 | `body`, `query`, `header`, `path` |
| `pointer` | body 오류 | RFC 6901 JSON Pointer, body 전체는 빈 문자열 |
| `parameter` | query/header/path 오류 | 원래 API parameter 이름 |
| `code` | 항상 | 안정적인 필드 오류 code |
| `detail` | 항상 | 개발자용 설명 |

필드 오류 code:

- `REQUIRED`
- `UNKNOWN_FIELD`
- `NULL_NOT_ALLOWED`
- `TOO_SHORT`
- `TOO_LONG`
- `INVALID_ENUM`
- `TIMEZONE_REQUIRED`
- `INVALID_FORMAT`
- `INVALID_TYPE`
- `INVALID_VALUE`

### 13.3 최상위 오류 code

| HTTP | code | 발생 조건 |
|---:|---|---|
| 400 | `MALFORMED_REQUEST` | JSON 문법 오류 |
| 404 | `SCHEDULE_NOT_FOUND` | 유효한 UUID지만 대상 일정 없음 |
| 404 | `ROUTE_NOT_FOUND` | 정의되지 않은 경로 |
| 405 | `METHOD_NOT_ALLOWED` | 허용되지 않은 method |
| 409 | `IDEMPOTENCY_KEY_CONFLICT` | 같은 key에 다른 생성 요청 |
| 409 | `CLOCK_AUTO_SYNC_ACTIVE` | NTP 활성으로 시각 변경 불가 |
| 422 | `VALIDATION_ERROR` | body/query/header/path 검증 실패 |
| 422 | `EMPTY_UPDATE` | PATCH body가 빈 객체 |
| 422 | `SCHEDULE_IN_THE_PAST` | 과거 일정 생성 또는 과거 시각으로 변경 |
| 422 | `INVALID_DATE_RANGE` | `to <= from` |
| 422 | `DATE_RANGE_TOO_LARGE` | 조회 기간이 92일 초과 |
| 422 | `CLOCK_TIME_OUT_OF_RANGE` | time-sync 입력이 허용 범위 밖 |
| 500 | `INTERNAL_ERROR` | 예상하지 못한 서버 오류 |
| 503 | `DATABASE_UNAVAILABLE` | DB 일시 장애 또는 `/health`의 DB·schema 준비 실패 |
| 503 | `TIME_SYNC_UNAVAILABLE` | 시각 동기화 lock·권한·명령 실패 |

### 13.4 필수 응답 헤더

- 모든 성공·오류 응답: `X-Request-ID`
- 생성 성공: `Location`
- 멱등성 replay: `Idempotent-Replayed: true`
- `405 METHOD_NOT_ALLOWED`: 해당 경로의 정확한 `Allow`
- `204 No Content`: 본문 없음

### 13.5 임시 timeout과 retry

다음 값은 Orange Pi 실기기 측정 전 사용하는 `BE-FE-AGREED` 임시 기준이다.

| 요청 | timeout | 자동 재시도 |
|---|---:|---|
| `GET /health` | 5초 | 1초 후 최대 1회 |
| Schedule 목록·CRUD | 10초 | timeout·네트워크 오류·503일 때 최대 1회 |
| `POST /api/v1/system/time-sync` | 15초 | 같은 요청을 즉시 재시도하지 않고 `/health`로 적용 여부 확인 |

- Schedule POST 재시도에는 같은 payload와 `Idempotency-Key`를 사용한다.
- PATCH는 같은 body, DELETE는 같은 `scheduleId`로만 재시도한다.
- 4xx, 409와 500 응답은 자동 재시도하지 않는다.
- 자동 재시도를 반복하지 않는다.
- 최종 timeout 값과 재시도 세부 조정은 Orange Pi 응답 시간 실측 후 확정한다(`TBD-TEAM`).

## 14. 로컬 알림 연동

백엔드는 `reminderAt`만 계산하며 실제 알림은 모바일 앱이 관리한다.

다음 조건을 모두 만족할 때 로컬 알림 등록 대상이다.

```text
completed == false
reminderAt != null
reminderAt > 기기 현재 시각
```

| 일정 변경 | 프론트 처리 |
|---|---|
| 완료 처리 | 기존 알림 취소 |
| 삭제 | 기존 알림 취소 |
| `reminderMinutesBefore: null` | 기존 알림 취소 |
| `scheduledAt` 변경 | 기존 알림 취소 후 재등록 판단 |
| `title` 변경 | 기존 알림 취소 후 최신 Schedule 제목으로 재등록 판단 |
| 미완료로 변경 | `reminderAt`이 미래면 등록 |

- 로컬 알림의 제목은 `일정 알림`, 본문은 Schedule의 `title`을 사용한다.
- `title` 변경이 성공하면 기존 알림을 취소하고, 변경 후 Schedule이 알림 등록 조건을 만족할 때만 새 제목으로 재예약한다.
- 백엔드는 Expo `notificationIdentifier`를 저장하지 않는다.
- 프론트는 `scheduleId ↔ notificationIdentifier`를 앱 재실행 후에도 유지되는 로컬 저장소에 보관하며 재예약 후 새 identifier로 갱신한다.
- 알림 권한이 거부돼도 일정 저장 자체는 성공시키고 로컬 알림만 생략한다.

## 15. OpenAPI 공개 계약

- `/openapi.json`은 OpenAPI `3.1.0`으로 제공한다.
- 4장의 `operationId`를 고정한다.
- 요청 객체는 알 수 없는 필드를 허용하지 않는다.
- 성공 JSON은 `application/json`, 오류는 `application/problem+json`으로 선언한다.
- FastAPI 기본 `HTTPValidationError`를 공개 오류 schema로 사용하지 않는다.
- 공개 component 이름은 다음과 같다.

```text
Category
ScheduleResponse
ScheduleCreate
ScheduleUpdate
ScheduleListResponse
HealthResponse
TimeSyncRequest
TimeSyncResponse
Problem
ValidationErrorItem
```

## 16. 현재 포함하지 않는 계약

다음 항목은 Schedule API 구현을 막지 않으며, 관련 담당자 확인 전 임의로 추가하지 않는다.

### `TBD-AI`

- AI 서비스 endpoint, port와 protocol
- AI와 백엔드의 프로세스 분리 방식
- AI가 Schedule API를 호출하는 방식
- AI의 `Idempotency-Key` 생성·재사용 규칙
- `affectedSchedules` schema와 알림 동기화 계약
- 재질문, 상대 날짜 해석과 AI가 생성하는 `scheduledAt` 규칙
- STT 결과와 후속 대화 protocol
- AI timeout과 retry

### `POST-MVP`

- 반복 일정, 종일 일정과 종료 시각
- 정시·30분 전·복수 알림
- 휴지통과 복원
- 다중 사용자, 인증과 사용자별 일정 소유권
- 제목 검색, 정렬 parameter와 pagination
- HTTPS, rate limiting과 외부 공개 production
- 변경분 동기화와 server push
- 재고·폐기·작업 기록 도메인
