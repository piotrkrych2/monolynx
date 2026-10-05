"""Serwis bloga -- metadane wpisów (BlogPost, 1:1 ze stroną wiki), publiczny odczyt i lista (MON-254).

Konwencja jak `services/projects.py`: funkcje z użytkownikiem biorą `(db, user, project, ...)`,
błąd biznesowy to `ValueError`, commit robi serwis. Wyjątek: `ensure_post` nie commituje, bo
działa w transakcji `create_wiki_page` / `update_wiki_page`. Ten moduł NIE importuje
`services/wiki.py` (wiki importuje stąd `ensure_post`).

MON-258: czyste buildery JSON-LD (`build_post_jsonld`, `build_blog_jsonld`, `dump_jsonld`) i odczyt
publicznych wersji językowych wpisu (`list_public_translations`) dla warstwy AI (`blog_ai.py`).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_page import WikiPage
from monolynx.services.permissions import ensure_permission

DESCRIPTION_MAX_LENGTH = 300
POST_LANGS = ("pl", "en")
MAX_TAGS = 10
TAG_MAX_LENGTH = 50
SLUG_MAX_LENGTH = 255
AUTHOR_NAME_MAX_LENGTH = 255
DEFAULT_AUTHOR_NAME = "Zespół Monolynx"

_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_UNSET: Any = object()


@dataclass
class PublicPost:
    post: BlogPost
    page: WikiPage


@dataclass
class PublicPostLookup:
    post: BlogPost | None
    page: WikiPage | None
    redirect_slug: str | None


@dataclass
class PublicPostList:
    items: list[PublicPost]
    total: int
    page: int
    per_page: int
    total_pages: int


@dataclass
class BlogPostView:
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


@dataclass
class BlogPostDetail(BlogPostView):
    content: str


def build_post_view(post: BlogPost, page: WikiPage) -> BlogPostView:
    """Widok wpisu z metadanych i strony; adresy publiczne tylko dla strony publicznej."""
    public_url: str | None = None
    markdown_url: str | None = None
    if page.is_public:
        public_url = f"{settings.APP_URL.rstrip('/')}/blog/{post.slug}"
        markdown_url = f"{public_url}.md"
    return BlogPostView(
        page_id=page.id,
        title=page.title,
        slug=post.slug,
        previous_slugs=list(post.previous_slugs),
        description=post.description,
        published_at=post.published_at,
        author_name=post.author_name,
        tags=list(post.tags),
        lang=post.lang,
        translation_of_id=post.translation_of_id,
        cover_attachment_id=post.cover_attachment_id,
        last_verified_at=post.last_verified_at,
        is_public=page.is_public,
        public_url=public_url,
        markdown_url=markdown_url,
    )


async def list_posts(db: AsyncSession, user: User, project: Project, status: str | None = None) -> list[BlogPostView]:
    """Wpisy bloga projektu (także szkice). Wymaga `wiki:read`, sprawdzane przed walidacją `status`."""
    await ensure_permission(db, user.id, project.id, "wiki", "read")

    conditions: list[Any] = [WikiPage.project_id == project.id]
    if status is not None:
        if status == "published":
            conditions.append(WikiPage.is_public.is_(True))
        elif status == "draft":
            conditions.append(WikiPage.is_public.is_(False))
        else:
            raise ValueError("Nieprawidłowy status: dozwolone published, draft")

    result = await db.execute(
        select(BlogPost, WikiPage)
        .join(WikiPage, WikiPage.id == BlogPost.page_id)
        .where(*conditions)
        .order_by(BlogPost.published_at.desc().nulls_last(), BlogPost.slug.asc())
    )
    return [build_post_view(row[0], row[1]) for row in result.all()]


async def get_post(
    db: AsyncSession,
    user: User,
    project: Project,
    page_id: uuid.UUID,
    *,
    content_fn: Callable[[WikiPage], str] | None = None,
) -> BlogPostDetail:
    """Wpis z treścią. Czysty odczyt: brak wiersza `BlogPost` to błąd, nie zakładanie go."""
    await ensure_permission(db, user.id, project.id, "wiki", "read")

    page = (await db.execute(select(WikiPage).where(WikiPage.id == page_id, WikiPage.project_id == project.id))).scalar_one_or_none()
    if page is None:
        raise ValueError("Strona wiki nie istnieje")

    post = (await db.execute(select(BlogPost).where(BlogPost.page_id == page.id))).scalar_one_or_none()
    if post is None:
        raise ValueError("Wpis bloga nie istnieje")

    if content_fn is None:
        # Import w czasie wywołania: services/wiki.py importuje ensure_post stąd (cykl).
        from monolynx.services.wiki import get_page_content

        content_fn = get_page_content

    view = build_post_view(post, page)
    return BlogPostDetail(**vars(view), content=content_fn(page))


async def _slug_holder(db: AsyncSession, slug: str, exclude_page_id: uuid.UUID) -> uuid.UUID | None:
    """page_id INNEGO wpisu, który zajmuje slug (jako aktualny albo jako stary adres)."""
    result = await db.execute(
        select(BlogPost.page_id)
        .where(
            BlogPost.page_id != exclude_page_id,
            or_(BlogPost.slug == slug, BlogPost.previous_slugs.contains([slug])),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ensure_post(db: AsyncSession, page: WikiPage) -> BlogPost:
    """Zwróć wpis strony, zakładając go z wartościami domyślnymi, gdy go brak.

    Idempotentne i bez zmiany sluga istniejącego wpisu (zmiana tytułu strony nie rusza adresu).
    Dla strony publicznej uzupełnia brakujące `published_at`. Robi flush, NIE commituje:
    commit należy do wołającego.
    """
    result = await db.execute(select(BlogPost).where(BlogPost.page_id == page.id))
    post = result.scalar_one_or_none()
    now = datetime.now(UTC)

    if post is not None:
        if page.is_public and post.published_at is None:
            post.published_at = now
            await db.flush()
        return post

    if await _slug_holder(db, page.slug, page.id) is not None:
        raise ValueError(f"Slug '{page.slug}' jest już zajęty przez inny wpis bloga. Zmień tytuł strony.")

    post = BlogPost(page_id=page.id, slug=page.slug, published_at=now if page.is_public else None)
    db.add(post)
    await db.flush()
    return post


async def get_public_post_by_slug(db: AsyncSession, slug: str) -> PublicPostLookup | None:
    """Znajdź publiczny wpis po aktualnym slugu albo po starym adresie (wtedy `redirect_slug`).

    Filtr `WikiPage.is_public` jest w SQL w obu ścieżkach, więc wpis prywatny (także jego stary
    adres) daje None i nie ujawnia swojego istnienia.
    """
    current_result = await db.execute(
        select(BlogPost, WikiPage).join(WikiPage, WikiPage.id == BlogPost.page_id).where(BlogPost.slug == slug, WikiPage.is_public.is_(True))
    )
    row = current_result.first()
    if row is not None:
        return PublicPostLookup(post=row[0], page=row[1], redirect_slug=None)

    old_result = await db.execute(
        select(BlogPost.slug)
        .join(WikiPage, WikiPage.id == BlogPost.page_id)
        .where(BlogPost.previous_slugs.contains([slug]), WikiPage.is_public.is_(True))
        .limit(1)
    )
    current = old_result.scalar_one_or_none()
    if current is None:
        return None
    return PublicPostLookup(post=None, page=None, redirect_slug=current)


async def list_public_posts(db: AsyncSession, page: int, per_page: int, tag: str | None = None, lang: str | None = None) -> PublicPostList:
    """Publiczne wpisy od najnowszego (`published_at`, brak daty na końcu, potem slug)."""
    conditions: list[Any] = [WikiPage.is_public.is_(True)]
    if tag is not None:
        conditions.append(BlogPost.tags.contains([tag]))
    if lang is not None:
        conditions.append(BlogPost.lang == lang)

    total = (await db.execute(select(func.count(BlogPost.page_id)).join(WikiPage, WikiPage.id == BlogPost.page_id).where(*conditions))).scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)

    result = await db.execute(
        select(BlogPost, WikiPage)
        .join(WikiPage, WikiPage.id == BlogPost.page_id)
        .where(*conditions)
        .order_by(BlogPost.published_at.desc().nulls_last(), BlogPost.slug.asc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    items = [PublicPost(post=row[0], page=row[1]) for row in result.all()]
    return PublicPostList(items=items, total=total, page=page, per_page=per_page, total_pages=total_pages)


async def list_public_translations(db: AsyncSession, post: BlogPost) -> list[BlogPost]:
    """Publiczne wersje językowe wpisu (root grupy i rodzeństwo po `translation_of_id`), bez samego wpisu.

    Filtr `WikiPage.is_public` jest w SQL, więc prywatne tłumaczenie nie wycieka do frontmatter.
    Tylko odczyt.
    """
    root_id = post.translation_of_id or post.page_id
    result = await db.execute(
        select(BlogPost)
        .join(WikiPage, WikiPage.id == BlogPost.page_id)
        .where(
            WikiPage.is_public.is_(True),
            or_(BlogPost.page_id == root_id, BlogPost.translation_of_id == root_id),
            BlogPost.page_id != post.page_id,
        )
        .order_by(BlogPost.lang.asc(), BlogPost.slug.asc())
    )
    return list(result.scalars().all())


def build_post_jsonld(
    post: BlogPost,
    page: WikiPage,
    *,
    base_url: str,
    description: str,
    word_count: int | None = None,
    image_url: str | None = None,
) -> dict[str, Any]:
    """`BlogPosting` + `BreadcrumbList` (schema.org) wpisu; czysta funkcja, bez IO."""
    base = base_url.rstrip("/")
    url = f"{base}/blog/{post.slug}"
    posting: dict[str, Any] = {
        "@type": "BlogPosting",
        "headline": page.title,
        "description": description,
        "datePublished": (post.published_at or page.created_at).isoformat(),
        "dateModified": page.updated_at.isoformat(),
        "author": {"@type": "Person", "name": post.author_name or DEFAULT_AUTHOR_NAME},
        "inLanguage": post.lang,
        "mainEntityOfPage": {"@type": "WebPage", "@id": url},
        "url": url,
    }
    if post.tags:
        posting["keywords"] = list(post.tags)
    if word_count is not None:
        posting["wordCount"] = word_count
    if image_url is not None:
        posting["image"] = image_url
    breadcrumbs = {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Blog", "item": f"{base}/blog/"},
            {"@type": "ListItem", "position": 2, "name": page.title, "item": url},
        ],
    }
    return {"@context": "https://schema.org", "@graph": [posting, breadcrumbs]}


def build_blog_jsonld(posts: Sequence[PublicPost], *, base_url: str) -> dict[str, Any]:
    """`Blog` z `blogPost[]` dla listy wpisów; czysta funkcja, bez IO."""
    base = base_url.rstrip("/")
    entries: list[dict[str, Any]] = []
    for item in posts:
        entry: dict[str, Any] = {
            "@type": "BlogPosting",
            "headline": item.page.title,
            "url": f"{base}/blog/{item.post.slug}",
            "datePublished": (item.post.published_at or item.page.created_at).isoformat(),
            "inLanguage": item.post.lang,
        }
        if item.post.description:
            entry["description"] = item.post.description
        entries.append(entry)
    return {"@context": "https://schema.org", "@type": "Blog", "url": f"{base}/blog/", "blogPost": entries}


def dump_jsonld(data: dict[str, Any]) -> str:
    """JSON bezpieczny w `<script type="application/ld+json">`.

    Każde `<` -> `\\u003c` (usuwa `</script>` i `<!--`), a U+2028/U+2029 (surowe łamią literały JS
    w starszych parserach) -> escape; wynik nadal parsuje się `json.loads` do tych samych danych.
    """
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _validate_description(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Opis musi być tekstem")
    stripped: str = value.strip()
    if len(stripped) > DESCRIPTION_MAX_LENGTH:
        raise ValueError(f"Opis może mieć maksymalnie {DESCRIPTION_MAX_LENGTH} znaków")
    return stripped


def _validate_lang(value: Any) -> str:
    if value not in POST_LANGS:
        raise ValueError(f"Język musi być jednym z: {', '.join(POST_LANGS)}")
    return str(value)


def _validate_author_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Autor nie może być pusty")
    stripped: str = value.strip()
    if len(stripped) > AUTHOR_NAME_MAX_LENGTH:
        raise ValueError(f"Nazwa autora może mieć maksymalnie {AUTHOR_NAME_MAX_LENGTH} znaków")
    return stripped


def is_valid_tag(value: str) -> bool:
    """Kształt tagu: małe litery, cyfry i myślniki, maksymalnie `TAG_MAX_LENGTH` znaków (fullmatch, bez `\\n` na końcu)."""
    return len(value) <= TAG_MAX_LENGTH and _SLUG_PATTERN.fullmatch(value) is not None


def _validate_tags(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValueError("Tagi muszą być listą tekstów")
    unique = list(dict.fromkeys(value))
    if len(unique) > MAX_TAGS:
        raise ValueError(f"Wpis może mieć maksymalnie {MAX_TAGS} tagów")
    for tag in unique:
        if not is_valid_tag(tag):
            raise ValueError(f"Tag '{tag}' jest nieprawidłowy: małe litery, cyfry i myślniki, maksymalnie {TAG_MAX_LENGTH} znaków")
    return unique


def _validate_slug_format(value: Any) -> str:
    if not isinstance(value, str) or len(value) > SLUG_MAX_LENGTH or not _SLUG_PATTERN.match(value):
        raise ValueError(f"Slug jest nieprawidłowy: małe litery, cyfry i myślniki, maksymalnie {SLUG_MAX_LENGTH} znaków")
    return value


async def set_post_meta(
    db: AsyncSession,
    user: User,
    project: Project,
    page_id: uuid.UUID,
    *,
    slug: Any = _UNSET,
    description: Any = _UNSET,
    published_at: Any = _UNSET,
    author_name: Any = _UNSET,
    tags: Any = _UNSET,
    lang: Any = _UNSET,
    translation_of_id: Any = _UNSET,
    cover_attachment_id: Any = _UNSET,
    last_verified_at: Any = _UNSET,
) -> BlogPost:
    """Zapisz metadane wpisu (częściowo: `_UNSET` = bez zmian, `None` czyści pole nullable).

    Wymaga `wiki:write`; nie zmienia `is_public`. Brakujący wiersz zakłada przez `ensure_post`.
    Wszystkie walidacje biegną przed pierwszą mutacją, więc błąd nic nie zapisuje.
    """
    await ensure_permission(db, user.id, project.id, "wiki", "write")

    page = (await db.execute(select(WikiPage).where(WikiPage.id == page_id, WikiPage.project_id == project.id))).scalar_one_or_none()
    if page is None:
        raise ValueError("Strona wiki nie istnieje")

    post = (await db.execute(select(BlogPost).where(BlogPost.page_id == page.id))).scalar_one_or_none()
    current_slug = post.slug if post is not None else page.slug
    previous = list(post.previous_slugs) if post is not None else []

    changes: dict[str, Any] = {}
    if description is not _UNSET:
        changes["description"] = _validate_description(description)
    if lang is not _UNSET:
        changes["lang"] = _validate_lang(lang)
    if author_name is not _UNSET:
        changes["author_name"] = _validate_author_name(author_name)
    if tags is not _UNSET:
        changes["tags"] = _validate_tags(tags)
    if published_at is not _UNSET:
        changes["published_at"] = published_at
    if last_verified_at is not _UNSET:
        changes["last_verified_at"] = last_verified_at

    if slug is not _UNSET:
        new_slug = _validate_slug_format(slug)
        if new_slug != current_slug:
            if await _slug_holder(db, new_slug, page.id) is not None:
                raise ValueError(f"Slug '{new_slug}' jest już zajęty przez inny wpis bloga")
            if current_slug not in previous:
                previous.append(current_slug)
            changes["slug"] = new_slug
            # Nowa lista (nie mutacja w miejscu) -- inaczej SQLAlchemy nie zapisze zmiany JSONB.
            changes["previous_slugs"] = [s for s in previous if s != new_slug]

    if cover_attachment_id is not _UNSET:
        if cover_attachment_id is not None:
            attachment = (
                await db.execute(select(WikiAttachment.id).where(WikiAttachment.id == cover_attachment_id, WikiAttachment.wiki_page_id == page.id))
            ).scalar_one_or_none()
            if attachment is None:
                raise ValueError("Okładka musi być załącznikiem tej strony")
        changes["cover_attachment_id"] = cover_attachment_id

    if translation_of_id is not _UNSET:
        if translation_of_id is not None:
            if translation_of_id == page.id:
                raise ValueError("Wpis nie może być tłumaczeniem samego siebie")
            original = (await db.execute(select(BlogPost.page_id).where(BlogPost.page_id == translation_of_id))).scalar_one_or_none()
            if original is None:
                raise ValueError("Wpis wskazany jako oryginał nie istnieje")
        changes["translation_of_id"] = translation_of_id

    if post is None:
        post = await ensure_post(db, page)
    for field, value in changes.items():
        setattr(post, field, value)

    await db.commit()
    await db.refresh(post)
    return post
