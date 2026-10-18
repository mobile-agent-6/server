from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app, create_app


def test_module_exposes_importable_app() -> None:
    assert app.title == "Schedule API"
    assert app.state.settings.app_env == "development"


def test_development_app_starts_with_docs_and_openapi() -> None:
    application = create_app(Settings(_env_file=None))

    assert application.state.assistant_schema_ready is False
    assert application.state.assistant_new_requests_enabled is False

    with TestClient(application) as client:
        assert client.get("/docs").status_code == 200
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Schedule API"


def test_demo_rejects_test_only_assistant_readiness(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        app_env="demo",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        docs_enabled=False,
        log_format="json",
        log_file=tmp_path / "backend.log",
        backup_dir=tmp_path / "backups",
    )

    with pytest.raises(ValueError, match="test readiness"):
        create_app(
            settings,
            _assistant_new_requests_enabled_for_tests=True,
        )


def test_demo_app_disables_docs_but_keeps_openapi(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        app_env="demo",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        docs_enabled=False,
        log_format="json",
        log_file=tmp_path / "backend.log",
        backup_dir=tmp_path / "backups",
    )
    application = create_app(settings)

    with TestClient(application) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 200
