"""Dashboard -- globalny widok rozliczen (cross-project)."""

from __future__ import annotations

import contextlib
import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_project import SettlementProject
from monolynx.services.permissions import check_permission

from .helpers import _get_user_id, templates

router = APIRouter(prefix="/dashboard", tags=["settlements_global"])

_PER_PAGE = 20


async def _get_user_settlement_project_ids(
    db: AsyncSession,
    user_id: uuid.UUID,
    is_superuser: bool,
) -> list[uuid.UUID]:
    """Projekty, w ktorych user ma rozliczenia:read (lub wszystkie dla superusera)."""
    if is_superuser:
        result = await db.execute(select(Project.id).where(Project.is_active.is_(True)))
        return list(result.scalars().all())

    member_result = await db.execute(
        select(Project.id)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == user_id,
            Project.is_active.is_(True),
        )
    )
    allowed: list[uuid.UUID] = []
    for project_id in member_result.scalars().all():
        if await check_permission(db, user_id, project_id, "rozliczenia", "read"):
            allowed.append(project_id)
    return allowed


@router.get("/rozliczenia", response_class=HTMLResponse)
async def settlements_global_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Globalny widok rozliczen -- cross-project lista dla zalogowanego uzytkownika."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    is_superuser = bool(request.session.get("is_superuser", False))
    allowed_project_ids = await _get_user_settlement_project_ids(db, user_id, is_superuser)

    # Parsowanie filtrow z query params
    params = request.query_params
    allowed_set = set(allowed_project_ids)

    # Filtr projektow (multi-select, walidacja do allowed_set)
    filter_project_ids: list[uuid.UUID] = []
    for raw in params.getlist("project_id"):
        with contextlib.suppress(ValueError):
            pid = uuid.UUID(raw)
            if pid in allowed_set:
                filter_project_ids.append(pid)

    # Efektywne projekty do filtrowania
    effective_project_ids = filter_project_ids if filter_project_ids else allowed_project_ids

    # Filtr statusu
    status_filter = params.get("status", "")
    valid_statuses = {"draft", "sent", "paid"}

    # Daty
    date_from: date | None = None
    date_to: date | None = None
    with contextlib.suppress(ValueError):
        raw_from = params.get("date_from", "")
        if raw_from:
            date_from = date.fromisoformat(raw_from)
    with contextlib.suppress(ValueError):
        raw_to = params.get("date_to", "")
        if raw_to:
            date_to = date.fromisoformat(raw_to)

    # Paginacja
    page = max(1, int(params.get("page", "1")))

    if not effective_project_ids:
        # User nie ma dostepu do zadnego projektu z rozliczeniami
        allowed_projects_list: list[Project] = []
        return templates.TemplateResponse(
            request,
            "dashboard/settlements_global/list.html",
            {
                "settlements": [],
                "allowed_projects": allowed_projects_list,
                "filters": {
                    "project_ids": filter_project_ids,
                    "status": status_filter,
                    "date_from": date_from,
                    "date_to": date_to,
                },
                "page": 1,
                "total_pages": 1,
                "total_count": 0,
                "has_next": False,
                "has_prev": False,
                "is_empty": True,
            },
        )

    # Buduj warunki zapytania
    conditions: list[Any] = [
        SettlementProject.project_id.in_(effective_project_ids),
        Settlement.is_active.is_(True),
    ]
    if status_filter and status_filter in valid_statuses:
        conditions.append(Settlement.status == status_filter)
    if date_from:
        conditions.append(Settlement.period_from >= date_from)
    if date_to:
        conditions.append(Settlement.period_to <= date_to)

    # Count
    count_result = await db.execute(
        select(func.count(Settlement.id.distinct())).join(SettlementProject, SettlementProject.settlement_id == Settlement.id).where(*conditions)
    )
    total_count = count_result.scalar() or 0
    total_pages = max(1, (total_count + _PER_PAGE - 1) // _PER_PAGE)
    page = min(page, total_pages)

    # Glowne zapytanie z eager load
    result = await db.execute(
        select(Settlement)
        .join(SettlementProject, SettlementProject.settlement_id == Settlement.id)
        .where(*conditions)
        .options(
            selectinload(Settlement.projects.and_(Project.is_active.is_(True))),
            selectinload(Settlement.tickets),
            selectinload(Settlement.attachments),
            selectinload(Settlement.created_by),
        )
        .order_by(Settlement.created_at.desc())
        .distinct()
        .limit(_PER_PAGE)
        .offset((page - 1) * _PER_PAGE)
    )
    settlements = list(result.scalars().unique().all())

    # Pobierz liste dostepnych projektow do multi-select filtra
    projects_result = await db.execute(select(Project).where(Project.id.in_(allowed_project_ids)).order_by(Project.name))
    allowed_projects_list = list(projects_result.scalars().all())

    return templates.TemplateResponse(
        request,
        "dashboard/settlements_global/list.html",
        {
            "settlements": settlements,
            "allowed_projects": allowed_projects_list,
            "filters": {
                "project_ids": filter_project_ids,
                "status": status_filter,
                "date_from": date_from,
                "date_to": date_to,
            },
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "is_empty": len(settlements) == 0 and not allowed_project_ids,
        },
    )
