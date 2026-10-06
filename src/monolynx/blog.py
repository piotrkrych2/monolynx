"""Publiczny blog -- GET /blog/ (lista), GET /blog/{slug} (wpis), GET /blog/{slug}/attachments/....

Bliźniaki markdown (`/blog/index.md`, `/blog/{slug}.md`) obsługuje `blog_ai.py` (MON-258).

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

MON-257: `public_page` liczy `collect_block_assets(content)` i przekazuje szablonowi `has_diagram` (biblioteka
Mermaid ładowana tylko wpisowi z diagramem) oraz `graphics` (nazwy komponentów z `BLOG_GRAPHICS`, których
skrypt i styl dołącza `page.html` tylko wpisowi, który ich używa).

MON-258: `/blog/{slug}` z `Accept: text/markdown` zwraca ten sam dokument co `/blog/{slug}.md`
(`blog_ai.markdown_twin_response`); odpowiedź HTML niesie `Vary: Accept` i `Link` do bliźniaka,
a strony dostają JSON-LD (`services/blog.build_post_jsonld` / `build_blog_jsonld`).

MON-260: strony dla ludzi. Lista przyjmuje `?tag=` i `?lang=` (poprawny `lang` filtruje wpisy i ustawia
język interfejsu), karty niosą okładkę, tagi i czas czytania (treść każdej karty czytana równolegle),
paginacja zachowuje filtry, a `canonical` / `prev` / `next` wskazują bieżącą stronę. Wpis dostaje spis
treści, powiązane wpisy, tłumaczenia i `hreflang`; jego interfejs to `?lang=` albo `lang` wpisu. Obie
odpowiedzi HTML idą przez `blog_ai.conditional_response` (ETag, Last-Modified, 304).

Rejestrowany bezwarunkowo w main.py, niezależnie od flagi SKIP_LANDING_PAGE -- ta flaga
bramkuje marketingową stronę landing, a blog to treść linkowana z zewnątrz (podobnie jak
endpointy .md, które też nie są nią bramkowane).
"""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.blog_ai import conditional_response, markdown_twin_response, wants_markdown
from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.blog_post import BlogPost
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_backlink import WikiBacklink
from monolynx.models.wiki_page import WikiPage
from monolynx.services.blog import (
    POST_LANGS,
    PublicPost,
    build_blog_jsonld,
    build_post_jsonld,
    dump_jsonld,
    extract_toc,
    get_cover_urls,
    get_public_post_by_slug,
    is_valid_tag,
    list_public_posts,
    list_public_translations,
    list_related_posts,
    reading_time_minutes,
)
from monolynx.services.blog_blocks import (
    collect_block_assets,
    extract_structured_data,
    render_post_html,
    render_post_markdown,
    strip_block_markers,
)
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
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


async def _fetch_card_data(item: PublicPost) -> tuple[str, int | None]:
    """Zajawka i czas czytania karty z JEDNEJ lektury treści z MinIO (executor, patrz komentarz w
    public_page). Zajawka to `description`, a gdy pusty - streszczenie treści. Błąd storage dla
    POJEDYNCZEJ strony nie wywraca całej listy /blog/ (granica systemu) -- karta dostaje
    `reading_time=None` i zajawkę z `description` albo pustą."""
    page = item.page
    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, get_page_content, page)
    except Exception:
        logger.exception("Blad pobierania tresci strony wiki z MinIO dla listy /blog/ (page_id=%s)", page.id)
        return item.post.description or "", None
    summary = item.post.description or extract_summary(strip_block_markers(content))
    return summary, reading_time_minutes(render_post_markdown(content))


def _index_path(tag: str | None, lang: str | None, page: int | None = None) -> str:
    """Ścieżka listy z query w stałej kolejności `tag`, `lang`, `page`; `page` tylko gdy > 1."""
    params: list[tuple[str, str]] = []
    if tag is not None:
        params.append(("tag", tag))
    if lang is not None:
        params.append(("lang", lang))
    if page is not None and page > 1:
        params.append(("page", str(page)))
    return f"/blog/?{urlencode(params)}" if params else "/blog/"


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


@router.get("/", response_class=HTMLResponse, response_model=None)
async def public_index(request: Request, lang: str | None = None, tag: str | None = None, db: AsyncSession = Depends(get_db)) -> Response:
    """Lista publicznych wpisow bloga, najnowsze (`published_at`) najpierw, z paginacja.

    `page` z try/except -> 1; clampowanie do <1, total_pages> robi `list_public_posts`.
    `lang` (`pl`/`en`) filtruje wpisy po jezyku tresci i ustawia jezyk interfejsu; brak albo inna
    wartosc = bez filtra, interfejs `en`. `tag`: pusty = bez filtra, niepoprawny (`is_valid_tag`) =
    pusta lista bez zapytania do bazy (bajt NUL w parametrze wywrocilby PostgreSQL), status 200.
    Odpowiedz niesie `ETag` i `Last-Modified` (najnowszy `updated_at` wsrod wpisow strony listy).
    """
    filter_lang = lang if lang in POST_LANGS else None
    ui_lang = filter_lang or "en"
    filter_tag = tag if tag and is_valid_tag(tag) else None

    try:
        page_num = max(1, int(request.query_params.get("page", "1")))
    except (ValueError, TypeError):
        page_num = 1

    items: list[PublicPost] = []
    current_page, total_pages = 1, 1
    if not tag or filter_tag is not None:
        listing = await list_public_posts(db, page_num, _PER_PAGE, tag=filter_tag, lang=filter_lang)
        items, current_page, total_pages = listing.items, listing.page, listing.total_pages

    # Tresc kazdej karty (zajawka bez description i czas czytania) wymaga odczytu z MinIO --
    # rownolegle (asyncio.gather), nie sekwencyjnie, zeby N wolnych obiektow nie sumowalo sie w czas ladowania listy.
    card_data = await asyncio.gather(*(_fetch_card_data(item) for item in items))
    covers = await get_cover_urls(db, [item.post for item in items])

    has_prev = current_page > 1
    has_next = current_page < total_pages
    context = {
        "request": request,
        "lang": ui_lang,
        "app_url": settings.APP_URL,
        "posts": [
            {
                "title": item.page.title,
                "slug": item.post.slug,
                "published_at": item.post.published_at or item.page.created_at,
                "summary": summary,
                "tags": list(item.post.tags or []),
                "reading_time": reading_time,
                "cover_image_url": covers.get(item.post.page_id),
                "lang": item.post.lang,
            }
            for item, (summary, reading_time) in zip(items, card_data, strict=True)
        ],
        "page": current_page,
        "total_pages": total_pages,
        "has_prev": has_prev,
        "has_next": has_next,
        "filter_tag": filter_tag,
        "filter_lang": filter_lang,
        "canonical_url": f"{settings.APP_URL.rstrip('/')}{_index_path(filter_tag, filter_lang, current_page)}",
        "prev_url": _index_path(filter_tag, filter_lang, current_page - 1) if has_prev else None,
        "next_url": _index_path(filter_tag, filter_lang, current_page + 1) if has_next else None,
        "lang_switch_url": _index_path(filter_tag, "pl"),
        "lang_switch_url_en": _index_path(filter_tag, "en"),
        "json_ld": dump_jsonld(build_blog_jsonld(items, base_url=settings.APP_URL)),
    }
    body = templates.get_template("blog/index.html").render(context)
    last_modified = max((item.page.updated_at for item in items), default=_EPOCH)
    return conditional_response(request, body, last_modified=last_modified, media_type="text/html; charset=utf-8")


@router.get("/{slug}", response_class=HTMLResponse, response_model=None)
async def public_page(slug: str, request: Request, lang: str | None = None, db: AsyncSession = Depends(get_db)) -> Response:
    """Publiczny render wpisu po `BlogPost.slug` -- bez sesji, bez RBAC, tylko is_public=True.

    Stary slug (`previous_slugs`) -> 301 na aktualny adres z zachowanym query stringiem.
    `Accept: text/markdown` -> bliźniak markdown (200/304). Odpowiedź HTML to 200 albo 304
    (`conditional_response`, `last_modified` = `updated_at` strony). `Vary: Accept` niosą odpowiedzi 200, 304
    i 301; 404 (`HTTPException`) jest bez nagłówków.
    Język interfejsu to `?lang=` (`pl`/`en`), a bez niego albo z inną wartością - `lang` wpisu.
    """
    lookup = await get_public_post_by_slug(db, slug)
    if lookup is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono strony")
    if lookup.redirect_slug is not None:
        target = f"/blog/{lookup.redirect_slug}"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=301, headers={"Vary": "Accept"})
    post, page = lookup.post, lookup.page
    if post is None or page is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono strony")
    ui_lang = lang if lang in POST_LANGS else post.lang

    if wants_markdown(request.headers.get("accept")):
        return await markdown_twin_response(request, db, post, page, headers={"Vary": "Accept"})

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
    assets = collect_block_assets(content)
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

    md_url = f"{settings.APP_URL.rstrip('/')}/blog/{post.slug}.md"
    json_ld = dump_jsonld(
        build_post_jsonld(
            post,
            page,
            base_url=settings.APP_URL,
            description=meta_description,
            word_count=len(render_post_markdown(content).split()),
            image_url=cover_image_url,
            structured=extract_structured_data(content),
        )
    )

    related = await list_related_posts(db, post)
    translations = await list_public_translations(db, post)
    base = settings.APP_URL.rstrip("/")
    hreflang_links = (
        [{"lang": post.lang, "url": f"{base}/blog/{post.slug}"}, *({"lang": item.lang, "url": f"{base}/blog/{item.slug}"} for item in translations)]
        if translations
        else []
    )
    translation_titles: dict[uuid.UUID, str] = {}
    if translations:
        title_rows = await db.execute(select(WikiPage.id, WikiPage.title).where(WikiPage.id.in_([item.page_id for item in translations])))
        translation_titles = {row.id: row.title for row in title_rows.all()}

    body = templates.get_template("blog/page.html").render(
        {
            "request": request,
            "lang": ui_lang,
            "app_url": settings.APP_URL,
            "page_title": page.title,
            "page_slug": post.slug,
            "content_html": content_html,
            "has_diagram": assets.has_diagram,
            "graphics": list(assets.graphics),
            "breadcrumbs": breadcrumbs,
            "children": children,
            "backlinks": backlinks,
            "updated_at": page.updated_at,
            "created_at": page.created_at,
            "published_at": post.published_at,
            "cover_image_url": cover_image_url,
            "meta_description": meta_description,
            "md_url": md_url,
            "json_ld": json_ld,
            "post_lang": post.lang,
            "description": post.description,
            "author_name": post.author_name,
            "last_verified_at": post.last_verified_at,
            "tags": list(post.tags or []),
            "reading_time": reading_time_minutes(render_post_markdown(content)),
            "toc": extract_toc(content_html),
            "related": [
                {
                    "title": item.page.title,
                    "slug": item.post.slug,
                    "description": item.post.description,
                    "published_at": item.post.published_at or item.page.created_at,
                    "tags": list(item.post.tags or []),
                }
                for item in related
            ],
            "translations": [
                {"lang": item.lang, "slug": item.slug, "title": translation_titles.get(item.page_id, item.slug)} for item in translations
            ],
            "hreflang_links": hreflang_links,
            "lang_switch_url": f"/blog/{post.slug}?lang=pl",
            "lang_switch_url_en": f"/blog/{post.slug}?lang=en",
        }
    )
    return conditional_response(
        request,
        body,
        last_modified=page.updated_at,
        media_type="text/html; charset=utf-8",
        headers={"Vary": "Accept", "Link": f'<{md_url}>; rel="alternate"; type="text/markdown"'},
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
