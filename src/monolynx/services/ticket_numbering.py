"""Serwis numeracji ticketow -- JIRA-style (PIM-1, PIM-99)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.project import Project
from monolynx.models.ticket import Ticket


async def get_next_ticket_number(project_id: uuid.UUID, db: AsyncSession) -> int:
    """Zwraca nastepny numer ticketu dla projektu. Thread-safe via row lock na Project."""
    # Blokujemy wiersz projektu zeby zapobiec race condition
    await db.execute(select(Project.id).where(Project.id == project_id).with_for_update())
    result = await db.execute(select(func.coalesce(func.max(Ticket.number), 0)).where(Ticket.project_id == project_id))
    current_max = result.scalar_one()
    return current_max + 1
