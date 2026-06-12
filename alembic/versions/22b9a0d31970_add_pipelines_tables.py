"""add pipelines tables

Revision ID: 22b9a0d31970
Revises: b8e3f1a2c4d9
Create Date: 2026-06-12 20:00:38.554837

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '22b9a0d31970'
down_revision: Union[str, None] = 'b8e3f1a2c4d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'pipelines',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('project_id', sa.UUID(), nullable=False),
        sa.Column('pipeline_type', sa.String(length=32), nullable=False),
        sa.Column('ticket_id', sa.UUID(), nullable=True),
        sa.Column('sprint_id', sa.UUID(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('branch', sa.String(length=255), nullable=True),
        sa.Column('triggered_by', sa.UUID(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id']),
        sa.ForeignKeyConstraint(['sprint_id'], ['sprints.id']),
        sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id']),
        sa.ForeignKeyConstraint(['triggered_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pipelines_project_id'), 'pipelines', ['project_id'], unique=False)
    op.create_index(op.f('ix_pipelines_status'), 'pipelines', ['status'], unique=False)
    op.create_table(
        'pipeline_steps',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('pipeline_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=32), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['pipeline_id'], ['pipelines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pipeline_steps_pipeline_id'), 'pipeline_steps', ['pipeline_id'], unique=False)
    op.create_table(
        'pipeline_jobs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('step_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('agent_type', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('wiki_page_id', sa.UUID(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['step_id'], ['pipeline_steps.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['wiki_page_id'], ['wiki_pages.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_pipeline_jobs_step_id'), 'pipeline_jobs', ['step_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pipeline_jobs_step_id'), table_name='pipeline_jobs')
    op.drop_table('pipeline_jobs')
    op.drop_index(op.f('ix_pipeline_steps_pipeline_id'), table_name='pipeline_steps')
    op.drop_table('pipeline_steps')
    op.drop_index(op.f('ix_pipelines_status'), table_name='pipelines')
    op.drop_index(op.f('ix_pipelines_project_id'), table_name='pipelines')
    op.drop_table('pipelines')
