"""Schematy request/response API v2 dla zasobu Wiki (MON-142).

Response modele budowane z `services/wiki.py` (MON-139) i
`services/embeddings.py::search_wiki_pages` -- ta ostatnia zwraca juz gotowe
dict-y (patrz `WikiSearchResultResponse`), reszta z dataclass/ORM przez
`model_validate(..., from_attributes=True)` albo `from_domain()`."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from monolynx.services.wiki import WikiPageFlat


class WikiPageListItemResponse(BaseModel):
    """Element listy plaskiej -- `WikiPageFlat`. `depth`/`parent_id` odtwarzaja drzewo po stronie CLI."""

    id: uuid.UUID
    title: str
    depth: int
    parent_id: uuid.UUID | None
    updated_at: datetime

    @classmethod
    def from_domain(cls, page: WikiPageFlat) -> WikiPageListItemResponse:
        return cls(id=page.id, title=page.title, depth=page.depth, parent_id=page.parent_id, updated_at=page.updated_at)


class WikiPageDetailResponse(BaseModel):
    """GET .../wiki/pages/{page_id} -- `WikiPageDetail` (z pelna trescia markdown)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    parent_id: uuid.UUID | None
    position: int
    content: str
    is_ai_touched: bool
    created_by: str
    last_edited_by: str
    created_at: datetime
    updated_at: datetime


class WikiPageCreateRequest(BaseModel):
    title: str
    content: str
    parent_id: uuid.UUID | None = None
    position: int = 0
    is_public: bool = False


class WikiPageUpdateRequest(BaseModel):
    """Semantyka PATCH: pole nieobecne = bez zmian."""

    title: str | None = None
    content: str | None = None
    position: int | None = None
    is_public: bool | None = None


class WikiPageResponse(BaseModel):
    """Odpowiedz POST/PATCH .../wiki/pages -- serwis zwraca `WikiPage` ORM."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    slug: str
    parent_id: uuid.UUID | None
    position: int
    is_ai_touched: bool
    is_public: bool


class WikiSearchResultResponse(BaseModel):
    """Element wyniku `search_wiki_pages` -- goly JSON array, bez koperty paginacji."""

    id: uuid.UUID
    title: str
    slug: str
    snippet: str
    similarity: float


class WikiConfigResponse(BaseModel):
    """GET .../wiki/config -- pelna `WikiConfig` (flaga + id stron systemowych)."""

    model_config = ConfigDict(from_attributes=True)

    wiki_llm_enabled: bool
    index_page_id: uuid.UUID | None
    log_page_id: uuid.UUID | None
    schema_page_id: uuid.UUID | None


class WikiConfigUpdateRequest(BaseModel):
    enabled: bool


class WikiConfigUpdateResponse(BaseModel):
    """PATCH .../wiki/config -- `set_wiki_config` zwraca `Project` ORM, nie `WikiConfig`
    (id stron systemowych by wymagaly dodatkowego zapytania, ktorego tool MCP tez nie robi)."""

    wiki_llm_enabled: bool
