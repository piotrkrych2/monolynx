"""add source field to issue

Revision ID: 1646e3dd1199
Revises: fcbd8345f9bf
Create Date: 2026-03-14 20:02:39.335906

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1646e3dd1199'
down_revision: Union[str, None] = 'fcbd8345f9bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('issues', sa.Column('source', sa.String(length=20), server_default=sa.text("'auto'"), nullable=False))


def downgrade() -> None:
    op.drop_column('issues', 'source')
