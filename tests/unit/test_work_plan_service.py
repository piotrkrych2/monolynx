"""Testy jednostkowe serwisu work_plan."""

from __future__ import annotations

import secrets
from datetime import date, timedelta
from uuid import uuid4

import pytest

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.models.work_plan import WorkPlanEntry
from monolynx.services import work_plan as svc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, tag: str) -> User:
    user = User(email=f"wp_svc_{tag}_{uuid4().hex[:6]}@x.com", password_hash="x")
    db.add(user)
    await db.flush()
    return user


async def _make_project(db, tag: str) -> Project:
    project = Project(
        name=f"WP Svc {tag}",
        slug=f"wp-svc-{tag}-{uuid4().hex[:6]}",
        code=f"W{secrets.token_hex(2).upper()}",
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db.add(project)
    await db.flush()
    return project


async def _make_ticket(db, project: Project, number: int) -> Ticket:
    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=f"Ticket #{number}",
        status="todo",
        priority="medium",
    )
    db.add(ticket)
    await db.flush()
    return ticket


async def _make_member(db, project: Project, user: User) -> ProjectMember:
    member = ProjectMember(project_id=project.id, user_id=user.id, role="member")
    db.add(member)
    await db.flush()
    return member


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def setup_project_user_ticket(db_session):
    """Tworzy User (member), Project, Ticket. Zwraca (user, project, ticket)."""
    user = await _make_user(db_session, "main")
    project = await _make_project(db_session, "main")
    await _make_member(db_session, project, user)
    ticket = await _make_ticket(db_session, project, number=1)
    return user, project, ticket


@pytest.fixture
async def setup_outsider(db_session):
    """User nie-czlonek zadnego projektu."""
    return await _make_user(db_session, "outsider")


# ---------------------------------------------------------------------------
# Testy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWorkPlanService:
    async def test_schedule_happy_path(self, db_session, setup_project_user_ticket):
        """schedule() tworzy entry dla czlonka projektu."""
        user, _project, ticket = setup_project_user_ticket
        result = await svc.schedule(db_session, user.id, ticket.id, date.today(), position=1, notes="aa")
        assert isinstance(result, WorkPlanEntry)
        assert result.user_id == user.id
        assert result.ticket_id == ticket.id
        assert result.notes == "aa"
        assert result.position == 1

    async def test_schedule_blocks_non_member(self, db_session, setup_project_user_ticket, setup_outsider):
        """schedule() blokuje usera spoza projektu."""
        _user, _project, ticket = setup_project_user_ticket
        outsider = setup_outsider
        result = await svc.schedule(db_session, outsider.id, ticket.id, date.today())
        assert isinstance(result, str)
        assert any(kw in result.lower() for kw in ("czlonk", "dostep", "member", "uzytkownik"))

    async def test_schedule_nonexistent_ticket_returns_error(self, db_session):
        """schedule() dla nieistniejacego ticketu zwraca blad."""
        result = await svc.schedule(db_session, uuid4(), uuid4(), date.today())
        assert isinstance(result, str)

    async def test_schedule_unique_conflict_returns_error(self, db_session):
        """schedule() drugi raz z tym samym (user, ticket, date) zwraca str, nie rzuca wyjatku."""
        # Tworzymy dane osobno — po rollback wewnatrz serwisu sesja jest cofnieta
        user = await _make_user(db_session, "uniq")
        project = await _make_project(db_session, "uniq")
        await _make_member(db_session, project, user)
        ticket = await _make_ticket(db_session, project, number=99)

        today = date.today()
        result1 = await svc.schedule(db_session, user.id, ticket.id, today)
        assert isinstance(result1, WorkPlanEntry), f"Pierwszy schedule powinien sie udac, dostal: {result1!r}"

        # Drugi schedule z tymi samymi parametrami — wewnatrz serwisu bedzie rollback
        # Serwis zwraca str zamiast rzucac wyjatku
        result2 = await svc.schedule(db_session, user.id, ticket.id, today)
        assert isinstance(result2, str)

    async def test_update_only_by_owner(self, db_session, setup_project_user_ticket, setup_outsider):
        """update() przez nie-wlasciciela zwraca blad dostepu."""
        user, _project, ticket = setup_project_user_ticket
        outsider = setup_outsider
        entry = await svc.schedule(db_session, user.id, ticket.id, date.today())
        assert isinstance(entry, WorkPlanEntry)

        result = await svc.update(db_session, outsider.id, entry.id, notes="hack")
        assert isinstance(result, str)
        assert "dostep" in result.lower()

    async def test_update_changes_fields(self, db_session, setup_project_user_ticket):
        """update() zmienia pola scheduled_date i position."""
        user, _project, ticket = setup_project_user_ticket
        entry = await svc.schedule(db_session, user.id, ticket.id, date.today(), position=0)
        assert isinstance(entry, WorkPlanEntry)

        new_date = date.today() + timedelta(days=1)
        result = await svc.update(db_session, user.id, entry.id, scheduled_date=new_date, position=5)
        assert isinstance(result, WorkPlanEntry)
        assert result.scheduled_date == new_date
        assert result.position == 5

    async def test_update_notes(self, db_session, setup_project_user_ticket):
        """update() zmienia notes."""
        user, _project, ticket = setup_project_user_ticket
        entry = await svc.schedule(db_session, user.id, ticket.id, date.today(), notes=None)
        assert isinstance(entry, WorkPlanEntry)

        result = await svc.update(db_session, user.id, entry.id, notes="nowa notatka")
        assert isinstance(result, WorkPlanEntry)
        assert result.notes == "nowa notatka"

    async def test_update_nonexistent_entry(self, db_session):
        """update() dla nieistniejacego entry zwraca blad."""
        result = await svc.update(db_session, uuid4(), uuid4(), notes="x")
        assert isinstance(result, str)

    async def test_unschedule_only_by_owner(self, db_session, setup_project_user_ticket, setup_outsider):
        """unschedule() przez nie-wlasciciela zwraca blad dostepu."""
        user, _project, ticket = setup_project_user_ticket
        outsider = setup_outsider
        entry = await svc.schedule(db_session, user.id, ticket.id, date.today())
        assert isinstance(entry, WorkPlanEntry)

        err = await svc.unschedule(db_session, outsider.id, entry.id)
        assert err is not None
        assert "dostep" in err.lower()

    async def test_unschedule_owner_ok(self, db_session, setup_project_user_ticket):
        """unschedule() przez wlasciciela zwraca None (sukces)."""
        user, _project, ticket = setup_project_user_ticket
        entry = await svc.schedule(db_session, user.id, ticket.id, date.today())
        assert isinstance(entry, WorkPlanEntry)

        err = await svc.unschedule(db_session, user.id, entry.id)
        assert err is None

    async def test_unschedule_nonexistent_entry(self, db_session):
        """unschedule() dla nieistniejacego entry zwraca blad."""
        err = await svc.unschedule(db_session, uuid4(), uuid4())
        assert err is not None
        assert isinstance(err, str)

    async def test_list_for_user_range(self, db_session, setup_project_user_ticket):
        """list_for_user_range() zwraca wpisy w zakresie dat (wlacznie)."""
        user, _project, ticket = setup_project_user_ticket
        today = date.today()
        for offset in (0, 1, 2):
            result = await svc.schedule(db_session, user.id, ticket.id, today + timedelta(days=offset))
            assert isinstance(result, WorkPlanEntry), f"schedule offset={offset}: {result!r}"

        entries = await svc.list_for_user_range(db_session, user.id, today, today + timedelta(days=1))
        assert len(entries) == 2

    async def test_list_for_user_range_empty(self, db_session, setup_project_user_ticket):
        """list_for_user_range() dla zakresu bez wpisow zwraca pusta liste."""
        user, _project, _ticket = setup_project_user_ticket
        future = date.today() + timedelta(days=365)
        entries = await svc.list_for_user_range(db_session, user.id, future, future + timedelta(days=1))
        assert entries == []

    async def test_list_for_user_range_end_before_start_returns_empty(self, db_session, setup_project_user_ticket):
        """list_for_user_range() gdy end < start zwraca pusta liste."""
        user, _project, _ticket = setup_project_user_ticket
        today = date.today()
        result = await svc.list_for_user_range(db_session, user.id, today + timedelta(days=1), today)
        assert result == []

    async def test_list_filters_by_project_ids(self, db_session, setup_project_user_ticket):
        """list_for_user_range() z project_ids filtruje po projekcie ticketu."""
        user, project, ticket = setup_project_user_ticket
        today = date.today()
        result = await svc.schedule(db_session, user.id, ticket.id, today)
        assert isinstance(result, WorkPlanEntry)

        result_in = await svc.list_for_user_range(db_session, user.id, today, today, project_ids=[project.id])
        assert len(result_in) == 1

        result_out = await svc.list_for_user_range(db_session, user.id, today, today, project_ids=[uuid4()])
        assert result_out == []

    async def test_today_for_user_wrapper(self, db_session, setup_project_user_ticket):
        """today_for_user() zwraca tylko wpisy na dzisiaj."""
        user, _project, ticket = setup_project_user_ticket
        today = date.today()
        result_today = await svc.schedule(db_session, user.id, ticket.id, today)
        assert isinstance(result_today, WorkPlanEntry)

        # Jutrzejszy wpis — nowy ticket zeby uniknac uniq constraint
        project2 = await _make_project(db_session, "today2")
        await _make_member(db_session, project2, user)
        ticket2 = await _make_ticket(db_session, project2, number=2)
        result_tomorrow = await svc.schedule(db_session, user.id, ticket2.id, today + timedelta(days=1))
        assert isinstance(result_tomorrow, WorkPlanEntry)

        entries = await svc.today_for_user(db_session, user.id)
        dates = [e.scheduled_date for e in entries]
        assert today in dates
        assert (today + timedelta(days=1)) not in dates

    async def test_today_for_user_in_project(self, db_session, setup_project_user_ticket):
        """today_for_user_in_project() filtruje do konkretnego projektu."""
        user, project, ticket = setup_project_user_ticket
        today = date.today()
        result = await svc.schedule(db_session, user.id, ticket.id, today)
        assert isinstance(result, WorkPlanEntry)

        entries = await svc.today_for_user_in_project(db_session, user.id, project.id)
        assert len(entries) == 1
        assert entries[0].scheduled_date == today

        entries_other = await svc.today_for_user_in_project(db_session, user.id, uuid4())
        assert entries_other == []

    async def test_schedule_for_ticket(self, db_session, setup_project_user_ticket):
        """schedule_for_ticket() zwraca wszystkie wpisy usera dla ticketu."""
        user, _project, ticket = setup_project_user_ticket
        today = date.today()
        for offset in (0, 1):
            result = await svc.schedule(db_session, user.id, ticket.id, today + timedelta(days=offset))
            assert isinstance(result, WorkPlanEntry), f"schedule offset={offset}: {result!r}"

        entries = await svc.schedule_for_ticket(db_session, user.id, ticket.id)
        assert len(entries) == 2

    async def test_schedule_for_ticket_other_user_not_returned(self, db_session, setup_project_user_ticket):
        """schedule_for_ticket() nie zwraca wpisow innych userow."""
        user, project, ticket = setup_project_user_ticket
        other_user = await _make_user(db_session, "other")
        await _make_member(db_session, project, other_user)

        today = date.today()
        result_user = await svc.schedule(db_session, user.id, ticket.id, today)
        assert isinstance(result_user, WorkPlanEntry)
        result_other = await svc.schedule(db_session, other_user.id, ticket.id, today)
        assert isinstance(result_other, WorkPlanEntry)

        entries = await svc.schedule_for_ticket(db_session, user.id, ticket.id)
        assert all(e.user_id == user.id for e in entries)
        assert len(entries) == 1

    async def test_schedule_blocks_soft_deleted_project(self, db_session):
        """schedule() blokuje ticket nalezacy do soft-deleted projektu (is_active=False)."""
        user = await _make_user(db_session, "soft_del")
        # Tworzymy projekt z is_active=False
        project = Project(
            name="Deleted Project",
            slug=f"deleted-proj-{uuid4().hex[:6]}",
            code=f"D{secrets.token_hex(2).upper()}",
            api_key=secrets.token_urlsafe(32),
            is_active=False,
        )
        db_session.add(project)
        await db_session.flush()

        await _make_member(db_session, project, user)
        ticket = await _make_ticket(db_session, project, number=10)

        result = await svc.schedule(db_session, user.id, ticket.id, date.today())
        assert isinstance(result, str), f"Oczekiwano bledu, dostal: {result!r}"

    async def test_update_notes_clear_to_none(self, db_session, setup_project_user_ticket):
        """update() z notes=None wyczyszcza pole do NULL (sentinel pattern)."""

        user, _project, ticket = setup_project_user_ticket
        entry = await svc.schedule(db_session, user.id, ticket.id, date.today(), notes="istniejaca notatka")
        assert isinstance(entry, WorkPlanEntry)
        assert entry.notes == "istniejaca notatka"

        # Przekazujemy notes=None (nie _UNSET) -- powinno wyczysc
        result = await svc.update(db_session, user.id, entry.id, notes=None)
        assert isinstance(result, WorkPlanEntry)
        assert result.notes is None

    async def test_update_notes_unset_does_not_clear(self, db_session, setup_project_user_ticket):
        """update() bez podania notes (domyslny _UNSET) nie zmienia pola."""

        user, _project, ticket = setup_project_user_ticket
        entry = await svc.schedule(db_session, user.id, ticket.id, date.today(), notes="zachowaj mnie")
        assert isinstance(entry, WorkPlanEntry)

        # Aktualizujemy tylko position, notes zostaje bez zmian (_UNSET)
        result = await svc.update(db_session, user.id, entry.id, position=7)
        assert isinstance(result, WorkPlanEntry)
        assert result.position == 7
        assert result.notes == "zachowaj mnie"
