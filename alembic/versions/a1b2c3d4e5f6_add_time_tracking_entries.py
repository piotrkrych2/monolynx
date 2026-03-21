"""add time_tracking_entries table

Revision ID: a1b2c3d4e5f6
Revises: de9a49f6e3ae
Create Date: 2026-02-25 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "de9a49f6e3ae"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "time_tracking_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("sprint_id", sa.UUID(), nullable=True),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("date_logged", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["sprint_id"], ["sprints.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_time_tracking_entries_ticket_id"), "time_tracking_entries", ["ticket_id"], unique=False)
    op.create_index(op.f("ix_time_tracking_entries_user_id"), "time_tracking_entries", ["user_id"], unique=False)
    op.create_index(op.f("ix_time_tracking_entries_sprint_id"), "time_tracking_entries", ["sprint_id"], unique=False)
    op.create_index(op.f("ix_time_tracking_entries_project_id"), "time_tracking_entries", ["project_id"], unique=False)
    op.create_index(op.f("ix_time_tracking_entries_date_logged"), "time_tracking_entries", ["date_logged"], unique=False)
    op.create_index("ix_time_tracking_entries_project_date", "time_tracking_entries", ["project_id", "date_logged"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_time_tracking_entries_project_date", table_name="time_tracking_entries")
    op.drop_index(op.f("ix_time_tracking_entries_date_logged"), table_name="time_tracking_entries")
    op.drop_index(op.f("ix_time_tracking_entries_project_id"), table_name="time_tracking_entries")
    op.drop_index(op.f("ix_time_tracking_entries_sprint_id"), table_name="time_tracking_entries")
    op.drop_index(op.f("ix_time_tracking_entries_user_id"), table_name="time_tracking_entries")
    op.drop_index(op.f("ix_time_tracking_entries_ticket_id"), table_name="time_tracking_entries")
    op.drop_table("time_tracking_entries")
