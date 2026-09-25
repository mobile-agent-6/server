"""Wall and monotonic clock sources used by the application."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock values must be timezone-aware")
    return value.astimezone(timezone.utc)


class SystemClock:
    """Return the current system wall-clock time in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Controllable UTC wall clock for deterministic tests."""

    def __init__(self, current: datetime) -> None:
        self._current = _as_utc(current)

    def now(self) -> datetime:
        return self._current

    def set(self, current: datetime) -> None:
        self._current = _as_utc(current)

    def advance(self, delta: timedelta) -> None:
        self._current += delta


class SystemMonotonicClock:
    """Measure elapsed time independently from system wall-clock changes."""

    def now(self) -> float:
        return time.monotonic()


class FixedMonotonicClock:
    """Controllable monotonic source for duration-sensitive tests."""

    def __init__(self, current: float = 0.0) -> None:
        if current < 0:
            raise ValueError("monotonic clock values cannot be negative")
        self._current = float(current)

    def now(self) -> float:
        return self._current

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("a monotonic clock cannot move backwards")
        self._current += seconds
