"""Zaleznosci FastAPI dla API v2: autoryzacja Bearer wspoldzielona z MCP."""

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.api.v2.errors import ApiV2Error
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.services.mcp_auth import verify_bearer_token


async def get_api_user(
    authorization: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Waliduj naglowek `Authorization: Bearer <token>` (OAuth + legacy osk_*).

    401 (nie 422 -- `Header(...)` bez default by wymuszal walidacje FastAPI)
    gdy naglowek brakuje, ma zly schemat albo token jest nieprawidlowy. Kazde
    401 niesie `WWW-Authenticate: Bearer` (RFC 6750).
    """
    if authorization is None or not authorization.lower().startswith("bearer "):
        raise ApiV2Error(
            status=401,
            title="Unauthorized",
            detail="Wymagany naglowek Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    raw_token = authorization[len("Bearer ") :]
    if not raw_token:
        raise ApiV2Error(
            status=401,
            title="Unauthorized",
            detail="Token Bearer nie moze byc pusty",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await verify_bearer_token(raw_token, db)
    if user is None:
        raise ApiV2Error(
            status=401,
            title="Unauthorized",
            detail="Nieprawidlowy lub nieaktywny token API",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_api_user_and_project(
    project_slug: str,
    user: User = Depends(get_api_user),
    db: AsyncSession = Depends(get_db),
) -> tuple[User, Project]:
    """Autoryzuj uzytkownika i sprawdz dostep do projektu po slugu.

    Ta sama logika co _get_user_and_project() w mcp_server.py: 404 dla braku/
    nieaktywnego projektu, 403 dla braku czlonkostwa. Uprawnienia per modul
    (module:action) sprawdzaja serwisy albo `require_permission` w routerze.
    """
    result = await db.execute(select(Project).where(Project.slug == project_slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        raise ApiV2Error(status=404, title="Not Found", detail=f"Projekt '{project_slug}' nie istnieje")

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise ApiV2Error(status=403, title="Forbidden", detail=f"Uzytkownik nie jest czlonkiem projektu '{project_slug}'")

    return user, project
