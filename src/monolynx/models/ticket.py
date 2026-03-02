"""Model Ticket -- zadanie Scrum (story, task, bug)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.issue import Issue
    from monolynx.models.project import Project
    from monolynx.models.sprint import Sprint
    from monolynx.models.ticket_comment import TicketComment
    from monolynx.models.time_tracking_entry import TimeTrackingEntry
    from monolynx.models.user import User


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("project_id", "number", name="uq_ticket_project_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    sprint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sprints.id"), nullable=True, index=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    issue_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="backlog", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    story_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_via_ai: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project: Mapped[Project] = relationship(back_populates="tickets")
    sprint: Mapped[Sprint | None] = relationship(back_populates="tickets")
    issue: Mapped[Issue | None] = relationship(back_populates="tickets", lazy="selectin")
    assignee: Mapped[User | None] = relationship()
    comments: Mapped[list[TicketComment]] = relationship(
        back_populates="ticket",
        order_by="TicketComment.created_at",
        cascade="all, delete-orphan",
    )
    time_entries: Mapped[list[TimeTrackingEntry]] = relationship(
        back_populates="ticket",
        order_by="TimeTrackingEntry.date_logged.desc()",
        cascade="all, delete-orphan",
    )

    @property
    def key(self) -> str:
        """Zwraca klucz JIRA-style np. PIM-1, LEP-99."""
        if self.project and self.project.code:
            return f"{self.project.code}-{self.number}"
        return f"?-{self.number}"
