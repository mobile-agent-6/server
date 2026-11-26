#!/usr/bin/env python3
"""Create separate Python 3.11 environments using prepared offline wheels."""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True, type=pathlib.Path)
    parser.add_argument("--wheelhouse-root", required=True, type=pathlib.Path)
    parser.add_argument("--environment-root", type=pathlib.Path)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 11):
        parser.error("the verified runtime uses Python 3.11")
    repo = args.repo_root.resolve()
    destination = (args.environment_root or repo).resolve()
    environments = [
        ("backend", "backend", "backend/requirements-runtime-linux-aarch64.lock",
         "import alembic, fastapi, greenlet, pydantic_settings, sqlalchemy, uvicorn; from app.main import app; assert app is not None"),
        ("voice", "ai/voice_server", "ai/voice_server/requirements.lock",
         "from faster_whisper import WhisperModel; from piper.voice import PiperVoice; import fastapi, uvicorn, multipart"),
        ("rkllm", "ai/rkllm_runtime", "ai/rkllm_runtime/requirements.txt",
         "import ctypes, flask, resource"),
    ]
    for name, relative, lock, _ in environments:
        if (destination / relative / ".venv").exists():
            parser.error(f"refusing to overwrite an existing environment: {relative}")
        if not (repo / lock).is_file():
            parser.error(f"runtime lock is missing: {lock}")
        if not any((args.wheelhouse_root / name).glob("*.whl")):
            parser.error(f"prepared wheels are missing: {name}")
    results = []
    for name, relative, lock, imports in environments:
        target = destination / relative / ".venv"
        venv.EnvBuilder(with_pip=True, symlinks=True).create(target)
        python = str(target / "bin/python")
        subprocess.run([
            python, "-m", "pip", "install", "--disable-pip-version-check", "--no-index",
            "--find-links", str((args.wheelhouse_root / name).resolve()), "-r", str(repo / lock),
        ], check=True)
        subprocess.run([python, "-m", "pip", "check"], check=True)
        subprocess.run([python, "-c", imports], cwd=repo / "backend", check=True)
        results.append({"environment": name, "python": python, "offline_install": "PASS", "imports": "PASS"})
    print(json.dumps(results), flush=True)


if __name__ == "__main__":
    main()
