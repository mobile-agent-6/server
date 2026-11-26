"""Single-owner Assistant worker, monotonic deadline sweeper, and recovery."""

from __future__ import annotations

import heapq
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.core.database import is_database_unavailable
from app.modules.assistant import repository
from app.modules.assistant.processor import ProcessOneResult
from app.modules.assistant.recovery import recover_startup_requests as recover_requests
from app.modules.assistant.runtime_failures import (
    interrupted_snapshot,
    timeout_snapshot,
)
from app.modules.assistant.runtime_guard import (
    MonotonicClock,
    ProcessDeadlineGuard,
)
from app.modules.assistant.safe_write import timeout_request

PROCESSING_LIMIT_SECONDS = 300.0
DEFAULT_SHUTDOWN_GRACE_SECONDS = 5.0
_SWEEPER_RETRY_DELAYS = (0.01, 0.05, 0.1, 0.25)
_LAZY_TIMEOUT_LIMIT = 16

logger = logging.getLogger(__name__)


class WallClock(Protocol):
    def now(self) -> datetime: ...


class RuntimeOwnershipLock(Protocol):
    def release(self) -> None: ...


class UnsafeActiveWriteMarker(RuntimeError):
    """Phase 5 cannot infer the outcome of an active write marker."""


class RegistrationOutcome(StrEnum):
    REGISTERED = "registered"
    DUPLICATE = "duplicate"
    SHUTTING_DOWN = "shutting_down"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RuntimeSnapshot:
    owner: bool
    healthy: bool
    accepting: bool
    worker_alive: bool
    sweeper_alive: bool
    current_request_id: UUID | None
    registered_count: int

    @property
    def ready(self) -> bool:
        return self.owner and self.healthy and self.accepting


@dataclass(frozen=True)
class _DeadlineEntry:
    request_id: UUID
    accepted_at: datetime
    deadline: float


class _RetryableTimeoutBusy(RuntimeError):
    pass


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("wall clock must return timezone-aware datetimes")
    return value.astimezone(timezone.utc)


def recover_startup_requests(
    session_factory: sessionmaker[Session], *, wall_clock: WallClock
) -> int:
    """Atomically stop every pre-write request left by an earlier process."""

    recovered_at = _aware_utc(wall_clock.now())
    return recover_requests(session_factory, recovered_at=recovered_at)


def compensate_registration_failure(
    session_factory: sessionmaker[Session],
    *,
    request_id: UUID,
    wall_clock: WallClock,
) -> repository.PrewriteCompletionResult:
    result = interrupted_snapshot()
    interrupted_at = _aware_utc(wall_clock.now())
    with session_factory() as session:
        with session.begin():
            return repository.complete_prewrite_request(
                session,
                request_id=request_id,
                result_json=result,
                at=interrupted_at,
            )


class AssistantRuntimeController:
    """Own all mutable state and background components for one database runtime."""

    def __init__(
        self,
        *,
        ownership_lock: RuntimeOwnershipLock,
        timeout_session_factory: sessionmaker[Session],
        process_request: Callable[[UUID, ProcessDeadlineGuard], ProcessOneResult],
        wall_clock: WallClock,
        monotonic_clock: MonotonicClock,
        cleanup_resources: Callable[[], None],
        shutdown_grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
        condition_waiter: Callable[
            [threading.Condition, float | None], None
        ] | None = None,
        on_unhealthy: Callable[[], None] | None = None,
        cleanup_once: Callable[[], None] | None = None,
        cleanup_interval_seconds: float = 3600.0,
    ) -> None:
        if shutdown_grace_seconds < 0:
            raise ValueError("shutdown grace cannot be negative")
        if cleanup_interval_seconds <= 0:
            raise ValueError("cleanup interval must be positive")
        self._ownership_lock = ownership_lock
        self._timeout_session_factory = timeout_session_factory
        self._process_request = process_request
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._cleanup_callback = cleanup_resources
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._condition_waiter = condition_waiter or self._default_waiter
        self._on_unhealthy = on_unhealthy
        self._periodic_cleanup = cleanup_once
        self._cleanup_interval_seconds = cleanup_interval_seconds

        self._condition = threading.Condition()
        self._shutdown_event = threading.Event()
        self._worker_claim_stop_event = threading.Event()
        self._entries: dict[UUID, _DeadlineEntry] = {}
        self._accepted_heap: list[tuple[datetime, str, UUID]] = []
        self._healthy = False
        self._accepting = False
        self._started = False
        self._current_request_id: UUID | None = None
        self._worker_thread: threading.Thread | None = None
        self._sweeper_thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None
        self._cleanup_done = False
        self._next_cleanup_deadline = (
            self._monotonic_clock.now() + cleanup_interval_seconds
        )

    @staticmethod
    def _default_waiter(
        condition: threading.Condition, timeout: float | None
    ) -> None:
        condition.wait(timeout=timeout)

    def start(self) -> bool:
        with self._condition:
            if self._started:
                raise RuntimeError("Assistant runtime has already started")
            self._started = True
            self._healthy = True
            self._accepting = True
            self._worker_thread = threading.Thread(
                target=lambda: self._thread_entry("worker", self._worker_loop),
                name="assistant-runtime-worker",
                daemon=True,
            )
            self._sweeper_thread = threading.Thread(
                target=lambda: self._thread_entry("sweeper", self._sweeper_loop),
                name="assistant-runtime-sweeper",
                daemon=True,
            )
            worker = self._worker_thread
            sweeper = self._sweeper_thread
        try:
            worker.start()
            sweeper.start()
        except Exception:
            self.mark_unhealthy()
            self._stop_worker_claims()
            return False
        return self.snapshot().ready

    def snapshot(self) -> RuntimeSnapshot:
        with self._condition:
            worker = self._worker_thread
            sweeper = self._sweeper_thread
            return RuntimeSnapshot(
                owner=True,
                healthy=self._healthy,
                accepting=self._accepting,
                worker_alive=worker is not None and worker.is_alive(),
                sweeper_alive=sweeper is not None and sweeper.is_alive(),
                current_request_id=self._current_request_id,
                registered_count=len(self._entries),
            )

    def register_accepted(
        self, *, request_id: UUID, accepted_at: datetime
    ) -> RegistrationOutcome:
        accepted_at = _aware_utc(accepted_at)
        with self._condition:
            if self._shutdown_event.is_set():
                return RegistrationOutcome.SHUTTING_DOWN
            if not self._accepting:
                return RegistrationOutcome.UNAVAILABLE
            if not self._healthy:
                return RegistrationOutcome.UNAVAILABLE
            if request_id in self._entries:
                return RegistrationOutcome.DUPLICATE
            deadline = self._monotonic_clock.now() + PROCESSING_LIMIT_SECONDS
            entry = _DeadlineEntry(
                request_id=request_id,
                accepted_at=accepted_at,
                deadline=deadline,
            )
            self._entries[request_id] = entry
            heapq.heappush(
                self._accepted_heap,
                (accepted_at, str(request_id), request_id),
            )
            self._condition.notify_all()
            return RegistrationOutcome.REGISTERED

    def discard(self, request_id: UUID) -> None:
        with self._condition:
            self._entries.pop(request_id, None)
            self._condition.notify_all()

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def mark_unhealthy(self) -> None:
        callback: Callable[[], None] | None = None
        with self._condition:
            if self._healthy or self._accepting:
                callback = self._on_unhealthy
            self._healthy = False
            self._accepting = False
            self._condition.notify_all()
        if callback is not None:
            try:
                callback()
            except Exception as exc:
                logger.error(
                    "Assistant runtime readiness callback failed: %s",
                    type(exc).__name__,
                    extra={"event": "assistant.runtime_callback_failed"},
                )

    def try_lazy_timeout(self, request_id: UUID | None = None) -> None:
        due = self._due_entries(request_id=request_id, limit=_LAZY_TIMEOUT_LIMIT)
        for entry in due:
            try:
                outcome = self._timeout_once(entry)
            except _RetryableTimeoutBusy:
                continue
            except Exception as exc:
                logger.warning(
                    "Assistant lazy timeout failed: %s",
                    type(exc).__name__,
                    extra={"event": "assistant.lazy_timeout_failed"},
                )
                self.mark_unhealthy()
                continue
            self._apply_timeout_outcome(entry, outcome)

    def _stop_worker_claims(self) -> None:
        with self._condition:
            self._worker_claim_stop_event.set()
            self._condition.notify_all()

    def _thread_entry(self, component: str, target: Callable[[], None]) -> None:
        try:
            target()
        except Exception as exc:
            logger.error(
                "Assistant runtime component failed: %s",
                type(exc).__name__,
                extra={"event": f"assistant.{component}_failed"},
            )
            self.mark_unhealthy()
            if component == "sweeper":
                self._stop_worker_claims()
            return

        expected_exit = self._shutdown_event.is_set() or (
            component == "worker" and self._worker_claim_stop_event.is_set()
        )
        if not expected_exit:
            logger.error(
                "Assistant runtime component exited unexpectedly",
                extra={"event": f"assistant.{component}_exited"},
            )
            self.mark_unhealthy()
            if component == "sweeper":
                self._stop_worker_claims()

    def _next_request(self) -> _DeadlineEntry | None:
        with self._condition:
            while not self._shutdown_event.is_set() and not (
                self._worker_claim_stop_event.is_set()
            ):
                while self._accepted_heap:
                    _, _, request_id = heapq.heappop(self._accepted_heap)
                    entry = self._entries.get(request_id)
                    if entry is None:
                        continue
                    self._current_request_id = request_id
                    return entry
                self._condition_waiter(self._condition, None)
            return None

    def _worker_loop(self) -> None:
        while not self._shutdown_event.is_set() and not (
            self._worker_claim_stop_event.is_set()
        ):
            entry = self._next_request()
            if entry is None:
                return
            guard = ProcessDeadlineGuard(
                monotonic_clock=self._monotonic_clock,
                deadline=entry.deadline,
                stop_signal=self._shutdown_event,
            )
            try:
                outcome = self._process_request(entry.request_id, guard)
            finally:
                with self._condition:
                    self._current_request_id = None
                    self._condition.notify_all()
            if outcome in {
                ProcessOneResult.COMPLETED,
                ProcessOneResult.SKIPPED,
                ProcessOneResult.LOST_FINALIZE,
            }:
                self.discard(entry.request_id)
            elif outcome is not ProcessOneResult.PENDING_WRITE:
                raise RuntimeError("process_one returned an unknown result")

    def _nearest_deadline(self) -> float | None:
        if not self._entries:
            return None
        return min(entry.deadline for entry in self._entries.values())

    def _sweeper_loop(self) -> None:
        retry_index = 0
        while not self._shutdown_event.is_set():
            with self._condition:
                nearest = self._nearest_deadline()
                wake_at = self._next_cleanup_deadline
                if nearest is not None:
                    wake_at = min(wake_at, nearest)
                remaining = wake_at - self._monotonic_clock.now()
                if remaining > 0:
                    self._condition_waiter(self._condition, remaining)
                    continue
            due = self._due_entries(request_id=None, limit=None)
            unhealthy_after_due = False
            for entry in due:
                if self._shutdown_event.is_set():
                    return
                try:
                    outcome = self._timeout_once(entry)
                except _RetryableTimeoutBusy:
                    delay = _SWEEPER_RETRY_DELAYS[
                        min(retry_index, len(_SWEEPER_RETRY_DELAYS) - 1)
                    ]
                    retry_index += 1
                    with self._condition:
                        self._condition_waiter(self._condition, delay)
                    break
                unhealthy_after_due = self._apply_timeout_outcome(
                    entry, outcome, defer_unhealthy=True
                ) or unhealthy_after_due
                retry_index = 0
            if unhealthy_after_due:
                self.mark_unhealthy()
            if self._monotonic_clock.now() >= self._next_cleanup_deadline:
                if self._periodic_cleanup is not None:
                    try:
                        self._periodic_cleanup()
                    except Exception as exc:
                        logger.warning(
                            "Assistant cleanup deferred: %s",
                            type(exc).__name__,
                            extra={"event": "assistant.cleanup_deferred"},
                        )
                self._next_cleanup_deadline = (
                    self._monotonic_clock.now() + self._cleanup_interval_seconds
                )

    def _due_entries(
        self, *, request_id: UUID | None, limit: int | None
    ) -> list[_DeadlineEntry]:
        now = self._monotonic_clock.now()
        with self._condition:
            if request_id is not None:
                entry = self._entries.get(request_id)
                values = [] if entry is None else [entry]
            else:
                values = list(self._entries.values())
        due = sorted(
            (entry for entry in values if entry.deadline <= now),
            key=lambda entry: (entry.deadline, entry.accepted_at, str(entry.request_id)),
        )
        return due if limit is None else due[:limit]

    def _timeout_once(
        self, entry: _DeadlineEntry
    ) -> repository.PrewriteCompletionResult:
        try:
            with self._timeout_session_factory() as session:
                with session.begin():
                    with self._condition:
                        current = self._entries.get(entry.request_id)
                    if current is None or current.deadline != entry.deadline:
                        return repository.PrewriteCompletionResult.NOOP
                    if self._monotonic_clock.now() < entry.deadline:
                        return repository.PrewriteCompletionResult.NOOP
                    return timeout_request(
                        session,
                        request_id=entry.request_id,
                        prewrite_result_json=timeout_snapshot(),
                        at=_aware_utc(self._wall_clock.now()),
                    )
        except Exception as exc:
            if is_database_unavailable(exc):
                raise _RetryableTimeoutBusy from exc
            raise

    def _apply_timeout_outcome(
        self,
        entry: _DeadlineEntry,
        outcome: repository.PrewriteCompletionResult,
        *,
        defer_unhealthy: bool = False,
    ) -> bool:
        if outcome is repository.PrewriteCompletionResult.WRITE_STARTED:
            logger.error(
                "Assistant runtime found an active write marker",
                extra={"event": "assistant.active_write_marker"},
            )
            self.discard(entry.request_id)
            if not defer_unhealthy:
                self.mark_unhealthy()
            return True
        if outcome is repository.PrewriteCompletionResult.COMPLETED:
            with self._condition:
                worker_still_processing = (
                    self._current_request_id == entry.request_id
                )
            self.discard(entry.request_id)
            if worker_still_processing:
                logger.error(
                    "Assistant provider remained active after timeout",
                    extra={"event": "assistant.worker_hung_after_timeout"},
                )
                if not defer_unhealthy:
                    self.mark_unhealthy()
                return True
            return False
        self.discard(entry.request_id)
        return False

    def shutdown(self) -> bool:
        with self._condition:
            self._accepting = False
            self._shutdown_event.set()
            self._condition.notify_all()
            threads = tuple(
                thread
                for thread in (self._worker_thread, self._sweeper_thread)
                if thread is not None
            )

        deadline = time.monotonic() + self._shutdown_grace_seconds
        for thread in threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
        clean = all(not thread.is_alive() for thread in threads)
        if clean:
            self._cleanup_resources()
            return True

        with self._condition:
            if self._cleanup_thread is None:
                self._cleanup_thread = threading.Thread(
                    target=self._deferred_cleanup,
                    name="assistant-runtime-deferred-cleanup",
                    daemon=True,
                )
                self._cleanup_thread.start()
        return False

    def _deferred_cleanup(self) -> None:
        for thread in (self._worker_thread, self._sweeper_thread):
            if thread is not None:
                thread.join()
        self._cleanup_resources()

    def _cleanup_resources(self) -> None:
        with self._condition:
            if self._cleanup_done:
                return
            self._cleanup_done = True
        try:
            self._cleanup_callback()
        finally:
            self._ownership_lock.release()
