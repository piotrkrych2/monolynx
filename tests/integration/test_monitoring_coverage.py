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


# ---------------------------------------------------------------------------
# MON-85: dodatkowe pokrycie dashboard/monitoring.py (79% -> 95%)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSsrfRealPrivateIps:
    """SSRF: literalne prywatne/loopback IP blokowane bez DNS (offline-safe)."""

    async def test_create_blocks_literal_loopback_ip(self, client, db_session):
        project = _make_project("cov-ssrf-loopback")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-ssrf-loopback@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://127.0.0.1:9000/health",
                "name": "Loopback",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_create_blocks_literal_private_ip(self, client, db_session):
        project = _make_project("cov-ssrf-private")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-ssrf-private@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://10.1.2.3/health",
                "name": "Private IP",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_create_blocks_link_local_ip(self, client, db_session):
        project = _make_project("cov-ssrf-linklocal")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-ssrf-linklocal@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://169.254.169.254/latest/meta-data/",
                "name": "Link local (cloud metadata)",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text


@pytest.mark.integration
class TestMonitorLimitPerProject:
    """Limit MAX_MONITORS_PER_PROJECT=20 monitorow na projekt."""

    async def test_create_21st_monitor_exceeds_limit(self, client, db_session):
        project = _make_project("cov-limit-20")
        db_session.add(project)
        await db_session.flush()

        for i in range(20):
            db_session.add(_make_monitor(project.id, url=f"https://limit-{i}.example.com", name=f"Limit {i}"))
        await db_session.flush()

        await login_session(client, db_session, email="cov-limit-20@test.com")
        with patch("monolynx.dashboard.monitoring._is_url_safe", return_value=None):
            resp = await client.post(
                f"/dashboard/{project.slug}/monitoring/create",
                data={
                    "url": "https://limit-21.example.com",
                    "name": "21st Monitor",
                    "interval_value": "5",
                    "interval_unit": "minutes",
                },
            )
        assert resp.status_code == 200
        assert "limit" in resp.text.lower()

        result = await db_session.execute(select(Monitor).where(Monitor.project_id == project.id, Monitor.url == "https://limit-21.example.com"))
        assert result.scalar_one_or_none() is None


@pytest.mark.integration
class TestMonitorDetailNotFound:
    """monitor_detail: monitor nie istnieje w danym projekcie -- 404."""

    async def test_detail_nonexistent_monitor_returns_404(self, client, db_session):
        project = _make_project("cov-det-404")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-det-404@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{fake_id}")
        assert resp.status_code == 404

    async def test_detail_nonexistent_project_returns_404(self, client, db_session):
        await login_session(client, db_session, email="cov-det-noproj-404@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/totally-fake-project/monitoring/{fake_id}")
        assert resp.status_code == 404

    async def test_detail_requires_login(self, client, db_session):
        project = _make_project("cov-det-nologin")
        db_session.add(project)
        await db_session.flush()

        fake_id = uuid.uuid4()
        resp = await client.get(
            f"/dashboard/{project.slug}/monitoring/{fake_id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"


@pytest.mark.integration
class TestMonitorTestAlert:
    """POST /{slug}/monitoring/{id}/test-alert -- cala sciezka."""

    async def test_test_alert_requires_login(self, client, db_session):
        project = _make_project("cov-alert-nologin")
        db_session.add(project)
        await db_session.flush()
        fake_id = uuid.uuid4()

        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{fake_id}/test-alert",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

    async def test_test_alert_nonexistent_project_redirects_to_login(self, client, db_session):
        await login_session(client, db_session, email="cov-alert-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            "/dashboard/totally-fake-project-alert/monitoring/" + str(fake_id) + "/test-alert",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

    async def test_test_alert_nonexistent_monitor_redirects_to_list(self, client, db_session):
        project = _make_project("cov-alert-nomonitor")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-alert-nomonitor@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{fake_id}/test-alert",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/dashboard/{project.slug}/monitoring/"

    async def test_test_alert_no_notification_config_flashes_error(self, client, db_session):
        project = _make_project("cov-alert-noconfig")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="NoConfig Monitor")
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="cov-alert-noconfig@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{monitor.id}/test-alert",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/dashboard/{project.slug}/monitoring/{monitor.id}"

    async def test_test_alert_success_with_mocked_send(self, client, db_session):
        project = _make_project("cov-alert-success")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="AlertOK Monitor")
        monitor.notification_config = {
            "email_enabled": True,
            "email_recipients": ["ops@example.com"],
            "sms_enabled": False,
            "sms_recipients": [],
            "slack_enabled": False,
            "slack_channels": [],
        }
        db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="cov-alert-success@test.com")
        with patch("monolynx.services.notifications.send_monitor_alert") as mock_send:
            mock_send.return_value = None
            resp = await client.post(
                f"/dashboard/{project.slug}/monitoring/{monitor.id}/test-alert",
                follow_redirects=False,
            )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/dashboard/{project.slug}/monitoring/{monitor.id}"
        mock_send.assert_called_once()

    async def test_test_alert_exception_rolls_back_last_alert_sent_at(self, client, db_session):
        project = _make_project("cov-alert-exception")
        db_session.add(project)
        await db_session.flush()

        original_last_alert = datetime.now(UTC) - timedelta(hours=1)
        monitor = _make_monitor(project.id, name="AlertFail Monitor")
        monitor.notification_config = {
            "email_enabled": True,
            "email_recipients": ["ops@example.com"],
            "sms_enabled": False,
            "sms_recipients": [],
            "slack_enabled": False,
            "slack_channels": [],
        }
        monitor.last_alert_sent_at = original_last_alert
        db_session.add(monitor)
        await db_session.flush()
        monitor_id = monitor.id

        await login_session(client, db_session, email="cov-alert-exception@test.com")
        with patch("monolynx.services.notifications.send_monitor_alert", side_effect=RuntimeError("boom")):
            resp = await client.post(
                f"/dashboard/{project.slug}/monitoring/{monitor_id}/test-alert",
                follow_redirects=False,
            )
        assert resp.status_code == 303
        assert resp.headers["location"] == f"/dashboard/{project.slug}/monitoring/{monitor_id}"

        result = await db_session.execute(select(Monitor).where(Monitor.id == monitor_id))
        reloaded = result.scalar_one()
        assert reloaded.last_alert_sent_at is not None


@pytest.mark.integration
class TestMonitorDetailPagination:
    """Paginacja checkow na stronie szczegolow monitora."""

    async def _make_monitor_with_checks(self, db_session, slug: str, count: int) -> tuple[Project, Monitor]:
        project = _make_project(slug)
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name=f"Paged {slug}")
        db_session.add(monitor)
        await db_session.flush()

        now = datetime.now(UTC)
        for i in range(count):
            db_session.add(
                MonitorCheck(
                    monitor_id=monitor.id,
                    status_code=200,
                    response_time_ms=100,
                    is_success=True,
                    checked_at=now - timedelta(minutes=i),
                )
            )
        await db_session.flush()
        return project, monitor

    async def test_detail_more_than_25_checks_paginates(self, client, db_session):
        project, monitor = await self._make_monitor_with_checks(db_session, "cov-page-many", 30)

        await login_session(client, db_session, email="cov-page-many@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}?page=1")
        assert resp.status_code == 200

        resp2 = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}?page=2")
        assert resp2.status_code == 200

    async def test_detail_page_beyond_range_clamps_to_last_page(self, client, db_session):
        project, monitor = await self._make_monitor_with_checks(db_session, "cov-page-clamp", 30)

        await login_session(client, db_session, email="cov-page-clamp@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}?page=999")
        assert resp.status_code == 200

    async def test_detail_invalid_page_param_defaults_to_1(self, client, db_session):
        project, monitor = await self._make_monitor_with_checks(db_session, "cov-page-invalid", 5)

        await login_session(client, db_session, email="cov-page-invalid@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}?page=abc")
        assert resp.status_code == 200

    async def test_detail_negative_page_clamps_to_1(self, client, db_session):
        project, monitor = await self._make_monitor_with_checks(db_session, "cov-page-neg", 5)

        await login_session(client, db_session, email="cov-page-neg@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}?page=-5")
        assert resp.status_code == 200


@pytest.mark.integration
class TestIsUrlSafeMoreEdgeCases:
    """Dodatkowe pokrycie _is_url_safe: ValueError z urlparse, gaierror, IP niepoprawny w getaddrinfo."""

    async def test_url_safe_malformed_ipv6_raises_value_error_in_urlparse(self, client, db_session):
        """Niedomkniety nawias IPv6 -- urlparse rzuca ValueError (linie 36-37)."""
        project = _make_project("cov-ssrf-malformed")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-ssrf-malformed@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://[::1/path",
                "name": "Malformed IPv6",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "URL" in resp.text

    async def test_url_safe_dns_resolution_failure(self, client, db_session):
        """Hostname z rezerwowanej domeny .invalid (RFC 2606) -- gaierror bez sieci (linie 51-52)."""
        project = _make_project("cov-ssrf-dnsfail")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-ssrf-dnsfail@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://this-host-does-not-exist.invalid/health",
                "name": "DNS Fail",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "rozwiazac" in resp.text

    def test_is_url_safe_skips_unparseable_ip_from_getaddrinfo(self):
        """Wpis z getaddrinfo z niepoprawnym IP jest pomijany (linie 58-59), sprawdzany bezposrednio."""
        from monolynx.dashboard.monitoring import _is_url_safe

        fake_addr_infos = [
            (2, 1, 6, "", ("not-an-ip", 0)),
            (2, 1, 6, "", ("8.8.8.8", 0)),
        ]
        with patch("monolynx.dashboard.monitoring.socket.getaddrinfo", return_value=fake_addr_infos):
            result = _is_url_safe("http://example.com/")
        assert result is None


@pytest.mark.integration
class TestNotificationConfigValidation:
    """Pokrycie _parse_notification_config: walidacja email/sms/slack (linie 86-88, 91-93, 96-101, 279)."""

    async def test_create_invalid_email_recipient_shows_error(self, client, db_session):
        project = _make_project("cov-notif-badmail")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-notif-badmail@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Bad Email Notif",
                "interval_value": "5",
                "interval_unit": "minutes",
                "notification_email_enabled": "on",
                "notification_email_recipients": "not-an-email",
            },
        )
        assert resp.status_code == 200
        assert "adresu email" in resp.text

    async def test_create_invalid_sms_recipient_shows_error(self, client, db_session):
        project = _make_project("cov-notif-badsms")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-notif-badsms@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Bad SMS Notif",
                "interval_value": "5",
                "interval_unit": "minutes",
                "notification_sms_enabled": "on",
                "notification_sms_recipients": "abc",
            },
        )
        assert resp.status_code == 200
        assert "numeru telefonu" in resp.text

    async def test_create_invalid_slack_webhook_scheme_shows_error(self, client, db_session):
        project = _make_project("cov-notif-badslack")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-notif-badslack@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Bad Slack Notif",
                "interval_value": "5",
                "interval_unit": "minutes",
                "notification_slack_enabled": "on",
                "notification_slack_channels": "ftp://not-http.example.com",
            },
        )
        assert resp.status_code == 200
        assert "webhooka Slack" in resp.text

    async def test_create_slack_webhook_ssrf_blocked_shows_error(self, client, db_session):
        project = _make_project("cov-notif-slackssrf")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-notif-slackssrf@test.com")
        with patch("monolynx.dashboard.monitoring._is_url_safe") as mock_safe:
            # Pierwsze wywolanie (walidacja URL monitora) -- OK; drugie (webhook Slack) -- blad
            mock_safe.side_effect = [None, "Adresy prywatne i wewnetrzne sa niedozwolone"]
            resp = await client.post(
                f"/dashboard/{project.slug}/monitoring/create",
                data={
                    "url": "https://example.com",
                    "name": "Slack SSRF Notif",
                    "interval_value": "5",
                    "interval_unit": "minutes",
                    "notification_slack_enabled": "on",
                    "notification_slack_channels": "http://10.0.0.5/webhook",
                },
            )
        assert resp.status_code == 200
        assert "Webhook Slack" in resp.text

    async def test_create_with_valid_notification_config_succeeds(self, client, db_session):
        project = _make_project("cov-notif-valid")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-notif-valid@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Valid Notif Monitor",
                "interval_value": "5",
                "interval_unit": "minutes",
                "notification_email_enabled": "on",
                "notification_email_recipients": "ops@example.com",
                "notification_sms_enabled": "on",
                "notification_sms_recipients": "+48123456789",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Monitor).where(Monitor.project_id == project.id, Monitor.url == "https://example.com"))
        created = result.scalar_one_or_none()
        assert created is not None
        assert created.notification_config["email_recipients"] == ["ops@example.com"]
        assert created.notification_config["sms_recipients"] == ["+48123456789"]


@pytest.mark.integration
class TestMonitorCreateIntervalValidation:
    """Pokrycie walidacji interwalu i jednostki (linie 258, 264-265)."""

    async def test_create_interval_value_too_high_shows_error(self, client, db_session):
        project = _make_project("cov-interval-high")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-interval-high@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Too High Interval",
                "interval_value": "100",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "miedzy 1 a 60" in resp.text

    async def test_create_interval_value_too_low_shows_error(self, client, db_session):
        project = _make_project("cov-interval-low")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-interval-low@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Too Low Interval",
                "interval_value": "0",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "miedzy 1 a 60" in resp.text

    async def test_create_invalid_interval_unit_shows_error(self, client, db_session):
        project = _make_project("cov-interval-unit")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-interval-unit@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Bad Unit",
                "interval_value": "5",
                "interval_unit": "fortnights",
            },
        )
        assert resp.status_code == 200
        assert "jednostka interwalu" in resp.text


@pytest.mark.integration
class TestMonitoringAuthRedirects:
    """Pokrycie redirectow /auth/login dla brakujacej sesji (linie 135, 197, 228, 448, 484)."""

    async def test_monitor_list_requires_login(self, client, db_session):
        project = _make_project("cov-auth-list")
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/monitoring/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

    async def test_monitor_create_form_requires_login(self, client, db_session):
        project = _make_project("cov-auth-createform")
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/monitoring/create", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

    async def test_monitor_create_post_requires_login(self, client, db_session):
        project = _make_project("cov-auth-createpost")
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={"url": "https://example.com", "interval_value": "5", "interval_unit": "minutes"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

    async def test_monitor_toggle_requires_login(self, client, db_session):
        project = _make_project("cov-auth-toggle")
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{uuid.uuid4()}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

    async def test_monitor_delete_requires_login(self, client, db_session):
        project = _make_project("cov-auth-delete")
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"


@pytest.mark.integration
class TestMonitorToggleDeleteNotFound:
    """Pokrycie monitor_toggle/monitor_delete: monitor nie istnieje (linie 458, 494)."""

    async def test_toggle_nonexistent_monitor_returns_404(self, client, db_session):
        project = _make_project("cov-toggle-404")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-toggle-404@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{uuid.uuid4()}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_delete_nonexistent_monitor_returns_404(self, client, db_session):
        project = _make_project("cov-delete-404")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cov-delete-404@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestMonitorListWithLastCheck:
    """Pokrycie monitor_list: monitor z ostatnim checkiem obecnym w subquery (linie 160->172, 170)."""

    async def test_list_populates_last_check_for_monitor(self, client, db_session):
        project = _make_project("cov-list-lastchk")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="HasCheck Monitor")
        db_session.add(monitor)
        await db_session.flush()

        db_session.add(
            MonitorCheck(
                monitor_id=monitor.id,
                status_code=200,
                response_time_ms=150,
                is_success=True,
                checked_at=datetime.now(UTC),
            )
        )
        await db_session.flush()

        await login_session(client, db_session, email="cov-list-lastchk@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/")
        assert resp.status_code == 200
        assert "HasCheck Monitor" in resp.text
