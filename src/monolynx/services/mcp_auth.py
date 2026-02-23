"""Serwis autentykacji MCP -- generowanie i walidacja tokenow API."""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken


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
