"""Testy integracyjne -- modul 500ki (issues, events)."""

import secrets
import uuid
from datetime import UTC, datetime

import pytest

from monolynx.models.event import Event
from monolynx.models.issue import Issue
from monolynx.models.project import Project
from tests.conftest import login_session


@pytest.mark.integration
class TestIssueList:
    async def test_issue_list_requires_auth(self, client, db_session):
        """GET /dashboard/{slug}/500ki/issues bez sesji redirectuje na login."""
        project = Project(
            name="SI Auth",
            slug="si-auth",
            code="SIA",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/500ki/issues",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_issue_list_empty(self, client, db_session):
        """Lista issues jest pusta -- wyswietla komunikat."""
        project = Project(
            name="SI Empty",
            slug="si-empty",
            code="SIE",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="si-empty@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        assert "Brak bledow" in resp.text

    async def test_issue_list_shows_issue(self, client, db_session):
        """Lista issues wyswietla istniejacy issue."""
        project = Project(
            name="SI Show",
            slug="si-show",
            code="SIS",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="abc123def456",
            title="ValueError: invalid literal",
            status="unresolved",
            event_count=5,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="si-show@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        assert "ValueError: invalid literal" in resp.text
        assert "5x" in resp.text

    async def test_issue_list_shows_multiple_issues(self, client, db_session):
        """Lista issues wyswietla wiele issues."""
        project = Project(
            name="SI Multi",
            slug="si-multi",
            code="SIM",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        issue1 = Issue(
            project_id=project.id,
            fingerprint="aaa111",
            title="TypeError: NoneType",
            status="unresolved",
            event_count=3,
        )
        issue2 = Issue(
            project_id=project.id,
            fingerprint="bbb222",
            title="KeyError: missing_key",
            status="resolved",
            event_count=1,
        )
        db_session.add_all([issue1, issue2])
        await db_session.flush()

        await login_session(client, db_session, email="si-multi@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues")
        assert resp.status_code == 200
        assert "TypeError: NoneType" in resp.text
        assert "KeyError: missing_key" in resp.text
        assert "2 issues" in resp.text

    async def test_issue_list_project_not_found(self, client, db_session):
        """Nieistniejacy projekt zwraca 404."""
        await login_session(client, db_session, email="si-notfound@test.com")
        resp = await client.get("/dashboard/nieistniejacy-slug/500ki/issues")
        assert resp.status_code == 404


@pytest.mark.integration
class TestIssueDetail:
    async def test_issue_detail_requires_auth(self, client, db_session):
        """GET /dashboard/{slug}/500ki/issues/{id} bez sesji redirectuje na login."""
        project = Project(
            name="SD Auth",
            slug="sd-auth",
            code="SDA",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="authfp123",
            title="Auth test issue",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/500ki/issues/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_issue_detail_loads(self, client, db_session):
        """Strona szczegolowa issue wyswietla tytul i dane."""
        project = Project(
            name="SD Det",
            slug="sd-det",
            code="SDD",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="detfp456",
            title="RuntimeError: something broke",
            culprit="app/views.py in handle_request",
            status="unresolved",
            event_count=3,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="sd-det@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "RuntimeError: something broke" in resp.text
        assert "app/views.py in handle_request" in resp.text
        assert "unresolved" in resp.text

    async def test_issue_detail_with_events(self, client, db_session):
        """Strona szczegolowa issue wyswietla powiazane eventy."""
        project = Project(
            name="SD Evt",
            slug="sd-evt",
            code="SDE",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="evtfp789",
            title="IndexError: list index out of range",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime(2026, 2, 20, 12, 0, 0, tzinfo=UTC),
            exception={
                "type": "IndexError",
                "value": "list index out of range",
                "stacktrace": {
                    "frames": [
                        {
                            "filename": "app/utils.py",
                            "function": "get_item",
                            "lineno": 15,
                        }
                    ]
                },
            },
        )
        db_session.add(event)
        await db_session.flush()

        await login_session(client, db_session, email="sd-evt@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 200
        assert "IndexError: list index out of range" in resp.text
        assert "IndexError" in resp.text
        assert "Eventy (1)" in resp.text

    async def test_issue_detail_not_found(self, client, db_session):
        """Nieistniejacy issue zwraca 404."""
        project = Project(
            name="SD NF",
            slug="sd-nf",
            code="SDN",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sd-nf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/{project.slug}/500ki/issues/{fake_id}")
        assert resp.status_code == 404

    async def test_issue_detail_wrong_project(self, client, db_session):
        """Issue z innego projektu nie jest widoczny -- zwraca 404."""
        project_a = Project(
            name="SD ProjA",
            slug="sd-proj-a",
            code="SDPA",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        project_b = Project(
            name="SD ProjB",
            slug="sd-proj-b",
            code="SDPB",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add_all([project_a, project_b])
        await db_session.flush()

        issue = Issue(
            project_id=project_a.id,
            fingerprint="wrongprojfp",
            title="Issue in project A",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="sd-wrongproj@test.com")
        # Probujemy otworzyc issue projektu A w kontekscie projektu B
        resp = await client.get(f"/dashboard/{project_b.slug}/500ki/issues/{issue.id}")
        assert resp.status_code == 404


@pytest.mark.integration
class TestSetupGuide:
    async def test_setup_guide_requires_auth(self, client, db_session):
        """GET /dashboard/{slug}/500ki/setup-guide bez sesji redirectuje na login."""
        project = Project(
            name="SG Auth",
            slug="sg-auth",
            code="SGA",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/500ki/setup-guide",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_setup_guide_loads(self, client, db_session):
        """Strona instrukcji instalacji wyswietla sie poprawnie."""
        project = Project(
            name="SG Load",
            slug="sg-load",
            code="SGL",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sg-load@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/500ki/setup-guide")
        assert resp.status_code == 200
        assert "Instrukcja instalacji" in resp.text
        assert "Zainstaluj SDK" in resp.text
        assert "MIDDLEWARE" in resp.text

    async def test_setup_guide_project_not_found(self, client, db_session):
        """Setup guide dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="sg-notfound@test.com")
        resp = await client.get("/dashboard/brak-projektu/500ki/setup-guide")
        assert resp.status_code == 404
