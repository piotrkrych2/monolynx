"""add ticket comments

Revision ID: 9531ee58f111
Revises: 28f96fce5af4
Create Date: 2026-02-20 07:41:08.740671

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9531ee58f111'
down_revision: str | None = '28f96fce5af4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('ticket_comments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('ticket_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ticket_comments_ticket_id'), 'ticket_comments', ['ticket_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ticket_comments_ticket_id'), table_name='ticket_comments')
    op.drop_table('ticket_comments')
