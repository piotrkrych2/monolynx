"""Serwis OAuth 2.1 -- rejestracja klientow, kody autoryzacji, tokeny."""

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

from monolynx.config import settings
from monolynx.models.oauth import (
    OAuthAccessToken,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthRefreshToken,
)
from monolynx.models.user import User

logger = logging.getLogger(__name__)

# Dozwolone redirect URIs -- Claude Desktop / claude.ai + localhost (dev)
ALLOWED_REDIRECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^https://claude\.ai/api/mcp/auth_callback$"),
    re.compile(r"^https://claude\.com/api/mcp/auth_callback$"),
    re.compile(r"^http://localhost(:\d+)?(/.*)?$"),
    re.compile(r"^http://127\.0\.0\.1(:\d+)?(/.*)?$"),
]


def _is_redirect_uri_allowed(uri: str) -> bool:
    """Sprawdz czy redirect_uri jest na allowlist."""
    return any(p.match(uri) for p in ALLOWED_REDIRECT_PATTERNS)


def _hash_token(raw: str) -> str:
    """SHA256 hash tokenu (szybkie wyszukiwanie w DB)."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """Weryfikacja PKCE S256: SHA256(code_verifier) == code_challenge."""
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
    # Walidacja redirect URIs
    for uri in redirect_uris:
        if not _is_redirect_uri_allowed(uri):
            raise ValueError(f"Niedozwolony redirect_uri: {uri}")

    # Walidacja grant types
    allowed_grants = {"authorization_code", "refresh_token"}
    for gt in grant_types:
        if gt not in allowed_grants:
            raise ValueError(f"Nieobslugiwany grant_type: {gt}")

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
    """Generuj jednorazowy authorization code."""
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
    """Wymien authorization code na access_token + refresh_token (z PKCE)."""
    result = await db.execute(
        select(OAuthAuthorizationCode).where(
            OAuthAuthorizationCode.code == code,
            OAuthAuthorizationCode.client_id == client_id,
        )
    )
    auth_code = result.scalar_one_or_none()

    if auth_code is None:
        raise ValueError("Nieprawidlowy authorization code")

    if auth_code.expires_at < datetime.now(UTC):
        await db.delete(auth_code)
        await db.flush()
        raise ValueError("Authorization code wygasl")

    if auth_code.redirect_uri != redirect_uri:
        raise ValueError("redirect_uri nie pasuje")

    if not _verify_pkce(code_verifier, auth_code.code_challenge):
        raise ValueError("Nieprawidlowy code_verifier (PKCE)")

    user_id = auth_code.user_id
    scope = auth_code.scope

    # Usun zuzyty code (jednorazowy)
    await db.delete(auth_code)

    # Generuj access token
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
    """Odswiez access token za pomoca refresh tokenu."""
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
        raise ValueError("Nieprawidlowy refresh token")

    if refresh_obj.expires_at < datetime.now(UTC):
        raise ValueError("Refresh token wygasl")

    user_id = refresh_obj.user_id
    scope = None  # odczytamy z nowego access tokenu

    # Pobierz scope z poprzedniego access tokenu
    old_access = await db.get(OAuthAccessToken, refresh_obj.access_token_id)
    if old_access:
        scope = old_access.scope

    # Generuj nowy access token
    raw_access = secrets.token_urlsafe(48)
    access_hash = _hash_token(raw_access)
    access_expires = datetime.now(UTC) + timedelta(seconds=settings.OAUTH_ACCESS_TOKEN_TTL)

    new_access = OAuthAccessToken(
        token_hash=access_hash,
        client_id=client_id,
        user_id=user_id,
        scope=scope,
        expires_at=access_expires,
    )
    db.add(new_access)
    await db.flush()

    # Generuj nowy refresh token (rotacja)
    raw_new_refresh = secrets.token_urlsafe(48)
    new_refresh_hash = _hash_token(raw_new_refresh)
    refresh_expires = datetime.now(UTC) + timedelta(seconds=settings.OAUTH_REFRESH_TOKEN_TTL)

    new_refresh = OAuthRefreshToken(
        token_hash=new_refresh_hash,
        access_token_id=new_access.id,
        client_id=client_id,
        user_id=user_id,
        expires_at=refresh_expires,
    )
    db.add(new_refresh)

    # Uniewaznij stary refresh token
    refresh_obj.is_revoked = True
    await db.flush()

    return {
        "access_token": raw_access,
        "token_type": "bearer",
        "expires_in": settings.OAUTH_ACCESS_TOKEN_TTL,
        "refresh_token": raw_new_refresh,
    }


async def verify_oauth_access_token(raw_token: str, db: AsyncSession) -> User | None:
    """Waliduj OAuth access token, zwroc User lub None."""
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
