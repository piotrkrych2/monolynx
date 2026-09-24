"""Model TicketDependency -- relacja blocker miedzy ticketami (M2M self-referential)."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from monolynx.models.base import Base


class TicketDependency(Base):
    """Wiersz (ticket_id=A, blocker_id=B) oznacza: B blokuje A (A.blocked_by zawiera B, B.blocking zawiera A)."""

    __tablename__ = "ticket_dependencies"
    __table_args__ = (
        CheckConstraint("ticket_id <> blocker_id", name="ck_ticket_dependency_no_self_reference"),
        # Klucz glowny zlozony (ticket_id, blocker_id) indeksuje pary od ticket_id, nie przyspiesza
        # wyszukiwania samym blocker_id (Ticket.blocking filtruje po tej kolumnie) -- wlasny indeks.
        Index("ix_ticket_dependencies_blocker_id", "blocker_id"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    blocker_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
