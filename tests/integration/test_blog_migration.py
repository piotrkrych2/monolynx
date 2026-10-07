"""Migracja j1e2f3a4b5c6_add_blog_posts (MON-254): tabela blog_posts + uzupełnienie wierszy.

Baza testowa powstaje przez `Base.metadata.create_all`, więc tabela `blog_posts` już w niej
jest. Testy migracji usuwają ją wewnątrz transakcji testu (DDL w PostgreSQL jest
transakcyjne, rollback fixture `db_session` przywraca tabelę), wykonują `upgrade()` /
`downgrade()` modułu migracji przez `alembic.operations.Operations` na połączeniu testowym
i sprawdzają wynik w bazie. Moduł migracji ładowany jest po ścieżce, bo nazwa pliku
zaczyna się od rewizji, a nie od poprawnego identyfikatora Pythona.
"""

from __future__ import annotations

import importlib.util
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage

pytestmark = pytest.mark.integration

_CREATED = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "j1e2f3a4b5c6_add_blog_posts.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_j1e2f3a4b5c6", _MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply(sync_conn, fn_name: str) -> None:
    context = MigrationContext.configure(sync_conn)
    with Operations.context(context):
        getattr(_load_migration(), fn_name)()


async def _run_migration(db_session, fn_name: str) -> None:
    connection = await db_session.connection()
    await connection.run_sync(_apply, fn_name)


async def _table_exists(db_session, name: str = "blog_posts") -> bool:
    return (await db_session.execute(text("SELECT to_regclass(CAST(:name AS text))"), {"name": name})).scalar() is not None


async def _table_names(db_session) -> set[str]:
    rows = await db_session.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"))
    return {row[0] for row in rows.all()}


async def _drop_blog_posts(db_session) -> None:
    await db_session.execute(text("DROP TABLE blog_posts"))


async def _seed_page(db_session, owner: User, project: Project, slug: str, *, is_public: bool, created_at: datetime) -> WikiPage:
    page = WikiPage(
        project_id=project.id,
        title=slug,
        slug=slug,
        position=0,
        minio_path=f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
        is_public=is_public,
        created_by_id=owner.id,
        last_edited_by_id=owner.id,
        created_at=created_at,
    )
    db_session.add(page)
    await db_session.flush()
    return page


async def _seed_owner_and_project(db_session, tag: str) -> tuple[User, Project]:
    owner = User(email=f"blogmig-{tag}-{uuid.uuid4().hex[:8]}@example.com", is_superuser=False)
    db_session.add(owner)
    await db_session.flush()
    project = Project(
        name=f"Blog mig {tag}",
        slug=f"blogmig-{tag}-{uuid.uuid4().hex[:6]}",
        code="M" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return owner, project


class TestMigrationRevisionChain:
    def test_revision_and_down_revision(self):
        module = _load_migration()

        assert module.revision == "j1e2f3a4b5c6"
        assert module.down_revision == "i1d2e3f4a5b6"


class TestUpgrade:
    async def test_creates_table(self, db_session):
        await _drop_blog_posts(db_session)
        assert not await _table_exists(db_session)

        await _run_migration(db_session, "upgrade")

        assert await _table_exists(db_session)

    async def test_touches_no_other_table(self, db_session):
        before = await _table_names(db_session)
        assert "blog_posts" in before
        await _drop_blog_posts(db_session)

        await _run_migration(db_session, "upgrade")

        assert await _table_names(db_session) == before

    async def test_backfills_row_for_each_public_page_with_page_values(self, db_session):
        owner, project = await _seed_owner_and_project(db_session, "bf")
        created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
        public = await _seed_page(db_session, owner, project, f"publiczna-{uuid.uuid4().hex[:6]}", is_public=True, created_at=created)
        await _drop_blog_posts(db_session)

        await _run_migration(db_session, "upgrade")

        row = (
            await db_session.execute(
                text(
                    "SELECT slug, published_at, lang, previous_slugs, tags, description, author_name, "
                    "translation_of_id, cover_attachment_id, last_verified_at FROM blog_posts WHERE page_id = :id"
                ),
                {"id": public.id},
            )
        ).one()
        assert row.slug == public.slug
        assert row.published_at == created
        assert row.lang == "pl"
        assert row.previous_slugs == []
        assert row.tags == []
        assert row.description == ""
        assert row.author_name == "Zespół Monolynx"
        assert row.translation_of_id is None
        assert row.cover_attachment_id is None
        assert row.last_verified_at is None

    async def test_backfills_every_public_page_and_no_private_one(self, db_session):
        owner, project = await _seed_owner_and_project(db_session, "bfall")
        created = datetime(2026, 2, 3, tzinfo=UTC)
        tag = uuid.uuid4().hex[:6]
        public_a = await _seed_page(db_session, owner, project, f"pub-a-{tag}", is_public=True, created_at=created)
        public_b = await _seed_page(db_session, owner, project, f"pub-b-{tag}", is_public=True, created_at=created)
        private = await _seed_page(db_session, owner, project, f"priv-{tag}", is_public=False, created_at=created)
        await _drop_blog_posts(db_session)

        await _run_migration(db_session, "upgrade")

        page_ids = {r[0] for r in (await db_session.execute(text("SELECT page_id FROM blog_posts"))).all()}
        assert {public_a.id, public_b.id} <= page_ids
        assert private.id not in page_ids

    async def test_upgraded_table_enforces_unique_slug(self, db_session):
        owner, project = await _seed_owner_and_project(db_session, "uq")
        created = datetime(2026, 2, 3, tzinfo=UTC)
        first = await _seed_page(db_session, owner, project, f"jeden-{uuid.uuid4().hex[:6]}", is_public=True, created_at=created)
        second = await _seed_page(db_session, owner, project, f"dwa-{uuid.uuid4().hex[:6]}", is_public=False, created_at=created)
        await _drop_blog_posts(db_session)
        await _run_migration(db_session, "upgrade")

        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text("INSERT INTO blog_posts (page_id, slug) VALUES (:id, :slug)"),
                    {"id": second.id, "slug": first.slug},
                )

    async def test_upgraded_table_rejects_lang_outside_pl_en(self, db_session):
        owner, project = await _seed_owner_and_project(db_session, "lang")
        page = await _seed_page(db_session, owner, project, f"lang-{uuid.uuid4().hex[:6]}", is_public=False, created_at=_CREATED)
        await _drop_blog_posts(db_session)
        await _run_migration(db_session, "upgrade")

        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text("INSERT INTO blog_posts (page_id, slug, lang) VALUES (:id, :slug, 'de')"),
                    {"id": page.id, "slug": page.slug},
                )

    async def test_deleting_page_cascades_to_blog_post(self, db_session):
        owner, project = await _seed_owner_and_project(db_session, "cascade")
        page = await _seed_page(db_session, owner, project, f"kaskada-{uuid.uuid4().hex[:6]}", is_public=True, created_at=_CREATED)
        await _drop_blog_posts(db_session)
        await _run_migration(db_session, "upgrade")

        await db_session.execute(text("DELETE FROM wiki_pages WHERE id = :id"), {"id": page.id})

        remaining = (await db_session.execute(text("SELECT count(*) FROM blog_posts WHERE page_id = :id"), {"id": page.id})).scalar()
        assert remaining == 0


class TestDowngrade:
    async def test_drops_table_without_error(self, db_session):
        assert await _table_exists(db_session)

        await _run_migration(db_session, "downgrade")

        assert not await _table_exists(db_session)

    async def test_touches_no_other_table(self, db_session):
        before = await _table_names(db_session)

        await _run_migration(db_session, "downgrade")

        assert await _table_names(db_session) == before - {"blog_posts"}


class TestPublicAddressSurvivesMigration:
    @patch("monolynx.services.wiki.get_markdown", return_value="Treść sprzed migracji.")
    async def test_public_page_created_before_backfill_serves_200_at_same_address(self, mock_get_markdown, client, db_session):
        """Kryterium 842bba36: strona publiczna bez BlogPost (stan sprzed migracji) po BACKFILL_SQL
        odpowiada 200 pod tym samym adresem /blog/{slug}."""
        owner, project = await _seed_owner_and_project(db_session, "addr")
        page = await _seed_page(db_session, owner, project, f"sprzed-migracji-{uuid.uuid4().hex[:6]}", is_public=True, created_at=_CREATED)
        before = await client.get(f"/blog/{page.slug}")
        assert before.status_code == 404

        await db_session.execute(text(_load_migration().BACKFILL_SQL))

        after = await client.get(f"/blog/{page.slug}")
        assert after.status_code == 200
        assert "Treść sprzed migracji." in after.text

    async def test_mon272_backfill_sql_skips_public_page_with_slug_over_255_chars_and_keeps_the_rest(self, db_session):
        """MON-272 pkt 9: slug dłuższy niż kolumna blog_posts.slug (255) nie przewraca całego backfillu - ta strona jest pomijana."""
        owner, project = await _seed_owner_and_project(db_session, "bf-long")
        tag = uuid.uuid4().hex[:6]
        short = await _seed_page(db_session, owner, project, f"krotki-{tag}", is_public=True, created_at=_CREATED)
        long_page = await _seed_page(db_session, owner, project, "d" * 300, is_public=True, created_at=_CREATED)

        await db_session.execute(text(_load_migration().BACKFILL_SQL))

        query = text("SELECT page_id, slug FROM blog_posts WHERE page_id IN (:a, :b)")
        result = await db_session.execute(query, {"a": short.id, "b": long_page.id})
        assert {row[0]: row[1] for row in result.all()} == {short.id: short.slug}

    async def test_backfill_sql_skips_private_page(self, client, db_session):
        owner, project = await _seed_owner_and_project(db_session, "addr-priv")
        page = await _seed_page(db_session, owner, project, f"prywatna-{uuid.uuid4().hex[:6]}", is_public=False, created_at=_CREATED)

        await db_session.execute(text(_load_migration().BACKFILL_SQL))

        remaining = (await db_session.execute(text("SELECT count(*) FROM blog_posts WHERE page_id = :id"), {"id": page.id})).scalar()
        assert remaining == 0
        assert (await client.get(f"/blog/{page.slug}")).status_code == 404
