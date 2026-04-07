"""Model Role -- rola z uprawnieniami per projekt."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.project import Project
    from monolynx.models.project_member import ProjectMember


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_role_project_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    permissions: Mapped[dict[str, list[str]]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    project: Mapped[Project | None] = relationship(back_populates="roles")
    members: Mapped[list[ProjectMember]] = relationship(back_populates="role_obj", cascade="all, delete-orphan")
