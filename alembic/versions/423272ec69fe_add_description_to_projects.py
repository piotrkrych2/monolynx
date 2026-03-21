"""add description to projects

Revision ID: 423272ec69fe
Revises: 7c5d4f074843
Create Date: 2026-03-14 13:12:58.778717

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '423272ec69fe'
down_revision: str | None = '7c5d4f074843'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('description', sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column('projects', 'description')
