"""add roles table and rbac

Revision ID: f1a2b3c4d5e6
Revises: 3970da934c22
Create Date: 2026-04-07 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "3970da934c22"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OWNER_PERMS = (
    '{"500ki": ["read", "write", "delete"],'
    ' "scrum": ["read", "write", "delete"],'
    ' "monitoring": ["read", "write", "delete"],'
    ' "heartbeat": ["read", "write", "delete"],'
    ' "wiki": ["read", "write", "delete"],'
    ' "connections": ["read", "write", "delete"],'
    ' "settings": ["read", "write", "delete"],'
    ' "reports": ["read", "write", "delete"],'
    ' "users": ["read", "write", "delete"]}'
)

_ADMIN_PERMS = (
    '{"500ki": ["read", "write", "delete"],'
    ' "scrum": ["read", "write", "delete"],'
    ' "monitoring": ["read", "write", "delete"],'
    ' "heartbeat": ["read", "write", "delete"],'
    ' "wiki": ["read", "write", "delete"],'
    ' "connections": ["read", "write", "delete"],'
    ' "settings": ["read", "write"],'
    ' "reports": ["read", "write", "delete"],'
    ' "users": ["read", "write"]}'
)

_MEMBER_PERMS = (
    '{"500ki": ["read"],'
    ' "scrum": ["read", "write"],'
    ' "monitoring": ["read"],'
    ' "heartbeat": ["read"],'
    ' "wiki": ["read", "write"],'
    ' "connections": ["read"],'
    ' "settings": ["read"],'
    ' "reports": ["read"],'
    ' "users": ["read"]}'
)


def upgrade() -> None:
    # 1. Utwórz tabelę roles
    op.create_table(
        "roles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("project_id", sa.UUID(), nullable=True),
        sa.Column(
            "permissions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_role_project_name"),
    )
    op.create_index(op.f("ix_roles_project_id"), "roles", ["project_id"], unique=False)

    # 2. Dodaj kolumnę role_id do project_members (kolumna `role` pozostaje bez zmian)
    op.add_column(
        "project_members",
        sa.Column("role_id", sa.UUID(), nullable=True),
    )
    op.create_index(op.f("ix_project_members_role_id"), "project_members", ["role_id"], unique=False)
    op.create_foreign_key(
        "fk_project_members_role_id",
        "project_members",
        "roles",
        ["role_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. Data migration: utwórz 3 systemowe role dla każdego projektu
    op.execute(
        f"""
        INSERT INTO roles (id, name, project_id, permissions, is_system, created_at)
        SELECT gen_random_uuid(), 'Owner', p.id, '{_OWNER_PERMS}'::jsonb, true, now()
        FROM projects p
        """
    )
    op.execute(
        f"""
        INSERT INTO roles (id, name, project_id, permissions, is_system, created_at)
        SELECT gen_random_uuid(), 'Admin', p.id, '{_ADMIN_PERMS}'::jsonb, true, now()
        FROM projects p
        """
    )
    op.execute(
        f"""
        INSERT INTO roles (id, name, project_id, permissions, is_system, created_at)
        SELECT gen_random_uuid(), 'Member', p.id, '{_MEMBER_PERMS}'::jsonb, true, now()
        FROM projects p
        """
    )

    # 4. Przypisz role_id na podstawie istniejącego pola role
    op.execute(
        """
        UPDATE project_members pm
        SET role_id = r.id
        FROM roles r
        WHERE r.project_id = pm.project_id
          AND r.is_system = true
          AND r.name = CASE pm.role
              WHEN 'owner' THEN 'Owner'
              WHEN 'admin' THEN 'Admin'
              ELSE 'Member'
          END
        """
    )


def downgrade() -> None:
    # Usuń FK i index role_id
    op.drop_constraint("fk_project_members_role_id", "project_members", type_="foreignkey")
    op.drop_index(op.f("ix_project_members_role_id"), table_name="project_members")
    op.drop_column("project_members", "role_id")

    # Usuń tabelę roles
    op.drop_index(op.f("ix_roles_project_id"), table_name="roles")
    op.drop_table("roles")
