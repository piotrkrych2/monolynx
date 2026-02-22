"""monitoring tables

Revision ID: 1bdf28994010
Revises: dff18bdf0cf7
Create Date: 2026-02-21 18:44:12.105473

"""
from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = '1bdf28994010'
down_revision: Union[str, None] = 'dff18bdf0cf7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Migracja wygenerowana na pustej bazie -- wszystkie tabele juz istnieja
    # z poprzednich migracji. Noop.
    pass


def downgrade() -> None:
    pass
