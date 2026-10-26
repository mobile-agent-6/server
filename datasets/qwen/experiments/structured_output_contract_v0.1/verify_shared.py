"""Verify tracked Issue #30 artifacts in a path-compatible temporary layout."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "shared-manifest-v0.1.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_root() -> Path:
    for candidate in (HERE, *HERE.parents):
        if (candidate / ".git").exists() and (candidate / "backend").is_dir():
            return candidate
    raise RuntimeError("repository root not found")


def verify_manifest() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError("manifest artifacts missing")
    for artifact in artifacts:
        path = HERE / artifact["path"]
        if not path.is_file():
            raise RuntimeError(f"missing artifact: {artifact['path']}")
        actual = sha256(path)
        if actual != artifact["sha256"]:
            raise RuntimeError(
                f"hash mismatch: {artifact['path']} expected={artifact['sha256']} actual={actual}"
            )
    return manifest


def choose_python(root: Path) -> Path:
    candidates = (
        root.parent / "server/backend/.venv/bin/python",
        root / "backend/.venv/bin/python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def run_tests(manifest: dict) -> None:
    root = repo_root()
    with tempfile.TemporaryDirectory(prefix="issue30-shared-verify.") as tmp:
        stage_root = Path(tmp)
        stage = stage_root / ".omx/research/issue30"
        stage.mkdir(parents=True)
        for artifact in manifest["artifacts"]:
            if not artifact.get("stageForTests", False):
                continue
            shutil.copy2(HERE / artifact["path"], stage / artifact["path"])
        os.symlink(root / "backend", stage_root / "backend", target_is_directory=True)
        os.symlink(root / "datasets", stage_root / "datasets", target_is_directory=True)
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [
            str(choose_python(root)),
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(stage),
            "-p",
            "test_*.py",
        ]
        subprocess.run(command, check=True, cwd=stage_root, env=env)


def main() -> int:
    manifest = verify_manifest()
    run_tests(manifest)
    print(
        "PASS: shared hashes and tests "
        f"({manifest['summary']['testCount']} tests, "
        f"{len(manifest['artifacts'])} artifacts)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
