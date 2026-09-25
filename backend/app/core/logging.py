"""Idempotent application and Uvicorn logging configuration."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.core.config import Settings

LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 2
LOG_FILE_MODE = 0o640
_OWNED_HANDLER = "_schedule_api_handler"
_JSON_FIELDS = (
    "requestId",
    "method",
    "path",
    "status",
    "durationMs",
    "errorCode",
    "operation",
    "scheduleId",
    "title",
    "scheduledAt",
    "category",
    "idempotencyKey",
    "previousServerTime",
    "requestedClientTime",
    "resultServerTime",
    "adjusted",
    "clientIp",
)


class JsonFormatter(logging.Formatter):
    """Render stable, single-line JSON without serializing arbitrary record data."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, timezone.utc)
        payload: dict[str, Any] = {
            "timestamp": timestamp.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": record.getMessage(),
        }
        for field in _JSON_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


class RestrictedRotatingFileHandler(RotatingFileHandler):
    """Create application log files with the documented 0640 mode."""

    def _open(self):  # type: ignore[no-untyped-def]
        stream = super()._open()
        os.chmod(self.baseFilename, LOG_FILE_MODE)
        return stream


def _formatter(settings: Settings) -> logging.Formatter:
    if settings.log_format == "json":
        return JsonFormatter()
    return logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _handler(settings: Settings) -> logging.Handler:
    if settings.log_file is None:
        handler: logging.Handler = logging.StreamHandler()
    else:
        handler = RestrictedRotatingFileHandler(
            Path(settings.log_file),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    setattr(handler, _OWNED_HANDLER, True)
    handler.setFormatter(_formatter(settings))
    return handler


def _remove_previous_handlers(
    root: logging.Logger, uvicorn_loggers: tuple[logging.Logger, ...]
) -> None:
    removed: set[logging.Handler] = set()
    for handler in list(root.handlers):
        if getattr(handler, _OWNED_HANDLER, False):
            root.removeHandler(handler)
            removed.add(handler)
    for logger in uvicorn_loggers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            removed.add(handler)
    for handler in removed:
        handler.close()


def configure_logging(settings: Settings) -> logging.Handler:
    """Configure one shared handler and prevent Uvicorn propagation duplicates."""

    root = logging.getLogger()
    uvicorn = logging.getLogger("uvicorn")
    uvicorn_error = logging.getLogger("uvicorn.error")
    uvicorn_access = logging.getLogger("uvicorn.access")
    uvicorn_loggers = (uvicorn, uvicorn_error, uvicorn_access)
    _remove_previous_handlers(root, uvicorn_loggers)

    level = getattr(logging, settings.log_level)
    handler = _handler(settings)
    root.setLevel(level)
    root.addHandler(handler)

    for logger in uvicorn_loggers:
        logger.setLevel(level)
        logger.propagate = False
        logger.addHandler(handler)

    uvicorn_access.disabled = settings.app_env == "demo"
    return handler
