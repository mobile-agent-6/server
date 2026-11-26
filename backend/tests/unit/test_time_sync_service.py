from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.core.clock import FixedClock, FixedMonotonicClock
from app.core.config import Settings
from app.core.exceptions import (
    ClockAutoSyncActive,
    ClockTimeOutOfRange,
    TimeSyncUnavailable,
)
from app.modules.system.schemas import TimeSyncRequest
from app.modules.system.service import sync_system_time
from app.modules.system.time_sync import DisabledTimeSyncAdapter, TimeSyncAdapterError

NOW = datetime(2026, 9, 13, 4, 30, tzinfo=timezone.utc)


class FakeTimeSyncAdapter:
    def __init__(
        self,
        current: datetime,
        *,
        ntp_active: bool = False,
        failure: str | None = None,
        monotonic: FixedMonotonicClock | None = None,
        ntp_delay: float = 0,
        set_delay: float = 0,
    ) -> None:
        self.current = current
        self.ntp_active = ntp_active
        self.failure = failure
        self.monotonic = monotonic
        self.ntp_delay = ntp_delay
        self.set_delay = set_delay
        self.calls: list[object] = []
        self.timeouts: list[float] = []

    def read_time(self) -> datetime:
        self.calls.append("read")
        if self.failure == "read":
            raise TimeSyncAdapterError("read failed")
        return self.current

    def is_ntp_active(self, *, timeout_seconds: float) -> bool:
        self.calls.append("ntp")
        self.timeouts.append(timeout_seconds)
        if self.monotonic:
            self.monotonic.advance(self.ntp_delay)
        if self.failure == "ntp":
            raise TimeSyncAdapterError("NTP failed")
        return self.ntp_active

    def set_time(self, target: datetime, *, timeout_seconds: float) -> None:
        self.calls.append(("set", target))
        self.timeouts.append(timeout_seconds)
        if self.monotonic:
            self.monotonic.advance(self.set_delay)
        if self.failure == "set":
            raise TimeSyncAdapterError("set failed")
        self.current = target


class AdvancingLock:
    def __init__(self, monotonic: FixedMonotonicClock, elapsed: float) -> None:
        self.monotonic = monotonic
        self.elapsed = elapsed
        self.released = False

    def acquire(self, *, timeout: float) -> bool:
        self.monotonic.advance(self.elapsed)
        return True

    def release(self) -> None:
        self.released = True


def request(current: datetime = NOW) -> TimeSyncRequest:
    return TimeSyncRequest.model_validate({"currentTime": current.isoformat()})


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)


def synchronize(
    adapter: FakeTimeSyncAdapter,
    *,
    configured: Settings | None = None,
    monotonic: FixedMonotonicClock | None = None,
    process_lock: object | None = None,
    data: TimeSyncRequest | None = None,
):
    clock = monotonic or FixedMonotonicClock()
    return sync_system_time(
        data=data or request(),
        settings=configured or settings(),
        adapter=adapter,
        process_lock=process_lock or threading.Lock(),
        monotonic_clock=clock,
        request_started_monotonic=0,
    )


@pytest.mark.parametrize("difference", [59, 60])
def test_59_and_60_second_differences_are_no_op(difference: int) -> None:
    adapter = FakeTimeSyncAdapter(NOW - timedelta(seconds=difference), ntp_active=True)

    result = synchronize(adapter)

    assert result.adjusted is False
    assert result.previous_server_time == adapter.current
    assert result.server_time == adapter.current
    assert adapter.calls == ["read"]


def test_61_second_difference_checks_ntp_sets_and_rereads() -> None:
    previous = NOW - timedelta(seconds=61)
    adapter = FakeTimeSyncAdapter(previous)

    result = synchronize(adapter, configured=settings(time_sync_enabled=True))

    assert result.adjusted is True
    assert result.previous_server_time == previous
    assert result.server_time == NOW
    assert adapter.calls == ["read", "ntp", ("set", NOW), "read"]
    assert adapter.timeouts == [5, 5]


def test_ntp_active_prevents_manual_change() -> None:
    adapter = FakeTimeSyncAdapter(NOW - timedelta(seconds=61), ntp_active=True)

    with pytest.raises(ClockAutoSyncActive):
        synchronize(adapter, configured=settings(time_sync_enabled=True))

    assert adapter.calls == ["read", "ntp"]


def test_disabled_change_fails_before_ntp_check() -> None:
    adapter = FakeTimeSyncAdapter(NOW - timedelta(seconds=61), ntp_active=True)

    with pytest.raises(TimeSyncUnavailable):
        synchronize(adapter)

    assert adapter.calls == ["read"]


def test_allowed_range_is_lower_inclusive_and_upper_exclusive() -> None:
    lower = datetime(2025, 1, 1, tzinfo=timezone.utc)
    upper = datetime(2035, 1, 1, tzinfo=timezone.utc)
    accepted = synchronize(
        FakeTimeSyncAdapter(lower),
        data=request(lower),
    )
    assert accepted.adjusted is False

    for value in (lower - timedelta(microseconds=1), upper, upper + timedelta(days=1)):
        with pytest.raises(ClockTimeOutOfRange):
            synchronize(FakeTimeSyncAdapter(value), data=request(value))


def test_lock_wait_adjusts_target_and_rechecks_range() -> None:
    monotonic = FixedMonotonicClock()
    lock = AdvancingLock(monotonic, elapsed=2)
    adapter = FakeTimeSyncAdapter(NOW - timedelta(minutes=5))

    result = synchronize(
        adapter,
        configured=settings(time_sync_enabled=True),
        monotonic=monotonic,
        process_lock=lock,
    )

    assert result.server_time == NOW + timedelta(seconds=2)
    assert adapter.calls[2] == ("set", NOW + timedelta(seconds=2))
    assert lock.released is True

    upper = datetime(2035, 1, 1, tzinfo=timezone.utc)
    boundary_monotonic = FixedMonotonicClock()
    with pytest.raises(ClockTimeOutOfRange):
        synchronize(
            FakeTimeSyncAdapter(upper - timedelta(minutes=5)),
            monotonic=boundary_monotonic,
            process_lock=AdvancingLock(boundary_monotonic, elapsed=2),
            data=request(upper - timedelta(seconds=1)),
        )


def test_lock_timeout_releases_no_lock_and_lock_remains_usable() -> None:
    lock = threading.Lock()
    lock.acquire()
    with pytest.raises(TimeSyncUnavailable):
        synchronize(
            FakeTimeSyncAdapter(NOW),
            configured=settings(time_sync_lock_timeout_seconds=0.001),
            process_lock=lock,
        )
    lock.release()

    result = synchronize(FakeTimeSyncAdapter(NOW), process_lock=lock)
    assert result.adjusted is False


@pytest.mark.parametrize("failure", ["read", "ntp", "set"])
def test_adapter_failures_map_to_unavailable(failure: str) -> None:
    adapter = FakeTimeSyncAdapter(
        NOW - timedelta(seconds=61),
        failure=failure,
    )

    with pytest.raises(TimeSyncUnavailable):
        synchronize(adapter, configured=settings(time_sync_enabled=True))


def test_two_concurrent_requests_change_once_then_no_op() -> None:
    adapter = FakeTimeSyncAdapter(NOW - timedelta(minutes=5))
    process_lock = threading.Lock()
    start = threading.Barrier(2)

    def run():
        start.wait(timeout=5)
        return synchronize(
            adapter,
            configured=settings(time_sync_enabled=True),
            process_lock=process_lock,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))

    assert sorted(result.adjusted for result in results) == [False, True]
    assert sum(1 for call in adapter.calls if isinstance(call, tuple)) == 1


def test_command_budget_never_exceeds_two_actions_or_12_seconds() -> None:
    monotonic = FixedMonotonicClock()
    adapter = FakeTimeSyncAdapter(
        NOW - timedelta(minutes=5),
        monotonic=monotonic,
        ntp_delay=5,
        set_delay=5,
    )

    result = synchronize(
        adapter,
        configured=settings(time_sync_enabled=True),
        monotonic=monotonic,
        process_lock=AdvancingLock(monotonic, elapsed=2),
    )

    assert result.adjusted is True
    assert monotonic.now() == 12
    assert adapter.timeouts == [5, 5]
    assert adapter.calls.count("ntp") == 1
    assert sum(1 for call in adapter.calls if isinstance(call, tuple)) == 1

    over_budget_clock = FixedMonotonicClock()
    over_budget = FakeTimeSyncAdapter(
        NOW - timedelta(minutes=5),
        monotonic=over_budget_clock,
        ntp_delay=5.1,
        set_delay=5,
    )
    with pytest.raises(TimeSyncUnavailable):
        synchronize(
            over_budget,
            configured=settings(time_sync_enabled=True),
            monotonic=over_budget_clock,
            process_lock=AdvancingLock(over_budget_clock, elapsed=2),
        )
    assert over_budget_clock.now() > 12


def test_development_adapter_can_only_read_injected_clock() -> None:
    adapter = DisabledTimeSyncAdapter(FixedClock(NOW))

    assert adapter.read_time() == NOW
    with pytest.raises(TimeSyncAdapterError):
        adapter.is_ntp_active(timeout_seconds=1)
    with pytest.raises(TimeSyncAdapterError):
        adapter.set_time(NOW, timeout_seconds=1)
