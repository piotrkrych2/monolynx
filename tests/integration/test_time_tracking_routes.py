"""Testy integracyjne -- routes time trackingu."""

import secrets
import uuid
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.time_tracking_entry import TimeTrackingEntry
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from monolynx.services.time_tracking import add_time_entry
from tests.conftest import login_session


async def _setup_project_with_ticket(db_session, slug):
    """Helper: tworzy projekt, sprint, ticket (bez usera)."""
    project = Project(
        name="TT Route",
        slug=slug,
        code="P" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
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
        title="Ticket TT Route",
        status="todo",
        priority="medium",
    )
    db_session.add(ticket)
    await db_session.flush()

    return project, ticket


async def _login_and_add_member(client, db_session, project, email):
    """Helper: loguje uzytkownika i dodaje go jako czlonka projektu.

    login_session tworzy nowego usera, wiec musimy go potem dodac do projektu.
    Zwraca user_id.
    """
    await login_session(client, db_session, email=email)

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()

    member = ProjectMember(project_id=project.id, user_id=user.id, role="member")
    db_session.add(member)
    await db_session.flush()

    return user


@pytest.mark.integration
class TestTimeTrackingLog:
    async def test_log_time_success(self, client, db_session):
        """POST log tworzy wpis i zwraca 201."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-log-ok")
        await _login_and_add_member(client, db_session, project, "tt-log-ok@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={
                "ticket_id": str(ticket.id),
                "duration_minutes": "90m",
                "date_logged": "2026-02-25",
                "description": "Code review",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["duration_minutes"] == 90
        assert data["ticket_id"] == str(ticket.id)
        assert data["description"] == "Code review"

    async def test_log_time_without_description(self, client, db_session):
        """POST bez opisu dziala poprawnie."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-log-nd")
        await _login_and_add_member(client, db_session, project, "tt-log-nd@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={
                "ticket_id": str(ticket.id),
                "duration_minutes": 60,
                "date_logged": "2026-02-25",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["description"] is None

    async def test_log_time_invalid_duration(self, client, db_session):
        """POST z duration <= 0 zwraca 400."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-log-dur")
        await _login_and_add_member(client, db_session, project, "tt-log-dur@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={
                "ticket_id": str(ticket.id),
                "duration_minutes": 0,
                "date_logged": "2026-02-25",
            },
        )
        assert resp.status_code == 400

    async def test_log_time_invalid_date(self, client, db_session):
        """POST z nieprawidlowa data zwraca 400."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-log-dt")
        await _login_and_add_member(client, db_session, project, "tt-log-dt@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={
                "ticket_id": str(ticket.id),
                "duration_minutes": 60,
                "date_logged": "not-a-date",
            },
        )
        assert resp.status_code == 400

    async def test_log_time_requires_auth(self, client, db_session):
        """POST bez zalogowania zwraca 401."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-log-auth")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={
                "ticket_id": str(ticket.id),
                "duration_minutes": 60,
                "date_logged": "2026-02-25",
            },
        )
        assert resp.status_code == 401

    async def test_log_time_nonexistent_ticket(self, client, db_session):
        """POST z nieistniejacym ticket_id zwraca 404."""
        project, _ticket = await _setup_project_with_ticket(db_session, "tt-log-noticket")
        await _login_and_add_member(client, db_session, project, "tt-log-noticket@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={
                "ticket_id": str(uuid4()),
                "duration_minutes": 60,
                "date_logged": "2026-02-25",
            },
        )
        assert resp.status_code == 404
        assert "Ticket nie istnieje" in resp.json()["error"]

    async def test_log_time_created_via_ai_is_false(self, client, db_session):
        """Wpis z frontendu ma created_via_ai=False."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-log-noai")
        await _login_and_add_member(client, db_session, project, "tt-log-noai@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={
                "ticket_id": str(ticket.id),
                "duration_minutes": 60,
                "date_logged": "2026-02-25",
            },
        )
        assert resp.status_code == 201
        entry_id = resp.json()["id"]

        result = await db_session.execute(select(TimeTrackingEntry).where(TimeTrackingEntry.id == uuid.UUID(entry_id)))
        entry = result.scalar_one()
        assert entry.created_via_ai is False


@pytest.mark.integration
class TestTimeTrackingDelete:
    async def test_delete_own_entry(self, client, db_session):
        """DELETE wlasnego wpisu zwraca 200."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-del-own")
        user = await _login_and_add_member(client, db_session, project, "tt-del-own@test.com")

        entry = await add_time_entry(ticket.id, user.id, 60, date(2026, 2, 25), None, db_session)
        assert not isinstance(entry, str)

        resp = await client.delete(f"/dashboard/{project.slug}/scrum/time-tracking/{entry.id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    async def test_delete_others_entry_forbidden(self, client, db_session):
        """DELETE cudzego wpisu zwraca 403."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-del-403")

        # Tworzymy usera A (wlasciciela wpisu) recznie -- nie logujemy go
        owner = User(email="tt-del-403-owner@test.com", password_hash=hash_password("testpass123"))
        db_session.add(owner)
        await db_session.flush()
        owner_member = ProjectMember(project_id=project.id, user_id=owner.id, role="member")
        db_session.add(owner_member)
        await db_session.flush()

        entry = await add_time_entry(ticket.id, owner.id, 60, date(2026, 2, 25), None, db_session)
        assert not isinstance(entry, str)

        # Logujemy sie jako inny uzytkownik (login_session tworzy nowego usera)
        await login_session(client, db_session, email="tt-del-403-other@test.com")

        resp = await client.delete(f"/dashboard/{project.slug}/scrum/time-tracking/{entry.id}")
        assert resp.status_code == 403

    async def test_delete_nonexistent_entry(self, client, db_session):
        """DELETE nieistniejacego wpisu zwraca 404."""
        project, _ticket = await _setup_project_with_ticket(db_session, "tt-del-404")
        await _login_and_add_member(client, db_session, project, "tt-del-404@test.com")

        resp = await client.delete(f"/dashboard/{project.slug}/scrum/time-tracking/{uuid4()}")
        assert resp.status_code == 404

    async def test_delete_requires_auth(self, client, db_session):
        """DELETE bez zalogowania zwraca 401."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-del-auth")

        # Tworzymy usera i wpis recznie, ale NIE logujemy
        user = User(email="tt-del-auth@test.com", password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        member = ProjectMember(project_id=project.id, user_id=user.id, role="member")
        db_session.add(member)
        await db_session.flush()

        entry = await add_time_entry(ticket.id, user.id, 60, date(2026, 2, 25), None, db_session)
        assert not isinstance(entry, str)

        resp = await client.delete(f"/dashboard/{project.slug}/scrum/time-tracking/{entry.id}")
        assert resp.status_code == 401


@pytest.mark.integration
class TestTicketDetailWithTimeEntries:
    async def test_ticket_detail_shows_time_entries(self, client, db_session):
        """Strona ticketu wyswietla wpisy czasu pracy."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-detail-te")
        user = await _login_and_add_member(client, db_session, project, "tt-detail-te@test.com")

        await add_time_entry(ticket.id, user.id, 90, date(2026, 2, 25), "Code review", db_session)

        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")
        assert resp.status_code == 200
        assert "Code review" in resp.text
        assert "Czas pracy" in resp.text

    async def test_ticket_detail_shows_total_hours(self, client, db_session):
        """Strona ticketu wyswietla sume godzin."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-detail-tot")
        user = await _login_and_add_member(client, db_session, project, "tt-detail-tot@test.com")

        await add_time_entry(ticket.id, user.id, 120, date(2026, 2, 25), None, db_session)

        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")
        assert resp.status_code == 200
        assert "2h" in resp.text
        assert "Zalogowany czas" in resp.text

    async def test_ticket_detail_no_entries(self, client, db_session):
        """Strona ticketu bez wpisow wyswietla pusta liste."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-detail-empty")
        await _login_and_add_member(client, db_session, project, "tt-detail-empty@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")
        assert resp.status_code == 200
        assert "Brak wpisow czasu pracy" in resp.text

    async def test_ticket_detail_has_log_button(self, client, db_session):
        """Strona ticketu ma przycisk 'Zaloguj czas'."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-detail-btn")
        await _login_and_add_member(client, db_session, project, "tt-detail-btn@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")
        assert resp.status_code == 200
        assert "Zaloguj czas" in resp.text

    async def test_ticket_detail_shows_ai_badge(self, client, db_session):
        """Wpis AI wyswietla badge AI na stronie ticketu."""
        project, ticket = await _setup_project_with_ticket(db_session, "tt-detail-ai")
        user = await _login_and_add_member(client, db_session, project, "tt-detail-ai@test.com")

        await add_time_entry(ticket.id, user.id, 60, date(2026, 2, 25), "AI work", db_session, created_via_ai=True)

        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")
        assert resp.status_code == 200
        assert "AI work" in resp.text
        # Badge AI powinien byc widoczny
        assert ">AI</span>" in resp.text
