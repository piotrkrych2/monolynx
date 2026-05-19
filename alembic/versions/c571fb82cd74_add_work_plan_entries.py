"""add_work_plan_entries

Revision ID: c571fb82cd74
Revises: 17420ab13509
Create Date: 2026-05-19 08:15:40.569213

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c571fb82cd74"
down_revision: str | None = "17420ab13509"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_plan_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("scheduled_date", sa.Date(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "ticket_id",
            "scheduled_date",
            name="uq_work_plan_user_ticket_date",
        ),
    )
    op.create_index("ix_work_plan_ticket", "work_plan_entries", ["ticket_id"], unique=False)
    op.create_index(
        "ix_work_plan_user_date",
        "work_plan_entries",
        ["user_id", "scheduled_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_work_plan_user_date", table_name="work_plan_entries")
    op.drop_index("ix_work_plan_ticket", table_name="work_plan_entries")
    op.drop_table("work_plan_entries")
