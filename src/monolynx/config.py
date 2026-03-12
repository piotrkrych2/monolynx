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
    SKIP_LANDING_PAGE: bool = True
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

    MCP_ALLOWED_HOSTS: str = ""

    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "monolynx-wiki"
    MINIO_USE_SSL: bool = False

    # Neo4j
    NEO4J_URI: str = "bolt://neo4j:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "neo4j_dev"
    ENABLE_GRAPH_DB: bool = True

    # Google OAuth
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    # OAuth 2.1
    OAUTH_ACCESS_TOKEN_TTL: int = 2592000  # 30 dni
    OAUTH_REFRESH_TOKEN_TTL: int = 2592000  # 30 dni
    OAUTH_AUTH_CODE_TTL: int = 600  # 10 min

    # Embeddings (RAG search for Wiki)
    OPENAI_API_KEY: str = ""
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536
    EMBEDDING_CHUNK_SIZE: int = 500
    EMBEDDING_CHUNK_OVERLAP: int = 50


settings = Settings()

if settings.SECRET_KEY == _SECRET_KEY_SENTINEL:
    warnings.warn(
        "SECRET_KEY nie jest ustawiony -- wygenerowano losowy klucz. Ustaw SECRET_KEY w zmiennych srodowiskowych w produkcji!",
        stacklevel=1,
    )
    settings.SECRET_KEY = secrets.token_urlsafe(32)
