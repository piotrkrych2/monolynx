"""Dashboard -- REST API i widok HTML modulu planu pracy."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.dashboard.helpers import _get_user_id, templates
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.schemas.work_plan import WorkPlanEntryCreate, WorkPlanEntryResponse, WorkPlanEntryUpdate
from monolynx.services import work_plan as work_plan_service
from monolynx.services.work_plan import _UNSET

router = APIRouter(prefix="/dashboard/plan", tags=["plan"])

_TICKET_SEARCH_LIMIT = 20


def _safe_parse_date(s: str | None) -> date | None:
    """Parsuje string YYYY-MM-DD. Zwraca None przy bledzie lub braku wartosci."""
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


async def _get_user_projects(
    db: AsyncSession,
    user_id: UUID,
    is_superuser: bool,
) -> list[Project]:
    """Zwraca obiekty Project dostepne dla uzytkownika (z name+slug do dropdownu).

    Superuser: wszystkie aktywne projekty.
    Normalny user: projekty, w ktorych jest czlonkiem.
    """
    if is_superuser:
        result = await db.execute(select(Project).where(Project.is_active.is_(True)).order_by(Project.name))
        return list(result.scalars().all())

    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == user_id,
            Project.is_active.is_(True),
        )
        .order_by(Project.name)
    )
    return list(result.scalars().all())


@router.post("/entries", response_model=WorkPlanEntryResponse)
async def create_entry(
    request: Request,
    body: WorkPlanEntryCreate,
    db: AsyncSession = Depends(get_db),
) -> WorkPlanEntryResponse:
    """Tworzy nowy wpis planu pracy dla zalogowanego uzytkownika."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    result = await work_plan_service.schedule(db, user_id, body.ticket_id, body.scheduled_date, body.position, body.notes)
    if isinstance(result, str):
        raise HTTPException(status_code=400, detail=result)
    return WorkPlanEntryResponse.from_entry(result)


@router.patch("/entries/{entry_id}", response_model=WorkPlanEntryResponse)
async def update_entry(
    entry_id: UUID,
    request: Request,
    body: WorkPlanEntryUpdate,
    db: AsyncSession = Depends(get_db),
) -> WorkPlanEntryResponse:
    """Aktualizuje wpis planu pracy (tylko wlasciciel)."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    # Odrozniamy "pole nie podane" od "podane jako null" przez model_fields_set
    notes_arg = body.notes if "notes" in body.model_fields_set else _UNSET
    result = await work_plan_service.update(db, user_id, entry_id, body.scheduled_date, body.position, notes_arg)
    if isinstance(result, str):
        code = 403 if "dostep" in result.lower() else 400
        raise HTTPException(status_code=code, detail=result)
    return WorkPlanEntryResponse.from_entry(result)


@router.delete("/entries/{entry_id}")
async def delete_entry(
    entry_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """Usuwa wpis planu pracy (tylko wlasciciel)."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    err = await work_plan_service.unschedule(db, user_id, entry_id)
    if err:
        code = 403 if "dostep" in err.lower() else 404
        raise HTTPException(status_code=code, detail=err)
    return {"status": "ok"}


@router.get("/api/data", response_model=list[WorkPlanEntryResponse])
async def api_data(
    request: Request,
    start: date,
    end: date,
    project_ids: list[UUID] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
) -> list[WorkPlanEntryResponse]:
    """Zwraca wpisy planu pracy dla zalogowanego uzytkownika w podanym zakresie (max 90 dni)."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    if end < start:
        raise HTTPException(status_code=400, detail="end < start")
    if (end - start).days > 90:
        raise HTTPException(status_code=400, detail="Zakres przekracza 90 dni")

    entries = await work_plan_service.list_for_user_range(db, user_id, start, end, project_ids if project_ids else None)
    return [WorkPlanEntryResponse.from_entry(e) for e in entries]


# ---------------------------------------------------------------------------
# Widok HTML + autocomplete ticketow
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, response_model=None)
async def plan_view(
    request: Request,
    view: str = "gantt",
    start: str | None = None,
    end: str | None = None,
    project_ids: list[str] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Glowny widok planu pracy (gantt lub calendar)."""
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse("/auth/login", status_code=303)

    if view not in ("gantt", "calendar"):
        view = "gantt"

    # Domyslny zakres dat: biezacy miesiac
    today = date.today()
    default_start = today.replace(day=1)
    next_month = (default_start + timedelta(days=32)).replace(day=1)
    default_end = next_month - timedelta(days=1)

    parsed_start = _safe_parse_date(start) or default_start
    parsed_end = _safe_parse_date(end) or default_end
    if parsed_end < parsed_start or (parsed_end - parsed_start).days > 90:
        parsed_start, parsed_end = default_start, default_end

    is_superuser = request.session.get("is_superuser", False)
    user_projects = await _get_user_projects(db, user_id, is_superuser)
    user_project_ids = {p.id for p in user_projects}

    valid_project_uuids: list[UUID] = []
    for pid in project_ids:
        try:
            puuid = UUID(pid)
            if puuid in user_project_ids:
                valid_project_uuids.append(puuid)
        except ValueError:
            continue

    entries = await work_plan_service.list_for_user_range(db, user_id, parsed_start, parsed_end, valid_project_uuids if valid_project_uuids else None)

    entries_data = [WorkPlanEntryResponse.from_entry(e).model_dump(mode="json") for e in entries]

    return templates.TemplateResponse(
        request,
        "dashboard/work_plan/index.html",
        {
            "view": view,
            "start_date": parsed_start.isoformat(),
            "end_date": parsed_end.isoformat(),
            "projects": user_projects,
            "selected_project_ids": [str(p) for p in valid_project_uuids],
            "entries": entries_data,
            "active_top_link": "plan",
        },
    )


@router.get("/api/tickets/search", response_model=None)
async def search_tickets(
    request: Request,
    q: str = "",
    project_ids: list[str] = Query(default=[]),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Autocomplete ticketow. Tylko z projektow, w ktorych user jest czlonkiem. Limit 20."""
    user_id = _get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    is_superuser = request.session.get("is_superuser", False)
    user_projects = await _get_user_projects(db, user_id, is_superuser)
    user_project_ids = {p.id for p in user_projects}

    filter_pids: list[UUID] = []
    for pid in project_ids:
        try:
            puuid = UUID(pid)
            if puuid in user_project_ids:
                filter_pids.append(puuid)
        except ValueError:
            continue

    search_pids = filter_pids if filter_pids else list(user_project_ids)

    if not search_pids:
        return JSONResponse([])

    stmt = (
        select(Ticket)
        .options(selectinload(Ticket.project))
        .where(Ticket.project_id.in_(search_pids))
        .order_by(Ticket.created_at.desc())
        .limit(_TICKET_SEARCH_LIMIT)
    )

    if q:
        stmt = stmt.where(Ticket.title.ilike(f"%{q}%"))

    result = await db.execute(stmt)
    tickets = result.scalars().all()

    return JSONResponse(
        [
            {
                "id": str(t.id),
                "key": t.key,
                "title": t.title,
                "project_slug": t.project.slug,
                "project_name": t.project.name,
            }
            for t in tickets
        ]
    )
