"""Dashboard -- ustawienia projektu (edycja, usuwanie, czlonkowie, role)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import ACTION_LABELS, MEMBER_ROLES, MODULE_LABELS, PERMISSION_ACTIONS, PERMISSION_MODULES, ROLE_LABELS
from monolynx.dashboard.projects import CODE_PATTERN
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.user import User
from monolynx.services.permissions import require_permission

from .helpers import SLUG_PATTERN, _get_user_id, flash, render_project_page, templates

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def _get_members(project_id: uuid.UUID, db: AsyncSession) -> list[ProjectMember]:
    result = await db.execute(
        select(ProjectMember)
        .options(selectinload(ProjectMember.user), selectinload(ProjectMember.role_obj))
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.created_at)
    )
    return list(result.scalars().all())


async def _get_project_roles(project_id: uuid.UUID, db: AsyncSession) -> list[Role]:
    result = await db.execute(select(Role).where(Role.project_id == project_id).order_by(Role.is_system.desc(), Role.name))
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

    await require_permission(db, user_id, project.id, "settings", "read")

    members = await _get_members(project.id, db)
    project_roles = await _get_project_roles(project.id, db)

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
            "project_roles": project_roles,
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

    await require_permission(db, user_id, project.id, "settings", "write")

    form = await request.form()
    new_name = str(form.get("name", "")).strip()
    new_slug = str(form.get("slug", "")).strip()
    new_code = str(form.get("code", "")).strip().upper()

    members = await _get_members(project.id, db)
    project_roles = await _get_project_roles(project.id, db)

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
                "project_roles": project_roles,
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
        project_roles = await _get_project_roles(project.id, db) if project else []
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
                "project_roles": project_roles,
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

    await require_permission(db, user_id, project.id, "settings", "delete")

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

    await require_permission(db, user_id, project.id, "settings", "write")

    form = await request.form()
    email = str(form.get("email", "")).strip()
    role_id_str = str(form.get("role_id", "")).strip()

    # Znajdz uzytkownika po emailu
    result = await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))
    user = result.scalar_one_or_none()

    members = await _get_members(project.id, db)
    project_roles = await _get_project_roles(project.id, db)

    def _member_error(msg: str) -> HTMLResponse:
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
                "member_error": msg,
                "roles": MEMBER_ROLES,
                "role_labels": ROLE_LABELS,
                "project_roles": project_roles,
                "active_module": "settings",
            },
        )

    if user is None:
        return _member_error(f"Uzytkownik z emailem {email} nie istnieje")

    # Sprawdz czy juz jest czlonkiem
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return _member_error("Ten uzytkownik jest juz czlonkiem projektu")

    # Pobierz role po UUID (jezeli podano)
    selected_role: Role | None = None
    if role_id_str:
        try:
            role_uuid = uuid.UUID(role_id_str)
            role_result = await db.execute(select(Role).where(Role.id == role_uuid, Role.project_id == project.id))
            selected_role = role_result.scalar_one_or_none()
        except ValueError:
            return _member_error("Niepoprawny identyfikator roli")

        if selected_role is None:
            return _member_error("Wybrana rola nie istnieje w tym projekcie")

    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role=selected_role.name.lower() if selected_role else "member",
        role_id=selected_role.id if selected_role else None,
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

    await require_permission(db, user_id, project.id, "settings", "delete")

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

    await require_permission(db, user_id, project.id, "settings", "write")

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
    role_id_str = str(form.get("role_id", "")).strip()

    if role_id_str:
        try:
            role_uuid = uuid.UUID(role_id_str)
        except ValueError:
            flash(request, "Niepoprawny identyfikator roli", "error")
            return RedirectResponse(url=f"/dashboard/{slug}/settings", status_code=303)

        role_result = await db.execute(select(Role).where(Role.id == role_uuid, Role.project_id == project.id))
        new_role_obj = role_result.scalar_one_or_none()
        if new_role_obj is None:
            flash(request, "Wybrana rola nie istnieje w tym projekcie", "error")
            return RedirectResponse(url=f"/dashboard/{slug}/settings", status_code=303)

        member.role_id = new_role_obj.id
        member.role = new_role_obj.name.lower()
        await db.commit()
        flash(request, "Rola czlonka zostala zaktualizowana", "success")

    return RedirectResponse(url=f"/dashboard/{slug}/settings", status_code=303)


# --- Zarzadzanie rolami projektu ---


@router.get("/{slug}/settings/roles", response_class=HTMLResponse, response_model=None)
async def roles_list(
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

    await require_permission(db, user_id, project.id, "settings", "read")

    project_roles = await _get_project_roles(project.id, db)

    # Policz czlonkow przypisanych do kazdej roli (jeden query zamiast N+1)
    role_member_counts: dict[str, int] = {}
    if project_roles:
        count_result = await db.execute(
            select(ProjectMember.role_id, func.count(ProjectMember.id))
            .where(ProjectMember.role_id.in_([r.id for r in project_roles]))
            .group_by(ProjectMember.role_id)
        )
        for role_id, count in count_result.all():
            role_member_counts[str(role_id)] = count

    return await render_project_page(
        request,
        "dashboard/settings/roles_list.html",
        {
            "project": project,
            "roles": project_roles,
            "role_member_counts": role_member_counts,
            "module_labels": MODULE_LABELS,
            "action_labels": ACTION_LABELS,
            "active_module": "settings",
        },
        db=db,
    )


@router.get("/{slug}/settings/roles/create", response_class=HTMLResponse, response_model=None)
async def role_create_form(
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

    await require_permission(db, user_id, project.id, "settings", "write")

    return await render_project_page(
        request,
        "dashboard/settings/role_form.html",
        {
            "project": project,
            "role": None,
            "error": None,
            "permission_modules": PERMISSION_MODULES,
            "permission_actions": PERMISSION_ACTIONS,
            "module_labels": MODULE_LABELS,
            "action_labels": ACTION_LABELS,
            "active_module": "settings",
        },
        db=db,
    )


@router.post("/{slug}/settings/roles/create", response_model=None)
async def role_create(
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

    await require_permission(db, user_id, project.id, "settings", "write")

    form = await request.form()
    name = str(form.get("name", "")).strip()

    # Buduj permissions z checkboxow (wczesniej, zeby zachowac przy bledzie walidacji)
    permissions: dict[str, list[str]] = {}
    for module in PERMISSION_MODULES:
        actions = []
        for action in PERMISSION_ACTIONS:
            if form.get(f"perm_{module}_{action}"):
                actions.append(action)
        if actions:
            permissions[module] = actions

    def _create_error_ctx(error_msg: str) -> dict[str, object]:
        return {
            "project": project,
            "role": None,
            "error": error_msg,
            "submitted_permissions": permissions,
            "name": name,
            "permission_modules": PERMISSION_MODULES,
            "permission_actions": PERMISSION_ACTIONS,
            "module_labels": MODULE_LABELS,
            "action_labels": ACTION_LABELS,
            "active_module": "settings",
        }

    if not name:
        return await render_project_page(
            request,
            "dashboard/settings/role_form.html",
            _create_error_ctx("Nazwa roli jest wymagana"),
            db=db,
        )

    if len(name) > 50:
        return await render_project_page(
            request,
            "dashboard/settings/role_form.html",
            _create_error_ctx("Nazwa roli nie moze przekraczac 50 znakow"),
            db=db,
        )

    new_role = Role(
        name=name,
        project_id=project.id,
        permissions=permissions,
        is_system=False,
    )
    db.add(new_role)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return await render_project_page(
            request,
            "dashboard/settings/role_form.html",
            _create_error_ctx("Rola o tej nazwie juz istnieje w projekcie"),
            db=db,
        )

    await db.commit()
    flash(request, f"Rola '{name}' zostala utworzona", "success")
    return RedirectResponse(url=f"/dashboard/{slug}/settings/roles", status_code=303)


@router.get("/{slug}/settings/roles/{role_id}/edit", response_class=HTMLResponse, response_model=None)
async def role_edit_form(
    request: Request,
    slug: str,
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "settings", "write")

    role_result = await db.execute(select(Role).where(Role.id == role_id, Role.project_id == project.id))
    role = role_result.scalar_one_or_none()
    if role is None:
        return HTMLResponse("Role not found", status_code=404)

    return await render_project_page(
        request,
        "dashboard/settings/role_form.html",
        {
            "project": project,
            "role": role,
            "error": None,
            "permission_modules": PERMISSION_MODULES,
            "permission_actions": PERMISSION_ACTIONS,
            "module_labels": MODULE_LABELS,
            "action_labels": ACTION_LABELS,
            "active_module": "settings",
        },
        db=db,
    )


@router.post("/{slug}/settings/roles/{role_id}/edit", response_model=None)
async def role_edit(
    request: Request,
    slug: str,
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "settings", "write")

    role_result = await db.execute(select(Role).where(Role.id == role_id, Role.project_id == project.id))
    role = role_result.scalar_one_or_none()
    if role is None:
        return HTMLResponse("Role not found", status_code=404)

    form = await request.form()

    # Nie pozwalaj edytowac nazwy rol systemowych
    if not role.is_system:
        new_name = str(form.get("name", "")).strip()
        if not new_name:
            return await render_project_page(
                request,
                "dashboard/settings/role_form.html",
                {
                    "project": project,
                    "role": role,
                    "error": "Nazwa roli jest wymagana",
                    "permission_modules": PERMISSION_MODULES,
                    "permission_actions": PERMISSION_ACTIONS,
                    "module_labels": MODULE_LABELS,
                    "action_labels": ACTION_LABELS,
                    "active_module": "settings",
                },
                db=db,
            )
        if len(new_name) > 50:
            return await render_project_page(
                request,
                "dashboard/settings/role_form.html",
                {
                    "project": project,
                    "role": role,
                    "error": "Nazwa roli nie moze przekraczac 50 znakow",
                    "permission_modules": PERMISSION_MODULES,
                    "permission_actions": PERMISSION_ACTIONS,
                    "module_labels": MODULE_LABELS,
                    "action_labels": ACTION_LABELS,
                    "active_module": "settings",
                },
                db=db,
            )
        role.name = new_name

    # Buduj permissions z checkboxow
    permissions: dict[str, list[str]] = {}
    for module in PERMISSION_MODULES:
        actions = []
        for action in PERMISSION_ACTIONS:
            if form.get(f"perm_{module}_{action}"):
                actions.append(action)
        if actions:
            permissions[module] = actions
    role.permissions = permissions

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        role_result2 = await db.execute(select(Role).where(Role.id == role_id, Role.project_id == project.id))
        role = role_result2.scalar_one_or_none()
        return await render_project_page(
            request,
            "dashboard/settings/role_form.html",
            {
                "project": project,
                "role": role,
                "error": "Rola o tej nazwie juz istnieje w projekcie",
                "permission_modules": PERMISSION_MODULES,
                "permission_actions": PERMISSION_ACTIONS,
                "module_labels": MODULE_LABELS,
                "action_labels": ACTION_LABELS,
                "active_module": "settings",
            },
            db=db,
        )

    await db.commit()
    flash(request, "Rola zostala zaktualizowana", "success")
    return RedirectResponse(url=f"/dashboard/{slug}/settings/roles", status_code=303)


@router.post("/{slug}/settings/roles/{role_id}/delete", response_model=None)
async def role_delete(
    request: Request,
    slug: str,
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "settings", "delete")

    role_result = await db.execute(select(Role).where(Role.id == role_id, Role.project_id == project.id))
    role = role_result.scalar_one_or_none()
    if role is None:
        return HTMLResponse("Role not found", status_code=404)

    if role.is_system:
        flash(request, "Nie mozna usunac roli systemowej", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/settings/roles", status_code=303)

    # Sprawdz czy rola ma przypisanych czlonkow
    count_result = await db.execute(select(func.count(ProjectMember.id)).where(ProjectMember.role_id == role_id))
    member_count = count_result.scalar_one()
    if member_count > 0:
        flash(request, f"Nie mozna usunac roli — jest przypisana do {member_count} czlonkow projektu", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/settings/roles", status_code=303)

    await db.delete(role)
    await db.commit()
    flash(request, f"Rola '{role.name}' zostala usunieta", "success")
    return RedirectResponse(url=f"/dashboard/{slug}/settings/roles", status_code=303)
