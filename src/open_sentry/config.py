"""Konfiguracja aplikacji ladowana z env vars."""

from __future__ import annotations

import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = (
        "postgresql+asyncpg://sentry:sentry_dev@localhost:5432/open_sentry"
    )
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "info"
    SESSION_COOKIE_NAME: str = "open_sentry_session"
    SESSION_MAX_AGE: int = 86400

    APP_URL: str = "http://localhost:8000"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@opensentry.local"
    SMTP_USE_TLS: bool = True


settings = Settings()
