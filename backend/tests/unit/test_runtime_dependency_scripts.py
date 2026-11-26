from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    BACKEND_ROOT / "scripts" / "generate-runtime-lock.sh",
    BACKEND_ROOT / "scripts" / "build-verify-wheelhouse.sh",
)
RUNTIME_LOCK = BACKEND_ROOT / "requirements" / "runtime-linux-aarch64.lock"
DEVELOPMENT_LOCK = BACKEND_ROOT / "requirements" / "development.lock"


def _locked_packages(lock_path: Path) -> dict[str, str]:
    packages: dict[str, str] = {}
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith((" ", "#", "--")):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", line)
        assert match is not None, f"dependency is not exactly pinned: {line}"
        name = re.sub(r"[-_.]+", "-", match.group(1).lower())
        assert name not in packages, f"duplicate locked dependency: {name}"
        packages[name] = match.group(2)
    return packages


def _fake_docker_environment(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/bin/sh
if [ "$1" = "info" ]; then
    printf '%s\\n' 'linux/aarch64'
    exit 0
fi

wheelhouse=''
for argument in "$@"; do
    case "$argument" in
        *:/wheelhouse)
            wheelhouse="${argument%:/wheelhouse}"
            ;;
    esac
done

case "${FAKE_WHEELHOUSE_KIND:-}" in
    macos)
        : > "$wheelhouse/greenlet-3.5.6-cp311-cp311-macosx_11_0_arm64.whl"
        ;;
    x86_64)
        : > "$wheelhouse/greenlet-3.5.6-cp311-cp311-manylinux_2_24_x86_64.whl"
        ;;
    sdist)
        : > "$wheelhouse/anyio-4.15.1-py3-none-any.whl"
        : > "$wheelhouse/greenlet-3.5.6.tar.gz"
        ;;
    empty|'')
        ;;
    *)
        printf 'unknown fake wheelhouse kind: %s\\n' "$FAKE_WHEELHOUSE_KIND" >&2
        exit 2
        ;;
esac
""",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return env


def test_runtime_lock_matches_development_lock_versions() -> None:
    runtime_packages = _locked_packages(RUNTIME_LOCK)
    development_packages = _locked_packages(DEVELOPMENT_LOCK)
    common_packages = runtime_packages.keys() & development_packages.keys()

    assert common_packages
    assert {
        package: (runtime_packages[package], development_packages[package])
        for package in common_packages
        if runtime_packages[package] != development_packages[package]
    } == {}
    assert runtime_packages.keys() - development_packages.keys() == {"greenlet"}


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


@pytest.mark.parametrize(
    ("wheelhouse_kind", "expected_error"),
    (
        ("macos", "incompatible wheel found"),
        ("x86_64", "incompatible wheel found"),
        ("sdist", "wheelhouse contains a non-wheel artifact"),
        ("empty", "no wheels were downloaded"),
    ),
)
def test_wheelhouse_script_rejects_invalid_downloads(
    wheelhouse_kind: str, expected_error: str, tmp_path: Path
) -> None:
    env = _fake_docker_environment(tmp_path)
    env["FAKE_WHEELHOUSE_KIND"] = wheelhouse_kind

    result = subprocess.run(
        ["bash", str(BACKEND_ROOT / "scripts" / "build-verify-wheelhouse.sh")],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 1
    assert expected_error in result.stderr


@pytest.mark.parametrize("output_kind", ("relative", "repository", "existing"))
def test_wheelhouse_script_rejects_unsafe_output_paths(
    output_kind: str, tmp_path: Path
) -> None:
    env = _fake_docker_environment(tmp_path)
    if output_kind == "relative":
        output_path = "relative-wheelhouse"
        expected_error = "--output must be an absolute path outside the repository"
    elif output_kind == "repository":
        output_path = str(BACKEND_ROOT / ".test-wheelhouse")
        expected_error = "--output must be outside the repository"
    else:
        output_path = str(tmp_path)
        expected_error = "--output must not already exist"

    result = subprocess.run(
        [
            "bash",
            str(BACKEND_ROOT / "scripts" / "build-verify-wheelhouse.sh"),
            "--output",
            output_path,
        ],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr
