"""add_settlements_module

Revision ID: 17420ab13509
Revises: f1a2b3c4d5e6
Create Date: 2026-04-08 10:35:34.167714

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "17420ab13509"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Tabela settlements
    op.create_table(
        "settlements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.UUID(), nullable=False),
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
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("number", name="uq_settlements_number"),
    )
    # Index na number — przyśpiesza MAX query w get_next_settlement_number
    op.create_index(op.f("ix_settlements_number"), "settlements", ["number"], unique=True)

    # 2. Tabela settlement_attachments
    op.create_table(
        "settlement_attachments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("settlement_id", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.Column("state", sa.String(length=10), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["settlement_id"], ["settlements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_settlement_attachments_settlement_id"),
        "settlement_attachments",
        ["settlement_id"],
        unique=False,
    )

    # 3. Tabela asocjacyjna settlement_projects
    op.create_table(
        "settlement_projects",
        sa.Column("settlement_id", sa.UUID(), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["settlement_id"], ["settlements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("settlement_id", "project_id"),
    )

    # 4. Tabela asocjacyjna settlement_tickets
    op.create_table(
        "settlement_tickets",
        sa.Column("settlement_id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["settlement_id"], ["settlements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("settlement_id", "ticket_id"),
    )

    # 5. Data migration: dodaj klucz 'rozliczenia' do istniejących ról systemowych
    op.execute(
        """
        UPDATE roles
        SET permissions = jsonb_set(permissions, '{rozliczenia}', '["read", "write", "delete"]'::jsonb)
        WHERE is_system = true AND name = 'Owner'
        """
    )
    op.execute(
        """
        UPDATE roles
        SET permissions = jsonb_set(permissions, '{rozliczenia}', '["read", "write"]'::jsonb)
        WHERE is_system = true AND name = 'Admin'
        """
    )
    op.execute(
        """
        UPDATE roles
        SET permissions = jsonb_set(permissions, '{rozliczenia}', '[]'::jsonb)
        WHERE is_system = true AND name = 'Member'
        """
    )


def downgrade() -> None:
    # 1. Usuń klucz 'rozliczenia' ze wszystkich ról (przed drop_table)
    op.execute("UPDATE roles SET permissions = permissions - 'rozliczenia'")

    # 2. Drop tabel w odwrotnej kolejności (association tables przed settlements)
    op.drop_table("settlement_tickets")
    op.drop_table("settlement_projects")
    op.drop_index(op.f("ix_settlement_attachments_settlement_id"), table_name="settlement_attachments")
    op.drop_table("settlement_attachments")
    op.drop_index(op.f("ix_settlements_number"), table_name="settlements")
    op.drop_table("settlements")
