"""add wiki_pages.is_public + partial unique index uq_wiki_public_slug

Revision ID: h1c2d3e4f5a6
Revises: e15363697163
Create Date: 2026-09-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'h1c2d3e4f5a6'
down_revision: Union[str, None] = 'e15363697163'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'wiki_pages',
        sa.Column('is_public', sa.Boolean(), server_default='false', nullable=False),
    )
    # Partial unique index (bez project_id) -- slug publicznej strony musi byc
    # globalnie unikalny, bo publiczny router GET /blog/{slug} nie zna project_slug (MON-201).
    op.create_index(
        'uq_wiki_public_slug',
        'wiki_pages',
        ['slug'],
        unique=True,
        postgresql_where=sa.text('is_public'),
    )


def downgrade() -> None:
    op.drop_index('uq_wiki_public_slug', table_name='wiki_pages')
    op.drop_column('wiki_pages', 'is_public')
