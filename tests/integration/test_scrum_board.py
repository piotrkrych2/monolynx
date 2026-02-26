"""Testy integracyjne -- tablica Kanban Scrum."""

import secrets
from datetime import date

import pytest

from monolynx.models.project import Project
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from tests.conftest import login_session


@pytest.mark.integration
class TestBoard:
    async def test_board_no_active_sprint(self, client, db_session):
        project = Project(
            name="Board NoSpr",
            slug="board-nospr",
            code="BOA",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="board-nospr@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/board")
        assert resp.status_code == 200
        assert "Brak aktywnego sprintu" in resp.text

    async def test_board_with_active_sprint(self, client, db_session):
        project = Project(
            name="Board Act",
            slug="board-act",
            code="BOA",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint Aktywny",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket_todo = Ticket(
            project_id=project.id,
            number=1,
            sprint_id=sprint.id,
            title="Ticket TODO",
            status="todo",
            priority="medium",
        )
        ticket_done = Ticket(
            project_id=project.id,
            number=2,
            sprint_id=sprint.id,
            title="Ticket DONE",
            status="done",
            priority="low",
        )
        db_session.add_all([ticket_todo, ticket_done])
        await db_session.flush()

        await login_session(client, db_session, email="board-act@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/board")
        assert resp.status_code == 200
        assert "Ticket TODO" in resp.text
        assert "Ticket DONE" in resp.text

    async def test_board_requires_auth(self, client, db_session):
        project = Project(
            name="Board Auth",
            slug="board-auth",
            code="BOA",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/board",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]
