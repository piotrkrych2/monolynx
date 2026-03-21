"""merge heartbeat and google_id heads

Revision ID: 7c5d4f074843
Revises: 7bb77af09979, g7b8c9d0e1f2
Create Date: 2026-03-12 15:12:00.324766

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = '7c5d4f074843'
down_revision: str | None = ('7bb77af09979', 'g7b8c9d0e1f2')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
