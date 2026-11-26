# Orange Pi 배포 도구

`install_services.py`는 장비 경로와 실행 사용자를 인자로 받아 3개 systemd 파일을 생성하고 `systemd-analyze verify`로 확인한다. 실제 경로가 들어간 생성물은 Git 밖에 둔다. Tailscale 서비스에 의존하지 않으며 STT/TTS는 코드에서 loopback을 사용한다.

`redeploy.py`는 기본 실행 시 Git 상태와 런타임 존재 여부만 확인한다. `--apply`는 깨끗한 develop에서만 백업·fast-forward·migration·서비스 재시작·health 확인을 수행한다. 작업 트리를 stash/reset하지 않는다. 의존성 파일이 달라지면 의존성을 먼저 검토·준비하도록 중단한다. backend와 음성 가상환경, 모델 파일이 이미 준비된 장비를 대상으로 한다.

## 서비스 생성과 설치

최초 설치용 wheelhouse는 `backend`, `voice`, `rkllm` 하위 폴더에 해당 환경의 고정 의존성 wheel을 준비한다. 다음 명령은 새 Python 3.11 가상환경 3개를 만들고 `--no-index` 설치, `pip check`, 실제 모듈 import를 확인한다. 기존 가상환경은 덮어쓰지 않는다.

```bash
python3.11 ai/deploy/bootstrap_runtime.py \
  --repo-root "$REPO_ROOT" --wheelhouse-root "$WHEELHOUSE_ROOT"
```

별도 검증 경로에 재현할 때는 `--environment-root "$STAGING_ROOT"`를 추가한다. 2026-11-16 실제 Pi에서 새 경로의 세 환경을 오프라인 방식으로 설치·검증했다. 음성 환경은 `ai/voice_server/requirements.lock`, NPU 웹 런타임은 `ai/rkllm_runtime/requirements.txt`를 사용한다. 모델·RKLLM native runtime과 음성 모델 cache는 별도 전달·설치 대상이며 부팅 중 다운로드하지 않는다.

backend 환경은 [Pi 예시](orangepi.env.example)를 검토해 `backend/.env`에 설정한다. DB 최초 생성은 backend에서 명시적인 관리 환경으로 `SCHEDULE_APP_ENV=development .venv/bin/python -m alembic -c config/alembic.ini upgrade head`를 실행한다. 실사용 DB에 적용할 때는 [백엔드 migration 지침](../../backend/README.md)을 따른다.

다음 변수에는 해당 장비에서 확인한 경로·사용자를 넣는다. 실행 사용자와 실제 접속 정보는 Git 예시에 고정하지 않는다.

```bash
python3 ai/deploy/install_services.py \
  --repo-root "$REPO_ROOT" \
  --rkllm-entry "$RKLLM_ENTRY" \
  --service-user "$SERVICE_USER" \
  --output-dir "$SERVICE_OUTPUT_DIR"
```

생성한 파일을 검토한 뒤 같은 명령을 sudo로 실행하고 `--install`을 붙이면 기존 서비스 파일을 output 디렉터리의 `previous-<UTC>`에 보존한 뒤 설치·enable·restart한다. 기존 drop-in은 보존된다. drop-in의 STT 설정이 생성한 unit보다 우선하므로 실제 환경은 `systemctl show voice-server.service --property=Environment,DropInPaths`로 확인한다.

NPU interpreter 기본 경로는 저장소의 `ai/rkllm_runtime/.venv/bin/python`이다. 별도 위치라면 `--rkllm-python`으로 전달한다. 가상환경의 interpreter symlink 경로를 유지한다. 설치 완료는 세 서비스의 실제 `/health` 200 확인 뒤 반환한다.

## 재배포

```bash
python3 ai/deploy/redeploy.py --repo-root "$REPO_ROOT" --backup-dir "$BACKUP_DIR"
sudo python3 ai/deploy/redeploy.py --repo-root "$REPO_ROOT" --backup-dir "$BACKUP_DIR" --apply
```

backup 디렉터리는 저장소 밖에 둔다. 코드 archive, backend 환경 파일, 서비스와 drop-in, SQLite online backup과 manifest를 보관한다. manifest에는 이전 SHA, DB 경로와 재배포 결과가 남는다. 실패하면 파일을 임의로 삭제하거나 DB downgrade하지 않고 manifest와 서비스 상태를 확인한다. 코드와 DB 복구본의 revision을 함께 맞춘다.

최초 의존성·모델 설치와 전체 rollback은 위 스크립트가 자동으로 수행하지 않는다. 현재 장비의 설치·rollback 검증 상태와 실제 절차는 요청된 최종 배포·시험 가이드에서 확인한다. commit·push는 저장소 지침에 따라 각각 명시적인 확인 뒤 실행한다.
