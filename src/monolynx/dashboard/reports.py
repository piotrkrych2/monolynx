"""Dashboard -- globalne raporty pracy (cross-project)."""

from __future__ import annotations

import contextlib
import csv
import io
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import DEFAULT_REPORT_DATE_RANGE_DAYS
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.schemas.time_tracking import TimeTrackingFilter
from monolynx.services.time_tracking import (
    aggregate_hours_per_project,
    aggregate_hours_per_sprint,
    aggregate_hours_per_user,
    get_work_report,
)

from .helpers import _get_user_id, templates

router = APIRouter(prefix="/dashboard", tags=["reports"])


async def _get_user_project_ids(
    user_id: uuid.UUID,
    db: AsyncSession,
    is_superuser: bool,
) -> list[uuid.UUID]:
    """Zwraca liste project_ids dostepnych dla uzytkownika.

    Superuser: wszystkie aktywne projekty.
    Normalny user: projekty, w ktorych jest czlonkiem.
    """
    if is_superuser:
        result = await db.execute(select(Project.id).where(Project.is_active.is_(True)))
        return list(result.scalars().all())

    result = await db.execute(
        select(ProjectMember.project_id)
        .join(Project, Project.id == ProjectMember.project_id)
        .where(
            ProjectMember.user_id == user_id,
            Project.is_active.is_(True),
        )
    )
    return list(result.scalars().all())


def _parse_global_report_filters(
    request: Request,
    allowed_project_ids: list[uuid.UUID],
) -> TimeTrackingFilter:
    """Parsuje query params raportu globalnego do TimeTrackingFilter.

    Obsluguje multi-select: project_id, user_id, sprint_id (wielokrotne).
    Waliduje project_ids -- odrzuca ID spoza allowed_project_ids.
    Domyslny zakres dat: ostatnie 30 dni.
    """
    params = request.query_params
    allowed_set = set(allowed_project_ids)

    # Multi-select project_ids
    filter_project_ids: list[uuid.UUID] | None = None
    raw_project_ids = params.getlist("project_id")
    if raw_project_ids:
        parsed: list[uuid.UUID] = []
        for raw in raw_project_ids:
            with contextlib.suppress(ValueError):
                pid = uuid.UUID(raw)
                if pid in allowed_set:
                    parsed.append(pid)
        if parsed:
            filter_project_ids = parsed

    # Multi-select user_ids
    filter_user_ids: list[uuid.UUID] | None = None
    raw_user_ids = params.getlist("user_id")
    if raw_user_ids:
        parsed_users: list[uuid.UUID] = []
        for raw in raw_user_ids:
            with contextlib.suppress(ValueError):
                parsed_users.append(uuid.UUID(raw))
        if parsed_users:
            filter_user_ids = parsed_users

    # Multi-select sprint_ids
    filter_sprint_ids: list[uuid.UUID] | None = None
    raw_sprint_ids = params.getlist("sprint_id")
    if raw_sprint_ids:
        parsed_sprints: list[uuid.UUID] = []
        for raw in raw_sprint_ids:
            with contextlib.suppress(ValueError):
                parsed_sprints.append(uuid.UUID(raw))
        if parsed_sprints:
            filter_sprint_ids = parsed_sprints

    # Daty
    filter_date_from: date | None = None
    with contextlib.suppress(ValueError):
        if params.get("date_from"):
            filter_date_from = date.fromisoformat(params["date_from"])

    filter_date_to: date | None = None
    with contextlib.suppress(ValueError):
        if params.get("date_to"):
            filter_date_to = date.fromisoformat(params["date_to"])

    # Domyslny zakres dat: ostatnie 30 dni
    if filter_date_from is None and filter_date_to is None and not params.get("date_from") and not params.get("date_to"):
        filter_date_to = date.today()
        filter_date_from = filter_date_to - timedelta(days=DEFAULT_REPORT_DATE_RANGE_DAYS)

    # Filtr AI
    filter_created_via_ai: bool | None = None
    ai_param = params.get("ai")
    if ai_param == "1":
        filter_created_via_ai = True
    elif ai_param == "0":
        filter_created_via_ai = False

    return TimeTrackingFilter(
        project_ids=filter_project_ids,
        user_ids=filter_user_ids,
        sprint_ids=filter_sprint_ids,
        date_from=filter_date_from,
        date_to=filter_date_to,
        created_via_ai=filter_created_via_ai,
    )


async def _resolve_report_context(
    request: Request,
    db: AsyncSession,
) -> tuple[uuid.UUID, list[uuid.UUID], TimeTrackingFilter, list[uuid.UUID]] | None:
    """Wspolny blok: auth + allowed projects + parse filters + effective project_ids.

    Zwraca None jesli user niezalogowany, w przeciwnym razie tuple:
    (user_id, allowed_project_ids, filters_with_effective_projects, effective_project_ids)
    """
    user_id = _get_user_id(request)
    if user_id is None:
        return None

    is_superuser = request.session.get("is_superuser", False)
    allowed_project_ids = await _get_user_project_ids(user_id, db, is_superuser)

    base_filters = _parse_global_report_filters(request, allowed_project_ids)
    effective_project_ids = base_filters.project_ids if base_filters.project_ids else allowed_project_ids

    filters = TimeTrackingFilter(
        project_ids=effective_project_ids,
        user_ids=base_filters.user_ids,
        sprint_ids=base_filters.sprint_ids,
        date_from=base_filters.date_from,
        date_to=base_filters.date_to,
        created_via_ai=base_filters.created_via_ai,
    )

    return user_id, allowed_project_ids, filters, effective_project_ids


@router.get("/reports", response_class=HTMLResponse, response_model=None)
async def global_reports(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Strona globalnych raportow pracy (cross-project)."""
    ctx = await _resolve_report_context(request, db)
    if ctx is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    _user_id, allowed_project_ids, filters, effective_project_ids = ctx

    # Parsuj page i sort
    params = request.query_params
    page = 1
    with contextlib.suppress(ValueError):
        if params.get("page"):
            page = max(1, int(params["page"]))

    sort_by = params.get("sort") if params.get("sort") in ("date", "hours", "user") else None

    filters = TimeTrackingFilter(
        project_ids=filters.project_ids,
        user_ids=filters.user_ids,
        sprint_ids=filters.sprint_ids,
        date_from=filters.date_from,
        date_to=filters.date_to,
        created_via_ai=filters.created_via_ai,
        page=page,
    )

    report = await get_work_report(filters, db, sort_by=sort_by)

    # Pobierz dostepne projekty do dropdownu
    projects_result = await db.execute(select(Project).where(Project.id.in_(allowed_project_ids), Project.is_active.is_(True)).order_by(Project.name))
    projects = list(projects_result.scalars().all())

    # Pobierz members z wybranych projektow do dropdownu
    members_result = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id.in_(effective_project_ids)).options(selectinload(ProjectMember.user))
    )
    members_raw = list(members_result.scalars().all())
    # Deduplikacja -- ten sam user moze byc w kilku projektach
    seen_user_ids: set[uuid.UUID] = set()
    members: list[ProjectMember] = []
    for m in members_raw:
        if m.user_id not in seen_user_ids:
            seen_user_ids.add(m.user_id)
            members.append(m)

    # Pobierz sprinty z wybranych projektow do dropdownu
    sprints_result = await db.execute(select(Sprint).where(Sprint.project_id.in_(effective_project_ids)).order_by(Sprint.start_date.desc()))
    sprints = list(sprints_result.scalars().all())

    # Lookup dicts
    user_lookup = {str(m.user_id): m.user.email.split("@")[0] for m in members}
    sprint_lookup = {str(s.id): s.name for s in sprints}
    project_lookup = {str(p.id): p.name for p in projects}
    project_slug_lookup = {str(p.id): p.slug for p in projects}
    project_code_lookup = {str(p.id): p.code for p in projects}

    # Ticket key lookup (PIM-1 style)
    ticket_ids = list({e.ticket_id for e in report.entries})
    ticket_key_lookup: dict[str, str] = {}
    if ticket_ids:
        tickets_result = await db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids)))
        for t in tickets_result.scalars().all():
            code = project_code_lookup.get(str(t.project_id), "?")
            ticket_key_lookup[str(t.id)] = f"{code}-{t.number}"

    # Stats
    avg_hours = report.total_hours / report.entry_count if report.entry_count > 0 else 0
    unique_users = len(report.hours_by_user)
    ai_entry_count = sum(1 for e in report.entries if e.created_via_ai)
    ai_hours = round(sum(e.duration_minutes for e in report.entries if e.created_via_ai) / 60, 1)

    total_pages = report.total_pages

    # selected_project_ids: tylko te jawnie wybrane przez usera (nie effective/all)
    raw_project_ids = request.query_params.getlist("project_id")
    selected_project_ids = [pid for pid in raw_project_ids if pid]

    return templates.TemplateResponse(
        request,
        "dashboard/reports/index.html",
        {
            "projects": projects,
            "selected_project_ids": selected_project_ids,
            "members": members,
            "sprints": sprints,
            "report": report,
            "user_lookup": user_lookup,
            "sprint_lookup": sprint_lookup,
            "project_lookup": project_lookup,
            "project_slug_lookup": project_slug_lookup,
            "ticket_key_lookup": ticket_key_lookup,
            "filters": filters,
            "avg_hours": avg_hours,
            "unique_users": unique_users,
            "page": page,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "sort": sort_by or "date",
            "ai_entry_count": ai_entry_count,
            "ai_hours": ai_hours,
        },
    )


@router.get("/reports/data/sprint-hours", response_model=None)
async def global_chart_sprint_hours(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Dane wykresu: godziny na sprint (cross-project)."""
    ctx = await _resolve_report_context(request, db)
    if ctx is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    _user_id, _allowed, filters, _effective = ctx

    hours_by_sprint = await aggregate_hours_per_sprint(filters, db)

    # Fetch sprint names
    sprint_ids = list(hours_by_sprint.keys())
    sprint_lookup: dict[str, str] = {}
    if sprint_ids:
        result = await db.execute(select(Sprint).where(Sprint.id.in_(sprint_ids)))
        for s in result.scalars().all():
            sprint_lookup[str(s.id)] = s.name

    data = [
        {
            "sprint_id": str(sid),
            "sprint_name": sprint_lookup.get(str(sid), "?"),
            "total_hours": hours,
        }
        for sid, hours in hours_by_sprint.items()
    ]

    return JSONResponse(data)


@router.get("/reports/data/user-hours", response_model=None)
async def global_chart_user_hours(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Dane wykresu: godziny na uzytkownika (cross-project)."""
    ctx = await _resolve_report_context(request, db)
    if ctx is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    _user_id, _allowed, filters, _effective = ctx

    hours_by_user = await aggregate_hours_per_user(filters, db)

    # Fetch user names
    user_ids_list = list(hours_by_user.keys())
    user_lookup: dict[str, str] = {}
    if user_ids_list:
        result = await db.execute(select(User).where(User.id.in_(user_ids_list)))
        for u in result.scalars().all():
            user_lookup[str(u.id)] = u.email.split("@")[0]

    total = sum(hours_by_user.values())
    data = [
        {
            "user_id": str(uid),
            "user_name": user_lookup.get(str(uid), "?"),
            "total_hours": hours,
            "percentage": round(hours / total * 100, 1) if total > 0 else 0,
        }
        for uid, hours in hours_by_user.items()
    ]

    return JSONResponse(data)


@router.get("/reports/data/project-hours", response_model=None)
async def global_chart_project_hours(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Dane wykresu: godziny na projekt (cross-project)."""
    ctx = await _resolve_report_context(request, db)
    if ctx is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    _user_id, _allowed, filters, _effective = ctx

    hours_by_project = await aggregate_hours_per_project(filters, db)

    # Fetch project names
    project_ids_list = list(hours_by_project.keys())
    project_lookup: dict[str, str] = {}
    if project_ids_list:
        result = await db.execute(select(Project).where(Project.id.in_(project_ids_list)))
        for p in result.scalars().all():
            project_lookup[str(p.id)] = p.name

    data = [
        {
            "project_id": str(pid),
            "project_name": project_lookup.get(str(pid), "?"),
            "total_hours": hours,
        }
        for pid, hours in hours_by_project.items()
    ]

    return JSONResponse(data)


@router.get("/reports/export", response_model=None)
async def global_export_report(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Eksport raportu globalnego do CSV lub PDF."""
    ctx = await _resolve_report_context(request, db)
    if ctx is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    _user_id, _allowed, filters, effective_project_ids = ctx

    export_format = request.query_params.get("format", "csv")

    # Fetch ALL entries (no pagination)
    report = await get_work_report(filters, db, paginate=False)

    # Build lookups -- members z wybranych projektow
    members_result = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id.in_(effective_project_ids)).options(selectinload(ProjectMember.user))
    )
    members_raw = list(members_result.scalars().all())
    user_lookup: dict[str, str] = {}
    for m in members_raw:
        if str(m.user_id) not in user_lookup:
            user_lookup[str(m.user_id)] = m.user.email.split("@")[0]

    sprints_result = await db.execute(select(Sprint).where(Sprint.project_id.in_(effective_project_ids)))
    sprints = list(sprints_result.scalars().all())
    sprint_lookup = {str(s.id): s.name for s in sprints}

    # Project lookup
    projects_result = await db.execute(select(Project).where(Project.id.in_(effective_project_ids)))
    projects = list(projects_result.scalars().all())
    project_lookup = {str(p.id): p.name for p in projects}
    project_lookup_code = {str(p.id): p.code for p in projects}

    # Ticket lookups (title + key)
    ticket_ids = list({e.ticket_id for e in report.entries})
    ticket_lookup: dict[str, str] = {}
    ticket_key_lookup: dict[str, str] = {}
    if ticket_ids:
        tickets_result = await db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids)))
        for t in tickets_result.scalars().all():
            ticket_lookup[str(t.id)] = t.title
            code = project_lookup_code.get(str(t.project_id), "?")
            ticket_key_lookup[str(t.id)] = f"{code}-{t.number}"

    today_str = date.today().isoformat()

    if export_format == "pdf":
        # Dane do wykresow w PDF
        sprint_chart_data = [{"sprint_name": sprint_lookup.get(sid, "?"), "total_hours": hours} for sid, hours in report.hours_by_sprint.items()]
        user_chart_data = []
        total_user_hours = sum(report.hours_by_user.values())
        for uid, hours in report.hours_by_user.items():
            user_chart_data.append(
                {
                    "user_name": user_lookup.get(uid, "?"),
                    "total_hours": hours,
                    "percentage": round(hours / total_user_hours * 100, 1) if total_user_hours > 0 else 0,
                }
            )

        project_chart_data = [{"project_name": project_lookup.get(pid, "?"), "total_hours": hours} for pid, hours in report.hours_by_project.items()]

        avg_hours = report.total_hours / report.entry_count if report.entry_count > 0 else 0

        project_names = [project_lookup.get(str(pid), "?") for pid in effective_project_ids]

        rendered_html = templates.get_template("dashboard/reports/reports_pdf.html").render(
            report=report,
            user_lookup=user_lookup,
            sprint_lookup=sprint_lookup,
            project_lookup=project_lookup,
            ticket_lookup=ticket_lookup,
            ticket_key_lookup=ticket_key_lookup,
            project_names=project_names,
            filters=filters,
            avg_hours=avg_hours,
            unique_users=len(report.hours_by_user),
            sprint_chart_data=sprint_chart_data,
            user_chart_data=user_chart_data,
            project_chart_data=project_chart_data,
            generated_at=today_str,
        )

        from weasyprint import HTML

        pdf_bytes = HTML(string=rendered_html).write_pdf()

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="global-work-report-{today_str}.pdf"'},
        )

    # CSV export (default)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Ticket", "Ticket Title", "Project", "User Name", "Hours", "Date", "Description", "AI"])

    for entry in report.entries:
        writer.writerow(
            [
                ticket_key_lookup.get(str(entry.ticket_id), str(entry.ticket_id)),
                ticket_lookup.get(str(entry.ticket_id), "?"),
                project_lookup.get(str(entry.project_id), "?"),
                user_lookup.get(str(entry.user_id), "?"),
                round(entry.duration_minutes / 60, 2),
                str(entry.date_logged),
                entry.description or "",
                "AI" if entry.created_via_ai else "",
            ]
        )

    csv_content = output.getvalue()
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="global-work-report-{today_str}.csv"'},
    )


# ---------------------------------------------------------------------------
# AJAX search endpoints (Tom Select async loading)
# ---------------------------------------------------------------------------

_SEARCH_LIMIT = 20


@router.get("/reports/search/projects", response_model=None)
async def search_projects(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Wyszukiwanie projektow dla Tom Select (multi-select)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    is_superuser = request.session.get("is_superuser", False)
    allowed_project_ids = await _get_user_project_ids(user_id, db, is_superuser)

    if not allowed_project_ids:
        return JSONResponse([])

    q = (request.query_params.get("q", "") or "").strip()[:100]

    stmt = (
        select(Project)
        .where(
            Project.id.in_(allowed_project_ids),
            Project.is_active.is_(True),
        )
        .order_by(Project.name)
        .limit(_SEARCH_LIMIT)
    )
    if q:
        stmt = stmt.where(Project.name.ilike(f"%{q}%"))

    result = await db.execute(stmt)
    projects = result.scalars().all()

    return JSONResponse([{"value": str(p.id), "text": p.name} for p in projects])


@router.get("/reports/search/users", response_model=None)
async def search_users(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Wyszukiwanie uzytkownikow dla Tom Select (multi-select)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    is_superuser = request.session.get("is_superuser", False)
    allowed_project_ids = await _get_user_project_ids(user_id, db, is_superuser)

    if not allowed_project_ids:
        return JSONResponse([])

    q = (request.query_params.get("q", "") or "").strip()[:100]

    # Okresl scope projektow
    raw_project_ids = request.query_params.getlist("project_id")
    allowed_set = set(allowed_project_ids)
    scope_project_ids: list[uuid.UUID] = []
    if raw_project_ids:
        for raw in raw_project_ids:
            with contextlib.suppress(ValueError):
                pid = uuid.UUID(raw)
                if pid in allowed_set:
                    scope_project_ids.append(pid)
    if not scope_project_ids:
        scope_project_ids = allowed_project_ids

    stmt = (
        select(distinct(User.id), User.email)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id.in_(scope_project_ids))
        .order_by(User.email)
        .limit(_SEARCH_LIMIT)
    )
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q}%"))

    result = await db.execute(stmt)
    rows = result.all()

    return JSONResponse([{"value": str(row[0]), "text": row[1].split("@")[0]} for row in rows])


@router.get("/reports/search/sprints", response_model=None)
async def search_sprints(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Wyszukiwanie sprintow dla Tom Select (multi-select)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    is_superuser = request.session.get("is_superuser", False)
    allowed_project_ids = await _get_user_project_ids(user_id, db, is_superuser)

    if not allowed_project_ids:
        return JSONResponse([])

    q = (request.query_params.get("q", "") or "").strip()[:100]

    # Okresl scope projektow
    raw_project_ids = request.query_params.getlist("project_id")
    allowed_set = set(allowed_project_ids)
    scope_project_ids: list[uuid.UUID] = []
    if raw_project_ids:
        for raw in raw_project_ids:
            with contextlib.suppress(ValueError):
                pid = uuid.UUID(raw)
                if pid in allowed_set:
                    scope_project_ids.append(pid)
    if not scope_project_ids:
        scope_project_ids = allowed_project_ids

    stmt = (
        select(Sprint.id, Sprint.name, Project.name.label("project_name"))
        .join(Project, Project.id == Sprint.project_id)
        .where(Sprint.project_id.in_(scope_project_ids))
        .order_by(Sprint.start_date.desc())
        .limit(_SEARCH_LIMIT)
    )
    if q:
        stmt = stmt.where(Sprint.name.ilike(f"%{q}%"))

    result = await db.execute(stmt)
    rows = result.all()

    return JSONResponse([{"value": str(row[0]), "text": f"{row[1]} ({row[2]})"} for row in rows])
