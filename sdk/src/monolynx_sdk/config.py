"""Konfiguracja SDK -- ladowana z Django settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class Config:
    server_url: str
    api_key: str
    environment: str = "production"
    release: str = ""
    max_body_size: int = 8192
    timeout: float = 5.0
    sensitive_headers: list[str] = field(
        default_factory=lambda: [
            "authorization",
            "cookie",
            "set-cookie",
            "x-api-key",
            "x-csrftoken",
        ]
    )

    @classmethod
    def from_django_settings(cls, settings: object) -> Config:
        dsn = getattr(settings, "MONOLYNX_DSN", None)

        if dsn:
            server_url, api_key = cls._parse_dsn(dsn)
        else:
            server_url = getattr(settings, "MONOLYNX_URL", None)
            api_key = getattr(settings, "MONOLYNX_API_KEY", None)

        if not server_url or not api_key:
            raise ValueError("Monolynx SDK wymaga MONOLYNX_DSN lub MONOLYNX_URL + MONOLYNX_API_KEY w settings.py")

        return cls(
            server_url=server_url.rstrip("/"),
            api_key=api_key,
            environment=getattr(settings, "MONOLYNX_ENVIRONMENT", "production"),
            release=getattr(settings, "MONOLYNX_RELEASE", ""),
        )

    @staticmethod
    def _parse_dsn(dsn: str) -> tuple[str, str]:
        """Parsuje DSN: https://<api_key>@<host>/api/v1/events -> (server_url, api_key)"""
        parsed = urlparse(dsn)
        if not parsed.username:
            raise ValueError(f"Nieprawidlowy DSN -- brak api_key: {dsn}")
        api_key = parsed.username
        server_url = f"{parsed.scheme}://{parsed.hostname}"
        if parsed.port:
            server_url += f":{parsed.port}"
        server_url += parsed.path
        return server_url, api_key
