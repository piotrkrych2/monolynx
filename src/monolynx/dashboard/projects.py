"""Dashboard -- zarzadzanie projektami (lista, tworzenie, setup guide)."""

from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.services.auth import get_current_user

from .helpers import SLUG_PATTERN, _get_user_id, templates

CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/", response_class=HTMLResponse, response_model=None)
async def project_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    user = await get_current_user(request, db)
    if user and user.is_superuser:
        query = select(Project).where(Project.is_active.is_(True))
    else:
        query = (
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                Project.is_active.is_(True),
                ProjectMember.user_id == user_id,
            )
        )

    result = await db.execute(query.order_by(Project.name))
    projects = result.scalars().all()

    return templates.TemplateResponse(request, "dashboard/projects.html", {"projects": projects})


@router.get("/create-project", response_class=HTMLResponse, response_model=None)
async def create_project_form(
    request: Request,
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    ctx = {"error": None, "name": "", "slug": "", "code": ""}
    return templates.TemplateResponse(request, "dashboard/create_project.html", ctx)


@router.post("/create-project", response_class=HTMLResponse, response_model=None)
async def create_project(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    slug = str(form.get("slug", "")).strip()
    code = str(form.get("code", "")).strip().upper()

    if not name or not slug or not code:
        return templates.TemplateResponse(
            request,
            "dashboard/create_project.html",
            {"error": "Nazwa, slug i kod sa wymagane", "name": name, "slug": slug, "code": code},
        )

    if not SLUG_PATTERN.match(slug):
        return templates.TemplateResponse(
            request,
            "dashboard/create_project.html",
            {
                "error": "Slug moze zawierac tylko male litery, cyfry i myslniki",
                "name": name,
                "slug": slug,
                "code": code,
            },
        )

    if not CODE_PATTERN.match(code):
        return templates.TemplateResponse(
            request,
            "dashboard/create_project.html",
            {
                "error": "Kod musi miec 2-10 znakow: wielkie litery i cyfry, zaczynac sie od litery (np. PIM, PROJ2)",
                "name": name,
                "slug": slug,
                "code": code,
            },
        )

    project = Project(
        name=name,
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
    )
    db.add(project)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return templates.TemplateResponse(
            request,
            "dashboard/create_project.html",
            {
                "error": "Projekt z takim slugiem lub kodem juz istnieje",
                "name": name,
                "slug": slug,
                "code": code,
            },
        )

    await db.commit()
    return RedirectResponse(url="/dashboard/", status_code=303)
