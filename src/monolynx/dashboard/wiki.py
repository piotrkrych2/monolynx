"""Dashboard -- modul wiki (strony markdown, drzewo, CRUD, upload obrazkow)."""

from __future__ import annotations

import asyncio
import io
import os
import re
import uuid
from datetime import UTC, datetime
from functools import partial

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_file import WikiFile
from monolynx.models.wiki_page import WikiPage
from monolynx.services.embeddings import is_enabled as embeddings_enabled
from monolynx.services.embeddings import search_wiki_pages
from monolynx.services.minio_client import delete_object as minio_delete_object
from monolynx.services.minio_client import get_attachment as minio_get_attachment
from monolynx.services.minio_client import upload_attachment
from monolynx.services.minio_client import upload_object as minio_upload_object
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
            "attachments": list(page.attachments),
            "can_edit": True,
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
            "attachments": list(page.attachments),
            "can_edit": True,
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
        return JSONResponse({"error": "Plik za duzy (max 200 MB)"}, status_code=400)

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

    if await _get_membership(db, project.id, user_id) is None:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    page = await _get_wiki_page(page_id, project.id, db)
    if page is None:
        return JSONResponse({"error": "Strona nie istnieje"}, status_code=404)

    if filepond.filename is None or filepond.filename == "":
        return JSONResponse({"error": "Brak nazwy pliku"}, status_code=400)

    safe_filename = os.path.basename(filepond.filename)
    safe_filename = re.sub(r"[^\w\s\-.]", "_", safe_filename).strip()
    if not safe_filename:
        safe_filename = "attachment"

    data = await filepond.read()
    if len(data) > MAX_ATTACHMENT_SIZE:
        return JSONResponse({"error": "Plik za duzy (max 200 MB)"}, status_code=400)

    content_type = filepond.content_type or "application/octet-stream"
    ext = safe_filename.rsplit(".", 1)[-1] if "." in safe_filename else "bin"
    now = datetime.now(UTC)
    date_prefix = f"{now.year}/{now.month:02d}/{now.day:02d}"
    storage_path = f"{project.slug}/wiki-attachments/{page_id}/{date_prefix}/{uuid.uuid4().hex}.{ext}"

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, partial(minio_upload_object, storage_path, data, content_type))
    except Exception:
        return JSONResponse({"error": "Blad uploadu pliku"}, status_code=500)

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

    if await _get_membership(db, project.id, user_id) is None:
        return HTMLResponse("Forbidden", status_code=403)

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

    if await _get_membership(db, project.id, user_id) is None:
        return JSONResponse({"error": "Forbidden"}, status_code=403)

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

    if await _get_membership(db, project.id, user_id) is None:
        return HTMLResponse("Forbidden", status_code=403)

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

    if await _get_membership(db, project.id, user_id) is None:
        return HTMLResponse("Forbidden", status_code=403)

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

    if await _get_membership(db, project.id, user_id) is None:
        return HTMLResponse("Forbidden", status_code=403)

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
