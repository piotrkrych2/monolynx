"""Bootstrap metody LLM Wiki - idempotentny setup stron systemowych."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.project import Project
from monolynx.services.wiki import (
    append_log,
    ensure_system_page,
    regenerate_index,
)
from monolynx.services.wiki_templates import DEFAULT_WIKI_SCHEMA


async def bootstrap_wiki_llm(
    *,
    project: Project,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> dict[str, object]:
    """Idempotentny bootstrap metody LLM Wiki dla projektu.

    Kolejność operacji:
    1. Włącz wiki_llm_enabled (mutacja obiektu project z bieżącej sesji + commit).
    2. Utwórz lub pobierz stronę wiki-schema z DEFAULT_WIKI_SCHEMA.
    3. Przebuduj wiki-index (katalog wszystkich stron).
    4. Dopisz wpis do wiki-log o bootstrapie (append_log tworzy wiki-log gdy brak).
    5. Zwróć słownik z ID stron systemowych i liczbą stron.

    Powtórny bootstrap jest bezpieczny: ensure_system_page to get-or-create,
    index się odświeża, log dostaje kolejny wpis (append-only).

    Uwaga: project musi być obiektem z bieżącej sesji db (nie z innej sesji).
    MCP tool pobiera projekt przez select(Project) w tej samej sesji przed wywołaniem.
    """
    # Krok 1: włącz flagę - projekt już załadowany z bieżącej sesji
    project.wiki_llm_enabled = True
    await db.commit()

    # Krok 2: strona wiki-schema (schemat/konwencje wiki)
    schema_page = await ensure_system_page(
        project=project,
        slug="wiki-schema",
        title="Schemat Wiki",
        default_content=DEFAULT_WIKI_SCHEMA,
        user_id=user_id,
        db=db,
    )

    # Krok 3: przebuduj index (katalog stron)
    index_page, page_count = await regenerate_index(
        project=project,
        user_id=user_id,
        db=db,
    )

    # Krok 4: dopisz wpis do dziennika - append_log tworzy wiki-log gdy go brak
    log_page = await append_log(
        project=project,
        entry=f"Metoda LLM Wiki włączona (bootstrap): {page_count} stron w katalogu",
        user_id=user_id,
        db=db,
    )

    return {
        "wiki_llm_enabled": True,
        "schema_page_id": str(schema_page.id),
        "log_page_id": str(log_page.id),
        "index_page_id": str(index_page.id),
        "catalogued_pages": page_count,
        "message": f"Bootstrap metody LLM Wiki zakończony - {page_count} stron skatalogowanych",
    }
