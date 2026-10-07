"""Warstwa AI publicznego bloga (MON-258): bliźniak markdown wpisu i lista w markdown.

Trasy (rejestrowane PRZED `blog.router`, inaczej `/blog/{slug}` połknęłoby adresy `.md`):
- `GET /blog/index.md` -- lista publicznych wpisów (`?lang=`, `?tag=`),
- `GET /blog/{slug}.md` -- wpis jako markdown z frontmatter YAML; stary slug daje 301 na nowy `.md`,
- `GET /blog/feed.xml` (Atom 1.0), `GET /blog/rss.xml` (RSS 2.0), `GET /blog/feed.json` (JSON Feed 1.1) --
  `FEED_LIMIT` najnowszych wpisów z pełną treścią (`?lang=`, XML wyłącznie przez ElementTree).

Poza routerem moduł wystawia dla `main.py` `build_llms_blog_section` (sekcja `## Blog` w `/llms.txt`)
i `build_llms_full` (`/llms-full.txt`, wpisy rozdzielone `LLMS_FULL_SEPARATOR`, limit `LLMS_FULL_MAX_BYTES`).

Ten sam dokument zwraca `blog.py` dla `Accept: text/markdown` (`markdown_twin_response`), więc
adres HTML i adres `.md` mają identyczne ciało i `ETag`. Odpowiedzi przechodzą przez
`conditional_response` (ETag + Last-Modified + 304). Moduł niczego nie zapisuje i nie importuje
z `blog.py` ani z `main.py` (cykl importów).

Znane ograniczenie: slug `index` jest zajęty przez trasę listy (rezerwacja sluga poza zakresem).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.blog_post import BlogPost
from monolynx.models.wiki_page import WikiPage
from monolynx.services.blog import (
    DEFAULT_AUTHOR_NAME,
    POST_LANGS,
    PublicPost,
    get_public_post_by_slug,
    is_valid_tag,
    list_public_posts,
    list_public_translations,
    reading_time_minutes,
    rewrite_attachment_urls,
)
from monolynx.services.blog_blocks import render_post_html, render_post_markdown, strip_block_markers
from monolynx.services.wiki import extract_summary, get_page_content

logger = logging.getLogger("monolynx.blog_ai")

router = APIRouter(prefix="/blog", tags=["blog-ai"])

MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
_INDEX_LIMIT = 1000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

FEED_LIMIT = 20
LLMS_BLOG_LIMIT = 10
LLMS_FULL_MAX_BYTES = 1_000_000
LLMS_FULL_SEPARATOR = "\n\n=====\n\n"

_FEED_TITLE = "Blog Monolynx"
_FEED_DESCRIPTION = "Wpisy bloga Monolynx: platforma projektowa AI-first, MCP, wiki i zarządzanie pracą."
_ATOM_NS = "http://www.w3.org/2005/Atom"
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
_XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'
# Znaki niedozwolone w XML 1.0 (sterujące poza \t \n \r oraz U+FFFE, U+FFFF); ElementTree ich nie odrzuca.
_XML_ILLEGAL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")

_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(``[^\n]*?``|`[^`\n]*`)")
_YAML_UNSAFE_CHARS = re.compile(r"[\u007f-\u009f\u2028\u2029]")
_ROOT_RELATIVE_TARGET = re.compile(r"(\]\()/(?!/)")


def _base_url() -> str:
    return settings.APP_URL.rstrip("/")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def wants_markdown(accept: str | None) -> bool:
    """True, gdy `Accept` wymienia dokładny typ `text/markdown` (nie `*/*`, nie `text/*`) z q > 0."""
    if not accept:
        return False
    for part in accept.split(","):
        media, *params = (piece.strip() for piece in part.split(";"))
        if media.lower() != "text/markdown":
            continue
        quality = 1.0
        for param in params:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            return True
    return False


def conditional_response(
    request: Request,
    body: str,
    *,
    last_modified: datetime,
    media_type: str = MARKDOWN_MEDIA_TYPE,
    headers: dict[str, str] | None = None,
) -> Response:
    """Odpowiedź z mocnym `ETag` i `Last-Modified`; pasujący `If-None-Match` daje 304 bez ciała.

    ETag = sha256 z (`last_modified` + treść), więc zmienia się zarówno przy edycji treści, jak i
    przy zmianie `updated_at`. `If-Modified-Since` celowo pominięte (ETag wystarcza).
    """
    modified = _utc(last_modified)
    digest = hashlib.sha256((modified.isoformat() + "\n" + body).encode("utf-8")).hexdigest()
    etag = f'"{digest[:32]}"'
    response_headers = {**(headers or {}), "ETag": etag, "Last-Modified": format_datetime(modified, usegmt=True)}

    candidates = [item.strip() for item in request.headers.get("if-none-match", "").split(",")]
    for candidate in candidates:
        if candidate == "*" or candidate.removeprefix("W/") == etag:
            return Response(status_code=304, headers=response_headers)
    return Response(content=body, media_type=media_type, headers=response_headers)


def absolutize_markdown_urls(markdown_text: str, base_url: str) -> str:
    """Cele `](/...)` (linki i obrazki) dostają prefiks `base_url`; kod fenced i inline bez zmian.

    Adresy `//host`, `http(s)://`, `mailto:`, `#kotwica` i ścieżki bez wiodącego `/` zostają.
    """
    base = base_url.rstrip("/")
    out: list[str] = []
    fence: str | None = None
    for line in markdown_text.split("\n"):
        match = _FENCE_OPEN.match(line)
        if fence is not None:
            if match is not None and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) and not line.strip().strip(fence[0]):
                fence = None
            out.append(line)
        elif match is not None:
            fence = match.group(1)
            out.append(line)
        else:
            segments = _INLINE_CODE.split(line)
            # re.split z grupą daje naprzemiennie tekst i kod: kod (indeksy nieparzyste) zostaje.
            out.append("".join(seg if i % 2 else _ROOT_RELATIVE_TARGET.sub(lambda m: m.group(1) + base + "/", seg) for i, seg in enumerate(segments)))
    return "\n".join(out)


def _yaml(value: Any) -> str:
    """Wartość jako YAML flow: JSON (stringi w cudzysłowie) z escape DEL, C1 (U+007F-U+009F), U+2028 i U+2029.

    PyYAML odrzuca te znaki surowe w strumieniu (a część jest końcem linii), więc bez escape tytuł
    z takim znakiem dawałby dokument nieparsowalny."""
    dumped = json.dumps(value, ensure_ascii=False)
    return _YAML_UNSAFE_CHARS.sub(lambda match: f"\\u{ord(match.group()):04x}", dumped)


def render_post_markdown_document(post: BlogPost, page: WikiPage, content: str, translations: list[dict[str, str]]) -> str:
    """Pełny dokument `.md` wpisu: frontmatter YAML + treść z bezwzględnymi adresami.

    Jedyne źródło dokumentu dla trasy `.md` i dla `Accept: text/markdown` w `blog.py`. Serializacja
    ręczna (bez PyYAML w kodzie produkcyjnym); pola zawsze obecne, w stałej kolejności.
    """
    base = _base_url()
    # Adresy załączników tej strony wychodzą publiczne (`/blog/<slug>/attachments/...`), nie dashboardowe (MON-271).
    content = rewrite_attachment_urls(content, page_id=page.id, post_slug=post.slug)
    body = absolutize_markdown_urls(render_post_markdown(content), base)
    word_count = len(body.split())
    fields: list[tuple[str, str]] = [
        ("title", _yaml(page.title)),
        ("description", _yaml(post.description or extract_summary(strip_block_markers(content)))),
        ("url", _yaml(f"{base}/blog/{post.slug}")),
        ("lang", _yaml(post.lang)),
        ("author", _yaml(post.author_name or DEFAULT_AUTHOR_NAME)),
        ("published", _yaml((post.published_at or page.created_at).isoformat())),
        ("modified", _yaml(page.updated_at.isoformat())),
        ("last_verified", _yaml(post.last_verified_at.isoformat()) if post.last_verified_at is not None else "null"),
        ("tags", _yaml(list(post.tags or []))),
        ("translations", _yaml(translations)),
        ("reading_time_minutes", str(reading_time_minutes(body))),
        ("word_count", str(word_count)),
    ]
    front = "".join(f"{key}: {value}\n" for key, value in fields)
    return f"---\n{front}---\n\n{body}\n" if body else f"---\n{front}---\n"


async def markdown_twin_response(
    request: Request,
    db: AsyncSession,
    post: BlogPost,
    page: WikiPage,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    """Bliźniak markdown wpisu (200 albo 304). Błąd storage -> 404, nigdy 500.

    `get_page_content` woła blokujące IO (MinIO), więc idzie w executorze; endpoint jest publiczny,
    więc anonimowy czytelnik nie może odróżnić "storage padło" od "nie ma wpisu".
    """
    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, get_page_content, page)
    except Exception:
        logger.exception("Blad pobierania tresci strony wiki z MinIO dla bliznaka .md (page_id=%s)", page.id)
        raise HTTPException(status_code=404, detail="Nie znaleziono strony") from None

    base = _base_url()
    translations = [{"lang": item.lang, "url": f"{base}/blog/{item.slug}"} for item in await list_public_translations(db, post)]
    document = render_post_markdown_document(post, page, content, translations)
    return conditional_response(request, document, last_modified=page.updated_at, headers=headers)


def _escape_link_text(title: str) -> str:
    collapsed = " ".join(title.split())
    return collapsed.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


@router.get("/index.md", response_model=None)
async def index_markdown(
    request: Request,
    lang: str | None = None,
    tag: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Lista publicznych wpisów jako markdown (najnowsze pierwsze), bez paginacji.

    Pusty `lang`/`tag` = brak filtra. `lang` spoza `POST_LANGS` i `tag` o złym kształcie dają pustą listę
    bez zapytania do bazy (wartość z URL, np. NUL, wywróciłaby PostgreSQL 500-tką).
    """
    lang = lang or None
    tag = tag or None
    items: list[PublicPost] = []
    if (lang is None or lang in POST_LANGS) and (tag is None or is_valid_tag(tag)):
        items = (await list_public_posts(db, 1, _INDEX_LIMIT, tag=tag, lang=lang)).items
    base = _base_url()
    lines: list[str] = []
    for item in items:
        published = _utc(item.post.published_at or item.page.created_at).date().isoformat()
        line = f"- [{_escape_link_text(item.page.title)}]({base}/blog/{item.post.slug}.md) - {published}"
        description = " ".join((item.post.description or "").split())
        if description:
            line += f" - {description}"
        if item.post.tags:
            line += f" (tagi: {', '.join(item.post.tags)})"
        lines.append(line)

    body = "# Blog\n\n" + ("\n".join(lines) if lines else "Brak wpisów.") + "\n"
    last_modified = max((_utc(item.page.updated_at) for item in items), default=_EPOCH)
    return conditional_response(request, body, last_modified=last_modified)


@router.get("/{slug}.md", response_model=None)
async def post_markdown(slug: str, request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    """Wpis jako markdown z frontmatter; prywatny, nieistniejący i błąd storage -> 404."""
    lookup = await get_public_post_by_slug(db, slug)
    if lookup is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono strony")
    if lookup.redirect_slug is not None:
        target = f"/blog/{lookup.redirect_slug}.md"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=301)
    if lookup.post is None or lookup.page is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono strony")
    return await markdown_twin_response(request, db, lookup.post, lookup.page)


# Treść wpisu w pamięci procesu: page_id -> (updated_at, treść). Feedy i lista /blog/ czytają ją z MinIO przy każdym
# GET, a ETag liczony z gotowego ciała tej pracy nie oszczędza. `updated_at` zmienia się przy każdym zapisie treści
# (services/wiki.py ustawia go jawnie), więc nowszy zapis unieważnia wpis; jeden wpis na page_id.
_POST_CONTENT_CACHE_MAX = 256
_post_content_cache: dict[uuid.UUID, tuple[datetime, str]] = {}


def clear_post_content_cache() -> None:
    """Czyści cache treści wpisów (testy; produkcyjnie unieważnia go `updated_at`)."""
    _post_content_cache.clear()


async def load_post_content(page: WikiPage) -> str:
    """Treść wpisu z cache'u po `(page_id, updated_at)`, inaczej z MinIO (executor).

    Błąd storage propaguje się do wywołującego i nie trafia do cache'u: następne żądanie próbuje ponownie.
    """
    cached = _post_content_cache.get(page.id)
    if cached is not None and cached[0] == page.updated_at:
        return cached[1]
    loop = asyncio.get_running_loop()
    content = await loop.run_in_executor(None, get_page_content, page)
    _post_content_cache.pop(page.id, None)
    if len(_post_content_cache) >= _POST_CONTENT_CACHE_MAX:
        del _post_content_cache[next(iter(_post_content_cache))]
    _post_content_cache[page.id] = (page.updated_at, content)
    return content


@dataclass
class _FeedEntry:
    post: BlogPost
    page: WikiPage
    content: str


def _xml_text(value: str) -> str:
    return _XML_ILLEGAL_CHARS.sub("", value)


def _text_element(parent: ET.Element, tag: str, value: str, **attrs: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attrs)
    element.text = _xml_text(value)
    return element


def _xml_document(root: ET.Element) -> str:
    return _XML_DECLARATION + ET.tostring(root, encoding="unicode") + "\n"


async def _load_feed_entries(db: AsyncSession, lang: str | None) -> tuple[list[_FeedEntry], datetime]:
    """Najnowsze publiczne wpisy z treścią i `Last-Modified` (max `updated_at` listy, `_EPOCH` gdy pusta).

    Błąd storage jednego wpisu pomija ten wpis (feed zawsze 200); `lang` spoza `POST_LANGS` daje pustą listę
    bez zapytania, jak w `index_markdown`.
    """
    lang = lang or None
    items: list[PublicPost] = []
    if lang is None or lang in POST_LANGS:
        items = (await list_public_posts(db, 1, FEED_LIMIT, lang=lang)).items

    results = await asyncio.gather(*(load_post_content(item.page) for item in items), return_exceptions=True)
    entries: list[_FeedEntry] = []
    for item, result in zip(items, results, strict=True):
        if isinstance(result, BaseException):
            logger.error("Blad pobierania tresci strony wiki z MinIO dla feedu (page_id=%s)", item.page.id, exc_info=result)
            continue
        entries.append(
            _FeedEntry(post=item.post, page=item.page, content=rewrite_attachment_urls(result, page_id=item.page.id, post_slug=item.post.slug))
        )
    last_modified = max((_utc(item.page.updated_at) for item in items), default=_EPOCH)
    return entries, last_modified


@router.get("/feed.xml", response_model=None)
async def atom_feed(request: Request, lang: str | None = None, db: AsyncSession = Depends(get_db)) -> Response:
    """Atom 1.0: pełny HTML wpisu w `content type="html"`, zajawka w `summary`."""
    entries, last_modified = await _load_feed_entries(db, lang)
    base = _base_url()
    feed = ET.Element("feed", {"xmlns": _ATOM_NS})
    _text_element(feed, "title", _FEED_TITLE)
    _text_element(feed, "id", f"{base}/blog/feed.xml")
    _text_element(feed, "updated", _utc(last_modified).isoformat())
    ET.SubElement(feed, "link", {"rel": "self", "href": f"{base}/blog/feed.xml"})
    ET.SubElement(feed, "link", {"rel": "alternate", "href": f"{base}/blog/"})
    for entry in entries:
        url = f"{base}/blog/{entry.post.slug}"
        element = ET.SubElement(feed, "entry")
        _text_element(element, "title", entry.page.title)
        _text_element(element, "id", url)
        ET.SubElement(element, "link", {"rel": "alternate", "href": url})
        _text_element(element, "updated", _utc(entry.page.updated_at).isoformat())
        if entry.post.published_at is not None:
            _text_element(element, "published", _utc(entry.post.published_at).isoformat())
        if entry.post.author_name:
            _text_element(ET.SubElement(element, "author"), "name", entry.post.author_name)
        if entry.post.description:
            _text_element(element, "summary", entry.post.description)
        _text_element(element, "content", render_post_html(entry.content), type="html")
    return conditional_response(request, _xml_document(feed), last_modified=last_modified, media_type="application/atom+xml; charset=utf-8")


@router.get("/rss.xml", response_model=None)
async def rss_feed(request: Request, lang: str | None = None, db: AsyncSession = Depends(get_db)) -> Response:
    """RSS 2.0: pełny HTML wpisu w `content:encoded`, zajawka w `description`."""
    entries, last_modified = await _load_feed_entries(db, lang)
    base = _base_url()
    # Prefiks `content:` zapisany literalnie, bez `ET.register_namespace` (rejestr jest wspólny dla całego procesu).
    rss = ET.Element("rss", {"version": "2.0", "xmlns:content": _CONTENT_NS})
    channel = ET.SubElement(rss, "channel")
    _text_element(channel, "title", _FEED_TITLE)
    _text_element(channel, "link", f"{base}/blog/")
    _text_element(channel, "description", _FEED_DESCRIPTION)
    for entry in entries:
        url = f"{base}/blog/{entry.post.slug}"
        item = ET.SubElement(channel, "item")
        _text_element(item, "title", entry.page.title)
        _text_element(item, "link", url)
        _text_element(item, "guid", url, isPermaLink="true")
        if entry.post.published_at is not None:
            _text_element(item, "pubDate", format_datetime(_utc(entry.post.published_at), usegmt=True))
        if entry.post.description:
            _text_element(item, "description", entry.post.description)
        _text_element(item, "content:encoded", render_post_html(entry.content))
    return conditional_response(request, _xml_document(rss), last_modified=last_modified, media_type="application/rss+xml; charset=utf-8")


@router.get("/feed.json", response_model=None)
async def json_feed(request: Request, lang: str | None = None, db: AsyncSession = Depends(get_db)) -> Response:
    """JSON Feed 1.1: `content_text` to markdown wpisu bez frontmattera (te same bezwzględne adresy co `.md`)."""
    entries, last_modified = await _load_feed_entries(db, lang)
    base = _base_url()
    items: list[dict[str, Any]] = []
    for entry in entries:
        url = f"{base}/blog/{entry.post.slug}"
        item: dict[str, Any] = {
            "id": url,
            "url": url,
            "title": entry.page.title,
            "content_text": absolutize_markdown_urls(render_post_markdown(entry.content), base),
        }
        if entry.post.description:
            item["summary"] = entry.post.description
        if entry.post.published_at is not None:
            item["date_published"] = _utc(entry.post.published_at).isoformat()
        item["date_modified"] = _utc(entry.page.updated_at).isoformat()
        if entry.post.tags:
            item["tags"] = list(entry.post.tags)
        item["language"] = entry.post.lang
        items.append(item)
    feed = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": _FEED_TITLE,
        "home_page_url": f"{base}/blog/",
        "feed_url": f"{base}/blog/feed.json",
        "items": items,
    }
    return conditional_response(
        request, json.dumps(feed, ensure_ascii=False), last_modified=last_modified, media_type="application/feed+json; charset=utf-8"
    )


async def build_llms_blog_section(db: AsyncSession) -> str:
    """Sekcja `## Blog` do `/llms.txt`: `LLMS_BLOG_LIMIT` najnowszych wpisów z adresami `.md`; pusty string bez wpisów.

    Czyta tylko bazę (opis wpisu), nie MinIO. Wyjątek z bazy wychodzi do wołającego, który zostawia samą treść statyczną.
    """
    items = (await list_public_posts(db, 1, LLMS_BLOG_LIMIT)).items
    if not items:
        return ""
    base = _base_url()
    lines = ["## Blog", ""]
    for item in items:
        line = f"- [{' '.join(item.page.title.split())}]({base}/blog/{item.post.slug}.md)"
        description = " ".join((item.post.description or "").split())
        if description:
            line += f": {description}"
        lines.append(line)
    lines.append(f"- [All posts]({base}/blog/index.md): the full post list as markdown")
    lines.append(f"- [Atom feed]({base}/blog/feed.xml): the latest posts with full content")
    return "\n".join(lines) + "\n"


# Gotowy `/llms-full.txt` w pamięci procesu: (odcisk metadanych, dokument). Dokument kosztuje po odczycie z MinIO
# na każdy publiczny wpis, a endpoint jest anonimowy i często odpytywany przez boty.
_llms_full_cache: tuple[str, str] | None = None


def clear_llms_full_cache() -> None:
    """Wyrzuca gotowy `/llms-full.txt` z pamięci procesu (testy; produkcyjnie unieważnia go odcisk)."""
    global _llms_full_cache
    _llms_full_cache = None


async def _llms_full_fingerprint(db: AsyncSession) -> str:
    """Odcisk wejścia dokumentu: jedno zapytanie o metadane publicznych wpisów (bez MinIO) i bieżący limit bajtów.

    `WikiPage.updated_at` łapie edycję treści (zapis treści jawnie go podbija), reszta pól to metadane wchodzące do
    frontmatter, listy tłumaczeń i kolejności wpisów. Cofnięcie publikacji usuwa wiersz z odcisku.
    """
    rows = (
        await db.execute(
            select(
                BlogPost.page_id,
                BlogPost.slug,
                BlogPost.lang,
                BlogPost.translation_of_id,
                BlogPost.published_at,
                BlogPost.description,
                BlogPost.tags,
                BlogPost.author_name,
                BlogPost.last_verified_at,
                WikiPage.title,
                WikiPage.created_at,
                WikiPage.updated_at,
            )
            .join(WikiPage, WikiPage.id == BlogPost.page_id)
            .where(WikiPage.is_public.is_(True))
            .order_by(BlogPost.page_id)
        )
    ).all()
    payload = json.dumps([[*map(str, row)] for row in rows] + [LLMS_FULL_MAX_BYTES, _base_url()], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def build_llms_full(db: AsyncSession) -> str:
    """Dokument `/llms-full.txt` z cache'u w pamięci procesu, gdy odcisk metadanych się nie zmienił, inaczej zbudowany od nowa.

    Dokument z pominiętym wpisem (błąd storage) nie trafia do cache'u: usterka nie może przeżyć do następnej zmiany metadanych.
    """
    global _llms_full_cache
    fingerprint = await _llms_full_fingerprint(db)
    cached = _llms_full_cache
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    document, complete = await _build_llms_full_document(db)
    if complete:
        _llms_full_cache = (fingerprint, document)
    return document


async def _build_llms_full_document(db: AsyncSession) -> tuple[str, bool]:
    """Pełna treść publicznych wpisów (`render_post_markdown_document`), najnowsze pierwsze, do `LLMS_FULL_MAX_BYTES`.
    Drugi element wyniku to `False`, gdy któryś wpis pominięto z powodu błędu storage.

    Wpis, który nie zmieści się w limicie (razem z linią obcięcia), i wszystkie następne są pomijane w całości;
    wtedy dokument kończy linia `> Truncated: <pominięte> of <wszystkie> posts omitted ...`. Wpis z błędem storage
    jest pomijany, ale wchodzi do `<wszystkie>`. Wpisy są czytane po kolei i pętla staje przy pierwszym przekroczeniu,
    więc nie czytamy z MinIO wpisów, które i tak nie wejdą.
    """
    items = (await list_public_posts(db, 1, _INDEX_LIMIT)).items
    base = _base_url()
    header = f"# Monolynx Blog: full content\n\nPublic posts as markdown with YAML frontmatter, newest first. Index: {base}/blog/index.md\n"
    if not items:
        return header + "\nNo public posts yet.\n", True

    total = len(items)

    def truncation_line(omitted: int) -> str:
        return f"\n> Truncated: {omitted} of {total} posts omitted (size limit {LLMS_FULL_MAX_BYTES} bytes). Full list: {base}/blog/index.md\n"

    # Rezerwa na linię obcięcia (najdłuższy wariant: wszystkie pominięte), żeby całość nigdy nie przekroczyła limitu.
    reserve = len(truncation_line(total).encode())
    separator_size = len(LLMS_FULL_SEPARATOR.encode())
    loop = asyncio.get_running_loop()
    parts = [header]
    size = len(header.encode())
    kept = 0
    truncated = False
    complete = True
    for index, item in enumerate(items):
        try:
            content = await loop.run_in_executor(None, get_page_content, item.page)
        except Exception:
            logger.exception("Blad pobierania tresci strony wiki z MinIO dla llms-full (page_id=%s)", item.page.id)
            complete = False
            continue
        translations = [{"lang": t.lang, "url": f"{base}/blog/{t.slug}"} for t in await list_public_translations(db, item.post)]
        document = render_post_markdown_document(item.post, item.page, content, translations)
        needed = size + separator_size + len(document.encode())
        if needed + (reserve if index < total - 1 else 0) > LLMS_FULL_MAX_BYTES:
            truncated = True
            break
        parts.append(document)
        size = needed
        kept += 1

    body = LLMS_FULL_SEPARATOR.join(parts)
    return (body + truncation_line(total - kept) if truncated else body), complete
