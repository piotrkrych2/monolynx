"""Blad domenowy API v2, mapowany na odpowiedz problem+json (RFC 7807)."""

from __future__ import annotations

from collections.abc import Mapping


class ApiV2Error(Exception):
    """Rzucany przez dependency/handlery API v2; register_api_v2() mapuje go na
    odpowiedz {"type", "title", "status", "detail", "instance"} z
    content-type application/problem+json. `headers` (np. WWW-Authenticate,
    Retry-After) sa dopisywane do odpowiedzi bez zmiany, tak jak
    `headers` w fastapi.HTTPException."""

    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        type: str = "about:blank",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.type = type
        self.headers = headers
        super().__init__(detail or title)
