"""Dashboard -- modul wiki (strony markdown, drzewo, CRUD, upload obrazkow).

MON-264: formularz strony niesie sekcje "Wpis na blogu" (metadane `BlogPost` zapisywane przez
`services.blog.set_post_meta`, kontekst `blog_meta` i pokrewne), menu "Wstaw blok" z rejestru
`blog_blocks` (`block_templates`), upload obrazka z edytora przypiety do strony (`?page_id=`,
adres publiczny dla opublikowanego wpisu) oraz podglad szkicu `.../pages/{page_id}/blog-preview`
w wygladzie publicznym, z `X-Robots-Tag: noindex` i bez zapisu do bazy.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import uuid
from datetime import UTC, date, datetime
from functools import partial
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_file import WikiFile
from monolynx.models.wiki_page import WikiPage
from monolynx.services.blog import DEFAULT_AUTHOR_NAME, extract_toc, reading_time_minutes, set_post_meta
from monolynx.services.blog_blocks import (
    collect_block_assets,
    list_block_templates,
    render_post_html,
    render_post_markdown,
    strip_block_markers,
)
from monolynx.services.embeddings import is_enabled as embeddings_enabled
from monolynx.services.embeddings import search_wiki_pages
from monolynx.services.minio_client import delete_object as minio_delete_object
from monolynx.services.minio_client import get_attachment as minio_get_attachment
from monolynx.services.minio_client import upload_attachment
from monolynx.services.minio_client import upload_object as minio_upload_object
from monolynx.services.permissions import has_blog_permission, require_permission
from monolynx.services.wiki import (
    create_wiki_page,
    delete_wiki_page,
    extract_summary,
    get_backlinks,
    get_breadcrumbs,
    get_page_content,
    get_page_tree,
    render_markdown_html,
    update_wiki_page,
)

from .helpers import _get_user_id, flash, get_session_user, render_project_page, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["wiki"])

MAX_ATTACHMENT_SIZE = 200 * 1024 * 1024  # 200 MB


async def _get_project(slug: str, db: AsyncSession) -> Project | None:
    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    return result.scalar_one_or_none()


async def _get_wiki_page(page_id: uuid.UUID, project_id: uuid.UUID, db: AsyncSession) -> WikiPage | None:
    result = await db.execute(
        select(WikiPage)
        .options(selectinload(WikiPage.created_by), selectinload(WikiPage.last_edited_by), selectinload(WikiPage.attachments))
        .where(WikiPage.id == page_id, WikiPage.project_id == project_id)
    )
    return result.scalar_one_or_none()


# --- Kontekst formularza strony i metadane wpisu bloga ---

_BLOG_FORM_FIELDS = (
    "blog_slug",
    "blog_description",
    "blog_author_name",
    "blog_tags",
    "blog_lang",
    "blog_translation_of_id",
    "blog_cover_attachment_id",
    "blog_last_verified_at",
)


def _creation_form_context(
    slug: str,
    project: Project | None,
    parent: WikiPage | None,
    *,
    error: str | None,
    form_data: dict[str, Any] | None,
    can_edit_blog: bool,
) -> dict[str, Any]:
    """Kontekst formularzy tworzenia: bez sekcji "Wpis na blogu" (strona jeszcze nie istnieje), z menu bloków.

    `project` bywa None po rollbacku w gałęzi błędu serwisu (formularz renderuje się wtedy jak przed MON-264),
    dlatego adres uploadu liczy się ze `slug` z trasy.
    """
    return {
        "project": project,
        "active_module": "wiki",
        "page": None,
        "parent": parent,
        "error": error,
        "form_data": form_data,
        "blog_meta": None,
        "blog_is_post": False,
        "blog_translation_candidates": [],
        "can_edit_blog": can_edit_blog,
        "block_templates": list_block_templates(),
        "image_upload_url": f"/dashboard/{slug}/wiki/upload",
    }


async def _get_blog_post(page_id: uuid.UUID, db: AsyncSession) -> BlogPost | None:
    return (await db.execute(select(BlogPost).where(BlogPost.page_id == page_id))).scalar_one_or_none()


async def _edit_form_context(
    project: Project,
    page: WikiPage,
    db: AsyncSession,
    *,
    error: str | None,
    form_data: dict[str, Any],
    can_edit_blog: bool,
    typed_blog_meta: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Kontekst formularza edycji; `typed_blog_meta` (wartości wpisane po błędzie) wygrywa z wartościami z bazy."""
    post = await _get_blog_post(page.id, db)
    if typed_blog_meta is not None:
        blog_meta = typed_blog_meta
    elif post is not None:
        blog_meta = {
            "slug": post.slug,
            "description": post.description,
            "author_name": post.author_name,
            "tags": ", ".join(post.tags or []),
            "lang": post.lang,
            "translation_of_id": str(post.translation_of_id) if post.translation_of_id else "",
            "cover_attachment_id": str(post.cover_attachment_id) if post.cover_attachment_id else "",
            "last_verified_at": post.last_verified_at.isoformat() if post.last_verified_at else "",
        }
    else:
        blog_meta = {
            "slug": page.slug,
            "description": "",
            "author_name": DEFAULT_AUTHOR_NAME,
            "tags": "",
            "lang": "pl",
            "translation_of_id": "",
            "cover_attachment_id": "",
            "last_verified_at": "",
        }
    candidates = await db.execute(
        select(BlogPost.page_id, WikiPage.title, BlogPost.lang)
        .join(WikiPage, WikiPage.id == BlogPost.page_id)
        .where(WikiPage.project_id == project.id, BlogPost.page_id != page.id)
        .order_by(WikiPage.title)
    )
    return {
        "project": project,
        "active_module": "wiki",
        "page": page,
        "parent": None,
        "error": error,
        "form_data": form_data,
        "attachments": list(page.attachments),
        "can_edit": True,
        "blog_meta": blog_meta,
        "blog_is_post": post is not None or page.is_public,
        "blog_translation_candidates": [{"page_id": str(row.page_id), "title": row.title, "lang": row.lang} for row in candidates.all()],
        "can_edit_blog": can_edit_blog,
        "block_templates": list_block_templates(),
        "image_upload_url": f"/dashboard/{project.slug}/wiki/upload?page_id={page.id}",
    }


def _read_blog_form(form: Any) -> dict[str, str]:
    """Surowe pola sekcji "Wpis na blogu" pod kluczami kontekstu `blog_meta` (tagi i identyfikatory jako tekst)."""
    raw = {name: str(form.get(name, "")) for name in _BLOG_FORM_FIELDS}
    return {name.removeprefix("blog_"): value for name, value in raw.items()}


def _blog_meta_kwargs(typed: dict[str, str]) -> dict[str, Any]:
    """Pola formularza -> argumenty `set_post_meta`; pusty slug i autor to "bez zmian", błąd parsowania to `ValueError`."""
    kwargs: dict[str, Any] = {
        "description": typed["description"],
        "tags": [tag.strip() for tag in typed["tags"].split(",") if tag.strip()],
        "lang": typed["lang"].strip(),
    }
    if typed["slug"].strip():
        kwargs["slug"] = typed["slug"].strip()
    if typed["author_name"].strip():
        kwargs["author_name"] = typed["author_name"].strip()
    for field, label in (("translation_of_id", "tłumaczenia"), ("cover_attachment_id", "okładki")):
        value = typed[field].strip()
        try:
            kwargs[field] = uuid.UUID(value) if value else None
        except ValueError:
            raise ValueError(f"Nieprawidłowy identyfikator {label}") from None
    verified = typed["last_verified_at"].strip()
    try:
        kwargs["last_verified_at"] = date.fromisoformat(verified) if verified else None
    except ValueError:
        raise ValueError("Data weryfikacji ma nieprawidłowy format (RRRR-MM-DD)") from None
    return kwargs


def _blog_meta_touched(typed: dict[str, str], page: WikiPage) -> bool:
    """Czy użytkownik ruszył sekcję: coś niepustego i innego niż domyślna wartość formularza (slug strony, domyślny autor, `pl`)."""
    return bool(
        (typed["slug"].strip() and typed["slug"].strip() != page.slug)
        or typed["description"].strip()
        or (typed["author_name"].strip() and typed["author_name"].strip() != DEFAULT_AUTHOR_NAME)
        or typed["tags"].strip()
        or typed["translation_of_id"].strip()
        or typed["cover_attachment_id"].strip()
        or typed["last_verified_at"].strip()
        or (typed["lang"].strip() and typed["lang"].strip() != "pl")
    )


# --- Lista stron (drzewo) ---


@router.get("/{slug}/wiki/", response_class=HTMLResponse, response_model=None)
async def wiki_index(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")

    tree = await get_page_tree(project.id, db)

    return await render_project_page(
        request,
        "dashboard/wiki/index.html",
        {
            "project": project,
            "tree": tree,
            "active_module": "wiki",
        },
        db=db,
    )


# --- Wyszukiwanie semantyczne ---


@router.get("/{slug}/wiki/search", response_class=HTMLResponse, response_model=None)
async def wiki_search(
    request: Request,
    slug: str,
    q: str = "",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")

    results: list[dict[str, object]] = []
    enabled = embeddings_enabled()

    if q.strip() and enabled:
        results = await search_wiki_pages(project.id, q.strip(), db)

    return await render_project_page(
        request,
        "dashboard/wiki/search.html",
        {
            "project": project,
            "active_module": "wiki",
            "query": q,
            "results": results,
            "embeddings_enabled": enabled,
        },
        db=db,
    )


# --- Tworzenie strony (root) ---


@router.get("/{slug}/wiki/pages/create", response_class=HTMLResponse, response_model=None)
async def wiki_page_create_form(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")
    can_edit_blog = await has_blog_permission(db, user_id)

    return await render_project_page(
        request,
        "dashboard/wiki/page_form.html",
        _creation_form_context(slug, project, None, error=None, form_data=None, can_edit_blog=can_edit_blog),
        db=db,
    )


@router.post("/{slug}/wiki/pages/create", response_class=HTMLResponse, response_model=None)
async def wiki_page_create(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "write")
    can_edit_blog = await has_blog_permission(db, user_id)

    form = await request.form()
    title = str(form.get("title", "")).strip()
    content = str(form.get("content", ""))
    position_raw = str(form.get("position", "0")).strip()
    is_public = form.get("is_public") is not None

    error = None
    if not title:
        error = "Tytul jest wymagany"

    try:
        position = int(position_raw)
    except ValueError:
        position = 0

    form_data = {"title": title, "content": content, "position": position, "is_public": is_public}
    if error:
        return templates.TemplateResponse(
            request,
            "dashboard/wiki/page_form.html",
            _creation_form_context(slug, project, None, error=error, form_data=form_data, can_edit_blog=can_edit_blog),
        )

    try:
        page = await create_wiki_page(
            project_id=project.id,
            project_slug=project.slug,
            title=title,
            content=content,
            user_id=user_id,
            position=position,
            is_public=is_public,
            db=db,
        )
    except ValueError as exc:
        # Po rollbacku w services/wiki.py obiekt `project` jest expired -- przeladuj go,
        # zanim trafi do Jinja2 (MissingGreenlet przy leniwym ladowaniu w sync renderze).
        flash(request, str(exc), "error")
        project = await _get_project(slug, db)
        return templates.TemplateResponse(
            request,
            "dashboard/wiki/page_form.html",
            _creation_form_context(slug, project, None, error=None, form_data=form_data, can_edit_blog=can_edit_blog),
        )

    flash(request, "Strona wiki zostala utworzona")
    return RedirectResponse(url=f"/dashboard/{slug}/wiki/pages/{page.id}", status_code=303)


# --- Tworzenie podstrony ---


@router.get("/{slug}/wiki/pages/{page_id}/create", response_class=HTMLResponse, response_model=None)
async def wiki_child_create_form(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")
    can_edit_blog = await has_blog_permission(db, user_id)

    parent = await _get_wiki_page(page_id, project.id, db)
    if parent is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    return await render_project_page(
        request,
        "dashboard/wiki/page_form.html",
        _creation_form_context(slug, project, parent, error=None, form_data=None, can_edit_blog=can_edit_blog),
        db=db,
    )


@router.post("/{slug}/wiki/pages/{page_id}/create", response_class=HTMLResponse, response_model=None)
async def wiki_child_create(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "write")
    can_edit_blog = await has_blog_permission(db, user_id)

    parent = await _get_wiki_page(page_id, project.id, db)
    if parent is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    form = await request.form()
    title = str(form.get("title", "")).strip()
    content = str(form.get("content", ""))
    position_raw = str(form.get("position", "0")).strip()
    is_public = form.get("is_public") is not None

    error = None
    if not title:
        error = "Tytul jest wymagany"

    try:
        position = int(position_raw)
    except ValueError:
        position = 0

    form_data = {"title": title, "content": content, "position": position, "is_public": is_public}
    if error:
        return templates.TemplateResponse(
            request,
            "dashboard/wiki/page_form.html",
            _creation_form_context(slug, project, parent, error=error, form_data=form_data, can_edit_blog=can_edit_blog),
        )

    try:
        page = await create_wiki_page(
            project_id=project.id,
            project_slug=project.slug,
            title=title,
            content=content,
            user_id=user_id,
            parent_id=parent.id,
            position=position,
            is_public=is_public,
            db=db,
        )
    except ValueError as exc:
        # Po rollbacku w services/wiki.py obiekty `project`/`parent` sa expired -- przeladuj je,
        # zanim trafia do Jinja2 (MissingGreenlet przy leniwym ladowaniu w sync renderze).
        flash(request, str(exc), "error")
        project = await _get_project(slug, db)
        parent = await _get_wiki_page(page_id, project.id, db) if project else None
        return templates.TemplateResponse(
            request,
            "dashboard/wiki/page_form.html",
            _creation_form_context(slug, project, parent, error=None, form_data=form_data, can_edit_blog=can_edit_blog),
        )

    flash(request, "Podstrona wiki zostala utworzona")
    return RedirectResponse(url=f"/dashboard/{slug}/wiki/pages/{page.id}", status_code=303)


# --- Widok strony ---


@router.get("/{slug}/wiki/pages/{page_id}", response_class=HTMLResponse, response_model=None)
async def wiki_page_detail(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    raw_content = get_page_content(page)
    rendered_html = render_markdown_html(raw_content)
    breadcrumbs = await get_breadcrumbs(page, db)

    # Pobierz podstrony
    children_result = await db.execute(select(WikiPage).where(WikiPage.parent_id == page.id).order_by(WikiPage.position, WikiPage.title))
    children = list(children_result.scalars().all())

    # Pobierz backlinki tylko gdy Metoda LLM Wiki jest włączona
    backlinks = await get_backlinks(page.id, db) if project.wiki_llm_enabled else []

    return await render_project_page(
        request,
        "dashboard/wiki/page_detail.html",
        {
            "project": project,
            "page": page,
            "rendered_html": rendered_html,
            "breadcrumbs": breadcrumbs,
            "children": children,
            "attachments": list(page.attachments),
            "can_edit": True,
            "active_module": "wiki",
            "backlinks": backlinks,
        },
        db=db,
    )


# --- Podglad szkicu wpisu bloga ---


@router.get("/{slug}/wiki/pages/{page_id}/blog-preview", response_class=HTMLResponse, response_model=None)
async def wiki_blog_preview(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Podglad strony w wygladzie publicznego wpisu (`blog/page.html`), takze dla strony prywatnej.

    Tylko z sesja i `wiki:read`, `X-Robots-Tag: noindex`. Nic nie zapisuje: brak wiersza `BlogPost`
    to wartosci domyslne liczone w pamieci (bez `ensure_post`, bez commita).
    """
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    loop = asyncio.get_running_loop()
    try:
        content = await loop.run_in_executor(None, get_page_content, page)
    except Exception:
        logger.exception("Blad pobierania tresci strony wiki z MinIO dla podgladu (page_id=%s)", page.id)
        return HTMLResponse("Strona nie istnieje", status_code=404)

    post = await _get_blog_post(page.id, db)
    content_html = render_post_html(content)
    assets = collect_block_assets(content)
    description = post.description if post is not None else ""
    return templates.TemplateResponse(
        request,
        "blog/page.html",
        {
            "lang": post.lang if post is not None else "pl",
            "app_url": settings.APP_URL,
            "page_title": page.title,
            "page_slug": post.slug if post is not None else page.slug,
            "content_html": content_html,
            "has_diagram": assets.has_diagram,
            "graphics": list(assets.graphics),
            "updated_at": page.updated_at,
            "created_at": page.created_at,
            "published_at": post.published_at if post is not None else None,
            "meta_description": description or extract_summary(strip_block_markers(content)),
            "post_lang": post.lang if post is not None else "pl",
            "description": description,
            "author_name": post.author_name if post is not None else DEFAULT_AUTHOR_NAME,
            "last_verified_at": post.last_verified_at if post is not None else None,
            "tags": list(post.tags or []) if post is not None else [],
            "reading_time": reading_time_minutes(render_post_markdown(content)),
            "toc": extract_toc(content_html),
            "preview": True,
            "preview_back_url": f"/dashboard/{project.slug}/wiki/pages/{page.id}",
        },
        headers={"X-Robots-Tag": "noindex"},
    )


# --- Edycja strony ---


@router.get("/{slug}/wiki/pages/{page_id}/edit", response_class=HTMLResponse, response_model=None)
async def wiki_page_edit_form(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")
    can_edit_blog = await has_blog_permission(db, user_id)

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    raw_content = get_page_content(page)

    form_data = {"title": page.title, "content": raw_content, "position": page.position, "is_public": page.is_public}
    return await render_project_page(
        request,
        "dashboard/wiki/page_form.html",
        await _edit_form_context(project, page, db, error=None, form_data=form_data, can_edit_blog=can_edit_blog),
        db=db,
    )


@router.post("/{slug}/wiki/pages/{page_id}/edit", response_class=HTMLResponse, response_model=None)
async def wiki_page_edit(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "write")
    can_edit_blog = await has_blog_permission(db, user_id)

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    form = await request.form()
    title = str(form.get("title", "")).strip()
    content = str(form.get("content", ""))
    position_raw = str(form.get("position", str(page.position))).strip()
    is_public_checked = form.get("is_public") is not None
    # Konto bez uprawnienia blog:write ma wyłączony checkbox, więc brak pola to nie prośba o cofnięcie
    # publikacji: None zostawia stan strony (zaznaczone pole to zmiana, którą serwis odrzuci).
    is_public: bool | None = is_public_checked if can_edit_blog or is_public_checked else None

    error = None
    if not title:
        error = "Tytul jest wymagany"

    try:
        position = int(position_raw)
    except ValueError:
        position = page.position

    form_data = {"title": title, "content": content, "position": position, "is_public": is_public_checked if can_edit_blog else page.is_public}
    typed_blog_meta = _read_blog_form(form) if form.get("blog_meta") is not None else None

    if error:
        return templates.TemplateResponse(
            request,
            "dashboard/wiki/page_form.html",
            await _edit_form_context(
                project, page, db, error=error, form_data=form_data, can_edit_blog=can_edit_blog, typed_blog_meta=typed_blog_meta
            ),
        )

    async def rerender_after_error(exc: ValueError) -> HTMLResponse:
        # Po rollbacku w services/wiki.py obiekty `project`/`page` sa expired -- przeladuj je,
        # zanim trafia do Jinja2 (MissingGreenlet przy leniwym ladowaniu w sync renderze).
        flash(request, str(exc), "error")
        fresh_project = await _get_project(slug, db)
        fresh_page = await _get_wiki_page(page_id, fresh_project.id, db) if fresh_project else None
        if fresh_project is None or fresh_page is None:
            return templates.TemplateResponse(
                request,
                "dashboard/wiki/page_form.html",
                {"project": fresh_project, "active_module": "wiki", "page": fresh_page, "parent": None, "error": None, "form_data": form_data},
            )
        return templates.TemplateResponse(
            request,
            "dashboard/wiki/page_form.html",
            await _edit_form_context(
                fresh_project, fresh_page, db, error=None, form_data=form_data, can_edit_blog=can_edit_blog, typed_blog_meta=typed_blog_meta
            ),
        )

    # Metadane PRZED trescia: set_post_meta waliduje wszystko przed pierwsza mutacja, wiec odrzucone
    # metadane nie zostawiaja zapisanej tresci ani brudnej sesji (stad bez db.rollback() w tej galezi).
    if typed_blog_meta is not None and (
        is_public_checked or _blog_meta_touched(typed_blog_meta, page) or await _get_blog_post(page.id, db) is not None
    ):
        user = await get_session_user(request, db)
        if user is None:
            return RedirectResponse(url="/auth/login", status_code=303)
        try:
            await set_post_meta(db, user, project, page.id, **_blog_meta_kwargs(typed_blog_meta))
        except ValueError as exc:
            return await rerender_after_error(exc)

    try:
        await update_wiki_page(
            page=page,
            project_slug=project.slug,
            title=title,
            content=content,
            position=position,
            user_id=user_id,
            is_public=is_public,
            db=db,
        )
    except ValueError as exc:
        return await rerender_after_error(exc)

    flash(request, "Strona wiki zostala zaktualizowana")
    return RedirectResponse(url=f"/dashboard/{slug}/wiki/pages/{page.id}", status_code=303)


# --- Usuwanie strony ---


@router.post("/{slug}/wiki/pages/{page_id}/delete", response_model=None)
async def wiki_page_delete(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "delete")

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    await delete_wiki_page(page, db)

    flash(request, "Strona wiki zostala usunieta")
    return RedirectResponse(url=f"/dashboard/{slug}/wiki/", status_code=303)


# --- Upload obrazkow (EasyMDE) ---


@router.post("/{slug}/wiki/upload", response_model=None)
async def wiki_upload(
    request: Request,
    slug: str,
    file: UploadFile | None = None,
    page_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse | RedirectResponse:
    """Upload obrazka z edytora (EasyMDE). Z `page_id` plik jest zalacznikiem tej strony, bez - stary plik projektu."""
    user_id = _get_user_id(request)
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    project = await _get_project(slug, db)
    if project is None:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "write")

    page: WikiPage | None = None
    if page_id is not None:
        page = await _get_wiki_page(page_id, project.id, db)
        if page is None:
            return JSONResponse({"error": "Strona nie istnieje"}, status_code=404)

    if file is None or file.filename is None:
        return JSONResponse({"error": "Brak pliku"}, status_code=400)

    data = await file.read()
    if len(data) > MAX_ATTACHMENT_SIZE:
        return JSONResponse({"error": "Plik za duzy (max 200 MB)"}, status_code=400)

    content_type = file.content_type or "application/octet-stream"
    if page is not None:
        return await _store_page_image(db, project, page, file.filename, data, content_type)
    minio_path = upload_attachment(project.slug, file.filename, data, content_type)

    url = f"/dashboard/{slug}/wiki/attachments/{minio_path.split('/')[-1]}"
    # EasyMDE oczekuje formatu {"data": {"filePath": "..."}}
    return JSONResponse({"data": {"filePath": url}})


# --- Serwowanie zalacznikow ---


@router.get("/{slug}/wiki/attachments/{filename}", response_model=None)
async def wiki_attachment(
    request: Request,
    slug: str,
    filename: str,
    db: AsyncSession = Depends(get_db),
) -> Response | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return Response("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")

    minio_path = f"{project.slug}/attachments/{filename}"
    try:
        data, content_type = minio_get_attachment(minio_path)
    except Exception:
        return Response("Plik nie istnieje", status_code=404)

    return Response(content=data, media_type=content_type)


# --- Zalaczniki do stron wiki ---


async def _get_membership(db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectMember | None:
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _save_page_attachment(
    db: AsyncSession, project_slug: str, page_id: uuid.UUID, filename: str, data: bytes, content_type: str
) -> WikiAttachment:
    """Zapisuje plik w MinIO (executor) i zalacznik strony w bazie z commitem; blad storage wychodzi wyjatkiem, nic nie trafia do bazy."""
    safe_filename = os.path.basename(filename)
    safe_filename = re.sub(r"[^\w\s\-.]", "_", safe_filename).strip()
    if not safe_filename:
        safe_filename = "attachment"

    ext = safe_filename.rsplit(".", 1)[-1] if "." in safe_filename else "bin"
    now = datetime.now(UTC)
    date_prefix = f"{now.year}/{now.month:02d}/{now.day:02d}"
    storage_path = f"{project_slug}/wiki-attachments/{page_id}/{date_prefix}/{uuid.uuid4().hex}.{ext}"

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(minio_upload_object, storage_path, data, content_type))

    attachment = WikiAttachment(
        wiki_page_id=page_id,
        filename=safe_filename,
        storage_path=storage_path,
        mime_type=content_type,
        size=len(data),
        created_via_ai=False,
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(attachment)
    return attachment


async def _store_page_image(db: AsyncSession, project: Project, page: WikiPage, filename: str, data: bytes, content_type: str) -> JSONResponse:
    """Upload z edytora do strony: zalacznik + adres dla EasyMDE (publiczny dla opublikowanego wpisu, inaczej dashboardowy)."""
    try:
        attachment = await _save_page_attachment(db, project.slug, page.id, filename, data, content_type)
    except Exception:
        logger.exception("Blad uploadu obrazka z edytora (page_id=%s)", page.id)
        return JSONResponse({"error": "Blad uploadu pliku"}, status_code=500)

    name = quote(attachment.filename)
    post = await _get_blog_post(page.id, db) if page.is_public else None
    if post is not None:
        # Wpis opublikowany: adres od razu publiczny, anonimowy czytelnik nie ma sesji dashboardu.
        url = f"/blog/{post.slug}/attachments/{attachment.id}/{name}"
    else:
        # Szkic: adres dashboardowy; render publiczny (`rewrite_attachment_urls`) przepisze go po publikacji.
        url = f"/dashboard/{project.slug}/wiki/pages/{page.id}/attachments/{attachment.id}/{name}"
    return JSONResponse({"data": {"filePath": url}})


@router.post(
    "/{slug}/wiki/pages/{page_id}/attachments/upload",
    response_model=None,
)
async def wiki_page_attachment_upload(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    filepond: UploadFile,
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse | JSONResponse | RedirectResponse:
    """Upload zalacznika do strony wiki (FilePond compatible)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    project = await _get_project(slug, db)
    if project is None:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "write")

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return JSONResponse({"error": "Strona nie istnieje"}, status_code=404)

    if filepond.filename is None or filepond.filename == "":
        return JSONResponse({"error": "Brak nazwy pliku"}, status_code=400)

    data = await filepond.read()
    if len(data) > MAX_ATTACHMENT_SIZE:
        return JSONResponse({"error": "Plik za duzy (max 200 MB)"}, status_code=400)

    content_type = filepond.content_type or "application/octet-stream"
    try:
        attachment = await _save_page_attachment(db, project.slug, page_id, filepond.filename, data, content_type)
    except Exception:
        return JSONResponse({"error": "Blad uploadu pliku"}, status_code=500)

    return PlainTextResponse(str(attachment.id), status_code=200)


@router.get(
    "/{slug}/wiki/pages/{page_id}/attachments/{attachment_id}/{filename}",
    response_model=None,
)
async def wiki_page_attachment_serve(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    attachment_id: uuid.UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse | HTMLResponse | RedirectResponse:
    """Serwowanie zalacznika strony wiki."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")

    result = await db.execute(
        select(WikiAttachment)
        .join(WikiPage, WikiAttachment.wiki_page_id == WikiPage.id)
        .where(
            WikiAttachment.id == attachment_id,
            WikiPage.id == page_id,
            WikiPage.project_id == project.id,
        )
    )
    attachment = result.scalar_one_or_none()
    if attachment is None:
        return HTMLResponse("Attachment not found", status_code=404)

    try:
        loop = asyncio.get_running_loop()
        data, content_type = await loop.run_in_executor(None, minio_get_attachment, attachment.storage_path)
    except Exception:
        return HTMLResponse("Blad pobierania pliku", status_code=500)

    safe_name = attachment.filename.replace('"', "_").replace("\\", "_")
    return StreamingResponse(
        io.BytesIO(data),
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@router.post(
    "/{slug}/wiki/pages/{page_id}/attachments/{attachment_id}/delete",
    response_model=None,
)
async def wiki_page_attachment_delete(
    request: Request,
    slug: str,
    page_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Usuwanie zalacznika strony wiki."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "delete")

    result = await db.execute(
        select(WikiAttachment)
        .join(WikiPage, WikiAttachment.wiki_page_id == WikiPage.id)
        .where(
            WikiAttachment.id == attachment_id,
            WikiPage.id == page_id,
            WikiPage.project_id == project.id,
        )
    )
    attachment = result.scalar_one_or_none()
    if attachment is None:
        return HTMLResponse("Attachment not found", status_code=404)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, minio_delete_object, attachment.storage_path)
    except Exception:
        pass

    await db.delete(attachment)
    await db.commit()

    return HTMLResponse("", status_code=200)


# --- Globalne pliki Wiki ---


@router.get("/{slug}/wiki/files/", response_model=None)
async def wiki_files_list(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> Response | RedirectResponse:
    """Lista globalnych plikow projektu w repozytorium Wiki."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")

    result = await db.execute(select(WikiFile).where(WikiFile.project_id == project.id).order_by(WikiFile.created_at.desc()))
    files = result.scalars().all()

    return await render_project_page(
        request,
        "dashboard/wiki/files.html",
        {
            "project": project,
            "files": files,
            "active_module": "wiki_files",
        },
        db=db,
    )


@router.post("/{slug}/wiki/files/upload", response_model=None)
async def wiki_file_upload(
    request: Request,
    slug: str,
    filepond: UploadFile,
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse | JSONResponse | RedirectResponse:
    """Upload globalnego pliku wiki (FilePond compatible)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    project = await _get_project(slug, db)
    if project is None:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "write")

    if filepond.filename is None or filepond.filename == "":
        return JSONResponse({"error": "Brak nazwy pliku"}, status_code=400)

    safe_filename = os.path.basename(filepond.filename)
    safe_filename = re.sub(r"[^\w\s\-.]", "_", safe_filename).strip()
    if not safe_filename:
        safe_filename = "file"

    data = await filepond.read()
    if len(data) > MAX_ATTACHMENT_SIZE:
        return JSONResponse({"error": "Plik za duzy (max 200 MB)"}, status_code=400)

    content_type = filepond.content_type or "application/octet-stream"
    ext = safe_filename.rsplit(".", 1)[-1] if "." in safe_filename else "bin"
    now = datetime.now(UTC)
    date_prefix = f"{now.year}/{now.month:02d}/{now.day:02d}"
    storage_path = f"{project.slug}/wiki-files/{date_prefix}/{uuid.uuid4().hex}.{ext}"

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(minio_upload_object, storage_path, data, content_type))
    except Exception:
        return JSONResponse({"error": "Blad uploadu pliku"}, status_code=500)

    wiki_file = WikiFile(
        project_id=project.id,
        filename=safe_filename,
        storage_path=storage_path,
        mime_type=content_type,
        size=len(data),
        created_via_ai=False,
    )
    db.add(wiki_file)
    await db.commit()
    await db.refresh(wiki_file)

    return PlainTextResponse(str(wiki_file.id), status_code=200)


@router.get("/{slug}/wiki/files/{file_id}/{filename}", response_model=None)
async def wiki_file_serve(
    request: Request,
    slug: str,
    file_id: uuid.UUID,
    filename: str,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse | HTMLResponse | RedirectResponse:
    """Serwowanie globalnego pliku wiki."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")

    result = await db.execute(
        select(WikiFile).where(
            WikiFile.id == file_id,
            WikiFile.project_id == project.id,
        )
    )
    wiki_file = result.scalar_one_or_none()
    if wiki_file is None:
        return HTMLResponse("File not found", status_code=404)

    try:
        loop = asyncio.get_running_loop()
        data, content_type = await loop.run_in_executor(None, minio_get_attachment, wiki_file.storage_path)
    except Exception:
        return HTMLResponse("Blad pobierania pliku", status_code=500)

    safe_name = wiki_file.filename.replace('"', "_").replace("\\", "_")
    return StreamingResponse(
        io.BytesIO(data),
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{safe_name}"'},
    )


@router.post("/{slug}/wiki/files/{file_id}/description", response_model=None)
async def wiki_file_update_description(
    request: Request,
    slug: str,
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response | RedirectResponse:
    """Aktualizacja opisu globalnego pliku wiki (HTMX inline edit)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "write")

    result = await db.execute(
        select(WikiFile).where(
            WikiFile.id == file_id,
            WikiFile.project_id == project.id,
        )
    )
    wiki_file = result.scalar_one_or_none()
    if wiki_file is None:
        return HTMLResponse("File not found", status_code=404)

    form = await request.form()
    description = str(form.get("description", "")).strip()
    wiki_file.description = description or None
    await db.commit()

    # Zwróć zaktualizowaną komórkę opisu (HTMX swap)
    escaped = (description or "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return Response(
        content=f'<span class="file-description-text cursor-pointer hover:text-indigo-400 transition"'
        f' hx-get="/dashboard/{slug}/wiki/files/{file_id}/description/edit"'
        f' hx-swap="outerHTML">'
        f"{escaped}</span>",
        status_code=200,
    )


@router.get("/{slug}/wiki/files/{file_id}/description/edit", response_model=None)
async def wiki_file_description_edit_form(
    request: Request,
    slug: str,
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response | RedirectResponse:
    """Formularz inline edycji opisu pliku (HTMX)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "read")

    result = await db.execute(
        select(WikiFile).where(
            WikiFile.id == file_id,
            WikiFile.project_id == project.id,
        )
    )
    wiki_file = result.scalar_one_or_none()
    if wiki_file is None:
        return HTMLResponse("File not found", status_code=404)

    desc_val = (wiki_file.description or "").replace('"', "&quot;")
    return Response(
        content=f'<form hx-post="/dashboard/{slug}/wiki/files/{file_id}/description"'
        f' hx-swap="outerHTML" class="flex items-center gap-2">'
        f'<input type="text" name="description" value="{desc_val}"'
        f' class="text-sm bg-gray-700 border border-gray-600 rounded px-2 py-1 text-gray-200 focus:border-indigo-500 focus:outline-none w-48"'
        f' placeholder="Dodaj opis..." autofocus>'
        f'<button type="submit" class="text-indigo-400 hover:text-indigo-300 text-xs">OK</button>'
        f"</form>",
        status_code=200,
    )


@router.post("/{slug}/wiki/files/{file_id}/delete", response_model=None)
async def wiki_file_delete(
    request: Request,
    slug: str,
    file_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Usuwanie globalnego pliku wiki."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "wiki", "delete")

    result = await db.execute(
        select(WikiFile).where(
            WikiFile.id == file_id,
            WikiFile.project_id == project.id,
        )
    )
    wiki_file = result.scalar_one_or_none()
    if wiki_file is None:
        return HTMLResponse("File not found", status_code=404)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, minio_delete_object, wiki_file.storage_path)
    except Exception:
        pass

    await db.delete(wiki_file)
    await db.commit()

    flash(request, "Plik zostal usuniety")
    return RedirectResponse(url=f"/dashboard/{slug}/wiki/files/", status_code=303)
