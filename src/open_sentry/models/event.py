"""Model Event -- pojedyncze wystapienie bledu."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from open_sentry.models.base import Base

if TYPE_CHECKING:
    from open_sentry.models.issue import Issue


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    issue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("issues.id"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    exception: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    request_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    environment: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    issue: Mapped[Issue] = relationship(back_populates="events")

    __table_args__ = (Index("ix_events_issue_timestamp", "issue_id", "timestamp"),)
