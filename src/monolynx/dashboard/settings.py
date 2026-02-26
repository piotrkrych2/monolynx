"""Dashboard -- ustawienia projektu (edycja, usuwanie, czlonkowie)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import MEMBER_ROLES, ROLE_LABELS
from monolynx.dashboard.projects import CODE_PATTERN
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User

from .helpers import SLUG_PATTERN, _get_user_id, render_project_page, templates

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def _get_members(project_id: uuid.UUID, db: AsyncSession) -> list[ProjectMember]:
    result = await db.execute(
        select(ProjectMember)
        .options(selectinload(ProjectMember.user))
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.created_at)
    )
    return list(result.scalars().all())


@router.get("/{slug}/settings", response_class=HTMLResponse, response_model=None)
async def edit_project_form(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    members = await _get_members(project.id, db)

    return await render_project_page(
        request,
        "dashboard/settings/index.html",
        {
            "project": project,
            "error": None,
            "name": None,
            "slug": None,
            "code": None,
            "members": members,
            "member_error": None,
            "roles": MEMBER_ROLES,
            "role_labels": ROLE_LABELS,
            "active_module": "settings",
        },
        db=db,
    )


@router.post("/{slug}/settings", response_class=HTMLResponse, response_model=None)
async def edit_project(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    form = await request.form()
    new_name = str(form.get("name", "")).strip()
    new_slug = str(form.get("slug", "")).strip()
    new_code = str(form.get("code", "")).strip().upper()

    members = await _get_members(project.id, db)

    def _error_response(error_msg: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "dashboard/settings/index.html",
            {
                "project": project,
                "error": error_msg,
                "name": new_name,
                "slug": new_slug,
                "code": new_code,
                "members": members,
                "member_error": None,
                "roles": MEMBER_ROLES,
                "role_labels": ROLE_LABELS,
                "active_module": "settings",
            },
        )

    if not new_name or not new_slug or not new_code:
        return _error_response("Nazwa, slug i kod sa wymagane")

    if not SLUG_PATTERN.match(new_slug):
        return _error_response("Slug moze zawierac tylko male litery, cyfry i myslniki")

    if not CODE_PATTERN.match(new_code):
        return _error_response("Kod musi miec 2-10 znakow: wielkie litery i cyfry, zaczynac sie od litery (np. PIM, PROJ2)")

    project.name = new_name
    project.slug = new_slug
    project.code = new_code
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
        project = result.scalar_one_or_none()
        members = await _get_members(project.id, db) if project else []
        return templates.TemplateResponse(
            request,
            "dashboard/settings/index.html",
            {
                "project": project,
                "error": "Projekt z takim slugiem lub kodem juz istnieje",
                "name": new_name,
                "slug": new_slug,
                "code": new_code,
                "members": members,
                "member_error": None,
                "roles": MEMBER_ROLES,
                "role_labels": ROLE_LABELS,
                "active_module": "settings",
            },
        )

    await db.commit()
    return RedirectResponse(url="/dashboard/", status_code=303)


@router.post("/{slug}/settings/delete", response_model=None)
async def delete_project(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    project.is_active = False
    await db.commit()
    return RedirectResponse(url="/dashboard/", status_code=303)


# --- Czlonkowie projektu ---


@router.post(
    "/{slug}/settings/members/add",
    response_class=HTMLResponse,
    response_model=None,
)
async def member_add(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    form = await request.form()
    email = str(form.get("email", "")).strip()
    role = str(form.get("role", "member"))

    if role not in MEMBER_ROLES:
        role = "member"

    # Znajdz uzytkownika po emailu
    result = await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))
    user = result.scalar_one_or_none()

    members = await _get_members(project.id, db)

    if user is None:
        return templates.TemplateResponse(
            request,
            "dashboard/settings/index.html",
            {
                "project": project,
                "error": None,
                "name": None,
                "slug": None,
                "code": None,
                "members": members,
                "member_error": f"Uzytkownik z emailem {email} nie istnieje",
                "roles": MEMBER_ROLES,
                "role_labels": ROLE_LABELS,
                "active_module": "settings",
            },
        )

    # Sprawdz czy juz jest czlonkiem
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return templates.TemplateResponse(
            request,
            "dashboard/settings/index.html",
            {
                "project": project,
                "error": None,
                "name": None,
                "slug": None,
                "code": None,
                "members": members,
                "member_error": "Ten uzytkownik jest juz czlonkiem projektu",
                "roles": MEMBER_ROLES,
                "role_labels": ROLE_LABELS,
                "active_module": "settings",
            },
        )

    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role=role,
    )
    db.add(member)
    await db.commit()

    return RedirectResponse(url=f"/dashboard/{slug}/settings", status_code=303)


@router.post("/{slug}/settings/members/{member_id}/remove", response_model=None)
async def member_remove(
    request: Request,
    slug: str,
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.id == member_id,
            ProjectMember.project_id == project.id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        return HTMLResponse("Member not found", status_code=404)

    await db.delete(member)
    await db.commit()

    return RedirectResponse(url=f"/dashboard/{slug}/settings", status_code=303)


@router.post("/{slug}/settings/members/{member_id}/role", response_model=None)
async def member_role(
    request: Request,
    slug: str,
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    member_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.id == member_id,
            ProjectMember.project_id == project.id,
        )
    )
    member = member_result.scalar_one_or_none()
    if member is None:
        return HTMLResponse("Member not found", status_code=404)

    form = await request.form()
    new_role = str(form.get("role", "member"))
    if new_role in MEMBER_ROLES:
        member.role = new_role
        await db.commit()

    return RedirectResponse(url=f"/dashboard/{slug}/settings", status_code=303)
