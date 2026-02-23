"""Dashboard -- autentykacja (login/logout, akceptacja zaproszenia)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.database import get_db
from monolynx.models.user import User
from monolynx.services.auth import authenticate_user, hash_password

from .helpers import templates

router = APIRouter(tags=["auth"])

MIN_PASSWORD_LENGTH = 8


@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth/login.html", {"error": None})


@router.post("/auth/login", response_class=HTMLResponse, response_model=None)
async def login(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    email = str(form.get("email", ""))
    password = str(form.get("password", ""))

    user = await authenticate_user(email, password, db)
    if user is None:
        return templates.TemplateResponse(request, "auth/login.html", {"error": "Nieprawidlowy email lub haslo"})

    request.session["user_id"] = str(user.id)
    request.session["is_superuser"] = user.is_superuser
    return RedirectResponse(url="/dashboard/", status_code=303)


@router.post("/auth/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/auth/accept-invite/{token}", response_class=HTMLResponse)
async def accept_invite_form(
    request: Request,
    token: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    result = await db.execute(
        select(User).where(
            User.invitation_token == token,
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()

    if user is None or (user.invitation_expires_at and user.invitation_expires_at < datetime.now(UTC)):
        return templates.TemplateResponse(
            request,
            "auth/accept_invite.html",
            {"valid": False, "error": None, "token": token},
        )

    return templates.TemplateResponse(
        request,
        "auth/accept_invite.html",
        {"valid": True, "error": None, "token": token},
    )


@router.post(
    "/auth/accept-invite/{token}",
    response_class=HTMLResponse,
    response_model=None,
)
async def accept_invite(
    request: Request,
    token: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    result = await db.execute(
        select(User).where(
            User.invitation_token == token,
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()

    if user is None or (user.invitation_expires_at and user.invitation_expires_at < datetime.now(UTC)):
        return templates.TemplateResponse(
            request,
            "auth/accept_invite.html",
            {"valid": False, "error": None, "token": token},
        )

    form = await request.form()
    password = str(form.get("password", ""))
    password_confirm = str(form.get("password_confirm", ""))

    if len(password) < MIN_PASSWORD_LENGTH:
        return templates.TemplateResponse(
            request,
            "auth/accept_invite.html",
            {
                "valid": True,
                "error": f"Haslo musi miec minimum {MIN_PASSWORD_LENGTH} znakow",
                "token": token,
            },
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            request,
            "auth/accept_invite.html",
            {"valid": True, "error": "Hasla nie sa zgodne", "token": token},
        )

    user.password_hash = hash_password(password)
    user.invitation_token = None
    user.invitation_expires_at = None
    await db.commit()

    return RedirectResponse(url="/auth/login", status_code=303)
