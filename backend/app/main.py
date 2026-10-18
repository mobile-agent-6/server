"""FastAPI application factory and ASGI entry point."""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.core.clock import SystemClock, SystemMonotonicClock
from app.core.config import Settings, get_settings
from app.core.database import (
    create_api_engine,
    create_assistant_engine,
    create_session_factory,
)
from app.core.logging import configure_logging
from app.core.middleware import AssistantRequestBodyMiddleware, RequestIdMiddleware
from app.core.problem_details import ProblemBoundaryMiddleware, register_exception_handlers
from app.modules.assistant.router import router as assistant_router
from app.modules.assistant.readiness import probe_assistant_schema
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
    _assistant_new_requests_enabled_for_tests: bool = False,
) -> FastAPI:
    resolved = settings or get_settings()
    if resolved.app_env == "demo" and _assistant_new_requests_enabled_for_tests:
        raise ValueError(
            "Assistant test readiness cannot be enabled in the demo environment"
        )
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        assistant_engine = None
        try:
            assistant_engine = create_assistant_engine(resolved)
            probe_assistant_schema(
                assistant_engine,
                expected_head=application.state.expected_alembic_head,
            )
        except Exception:
            if assistant_engine is not None:
                assistant_engine.dispose()
            logging.getLogger(__name__).warning(
                "Assistant persistence is unavailable",
                extra={"event": "assistant.initialization_unavailable"},
            )
        else:
            application.state.assistant_engine = assistant_engine
            application.state.assistant_session_factory = create_session_factory(
                assistant_engine
            )
            application.state.assistant_schema_ready = True
            application.state.assistant_new_requests_enabled = (
                _assistant_new_requests_enabled_for_tests
            )
        try:
            yield
        finally:
            application.state.assistant_schema_ready = False
            application.state.assistant_new_requests_enabled = False
            engine = application.state.assistant_engine
            application.state.assistant_session_factory = None
            application.state.assistant_engine = None
            if engine is not None:
                engine.dispose()

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
    application.state.monotonic_clock = SystemMonotonicClock()
    application.state.engine = create_api_engine(resolved)
    application.state.session_factory = create_session_factory(application.state.engine)
    application.state.assistant_engine = None
    application.state.assistant_session_factory = None
    application.state.assistant_schema_ready = False
    application.state.assistant_new_requests_enabled = False
    application.state.assistant_uuid_factory = uuid4
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
