"""Dashboard -- zarzadzanie uzytkownikami (tylko superuser)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from open_sentry.constants import MEMBER_ROLES, ROLE_LABELS
from open_sentry.database import get_db
from open_sentry.models.project import Project
from open_sentry.models.project_member import ProjectMember
from open_sentry.models.user import User
from open_sentry.services.auth import get_current_user, hash_password
from open_sentry.services.email import send_invitation_email

from .helpers import flash, templates

router = APIRouter(prefix="/dashboard", tags=["users"])

INVITATION_DAYS = 7


async def _require_superuser(request: Request, db: AsyncSession) -> User | None:
    """Zwraca usera jesli jest superuserem, None jesli nie."""
    user = await get_current_user(request, db)
    if user is None or not user.is_superuser:
        return None
    return user


@router.get("/users", response_class=HTMLResponse, response_model=None)
async def user_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(User).where(User.is_active.is_(True)).order_by(User.created_at.desc()))
    users = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "dashboard/users/index.html",
        {"users": users, "current_user": current_user},
    )


@router.get("/users/create", response_class=HTMLResponse, response_model=None)
async def user_create_form(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    return templates.TemplateResponse(
        request,
        "dashboard/users/create.html",
        {
            "error": None,
            "first_name": "",
            "last_name": "",
            "email": "",
            "current_user": current_user,
        },
    )


@router.post("/users/create", response_class=HTMLResponse, response_model=None)
async def user_create(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    form = await request.form()
    first_name = str(form.get("first_name", "")).strip()
    last_name = str(form.get("last_name", "")).strip()
    email = str(form.get("email", "")).strip()
    send_email_flag = form.get("send_email") == "on"

    ctx = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "current_user": current_user,
    }

    if not email:
        return templates.TemplateResponse(
            request,
            "dashboard/users/create.html",
            {**ctx, "error": "Email jest wymagany"},
        )

    token = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=INVITATION_DAYS)

    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        password_hash=None,
        invitation_token=token,
        invitation_expires_at=expires_at,
    )
    db.add(user)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return templates.TemplateResponse(
            request,
            "dashboard/users/create.html",
            {**ctx, "error": "Uzytkownik z takim emailem juz istnieje"},
        )

    await db.commit()

    if send_email_flag:
        send_invitation_email(email, first_name, token)

    return RedirectResponse(url="/dashboard/users", status_code=303)


@router.post("/users/{user_id}/resend-invite", response_model=None)
async def resend_invite(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        return HTMLResponse("Uzytkownik nie znaleziony", status_code=404)

    token = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=INVITATION_DAYS)
    user.invitation_token = token
    user.invitation_expires_at = expires_at
    await db.commit()

    send_invitation_email(user.email, user.first_name, token)

    return RedirectResponse(url="/dashboard/users", status_code=303)


# --- Edycja uzytkownika ---


async def _get_edit_context(user: User, db: AsyncSession) -> dict[str, object]:
    """Wspolny kontekst dla strony edycji uzytkownika."""
    # Projekty przypisane do usera
    members_result = await db.execute(select(ProjectMember).where(ProjectMember.user_id == user.id).options(selectinload(ProjectMember.project)))
    memberships = members_result.scalars().all()

    # Projekty, do ktorych user jeszcze nie nalezy
    assigned_project_ids = [m.project_id for m in memberships]
    available_query = select(Project).where(Project.is_active.is_(True))
    if assigned_project_ids:
        available_query = available_query.where(Project.id.notin_(assigned_project_ids))
    available_result = await db.execute(available_query.order_by(Project.name))
    available_projects = available_result.scalars().all()

    return {
        "user": user,
        "memberships": memberships,
        "available_projects": available_projects,
        "roles": MEMBER_ROLES,
        "role_labels": ROLE_LABELS,
    }


@router.get("/users/{user_id}", response_class=HTMLResponse, response_model=None)
async def user_edit_form(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        return HTMLResponse("Uzytkownik nie znaleziony", status_code=404)

    ctx = await _get_edit_context(user, db)
    return templates.TemplateResponse(
        request,
        "dashboard/users/edit.html",
        {**ctx, "error": None, "current_user": current_user},
    )


@router.post("/users/{user_id}", response_class=HTMLResponse, response_model=None)
async def user_edit(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        return HTMLResponse("Uzytkownik nie znaleziony", status_code=404)

    form = await request.form()
    first_name = str(form.get("first_name", "")).strip()
    last_name = str(form.get("last_name", "")).strip()
    email = str(form.get("email", "")).strip()

    if not email:
        ctx = await _get_edit_context(user, db)
        return templates.TemplateResponse(
            request,
            "dashboard/users/edit.html",
            {**ctx, "error": "Email jest wymagany", "current_user": current_user},
        )

    user.first_name = first_name
    user.last_name = last_name
    user.email = email

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # Re-query user after rollback
        result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
        user = result.scalar_one_or_none()
        if user is None:
            return HTMLResponse("Uzytkownik nie znaleziony", status_code=404)
        ctx = await _get_edit_context(user, db)
        return templates.TemplateResponse(
            request,
            "dashboard/users/edit.html",
            {
                **ctx,
                "error": "Uzytkownik z takim emailem juz istnieje",
                "current_user": current_user,
            },
        )

    await db.commit()
    flash(request, "Dane uzytkownika zostaly zapisane")
    return RedirectResponse(url=f"/dashboard/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/activate", response_class=HTMLResponse, response_model=None)
async def user_activate(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        return HTMLResponse("Uzytkownik nie znaleziony", status_code=404)

    form = await request.form()
    password = str(form.get("password", ""))
    password_confirm = str(form.get("password_confirm", ""))

    error = None
    if len(password) < 8:
        error = "Haslo musi miec co najmniej 8 znakow"
    elif password != password_confirm:
        error = "Hasla sie nie zgadzaja"

    if error:
        ctx = await _get_edit_context(user, db)
        return templates.TemplateResponse(
            request,
            "dashboard/users/edit.html",
            {**ctx, "error": error, "current_user": current_user},
        )

    user.password_hash = hash_password(password)
    user.invitation_token = None
    user.invitation_expires_at = None
    await db.commit()

    flash(request, "Konto zostalo aktywowane")
    return RedirectResponse(url=f"/dashboard/users/{user_id}", status_code=303)


@router.post("/users/{user_id}/deactivate", response_class=HTMLResponse, response_model=None)
async def user_deactivate(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    if current_user.id == user_id:
        flash(request, "Nie mozesz dezaktywowac wlasnego konta", "error")
        return RedirectResponse(url=f"/dashboard/users/{user_id}", status_code=303)

    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        return HTMLResponse("Uzytkownik nie znaleziony", status_code=404)

    user.is_active = False
    await db.commit()

    flash(request, "Konto zostalo dezaktywowane")
    return RedirectResponse(url="/dashboard/users", status_code=303)


# --- Przypisanie do projektow ---


@router.post(
    "/users/{user_id}/projects/add",
    response_class=HTMLResponse,
    response_model=None,
)
async def user_project_add(
    request: Request,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        return HTMLResponse("Uzytkownik nie znaleziony", status_code=404)

    form = await request.form()
    project_id_str = str(form.get("project_id", ""))
    role = str(form.get("role", "member"))

    if not project_id_str:
        flash(request, "Wybierz projekt", "error")
        return RedirectResponse(url=f"/dashboard/users/{user_id}", status_code=303)

    if role not in MEMBER_ROLES:
        role = "member"

    member = ProjectMember(
        project_id=uuid.UUID(project_id_str),
        user_id=user_id,
        role=role,
    )
    db.add(member)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        flash(request, "Uzytkownik jest juz przypisany do tego projektu", "error")
        return RedirectResponse(url=f"/dashboard/users/{user_id}", status_code=303)

    await db.commit()
    flash(request, "Uzytkownik zostal przypisany do projektu")
    return RedirectResponse(url=f"/dashboard/users/{user_id}", status_code=303)


@router.post(
    "/users/{user_id}/projects/{member_id}/remove",
    response_class=HTMLResponse,
    response_model=None,
)
async def user_project_remove(
    request: Request,
    user_id: uuid.UUID,
    member_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    current_user = await _require_superuser(request, db)
    if current_user is None:
        if request.session.get("user_id"):
            return HTMLResponse("Brak uprawnien", status_code=403)
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.id == member_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        return HTMLResponse("Przypisanie nie znalezione", status_code=404)

    await db.delete(member)
    await db.commit()

    flash(request, "Uzytkownik zostal usuniety z projektu")
    return RedirectResponse(url=f"/dashboard/users/{user_id}", status_code=303)
