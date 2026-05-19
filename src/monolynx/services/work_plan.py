"""Serwis planu pracy -- logika zarzadzania wpisami harmonogramu."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.models.work_plan import WorkPlanEntry

# Sentinel oznaczajacy "pole nie zostalo podane w PATCH" (inny niz None = "wyczysc")
_UNSET: Any = object()


async def schedule(
    db: AsyncSession,
    user_id: uuid.UUID,
    ticket_id: uuid.UUID,
    scheduled_date: date,
    position: int = 0,
    notes: str | None = None,
) -> WorkPlanEntry | str:
    """Tworzy wpis planu pracy dla uzytkownika. Zwraca entry lub komunikat bledu."""
    # Pobierz ticket z projektem (tylko aktywny projekt)
    result = await db.execute(
        select(Ticket)
        .options(selectinload(Ticket.project))
        .join(Project, Ticket.project_id == Project.id)
        .where(Ticket.id == ticket_id, Project.is_active.is_(True))
    )
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return "Ticket nie istnieje"

    # Sprawdz czlonkostwo w projekcie
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == ticket.project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        return "Uzytkownik nie jest czlonkiem projektu"

    entry = WorkPlanEntry(
        user_id=user_id,
        ticket_id=ticket_id,
        scheduled_date=scheduled_date,
        position=position,
        notes=notes,
    )
    db.add(entry)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return "Ticket juz zaplanowany na ten dzien"

    # Przeladuj z relacjami
    await db.refresh(entry)
    result2 = await db.execute(
        select(WorkPlanEntry).options(selectinload(WorkPlanEntry.ticket).selectinload(Ticket.project)).where(WorkPlanEntry.id == entry.id)
    )
    loaded = result2.scalar_one()
    return loaded


async def update(
    db: AsyncSession,
    user_id: uuid.UUID,
    entry_id: uuid.UUID,
    scheduled_date: date | None = None,
    position: int | None = None,
    notes: Any = _UNSET,
) -> WorkPlanEntry | str:
    """Aktualizuje wpis planu pracy (PATCH). Zwraca entry lub komunikat bledu.

    notes=_UNSET (domyslnie) -- nie zmieniaj pola.
    notes=None -- wyczysc do NULL.
    notes="tekst" -- ustaw wartosc.
    """
    result = await db.execute(
        select(WorkPlanEntry).options(selectinload(WorkPlanEntry.ticket).selectinload(Ticket.project)).where(WorkPlanEntry.id == entry_id)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        return "Entry nie znaleziony"

    if entry.user_id != user_id:
        return "Brak dostepu"

    if scheduled_date is not None:
        entry.scheduled_date = scheduled_date
    if position is not None:
        entry.position = position
    if notes is not _UNSET:
        entry.notes = notes

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return "Ticket juz zaplanowany na ten dzien"

    result2 = await db.execute(
        select(WorkPlanEntry).options(selectinload(WorkPlanEntry.ticket).selectinload(Ticket.project)).where(WorkPlanEntry.id == entry.id)
    )
    return result2.scalar_one()


async def unschedule(db: AsyncSession, user_id: uuid.UUID, entry_id: uuid.UUID) -> str | None:
    """Usuwa wpis planu pracy. Zwraca None na sukces lub komunikat bledu."""
    result = await db.execute(select(WorkPlanEntry).where(WorkPlanEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if entry is None:
        return "Entry nie znaleziony"

    if entry.user_id != user_id:
        return "Brak dostepu"

    await db.delete(entry)
    await db.flush()
    return None


async def list_for_user_range(
    db: AsyncSession,
    user_id: uuid.UUID,
    start_date: date,
    end_date: date,
    project_ids: list[uuid.UUID] | None = None,
) -> list[WorkPlanEntry]:
    """Zwraca wpisy planu pracy dla uzytkownika w podanym zakresie dat."""
    if end_date < start_date:
        return []

    query = (
        select(WorkPlanEntry)
        .options(selectinload(WorkPlanEntry.ticket).selectinload(Ticket.project))
        .where(
            WorkPlanEntry.user_id == user_id,
            WorkPlanEntry.scheduled_date >= start_date,
            WorkPlanEntry.scheduled_date <= end_date,
        )
    )

    if project_ids:
        query = query.join(Ticket, WorkPlanEntry.ticket_id == Ticket.id).where(Ticket.project_id.in_(project_ids))

    query = query.order_by(WorkPlanEntry.scheduled_date, WorkPlanEntry.position)

    result = await db.execute(query)
    return list(result.scalars().all())


async def today_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[WorkPlanEntry]:
    """Zwraca wpisy planu pracy na dzisiaj dla uzytkownika."""
    today = date.today()
    return await list_for_user_range(db, user_id, today, today)


async def today_for_user_in_project(db: AsyncSession, user_id: uuid.UUID, project_id: uuid.UUID) -> list[WorkPlanEntry]:
    """Zwraca wpisy planu pracy na dzisiaj dla uzytkownika w danym projekcie."""
    today = date.today()
    return await list_for_user_range(db, user_id, today, today, project_ids=[project_id])


async def schedule_for_ticket(db: AsyncSession, user_id: uuid.UUID, ticket_id: uuid.UUID) -> list[WorkPlanEntry]:
    """Zwraca wszystkie wpisy danego uzytkownika dla konkretnego ticketu."""
    result = await db.execute(
        select(WorkPlanEntry)
        .options(selectinload(WorkPlanEntry.ticket).selectinload(Ticket.project))
        .where(
            WorkPlanEntry.user_id == user_id,
            WorkPlanEntry.ticket_id == ticket_id,
        )
        .order_by(WorkPlanEntry.scheduled_date, WorkPlanEntry.position)
    )
    return list(result.scalars().all())
