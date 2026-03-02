"""Model Issue -- zgrupowany blad (fingerprint)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.event import Event
    from monolynx.models.project import Project
    from monolynx.models.ticket import Ticket


class Issue(Base):
    __tablename__ = "issues"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    culprit: Mapped[str | None] = mapped_column(String(512), nullable=True)
    level: Mapped[str] = mapped_column(String(20), default="error")
    status: Mapped[str] = mapped_column(String(20), default="unresolved", index=True)
    event_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="issues")
    events: Mapped[list[Event]] = relationship(back_populates="issue", order_by="desc(Event.timestamp)")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="issue", lazy="selectin", passive_deletes=True)

    __table_args__ = (UniqueConstraint("project_id", "fingerprint", name="uq_project_fingerprint"),)
