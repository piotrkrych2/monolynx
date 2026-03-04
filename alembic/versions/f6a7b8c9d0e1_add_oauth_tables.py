"""add OAuth 2.1 tables

Revision ID: f6a7b8c9d0e1
Revises: 3e87493c1b6f
Create Date: 2026-03-03 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "3e87493c1b6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # oauth_clients
    op.create_table(
        "oauth_clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", sa.String(255), unique=True, nullable=False),
        sa.Column("client_name", sa.String(255), nullable=True),
        sa.Column("redirect_uris", postgresql.JSON(), nullable=False),
        sa.Column("grant_types", postgresql.JSON(), nullable=False),
        sa.Column("client_secret", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_oauth_clients_client_id", "oauth_clients", ["client_id"], unique=True)

    # oauth_authorization_codes
    op.create_table(
        "oauth_authorization_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(255), unique=True, nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("redirect_uri", sa.String(2048), nullable=False),
        sa.Column("scope", sa.String(255), nullable=True),
        sa.Column("code_challenge", sa.String(255), nullable=False),
        sa.Column("code_challenge_method", sa.String(10), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_oauth_authorization_codes_code",
        "oauth_authorization_codes",
        ["code"],
        unique=True,
    )
    op.create_index(
        "ix_oauth_authorization_codes_user_id",
        "oauth_authorization_codes",
        ["user_id"],
    )

    # oauth_access_tokens
    op.create_table(
        "oauth_access_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(255), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_oauth_access_tokens_token_hash",
        "oauth_access_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_oauth_access_tokens_user_id",
        "oauth_access_tokens",
        ["user_id"],
    )

    # oauth_refresh_tokens
    op.create_table(
        "oauth_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
        sa.Column(
            "access_token_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("oauth_access_tokens.id"),
            nullable=False,
        ),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), default=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_oauth_refresh_tokens_token_hash",
        "oauth_refresh_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_oauth_refresh_tokens_user_id",
        "oauth_refresh_tokens",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_oauth_refresh_tokens_user_id", table_name="oauth_refresh_tokens")
    op.drop_index("ix_oauth_refresh_tokens_token_hash", table_name="oauth_refresh_tokens")
    op.drop_table("oauth_refresh_tokens")

    op.drop_index("ix_oauth_access_tokens_user_id", table_name="oauth_access_tokens")
    op.drop_index("ix_oauth_access_tokens_token_hash", table_name="oauth_access_tokens")
    op.drop_table("oauth_access_tokens")

    op.drop_index("ix_oauth_authorization_codes_user_id", table_name="oauth_authorization_codes")
    op.drop_index("ix_oauth_authorization_codes_code", table_name="oauth_authorization_codes")
    op.drop_table("oauth_authorization_codes")

    op.drop_index("ix_oauth_clients_client_id", table_name="oauth_clients")
    op.drop_table("oauth_clients")
