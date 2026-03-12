"""Testy integracyjne -- cykl zycia sprintow."""

import secrets
from datetime import date

import pytest

from monolynx.models.project import Project
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from tests.conftest import login_session


@pytest.mark.integration
class TestSprintList:
    async def test_sprint_list_empty(self, client, db_session):
        project = Project(
            name="SL Empty",
            slug="sl-empty",
            code="SLE",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sl-empty@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/sprints")
        assert resp.status_code == 200
        assert "Brak sprintów" in resp.text

    async def test_sprint_list_shows_sprint(self, client, db_session):
        project = Project(
            name="SL Show",
            slug="sl-show",
            code="SLS",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint widoczny",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        await login_session(client, db_session, email="sl-show@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/sprints")
        assert resp.status_code == 200
        assert "Sprint widoczny" in resp.text


@pytest.mark.integration
class TestSprintCreate:
    async def test_create_sprint_success(self, client, db_session):
        project = Project(
            name="SC Succ",
            slug="sc-succ",
            code="SCS",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sc-succ@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/create",
            data={
                "name": "Sprint Nowy",
                "start_date": "2026-03-01",
                "goal": "Cel testowy",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/sprints" in resp.headers["location"]

    async def test_create_sprint_missing_fields(self, client, db_session):
        project = Project(
            name="SC Miss",
            slug="sc-miss",
            code="SCM",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sc-miss@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/create",
            data={"name": "", "start_date": ""},
        )
        assert resp.status_code == 200
        assert "Nazwa i data rozpoczecia sa wymagane" in resp.text


@pytest.mark.integration
class TestSprintLifecycle:
    async def test_start_sprint(self, client, db_session):
        project = Project(
            name="SL Start",
            slug="sl-start",
            code="SLS",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Do startu",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        await login_session(client, db_session, email="sl-start@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{sprint.id}/start",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/board" in resp.headers["location"]

    async def test_cannot_start_two_sprints(self, client, db_session):
        project = Project(
            name="SL Two",
            slug="sl-two",
            code="SLT",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        active_sprint = Sprint(
            project_id=project.id,
            name="Aktywny",
            start_date=date(2026, 3, 1),
            status="active",
        )
        planning_sprint = Sprint(
            project_id=project.id,
            name="Planowany",
            start_date=date(2026, 3, 15),
            status="planning",
        )
        db_session.add_all([active_sprint, planning_sprint])
        await db_session.flush()

        await login_session(client, db_session, email="sl-two@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{planning_sprint.id}/start",
        )
        assert resp.status_code == 200
        assert "aktywny sprint" in resp.text

    async def test_complete_sprint_moves_tickets_to_backlog(self, client, db_session):
        project = Project(
            name="SL Compl",
            slug="sl-compl",
            code="SLC",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Do zakonczenia",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket_done = Ticket(
            project_id=project.id,
            number=1,
            sprint_id=sprint.id,
            title="Zrobiony",
            status="done",
            priority="medium",
        )
        ticket_in_progress = Ticket(
            project_id=project.id,
            number=2,
            sprint_id=sprint.id,
            title="Niedokonczony",
            status="in_progress",
            priority="high",
        )
        db_session.add_all([ticket_done, ticket_in_progress])
        await db_session.flush()

        await login_session(client, db_session, email="sl-compl@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{sprint.id}/complete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/sprints" in resp.headers["location"]

        # Sprawdz ze niedokonczony ticket wraca do backloga
        from sqlalchemy import select

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_in_progress.id))
        ticket = result.scalar_one()
        assert ticket.status == "backlog"
        assert ticket.sprint_id is None

        # Zrobiony ticket zostaje w sprincie
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_done.id))
        done = result.scalar_one()
        assert done.status == "done"
        assert done.sprint_id == sprint.id
