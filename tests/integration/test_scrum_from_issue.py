"""Testy integracyjne -- tworzenie ticketa z bledu 500ki (ticket_create_from_issue).

Pokrywa endpoint POST /{slug}/scrum/tickets/create-from-issue/{issue_id}
z dashboard/scrum.py (linie 354-476).
"""

import secrets
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from monolynx.models.event import Event
from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.models.ticket import Ticket
from tests.conftest import login_session


def _make_project(name: str, slug: str) -> Project:
    return Project(
        name=name,
        slug=slug,
        code="P" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


def _make_issue(project_id: uuid.UUID, title: str = "ValueError: bad value") -> Issue:
    return Issue(
        project_id=project_id,
        fingerprint=secrets.token_hex(32),
        title=title,
        event_count=5,
        first_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        last_seen=datetime(2026, 3, 3, 12, 0, tzinfo=UTC),
    )


def _make_event(issue_id: uuid.UUID) -> Event:
    return Event(
        issue_id=issue_id,
        timestamp=datetime(2026, 3, 3, 12, 0, tzinfo=UTC),
        exception={
            "type": "ValueError",
            "value": "bad value",
            "stacktrace": {
                "frames": [
                    {
                        "filename": "app/views.py",
                        "function": "handle_request",
                        "lineno": 42,
                        "context_line": "    result = int(data)",
                    },
                    {
                        "filename": "app/utils.py",
                        "function": "parse_int",
                        "lineno": 10,
                        "context_line": "    return int(value)",
                    },
                ]
            },
        },
        request_data={
            "url": "https://example.com/api/items",
            "method": "POST",
        },
        environment={
            "environment": "production",
            "hostname": "web-01",
        },
    )


@pytest.mark.integration
class TestTicketCreateFromIssue:
    async def test_requires_auth(self, client, db_session):
        project = _make_project("Auth Proj", "auth-from-issue")
        db_session.add(project)
        await db_session.flush()
        issue = _make_issue(project.id)
        db_session.add(issue)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_issue_not_found_returns_404(self, client, db_session):
        await login_session(client, db_session, email="fromissue_404@test.com")
        project = _make_project("NotFound Proj", "notfound-from-issue")
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{uuid.uuid4()}",
        )
        assert resp.status_code == 404

    async def test_issue_already_has_ticket_redirects(self, client, db_session):
        await login_session(client, db_session, email="fromissue_existing@test.com")
        project = _make_project("Existing Proj", "existing-from-issue")
        db_session.add(project)
        await db_session.flush()

        issue = _make_issue(project.id)
        db_session.add(issue)
        await db_session.flush()

        existing_ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Existing ticket",
            status="backlog",
            priority="medium",
            issue_id=issue.id,
        )
        db_session.add(existing_ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/scrum/tickets/{existing_ticket.id}" in resp.headers["location"]

    async def test_success_creates_ticket_with_event_data(self, client, db_session):
        await login_session(client, db_session, email="fromissue_success@test.com")
        project = _make_project("Success Proj", "success-from-issue")
        db_session.add(project)
        await db_session.flush()

        issue = _make_issue(project.id, title="ValueError: bad value")
        db_session.add(issue)
        await db_session.flush()

        event = _make_event(issue.id)
        db_session.add(event)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.issue_id == issue.id))
        ticket = result.scalar_one()

        assert ticket.title == "[500ki] ValueError: bad value"
        assert ticket.status == "backlog"
        assert ticket.priority == "medium"
        assert ticket.issue_id == issue.id

        desc = ticket.description
        assert "## Powiazany blad 500ki" in desc
        assert "## Traceback" in desc
        assert 'File "app/views.py":42, in handle_request' in desc
        assert "result = int(data)" in desc
        assert "https://example.com/api/items" in desc
        assert "POST" in desc
        assert "production" in desc

    async def test_success_issue_without_events(self, client, db_session):
        await login_session(client, db_session, email="fromissue_noevents@test.com")
        project = _make_project("NoEvents Proj", "noevents-from-issue")
        db_session.add(project)
        await db_session.flush()

        issue = _make_issue(project.id, title="RuntimeError: something broke")
        db_session.add(issue)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.issue_id == issue.id))
        ticket = result.scalar_one()

        assert ticket.title == "[500ki] RuntimeError: something broke"
        assert ticket.status == "backlog"
        assert "## Traceback" in ticket.description
        assert "\u2014" in ticket.description

    async def test_project_not_found_returns_404(self, client, db_session):
        await login_session(client, db_session, email="fromissue_noproj@test.com")
        resp = await client.post(
            f"/dashboard/nonexistent-slug/scrum/tickets/create-from-issue/{uuid.uuid4()}",
        )
        assert resp.status_code == 404
