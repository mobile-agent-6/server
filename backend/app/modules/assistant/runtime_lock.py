"""Per-database POSIX advisory ownership lock for the Assistant runtime."""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path


class AssistantRuntimeLockContended(RuntimeError):
    """Another open file description owns the runtime lock."""


class AssistantRuntimeLockUnavailable(RuntimeError):
    """The runtime lock file could not be opened or locked safely."""


class AssistantRuntimeFileLock:
    def __init__(self, *, descriptor: int, path: Path) -> None:
        self._descriptor = descriptor
        self.path = path

    @classmethod
    def acquire(cls, database_path: Path) -> "AssistantRuntimeFileLock":
        path = database_path.with_name(
            f"{database_path.name}.assistant-runtime.lock"
        )
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise AssistantRuntimeLockUnavailable(
                "Assistant runtime lock could not be opened"
            ) from exc
        try:
            os.set_inheritable(descriptor, False)
        except OSError as exc:
            os.close(descriptor)
            raise AssistantRuntimeLockUnavailable(
                "Assistant runtime lock inheritance could not be disabled"
            ) from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(descriptor)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise AssistantRuntimeLockContended(
                    "Assistant runtime lock is already held"
                ) from exc
            raise AssistantRuntimeLockUnavailable(
                "Assistant runtime lock could not be acquired"
            ) from exc
        return cls(descriptor=descriptor, path=path)

    @property
    def descriptor(self) -> int:
        return self._descriptor

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
