"""Typed development and demo environment configuration."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = SERVICE_ROOT / ".env"
ENV_PREFIX = "SCHEDULE_"


class Settings(BaseSettings):
    """Validated settings shared by the app factory and future commands."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    app_env: Literal["development", "demo"] = "development"
    database_url: str = "sqlite:///data/app.db"
    api_host: str = "0.0.0.0"
    api_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    debug: bool = False
    docs_enabled: bool = True
    cors_origins: list[str] = Field(default_factory=list)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["console", "json"] = "console"
    log_file: Path | None = None
    sqlite_busy_timeout_ms: Annotated[int, Field(gt=0)] = 5000
    db_pool_timeout_seconds: Annotated[float, Field(gt=0)] = 5
    backup_dir: Path = Path("data/backups")
    backup_keep: Annotated[int, Field(ge=1)] = 5
    time_sync_enabled: bool = False
    time_sync_min_utc: datetime = datetime(2025, 1, 1, tzinfo=timezone.utc)
    time_sync_max_utc: datetime = datetime(2035, 1, 1, tzinfo=timezone.utc)
    time_sync_lock_timeout_seconds: Annotated[float, Field(gt=0)] = 2
    time_sync_command_timeout_seconds: Annotated[float, Field(gt=0)] = 5

    @model_validator(mode="before")
    @classmethod
    def require_explicit_demo_paths(cls, values: object) -> object:
        if not isinstance(values, dict) or values.get("app_env") != "demo":
            return values

        errors: list[str] = []
        raw_database_url = values.get("database_url")
        raw_log_file = values.get("log_file")
        raw_backup_dir = values.get("backup_dir")
        if not isinstance(raw_database_url, str) or not raw_database_url.startswith(
            "sqlite:////"
        ):
            errors.append("an explicit absolute database URL is required")
        if raw_log_file is None or not Path(raw_log_file).is_absolute():
            errors.append("an explicit absolute log file is required")
        if raw_backup_dir is None or not Path(raw_backup_dir).is_absolute():
            errors.append("an explicit absolute backup directory is required")
        if errors:
            raise ValueError("invalid demo configuration: " + "; ".join(errors))
        return values

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        prefix = "sqlite:///"
        if not value.startswith(prefix) or value.startswith("sqlite+pysqlite:"):
            raise ValueError("database URL must use sqlite:/// syntax")

        raw_path = value[len(prefix) :]
        if not raw_path or "?" in raw_path or "#" in raw_path:
            raise ValueError("database URL must contain a plain SQLite file path")

        path = Path(raw_path)
        if not path.is_absolute():
            path = SERVICE_ROOT / path
        path = path.resolve(strict=False)
        return f"sqlite:///{path.as_posix()}"

    @field_validator("api_host")
    @classmethod
    def validate_api_host(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("API host cannot be empty")
        return value

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("CORS origins must be exact HTTP(S) origins")
            normalized.append(f"{parsed.scheme}://{parsed.netloc}")
        return normalized

    @field_validator("log_file", mode="before")
    @classmethod
    def empty_log_file_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("log_file")
    @classmethod
    def normalize_log_file(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute():
            value = SERVICE_ROOT / value
        return value.resolve(strict=False)

    @field_validator("backup_dir")
    @classmethod
    def normalize_backup_dir(cls, value: Path) -> Path:
        if not value.is_absolute():
            value = SERVICE_ROOT / value
        return value.resolve(strict=False)

    @field_validator("time_sync_min_utc", "time_sync_max_utc")
    @classmethod
    def normalize_utc_datetime(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("time-sync bounds must be timezone-aware")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_environment(self) -> "Settings":
        if self.time_sync_min_utc >= self.time_sync_max_utc:
            raise ValueError("time-sync minimum must be earlier than maximum")

        if self.app_env == "demo":
            errors: list[str] = []
            if self.debug:
                errors.append("debug must be false")
            if self.docs_enabled:
                errors.append("docs must be disabled")
            if self.log_format != "json":
                errors.append("log format must be json")
            if self.log_file is None or not self.log_file.is_absolute():
                errors.append("an absolute log file is required")
            if not self.database_path.is_absolute():
                errors.append("an absolute database path is required")
            if not self.backup_dir.is_absolute():
                errors.append("an absolute backup directory is required")
            if errors:
                raise ValueError("invalid demo configuration: " + "; ".join(errors))
        return self

    @property
    def database_path(self) -> Path:
        return Path(self.database_url.removeprefix("sqlite:///"))


def _reject_unknown_schedule_environment() -> None:
    allowed = {f"{ENV_PREFIX}{name.upper()}" for name in Settings.model_fields}
    unknown = sorted(
        name for name in os.environ if name.startswith(ENV_PREFIX) and name not in allowed
    )
    if unknown:
        raise ValueError(f"unknown {ENV_PREFIX} environment variables: {', '.join(unknown)}")


def load_settings(*, env_file: Path | str | None = DEFAULT_ENV_FILE) -> Settings:
    """Load settings after rejecting misspelled project environment variables."""

    _reject_unknown_schedule_environment()
    return Settings(_env_file=env_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
