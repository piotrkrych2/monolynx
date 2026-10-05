"""Publiczny blog -- GET /blog/ (lista), GET /blog/{slug} (wpis), GET /blog/{slug}/attachments/....

MON-201: router działa BEZ sesji i BEZ RBAC (w odróżnieniu od dashboard/wiki.py).
Renderuje wyłącznie strony wiki z is_public=True; wszystko inne (strona prywatna,
nieistniejący slug) mapuje na 404 -- nigdy 403, żeby nie potwierdzać istnienia zasobu.

MON-254: wpis adresuje stabilny `BlogPost.slug` (nie slug strony wiki, który zmienia się wraz
z tytułem). Stary slug z `previous_slugs` daje 301 na aktualny adres (poza trasą załączników:
tam 404). Strona publiczna bez wiersza BlogPost to 404 -- wiersz zakładają `ensure_post` i
migracja. Lista sortuje po `published_at`, zajawka to `description` z fallbackiem na streszczenie
treści.

MON-255: treść wpisu renderuje `services/blog_blocks.render_post_html` (bloki + sanityzacja nh3);
streszczenie (zajawka, meta description) liczy się ze źródła bez znaczników bloków (`strip_block_markers`).

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
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.blog_post import BlogPost
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_backlink import WikiBacklink
from monolynx.models.wiki_page import WikiPage
from monolynx.services.blog import PublicPost, get_public_post_by_slug, list_public_posts
from monolynx.services.blog_blocks import render_post_html, strip_block_markers
from monolynx.services.minio_client import get_attachment as minio_get_attachment
from monolynx.services.wiki import extract_summary, get_page_content

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


async def _fetch_summary(item: PublicPost) -> str:
    """Zajawka wpisu: `description`, a gdy pusty - streszczenie treści z MinIO (executor, patrz
    komentarz w public_page). Przy niepustym opisie MinIO nie jest czytane. Błąd storage dla
    POJEDYNCZEJ strony nie wywraca całej listy /blog/ (granica systemu) -- wpis ląduje na liście
    z pustą zajawką."""
    if item.post.description:
        return item.post.description
    page = item.page
    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, get_page_content, page)
    except Exception:
        logger.exception("Blad pobierania tresci strony wiki z MinIO dla listy /blog/ (page_id=%s)", page.id)
        return ""
    return extract_summary(strip_block_markers(content))


async def _public_breadcrumbs(page: WikiPage, post: BlogPost, db: AsyncSession) -> list[dict[str, str]]:
    """Breadcrumbs od korzenia do strony, wyłącznie is_public=True, linki po BlogPost.slug.

    Filtr is_public.is_(True) jest w WHERE (SQL, nie w szablonie). Jeśli rodzic jest
    prywatny (albo nie ma wpisu BlogPost), zapytanie zwraca None i łańcuch się przerywa --
    nie przeskakujemy takiego rodzica do jego przodków.

    Ostatni element listy to BIEŻĄCA strona (page) -- ten sam kształt co
    get_breadcrumbs() w services/wiki.py (tam `crumbs = [page]` na starcie).
    Szablon blog/page.html renderuje ostatni element jako aktywny (nieklikalny),
    resztę jako linki -- zgodnie z tym kontraktem.
    """
    crumbs: list[dict[str, str]] = []
    parent_id = page.parent_id
    while parent_id is not None:
        result = await db.execute(
            select(WikiPage, BlogPost.slug)
            .join(BlogPost, BlogPost.page_id == WikiPage.id)
            .where(WikiPage.id == parent_id, WikiPage.is_public.is_(True))
        )
        row = result.first()
        if row is None:
            break
        parent, parent_slug = row[0], row[1]
        crumbs.insert(0, {"slug": parent_slug, "title": parent.title})
        parent_id = parent.parent_id
    crumbs.append({"slug": post.slug, "title": page.title})
    return crumbs


async def _public_children(page: WikiPage, db: AsyncSession) -> list[dict[str, str]]:
    """Bezpośrednie dzieci strony będące publicznymi wpisami (slug = BlogPost.slug, filtr w SQL)."""
    result = await db.execute(
        select(BlogPost.slug, WikiPage.title)
        .join(WikiPage, WikiPage.id == BlogPost.page_id)
        .where(WikiPage.parent_id == page.id, WikiPage.is_public.is_(True))
        .order_by(WikiPage.position, WikiPage.title)
    )
    return [{"slug": row.slug, "title": row.title} for row in result.all()]


async def _public_backlinks(page: WikiPage, db: AsyncSession) -> list[dict[str, str]]:
    """Publiczne wpisy źródłowe backlinków wskazujących na tę stronę (slug = BlogPost.slug, filtr w SQL)."""
    result = await db.execute(
        select(BlogPost.slug, WikiPage.title)
        .join(WikiPage, WikiPage.id == BlogPost.page_id)
        .join(WikiBacklink, WikiBacklink.source_page_id == WikiPage.id)
        .where(WikiBacklink.target_page_id == page.id, WikiPage.is_public.is_(True))
    )
    return [{"slug": row.slug, "title": row.title} for row in result.all()]


@router.get("/", response_class=HTMLResponse)
async def public_index(request: Request, lang: str = "en", db: AsyncSession = Depends(get_db)) -> HTMLResponse:
    """Lista publicznych wpisow bloga, najnowsze (`published_at`) najpierw, z paginacja.

    `page` z try/except -> 1; clampowanie do <1, total_pages> robi `list_public_posts`.
    `lang` to jezyk interfejsu, nie filtr wpisow.
    """
    if lang not in ("en", "pl"):
        lang = "en"

    try:
        page_num = max(1, int(request.query_params.get("page", "1")))
    except (ValueError, TypeError):
        page_num = 1

    listing = await list_public_posts(db, page_num, _PER_PAGE)

    # Zajawki bez description wymagaja odczytow z MinIO -- rownolegle (asyncio.gather), nie
    # sekwencyjnie, zeby N wolnych obiektow nie sumowalo sie w czas ladowania listy.
    summaries = await asyncio.gather(*(_fetch_summary(item) for item in listing.items))

    return templates.TemplateResponse(
        request,
        "blog/index.html",
        {
            "lang": lang,
            "app_url": settings.APP_URL,
            "posts": [
                {
                    "title": item.page.title,
                    "slug": item.post.slug,
                    "published_at": item.post.published_at or item.page.created_at,
                    "summary": s,
                }
                for item, s in zip(listing.items, summaries, strict=True)
            ],
            "page": listing.page,
            "total_pages": listing.total_pages,
            "has_prev": listing.page > 1,
            "has_next": listing.page < listing.total_pages,
        },
    )


@router.get("/{slug}", response_class=HTMLResponse, response_model=None)
async def public_page(slug: str, request: Request, lang: str = "en", db: AsyncSession = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    """Publiczny render wpisu po `BlogPost.slug` -- bez sesji, bez RBAC, tylko is_public=True.

    Stary slug (`previous_slugs`) -> 301 na aktualny adres z zachowanym query stringiem.
    """
    if lang not in ("en", "pl"):
        lang = "en"

    lookup = await get_public_post_by_slug(db, slug)
    if lookup is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono strony")
    if lookup.redirect_slug is not None:
        target = f"/blog/{lookup.redirect_slug}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=301)
    post, page = lookup.post, lookup.page
    if post is None or page is None:
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
    content_html = render_post_html(content)
    meta_description = post.description or extract_summary(strip_block_markers(content))

    # Okladka musi nalezec do tej strony (set_post_meta to pilnuje, ale zalacznik mogl byc potem
    # przeniesiony/usuniety) -- brak wiersza = domyslny og:image layoutu.
    cover_image_url = None
    if post.cover_attachment_id is not None:
        cover = (
            await db.execute(
                select(WikiAttachment.filename).where(WikiAttachment.id == post.cover_attachment_id, WikiAttachment.wiki_page_id == page.id)
            )
        ).scalar_one_or_none()
        if cover is not None:
            cover_image_url = f"{settings.APP_URL.rstrip('/')}/blog/{post.slug}/attachments/{post.cover_attachment_id}/{quote(cover)}"

    breadcrumbs = await _public_breadcrumbs(page, post, db)
    children = await _public_children(page, db)
    backlinks = await _public_backlinks(page, db)

    return templates.TemplateResponse(
        request,
        "blog/page.html",
        {
            "lang": lang,
            "app_url": settings.APP_URL,
            "page_title": page.title,
            "page_slug": post.slug,
            "content_html": content_html,
            "breadcrumbs": breadcrumbs,
            "children": children,
            "backlinks": backlinks,
            "updated_at": page.updated_at,
            "created_at": page.created_at,
            "published_at": post.published_at,
            "cover_image_url": cover_image_url,
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

    Wpis wyszukiwany po AKTUALNYM `BlogPost.slug` (MON-254); stary slug daje 404, bez 301
    (adres zalacznika nie jest linkowany z zewnatrz jak adres wpisu). Zapytanie wiaze
    attachment_id z page.id (page samo jest juz przefiltrowane po is_public=True przez
    get_public_post_by_slug) -- sam attachment_id nie wystarcza,
    inaczej kazdy zalacznik w systemie stalby sie publiczny pod tym adresem. Zalacznik
    nalezacy do INNEJ strony niz {slug} w URL rowniez konczy sie 404 (filtr wiki_page_id).

    X-Content-Type-Options: nosniff na KAZDEJ odpowiedzi, rowniez na 404 -- endpoint jest
    bez uwierzytelnienia, wiec to jedyna ochrona anonimowego czytelnika przed MIME-sniffing.
    """
    nosniff = {"X-Content-Type-Options": "nosniff"}

    lookup = await get_public_post_by_slug(db, slug)
    if lookup is None or lookup.page is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono strony", headers=nosniff)
    page = lookup.page

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
