"""FastAPI application factory and ASGI entry point."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved)

    application = FastAPI(
        title="Schedule API",
        version="0.1.0",
        debug=resolved.debug,
        docs_url="/docs" if resolved.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    application.state.settings = resolved
    logging.getLogger(__name__).info(
        "application configured",
        extra={"event": "application.configured"},
    )
    return application


app = create_app()
