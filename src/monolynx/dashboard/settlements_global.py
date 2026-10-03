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
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_project import SettlementProject
from monolynx.services.settlements import fully_permitted_settlements_clause, get_permitted_project_ids

from .helpers import get_session_user, templates

router = APIRouter(prefix="/dashboard", tags=["settlements_global"])

_PER_PAGE = 20


@router.get("/rozliczenia", response_class=HTMLResponse)
async def settlements_global_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Globalny widok rozliczen -- cross-project lista dla zalogowanego uzytkownika."""
    user = await get_session_user(request, db)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=302)

    user_id = user.id
    allowed_set = await get_permitted_project_ids(db, user_id, "read")
    allowed_project_ids = list(allowed_set)

    # Parsowanie filtrow z query params
    params = request.query_params

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
                "visible_projects": {},
                "visible_ticket_counts": {},
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
        # Rozliczenie z projektem bez rozliczenia:read nie jest listowane (MON-240)
        fully_permitted_settlements_clause(allowed_set),
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

    # Defensywnie: nazwy projektow i liczba ticketow tylko z projektow dozwolonych
    visible_projects = {s.id: [p for p in s.projects if p.id in allowed_set] for s in settlements}
    visible_ticket_counts = {s.id: sum(1 for t in s.tickets if t.project_id in allowed_set) for s in settlements}

    return templates.TemplateResponse(
        request,
        "dashboard/settlements_global/list.html",
        {
            "settlements": settlements,
            "visible_projects": visible_projects,
            "visible_ticket_counts": visible_ticket_counts,
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
