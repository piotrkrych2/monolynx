"""Model ActivityLog -- historia zmian w projekcie."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.project import Project
    from monolynx.models.user import User


class ActivityLog(Base):
    """Wpis logu aktywnosci -- audyt operacji w projekcie."""

    __tablename__ = "activity_log"
    __table_args__ = (Index("ix_activity_log_project_created_at", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    entity_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationships
    project: Mapped[Project] = relationship()
    actor: Mapped[User | None] = relationship()
