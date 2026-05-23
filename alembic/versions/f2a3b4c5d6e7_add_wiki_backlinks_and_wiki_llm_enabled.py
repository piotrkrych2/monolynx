"""add wiki_backlinks table and wiki_llm_enabled flag

Revision ID: f2a3b4c5d6e7
Revises: c571fb82cd74
Create Date: 2026-05-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "c571fb82cd74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Tabela wiki_backlinks
    op.create_table(
        "wiki_backlinks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "source_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "target_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("anchor_text", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Indeksy złożone z __table_args__ modelu
    op.create_index(
        "ix_wiki_backlinks_source_target",
        "wiki_backlinks",
        ["source_page_id", "target_page_id"],
        unique=False,
    )
    op.create_index("ix_wiki_backlinks_target", "wiki_backlinks", ["target_page_id"], unique=False)

    # 2. Flaga opt-in LLM Wiki na projektach
    op.add_column(
        "projects",
        sa.Column("wiki_llm_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    # Odwrotna kolejność
    op.drop_column("projects", "wiki_llm_enabled")

    op.drop_index("ix_wiki_backlinks_target", table_name="wiki_backlinks")
    op.drop_index("ix_wiki_backlinks_source_target", table_name="wiki_backlinks")
    op.drop_table("wiki_backlinks")
