"""Testy integracyjne planu pracy: aktualne czlonkostwo i brak enumeracji ticketow (MON-239).

Zapytania ida do prawdziwej bazy (db_session). Narzedzia MCP dostaja fabryke sesji
opartą o db_session (commit -> flush, zeby nie wychodzic poza transakcje testu) oraz
zamockowane `_auth`. Jedyny test commita (regresja MON-71) uzywa osobnych sesji.

Kontrakt:
- odczyty (serwis, MCP, dashboard) zwracaja tylko wpisy z aktywnych projektow,
  w ktorych uzytkownik jest OBECNIE czlonkiem;
- schedule / schedule_ticket / get_ticket_schedule nie odrozniaja ticketu nieistniejacego
  od cudzego ("Ticket nie istnieje");
- update po utracie czlonkostwa -> "Brak dostepu", unschedule nadal dziala.
"""

from __future__ import annotations

import secrets
import string
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from monolynx import mcp_server
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.models.work_plan import WorkPlanEntry
from monolynx.services import work_plan as svc
from monolynx.services.mcp_auth import generate_api_token
from tests.conftest import login_session

NOT_FOUND = "Ticket nie istnieje"
NO_ACCESS = "Brak dostepu"
FIXED_DAY = date(2026, 6, 15)


# ---------------------------------------------------------------------------
# Helpery
# ---------------------------------------------------------------------------


def _letters(n: int = 8) -> str:
    """Kod projektu z samych liter - klucz ticketu (regex) nie przyjmuje cyfr w kodzie."""
    return "".join(secrets.choice(string.ascii_uppercase) for _ in range(n))


async def _make_user(db, tag: str) -> User:
    user = User(email=f"wpm-{tag}-{uuid.uuid4().hex[:8]}@test.com", password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def _make_project(db, tag: str, *, active: bool = True) -> Project:
    uid = uuid.uuid4().hex[:8]
    project = Project(
        name=f"WPM {tag} {uid}",
        slug=f"wpm-{tag}-{uid}",
        code=_letters(),
        api_key=secrets.token_urlsafe(32),
        is_active=active,
    )
    db.add(project)
    await db.flush()
    return project


async def _make_ticket(db, project: Project, number: int = 1, title: str | None = None) -> Ticket:
    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=title or f"WPM Ticket {uuid.uuid4().hex[:8]}",
        status="todo",
        priority="medium",
    )
    db.add(ticket)
    await db.flush()
    return ticket


async def _add_member(db, project: Project, user: User) -> None:
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="member"))
    await db.flush()


async def _remove_member(db, project: Project, user: User) -> None:
    await db.execute(delete(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id))
    await db.flush()


async def _soft_delete(db, project: Project) -> None:
    project.is_active = False
    await db.flush()


async def _make_entry(db, user: User, ticket: Ticket, day: date, *, position: int = 0, notes: str | None = None) -> WorkPlanEntry:
    entry = WorkPlanEntry(user_id=user.id, ticket_id=ticket.id, scheduled_date=day, position=position, notes=notes)
    db.add(entry)
    await db.flush()
    return entry


async def _build_world(db, user: User, day: date) -> SimpleNamespace:
    """User jest czlonkiem projektow A i B (po jednym tickecie i wpisie na `day`).

    W projekcie A jest tez `other` - drugi czlonek, ktory zostaje po utracie
    czlonkostwa przez `user` (filtr ma dotyczyc TEGO uzytkownika, nie "kogokolwiek").
    """
    other = await _make_user(db, "other")
    project_a = await _make_project(db, "a")
    project_b = await _make_project(db, "b")
    for project in (project_a, project_b):
        await _add_member(db, project, user)
    await _add_member(db, project_a, other)
    ticket_a = await _make_ticket(db, project_a, title=f"UniqueTitleA{uuid.uuid4().hex[:8]}")
    ticket_b = await _make_ticket(db, project_b, title=f"UniqueTitleB{uuid.uuid4().hex[:8]}")
    entry_a = await _make_entry(db, user, ticket_a, day, position=1, notes="notatka A")
    entry_b = await _make_entry(db, user, ticket_b, day, position=2, notes="notatka B")
    return SimpleNamespace(
        user=user,
        other=other,
        project_a=project_a,
        project_b=project_b,
        ticket_a=ticket_a,
        ticket_b=ticket_b,
        entry_a=entry_a,
        entry_b=entry_b,
        day=day,
    )


async def _foreign_ticket(db) -> tuple[Project, Ticket]:
    """Projekt i ticket, w ktorym testowany uzytkownik NIE jest czlonkiem."""
    owner = await _make_user(db, "foreign-owner")
    project = await _make_project(db, "foreign")
    await _add_member(db, project, owner)
    ticket = await _make_ticket(db, project, number=7)
    return project, ticket


@pytest.fixture
async def world(db_session):
    user = await _make_user(db_session, "main")
    return await _build_world(db_session, user, date.today())


def _flush_factory(db_session):
    """Fabryka sesji MCP na db_session; commit -> flush (outer transakcja testu zostaje)."""

    @asynccontextmanager
    async def _factory():
        original_commit = db_session.commit

        async def _flush_instead():
            await db_session.flush()

        db_session.commit = _flush_instead
        try:
            yield db_session
        finally:
            db_session.commit = original_commit

    return _factory


@contextmanager
def _mcp_as(db_session, user: User):
    """Narzedzia MCP wykonuja sie na db_session jako `user` (realna baza, bez mockow zapytan)."""
    with (
        patch("monolynx.mcp_server._auth", AsyncMock(return_value=user)),
        patch("monolynx.mcp_server.async_session_factory", _flush_factory(db_session)),
    ):
        yield


def _ctx() -> MagicMock:
    return MagicMock()


def _ticket_ids(entries) -> set[str]:
    """Zbior ticket_id z wpisow serwisu (ORM) albo odpowiedzi MCP/API (dict)."""
    return {str(e["ticket_id"] if isinstance(e, dict) else e.ticket_id) for e in entries}


async def _login_user(client, db_session, tag: str) -> User:
    email = f"wpm-{tag}-{uuid.uuid4().hex[:8]}@test.com"
    await login_session(client, db_session, email=email, is_superuser=False)
    return (await db_session.execute(select(User).where(User.email == email))).scalar_one()


async def _entry_row(db_session, entry_id: uuid.UUID):
    """Swieze wartosci pol wpisu prosto z bazy (omija identity map)."""
    result = await db_session.execute(
        select(WorkPlanEntry.scheduled_date, WorkPlanEntry.position, WorkPlanEntry.notes).where(WorkPlanEntry.id == entry_id)
    )
    return result.one_or_none()


# ---------------------------------------------------------------------------
# 1. Utrata czlonkostwa - serwis
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestServiceHidesEntriesAfterMembershipLoss:
    async def test_list_for_user_range_hides_entry_after_membership_loss(self, db_session, world):
        before = await svc.list_for_user_range(db_session, world.user.id, world.day, world.day)
        assert _ticket_ids(before) == {str(world.ticket_a.id), str(world.ticket_b.id)}

        await _remove_member(db_session, world.project_a, world.user)

        after = await svc.list_for_user_range(db_session, world.user.id, world.day, world.day)
        assert _ticket_ids(after) == {str(world.ticket_b.id)}

    async def test_list_for_user_range_with_project_filter_hides_entry_after_membership_loss(self, db_session, world):
        before = await svc.list_for_user_range(db_session, world.user.id, world.day, world.day, project_ids=[world.project_a.id])
        assert _ticket_ids(before) == {str(world.ticket_a.id)}

        await _remove_member(db_session, world.project_a, world.user)

        after = await svc.list_for_user_range(db_session, world.user.id, world.day, world.day, project_ids=[world.project_a.id])
        assert after == []

    async def test_today_for_user_hides_entry_after_membership_loss(self, db_session, world):
        before = await svc.today_for_user(db_session, world.user.id)
        assert _ticket_ids(before) == {str(world.ticket_a.id), str(world.ticket_b.id)}

        await _remove_member(db_session, world.project_a, world.user)

        after = await svc.today_for_user(db_session, world.user.id)
        assert _ticket_ids(after) == {str(world.ticket_b.id)}

    async def test_today_for_user_in_project_hides_entry_after_membership_loss(self, db_session, world):
        before = await svc.today_for_user_in_project(db_session, world.user.id, world.project_a.id)
        assert _ticket_ids(before) == {str(world.ticket_a.id)}

        await _remove_member(db_session, world.project_a, world.user)

        after = await svc.today_for_user_in_project(db_session, world.user.id, world.project_a.id)
        assert after == []

    async def test_schedule_for_ticket_hides_entry_after_membership_loss(self, db_session, world):
        before = await svc.schedule_for_ticket(db_session, world.user.id, world.ticket_a.id)
        assert [e.id for e in before] == [world.entry_a.id]

        await _remove_member(db_session, world.project_a, world.user)

        after = await svc.schedule_for_ticket(db_session, world.user.id, world.ticket_a.id)
        assert after == []

    async def test_other_member_does_not_keep_entry_visible(self, db_session, world):
        """W projekcie A zostaje `other` - to nie daje `user` dostepu do jego wpisu."""
        await _remove_member(db_session, world.project_a, world.user)

        remaining = await svc.list_for_user_range(db_session, world.user.id, world.day, world.day)
        assert str(world.ticket_a.id) not in _ticket_ids(remaining)

    async def test_entry_of_other_user_not_visible_to_member(self, db_session, world):
        """Filtr czlonkostwa nie rozszerza widocznosci: cudzy wpis w moim projekcie dalej ukryty."""
        await _make_entry(db_session, world.other, world.ticket_a, world.day, position=9, notes="cudzy")

        entries = await svc.list_for_user_range(db_session, world.user.id, world.day, world.day)
        assert {e.user_id for e in entries} == {world.user.id}


# ---------------------------------------------------------------------------
# 2. Soft delete projektu - serwis
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestServiceHidesEntriesAfterSoftDelete:
    async def test_list_for_user_range_hides_entry_after_soft_delete(self, db_session, world):
        before = await svc.list_for_user_range(db_session, world.user.id, world.day, world.day)
        assert str(world.ticket_a.id) in _ticket_ids(before)

        await _soft_delete(db_session, world.project_a)

        after = await svc.list_for_user_range(db_session, world.user.id, world.day, world.day)
        assert _ticket_ids(after) == {str(world.ticket_b.id)}

    async def test_list_for_user_range_with_project_filter_hides_entry_after_soft_delete(self, db_session, world):
        await _soft_delete(db_session, world.project_a)

        after = await svc.list_for_user_range(db_session, world.user.id, world.day, world.day, project_ids=[world.project_a.id])
        assert after == []

    async def test_today_for_user_hides_entry_after_soft_delete(self, db_session, world):
        await _soft_delete(db_session, world.project_a)

        after = await svc.today_for_user(db_session, world.user.id)
        assert _ticket_ids(after) == {str(world.ticket_b.id)}

    async def test_today_for_user_in_project_hides_entry_after_soft_delete(self, db_session, world):
        await _soft_delete(db_session, world.project_a)

        after = await svc.today_for_user_in_project(db_session, world.user.id, world.project_a.id)
        assert after == []

    async def test_schedule_for_ticket_hides_entry_after_soft_delete(self, db_session, world):
        await _soft_delete(db_session, world.project_a)

        after = await svc.schedule_for_ticket(db_session, world.user.id, world.ticket_a.id)
        assert after == []


# ---------------------------------------------------------------------------
# 3. Utrata czlonkostwa / soft delete - narzedzia MCP
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMcpHidesEntries:
    async def test_list_work_plan_hides_entry_after_membership_loss(self, db_session, world):
        day = world.day.isoformat()
        with _mcp_as(db_session, world.user):
            before = await mcp_server.list_work_plan(_ctx(), start_date=day, end_date=day)
            assert _ticket_ids(before) == {str(world.ticket_a.id), str(world.ticket_b.id)}

            await _remove_member(db_session, world.project_a, world.user)
            after = await mcp_server.list_work_plan(_ctx(), start_date=day, end_date=day)

        assert _ticket_ids(after) == {str(world.ticket_b.id)}
        assert str(world.ticket_a.id) not in str(after)

    async def test_list_work_plan_hides_entry_after_soft_delete(self, db_session, world):
        day = world.day.isoformat()
        with _mcp_as(db_session, world.user):
            before = await mcp_server.list_work_plan(_ctx(), start_date=day, end_date=day)
            assert str(world.ticket_a.id) in _ticket_ids(before)

            await _soft_delete(db_session, world.project_a)
            after = await mcp_server.list_work_plan(_ctx(), start_date=day, end_date=day)

        assert _ticket_ids(after) == {str(world.ticket_b.id)}

    async def test_get_today_tasks_hides_entry_after_membership_loss(self, db_session, world):
        with _mcp_as(db_session, world.user):
            before = await mcp_server.get_today_tasks(_ctx())
            assert _ticket_ids(before) == {str(world.ticket_a.id), str(world.ticket_b.id)}

            await _remove_member(db_session, world.project_a, world.user)
            after = await mcp_server.get_today_tasks(_ctx())

        assert _ticket_ids(after) == {str(world.ticket_b.id)}

    async def test_get_today_tasks_hides_entry_after_soft_delete(self, db_session, world):
        with _mcp_as(db_session, world.user):
            before = await mcp_server.get_today_tasks(_ctx())
            assert str(world.ticket_a.id) in _ticket_ids(before)

            await _soft_delete(db_session, world.project_a)
            after = await mcp_server.get_today_tasks(_ctx())

        assert _ticket_ids(after) == {str(world.ticket_b.id)}

    async def test_get_ticket_schedule_hides_entry_after_membership_loss_by_uuid(self, db_session, world):
        with _mcp_as(db_session, world.user):
            before = await mcp_server.get_ticket_schedule(_ctx(), ticket_id=str(world.ticket_a.id))
            assert _ticket_ids(before) == {str(world.ticket_a.id)}

            await _remove_member(db_session, world.project_a, world.user)
            after = await mcp_server.get_ticket_schedule(_ctx(), ticket_id=str(world.ticket_a.id))

        assert after == []

    async def test_get_ticket_schedule_hides_entry_after_soft_delete_by_uuid(self, db_session, world):
        await _soft_delete(db_session, world.project_a)

        with _mcp_as(db_session, world.user):
            after = await mcp_server.get_ticket_schedule(_ctx(), ticket_id=str(world.ticket_a.id))

        assert after == []

    async def test_get_ticket_schedule_by_key_works_while_member(self, db_session, world):
        """Kontrola pozytywna: klucz wlasnego ticketu rozwiazuje sie i zwraca wpis."""
        key = f"{world.project_a.code}-{world.ticket_a.number}"
        with _mcp_as(db_session, world.user):
            result = await mcp_server.get_ticket_schedule(_ctx(), ticket_id=key)

        assert [r["id"] for r in result] == [str(world.entry_a.id)]
        assert result[0]["ticket_key"] == key


# ---------------------------------------------------------------------------
# 4. Utrata czlonkostwa / soft delete - dashboard
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDashboardHidesEntries:
    async def test_api_data_hides_entry_after_membership_loss(self, client, db_session):
        user = await _login_user(client, db_session, "api-member")
        world = await _build_world(db_session, user, FIXED_DAY)
        url = f"/dashboard/plan/api/data?start={FIXED_DAY}&end={FIXED_DAY}"

        before = await client.get(url)
        assert before.status_code == 200
        assert _ticket_ids(before.json()) == {str(world.ticket_a.id), str(world.ticket_b.id)}

        await _remove_member(db_session, world.project_a, user)
        after = await client.get(url)

        assert after.status_code == 200
        assert _ticket_ids(after.json()) == {str(world.ticket_b.id)}

    async def test_api_data_hides_entry_after_soft_delete(self, client, db_session):
        user = await _login_user(client, db_session, "api-soft")
        world = await _build_world(db_session, user, FIXED_DAY)
        url = f"/dashboard/plan/api/data?start={FIXED_DAY}&end={FIXED_DAY}"

        before = await client.get(url)
        assert str(world.ticket_a.id) in _ticket_ids(before.json())

        await _soft_delete(db_session, world.project_a)
        after = await client.get(url)

        assert after.status_code == 200
        assert _ticket_ids(after.json()) == {str(world.ticket_b.id)}

    async def test_plan_view_hides_entry_after_membership_loss(self, client, db_session):
        user = await _login_user(client, db_session, "view-member")
        world = await _build_world(db_session, user, FIXED_DAY)
        url = f"/dashboard/plan/?start={FIXED_DAY}&end={FIXED_DAY}"

        before = await client.get(url)
        assert before.status_code == 200
        assert world.ticket_a.title in before.text
        assert world.ticket_b.title in before.text

        await _remove_member(db_session, world.project_a, user)
        after = await client.get(url)

        assert after.status_code == 200
        assert world.ticket_a.title not in after.text
        assert world.ticket_b.title in after.text

    async def test_plan_view_hides_entry_after_soft_delete(self, client, db_session):
        user = await _login_user(client, db_session, "view-soft")
        world = await _build_world(db_session, user, FIXED_DAY)
        url = f"/dashboard/plan/?start={FIXED_DAY}&end={FIXED_DAY}"

        before = await client.get(url)
        assert world.ticket_a.title in before.text

        await _soft_delete(db_session, world.project_a)
        after = await client.get(url)

        assert after.status_code == 200
        assert world.ticket_a.title not in after.text
        assert world.ticket_b.title in after.text


# ---------------------------------------------------------------------------
# 5. update / PATCH po utracie czlonkostwa
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateRequiresCurrentMembership:
    async def test_update_with_membership_changes_fields(self, db_session, world):
        """Kontrola pozytywna: dopoki jest czlonkiem, update dziala."""
        result = await svc.update(db_session, world.user.id, world.entry_a.id, position=5, notes="nowa")

        assert isinstance(result, WorkPlanEntry)
        row = await _entry_row(db_session, world.entry_a.id)
        assert (row.position, row.notes) == (5, "nowa")

    async def test_update_after_membership_loss_returns_no_access_and_keeps_entry(self, db_session, world):
        await _remove_member(db_session, world.project_a, world.user)
        new_day = date(2026, 7, 1)

        result = await svc.update(db_session, world.user.id, world.entry_a.id, scheduled_date=new_day, position=9, notes="hack")

        assert result == NO_ACCESS
        row = await _entry_row(db_session, world.entry_a.id)
        assert (row.scheduled_date, row.position, row.notes) == (world.day, 1, "notatka A")

    async def test_update_after_soft_delete_returns_no_access_and_keeps_entry(self, db_session, world):
        await _soft_delete(db_session, world.project_a)

        result = await svc.update(db_session, world.user.id, world.entry_a.id, position=9, notes="hack")

        assert result == NO_ACCESS
        row = await _entry_row(db_session, world.entry_a.id)
        assert (row.position, row.notes) == (1, "notatka A")

    async def test_update_of_entry_in_other_project_still_works_after_loss_in_one(self, db_session, world):
        """Utrata czlonkostwa w A nie blokuje edycji wpisu w B."""
        await _remove_member(db_session, world.project_a, world.user)

        result = await svc.update(db_session, world.user.id, world.entry_b.id, position=4)

        assert isinstance(result, WorkPlanEntry)
        assert result.position == 4

    async def test_update_missing_entry_message_unchanged(self, db_session, world):
        assert await svc.update(db_session, world.user.id, uuid.uuid4(), position=1) == "Entry nie znaleziony"

    async def test_update_foreign_entry_returns_no_access(self, db_session, world):
        assert await svc.update(db_session, world.other.id, world.entry_a.id, position=1) == NO_ACCESS

    async def test_mcp_update_after_membership_loss_raises_no_access(self, db_session, world):
        await _remove_member(db_session, world.project_a, world.user)

        with _mcp_as(db_session, world.user), pytest.raises(ValueError) as exc:
            await mcp_server.update_work_plan_entry(_ctx(), entry_id=str(world.entry_a.id), position=9)

        assert str(exc.value) == NO_ACCESS
        row = await _entry_row(db_session, world.entry_a.id)
        assert row.position == 1

    async def test_patch_after_membership_loss_returns_403_and_keeps_entry(self, client, db_session):
        user = await _login_user(client, db_session, "patch-member")
        world = await _build_world(db_session, user, FIXED_DAY)
        await _remove_member(db_session, world.project_a, user)

        resp = await client.patch(f"/dashboard/plan/entries/{world.entry_a.id}", json={"position": 9, "notes": "hack"})

        assert resp.status_code == 403
        assert resp.json()["detail"] == NO_ACCESS
        row = await _entry_row(db_session, world.entry_a.id)
        assert (row.position, row.notes) == (1, "notatka A")

    async def test_patch_after_soft_delete_returns_403_and_keeps_entry(self, client, db_session):
        user = await _login_user(client, db_session, "patch-soft")
        world = await _build_world(db_session, user, FIXED_DAY)
        await _soft_delete(db_session, world.project_a)

        resp = await client.patch(f"/dashboard/plan/entries/{world.entry_a.id}", json={"position": 9})

        assert resp.status_code == 403
        row = await _entry_row(db_session, world.entry_a.id)
        assert row.position == 1

    async def test_patch_with_membership_returns_200(self, client, db_session):
        """Kontrola pozytywna dla PATCH."""
        user = await _login_user(client, db_session, "patch-ok")
        world = await _build_world(db_session, user, FIXED_DAY)

        resp = await client.patch(f"/dashboard/plan/entries/{world.entry_a.id}", json={"position": 9})

        assert resp.status_code == 200
        assert resp.json()["position"] == 9


# ---------------------------------------------------------------------------
# 6. unschedule / DELETE po utracie czlonkostwa
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUnscheduleAfterAccessLoss:
    async def test_unschedule_after_membership_loss_deletes_entry(self, db_session, world):
        await _remove_member(db_session, world.project_a, world.user)

        err = await svc.unschedule(db_session, world.user.id, world.entry_a.id)

        assert err is None
        assert await _entry_row(db_session, world.entry_a.id) is None

    async def test_unschedule_after_soft_delete_deletes_entry(self, db_session, world):
        await _soft_delete(db_session, world.project_a)

        err = await svc.unschedule(db_session, world.user.id, world.entry_a.id)

        assert err is None
        assert await _entry_row(db_session, world.entry_a.id) is None

    async def test_mcp_delete_after_membership_loss_succeeds(self, db_session, world):
        await _remove_member(db_session, world.project_a, world.user)

        with _mcp_as(db_session, world.user):
            result = await mcp_server.delete_work_plan_entry(_ctx(), entry_id=str(world.entry_a.id))

        assert result["success"] is True
        assert await _entry_row(db_session, world.entry_a.id) is None

    async def test_delete_after_membership_loss_returns_200(self, client, db_session):
        user = await _login_user(client, db_session, "del-member")
        world = await _build_world(db_session, user, FIXED_DAY)
        await _remove_member(db_session, world.project_a, user)

        resp = await client.delete(f"/dashboard/plan/entries/{world.entry_a.id}")

        assert resp.status_code == 200
        assert await _entry_row(db_session, world.entry_a.id) is None

    async def test_delete_after_soft_delete_returns_200(self, client, db_session):
        user = await _login_user(client, db_session, "del-soft")
        world = await _build_world(db_session, user, FIXED_DAY)
        await _soft_delete(db_session, world.project_a)

        resp = await client.delete(f"/dashboard/plan/entries/{world.entry_a.id}")

        assert resp.status_code == 200
        assert await _entry_row(db_session, world.entry_a.id) is None

    async def test_unschedule_foreign_entry_still_denied(self, db_session, world):
        """Rozluznienie dotyczy wlasciciela; cudzy wpis dalej "Brak dostepu"."""
        assert await svc.unschedule(db_session, world.other.id, world.entry_a.id) == NO_ACCESS
        assert await _entry_row(db_session, world.entry_a.id) is not None


# ---------------------------------------------------------------------------
# 7. Enumeracja ticketow - serwis, MCP, dashboard
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestScheduleServiceDoesNotEnumerate:
    async def test_schedule_foreign_ticket_returns_not_found(self, db_session, world):
        _project, foreign = await _foreign_ticket(db_session)

        result = await svc.schedule(db_session, world.user.id, foreign.id, world.day)

        assert result == NOT_FOUND

    async def test_schedule_nonexistent_ticket_returns_not_found(self, db_session, world):
        result = await svc.schedule(db_session, world.user.id, uuid.uuid4(), world.day)

        assert result == NOT_FOUND

    async def test_schedule_ticket_of_soft_deleted_project_returns_not_found(self, db_session, world):
        await _soft_delete(db_session, world.project_a)
        extra = await _make_ticket(db_session, world.project_a, number=2)

        result = await svc.schedule(db_session, world.user.id, extra.id, world.day)

        assert result == NOT_FOUND

    async def test_schedule_foreign_and_nonexistent_are_identical(self, db_session, world):
        _project, foreign = await _foreign_ticket(db_session)

        foreign_result = await svc.schedule(db_session, world.user.id, foreign.id, world.day)
        missing_result = await svc.schedule(db_session, world.user.id, uuid.uuid4(), world.day)

        assert foreign_result == missing_result

    async def test_schedule_own_ticket_still_works(self, db_session, world):
        """Kontrola pozytywna: czlonek planuje ticket z wlasnego projektu."""
        extra = await _make_ticket(db_session, world.project_a, number=2)

        result = await svc.schedule(db_session, world.user.id, extra.id, world.day)

        assert isinstance(result, WorkPlanEntry)

    async def test_schedule_foreign_ticket_creates_no_entry(self, db_session, world):
        _project, foreign = await _foreign_ticket(db_session)

        await svc.schedule(db_session, world.user.id, foreign.id, world.day)

        rows = (await db_session.execute(select(WorkPlanEntry.id).where(WorkPlanEntry.ticket_id == foreign.id))).all()
        assert rows == []


@pytest.mark.integration
class TestMcpScheduleDoesNotEnumerate:
    async def _error(self, db_session, user, ticket_id: str) -> str:
        with _mcp_as(db_session, user), pytest.raises(ValueError) as exc:
            await mcp_server.schedule_ticket(_ctx(), ticket_id=ticket_id, scheduled_date=date.today().isoformat())
        return str(exc.value)

    async def test_schedule_ticket_foreign_key_and_nonexistent_key_have_identical_error(self, db_session, world):
        foreign_project, foreign = await _foreign_ticket(db_session)
        foreign_key = f"{foreign_project.code}-{foreign.number}"
        missing_key = f"{_letters(9)}-1"

        foreign_error = await self._error(db_session, world.user, foreign_key)
        missing_error = await self._error(db_session, world.user, missing_key)

        assert foreign_error == missing_error
        assert foreign_error == NOT_FOUND

    async def test_schedule_ticket_error_does_not_echo_key(self, db_session, world):
        foreign_project, foreign = await _foreign_ticket(db_session)
        foreign_key = f"{foreign_project.code}-{foreign.number}"

        error = await self._error(db_session, world.user, foreign_key)

        assert foreign_key not in error
        assert foreign_project.code not in error

    async def test_schedule_ticket_foreign_uuid_and_random_uuid_have_identical_error(self, db_session, world):
        _project, foreign = await _foreign_ticket(db_session)

        foreign_error = await self._error(db_session, world.user, str(foreign.id))
        random_error = await self._error(db_session, world.user, str(uuid.uuid4()))

        assert foreign_error == random_error
        assert foreign_error == NOT_FOUND

    async def test_schedule_ticket_key_of_soft_deleted_project_returns_not_found(self, db_session, world):
        await _soft_delete(db_session, world.project_a)
        key = f"{world.project_a.code}-{world.ticket_a.number}"

        assert await self._error(db_session, world.user, key) == NOT_FOUND

    async def test_schedule_ticket_key_after_membership_loss_returns_not_found(self, db_session, world):
        await _remove_member(db_session, world.project_a, world.user)
        key = f"{world.project_a.code}-{world.ticket_a.number}"

        assert await self._error(db_session, world.user, key) == NOT_FOUND

    async def test_schedule_ticket_own_key_works(self, db_session, world):
        """Kontrola pozytywna: klucz ticketu z wlasnego projektu tworzy wpis."""
        extra = await _make_ticket(db_session, world.project_a, number=2)
        key = f"{world.project_a.code}-{extra.number}"

        with _mcp_as(db_session, world.user):
            result = await mcp_server.schedule_ticket(_ctx(), ticket_id=key, scheduled_date=date.today().isoformat())

        assert result["ticket_id"] == str(extra.id)
        assert result["ticket_key"] == key

    async def test_invalid_key_format_error_unchanged(self, db_session, world):
        error = await self._error(db_session, world.user, "to_nie_klucz")

        assert error.startswith("Nieprawidlowy identyfikator ticketa")


@pytest.mark.integration
class TestMcpGetTicketScheduleDoesNotEnumerate:
    async def _key_error(self, db_session, user, key: str) -> str:
        with _mcp_as(db_session, user), pytest.raises(ValueError) as exc:
            await mcp_server.get_ticket_schedule(_ctx(), ticket_id=key)
        return str(exc.value)

    async def test_foreign_key_and_nonexistent_key_have_identical_error(self, db_session, world):
        foreign_project, foreign = await _foreign_ticket(db_session)
        foreign_key = f"{foreign_project.code}-{foreign.number}"
        missing_key = f"{_letters(9)}-1"

        foreign_error = await self._key_error(db_session, world.user, foreign_key)
        missing_error = await self._key_error(db_session, world.user, missing_key)

        assert foreign_error == missing_error
        assert foreign_error == NOT_FOUND

    async def test_foreign_uuid_and_random_uuid_return_identical_empty_result(self, db_session, world):
        _project, foreign = await _foreign_ticket(db_session)

        with _mcp_as(db_session, world.user):
            foreign_result = await mcp_server.get_ticket_schedule(_ctx(), ticket_id=str(foreign.id))
            random_result = await mcp_server.get_ticket_schedule(_ctx(), ticket_id=str(uuid.uuid4()))

        assert foreign_result == random_result == []


@pytest.mark.integration
class TestDashboardCreateEntryDoesNotEnumerate:
    async def test_post_foreign_and_nonexistent_ticket_have_identical_400(self, client, db_session):
        user = await _login_user(client, db_session, "post-enum")
        await _build_world(db_session, user, FIXED_DAY)
        _project, foreign = await _foreign_ticket(db_session)
        body = {"scheduled_date": FIXED_DAY.isoformat()}

        foreign_resp = await client.post("/dashboard/plan/entries", json={**body, "ticket_id": str(foreign.id)})
        missing_resp = await client.post("/dashboard/plan/entries", json={**body, "ticket_id": str(uuid.uuid4())})

        assert foreign_resp.status_code == 400
        assert missing_resp.status_code == 400
        assert foreign_resp.json()["detail"] == missing_resp.json()["detail"]
        assert foreign_resp.json()["detail"] == NOT_FOUND

    async def test_post_own_ticket_returns_200(self, client, db_session):
        """Kontrola pozytywna dla POST."""
        user = await _login_user(client, db_session, "post-ok")
        world = await _build_world(db_session, user, FIXED_DAY)
        extra = await _make_ticket(db_session, world.project_a, number=2)

        resp = await client.post("/dashboard/plan/entries", json={"ticket_id": str(extra.id), "scheduled_date": FIXED_DAY.isoformat()})

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 8. project_slug w list_work_plan i get_today_tasks
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProjectSlugDoesNotEnumerate:
    async def _list_error(self, db_session, user, slug: str) -> str:
        day = date.today().isoformat()
        with _mcp_as(db_session, user), pytest.raises(ValueError) as exc:
            await mcp_server.list_work_plan(_ctx(), start_date=day, end_date=day, project_slug=slug)
        return str(exc.value)

    async def _today_error(self, db_session, user, slug: str) -> str:
        with _mcp_as(db_session, user), pytest.raises(ValueError) as exc:
            await mcp_server.get_today_tasks(_ctx(), project_slug=slug)
        return str(exc.value)

    async def test_list_work_plan_foreign_and_nonexistent_slug_have_identical_error(self, db_session, world):
        foreign_project, _ticket = await _foreign_ticket(db_session)
        missing_slug = f"wpm-missing-{uuid.uuid4().hex[:8]}"

        foreign_error = await self._list_error(db_session, world.user, foreign_project.slug)
        missing_error = await self._list_error(db_session, world.user, missing_slug)

        assert foreign_error == f"Projekt '{foreign_project.slug}' nie istnieje"
        assert missing_error == f"Projekt '{missing_slug}' nie istnieje"
        assert foreign_error.replace(foreign_project.slug, "<slug>") == missing_error.replace(missing_slug, "<slug>")

    async def test_list_work_plan_inactive_project_slug_returns_same_error(self, db_session, world):
        await _soft_delete(db_session, world.project_a)

        error = await self._list_error(db_session, world.user, world.project_a.slug)

        assert error == f"Projekt '{world.project_a.slug}' nie istnieje"

    async def test_list_work_plan_slug_after_membership_loss_returns_same_error(self, db_session, world):
        await _remove_member(db_session, world.project_a, world.user)

        error = await self._list_error(db_session, world.user, world.project_a.slug)

        assert error == f"Projekt '{world.project_a.slug}' nie istnieje"

    async def test_list_work_plan_member_slug_returns_only_that_project(self, db_session, world):
        day = world.day.isoformat()
        with _mcp_as(db_session, world.user):
            result = await mcp_server.list_work_plan(_ctx(), start_date=day, end_date=day, project_slug=world.project_a.slug)

        assert _ticket_ids(result) == {str(world.ticket_a.id)}

    async def test_get_today_tasks_foreign_and_nonexistent_slug_have_identical_error(self, db_session, world):
        foreign_project, _ticket = await _foreign_ticket(db_session)
        missing_slug = f"wpm-missing-{uuid.uuid4().hex[:8]}"

        foreign_error = await self._today_error(db_session, world.user, foreign_project.slug)
        missing_error = await self._today_error(db_session, world.user, missing_slug)

        assert foreign_error == f"Projekt '{foreign_project.slug}' nie istnieje"
        assert missing_error == f"Projekt '{missing_slug}' nie istnieje"
        assert foreign_error.replace(foreign_project.slug, "<slug>") == missing_error.replace(missing_slug, "<slug>")

    async def test_get_today_tasks_inactive_project_slug_returns_same_error(self, db_session, world):
        await _soft_delete(db_session, world.project_a)

        error = await self._today_error(db_session, world.user, world.project_a.slug)

        assert error == f"Projekt '{world.project_a.slug}' nie istnieje"

    async def test_get_today_tasks_slug_after_membership_loss_returns_same_error(self, db_session, world):
        await _remove_member(db_session, world.project_a, world.user)

        error = await self._today_error(db_session, world.user, world.project_a.slug)

        assert error == f"Projekt '{world.project_a.slug}' nie istnieje"

    async def test_get_today_tasks_member_slug_returns_only_that_project(self, db_session, world):
        with _mcp_as(db_session, world.user):
            result = await mcp_server.get_today_tasks(_ctx(), project_slug=world.project_a.slug)

        assert _ticket_ids(result) == {str(world.ticket_a.id)}


# ---------------------------------------------------------------------------
# 9. Regresja MON-71: kazdy zapis konczy sie commitem
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestServiceWritesCommit:
    """Fixture trzyma sesje w zewnetrznej transakcji, wiec commit nie wychodzi poza nia.

    Spy liczy wywolania commit (flush zamiast commita zachowuje rollback testu).
    Mutant "commit -> flush" w serwisie zostawia licznik na 0.
    """

    @staticmethod
    def _spy_commit(monkeypatch, db_session) -> list[int]:
        calls: list[int] = []

        async def _spy() -> None:
            calls.append(1)
            await db_session.flush()

        monkeypatch.setattr(db_session, "commit", _spy)
        return calls

    async def test_update_commits(self, monkeypatch, db_session, world):
        calls = self._spy_commit(monkeypatch, db_session)

        result = await svc.update(db_session, world.user.id, world.entry_a.id, position=3)

        assert isinstance(result, WorkPlanEntry)
        assert len(calls) == 1

    async def test_schedule_commits(self, monkeypatch, db_session, world):
        extra = await _make_ticket(db_session, world.project_a, number=2)
        calls = self._spy_commit(monkeypatch, db_session)

        result = await svc.schedule(db_session, world.user.id, extra.id, world.day)

        assert isinstance(result, WorkPlanEntry)
        assert len(calls) == 1

    async def test_unschedule_commits(self, monkeypatch, db_session, world):
        calls = self._spy_commit(monkeypatch, db_session)

        err = await svc.unschedule(db_session, world.user.id, world.entry_a.id)

        assert err is None
        assert len(calls) == 1

    async def test_update_denied_after_membership_loss_does_not_commit(self, monkeypatch, db_session, world):
        await _remove_member(db_session, world.project_a, world.user)
        calls = self._spy_commit(monkeypatch, db_session)

        result = await svc.update(db_session, world.user.id, world.entry_a.id, position=3)

        assert result == NO_ACCESS
        assert calls == []


def _real_ctx(token: str) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


@pytest.mark.integration
async def test_mcp_update_persists_to_separate_session(engine, monkeypatch):
    """update_work_plan_entry (realna sesja + realny token) utrwala zmiane: widac ja z OSOBNEJ sesji."""
    test_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)

    uid = uuid.uuid4().hex[:8]
    raw_token, token_hash = generate_api_token()
    day = date.today()

    async with test_factory() as setup_db:
        user = User(email=f"wpm-persist-{uid}@test.com", is_superuser=False)
        setup_db.add(user)
        await setup_db.flush()
        setup_db.add(UserApiToken(user_id=user.id, token_hash=token_hash, token_prefix=raw_token[:8], name="wpm-persist"))
        project = Project(
            name=f"WPM Persist {uid}",
            slug=f"wpm-persist-{uid}",
            code=_letters(),
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        setup_db.add(project)
        await setup_db.flush()
        setup_db.add(ProjectMember(project_id=project.id, user_id=user.id, role="member"))
        ticket = Ticket(project_id=project.id, number=1, title="WPM persist", status="todo", priority="medium")
        setup_db.add(ticket)
        await setup_db.flush()
        entry = WorkPlanEntry(user_id=user.id, ticket_id=ticket.id, scheduled_date=day, position=0)
        setup_db.add(entry)
        await setup_db.commit()
        user_id, project_id, entry_id = user.id, project.id, entry.id

    try:
        await mcp_server.update_work_plan_entry(_real_ctx(raw_token), entry_id=str(entry_id), position=7, notes="utrwalona")

        async with test_factory() as check_db:
            row = (await check_db.execute(select(WorkPlanEntry.position, WorkPlanEntry.notes).where(WorkPlanEntry.id == entry_id))).one()
        assert (row.position, row.notes) == (7, "utrwalona")
    finally:
        async with test_factory() as cleanup_db:
            await cleanup_db.execute(delete(WorkPlanEntry).where(WorkPlanEntry.user_id == user_id))
            await cleanup_db.execute(delete(Ticket).where(Ticket.project_id == project_id))
            await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            await cleanup_db.execute(delete(UserApiToken).where(UserApiToken.user_id == user_id))
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.execute(delete(User).where(User.id == user_id))
            await cleanup_db.commit()
