"""Dashboard -- modul wiki (strony markdown, drzewo, CRUD, upload obrazkow)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.wiki_page import WikiPage
from monolynx.services.embeddings import is_enabled as embeddings_enabled
from monolynx.services.embeddings import search_wiki_pages
from monolynx.services.minio_client import get_attachment, upload_attachment
from monolynx.services.wiki import (
    create_wiki_page,
    delete_wiki_page,
    get_breadcrumbs,
    get_page_content,
    get_page_tree,
    render_markdown_html,
    update_wiki_page,
)

from .helpers import _get_user_id, flash, render_project_page, templates

router = APIRouter(prefix="/dashboard", tags=["wiki"])

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB


async def _get_project(slug: str, db: AsyncSession) -> Project | None:
    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    return result.scalar_one_or_none()


async def _get_wiki_page(page_id: uuid.UUID, project_id: uuid.UUID, db: AsyncSession) -> WikiPage | None:
    result = await db.execute(
        select(WikiPage)
        .options(selectinload(WikiPage.created_by), selectinload(WikiPage.last_edited_by))
        .where(WikiPage.id == page_id, WikiPage.project_id == project_id)
    )
    return result.scalar_one_or_none()


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

    return await render_project_page(
        request,
        "dashboard/wiki/page_form.html",
        {
            "project": project,
            "active_module": "wiki",
            "page": None,
            "parent": None,
            "error": None,
            "form_data": None,
        },
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

    form = await request.form()
    title = str(form.get("title", "")).strip()
    content = str(form.get("content", ""))
    position_raw = str(form.get("position", "0")).strip()

    error = None
    if not title:
        error = "Tytul jest wymagany"

    try:
        position = int(position_raw)
    except ValueError:
        position = 0

    if error:
        return templates.TemplateResponse(
            request,
            "dashboard/wiki/page_form.html",
            {
                "project": project,
                "active_module": "wiki",
                "page": None,
                "parent": None,
                "error": error,
                "form_data": {"title": title, "content": content, "position": position},
            },
        )

    page = await create_wiki_page(
        project_id=project.id,
        project_slug=project.slug,
        title=title,
        content=content,
        user_id=user_id,
        position=position,
        db=db,
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

    parent = await _get_wiki_page(page_id, project.id, db)
    if parent is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    return await render_project_page(
        request,
        "dashboard/wiki/page_form.html",
        {
            "project": project,
            "active_module": "wiki",
            "page": None,
            "parent": parent,
            "error": None,
            "form_data": None,
        },
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

    parent = await _get_wiki_page(page_id, project.id, db)
    if parent is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    form = await request.form()
    title = str(form.get("title", "")).strip()
    content = str(form.get("content", ""))
    position_raw = str(form.get("position", "0")).strip()

    error = None
    if not title:
        error = "Tytul jest wymagany"

    try:
        position = int(position_raw)
    except ValueError:
        position = 0

    if error:
        return templates.TemplateResponse(
            request,
            "dashboard/wiki/page_form.html",
            {
                "project": project,
                "active_module": "wiki",
                "page": None,
                "parent": parent,
                "error": error,
                "form_data": {"title": title, "content": content, "position": position},
            },
        )

    page = await create_wiki_page(
        project_id=project.id,
        project_slug=project.slug,
        title=title,
        content=content,
        user_id=user_id,
        parent_id=parent.id,
        position=position,
        db=db,
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

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    raw_content = get_page_content(page)
    rendered_html = render_markdown_html(raw_content)
    breadcrumbs = await get_breadcrumbs(page, db)

    # Pobierz podstrony
    children_result = await db.execute(select(WikiPage).where(WikiPage.parent_id == page.id).order_by(WikiPage.position, WikiPage.title))
    children = list(children_result.scalars().all())

    return await render_project_page(
        request,
        "dashboard/wiki/page_detail.html",
        {
            "project": project,
            "page": page,
            "rendered_html": rendered_html,
            "breadcrumbs": breadcrumbs,
            "children": children,
            "active_module": "wiki",
        },
        db=db,
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

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    raw_content = get_page_content(page)

    return await render_project_page(
        request,
        "dashboard/wiki/page_form.html",
        {
            "project": project,
            "active_module": "wiki",
            "page": page,
            "parent": None,
            "error": None,
            "form_data": {"title": page.title, "content": raw_content, "position": page.position},
        },
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

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return HTMLResponse("Strona nie istnieje", status_code=404)

    form = await request.form()
    title = str(form.get("title", "")).strip()
    content = str(form.get("content", ""))
    position_raw = str(form.get("position", str(page.position))).strip()

    error = None
    if not title:
        error = "Tytul jest wymagany"

    try:
        position = int(position_raw)
    except ValueError:
        position = page.position

    if error:
        return templates.TemplateResponse(
            request,
            "dashboard/wiki/page_form.html",
            {
                "project": project,
                "active_module": "wiki",
                "page": page,
                "parent": None,
                "error": error,
                "form_data": {"title": title, "content": content, "position": position},
            },
        )

    await update_wiki_page(
        page=page,
        project_slug=project.slug,
        title=title,
        content=content,
        position=position,
        user_id=user_id,
        db=db,
    )

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
    db: AsyncSession = Depends(get_db),
) -> JSONResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    project = await _get_project(slug, db)
    if project is None:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    if file is None or file.filename is None:
        return JSONResponse({"error": "Brak pliku"}, status_code=400)

    data = await file.read()
    if len(data) > MAX_ATTACHMENT_SIZE:
        return JSONResponse({"error": "Plik za duzy (max 10 MB)"}, status_code=400)

    content_type = file.content_type or "application/octet-stream"
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

    minio_path = f"{project.slug}/attachments/{filename}"
    try:
        data, content_type = get_attachment(minio_path)
    except Exception:
        return Response("Plik nie istnieje", status_code=404)

    return Response(content=data, media_type=content_type)
