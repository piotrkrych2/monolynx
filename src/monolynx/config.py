"""Konfiguracja aplikacji ladowana z env vars."""

from __future__ import annotations

import secrets
import warnings

from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_KEY_SENTINEL = "__NOT_SET__"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://sentry:sentry_dev@localhost:5432/open_sentry"
    SECRET_KEY: str = _SECRET_KEY_SENTINEL
    ENVIRONMENT: str = "development"
    ENABLE_MONITOR_LOOP: bool = True
    LOG_LEVEL: str = "info"
    SESSION_COOKIE_NAME: str = "monolynx_session"
    SESSION_MAX_AGE: int = 86400

    APP_URL: str = "http://localhost:8000"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@monolynx.local"
    SMTP_USE_TLS: bool = True


settings = Settings()

if settings.SECRET_KEY == _SECRET_KEY_SENTINEL:
    warnings.warn(
        "SECRET_KEY nie jest ustawiony -- wygenerowano losowy klucz. Ustaw SECRET_KEY w zmiennych srodowiskowych w produkcji!",
        stacklevel=1,
    )
    settings.SECRET_KEY = secrets.token_urlsafe(32)
