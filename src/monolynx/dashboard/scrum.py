"""Dashboard -- modul Scrum (backlog, tablica, tickety, sprinty)."""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import (
    BOARD_STATUSES,
    PRIORITIES,
    PRIORITY_LABELS,
    SPRINT_STATUSES,
    STATUS_LABELS,
    TICKET_STATUSES,
)
from monolynx.database import get_db
from monolynx.models.event import Event
from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.time_tracking_entry import TimeTrackingEntry
from monolynx.services.sprint import complete_sprint, start_sprint
from monolynx.services.ticket_numbering import get_next_ticket_number
from monolynx.services.time_tracking import (
    add_time_entry,
    delete_time_entry,
    get_ticket_total_hours,
)
from monolynx.services.wiki import render_markdown_html

from .helpers import _get_user_id, flash, render_project_page, templates

router = APIRouter(prefix="/dashboard", tags=["scrum"])


async def _get_project(slug: str, db: AsyncSession) -> Project | None:
    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    return result.scalar_one_or_none()


async def _get_project_members(project_id: uuid.UUID, db: AsyncSession) -> list[ProjectMember]:
    result = await db.execute(select(ProjectMember).options(selectinload(ProjectMember.user)).where(ProjectMember.project_id == project_id))
    return list(result.scalars().all())


# --- Backlog ---


@router.get("/{slug}/scrum/backlog", response_class=HTMLResponse, response_model=None)
async def backlog(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    # Read filter params
    f_status = request.query_params.get("status", "")
    f_priority = request.query_params.get("priority", "")
    f_assignee_id = request.query_params.get("assignee_id", "")
    f_sprint_id = request.query_params.get("sprint_id", "")
    f_search = request.query_params.get("search", "").strip()
    show_completed_sprints = request.query_params.get("show_completed_sprints", "") == "1"

    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except (ValueError, TypeError):
        page = 1
    per_page = 20

    # Build filter conditions
    conditions: list[Any] = [Ticket.project_id == project.id]
    if f_status and f_status in TICKET_STATUSES:
        conditions.append(Ticket.status == f_status)
    if f_priority and f_priority in PRIORITIES:
        conditions.append(Ticket.priority == f_priority)
    if f_assignee_id:
        with contextlib.suppress(ValueError):
            conditions.append(Ticket.assignee_id == uuid.UUID(f_assignee_id))
    if f_sprint_id:
        with contextlib.suppress(ValueError):
            conditions.append(Ticket.sprint_id == uuid.UUID(f_sprint_id))
    if f_search:
        conditions.append(Ticket.title.ilike(f"%{f_search}%"))

    # Hide tickets from completed sprints by default
    sprint_join_filter = (Ticket.sprint_id.is_(None)) | (Sprint.status != "completed")

    # Count total (after filters, before pagination)
    count_q = select(func.count(Ticket.id)).where(*conditions)
    if not show_completed_sprints:
        count_q = count_q.outerjoin(Sprint, Ticket.sprint_id == Sprint.id).where(sprint_join_filter)
    total_count = (await db.execute(count_q)).scalar() or 0

    # SP total across all filtered tickets
    sp_q = select(func.coalesce(func.sum(Ticket.story_points), 0)).where(*conditions)
    if not show_completed_sprints:
        sp_q = sp_q.outerjoin(Sprint, Ticket.sprint_id == Sprint.id).where(sprint_join_filter)
    sp_total = (await db.execute(sp_q)).scalar() or 0

    # Pagination
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)

    # Main query with eager loads
    query = select(Ticket).options(selectinload(Ticket.assignee), selectinload(Ticket.sprint)).where(*conditions)
    if not show_completed_sprints:
        query = query.outerjoin(Sprint, Ticket.sprint_id == Sprint.id).where(sprint_join_filter)

    query = query.order_by(Ticket.order, Ticket.created_at.desc())
    query = query.limit(per_page).offset((page - 1) * per_page)
    result = await db.execute(query)
    tickets = result.scalars().all()

    members = await _get_project_members(project.id, db)

    result = await db.execute(select(Sprint).where(Sprint.project_id == project.id, Sprint.status != "completed").order_by(Sprint.created_at.desc()))
    sprints = result.scalars().all()

    return await render_project_page(
        request,
        "dashboard/scrum/backlog.html",
        {
            "project": project,
            "tickets": tickets,
            "sprints": sprints,
            "members": members,
            "sp_total": sp_total,
            "active_module": "scrum",
            "status_labels": STATUS_LABELS,
            "statuses": TICKET_STATUSES,
            "priorities": PRIORITIES,
            "priority_labels": PRIORITY_LABELS,
            "filters": {
                "status": f_status,
                "priority": f_priority,
                "assignee_id": f_assignee_id,
                "sprint_id": f_sprint_id,
                "search": f_search,
            },
            "show_completed_sprints": show_completed_sprints,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
        db=db,
    )


# --- Board ---


@router.get("/{slug}/scrum/board", response_class=HTMLResponse, response_model=None)
async def board(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    # Aktywny sprint
    result = await db.execute(select(Sprint).where(Sprint.project_id == project.id, Sprint.status == "active"))
    active_sprint = result.scalar_one_or_none()

    columns: dict[str, list[Ticket]] = {s: [] for s in BOARD_STATUSES}
    sp_per_column: dict[str, int] = {s: 0 for s in BOARD_STATUSES}
    sp_total = 0
    sp_done = 0

    if active_sprint:
        ticket_result = await db.execute(
            select(Ticket).options(selectinload(Ticket.assignee)).where(Ticket.sprint_id == active_sprint.id).order_by(Ticket.order)
        )
        for ticket in ticket_result.scalars().all():
            if ticket.status in columns:
                columns[ticket.status].append(ticket)
                sp_per_column[ticket.status] += ticket.story_points or 0

        sp_total = sum(sp_per_column.values())
        sp_done = sp_per_column.get("done", 0)

    return await render_project_page(
        request,
        "dashboard/scrum/board.html",
        {
            "project": project,
            "sprint": active_sprint,
            "columns": columns,
            "sp_per_column": sp_per_column,
            "sp_total": sp_total,
            "sp_done": sp_done,
            "active_module": "scrum",
            "status_labels": STATUS_LABELS,
        },
        db=db,
    )


# --- Tickety CRUD ---


@router.get(
    "/{slug}/scrum/tickets/create",
    response_class=HTMLResponse,
    response_model=None,
)
async def ticket_create_form(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    members = await _get_project_members(project.id, db)

    result = await db.execute(select(Sprint).where(Sprint.project_id == project.id, Sprint.status != "completed").order_by(Sprint.created_at.desc()))
    sprints = result.scalars().all()

    return await render_project_page(
        request,
        "dashboard/scrum/ticket_form.html",
        {
            "project": project,
            "ticket": None,
            "members": members,
            "sprints": sprints,
            "priorities": PRIORITIES,
            "statuses": TICKET_STATUSES,
            "error": None,
            "active_module": "scrum",
        },
        db=db,
    )


@router.post(
    "/{slug}/scrum/tickets/create",
    response_class=HTMLResponse,
    response_model=None,
)
async def ticket_create(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    form = await request.form()
    title = str(form.get("title", "")).strip()
    description = str(form.get("description", "")).strip() or None
    priority = str(form.get("priority", "medium"))
    story_points_raw = str(form.get("story_points", "")).strip()
    sprint_id_raw = str(form.get("sprint_id", "")).strip()
    assignee_id_raw = str(form.get("assignee_id", "")).strip()

    if not title:
        members = await _get_project_members(project.id, db)
        result = await db.execute(
            select(Sprint).where(
                Sprint.project_id == project.id,
                Sprint.status != "completed",
            )
        )
        sprints = result.scalars().all()
        return templates.TemplateResponse(
            request,
            "dashboard/scrum/ticket_form.html",
            {
                "project": project,
                "ticket": None,
                "members": members,
                "sprints": sprints,
                "priorities": PRIORITIES,
                "statuses": TICKET_STATUSES,
                "error": "Tytul jest wymagany",
                "active_module": "scrum",
            },
        )

    try:
        story_points = int(story_points_raw) if story_points_raw else None
    except ValueError:
        story_points = None
    try:
        sprint_id = uuid.UUID(sprint_id_raw) if sprint_id_raw else None
    except ValueError:
        sprint_id = None
    try:
        assignee_id = uuid.UUID(assignee_id_raw) if assignee_id_raw else None
    except ValueError:
        assignee_id = None

    if priority not in PRIORITIES:
        priority = "medium"

    next_number = await get_next_ticket_number(project.id, db)

    ticket = Ticket(
        project_id=project.id,
        number=next_number,
        title=title,
        description=description,
        priority=priority,
        story_points=story_points,
        sprint_id=sprint_id,
        assignee_id=assignee_id,
        status="backlog" if sprint_id is None else "todo",
    )
    db.add(ticket)
    await db.commit()

    flash(request, "Ticket zostal utworzony")
    return RedirectResponse(url=f"/dashboard/{slug}/scrum/backlog", status_code=303)


@router.post(
    "/{slug}/scrum/tickets/create-from-issue/{issue_id}",
    response_class=HTMLResponse,
    response_model=None,
)
async def ticket_create_from_issue(
    request: Request,
    slug: str,
    issue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    # Pobierz issue i sprawdz czy nalezy do projektu
    result = await db.execute(select(Issue).options(selectinload(Issue.tickets)).where(Issue.id == issue_id, Issue.project_id == project.id))
    issue = result.scalar_one_or_none()
    if issue is None:
        return HTMLResponse("Issue not found", status_code=404)

    # Sprawdz czy issue juz ma powiazany ticket
    if issue.tickets:
        existing_ticket = issue.tickets[0]
        flash(request, "Ticket juz istnieje dla tego bledu", "warning")
        return RedirectResponse(
            url=f"/dashboard/{slug}/scrum/tickets/{existing_ticket.id}",
            status_code=303,
        )

    # Pobierz ostatni event dla tracebacku
    event_result = await db.execute(select(Event).where(Event.issue_id == issue.id).order_by(Event.timestamp.desc()).limit(1))
    last_event = event_result.scalar_one_or_none()

    # Wyciagnij typ wyjatku z tytulu issue (format: "ExceptionType: message")
    issue_title_parts = issue.title.split(": ", 1)
    exception_type = issue_title_parts[0] if issue_title_parts else issue.title

    # Auto-generuj tytul ticketu
    ticket_title = f"[500ki] {issue.title}"
    if len(ticket_title) > 512:
        ticket_title = ticket_title[:509] + "..."

    # Auto-generuj opis w Markdown
    traceback_text = ""
    request_url = "—"
    request_method = "—"
    environment_name = "—"

    if last_event is not None:
        exc_data = last_event.exception or {}
        if isinstance(exc_data, dict):
            stacktrace = exc_data.get("stacktrace") or {}
            frames = stacktrace.get("frames", []) if isinstance(stacktrace, dict) else []
            if frames:
                lines = []
                for frame in frames:
                    if isinstance(frame, dict):
                        filename = frame.get("filename", "")
                        function = frame.get("function", "?")
                        lineno = frame.get("lineno")
                        lineno_str = f":{lineno}" if lineno is not None else ""
                        lines.append(f'  File "{filename}"{lineno_str}, in {function}')
                        ctx = frame.get("context_line")
                        if ctx:
                            lines.append(f"    {ctx.strip()}")
                traceback_text = "\n".join(lines)
            elif "traceback" in exc_data:
                traceback_text = str(exc_data["traceback"])
            else:
                exc_type = exc_data.get("type", "")
                exc_value = exc_data.get("value", "")
                traceback_text = f"{exc_type}: {exc_value}" if exc_type else str(exc_data)

        request_data = last_event.request_data or {}
        if isinstance(request_data, dict):
            request_url = request_data.get("url") or "—"
            request_method = request_data.get("method") or "—"

        env_data = last_event.environment or {}
        if isinstance(env_data, dict):
            environment_name = env_data.get("environment") or env_data.get("hostname") or "—"

    fingerprint_short = issue.fingerprint[:8] if issue.fingerprint else "?"
    first_seen_str = issue.first_seen.strftime("%Y-%m-%d %H:%M") if issue.first_seen else "—"
    last_seen_str = issue.last_seen.strftime("%Y-%m-%d %H:%M") if issue.last_seen else "—"

    description = (
        f"## Powiazany blad 500ki\n\n"
        f"**Issue:** [#{fingerprint_short}](/dashboard/{slug}/500ki/issues/{issue.id})"
        f" — {issue.title}\n"
        f"**Typ wyjatku:** `{exception_type}`\n"
        f"**Liczba wystapien:** {issue.event_count}\n"
        f"**Pierwsze wystapienie:** {first_seen_str}\n"
        f"**Ostatnie wystapienie:** {last_seen_str}\n\n"
        f"## Traceback\n\n"
        f"```\n{traceback_text}\n```\n\n"
        f"## Srodowisko\n\n"
        f"- **URL zadania:** {request_url}\n"
        f"- **Metoda:** {request_method}\n"
        f"- **Srodowisko:** {environment_name}\n"
    )

    next_number = await get_next_ticket_number(project.id, db)

    ticket = Ticket(
        project_id=project.id,
        number=next_number,
        title=ticket_title,
        description=description,
        priority="medium",
        status="backlog",
        issue_id=issue.id,
    )
    db.add(ticket)
    await db.commit()

    flash(request, "Ticket zostal utworzony na podstawie bledu 500ki")
    return RedirectResponse(url=f"/dashboard/{slug}/scrum/tickets/{ticket.id}", status_code=303)


@router.get(
    "/{slug}/scrum/tickets/{ticket_id}",
    response_class=HTMLResponse,
    response_model=None,
)
async def ticket_detail(
    request: Request,
    slug: str,
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    result = await db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.sprint),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.time_entries).selectinload(TimeTrackingEntry.user),
            selectinload(Ticket.issue),
        )
        .where(Ticket.id == ticket_id, Ticket.project_id == project.id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return HTMLResponse("Ticket not found", status_code=404)

    total_logged_hours = await get_ticket_total_hours(ticket_id, db)

    rendered_description = render_markdown_html(ticket.description) if ticket.description else ""
    rendered_comments = [{"comment": c, "html": render_markdown_html(c.content)} for c in ticket.comments]

    return await render_project_page(
        request,
        "dashboard/scrum/ticket_detail.html",
        {
            "project": project,
            "ticket": ticket,
            "active_module": "scrum",
            "status_labels": STATUS_LABELS,
            "total_logged_hours": total_logged_hours,
            "time_entries": ticket.time_entries,
            "current_user_id": user_id,
            "rendered_description": rendered_description,
            "rendered_comments": rendered_comments,
        },
        db=db,
    )


@router.post("/{slug}/scrum/tickets/{ticket_id}/comments", response_model=None)
async def ticket_comment_create(
    request: Request,
    slug: str,
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id, Ticket.project_id == project.id))
    if result.scalar_one_or_none() is None:
        return HTMLResponse("Ticket not found", status_code=404)

    form = await request.form()
    content = str(form.get("content", "")).strip()
    if not content:
        flash(request, "Tresc komentarza nie moze byc pusta", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/scrum/tickets/{ticket_id}#comments", status_code=303)

    comment = TicketComment(
        ticket_id=ticket_id,
        user_id=user_id,
        content=content,
    )
    db.add(comment)
    await db.commit()

    flash(request, "Komentarz dodany")
    return RedirectResponse(url=f"/dashboard/{slug}/scrum/tickets/{ticket_id}#comments", status_code=303)


@router.get(
    "/{slug}/scrum/tickets/{ticket_id}/edit",
    response_class=HTMLResponse,
    response_model=None,
)
async def ticket_edit_form(
    request: Request,
    slug: str,
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.assignee), selectinload(Ticket.sprint))
        .where(Ticket.id == ticket_id, Ticket.project_id == project.id)
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return HTMLResponse("Ticket not found", status_code=404)

    members = await _get_project_members(project.id, db)
    result = await db.execute(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status != "completed",
        )
    )
    sprints = result.scalars().all()

    return await render_project_page(
        request,
        "dashboard/scrum/ticket_form.html",
        {
            "project": project,
            "ticket": ticket,
            "members": members,
            "sprints": sprints,
            "priorities": PRIORITIES,
            "statuses": TICKET_STATUSES,
            "error": None,
            "active_module": "scrum",
        },
        db=db,
    )


@router.post(
    "/{slug}/scrum/tickets/{ticket_id}/edit",
    response_class=HTMLResponse,
    response_model=None,
)
async def ticket_edit(
    request: Request,
    slug: str,
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id, Ticket.project_id == project.id))
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return HTMLResponse("Ticket not found", status_code=404)

    form = await request.form()
    title = str(form.get("title", "")).strip()
    if not title:
        edit_url = f"/dashboard/{slug}/scrum/tickets/{ticket_id}/edit"
        return RedirectResponse(url=edit_url, status_code=303)

    ticket.title = title
    ticket.description = str(form.get("description", "")).strip() or None
    priority = str(form.get("priority", "medium"))
    ticket.priority = priority if priority in PRIORITIES else "medium"

    story_points_raw = str(form.get("story_points", "")).strip()
    try:
        ticket.story_points = int(story_points_raw) if story_points_raw else None
    except ValueError:
        ticket.story_points = None

    sprint_id_raw = str(form.get("sprint_id", "")).strip()
    with contextlib.suppress(ValueError):
        ticket.sprint_id = uuid.UUID(sprint_id_raw) if sprint_id_raw else None

    assignee_id_raw = str(form.get("assignee_id", "")).strip()
    with contextlib.suppress(ValueError):
        ticket.assignee_id = uuid.UUID(assignee_id_raw) if assignee_id_raw else None

    status = str(form.get("status", ticket.status))
    if status in TICKET_STATUSES:
        ticket.status = status

    await db.commit()
    flash(request, "Ticket zostal zaktualizowany")
    ticket_url = f"/dashboard/{slug}/scrum/tickets/{ticket_id}"
    return RedirectResponse(url=ticket_url, status_code=303)


@router.post("/{slug}/scrum/tickets/{ticket_id}/delete", response_model=None)
async def ticket_delete(
    request: Request,
    slug: str,
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id, Ticket.project_id == project.id))
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return HTMLResponse("Ticket not found", status_code=404)

    await db.delete(ticket)
    await db.commit()
    flash(request, "Ticket zostal usuniety")
    return RedirectResponse(url=f"/dashboard/{slug}/scrum/backlog", status_code=303)


@router.patch("/{slug}/scrum/tickets/{ticket_id}/status", response_model=None)
async def ticket_status_update(
    request: Request,
    slug: str,
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return HTMLResponse("Unauthorized", status_code=401)

    body = await request.json()
    new_status = body.get("status", "")
    if new_status not in TICKET_STATUSES:
        return HTMLResponse("Invalid status", status_code=422)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id, Ticket.project_id == project.id))
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return HTMLResponse("Ticket not found", status_code=404)

    ticket.status = new_status
    await db.commit()
    return HTMLResponse("OK", status_code=200)


@router.patch("/{slug}/scrum/tickets/{ticket_id}/sprint", response_model=None)
async def ticket_sprint_update(
    request: Request,
    slug: str,
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return HTMLResponse("Unauthorized", status_code=401)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id, Ticket.project_id == project.id))
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return HTMLResponse("Ticket not found", status_code=404)

    body = await request.json()
    sprint_id_raw = body.get("sprint_id")

    if sprint_id_raw:
        try:
            sprint_id = uuid.UUID(sprint_id_raw)
        except ValueError:
            return HTMLResponse("Invalid sprint_id", status_code=422)

        # Validate sprint belongs to same project and is not completed
        sprint_result = await db.execute(
            select(Sprint).where(
                Sprint.id == sprint_id,
                Sprint.project_id == project.id,
                Sprint.status != "completed",
            )
        )
        if sprint_result.scalar_one_or_none() is None:
            return HTMLResponse("Sprint not found", status_code=404)

        ticket.sprint_id = sprint_id
        if ticket.status == "backlog":
            ticket.status = "todo"
    else:
        ticket.sprint_id = None
        if ticket.status == "todo":
            ticket.status = "backlog"

    await db.commit()
    return HTMLResponse("OK", status_code=200)


async def _compute_sprint_stats(sprints: Sequence[Sprint], db: AsyncSession) -> dict[str, dict[str, int]]:
    """Compute ticket count and story points sum per sprint."""
    sprint_stats: dict[str, dict[str, int]] = {}
    for sprint in sprints:
        stats_result = await db.execute(
            select(
                func.count(Ticket.id),
                func.coalesce(func.sum(Ticket.story_points), 0),
            ).where(Ticket.sprint_id == sprint.id)
        )
        row = stats_result.one()
        sprint_stats[str(sprint.id)] = {
            "ticket_count": int(row[0]),
            "sp_sum": int(row[1]),
        }
    return sprint_stats


# --- Time Tracking ---


@router.post("/{slug}/scrum/time-tracking/log", response_model=None)
async def time_tracking_log(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    project = await _get_project(slug, db)
    if project is None:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    ticket_id_raw = body.get("ticket_id", "")
    duration_raw = body.get("duration_minutes", 0)
    date_raw = body.get("date_logged", "")
    description = body.get("description") or None

    try:
        ticket_id = uuid.UUID(str(ticket_id_raw))
    except (ValueError, TypeError):
        return JSONResponse({"error": "Nieprawidlowy ticket_id"}, status_code=400)

    try:
        duration_minutes = int(duration_raw)
        if duration_minutes <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return JSONResponse({"error": "Czas musi byc wiekszy niz 0"}, status_code=400)

    try:
        date_logged = date.fromisoformat(str(date_raw))
    except (ValueError, TypeError):
        return JSONResponse({"error": "Nieprawidlowa data"}, status_code=400)

    # Walidacja: ticket musi nalezec do projektu z URL
    ticket_check = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket_obj = ticket_check.scalar_one_or_none()
    if ticket_obj is None:
        return JSONResponse({"error": "Ticket nie istnieje"}, status_code=404)
    if ticket_obj.project_id != project.id:
        return JSONResponse({"error": "Ticket nie nalezy do tego projektu"}, status_code=400)

    result = await add_time_entry(
        ticket_id=ticket_id,
        user_id=user_id,
        duration_minutes=duration_minutes,
        date_logged=date_logged,
        description=description,
        db=db,
    )

    if isinstance(result, str):
        return JSONResponse({"error": result}, status_code=400)

    return JSONResponse(
        {
            "id": str(result.id),
            "ticket_id": str(result.ticket_id),
            "user_id": str(result.user_id),
            "duration_minutes": result.duration_minutes,
            "date_logged": result.date_logged.isoformat(),
            "description": result.description,
            "status": result.status,
        },
        status_code=201,
    )


@router.delete("/{slug}/scrum/time-tracking/{entry_id}", response_model=None)
async def time_tracking_delete(
    request: Request,
    slug: str,
    entry_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    project = await _get_project(slug, db)
    if project is None:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    # Walidacja: wpis musi nalezec do projektu z URL
    entry_check = await db.execute(select(TimeTrackingEntry).where(TimeTrackingEntry.id == entry_id))
    entry_obj = entry_check.scalar_one_or_none()
    if entry_obj is None:
        return JSONResponse({"error": "Wpis nie istnieje"}, status_code=404)
    if entry_obj.project_id != project.id:
        return JSONResponse({"error": "Wpis nie nalezy do tego projektu"}, status_code=403)

    error = await delete_time_entry(entry_id, user_id, db)
    if error == "Wpis nie istnieje":
        return JSONResponse({"error": error}, status_code=404)
    if error:
        return JSONResponse({"error": error}, status_code=403)

    return JSONResponse({"ok": True}, status_code=200)


# --- Sprinty ---


@router.get("/{slug}/scrum/sprints", response_class=HTMLResponse, response_model=None)
async def sprint_list(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    # Filter & pagination params
    filter_status = request.query_params.get("status", "")
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except (ValueError, TypeError):
        page = 1
    per_page = 10

    # Build filter conditions
    conditions = [Sprint.project_id == project.id]
    if filter_status == "all":
        pass
    elif filter_status and filter_status in SPRINT_STATUSES:
        conditions.append(Sprint.status == filter_status)
    else:
        # Default: hide completed sprints
        conditions.append(Sprint.status != "completed")

    # Count total
    total_count = (await db.execute(select(func.count(Sprint.id)).where(*conditions))).scalar() or 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)

    # Fetch paginated sprints
    result = await db.execute(select(Sprint).where(*conditions).order_by(Sprint.created_at.desc()).limit(per_page).offset((page - 1) * per_page))
    sprints = list(result.scalars().all())

    sprint_stats = await _compute_sprint_stats(sprints, db)

    return await render_project_page(
        request,
        "dashboard/scrum/sprints.html",
        {
            "project": project,
            "sprints": sprints,
            "sprint_stats": sprint_stats,
            "active_module": "scrum",
            "error": None,
            "filter_status": filter_status,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
        db=db,
    )


@router.post(
    "/{slug}/scrum/sprints/create",
    response_class=HTMLResponse,
    response_model=None,
)
async def sprint_create(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    goal = str(form.get("goal", "")).strip() or None
    start_date_raw = str(form.get("start_date", "")).strip()
    end_date_raw = str(form.get("end_date", "")).strip()

    if not name or not start_date_raw:
        result = await db.execute(select(Sprint).where(Sprint.project_id == project.id).order_by(Sprint.created_at.desc()))
        sprints = result.scalars().all()
        sprint_stats = await _compute_sprint_stats(sprints, db)
        return templates.TemplateResponse(
            request,
            "dashboard/scrum/sprints.html",
            {
                "project": project,
                "sprints": sprints,
                "sprint_stats": sprint_stats,
                "active_module": "scrum",
                "error": "Nazwa i data rozpoczecia sa wymagane",
                "filter_status": "",
                "page": 1,
                "total_pages": 1,
                "total_count": len(sprints),
                "has_next": False,
                "has_prev": False,
            },
        )

    sprint = Sprint(
        project_id=project.id,
        name=name,
        goal=goal,
        start_date=date.fromisoformat(start_date_raw),
        end_date=date.fromisoformat(end_date_raw) if end_date_raw else None,
    )
    db.add(sprint)
    await db.commit()

    flash(request, "Sprint zostal utworzony")
    return RedirectResponse(url=f"/dashboard/{slug}/scrum/sprints", status_code=303)


@router.post("/{slug}/scrum/sprints/{sprint_id}/start", response_model=None)
async def sprint_start(
    request: Request,
    slug: str,
    sprint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    error = await start_sprint(sprint_id, project.id, db)
    if error:
        flash(request, error, "error")
        result = await db.execute(select(Sprint).where(Sprint.project_id == project.id).order_by(Sprint.created_at.desc()))
        sprints = result.scalars().all()
        sprint_stats = await _compute_sprint_stats(sprints, db)
        return templates.TemplateResponse(
            request,
            "dashboard/scrum/sprints.html",
            {
                "project": project,
                "sprints": sprints,
                "sprint_stats": sprint_stats,
                "active_module": "scrum",
                "error": error,
                "filter_status": "",
                "page": 1,
                "total_pages": 1,
                "total_count": len(sprints),
                "has_next": False,
                "has_prev": False,
            },
        )

    flash(request, "Sprint zostal rozpoczety")
    return RedirectResponse(url=f"/dashboard/{slug}/scrum/board", status_code=303)


@router.post("/{slug}/scrum/sprints/{sprint_id}/complete", response_model=None)
async def sprint_complete(
    request: Request,
    slug: str,
    sprint_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    error = await complete_sprint(sprint_id, project.id, db)
    if error:
        flash(request, error, "error")
        result = await db.execute(select(Sprint).where(Sprint.project_id == project.id).order_by(Sprint.created_at.desc()))
        sprints = result.scalars().all()
        sprint_stats = await _compute_sprint_stats(sprints, db)
        return templates.TemplateResponse(
            request,
            "dashboard/scrum/sprints.html",
            {
                "project": project,
                "sprints": sprints,
                "sprint_stats": sprint_stats,
                "active_module": "scrum",
                "error": error,
                "filter_status": "",
                "page": 1,
                "total_pages": 1,
                "total_count": len(sprints),
                "has_next": False,
                "has_prev": False,
            },
        )

    flash(request, "Sprint zostal zakonczony")
    return RedirectResponse(url=f"/dashboard/{slug}/scrum/sprints", status_code=303)
