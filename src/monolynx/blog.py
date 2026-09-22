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
import io
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_backlink import WikiBacklink
from monolynx.models.wiki_page import WikiPage
from monolynx.services.minio_client import get_attachment as minio_get_attachment
from monolynx.services.wiki import extract_summary, get_page_content, get_public_page_by_slug, render_markdown_public

logger = logging.getLogger("monolynx.blog")

router = APIRouter(prefix="/blog", tags=["blog"])

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["ga_measurement_id"] = settings.GA_MEASUREMENT_ID

# Allow-lista typow dla Content-Disposition: inline (MON-203). Wszystko inne -- w tym
# image/svg+xml, ktory moze osadzic <script>/onload -- idzie jako attachment, mimo ze
# zaczyna sie od "image/". Endpoint jest publiczny i bez sesji dashboardu, ale wciaz na
# tej samej domenie, wiec inline-render zlosliwego SVG/HTML nie jest opcja.
_PUBLIC_INLINE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

_PER_PAGE = 20


async def _fetch_summary(page: WikiPage) -> str:
    """Pobierz treść strony z MinIO (executor, patrz komentarz w public_page) i wyciągnij
    streszczenie. Błąd storage dla POJEDYNCZEJ strony nie wywraca całej listy /blog/
    (granica systemu) -- strona ląduje na liście z pustym streszczeniem."""
    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, get_page_content, page)
    except Exception:
        logger.exception("Blad pobierania tresci strony wiki z MinIO dla listy /blog/ (page_id=%s)", page.id)
        return ""
    return extract_summary(content)


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


@router.get("/", response_class=HTMLResponse)
async def public_index(request: Request, lang: str = "en", db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Lista publicznych wpisow bloga (is_public=True), najnowsze najpierw, z paginacja.

    Wzorzec paginacji projektu (dashboard/scrum.py): `page` z try/except -> 1,
    stala per_page, func.count() + limit/offset, page clampowany do <1, total_pages>.
    """
    if lang not in ("en", "pl"):
        lang = "en"

    try:
        page_num = max(1, int(request.query_params.get("page", "1")))
    except (ValueError, TypeError):
        page_num = 1

    total_count = (await db.execute(select(func.count(WikiPage.id)).where(WikiPage.is_public.is_(True)))).scalar() or 0
    total_pages = max(1, (total_count + _PER_PAGE - 1) // _PER_PAGE)
    page_num = min(page_num, total_pages)

    result = await db.execute(
        select(WikiPage).where(WikiPage.is_public.is_(True)).order_by(WikiPage.updated_at.desc()).limit(_PER_PAGE).offset((page_num - 1) * _PER_PAGE)
    )
    pages = list(result.scalars().all())

    # Streszczenia wymagaja N odczytow z MinIO -- rownolegle (asyncio.gather), nie
    # sekwencyjnie, zeby N wolnych obiektow nie sumowalo sie w czas ladowania listy.
    summaries = await asyncio.gather(*(_fetch_summary(page) for page in pages))

    return templates.TemplateResponse(
        request,
        "blog/index.html",
        {
            "lang": lang,
            "app_url": settings.APP_URL,
            "posts": [{"title": p.title, "slug": p.slug, "updated_at": p.updated_at, "summary": s} for p, s in zip(pages, summaries, strict=True)],
            "page": page_num,
            "total_pages": total_pages,
            "has_prev": page_num > 1,
            "has_next": page_num < total_pages,
        },
    )


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
    meta_description = extract_summary(content)

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
            "created_at": page.created_at,
            "meta_description": meta_description,
        },
    )


@router.get("/{slug}/attachments/{attachment_id}/{filename}", response_model=None)
async def public_attachment(
    slug: str,
    attachment_id: uuid.UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Publiczny zalacznik strony wiki -- bez sesji, tylko is_public=True (MON-203).

    Zapytanie wiaze attachment_id z page.id (page samo jest juz przefiltrowane po
    is_public=True przez get_public_page_by_slug) -- sam attachment_id nie wystarcza,
    inaczej kazdy zalacznik w systemie stalby sie publiczny pod tym adresem. Zalacznik
    nalezacy do INNEJ strony niz {slug} w URL rowniez konczy sie 404 (filtr wiki_page_id).

    X-Content-Type-Options: nosniff na KAZDEJ odpowiedzi, rowniez na 404 -- endpoint jest
    bez uwierzytelnienia, wiec to jedyna ochrona anonimowego czytelnika przed MIME-sniffing.
    """
    nosniff = {"X-Content-Type-Options": "nosniff"}

    page = await get_public_page_by_slug(slug, db)
    if page is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono strony", headers=nosniff)

    result = await db.execute(
        select(WikiAttachment).where(
            WikiAttachment.id == attachment_id,
            WikiAttachment.wiki_page_id == page.id,
        )
    )
    attachment = result.scalar_one_or_none()
    if attachment is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono zalacznika", headers=nosniff)

    # Blokujace IO (MinIO) w handlerze async -- w executorze, ten sam wzorzec co
    # get_page_content() powyzej. Blad storage nigdy nie moze wyciec jako niezlapany
    # wyjatek do anonimowego czytelnika -- mapujemy na 404, tak jak brak strony/zalacznika.
    loop = asyncio.get_running_loop()
    try:
        data, content_type = await loop.run_in_executor(None, minio_get_attachment, attachment.storage_path)
    except Exception:
        logger.exception("Blad pobierania zalacznika z MinIO (attachment_id=%s)", attachment.id)
        raise HTTPException(status_code=404, detail="Nie znaleziono zalacznika", headers=nosniff) from None

    disposition = "inline" if content_type in _PUBLIC_INLINE_MIME_TYPES else "attachment"
    safe_name = attachment.filename.replace('"', "_").replace("\\", "_")
    return StreamingResponse(
        io.BytesIO(data),
        media_type=content_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
