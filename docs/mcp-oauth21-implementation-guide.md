# Implementacja MCP OAuth 2.1 — Instrukcja dla Claude Code

> Kompletna instrukcja wdrożenia OAuth 2.1 dla serwera MCP (Model Context Protocol) w aplikacji FastAPI.
> Bazuje na produkcyjnej implementacji z projektu **Monolynx** (ticket MON-16, sprint "Integracja 500ki ze Scrumem", 13 SP).
>
> Po tej implementacji Claude Desktop (i claude.ai) łączy się z Twoim serwerem MCP jednym kliknięciem — bez ręcznego kopiowania tokenów.

---

## Spis treści

1. [Jak to działa — flow autoryzacji](#1-jak-to-działa--flow-autoryzacji)
2. [Wymagane RFC / specyfikacje](#2-wymagane-rfc--specyfikacje)
3. [Zmiany w bazie danych — 4 tabele](#3-zmiany-w-bazie-danych--4-tabele)
4. [Konfiguracja (env vars)](#4-konfiguracja-env-vars)
5. [Implementacja krok po kroku](#5-implementacja-krok-po-kroku)
   - [5.1 Modele SQLAlchemy](#51-modele-sqlalchemy)
   - [5.2 Migracja Alembic](#52-migracja-alembic)
   - [5.3 Serwis OAuth (logika biznesowa)](#53-serwis-oauth-logika-biznesowa)
   - [5.4 Endpointy API (router)](#54-endpointy-api-router)
   - [5.5 Template autoryzacji (HTML)](#55-template-autoryzacji-html)
   - [5.6 Integracja z MCP Server](#56-integracja-z-mcp-server)
   - [5.7 Mount w FastAPI + lifespan](#57-mount-w-fastapi--lifespan)
6. [Struktura URL / endpointy](#6-struktura-url--endpointy)
7. [Bezpieczeństwo — checklista](#7-bezpieczeństwo--checklista)
8. [Testy](#8-testy)
9. [Troubleshooting](#9-troubleshooting)
10. [Backward compatibility z legacy tokenami](#10-backward-compatibility-z-legacy-tokenami)

---

## 1. Jak to działa — flow autoryzacji

Cały flow to standardowy **Authorization Code Grant z PKCE** (OAuth 2.1). Claude Desktop robi to automatycznie:

```
Claude Desktop / claude.ai
    │
    ├─ 1. GET /.well-known/oauth-protected-resource
    │      → Dowiaduje się, jaki authorization server obsługuje ten zasób
    │
    ├─ 2. GET /.well-known/oauth-authorization-server
    │      → Odkrywa endpointy: /authorize, /token, /register
    │
    ├─ 3. POST /register
    │      → Dynamic Client Registration (RFC 7591)
    │      → Wysyła: {client_name, redirect_uris, grant_types}
    │      → Dostaje: {client_id}  (publiczny klient, bez secret)
    │
    ├─ 4. Generuje PKCE: code_verifier + code_challenge (SHA256)
    │
    ├─ 5. Otwiera przeglądarkę:
    │      GET /authorize?client_id=X&redirect_uri=Y&code_challenge=Z&code_challenge_method=S256&state=W
    │      → Użytkownik widzi formularz logowania
    │
    ├─ 6. POST /authorize (action=login, email, password)
    │      → Serwer loguje użytkownika (sesja cookie)
    │      → 303 redirect z powrotem na GET /authorize (consent screen)
    │
    ├─ 7. POST /authorize (action=consent)
    │      → Serwer generuje jednorazowy authorization code (10 min TTL)
    │      → 302 redirect na redirect_uri?code=ABC&state=W
    │      → Np. https://claude.ai/api/mcp/auth_callback?code=ABC&state=W
    │
    ├─ 8. POST /token (grant_type=authorization_code, code=ABC, code_verifier=V)
    │      → Serwer weryfikuje PKCE: SHA256(code_verifier) == code_challenge
    │      → Usuwa zużyty code
    │      → Zwraca: {access_token, refresh_token, expires_in, token_type: "bearer"}
    │
    └─ 9. Claude Desktop wywołuje narzędzia MCP:
           Authorization: Bearer <access_token>
           │
           └─ Gdy token wygaśnie:
              POST /token (grant_type=refresh_token, refresh_token=R)
              → Nowy access_token + nowy refresh_token (rotacja)
```

**Kluczowe:** Użytkownik robi tylko jedno — loguje się i klika "Zezwól". Reszta jest automatyczna.

---

## 2. Wymagane RFC / specyfikacje

| RFC | Co robi | Gdzie używane |
|-----|---------|---------------|
| **RFC 8414** | OAuth Authorization Server Metadata | `/.well-known/oauth-authorization-server` |
| **RFC 9728** | OAuth Protected Resource Metadata | `/.well-known/oauth-protected-resource` |
| **RFC 7591** | Dynamic Client Registration | `POST /register` |
| **RFC 7636** | PKCE (Proof Key for Code Exchange) | S256 challenge/verifier w `/authorize` i `/token` |
| **OAuth 2.1** | Uproszczenie OAuth 2.0 | PKCE obowiązkowe, brak implicit grant |

---

## 3. Zmiany w bazie danych — 4 tabele

### Schemat tabel

```sql
-- 1. Zarejestrowani klienci OAuth (np. Claude Desktop)
CREATE TABLE oauth_clients (
    id UUID PRIMARY KEY,
    client_id VARCHAR(255) NOT NULL UNIQUE,     -- "mlx_<random>" (prefiks customowy)
    client_name VARCHAR(255),                    -- np. "Claude AI"
    redirect_uris JSON NOT NULL,                 -- ["https://claude.ai/api/mcp/auth_callback"]
    grant_types JSON NOT NULL,                   -- ["authorization_code", "refresh_token"]
    client_secret VARCHAR(255),                  -- NULL dla public clients (Claude Desktop)
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX ix_oauth_clients_client_id ON oauth_clients(client_id);

-- 2. Jednorazowe kody autoryzacji (10 min TTL)
CREATE TABLE oauth_authorization_codes (
    id UUID PRIMARY KEY,
    code VARCHAR(255) NOT NULL UNIQUE,           -- secrets.token_urlsafe(48)
    client_id VARCHAR(255) NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id),
    redirect_uri VARCHAR(2048) NOT NULL,         -- musi matchować zarejestrowany
    scope VARCHAR(255),
    code_challenge VARCHAR(255) NOT NULL,        -- PKCE S256
    code_challenge_method VARCHAR(10) NOT NULL,  -- zawsze "S256"
    expires_at TIMESTAMPTZ NOT NULL,             -- NOW() + 10min
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX ix_oauth_authorization_codes_code ON oauth_authorization_codes(code);
CREATE INDEX ix_oauth_authorization_codes_user_id ON oauth_authorization_codes(user_id);

-- 3. Tokeny dostępu (30 dni TTL)
CREATE TABLE oauth_access_tokens (
    id UUID PRIMARY KEY,
    token_hash VARCHAR(255) NOT NULL UNIQUE,     -- SHA256, NIGDY surowy token
    client_id VARCHAR(255) NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id),
    scope VARCHAR(255),
    expires_at TIMESTAMPTZ NOT NULL,             -- NOW() + 30 dni
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX ix_oauth_access_tokens_token_hash ON oauth_access_tokens(token_hash);
CREATE INDEX ix_oauth_access_tokens_user_id ON oauth_access_tokens(user_id);

-- 4. Tokeny odświeżania (30 dni TTL, z rotacją)
CREATE TABLE oauth_refresh_tokens (
    id UUID PRIMARY KEY,
    token_hash VARCHAR(255) NOT NULL UNIQUE,     -- SHA256
    access_token_id UUID NOT NULL REFERENCES oauth_access_tokens(id),
    client_id VARCHAR(255) NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id),
    expires_at TIMESTAMPTZ NOT NULL,
    is_revoked BOOLEAN DEFAULT FALSE,            -- rotacja: stary → revoked
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX ix_oauth_refresh_tokens_token_hash ON oauth_refresh_tokens(token_hash);
CREATE INDEX ix_oauth_refresh_tokens_user_id ON oauth_refresh_tokens(user_id);
```

### Wymagania
- Tabela `users` musi istnieć z kolumnami: `id` (UUID PK), `email`, `password_hash`, `is_active` (bool)
- Twoja aplikacja musi mieć mechanizm autentykacji (email + hasło)

---

## 4. Konfiguracja (env vars)

Dodaj do swojego `config.py` / `.env`:

```python
# .env
APP_URL=https://twoja-domena.com        # WAŻNE: pełny URL bez trailing slash
SECRET_KEY=losowy-ciag-min-32-znaki     # do podpisywania sesji cookie

# OAuth 2.1 TTL (opcjonalne — domyślne wartości poniżej)
OAUTH_ACCESS_TOKEN_TTL=2592000          # 30 dni w sekundach
OAUTH_REFRESH_TOKEN_TTL=2592000         # 30 dni
OAUTH_AUTH_CODE_TTL=600                 # 10 minut
```

```python
# config.py (pydantic-settings)
class Settings(BaseSettings):
    APP_URL: str = "http://localhost:8000"
    SECRET_KEY: str = "change-me"

    OAUTH_ACCESS_TOKEN_TTL: int = 2592000   # 30 dni
    OAUTH_REFRESH_TOKEN_TTL: int = 2592000  # 30 dni
    OAUTH_AUTH_CODE_TTL: int = 600          # 10 min
```

---

## 5. Implementacja krok po kroku

### 5.1 Modele SQLAlchemy

Plik: `models/oauth.py`

```python
"""Modele OAuth 2.1 — klienty, kody autoryzacji, tokeny dostępu i odświeżania."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from your_app.models.base import Base  # ← zamień na swój import Base

if TYPE_CHECKING:
    from your_app.models.user import User


class OAuthClient(Base):
    """Zarejestrowany klient OAuth (Dynamic Client Registration)."""
    __tablename__ = "oauth_clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    grant_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    client_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthAuthorizationCode(Base):
    """Kod autoryzacji OAuth (jednorazowy, krótki TTL)."""
    __tablename__ = "oauth_authorization_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    code_challenge: Mapped[str] = mapped_column(String(255), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(10), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship()


class OAuthAccessToken(Base):
    """Token dostępu OAuth (SHA256 hash w DB)."""
    __tablename__ = "oauth_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship()


class OAuthRefreshToken(Base):
    """Token odświeżania OAuth (powiązany z access token)."""
    __tablename__ = "oauth_refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    access_token_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oauth_access_tokens.id"), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    access_token: Mapped[OAuthAccessToken] = relationship()
    user: Mapped[User] = relationship()
```

### 5.2 Migracja Alembic

Wygeneruj migrację:
```bash
alembic revision -m "add OAuth 2.1 tables"
```

Pełna migracja — patrz sekcja [3. Zmiany w bazie danych](#3-zmiany-w-bazie-danych--4-tabele) po dokładny SQL. Kolejność tworzenia tabel jest ważna (FK):
1. `oauth_clients` (brak FK do innych tabel OAuth)
2. `oauth_authorization_codes` (FK → users)
3. `oauth_access_tokens` (FK → users)
4. `oauth_refresh_tokens` (FK → oauth_access_tokens, users)

**Downgrade** — usuwaj w odwrotnej kolejności (refresh → access → codes → clients).

### 5.3 Serwis OAuth (logika biznesowa)

Plik: `services/oauth.py`

```python
"""Serwis OAuth 2.1 — rejestracja klientów, kody autoryzacji, tokeny."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from your_app.config import settings
from your_app.models.oauth import (
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
)
from your_app.models.user import User

logger = logging.getLogger(__name__)

# =====================================================================
# REDIRECT URI ALLOWLIST
# Tylko te URI mogą być zarejestrowane jako redirect_uri.
# Claude Desktop/claude.ai używa tych callbacków.
# Localhost dozwolony do developmentu.
# =====================================================================
ALLOWED_REDIRECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^https://claude\.ai/api/mcp/auth_callback$"),
    re.compile(r"^https://claude\.com/api/mcp/auth_callback$"),
    re.compile(r"^http://localhost(:\d+)?(/.*)?$"),
    re.compile(r"^http://127\.0\.0\.1(:\d+)?(/.*)?$"),
]


def _is_redirect_uri_allowed(uri: str) -> bool:
    return any(p.match(uri) for p in ALLOWED_REDIRECT_PATTERNS)


def _hash_token(raw: str) -> str:
    """SHA256 hash tokenu — NIGDY nie przechowuj surowego tokenu w DB."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """PKCE S256: base64url(SHA256(code_verifier)) == code_challenge.
    Używa hmac.compare_digest() dla bezpieczeństwa przed timing attacks."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(computed, code_challenge)


async def register_client(
    client_name: str | None,
    redirect_uris: list[str],
    grant_types: list[str],
    db: AsyncSession,
) -> dict[str, object]:
    """Dynamic Client Registration (RFC 7591)."""
    for uri in redirect_uris:
        if not _is_redirect_uri_allowed(uri):
            raise ValueError(f"Niedozwolony redirect_uri: {uri}")

    allowed_grants = {"authorization_code", "refresh_token"}
    for gt in grant_types:
        if gt not in allowed_grants:
            raise ValueError(f"Nieobsługiwany grant_type: {gt}")

    # Prefiks "mlx_" — zamień na swój (np. "myapp_")
    client_id = "mlx_" + secrets.token_urlsafe(32)

    client = OAuthClient(
        client_id=client_id,
        client_name=client_name,
        redirect_uris=redirect_uris,
        grant_types=grant_types,
    )
    db.add(client)
    await db.flush()

    return {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "grant_types": client.grant_types,
    }


async def create_authorization_code(
    client_id: str,
    user_id: str,
    redirect_uri: str,
    scope: str | None,
    code_challenge: str,
    code_challenge_method: str,
    db: AsyncSession,
) -> str:
    """Generuj jednorazowy authorization code (10 min TTL)."""
    if code_challenge_method != "S256":
        raise ValueError("Wymagana metoda PKCE: S256")

    code = secrets.token_urlsafe(48)
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.OAUTH_AUTH_CODE_TTL)

    auth_code = OAuthAuthorizationCode(
        code=code,
        client_id=client_id,
        user_id=user_id,
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        expires_at=expires_at,
    )
    db.add(auth_code)
    await db.flush()
    return code


async def exchange_code_for_tokens(
    code: str,
    code_verifier: str,
    client_id: str,
    redirect_uri: str,
    db: AsyncSession,
) -> dict[str, object]:
    """Wymień authorization code na access_token + refresh_token (z weryfikacją PKCE)."""
    result = await db.execute(
        select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code == code,
            OAuthAuthorizationCode.client_id == client_id,
        )
    )
    auth_code = result.scalar_one_or_none()

    if auth_code is None:
        raise ValueError("Nieprawidłowy authorization code")

    if auth_code.expires_at < datetime.now(UTC):
        await db.delete(auth_code)
        await db.flush()
        raise ValueError("Authorization code wygasł")

    if auth_code.redirect_uri != redirect_uri:
        raise ValueError("redirect_uri nie pasuje")

    # PKCE — kluczowy krok bezpieczeństwa
    if not _verify_pkce(code_verifier, auth_code.code_challenge):
        raise ValueError("Nieprawidłowy code_verifier (PKCE)")

    user_id = auth_code.user_id
    scope = auth_code.scope

    # Usuń zużyty code (jednorazowy!)
    await db.delete(auth_code)

    # Generuj access token (surowy → SHA256 do DB)
    raw_access = secrets.token_urlsafe(48)
    access_hash = _hash_token(raw_access)
    access_expires = datetime.now(UTC) + timedelta(seconds=settings.OAUTH_ACCESS_TOKEN_TTL)

    access_token = OAuthAccessToken(
        token_hash=access_hash,
        client_id=client_id,
        user_id=user_id,
        scope=scope,
        expires_at=access_expires,
    )
    db.add(access_token)
    await db.flush()

    # Generuj refresh token
    raw_refresh = secrets.token_urlsafe(48)
    refresh_hash = _hash_token(raw_refresh)
    refresh_expires = datetime.now(UTC) + timedelta(seconds=settings.OAUTH_REFRESH_TOKEN_TTL)

    refresh_token = OAuthRefreshToken(
        token_hash=refresh_hash,
        access_token_id=access_token.id,
        client_id=client_id,
        user_id=user_id,
        expires_at=refresh_expires,
    )
    db.add(refresh_token)
    await db.flush()

    return {
        "access_token": raw_access,
        "token_type": "bearer",
        "expires_in": settings.OAUTH_ACCESS_TOKEN_TTL,
        "refresh_token": raw_refresh,
    }


async def refresh_access_token(
    refresh_token_raw: str,
    client_id: str,
    db: AsyncSession,
) -> dict[str, object]:
    """Odśwież access token. Implementuje rotację refresh tokenów."""
    refresh_hash = _hash_token(refresh_token_raw)

    result = await db.execute(
        select(OAuthRefreshToken).where(
            OAuthRefreshToken.token_hash == refresh_hash,
            OAuthRefreshToken.client_id == client_id,
            OAuthRefreshToken.is_revoked.is_(False),
        )
    )
    refresh_obj = result.scalar_one_or_none()

    if refresh_obj is None:
        raise ValueError("Nieprawidłowy refresh token")

    if refresh_obj.expires_at < datetime.now(UTC):
        raise ValueError("Refresh token wygasł")

    user_id = refresh_obj.user_id

    # Zachowaj scope z poprzedniego access tokenu
    scope = None
    old_access = await db.get(OAuthAccessToken, refresh_obj.access_token_id)
    if old_access:
        scope = old_access.scope

    # Nowy access token
    raw_access = secrets.token_urlsafe(48)
    access_hash = _hash_token(raw_access)
    access_expires = datetime.now(UTC) + timedelta(seconds=settings.OAUTH_ACCESS_TOKEN_TTL)

    new_access = OAuthAccessToken(
        token_hash=access_hash, client_id=client_id,
        user_id=user_id, scope=scope, expires_at=access_expires,
    )
    db.add(new_access)
    await db.flush()

    # Nowy refresh token (ROTACJA — stary jest revoked)
    raw_new_refresh = secrets.token_urlsafe(48)
    new_refresh_hash = _hash_token(raw_new_refresh)
    refresh_expires = datetime.now(UTC) + timedelta(seconds=settings.OAUTH_REFRESH_TOKEN_TTL)

    new_refresh = OAuthRefreshToken(
        token_hash=new_refresh_hash, access_token_id=new_access.id,
        client_id=client_id, user_id=user_id, expires_at=refresh_expires,
    )
    db.add(new_refresh)

    # Unieważnij stary refresh token
    refresh_obj.is_revoked = True
    await db.flush()

    return {
        "access_token": raw_access,
        "token_type": "bearer",
        "expires_in": settings.OAUTH_ACCESS_TOKEN_TTL,
        "refresh_token": raw_new_refresh,
    }


async def verify_oauth_access_token(raw_token: str, db: AsyncSession) -> User | None:
    """Waliduj OAuth access token, zwróć User lub None."""
    token_hash = _hash_token(raw_token)

    result = await db.execute(
        select(User)
        .join(OAuthAccessToken, OAuthAccessToken.user_id == User.id)
        .where(
            OAuthAccessToken.token_hash == token_hash,
            OAuthAccessToken.expires_at > datetime.now(UTC),
            User.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()
```

### 5.4 Endpointy API (router)

Plik: `api/oauth.py`

**6 endpointów** — 2 discovery, 1 registration, 2 authorize (GET+POST), 1 token:

```python
"""OAuth 2.1 API — endpointy autoryzacji zgodne ze specyfikacją MCP."""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from your_app.config import settings
from your_app.database import get_db
from your_app.models.oauth import OAuthClient
from your_app.services.auth import authenticate_user  # Twoja funkcja logowania
from your_app.services.oauth import (
    create_authorization_code,
    exchange_code_for_tokens,
    refresh_access_token,
    register_client,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["oauth"])
templates = Jinja2Templates(directory="templates")


# =====================================================================
# 1. DISCOVERY ENDPOINTS (RFC 9728 + RFC 8414)
# Claude Desktop odpytuje te endpointy automatycznie.
# =====================================================================

@router.get("/.well-known/oauth-protected-resource")
async def oauth_protected_resource() -> JSONResponse:
    """RFC 9728 — metadata chronionego zasobu (MCP server)."""
    return JSONResponse({
        "resource": settings.APP_URL,
        "authorization_servers": [settings.APP_URL],
        "bearer_methods_supported": ["header"],
    })


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata() -> JSONResponse:
    """RFC 8414 — metadata serwera autoryzacji OAuth."""
    return JSONResponse({
        "issuer": settings.APP_URL,
        "authorization_endpoint": f"{settings.APP_URL}/authorize",
        "token_endpoint": f"{settings.APP_URL}/token",
        "registration_endpoint": f"{settings.APP_URL}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],  # public clients
    })


# =====================================================================
# 2. DYNAMIC CLIENT REGISTRATION (RFC 7591)
# =====================================================================

@router.post("/register")
async def register_oauth_client(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
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


# =====================================================================
# 3. AUTHORIZATION ENDPOINT (GET = formularz, POST = login/consent/deny)
# =====================================================================

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
    """Formularz autoryzacji — logowanie lub consent screen."""
    if response_type != "code":
        return HTMLResponse("Nieobsługiwany response_type", status_code=400)
    if code_challenge_method != "S256":
        return HTMLResponse("Wymagana metoda PKCE: S256", status_code=400)

    # Sprawdź klienta
    result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        return HTMLResponse("Nieznany client_id", status_code=400)
    if redirect_uri not in client.redirect_uris:
        return HTMLResponse("Niedozwolony redirect_uri", status_code=400)

    user_id = request.session.get("user_id")

    return templates.TemplateResponse(request, "oauth/authorize.html", {
        "logged_in": user_id is not None,
        "client_name": client.client_name or client.client_id,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "scope": scope,
        "error": None,
    })


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
    """Przetworzenie: login → consent → redirect z kodem."""
    # Walidacja klienta
    result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        return HTMLResponse("Nieznany client_id", status_code=400)
    if redirect_uri not in client.redirect_uris:
        return HTMLResponse("Niedozwolony redirect_uri", status_code=400)

    user_id = request.session.get("user_id")

    # --- LOGIN ---
    if action == "login" and email and password:
        user = await authenticate_user(email, password, db)
        if user is None:
            return templates.TemplateResponse(request, "oauth/authorize.html", {
                "logged_in": False,
                "client_name": client.client_name or client.client_id,
                "client_id": client_id, "redirect_uri": redirect_uri,
                "state": state, "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
                "scope": scope, "error": "Nieprawidłowy email lub hasło",
            })

        # Zaloguj w sesji
        request.session["user_id"] = str(user.id)

        # Redirect z powrotem na GET /authorize (teraz pokaże consent)
        params = {
            "client_id": client_id, "redirect_uri": redirect_uri,
            "response_type": "code", "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method, "scope": scope,
        }
        return RedirectResponse(url=f"/authorize?{urlencode(params)}", status_code=303)

    # --- CONSENT (użytkownik zalogowany, klika "Zezwól") ---
    if action == "consent" and user_id:
        code = await create_authorization_code(
            client_id=client_id, user_id=user_id,
            redirect_uri=redirect_uri, scope=scope or None,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method, db=db,
        )
        await db.commit()

        params = {"code": code}
        if state:
            params["state"] = state
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{separator}{urlencode(params)}",
            status_code=302,
        )

    # --- DENY ---
    if action == "deny":
        params = {"error": "access_denied"}
        if state:
            params["state"] = state
        separator = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(
            url=f"{redirect_uri}{separator}{urlencode(params)}",
            status_code=302,
        )

    return HTMLResponse("Nieprawidłowe żądanie", status_code=400)


# =====================================================================
# 4. TOKEN ENDPOINT (form-urlencoded — standard OAuth)
# =====================================================================

@router.post("/token")
async def token_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Wymiana code → tokeny lub refresh → nowe tokeny."""
    form = await request.form()
    grant_type = form.get("grant_type", "")
    client_id = form.get("client_id", "")

    if not client_id:
        return JSONResponse(
            {"error": "invalid_client", "error_description": "client_id wymagany"},
            status_code=401,
        )

    # Sprawdź klienta
    result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == str(client_id)))
    client = result.scalar_one_or_none()
    if client is None:
        return JSONResponse(
            {"error": "invalid_client", "error_description": "Nieznany client_id"},
            status_code=401,
        )

    # --- AUTHORIZATION CODE GRANT ---
    if grant_type == "authorization_code":
        code = form.get("code", "")
        code_verifier = form.get("code_verifier", "")
        redirect_uri = form.get("redirect_uri", "")

        if not code or not code_verifier or not redirect_uri:
            return JSONResponse(
                {"error": "invalid_request", "error_description": "Brak wymaganych parametrów"},
                status_code=400,
            )

        try:
            tokens = await exchange_code_for_tokens(
                code=str(code), code_verifier=str(code_verifier),
                client_id=str(client_id), redirect_uri=str(redirect_uri), db=db,
            )
            await db.commit()
        except ValueError as e:
            return JSONResponse(
                {"error": "invalid_grant", "error_description": str(e)},
                status_code=400,
            )

        return JSONResponse(tokens)

    # --- REFRESH TOKEN GRANT ---
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
                client_id=str(client_id), db=db,
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
            {"error": "unsupported_grant_type"},
            status_code=400,
        )
```

### 5.5 Template autoryzacji (HTML)

Plik: `templates/oauth/authorize.html`

Template musi obsługiwać dwa stany: **formularz logowania** (gdy użytkownik nie jest zalogowany) i **consent screen** (gdy jest zalogowany). Wszystkie parametry OAuth przekazywane jako hidden fields.

```html
<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Autoryzacja - Twoja Aplikacja</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-900 text-gray-100 min-h-screen flex items-center justify-center">
    <div class="bg-gray-800 p-8 rounded-lg shadow-lg w-full max-w-md">
        <h1 class="text-2xl font-bold text-indigo-400 mb-6 text-center">Twoja Aplikacja</h1>

        {% if error %}
        <div class="bg-red-900/50 border border-red-500 text-red-300 px-4 py-2 rounded mb-4">
            {{ error }}
        </div>
        {% endif %}

        {% if not logged_in %}
        {# ===== FORMULARZ LOGOWANIA ===== #}
        <p class="text-gray-400 text-sm mb-4 text-center">
            Zaloguj się, aby zezwolić aplikacji
            <strong class="text-gray-200">{{ client_name }}</strong>
            na dostęp do Twojego konta.
        </p>
        <form method="post" action="/authorize" class="space-y-4">
            {# Parametry OAuth — hidden fields #}
            <input type="hidden" name="client_id" value="{{ client_id }}">
            <input type="hidden" name="redirect_uri" value="{{ redirect_uri }}">
            <input type="hidden" name="state" value="{{ state }}">
            <input type="hidden" name="code_challenge" value="{{ code_challenge }}">
            <input type="hidden" name="code_challenge_method" value="{{ code_challenge_method }}">
            <input type="hidden" name="scope" value="{{ scope }}">
            <input type="hidden" name="action" value="login">

            <div>
                <label class="block text-sm text-gray-400 mb-1">Email</label>
                <input type="email" name="email" required
                    class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
            </div>
            <div>
                <label class="block text-sm text-gray-400 mb-1">Hasło</label>
                <input type="password" name="password" required
                    class="w-full bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white">
            </div>
            <button type="submit"
                class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2 rounded">
                Zaloguj się
            </button>
        </form>

        {% else %}
        {# ===== CONSENT SCREEN ===== #}
        <p class="text-gray-400 text-sm mb-4 text-center">
            Aplikacja <strong class="text-gray-200">{{ client_name }}</strong>
            prosi o dostęp do Twojego konta.
        </p>

        <div class="bg-gray-700/50 rounded p-3 mb-6">
            <p class="text-sm text-gray-400">Aplikacja będzie mogła:</p>
            <ul class="text-sm text-gray-300 mt-2 space-y-1">
                <li>- Odczytywać Twoje projekty i dane</li>
                <li>- Tworzyć i modyfikować zasoby w Twoim imieniu</li>
            </ul>
        </div>

        <div class="flex gap-3">
            {# Przycisk ODMÓW #}
            <form method="post" action="/authorize" class="flex-1">
                <input type="hidden" name="client_id" value="{{ client_id }}">
                <input type="hidden" name="redirect_uri" value="{{ redirect_uri }}">
                <input type="hidden" name="state" value="{{ state }}">
                <input type="hidden" name="code_challenge" value="{{ code_challenge }}">
                <input type="hidden" name="code_challenge_method" value="{{ code_challenge_method }}">
                <input type="hidden" name="scope" value="{{ scope }}">
                <input type="hidden" name="action" value="deny">
                <button type="submit"
                    class="w-full bg-gray-600 hover:bg-gray-500 text-white font-medium py-2 rounded">
                    Odmów
                </button>
            </form>
            {# Przycisk ZEZWÓL #}
            <form method="post" action="/authorize" class="flex-1">
                <input type="hidden" name="client_id" value="{{ client_id }}">
                <input type="hidden" name="redirect_uri" value="{{ redirect_uri }}">
                <input type="hidden" name="state" value="{{ state }}">
                <input type="hidden" name="code_challenge" value="{{ code_challenge }}">
                <input type="hidden" name="code_challenge_method" value="{{ code_challenge_method }}">
                <input type="hidden" name="scope" value="{{ scope }}">
                <input type="hidden" name="action" value="consent">
                <button type="submit"
                    class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2 rounded">
                    Zezwól
                </button>
            </form>
        </div>
        {% endif %}
    </div>
</body>
</html>
```

### 5.6 Integracja z MCP Server

W pliku `mcp_server.py` — zmień weryfikację tokenu na dual-mode (OAuth + legacy):

```python
from fastmcp import FastMCP, Context
from your_app.services.oauth import verify_oauth_access_token

mcp = FastMCP(
    "Your App",
    streamable_http_path="/",
    json_response=True,
)


async def _auth(ctx: Context) -> User:
    """Wyciągnij token z nagłówka HTTP i zwaliduj (OAuth + legacy)."""
    raw_token = await _get_auth_header(ctx)
    return await _verify_token(raw_token)


async def _get_auth_header(ctx: Context) -> str:
    """Pobierz raw token z nagłówka Authorization: Bearer <token>."""
    request_ctx = ctx.request_context
    if request_ctx is None:
        raise ValueError("Brak kontekstu HTTP")
    starlette_request = getattr(request_ctx, "request", None)
    if starlette_request is None:
        raise ValueError("Brak kontekstu HTTP request")
    auth_header = starlette_request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise ValueError("Brak tokenu Bearer")
    return auth_header[7:]


async def _verify_token(raw_token: str) -> User:
    """Waliduj token — najpierw OAuth, potem legacy."""
    # 1. Próbuj OAuth access token
    try:
        async with async_session_factory() as db:
            user = await verify_oauth_access_token(raw_token, db)
        if user is not None:
            return user
    except Exception:
        pass  # Tabele OAuth mogą nie istnieć

    # 2. Fallback na legacy token (opcjonalny — jeśli masz stary system)
    async with async_session_factory() as db:
        user = await verify_legacy_token(raw_token, db)
    if user is None:
        raise ValueError("Nieprawidłowy lub nieaktywny token API")
    return user


# Każde narzędzie MCP używa _auth():
@mcp.tool()
async def my_tool(ctx: Context, param: str) -> dict:
    user = await _auth(ctx)  # ← zawsze najpierw autoryzacja
    # ... logika narzędzia
```

### 5.7 Mount w FastAPI + lifespan

**Kluczowa konfiguracja w `main.py`:**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from your_app.config import settings
from your_app.mcp_server import mcp as mcp_server


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... inne inicjalizacje ...

    # WAŻNE: Starlette NIE wywołuje lifespanów zamontowanych sub-aplikacji,
    # więc session_manager MCP musi być uruchomiony ręcznie!
    async with mcp_server.session_manager.run():
        yield

    # ... cleanup ...


app = FastAPI(title="Your App", lifespan=lifespan)

# Session middleware (wymagane do logowania w /authorize)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)

# Rejestracja OAuth routera (WAŻNE: bez prefiksu — endpointy na root level)
from your_app.api.oauth import router as oauth_router
app.include_router(oauth_router)

# Mount MCP na /mcp
_mcp_http_app = mcp_server.streamable_http_app()
app.mount("/mcp", _mcp_http_app)

# OPCJONALNIE: mount MCP też na root "/" (Claude Desktop łączy się na APP_URL)
# Routery FastAPI mają priorytet nad mountami, więc nie zaslonią /authorize, /token itp.
app.mount("/", _mcp_http_app)
```

**Ważne uwagi:**
- `SessionMiddleware` jest **wymagane** — OAuth flow używa sesji cookie do przechowywania stanu logowania między krokami login → consent
- OAuth router **nie ma prefiksu** — endpointy muszą być na `/.well-known/...`, `/authorize`, `/token`, `/register` (root level)
- MCP mount na `"/"` jest opcjonalny ale wygodny — Claude Desktop może łączyć się na `APP_URL` bez dodawania `/mcp/`
- `mcp_server.session_manager.run()` w lifespan jest **obowiązkowe** — bez tego MCP nie będzie działać

---

## 6. Struktura URL / endpointy

```
# Discovery (GET, publiczne)
/.well-known/oauth-protected-resource     → metadata zasobu (RFC 9728)
/.well-known/oauth-authorization-server   → metadata serwera OAuth (RFC 8414)

# Registration (POST, publiczne)
/register                                 → Dynamic Client Registration (RFC 7591)

# Authorization (GET+POST, formularz HTML)
/authorize                                → GET: formularz logowania/consent
                                          → POST: login, consent, deny

# Token (POST, form-urlencoded)
/token                                    → grant_type=authorization_code
                                          → grant_type=refresh_token

# MCP Server (POST, Bearer token auth)
/mcp/                                     → narzędzia MCP (Streamable HTTP)
/                                         → fallback MCP mount (opcjonalny)
```

---

## 7. Bezpieczeństwo — checklista

| Element | Jak zaimplementowane | Dlaczego |
|---------|---------------------|----------|
| **PKCE S256** | Obowiązkowe (`code_challenge_method != "S256"` → error) | Zapobiega przechwyceniu authorization code |
| **Timing-safe PKCE** | `hmac.compare_digest()` zamiast `==` | Zapobiega timing attacks |
| **Token hashing** | SHA256 hash w DB, surowy token nigdy nie jest zapisywany | Wyciek DB nie kompromituje tokenów |
| **Auth code jednorazowy** | `await db.delete(auth_code)` po wymianie | Zapobiega replay attacks |
| **Auth code TTL** | 10 minut (`OAUTH_AUTH_CODE_TTL=600`) | Minimalizuje okno ataku |
| **Refresh token rotation** | Nowy refresh token przy każdym odświeżeniu, stary `is_revoked=True` | Wykrywa kradzież tokenu |
| **Redirect URI allowlist** | Regex patterns — tylko claude.ai + localhost | Zapobiega open redirect |
| **Public clients** | `token_endpoint_auth_methods_supported: ["none"]`, brak client_secret | Claude Desktop to public client |
| **State parameter** | Przekazywany przez cały flow | CSRF protection |
| **Session cookie** | `SessionMiddleware` z `SECRET_KEY` | Bezpieczne przechowywanie stanu logowania |

---

## 8. Testy

### Jak generować PKCE w testach

```python
import base64
import hashlib
import secrets

def _generate_pkce() -> tuple[str, str]:
    """Zwraca (code_verifier, code_challenge) dla S256."""
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge
```

### Co testować (klasy testów z Monolynx)

1. **TestOAuthMetadata** — oba `.well-known` endpointy zwracają poprawny JSON
2. **TestClientRegistration** — rejestracja z poprawnymi URI, z claude.ai URI, z niedozwolonym URI (400)
3. **TestAuthorizeEndpoint** — walidacja parametrów, renderowanie formularza
4. **TestFullOAuthFlow** — pełen e2e: register → login → consent → token exchange → weryfikacja tokenu
5. **TestRefreshToken** — odświeżanie z rotacją, zrevokowany token nie działa
6. **TestTokenEndpointErrors** — brakujące parametry, nieprawidłowy code, wygasły code
7. **TestLegacyTokenStillWorks** — backward compatibility ze starymi tokenami

### Przykład testu pełnego flow

```python
@pytest.mark.integration
class TestFullOAuthFlow:
    async def test_complete_flow(self, client, db_session):
        # 1. Zarejestruj klienta
        reg = await client.post("/register", json={
            "client_name": "Test",
            "redirect_uris": ["http://localhost:3000/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
        })
        assert reg.status_code == 201
        client_id = reg.json()["client_id"]

        # 2. Stwórz usera
        user = User(email="test@example.com", password_hash=hash_password("secret123"))
        db_session.add(user)
        await db_session.flush()

        # 3. PKCE
        code_verifier, code_challenge = _generate_pkce()

        # 4. Login
        login_resp = await client.post("/authorize", data={
            "client_id": client_id,
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "scope": "",
            "email": "test@example.com",
            "password": "secret123",
            "action": "login",
        })
        assert login_resp.status_code == 303  # redirect do consent

        # 5. Consent
        consent_resp = await client.post("/authorize", data={
            "client_id": client_id,
            "redirect_uri": "http://localhost:3000/callback",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "scope": "",
            "action": "consent",
        }, follow_redirects=False)
        assert consent_resp.status_code == 302
        location = consent_resp.headers["location"]
        assert "code=" in location

        # 6. Wyciągnij code z URL
        from urllib.parse import parse_qs, urlparse
        code = parse_qs(urlparse(location).query)["code"][0]

        # 7. Wymień code na tokeny
        token_resp = await client.post("/token", data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": "http://localhost:3000/callback",
        })
        assert token_resp.status_code == 200
        tokens = token_resp.json()
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert tokens["token_type"] == "bearer"
```

---

## 9. Troubleshooting

### "OAuth not configured — migration pending" (503)
Tabele OAuth nie istnieją. Uruchom migrację:
```bash
make migrate
# lub: alembic upgrade head
```

### Claude Desktop nie otwiera przeglądarki
- Sprawdź czy `APP_URL` jest poprawny i dostępny z internetu (lub localhost w dev)
- Sprawdź logi — Claude Desktop odpytuje `/.well-known/oauth-protected-resource` jako pierwszy krok

### "Nieznany client_id" po restarcie
- Dynamic Client Registration tworzy klienta w DB — jeśli czyścisz DB, klient znika
- Claude Desktop zarejestruje się ponownie automatycznie

### "Niedozwolony redirect_uri"
- Sprawdź `ALLOWED_REDIRECT_PATTERNS` w `services/oauth.py`
- Dla claude.ai: `https://claude.ai/api/mcp/auth_callback`
- Dla Claude Desktop (local): `http://localhost:*`

### "Nieprawidłowy code_verifier (PKCE)"
- Code verifier musi pasować do code_challenge z kroku `/authorize`
- Upewnij się, że `code_challenge = base64url(SHA256(code_verifier))` (bez paddingu `=`)

### Sesja nie działa (użytkownik nie jest zalogowany po login)
- Upewnij się, że `SessionMiddleware` jest dodane do FastAPI app
- Sprawdź `SECRET_KEY` — musi być stały między restartami
- Cookie `same_site` musi być `"lax"` (domyślne w Starlette)

### MCP nie działa (timeout, connection refused)
- `mcp_server.session_manager.run()` musi być w `lifespan` — bez tego MCP nie przyjmuje połączeń
- Sprawdź mount: `/mcp` i opcjonalnie `/`

---

## 10. Backward compatibility z legacy tokenami

Jeśli masz istniejących użytkowników z ręcznymi tokenami API (np. `osk_*`), implementuj dual-mode w `_verify_token()`:

```python
async def _verify_token(raw_token: str) -> User:
    # 1. Najpierw OAuth
    try:
        user = await verify_oauth_access_token(raw_token, db)
        if user:
            return user
    except Exception:
        pass  # Tabele mogą nie istnieć

    # 2. Fallback na legacy
    user = await verify_legacy_token(raw_token, db)
    if user is None:
        raise ValueError("Nieprawidłowy token")
    return user
```

To pozwala na stopniową migrację — stare tokeny działają, nowi użytkownicy używają OAuth.

---

## Zależności Python

```
fastapi
fastmcp
starlette          # SessionMiddleware
sqlalchemy[asyncio]
pydantic-settings
```

Nie potrzebujesz żadnej dodatkowej biblioteki OAuth — cała implementacja to czysty Python (hashlib, secrets, base64, hmac).

---

## Podsumowanie — co musisz zrobić

1. **Dodaj 4 tabele** do bazy (migracja Alembic)
2. **Dodaj 3 zmienne** do config (`OAUTH_*_TTL`)
3. **Stwórz 3 pliki**: `models/oauth.py`, `services/oauth.py`, `api/oauth.py`
4. **Stwórz template**: `templates/oauth/authorize.html`
5. **Zarejestruj router** OAuth w `main.py` (bez prefiksu!)
6. **Zmień auth w MCP** na dual-mode (OAuth + legacy)
7. **Dodaj `session_manager.run()`** do lifespan
8. **Upewnij się** że `SessionMiddleware` jest skonfigurowany
9. **Uruchom migrację** i przetestuj

Claude Desktop / claude.ai podłączy się automatycznie — wystarczy podać URL serwera.
