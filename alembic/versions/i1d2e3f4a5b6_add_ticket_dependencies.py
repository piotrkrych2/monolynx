"""add ticket_dependencies

Revision ID: i1d2e3f4a5b6
Revises: h1c2d3e4f5a6
Create Date: 2026-09-23 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'i1d2e3f4a5b6'
down_revision: str | None = 'h1c2d3e4f5a6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'ticket_dependencies',
        sa.Column('ticket_id', sa.UUID(), nullable=False),
        sa.Column('blocker_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['blocker_id'], ['tickets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('ticket_id', 'blocker_id'),
        sa.CheckConstraint('ticket_id <> blocker_id', name='ck_ticket_dependency_no_self_reference'),
    )
    # Klucz glowny zlozony (ticket_id, blocker_id) indeksuje pary od ticket_id, nie przyspiesza
    # wyszukiwania samym blocker_id (Ticket.blocking filtruje po tej kolumnie) -- wlasny indeks.
    op.create_index('ix_ticket_dependencies_blocker_id', 'ticket_dependencies', ['blocker_id'])


def downgrade() -> None:
    op.drop_index('ix_ticket_dependencies_blocker_id', table_name='ticket_dependencies')
    op.drop_table('ticket_dependencies')
