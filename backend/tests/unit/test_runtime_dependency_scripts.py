from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tomllib

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    BACKEND_ROOT / "scripts" / "generate-runtime-lock.sh",
    BACKEND_ROOT / "scripts" / "build-verify-wheelhouse.sh",
)
RUNTIME_LOCK = BACKEND_ROOT / "requirements-runtime-linux-aarch64.lock"


def _locked_package_names() -> set[str]:
    names: set[str] = set()
    for line in RUNTIME_LOCK.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith((" ", "#", "--")):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==[^\s]+", line)
        assert match is not None, f"runtime dependency is not exactly pinned: {line}"
        names.add(match.group(1).lower().replace("_", "-"))
    return names


def test_runtime_lock_contains_greenlet_and_excludes_dev_dependencies() -> None:
    locked_names = _locked_package_names()
    pyproject = tomllib.loads(
        (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    dev_requirements = pyproject["project"]["optional-dependencies"]["dev"]
    dev_names = {
        re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0]
        .lower()
        .replace("_", "-")
        for requirement in dev_requirements
    }

    assert "greenlet" in locked_names
    assert locked_names.isdisjoint(dev_names)


@pytest.mark.parametrize("script", SCRIPTS)
def test_runtime_dependency_script_help(script: Path) -> None:
    result = subprocess.run(
        ["bash", str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "linux/aarch64" in result.stdout
    assert "Python 3.11" in result.stdout


@pytest.mark.parametrize("script", SCRIPTS)
def test_runtime_dependency_script_rejects_unknown_argument(script: Path) -> None:
    result = subprocess.run(
        ["bash", str(script), "--not-a-real-option"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "unknown argument" in result.stderr


@pytest.mark.parametrize("script", SCRIPTS)
def test_runtime_dependency_script_rejects_wrong_docker_platform(
    script: Path, tmp_path: Path
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'linux/amd64'\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 1
    assert "Docker daemon platform must be linux/aarch64" in result.stderr
