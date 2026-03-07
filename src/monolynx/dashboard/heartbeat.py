"""Dashboard -- modul heartbeat (lista, CRUD, szczegoly)."""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.heartbeat import Heartbeat
from monolynx.models.project import Project
from monolynx.services.heartbeat import (
    create_heartbeat,
    delete_heartbeat,
    get_heartbeat_status,
    update_heartbeat,
)

from .helpers import _get_user_id, flash, render_project_page, templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["heartbeat"])

MAX_HEARTBEATS_PER_PROJECT = 50


async def _get_project(slug: str, db: AsyncSession) -> Project | None:
    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    return result.scalar_one_or_none()


async def _get_heartbeat(heartbeat_id: uuid.UUID, project_id: uuid.UUID, db: AsyncSession) -> Heartbeat | None:
    result = await db.execute(select(Heartbeat).where(Heartbeat.id == heartbeat_id, Heartbeat.project_id == project_id))
    return result.scalar_one_or_none()


# --- Lista heartbeatow ---


@router.get("/{slug}/heartbeat/", response_class=HTMLResponse, response_model=None)
async def heartbeat_list(
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

    result = await db.execute(select(Heartbeat).where(Heartbeat.project_id == project.id).order_by(Heartbeat.created_at.desc()))
    heartbeats = list(result.scalars().all())

    # Oblicz aktualny status dla kazdego heartbeatu
    heartbeat_statuses = {hb.id: get_heartbeat_status(hb) for hb in heartbeats}

    return await render_project_page(
        request,
        "dashboard/heartbeat/list.html",
        {
            "project": project,
            "heartbeats": heartbeats,
            "heartbeat_statuses": heartbeat_statuses,
            "active_module": "heartbeat",
        },
        db=db,
    )


# --- Tworzenie heartbeatu ---


@router.get("/{slug}/heartbeat/create", response_class=HTMLResponse, response_model=None)
async def heartbeat_create_form(
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
        "dashboard/heartbeat/create.html",
        {
            "project": project,
            "error": None,
            "form_data": None,
            "active_module": "heartbeat",
        },
        db=db,
    )


@router.post("/{slug}/heartbeat/create", response_class=HTMLResponse, response_model=None)
async def heartbeat_create(
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
    name = str(form.get("name", "")).strip()
    period_minutes_raw = str(form.get("period", "60")).strip()
    grace_minutes_raw = str(form.get("grace", "1")).strip()

    error = None

    if not name:
        error = "Nazwa jest wymagana"
    elif len(name) > 255:
        error = "Nazwa moze miec maksymalnie 255 znakow"

    try:
        period_minutes = int(period_minutes_raw)
        if error is None and period_minutes < 1:
            error = "Okres musi byc wiekszy niz 0 minut"
    except ValueError:
        period_minutes = 60
        if error is None:
            error = "Okres musi byc liczba calkowita"

    try:
        grace_minutes = int(grace_minutes_raw)
        if error is None and grace_minutes < 0:
            error = "Tolerancja musi byc nieujemna"
    except ValueError:
        grace_minutes = 1
        if error is None:
            error = "Tolerancja musi byc liczba calkowita"

    # Limit heartbeatow na projekt
    if error is None:
        count_result = await db.execute(select(func.count(Heartbeat.id)).where(Heartbeat.project_id == project.id))
        hb_count = count_result.scalar() or 0
        if hb_count >= MAX_HEARTBEATS_PER_PROJECT:
            error = f"Osiagnieto limit {MAX_HEARTBEATS_PER_PROJECT} heartbeatow na projekt"

    if error:
        return templates.TemplateResponse(
            request,
            "dashboard/heartbeat/create.html",
            {
                "project": project,
                "error": error,
                "form_data": {
                    "name": name,
                    "period": period_minutes,
                    "grace": grace_minutes,
                },
                "active_module": "heartbeat",
            },
        )

    try:
        await create_heartbeat(
            db,
            project.id,
            {
                "name": name,
                "period": period_minutes * 60,
                "grace": grace_minutes * 60,
            },
        )
    except IntegrityError:
        await db.rollback()
        project = await _get_project(slug, db)
        return templates.TemplateResponse(
            request,
            "dashboard/heartbeat/create.html",
            {
                "project": project,
                "error": "Heartbeat o tej nazwie juz istnieje w tym projekcie",
                "form_data": {
                    "name": name,
                    "period": period_minutes,
                    "grace": grace_minutes,
                },
                "active_module": "heartbeat",
            },
        )

    flash(request, "Heartbeat zostal utworzony")
    return RedirectResponse(url=f"/dashboard/{slug}/heartbeat/", status_code=303)


# --- Szczegoly heartbeatu ---


@router.get("/{slug}/heartbeat/{heartbeat_id}", response_class=HTMLResponse, response_model=None)
async def heartbeat_detail(
    request: Request,
    slug: str,
    heartbeat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    heartbeat = await _get_heartbeat(heartbeat_id, project.id, db)
    if heartbeat is None:
        return HTMLResponse("Heartbeat not found", status_code=404)

    status = get_heartbeat_status(heartbeat)
    ping_url = f"{settings.APP_URL}/hb/{heartbeat.token}"
    period_minutes = heartbeat.period // 60
    grace_minutes = heartbeat.grace // 60

    return await render_project_page(
        request,
        "dashboard/heartbeat/detail.html",
        {
            "project": project,
            "heartbeat": heartbeat,
            "status": status,
            "ping_url": ping_url,
            "period_minutes": period_minutes,
            "grace_minutes": grace_minutes,
            "active_module": "heartbeat",
        },
        db=db,
    )


# --- Edycja heartbeatu ---


@router.get("/{slug}/heartbeat/{heartbeat_id}/edit", response_class=HTMLResponse, response_model=None)
async def heartbeat_edit_form(
    request: Request,
    slug: str,
    heartbeat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    heartbeat = await _get_heartbeat(heartbeat_id, project.id, db)
    if heartbeat is None:
        return HTMLResponse("Heartbeat not found", status_code=404)

    return await render_project_page(
        request,
        "dashboard/heartbeat/edit.html",
        {
            "project": project,
            "heartbeat": heartbeat,
            "error": None,
            "form_data": {
                "name": heartbeat.name,
                "period": heartbeat.period // 60,
                "grace": heartbeat.grace // 60,
            },
            "active_module": "heartbeat",
        },
        db=db,
    )


@router.post("/{slug}/heartbeat/{heartbeat_id}/edit", response_class=HTMLResponse, response_model=None)
async def heartbeat_edit(
    request: Request,
    slug: str,
    heartbeat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    heartbeat = await _get_heartbeat(heartbeat_id, project.id, db)
    if heartbeat is None:
        return HTMLResponse("Heartbeat not found", status_code=404)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    period_minutes_raw = str(form.get("period", "60")).strip()
    grace_minutes_raw = str(form.get("grace", "1")).strip()

    error = None

    if not name:
        error = "Nazwa jest wymagana"
    elif len(name) > 255:
        error = "Nazwa moze miec maksymalnie 255 znakow"

    try:
        period_minutes = int(period_minutes_raw)
        if error is None and period_minutes < 1:
            error = "Okres musi byc wiekszy niz 0 minut"
    except ValueError:
        period_minutes = heartbeat.period // 60
        if error is None:
            error = "Okres musi byc liczba calkowita"

    try:
        grace_minutes = int(grace_minutes_raw)
        if error is None and grace_minutes < 0:
            error = "Tolerancja musi byc nieujemna"
    except ValueError:
        grace_minutes = heartbeat.grace // 60
        if error is None:
            error = "Tolerancja musi byc liczba calkowita"

    if error:
        return templates.TemplateResponse(
            request,
            "dashboard/heartbeat/edit.html",
            {
                "project": project,
                "heartbeat": heartbeat,
                "error": error,
                "form_data": {
                    "name": name,
                    "period": period_minutes,
                    "grace": grace_minutes,
                },
                "active_module": "heartbeat",
            },
        )

    try:
        await update_heartbeat(
            db,
            project.id,
            heartbeat_id,
            {
                "name": name,
                "period": period_minutes * 60,
                "grace": grace_minutes * 60,
            },
        )
    except IntegrityError:
        await db.rollback()
        project = await _get_project(slug, db)
        if project is None:
            raise HTTPException(status_code=404) from None
        heartbeat = await _get_heartbeat(heartbeat_id, project.id, db)
        return templates.TemplateResponse(
            request,
            "dashboard/heartbeat/edit.html",
            {
                "project": project,
                "heartbeat": heartbeat,
                "error": "Heartbeat o tej nazwie juz istnieje w tym projekcie",
                "form_data": {
                    "name": name,
                    "period": period_minutes,
                    "grace": grace_minutes,
                },
                "active_module": "heartbeat",
            },
        )

    flash(request, "Heartbeat zostal zaktualizowany")
    return RedirectResponse(url=f"/dashboard/{slug}/heartbeat/{heartbeat_id}", status_code=303)


# --- Usuwanie ---


@router.post("/{slug}/heartbeat/{heartbeat_id}/delete", response_model=None)
async def heartbeat_delete(
    request: Request,
    slug: str,
    heartbeat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    heartbeat = await _get_heartbeat(heartbeat_id, project.id, db)
    if heartbeat is None:
        return HTMLResponse("Heartbeat not found", status_code=404)

    await delete_heartbeat(db, project.id, heartbeat_id)

    flash(request, "Heartbeat zostal usuniety")
    return RedirectResponse(url=f"/dashboard/{slug}/heartbeat/", status_code=303)
