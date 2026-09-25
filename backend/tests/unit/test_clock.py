from datetime import datetime, timedelta, timezone

import pytest

from app.core.clock import FixedClock, FixedMonotonicClock, SystemClock, SystemMonotonicClock


def test_system_clock_returns_utc_aware_time() -> None:
    current = SystemClock().now()

    assert current.tzinfo is timezone.utc
    assert current.utcoffset() == timedelta(0)


def test_fixed_clock_normalizes_to_utc_and_can_advance() -> None:
    clock = FixedClock(datetime(2026, 9, 13, 13, 30, tzinfo=timezone(timedelta(hours=9))))

    assert clock.now() == datetime(2026, 9, 13, 4, 30, tzinfo=timezone.utc)
    clock.advance(timedelta(seconds=2))
    assert clock.now() == datetime(2026, 9, 13, 4, 30, 2, tzinfo=timezone.utc)


def test_fixed_clock_rejects_naive_values() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FixedClock(datetime(2026, 9, 13, 4, 30))


def test_monotonic_clocks_support_elapsed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.clock.time.monotonic", lambda: 12.5)
    assert SystemMonotonicClock().now() == 12.5

    fixed = FixedMonotonicClock(3.0)
    fixed.advance(0.25)
    assert fixed.now() == 3.25
    with pytest.raises(ValueError, match="backwards"):
        fixed.advance(-0.01)
