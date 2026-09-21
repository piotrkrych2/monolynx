"""Publiczny blog -- GET /blog/{slug}.

MON-201: router działa BEZ sesji i BEZ RBAC (w odróżnieniu od dashboard/wiki.py).
Renderuje wyłącznie strony wiki z is_public=True; wszystko inne (strona prywatna,
nieistniejący slug) mapuje na 404 -- nigdy 403, żeby nie potwierdzać istnienia zasobu.

Rejestrowany bezwarunkowo w main.py, niezależnie od flagi SKIP_LANDING_PAGE -- ta flaga
bramkuje marketingową stronę landing, a blog to treść linkowana z zewnątrz (podobnie jak
endpointy .md, które też nie są nią bramkowane).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.wiki_backlink import WikiBacklink
from monolynx.models.wiki_page import WikiPage
from monolynx.services.wiki import get_page_content, get_public_page_by_slug, render_markdown_public

logger = logging.getLogger("monolynx.blog")

router = APIRouter(prefix="/blog", tags=["blog"])

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["ga_measurement_id"] = settings.GA_MEASUREMENT_ID


async def _public_breadcrumbs(page: WikiPage, db: AsyncSession) -> list[dict[str, str]]:
    """Breadcrumbs od korzenia do strony, wyłącznie is_public=True.

    Filtr is_public.is_(True) jest w WHERE (SQL, nie w szablonie). Jeśli rodzic jest
    prywatny, zapytanie zwraca None i łańcuch się przerywa -- nie przeskakujemy
    prywatnego rodzica do jego przodków.

    Ostatni element listy to BIEŻĄCA strona (page) -- ten sam kształt co
    get_breadcrumbs() w services/wiki.py (tam `crumbs = [page]` na starcie).
    Szablon blog/page.html renderuje ostatni element jako aktywny (nieklikalny),
    resztę jako linki -- zgodnie z tym kontraktem.
    """
    crumbs: list[dict[str, str]] = []
    parent_id = page.parent_id
    while parent_id is not None:
        result = await db.execute(select(WikiPage).where(WikiPage.id == parent_id, WikiPage.is_public.is_(True)))
        parent = result.scalar_one_or_none()
        if parent is None:
            break
        crumbs.insert(0, {"slug": parent.slug, "title": parent.title})
        parent_id = parent.parent_id
    crumbs.append({"slug": page.slug, "title": page.title})
    return crumbs


async def _public_children(page: WikiPage, db: AsyncSession) -> list[dict[str, str]]:
    """Bezpośrednie dzieci strony, wyłącznie is_public=True (filtr w SQL)."""
    result = await db.execute(
        select(WikiPage.slug, WikiPage.title)
        .where(WikiPage.parent_id == page.id, WikiPage.is_public.is_(True))
        .order_by(WikiPage.position, WikiPage.title)
    )
    return [{"slug": row.slug, "title": row.title} for row in result.all()]


async def _public_backlinks(page: WikiPage, db: AsyncSession) -> list[dict[str, str]]:
    """Strony źródłowe backlinków wskazujących na tę stronę, wyłącznie is_public=True (filtr w SQL)."""
    result = await db.execute(
        select(WikiPage.slug, WikiPage.title)
        .join(WikiBacklink, WikiBacklink.source_page_id == WikiPage.id)
        .where(WikiBacklink.target_page_id == page.id, WikiPage.is_public.is_(True))
    )
    return [{"slug": row.slug, "title": row.title} for row in result.all()]


@router.get("/{slug}", response_class=HTMLResponse)
async def public_page(slug: str, request: Request, lang: str = "en", db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Publiczny render strony wiki -- bez sesji, bez RBAC, tylko is_public=True."""
    if lang not in ("en", "pl"):
        lang = "en"

    page = await get_public_page_by_slug(slug, db)
    if page is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono strony")

    # get_page_content() woła get_markdown() rozwiązywane w globalsach monolynx.services.wiki
    # -- treść MUSI przechodzić przez tę ścieżkę (nie wolać minio_client.get_markdown wprost tutaj),
    # ale to sync/blokujące IO (MinIO) -- w handlerze async odpalamy w executorze (wzorzec jak
    # dashboard/scrum.py przy pobieraniu załączników). Endpoint jest publiczny i bez uwierzytelnienia,
    # więc błąd storage (obiekt usunięty, MinIO niedostępne) nigdy nie może wyciec jako niezłapany
    # wyjątek -- mapujemy na 404, tak samo jak brak strony, żeby anonimowy czytelnik nie odróżnił
    # "nie ma strony" od "storage padło".
    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, get_page_content, page)
    except Exception:
        logger.exception("Blad pobierania tresci strony wiki z MinIO (page_id=%s)", page.id)
        raise HTTPException(status_code=404, detail="Nie znaleziono strony") from None
    content_html = render_markdown_public(content)

    breadcrumbs = await _public_breadcrumbs(page, db)
    children = await _public_children(page, db)
    backlinks = await _public_backlinks(page, db)

    return templates.TemplateResponse(
        request,
        "blog/page.html",
        {
            "lang": lang,
            "app_url": settings.APP_URL,
            "page_title": page.title,
            "page_slug": page.slug,
            "content_html": content_html,
            "breadcrumbs": breadcrumbs,
            "children": children,
            "backlinks": backlinks,
            "updated_at": page.updated_at,
        },
    )
