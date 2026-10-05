"""add blog_posts (metadane wpisu bloga 1:1 ze strona wiki)

Revision ID: j1e2f3a4b5c6
Revises: i1d2e3f4a5b6
Create Date: 2026-10-05 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'j1e2f3a4b5c6'
down_revision: Union[str, None] = 'i1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Kazda strona publiczna dostaje wpis z dotychczasowym slugiem (bez normalizacji), zeby kazdy
# adres /blog/{slug} dzialajacy przed migracja dzialal po niej. Reszta kolumn z server_default.
BACKFILL_SQL = """
INSERT INTO blog_posts (page_id, slug, published_at, lang)
SELECT id, slug, created_at, 'pl'
FROM wiki_pages
WHERE is_public
"""


def upgrade() -> None:
    op.create_table(
        'blog_posts',
        sa.Column('page_id', sa.UUID(), nullable=False),
        sa.Column('slug', sa.String(length=255), nullable=False),
        sa.Column('previous_slugs', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('description', sa.String(length=300), server_default='', nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('author_name', sa.String(length=255), server_default='Zespół Monolynx', nullable=False),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column('lang', sa.String(length=2), server_default='pl', nullable=False),
        sa.Column('translation_of_id', sa.UUID(), nullable=True),
        sa.Column('cover_attachment_id', sa.UUID(), nullable=True),
        sa.Column('last_verified_at', sa.Date(), nullable=True),
        sa.CheckConstraint("lang IN ('pl', 'en')", name='ck_blog_posts_lang'),
        sa.ForeignKeyConstraint(['page_id'], ['wiki_pages.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['translation_of_id'], ['blog_posts.page_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['cover_attachment_id'], ['wiki_attachments.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('page_id'),
        sa.UniqueConstraint('slug', name='uq_blog_posts_slug'),
    )
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_table('blog_posts')
