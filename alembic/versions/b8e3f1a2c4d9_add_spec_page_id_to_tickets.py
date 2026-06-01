"""add spec_page_id to tickets

Revision ID: b8e3f1a2c4d9
Revises: f2a3b4c5d6e7
Create Date: 2026-05-29 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8e3f1a2c4d9'
down_revision: str | None = 'f2a3b4c5d6e7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('tickets', sa.Column('spec_page_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f('ix_tickets_spec_page_id'), 'tickets', ['spec_page_id'], unique=False)
    op.create_foreign_key(
        'fk_tickets_spec_page_id_wiki_pages',
        'tickets',
        'wiki_pages',
        ['spec_page_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_tickets_spec_page_id_wiki_pages', 'tickets', type_='foreignkey')
    op.drop_index(op.f('ix_tickets_spec_page_id'), table_name='tickets')
    op.drop_column('tickets', 'spec_page_id')
