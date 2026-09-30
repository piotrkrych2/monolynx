"""Serwis autentykacji MCP -- generowanie i walidacja tokenow API.

verify_bearer_token() jest wspolnym punktem wejscia dla MCP (mcp_server._verify_token)
i API v2 (api/v2/deps.py::get_api_user): probuje OAuth access token, potem legacy
osk_* (MON-140).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken

logger = logging.getLogger(__name__)


def generate_api_token() -> tuple[str, str]:
    """Generuj token API. Zwraca (raw_token, token_hash)."""
    raw = "osk_" + secrets.token_urlsafe(32)
    token_hash = hash_token(raw)
    return raw, token_hash


def hash_token(raw: str) -> str:
    """SHA256 hash tokenu (szybkie wyszukiwanie w DB)."""
    return hashlib.sha256(raw.encode()).hexdigest()


async def verify_mcp_token(raw_token: str, db: AsyncSession) -> User | None:
    """Waliduj token MCP, zwroc User lub None."""
    hashed = hash_token(raw_token)
    result = await db.execute(
        select(User)
        .join(UserApiToken)
        .where(
            UserApiToken.token_hash == hashed,
            UserApiToken.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if user:
        await db.execute(update(UserApiToken).where(UserApiToken.token_hash == hashed).values(last_used_at=func.now()))
        await db.commit()
    return user


async def verify_bearer_token(
    raw_token: str,
    db: AsyncSession,
    *,
    legacy_verifier: Callable[[str, AsyncSession], Awaitable[User | None]] | None = None,
) -> User | None:
    """Waliduj token Bearer: najpierw OAuth access token, potem fallback na legacy osk_*.

    Uzywa PRZEKAZANEJ sesji `db` -- nie otwiera wlasnej (wywolujacy decyduje o cyklu
    zycia sesji, patrz .claude/rules/db-commit.md). Wyjatek na sciezce OAuth (np.
    tabele OAuth jeszcze nie istnieja) zostawia przerwana transakcje na wspolnej
    sesji, dlatego rollback() idzie PRZED fallbackiem, nie po nim.

    `legacy_verifier` domyslnie None -> uzywamy `verify_mcp_token` z TEGO modulu,
    rozwiazywanego jako nazwa modulowa W CZASIE WYWOLANIA (nie jako wartosc domyslna
    argumentu), bo testy tej funkcji (tests/unit/test_mcp_auth.py) patchuja
    `monolynx.services.mcp_auth.verify_mcp_token` i oczekuja, ze ta podmiana
    zadziala tu bez zmian. mcp_server._verify_token przekazuje explicite
    legacy_verifier=verify_mcp_token (jego wlasny modulowy import) z innego
    powodu: ~440 istniejacych testow MCP (tests/unit/test_mcp_server.py i inne)
    patchuja `monolynx.mcp_server.verify_mcp_token`, nie nazwe w tym module --
    bez explicite przekazania tamten patch nie mialby wplywu na fallback.
    """
    try:
        from monolynx.services.oauth import verify_oauth_access_token

        user = await verify_oauth_access_token(raw_token, db)
        if user is not None:
            return user
    except Exception:
        logger.debug("OAuth verification skipped (tables may not exist)")
        await db.rollback()

    verifier = legacy_verifier if legacy_verifier is not None else verify_mcp_token
    return await verifier(raw_token, db)
