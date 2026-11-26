# 너만의 비서 API 설계

> 기준: 현재 System, Schedule, Assistant와 Voice API 구현 및 [`openapi.json`](openapi.json)
>
> 서버는 runtime lock, worker, deadline, 안전한 Schedule write, recovery, tombstone 기반과 설정으로 활성화하는 Qwen 연결을 포함한다. 2026-11-16 Orange Pi에서 실제 STT와 TTS 엔진 및 합성 WAV의 HTTP 시험을 수행했고, 2026-11-17에는 실제 사람 녹음 17개를 재시험했다. API 계약, 자동 시험, Orange Pi 시험과 모바일 통합시험 결과는 구분해 기록한다.
>
> 범위: MVP Schedule CRUD, 상태 확인, Orange Pi 시각 동기화, 텍스트·음성 Assistant 공개 계약

## 1. 문서 목적과 범위

이 문서는 모바일 앱이 Orange Pi의 Schedule 서버와 연동할 때 사용하는 HTTP API를 정의한다. FastAPI 구현과 자동 생성되는 `/openapi.json`은 이 계약을 따라야 한다.

- 일반 일정 화면은 이 문서의 Schedule REST API를 직접 호출한다.
- 알림 기능은 제품 범위에서 제외했으며 Schedule 요청·응답에 알림 필드를 제공하지 않는다.
- 일정 비서 화면은 이 문서의 Assistant API를 호출하고, 서버가 모델 해석·검증 뒤 Schedule API를 실행한다. 앱은 Assistant 결과를 근거로 같은 CRUD를 다시 실행하지 않는다.
- Assistant endpoint, 영속 상태, runtime과 안전한 Schedule 실행 기반은 구현됐다. 모델 연결은 설정으로 켤 때만 동작한다(`backend/README.md`의 모델 연결). 백엔드보다 모델 서버가 늦게 시작되면 첫 text 입력에서 연결을 다시 확인한다. 그때도 연결되지 않으면 새 text와 자연어 후속 입력은 `503 ASSISTANT_UNAVAILABLE`이며, 저장 정보가 완전한 selection/confirmation과 기존 요청 조회, 취소는 처리할 수 있다.
- 음성 Assistant의 앱 공개 WAV 형식, 후속 답변, 재전송과 TTS 계약을 포함한다. 백엔드와 STT/TTS 엔진 사이의 내부 배포 protocol은 포함하지 않는다.
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
- Assistant 정상 body의 `requestId`는 앱이 입력마다 생성하는 논리 요청 ID다. HTTP 추적 ID와 다른 값이며, Assistant 오류에서 논리 ID를 연결할 수 있으면 `assistantRequestId`로 별도 제공한다.

### 2.4 경로

- 이 문서에 적힌 마지막 `/`가 없는 경로가 canonical path다.
- 마지막 `/`만 추가된 요청은 `307 Temporary Redirect`가 될 수 있으므로 클라이언트는 canonical path를 호출한다.
- 정의되지 않은 경로는 `404 ROUTE_NOT_FOUND`다.
- UUID 형식이 아닌 `{scheduleId}`는 `422 VALIDATION_ERROR`의 path 오류다.
- UUID 형식이 아닌 Assistant `{requestId}`·`{conversationId}`도 `422 VALIDATION_ERROR`의 path 오류다.

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

MVP 카테고리는 입고, 재고, 출고, 반품, 기타 5개이며 API 값은 아래 표를
따른다.

| 화면 표시 | API 값 | 포함할 일정의 대표 예시 |
|---|---|---|
| 입고 | `INBOUND` | 들어오는 상품 확인 등 입고 관련 업무 |
| 재고 | `INVENTORY` | 재고 실사, 재확인, 부족 수량 확인, 재고 위치 확인 |
| 출고 | `OUTBOUND` | 상품 피킹, 포장, 출고 전 검수, 송장 확인, 출고 준비 |
| 반품 | `RETURN` | 반품 상품 확인, 검수, 분류와 처리 |
| 기타 | `OTHER` | 교대 인수인계, 팀 회의, 안전·장비 점검 등 그 밖의 일정 |

- 생성과 수정의 `category`, 목록 조회의 `category` 필터에는 위 대문자 enum만 전송한다. 응답의 `category`도 같은 5개 값을 사용한다.
- `MEETING`, `PERSONAL`, `PICKING`, 소문자와 한글 값은 허용하지 않는다. 요청하면 `422 VALIDATION_ERROR`의 `INVALID_ENUM`으로 거절한다.
- 대표 예시는 카테고리의 의미를 설명하며, 허용하는 일정 제목을 예시 목록으로 제한하지 않는다. 모델의 상세 업무 분류 규칙은 AI와 데이터 담당 영역에서 정리한다.
- 표의 행 순서는 프론트의 화면 표시 순서를 강제하지 않는다.
- MVP 이후 필요에 따라 관련 담당자와 협의해 카테고리를 추가할 수 있다. 추가가 확정되면 API 문서와 관련 구현을 함께 갱신한다.

### 3.2 Schedule 응답

| 필드 | 타입 | nullable | 설명 |
|---|---|---:|---|
| `id` | UUID v4 string | 아니요 | 서버 생성 |
| `title` | string | 아니요 | trim 후 1~100자 |
| `scheduledAt` | datetime string | 아니요 | 일정 시각 |
| `category` | Category | 아니요 | 위 5개 enum 중 하나 |
| `completed` | boolean | 아니요 | 완료 여부 |
| `createdAt` | datetime string | 아니요 | 서버 생성 시각 |
| `updatedAt` | datetime string | 아니요 | 마지막 실제 변경 시각 |

```json
{
  "id": "5b3e9a7c-2f1d-4c8e-9a6b-1d2e3f4a5b6c",
  "title": "3분기 재고 실사",
  "scheduledAt": "2026-09-14T06:00:00Z",
  "category": "INVENTORY",
  "completed": false,
  "createdAt": "2026-09-13T02:10:00Z",
  "updatedAt": "2026-09-13T02:10:00Z"
}
```

- `title`에는 일반 Unicode 텍스트를 허용한다.
- NUL, 줄바꿈, 탭 등 제어문자는 `INVALID_VALUE`로 거부한다.
- soft delete용 `deletedAt`은 API에 노출하지 않는다.

### 3.3 일정 생성 요청

| 필드 | 필수 | 허용값 |
|---|---:|---|
| `title` | 예 | trim 후 1~100자 |
| `scheduledAt` | 예 | 공통 datetime 문법 |
| `category` | 예 | Category enum |

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

- 전송된 필드만 수정한다.
- 한 개 이상의 수정 필드가 있어야 한다.
- 이 API는 RFC 7396이 아닌 프로젝트 자체 부분 수정 규칙이다.

## 4. Endpoint 요약

| operationId | Method | Path | 성공 | 용도 | 구현 상태 |
|---|---|---|---:|---|---|
| `healthCheck` | GET | `/health` | 200 | 서버·DB·schema와 Pi 시각 확인 | 구현됨 |
| `syncSystemTime` | POST | `/api/v1/system/time-sync` | 200 | Pi 시스템 시각 동기화 | 구현됨 |
| `listSchedules` | GET | `/api/v1/schedules` | 200 | 일정 목록 조회 | 구현됨 |
| `createSchedule` | POST | `/api/v1/schedules` | 201 | 일정 생성 | 구현됨 |
| `getSchedule` | GET | `/api/v1/schedules/{scheduleId}` | 200 | 일정 단건 조회 | 구현됨 |
| `updateSchedule` | PATCH | `/api/v1/schedules/{scheduleId}` | 200 | 일정 부분 수정 | 구현됨 |
| `deleteSchedule` | DELETE | `/api/v1/schedules/{scheduleId}` | 204 | 일정 soft delete | 구현됨 |
| `createAssistantRequest` | POST | `/api/v1/assistant/requests` | 202/200 | 새 입력 접수·동일 요청 복구 | 계약 확정, 구현(모델 연결은 설정으로 켤 때만, 미연결 시 새 text 503) |
| `getAssistantRequest` | GET | `/api/v1/assistant/requests/{requestId}` | 200 | 입력 상태·결과 조회 | 계약 확정·부분 구현(영속 조회·deadline·tombstone 동작) |
| `cancelAssistantTarget` | POST | `/api/v1/assistant/conversations/{conversationId}/cancel` | 200 | 요청 또는 질문 취소 판정 | 계약 확정·부분 구현(marker-aware 취소 판정 동작) |
| `createVoiceRequest` | POST | `/api/v1/voice/requests` | 202/200 | WAV 음성 입력 접수·동일 요청 복구 | Pi 합성 WAV 시험 수행, iPhone 공동 검증 대기 |
| `getVoiceRequestSpeech` | GET | `/api/v1/voice/requests/{requestId}/speech` | 200 | 완료된 Assistant 결과의 WAV 음성 출력 | Pi 합성 WAV 시험 수행, iPhone 공동 검증 대기 |

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
GET /api/v1/schedules?from=2026-09-01T00:00:00%2B09:00&to=2026-10-01T00:00:00%2B09:00&category=OUTBOUND&completed=false
```

규칙:

- 삭제되지 않았고 `scheduledAt`이 `[from, to)`에 속하는 일정을 반환한다.
- `to <= from`이면 `422 INVALID_DATE_RANGE`다.
- `to - from`이 92일을 초과하면 `422 DATE_RANGE_TOO_LARGE`다.
- `completed`는 소문자 `true` 또는 `false`로만 전송한다.
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
  "category": "INVENTORY"
}
```

규칙:

- `Idempotency-Key`와 세 body 필드는 모두 필수다.
- `scheduledAt`이 요청 처리 시점의 Pi 시각보다 과거이면 `422 SCHEDULE_IN_THE_PAST`다.
- 같은 제목과 같은 시각의 일정도 별개의 일정으로 생성할 수 있다.

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
- `title`, `scheduledAt`, `category`, `completed`의 명시적 null은 `NULL_NOT_ALLOWED`다.
- 읽기 전용 필드, snake_case와 알 수 없는 필드는 `UNKNOWN_FIELD`다.
- `scheduledAt`이 실제로 변경됐을 때만 과거 여부를 검사한다.
- 같은 instant를 다른 offset으로 표현한 값은 시각 변경이 아니다.
- 새 `scheduledAt`이 Pi 현재 시각보다 과거이면 `422 SCHEDULE_IN_THE_PAST`다.
- 일정 시각을 변경하지 않는 제목·카테고리·완료 수정은 기존 일정이 과거여도 허용한다.
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
- 현재 생성 fingerprint는 version `2`와 HTTP method, canonical route 및 정규화한
  `title`, UTC `scheduledAt`, `category`를 포함한다.
- version `1`은 알림 필드를 포함하던 이전 규격이다. 아직 유효한 version `1` key를
  새 규격 요청에 사용하면 구형 응답을 replay하거나 새 일정을 만들지 않고
  `409 IDEMPOTENCY_KEY_CONFLICT`로 거절한다. 저장된 구형 fingerprint, 응답 snapshot과
  만료 시각은 변경하지 않는다.
- `reminderMinutesBefore`, `reminderAt`을 포함한 구형 요청은 멱등성 조회 전에
  `422 VALIDATION_ERROR`의 `UNKNOWN_FIELD`로 거절한다.
- 위 409 또는 422 뒤 새 key로 자동 재등록하지 않는다. 기존 일정이 생성됐을 수 있으므로
  먼저 목록 또는 단건 조회로 결과를 확인한다.
- 카테고리 전환 전 `MEETING`, `PERSONAL`, `PICKING` payload로 생성된 기존
  기록의 snapshot, fingerprint와 만료 시각은 변경하지 않는다. 해당
  이전 payload를 다시 요청하면 replay하지 않고 category 검증에서
  `422 VALIDATION_ERROR`의 `INVALID_ENUM`으로 거절한다.

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
      "detail": "Allowed: INBOUND, INVENTORY, OUTBOUND, RETURN, OTHER"
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
| 404 | `ASSISTANT_REQUEST_NOT_FOUND` | Assistant 요청 기록 없음. 원래 쓰기 미실행의 증거가 아님 |
| 404 | `ASSISTANT_CONVERSATION_NOT_FOUND` | Assistant 대화 기록 없음 |
| 404 | `ASSISTANT_TARGET_NOT_FOUND` | 취소 대상 없음 또는 다른 대화 소속 |
| 404 | `ROUTE_NOT_FOUND` | 정의되지 않은 경로 |
| 405 | `METHOD_NOT_ALLOWED` | 허용되지 않은 method |
| 409 | `IDEMPOTENCY_KEY_CONFLICT` | 같은 key에 다른 생성 요청 |
| 409 | `CLOCK_AUTO_SYNC_ACTIVE` | NTP 활성으로 시각 변경 불가 |
| 409 | `REQUEST_ID_CONFLICT` | 같은 Assistant 요청 ID에 다른 body |
| 409 | `CONVERSATION_BUSY` | 기존 Assistant 요청 처리 중 |
| 409 | `UNRESOLVED_UNCERTAIN` | 미인지 Assistant 불확실 결과가 남음 |
| 409 | `STALE_QUESTION` | 현재 활성 질문과 맞지 않는 후속 입력 |
| 409 | `QUESTION_EXPIRED` | 후보 선택·승인 질문 만료 |
| 409 | `ACKNOWLEDGEMENT_NOT_APPLICABLE` | 지정 ID를 uncertain 인지 대상으로 사용할 수 없음 |
| 409 | `REQUEST_EXPIRED` | 상세가 정리된 Assistant ID의 POST 재접수 거절 |
| 409 | `CONVERSATION_EXPIRED` | 만료된 Assistant 대화에 새 입력 |
| 409 | `VOICE_BUSY` | STT 또는 TTS 처리 슬롯이 이미 사용 중 |
| 409 | `SPEECH_NOT_READY` | Assistant 결과가 아직 TTS 가능한 완료 상태가 아님 |
| 410 | `REQUEST_EXPIRED` | Assistant 요청의 상세 결과가 정리됨 |
| 413 | `REQUEST_BODY_TOO_LARGE` | Assistant JSON body가 UTF-8 16 KiB 초과 또는 음성이 1 MiB·15초 초과 |
| 422 | `VALIDATION_ERROR` | body/query/header/path 검증 실패 |
| 422 | `EMPTY_UPDATE` | PATCH body가 빈 객체 |
| 422 | `SCHEDULE_IN_THE_PAST` | 과거 일정 생성 또는 과거 시각으로 변경 |
| 422 | `INVALID_DATE_RANGE` | `to <= from` |
| 422 | `DATE_RANGE_TOO_LARGE` | 조회 기간이 92일 초과 |
| 422 | `CLOCK_TIME_OUT_OF_RANGE` | time-sync 입력이 허용 범위 밖 |
| 422 | `SPEECH_NOT_RECOGNIZED` | 빈 음성·0 frame 또는 인식 결과 없음 |
| 422 | `AUDIO_UNSUPPORTED` | WAV/PCM 규격 또는 `Content-Type` 불일치 |
| 422 | `VOICE_ANSWER_UNCLEAR` | 후보 번호·승인 답변을 하나로 확정할 수 없음 |
| 500 | `INTERNAL_ERROR` | 예상하지 못한 서버 오류 |
| 503 | `DATABASE_UNAVAILABLE` | DB 일시 장애 또는 `/health`의 DB·schema 준비 실패 |
| 503 | `TIME_SYNC_UNAVAILABLE` | 시각 동기화 lock·권한·명령 실패 |
| 503 | `ASSISTANT_UNAVAILABLE` | Assistant 서비스 사용 불가 |
| 503 | `VOICE_UNAVAILABLE` | STT 서비스 또는 음성 기능 사용 불가 |
| 503 | `TTS_UNAVAILABLE` | TTS 서비스 또는 출력 WAV 사용 불가 |

### 13.4 필수 응답 헤더

- 모든 성공·오류 응답: `X-Request-ID`
- 생성 성공: `Location`
- 멱등성 replay: `Idempotent-Replayed: true`
- `405 METHOD_NOT_ALLOWED`: 해당 경로의 정확한 `Allow`
- `204 No Content`: 본문 없음

### 13.5 timeout과 retry 정책

다음 값은 앱과 공개 API 사이의 요청 제한 시간 및 자동 재시도 정책이다.

| 요청 | timeout | 자동 재시도 |
|---|---:|---|
| `GET /health` | 5초 | 1초 후 최대 1회 |
| Schedule 목록·CRUD | 10초 | timeout·네트워크 오류·503일 때 최대 1회 |
| `POST /api/v1/system/time-sync` | 15초 | 같은 요청을 즉시 재시도하지 않고 `/health`로 적용 여부 확인 |
| Assistant POST·GET | 각 5초 | POST 응답이 없으면 먼저 원래 ID로 GET. GET 실패만 다시 조회 |

- Schedule POST 재시도에는 같은 payload와 `Idempotency-Key`를 사용한다.
- PATCH는 같은 body, DELETE는 같은 `scheduleId`로만 재시도한다.
- Schedule·time-sync의 4xx, 409와 500 응답은 자동 재시도하지 않는다.
- Assistant 오류의 재조회·재전송은 16.9절을 따른다. 같은 앱 실행·120초 안의 `404 ASSISTANT_REQUEST_NOT_FOUND`에 같은 ID·같은 body를 보내는 경우만 제한된 예외다.
- 자동 재시도를 반복하지 않는다.
- Assistant 정상 polling은 직전 GET 응답 뒤 2초를 기다리며 요청을 겹치지 않는다. 최초 POST 시도부터 120초가 지나면 자동 조회와 재전송을 중단하되 로컬 복구 정보를 자동 삭제하지 않는다.
- 음성 POST와 TTS GET의 운영 권장값은 16.14.4의 Orange Pi 실측 결과를 따른다.

## 14. 알림 제거와 구형 클라이언트 호환성

- 제품 범위에서 알림을 제외했으며 백엔드는 알림 설정을 저장·계산·응답하지 않는다.
- 생성·수정 요청의 `reminderMinutesBefore`와 읽기 전용 `reminderAt`은 알 수 없는
  필드로 처리한다.
- 새 API 응답에는 두 필드가 없으므로 구형 클라이언트·AI 제어부·평가 도구는 새
  서버 버전에 맞게 조정한 뒤 연동한다.
- 과거 migration과 기존 멱등성 응답 snapshot에는 재현·보존 목적으로 알림 필드가
  남을 수 있다. 이를 현재 API가 알림을 지원한다는 뜻으로 해석하지 않는다.

## 15. OpenAPI 공개 계약

- `/openapi.json`은 OpenAPI `3.1.0`으로 제공한다.
- 4장의 구현된 endpoint `operationId`를 고정한다. Assistant와 Voice의 다섯 operation도 공개 schema에 포함된다.
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
AssistantRequestCreate
AssistantRequestAcceptedResponse
AssistantRequestStatusResponse
AssistantCancelRequest
AssistantCancelResponse
AssistantResultNeedMoreInfo
AssistantResultCandidateSelection
AssistantResultConfirmation
AssistantResultOutcome
AssistantResultFailure
AssistantResultUncertain
AssistantCandidate
AssistantScheduleSummary
VoiceRequestAcceptedResponse
Problem
ValidationErrorItem
```

현재 `/openapi.json`은 다음 Assistant·Voice path 5개를 공개한다.

```text
/api/v1/assistant/requests
/api/v1/assistant/requests/{requestId}
/api/v1/assistant/conversations/{conversationId}/cancel
/api/v1/voice/requests
/api/v1/voice/requests/{requestId}/speech
```

위 component 목록은 앱·연동 계약에서 직접 참조하는 주요 schema이며 `components.schemas`의 전체 집합은 아니다. 중첩 객체·enum·union을 표현하는 `AssistantCancelTarget`, `AssistantCancelTargetState`, `AssistantCancelTargetType`, `AssistantConfirmationChanges`, `AssistantConfirmationOperation`, `AssistantFailureCode`, `AssistantFailureNextAction`, `AssistantInputConfirmation`, `AssistantInputSelection`, `AssistantInputText`, `AssistantOutcome`, `AssistantOutcomeOperation`, `AssistantRequestStatus` 보조 component도 OpenAPI에 노출된다.

## 16. 텍스트 Assistant API 공개 계약

> 구현: DB별 runtime lock, 단일 worker, 300초 deadline, gate와 marker 기반 Schedule 안전 write, idempotent create recovery, atomic update/delete, uncertain acknowledge와 bounded tombstone cleanup을 포함한다. 모델(Qwen) 연결은 설정으로 활성화하며, 시작할 때 준비되지 않으면 첫 text 입력에서 한 번 더 연결을 확인한다. 그때도 연결되지 않으면 새 text와 자연어 후속 POST는 insert 없이 `503 ASSISTANT_UNAVAILABLE`이고, 저장 정보가 완전한 selection/confirmation과 기존 GET, cancel은 동작한다.

### 16.1 역할과 기본 흐름

- 수동 일정 조작은 앱이 기존 Schedule API를 직접 호출한다.
- 일정 비서는 앱이 Assistant API를 호출하고, 서버가 모델 해석·검증 뒤 Schedule API를 실행한다.
- 앱은 Assistant의 성공 결과를 근거로 같은 Schedule write를 다시 실행하지 않는다.
- 지원 작업은 등록·조회·수정·삭제·완료 상태 변경이다.
- 정보가 충분한 단일 등록은 추가 승인 없이 실행한다. 다만 한 입력에 여러 작업이 있어 첫 작업이 등록인 경우, 요청이 수정·삭제·완료 변경·조회로도 읽힐 수 있거나 등록 요청이 분명하지 않은 경우, 정정한 날짜·시각을 다시 확인해야 하는 경우에는 실행 전에 `need_more_info` 질문으로 등록 여부를 묻고 `text` 답변(예: 네, 아니요)을 받는다. 수정·삭제·AI를 통한 완료 상태 변경은 대상과 변경 내용을 확인한 뒤 실행한다.
- 한 Assistant write 성공은 일정 하나만 변경한다. MVP에서 여러 일정을 한 입력으로 일괄 변경하지 않는다.
- 한 입력에 여러 작업이 있으면 서버는 첫 번째 작업만 처리하고, 나머지 작업은 자동 저장하거나 대기열에 넣지 않는다. 첫 작업에 대한 질문·후보·확인·조회·대상 없음·취소 문구에는 나머지를 이어서 요청하라는 안내를 붙일 수 있으며, 등록·수정·삭제 성공이나 실패 문구에는 붙지 않을 수 있다.
- 알림은 제외하며 `reminderMinutesBefore`·`reminderAt`을 만들거나 전달하지 않는다.

처리 상태는 다음 단방향 흐름을 기본으로 한다.

```text
accepted → processing → completed
```

`completed`는 입력 하나의 처리가 종결됐다는 뜻이다. 결과가 추가 질문·후보 선택·승인 요청·불확실일 수 있으므로 대화 종료나 일정 변경 성공과 같지 않다.

### 16.2 요청 body와 식별 정보

`POST /api/v1/assistant/requests`는 첫 입력·후속 답변·후보 선택·승인 또는 거절을 접수한다.

| 필드 | 타입·주체 | 필수·용도 |
|---|---|---|
| `requestId` | 앱 생성 UUID v4 string | 입력마다 필수. 대화 ID나 HTTP 추적 ID가 아님 |
| `conversationId` | 서버 발급 UUID v4 string | 첫 입력은 생략, 같은 대화의 후속 입력에는 필수 |
| `input` | object | 아래 3종 중 하나 |
| `acknowledgedUncertainRequestId` | UUID v4 string | 불확실을 인지하고 새 text 작업을 시작할 때만 사용 |

| `input.type` | `input` 필드 | 조건 |
|---|---|---|
| `text` | `text`, 선택적으로 `questionId` | 첫 입력은 text만. 활성 질문에 답하는 자유 텍스트에는 현재 questionId 필수 |
| `selection` | `questionId`, `candidateId` | 서버가 현재 질문에 제공한 값을 그대로 전송 |
| `confirmation` | `questionId`, `approved` | `approved`는 boolean. false는 해당 변경안 거절이며 대화 전체 삭제가 아님 |

- `conversationId`와 `questionId`는 서버가 발급한다.
- `requestId`·`conversationId`·`questionId`·`acknowledgedUncertainRequestId`는 하이픈을 포함한 canonical RFC variant UUID v4 형식이다. 영문 대문자를 포함한 유효한 입력도 받지만 서버는 소문자로 정규화해 저장·응답한다.
- `candidateId`는 불투명 문자열이며 앱이 생성하거나 순번·Schedule ID로 바꾸지 않는다.
- `text`는 짝이 맞지 않는 Unicode surrogate를 거절한다. Unicode `Cc` 제어문자 중 탭·LF·CR만 허용하며 나머지는 `422 VALIDATION_ERROR`다.
- `text`는 trim 전 원문을 Unicode 코드 포인트 기준 1~500자로 검사한다. 검사 후 U+3000을 포함한 Unicode 앞뒤 공백을 제거하며, 공백만 남으면 `422 VALIDATION_ERROR`다.
- 서버는 정규화한 `text`를 저장하고 같은 `requestId`의 동일 요청 판정에도 사용한다.
- 전체 JSON 요청 body는 UTF-8 기준 16 KiB 이하다. 초과하면 `413 REQUEST_BODY_TOO_LARGE`다.
- 잘못된 타입·enum·null·알 수 없는 필드는 거절한다.
- 앱은 전체 채팅 이력·모델 출력·Schedule 변경 payload를 보내지 않는다.

첫 입력 예시:

```json
{
  "requestId": "11111111-1111-4111-8111-111111111111",
  "input": {
    "type": "text",
    "text": "내일 반품 검수 등록해줘"
  }
}
```

후속 입력 예시:

```json
{
  "requestId": "33333333-3333-4333-8333-333333333333",
  "conversationId": "22222222-2222-4222-8222-222222222222",
  "input": {
    "type": "text",
    "questionId": "44444444-4444-4444-8444-444444444444",
    "text": "오후 3시"
  }
}
```

### 16.3 접수·조회와 중복 요청

신규 요청을 영속적으로 접수하면 `202 Accepted`를 반환한다.

```json
{
  "requestId": "11111111-1111-4111-8111-111111111111",
  "conversationId": "22222222-2222-4222-8222-222222222222",
  "status": "accepted"
}
```

앱은 `GET /api/v1/assistant/requests/{requestId}`로 원래 입력의 상태와 결과를 조회한다. 정상 GET과 동일 요청 재수신의 `200`은 `requestId`·`conversationId`·`status`를 반환한다. `accepted`·`processing`에서는 `result`를 생략하고 `completed`에서만 포함한다.

전체 대화 내역을 조회하는 endpoint는 없다. `conversationId`는 같은 작업의 후속 입력을 연결하지만 대화 transcript를 조회하는 ID가 아니며, 앱은 필요한 개별 요청을 원래 `requestId`로 조회한다.

```json
{
  "requestId": "11111111-1111-4111-8111-111111111111",
  "conversationId": "22222222-2222-4222-8222-222222222222",
  "status": "completed",
  "result": {
    "kind": "need_more_info",
    "text": "몇 시로 등록할까요?",
    "questionId": "44444444-4444-4444-8444-444444444444"
  }
}
```

- 같은 `requestId`와 동일한 정규화 body를 다시 받으면 기존 상태·결과를 `200`으로 반환하고 모델이나 Schedule write를 재실행하지 않는다.
- 이 기존 요청 판정은 `CONVERSATION_BUSY`·질문 유효성·대화 만료 검사보다 먼저 한다.
- 같은 ID에 다른 body가 오면 `409 REQUEST_ID_CONFLICT`다.
- 상세가 최소 기록으로 정리된 ID는 body 동일 여부와 관계없이 `409 REQUEST_EXPIRED`다.
- POST 판정 순서는 body 크기 `413` → Content-Type `422` → JSON 형식 `400` → schema `422` → requestId의 기존 상세·충돌·최소 기록 → 대화·질문·불확실 보류다.
- GET이나 서버 재시작을 이유로 모델 처리·Schedule write를 다시 실행하지 않는다.
- 서버 재시작 전에 실행되지 않은 질문 대기는 자동으로 활성 재개하지 않는다. 과거 질문 결과가 GET에 남아 있어도 후속 입력은 현재 유효성 검사를 통과해야 한다.

### 16.4 결과 6종

모든 `result`에는 `kind`와 `text: string`이 필수다.

| `result.kind` | 추가 필드 | 앱 처리 |
|---|---|---|
| `need_more_info` | `questionId` | 질문 표시 후 답변 |
| `candidate_selection` | `questionId`, `expiresAt`, `candidates` | 실제 후보 표시·선택 |
| `confirmation` | `questionId`, `expiresAt`, `operation`, `target`, 수정이면 `changes` | 대상·변경 내용 확인 후 승인·거절 |
| `outcome` | `outcome`, `schedulesChanged`, 상황별 `operation`·결과 데이터 | 성공·취소·대상 없음 구분 |
| `failure` | `code`, `nextAction` | 오류 code에 맞게 안내 |
| `uncertain` | `code`, `nextAction` | 결과 미확인 표시·원래 ID 조회·명시적 보류 해제 |

공통 규칙:

- 앱은 `result.text`를 사용자에게 표시하되, 화면과 후속 입력 방식은 문구를 해석하지 않고 `kind`와 각 결과의 `outcome`·`code` 등 구조화 필드로 결정한다.
- `need_more_info`는 같은 대화에서 연속으로 반환될 수 있다. 후속 답변은 항상 가장 최근 활성 질문의 `questionId`를 사용한다.
- `expiresAt`은 UTC `Z` datetime string이다. 후보·승인에는 필수이며 기한 없는 정보 보완에서는 생략한다.
- `candidates`는 2~10개다. 각 항목은 `candidateId`·`title`·`scheduledAt`·`category`를 항상 제공한다.
- candidate의 `category`는 `INBOUND / INVENTORY / OUTBOUND / RETURN / OTHER` 중 하나다.
- `target`과 조회 `items`의 일정 요약은 `id`·`title`·`scheduledAt`·`category`·`completed`·`updatedAt`이다.
- `confirmation.operation`은 `update / delete`다. 완료 상태 변경은 `update`와 `changes.completed`를 사용한다. `changes.scheduledAt`은 값이 있을 때 UTC `Z` RFC 3339 datetime string이다. delete에서는 `changes`를 생략한다.
- `outcome.outcome`은 `success / cancelled / no_match`다.
- `outcome.operation`은 `create / list / update / delete`이며 작업 특정 전 취소에서는 생략할 수 있다.
- 성공한 create/update/delete에는 `schedulesChanged: true`와 실제 Schedule ID 하나를 담은 `affectedScheduleIds`가 필수다.
- 조회 성공은 `operation: list`, `schedulesChanged: false`, `items`를 반환한다. 0건은 `items: []`다.
- 취소·거절·대상 없음은 `schedulesChanged: false`이며 관련 없는 `items`와 변경 ID를 생략한다.
- `uncertain`에는 `schedulesChanged: false`를 넣지 않는다. 미변경이 확정됐다는 뜻으로 해석하면 안 된다.

후보의 `title + scheduledAt + category`가 모두 같아 앱에서 구별할 수 없으면 `candidate_selection`을 반환하지 않는다. 실제 조회 결과에 사용자가 설명할 수 있는 구분 조건이 있으면 `need_more_info`로 먼저 질문한다. 끝내 하나로 확정할 수 없으면 `failure / TARGET_AMBIGUOUS / revise_input`으로 종결하고 쓰기를 실행하지 않는다.

confirmation 예시:

```json
{
  "kind": "confirmation",
  "text": "반품 검수를 오후 4시로 변경할까요?",
  "questionId": "44444444-4444-4444-8444-444444444444",
  "expiresAt": "2026-10-18T05:10:00Z",
  "operation": "update",
  "target": {
    "id": "77777777-7777-4777-8777-777777777777",
    "title": "반품 검수",
    "scheduledAt": "2026-10-18T06:00:00Z",
    "category": "RETURN",
    "completed": false,
    "updatedAt": "2026-10-17T03:00:00Z"
  },
  "changes": {
    "scheduledAt": "2026-10-18T07:00:00Z"
  }
}
```

### 16.5 취소

`POST /api/v1/assistant/conversations/{conversationId}/cancel`은 지정한 요청 또는 질문의 취소 가능 상태를 즉시 `200`으로 반환한다. 취소에는 새 requestId·`202`·추가 polling을 사용하지 않는다. 별도 취소 버튼은 MVP 필수가 아니며, confirmation의 `approved: false`는 변경안 거절이다. 앱 종료·화면 이탈·통신 종료는 서버 취소가 아니다.

```json
{
  "target": {
    "type": "request",
    "id": "11111111-1111-4111-8111-111111111111"
  }
}
```

`target.type`은 `request / question`이다. 대상은 경로의 `conversationId`에 속해야 한다.

정상 응답은 요청의 `target`, 아래 `targetState`, 사용자 안내용 `text`를 반환한다.

| `targetState` | 의미·후속 처리 |
|---|---|
| `cancelled` | 실행 전 취소 확정 |
| `not_guaranteed` | write 전송이 시작돼 중단 보장 불가. 원래 요청 GET |
| `already_completed` | 입력 처리가 이미 끝남. 남은 활성 질문은 questionId로 별도 취소 |
| `uncertain` | 원래 결과 미확인 유지 |
| `not_active` | 질문이 이미 답변·만료·무효 상태 |

request 취소가 `cancelled`로 확정된 경우에만 원래 GET을 `completed + outcome/cancelled + schedulesChanged: false`로 바꾼다. `not_guaranteed`·`uncertain`은 원래 결과를 취소 성공으로 덮어쓰지 않는다. question 취소는 질문만 무효화하며 질문을 반환한 과거 요청 결과는 유지한다.

### 16.6 접수 전 오류와 복구 필드

Assistant의 접수 전 거절·GET·취소 오류도 13장의 Problem Details를 사용한다. HTTP별 추적 ID는 `requestId`, 앱의 논리 요청 ID는 연결할 수 있을 때 `assistantRequestId`로 제공한다.

추가 복구 필드:

- `409 CONVERSATION_BUSY`: 처리 중인 `blockingRequestId`
- `409 UNRESOLVED_UNCERTAIN`: 가장 오래된 미인지 불확실의 `blockingRequestId`
- `409 STALE_QUESTION`: 활성 질문이 있으면 `activeQuestionId`와 그 질문 결과를 반환한 `activeQuestionRequestId`

앱은 `activeQuestionRequestId`를 GET해 질문 내용을 복구하되 과거 승인·선택을 자동 전송하지 않는다. `404`는 status만 보지 않고 Problem Details의 `code`로 `ASSISTANT_REQUEST_NOT_FOUND`와 `ROUTE_NOT_FOUND` 등을 구별한다.

### 16.7 접수 후 실패·불확실

접수 뒤 오류는 GET의 `completed` 결과로 전달한다.

| kind·code | `nextAction` | 의미 |
|---|---|---|
| `failure · MODEL_OUTPUT_INVALID` | `revise_input` | 모델 출력 검증 실패, write 미실행 |
| `failure · SCHEDULE_REJECTED` | `revise_input` | Schedule API가 요청을 확정 거절 |
| `failure · TARGET_AMBIGUOUS` | `revise_input` | 대상을 하나로 확정할 수 없어 write 미실행 |
| `failure · MODEL_TIMEOUT / SCHEDULE_UNAVAILABLE / PROCESSING_INTERRUPTED` | `retry_later` | 확인된 실패. 자동 재실행 지시가 아님 |
| `failure · PROCESSING_TIMEOUT` | `retry_later` | 300초 상한 도달, write 미실행이 확실함 |
| `failure · INTERNAL_ERROR` | `none` | 내부 오류 |
| `uncertain · EXECUTION_RESULT_UNKNOWN` | `check_status` | write 전송 또는 반영 가능성이 남아 있음 |

`nextAction`은 앱이 write를 자동 실행하라는 명령이 아니다. `completed` 결과는 원칙적으로 바뀌지 않으며, 실제 근거를 확보한 `uncertain → outcome/failure`만 예외로 허용한다. 반대 방향 전환은 없다. 앱은 `uncertain`을 사용자가 다시 확인할 때와 앱 재실행 때 원래 requestId로 다시 GET한다.

### 16.8 시간·대화·결과 보관

| 항목 | 값 | 규칙 |
|---|---:|---|
| POST 접수 응답 대기 | 5초 | 앱 timeout과 서버 취소는 별개 |
| GET 1회 응답 대기 | 5초 | 실패한 GET만 재조회하며 write를 반복하지 않음 |
| 정상 polling 간격 | 2초 | 직전 GET 응답 뒤 대기, 요청을 겹치지 않음 |
| 앱 자동 조회 한도 | 최초 POST 시도부터 120초 | 동일 요청 재전송으로 초기화하지 않음 |
| 후보·승인 질문 만료 | 발급부터 10분 | 조회·재전송으로 연장하지 않음. `expiresAt` 우선 |
| 정보 보완 질문 | 일률 10분 미적용 | 오래돼 문맥이 애매하면 필요한 정보·날짜 재확인 |
| 확정된 상세 결과 | 아래 기산점부터 최소 24시간 | 기간 안에 원래 requestId로 조회 가능 |
| 대화 전체 수명 | 마지막 유효 입력부터 24시간 무활동 | GET·동일 요청 재전송·거절된 입력으로 연장하지 않음 |
| 서버 요청 처리 상한 | 영속 접수부터 300초 | 앱 자동 조회 120초와 별개인 초기 운영값 |

300초에 도달하면 요청은 `completed`로 종결한다. write 미실행이 확실하면 `failure / PROCESSING_TIMEOUT`, write가 전송됐거나 실행 여부를 확정할 수 없으면 `uncertain / EXECUTION_RESULT_UNKNOWN`이다. 이미 전송된 write의 늦은 반영 가능성을 실패로 단정하지 않는다.

`accepted`·`processing` 또는 미해소 `uncertain`이 연결된 대화는 시간만으로 만료하지 않는다. 만료된 `conversationId`의 새 입력은 `409 CONVERSATION_EXPIRED`이며 과거 문맥을 자동 복원하거나 새 실행을 시작하지 않는다. 기존 requestId의 GET 가능 기간은 개별 요청 보관 규칙을 따른다.

상세 결과를 최소 기록으로 줄일 수 있는 시점:

- `outcome`·`failure`: 결과 확정부터 최소 24시간 뒤
- 질문 3종: 질문이 답변·취소·만료·무효로 비활성이 된 때부터 최소 24시간 뒤
- 나중에 확정된 `uncertain`: `outcome`·`failure` 확정 전환부터 최소 24시간 뒤

`accepted`·`processing`, 활성 질문, 아직 확정되지 않은 `uncertain`은 사용자가 인지했더라도 최소 기록으로 줄이지 않는다. 상세를 정리한 뒤에는 requestId의 최소 기록을 MVP 기간 동안 자동 삭제하지 않는다. DB 초기화 같은 운영 작업은 예외로 기록한다.

최소 기록 상태에서 GET은 `410 REQUEST_EXPIRED`, 같은 ID의 POST는 `409 REQUEST_EXPIRED`다. 해당 ID에 진행 중 처리·활성 질문·미해소 불확실이 없고 새 실행도 없다는 뜻이지만 과거 성공·실패는 알려주지 않는다. 최소 기록에서 상세 결과 상태로 되돌아가지 않는다.

### 16.9 앱 재실행과 로컬 복구 정보 정리

앱은 POST 전에 `requestId`·서버 식별값·`firstPostAttemptAt`·`lastPostAttemptAt`·`acceptConfirmed`를 로컬에 저장한다. `202`·동일 요청 `200` 또는 GET `200`을 받으면 `acceptConfirmed: true`로 갱신하고, 확인한 `conversationId`와 마지막 상태·결과 종류도 저장한다. 저장 실패 시 POST를 보내지 않는다. 원래 body의 영구 저장은 요구하지 않으며 앱 재실행 후에는 원래 requestId로 GET만 수행한다. 복구 항목이 있으면 새 입력 전에 먼저 원래 requestId를 GET한다. 확인 뒤에도 항목이 남아 있다는 이유만으로 새 대화를 막지 않으며, 새 작업 허용 여부는 서버의 `CONVERSATION_BUSY`·`UNRESOLVED_UNCERTAIN` 판정을 따른다.

로컬의 자동 복구 목록 정리 기준:

| 확인 결과·상황 | 앱 처리 | 자동 정리 |
|---|---|---|
| GET `200 completed` + `outcome/failure` | 결과 표시 | 표시 뒤 가능 |
| GET `200 completed` + 질문 3종 | 질문 복구 유지 | 다음 입력 `2xx` 접수로 교체되거나 답변·취소·만료·`not_active`가 확인되면 가능 |
| GET `200 completed` + `uncertain` | 결과 미확인 표시·사용자 확인과 앱 재실행 때 재조회 | 확정 결과로 바뀌거나 해당 ID를 인지한 새 text 요청이 `2xx`로 접수될 때만 자동 복구 목록에서 제외 가능 |
| GET `200 accepted/processing` | 유지. 120초 뒤 자동 조회만 중단 | 금지 |
| GET `410` 또는 POST `409 REQUEST_EXPIRED` | 상세 결과 보관 기간 경과 안내·Schedule 목록 GET 권장 | 즉시 가능 |
| `404 ASSISTANT_REQUEST_NOT_FOUND` | 미실행으로 해석하지 않음. 늦은 POST·기록 유실·DB 초기화·다른 서버 가능성 안내 | 금지. 사용자 확인 뒤 수동 정리만 허용 |
| 네트워크·timeout·`5xx`·`503`·`ROUTE_NOT_FOUND`·비표준 오류 body | 다음 기회에 원래 ID GET | 금지 |
| 저장 서버와 현재 서버가 다름 | 다른 서버에 GET하지 않고 설정 확인 | 금지. 수동 정리만 허용 |
| 120초 자동 조회 종료 또는 단순 시간 경과 | 결과 확인 필요 상태 유지 | 금지 |

같은 앱 실행 중 POST 응답을 확인하지 못하면 먼저 GET한다. 최초 POST 시도부터 120초 안에 `404 ASSISTANT_REQUEST_NOT_FOUND`를 확인하고 원래 body를 메모리에 그대로 보유한 경우에만 같은 ID·같은 body의 제한된 재전송을 허용한다. 120초 뒤에는 자동 조회와 재전송을 중단하지만 로컬 항목을 자동 삭제하지 않는다.

사용자는 모든 상태에서 한 번의 명시적 확인 뒤 로컬 복구 정보만 수동 정리할 수 있다. 이 정리는 서버 취소·불확실 해소·원래 write 미실행·동일 작업의 안전한 재실행을 뜻하지 않는다.

### 16.10 불확실 보류 해제

단일 사용자·단일 단말 MVP에서는 미인지 `uncertain`이 있으면 새 `conversationId`를 만들어도 같은 서버의 새 Assistant 작업 전체를 보류한다. GET·질문 복구·불확실 고지·명시적 보류 해제는 허용하며 수동 Schedule API는 차단하지 않는다.

여러 미인지 불확실이 있으면 `blockingRequestId`는 가장 오래된 한 건을 가리킨다. 사용자가 위험을 인지하고 새 text 작업을 명시적으로 시작할 때만 `acknowledgedUncertainRequestId`를 보낼 수 있다.

- 현재 미인지 uncertain이면 인지 처리와 새 요청 접수를 원자적으로 적용한다.
- 해당 ID가 이미 인지됐거나 뒤에 `outcome`·`failure`로 확정됐으면 인지 조건을 충족한 것으로 보고 새 요청 검사를 계속한다.
- 존재하지 않거나 uncertain 이력이 아닌 ID는 `409 ACKNOWLEDGEMENT_NOT_APPLICABLE`이다.
- 새 요청이 다른 검증·대화 상태·동시성 사유로 거절되면 인지 처리도 적용하지 않는다.
- 보류 해제는 원래 요청을 성공·실패·취소로 바꾸거나 재실행하지 않는다.

### 16.11 상대 날짜·시간대

- 상대 날짜는 `Asia/Seoul`과 작업을 시작한 첫 유효 입력의 서버 접수 시각을 기준으로 해석한다.
- 같은 작업의 후속 답변·재전송·복구에서는 기준 시각과 이미 해석한 날짜를 유지한다.
- 사용자가 날짜를 명시적으로 고치거나 새 작업을 시작하면 새 기준을 적용한다.
- 기간을 말하지 않은 일반 일정 조회는 기준 시각부터 92일 뒤까지를 조회한다.
- 일정 조회와 수정·삭제 대상 검색에서 연도를 포함해 명시한 과거 날짜는 해당 날짜를 조회·검색한다.
- 등록할 시각과 수정의 새 날짜·시각에서 연도까지 명시한 과거 시각은 미래로 바꾸지 않고 Schedule 과거 시각 거절 계약을 따른다. 상대 표현으로 해석한 시각이 이미 지났으면 실행 전에 날짜와 시간을 다시 묻는다.
- 연도 없는 날짜는 경로별로 다르다. 일정 조회는 기준일 이후 가장 가까운 해당 날짜를 조회하고, 수정·삭제 대상 검색은 올해와 내년의 해당 날짜를 모두 검색한다. 등록할 시각이나 수정의 새 날짜에서 연도 없는 날짜가 올해 이미 지났으면 다음 해를 말하는지 다시 묻는다.
- `이번 주`·`다음 주`·`지난주`는 `Asia/Seoul` 기준 월요일 00:00 이상, 다음 월요일 00:00 미만의 범위다.
- 날짜를 말하지 않은 수정·삭제 대상 검색은 기준일의 시작을 경계로 과거 92일과 미래 92일을 검색한다. 따라서 과거 일정도 후보가 될 수 있다.
- 서버 시각을 신뢰할 수 없거나 오래 지나 의미가 애매하면 추측해 write를 실행하지 않고 절대 날짜·시각을 다시 확인한다.
- 서버 시각은 `POST /api/v1/system/time-sync`가 성공한 뒤부터 신뢰한다. 서버가 다시 시작되거나, 이후 시각 동기화가 503·409로 실패하거나, 시스템 시각이 경과 시간과 60초 넘게 어긋나면 다시 동기화할 때까지 신뢰하지 않는다.
- Schedule API에는 2.5절의 시간대 포함 datetime을 보내며 UTC 정규화·과거 시각 검증을 그대로 적용한다.

### 16.12 Assistant 카테고리 해석

- 등록할 일정에서 사용자가 카테고리를 명시하지 않아도 등록할 수 있다. 서버는 제목의 표현으로 카테고리를 판단하고, 판단할 표현이 없으면 `OTHER`를 사용한다.
- `입고`는 `INBOUND`, `재고`는 `INVENTORY`, `출고`·`피킹`·`포장`은 `OUTBOUND`, `반품`은 `RETURN`으로 해석한다.
- 등록할 일정에서 `출하`·`납품`만으로는 `OUTBOUND`로 분류하지 않고 `OTHER`를 사용한다. 같은 입력에 `출고`·`피킹`·`포장`이 명시되면 `OUTBOUND`를 사용한다.
- 등록할 일정에서 서로 다른 카테고리 후보가 함께 있어 하나로 정할 수 없으면 카테고리를 다시 질문한다.
- 서버가 확정한 최종 카테고리는 Schedule 응답의 `category`로 확인한다. Assistant 등록 성공 결과에는 `category`가 포함되지 않으며, 앱은 16.13에 따라 Schedule 목록을 다시 조회한다.

### 16.13 Schedule 목록 동기화

- Assistant create/update/delete 성공 뒤 앱은 현재 화면에 필요한 Schedule 목록을 기존 Schedule GET으로 다시 조회한다.
- 목록 조회만 실패하면 GET만 재시도하고 완료된 Assistant write를 다시 실행하지 않는다.
- candidate snapshot이나 모델 응답으로 Schedule 목록을 임의 재구성하지 않는다.

### 16.14 음성 Assistant 공개 계약

> 구현 경계: 백엔드 계약과 가짜 엔진 회귀 테스트는 Orange Pi의 Whisper, Piper 및 WAV 실기기 시험과 구분한다.
> 아래 실측은 2026-11-16 Orange Pi 시험 기록을 기준으로 한다.
> 이 절의 실측은 Orange Pi 음성 처리 시험이며 모바일 앱 통합시험 결과와 구분한다.

`POST /api/v1/voice/requests`는 raw body로 WAV 음성을 받는다. `requestId`는 필수 query이며
후속 답변이면 `conversationId`와 `questionId`를 함께 보낸다.

- `Content-Type: audio/wav`
- RIFF/WAVE, PCM S16LE, 16 kHz, mono, 16-bit
- 최대 15초, 최대 1,048,576 bytes
- 정상 신규 접수는 `202`이며 기존 Assistant 접수 필드에 인식된 `transcript`와 실제
  접수된 `input`을 함께 반환한다.
- 같은 `requestId`가 이미 있으면 body와 STT를 다시 처리하지 않고 기존 상태를 `200`으로
  반환한다. 앱은 응답 유실 때 GET-first 정책과 동일 ID 최대 1회 재전송을 유지한다.
- 활성 후보 질문은 하나의 유효한 번호만 selection으로 접수한다. 활성 승인 질문의
  명확한 긍정·부정만 confirmation으로 접수하며 불명확한 답은
  `422 VOICE_ANSWER_UNCLEAR`이다.
- 무음이나 빈 인식 결과를 임의의 일정 명령으로 바꾸지 않는다.

`GET /api/v1/voice/requests/{requestId}/speech`는 완료된 구조화 결과를 읽을 문장으로
변환해 `audio/wav`를 반환한다. 조회 결과는 제목·한국어 날짜/시각·카테고리를 읽고,
후보와 수정·삭제 확인은 선택지 또는 변경 대상을 말한다. 등록·수정·삭제 성공은 상세
필드가 없으므로 결과의 합의된 짧은 성공 문구를 사용한다.

STT와 TTS는 같은 비차단 단일 처리 슬롯을 사용한다. TTS 실패는 이미 완료된 Assistant
요청이나 Schedule write를 다시 실행하지 않는다. Voice 오류는 논리 요청 ID를 파싱한
뒤 발생한 경우 `assistantRequestId`에 앱의 `requestId`를 담는다.

#### 16.14.1 음성 POST 요청과 접수 응답

| 위치 | 이름 | 필수 | 타입·규칙 |
|---|---|---|---|
| query | `requestId` | 예 | 앱이 만든 canonical UUID v4. 같은 논리 요청은 같은 ID 유지 |
| query | `conversationId` | 후속 답변 시 | 이전 응답의 UUID v4 |
| query | `questionId` | 후속 답변 시 | 서버가 준 현재 활성 질문 UUID v4. `conversationId`와 함께 전송 |
| header | `Content-Type` | 예 | `audio/wav` |
| raw body | WAV 바이트 | 예 | RIFF/WAVE, PCM S16LE, 16 kHz, mono, 16-bit, 15초·1,048,576 bytes 이하 |

PCM fmt tag 1과 PCM EXTENSIBLE `0xFFFE`를 허용한다. EXTENSIBLE은 PCM subformat과 유효한 16-bit header여야 한다. 정확히 15초·1,048,576 bytes는 허용 경계이며, 초과하면 413이다. 빈 body와 유효한 무음은 422 `SPEECH_NOT_RECOGNIZED`다. 공개 endpoint는 m4a·aac·압축 WAV를 받지 않는다.

```bash
curl -i -X POST "$BASE_URL/api/v1/voice/requests?requestId=$REQUEST_ID" \
  -H 'Content-Type: audio/wav' --data-binary '@input.wav'
```

신규 접수는 202 JSON이며 다음 다섯 필드가 필수다. 아래는 실제 Pi 합성 WAV 시험의 응답이다.

```json
{
  "requestId": "7e71513f-be91-44b2-a0de-1d8f92845f0e",
  "conversationId": "63e919b4-d1e0-4b50-b6f5-fa7e0cef6d85",
  "status": "accepted",
  "transcript": "모레 입고 수량 대조 등록해줘.",
  "input": {"type": "text", "text": "모레 입고 수량 대조 등록해줘."}
}
```

`transcript`는 STT 결과이고 `input`은 실제 Assistant 접수 입력이다. 앱은 이를 다시 텍스트 Assistant POST로 보내지 않는다. `input.type`은 일반·정보 보충의 `text`, 후보 선택의 `selection`, 승인·거절의 `confirmation` 중 하나다. 후속 입력의 schema는 §16.2와 같다. `X-Transcript` 헤더는 URL 인코딩된 보조 값이며 응답 body를 우선한다.

후속 답변은 새 requestId와 기존 conversationId·현재 questionId를 사용한다.

```bash
curl -i -X POST \
  "$BASE_URL/api/v1/voice/requests?requestId=$NEW_REQUEST_ID&conversationId=$CONVERSATION_ID&questionId=$QUESTION_ID" \
  -H 'Content-Type: audio/wav' --data-binary '@answer.wav'
```

후보 선택은 `첫 번째`, `2번`, `두 번째 일정` 같은 완전한 단일 선택 표현을 받는다. 복수 번호, 범위 밖 번호, 다른 말 속의 부분 번호는 422이며 현재 질문을 유지한다. 승인 중 정정 요청도 승인으로 처리하지 않고 422를 반환한다. 거절 뒤 새 요청으로 정정한다.

#### 16.14.2 polling, replay와 TTS

상태는 `GET /api/v1/assistant/requests/{requestId}`로 조회한다. `accepted`·`processing`에는 result가 없고 `completed`에 §16.4의 구조화 결과가 있다. 직전 응답부터 2초 뒤 다시 조회하며 요청을 겹치지 않는다. 최초 POST 시도부터 자동 조회 한도는 120초다.

이미 접수된 ID를 같은 음성 POST에 쓰면 body와 STT를 다시 처리하지 않고 200을 반환한다. 200 body는 Assistant GET과 같으며 `transcript`, `input`이 없다. STT가 진행 중이어서 아직 Assistant에 접수되지 않은 같은 ID는 409 `VOICE_BUSY`다.

완료된 요청의 TTS는 다음과 같이 header와 WAV를 따로 받는다.

```bash
curl -sS -D response.headers \
  "$BASE_URL/api/v1/voice/requests/$REQUEST_ID/speech" -o response.wav
```

200 응답은 `audio/wav` 바이너리다. 입력 WAV 규격을 출력에 강제하지 말고 실제 RIFF/PCM header로 재생한다. 이번 Pi 출력은 22,050 Hz·mono·16-bit였다. TTS GET은 읽기 전용이며 Assistant나 Schedule write를 재실행하지 않는다. 별도 TTS cache를 전제하지 않는다.

현재 앱 실행에서 음성으로 시작한 요청과 후속 응답만 TTS를 자동 재생한다. 텍스트 Assistant, 수동 Schedule CRUD, 앱 재실행·서버 변경 후 GET-only로 복구한 결과는 자동 재생하지 않는다. 재생 실패 시 기존 화면 결과를 유지하고 일정 작업을 다시 요청하지 않는다.

#### 16.14.3 음성 오류와 복구

| HTTP | code | 처리 |
|---:|---|---|
| 422 | `AUDIO_UNSUPPORTED` | 지원하는 WAV로 다시 녹음 |
| 413 | `REQUEST_BODY_TOO_LARGE` | 길이·크기를 줄여 다시 녹음 |
| 422 | `SPEECH_NOT_RECOGNIZED` | 인식 실패. 일정 write 없음 |
| 503 | `VOICE_UNAVAILABLE` | 음성 기능·STT 사용 불가 또는 timeout. 텍스트 입력 유지 |
| 409 | `VOICE_BUSY` | 대기 후 같은 ID의 Assistant GET부터 재확인 |
| 409 | `STALE_QUESTION` | 현재 질문 재조회. 과거 답변 자동 재전송 금지 |
| 409 | `QUESTION_EXPIRED` | 만료 질문을 재사용하지 않고 새 요청 시작 |
| 422 | `VOICE_ANSWER_UNCLEAR` | 기존 질문 유지. 명확한 단일 답변 재요청 |
| 409 | `SPEECH_NOT_READY` | 결과 준비 중. 일정 작업 재실행 금지 |
| 503 | `TTS_UNAVAILABLE` | 화면 결과 유지. 일정 작업 재실행 금지 |
| 410 | `REQUEST_EXPIRED` | 상세 보관이 끝난 요청을 음성으로 재접수하지 않음 |
| 404 | `ASSISTANT_REQUEST_NOT_FOUND` | 아래 재전송 조건을 모두 충족할 때만 제한적으로 재전송 |

Problem body는 §13.1과 같으며 `requestId`는 HTTP 추적 ID, `assistantRequestId`는 앱의 논리 요청 ID다. 기존 Assistant 오류도 §13.3의 code를 유지한다.

응답 유실 시 먼저 같은 ID로 Assistant GET을 호출한다. 같은 앱 실행·같은 Base URL·최초 전송 후 120초 이내·같은 원본 녹음 보유·GET이 정확히 404 `ASSISTANT_REQUEST_NOT_FOUND`인 조건을 모두 충족할 때만 같은 ID와 같은 WAV를 최대 1회 재전송한다. 이후에는 GET polling만 한다. VOICE_BUSY일 때 새 ID를 만들거나 즉시 반복하지 않는다. 앱 재실행 또는 서버를 변경했다가 돌아온 뒤에는 과거 POST를 자동 재전송하지 않고 GET-only로 복구한다.

후보·승인 질문은 발급부터 10분이며 expiresAt을 우선한다. GET·replay는 수명을 연장하지 않는다. 정보 보충에는 일률 10분을 적용하지 않는다. 완료 결과의 최소 24시간 보관, 24시간 무활동 대화 만료와 서버 재시작 복구는 §16.8~16.9를 따른다.

#### 16.14.4 Pi 실측에 따른 운영 권장값

Microsoft Heami 합성 WAV(3초, 16 kHz mono PCM)를 실제 Pi에 전송한 10회 측정이다. Whisper small·CPU int8·beam 3·물류 hotwords, backend·RKLLM·STT/TTS 동시 실행 조건이며 사람 음성의 정확도 평가가 아니다. p95는 10개 원본값에 nearest-rank를 적용해 최댓값과 같다.

| 항목 | 평균 | p95·최대 |
|---|---:|---:|
| 음성 POST | 5.874초 | 6.102초 |
| TTS GET | 0.187초 | 0.225초 |
| POST부터 TTS 수신까지 | 34.323초 | 34.542초 |

2026-11-17 실제 사람 녹음 재시험에서는 최종 최소 hotwords 적용 이후의 정상 접수·TTS 요청10개를 집계했다. 서로 다른 길이의 발화와 후보 선택·거절·등록 등 결과 종류가 섞여 있어 위 합성 단일 파일10회와 같은 분포의 비교가 아니다. 격리 backend(동일 소스·venv)와 실제 NPU/Whisper/Piper를 사용했다.

| 항목 | 평균 | p95·최대 |
| --- | --- | --- |
| 실제 녹음 음성 POST | 5.966초 | 7.350초 |
| 실제 녹음 TTS GET | 0.749초 | 1.796초 |
| 실제 녹음 POST부터 TTS수신 | 23.124초 | 54.855초 |

실제 녹음의 상세 결과와 당시 남은 오류는 [Orange Pi 음성 연동 검증 기록 19장](orangepi-voice-validation-20261117.md)을 따른다. 17개 문장 전체가 정확히 해석 및 등록됐다는 의미가 아니다.

운영 권장은 음성 POST 35초, TTS GET 35초, VOICE_BUSY 재확인 2초다. 정상 실측에 더해 내부 음성 서버 timeout 30초의 503을 클라이언트가 받을 여유 5초를 둔 값이다. 최대 15초 음원의 모든 발화에 대한 지연 상한을 보장하지 않는다. 앱 적용 결과는 별도 통합시험 기록으로 관리한다. Assistant GET의 기존 2초 polling과 최초 시도부터 120초 자동 조회 한도는 유지한다.

## 17. 제외 범위와 향후 확장

다음 항목은 현재 MVP API 범위에 포함하지 않는다.

- 반복 일정, 종일 일정과 종료 시각
- 알림 기능 재도입과 구체적인 알림 시점
- 휴지통과 복원
- 다중 사용자, 인증과 사용자별 일정 소유권
- 제목 검색, 정렬 parameter와 pagination
- HTTPS, rate limiting과 외부 공개 production
- 변경분 동기화와 server push
- 다중 단말 동시 사용 정책
- 미해소 uncertain 요청의 장기 보관과 폐기 정책
- 재고·폐기·작업 기록 도메인
