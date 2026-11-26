#!/usr/bin/env python3
"""Back up and redeploy a clean develop checkout with existing environments."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import pwd
import shutil
import sqlite3
import subprocess
import time
import urllib.request

SERVICES = ["rkllm-npu.service", "voice-server.service", "schedule-backend.service"]
DEPENDENCIES = ["backend/pyproject.toml", "backend/requirements/runtime-linux-aarch64.lock", "ai/voice_server/requirements.txt", "ai/voice_server/requirements.lock", "ai/rkllm_runtime/requirements.txt"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=pathlib.Path)
    parser.add_argument("--backup-dir", required=True, type=pathlib.Path)
    parser.add_argument("--apply", action="store_true", help="perform redeploy; requires root")
    args = parser.parse_args()
    repo = args.repo_root.resolve();backend = repo / "backend";python = backend / ".venv/bin/python"
    owner = pwd.getpwuid(repo.stat().st_uid)
    prefix = ["runuser", "-u", owner.pw_name, "--"] if os.geteuid() == 0 else []
    git = prefix + ["git", "-C", str(repo)]
    def git_output(*parts):
        return subprocess.check_output([*git, *parts], text=True).strip()
    branch = git_output("branch", "--show-current");before = git_output("rev-parse", "HEAD")
    dirty = git_output("status", "--porcelain")
    check = {"branch": branch, "commit": before, "clean": not bool(dirty), "runtime_present": python.is_file()}
    print(json.dumps(check), flush=True)
    if not args.apply:
        return
    if os.geteuid() != 0:
        parser.error("--apply requires root")
    if branch != "develop" or dirty:
        parser.error("redeploy requires a clean develop checkout; local changes are never reset or stashed")
    if not python.is_file() or not (backend / ".env").is_file():
        parser.error("existing backend environment is required")
    backup_root = args.backup_dir.resolve()
    if backup_root == repo or repo in backup_root.parents:
        parser.error("backups must be outside the Git checkout")
    upstream = git_output("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    subprocess.run([*git, "fetch"], check=True)
    changed = git_output("diff", "--name-only", before, upstream, "--", *DEPENDENCIES)
    if changed:
        parser.error("dependency files changed; provision reviewed offline dependencies before redeploy: " + changed)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = backup_root / stamp;backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(backend / ".env", backup / "backend.env");(backup / "backend.env").chmod(0o600)
    for service in SERVICES:
        source = pathlib.Path("/etc/systemd/system") / service
        if source.exists(): shutil.copy2(source, backup / service)
        dropins = pathlib.Path(str(source) + ".d")
        if dropins.exists(): shutil.copytree(dropins, backup / dropins.name)
    with (backup / "code.tar.gz").open("wb") as archive:
        subprocess.run([*git, "archive", "--format=tar.gz", before], stdout=archive, check=True)
    read_config = "import json;from app.core.config import Settings;s=Settings();print(json.dumps({'database':str(s.database_path),'app_env':s.app_env}))"
    config = json.loads(subprocess.check_output([str(python), "-c", read_config], cwd=backend, text=True))
    manifest = {"previous_sha": before, "upstream": upstream, "database_path": config["database"], "backup": str(backup), "status": "backup_started"}
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
    subprocess.run(["systemctl", "stop", "schedule-backend.service"], check=True)
    try:
        with sqlite3.connect("file:" + config["database"] + "?mode=ro", uri=True) as source, sqlite3.connect(backup / "app.db") as destination:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("database backup integrity check failed")
        subprocess.run([*git, "pull", "--ff-only"], check=True)
        environment = dict(os.environ, SCHEDULE_APP_ENV=config["app_env"])
        subprocess.run([str(python), "-m", "alembic", "upgrade", "head"], cwd=backend, env=environment, check=True)
        manifest.update({"deployed_sha": git_output("rev-parse", "HEAD"), "upstream_sha": git_output("rev-parse", upstream)})
        if manifest["deployed_sha"] != manifest["upstream_sha"]:
            raise RuntimeError("deployment SHA differs from upstream")
        subprocess.run(["systemctl", "restart", *SERVICES], check=True)
        deadline = time.monotonic() + 45
        while True:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
                    if response.status == 200: break
            except OSError:
                if time.monotonic() >= deadline: raise
            time.sleep(1)
        manifest["status"] = "health_passed"
    except Exception:
        manifest["status"] = "failed_review_backup_and_service_state"
        raise
    finally:
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print("Backup and recovery manifest: " + str(backup / "manifest.json"), flush=True)
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
