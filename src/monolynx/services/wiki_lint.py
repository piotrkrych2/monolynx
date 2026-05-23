"""Serwis wiki lint -- analiza spójności wiki projektu."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.wiki_backlink import WikiBacklink
from monolynx.models.wiki_page import WikiPage
from monolynx.services.minio_client import get_markdown
from monolynx.services.wiki import RESERVED_SLUGS, extract_wiki_links

logger = logging.getLogger("monolynx.wiki_lint")

# Regex na marker sprzeczności w treści (toleruje datę i drobne odmiany)
_CONTRADICTION_RE = re.compile(r">\s*\*\*Sprzeczność", re.IGNORECASE)


async def lint_wiki(project_id: uuid.UUID, db: AsyncSession) -> dict[str, Any]:
    """Przeskanuj wiki projektu i zwróć ustrukturyzowany raport spójności.

    Zwracane klucze:
    - orphans: strony bez żadnego backlinku przychodzącego
    - dead_links: referencje wikilink nierozwiązane do istniejących stron
    - contradictions: strony zawierające marker "> **Sprzeczność"
    - gaps: koncepty wzmiankowane wielokrotnie jako [[x]] bez własnej strony (heurystyka)
    """
    result = await db.execute(select(WikiPage).where(WikiPage.project_id == project_id))
    pages = list(result.scalars().all())

    # Słownik slug->id i id->page dla szybkiego lookup
    slug_to_id: dict[str, uuid.UUID] = {p.slug: p.id for p in pages}
    id_set: set[uuid.UUID] = {p.id for p in pages}

    # Jeden odczyt MinIO na stronę - cache używany przez dead_links i contradictions
    content_cache: dict[uuid.UUID, str] = {}
    for page in pages:
        try:
            content_cache[page.id] = get_markdown(page.minio_path)
        except Exception:
            logger.warning("Nie można odczytać treści strony %s z MinIO", page.id)
            content_cache[page.id] = ""

    orphans = await _find_orphans(project_id, pages, db)
    dead_links, missing_ref_counts = _find_dead_links(pages, slug_to_id, id_set, content_cache)
    contradictions = _find_contradictions(pages, content_cache)
    gaps = _find_gaps(missing_ref_counts)

    return {
        "orphans": orphans,
        "dead_links": dead_links,
        "contradictions": contradictions,
        "gaps": gaps,
    }


async def _find_orphans(
    project_id: uuid.UUID,
    pages: list[WikiPage],
    db: AsyncSession,
) -> list[dict[str, Any]]:
    """Strony bez backlinku przychodzącego.

    Wyklucza strony systemowe (RESERVED_SLUGS) i strony-korzenie (parent_id is None),
    bo korzenie są normalnie dostępne przez drzewo nawigacyjne - nie ma potrzeby
    ich linkowania i generowałyby szum w raporcie.
    """
    result = await db.execute(select(WikiBacklink.target_page_id).where(WikiBacklink.target_page_id.in_([p.id for p in pages])))
    linked_ids: set[uuid.UUID] = set(result.scalars().all())

    orphan_pages = [
        p
        for p in pages
        if p.id not in linked_ids and p.slug not in RESERVED_SLUGS and p.parent_id is not None  # wyklucz korzenie - są nawigowalne przez drzewo
    ]

    return [{"page_id": str(p.id), "title": p.title, "slug": p.slug} for p in orphan_pages]


def _find_dead_links(
    pages: list[WikiPage],
    slug_to_id: dict[str, uuid.UUID],
    id_set: set[uuid.UUID],
    content_cache: dict[uuid.UUID, str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Martwe linki: referencje wikilink nierozwiązane do istniejących stron w projekcie.

    Zwraca też licznik powtórzeń brakujących referencji (potrzebny do wykrywania luk).
    Treść stron pochodzi z content_cache zbudowanego w lint_wiki() - brak dodatkowych odczytów MinIO.
    """
    dead: list[dict[str, Any]] = []
    missing_ref_counts: dict[str, int] = {}

    for page in pages:
        content = content_cache.get(page.id, "")
        if not content:
            continue

        refs = extract_wiki_links(content)
        for ref, anchor in refs.items():
            resolved = _resolve_ref(ref, slug_to_id, id_set)
            if resolved is None:
                dead.append(
                    {
                        "source_page_id": str(page.id),
                        "source_title": page.title,
                        "source_slug": page.slug,
                        "ref": ref,
                        "anchor": anchor,
                    }
                )
                missing_ref_counts[ref] = missing_ref_counts.get(ref, 0) + 1

    return dead, missing_ref_counts


def _resolve_ref(
    ref: str,
    slug_to_id: dict[str, uuid.UUID],
    id_set: set[uuid.UUID],
) -> uuid.UUID | None:
    """Spróbuj rozwiązać referencję do UUID strony (po slug lub UUID)."""
    # UUID
    try:
        ref_uuid = uuid.UUID(ref)
        if ref_uuid in id_set:
            return ref_uuid
        return None
    except ValueError:
        pass
    # Slug
    return slug_to_id.get(ref)


def _find_contradictions(
    pages: list[WikiPage],
    content_cache: dict[uuid.UUID, str],
) -> list[dict[str, Any]]:
    """Strony zawierające marker "> **Sprzeczność" w treści.

    Treść stron pochodzi z content_cache zbudowanego w lint_wiki() - brak dodatkowych odczytów MinIO.
    """
    result: list[dict[str, Any]] = []
    for page in pages:
        content = content_cache.get(page.id, "")
        if _CONTRADICTION_RE.search(content):
            result.append(
                {
                    "page_id": str(page.id),
                    "title": page.title,
                    "slug": page.slug,
                }
            )

    return result


def _find_gaps(missing_ref_counts: dict[str, int], min_count: int = 2) -> list[dict[str, Any]]:
    """Heurystyka: koncepty wzmiankowane >=2 razy jako wikilink bez własnej strony.

    To jest heurystyka - false positive możliwe gdy strona istnieje pod innym slugiem.
    Tylko referencje w formacie slug (nie UUID) - UUID-y to zwykle błędne linki.
    """
    gaps: list[dict[str, Any]] = []
    for ref, count in missing_ref_counts.items():
        if count >= min_count:
            # Pomiń UUID-podobne referencje - to nie są koncepty do stworzenia
            try:
                uuid.UUID(ref)
                continue
            except ValueError:
                pass
            gaps.append({"ref": ref, "mention_count": count})

    return sorted(gaps, key=lambda x: x["mention_count"], reverse=True)
