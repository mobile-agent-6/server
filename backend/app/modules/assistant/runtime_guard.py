"""Deadline and shutdown guard passed explicitly into Assistant processing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class MonotonicClock(Protocol):
    def now(self) -> float: ...


class StopSignal(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True)
class ProcessDeadlineGuard:
    monotonic_clock: MonotonicClock
    deadline: float
    stop_signal: StopSignal

    def deadline_reached(self) -> bool:
        return self.monotonic_clock.now() >= self.deadline

    def stop_requested(self) -> bool:
        return self.stop_signal.is_set()
