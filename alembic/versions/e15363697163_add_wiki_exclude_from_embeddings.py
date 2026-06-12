"""add wiki exclude_from_embeddings

Revision ID: e15363697163
Revises: 22b9a0d31970
Create Date: 2026-06-12 20:12:01.670349

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e15363697163'
down_revision: Union[str, None] = '22b9a0d31970'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'wiki_pages',
        sa.Column('exclude_from_embeddings', sa.Boolean(), server_default='false', nullable=False),
    )


def downgrade() -> None:
    op.drop_column('wiki_pages', 'exclude_from_embeddings')
