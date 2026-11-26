from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from app.modules.assistant.runtime_lock import (
    AssistantRuntimeFileLock,
    AssistantRuntimeLockContended,
)


def lock_path(database: Path) -> Path:
    return database.with_name(f"{database.name}.assistant-runtime.lock")


def test_same_database_has_one_nonblocking_owner_and_keeps_file(
    tmp_path: Path,
) -> None:
    database = tmp_path / "app.db"
    database.touch()

    first = AssistantRuntimeFileLock.acquire(database)
    try:
        with pytest.raises(AssistantRuntimeLockContended):
            AssistantRuntimeFileLock.acquire(database)
        path = lock_path(database)
        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert os.get_inheritable(first.descriptor) is False
    finally:
        first.release()

    assert lock_path(database).exists()
    second = AssistantRuntimeFileLock.acquire(database)
    second.release()
    assert lock_path(database).exists()


def test_different_database_paths_can_each_be_owned(tmp_path: Path) -> None:
    first_database = tmp_path / "first.db"
    second_database = tmp_path / "second.db"
    first_database.touch()
    second_database.touch()

    first = AssistantRuntimeFileLock.acquire(first_database)
    second = AssistantRuntimeFileLock.acquire(second_database)
    try:
        assert first.descriptor != second.descriptor
    finally:
        second.release()
        first.release()


def test_stale_unlocked_file_does_not_block_acquisition(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    database.touch()
    path = lock_path(database)
    path.write_bytes(b"")

    ownership = AssistantRuntimeFileLock.acquire(database)
    ownership.release()

    assert path.exists()


def test_lock_descriptor_is_not_inherited_by_child_process(tmp_path: Path) -> None:
    database = tmp_path / "app.db"
    database.touch()
    ownership = AssistantRuntimeFileLock.acquire(database)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os,sys; "
                    "fd=int(sys.argv[1]); "
                    "\ntry: os.fstat(fd)\n"
                    "except OSError: print('closed')\n"
                    "else: print('open')"
                ),
                str(ownership.descriptor),
            ],
            check=True,
            capture_output=True,
            text=True,
            close_fds=False,
        )
    finally:
        ownership.release()

    assert result.stdout.strip() == "closed"
