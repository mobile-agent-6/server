import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.logging import (
    LOG_BACKUP_COUNT,
    LOG_FILE_MODE,
    LOG_MAX_BYTES,
    JsonFormatter,
    configure_logging,
)


def logging_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "log_format": "json",
        "log_file": tmp_path / "backend.log",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_json_formatter_emits_contract_fields() -> None:
    record = logging.LogRecord(
        "app.test", logging.WARNING, __file__, 1, "failed %s", ("request",), None
    )
    record.event = "request.completed"
    record.requestId = "request-id"
    record.durationMs = 12.5
    record.errorCode = "INTERNAL_ERROR"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["timestamp"].endswith("Z")
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "app.test"
    assert payload["event"] == "request.completed"
    assert payload["message"] == "failed request"
    assert payload["requestId"] == "request-id"
    assert payload["durationMs"] == 12.5
    assert payload["errorCode"] == "INTERNAL_ERROR"


@pytest.mark.integration
def test_logging_configuration_is_idempotent_and_uses_rotation(tmp_path: Path) -> None:
    settings = logging_settings(tmp_path)

    first = configure_logging(settings)
    second = configure_logging(settings)

    owned = [
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_schedule_api_handler", False)
    ]
    assert owned == [second]
    assert first not in logging.getLogger().handlers
    assert isinstance(second, RotatingFileHandler)
    assert second.maxBytes == LOG_MAX_BYTES
    assert second.backupCount == LOG_BACKUP_COUNT

    logger = logging.getLogger("app.logging-test")
    logger.info("ready", extra={"event": "test.ready", "requestId": "request-id"})
    second.flush()

    line = (tmp_path / "backend.log").read_text(encoding="utf-8").strip()
    assert json.loads(line)["event"] == "test.ready"
    assert os.stat(tmp_path / "backend.log").st_mode & 0o777 == LOG_FILE_MODE


def test_uvicorn_logging_does_not_propagate_or_duplicate(tmp_path: Path) -> None:
    previous_handler = logging.StreamHandler()
    logging.getLogger("uvicorn.error").addHandler(previous_handler)
    settings = logging_settings(tmp_path)
    handler = configure_logging(settings)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        assert logger.propagate is False
        assert logger.handlers.count(handler) == 1
        assert previous_handler not in logger.handlers

    assert logging.getLogger("uvicorn.access").disabled is False

    logging.getLogger("uvicorn.error").error("single copy", extra={"event": "test.error"})
    handler.flush()
    lines = (tmp_path / "backend.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_demo_disables_uvicorn_access_log(tmp_path: Path) -> None:
    settings = logging_settings(
        tmp_path,
        **{
            "app_env": "demo",
            "database_url": f"sqlite:///{tmp_path / 'app.db'}",
            "docs_enabled": False,
            "backup_dir": tmp_path / "backups",
        },
    )

    configure_logging(settings)

    assert logging.getLogger("uvicorn.access").disabled is True
