"""Schematy request/response API v2 dla metadanych, lintu i publikacji wpisów bloga (MON-261, MON-262, MON-263).

Response budowane z `services/blog.py::BlogPostView` przez `from_attributes`.
Wynik lintu (`BlogLintResponse`) budowany ze slownika `services/blog_lint.py::lint_result_dict`, bez pola `table` (jest tylko w MCP).
Request PATCH celowo bez `published_at` i `is_public`: data publikacji należy do publikacji.
Publikacja (`BlogPublishRequest`, `BlogPublishResponse`) to osobna operacja z bramką lintu; cofnięcie publikacji zwraca `BlogPostResponse`.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class BlogPostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_id: uuid.UUID
    title: str
    slug: str
    previous_slugs: list[str]
    description: str
    published_at: datetime | None
    author_name: str
    tags: list[str]
    lang: str
    translation_of_id: uuid.UUID | None
    cover_attachment_id: uuid.UUID | None
    last_verified_at: date | None
    is_public: bool
    public_url: str | None
    markdown_url: str | None


class BlogPostDetailResponse(BlogPostResponse):
    content: str


class BlogPostMetaUpdateRequest(BaseModel):
    """Pole nieobecne = bez zmian; `null` czyści pola nullable, dla pozostałych daje 422."""

    slug: str | None = None
    description: str | None = None
    author_name: str | None = None
    tags: list[str] | None = None
    lang: str | None = None
    translation_of_id: uuid.UUID | None = None
    cover_attachment_id: uuid.UUID | None = None
    last_verified_at: date | None = None


class BlogLintIssueResponse(BaseModel):
    rule: str
    level: str
    line: int
    message: str


class BlogLintResponse(BaseModel):
    page_id: str
    score: int
    error_count: int
    warning_count: int
    info_count: int
    issues: list[BlogLintIssueResponse]


class BlogPublishRequest(BaseModel):
    force: bool = False


class BlogPublishResponse(BlogPostResponse):
    lint: BlogLintResponse
