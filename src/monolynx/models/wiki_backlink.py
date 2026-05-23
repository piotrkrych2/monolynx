"""Model WikiBacklink -- indeks linków między stronami wiki."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.wiki_page import WikiPage


class WikiBacklink(Base):
    __tablename__ = "wiki_backlinks"
    __table_args__ = (
        Index("ix_wiki_backlinks_source_target", "source_page_id", "target_page_id"),
        Index("ix_wiki_backlinks_target", "target_page_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False, index=True)
    target_page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False, index=True)
    anchor_text: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_page: Mapped[WikiPage] = relationship(foreign_keys=[source_page_id])
    target_page: Mapped[WikiPage] = relationship(foreign_keys=[target_page_id])
