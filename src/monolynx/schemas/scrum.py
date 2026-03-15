"""Schematy Pydantic dla modulu Scrum."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field

from monolynx.constants import PRIORITIES, TICKET_STATUSES


class TicketCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    priority: str = "medium"
    story_points: int | None = None
    sprint_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    due_date: date | None = None

    def validate_priority(self) -> bool:
        return self.priority in PRIORITIES


class TicketUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    priority: str | None = None
    story_points: int | None = None
    sprint_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    status: str | None = None
    due_date: date | None = None

    def validate_status(self) -> bool:
        return self.status is None or self.status in TICKET_STATUSES

    def validate_priority(self) -> bool:
        return self.priority is None or self.priority in PRIORITIES


class TicketStatusUpdate(BaseModel):
    status: str

    def validate_status(self) -> bool:
        return self.status in TICKET_STATUSES


class SprintCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    goal: str | None = None
    start_date: date
    end_date: date | None = None


class MemberAdd(BaseModel):
    email: str = Field(min_length=1)
    role: str = "member"
