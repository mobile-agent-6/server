from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import DEFAULT_ENV_FILE, SERVICE_ROOT, Settings, load_settings


def demo_values(tmp_path: Path) -> dict[str, object]:
    return {
        "app_env": "demo",
        "database_url": f"sqlite:///{tmp_path / 'app.db'}",
        "docs_enabled": False,
        "log_format": "json",
        "log_file": tmp_path / "backend.log",
        "backup_dir": tmp_path / "backups",
    }


def test_development_defaults_are_safe_and_paths_are_stable() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.database_path == (SERVICE_ROOT / "data/app.db").resolve()
    assert settings.debug is False
    assert settings.docs_enabled is True
    assert settings.time_sync_enabled is False


def test_shared_env_example_loads_as_development_configuration() -> None:
    settings = load_settings(env_file=SERVICE_ROOT / "config" / "env.example")

    assert settings.app_env == "development"
    assert settings.log_file is None
    assert settings.database_path == (SERVICE_ROOT / "data/app.db").resolve()
    assert DEFAULT_ENV_FILE == SERVICE_ROOT / ".env"


def test_relative_paths_are_resolved_from_backend_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir("/")

    settings = Settings(
        _env_file=None,
        database_url="sqlite:///custom/app.db",
        backup_dir="custom/backups",
        log_file="custom/backend.log",
    )

    assert settings.database_path == (SERVICE_ROOT / "custom/app.db").resolve()
    assert settings.backup_dir == (SERVICE_ROOT / "custom/backups").resolve()
    assert settings.log_file == (SERVICE_ROOT / "custom/backend.log").resolve()


def test_valid_demo_configuration_is_accepted(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, **demo_values(tmp_path))

    assert settings.app_env == "demo"
    assert settings.database_path == tmp_path / "app.db"
    assert settings.log_file == tmp_path / "backend.log"


def test_demo_does_not_fall_back_to_development_paths(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="explicit absolute database URL"):
        Settings(
            _env_file=None,
            app_env="demo",
            docs_enabled=False,
            log_format="json",
            log_file=tmp_path / "backend.log",
            backup_dir=tmp_path / "backups",
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"debug": True}, "debug must be false"),
        ({"docs_enabled": True}, "docs must be disabled"),
        ({"log_format": "console"}, "log format must be json"),
        ({"log_file": None}, "absolute log file is required"),
    ],
)
def test_invalid_demo_configuration_fails_before_startup(
    tmp_path: Path, override: dict[str, object], message: str
) -> None:
    values = demo_values(tmp_path)
    values.update(override)

    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **values)


def test_invalid_port_and_time_sync_range_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_port=0)

    with pytest.raises(ValidationError, match="minimum must be earlier"):
        Settings(
            _env_file=None,
            time_sync_min_utc=datetime(2035, 1, 1, tzinfo=timezone.utc),
            time_sync_max_utc=datetime(2025, 1, 1, tzinfo=timezone.utc),
        )


def test_unknown_schedule_environment_variable_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SCHEDULE_LOG_LEVL", "INFO")

    with pytest.raises(ValueError, match="SCHEDULE_LOG_LEVL"):
        load_settings(env_file=None)


def test_uppercase_environment_names_are_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCHEDULE_API_PORT", "9000")

    assert load_settings(env_file=None).api_port == 9000


def test_demo_configuration_loads_from_uppercase_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = {
        "SCHEDULE_APP_ENV": "demo",
        "SCHEDULE_DATABASE_URL": f"sqlite:///{tmp_path / 'app.db'}",
        "SCHEDULE_DOCS_ENABLED": "false",
        "SCHEDULE_LOG_FORMAT": "json",
        "SCHEDULE_LOG_FILE": str(tmp_path / "backend.log"),
        "SCHEDULE_BACKUP_DIR": str(tmp_path / "backups"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = load_settings(env_file=None)

    assert settings.app_env == "demo"
    assert settings.database_path == tmp_path / "app.db"


def test_unknown_schedule_name_in_dotenv_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SCHEDULE_LOG_LEVL=INFO\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_settings(env_file=env_file)


def test_exact_cors_origins_are_normalized() -> None:
    settings = Settings(
        _env_file=None,
        cors_origins=["http://localhost:8081/", "https://example.invalid"],
    )
    assert settings.cors_origins == ["http://localhost:8081", "https://example.invalid"]

    with pytest.raises(ValidationError, match="exact HTTP"):
        Settings(_env_file=None, cors_origins=["https://example.invalid/path"])
