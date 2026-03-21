"""add user management fields

Revision ID: 28f96fce5af4
Revises: 85f88b78d72c
Create Date: 2026-02-20 06:38:37.075133

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '28f96fce5af4'
down_revision: str | None = '85f88b78d72c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('first_name', sa.String(length=100), nullable=False, server_default=''))
    op.add_column('users', sa.Column('last_name', sa.String(length=100), nullable=False, server_default=''))
    op.add_column('users', sa.Column('is_superuser', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.add_column('users', sa.Column('invitation_token', sa.UUID(), nullable=True))
    op.add_column('users', sa.Column('invitation_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=True)
    op.create_index(op.f('ix_users_invitation_token'), 'users', ['invitation_token'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_invitation_token'), table_name='users')
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=255), nullable=False)
    op.drop_column('users', 'invitation_expires_at')
    op.drop_column('users', 'invitation_token')
    op.drop_column('users', 'is_superuser')
    op.drop_column('users', 'last_name')
    op.drop_column('users', 'first_name')
