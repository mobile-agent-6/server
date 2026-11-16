#!/usr/bin/env python3
"""Render or install systemd services using caller-supplied device paths."""
from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import pwd
import shutil
import subprocess
import time
import urllib.request

SERVICES = ("rkllm-npu", "voice-server", "schedule-backend")


def systemd_path(value: pathlib.Path) -> str:
    text = os.path.abspath(value)
    if any(character in text for character in '\n\r%$"\\') or any(character.isspace() for character in text):
        raise ValueError("service paths must not contain whitespace, quotes, escapes, or specifiers")
    return text


def render_service(description, user, directory, command, environment=(), after="network.target", wants=""):
    lines = ["[Unit]", f"Description={description}", f"After={after}"]
    if wants:
        lines.append(f"Wants={wants}")
    lines.extend(["", "[Service]", "Type=simple", f"User={user}", f"WorkingDirectory={directory}", *[f"Environment={value}" for value in environment], f"ExecStart={command}", "Restart=always", "RestartSec=3", "", "[Install]", "WantedBy=multi-user.target", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=pathlib.Path)
    parser.add_argument("--rkllm-entry", required=True, type=pathlib.Path)
    parser.add_argument("--service-user", required=True)
    parser.add_argument("--rkllm-python", type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--stt-model", choices=("base", "small"), default="small")
    parser.add_argument("--install", action="store_true", help="install, enable, and restart services; requires root")
    args = parser.parse_args()
    user = pwd.getpwnam(args.service_user)
    if not args.service_user or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.service_user):
        parser.error("invalid service user")
    repo = args.repo_root.resolve();backend = repo / "backend";voice = repo / "ai/voice_server"
    output = args.output_dir.resolve()
    systemd_root = pathlib.Path("/etc/systemd/system")
    if output == repo or repo in output.parents:
        parser.error("generated device service files must be outside the Git checkout")
    if output == systemd_root or systemd_root in output.parents:
        parser.error("render outside /etc/systemd/system so original units can be backed up")
    rkllm_python = args.rkllm_python or repo / "ai/rkllm_runtime/.venv/bin/python"
    required = [rkllm_python, backend / ".venv/bin/uvicorn", backend / ".env", voice / ".venv/bin/python", voice / "server.py", args.rkllm_entry]
    for path in required:
        if not path.is_file():
            parser.error(f"required deployment file is missing: {path}")
    model_cache = pathlib.Path(user.pw_dir) / ".cache/huggingface/hub" / f"models--Systran--faster-whisper-{args.stt_model}"
    if not any(path.is_file() for path in model_cache.glob("snapshots/*/model.bin")):
        parser.error("the selected STT model must already be cached for the service user")
    systemd_path(repo);backend_path = systemd_path(backend);voice_path = systemd_path(voice);rkllm = systemd_path(args.rkllm_entry)
    units = {
        "rkllm-npu": render_service("RKLLM NPU inference", args.service_user, systemd_path(args.rkllm_entry.parent), systemd_path(rkllm_python) + " " + rkllm),
        "voice-server": render_service("Local STT/TTS", args.service_user, voice_path, voice_path + "/.venv/bin/python " + voice_path + "/server.py", ("PYTHONUNBUFFERED=1", "STT_MODEL_SIZE=" + args.stt_model, "STT_BEAM_SIZE=3", "STT_THREADS=8")),
        "schedule-backend": render_service("Schedule and voice API", args.service_user, backend_path, backend_path + "/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000", ("PYTHONUNBUFFERED=1",), "network.target rkllm-npu.service voice-server.service", "rkllm-npu.service voice-server.service"),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, content in units.items():
        target = args.output_dir / (name + ".service");target.write_text(content);paths.append(str(target))
    subprocess.run(["systemd-analyze", "verify", *paths], check=True)
    print("Validated three generated service units.")
    if not args.install:
        return
    if os.geteuid() != 0:
        parser.error("--install requires root")
    backup = args.output_dir / ("previous-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"));backup.mkdir(mode=0o700)
    destination = pathlib.Path("/etc/systemd/system")
    for name in SERVICES:
        target = destination / (name + ".service")
        if target.exists(): shutil.copy2(target, backup / target.name)
        shutil.copyfile(args.output_dir / target.name, target);target.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", *[name + ".service" for name in SERVICES]], check=True)
    subprocess.run(["systemctl", "restart", *[name + ".service" for name in SERVICES]], check=True)
    deadline = time.monotonic() + 60
    for port in (8000, 8080, 8081):
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
            time.sleep(1)
    print("Installed services. Previous units: " + str(backup))


if __name__ == "__main__":
    main()
