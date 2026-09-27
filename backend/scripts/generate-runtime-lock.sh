#!/usr/bin/env bash

set -Eeuo pipefail

readonly TARGET_PLATFORM="linux/aarch64"
readonly PYTHON_MINOR="3.11"
readonly PYTHON_IMAGE="python:3.11-slim-bookworm"
readonly PIP_TOOLS_VERSION="7.6.1"
readonly LOCK_NAME="requirements-runtime-linux-aarch64.lock"

usage() {
    cat <<EOF
Usage: $(basename "$0")

Generate ${LOCK_NAME} from pyproject.toml base dependencies in a clean
${TARGET_PLATFORM} Docker container using Python ${PYTHON_MINOR},
${PYTHON_IMAGE}, and pip-tools==${PIP_TOOLS_VERSION}.
EOF
}

if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help)
            if [[ $# -eq 1 ]]; then
                usage
                exit 0
            fi
            ;;
    esac
    printf 'error: unknown argument: %s\n' "$1" >&2
    usage >&2
    exit 2
fi

if ! command -v docker >/dev/null 2>&1; then
    printf 'error: docker is required\n' >&2
    exit 1
fi

docker_platform="$(docker info --format '{{.OSType}}/{{.Architecture}}')"
if [[ "$docker_platform" != "$TARGET_PLATFORM" ]]; then
    printf 'error: Docker daemon platform must be %s; got %s\n' \
        "$TARGET_PLATFORM" "$docker_platform" >&2
    exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
backend_dir="$(cd "${script_dir}/.." && pwd -P)"
lock_path="${backend_dir}/${LOCK_NAME}"
output_dir="$(mktemp -d "${TMPDIR:-/tmp}/schedule-api-runtime-lock.XXXXXX")"
trap 'rm -rf "$output_dir"' EXIT

if [[ -f "$lock_path" ]]; then
    cp "$lock_path" "${output_dir}/${LOCK_NAME}"
fi

docker run --rm \
    --platform "$TARGET_PLATFORM" \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp/home \
    --volume "${backend_dir}:/source:ro" \
    --volume "${output_dir}:/output" \
    --workdir /source \
    "$PYTHON_IMAGE" \
    sh -ceu '
        test "$(uname -m)" = aarch64
        test "$(python -c '\''import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'\'')" = "$1"
        mkdir -p "$HOME"
        python -m venv /tmp/lock-venv
        /tmp/lock-venv/bin/python -m pip install \
            --disable-pip-version-check \
            --no-cache-dir \
            "pip-tools==$2"
        /tmp/lock-venv/bin/pip-compile \
            --index-url=https://pypi.org/simple \
            --output-file="/output/$3" \
            --strip-extras \
            /source/pyproject.toml
    ' sh "$PYTHON_MINOR" "$PIP_TOOLS_VERSION" "$LOCK_NAME"

if [[ ! -s "${output_dir}/${LOCK_NAME}" ]]; then
    printf 'error: lock generation did not produce %s\n' "$LOCK_NAME" >&2
    exit 1
fi

if cmp -s "${output_dir}/${LOCK_NAME}" "$lock_path" 2>/dev/null; then
    printf '%s is already up to date.\n' "$lock_path"
    exit 0
fi

cp "${output_dir}/${LOCK_NAME}" "$lock_path"
printf 'Generated %s\n' "$lock_path"
