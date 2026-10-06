"""Serwis uprawnien RBAC -- sprawdzanie dostepu per modul i akcja."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import (
    DEFAULT_ROLE_PERMISSIONS,
    PERMISSION_ACTIONS,
    PERMISSION_MODULES,
)
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.user import User

OWNER_ROLE_NAME = "Owner"


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


async def ensure_permission(
    db: AsyncSession,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
    module: str,
    action: str,
) -> None:
    """Jak check_permission ale rzuca ValueError jezeli brak dostepu.

    Wariant dla serwisow i narzedzi MCP: komunikat zaczyna sie od
    "Brak uprawnienia", wiec api/v2/common.py::service_error mapuje go na 403,
    a tresc jest identyczna z require_permission z api/v2/common.py.
    """
    if not await check_permission(db, user_id, project_id, module, action):
        raise ValueError(f"Brak uprawnienia {module}:{action}")


async def has_blog_permission(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Czy konto moze publikowac na blogu: aktywny superuser albo aktywne konto z `can_edit_blog`.

    Uprawnienie jest kontowe, nie projektowe: publiczny blog nie jest przypiety do projektu,
    wiec rola w projekcie (takze owner) go nie daje. Nadaje je wylacznie superuser.
    """
    user = (await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))).scalar_one_or_none()
    return user is not None and (user.is_superuser or user.can_edit_blog)


async def ensure_blog_permission(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Jak has_blog_permission ale rzuca ValueError("Brak uprawnienia blog:write") (-> 403 w API v2)."""
    if not await has_blog_permission(db, user_id):
        raise ValueError("Brak uprawnienia blog:write")


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


def missing_permissions(
    requested: Mapping[str, Iterable[str]],
    held: Mapping[str, Iterable[str]],
) -> list[str]:
    """Zwraca posortowana liste "modul:akcja" z `requested`, ktorych brakuje w `held`."""
    missing: set[str] = set()
    for module, actions in requested.items():
        held_actions = set(held.get(module, ()))
        missing.update(f"{module}:{action}" for action in actions if action not in held_actions)
    return sorted(missing)


async def _is_owner_member(db: AsyncSession, member: ProjectMember) -> bool:
    # Z role_id decyduje rola, nie legacy napis: assign_role zapisuje member.role = role.name.lower(),
    # wiec niesystemowa rola "Owner" nie moze czynic ownerem.
    if member.role_id is None:
        return member.role == "owner"
    role = await db.get(Role, member.role_id)
    return role is not None and role.is_system and role.name == OWNER_ROLE_NAME


async def ensure_can_manage_role(
    db: AsyncSession,
    actor_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    granted: Mapping[str, Iterable[str]],
    edited_role: Role | None = None,
    assigned_role: Role | None = None,
    target_member: ProjectMember | None = None,
) -> None:
    """Regula zarzadzania rolami: rzuca ValueError("Brak uprawnienia ...") przy probie eskalacji.

    - `edited_role`: edytowana rola; permissions rol systemowych sa niemodyfikowalne dla wszystkich,
    - `granted`: uprawnienia tworzone/edytowane/przypisywane/nadawane przy zaproszeniu,
    - `assigned_role`: przypisywana rola (systemowa Owner tylko dla ownera i superusera),
    - `target_member`: czlonek, ktorego rola sie zmienia (zmiana roli ownera tylko dla ownera i superusera).

    Owner i superuser nie podlegaja regule podzbioru ani ochronie ownera.
    """
    if edited_role is not None and edited_role.is_system:
        raise ValueError("Brak uprawnienia do zmiany uprawnien roli systemowej")

    user = (await db.execute(select(User).where(User.id == actor_id, User.is_active.is_(True)))).scalar_one_or_none()
    if user is None:
        raise ValueError("Brak uprawnienia do zarzadzania rolami")
    if user.is_superuser:
        return

    actor_member = (
        await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == actor_id))
    ).scalar_one_or_none()
    if actor_member is None:
        raise ValueError("Brak uprawnienia do zarzadzania rolami")
    if await _is_owner_member(db, actor_member):
        return

    if assigned_role is not None and assigned_role.is_system and assigned_role.name == OWNER_ROLE_NAME:
        raise ValueError("Brak uprawnienia do przypisania roli Owner (tylko owner projektu lub superuser)")
    if target_member is not None and await _is_owner_member(db, target_member):
        raise ValueError("Brak uprawnienia do zmiany roli ownera projektu (tylko owner projektu lub superuser)")

    missing = missing_permissions(granted, await get_user_permissions(db, actor_id, project_id))
    if missing:
        raise ValueError(f"Brak uprawnienia do nadania uprawnien, ktorych nie posiadasz: {', '.join(missing)}")
