"""Serwis sprintow -- logika cyklu zycia sprintu."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from open_sentry.models.sprint import Sprint
from open_sentry.models.ticket import Ticket


async def start_sprint(sprint_id: uuid.UUID, project_id: uuid.UUID, db: AsyncSession) -> str | None:
    """Rozpoczyna sprint. Zwraca blad lub None."""
    # Sprawdz czy nie ma innego aktywnego sprintu
    result = await db.execute(
        select(Sprint).where(
            Sprint.project_id == project_id,
            Sprint.status == "active",
        )
    )
    active = result.scalar_one_or_none()
    if active is not None:
        return f"Projekt ma juz aktywny sprint: {active.name}"

    result = await db.execute(select(Sprint).where(Sprint.id == sprint_id, Sprint.project_id == project_id))
    sprint = result.scalar_one_or_none()
    if sprint is None:
        return "Sprint nie istnieje"

    if sprint.status != "planning":
        return "Mozna wystartowac tylko sprint w fazie planowania"

    sprint.status = "active"
    await db.commit()
    return None


async def complete_sprint(sprint_id: uuid.UUID, project_id: uuid.UUID, db: AsyncSession) -> str | None:
    """Konczy sprint. Niedokonczone tickety wracaja do backloga.

    Zwraca blad lub None.
    """
    result = await db.execute(select(Sprint).where(Sprint.id == sprint_id, Sprint.project_id == project_id))
    sprint = result.scalar_one_or_none()
    if sprint is None:
        return "Sprint nie istnieje"

    if sprint.status != "active":
        return "Mozna zakonczyc tylko aktywny sprint"

    # Niedokonczone tickety wracaja do backloga
    await db.execute(
        update(Ticket)
        .where(
            Ticket.sprint_id == sprint_id,
            Ticket.status != "done",
        )
        .values(sprint_id=None, status="backlog")
    )

    sprint.status = "completed"
    await db.commit()
    return None
