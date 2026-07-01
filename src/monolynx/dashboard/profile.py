"""Dashboard -- profil uzytkownika (tokeny API)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.user_api_token import UserApiToken
from monolynx.services.mcp_auth import generate_api_token

from .helpers import _get_user_id, flash, templates

router = APIRouter(prefix="/dashboard", tags=["profile"])


@router.get("/profile/tokens", response_class=HTMLResponse, response_model=None)
async def tokens_list(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    """Lista tokenow API zalogowanego uzytkownika."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse("/auth/login", status_code=303)

    result = await db.execute(select(UserApiToken).where(UserApiToken.user_id == user_id).order_by(UserApiToken.created_at.desc()))
    tokens = result.scalars().all()

    return templates.TemplateResponse(request, "dashboard/profile/tokens.html", {"tokens": tokens})


@router.post("/profile/tokens/create", response_class=HTMLResponse, response_model=None)
async def token_create(request: Request, db: AsyncSession = Depends(get_db)) -> HTMLResponse | RedirectResponse:
    """Generowanie nowego tokenu API."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse("/auth/login", status_code=303)

    form = await request.form()
    name = str(form.get("name", "")).strip()

    if not name:
        flash(request, "Nazwa tokenu jest wymagana.", "error")
        return RedirectResponse("/dashboard/profile/tokens", status_code=303)

    raw_token, token_hash = generate_api_token()

    token = UserApiToken(
        user_id=user_id,
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        name=name,
    )
    db.add(token)
    await db.commit()

    # Pobierz liste tokenow do wyswietlenia razem z nowym raw tokenem
    result = await db.execute(select(UserApiToken).where(UserApiToken.user_id == user_id).order_by(UserApiToken.created_at.desc()))
    tokens = result.scalars().all()

    return templates.TemplateResponse(
        request,
        "dashboard/profile/tokens.html",
        {"tokens": tokens, "new_token": raw_token},
    )


@router.get("/profile/mcp-guide", response_class=HTMLResponse, response_model=None)
async def mcp_guide(
    request: Request,
) -> HTMLResponse | RedirectResponse:
    """Instrukcja konfiguracji MCP dla Claude Code."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse("/auth/login", status_code=303)

    return templates.TemplateResponse(request, "dashboard/profile/mcp_guide.html", {"app_url": settings.APP_URL})


@router.post("/profile/tokens/{token_id}/revoke", response_model=None)
async def token_revoke(
    token_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Dezaktywacja tokenu API."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse("/auth/login", status_code=303)

    result = await db.execute(
        select(UserApiToken).where(
            UserApiToken.id == token_id,
            UserApiToken.user_id == user_id,
        )
    )
    token = result.scalar_one_or_none()

    if token is None:
        flash(request, "Token nie zostal znaleziony.", "error")
        return RedirectResponse("/dashboard/profile/tokens", status_code=303)

    token.is_active = False
    await db.commit()

    flash(request, "Token zostal dezaktywowany.", "success")
    return RedirectResponse("/dashboard/profile/tokens", status_code=303)
