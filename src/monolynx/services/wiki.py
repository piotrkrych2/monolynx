"""Serwis wiki -- CRUD stron, drzewo, rendering markdown."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import markdown
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.models.wiki_page import WikiPage
from monolynx.services.minio_client import delete_object, get_markdown, upload_markdown

logger = logging.getLogger("monolynx.wiki")


def generate_slug(title: str) -> str:
    """Generuj slug z tytulu strony."""
    slug = title.lower().strip()
    slug = re.sub(r"[ąà]", "a", slug)
    slug = re.sub(r"[ćč]", "c", slug)
    slug = re.sub(r"[ęè]", "e", slug)
    slug = re.sub(r"[łl]", "l", slug)
    slug = re.sub(r"[ńñ]", "n", slug)
    slug = re.sub(r"[óò]", "o", slug)
    slug = re.sub(r"[śš]", "s", slug)
    slug = re.sub(r"[źżž]", "z", slug)
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug or "strona"


async def _ensure_unique_slug(project_id: uuid.UUID, slug: str, db: AsyncSession, exclude_id: uuid.UUID | None = None) -> str:
    """Upewnij sie, ze slug jest unikalny w projekcie. Dodaje suffix jesli trzeba."""
    base_slug = slug
    counter = 1
    while True:
        conditions: list[Any] = [WikiPage.project_id == project_id, WikiPage.slug == slug]
        if exclude_id:
            conditions.append(WikiPage.id != exclude_id)
        result = await db.execute(select(WikiPage.id).where(*conditions).limit(1))
        if result.scalar_one_or_none() is None:
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


def render_markdown_html(raw: str) -> str:
    """Renderuj markdown do HTML."""
    result: str = markdown.markdown(
        raw,
        extensions=["fenced_code", "tables", "toc", "nl2br", "sane_lists"],
    )
    return result


async def create_wiki_page(
    *,
    project_id: uuid.UUID,
    project_slug: str,
    title: str,
    content: str,
    user_id: uuid.UUID,
    parent_id: uuid.UUID | None = None,
    position: int = 0,
    is_ai: bool = False,
    db: AsyncSession,
) -> WikiPage:
    """Utworz strone wiki -- zapis metadanych w DB, tresc w MinIO."""
    slug = generate_slug(title)
    slug = await _ensure_unique_slug(project_id, slug, db)

    page_id = uuid.uuid4()
    minio_path = upload_markdown(project_slug, page_id, content)

    page = WikiPage(
        id=page_id,
        project_id=project_id,
        parent_id=parent_id,
        title=title.strip(),
        slug=slug,
        position=position,
        minio_path=minio_path,
        is_ai_touched=is_ai,
        created_by_id=user_id,
        last_edited_by_id=user_id,
    )
    db.add(page)
    await db.commit()
    await db.refresh(page)

    # Best-effort embedding generation
    try:
        from monolynx.services.embeddings import update_page_embeddings

        await update_page_embeddings(page.id, content, db)
    except Exception:
        logger.warning("Nie udalo sie wygenerowac embeddingow dla strony %s", page.id)

    return page


async def update_wiki_page(
    *,
    page: WikiPage,
    project_slug: str,
    title: str | None = None,
    content: str | None = None,
    position: int | None = None,
    user_id: uuid.UUID,
    is_ai: bool = False,
    db: AsyncSession,
) -> WikiPage:
    """Aktualizuj strone wiki."""
    if title is not None and title.strip() != page.title:
        page.title = title.strip()
        new_slug = generate_slug(title)
        page.slug = await _ensure_unique_slug(page.project_id, new_slug, db, exclude_id=page.id)

    if content is not None:
        upload_markdown(project_slug, page.id, content)

    if position is not None:
        page.position = position

    page.last_edited_by_id = user_id
    if is_ai:
        page.is_ai_touched = True

    await db.commit()
    await db.refresh(page)

    # Best-effort embedding update
    if content is not None:
        try:
            from monolynx.services.embeddings import update_page_embeddings

            await update_page_embeddings(page.id, content, db)
        except Exception:
            logger.warning("Nie udalo sie zaktualizowac embeddingow dla strony %s", page.id)

    return page


async def delete_wiki_page(page: WikiPage, db: AsyncSession) -> None:
    """Usun strone wiki wraz z potomkami. Usuwa pliki z MinIO."""
    descendants = await _collect_descendants(page.id, db)
    all_pages = [page, *descendants]

    for p in all_pages:
        delete_object(p.minio_path)

    await db.delete(page)
    await db.commit()


async def _collect_descendants(page_id: uuid.UUID, db: AsyncSession) -> list[WikiPage]:
    """Zbierz wszystkie strony potomne (rekurencyjnie)."""
    result = await db.execute(select(WikiPage).where(WikiPage.parent_id == page_id))
    children = list(result.scalars().all())
    descendants = list(children)
    for child in children:
        descendants.extend(await _collect_descendants(child.id, db))
    return descendants


async def get_page_tree(project_id: uuid.UUID, db: AsyncSession) -> list[dict[str, Any]]:
    """Pobierz drzewo stron wiki jako zagniezdzona liste."""
    result = await db.execute(
        select(WikiPage)
        .options(selectinload(WikiPage.created_by), selectinload(WikiPage.last_edited_by))
        .where(WikiPage.project_id == project_id)
        .order_by(WikiPage.position, WikiPage.title)
    )
    all_pages = list(result.scalars().all())

    pages_by_parent: dict[uuid.UUID | None, list[WikiPage]] = {}
    for p in all_pages:
        pages_by_parent.setdefault(p.parent_id, []).append(p)

    def _build_tree(parent_id: uuid.UUID | None) -> list[dict[str, Any]]:
        children = pages_by_parent.get(parent_id, [])
        return [
            {
                "page": page,
                "children": _build_tree(page.id),
            }
            for page in children
        ]

    return _build_tree(None)


async def get_breadcrumbs(page: WikiPage, db: AsyncSession) -> list[WikiPage]:
    """Zbuduj breadcrumbs od roota do aktualnej strony."""
    crumbs: list[WikiPage] = [page]
    current = page
    while current.parent_id is not None:
        result = await db.execute(select(WikiPage).where(WikiPage.id == current.parent_id))
        parent = result.scalar_one_or_none()
        if parent is None:
            break
        crumbs.insert(0, parent)
        current = parent
    return crumbs


def get_page_content(page: WikiPage) -> str:
    """Pobierz tresc markdown strony z MinIO."""
    return get_markdown(page.minio_path)
