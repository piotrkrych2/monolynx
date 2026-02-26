"""add ticket numbering (project code + ticket number)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-25 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 1: Add nullable columns
    op.add_column("projects", sa.Column("code", sa.String(10), nullable=True))
    op.add_column("tickets", sa.Column("number", sa.Integer(), nullable=True))

    # Phase 2: Populate existing data
    # Set project code from uppercase slug (first part before hyphen, max 10 chars)
    op.execute("""
        UPDATE projects
        SET code = UPPER(SUBSTRING(slug FROM 1 FOR LEAST(LENGTH(SPLIT_PART(slug, '-', 1)), 10)))
        WHERE code IS NULL
    """)

    # Set ticket numbers per project (ordered by created_at)
    op.execute("""
        UPDATE tickets SET number = sub.rn
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY project_id ORDER BY created_at) AS rn
            FROM tickets
        ) sub
        WHERE tickets.id = sub.id AND tickets.number IS NULL
    """)

    # Phase 3: Set NOT NULL and add constraints
    op.alter_column("projects", "code", nullable=False)
    op.alter_column("tickets", "number", nullable=False)
    op.create_unique_constraint("uq_project_code", "projects", ["code"])
    op.create_unique_constraint("uq_ticket_project_number", "tickets", ["project_id", "number"])


def downgrade() -> None:
    op.drop_constraint("uq_ticket_project_number", "tickets", type_="unique")
    op.drop_constraint("uq_project_code", "projects", type_="unique")
    op.drop_column("tickets", "number")
    op.drop_column("projects", "code")
