"""Testy integracyjne -- dashboard monitoringu (lista, CRUD, toggle, delete)."""

import secrets
import uuid

import pytest

from monolynx.models.monitor import Monitor
from monolynx.models.project import Project
from tests.conftest import login_session


@pytest.mark.integration
class TestMonitorList:
    async def test_monitor_list_requires_auth(self, client, db_session):
        project = Project(
            name="ML Auth",
            slug="ml-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/monitoring/",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_monitor_list_empty(self, client, db_session):
        project = Project(
            name="ML Empty",
            slug="ml-empty",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ml-empty@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/")
        assert resp.status_code == 200
        assert "Brak monitorow" in resp.text

    async def test_monitor_list_shows_monitors(self, client, db_session):
        project = Project(
            name="ML Show",
            slug="ml-show",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Example Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="ml-show@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/")
        assert resp.status_code == 200
        assert "Example Monitor" in resp.text
        assert "https://example.com" in resp.text

    async def test_monitor_list_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="ml-noproj@test.com")
        resp = await client.get("/dashboard/nonexistent-slug/monitoring/")
        assert resp.status_code == 404


@pytest.mark.integration
class TestMonitorCreateForm:
    async def test_create_form_requires_auth(self, client, db_session):
        project = Project(
            name="MCF Auth",
            slug="mcf-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/monitoring/create",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_create_form_loads(self, client, db_session):
        project = Project(
            name="MCF Load",
            slug="mcf-load",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcf-load@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/create")
        assert resp.status_code == 200
        assert "Nowy monitor" in resp.text

    async def test_create_form_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="mcf-noproj@test.com")
        resp = await client.get("/dashboard/nonexistent-slug/monitoring/create")
        assert resp.status_code == 404


@pytest.mark.integration
class TestMonitorCreate:
    async def test_create_monitor_success(self, client, db_session):
        project = Project(
            name="MC Succ",
            slug="mc-succ",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mc-succ@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "My Monitor",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/monitoring/" in resp.headers["location"]

    async def test_create_monitor_with_custom_interval(self, client, db_session):
        project = Project(
            name="MC Intv",
            slug="mc-intv",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mc-intv@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Hourly Check",
                "interval_value": "2",
                "interval_unit": "hours",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/monitoring/" in resp.headers["location"]

    async def test_create_monitor_empty_url(self, client, db_session):
        project = Project(
            name="MC Empty",
            slug="mc-empty",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mc-empty@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "",
                "name": "",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "URL jest wymagany" in resp.text

    async def test_create_monitor_invalid_url_no_scheme(self, client, db_session):
        project = Project(
            name="MC NoScheme",
            slug="mc-noscheme",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mc-noscheme@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "example.com",
                "name": "",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "http://" in resp.text or "https://" in resp.text

    async def test_create_monitor_ssrf_localhost_blocked(self, client, db_session):
        project = Project(
            name="MC SSRF",
            slug="mc-ssrf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mc-ssrf@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://localhost:8080",
                "name": "",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_create_monitor_requires_auth(self, client, db_session):
        project = Project(
            name="MC Auth",
            slug="mc-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_create_monitor_invalid_interval_value(self, client, db_session):
        project = Project(
            name="MC InvIntv",
            slug="mc-invintv",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mc-invintv@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "",
                "interval_value": "abc",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "liczba" in resp.text

    async def test_create_monitor_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="mc-noproj@test.com")
        resp = await client.post(
            "/dashboard/nonexistent-slug/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestMonitorDetail:
    async def test_monitor_detail_loads(self, client, db_session):
        project = Project(
            name="MD Det",
            slug="md-det",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Detail Monitor",
            interval_value=10,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="md-det@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        assert "Detail Monitor" in resp.text
        assert "https://example.com" in resp.text

    async def test_monitor_detail_requires_auth(self, client, db_session):
        project = Project(
            name="MD Auth",
            slug="md-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Auth Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_monitor_detail_not_found(self, client, db_session):
        project = Project(
            name="MD NF",
            slug="md-nf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="md-nf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{fake_id}")
        assert resp.status_code == 404

    async def test_monitor_detail_shows_no_checks_message(self, client, db_session):
        project = Project(
            name="MD NoChk",
            slug="md-nochk",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="No Checks Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="md-nochk@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        assert "Brak sprawdzen" in resp.text


@pytest.mark.integration
class TestMonitorToggle:
    async def test_toggle_monitor_off(self, client, db_session):
        project = Project(
            name="MT Off",
            slug="mt-off",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Toggle Off Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="mt-off@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Verify the monitor was toggled off
        await db_session.refresh(monitor)
        assert monitor.is_active is False

    async def test_toggle_monitor_on(self, client, db_session):
        project = Project(
            name="MT On",
            slug="mt-on",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Toggle On Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=False,
        )
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="mt-on@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Verify the monitor was toggled on
        await db_session.refresh(monitor)
        assert monitor.is_active is True

    async def test_toggle_requires_auth(self, client, db_session):
        project = Project(
            name="MT Auth",
            slug="mt-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Auth Toggle Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_toggle_nonexistent_monitor_returns_404(self, client, db_session):
        project = Project(
            name="MT NF",
            slug="mt-nf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mt-nf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{fake_id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestMonitorDelete:
    async def test_delete_monitor(self, client, db_session):
        project = Project(
            name="MD Del",
            slug="md-del",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Delete Me",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()
        monitor_id = monitor.id

        await login_session(client, db_session, email="md-del@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/monitoring/" in resp.headers["location"]

    async def test_delete_requires_auth(self, client, db_session):
        project = Project(
            name="MD DelAuth",
            slug="md-delauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Auth Delete Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_delete_nonexistent_monitor_returns_404(self, client, db_session):
        project = Project(
            name="MD DelNF",
            slug="md-delnf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="md-delnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{fake_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404
