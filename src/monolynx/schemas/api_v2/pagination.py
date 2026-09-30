"""Generyczna koperta paginacji dla list w API v2."""

from __future__ import annotations

from pydantic import BaseModel


class PaginatedResponse[T](BaseModel):
    """Koperta {"items", "page", "per_page", "total", "total_pages"} dla list API v2."""

    items: list[T]
    page: int
    per_page: int
    total: int
    total_pages: int
