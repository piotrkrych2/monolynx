"""MCP Server -- narzedzia do zarzadzania Monolynx z Claude Code."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from monolynx.config import settings as app_settings
from monolynx.constants import BOARD_STATUSES, PRIORITIES, TICKET_STATUSES
from monolynx.database import async_session_factory
from monolynx.models.event import Event
from monolynx.models.issue import Issue
from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.user import User
from monolynx.services.mcp_auth import verify_mcp_token
from monolynx.services.sprint import complete_sprint as svc_complete_sprint
from monolynx.services.sprint import start_sprint as svc_start_sprint
from monolynx.services.ticket_numbering import get_next_ticket_number
from monolynx.services.time_tracking import add_time_entry

logger = logging.getLogger("monolynx.mcp")


def _build_allowed_hosts() -> list[str]:
    """Zbuduj liste dozwolonych hostow z MCP_ALLOWED_HOSTS (env/.env)."""
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    if app_settings.MCP_ALLOWED_HOSTS:
        for h in app_settings.MCP_ALLOWED_HOSTS.split(","):
            h = h.strip()
            if h:
                hosts.append(h)
    return hosts


mcp = FastMCP(
    "Monolynx",
    instructions=(
        "Serwer MCP platformy Monolynx. "
        "Moduly: Scrum (tickety, sprinty, tablica Kanban), "
        "500ki (error tracking — issues, eventy), "
        "Monitoring (URL health checks, uptime). "
        "Wymaga tokenu API (Bearer) w naglowku Authorization."
    ),
    streamable_http_path="/",
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_build_allowed_hosts(),
    ),
)


async def _auth(ctx: Context[Any, Any]) -> User:
    """Wyciagnij token z naglowka HTTP i zwaliduj uzytkownika."""
    request_ctx = ctx.request_context
    if request_ctx is None:
        raise ValueError("Brak kontekstu HTTP — token wymagany")

    starlette_request = getattr(request_ctx, "request", None)
    if starlette_request is None:
        raise ValueError("Brak kontekstu HTTP request")

    headers = starlette_request.headers
    auth_header = headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise ValueError("Brak tokenu Bearer w naglowku Authorization")

    raw_token = auth_header[7:]
    async with async_session_factory() as db:
        user = await verify_mcp_token(raw_token, db)
    if user is None:
        raise ValueError("Nieprawidlowy lub nieaktywny token API")
    return user


async def _get_auth_header(ctx: Context[Any, Any]) -> str:
    """Pobierz raw token z kontekstu."""
    request_ctx = ctx.request_context
    if request_ctx is None:
        raise ValueError("Brak kontekstu HTTP")
    starlette_request = getattr(request_ctx, "request", None)
    if starlette_request is None:
        raise ValueError("Brak kontekstu HTTP request")
    headers = starlette_request.headers
    auth_header = headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise ValueError("Brak tokenu Bearer")
    return str(auth_header[7:])


async def _get_user_and_project(ctx: Context[Any, Any], project_slug: str) -> tuple[User, Project]:
    """Autoryzuj uzytkownika i sprawdz dostep do projektu."""
    raw_token = await _get_auth_header(ctx)

    async with async_session_factory() as db:
        user = await verify_mcp_token(raw_token, db)
        if user is None:
            raise ValueError("Nieprawidlowy lub nieaktywny token API")

        result = await db.execute(select(Project).where(Project.slug == project_slug, Project.is_active.is_(True)))
        project = result.scalar_one_or_none()
        if project is None:
            raise ValueError(f"Projekt '{project_slug}' nie istnieje")

        result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            raise ValueError(f"Uzytkownik nie jest czlonkiem projektu '{project_slug}'")

    return user, project


# --- Projekty ---


@mcp.tool()
async def list_projects(ctx: Context[Any, Any]) -> list[dict[str, Any]]:
    """Lista projektow, do ktorych uzytkownik jest przypisany."""
    user = await _auth(ctx)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project, ProjectMember.role)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                ProjectMember.user_id == user.id,
                Project.is_active.is_(True),
            )
            .order_by(Project.name)
        )
        rows = result.all()

    return [
        {
            "name": project.name,
            "slug": project.slug,
            "role": role,
            "created_at": project.created_at.isoformat(),
        }
        for project, role in rows
    ]


# --- 500ki (Error Tracking) ---


@mcp.tool()
async def list_issues(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str = "unresolved",
    search: str | None = None,
    page: int = 1,
) -> list[dict[str, Any]]:
    """Lista bledow (issues) w projekcie.

    Filtrowanie po statusie (unresolved/resolved) i tekscie.
    Paginacja po 20. Domyslnie tylko nierozwiazane.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)
    per_page = 20

    async with async_session_factory() as db:
        conditions: list[Any] = [Issue.project_id == project.id]
        if status in ("unresolved", "resolved"):
            conditions.append(Issue.status == status)
        if search:
            conditions.append(Issue.title.ilike(f"%{search}%"))

        total = (await db.execute(select(func.count(Issue.id)).where(*conditions))).scalar() or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        result = await db.execute(select(Issue).where(*conditions).order_by(Issue.last_seen.desc()).limit(per_page).offset((page - 1) * per_page))
        issues = result.scalars().all()

    return [
        {
            "id": str(i.id),
            "title": i.title,
            "culprit": i.culprit,
            "level": i.level,
            "status": i.status,
            "event_count": i.event_count,
            "first_seen": i.first_seen.isoformat(),
            "last_seen": i.last_seen.isoformat(),
        }
        for i in issues
    ] + [{"_meta": {"page": page, "total_pages": total_pages, "total": total}}]


@mcp.tool()
async def get_issue(
    ctx: Context[Any, Any],
    project_slug: str,
    issue_id: str,
) -> dict[str, Any]:
    """Szczegoly bledu z ostatnimi 5 eventami (traceback, request, environment)."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Issue).where(
                Issue.id == uuid.UUID(issue_id),
                Issue.project_id == project.id,
            )
        )
        issue = result.scalar_one_or_none()
        if issue is None:
            raise ValueError("Issue nie istnieje")

        events_result = await db.execute(select(Event).where(Event.issue_id == issue.id).order_by(Event.timestamp.desc()).limit(5))
        events = events_result.scalars().all()

    return {
        "id": str(issue.id),
        "title": issue.title,
        "culprit": issue.culprit,
        "level": issue.level,
        "status": issue.status,
        "event_count": issue.event_count,
        "first_seen": issue.first_seen.isoformat(),
        "last_seen": issue.last_seen.isoformat(),
        "events": [
            {
                "id": str(e.id),
                "timestamp": e.timestamp.isoformat(),
                "exception": e.exception,
                "request_data": e.request_data,
                "environment": e.environment,
            }
            for e in events
        ],
    }


@mcp.tool()
async def update_issue_status(
    ctx: Context[Any, Any],
    project_slug: str,
    issue_id: str,
    status: str,
) -> dict[str, Any]:
    """Zmien status bledu (unresolved/resolved)."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    if status not in ("unresolved", "resolved"):
        raise ValueError("Status musi byc 'unresolved' lub 'resolved'")

    async with async_session_factory() as db:
        result = await db.execute(
            select(Issue).where(
                Issue.id == uuid.UUID(issue_id),
                Issue.project_id == project.id,
            )
        )
        issue = result.scalar_one_or_none()
        if issue is None:
            raise ValueError("Issue nie istnieje")

        issue.status = status
        await db.commit()

    return {
        "id": str(issue.id),
        "title": issue.title,
        "status": status,
        "message": f"Issue '{issue.title}' — status zmieniony na {status}",
    }


# --- Monitoring ---


@mcp.tool()
async def list_monitors(
    ctx: Context[Any, Any],
    project_slug: str,
) -> list[dict[str, Any]]:
    """Lista monitorow URL z aktualnym statusem i uptime 24h."""
    _user, project = await _get_user_and_project(ctx, project_slug)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

    async with async_session_factory() as db:
        result = await db.execute(select(Monitor).where(Monitor.project_id == project.id).order_by(Monitor.created_at))
        monitors = result.scalars().all()

        monitor_data = []
        for m in monitors:
            # Ostatni check
            last_check_result = await db.execute(
                select(MonitorCheck).where(MonitorCheck.monitor_id == m.id).order_by(MonitorCheck.checked_at.desc()).limit(1)
            )
            last_check = last_check_result.scalar_one_or_none()

            # Uptime 24h
            uptime_result = await db.execute(
                select(
                    func.count(MonitorCheck.id),
                    func.count(case((MonitorCheck.is_success.is_(True), 1))),
                ).where(
                    MonitorCheck.monitor_id == m.id,
                    MonitorCheck.checked_at >= twenty_four_hours_ago,
                )
            )
            uptime_row = uptime_result.one()
            total_checks: int = uptime_row[0]
            success_checks: int = uptime_row[1]
            uptime_24h: float | None = None
            if total_checks > 0:
                uptime_24h = round((success_checks / total_checks) * 100, 1)

            monitor_data.append(
                {
                    "id": str(m.id),
                    "name": m.name or m.url,
                    "url": m.url,
                    "interval": f"{m.interval_value} {m.interval_unit}",
                    "is_active": m.is_active,
                    "last_check": {
                        "is_success": last_check.is_success,
                        "status_code": last_check.status_code,
                        "response_time_ms": last_check.response_time_ms,
                        "checked_at": last_check.checked_at.isoformat(),
                    }
                    if last_check
                    else None,
                    "uptime_24h": uptime_24h,
                }
            )

    return monitor_data


@mcp.tool()
async def get_monitor(
    ctx: Context[Any, Any],
    project_slug: str,
    monitor_id: str,
) -> dict[str, Any]:
    """Szczegoly monitora z ostatnimi 20 checkami."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Monitor).where(
                Monitor.id == uuid.UUID(monitor_id),
                Monitor.project_id == project.id,
            )
        )
        monitor = result.scalar_one_or_none()
        if monitor is None:
            raise ValueError("Monitor nie istnieje")

        checks_result = await db.execute(
            select(MonitorCheck).where(MonitorCheck.monitor_id == monitor.id).order_by(MonitorCheck.checked_at.desc()).limit(20)
        )
        checks = checks_result.scalars().all()

    return {
        "id": str(monitor.id),
        "name": monitor.name or monitor.url,
        "url": monitor.url,
        "interval_value": monitor.interval_value,
        "interval_unit": monitor.interval_unit,
        "is_active": monitor.is_active,
        "created_at": monitor.created_at.isoformat(),
        "checks": [
            {
                "is_success": c.is_success,
                "status_code": c.status_code,
                "response_time_ms": c.response_time_ms,
                "error_message": c.error_message,
                "checked_at": c.checked_at.isoformat(),
            }
            for c in checks
        ],
    }


# --- Scrum: Tablica Kanban ---


@mcp.tool()
async def get_board(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Tablica Kanban — tickety aktywnego sprintu pogrupowane po statusie.

    Kolumny: todo, in_progress, in_review, done.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        sprint_result = await db.execute(
            select(Sprint).where(
                Sprint.project_id == project.id,
                Sprint.status == "active",
            )
        )
        sprint = sprint_result.scalar_one_or_none()
        if sprint is None:
            return {"message": "Brak aktywnego sprintu", "columns": {}}

        result = await db.execute(
            select(Ticket).options(selectinload(Ticket.assignee)).where(Ticket.sprint_id == sprint.id).order_by(Ticket.order, Ticket.created_at)
        )
        tickets = result.scalars().all()

    columns: dict[str, list[dict[str, Any]]] = {s: [] for s in BOARD_STATUSES}
    for t in tickets:
        if t.status in columns:
            columns[t.status].append(
                {
                    "id": str(t.id),
                    "key": f"{project.code}-{t.number}",
                    "title": t.title,
                    "priority": t.priority,
                    "story_points": t.story_points,
                    "assignee": t.assignee.email if t.assignee else None,
                }
            )

    return {
        "sprint": {
            "id": str(sprint.id),
            "name": sprint.name,
            "goal": sprint.goal,
            "start_date": sprint.start_date.isoformat(),
            "end_date": sprint.end_date.isoformat() if sprint.end_date else None,
        },
        "columns": columns,
    }


# --- Podsumowanie projektu ---


@mcp.tool()
async def get_project_summary(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Zagregowane statystyki projektu: otwarte bledy, monitory, aktywny sprint."""
    _user, project = await _get_user_and_project(ctx, project_slug)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

    async with async_session_factory() as db:
        # 500ki: nierozwiazane issues
        issues_count = (
            await db.execute(
                select(func.count(Issue.id)).where(
                    Issue.project_id == project.id,
                    Issue.status == "unresolved",
                )
            )
        ).scalar() or 0

        # Monitoring: failing + uptime 24h
        latest_check_sq = (
            select(
                MonitorCheck.monitor_id,
                func.max(MonitorCheck.checked_at).label("max_checked_at"),
            )
            .group_by(MonitorCheck.monitor_id)
            .subquery()
        )
        failing_result = await db.execute(
            select(func.count(Monitor.id))
            .join(latest_check_sq, Monitor.id == latest_check_sq.c.monitor_id)
            .join(
                MonitorCheck,
                (MonitorCheck.monitor_id == latest_check_sq.c.monitor_id) & (MonitorCheck.checked_at == latest_check_sq.c.max_checked_at),
            )
            .where(
                Monitor.project_id == project.id,
                Monitor.is_active.is_(True),
                MonitorCheck.is_success.is_(False),
            )
        )
        monitors_failing = failing_result.scalar() or 0

        uptime_result = await db.execute(
            select(
                func.count(MonitorCheck.id),
                func.count(case((MonitorCheck.is_success.is_(True), 1))),
            )
            .join(Monitor, MonitorCheck.monitor_id == Monitor.id)
            .where(
                Monitor.project_id == project.id,
                Monitor.is_active.is_(True),
                MonitorCheck.checked_at >= twenty_four_hours_ago,
            )
        )
        uptime_row = uptime_result.one()
        uptime_24h: float | None = None
        if uptime_row[0] > 0:
            uptime_24h = round((uptime_row[1] / uptime_row[0]) * 100, 1)

        # Scrum: aktywny sprint
        sprint_result = await db.execute(
            select(Sprint).where(
                Sprint.project_id == project.id,
                Sprint.status == "active",
            )
        )
        sprint = sprint_result.scalar_one_or_none()

        sprint_summary = None
        if sprint:
            ticket_stats = await db.execute(select(Ticket.status, func.count(Ticket.id)).where(Ticket.sprint_id == sprint.id).group_by(Ticket.status))
            status_counts: dict[str, int] = {row[0]: row[1] for row in ticket_stats.all()}
            sprint_summary = {
                "name": sprint.name,
                "goal": sprint.goal,
                "tickets_by_status": status_counts,
                "total_tickets": sum(status_counts.values()),
            }

        # Backlog count
        backlog_count = (
            await db.execute(
                select(func.count(Ticket.id)).where(
                    Ticket.project_id == project.id,
                    Ticket.status == "backlog",
                )
            )
        ).scalar() or 0

    return {
        "project": {"name": project.name, "slug": project.slug},
        "issues_unresolved": issues_count,
        "monitors_failing": monitors_failing,
        "uptime_24h": uptime_24h,
        "active_sprint": sprint_summary,
        "backlog_count": backlog_count,
    }


# --- Tickety ---


@mcp.tool()
async def list_tickets(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    sprint_id: str | None = None,
    page: int = 1,
) -> list[dict[str, Any]]:
    """Lista ticketow w projekcie.

    Filtrowanie po statusie, priorytecie, sprincie, tekscie.
    Paginacja po 20.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)
    per_page = 20

    async with async_session_factory() as db:
        conditions = [Ticket.project_id == project.id]
        if status and status in TICKET_STATUSES:
            conditions.append(Ticket.status == status)
        if priority and priority in PRIORITIES:
            conditions.append(Ticket.priority == priority)
        if sprint_id:
            conditions.append(Ticket.sprint_id == uuid.UUID(sprint_id))
        if search:
            conditions.append(Ticket.title.ilike(f"%{search}%"))

        total = (await db.execute(select(func.count(Ticket.id)).where(*conditions))).scalar() or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        result = await db.execute(
            select(Ticket)
            .options(selectinload(Ticket.assignee), selectinload(Ticket.sprint))
            .where(*conditions)
            .order_by(Ticket.order, Ticket.created_at.desc())
            .limit(per_page)
            .offset((page - 1) * per_page)
        )
        tickets = result.scalars().all()

    return [
        {
            "id": str(t.id),
            "key": f"{project.code}-{t.number}",
            "title": t.title,
            "description": t.description,
            "status": t.status,
            "priority": t.priority,
            "story_points": t.story_points,
            "sprint": t.sprint.name if t.sprint else None,
            "sprint_id": str(t.sprint_id) if t.sprint_id else None,
            "assignee": t.assignee.email if t.assignee else None,
            "created_via_ai": t.created_via_ai,
            "created_at": t.created_at.isoformat(),
        }
        for t in tickets
    ] + [{"_meta": {"page": page, "total_pages": total_pages, "total": total}}]


@mcp.tool()
async def get_ticket(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
) -> dict[str, Any]:
    """Szczegoly ticketa z komentarzami."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket)
            .options(
                selectinload(Ticket.assignee),
                selectinload(Ticket.sprint),
                selectinload(Ticket.comments).selectinload(TicketComment.author),
            )
            .where(
                Ticket.id == uuid.UUID(ticket_id),
                Ticket.project_id == project.id,
            )
        )
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError("Ticket nie istnieje")

    return {
        "id": str(ticket.id),
        "key": f"{project.code}-{ticket.number}",
        "title": ticket.title,
        "description": ticket.description,
        "status": ticket.status,
        "priority": ticket.priority,
        "story_points": ticket.story_points,
        "sprint": ticket.sprint.name if ticket.sprint else None,
        "sprint_id": str(ticket.sprint_id) if ticket.sprint_id else None,
        "assignee": ticket.assignee.email if ticket.assignee else None,
        "created_via_ai": ticket.created_via_ai,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
        "comments": [
            {
                "id": str(c.id),
                "author": c.author.email,
                "content": c.content,
                "created_via_ai": c.created_via_ai,
                "created_at": c.created_at.isoformat(),
            }
            for c in ticket.comments
        ],
    }


@mcp.tool()
async def create_ticket(
    ctx: Context[Any, Any],
    project_slug: str,
    title: str,
    description: str | None = None,
    priority: str = "medium",
    story_points: int | None = None,
    sprint_id: str | None = None,
    assignee_email: str | None = None,
) -> dict[str, Any]:
    """Utworz nowy ticket w projekcie. Oznaczany jako created_via_ai=True."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not title.strip():
        raise ValueError("Tytul jest wymagany")
    if priority not in PRIORITIES:
        priority = "medium"

    async with async_session_factory() as db:
        assignee_id = None
        if assignee_email:
            result = await db.execute(select(User).where(User.email == assignee_email))
            assignee = result.scalar_one_or_none()
            if assignee:
                assignee_id = assignee.id

        resolved_sprint_id = None
        if sprint_id:
            resolved_sprint_id = uuid.UUID(sprint_id)

        next_number = await get_next_ticket_number(project.id, db)

        ticket = Ticket(
            project_id=project.id,
            number=next_number,
            title=title.strip(),
            description=description.strip() if description else None,
            priority=priority,
            story_points=story_points,
            sprint_id=resolved_sprint_id,
            assignee_id=assignee_id,
            status="backlog" if resolved_sprint_id is None else "todo",
            created_via_ai=True,
        )
        db.add(ticket)
        await db.commit()
        await db.refresh(ticket)

    return {
        "id": str(ticket.id),
        "key": f"{project.code}-{ticket.number}",
        "title": ticket.title,
        "status": ticket.status,
        "created_via_ai": True,
        "message": f"Ticket '{ticket.title}' utworzony",
    }


@mcp.tool()
async def update_ticket(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    story_points: int | None = None,
    sprint_id: str | None = None,
    assignee_email: str | None = None,
) -> dict[str, Any]:
    """Aktualizuj istniejacy ticket. Podaj tylko pola do zmiany."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket).where(
                Ticket.id == uuid.UUID(ticket_id),
                Ticket.project_id == project.id,
            )
        )
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError("Ticket nie istnieje")

        if title is not None:
            title = title.strip()
            if not title:
                raise ValueError("Tytul nie moze byc pusty")
            ticket.title = title

        if description is not None:
            ticket.description = description.strip() or None

        if status is not None:
            if status not in TICKET_STATUSES:
                raise ValueError(f"Nieprawidlowy status: {status}")
            ticket.status = status

        if priority is not None:
            if priority not in PRIORITIES:
                raise ValueError(f"Nieprawidlowy priorytet: {priority}")
            ticket.priority = priority

        if story_points is not None:
            ticket.story_points = story_points

        if sprint_id is not None:
            if sprint_id == "":
                ticket.sprint_id = None
            else:
                ticket.sprint_id = uuid.UUID(sprint_id)

        if assignee_email is not None:
            if assignee_email == "":
                ticket.assignee_id = None
            else:
                result = await db.execute(select(User).where(User.email == assignee_email))
                assignee = result.scalar_one_or_none()
                if assignee:
                    ticket.assignee_id = assignee.id

        await db.commit()

    return {
        "id": str(ticket.id),
        "key": f"{project.code}-{ticket.number}",
        "title": ticket.title,
        "status": ticket.status,
        "message": f"Ticket '{ticket.title}' zaktualizowany",
    }


@mcp.tool()
async def delete_ticket(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
) -> dict[str, Any]:
    """Usun ticket z projektu."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket).where(
                Ticket.id == uuid.UUID(ticket_id),
                Ticket.project_id == project.id,
            )
        )
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError("Ticket nie istnieje")

        title = ticket.title
        await db.delete(ticket)
        await db.commit()

    return {"message": f"Ticket '{title}' usuniety"}


# --- Sprinty ---


@mcp.tool()
async def list_sprints(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Lista sprintow w projekcie ze statystykami (liczba ticketow, suma SP)."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        conditions = [Sprint.project_id == project.id]
        if status:
            conditions.append(Sprint.status == status)

        result = await db.execute(select(Sprint).where(*conditions).order_by(Sprint.created_at.desc()))
        sprints = result.scalars().all()

        sprint_data = []
        for s in sprints:
            stats_result = await db.execute(
                select(
                    func.count(Ticket.id),
                    func.coalesce(func.sum(Ticket.story_points), 0),
                ).where(Ticket.sprint_id == s.id)
            )
            row = stats_result.one()
            sprint_data.append(
                {
                    "id": str(s.id),
                    "name": s.name,
                    "goal": s.goal,
                    "status": s.status,
                    "start_date": s.start_date.isoformat(),
                    "end_date": s.end_date.isoformat() if s.end_date else None,
                    "ticket_count": row[0],
                    "story_points_total": row[1],
                    "created_at": s.created_at.isoformat(),
                }
            )

    return sprint_data


@mcp.tool()
async def get_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
) -> dict[str, Any]:
    """Szczegoly sprintu z lista ticketow."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Sprint)
            .options(selectinload(Sprint.tickets).selectinload(Ticket.assignee))
            .where(
                Sprint.id == uuid.UUID(sprint_id),
                Sprint.project_id == project.id,
            )
        )
        sprint = result.scalar_one_or_none()
        if sprint is None:
            raise ValueError("Sprint nie istnieje")

    return {
        "id": str(sprint.id),
        "name": sprint.name,
        "goal": sprint.goal,
        "status": sprint.status,
        "start_date": sprint.start_date.isoformat(),
        "end_date": sprint.end_date.isoformat() if sprint.end_date else None,
        "created_at": sprint.created_at.isoformat(),
        "tickets": [
            {
                "id": str(t.id),
                "key": f"{project.code}-{t.number}",
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "story_points": t.story_points,
                "assignee": t.assignee.email if t.assignee else None,
            }
            for t in sprint.tickets
        ],
    }


@mcp.tool()
async def create_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    name: str,
    start_date: str,
    goal: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Utworz nowy sprint (status: planning). start_date w formacie YYYY-MM-DD."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not name.strip():
        raise ValueError("Nazwa sprintu jest wymagana")

    async with async_session_factory() as db:
        sprint = Sprint(
            project_id=project.id,
            name=name.strip(),
            goal=goal.strip() if goal else None,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date) if end_date else None,
        )
        db.add(sprint)
        await db.commit()
        await db.refresh(sprint)

    return {
        "id": str(sprint.id),
        "name": sprint.name,
        "status": sprint.status,
        "message": f"Sprint '{sprint.name}' utworzony",
    }


@mcp.tool()
async def start_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
) -> dict[str, Any]:
    """Rozpocznij sprint (zmiana statusu na active).

    Tylko jeden aktywny sprint na projekt.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        error = await svc_start_sprint(uuid.UUID(sprint_id), project.id, db)
        if error:
            raise ValueError(error)

    return {"message": "Sprint rozpoczety", "sprint_id": sprint_id}


@mcp.tool()
async def complete_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
) -> dict[str, Any]:
    """Zakoncz sprint. Niedokonczone tickety wracaja do backloga."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        error = await svc_complete_sprint(uuid.UUID(sprint_id), project.id, db)
        if error:
            raise ValueError(error)

    return {"message": "Sprint zakonczony", "sprint_id": sprint_id}


# --- Komentarze ---


@mcp.tool()
async def list_comments(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
) -> list[dict[str, Any]]:
    """Lista komentarzy do ticketa."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        ticket_result = await db.execute(
            select(Ticket).where(
                Ticket.id == uuid.UUID(ticket_id),
                Ticket.project_id == project.id,
            )
        )
        if ticket_result.scalar_one_or_none() is None:
            raise ValueError("Ticket nie istnieje")

        comment_result = await db.execute(
            select(TicketComment)
            .options(selectinload(TicketComment.author))
            .where(TicketComment.ticket_id == uuid.UUID(ticket_id))
            .order_by(TicketComment.created_at)
        )
        comments = comment_result.scalars().all()

    return [
        {
            "id": str(c.id),
            "author": c.author.email,
            "content": c.content,
            "created_via_ai": c.created_via_ai,
            "created_at": c.created_at.isoformat(),
        }
        for c in comments
    ]


@mcp.tool()
async def add_comment(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    content: str,
) -> dict[str, Any]:
    """Dodaj komentarz do ticketa. Oznaczany jako created_via_ai=True."""
    user, project = await _get_user_and_project(ctx, project_slug)

    if not content.strip():
        raise ValueError("Tresc komentarza nie moze byc pusta")

    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket).where(
                Ticket.id == uuid.UUID(ticket_id),
                Ticket.project_id == project.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("Ticket nie istnieje")

        comment = TicketComment(
            ticket_id=uuid.UUID(ticket_id),
            user_id=user.id,
            content=content.strip(),
            created_via_ai=True,
        )
        db.add(comment)
        await db.commit()
        await db.refresh(comment)

    return {
        "id": str(comment.id),
        "message": "Komentarz dodany",
        "created_via_ai": True,
    }


# --- Time Tracking ---


@mcp.tool()
async def log_time(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    duration_minutes: int,
    date_logged: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Zaloguj czas pracy na tickecie. Oznaczany jako created_via_ai=True.

    date_logged w formacie YYYY-MM-DD. duration_minutes musi byc > 0.
    """
    user, _project = await _get_user_and_project(ctx, project_slug)

    if duration_minutes <= 0:
        raise ValueError("Czas musi byc wiekszy niz 0")

    parsed_date = date.fromisoformat(date_logged)

    async with async_session_factory() as db:
        result = await add_time_entry(
            ticket_id=uuid.UUID(ticket_id),
            user_id=user.id,
            duration_minutes=duration_minutes,
            date_logged=parsed_date,
            description=description.strip() if description else None,
            db=db,
            created_via_ai=True,
        )

        if isinstance(result, str):
            raise ValueError(result)

    return {
        "id": str(result.id),
        "ticket_id": str(result.ticket_id),
        "duration_minutes": result.duration_minutes,
        "date_logged": result.date_logged.isoformat(),
        "description": result.description,
        "created_via_ai": True,
        "message": f"Zalogowano {duration_minutes} min na tickecie",
    }
