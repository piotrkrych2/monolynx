"""Serwis logiki ticketow, tablicy Kanban, komentarzy, kryteriow akceptacji i sprintow
(poza start/complete sprintu i burndown, ktore zostaja w services/sprint.py i
services/burndown.py) -- sesje przekazuje wywolujacy (MCP tool / REST API v2).

Kazda funkcja przyjmujaca user i project zaczyna sie od ensure_permission (scrum:read|write|delete, wg mapy
operacji), przed lookupem zasobu i walidacja, wiec samo czlonkostwo w projekcie nie wystarcza."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import BOARD_STATUSES, PRIORITIES, TICKET_STATUSES
from monolynx.models.label import Label, TicketLabel
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services import ticket_dependencies
from monolynx.services.permissions import check_permission, ensure_permission
from monolynx.services.settlements import is_ticket_frozen
from monolynx.services.ticket_numbering import get_next_ticket_number

_TICKET_KEY_RE = re.compile(r"^([A-Za-z]{1,10})-(\d+)$")
_NOT_PROJECT_MEMBER_MSG = "Wskazany użytkownik nie jest członkiem projektu"


async def resolve_ticket_uuid(ticket_id: str, project_id: uuid.UUID, db: AsyncSession) -> uuid.UUID:
    """Zamien ticket_id (UUID lub klucz jak MNX-12) na UUID ticketa."""
    try:
        return uuid.UUID(ticket_id)
    except ValueError:
        pass

    match = _TICKET_KEY_RE.match(ticket_id.strip())
    if not match:
        raise ValueError(f"Nieprawidlowy identyfikator ticketa: '{ticket_id}'. Podaj UUID lub klucz (np. MNX-12)")

    number = int(match.group(2))
    result = await db.execute(
        select(Ticket.id).where(
            Ticket.project_id == project_id,
            Ticket.number == number,
        )
    )
    ticket_uuid = result.scalar_one_or_none()
    if ticket_uuid is None:
        raise ValueError(f"Ticket '{ticket_id}' nie istnieje w projekcie")
    return ticket_uuid


async def ensure_sprint_in_project(db: AsyncSession, project_id: uuid.UUID, sprint_id: uuid.UUID) -> None:
    """Sprint z `sprint_id` musi nalezec do projektu operacji (UUID z cudzego projektu nie jest referencja)."""
    found = await db.execute(select(Sprint.id).where(Sprint.id == sprint_id, Sprint.project_id == project_id))
    if found.scalar_one_or_none() is None:
        raise ValueError("Wskazany sprint nie istnieje w tym projekcie")


async def ensure_member_of_project(db: AsyncSession, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Assignee musi byc czlonkiem projektu operacji."""
    found = await db.execute(select(ProjectMember.id).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id))
    if found.scalar_one_or_none() is None:
        raise ValueError(_NOT_PROJECT_MEMBER_MSG)


async def _ensure_spec_page_in_project(db: AsyncSession, project_id: uuid.UUID, page_id: uuid.UUID) -> None:
    found = await db.execute(select(WikiPage.id).where(WikiPage.id == page_id, WikiPage.project_id == project_id))
    if found.scalar_one_or_none() is None:
        raise ValueError("Wskazana strona wiki (spec_page_id) nie istnieje w tym projekcie")


async def _resolve_assignee_id(db: AsyncSession, project_id: uuid.UUID, email: str) -> uuid.UUID:
    """Email -> id czlonka projektu; nieznany email i uzytkownik spoza projektu daja ten sam blad."""
    found = await db.execute(select(User.id).where(User.email == email))
    user_id = found.scalar_one_or_none()
    if user_id is None:
        raise ValueError(_NOT_PROJECT_MEMBER_MSG)
    await ensure_member_of_project(db, project_id, user_id)
    return user_id


async def _can_read_settlements(db: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    """Sprawdza czy user ma rozliczenia:read w projekcie."""
    return await check_permission(db, user_id, project_id, "rozliczenia", "read")


# --- Tablica Kanban ---


@dataclass
class BoardResult:
    sprint: Sprint | None
    columns: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


async def get_board(db: AsyncSession, user: User, project: Project) -> BoardResult:
    """Tickety aktywnego sprintu pogrupowane po statusie. sprint=None gdy brak aktywnego sprintu."""
    await ensure_permission(db, user.id, project.id, "scrum", "read")
    sprint_result = await db.execute(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status == "active",
        )
    )
    sprint = sprint_result.scalar_one_or_none()
    if sprint is None:
        return BoardResult(sprint=None, columns={})

    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.assignee), selectinload(Ticket.labels))
        .where(Ticket.sprint_id == sprint.id, Ticket.project_id == project.id)
        .order_by(Ticket.order, Ticket.created_at)
    )
    tickets = result.scalars().all()

    columns: dict[str, list[dict[str, Any]]] = {s: [] for s in BOARD_STATUSES}
    for t in tickets:
        if t.status in columns:
            columns[t.status].append(
                {
                    "key": f"{project.code}-{t.number}",
                    "title": t.title,
                    "priority": t.priority,
                    "story_points": t.story_points,
                    "assignee": t.assignee.email if t.assignee else None,
                    "labels": [lbl.name for lbl in t.labels] if t.labels else [],
                }
            )

    return BoardResult(sprint=sprint, columns=columns)


# --- Tickety ---


@dataclass
class ListTicketsResult:
    tickets: list[Ticket]
    total: int
    page: int
    total_pages: int


async def list_tickets(
    db: AsyncSession,
    user: User,
    project: Project,
    status: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    sprint_id: str | None = None,
    due_date_before: str | None = None,
    due_date_after: str | None = None,
    overdue: bool = False,
    label_id: str | None = None,
    page: int = 1,
) -> ListTicketsResult:
    """Lista ticketow w projekcie z filtrowaniem i paginacja (20 na strone)."""
    await ensure_permission(db, user.id, project.id, "scrum", "read")
    per_page = 20

    conditions = [Ticket.project_id == project.id]
    if status and status in TICKET_STATUSES:
        conditions.append(Ticket.status == status)
    if priority and priority in PRIORITIES:
        conditions.append(Ticket.priority == priority)
    if sprint_id:
        conditions.append(Ticket.sprint_id == uuid.UUID(sprint_id))
    if search:
        conditions.append(Ticket.title.ilike(f"%{search}%"))
    if due_date_before:
        try:
            conditions.append(Ticket.due_date <= date.fromisoformat(due_date_before))
        except ValueError as e:
            raise ValueError(f"Nieprawidlowy format due_date_before: '{due_date_before}'. Uzyj YYYY-MM-DD") from e
    if due_date_after:
        try:
            conditions.append(Ticket.due_date >= date.fromisoformat(due_date_after))
        except ValueError as e:
            raise ValueError(f"Nieprawidlowy format due_date_after: '{due_date_after}'. Uzyj YYYY-MM-DD") from e
    if overdue:
        today = date.today()
        conditions.append(Ticket.due_date < today)
        conditions.append(Ticket.due_date.is_not(None))
        conditions.append(Ticket.status != "done")
    if label_id:
        conditions.append(Ticket.id.in_(select(TicketLabel.ticket_id).where(TicketLabel.label_id == uuid.UUID(label_id))))

    total = (await db.execute(select(func.count(Ticket.id)).where(*conditions))).scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    result = await db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.sprint),
            selectinload(Ticket.labels),
            selectinload(Ticket.settlements),
            selectinload(Ticket.blocked_by),
        )
        .where(*conditions)
        .order_by(Ticket.order, Ticket.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    tickets = list(result.scalars().all())

    return ListTicketsResult(tickets=tickets, total=total, page=page, total_pages=total_pages)


@dataclass
class SearchTicketsResult:
    results: list[dict[str, Any]]
    total: int
    page: int
    total_pages: int


async def search_tickets(
    db: AsyncSession,
    user: User,
    project: Project,
    query: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee_email: str | None = None,
    sprint_id: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    page: int = 1,
) -> SearchTicketsResult:
    """Wyszukaj tickety w projekcie z filtrami i paginacja (20 na strone).

    Redakcja settlements w wynikach zalezy od uprawnienia rozliczenia:read wywolujacego.
    """
    await ensure_permission(db, user.id, project.id, "scrum", "read")
    per_page = 20

    conditions = [Ticket.project_id == project.id]

    if query:
        pattern = f"%{query}%"
        conditions.append(
            or_(
                Ticket.title.ilike(pattern),
                Ticket.description.ilike(pattern),
            )
        )

    if status and status in TICKET_STATUSES:
        conditions.append(Ticket.status == status)

    if priority and priority in PRIORITIES:
        conditions.append(Ticket.priority == priority)

    if sprint_id:
        conditions.append(Ticket.sprint_id == uuid.UUID(sprint_id))

    if due_before:
        try:
            conditions.append(Ticket.due_date <= date.fromisoformat(due_before))
        except ValueError as e:
            raise ValueError(f"Nieprawidlowy format due_before: '{due_before}'. Uzyj YYYY-MM-DD") from e

    if due_after:
        try:
            conditions.append(Ticket.due_date >= date.fromisoformat(due_after))
        except ValueError as e:
            raise ValueError(f"Nieprawidlowy format due_after: '{due_after}'. Uzyj YYYY-MM-DD") from e

    if assignee_email:
        assignee_result = await db.execute(select(User).where(User.email == assignee_email))
        assignee = assignee_result.scalar_one_or_none()
        if assignee is None:
            return SearchTicketsResult(results=[], total=0, page=1, total_pages=1)
        conditions.append(Ticket.assignee_id == assignee.id)

    total = (await db.execute(select(func.count(Ticket.id)).where(*conditions))).scalar() or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))

    result = await db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.sprint),
            selectinload(Ticket.settlements),
        )
        .where(*conditions)
        .order_by(Ticket.order, Ticket.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    tickets = result.scalars().all()
    can_read = await _can_read_settlements(db, user.id, project.id)

    def _settlement_dict(s: Settlement) -> dict[str, Any]:
        return {
            "id": str(s.id),
            "number": s.number,
            "name": s.name,
            "status": s.status,
            "period_from": s.period_from.isoformat() if s.period_from else None,
            "period_to": s.period_to.isoformat() if s.period_to else None,
        }

    def _ticket_row(t: Ticket) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": str(t.id),
            "key": f"{project.code}-{t.number}",
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "story_points": t.story_points,
            "assignee": t.assignee.email if t.assignee else None,
            "due_date": t.due_date.isoformat() if t.due_date else None,
        }
        if can_read:
            row["settlements"] = [_settlement_dict(s) for s in t.settlements if s.is_active]
        return row

    return SearchTicketsResult(
        results=[_ticket_row(t) for t in tickets],
        total=total,
        page=page,
        total_pages=total_pages,
    )


@dataclass
class TicketDetailResult:
    ticket: Ticket
    can_read_settlements: bool


async def get_ticket(db: AsyncSession, user: User, project: Project, ticket_id: str) -> TicketDetailResult:
    """Szczegoly ticketa z relacjami potrzebnymi do formatowania (wrapper formatuje po zamknieciu sesji)."""
    await ensure_permission(db, user.id, project.id, "scrum", "read")
    resolved_id = await resolve_ticket_uuid(ticket_id, project.id, db)

    result = await db.execute(
        select(Ticket)
        .options(
            selectinload(Ticket.assignee),
            selectinload(Ticket.sprint),
            selectinload(Ticket.comments).selectinload(TicketComment.author),
            selectinload(Ticket.labels),
            selectinload(Ticket.attachments),
            selectinload(Ticket.acceptance_criteria),
            selectinload(Ticket.settlements),
            selectinload(Ticket.blocked_by),
            selectinload(Ticket.blocking),
        )
        .where(
            Ticket.id == resolved_id,
            Ticket.project_id == project.id,
        )
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise ValueError("Ticket nie istnieje")

    can_read = await _can_read_settlements(db, user.id, project.id)

    return TicketDetailResult(ticket=ticket, can_read_settlements=can_read)


@dataclass
class CreateTicketResult:
    ticket: Ticket
    criteria_count: int


async def create_ticket(
    db: AsyncSession,
    user: User,
    project: Project,
    title: str,
    description: str | None = None,
    priority: str = "medium",
    story_points: int | None = None,
    sprint_id: str | None = None,
    assignee_email: str | None = None,
    due_date: str | None = None,
    label_ids: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    spec_page_id: str | None = None,
    blocked_by_ids: list[str] | None = None,
) -> CreateTicketResult:
    """Utworz nowy ticket w projekcie. Oznaczany jako created_via_ai=True.

    blocked_by_ids: deleguje walidacje i zapis do services/ticket_dependencies.set_blockers.
    """
    await ensure_permission(db, user.id, project.id, "scrum", "write")
    if not title.strip():
        raise ValueError("Tytul jest wymagany")
    if priority not in PRIORITIES:
        priority = "medium"

    parsed_due_date: date | None = None
    if due_date:
        try:
            parsed_due_date = date.fromisoformat(due_date)
        except ValueError as e:
            raise ValueError(f"Nieprawidlowy format daty due_date: '{due_date}'. Uzyj YYYY-MM-DD") from e

    parsed_blocked_by_ids: list[uuid.UUID] | None = None
    if blocked_by_ids is not None:
        parsed_blocked_by_ids = []
        for bid in blocked_by_ids:
            try:
                parsed_blocked_by_ids.append(uuid.UUID(bid))
            except ValueError as e:
                raise ValueError(f"Nieprawidlowy UUID blokera: '{bid}'") from e

    assignee_id = None
    if assignee_email:
        assignee_id = await _resolve_assignee_id(db, project.id, assignee_email)

    resolved_sprint_id = None
    if sprint_id:
        resolved_sprint_id = uuid.UUID(sprint_id)
        await ensure_sprint_in_project(db, project.id, resolved_sprint_id)

    resolved_spec_page_id = None
    if spec_page_id:
        resolved_spec_page_id = uuid.UUID(spec_page_id)
        await _ensure_spec_page_in_project(db, project.id, resolved_spec_page_id)

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
        due_date=parsed_due_date,
    )
    if resolved_spec_page_id:
        ticket.spec_page_id = resolved_spec_page_id
    db.add(ticket)
    await db.flush()

    if label_ids:
        label_uuids = [uuid.UUID(lid) for lid in label_ids]
        labels_result = await db.execute(
            select(Label).where(
                Label.id.in_(label_uuids),
                Label.project_id == project.id,
            )
        )
        labels = labels_result.scalars().all()
        for lb in labels:
            db.add(TicketLabel(ticket_id=ticket.id, label_id=lb.id))

    criteria_count = 0
    if acceptance_criteria:
        for idx, desc in enumerate(acceptance_criteria):
            desc_stripped = desc.strip()
            if desc_stripped:
                db.add(
                    TicketAcceptanceCriterion(
                        ticket_id=ticket.id,
                        description=desc_stripped,
                        position=idx,
                        created_by_user_id=user.id,
                        created_via_ai=True,
                    )
                )
                criteria_count += 1

    if parsed_blocked_by_ids:
        await ticket_dependencies.set_blockers(db, ticket, parsed_blocked_by_ids)

    await db.commit()
    await db.refresh(ticket)

    return CreateTicketResult(ticket=ticket, criteria_count=criteria_count)


async def update_ticket(
    db: AsyncSession,
    user: User,
    project: Project,
    ticket_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    story_points: int | None = None,
    sprint_id: str | None = None,
    assignee_email: str | None = None,
    due_date: str | None = None,
    label_ids: list[str] | None = None,
    blocked_by_ids: list[str] | None = None,
) -> Ticket:
    """Aktualizuj istniejacy ticket (PATCH semantics).

    Ticket zamrozony (settlement sent/paid): dozwolona tylko zmiana statusu.
    blocked_by_ids: replace-all przez services/ticket_dependencies.set_blockers.
    """
    await ensure_permission(db, user.id, project.id, "scrum", "write")
    resolved_id = await resolve_ticket_uuid(ticket_id, project.id, db)

    parsed_blocked_by_ids: list[uuid.UUID] | None = None
    if blocked_by_ids is not None:
        parsed_blocked_by_ids = []
        for bid in blocked_by_ids:
            try:
                parsed_blocked_by_ids.append(uuid.UUID(bid))
            except ValueError as e:
                raise ValueError(f"Nieprawidlowy UUID blokera: '{bid}'") from e

    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.settlements))
        .where(
            Ticket.id == resolved_id,
            Ticket.project_id == project.id,
        )
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise ValueError("Ticket nie istnieje")

    if is_ticket_frozen(ticket):
        # Frozen ticket: dozwolona tylko zmiana statusu (wszystkie inne pola musza byc None)
        other_fields = (title, description, priority, story_points, sprint_id, assignee_email, due_date, label_ids, blocked_by_ids)
        if any(f is not None for f in other_fields):
            raise ValueError("Ticket zamrozony — dozwolona tylko zmiana statusu. Pelna edycja zablokowana dla rozliczen sent/paid.")

    # Referencje waliduje przed jakakolwiek mutacja pol, zeby odrzucenie nie zostawialo czesciowego zapisu.
    new_sprint_id: uuid.UUID | None = None
    if sprint_id:
        new_sprint_id = uuid.UUID(sprint_id)
        await ensure_sprint_in_project(db, project.id, new_sprint_id)

    new_assignee_id: uuid.UUID | None = None
    if assignee_email:
        if assignee_email == "me":
            assignee_email = user.email
        new_assignee_id = await _resolve_assignee_id(db, project.id, assignee_email)

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
        ticket.sprint_id = new_sprint_id

    if assignee_email is not None:
        ticket.assignee_id = new_assignee_id

    if due_date is not None:
        if due_date == "":
            ticket.due_date = None
        else:
            try:
                ticket.due_date = date.fromisoformat(due_date)
            except ValueError as e:
                raise ValueError(f"Nieprawidlowy format daty due_date: '{due_date}'. Uzyj YYYY-MM-DD") from e

    if label_ids is not None:
        # Usun stare etykiety
        await db.execute(delete(TicketLabel).where(TicketLabel.ticket_id == ticket.id))
        if label_ids:
            label_uuids = [uuid.UUID(lid) for lid in label_ids]
            labels_result = await db.execute(
                select(Label).where(
                    Label.id.in_(label_uuids),
                    Label.project_id == project.id,
                )
            )
            labels = labels_result.scalars().all()
            for lb in labels:
                db.add(TicketLabel(ticket_id=ticket.id, label_id=lb.id))

    if parsed_blocked_by_ids is not None:
        await ticket_dependencies.set_blockers(db, ticket, parsed_blocked_by_ids)

    await db.commit()
    return ticket


async def delete_ticket(db: AsyncSession, user: User, project: Project, ticket_id: str) -> str:
    """Usun ticket z projektu. Zwraca tytul usunietego ticketa (do komunikatu wrappera)."""
    await ensure_permission(db, user.id, project.id, "scrum", "delete")
    resolved_id = await resolve_ticket_uuid(ticket_id, project.id, db)

    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.settlements))
        .where(
            Ticket.id == resolved_id,
            Ticket.project_id == project.id,
        )
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        raise ValueError("Ticket nie istnieje")

    if is_ticket_frozen(ticket):
        raise ValueError("Ticket zamrozony — powiazany z rozliczeniem w statusie sent/paid")

    title = ticket.title
    await db.delete(ticket)
    await db.commit()
    return title


@dataclass
class BulkUpdateResult:
    updated: int
    failed: list[dict[str, str]]


async def bulk_update_tickets(
    db: AsyncSession,
    user: User,
    project: Project,
    ticket_ids: list[str],
    status: str | None = None,
    priority: str | None = None,
    assignee_email: str | None = None,
    sprint_id: str | None = None,
    due_date: str | None = None,
) -> BulkUpdateResult:
    """Masowa aktualizacja ticketow (max 100 na wywolanie). Frozen tickety: tylko status."""
    await ensure_permission(db, user.id, project.id, "scrum", "write")
    if len(ticket_ids) > 100:
        raise ValueError(f"Zbyt duzo ticketow: {len(ticket_ids)}. Limit to 100 na jedno wywolanie.")

    # Walidacja statusu i priorytetu przed petla (fail fast)
    if status is not None and status not in TICKET_STATUSES:
        raise ValueError(f"Nieprawidlowy status: {status}. Dozwolone: {', '.join(TICKET_STATUSES)}")
    if priority is not None and priority not in PRIORITIES:
        raise ValueError(f"Nieprawidlowy priorytet: {priority}. Dozwolone: {', '.join(PRIORITIES)}")

    # Parsowanie due_date przed petla (fail fast)
    # clear_due_date=True oznacza ze nalezy ustawic None; parsed_due_date=date = nowa wartosc
    clear_due_date = False
    parsed_due_date: date | None = None
    if due_date is not None:
        if due_date == "":
            clear_due_date = True
        else:
            try:
                parsed_due_date = date.fromisoformat(due_date)
            except ValueError as e:
                raise ValueError(f"Nieprawidlowy format daty due_date: '{due_date}'. Uzyj YYYY-MM-DD") from e

    updated = 0
    failed: list[dict[str, str]] = []

    # Sprint i assignee waliduje raz przed petla: odrzucenie dotyczy calego wywolania
    resolved_sprint_id = None
    if sprint_id:
        resolved_sprint_id = uuid.UUID(sprint_id)
        await ensure_sprint_in_project(db, project.id, resolved_sprint_id)

    resolved_assignee_id = None
    if assignee_email:
        resolved_assignee_id = await _resolve_assignee_id(db, project.id, assignee_email)

    # Resolve ticket identifiers (UUID or key like MNX-12)
    valid_uuids: dict[uuid.UUID, str] = {}
    for ticket_id_str in ticket_ids:
        try:
            resolved = await resolve_ticket_uuid(ticket_id_str, project.id, db)
            valid_uuids[resolved] = ticket_id_str
        except ValueError:
            failed.append({"id": ticket_id_str, "reason": "Nieprawidlowy identyfikator ticketa"})

    if valid_uuids:
        tickets_result = await db.execute(
            select(Ticket)
            .options(selectinload(Ticket.settlements))
            .where(
                Ticket.id.in_(valid_uuids.keys()),
                Ticket.project_id == project.id,
            )
        )
        found_tickets = {t.id: t for t in tickets_result.scalars().all()}

        for ticket_uuid, ticket_id_str in valid_uuids.items():
            ticket = found_tickets.get(ticket_uuid)
            if ticket is None:
                failed.append({"id": ticket_id_str, "reason": "Ticket nie istnieje"})
                continue

            # Frozen ticket: dozwolona tylko zmiana statusu (wszystkie inne pola musza byc None)
            if is_ticket_frozen(ticket) and any(f is not None for f in (priority, assignee_email, sprint_id, due_date)):
                failed.append({"id": ticket_id_str, "reason": "Ticket zamrozony — dozwolona tylko zmiana statusu"})
                continue

            try:
                if status is not None:
                    ticket.status = status
                if priority is not None:
                    ticket.priority = priority
                if sprint_id is not None:
                    ticket.sprint_id = resolved_sprint_id
                if assignee_email is not None:
                    ticket.assignee_id = None if assignee_email == "" else resolved_assignee_id
                if due_date is not None:
                    ticket.due_date = None if clear_due_date else parsed_due_date

                updated += 1
            except Exception as e:
                failed.append({"id": ticket_id_str, "reason": str(e)})

    await db.commit()

    return BulkUpdateResult(updated=updated, failed=failed)


# --- Komentarze ---


async def list_comments(db: AsyncSession, user: User, project: Project, ticket_id: str) -> list[TicketComment]:
    """Lista komentarzy do ticketa (autor preloaded)."""
    await ensure_permission(db, user.id, project.id, "scrum", "read")
    resolved_id = await resolve_ticket_uuid(ticket_id, project.id, db)

    ticket_result = await db.execute(
        select(Ticket).where(
            Ticket.id == resolved_id,
            Ticket.project_id == project.id,
        )
    )
    if ticket_result.scalar_one_or_none() is None:
        raise ValueError("Ticket nie istnieje")

    comment_result = await db.execute(
        select(TicketComment)
        .options(selectinload(TicketComment.author))
        .where(TicketComment.ticket_id == resolved_id)
        .order_by(TicketComment.created_at)
    )
    return list(comment_result.scalars().all())


async def add_comment(db: AsyncSession, user: User, project: Project, ticket_id: str, content: str) -> TicketComment:
    """Dodaj komentarz do ticketa. Oznaczany jako created_via_ai=True."""
    await ensure_permission(db, user.id, project.id, "scrum", "write")
    resolved_id = await resolve_ticket_uuid(ticket_id, project.id, db)

    if not content.strip():
        raise ValueError("Tresc komentarza nie moze byc pusta")

    result = await db.execute(
        select(Ticket).where(
            Ticket.id == resolved_id,
            Ticket.project_id == project.id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise ValueError("Ticket nie istnieje")

    comment = TicketComment(
        ticket_id=resolved_id,
        user_id=user.id,
        content=content.strip(),
        created_via_ai=True,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


# --- Kryteria akceptacji ---


async def list_acceptance_criteria(db: AsyncSession, user: User, project: Project, ticket_id: str) -> list[TicketAcceptanceCriterion]:
    """Lista kryteriow akceptacji ticketa (autor + kto ukonczyl, preloaded)."""
    await ensure_permission(db, user.id, project.id, "scrum", "read")
    resolved_id = await resolve_ticket_uuid(ticket_id, project.id, db)

    ticket_result = await db.execute(select(Ticket).where(Ticket.id == resolved_id, Ticket.project_id == project.id))
    if ticket_result.scalar_one_or_none() is None:
        raise ValueError("Ticket nie istnieje")

    crit_result = await db.execute(
        select(TicketAcceptanceCriterion)
        .options(
            selectinload(TicketAcceptanceCriterion.created_by_user),
            selectinload(TicketAcceptanceCriterion.completed_by_user),
        )
        .where(TicketAcceptanceCriterion.ticket_id == resolved_id)
        .order_by(TicketAcceptanceCriterion.position)
    )
    return list(crit_result.scalars().all())


async def add_acceptance_criterion(db: AsyncSession, user: User, project: Project, ticket_id: str, description: str) -> TicketAcceptanceCriterion:
    """Dodaj kryterium akceptacji do ticketa. Oznaczane jako created_via_ai=True."""
    await ensure_permission(db, user.id, project.id, "scrum", "write")
    resolved_id = await resolve_ticket_uuid(ticket_id, project.id, db)

    if not description.strip():
        raise ValueError("Opis kryterium nie może być pusty")

    ticket_result = await db.execute(select(Ticket).where(Ticket.id == resolved_id, Ticket.project_id == project.id))
    if ticket_result.scalar_one_or_none() is None:
        raise ValueError("Ticket nie istnieje")

    max_pos_result = await db.execute(
        select(func.coalesce(func.max(TicketAcceptanceCriterion.position), -1)).where(TicketAcceptanceCriterion.ticket_id == resolved_id)
    )
    max_pos: int = max_pos_result.scalar_one()

    criterion = TicketAcceptanceCriterion(
        ticket_id=resolved_id,
        description=description.strip(),
        position=max_pos + 1,
        created_by_user_id=user.id,
        created_via_ai=True,
    )
    db.add(criterion)
    await db.commit()
    await db.refresh(criterion)
    return criterion


async def update_acceptance_criterion(
    db: AsyncSession,
    user: User,
    project: Project,
    ticket_id: str,
    criterion_id: str,
    description: str | None = None,
    is_completed: bool | None = None,
) -> TicketAcceptanceCriterion:
    """Aktualizuj kryterium akceptacji (opis i/lub status ukonczenia). Podaj tylko pola do zmiany.

    Przy ustawieniu is_completed=True: completed_via_ai=True.
    """
    await ensure_permission(db, user.id, project.id, "scrum", "write")
    resolved_id = await resolve_ticket_uuid(ticket_id, project.id, db)

    try:
        crit_uuid = uuid.UUID(criterion_id)
    except ValueError:
        raise ValueError("Nieprawidlowy format criterion_id (oczekiwany UUID)") from None

    ticket_result = await db.execute(select(Ticket).where(Ticket.id == resolved_id, Ticket.project_id == project.id))
    if ticket_result.scalar_one_or_none() is None:
        raise ValueError("Ticket nie istnieje")

    crit_result = await db.execute(
        select(TicketAcceptanceCriterion).where(
            TicketAcceptanceCriterion.id == crit_uuid,
            TicketAcceptanceCriterion.ticket_id == resolved_id,
        )
    )
    criterion = crit_result.scalar_one_or_none()
    if criterion is None:
        raise ValueError("Kryterium akceptacji nie istnieje w tym tickecie")

    if description is not None:
        if not description.strip():
            raise ValueError("Opis kryterium nie może być pusty")
        criterion.description = description.strip()

    if is_completed is not None:
        if is_completed and not criterion.is_completed:
            criterion.is_completed = True
            criterion.completed_by_user_id = user.id
            criterion.completed_at = datetime.now(UTC)
            criterion.completed_via_ai = True
        elif not is_completed and criterion.is_completed:
            criterion.is_completed = False
            criterion.completed_by_user_id = None
            criterion.completed_at = None
            criterion.completed_via_ai = False

    await db.commit()
    await db.refresh(criterion)
    return criterion


async def delete_acceptance_criterion(db: AsyncSession, user: User, project: Project, ticket_id: str, criterion_id: str) -> None:
    """Usun kryterium akceptacji."""
    await ensure_permission(db, user.id, project.id, "scrum", "delete")
    resolved_id = await resolve_ticket_uuid(ticket_id, project.id, db)

    try:
        crit_uuid = uuid.UUID(criterion_id)
    except ValueError:
        raise ValueError("Nieprawidlowy format criterion_id (oczekiwany UUID)") from None

    ticket_result = await db.execute(select(Ticket).where(Ticket.id == resolved_id, Ticket.project_id == project.id))
    if ticket_result.scalar_one_or_none() is None:
        raise ValueError("Ticket nie istnieje")

    crit_result = await db.execute(
        select(TicketAcceptanceCriterion).where(
            TicketAcceptanceCriterion.id == crit_uuid,
            TicketAcceptanceCriterion.ticket_id == resolved_id,
        )
    )
    criterion = crit_result.scalar_one_or_none()
    if criterion is None:
        raise ValueError("Kryterium akceptacji nie istnieje w tym tickecie")

    await db.delete(criterion)
    await db.commit()


# --- Sprinty (poza start/complete -- zostaja w services/sprint.py) ---


@dataclass
class SprintSummary:
    sprint: Sprint
    ticket_count: int
    story_points_total: int


async def list_sprints(db: AsyncSession, user: User, project: Project, status: str | None = None) -> list[SprintSummary]:
    """Lista sprintow w projekcie ze statystykami (liczba ticketow, suma SP)."""
    await ensure_permission(db, user.id, project.id, "scrum", "read")
    conditions = [Sprint.project_id == project.id]
    if status:
        conditions.append(Sprint.status == status)

    result = await db.execute(select(Sprint).where(*conditions).order_by(Sprint.created_at.desc()))
    sprints = result.scalars().all()

    summaries: list[SprintSummary] = []
    for s in sprints:
        stats_result = await db.execute(
            select(
                func.count(Ticket.id),
                func.coalesce(func.sum(Ticket.story_points), 0),
            ).where(Ticket.sprint_id == s.id, Ticket.project_id == project.id)
        )
        row = stats_result.one()
        summaries.append(SprintSummary(sprint=s, ticket_count=row[0], story_points_total=row[1]))

    return summaries


async def get_sprint(db: AsyncSession, user: User, project: Project, sprint_id: str) -> Sprint:
    """Sprint z ticketami (i ich assignee) preloaded, do formatowania przez wrapper."""
    await ensure_permission(db, user.id, project.id, "scrum", "read")
    result = await db.execute(
        select(Sprint)
        .options(selectinload(Sprint.tickets.and_(Ticket.project_id == project.id)).selectinload(Ticket.assignee))
        .where(
            Sprint.id == uuid.UUID(sprint_id),
            Sprint.project_id == project.id,
        )
    )
    sprint = result.scalar_one_or_none()
    if sprint is None:
        raise ValueError("Sprint nie istnieje")
    return sprint


async def create_sprint(
    db: AsyncSession,
    user: User,
    project: Project,
    name: str,
    start_date: str,
    goal: str | None = None,
    end_date: str | None = None,
) -> Sprint:
    """Utworz nowy sprint (status: planning)."""
    await ensure_permission(db, user.id, project.id, "scrum", "write")
    if not name.strip():
        raise ValueError("Nazwa sprintu jest wymagana")

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
    return sprint


async def update_sprint(
    db: AsyncSession,
    user: User,
    project: Project,
    sprint_id: str,
    name: str | None = None,
    goal: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> Sprint:
    """Aktualizuj istniejacy sprint (PATCH semantics).

    Zmiana dat jest zablokowana dla sprintow o statusie 'completed'.
    Walidacja: end_date musi byc pozniejsza niz start_date.
    """
    await ensure_permission(db, user.id, project.id, "scrum", "write")
    result = await db.execute(
        select(Sprint).where(
            Sprint.id == uuid.UUID(sprint_id),
            Sprint.project_id == project.id,
        )
    )
    sprint = result.scalar_one_or_none()
    if sprint is None:
        raise ValueError("Sprint nie istnieje")

    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Nazwa sprintu nie moze byc pusta")
        if len(name) > 255:
            raise ValueError("Nazwa sprintu nie moze przekraczac 255 znakow")
        sprint.name = name

    if goal is not None:
        sprint.goal = goal.strip() or None

    if start_date is not None or end_date is not None:
        if sprint.status == "completed":
            raise ValueError("Nie mozna zmienic dat zakonczonego sprintu")

        new_start = date.fromisoformat(start_date) if start_date is not None else sprint.start_date
        new_end = None if end_date == "" else (date.fromisoformat(end_date) if end_date is not None else sprint.end_date)

        if new_end is not None and new_end <= new_start:
            raise ValueError("Data zakonczenia musi byc pozniejsza niz data rozpoczecia")

        if start_date is not None:
            sprint.start_date = new_start
        if end_date is not None:
            sprint.end_date = new_end

    await db.commit()
    await db.refresh(sprint)
    return sprint
