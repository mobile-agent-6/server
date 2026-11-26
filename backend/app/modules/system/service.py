"""System health and time synchronization application services."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import SERVICE_ROOT, Settings
from app.core.exceptions import (
    ClockAutoSyncActive,
    ClockTimeOutOfRange,
    DatabaseUnavailable,
    TimeSyncUnavailable,
)
from app.modules.system.schemas import HealthResponse, TimeSyncRequest, TimeSyncResponse
from app.modules.system.time_sync import TimeSyncAdapter, TimeSyncAdapterError

TIME_SYNC_NO_OP_THRESHOLD = timedelta(seconds=60)
TIME_SYNC_PROCESSING_BUDGET_SECONDS = 12.0


class Clock(Protocol):
    def now(self) -> datetime: ...


class MonotonicClock(Protocol):
    def now(self) -> float: ...


class ProcessLock(Protocol):
    def acquire(self, *, timeout: float) -> bool: ...

    def release(self) -> None: ...


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def resolve_expected_head(
    *,
    config_path: Path = SERVICE_ROOT / "config" / "alembic.ini",
    scripts: ScriptDirectory | None = None,
) -> str:
    script_directory = scripts or ScriptDirectory.from_config(Config(str(config_path)))
    heads = tuple(script_directory.get_heads())
    if len(heads) != 1:
        raise RuntimeError("exactly one Alembic migration head is required")
    return heads[0]


def health(
    *, engine: Engine, expected_head: str, clock: Clock
) -> HealthResponse:
    try:
        with engine.connect() as connection:
            current_heads = tuple(
                MigrationContext.configure(connection).get_current_heads()
            )
    except SQLAlchemyError as exc:
        raise DatabaseUnavailable from exc
    if current_heads != (expected_head,):
        raise DatabaseUnavailable
    return HealthResponse(status="ok", api_version="v1", server_time=_utc(clock.now()))


def _in_allowed_range(target: datetime, settings: Settings) -> bool:
    return settings.time_sync_min_utc <= target < settings.time_sync_max_utc


def _elapsed(monotonic_clock: MonotonicClock, started: float) -> float:
    return max(0.0, monotonic_clock.now() - started)


def _remaining_budget(monotonic_clock: MonotonicClock, started: float) -> float:
    return TIME_SYNC_PROCESSING_BUDGET_SECONDS - _elapsed(monotonic_clock, started)


def _operation_timeout(
    *, settings: Settings, monotonic_clock: MonotonicClock, started: float
) -> float:
    remaining = _remaining_budget(monotonic_clock, started)
    if remaining <= 0:
        raise TimeSyncUnavailable
    return min(settings.time_sync_command_timeout_seconds, remaining)


def sync_system_time(
    *,
    data: TimeSyncRequest,
    settings: Settings,
    adapter: TimeSyncAdapter,
    process_lock: ProcessLock,
    monotonic_clock: MonotonicClock,
    request_started_monotonic: float,
) -> TimeSyncResponse:
    requested = _utc(data.current_time)
    if not _in_allowed_range(requested, settings):
        raise ClockTimeOutOfRange

    remaining = _remaining_budget(monotonic_clock, request_started_monotonic)
    lock_timeout = min(settings.time_sync_lock_timeout_seconds, max(0.0, remaining))
    if lock_timeout <= 0 or not process_lock.acquire(timeout=lock_timeout):
        raise TimeSyncUnavailable
    try:
        try:
            target = requested + timedelta(
                seconds=_elapsed(monotonic_clock, request_started_monotonic)
            )
        except OverflowError as exc:
            raise ClockTimeOutOfRange from exc
        if not _in_allowed_range(target, settings):
            raise ClockTimeOutOfRange

        previous = _utc(adapter.read_time())
        if abs(target - previous) <= TIME_SYNC_NO_OP_THRESHOLD:
            return TimeSyncResponse(
                adjusted=False,
                previous_server_time=previous,
                server_time=previous,
            )
        if not settings.time_sync_enabled:
            raise TimeSyncUnavailable

        ntp_active = adapter.is_ntp_active(
            timeout_seconds=_operation_timeout(
                settings=settings,
                monotonic_clock=monotonic_clock,
                started=request_started_monotonic,
            )
        )
        if _remaining_budget(monotonic_clock, request_started_monotonic) < 0:
            raise TimeSyncUnavailable
        if ntp_active:
            raise ClockAutoSyncActive
        adapter.set_time(
            target,
            timeout_seconds=_operation_timeout(
                settings=settings,
                monotonic_clock=monotonic_clock,
                started=request_started_monotonic,
            ),
        )
        if _remaining_budget(monotonic_clock, request_started_monotonic) < 0:
            raise TimeSyncUnavailable
        current = _utc(adapter.read_time())
        return TimeSyncResponse(
            adjusted=True,
            previous_server_time=previous,
            server_time=current,
        )
    except TimeSyncAdapterError as exc:
        raise TimeSyncUnavailable from exc
    finally:
        process_lock.release()
