"""Testy integracyjne -- backlog i CRUD ticketow Scrum."""

import secrets

import pytest

from open_sentry.models.project import Project
from open_sentry.models.ticket import Ticket
from tests.conftest import login_session


@pytest.mark.integration
class TestBacklog:
    async def test_backlog_requires_auth(self, client, db_session):
        project = Project(
            name="BL Auth",
            slug="bl-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/backlog",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_backlog_empty(self, client, db_session):
        project = Project(
            name="BL Empty",
            slug="bl-empty",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="bl-empty@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog")
        assert resp.status_code == 200
        assert "Brak ticketow" in resp.text

    async def test_backlog_shows_ticket(self, client, db_session):
        project = Project(
            name="BL Show",
            slug="bl-show",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Test ticket w backlogu",
            status="backlog",
            priority="high",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="bl-show@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog")
        assert resp.status_code == 200
        assert "Test ticket w backlogu" in resp.text


@pytest.mark.integration
class TestTicketCreate:
    async def test_create_form_loads(self, client, db_session):
        project = Project(
            name="TC Form",
            slug="tc-form",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tc-form@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/create")
        assert resp.status_code == 200
        assert "Nowy ticket" in resp.text

    async def test_create_ticket_success(self, client, db_session):
        project = Project(
            name="TC Succ",
            slug="tc-succ",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tc-succ@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Nowy bug", "priority": "high", "story_points": "3"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/backlog" in resp.headers["location"]

    async def test_create_ticket_empty_title(self, client, db_session):
        project = Project(
            name="TC Empty",
            slug="tc-empty",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tc-empty@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "", "priority": "medium"},
        )
        assert resp.status_code == 200
        assert "Tytul jest wymagany" in resp.text


@pytest.mark.integration
class TestTicketDetail:
    async def test_ticket_detail_loads(self, client, db_session):
        project = Project(
            name="TD Det",
            slug="td-det",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Detale ticketa",
            description="Opis testowy",
            status="backlog",
            priority="medium",
            story_points=5,
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="td-det@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")
        assert resp.status_code == 200
        assert "Detale ticketa" in resp.text
        assert "Opis testowy" in resp.text

    async def test_ticket_not_found(self, client, db_session):
        project = Project(
            name="TD NF",
            slug="td-nf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="td-nf@test.com")
        import uuid

        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{fake_id}")
        assert resp.status_code == 404


@pytest.mark.integration
class TestTicketEdit:
    async def test_edit_form_loads(self, client, db_session):
        project = Project(
            name="TE Form",
            slug="te-form",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Edytuj mnie",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="te-form@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit")
        assert resp.status_code == 200
        assert "Edytuj ticket" in resp.text
        assert "Edytuj mnie" in resp.text

    async def test_edit_ticket_success(self, client, db_session):
        project = Project(
            name="TE Succ",
            slug="te-succ",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Stary tytul",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="te-succ@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Nowy tytul", "priority": "high", "status": "todo"},
            follow_redirects=False,
        )
        assert resp.status_code == 303


@pytest.mark.integration
class TestTicketDelete:
    async def test_delete_ticket(self, client, db_session):
        project = Project(
            name="TD Del",
            slug="td-del",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Do usuniecia",
            status="backlog",
            priority="low",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="td-del@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/backlog" in resp.headers["location"]


@pytest.mark.integration
class TestTicketStatusUpdate:
    async def test_patch_status(self, client, db_session):
        project = Project(
            name="TS Patch",
            slug="ts-patch",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(project_id=project.id, title="Status test", status="todo", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ts-patch@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/status",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 200

    async def test_patch_invalid_status(self, client, db_session):
        project = Project(
            name="TS Inv",
            slug="ts-inv",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(project_id=project.id, title="Inv status", status="todo", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ts-inv@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/status",
            json={"status": "invalid"},
        )
        assert resp.status_code == 422
