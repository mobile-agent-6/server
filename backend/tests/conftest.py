from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

# Tests never read the developer's SCHEDULE_* settings.  This runs before
# app.main is imported, whose module-level app would otherwise open the
# configured log file or database.
for _name in [key for key in os.environ if key.startswith("SCHEDULE_")]:
    del os.environ[_name]

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

from app.core.clock import FixedClock
from app.core.config import Settings
from app.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FIXED_NOW = datetime(2026, 9, 13, 4, 30, tzinfo=timezone.utc)


def alembic_config(database_path: Path) -> Config:
    config = Config(str(BACKEND_ROOT / "config" / "alembic.ini"))
    config.attributes["database_url"] = f"sqlite:///{database_path.as_posix()}"
    return config


def migrate(database_path: Path) -> None:
    command.upgrade(alembic_config(database_path), "head")


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "app.db"
    migrate(path)
    return path


@pytest.fixture
def application(database_path: Path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{database_path}")
    app = create_app(settings)
    app.state.clock = FixedClock(FIXED_NOW)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(application) -> Iterator[TestClient]:
    with TestClient(application, raise_server_exceptions=False) as test_client:
        yield test_client
