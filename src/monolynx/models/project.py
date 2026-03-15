"""Model Project -- reprezentuje monitorowana aplikacje."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.heartbeat import Heartbeat
    from monolynx.models.issue import Issue
    from monolynx.models.monitor import Monitor
    from monolynx.models.project_member import ProjectMember
    from monolynx.models.sprint import Sprint
    from monolynx.models.ticket import Ticket
    from monolynx.models.wiki_page import WikiPage


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True, default=None)
    api_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    heartbeats: Mapped[list[Heartbeat]] = relationship(back_populates="project", cascade="all, delete-orphan")
    issues: Mapped[list[Issue]] = relationship(back_populates="project")
    members: Mapped[list[ProjectMember]] = relationship(back_populates="project")
    monitors: Mapped[list[Monitor]] = relationship(back_populates="project")
    sprints: Mapped[list[Sprint]] = relationship(back_populates="project")
    tickets: Mapped[list[Ticket]] = relationship(back_populates="project")
    wiki_pages: Mapped[list[WikiPage]] = relationship(back_populates="project")
