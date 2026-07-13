"""Testy jednostkowe serwisu activity log -- log_activity() i get_activity_log().

Uzywa realnej bazy (db_session, fixture z rollbackiem) -- bez mockowania ORM.

Zgodnie z .claude/rules/db-commit.md: log_activity() robi tylko `await db.flush()`,
NIE `commit()` -- to jest zamierzone (serwis flush, commit w callerze). Ponizej
test explicite weryfikujacy ten kontrakt (dane niewidoczne z innej polaczenia
dopoki nie zostana commitowane).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from monolynx.models.activity_log import ActivityLog
from monolynx.models.user import User
from monolynx.services.activity import get_activity_log, log_activity


async def _make_user(db_session, email: str | None = None) -> User:
    """actor_id ma FK do users.id -- testy nie moga uzywac losowego uuid4()."""
    user = User(email=email or f"{uuid.uuid4()}@example.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.mark.unit
class TestLogActivity:
    async def test_log_activity_creates_entry_with_expected_fields(self, db_session, test_project):
        actor = await _make_user(db_session)

        entry = await log_activity(
            db_session,
            project_id=test_project.id,
            action="created",
            entity_type="ticket",
            entity_id="TST-1",
            entity_title="Nowy ticket",
            actor_id=actor.id,
            actor_type="user",
            changes={"status": {"old": None, "new": "backlog"}},
        )

        assert entry.id is not None
        assert entry.created_at is not None
        assert entry.project_id == test_project.id
        assert entry.action == "created"
        assert entry.entity_type == "ticket"
        assert entry.entity_id == "TST-1"
        assert entry.entity_title == "Nowy ticket"
        assert entry.actor_id == actor.id
        assert entry.actor_type == "user"
        assert entry.changes == {"status": {"old": None, "new": "backlog"}}

    async def test_log_activity_defaults_actor_type_to_user_and_allows_no_actor(self, db_session, test_project):
        entry = await log_activity(
            db_session,
            project_id=test_project.id,
            action="deleted",
            entity_type="sprint",
            entity_id="sprint-123",
        )

        assert entry.actor_id is None
        assert entry.actor_type == "user"
        assert entry.entity_title is None
        assert entry.changes is None

    async def test_log_activity_supports_system_actor_type(self, db_session, test_project):
        entry = await log_activity(
            db_session,
            project_id=test_project.id,
            action="check_failed",
            entity_type="monitor",
            entity_id="monitor-1",
            actor_type="system",
        )

        assert entry.actor_type == "system"
        assert entry.actor_id is None

    async def test_log_activity_does_not_commit_data_is_invisible_on_other_connection(self, db_session, engine, test_project):
        """log_activity() robi tylko flush() -- callerze odpowiada za commit().

        Weryfikujemy to otwierajac NIEZALEZNE polaczenie z ta sama baza (poza
        transakcja test_session) i sprawdzajac ze wpis nie jest tam widoczny.
        """
        entry = await log_activity(
            db_session,
            project_id=test_project.id,
            action="created",
            entity_type="ticket",
            entity_id="TST-999",
        )
        # flush() wyslal INSERT do bazy w ramach otwartej transakcji test_session,
        # wiec ID jest dostepne mimo braku commit -- to jest wlasnie pulapka
        # opisana w db-commit.md (flush "wyglada" na sukces).
        assert entry.id is not None

        async with engine.connect() as other_connection:
            result = await other_connection.execute(
                text("SELECT COUNT(*) FROM activity_log WHERE entity_id = :entity_id"),
                {"entity_id": "TST-999"},
            )
            count = result.scalar()

        assert count == 0, (
            "log_activity() nie powinno commitowac -- wpis nie moze byc widoczny z innego polaczenia dopoki caller nie wywola db.commit()"
        )


@pytest.mark.unit
class TestGetActivityLog:
    async def _seed(self, db_session, project_id, **overrides):
        defaults: dict = {
            "action": "created",
            "entity_type": "ticket",
            "entity_id": "TST-1",
            "actor_id": None,
            "actor_type": "user",
        }
        defaults.update(overrides)
        # created_at (jesli podany) jest przekazywany bezposrednio do konstruktora,
        # co pozwala zbudowac deterministyczna kolejnosc wpisow w testach.
        entry = ActivityLog(project_id=project_id, **defaults)
        db_session.add(entry)
        await db_session.flush()
        return entry

    async def test_filters_by_entity_type(self, db_session, test_project):
        await self._seed(db_session, test_project.id, entity_type="ticket", entity_id="TST-1")
        await self._seed(db_session, test_project.id, entity_type="sprint", entity_id="sprint-1")

        results = await get_activity_log(db_session, test_project.id, entity_type="sprint")

        assert len(results) == 1
        assert results[0].entity_type == "sprint"

    async def test_filters_by_entity_id(self, db_session, test_project):
        await self._seed(db_session, test_project.id, entity_type="ticket", entity_id="TST-1")
        await self._seed(db_session, test_project.id, entity_type="ticket", entity_id="TST-2")

        results = await get_activity_log(db_session, test_project.id, entity_id="TST-2")

        assert len(results) == 1
        assert results[0].entity_id == "TST-2"

    async def test_filters_by_actor_id(self, db_session, test_project):
        actor_a = await _make_user(db_session)
        actor_b = await _make_user(db_session)
        await self._seed(db_session, test_project.id, entity_id="TST-1", actor_id=actor_a.id)
        await self._seed(db_session, test_project.id, entity_id="TST-2", actor_id=actor_b.id)

        results = await get_activity_log(db_session, test_project.id, actor_id=actor_a.id)

        assert len(results) == 1
        assert results[0].actor_id == actor_a.id

    async def test_filters_by_actor_type(self, db_session, test_project):
        await self._seed(db_session, test_project.id, entity_id="TST-1", actor_type="user")
        await self._seed(db_session, test_project.id, entity_id="TST-2", actor_type="system")

        results = await get_activity_log(db_session, test_project.id, actor_type_filter="system")

        assert len(results) == 1
        assert results[0].actor_type == "system"

    async def test_orders_by_created_at_descending(self, db_session, test_project):
        now = datetime.now(UTC)
        oldest = await self._seed(db_session, test_project.id, entity_id="TST-old", created_at=now - timedelta(minutes=10))
        newest = await self._seed(db_session, test_project.id, entity_id="TST-new", created_at=now)
        middle = await self._seed(db_session, test_project.id, entity_id="TST-mid", created_at=now - timedelta(minutes=5))

        results = await get_activity_log(db_session, test_project.id)

        ids_in_order = [r.id for r in results]
        assert ids_in_order == [newest.id, middle.id, oldest.id]

    async def test_respects_limit(self, db_session, test_project):
        for i in range(5):
            await self._seed(db_session, test_project.id, entity_id=f"TST-{i}")

        results = await get_activity_log(db_session, test_project.id, limit=2)

        assert len(results) == 2

    async def test_no_filters_returns_all_entries_for_project(self, db_session, test_project):
        await self._seed(db_session, test_project.id, entity_id="TST-1")
        await self._seed(db_session, test_project.id, entity_id="TST-2")

        results = await get_activity_log(db_session, test_project.id)

        assert len(results) == 2
