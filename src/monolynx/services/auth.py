"""Serwis autentykacji -- API key + sesje dashboardu."""

from __future__ import annotations

import time
import uuid

import bcrypt as _bcrypt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.user import User

# In-memory cache API keys (dict z TTL)
_api_key_cache: dict[str, tuple[uuid.UUID, float]] = {}
CACHE_TTL = 60


async def verify_api_key(
    x_monolynx_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """FastAPI dependency -- walidacja API key z cache."""
    now = time.time()
    if x_monolynx_key in _api_key_cache:
        project_id, cached_at = _api_key_cache[x_monolynx_key]
        if now - cached_at < CACHE_TTL:
            project = await db.get(Project, project_id)
            if project is not None:
                return project

    result = await db.execute(
        select(Project).where(
            Project.api_key == x_monolynx_key,
            Project.is_active.is_(True),
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    _api_key_cache[x_monolynx_key] = (project.id, now)
    return project


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return _bcrypt.checkpw(password.encode(), password_hash.encode())


async def authenticate_user(
    email: str,
    password: str,
    db: AsyncSession,
) -> User | None:
    result = await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None or not user.password_hash:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def get_current_user(
    request: Request,
    db: AsyncSession,
) -> User | None:
    """Pobiera obiekt User z sesji (do sprawdzenia is_superuser itp.)."""
    user_id_str = request.session.get("user_id")
    if not user_id_str:
        return None
    try:
        user_id = uuid.UUID(user_id_str)
    except (ValueError, AttributeError):
        return None
    result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    return result.scalar_one_or_none()
