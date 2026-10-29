"""Trust the Pi clock only after the phone has confirmed it (time-sync).

The Orange Pi may boot with a wrong clock and no network.  A successful
time-sync means the clock matched (or was set to) the phone's time at that
moment.  From then on the wall clock must keep moving with the monotonic
clock; if someone changes the clock, or a later sync fails, trust is dropped
until the next successful sync.  Trust lives in memory, so a restart starts
untrusted again.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Callable

MAX_CLOCK_DRIFT = timedelta(seconds=60)


class SyncedTimeSource:
    def __init__(self, wall_now: Callable[[], datetime], monotonic_now: Callable[[], float]) -> None:
        self._wall_now = wall_now
        self._monotonic_now = monotonic_now
        self._lock = threading.Lock()
        self._anchor: tuple[datetime, float] | None = None

    def mark_synced(self) -> None:
        with self._lock:
            self._anchor = (self._wall_now(), self._monotonic_now())

    def revoke(self) -> None:
        with self._lock:
            self._anchor = None

    def is_trusted(self) -> bool:
        with self._lock:
            if self._anchor is None:
                return False
            wall, monotonic = self._anchor
            expected = wall + timedelta(seconds=self._monotonic_now() - monotonic)
            if abs(self._wall_now() - expected) > MAX_CLOCK_DRIFT:
                self._anchor = None
                return False
            return True
