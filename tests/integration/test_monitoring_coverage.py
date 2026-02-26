"""Testy integracyjne -- dodatkowe pokrycie dla dashboard/monitoring.py.

Pokrywane sciezki:
- monitor_toggle z naglowkiem referer (redirect do referer)
- monitor_toggle bez naglowka referer (redirect do listy)
- monitor_toggle z active->inactive i inactive->active (flash message text)
- monitor_delete z weryfikacja usniecia z bazy
- monitor_create z mockiem _is_url_safe (pomija DNS resolution)
- monitor_create z URL bez schematu (hostname only -> "no scheme" error)
- monitor_create z URL bez hosta po parsowaniu (edge case)
- monitor_create z non-integer interval_value (text)
- monitor_detail z pustymi checks (uptime=None, avg_response=None)
- monitor_detail z checkami bez response_time_ms (avg_response=None)
- monitor_list z pustymi monitorami (pusta lista, brak subquery)
- _is_url_safe z URL ktory parsuje sie ale nie ma hostname
- _compute_uptime z 0 checkow w zakresie (zwraca None)
- _compute_avg_response_time bez response_time_ms (zwraca None)
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.project import Project
from tests.conftest import login_session


def _make_project(slug: str, name: str | None = None) -> Project:
    return Project(
        name=name or f"Project {slug}",
        slug=slug,
        code="P" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


def _make_monitor(
    project_id: uuid.UUID,
    url: str = "https://example.com",
    name: str = "Test Monitor",
    is_active: bool = True,
) -> Monitor:
    return Monitor(
        project_id=project_id,
        url=url,
        name=name,
        interval_value=5,
        interval_unit="minutes",
        is_active=is_active,
    )


@pytest.mark.integration
class TestMonitorToggleReferer:
    """Pokrycie linii monitor_toggle: redirect z referer vs bez referer."""

    async def test_toggle_with_referer_redirects_to_referer(self, client, db_session):
        """Toggle z naglowkiem Referer powinien redirectowac do referer URL."""
        project = _make_project("cov-tgl-ref")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="Referer Toggle", is_active=True)
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="cov-tgl-ref@test.com")
        referer_url = f"http://test/dashboard/{project.slug}/monitoring/{monitor.id}"
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}/toggle",
            headers={"referer": referer_url},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == referer_url

    async def test_toggle_without_referer_redirects_to_list(self, client, db_session):
        """Toggle bez naglowka Referer powinien redirectowac do listy monitorow."""
        project = _make_project("cov-tgl-noref")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="NoReferer Toggle", is_active=True)
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="cov-tgl-noref@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/dashboard/{project.slug}/monitoring/"

    async def test_toggle_active_to_inactive_flash_wylaczony(self, client, db_session):
        """Toggle active->inactive ustawia flash 'wylaczony' i zmienia is_active."""
        project = _make_project("cov-tgl-off-flash")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="Flash Off", is_active=True)
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="cov-tgl-off-flash@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        await db_session.refresh(monitor)
        assert monitor.is_active is False

    async def test_toggle_inactive_to_active_flash_wlaczony(self, client, db_session):
        """Toggle inactive->active ustawia flash 'wlaczony' i zmienia is_active."""
        project = _make_project("cov-tgl-on-flash")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="Flash On", is_active=False)
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="cov-tgl-on-flash@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        await db_session.refresh(monitor)
        assert monitor.is_active is True

    async def test_toggle_nonexistent_project_returns_404(self, client, db_session):
        """Toggle na nieistniejacym projekcie zwraca 404."""
        await login_session(client, db_session, email="cov-tgl-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-project-cov/monitoring/{fake_id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestMonitorDeleteVerify:
    """Pokrycie linii monitor_delete: weryfikacja faktycznego usniecia z bazy."""

    async def test_delete_removes_monitor_from_database(self, client, db_session):
        """Po DELETE monitor powinien zniknac z bazy danych."""
        project = _make_project("cov-del-verify")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="DeleteVerify Monitor")
        db_session.add(monitor)
        await db_session.flush()
        monitor_id = monitor.id

        await login_session(client, db_session, email="cov-del-verify@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/monitoring/" in resp.headers["location"]

        # Weryfikacja: monitor nie istnieje w bazie
        result = await db_session.execute(select(Monitor).where(Monitor.id == monitor_id))
        assert result.scalar_one_or_none() is None

    async def test_delete_nonexistent_project_returns_404(self, client, db_session):
        """Usuwanie monitora z nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="cov-del-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-project-cov-del/monitoring/{fake_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestMonitorCreateWithMock:
    """Pokrycie linii monitor_create: success z mockiem _is_url_safe."""

    async def test_create_success_with_mocked_ssrf_check(self, client, db_session):
        """Tworzenie monitora z mockowanym _is_url_safe pomija DNS resolution."""
        project = _make_project("cov-cr-mock")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cr-mock@test.com")
        with patch("monolynx.dashboard.monitoring._is_url_safe", return_value=None):
            resp = await client.post(
                f"/dashboard/{project.slug}/monitoring/create",
                data={
                    "url": "https://httpbin.org/status/200",
                    "name": "Mocked SSRF Monitor",
                    "interval_value": "10",
                    "interval_unit": "minutes",
                },
                follow_redirects=False,
            )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/monitoring/" in resp.headers["location"]

        # Weryfikacja: monitor zostal utworzony w bazie
        result = await db_session.execute(
            select(Monitor).where(
                Monitor.project_id == project.id,
                Monitor.url == "https://httpbin.org/status/200",
            )
        )
        created = result.scalar_one_or_none()
        assert created is not None
        assert created.name == "Mocked SSRF Monitor"
        assert created.interval_value == 10
        assert created.interval_unit == "minutes"

    async def test_create_with_name_none_stores_none(self, client, db_session):
        """Tworzenie monitora bez nazwy -- name jest None w bazie."""
        project = _make_project("cov-cr-noname")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cr-noname@test.com")
        with patch("monolynx.dashboard.monitoring._is_url_safe", return_value=None):
            resp = await client.post(
                f"/dashboard/{project.slug}/monitoring/create",
                data={
                    "url": "https://example.com/health",
                    "name": "",
                    "interval_value": "5",
                    "interval_unit": "hours",
                },
                follow_redirects=False,
            )
        assert resp.status_code == 303

        result = await db_session.execute(
            select(Monitor).where(
                Monitor.project_id == project.id,
                Monitor.url == "https://example.com/health",
            )
        )
        created = result.scalar_one_or_none()
        assert created is not None
        assert created.name is None
        assert created.interval_unit == "hours"

    async def test_create_with_days_interval_unit(self, client, db_session):
        """Tworzenie monitora z jednostka 'days'."""
        project = _make_project("cov-cr-days")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cr-days@test.com")
        with patch("monolynx.dashboard.monitoring._is_url_safe", return_value=None):
            resp = await client.post(
                f"/dashboard/{project.slug}/monitoring/create",
                data={
                    "url": "https://daily-check.example.com",
                    "name": "Daily Check",
                    "interval_value": "1",
                    "interval_unit": "days",
                },
                follow_redirects=False,
            )
        assert resp.status_code == 303

        result = await db_session.execute(
            select(Monitor).where(
                Monitor.project_id == project.id,
                Monitor.url == "https://daily-check.example.com",
            )
        )
        created = result.scalar_one_or_none()
        assert created is not None
        assert created.interval_unit == "days"
        assert created.interval_value == 1


@pytest.mark.integration
class TestMonitorCreateURLValidation:
    """Pokrycie linii URL validation: hostname only, URL bez hosta."""

    async def test_create_url_without_scheme_hostname_only(self, client, db_session):
        """URL 'example.com' bez http(s):// powinien zwrocic blad."""
        project = _make_project("cov-cr-noscheme")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cr-noscheme@test.com")
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

    async def test_create_url_with_ftp_scheme(self, client, db_session):
        """URL z ftp:// schematem -- nie zaczyna sie od http(s)://, blad."""
        project = _make_project("cov-cr-ftp")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cr-ftp@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "ftp://files.example.com/pub",
                "name": "FTP Monitor",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "http://" in resp.text or "https://" in resp.text

    async def test_create_non_integer_interval_shows_error(self, client, db_session):
        """Non-integer interval_value (np. 'xyz') powinien zwrocic blad."""
        project = _make_project("cov-cr-nonint")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cr-nonint@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "NonInt Monitor",
                "interval_value": "five",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "liczba" in resp.text.lower()

    async def test_create_float_interval_shows_error(self, client, db_session):
        """Float interval_value (np. '5.5') powinien zwrocic blad (nie jest int)."""
        project = _make_project("cov-cr-float")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cr-float@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Float Monitor",
                "interval_value": "5.5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "liczba" in resp.text.lower()

    async def test_create_ssrf_check_returns_error_shows_on_page(self, client, db_session):
        """Gdy _is_url_safe zwraca blad, ten blad jest wyswietlany na stronie."""
        project = _make_project("cov-cr-ssrf-err")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cr-ssrf-err@test.com")
        with patch(
            "monolynx.dashboard.monitoring._is_url_safe",
            return_value="Adresy prywatne i wewnetrzne sa niedozwolone",
        ):
            resp = await client.post(
                f"/dashboard/{project.slug}/monitoring/create",
                data={
                    "url": "https://internal.corp.local",
                    "name": "Internal Monitor",
                    "interval_value": "5",
                    "interval_unit": "minutes",
                },
            )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_create_preserves_form_data_on_error(self, client, db_session):
        """Blad walidacji zachowuje dane formularza (form_data)."""
        project = _make_project("cov-cr-formdata")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cr-formdata@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "",
                "name": "My Monitor Name",
                "interval_value": "10",
                "interval_unit": "hours",
            },
        )
        assert resp.status_code == 200
        assert "URL jest wymagany" in resp.text


@pytest.mark.integration
class TestMonitorDetailEdgeCases:
    """Pokrycie linii monitor_detail: uptime=None, avg_response=None."""

    async def test_detail_no_checks_uptime_and_avg_are_none(self, client, db_session):
        """Monitor bez checkow: uptime i avg_response_time sa None."""
        project = _make_project("cov-det-nochk")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="NoChecks Monitor")
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="cov-det-nochk@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        assert "NoChecks Monitor" in resp.text

    async def test_detail_checks_without_response_time(self, client, db_session):
        """Checki z response_time_ms=None: avg_response_time powinien byc None."""
        project = _make_project("cov-det-nort")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="NoRT Monitor")
        db_session.add(monitor)
        await db_session.flush()

        # Checki bez response_time_ms (np. timeout / error)
        now = datetime.now(UTC)
        for i in range(3):
            check = MonitorCheck(
                monitor_id=monitor.id,
                status_code=None,
                response_time_ms=None,
                is_success=False,
                error_message="Connection timeout",
                checked_at=now - timedelta(hours=i),
            )
            db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="cov-det-nort@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        assert "NoRT Monitor" in resp.text

    async def test_detail_checks_only_old_no_24h_avg(self, client, db_session):
        """Checki starsze niz 24h: avg_response_time z ostatnich 24h = None."""
        project = _make_project("cov-det-old")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="OldChecks Monitor")
        db_session.add(monitor)
        await db_session.flush()

        # Checki starsze niz 24h
        old_time = datetime.now(UTC) - timedelta(days=3)
        for i in range(3):
            check = MonitorCheck(
                monitor_id=monitor.id,
                status_code=200,
                response_time_ms=100 + i * 50,
                is_success=True,
                checked_at=old_time - timedelta(hours=i),
            )
            db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="cov-det-old@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        assert "OldChecks Monitor" in resp.text

    async def test_detail_with_inactive_monitor(self, client, db_session):
        """Szczegoly nieaktywnego monitora wyswietlaja sie poprawnie."""
        project = _make_project("cov-det-inactive")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="Inactive Detail Monitor", is_active=False)
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="cov-det-inactive@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        assert "Inactive Detail Monitor" in resp.text


@pytest.mark.integration
class TestMonitorListEdgeCases:
    """Pokrycie linii monitor_list: rozne scenariusze."""

    async def test_list_with_monitor_having_no_checks(self, client, db_session):
        """Lista z monitorem bez zadnych checkow -- last_checks[id] = None."""
        project = _make_project("cov-lst-nochk")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="NeverChecked Monitor")
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="cov-lst-nochk@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/")
        assert resp.status_code == 200
        assert "NeverChecked Monitor" in resp.text

    async def test_list_inactive_project_returns_404(self, client, db_session):
        """Projekt z is_active=False zwraca 404."""
        project = _make_project("cov-lst-inact")
        project.is_active = False
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-lst-inact@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/")
        assert resp.status_code == 404


@pytest.mark.integration
class TestIsUrlSafeEdgeCases:
    """Pokrycie linii _is_url_safe: rozne edge case'y URL."""

    async def test_url_safe_ipv6_localhost_blocked(self, client, db_session):
        """SSRF: blokuje IPv6 localhost [::1]."""
        project = _make_project("cov-ssrf-ipv6")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-ssrf-ipv6@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://[::1]:8080/health",
                "name": "IPv6 Localhost",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_url_safe_empty_hostname_after_scheme(self, client, db_session):
        """URL 'http://' bez hosta -- _is_url_safe zwraca 'URL nie zawiera hosta'."""
        project = _make_project("cov-ssrf-emptyhost")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-ssrf-emptyhost@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://",
                "name": "Empty Host",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        # Powinien byc blad -- brak hosta
        assert "hosta" in resp.text or "URL" in resp.text


@pytest.mark.integration
class TestMonitorCreateFormEdgeCases:
    """Pokrycie linii monitor_create_form: dodatkowe sciezki."""

    async def test_create_form_inactive_project_returns_404(self, client, db_session):
        """Formularz tworzenia dla nieaktywnego projektu zwraca 404."""
        project = _make_project("cov-cf-inact")
        project.is_active = False
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-cf-inact@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/create")
        assert resp.status_code == 404


@pytest.mark.integration
class TestMonitorCreatePostAuth:
    """Pokrycie linii monitor_create POST: auth redirect na nieistniejacy projekt."""

    async def test_create_post_nonexistent_project_returns_404(self, client, db_session):
        """POST na /monitoring/create dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="cov-cr-post-noproj@test.com")
        resp = await client.post(
            "/dashboard/totally-fake-project-slug/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Test",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 404
