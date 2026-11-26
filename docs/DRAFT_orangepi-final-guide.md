DRAFT / 미확정 / 연동 기준으로 사용 금지

# Orange Pi 배포·시험 결과

- 작성일·사전 시험 종료: 2026-11-17 KST (시험은 11/16~17)
- 작성: Codex, 실제 Orange Pi 원격 실행 근거
- 시험 당시 기준 HEAD: `080e6f2309a9ddd4622f8afc0d381b979f193c30` + 아래 기록한 미커밋 수정본
- 전달 묶음: `DRAFT_OrangePi_최종인계.zip`
- Git 기록 브랜치: `fix/orangepi-voice-deploy-validation-20261117`. 본문의 미커밋·080e6f2 표기는 시험을 수행한 당시 기준이며, 수정 소스는 이 브랜치의 커밋으로 보존한다.
- 전체 연동 완료본이 아니다. 17번 실패와 공동 시험을 해소한 뒤 PI_AI_FINAL/FINAL 여부를 판단한다.
- 초기 시험은 Microsoft Heami 합성 음성으로 수행했고, 2026-11-17 사용자가 제공한 **실제 사람 녹음17개로 재시험**했다. 최신 실제 녹음 결과는 §19, 기존 합성 결과는 §1~18에 보존한다.
- 실제 녹음 최신 요약: 핵심3시나리오 각2회 PASS, 후속 답변 PASS, 추가17번 모델 해석 FAIL(미해결),15번은 재질문. AI36 PASS. 최종 인계 확정·전체 연동 완료 상태가 아니다.

## 1. 최종 판정

최신 develop 기준 코드는 반영했고, 수정본을 실제 Pi에서 실행했다. Git HEAD는 origin/develop과 일치하지만 작업 트리가 변경되어 있으므로 최종 배포 commit 확정은 미완료다. 자동 시험은 backend **1515 PASS / 1 SKIP**, AI **34 PASS**다. SKIP은 별도 환경변수로 켜는 실제 모델 pytest smoke이며, 실제 NPU 호출은 아래 HTTP 시험에서 별도로 수행했다.

Pi의 기존 네트워크에서 공동 시험 준비는 가능하다. 전체 완료 판정은 보류한다. NET-01/02/03, API-01의 수동 조정·iPhone UI 부분, JOINT-01과17번 모델 해석 실패가 남았다. 네트워크 구성은 사용자 요청에서 제외됐다.

## 2. 배포 결과

| 항목 | 실제 값·근거 |
| --- | --- |
| 기존 코드 | ffad75bb2590ea506b834783933e3c46a70e8446 |
| 반영 기준 | 080e6f2309a9ddd4622f8afc0d381b979f193c30 + 미커밋 수정 |
| OS·하드웨어 | Ubuntu 22.04.5 / OrangePi 1.2.2 / Linux 6.1.99 / aarch64 / RK3588 / RAM 16,353,264 KiB |
| Python | backend·RKLLM·STT/TTS 모두 3.11.18; 시스템 Python은 변경하지 않음 |
| DB | SQLite integrity ok, alembic 20261017_0004=head; 운영 일정 5행 보존 |
| backend | schedule-backend.service active/enabled; 8000 |
| NPU | rkllm-npu.service active/enabled; 8080 |
| 음성 | voice-server.service active/enabled; 127.0.0.1:8081 |
| 설정 | backend/.env; SCHEDULE_VOICE_ENABLED=true; SCHEDULE_VOICE_SERVER_BASE_URL=http://127.0.0.1:8081; SCHEDULE_VOICE_SERVER_TIMEOUT_SECONDS=30; SCHEDULE_ASSISTANT_MODEL_TIMEOUT_SECONDS=110; LOG_FORMAT=json; TRUST_SERVER_TIME=false |
| systemd | /etc/systemd/system/의 위 3개 unit + voice-server.service.d/20-stt-model.conf |
| 재부팅 | 실제 boot ID 변경, 3개 enabled/active 및 health200 확인. NPU cold 준비는 프로세스 active보다 늦음 |
| 백업 | 별도 비공개 $BACKUP_DIR: 이전 코드 archive·환경·3개 unit·DB online backup. 20261116T131051Z |
| 보존 순서 주의 | 초기 git fast-forward는 백업보다 먼저 수행했다. ffad 코드 archive와 기존 DB/환경/unit을 실제 서비스 변경 전 보존했다. 배포 전 전체 선백업으로 소급해서 표시하지 않음 |
| rollback | 이전 코드·DB를 별도 경로로 복원한 격리 rehearsal health200/exit0/5행 해시 일치. 운영 DB를 과거 백업으로 덮어쓰는 작업은 하지 않음 |

설치·재배포·서비스 template 및 검증한 명령은 `ai/deploy/README.md`, `ai/voice_server/README.md`, `ai/rkllm_runtime/README.md`, `backend/README.md`를 따른다. 새 경로에 offline wheelhouse로 가상환경 3개를 설치해 pip check·실제 import를 통과했다. `redeploy.py --apply`는 현재 dirty develop에서 exit2로 거부하는 것을 확인했다. 최종 코드가 미커밋인 상태에서 이 스크립트의 전체 apply 성공으로 표시하지 않는다.

```bash
systemctl status schedule-backend.service rkllm-npu.service voice-server.service
sudo systemctl start schedule-backend.service rkllm-npu.service voice-server.service
sudo systemctl stop schedule-backend.service rkllm-npu.service voice-server.service
sudo systemctl restart schedule-backend.service rkllm-npu.service voice-server.service
journalctl -u schedule-backend.service -n 100 --no-pager
```

rollback rehearsal의 실제 실행은 별도 `$VERIFY_DIR/verify_rollback.py`에 보존했다. 이전 code archive를 새 경로에 추출하고 DB backup을 별도 DB로 복사한 뒤 기존 backend Python으로 loopback 8094 uvicorn을 구동해 검증했다. 운영 전환은 revision과 환경·unit을 함께 맞춘 후 health를 확인해야 하며, 현재 운영 DB는 복원된 원래 경로를 사용한다.

## 3. 네트워크·원격 접속

Windows에서 LAN SSH 및 Tailscale SSH 연결, 로그 조회와 sudo 서비스 재시작을 실제 수행했다. 실제 IP·SSH 사용자·접속 명령은 ZIP 밖 별도 파일로 전달한다. SSH 비밀번호·key·token은 기록하지 않았다.

키트 공유기 DHCP 예약·고정 IP, WAN/셀룰러/Tailscale 차단, 공유기 오프라인 재부팅, Mac 실제 접속, iPhone Settings, 물리 장비·Wi-Fi 전달은 NOT_RUN이다. 현 네트워크에서 Pi 재부팅 뒤 같은 LAN SSH 주소로 다시 접속한 사실만 확인했다. Tailscale 상태의 공용 계정 이름은 확인했으나 전체 계정 관리·새 Mac 등록 절차는 실행하지 않았다. 최종 앱은 내부 LAN의 backend 8000만 사용하며 실제 iPhone 확인은 11/18 대상이다.

## 4. 모델·음성 엔진

| 항목 | 실제 값 |
| --- | --- |
| RKLLM | qwen440-v37 / W8A8 / context4096 / new tokens1024 / NPU3 cores / CPU4~7 / runtime-toolkit1.2.1 driver0.9.8 |
| 양자화 산출물 | qwen440-v37-w8a8-opt0-rk3588.rkllm |
| 모델 bytes·SHA256 | 4844571892 / 012c5a256186c1c342d82442b7bb60766a7929c76511a4caca571b7f3a640a06 |
| prompt SHA256 | afc3dab1355cc9d75fe2e73d0bc9957b72da90d6fcfc4783718a00895949a262 |
| STT | faster-whisper1.2.1 / small / CPU int8 / beam3 / threads8 / temperature0 / VAD / condition_on_previous_text=false / 물류 hotwords / local_files_only |
| TTS | piper-tts1.8.0 / piper-ko-std piper_ko_female_v1.onnx / 22,050Hz mono16bit |
| TTS model SHA256 | 87761de9a1b1ae548d3b099304620f400e92c49e828befa172331b42e9c7ee2d |
| 오디오 | ctranslate2 4.9.0 / onnxruntime1.32.0 / soundfile0.14.0 / ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright (c) 2000-2021 the FFmpeg developers |
| NPU Python | 별도 ai/rkllm_runtime/.venv Python3.11.18 / Flask3.1.3; 외부 모델 wrapper는 수정하지 않음 |

STT cache·Piper/RKLLM 모델은 별도 설치 자료다. 모델 본체·DB·wheelhouse는 이 ZIP에 포함하지 않는다.

## 5. 로그

systemd journal을 사용한다. JSON `event=voice.stage`, `assistantRequestId`, `stage`, `status`, `durationMs`, `errorCode`로 추적한다. STT 성공에 transcriptLength/transcriptSha256을 기록하고 원문·모델 원출력은 기록하지 않는다. 수집한 voice.stage에 필수 ID/단계와 원문 필드 부재를 확인했다. 시각은 JSON UTC다.

```bash
journalctl -u schedule-backend.service --no-pager -o cat | grep voice.stage
journalctl -u schedule-backend.service --no-pager -o cat | grep "$REQUEST_ID"
```

원문 transcript는 운영 로그가 아닌 아래 고정 시험 결과에만 기재한다. 원본 로그·시험 DB와 상세 수집 JSON은 비공개 $VERIFY_DIR에 보존했다.

## 6. Pi 실측 권장값

| 설정 | 권장 | 근거 |
| --- | --- | --- |
| 음성 POST timeout | 35초 | 10회 평균5.874/최대6.102초; 내부30초 timeout의 오류 수신 여유5초 |
| TTS GET timeout | 35초 | 10회 평균0.187/최대0.225초; 내부30초 timeout 여유 |
| VOICE_BUSY | 2초 뒤 GET 우선 재확인 | 공통 음성 slot; 즉시 무한 POST 금지 |
| Assistant GET | 2초 / 최초시도부터 자동조회120초 유지 | NPU 포함 warm 종단 평균34.323초. 초기 부하 중86초 사례도 있어 POST 시간과 분리 |

모든 15초 발화의 상한을 측정한 결과가 아니다. 모호 발화의 반복 인식으로 POST 27.058초가 걸린 사례가 있으며 422로 안전 거절했다. 권장값을 iPhone에서 적용·확인하는 작업은 미실행이다.

## 7. 자료 전달표

| 자료 | 확정 여부 | Git 경로 | ZIP 내부 |
| --- | --- | --- | --- |
| 이 가이드 | DRAFT | docs/DRAFT_orangepi-final-guide.md | DRAFT_orangepi-final-guide.md |
| API 설명 | 미커밋 배포 기준 | docs/api.md | api.md |
| OpenAPI | 실제 서버 전체 원본; 미커밋 배포 기준 | docs/openapi.json | openapi.json |
| 배포 도구·환경 예시 | 실제 검증; 미커밋 | ai/deploy/ 및 각 README | Git 작업본에서 검토 |
| STT/TTS 샘플 | 합성 입력/실제 Pi 출력; 별도 전달 | Git 제외 | samples/ |

공동 시험 순서: 배포 SHA 확정→별도 접속 정보 수신→Settings IP 저장→health/time-sync→raw WAV202→GET2초 polling→현재 실행의 음성 요청만 speech재생→오프라인/재시작/오류 복구 확인.

## 8. 시험 결과 요약

| ID | 항목 | 상태 | 실제 결과·미실행 범위 | 근거 |
| --- | --- | --- | --- | --- |
| ENV-01 | 배포·실행 환경 | PASS | 기준 develop 080e6f2; 3개 Python 3.11.18; DB head; 수정본 미커밋 | §2/4; final-metadata.json |
| NET-01 | 오프라인 LAN·공유기 재부팅 | NOT_RUN | 사용자 요청의 네트워크 제외 범위; 실제 WAN/셀룰러 차단 및 공유기 미접근 | 11/18 장비 공동 시험 필요 |
| NET-02 | 사설 IP 유지 | NOT_RUN | 실제 Pi 재부팅 뒤 LAN 재접속은 성공; DHCP 예약/키트 공유기 고정 검증 미실행 | 네트워크 구성 제외 |
| NET-03 | iPhone Settings·서버 구분 | NOT_RUN | 실제 iPhone 앱 세션 없음 | 11/18 공동 시험 |
| BOOT-01 | 자동 시작·장애 복구 | PASS | 실제 Pi reboot; 3개 enabled/active; 음성 서비스 중단→503→복구 | reboot-result.json; service-recovery-result.json |
| API-01 | health·time-sync | NOT_RUN | Pi HTTP no-op/65초 차이 503/재시작 전후 상대 날짜 PASS; 수동 OS 조정·iPhone 동의 UI는 미실행 | §10 |
| API-02 | CRUD·V1~V6·DB 보존 | PASS | CRUD·6개 Assistant 흐름; DB head; 운영 5행 해시 불변 | §11; production-restored.json |
| API-03 | Assistant 취소·재시작 복구 | PASS | 완료 요청 재시작/재부팅 복구; 이미 완료 취소 판정; 질문·처리 중 요청 취소 | reboot-result.json; cancel-result.json |
| VOICE-01 | 한국어 음성 202 | PASS | 사용자 지시로 사람 녹음 대신 합성 WAV 실제 Pi 업로드; 사람 정확도는 미측정 | §9/13/15 |
| VOICE-02 | 무음·인식 실패 | PASS | 무음·빈 body 422 SPEECH_NOT_RECOGNIZED; 고정 성공 대체 없음 | audio-boundaries.json |
| VOICE-03 | WAV·15초·1 MiB | PASS | 11/11 경계 사례 PASS; 정확한 최대값 무음은 규격 통과 후 422 | §9 |
| REPLAY-01 | 동일 ID·GET-first | PASS | STT 중 409; 접수 뒤 GET; 완료 replay200 GET 형태; STT 1회 | §12 |
| ANSWER-01 | 후속 답변·승인·거절·만료 | PASS | 첫/두 번째·2번·네·아니요; 복수·범위외·모호·정정422; 만료409; DB 불변 | §12/15; fixture 만료 증거 |
| TTS-01 | WAV·read-only | PASS | 실제 Piper 22,050 Hz mono16bit; 반복 TTS로 Schedule write 없음 | §14 |
| TTS-02 | 종류별 TTS 문장 | PASS | 14종 실제 Pi WAV; backend fixture 포함, 청취 이해도 미평가 | §14 |
| TTS-03 | TTS 오류 | PASS | 준비 전409; 질문 stale409; tombstone410; 서비스 중단503 | service-fault-result.json; fixture 결과 |
| SLOT-01 | 공통 STT/TTS slot | PASS | STT 중 음성 POST·speech GET 모두409 VOICE_BUSY | §12 |
| CONC-01 | STT 중 API 응답 | PASS | health22.941ms / Schedule9.595ms / 미접수GET40412.402ms | §12 |
| PERF-01 | POST·TTS 각10회 | PASS | 동일 3초 합성 WAV 10회; 표 원본값 | §13 |
| PERF-02 | CPU·메모리·안정성 | PASS | 5초 표본; 동일 PID/NRestarts0; 수치와 측정 한계 기재 | §13 |
| JOINT-01 | 실제 iPhone 종단 간 | NOT_RUN | 실제 iPhone·녹음·재생·내부 LAN 공동 세션 없음 | 11/18 공동 시험 |
| LOG-01 | 추적·원문 미기록 | PASS | voice.stage logical ID·단계·시간·SHA/길이; 원문 필드 없음 | §5 |
| SCN-01 | 등록→수정→조회 2회 | PASS | 146.137 /146.279초; 모델4회씩; 최종 제목·일시·분류 일치 | §15 |
| SCN-02 | 후보→선택→삭제 2회 | PASS | 57.153 /57.067초; 모델1회씩; 둘째만 삭제 | §15 |
| SCN-03 | 짧은 등록 2회 | PASS | 74.359 /74.765초; 모델2회씩; INBOUND/14시 일치 | §15 |


## 9. 시험별 상세 근거

공통 조건: 실제 Orange Pi, 현재 LAN의 loopback backend API, 위 모델·환경, 합성 WAV. 일정 쓰기 시험은 격리 DB에서 수행하고 운영 5행은 보존했다. 각 요청의 고정 문장·ID·결과는 §11~16과 samples/README에 연결한다. 음성 시험 중의 실제 사람이 말하는 환경·정확도는 평가하지 않았다.

| WAV 경계 | requestId | bytes | 실제 HTTP/code | 판정 |
| --- | --- | --- | --- | --- |
| silence | 0fc98a6e-ebd5-42fe-bba9-4ee2ed67ba72 | 32044 | 422 SPEECH_NOT_RECOGNIZED | PASS |
| empty | 726ecfde-6d23-424e-ade4-0a44bc5c1c51 | 0 | 422 SPEECH_NOT_RECOGNIZED | PASS |
| wrong-content-type | 4e29d00e-9d37-4c13-8e04-e3a0e4bd2ead | 32044 | 422 AUDIO_UNSUPPORTED | PASS |
| stereo | 49428751-3bdf-4475-9910-894b9251dadc | 64044 | 422 AUDIO_UNSUPPORTED | PASS |
| sample-rate-22050 | 100d3f81-b243-4744-b35d-6966fc104b96 | 44144 | 422 AUDIO_UNSUPPORTED | PASS |
| malformed | 2c5c7583-9b36-4a90-adc1-c84b6378855e | 10 | 422 AUDIO_UNSUPPORTED | PASS |
| duration-over-15 | 803e1395-1bcc-411f-9797-093e23a3d255 | 480076 | 413 REQUEST_BODY_TOO_LARGE | PASS |
| duration-exactly-15-silence | 0e127065-c7c1-44d0-8b83-a58043c48b15 | 480044 | 422 SPEECH_NOT_RECOGNIZED | PASS |
| exactly-1MiB-silence | 2292b136-7668-4df7-aa94-4e87bd9d0a14 | 1048576 | 422 SPEECH_NOT_RECOGNIZED | PASS |
| over-1MiB | 3ca88ce2-6060-4a20-996d-605de8da7b79 | 1048577 | 413 REQUEST_BODY_TOO_LARGE | PASS |
| pcm-extensible-silence | 7e9fa478-96f5-40c0-aba3-9083ec5b8ce8 | 32068 | 422 SPEECH_NOT_RECOGNIZED | PASS |

정확히 15초·1MiB의 무음은 형식/크기 검사를 통과하고 인식 실패 422였다. 202 성공으로 표시하지 않는다. PCM_EXTENSIBLE도 같은 경로를 확인했다.

## 10. time-sync 상세

| 조건 | 실제 응답·RTT | 상대 날짜 처리 | 판정 |
| --- | --- | --- | --- |
| 60초 이내 | 200 adjusted=false; reboot 후27.286ms | 동기화 뒤 내일 조회 outcome/list, write0 | PASS |
| 65초 차이, 자동 OS 조정 비활성 | 503 TIME_SYNC_UNAVAILABLE /10.907ms | 시계 조정 없음; 신뢰 revoke | PASS |
| 재시작·재부팅 후 sync전 | GET health200 | 내일 조회 need_more_info: 연월일·시간 요구 | PASS |
| 재부팅 뒤 재동기화 | 200 adjusted=false | 내일 조회 정상, 원 DB5행 해시 불변 | PASS |
| 실제 수동 OS 시각 조정·iPhone 동의 UI | 미실행 | 물리 단말·OS 조정 공동 확인 필요 | NOT_RUN |

실제로 OS 시계를 틀어 놓거나 NTP를 끄지 않았다. 가짜 adapter로 adjusted=true 경로를 검증한 자동 테스트를 실제 OS 변경 시험으로 대신 표시하지 않는다.

## 11. 텍스트 Assistant V1~V6

| ID | requestId | kind/outcome 순서 | 검증 |
| --- | --- | --- | --- |
| V1 | acb1ec6e-96f6-4c4b-a5a0-80864d2e4d6e | outcome/success | 12/14 16시 INVENTORY 미완료1건 등록 PASS |
| V2 | d3859289-3b05-45e4-b481-fce4ef8b43e9 | outcome/success | 내일 INBOUND 조회1건/DB불변 PASS |
| V3 | 45a2dabf-0a4f-43d8-9811-1bb33a0980fb<br>c336488b-d8e4-414f-bd08-5eff31d00476 | confirmation → outcome/success | 승인 뒤 제목만 변경 PASS |
| V4 | a69433c3-8212-4b3a-a75b-0c4e7521abdc<br>172bcca3-4d68-44e3-9a78-47b742f6ff66 | confirmation → outcome/success | 승인 뒤 completed만 true PASS |
| V5 | 9d3b04e7-67d8-489b-85a5-38fe32a3752f<br>ef529bfd-5143-4508-97c5-837887732368<br>9e434f45-8d37-40b0-b83e-fc9b8be2ceca | candidate_selection → confirmation → outcome/success | 둘째후보만 soft delete PASS |
| V6 | a9d7519b-4c26-45fa-9796-04dde96290a2<br>11e69f1d-f30e-46b0-a5fb-5912b24203d3 | need_more_info → outcome/success | 모레14시 INBOUND1건 PASS |


## 12. replay·후속 답변

replay 검증 logical requestId `b5cf5919-43a2-49e2-bd80-fdbf7810c5cf`. STT 중 동일 ID409 VOICE_BUSY, TTS도409 VOICE_BUSY, 완료 전 speech409 SPEECH_NOT_READY, 접수 뒤 GET으로 결과 복구, 완료 뒤 동일 WAV/ID POST는200 GET 형식이며 transcript/input이 없다. 이 ID의 stt_completed 로그는 1개다.

GET-first: 같은 앱 실행·같은 Base URL·120초 이내에만 기존 ID를 GET하고,404일 때 원본 WAV를 동일 ID로 최대1회 재전송한다. 앱 재실행·서버 변경은 GET-only, 이전 결과 TTS 자동재생 금지다. Pi HTTP 복구는 검증했으나 앱 동작 자체는 JOINT-01 대상이다.

| 답변 | requestId | HTTP/code | DB·질문 |
| --- | --- | --- | --- |
| ambiguous.wav | bb4513d2-97f1-4261-b5c6-21236126bc18 | 422 VOICE_ANSWER_UNCLEAR | write0, 유효 질문 유지; 다음 정상 답변 가능 |
| outofrange.wav | ed44562b-15b3-480f-957e-0d122aeaaa8e | 422 VOICE_ANSWER_UNCLEAR | write0, 유효 질문 유지; 다음 정상 답변 가능 |
| vague.wav | 9ca86593-3967-4a2f-be0c-6d2a6f52c0a1 | 422 VOICE_ANSWER_UNCLEAR | write0, 유효 질문 유지; 다음 정상 답변 가능 |
| correction.wav | f72482d7-5e00-4864-be9b-687a25d226c0 | 422 VOICE_ANSWER_UNCLEAR | write0, 유효 질문 유지; 다음 정상 답변 가능 |

첫 번째, 두 번째 일정, 2번 선택과 네 승인, 아니요 거절도 실제 음성 입력으로 통과했다. QUESTION_EXPIRED/STALE_QUESTION/REQUEST_EXPIRED는 별도 backend fixture에서 실제 HTTP로 검증했으며 미리 준비한 상태임을 구분한다.

질문 취소 request 8cde5c29-9790-4bcf-82a3-ddba45d04183, 처리 중 요청 취소 0d25141b-912a-4a54-a2f4-a5098c8f5d8c 모두 HTTP200 targetState=cancelled, GET outcome=cancelled/schedulesChanged=false로 확인했다.

## 13. 성능 10회 실측

2026-11-16 14:27:01~14:32:45 UTC / 23:27~23:32 KST. register.wav(3.000초, 96,046 bytes), SHA b42c98ff3bd1a7d18429b9be93e83b897c67d458a3e792168b5c8f355d8bd23a. 전부 합성 입력이며 동일 파일 반복이다. NPU·backend·음성3서비스 동시 실행. STT는 실제 전사 호출 단계, RKLLM은 assistant.model_call, POST와 TTS는 HTTP roundtrip, 종단은 업로드부터 polling완료·TTS수신까지다.

| 회차 | requestId | STT ms | RKLLM ms | POST ms | TTS ms | 종단 ms | 결과 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | bd2a182c-bcfc-4a9b-9c1f-53bbc9276adb | 5699.87 | 26956 | 5753.994 | 188.181 | 34220.837 | 202 → need_more_info → WAV200 |
| 2 | 90e4ff46-9e78-4ea7-b994-e120efb8c162 | 5795.029 | 26984 | 5841.38 | 178.421 | 34283.702 | 202 → need_more_info → WAV200 |
| 3 | 2e7def9f-20c6-479a-8a45-a814e1cab6b8 | 5874.569 | 26965 | 5917.73 | 192.945 | 34362.528 | 202 → need_more_info → WAV200 |
| 4 | d13d8bef-7015-4cbd-a739-7ffcda3434e8 | 5820.95 | 26919 | 5860.842 | 224.511 | 34348.352 | 202 → need_more_info → WAV200 |
| 5 | 8c59830d-685b-46e2-8e07-4fbf25d32590 | 6062.703 | 26881 | 6102.455 | 176.094 | 34541.583 | 202 → need_more_info → WAV200 |
| 6 | 2f24dfd2-ce62-4d9b-b663-602e87d318a2 | 5749.85 | 26909 | 5791.648 | 180.742 | 34248.538 | 202 → need_more_info → WAV200 |
| 7 | 5025c11c-4a34-464c-a36c-90346267eff4 | 5848.174 | 26877 | 5882.302 | 179.757 | 34318.157 | 202 → need_more_info → WAV200 |
| 8 | c819823b-0219-4c0b-adc0-03e9c59ec02f | 5794.549 | 26906 | 5833.237 | 178.227 | 34266.753 | 202 → need_more_info → WAV200 |
| 9 | 9afa2813-5640-4d53-8bc8-ad6596b8008e | 5822.51 | 26886 | 5882.094 | 189.939 | 34325.226 | 202 → need_more_info → WAV200 |
| 10 | 84cc471b-eb7c-497b-a67f-bda401eaa185 | 5839.957 | 26918 | 5870.861 | 183.104 | 34314.282 | 202 → need_more_info → WAV200 |

| 측정 | min ms | 평균 ms | p95 ms | max ms |
| --- | --- | --- | --- | --- |
| post_ms | 5753.994 | 5873.654 | 6102.455 | 6102.455 |
| tts_get_ms | 176.094 | 187.192 | 224.511 | 224.511 |
| completion_ms | 34030.981 | 34134.418 | 34363.951 | 34363.951 |
| e2e_ms | 34220.837 | 34322.996 | 34541.583 | 34541.583 |

p95=nearest-rank, n10이므로 max와 같다.

| 프로세스 | 시작 CPU%/RSS MiB | 처리 중 최대 CPU%/RSS MiB | 종료 CPU%/RSS MiB |
| --- | --- | --- | --- |
| backend | 2 / 83.4 | 2 / 84.1 | 1.4 / 84.1 |
| RKLLM | 11.3 / 4639.7 | 48.9 / 4639.7 | 48.9 / 4639.7 |
| STT/TTS | 27.3 / 1341.4 | 45.9 / 1411.5 | 45.2 / 1375.3 |

CPU%는 ps가 반환한 프로세스 수명 평균의 5초 표본으로 순간 CPU 피크가 아니다. NPU/CPU child 또는 커널 메모리를 모두 개별 프로세스 RSS로 설명할 수는 없다. MemAvailable 시작 9397.0MiB, 표본 최소 9318.2MiB, 종료 9363.8MiB. 온도 시작 41.615°C, 최대 63.769°C, 종료 61°C. 세 PID와 NRestarts=0이 측정 구간에서 유지됐다. 이후 의도적인 장애주입·재시작·reboot는 이 구간과 분리한다.

최종 파일시스템 수치(시험용 wheelhouse·새 venv 포함):
```text
Filesystem       1B-blocks        Used   Available Use% Mounted on
/dev/mmcblk1p1 30990680064 15658725376 14969217024  52% /
```

## 14. TTS 문장·샘플

다음은 결과 종류별 backend fixture를 별도 DB·8092 프로세스에서 구성한 뒤 실제 Pi Piper로 만든 WAV다. 모든 Schedule row 수는0으로 불변이었다. 후보·확인·성공/취소·실패·uncertain 문장을 생성했으며 uncertain은 실제 NPU write불명확 사고를 재현했다는 의미가 아니다. 완료 상태 표현은 update-confirmation의 completed=true에서 확인했다.

| 종류 | requestId | 실제 생성 문장 | WAV | 생성 ms |
| --- | --- | --- | --- | --- |
| TTS-lookup-0 | df38f9d4-0a04-4cb2-ab98-98388bad340d | 조건에 맞는 일정을 찾지 못했어요. | samples/tts-output/fixture-lookup-0.wav | 322.577 |
| TTS-lookup-1 | d3be2645-c369-4d07-8d95-8bcf54b54f25 | 요청한 일정은 '고정 시험 일정 0', 11월 18일 오후 2시, 입고 일정입니다. | samples/tts-output/fixture-lookup-1.wav | 619.988 |
| TTS-lookup-2 | 1251e314-aab2-42a2-bf68-de10d198151d | 요청한 일정은 총 2건입니다. 첫 번째는 '고정 시험 일정 0', 11월 18일 오후 2시, 입고 일정입니다. 두 번째는 '고정 시험 일정 1', 11월 18일 오후 2시, 입고 일정입니다. | samples/tts-output/fixture-lookup-2.wav | 1411.387 |
| TTS-lookup-4 | b89cedef-c4b0-4bcd-b78d-873a9b7d88f0 | 요청한 일정은 총 4건입니다. 먼저 세 건을 안내할게요. 첫 번째는 '고정 시험 일정 0', 11월 18일 오후 2시, 입고 일정입니다. 두 번째는 '고정 시험 일정 1', 11월 18일 오후 2시, 입고 일정입니다. 세 번째는 '고정 시험 일정 2', 11월 18일 오후 2시, 입고 일정입니다. 나머지는 화면에서 확인하거나 조회 조건을 좁혀 주세요. | samples/tts-output/fixture-lookup-4.wav | 2638.419 |
| TTS-candidates | 9d408b37-02e7-492b-88d2-94ab5582ac78 | 일정 후보는 총 2건입니다. 첫 번째는 '고정 시험 일정 1', 11월 18일 오후 2시, 입고 일정입니다. 두 번째는 '고정 시험 일정 2', 11월 18일 오후 2시, 입고 일정입니다. 몇 번째 일정을 선택하시겠어요? | samples/tts-output/fixture-candidates.wav | 1648.237 |
| TTS-update-confirmation | 3b8f0e0f-35a8-4890-9f04-845d456dd3e4 | '고정 시험 일정 1' 일정의 제목을 '변경 후 고정 제목'로, 시간을 11월 19일 오후 3시로, 카테고리를 출고로, 완료 상태로 변경할까요? | samples/tts-output/fixture-update-confirmation.wav | 1056.694 |
| TTS-delete-confirmation | ee74a246-2023-47b2-b694-266812b01d65 | '고정 시험 일정 1', 11월 18일 오후 2시, 입고, 미완료 일정을 삭제할까요? | samples/tts-output/fixture-delete-confirmation.wav | 642.704 |
| TTS-success-create | e36783f6-479e-48ff-aeb9-80c352663b54 | 일정을 등록했어요. | samples/tts-output/fixture-success-create.wav | 206.055 |
| TTS-success-update | 628bc1b7-b82c-41e0-91e7-90ad8ee3435c | 일정을 변경했어요. | samples/tts-output/fixture-success-update.wav | 194.631 |
| TTS-success-delete | 2a739bf8-b4d4-4d19-8e7b-c7ddb47e9530 | 일정을 삭제했어요. | samples/tts-output/fixture-success-delete.wav | 186.04 |
| TTS-need-more-info | ed7eaf06-e40f-42c7-b13a-e6634b375c67 | 몇 시로 할까요? | samples/tts-output/fixture-need-more-info.wav | 167.823 |
| TTS-uncertain | 3620b532-5f44-41ef-8198-ce90be78fa80 | 일정 처리 결과를 확인하지 못했어요. 화면에서 현재 일정을 확인해 주세요. | samples/tts-output/fixture-uncertain.wav | 567.497 |
| TTS-failure | c71faf89-a3b5-415c-8b8c-cd61b458a550 | 입력을 이해하지 못했어요. 내용을 확인해 다시 말씀해 주세요. | samples/tts-output/fixture-failure.wav | 478.473 |
| TTS-cancelled | 19094540-f074-4292-976c-99e08d6c8576 | 요청한 변경을 취소했어요. | samples/tts-output/fixture-cancelled.wav | 244.652 |

실제 Assistant 시나리오에서 생성한 추가4개 WAV도 samples/README에 연결했다. 출력 header는 전부22,050Hz mono16bit이며 입력16kHz로 가정해 재생하면 안 된다. 텍스트 생성과 header/read-only는 PASS, 실제 iPhone 청취·재생 품질은 NOT_RUN이다.

## 15. 발표 시나리오

| 시나리오 | 회차 | transcript 순서 | 모델 호출 | 최종 DB | 전체 초 |
| --- | --- | --- | --- | --- | --- |
| SCN-01 | 1 | 모레 입고 수량 대조 등록해줘. → 오후 2시로 잡아 줘. → 입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘. → 네. → 입고 수량 최종 대조 일정 알려 줘. | 4 | 입고 수량 최종 대조 / 2026-11-18T05:00:00Z / INBOUND / completed=false | 146.137 |
| SCN-01 | 2 | 모레 입고 수량 대조 등록해줘. → 오후 2시로 잡아 줘. → 입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘. → 네. → 입고 수량 최종 대조 일정 알려 줘. | 4 | 입고 수량 최종 대조 / 2026-11-18T05:00:00Z / INBOUND / completed=false | 146.279 |
| SCN-02 | 1 | 내일 세 시 입고 납품서 대조 일정 삭제해 줘. → 2번. → 네. | 1 | 입고 납품서 대조 / 2026-11-17T18:00:00Z / INBOUND / completed=false | 57.153 |
| SCN-02 | 2 | 내일 세 시 입고 납품서 대조 일정 삭제해 줘. → 2번. → 네. | 1 | 입고 납품서 대조 / 2026-11-17T18:00:00Z / INBOUND / completed=false | 57.067 |
| SCN-03 | 1 | 모레 입고 수량 대조 등록해줘. → 오후 2시로 잡아 줘. | 2 | 입고 수량 대조 / 2026-11-18T05:00:00Z / INBOUND / completed=false | 74.359 |
| SCN-03 | 2 | 모레 입고 수량 대조 등록해줘. → 오후 2시로 잡아 줘. | 2 | 입고 수량 대조 / 2026-11-18T05:00:00Z / INBOUND / completed=false | 74.765 |

주 시나리오 추천은 SCN-02(약57초, 모델1회, 후보·승인·삭제 시연), 예비는 SCN-03(약75초, 등록). 전체 등록·수정·조회 설명이 필요하면 SCN-01을 선택하되 약146초를 확보한다. 최종 선택은 발표 담당자가 한다. 모두 합성 음성 조건의 추천이다.

### SCN-01 / 1회

| requestId | conversationId / questionId | transcript | Assistant input | kind/outcome | STT/RKLLM/TTS ms | 종단 ms |
| --- | --- | --- | --- | --- | --- | --- |
| 79aeeb0e-3354-4fef-8904-e5bb9c97c69e | b25e5a49-cbc9-4506-97f6-28c6470d7838 / 27ae3acd-7833-4d40-9761-388b33dd120b | 모레 입고 수량 대조 등록해줘. | {"type":"text","text":"모레 입고 수량 대조 등록해줘."} | need_more_info/ | 5978.869 / 26983 / 212.32 | 34449.988 |
| d559f3a3-aad7-4383-a5fb-5f74e7708436 | b25e5a49-cbc9-4506-97f6-28c6470d7838 / 없음 | 오후 2시로 잡아 줘. | {"type":"text","text":"오후 2시로 잡아 줘.","questionId":"27ae3acd-7833-4d40-9761-388b33dd120b"} | outcome/success | 5601.848 / 32384 / 254.62 | 40222.235 |
| 221738a5-7ca4-4b83-83af-ea4499e35836 | c595219f-1b81-4418-9d1d-8ec6043317ce / 1259264f-e99b-487d-a4ca-7fe3010cf0ef | 입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘. | {"type":"text","text":"입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘."} | confirmation/ | 6826.92 / 28174 / 499.155 | 37629.919 |
| 25b3c06d-197a-44c1-9694-0217b394b3d0 | c595219f-1b81-4418-9d1d-8ec6043317ce / 없음 | 네. | {"type":"confirmation","questionId":"1259264f-e99b-487d-a4ca-7fe3010cf0ef","approved":true} | outcome/success | 4914.157 / 0(모델 미호출) / 201.677 | 7239.352 |
| 749e3cc3-be1e-40cd-ad02-a9e2af8be5fc | a185f337-5885-43eb-ae11-7568195fc742 / 없음 | 입고 수량 최종 대조 일정 알려 줘. | {"type":"text","text":"입고 수량 최종 대조 일정 알려 줘."} | outcome/success | 5657.062 / 18426 / 676.319 | 26555.291 |

### SCN-01 / 2회

| requestId | conversationId / questionId | transcript | Assistant input | kind/outcome | STT/RKLLM/TTS ms | 종단 ms |
| --- | --- | --- | --- | --- | --- | --- |
| 943e46b6-e2fd-4f77-bf14-a0e64b9580a0 | c97490d6-ef54-4d37-8a15-da63267675b8 / e926e189-020d-4720-879a-a14501d7f1c6 | 모레 입고 수량 대조 등록해줘. | {"type":"text","text":"모레 입고 수량 대조 등록해줘."} | need_more_info/ | 5831.768 / 26928 / 176.832 | 34295.78 |
| ae9feee3-394b-4254-88b8-69430cc42fef | c97490d6-ef54-4d37-8a15-da63267675b8 / 없음 | 오후 2시로 잡아 줘. | {"type":"text","text":"오후 2시로 잡아 줘.","questionId":"e926e189-020d-4720-879a-a14501d7f1c6"} | outcome/success | 5602.432 / 32285 / 206.238 | 40185.886 |
| 47992522-d0e0-41de-bed7-52b188bf0060 | 79b03b75-0263-4837-a9f5-d9f61be2c8d6 / 3d063ef0-54da-4bb3-ada1-4e2d22dba3b0 | 입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘. | {"type":"text","text":"입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘."} | confirmation/ | 6732.61 / 28191 / 630.018 | 37707.769 |
| 122ae16d-de63-48fb-a9da-a2a2f1b4d1a9 | 79b03b75-0263-4837-a9f5-d9f61be2c8d6 / 없음 | 네. | {"type":"confirmation","questionId":"3d063ef0-54da-4bb3-ada1-4e2d22dba3b0","approved":true} | outcome/success | 4892.459 / 0(모델 미호출) / 214.446 | 7196.642 |
| edd39f5c-655e-4f02-9ada-0ed2d9a46616 | 889cd956-022e-40d8-aca5-ae7a551dc2fe / 없음 | 입고 수량 최종 대조 일정 알려 줘. | {"type":"text","text":"입고 수량 최종 대조 일정 알려 줘."} | outcome/success | 5928.233 / 18397 / 664.106 | 26837.586 |

### SCN-02 / 1회

| requestId | conversationId / questionId | transcript | Assistant input | kind/outcome | STT/RKLLM/TTS ms | 종단 ms |
| --- | --- | --- | --- | --- | --- | --- |
| 7123c5a4-0620-4545-847f-b4212af05437 | 6cee6672-e428-458a-bb5d-3cffeb53c149 / 4c3539cd-1810-4fd8-86ad-350911a2c401 | 내일 세 시 입고 납품서 대조 일정 삭제해 줘. | {"type":"text","text":"내일 세 시 입고 납품서 대조 일정 삭제해 줘."} | candidate_selection/ | 6285.143 / 32911 / 1756.096 | 42393.83 |
| b14eab38-a4b3-4a11-a113-85f398057f3d | 6cee6672-e428-458a-bb5d-3cffeb53c149 / d8978025-b122-49db-9f0f-c8e50f2577bc | 2번. | {"type":"selection","questionId":"4c3539cd-1810-4fd8-86ad-350911a2c401","candidateId":"fe93be6e-fae8-4f68-8636-a22b811d1b71"} | confirmation/ | 4712.954 / 0(모델 미호출) / 738.264 | 7567.149 |
| 6acdd474-045e-458e-8b89-cabccc7f6b45 | 6cee6672-e428-458a-bb5d-3cffeb53c149 / 없음 | 네. | {"type":"confirmation","questionId":"d8978025-b122-49db-9f0f-c8e50f2577bc","approved":true} | outcome/success | 4769.334 / 0(모델 미호출) / 213.125 | 7097.781 |

### SCN-02 / 2회

| requestId | conversationId / questionId | transcript | Assistant input | kind/outcome | STT/RKLLM/TTS ms | 종단 ms |
| --- | --- | --- | --- | --- | --- | --- |
| 1ed8d0b3-d3f9-45e8-8205-af194279c833 | 00692489-254b-4e1e-8b92-588ac86faab9 / 2f78b7a1-1352-4c2b-bac0-fe9882a0b191 | 내일 세 시 입고 납품서 대조 일정 삭제해 줘. | {"type":"text","text":"내일 세 시 입고 납품서 대조 일정 삭제해 줘."} | candidate_selection/ | 6106.808 / 32639 / 1769.44 | 42212.598 |
| 2bdaeece-1b4c-4e1f-b9d9-ffa931889fa1 | 00692489-254b-4e1e-8b92-588ac86faab9 / e0efe10c-b3fc-4e6c-a7d9-4b43a66c9b02 | 2번. | {"type":"selection","questionId":"2f78b7a1-1352-4c2b-bac0-fe9882a0b191","candidateId":"e3789eb4-79f0-4c57-a520-2ae4ddd9d857"} | confirmation/ | 4881.78 / 0(모델 미호출) / 688.739 | 7682.721 |
| 9e2f4749-1a3c-467f-a9bc-51f8d216e2a3 | 00692489-254b-4e1e-8b92-588ac86faab9 / 없음 | 네. | {"type":"confirmation","questionId":"e0efe10c-b3fc-4e6c-a7d9-4b43a66c9b02","approved":true} | outcome/success | 4734.218 / 0(모델 미호출) / 209.337 | 7062.15 |

### SCN-03 / 1회

| requestId | conversationId / questionId | transcript | Assistant input | kind/outcome | STT/RKLLM/TTS ms | 종단 ms |
| --- | --- | --- | --- | --- | --- | --- |
| 580af696-7141-4bf9-aa8a-3564e8a370e4 | 3504f604-bf8d-4546-8f47-3bcad328bb85 / e41642e8-7d3e-45c4-85c3-2929a4567134 | 모레 입고 수량 대조 등록해줘. | {"type":"text","text":"모레 입고 수량 대조 등록해줘."} | need_more_info/ | 5782.855 / 26914 / 174.846 | 34233.121 |
| 8157b8ea-b490-4f17-a387-41099c8018f4 | 3504f604-bf8d-4546-8f47-3bcad328bb85 / 없음 | 오후 2시로 잡아 줘. | {"type":"text","text":"오후 2시로 잡아 줘.","questionId":"e41642e8-7d3e-45c4-85c3-2929a4567134"} | outcome/success | 5420.155 / 32632 / 302.598 | 40102.691 |

### SCN-03 / 2회

| requestId | conversationId / questionId | transcript | Assistant input | kind/outcome | STT/RKLLM/TTS ms | 종단 ms |
| --- | --- | --- | --- | --- | --- | --- |
| e5b2c590-0628-4e07-8227-8b1b147e4978 | 4924d310-9d20-4521-a076-4e13b0d65a13 / 095d35b3-118d-477e-b472-45292596a7e0 | 모레 입고 수량 대조 등록해줘. | {"type":"text","text":"모레 입고 수량 대조 등록해줘."} | need_more_info/ | 5982.052 / 26924 / 193.049 | 34470.849 |
| be368d54-a86e-4253-abf6-5e4891f272bf | 4924d310-9d20-4521-a076-4e13b0d65a13 / 없음 | 오후 2시로 잡아 줘. | {"type":"text","text":"오후 2시로 잡아 줘.","questionId":"095d35b3-118d-477e-b472-45292596a7e0"} | outcome/success | 5656.812 / 32307 / 261.778 | 40272.003 |


## 16. 실제 정상·오류 예시

공개 endpoint 전체의 query/body/schema·오류·제한·복구는 api.md와 실제 서버의 전체 openapi.json을 함께 본다. OpenAPI는 40,709 bytes,9개 공개 path, info/paths/components, 필수 audio/wav body,202 필수5필드, speech200 audio/wav를 확인했다. 원본 SHA256: 229b61fb5a62146792bb3898823fac485a443b565f681f98a26565c12adfbdea.

실제 신규202:
```json
{
  "requestId": "bd2a182c-bcfc-4a9b-9c1f-53bbc9276adb",
  "conversationId": "eb72b565-82d5-4230-8a70-940ef9847c99",
  "status": "accepted",
  "transcript": "모레 입고 수량 대조 등록해줘.",
  "input": {
    "type": "text",
    "text": "모레 입고 수량 대조 등록해줘."
  }
}
```

실제 완료replay200:
```json
{
  "requestId": "b5cf5919-43a2-49e2-bd80-fdbf7810c5cf",
  "conversationId": "aa270791-ddb4-46e3-aa56-f367ab5e8660",
  "status": "completed",
  "result": {
    "kind": "need_more_info",
    "text": "몇 시로 할까요?",
    "questionId": "ce2843db-5785-4a54-89f1-0a91430cdf4d"
  }
}
```

실제 busy Problem:
```json
{
  "type": "https://example.invalid/problems/voice-busy",
  "title": "Voice pipeline busy",
  "status": 409,
  "detail": "Another voice request is currently being processed.",
  "instance": "/api/v1/voice/requests",
  "code": "VOICE_BUSY",
  "requestId": "d2eabe21-8ab8-4ea6-8d4c-4b21aa9e3550",
  "assistantRequestId": "b5cf5919-43a2-49e2-bd80-fdbf7810c5cf"
}
```

음성 서비스 중단 시 speech503 TTS_UNAVAILABLE, 새 음성POST503 VOICE_UNAVAILABLE, backend health200·일정불변을 확인했다. 재시작 뒤 기존 TTS와 새 음성202가 정상 복구됐다. 오류422/413의 개별 실제코드·ID는 §9, 불명확422는 §12, 만료410/질문409는 fixture 증거에 보존했다.

```bash
curl -fsS "$BASE_URL/health"
curl -i -X POST "$BASE_URL/api/v1/voice/requests?requestId=$REQUEST_ID" -H "Content-Type: audio/wav" --data-binary @samples/stt-input/register.wav
curl -fsS "$BASE_URL/api/v1/assistant/requests/$REQUEST_ID"
curl -fsS "$BASE_URL/api/v1/voice/requests/$REQUEST_ID/speech" -o speech.wav
```

## 17. 실패·수정·재시험

| 최초 실패 | 수정 | 재시험 |
| --- | --- | --- |
| 한국어 물류 명사·수정 발화 오인식 | small int8/beam3/고정 decoding/물류 hotwords; 애매한 답변은 전체 문장 match로 거절 | 동일 원본 WAV SCN-01/03 각2회 PASS |
| 복수 번호의 일부만 인식해 후보를 고르는 위험 | 후보 번호 parser fullmatch; 복수·정정·범위외 문장 거절 | ANSWER-01 실제 WAV422와 질문/DB보존 PASS |
| 정확한 세 시 인식 후 삭제 no_match | backend adapter가 엄격한 단일 삭제 원문에서 기존 title에 합쳐진 고유어 시간만 분리. v37 출처38규칙·prompt·모델 불변 | SCN-02 동일 WAV2회 PASS; 회귀4개와 전체1515 PASS |
| 긴 초기 추론 MODEL_TIMEOUT | 배포 model timeout60→110초, 앱 자동조회120초 유지 | 텍스트 V1~V6 전체 재시험 PASS |
| NPU Python symlink가 system Python으로 resolve되어 Flask없음 | installer가 가상환경 symlink 경로 유지; 별도 Python3.11 NPU venv | 34AI시험 및 실제 unit재설치·reboot PASS |
| 미실행 항목 | 네트워크/iPhone·수동시계 조정/사람녹음/최종SHA·업로드 | NOT_RUN 또는 미확정; 최종 완료로 표시하지 않음 |

이번 변경: STT·로그·후보답변 parser·native-clock adapter·OpenAPI speech media·배포 재현 도구·문서. 공개 API와 DB schema를 새로 바꾸거나 frozen dataset/규칙을 수정하지 않았다.

운영5행 전체 행 해시(백업/시험전/시험후 동일): `7e4b86e732ac873d146c1e8d0688845c3a9ffa261fd2eb6d8a28214601a880b7`. 원 DB 내용은 공유하지 않는다.

### 합성 시험 당시 실행 수정본의 파일 SHA256

최종 commit이 없으므로 아래 해시로 실행한 소스를 식별한다.

| 상대 경로 | SHA256 |
| --- | --- |
| ai/deploy/bootstrap_runtime.py | 7421ae88e9b1601ac0353e35619db0fec636f89b539168396745a978fc467874 |
| ai/deploy/install_services.py | 42d903f2db012bc5f70aa01264c6f962827dacd5dc6bcff89f00c8216e725d96 |
| ai/deploy/redeploy.py | 1e119f34eac346e4fb4d76232656234c333569b85acd674e1227cc4c167ca141 |
| ai/rkllm_runtime/requirements.txt | d17e846cde1ec32c0f8a3c53b7bb58eb5cbc16f70ae442da40bda7d41d677b50 |
| ai/tests/test_deploy.py | 3561aa3cb21ce3ea5737fa42e0c8b0c34e7f6bc47931ef4ce1d60b01ee708955 |
| ai/tests/test_voice_server.py | 45f947d19aba7a2686dba45e66f322b9fd628c6464bae10488a55a0a6b984b82 |
| ai/voice_server/requirements.lock | b82bd544815f9b8780710354ea1ce9283fa19dc7092495cdbee5158f989a4d15 |
| ai/voice_server/requirements.txt | 1678880ef03ba17cf8ab7a0438c313f7617ebb14357d926bdd5a385ad59b646a |
| ai/voice_server/server.py | 91ae060274350f90e669f1400ffb4fd0d1d04a1c37415f5f52bf39fce0c4b7e3 |
| backend/app/core/logging.py | 7e5474e5ae98b177b8b9b7a1fabb1448e50e2c05547e4b5a80ab442e9866099e |
| backend/app/modules/assistant/evidence/native_clock.py | 1f4cece9b5e5b90eb337b0d0f353ad0c39c678f8a99d51ad36b9942f2be4e25b |
| backend/app/modules/assistant/evidence/provider.py | 88c0f1d57b10b2a0817efdaf05b465979493cd589d4ca4abea5932742b9dc529 |
| backend/app/modules/voice/router.py | 9732df22f2fb152573e3e6c407bb88a46a1759e0c16a600b797faf8a264c9e9c |
| backend/tests/api/test_openapi_contract.py | e23350ec3d7f9a6d7b75905791fde0f76a80b7a1d0feca285dc644c037d88d48 |
| backend/tests/api/test_voice_api.py | fe18d9902042dbff05628d09ab30c3c039956114921789d65cf8209c7252be47 |
| backend/tests/integration/test_logging.py | 918a684fdaaa4c953e93d08986dd64a77bfbbc8644bcaf3cd1e511904892b0b7 |
| backend/tests/unit/test_assistant_evidence_provider.py | 4b4c576f99b101df96c788d1af9d33efbc238ed99c146b1024137426b4a3e111 |
| backend/tests/unit/test_voice_router.py | cc9689ebe0fdca234b5d8a622df5394c5cde06c484d445abf4476b6083dbee2d |


## 18. 최종 파일·체크섬

| 파일 | 위치·상태 |
| --- | --- |
| DRAFT_orangepi-final-guide.md | 이 묶음의 미확정 결과; 자체 해시는 ZIP 밖 checksum |
| api.md | 전체 공개 계약·실측 권장값; docs/api.md와 동일 |
| openapi.json | 실행 서버에서 받은 전체 원본, 편집 없음; docs/openapi.json과 동일 |
| samples/ | 합성 STT입력17개 + 실제 사람 녹음 WAV17개 + Pi TTS출력30개 + README |
| ZIP | DRAFT_OrangePi_최종인계.zip; 아직 최종 인계본 아님 |
| 외부 checksum | DRAFT_OrangePi_최종인계.sha256; ZIP·guide 자신 해시 |
| 별도 전달 | 접속 정보 파일, 비공개 백업·시험 근거, 모델·wheelhouse는 ZIP 밖 |

실제 IP·SSH 사용자·Wi-Fi 암호·계정·key·token·환경 원본·DB·운영 로그·모델 본체는 ZIP에 포함하지 않는다. 시험 당시에는 commit·push를 보류했으며, 이후 사용자의 명시적 커밋·푸시 지시에 따라 상단 브랜치로 기록한다.

### ZIP 내부 파일 SHA256 (guide·ZIP 제외)

| 파일 | SHA256 |
| --- | --- |
| api.md | a91ecb882fb24e0fb0b4f5c238e86da2a5910b2d6481d3c477c2c0f4e206a718 |
| openapi.json | 229b61fb5a62146792bb3898823fac485a443b565f681f98a26565c12adfbdea |
| samples/README.md | af2f36d5aaf5ba4c43c8bd387dfb2ac8c347ccfb70d90f196b36cbb42d1cf3b3 |
| samples/stt-input/ambiguous.wav | 935cce32e27934491f49881bbd4aee3946e80200d74a93bad6aea88f828041ab |
| samples/stt-input/correction.wav | ebed6fe60384de5c045a9400ec9d38ac1206cf2550d1e464ac807c2ff3122869 |
| samples/stt-input/delete.wav | 565fe060b24e9284f8c506f5925085dbf8f32f9f00f67b12802ab3d5e8978840 |
| samples/stt-input/first.wav | 48ec36f4ba619cb8b17171f7f95f0d0c3fa2af005735b75060800f28f309f862 |
| samples/stt-input/human/01_register.wav | 3d6a1c09f05241f586190175ffbcbac39135ad6a601b8338c1a8ab583255ba3e |
| samples/stt-input/human/02_time.wav | f9e7c3fbf6906defb7e7b51b3daf1cee2c58251f535f71bfb209816acf9c1522 |
| samples/stt-input/human/03_update.wav | fe9a7f3abb77303f25ad645d3a0d60744b2461f253991a3bcf560f3337a85d12 |
| samples/stt-input/human/04_yes.wav | fb6bacad633ffd178ec98a5b552bc9041d3d681245eac54eb205de78ed0ba9a3 |
| samples/stt-input/human/05_lookup.wav | 3093b47210fb72ebde92c18791f97718379e30744f608b6184968791bef00f71 |
| samples/stt-input/human/06_delete.wav | ae7f988beb0a1b1e4489353495c8311eb589c2f3339201ac315375c17810ff1b |
| samples/stt-input/human/07_select2.wav | cc8a18d73c57c8bedf09c790ced077a1fed50aa56448118391e601e663e8cf5f |
| samples/stt-input/human/08_no.wav | 9e09f0964473ed6dab040f47b52c6bb38a061bf5e1aecf867f7444f5f5eb8c13 |
| samples/stt-input/human/09_first.wav | 20e7f0edbc9051102ffe1039cab97c741dda65f5cd56c4b6108f10421b4bdfff |
| samples/stt-input/human/10_second.wav | 09ba1829e720526e079887a3ad35ccac94231f5003a2746bf21895d925273630 |
| samples/stt-input/human/11_ambiguous.wav | 21d5a1ebf05ca0625f7aff49fb8755756d01cfb6c8fcd16f97e87322f96d223c |
| samples/stt-input/human/12_outofrange.wav | 7831b4ca4e458cfc837327b4954ed78ffa2fc38d574d96a5c25507288e59130a |
| samples/stt-input/human/13_vague.wav | e3dd27498625bf5a0c7e54be1101a694e12259ce703c8072868e9ebb293ad2a5 |
| samples/stt-input/human/14_correction.wav | 3cb4085a44516db99420126f176ac169fc349c3054582b3d8190961232d98169 |
| samples/stt-input/human/15_logistics1.wav | dccabd78bed64837336488724c9d7d4584dc0f9d9b82ffb836771e56c91843c0 |
| samples/stt-input/human/16_logistics2.wav | 852ae9cf190e5eb990263de422068a0b411e30c0546374f8e1951be4164ceb65 |
| samples/stt-input/human/17_logistics3.wav | a14735b3e2fbac54d02f0a9a7335902c28bb43c1509a5c4c553f79548f914dbd |
| samples/stt-input/logistics1.wav | efd1082815f32792d2ed4bbb38df7de7069a378a6f867aa138aee8d8e5e84b9b |
| samples/stt-input/logistics2.wav | 595357f7925d17d460d67c806e4ea895baf44245ed7566a6b12eadfdf905e49b |
| samples/stt-input/logistics3.wav | 0d6ccff679861fd248da6474e7f0984ec2356af6ecae30b39632197921fbc986 |
| samples/stt-input/lookup.wav | fc9f39ead8c1b96e830f83f0e6bf7c7205ef4321d20ebf3742115b233cf337e8 |
| samples/stt-input/no.wav | 1abdd612c8a9f9d5f35cc2b81847c06c74e64e0798a4910da8fdef157d36a546 |
| samples/stt-input/outofrange.wav | 844810c89d810af4d95ffdb6627602e6340988bbc341b241baf5633946bb5a66 |
| samples/stt-input/register.wav | b42c98ff3bd1a7d18429b9be93e83b897c67d458a3e792168b5c8f355d8bd23a |
| samples/stt-input/second.wav | 5c3cbc7e3f789a8ca7debcc16fa1d9f047c57354793dcff0ee79f7a72f6eb597 |
| samples/stt-input/select2.wav | 2d08dd18e9e0ef06fc2183babc2d1fee1299bb013846ae8148180b14e7e5fdbc |
| samples/stt-input/time.wav | b58aa484eb59deab7f27596bf1b9258cdad232a72540d669c448b6ae81d52915 |
| samples/stt-input/update.wav | 6bddd6f693ad424197accaa717ce2b03e583e8dde4bab2949f549a1c24692049 |
| samples/stt-input/vague.wav | 8939506002c34fe05f5ba4602629ca02d51492fb7d376c9ef3143c94c4047b12 |
| samples/stt-input/yes.wav | 728225da77fdd039f42eb93e298aa43f55698a68b23daa1304159ac3e24d1430 |
| samples/tts-output/fixture-cancelled.wav | 3b81a94d0a2d188c1c37d27123955db4d0409ad8b8399eb849198fa8af9fa843 |
| samples/tts-output/fixture-candidates.wav | d110298f08ba8c9702e817b48fb664b7958923d3aac8d0cb50dfaa438b767b3c |
| samples/tts-output/fixture-delete-confirmation.wav | 48d2df24270f077237261d206638eaa5f34fa1848e131d7a12d228115e82fcd0 |
| samples/tts-output/fixture-failure.wav | 19c327f57e6c74fc3739dac8fd61c68ca880ace4d033ecedc0d5859057bd682f |
| samples/tts-output/fixture-lookup-0.wav | 9e6ecab4453fe5d312574aa2bbeaf60b896c917b6d4b8249715e9f78b6608260 |
| samples/tts-output/fixture-lookup-1.wav | 4bfa7b930ea1f0234441cf3c6905a75ab076e206609303ed5f5f451e67fb18c4 |
| samples/tts-output/fixture-lookup-2.wav | a823445360e17d2f0158e3859f40b01f8b35ec43f8f012149326516409133760 |
| samples/tts-output/fixture-lookup-4.wav | fae370579aef8ead0c3ed903d6b5134e8a96556d7b65547a0f9c872369fa0e83 |
| samples/tts-output/fixture-need-more-info.wav | a73d3e11370cff54be33767775f3329df1428abde283e7747e605190c762981a |
| samples/tts-output/fixture-success-create.wav | 6a5a5f7ac1ea4da5f97fe944c987ecaf8b5e64f9d0df9ed608b36f4be7379e55 |
| samples/tts-output/fixture-success-delete.wav | 12a75eb2f73d4461da62b08fce83f9ae3f590fa59c60c7e0d4fee9b7f1de787a |
| samples/tts-output/fixture-success-update.wav | c9043627fdea268eadd88810c2a220c4612554e4e5fc6111464184ae67cc8b17 |
| samples/tts-output/fixture-uncertain.wav | 1fc354bfe9e24114fdd6fc435f3a8eb37a8b7e49eea84ca80d3edb4c962e456e |
| samples/tts-output/fixture-update-confirmation.wav | 242a1a9befe4356456b80484b6c6cd18c7bcfab653cff7c616577577fb50473b |
| samples/tts-output/human/voice-01463c97-935c-480c-8a00-20bc9272ce28.wav | 699c25594e4716ab54eb5ab9f7f6b2337165f965c94bf5ed25a06767006d541e |
| samples/tts-output/human/voice-0507cbdc-8c39-4e73-b07e-5187c643fa30.wav | 764f631611949169e9d6b806535b52a1431ca92e9e8dafa734b667d0ee3bac9d |
| samples/tts-output/human/voice-06abd4b0-6564-4b42-85d4-bca5d3b7fb17.wav | 41fe00af66be5a056fe260efaf6850d6f7cb77affebe5eba7a53e048e0733f87 |
| samples/tts-output/human/voice-2b659cfb-d3c3-486e-8c90-863301165ec8.wav | fc37d382389fba4d704784dd6dd597d27db09b59add73f9fef131c259c304b91 |
| samples/tts-output/human/voice-311d108f-1ce4-4c37-8a6a-daab37f46983.wav | e3c9cb1a31d0138c9035c8ad34988b4648741ffb3e80bf8664c9fd6f072a52f2 |
| samples/tts-output/human/voice-5963aa12-627a-4e2d-980f-3cf721f6049a.wav | 6ea2a17b50c2eb81161925ada0e12e85858ee0b7ab857d1fbb1ce01789f6e2ea |
| samples/tts-output/human/voice-5a4fdfc5-7f64-4757-943f-2c35accfdc71.wav | 7b8a21e303b2783012990d07b03230184a7512518c1573ce8d1c3b67584e048f |
| samples/tts-output/human/voice-9d25aa90-7533-4c9f-9d73-699ed0fe8364.wav | 6c6ffeb3509a006798012affede5d91677e43e484b1cdcf0e5ddbd58a9c609d0 |
| samples/tts-output/human/voice-aab671cc-4da8-4b2a-89f5-ec0ca963d69c.wav | 84a4b2bb9259b876100224a9ca91c3f20d06834dce4119a156cbf639f15a7715 |
| samples/tts-output/human/voice-c1ad8110-8af0-4f19-9908-16d82dd5bbf1.wav | 16903877f727de228409bb0db868173661d040070ec18b8c71e9605008a2d2e2 |
| samples/tts-output/human/voice-df6c8d79-7c71-47fd-a91b-887c813f07c1.wav | 3118fd245eff18e09220fbe62638680610227d12cc8149511515166ce0a8c926 |
| samples/tts-output/human/voice-f1f2de8c-6bea-4f94-a978-2c4c4c718761.wav | da65314d9b849b0d92dc17c7d6aa9cbe88dbfe10a92c3ffcd582079aed33fef8 |
| samples/tts-output/voice-1ed8d0b3-d3f9-45e8-8205-af194279c833.wav | fb425a5969af7cf69e0c787166921f8c9cbac5cd0f9fdc0edba96905e66516ef |
| samples/tts-output/voice-2bdaeece-1b4c-4e1f-b9d9-ffa931889fa1.wav | 3857ecab9684f1da2110bccc0a2eecbb38b862454ba4c6b5d2b56c48bce2396b |
| samples/tts-output/voice-9e2f4749-1a3c-467f-a9bc-51f8d216e2a3.wav | b560142755eaed9ed2875dafa731dfc7a7a283dbb1453411be287a0bdd91446c |
| samples/tts-output/voice-bd2a182c-bcfc-4a9b-9c1f-53bbc9276adb.wav | 00201df3c1dc0bfa8be3c6bc2d3f03c01cbaa99cb57e97f7ce703d0ee146bc6c |


## 19. 실제 사람 녹음 17개 재시험 — 2026-11-17

이 절이 실제 녹음 재시험의 최신 결과다. §1~18의 합성 음성 실측은 이전 배포의 비교 기준으로 보존했다. 사용자가 제공한 01.m4a~17.m4a 원본을 보존하고 ffmpeg로 PCM16LE/16kHz/mono WAV로 변환했다. 원본 m4a를 backend에 직접 보낸 것이 아니라 변환한 WAV를 raw body로 업로드했다. 앱의 m4a 업로드 허용을 뜻하지 않는다.

- 기간: 2026-11-16T15:35:04.544660+00:00 ~ 2026-11-16T15:53:55.311083+00:00 (UTC; KST는 +09:00)
- 배포: 기존080e6f2 기반 미커밋 수정본. 모델·prompt·backend API·schema는 변경하지 않았다.
- 환경: 실제 Pi NPU8080/Whisper·Piper8081, 같은 backend 소스·venv의 격리 loopback8095 프로세스. 운영8000도 계속 실행했다.
- DB: 실제 Schedule 쓰기는 별도 검증 DB에서만 수행했다. 운영 일정 5행의 해시가 시험 전후 동일하다.
- 검사: AI36 PASS; backend 변경이 없어 이전1515 PASS/1 SKIP을 재실행하지 않았다.
- 최신 판정은 각 시험 이름의 마지막 결과를 기준으로 하며 이전 FAIL 행은 이력으로 보존한다. 등록·수정·조회와 짧은 등록은 broad hotwords 단계에서 통과했고, 최종 최소 hotwords로17개 STT를 다시 비교했다. 삭제와 후속 답변은 최종 설정으로 재시험했다.
- 실제 녹음 시험 판정: FAIL — 아래 실패 항목 참조. iPhone 재생·오프라인 LAN·수동 OS 시각 조정·최종commit/push는 계속 미확정이다.

### 19.1 최초 실패와 수정

02번의 ASR 출력 `오후, 2시로 잡아줘.`로 시간 보충이 MODEL_OUTPUT_INVALID였다. 시간대 뒤 시각 사이에만 들어간 쉼표를 정리하는 normalize_clock_separator를 추가했고, 같은 WAV를 그대로 사용해 재시험했다. 인용문·일반 쉼표·소수점을 보존하는 경계 테스트를 추가했다.

17번의 `입고 검수 및 재고 실사`가 최초 `입고 검수빗, 재고 실사`로 인식됐다. 물류 hotwords에 검수 및 재고 실사를 보완했다. 도크·냉장창고 등의 중복 추가는 세 시를 셋이로 인식하는 퇴행이 있어 제거하고 필요한 어휘만 유지했다. 최소 어휘로 세 시와 검수 및 재고 실사를 모두 확인하고 삭제를 재시험했다. 녹음이나 기대 문장을 수정하지 않았다.

13번 `이번 거`는 `2번, 거.`,14번 `4시`는 `내시`로 인식되는 한계가 남았다. 이런 출력은 실제 STT 결과로 기록하며, 의미를 추측해 다른 말로 고치지 않았다. 해당 답변이 후보 선택·승인으로 처리되지 않고422로 거절되는지를 별도로 확인했다. `세 시`/`3시` 표기 차이는 실제 transcript를 그대로 보존했다.

음성 서비스를 보완 적용하는 동안 후속 답변의 TTS가503이 된1회도 실패 이력으로 보존했다. 안정화 후 동일 음원의 후속 답변 전체를 재시험했다. 물류 문장의 PASS는 실제202 및 안전한 결과 반환 검사이며, need_more_info는 등록 성공으로 표시하지 않는다.

### 19.2 17개 녹음별 STT

| 파일 | 초/bytes | 기대 발화 | 실제 STT 출력 | STT HTTP/ms |
| --- | --- | --- | --- | --- |
| 01.m4a → register.wav | 5.828 / 186582 | 모레 입고 수량 대조 등록해줘 | 모레 입고 수량 대조 등록해 줘. | 200 / 5895.327 |
| 02.m4a → time.wav | 4.319 / 138284 | 오후 2시로 잡아줘 | 오후 2시로 잡아줘. | 200 / 6048.411 |
| 03.m4a → update.wav | 8.429 / 269802 | 입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘 | 입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘. | 200 / 7436.3 |
| 04.m4a → yes.wav | 2.368 / 75868 | 네 | 네. | 200 / 5139.911 |
| 05.m4a → lookup.wav | 5.503 / 176178 | 입고 수량 최종 대조 일정 알려 줘 | 입고 수량 최종 대조 일정 알려 줘 | 200 / 6445.275 |
| 06.m4a → delete.wav | 6.711 / 214816 | 내일 3시 입고 납품서 대조 일정 삭제해 줘 | 내일 세 시 입고 납품서 대조 일정 삭제해 줘. | 200 / 7039.638 |
| 07.m4a → select2.wav | 2.786 / 89242 | 2번 | 2번. | 200 / 5212.481 |
| 08.m4a → no.wav | 2.833 / 90730 | 아니요 | 아니요. | 200 / 5172.223 |
| 09.m4a → first.wav | 2.740 / 87756 | 첫 번째 | 첫 번째. | 200 / 5173.69 |
| 10.m4a → second.wav | 4.017 / 128624 | 두 번째 일정 | 두 번째 일정. | 200 / 5482.817 |
| 11.m4a → ambiguous.wav | 3.483 / 111534 | 첫 번째랑 두 번째 | 첫 번째랑 두 번째. | 200 / 5651.64 |
| 12.m4a → outofrange.wav | 2.276 / 72896 | 11번 | 11번. | 200 / 5116.583 |
| 13.m4a → vague.wav | 2.717 / 87014 | 이번 거 | 2번, 거. | 200 / 5402.947 |
| 14.m4a → correction.wav | 4.551 / 145714 | 잠깐, 시간을 4시로 바꾸고 싶어 | 잠깐 시간을 내시로 바꾸고 싶어. | 200 / 6117.276 |
| 15.m4a → logistics1.wav | 9.125 / 292092 | 내일 오전 10시 30분 2번 도크 냉장창고 온도 점검 일정 등록해 줘 | 내일, 오전 10시 30분. 2번, 도크, 냉장창고 온도 점검, 일정 등록해 줘. | 200 / 7899.493 |
| 16.m4a → logistics2.wav | 5.828 / 186582 | 모레 출고 차량 안전 점검 일정 추가해 줘 | 모레, 출고 차량 안전 점검, 일정 추가해 줘. | 200 / 6683.275 |
| 17.m4a → logistics3.wav | 10.286 / 329244 | 다음 주 월요일 오후 2시 3번 도크 입고 검수 및 재고 실사 등록해 줘 | 다음 주 월요일 오후 2시 3번 도크 입고 검수 및 재고 실사 등록해 줘. | 200 / 8085.744 |

17건의 HTTP200을 어휘100% 정확도로 해석하지 않는다. 특히13·14의 오인식을 위에 명시했다.

### 19.3 시나리오별 2회 결과

| 시나리오 | 회차 | 실제 transcript 순서 | 모델 호출 수 | 최종 검증 DB | 전체 초 |
| --- | --- | --- | --- | --- | --- |
| SCN-01 | 1 | 모레 입고 수량 대조 등록해 줘. → 오후 2시로 잡아줘. → 입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘. → 네. → 입고 수량 최종 대조 일정 알려 줘. | 4 | 입고 수량 최종 대조 / 2026-11-19T05:00:00Z / INBOUND / completed=false | 152.152 |
| SCN-01 | 2 | 모레 입고 수량 대조 등록해 줘. → 오후 2시로 잡아줘. → 입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘. → 네. → 입고 수량 최종 대조 일정 알려 줘. | 4 | 입고 수량 최종 대조 / 2026-11-19T05:00:00Z / INBOUND / completed=false | 154.279 |
| SCN-02 | 1 | 내일 세 시 입고 납품서 대조 일정 삭제해 줘. → 2번. → 네. | 1 | 입고 납품서 대조 / 2026-11-17T18:00:00Z / INBOUND / completed=false | 61.029 |
| SCN-02 | 2 | 내일 세 시 입고 납품서 대조 일정 삭제해 줘. → 2번. → 네. | 1 | 입고 납품서 대조 / 2026-11-17T18:00:00Z / INBOUND / completed=false | 60.443 |
| SCN-03 | 1 | 모레 입고 수량 대조 등록해 줘. → 오후 2시로 잡아줘. | 2 | 입고 수량 대조 / 2026-11-19T05:00:00Z / INBOUND / completed=false | 79.172 |
| SCN-03 | 2 | 모레 입고 수량 대조 등록해 줘. → 오후 2시로 잡아줘. | 2 | 입고 수량 대조 / 2026-11-19T05:00:00Z / INBOUND / completed=false | 85.265 |

| 시험 | 상태 | 초·상세 |
| --- | --- | --- |
| API-01-human-session | PASS | 0.07 / 실제 응답과 DB 조건 검사 통과 |
| SCN-01-human-1 | PASS | 152.181 / 실제 응답과 DB 조건 검사 통과 |
| SCN-01-human-2 | PASS | 154.326 / 실제 응답과 DB 조건 검사 통과 |
| SCN-02-human-1 | FAIL | 39.75 / {'requestId': '9100db80-8102-440e-baec-3a39ed27ac99', 'conversationId': '6b2be15e-5446-407b-9c60-6e25dbdc56a2', 'status': 'completed', 'result': {'kind': 'outcome', 'text': '조건에 맞는 일정을 찾지 못했어요.', 'outcome': 'no_match', 'schedulesChanged': False, 'operation': 'delete'}} |
| SCN-02-human-2 | FAIL | 39.777 / {'requestId': 'accd23ee-50e4-4702-a8d2-4f9f6f7c3191', 'conversationId': '8c47dadc-84a8-4487-a30a-69e47207a0c7', 'status': 'completed', 'result': {'kind': 'outcome', 'text': '조건에 맞는 일정을 찾지 못했어요.', 'outcome': 'no_match', 'schedulesChanged': False, 'operation': 'delete'}} |
| SCN-03-human-1 | PASS | 79.253 / 실제 응답과 DB 조건 검사 통과 |
| SCN-03-human-2 | PASS | 85.312 / 실제 응답과 DB 조건 검사 통과 |
| ANSWER-01-human | FAIL | 61.33 / {'method': 'GET', 'path': '/api/v1/voice/requests/57ea57b2-de7c-4071-961c-9850244dd92c/speech', 'status': 503, 'ms': 16.46, 'body': {'type': 'https://example.invalid/problems/tts-unavailable', 'title': 'Text-to-speech unavailable', 'status': 503, 'detail': 'Text-to-speech is currently unavailable.', 'instance': '/api/v1/voice/requests/57ea57b2-de7c-4071-961c-9850244dd92c/speech', 'code': 'TTS_UNAVAILABLE', 'requestId': '1935cb10-0c26-48fc-bf03-6063c4c8aede', 'assistantRequestId': '57ea57b2-de7c-4071-961c-9850244dd92c'}, 'bytes': 377, 'content_type': 'application/problem+json'} |
| ANSWER-01-human-first | PASS | 52.485 / 실제 응답과 DB 조건 검사 통과 |
| VOICE-01-human-logistics1 | PASS | 54.866 / 실제 응답과 DB 조건 검사 통과 |
| VOICE-01-human-logistics2 | PASS | 39.315 / 실제 응답과 DB 조건 검사 통과 |
| VOICE-01-human-logistics3 | FAIL | 70.373 / {'requestId': '01463c97-935c-480c-8a00-20bc9272ce28', 'conversationId': '1b000f99-4348-44ba-909b-0a1a29086f72', 'status': 'completed', 'result': {'kind': 'failure', 'text': '입력을 이해하지 못했어요. 내용을 확인해 다시 말씀해 주세요.', 'code': 'MODEL_OUTPUT_INVALID', 'nextAction': 'revise_input'}} |
| API-01-human-final | PASS | 0.018 / 실제 응답과 DB 조건 검사 통과 |
| SCN-02-human-1 | PASS | 61.176 / 실제 응답과 DB 조건 검사 통과 |
| SCN-02-human-2 | PASS | 60.52 / 실제 응답과 DB 조건 검사 통과 |
| ANSWER-01-human | PASS | 74.545 / 실제 응답과 DB 조건 검사 통과 |

#### SCN-01 / 1회 요청 근거

| requestId | conversationId/questionId | input | kind/outcome | STT/RKLLM/TTS ms |
| --- | --- | --- | --- | --- |
| c8164c7c-af6f-4de6-b25f-48c3678844e2 | fe6e9c1a-7d79-4b1e-87a6-0ef109c5daa2/479b9c71-895a-422a-a9f1-3836405ecc80 | {"type":"text","text":"모레 입고 수량 대조 등록해 줘."} | need_more_info/ | 6402.586 / 29574 / 237.808 |
| 5b7fe76a-12a8-40d5-9449-06e7d16305ac | fe6e9c1a-7d79-4b1e-87a6-0ef109c5daa2/없음 | {"type":"text","text":"오후 2시로 잡아줘.","questionId":"479b9c71-895a-422a-a9f1-3836405ecc80"} | outcome/success | 5710.713 / 35478 / 230.768 |
| 518b45ba-2dd7-46df-879d-34c2e4c1e8da | 85fb771c-79cf-4559-a1d5-6d22410b496e/3615c65a-f452-434b-bce2-2053a64e2044 | {"type":"text","text":"입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘."} | confirmation/ | 7293.737 / 30056 / 528.45 |
| 96af0ec4-4e5a-45be-8eaa-f691c76152b8 | 85fb771c-79cf-4559-a1d5-6d22410b496e/없음 | {"type":"confirmation","questionId":"3615c65a-f452-434b-bce2-2053a64e2044","approved":true} | outcome/success | 5088.152 / 0 / 236.461 |
| 6fde1ab4-f38c-4c60-87fb-a54a19553e98 | 95150a2f-7229-4d3e-be02-d47fd3603108/없음 | {"type":"text","text":"입고 수량 최종 대조 일정 알려 줘."} | outcome/success | 6015.806 / 20009 / 751.883 |

#### SCN-01 / 2회 요청 근거

| requestId | conversationId/questionId | input | kind/outcome | STT/RKLLM/TTS ms |
| --- | --- | --- | --- | --- |
| c1ad8110-8af0-4f19-9908-16d82dd5bbf1 | 904a8581-9300-4be1-a00e-71d1cdefdeed/09e0976c-21f4-4688-a31f-2ad0a025abc3 | {"type":"text","text":"모레 입고 수량 대조 등록해 줘."} | need_more_info/ | 6138.892 / 29258 / 208.028 |
| 5963aa12-627a-4e2d-980f-3cf721f6049a | 904a8581-9300-4be1-a00e-71d1cdefdeed/없음 | {"type":"text","text":"오후 2시로 잡아줘.","questionId":"09e0976c-21f4-4688-a31f-2ad0a025abc3"} | outcome/success | 5878.448 / 35326 / 236.655 |
| df6c8d79-7c71-47fd-a91b-887c813f07c1 | 53e411b3-cd78-4a3c-8548-38290a557388/6f0e4f27-cc2b-4637-b17e-544854c08029 | {"type":"text","text":"입고 수량 대조 제목을 입고 수량 최종 대조로 바꿔 줘."} | confirmation/ | 7164.755 / 30066 / 537.991 |
| 06abd4b0-6564-4b42-85d4-bca5d3b7fb17 | 53e411b3-cd78-4a3c-8548-38290a557388/없음 | {"type":"confirmation","questionId":"6f0e4f27-cc2b-4637-b17e-544854c08029","approved":true} | outcome/success | 5200.271 / 0 / 207.681 |
| 9d25aa90-7533-4c9f-9d73-699ed0fe8364 | fbbf24d5-7662-4c29-ac82-29efed2979a2/없음 | {"type":"text","text":"입고 수량 최종 대조 일정 알려 줘."} | outcome/success | 6348.91 / 20150 / 686.582 |

#### SCN-02 / 1회 요청 근거

| requestId | conversationId/questionId | input | kind/outcome | STT/RKLLM/TTS ms |
| --- | --- | --- | --- | --- |
| 59e90377-6f28-41df-93f0-6385a453b21d | 57aa9851-1634-4170-82db-a5f5950d7329/bffafe4b-0042-448b-938a-81b45d239415 | {"type":"text","text":"내일 세 시 입고 납품서 대조 일정 삭제해 줘."} | candidate_selection/ | 7123.485 / 35660 / 1795.83 |
| 8694b925-03b6-4252-b6ac-1e3b9a864218 | 57aa9851-1634-4170-82db-a5f5950d7329/35c013b2-635c-4d1b-9501-1c258868c43d | {"type":"selection","questionId":"bffafe4b-0042-448b-938a-81b45d239415","candidateId":"fe2a4aee-839d-490c-adb1-c52360767eaf"} | confirmation/ | 5392.513 / 0 / 672.3 |
| 5535a10c-49a4-42c9-a20e-6285c9aa123a | 57aa9851-1634-4170-82db-a5f5950d7329/없음 | {"type":"confirmation","questionId":"35c013b2-635c-4d1b-9501-1c258868c43d","approved":true} | outcome/success | 4988.979 / 0 / 211.125 |

#### SCN-02 / 2회 요청 근거

| requestId | conversationId/questionId | input | kind/outcome | STT/RKLLM/TTS ms |
| --- | --- | --- | --- | --- |
| 0507cbdc-8c39-4e73-b07e-5187c643fa30 | 71001da7-1dc8-4eff-bdc9-258f6532645d/750f012f-8b62-4307-bde1-70634a2c945b | {"type":"text","text":"내일 세 시 입고 납품서 대조 일정 삭제해 줘."} | candidate_selection/ | 6770.226 / 35633 / 1707.546 |
| f1f2de8c-6bea-4f94-a978-2c4c4c718761 | 71001da7-1dc8-4eff-bdc9-258f6532645d/0859c8e7-b223-4031-943f-1faf61f60a40 | {"type":"selection","questionId":"750f012f-8b62-4307-bde1-70634a2c945b","candidateId":"c514494a-bfd2-4003-ae3c-8eb314cbb160"} | confirmation/ | 5077.875 / 0 / 681.186 |
| aab671cc-4da8-4b2a-89f5-ec0ca963d69c | 71001da7-1dc8-4eff-bdc9-258f6532645d/없음 | {"type":"confirmation","questionId":"0859c8e7-b223-4031-943f-1faf61f60a40","approved":true} | outcome/success | 5045.78 / 0 / 216.822 |

#### SCN-03 / 1회 요청 근거

| requestId | conversationId/questionId | input | kind/outcome | STT/RKLLM/TTS ms |
| --- | --- | --- | --- | --- |
| 4f76825a-cf9c-4f57-a207-d72fc83e09b4 | c7464bd5-efa7-43e3-967f-e2385a20208c/45e683da-82b8-4b15-a164-91698024577a | {"type":"text","text":"모레 입고 수량 대조 등록해 줘."} | need_more_info/ | 6167.253 / 29487 / 182.468 |
| 066f4c5a-a243-4302-8f87-07d3cd113e80 | c7464bd5-efa7-43e3-967f-e2385a20208c/없음 | {"type":"text","text":"오후 2시로 잡아줘.","questionId":"45e683da-82b8-4b15-a164-91698024577a"} | outcome/success | 5786.665 / 35322 / 226.722 |

#### SCN-03 / 2회 요청 근거

| requestId | conversationId/questionId | input | kind/outcome | STT/RKLLM/TTS ms |
| --- | --- | --- | --- | --- |
| 8d32391f-0b69-49bf-8cf3-39ed672ebd7d | 825782d7-67ac-423f-b7ed-273e621b28a8/5d3d6505-4008-4ef9-9fdf-490cfee1e5d1 | {"type":"text","text":"모레 입고 수량 대조 등록해 줘."} | need_more_info/ | 10522.493 / 30300 / 179.666 |
| 0b4059ed-3513-41db-9205-c9324e82fc94 | 825782d7-67ac-423f-b7ed-273e621b28a8/없음 | {"type":"text","text":"오후 2시로 잡아줘.","questionId":"5d3d6505-4008-4ef9-9fdf-490cfee1e5d1"} | outcome/success | 5628.262 / 35323 / 233.557 |

### 19.4 불명확 답변 및 물류 문장

| 입력파일 | requestId | 실제 transcript | HTTP/code·결과 |
| --- | --- | --- | --- |
| ambiguous.wav | fd6de525-396e-4c6a-8866-a301c2411611 | 첫 번째랑 두 번째. | 422 / VOICE_ANSWER_UNCLEAR /  |
| outofrange.wav | 4f6b283a-8194-43c2-910b-72f511d440a2 | 11번. | 422 / VOICE_ANSWER_UNCLEAR /  |
| vague.wav | b1669407-c5df-4f7d-a03e-45b83c269de3 | 2번, 거. | 422 / VOICE_ANSWER_UNCLEAR /  |
| logistics1.wav | 2b659cfb-d3c3-486e-8c90-863301165ec8 | 내일, 오전 10시 30분. 2번, 도크, 냉장창고 온도 점검, 일정 등록해 줘. | 202 / need_more_info /  |
| logistics2.wav | 5a4fdfc5-7f64-4757-943f-2c35accfdc71 | 모레, 출고 차량 안전 점검, 일정 추가해 줘. | 202 / need_more_info /  |
| logistics3.wav | 01463c97-935c-480c-8a00-20bc9272ce28 | 다음 주 월요일 오후 2시 3번 도크 입고 검수 및 재고 실사 등록해 줘. | 202 / failure / MODEL_OUTPUT_INVALID |
| ambiguous.wav | ab7ec814-41c8-4457-9ed0-37a6280839de | 첫 번째랑 두 번째. | 422 / VOICE_ANSWER_UNCLEAR /  |
| outofrange.wav | 712c0b8b-e6c6-4b05-adec-395533768aaf | 11번. | 422 / VOICE_ANSWER_UNCLEAR /  |
| vague.wav | 020dbdcc-cbc9-4909-9b37-2d922a26b12c | 2번, 거. | 422 / VOICE_ANSWER_UNCLEAR /  |
| correction.wav | 92137d40-2f32-418e-b103-33f8d5cb3b7b | 잠깐 시간을 내시로 바꾸고 싶어. | 422 / VOICE_ANSWER_UNCLEAR /  |

거절 뒤 DB불변, 불명확 답변 뒤 질문 유지 및 후속 정상 선택·거절은 ANSWER-01 검증 함수에서 직접 비교했다.

### 19.5 실제 사람 녹음 POST·TTS 10회

최종 최소 hotwords를 적용한 뒤의 완료 요청10개를 측정값으로 사용했다. 입력 길이와 후속 답변 종류가 서로 다르며, 앞선 합성 register.wav 단일 파일10회와 같은 분포의 비교가 아니다. 이미 필요한 시나리오·후속 답변·물류 문장의 측정값을 재사용했으며 별도 성능용10회를 더 실행하지 않았다. 수치는 검증 backend8095에서의 실제 roundtrip이다. POST는 STT·접수까지, 종단은 모델완료 polling·TTS WAV 수신까지다. 모델 미호출 답변은 RKLLM0ms로 표시한다.

| 회차 | 파일/초 | requestId | STT ms | RKLLM ms | POST ms | TTS GET ms | 종단 ms | kind |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | first.wav/2.740 | 3680bf89-557c-4438-9605-7075b5f93f3c | 5453.053 | 0 | 5582.701 | 683.283 | 8342.658 | confirmation |
| 2 | no.wav/2.833 | 45f1e70b-a3b9-4e78-a108-6f52c3355a90 | 5100.231 | 0 | 5198.978 | 281.547 | 7574.287 | outcome |
| 3 | logistics1.wav/9.125 | 2b659cfb-d3c3-486e-8c90-863301165ec8 | 7298.761 | 46196 | 7349.821 | 1021.912 | 54855.154 | need_more_info |
| 4 | logistics2.wav/5.828 | 5a4fdfc5-7f64-4757-943f-2c35accfdc71 | 6732.644 | 30573 | 6771.159 | 214.381 | 39304.612 | need_more_info |
| 5 | delete.wav/6.711 | 59e90377-6f28-41df-93f0-6385a453b21d | 7123.485 | 35660 | 7208.318 | 1795.83 | 45391.541 | candidate_selection |
| 6 | select2.wav/2.786 | 8694b925-03b6-4252-b6ac-1e3b9a864218 | 5392.513 | 0 | 5465.831 | 672.3 | 8210.481 | confirmation |
| 7 | yes.wav/2.368 | 5535a10c-49a4-42c9-a20e-6285c9aa123a | 4988.979 | 0 | 5031.239 | 211.125 | 7296.117 | outcome |
| 8 | delete.wav/6.711 | 0507cbdc-8c39-4e73-b07e-5187c643fa30 | 6770.226 | 35633 | 6819.181 | 1707.546 | 44903.062 | candidate_selection |
| 9 | select2.wav/2.786 | f1f2de8c-6bea-4f94-a978-2c4c4c718761 | 5077.875 | 0 | 5137.508 | 681.186 | 7872.701 | confirmation |
| 10 | yes.wav/2.368 | aab671cc-4da8-4b2a-89f5-ec0ca963d69c | 5045.78 | 0 | 5095.578 | 216.822 | 7487.344 | outcome |

| 구간 | 최소 ms | 평균 ms | p95 ms | 최대 ms |
| --- | --- | --- | --- | --- |
| post_ms | 5031.239 | 5966.031 | 7349.821 | 7349.821 |
| completion_ms | 7084.233 | 22373.936 | 53831.788 | 53831.788 |
| tts_get_ms | 211.125 | 748.593 | 1795.83 | 1795.83 |
| e2e_ms | 7296.117 | 23123.796 | 54855.154 | 54855.154 |

p95는 nearest-rank(n10)이다. 권장은 POST35초·TTS GET35초·VOICE_BUSY2초·GET polling2초·최초시도부터120초 자동조회 유지다. 내부 음성 timeout30초를 클라이언트가 기다리고 오류를 수신할 여유5초를 포함한다. 모든15초 음성의 최대 지연을 보장하는 값은 아니다.

### 19.6 자원·보존·운영 복귀

| PID | 구분 | 처음 관측 CPU/RSS | 표본 최대 CPU/RSS | 구간 종료 CPU/RSS |
| --- | --- | --- | --- | --- |
| 1192 | rkllm-npu.service | 63.1% / 4639.7MiB | 74.4% / 4640.2MiB | 73.4% / 4640.2MiB |
| 5984 | schedule-backend.service | 0.4% / 80.9MiB | 0.4% / 80.9MiB | 0.4% / 80.9MiB |
| 9975 | 검증 backend8095 | 1.5% / 85.6MiB | 1.5% / 85.9MiB | — |
| 13552 | voice-server.service | 21.9% / 843.8MiB | 174% / 1423.3MiB | 142% / 1306.9MiB |
| 14758 | 검증 backend8095 | 149% / 80.9MiB | 149% / 85.1MiB | 2.3% / 85.1MiB |

ps CPU는 프로세스 수명 평균의5초 표본이다. 처음 관측값은 PID별 최초 표본이며, 검증 backend만 작업 단계 사이에 재시작했다. 실험 backend8095와 기존8000이 동시에 상주하므로 이전3프로세스 측정과 조건을 구분한다. MemAvailable 시작 9937.2MiB / 최소 9360.0MiB / 종료 9473.4MiB. 표본 최고온도 66.538°C.

운영 일정 행 해시 전후 동일: 7e4b86e732ac873d146c1e8d0688845c3a9ffa261fd2eb6d8a28214601a880b7. 검증용 backend는 종료했고 운영8000·NPU8080·음성8081의 health를 다시 확인했다. 운영 .env와 DB 경로는 바꾸지 않았다. 이번 시험을 위해 장비·공유기를 다시 재부팅하지 않았으며 이전 BOOT-01 증거와 구분한다.

### 19.7 현재 실행 소스 식별

| 변경 파일 | SHA256 |
| --- | --- |
| ai/voice_server/server.py | e9544c4d508300a3387bf428d66876e88a131defddd85a5c0b1497d8834a2e57 |
| ai/tests/test_voice_server.py | 6edf297547574d54552a93940e00bf30f20e33e44349791da8c43a646d32e0a5 |

원본 녹음·변환 WAV·실제 TTS 샘플의 header와SHA는 samples/README에 기록한다. 음원·운영로그·검증DB는 Git 대상에서 제외한다. 원본 실패와 전체 HTTP/단계·자원 근거는 별도 비공개 검증 경로에 보존한다.

### 19.8 남은 실패와 사용상 한계

- **17번: FAIL.** STT는 기대 문장과 일치하지만 backend Assistant는 `failure / MODEL_OUTPUT_INVALID`를 반환했다. 오류를 무시하거나 임의의 일정 write로 대체하지 않았다. 이 실패는 현재 미해결이며 전체 음성 연동 완료로 표시하지 않는다.
- **15번:** `need_more_info`로 원문과 제목·시간을 다시 연결하도록 재질문했다. HTTP202 및 안전 응답 검사는 통과했지만 일정 등록 성공이 아니다.
- **13·14번:** 어휘 오인식이 남았으나422 `VOICE_ANSWER_UNCLEAR`, DB 불변·활성 질문 유지·이후 정상 답변이 확인됐다. STT 정확도100%로 표시하지 않는다.
- iPhone 실제 재생과 외부 인터넷 없는 LAN 구성은 이번 녹음 파일 시험에 포함하지 않았다.

17번의 별도 read-only 모델 진단에서는 모델이 허용되지 않은 `completedState` 필드를 출력했고, backend 계약 검증이 `EXTRA_FIELD: completedState`로 거절한 것을 확인했다. 원출력의 시간·제목 근거도 겹쳐 있어 필드를 임의로 삭제해 성공 처리하지 않았다. 모델 원출력은 비공개 진단 자료에만 보존했다.
