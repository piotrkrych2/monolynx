"""Model WikiPage -- strona wiki projektu."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.project import Project
    from monolynx.models.user import User
    from monolynx.models.wiki_embedding import WikiEmbedding


class WikiPage(Base):
    __tablename__ = "wiki_pages"
    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_wiki_page_project_slug"),
        Index("ix_wiki_pages_project_parent", "project_id", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    slug: Mapped[str] = mapped_column(String(512), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    minio_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    is_ai_touched: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    last_edited_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project: Mapped[Project] = relationship(back_populates="wiki_pages")
    parent: Mapped[WikiPage | None] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list[WikiPage]] = relationship(back_populates="parent", cascade="all, delete-orphan")
    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    last_edited_by: Mapped[User] = relationship(foreign_keys=[last_edited_by_id])
    embeddings: Mapped[list[WikiEmbedding]] = relationship(back_populates="wiki_page", cascade="all, delete-orphan")
