"""add_due_date_to_tickets

Revision ID: fcbd8345f9bf
Revises: 423272ec69fe
Create Date: 2026-03-14 15:46:53.129641

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'fcbd8345f9bf'
down_revision: str | None = '423272ec69fe'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('tickets', sa.Column('due_date', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('tickets', 'due_date')
