"""add issue_id to tickets

Revision ID: 3e87493c1b6f
Revises: e5f6a7b8c9d0
Create Date: 2026-03-02 10:33:29.922480

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '3e87493c1b6f'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tickets', sa.Column('issue_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index(op.f('ix_tickets_issue_id'), 'tickets', ['issue_id'], unique=False)
    op.create_foreign_key(
        'fk_tickets_issue_id_issues',
        'tickets',
        'issues',
        ['issue_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_tickets_issue_id_issues', 'tickets', type_='foreignkey')
    op.drop_index(op.f('ix_tickets_issue_id'), table_name='tickets')
    op.drop_column('tickets', 'issue_id')
