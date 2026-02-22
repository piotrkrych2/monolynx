"""Model Monitor -- monitorowany URL."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from open_sentry.models.base import Base

if TYPE_CHECKING:
    from open_sentry.models.monitor_check import MonitorCheck
    from open_sentry.models.project import Project


class Monitor(Base):
    __tablename__ = "monitors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    interval_value: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    interval_unit: Mapped[str] = mapped_column(String(20), nullable=False, default="minutes")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="monitors")
    checks: Mapped[list[MonitorCheck]] = relationship(back_populates="monitor", cascade="all, delete-orphan")
