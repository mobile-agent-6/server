"""FastAPI application factory and ASGI entry point."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.core.clock import SystemClock, SystemMonotonicClock
from app.core.config import Settings, get_settings
from app.core.database import (
    create_api_engine,
    create_assistant_engine,
    create_assistant_probe_engine,
    create_session_factory,
)
from app.core.logging import configure_logging
from app.core.middleware import AssistantRequestBodyMiddleware, RequestIdMiddleware
from app.core.problem_details import ProblemBoundaryMiddleware, register_exception_handlers
from app.modules.assistant.decisions import (
    DecisionProvider,
    TimeTrustSource,
    UntrustedTimeSource,
)
from app.modules.assistant.cleanup import cleanup_once
from app.modules.assistant.router import router as assistant_router
from app.modules.assistant.readiness import probe_assistant_schema
from app.modules.assistant.processor import ProcessOneResult, process_one
from app.modules.assistant.runtime import (
    AssistantRuntimeController,
    DEFAULT_SHUTDOWN_GRACE_SECONDS,
    RuntimeOwnershipLock,
    UnsafeActiveWriteMarker,
    recover_startup_requests,
)
from app.modules.assistant.runtime_guard import MonotonicClock, ProcessDeadlineGuard
from app.modules.assistant.runtime_lock import (
    AssistantRuntimeFileLock,
    AssistantRuntimeLockContended,
    AssistantRuntimeLockUnavailable,
)
from app.modules.assistant.safe_write import AssistantSafeWriter
from app.modules.assistant.schedule_read import SqlAlchemyScheduleReader
from app.modules.schedules.router import router as schedules_router
from app.modules.system.router import router as system_router
from app.modules.system.service import resolve_expected_head
from app.modules.system.time_sync import DisabledTimeSyncAdapter


def _install_openapi_contract(application: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if application.openapi_schema is not None:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            openapi_version="3.1.0",
            routes=application.routes,
        )
        request_id_header = {
            "description": "Server-generated request UUID",
            "schema": {"type": "string", "format": "uuid"},
        }
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict) or "responses" not in operation:
                    continue
                for status_code, response in operation["responses"].items():
                    response.setdefault("headers", {})["X-Request-ID"] = request_id_header
                    if str(status_code).startswith(("4", "5")):
                        response["content"] = {
                            "application/problem+json": {
                                "schema": {"$ref": "#/components/schemas/Problem"}
                            }
                        }
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        application.openapi_schema = schema
        return schema

    application.openapi = custom_openapi  # type: ignore[method-assign]


def create_app(
    settings: Settings | None = None,
    *,
    _assistant_runtime_ready_for_tests: bool | None = None,
    _assistant_decision_provider_for_tests: DecisionProvider | None = None,
    _assistant_time_trust_source_for_tests: TimeTrustSource | None = None,
    _assistant_uuid_factory_for_tests: Callable[[], UUID] | None = None,
    _assistant_runtime_lock_factory_for_tests: (
        Callable[[Path], RuntimeOwnershipLock] | None
    ) = None,
    _assistant_monotonic_clock_for_tests: MonotonicClock | None = None,
    _assistant_process_one_for_tests: (
        Callable[[UUID, ProcessDeadlineGuard], ProcessOneResult] | None
    ) = None,
    _assistant_shutdown_grace_seconds_for_tests: float | None = None,
    _assistant_condition_waiter_for_tests: (
        Callable[[threading.Condition, float | None], None] | None
    ) = None,
) -> FastAPI:
    resolved = settings or get_settings()
    if resolved.app_env == "demo" and (
        _assistant_runtime_ready_for_tests is not None
        or _assistant_decision_provider_for_tests is not None
        or _assistant_time_trust_source_for_tests is not None
        or _assistant_uuid_factory_for_tests is not None
        or _assistant_runtime_lock_factory_for_tests is not None
        or _assistant_monotonic_clock_for_tests is not None
        or _assistant_process_one_for_tests is not None
        or _assistant_shutdown_grace_seconds_for_tests is not None
        or _assistant_condition_waiter_for_tests is not None
    ):
        raise ValueError(
            "Assistant test-only dependencies cannot be enabled in the demo environment"
        )
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        probe_engine = None
        assistant_engine = None
        timeout_engine = None
        ownership_lock: RuntimeOwnershipLock | None = None
        runtime: AssistantRuntimeController | None = None
        try:
            probe_engine = create_assistant_probe_engine(resolved)
            probe_assistant_schema(
                probe_engine,
                expected_head=application.state.expected_alembic_head,
            )
        except Exception:
            logging.getLogger(__name__).warning(
                "Assistant persistence is unavailable",
                extra={"event": "assistant.initialization_unavailable"},
            )
        else:
            application.state.assistant_schema_ready = True
            lock_factory = (
                _assistant_runtime_lock_factory_for_tests
                or AssistantRuntimeFileLock.acquire
            )
            try:
                ownership_lock = lock_factory(resolved.database_path)
            except AssistantRuntimeLockContended:
                logging.getLogger(__name__).warning(
                    "Assistant runtime ownership is contended",
                    extra={"event": "assistant.runtime_lock_contended"},
                )
            except (AssistantRuntimeLockUnavailable, OSError) as exc:
                logging.getLogger(__name__).warning(
                    "Assistant runtime ownership failed: %s",
                    type(exc).__name__,
                    extra={"event": "assistant.runtime_lock_failed"},
                )

            assistant_engine = create_assistant_engine(resolved)
            application.state.assistant_engine = assistant_engine
            application.state.assistant_session_factory = create_session_factory(
                assistant_engine
            )
            application.state.assistant_decision_provider = (
                _assistant_decision_provider_for_tests
            )
            application.state.assistant_provider_available = (
                _assistant_decision_provider_for_tests is not None
            )
            if ownership_lock is not None:
                try:
                    timeout_engine = create_assistant_engine(
                        resolved, busy_timeout_ms=0
                    )
                    recover_startup_requests(
                        application.state.assistant_session_factory,
                        wall_clock=application.state.clock,
                    )
                except UnsafeActiveWriteMarker:
                    logging.getLogger(__name__).error(
                        "Assistant runtime found an active write marker",
                        extra={"event": "assistant.active_write_marker"},
                    )
                    if timeout_engine is not None:
                        timeout_engine.dispose()
                        timeout_engine = None
                    ownership_lock.release()
                    ownership_lock = None
                except Exception as exc:
                    logging.getLogger(__name__).error(
                        "Assistant startup recovery failed: %s",
                        type(exc).__name__,
                        extra={"event": "assistant.recovery_failed"},
                    )
                    if timeout_engine is not None:
                        timeout_engine.dispose()
                        timeout_engine = None
                    ownership_lock.release()
                    ownership_lock = None
                else:
                    timeout_session_factory = create_session_factory(timeout_engine)

                    try:
                        cleanup_once(
                            application.state.assistant_session_factory,
                            wall_clock=application.state.clock,
                        )
                    except Exception as exc:
                        logging.getLogger(__name__).warning(
                            "Assistant startup cleanup deferred: %s",
                            type(exc).__name__,
                            extra={"event": "assistant.cleanup_deferred"},
                        )

                    def mark_safe_writer_unhealthy() -> None:
                        active_runtime = application.state.assistant_runtime
                        if isinstance(active_runtime, AssistantRuntimeController):
                            active_runtime.mark_unhealthy()
                        else:
                            application.state.assistant_runtime_ready = False
                            application.state.assistant_runtime_healthy = False

                    safe_writer = AssistantSafeWriter(
                        assistant_session_factory=(
                            application.state.assistant_session_factory
                        ),
                        schedule_session_factory=application.state.session_factory,
                        clock=application.state.clock,
                        uuid_factory=application.state.assistant_uuid_factory,
                        on_unhealthy=mark_safe_writer_unhealthy,
                    )

                    def run_process_one(
                        request_id: UUID, guard: ProcessDeadlineGuard
                    ) -> ProcessOneResult:
                        if _assistant_process_one_for_tests is not None:
                            return _assistant_process_one_for_tests(request_id, guard)
                        return process_one(
                            application.state.assistant_session_factory,
                            request_id=request_id,
                            provider=application.state.assistant_decision_provider,
                            schedule_reader=SqlAlchemyScheduleReader(
                                application.state.session_factory
                            ),
                            clock=application.state.clock,
                            time_trust_source=(
                                application.state.assistant_time_trust_source
                            ),
                            uuid_factory=application.state.assistant_uuid_factory,
                            runtime_guard=guard,
                            safe_writer=safe_writer,
                        )

                    def mark_runtime_unready() -> None:
                        application.state.assistant_runtime_ready = False
                        application.state.assistant_runtime_healthy = False

                    def cleanup_runtime_resources() -> None:
                        assert timeout_engine is not None
                        timeout_engine.dispose()
                        assert assistant_engine is not None
                        assistant_engine.dispose()

                    runtime = AssistantRuntimeController(
                        ownership_lock=ownership_lock,
                        timeout_session_factory=timeout_session_factory,
                        process_request=run_process_one,
                        wall_clock=application.state.clock,
                        monotonic_clock=application.state.monotonic_clock,
                        cleanup_resources=cleanup_runtime_resources,
                        shutdown_grace_seconds=(
                            DEFAULT_SHUTDOWN_GRACE_SECONDS
                            if _assistant_shutdown_grace_seconds_for_tests is None
                            else _assistant_shutdown_grace_seconds_for_tests
                        ),
                        condition_waiter=_assistant_condition_waiter_for_tests,
                        on_unhealthy=mark_runtime_unready,
                        cleanup_once=lambda: cleanup_once(
                            application.state.assistant_session_factory,
                            wall_clock=application.state.clock,
                        ),
                    )
                    application.state.assistant_runtime = runtime
                    started = runtime.start()
                    application.state.assistant_runtime_owner = True
                    application.state.assistant_runtime_healthy = started
                    application.state.assistant_runtime_ready = bool(
                        started and _assistant_runtime_ready_for_tests is not False
                    )
        finally:
            if probe_engine is not None:
                probe_engine.dispose()
        try:
            yield
        finally:
            application.state.assistant_schema_ready = False
            application.state.assistant_runtime_ready = False
            application.state.assistant_provider_available = False
            application.state.assistant_decision_provider = None
            application.state.assistant_session_factory = None
            application.state.assistant_engine = None
            application.state.assistant_runtime_owner = False
            application.state.assistant_runtime_healthy = False
            if runtime is not None:
                runtime.shutdown()
            elif assistant_engine is not None:
                assistant_engine.dispose()
            application.state.assistant_runtime = None

    application = FastAPI(
        title="Schedule API",
        version="0.1.0",
        debug=resolved.debug,
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.clock = SystemClock()
    application.state.monotonic_clock = (
        _assistant_monotonic_clock_for_tests or SystemMonotonicClock()
    )
    application.state.engine = create_api_engine(resolved)
    application.state.session_factory = create_session_factory(application.state.engine)
    application.state.assistant_engine = None
    application.state.assistant_session_factory = None
    application.state.assistant_schema_ready = False
    application.state.assistant_runtime_ready = False
    application.state.assistant_provider_available = False
    application.state.assistant_decision_provider = None
    application.state.assistant_runtime = None
    application.state.assistant_runtime_owner = False
    application.state.assistant_runtime_healthy = False
    application.state.assistant_time_trust_source = (
        _assistant_time_trust_source_for_tests or UntrustedTimeSource()
    )
    application.state.assistant_uuid_factory = (
        _assistant_uuid_factory_for_tests or uuid4
    )
    application.state.expected_alembic_head = resolve_expected_head()
    application.state.time_sync_lock = threading.Lock()
    application.state.time_sync_adapter = DisabledTimeSyncAdapter(
        application.state.clock
    )

    application.add_middleware(ProblemBoundaryMiddleware)
    application.add_middleware(AssistantRequestBodyMiddleware)
    application.add_middleware(RequestIdMiddleware)
    if resolved.cors_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID", "Location", "Idempotent-Replayed"],
        )

    register_exception_handlers(application)
    application.include_router(assistant_router)
    application.include_router(schedules_router)
    application.include_router(system_router)
    _install_openapi_contract(application)
    logging.getLogger(__name__).info(
        "application configured",
        extra={"event": "application.configured"},
    )
    return application


app = create_app()
