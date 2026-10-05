"""Warstwa AI publicznego bloga (MON-258): bliźniak markdown wpisu i lista w markdown.

Trasy (rejestrowane PRZED `blog.router`, inaczej `/blog/{slug}` połknęłoby adresy `.md`):
- `GET /blog/index.md` -- lista publicznych wpisów (`?lang=`, `?tag=`),
- `GET /blog/{slug}.md` -- wpis jako markdown z frontmatter YAML; stary slug daje 301 na nowy `.md`.

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
import math
import re
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
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
)
from monolynx.services.blog_blocks import render_post_markdown, strip_block_markers
from monolynx.services.wiki import extract_summary, get_page_content

logger = logging.getLogger("monolynx.blog_ai")

router = APIRouter(prefix="/blog", tags=["blog-ai"])

MARKDOWN_MEDIA_TYPE = "text/markdown; charset=utf-8"
_INDEX_LIMIT = 1000
_WORDS_PER_MINUTE = 200
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

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
        ("reading_time_minutes", str(max(1, math.ceil(word_count / _WORDS_PER_MINUTE)))),
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
