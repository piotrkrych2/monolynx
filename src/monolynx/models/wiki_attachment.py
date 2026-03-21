"""Model WikiAttachment -- zalaczniki do stron wiki."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.wiki_page import WikiPage


class WikiAttachment(Base):
    __tablename__ = "wiki_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wiki_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_via_ai: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    wiki_page: Mapped[WikiPage] = relationship(back_populates="attachments")
