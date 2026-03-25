"""add ticket acceptance criteria

Revision ID: 3970da934c22
Revises: a79c5a7c5c7b
Create Date: 2026-03-24 18:14:38.055064

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3970da934c22'
down_revision: Union[str, None] = 'a79c5a7c5c7b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ticket_acceptance_criteria',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('ticket_id', sa.UUID(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('is_completed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_by_user_id', sa.UUID(), nullable=False),
    sa.Column('completed_by_user_id', sa.UUID(), nullable=True),
    sa.Column('created_via_ai', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('completed_via_ai', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['completed_by_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ticket_acceptance_criteria_ticket_id'), 'ticket_acceptance_criteria', ['ticket_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ticket_acceptance_criteria_ticket_id'), table_name='ticket_acceptance_criteria')
    op.drop_table('ticket_acceptance_criteria')
