"""add activity_log table

Revision ID: e48df4ceb99e
Revises: e19de60bf3c0
Create Date: 2026-03-14 20:59:27.455685

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e48df4ceb99e'
down_revision: Union[str, None] = 'e19de60bf3c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'activity_log',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('actor_id', sa.UUID(), nullable=True),
        sa.Column('actor_type', sa.String(length=20), nullable=False),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.String(length=36), nullable=False),
        sa.Column('entity_title', sa.String(length=512), nullable=True),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id']),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_activity_log_actor_id'), 'activity_log', ['actor_id'], unique=False)
    op.create_index('ix_activity_log_project_created_at', 'activity_log', ['project_id', 'created_at'], unique=False)
    op.create_index(op.f('ix_activity_log_project_id'), 'activity_log', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_activity_log_project_id'), table_name='activity_log')
    op.drop_index('ix_activity_log_project_created_at', table_name='activity_log')
    op.drop_index(op.f('ix_activity_log_actor_id'), table_name='activity_log')
    op.drop_table('activity_log')
