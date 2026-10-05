"""Model BlogPost -- metadane wpisu bloga 1:1 ze stroną wiki (MON-254)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from monolynx.models.base import Base


class BlogPost(Base):
    """Stabilny adres i metadane publicznego wpisu.

    `page_id` jest jednocześnie PK i FK, więc strona wiki ma co najwyżej jeden wpis. Publiczność
    wpisu nadal wyznacza `WikiPage.is_public`; wiersz sam niczego nie publikuje. `slug` jest
    unikalny globalnie (trasa /blog/{slug} nie zna projektu) i nie zmienia się wraz z tytułem
    strony; stare adresy trzyma `previous_slugs` (301 na aktualny).
    """

    __tablename__ = "blog_posts"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_blog_posts_slug"),
        CheckConstraint("lang IN ('pl', 'en')", name="ck_blog_posts_lang"),
    )

    page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wiki_pages.id", ondelete="CASCADE"), primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    previous_slugs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="", server_default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    author_name: Mapped[str] = mapped_column(String(255), nullable=False, default="Zespół Monolynx", server_default="Zespół Monolynx")
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    lang: Mapped[str] = mapped_column(String(2), nullable=False, default="pl", server_default="pl")
    translation_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("blog_posts.page_id", ondelete="SET NULL"), nullable=True
    )
    cover_attachment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wiki_attachments.id", ondelete="SET NULL"), nullable=True
    )
    last_verified_at: Mapped[date | None] = mapped_column(Date, nullable=True)
