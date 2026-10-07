"""Migracja k1f2a3b4c5d6_add_can_edit_blog_to_users (MON-270): kolumna `users.can_edit_blog`.

Baza testowa powstaje przez `Base.metadata.create_all`, więc kolumna `can_edit_blog` już w niej
jest. Testy migracji usuwają ją wewnątrz transakcji testu (DDL w PostgreSQL jest
transakcyjne, rollback fixture `db_session` przywraca kolumnę), wykonują `upgrade()` /
`downgrade()` modułu migracji przez `alembic.operations.Operations` na połączeniu testowym
i sprawdzają wynik w bazie. Moduł migracji ładowany jest po ścieżce, bo nazwa pliku
zaczyna się od rewizji, a nie od poprawnego identyfikatora Pythona.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from types import ModuleType

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration

_MIGRATION_PATH = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "k1f2a3b4c5d6_add_can_edit_blog_to_users.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_k1f2a3b4c5d6", _MIGRATION_PATH)
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


async def _column(db_session):
    return (
        await db_session.execute(
            text(
                "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'users' AND column_name = 'can_edit_blog'"
            )
        )
    ).first()


async def _user_columns(db_session) -> set[str]:
    rows = await db_session.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'users'")
    )
    return {row[0] for row in rows.all()}


async def _drop_column(db_session) -> None:
    await db_session.execute(text("ALTER TABLE users DROP COLUMN can_edit_blog"))


async def _insert_user_without_column(db_session, email: str, *, is_superuser: bool = False) -> uuid.UUID:
    user_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO users (id, email, first_name, last_name, is_superuser, is_active) VALUES (:id, :email, '', '', :su, true)"),
        {"id": user_id, "email": email, "su": is_superuser},
    )
    return user_id


class TestMigrationRevisionChain:
    def test_revision_and_down_revision(self):
        module = _load_migration()

        assert module.revision == "k1f2a3b4c5d6"
        assert module.down_revision == "j1e2f3a4b5c6"


class TestUpgrade:
    async def test_adds_boolean_not_null_column_with_false_default(self, db_session):
        await _drop_column(db_session)
        assert await _column(db_session) is None

        await _run_migration(db_session, "upgrade")

        column = await _column(db_session)
        assert column is not None
        data_type, is_nullable, column_default = column
        assert data_type == "boolean"
        assert is_nullable == "NO"
        assert column_default == "false"

    async def test_existing_rows_get_false_including_superuser(self, db_session):
        await _drop_column(db_session)
        plain_id = await _insert_user_without_column(db_session, f"mig-plain-{uuid.uuid4().hex[:8]}@example.com")
        super_id = await _insert_user_without_column(db_session, f"mig-super-{uuid.uuid4().hex[:8]}@example.com", is_superuser=True)

        await _run_migration(db_session, "upgrade")

        rows = (await db_session.execute(text("SELECT id, can_edit_blog FROM users WHERE id IN (:a, :b)"), {"a": plain_id, "b": super_id})).all()
        assert {row.id: row.can_edit_blog for row in rows} == {plain_id: False, super_id: False}

    async def test_touches_no_other_user_column(self, db_session):
        before = await _user_columns(db_session)
        assert "can_edit_blog" in before
        await _drop_column(db_session)

        await _run_migration(db_session, "upgrade")

        assert await _user_columns(db_session) == before

    async def test_new_row_without_value_defaults_to_false(self, db_session):
        await _drop_column(db_session)
        await _run_migration(db_session, "upgrade")
        user_id = uuid.uuid4()

        await db_session.execute(
            text("INSERT INTO users (id, email, first_name, last_name, is_superuser, is_active) VALUES (:id, :email, '', '', false, true)"),
            {"id": user_id, "email": f"mig-new-{uuid.uuid4().hex[:8]}@example.com"},
        )

        assert (await db_session.execute(text("SELECT can_edit_blog FROM users WHERE id = :id"), {"id": user_id})).scalar_one() is False

    async def test_upgraded_column_rejects_null(self, db_session):
        await _drop_column(db_session)
        await _run_migration(db_session, "upgrade")

        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(
                        "INSERT INTO users (id, email, first_name, last_name, is_superuser, is_active, can_edit_blog) "
                        "VALUES (:id, :email, '', '', false, true, NULL)"
                    ),
                    {"id": uuid.uuid4(), "email": f"mig-null-{uuid.uuid4().hex[:8]}@example.com"},
                )


class TestDowngrade:
    async def test_drops_the_column(self, db_session):
        assert await _column(db_session) is not None

        await _run_migration(db_session, "downgrade")

        assert await _column(db_session) is None

    async def test_touches_no_other_user_column(self, db_session):
        before = await _user_columns(db_session)

        await _run_migration(db_session, "downgrade")

        assert await _user_columns(db_session) == before - {"can_edit_blog"}

    async def test_upgrade_after_downgrade_restores_the_column(self, db_session):
        await _run_migration(db_session, "downgrade")

        await _run_migration(db_session, "upgrade")

        assert await _column(db_session) is not None
