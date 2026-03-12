"""Dashboard -- autentykacja (login/logout, akceptacja zaproszenia, Google OAuth)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.user import User
from monolynx.services.auth import authenticate_user, hash_password

from .helpers import templates

logger = logging.getLogger("monolynx")

router = APIRouter(tags=["auth"])

MIN_PASSWORD_LENGTH = 8

# Google OAuth setup
oauth = OAuth()
if settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET:
    oauth.register(
        name="google",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def _google_enabled() -> bool:
    return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"error": None, "google_enabled": _google_enabled()},
    )


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
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Nieprawidlowy email lub haslo", "google_enabled": _google_enabled()},
        )

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


# --- Google OAuth ---


@router.get("/auth/google/login")
async def google_login(request: Request) -> RedirectResponse:
    if not _google_enabled():
        return RedirectResponse(url="/auth/login", status_code=302)
    redirect_uri = f"{settings.APP_URL}/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)  # type: ignore[no-any-return]


@router.get("/auth/google/callback", response_model=None)
async def google_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    if not _google_enabled():
        return RedirectResponse(url="/auth/login", status_code=302)

    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        logger.exception("Google OAuth error")
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Blad logowania przez Google. Sprobuj ponownie.", "google_enabled": True},
        )

    userinfo = token.get("userinfo")
    if not userinfo or not userinfo.get("email"):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"error": "Nie udalo sie pobrac danych z Google.", "google_enabled": True},
        )

    google_id = userinfo["sub"]
    email = userinfo["email"]

    # Try to find user by google_id
    result = await db.execute(select(User).where(User.google_id == google_id, User.is_active.is_(True)))
    user = result.scalar_one_or_none()

    if user is None:
        # Try to find existing user by email and link Google account
        result = await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))
        user = result.scalar_one_or_none()

        if user is not None:
            user.google_id = google_id
            if not user.first_name and userinfo.get("given_name"):
                user.first_name = userinfo["given_name"]
            if not user.last_name and userinfo.get("family_name"):
                user.last_name = userinfo["family_name"]
            await db.commit()
        else:
            # Create new account — first user ever becomes superuser
            user_count = await db.scalar(select(func.count()).select_from(User))
            is_first_user = user_count == 0

            user = User(
                email=email,
                google_id=google_id,
                first_name=userinfo.get("given_name", ""),
                last_name=userinfo.get("family_name", ""),
                is_superuser=is_first_user,
                is_active=True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

    request.session["user_id"] = str(user.id)
    request.session["is_superuser"] = user.is_superuser
    return RedirectResponse(url="/dashboard/", status_code=302)
