"""add wiki_attachment and wiki_file tables

Revision ID: 1694c9ca760f
Revises: e48df4ceb99e
Create Date: 2026-03-21 11:01:10.160868

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1694c9ca760f"
down_revision: str | None = "e48df4ceb99e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wiki_files",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_via_ai", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_wiki_files_project_id"), "wiki_files", ["project_id"], unique=False)
    op.create_table(
        "wiki_attachments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("wiki_page_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_via_ai", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["wiki_page_id"], ["wiki_pages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_wiki_attachments_wiki_page_id"), "wiki_attachments", ["wiki_page_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_wiki_attachments_wiki_page_id"), table_name="wiki_attachments")
    op.drop_table("wiki_attachments")
    op.drop_index(op.f("ix_wiki_files_project_id"), table_name="wiki_files")
    op.drop_table("wiki_files")
