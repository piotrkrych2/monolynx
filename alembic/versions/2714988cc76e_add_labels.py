"""add labels

Revision ID: 2714988cc76e
Revises: 1646e3dd1199
Create Date: 2026-03-14 20:06:36.649085

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2714988cc76e'
down_revision: Union[str, None] = '1646e3dd1199'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'labels',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('color', sa.String(length=7), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'name', name='uq_label_project_name'),
    )
    op.create_index(op.f('ix_labels_project_id'), 'labels', ['project_id'], unique=False)
    op.create_table(
        'ticket_labels',
        sa.Column('ticket_id', sa.UUID(), nullable=False),
        sa.Column('label_id', sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(['label_id'], ['labels.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('ticket_id', 'label_id'),
    )


def downgrade() -> None:
    op.drop_table('ticket_labels')
    op.drop_index(op.f('ix_labels_project_id'), table_name='labels')
    op.drop_table('labels')
