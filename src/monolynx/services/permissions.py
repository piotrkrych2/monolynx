"""Serwis uprawnien RBAC -- sprawdzanie dostepu per modul i akcja."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import DEFAULT_ROLE_PERMISSIONS, PERMISSION_ACTIONS, PERMISSION_MODULES
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User


async def check_permission(
    db: AsyncSession,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    module: str,
    action: str,
) -> bool:
    """Sprawdza czy user ma uprawnienie module:action w projekcie.

    1. Pobiera User -- jezeli is_superuser=True zwraca True (bypass).
    2. Pobiera ProjectMember z selectinload(role_obj).
    3. Jezeli brak member zwraca False.
    4. Jezeli member ma role_obj -- sprawdza role_obj.permissions.
    5. Fallback: jezeli brak role_obj ale member.role jest ustawione --
       sprawdza DEFAULT_ROLE_PERMISSIONS (kompatybilnosc wsteczna).
    """
    if module not in PERMISSION_MODULES:
        return False
    if action not in PERMISSION_ACTIONS:
        return False

    user_result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = user_result.scalar_one_or_none()
    if user is None:
        return False
    if user.is_superuser:
        return True

    member_result = await db.execute(
        select(ProjectMember)
        .options(selectinload(ProjectMember.role_obj))
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = member_result.scalar_one_or_none()
    if member is None:
        return False

    # Jezeli member ma przypisany role_obj -- uzywamy uprawnien z roli
    if member.role_obj is not None:
        allowed_actions: list[str] = member.role_obj.permissions.get(module, [])
        return action in allowed_actions

    # Fallback: jezeli brak role_obj ale member.role jest jednym z ról systemowych
    # Uzywamy DEFAULT_ROLE_PERMISSIONS dla kompatybilnosci wstecznej
    legacy_role = member.role
    if legacy_role and legacy_role in DEFAULT_ROLE_PERMISSIONS:
        default_actions: list[str] = DEFAULT_ROLE_PERMISSIONS[legacy_role].get(module, [])
        return action in default_actions

    return False


async def require_permission(
    db: AsyncSession,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    module: str,
    action: str,
) -> None:
    """Jak check_permission ale rzuca HTTPException(403) jezeli brak dostepu."""
    has_access = await check_permission(db, user_id, project_id, module, action)
    if not has_access:
        raise HTTPException(
            status_code=403,
            detail=f"Brak uprawnienia {module}:{action}",
        )


async def get_user_permissions(
    db: AsyncSession,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
) -> dict[str, list[str]]:
    """Zwraca pelna mape uprawnien usera w projekcie.

    Superuser zwraca pelne uprawnienia dla wszystkich modulow.
    Zwykly user zwraca permissions z role_obj lub {} jezeli brak roli.
    """
    user_result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    user = user_result.scalar_one_or_none()
    if user is None:
        return {}
    if user.is_superuser:
        return {m: list(PERMISSION_ACTIONS) for m in PERMISSION_MODULES}

    member_result = await db.execute(
        select(ProjectMember)
        .options(selectinload(ProjectMember.role_obj))
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = member_result.scalar_one_or_none()
    if member is None:
        return {}

    # Jezeli member ma przypisany role_obj -- uzywamy uprawnien z roli
    if member.role_obj is not None:
        return dict(member.role_obj.permissions)

    # Fallback: jezeli brak role_obj ale member.role jest jednym z ról systemowych
    legacy_role = member.role
    if legacy_role and legacy_role in DEFAULT_ROLE_PERMISSIONS:
        return {m: list(actions) for m, actions in DEFAULT_ROLE_PERMISSIONS[legacy_role].items()}

    return {}
