"""OAuth 2.1 API -- endpointy autoryzacji zgodne ze specyfikacja MCP."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.database import get_db
from monolynx.models.oauth import OAuthClient
from monolynx.services.auth import authenticate_user
from monolynx.services.oauth import (
    create_authorization_code,
    exchange_code_for_tokens,
    refresh_access_token,
    register_client,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata() -> JSONResponse:
    """RFC 8414 -- metadata serwera autoryzacji OAuth."""
    return JSONResponse(
        {
            "issuer": settings.APP_URL,
            "authorization_endpoint": f"{settings.APP_URL}/authorize",
            "token_endpoint": f"{settings.APP_URL}/token",
            "registration_endpoint": f"{settings.APP_URL}/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
    )


@router.post("/register")
async def register_oauth_client(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Dynamic Client Registration (RFC 7591)."""
    body = await request.json()
    client_name = body.get("client_name")
    redirect_uris = body.get("redirect_uris", [])
    grant_types = body.get("grant_types", ["authorization_code", "refresh_token"])

    if not redirect_uris:
        return JSONResponse(
            {"error": "invalid_client_metadata", "error_description": "redirect_uris wymagane"},
            status_code=400,
        )

    try:
        result = await register_client(client_name, redirect_uris, grant_types, db)
        await db.commit()
    except ValueError as e:
        return JSONResponse(
            {"error": "invalid_client_metadata", "error_description": str(e)},
            status_code=400,
        )

    return JSONResponse(result, status_code=201)


@router.get("/authorize", response_class=HTMLResponse)
async def authorize_get(
    request: Request,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query("code"),
    state: str = Query(""),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query("S256"),
    scope: str = Query(""),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Formularz autoryzacji OAuth -- logowanie lub consent."""
    # Walidacja parametrow
    if response_type != "code":
        return HTMLResponse("Nieobslugiwany response_type", status_code=400)

    if code_challenge_method != "S256":
        return HTMLResponse("Wymagana metoda PKCE: S256", status_code=400)

    # Sprawdz czy klient istnieje
    result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        return HTMLResponse("Nieznany client_id", status_code=400)

    # Sprawdz redirect_uri
    if redirect_uri not in client.redirect_uris:
        return HTMLResponse("Niedozwolony redirect_uri", status_code=400)

    # Sprawdz czy uzytkownik jest zalogowany
    user_id = request.session.get("user_id")

    return templates.TemplateResponse(
        request,
        "oauth/authorize.html",
        {
            "logged_in": user_id is not None,
            "client_name": client.client_name or client.client_id,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "scope": scope,
            "error": None,
        },
    )


@router.post("/authorize", response_model=None)
async def authorize_post(
    request: Request,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(""),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form("S256"),
    scope: str = Form(""),
    email: str | None = Form(None),
    password: str | None = Form(None),
    action: str = Form("login"),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse | HTMLResponse:
    """Przetworzenie formularza autoryzacji -- logowanie lub consent."""
    # Sprawdz czy klient istnieje
    result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        return HTMLResponse("Nieznany client_id", status_code=400)

    if redirect_uri not in client.redirect_uris:
        return HTMLResponse("Niedozwolony redirect_uri", status_code=400)

    user_id = request.session.get("user_id")

    # Logowanie
    if action == "login" and email and password:
        user = await authenticate_user(email, password, db)
        if user is None:
            return templates.TemplateResponse(
                request,
                "oauth/authorize.html",
                {
                    "logged_in": False,
                    "client_name": client.client_name or client.client_id,
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "state": state,
                    "code_challenge": code_challenge,
                    "code_challenge_method": code_challenge_method,
                    "scope": scope,
                    "error": "Nieprawidlowy email lub haslo",
                },
            )

        # Zaloguj uzytkownika w sesji
        request.session["user_id"] = str(user.id)
        request.session["is_superuser"] = user.is_superuser

        # Przejdz do consent -- redirect z powrotem na GET /authorize
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
            "scope": scope,
        }
        return RedirectResponse(
            url=f"/authorize?{urlencode(params)}",
            status_code=303,
        )

    # Consent -- uzytkownik jest zalogowany i zatwierdza dostep
    if action == "consent" and user_id:
        code = await create_authorization_code(
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            scope=scope or None,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            db=db,
        )
        await db.commit()

        # Redirect na redirect_uri z code i state
        params = {"code": code}
        if state:
            params["state"] = state
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{separator}{urlencode(params)}",
            status_code=302,
        )

    # Odmowa dostepu
    if action == "deny":
        params = {"error": "access_denied"}
        if state:
            params["state"] = state
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{separator}{urlencode(params)}",
            status_code=302,
        )

    return HTMLResponse("Nieprawidlowe zadanie", status_code=400)


@router.post("/token")
async def token_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Token endpoint -- wymiana code/refresh_token na tokeny."""
    # Parsuj form-encoded body (standard OAuth)
    form = await request.form()
    grant_type = form.get("grant_type", "")
    client_id = form.get("client_id", "")

    if not client_id:
        return JSONResponse(
            {"error": "invalid_client", "error_description": "client_id wymagany"},
            status_code=401,
        )

    # Sprawdz czy klient istnieje
    result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == str(client_id)))
    client = result.scalar_one_or_none()
    if client is None:
        return JSONResponse(
            {"error": "invalid_client", "error_description": "Nieznany client_id"},
            status_code=401,
        )

    if grant_type == "authorization_code":
        code = form.get("code", "")
        code_verifier = form.get("code_verifier", "")
        redirect_uri = form.get("redirect_uri", "")

        if not code or not code_verifier or not redirect_uri:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Brak wymaganych parametrow"},
                status_code=400,
            )

        try:
            tokens = await exchange_code_for_tokens(
                code=str(code),
                code_verifier=str(code_verifier),
                client_id=str(client_id),
                redirect_uri=str(redirect_uri),
                db=db,
            )
            await db.commit()
        except ValueError as e:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": str(e)},
                status_code=400,
            )

        return JSONResponse(tokens)

    elif grant_type == "refresh_token":
        refresh_token_raw = form.get("refresh_token", "")

        if not refresh_token_raw:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Brak refresh_token"},
                status_code=400,
            )

        try:
            tokens = await refresh_access_token(
                refresh_token_raw=str(refresh_token_raw),
                client_id=str(client_id),
                db=db,
            )
            await db.commit()
        except ValueError as e:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": str(e)},
                status_code=400,
            )

        return JSONResponse(tokens)

    else:
        return JSONResponse(
            {"error": "unsupported_grant_type", "error_description": f"Nieobslugiwany grant_type: {grant_type}"},
            status_code=400,
        )
