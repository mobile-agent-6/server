#!/usr/bin/env bash

set -Eeuo pipefail

readonly TARGET_PLATFORM="linux/aarch64"
readonly PYTHON_MINOR="3.11"
readonly PYTHON_IMAGE="python:3.11-slim-bookworm"
readonly LOCK_NAME="requirements-runtime-linux-aarch64.lock"

usage() {
    cat <<EOF
Usage: $(basename "$0") [--output ABSOLUTE_DIRECTORY]

Download binary-only wheels for ${TARGET_PLATFORM}, reject incompatible wheel
tags, and verify a network-disabled installation in a clean Python ${PYTHON_MINOR}
container (${PYTHON_IMAGE}).

Without --output, a temporary wheelhouse is used and removed. To keep the
wheelhouse, provide a new absolute directory outside this repository.
EOF
}

requested_output=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --output)
            if [[ $# -lt 2 ]]; then
                printf 'error: --output requires a directory\n' >&2
                exit 2
            fi
            requested_output="$2"
            shift 2
            ;;
        *)
            printf 'error: unknown argument: %s\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

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
repository_dir="$(cd "${backend_dir}/.." && pwd -P)"
lock_path="${backend_dir}/${LOCK_NAME}"

if [[ ! -s "$lock_path" ]]; then
    printf 'error: runtime lock not found: %s\n' "$lock_path" >&2
    exit 1
fi

temporary_output=false
if [[ -z "$requested_output" ]]; then
    output_dir="$(mktemp -d "${TMPDIR:-/tmp}/schedule-api-wheelhouse.XXXXXX")"
    temporary_output=true
    trap 'rm -rf "$output_dir"' EXIT
else
    case "$requested_output" in
        /*) ;;
        *)
            printf 'error: --output must be an absolute path outside the repository\n' >&2
            exit 2
            ;;
    esac
    output_parent="$(cd "$(dirname "$requested_output")" && pwd -P)"
    output_name="$(basename "$requested_output")"
    if [[ "$output_name" == "." || "$output_name" == ".." ]]; then
        printf 'error: invalid --output directory\n' >&2
        exit 2
    fi
    output_dir="${output_parent}/${output_name}"
    case "${output_dir}/" in
        "${repository_dir}/"*)
            printf 'error: --output must be outside the repository\n' >&2
            exit 2
            ;;
    esac
    if [[ -e "$output_dir" ]]; then
        printf 'error: --output must not already exist: %s\n' "$output_dir" >&2
        exit 2
    fi
    mkdir "$output_dir"
fi

docker run --rm \
    --platform "$TARGET_PLATFORM" \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp/home \
    --volume "${backend_dir}:/source:ro" \
    --volume "${output_dir}:/wheelhouse" \
    --workdir /source \
    "$PYTHON_IMAGE" \
    sh -ceu '
        test "$(uname -m)" = aarch64
        test "$(python -c '\''import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'\'')" = "$1"
        mkdir -p "$HOME"
        python -m pip download \
            --disable-pip-version-check \
            --no-cache-dir \
            --only-binary=:all: \
            --requirement "/source/$2" \
            --dest /wheelhouse
    ' sh "$PYTHON_MINOR" "$LOCK_NAME"

wheel_count=0
while IFS= read -r -d '' wheel_path; do
    wheel_count=$((wheel_count + 1))
    wheel_name="$(basename "$wheel_path")"
    normalized_name="$(printf '%s' "$wheel_name" | tr '[:upper:]' '[:lower:]')"
    case "$normalized_name" in
        *macosx*|*x86_64*|*amd64*|*i686*|*win32*|*win_amd64*)
            printf 'error: incompatible wheel found: %s\n' "$wheel_name" >&2
            exit 1
            ;;
    esac
    case "$normalized_name" in
        *-none-any.whl|*aarch64*.whl) ;;
        *)
            printf 'error: wheel is neither platform-independent nor AArch64: %s\n' \
                "$wheel_name" >&2
            exit 1
            ;;
    esac
done < <(find "$output_dir" -maxdepth 1 -type f -name '*.whl' -print0)

if [[ "$wheel_count" -eq 0 ]]; then
    printf 'error: no wheels were downloaded\n' >&2
    exit 1
fi

if find "$output_dir" -maxdepth 1 -type f ! -name '*.whl' -print -quit | grep -q .; then
    printf 'error: wheelhouse contains a non-wheel artifact\n' >&2
    exit 1
fi

docker run --rm \
    --platform "$TARGET_PLATFORM" \
    --network none \
    --volume "${backend_dir}:/source:ro" \
    --volume "${output_dir}:/wheelhouse:ro" \
    --workdir /source \
    "$PYTHON_IMAGE" \
    sh -ceu '
        test "$(uname -m)" = aarch64
        test "$(python -c '\''import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'\'')" = "$1"
        python -m venv /tmp/runtime-venv
        /tmp/runtime-venv/bin/python -m pip install \
            --disable-pip-version-check \
            --no-index \
            --find-links=/wheelhouse \
            --requirement "/source/$2"
        /tmp/runtime-venv/bin/python -m pip check
        /tmp/runtime-venv/bin/python -c '\''
import alembic
import fastapi
import greenlet
import pydantic_settings
import sqlalchemy
import uvicorn
from app.main import app

assert app is not None
'\''
    ' sh "$PYTHON_MINOR" "$LOCK_NAME"

if [[ "$temporary_output" == true ]]; then
    printf 'Verified %s wheels in a temporary wheelhouse; it will now be removed.\n' \
        "$wheel_count"
else
    printf 'Verified %s wheels in %s\n' "$wheel_count" "$output_dir"
fi
