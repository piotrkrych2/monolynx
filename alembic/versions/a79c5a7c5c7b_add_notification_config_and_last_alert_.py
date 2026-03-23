"""add notification_config and last_alert_sent_at to monitors

Revision ID: a79c5a7c5c7b
Revises: 1694c9ca760f
Create Date: 2026-03-23 21:15:41.870704

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a79c5a7c5c7b'
down_revision: Union[str, None] = '1694c9ca760f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('monitors', sa.Column('notification_config', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False))
    op.add_column('monitors', sa.Column('last_alert_sent_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('monitors', 'last_alert_sent_at')
    op.drop_column('monitors', 'notification_config')
