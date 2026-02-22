"""MCP Server -- narzedzia Scrum dla Claude Code."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from open_sentry.constants import PRIORITIES, TICKET_STATUSES
from open_sentry.database import async_session_factory
from open_sentry.models.project import Project
from open_sentry.models.project_member import ProjectMember
from open_sentry.models.sprint import Sprint
from open_sentry.models.ticket import Ticket
from open_sentry.models.ticket_comment import TicketComment
from open_sentry.models.user import User
from open_sentry.services.mcp_auth import verify_mcp_token
from open_sentry.services.sprint import complete_sprint as svc_complete_sprint
from open_sentry.services.sprint import start_sprint as svc_start_sprint

logger = logging.getLogger("open_sentry.mcp")

mcp = FastMCP(
    "Open Sentry Scrum",
    instructions=(
        "Serwer MCP do zarzadzania modulem Scrum w Open Sentry. "
        "Pozwala na CRUD ticketow, sprintow i komentarzy. "
        "Wymaga tokenu API (Bearer) w naglowku Authorization."
    ),
)


async def _auth(ctx: Context[Any, Any]) -> User:
    """Wyciagnij token z naglowka HTTP i zwaliduj uzytkownika."""
    request = ctx.request_context
    if request is None:
        raise ValueError("Brak kontekstu HTTP — token wymagany")

    transport = getattr(request, "transport", None)
    if transport is None:
        raise ValueError("Brak transportu HTTP")

    headers = getattr(transport, "headers", {}) or {}
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
    request = ctx.request_context
    if request is None:
        raise ValueError("Brak kontekstu HTTP")
    transport = getattr(request, "transport", None)
    if transport is None:
        raise ValueError("Brak transportu HTTP")
    headers = getattr(transport, "headers", {}) or {}
    auth_header = headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise ValueError("Brak tokenu Bearer")
    return str(auth_header[7:])


async def _get_user_and_project(
    ctx: Context[Any, Any], project_slug: str
) -> tuple[User, Project]:
    """Autoryzuj uzytkownika i sprawdz dostep do projektu."""
    raw_token = await _get_auth_header(ctx)

    async with async_session_factory() as db:
        user = await verify_mcp_token(raw_token, db)
        if user is None:
            raise ValueError("Nieprawidlowy lub nieaktywny token API")

        result = await db.execute(
            select(Project).where(
                Project.slug == project_slug, Project.is_active.is_(True)
            )
        )
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

        total = (
            await db.execute(select(func.count(Ticket.id)).where(*conditions))
        ).scalar() or 0
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

        ticket = Ticket(
            project_id=project.id,
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
                result = await db.execute(
                    select(User).where(User.email == assignee_email)
                )
                assignee = result.scalar_one_or_none()
                if assignee:
                    ticket.assignee_id = assignee.id

        await db.commit()

    return {
        "id": str(ticket.id),
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

        result = await db.execute(
            select(Sprint).where(*conditions).order_by(Sprint.created_at.desc())
        )
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
