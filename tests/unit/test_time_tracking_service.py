"""Testy serwisu time trackingu."""

import secrets
from datetime import date
from uuid import uuid4

import pytest

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.schemas.time_tracking import TimeTrackingFilter
from monolynx.services.auth import hash_password
from monolynx.services.time_tracking import (
    add_time_entry,
    aggregate_hours_per_sprint,
    aggregate_hours_per_user,
    delete_time_entry,
    get_ticket_total_hours,
)


@pytest.mark.integration
class TestAddTimeEntry:
    async def test_creates_entry(self, db_session):
        """Tworzy wpis czasu pracy."""
        project, user, ticket = await _create_test_data(db_session, slug="tt-add-ok", email="tt-add-ok@test.com")
        result = await add_time_entry(
            ticket_id=ticket.id,
            user_id=user.id,
            duration_minutes=90,
            date_logged=date(2026, 2, 25),
            description="Code review",
            db=db_session,
        )
        assert not isinstance(result, str)
        assert result.duration_minutes == 90
        assert result.project_id == project.id
        assert result.sprint_id == ticket.sprint_id
        assert result.description == "Code review"
        assert result.status == "draft"

    async def test_creates_entry_without_description(self, db_session):
        """Tworzy wpis bez opisu."""
        _project, user, ticket = await _create_test_data(db_session, slug="tt-add-nodesc", email="tt-add-nodesc@test.com")
        result = await add_time_entry(
            ticket_id=ticket.id,
            user_id=user.id,
            duration_minutes=60,
            date_logged=date(2026, 2, 25),
            description=None,
            db=db_session,
        )
        assert not isinstance(result, str)
        assert result.description is None

    async def test_nonexistent_ticket(self, db_session):
        """Zwraca blad dla nieistniejacego ticketu."""
        result = await add_time_entry(
            ticket_id=uuid4(),
            user_id=uuid4(),
            duration_minutes=60,
            date_logged=date(2026, 2, 25),
            description=None,
            db=db_session,
        )
        assert result == "Ticket nie istnieje"

    async def test_non_member_rejected(self, db_session):
        """Uzytkownik spoza projektu nie moze logowac czasu."""
        _project, _user, ticket = await _create_test_data(db_session, slug="tt-add-nonmem", email="tt-add-nonmem@test.com")
        outsider = User(email="tt-outsider@test.com", password_hash=hash_password("pass"))
        db_session.add(outsider)
        await db_session.flush()

        result = await add_time_entry(
            ticket_id=ticket.id,
            user_id=outsider.id,
            duration_minutes=60,
            date_logged=date(2026, 2, 25),
            description=None,
            db=db_session,
        )
        assert result == "Uzytkownik nie jest czlonkiem projektu"

    async def test_entry_inherits_sprint_from_ticket(self, db_session):
        """Wpis dziedziczy sprint_id z ticketu."""
        _project, user, ticket = await _create_test_data(db_session, slug="tt-add-spr", email="tt-add-spr@test.com")
        result = await add_time_entry(
            ticket_id=ticket.id,
            user_id=user.id,
            duration_minutes=45,
            date_logged=date(2026, 2, 25),
            description=None,
            db=db_session,
        )
        assert not isinstance(result, str)
        assert result.sprint_id == ticket.sprint_id
        assert result.sprint_id is not None

    async def test_created_via_ai_default_false(self, db_session):
        """Domyslnie created_via_ai jest False."""
        _project, user, ticket = await _create_test_data(db_session, slug="tt-add-noai", email="tt-add-noai@test.com")
        result = await add_time_entry(
            ticket_id=ticket.id,
            user_id=user.id,
            duration_minutes=30,
            date_logged=date(2026, 2, 25),
            description=None,
            db=db_session,
        )
        assert not isinstance(result, str)
        assert result.created_via_ai is False

    async def test_created_via_ai_true(self, db_session):
        """Parametr created_via_ai=True ustawia flage."""
        _project, user, ticket = await _create_test_data(db_session, slug="tt-add-ai", email="tt-add-ai@test.com")
        result = await add_time_entry(
            ticket_id=ticket.id,
            user_id=user.id,
            duration_minutes=30,
            date_logged=date(2026, 2, 25),
            description=None,
            db=db_session,
            created_via_ai=True,
        )
        assert not isinstance(result, str)
        assert result.created_via_ai is True


@pytest.mark.integration
class TestDeleteTimeEntry:
    async def test_owner_can_delete(self, db_session):
        """Wlasciciel moze usunac swoj wpis."""
        _project, user, ticket = await _create_test_data(db_session, slug="tt-del-own", email="tt-del-own@test.com")
        entry = await add_time_entry(
            ticket_id=ticket.id,
            user_id=user.id,
            duration_minutes=60,
            date_logged=date(2026, 2, 25),
            description=None,
            db=db_session,
        )
        assert not isinstance(entry, str)
        result = await delete_time_entry(entry.id, user.id, db_session)
        assert result is None

    async def test_non_owner_cannot_delete(self, db_session):
        """Inny uzytkownik nie moze usunac cudzego wpisu."""
        _project, user, ticket = await _create_test_data(db_session, slug="tt-del-noown", email="tt-del-noown@test.com")
        entry = await add_time_entry(
            ticket_id=ticket.id,
            user_id=user.id,
            duration_minutes=60,
            date_logged=date(2026, 2, 25),
            description=None,
            db=db_session,
        )
        assert not isinstance(entry, str)
        other_user = User(email="tt-del-other@test.com", password_hash=hash_password("pass"))
        db_session.add(other_user)
        await db_session.flush()

        result = await delete_time_entry(entry.id, other_user.id, db_session)
        assert result == "Brak uprawnien do usuniecia tego wpisu"

    async def test_nonexistent_entry(self, db_session):
        """Usuwanie nieistniejacego wpisu zwraca blad."""
        result = await delete_time_entry(uuid4(), uuid4(), db_session)
        assert result == "Wpis nie istnieje"


@pytest.mark.integration
class TestGetTicketTotalHours:
    async def test_no_entries(self, db_session):
        """Ticket bez wpisow ma 0 godzin."""
        _project, _user, ticket = await _create_test_data(db_session, slug="tt-tot-zero", email="tt-tot-zero@test.com")
        total = await get_ticket_total_hours(ticket.id, db_session)
        assert total == 0.0

    async def test_sums_entries(self, db_session):
        """Sumuje godziny z wielu wpisow."""
        _project, user, ticket = await _create_test_data(db_session, slug="tt-tot-sum", email="tt-tot-sum@test.com")
        await add_time_entry(ticket.id, user.id, 90, date(2026, 2, 25), None, db_session)
        await add_time_entry(ticket.id, user.id, 30, date(2026, 2, 26), None, db_session)
        total = await get_ticket_total_hours(ticket.id, db_session)
        assert total == 2.0  # 120 min = 2h

    async def test_single_entry(self, db_session):
        """Pojedynczy wpis zwraca poprawna liczbe godzin."""
        _project, user, ticket = await _create_test_data(db_session, slug="tt-tot-one", email="tt-tot-one@test.com")
        await add_time_entry(ticket.id, user.id, 45, date(2026, 2, 25), None, db_session)
        total = await get_ticket_total_hours(ticket.id, db_session)
        assert total == 0.75  # 45 min = 0.75h

    async def test_nonexistent_ticket_returns_zero(self, db_session):
        """Nieistniejacy ticket zwraca 0."""
        total = await get_ticket_total_hours(uuid4(), db_session)
        assert total == 0.0


@pytest.mark.integration
class TestAggregateHoursPerUser:
    async def test_hours_per_user(self, db_session):
        """Agregacja godzin per uzytkownik."""
        project, user, ticket = await _create_test_data(db_session, slug="tt-agg-usr", email="tt-agg-usr@test.com")
        await add_time_entry(ticket.id, user.id, 120, date(2026, 2, 25), None, db_session)
        result = await aggregate_hours_per_user(TimeTrackingFilter(project_ids=[project.id]), db_session)
        assert user.id in result
        assert result[user.id] == 2.0

    async def test_empty_project(self, db_session):
        """Projekt bez wpisow zwraca pusty dict."""
        project, _user, _ticket = await _create_test_data(db_session, slug="tt-agg-usr-e", email="tt-agg-usr-e@test.com")
        result = await aggregate_hours_per_user(TimeTrackingFilter(project_ids=[project.id]), db_session)
        assert result == {}

    async def test_date_filter(self, db_session):
        """Filtrowanie po zakresie dat."""
        project, user, ticket = await _create_test_data(db_session, slug="tt-agg-usr-d", email="tt-agg-usr-d@test.com")
        await add_time_entry(ticket.id, user.id, 60, date(2026, 1, 15), None, db_session)
        await add_time_entry(ticket.id, user.id, 60, date(2026, 3, 15), None, db_session)

        # Filtruj tylko styczen
        filters = TimeTrackingFilter(project_ids=[project.id], date_from=date(2026, 1, 1), date_to=date(2026, 1, 31))
        result = await aggregate_hours_per_user(filters, db_session)
        assert user.id in result
        assert result[user.id] == 1.0  # Tylko 60 min ze stycznia


@pytest.mark.integration
class TestAggregateHoursPerSprint:
    async def test_hours_per_sprint(self, db_session):
        """Agregacja godzin per sprint."""
        project, user, ticket = await _create_test_data(db_session, slug="tt-agg-spr", email="tt-agg-spr@test.com")
        await add_time_entry(ticket.id, user.id, 60, date(2026, 2, 25), None, db_session)
        result = await aggregate_hours_per_sprint(TimeTrackingFilter(project_ids=[project.id]), db_session)
        assert ticket.sprint_id in result
        assert result[ticket.sprint_id] == 1.0

    async def test_empty_project(self, db_session):
        """Projekt bez wpisow zwraca pusty dict."""
        project, _user, _ticket = await _create_test_data(db_session, slug="tt-agg-spr-e", email="tt-agg-spr-e@test.com")
        result = await aggregate_hours_per_sprint(TimeTrackingFilter(project_ids=[project.id]), db_session)
        assert result == {}

    async def test_multiple_entries_same_sprint(self, db_session):
        """Wiele wpisow w tym samym sprincie sa sumowane."""
        project, user, ticket = await _create_test_data(db_session, slug="tt-agg-spr-m", email="tt-agg-spr-m@test.com")
        await add_time_entry(ticket.id, user.id, 30, date(2026, 2, 25), None, db_session)
        await add_time_entry(ticket.id, user.id, 90, date(2026, 2, 26), None, db_session)
        result = await aggregate_hours_per_sprint(TimeTrackingFilter(project_ids=[project.id]), db_session)
        assert result[ticket.sprint_id] == 2.0  # 120 min = 2h


async def _create_test_data(db_session, slug="tt-test", email="tt-test@test.com"):
    """Helper: tworzy projekt, uzytkownika, sprint i ticket do testow."""
    project = Project(
        name="TT Test",
        slug=slug,
        code="P" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()

    user = User(email=email, password_hash=hash_password("testpass"))
    db_session.add(user)
    await db_session.flush()

    member = ProjectMember(project_id=project.id, user_id=user.id, role="member")
    db_session.add(member)
    await db_session.flush()

    sprint = Sprint(
        project_id=project.id,
        name="Sprint TT",
        start_date=date(2026, 3, 1),
        status="active",
    )
    db_session.add(sprint)
    await db_session.flush()

    ticket = Ticket(
        project_id=project.id,
        number=1,
        sprint_id=sprint.id,
        title="Ticket TT",
        status="todo",
        priority="medium",
    )
    db_session.add(ticket)
    await db_session.flush()

    return project, user, ticket
