"""monitoring tables

Revision ID: 1bdf28994010
Revises: dff18bdf0cf7
Create Date: 2026-02-21 18:44:12.105473

"""
from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = '1bdf28994010'
down_revision: str | None = 'dff18bdf0cf7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Migracja wygenerowana na pustej bazie -- wszystkie tabele juz istnieja
    # z poprzednich migracji. Noop.
    pass


def downgrade() -> None:
    pass
