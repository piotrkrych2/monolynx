"""Schematy Pydantic dla modulu planu pracy."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from monolynx.models.work_plan import WorkPlanEntry


class WorkPlanEntryCreate(BaseModel):
    """Schemat tworzenia wpisu planu pracy."""

    ticket_id: UUID
    scheduled_date: date
    position: int = Field(default=0, ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class WorkPlanEntryUpdate(BaseModel):
    """Schemat aktualizacji wpisu planu pracy (PATCH)."""

    scheduled_date: date | None = None
    position: int | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=1000)


class WorkPlanEntryResponse(BaseModel):
    """Schemat odpowiedzi API z wpisem planu pracy."""

    id: UUID
    user_id: UUID
    ticket_id: UUID
    ticket_key: str
    ticket_title: str
    project_id: UUID
    project_slug: str
    project_name: str
    scheduled_date: date
    position: int
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_entry(cls, entry: WorkPlanEntry) -> WorkPlanEntryResponse:
        """Buduje response z eagerly-loaded entry (ticket + project musza byc zaladowane)."""
        ticket = entry.ticket
        project = ticket.project
        return cls(
            id=entry.id,
            user_id=entry.user_id,
            ticket_id=entry.ticket_id,
            ticket_key=ticket.key,
            ticket_title=ticket.title,
            project_id=project.id,
            project_slug=project.slug,
            project_name=project.name,
            scheduled_date=entry.scheduled_date,
            position=entry.position,
            notes=entry.notes,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
        )
