from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.clock import FixedClock, FixedMonotonicClock
from app.modules.system.time_trust import SyncedTimeSource

NOW = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)


def _source() -> tuple[SyncedTimeSource, FixedClock, FixedMonotonicClock]:
    wall = FixedClock(NOW)
    monotonic = FixedMonotonicClock(100.0)
    return SyncedTimeSource(wall.now, monotonic.now), wall, monotonic


def test_untrusted_until_synced() -> None:
    source, _, _ = _source()
    assert source.is_trusted() is False
    source.mark_synced()
    assert source.is_trusted() is True


def test_stays_trusted_while_clock_moves_normally() -> None:
    source, wall, monotonic = _source()
    source.mark_synced()
    monotonic.advance(3 * 3600)
    wall.advance(timedelta(hours=3, seconds=30))
    assert source.is_trusted() is True


def test_clock_change_after_sync_drops_trust() -> None:
    source, wall, monotonic = _source()
    source.mark_synced()
    monotonic.advance(10)
    wall.advance(timedelta(days=1))
    assert source.is_trusted() is False
    wall.set(NOW + timedelta(seconds=10))
    assert source.is_trusted() is False  # needs a new sync


def test_revoke_drops_trust() -> None:
    source, _, _ = _source()
    source.mark_synced()
    source.revoke()
    assert source.is_trusted() is False
