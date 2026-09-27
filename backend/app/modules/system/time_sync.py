"""Safe time synchronization adapter boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class TimeSyncAdapterError(Exception):
    """An OS adapter operation could not be completed."""


class Clock(Protocol):
    def now(self) -> datetime: ...


class TimeSyncAdapter(Protocol):
    def read_time(self) -> datetime: ...

    def is_ntp_active(self, *, timeout_seconds: float) -> bool: ...

    def set_time(self, target: datetime, *, timeout_seconds: float) -> None: ...


class DisabledTimeSyncAdapter:
    """Development adapter that cannot inspect NTP or change system time."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def read_time(self) -> datetime:
        return self._clock.now()

    def is_ntp_active(self, *, timeout_seconds: float) -> bool:
        raise TimeSyncAdapterError("NTP inspection is not configured")

    def set_time(self, target: datetime, *, timeout_seconds: float) -> None:
        raise TimeSyncAdapterError("system time changes are not configured")
