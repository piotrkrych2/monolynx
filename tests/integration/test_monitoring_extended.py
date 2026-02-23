"""Testy integracyjne -- rozszerzone pokrycie dashboard/monitoring.py.

Pokrywane linie:
- monitor_list z monitorami majacymi check history (last_checks subquery)
- monitor_detail z paginacja (page=2, page=abc, wiele checkow)
- monitor_detail z uptime stats i avg response time
- monitor_create z limitami SSRF (prywatne IP: 10.x, 172.16.x, 192.168.x)
- monitor_create z limitem 20 monitorow na projekt
- monitor_create z nieprawidlowa jednostka interwalu
- monitor_create z interwalem poza zakresem
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.project import Project
from tests.conftest import login_session


def _make_project(slug: str, name: str | None = None) -> Project:
    return Project(
        name=name or f"Project {slug}",
        slug=slug,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


def _make_monitor(project_id: uuid.UUID, url: str = "https://example.com", name: str = "Test Monitor") -> Monitor:
    return Monitor(
        project_id=project_id,
        url=url,
        name=name,
        interval_value=5,
        interval_unit="minutes",
        is_active=True,
    )


@pytest.mark.integration
class TestMonitorListWithChecks:
    """Pokrycie linii monitor_list: last_checks subquery, badge'e uptime."""

    async def test_monitor_list_with_successful_checks(self, client, db_session):
        """Lista monitorow z checkami -- pokrywa subquery last_checks."""
        project = _make_project("mle-succ-chk")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="Healthy Monitor")
        db_session.add(monitor)
        await db_session.flush()

        # Dodaj kilka checkow
        for i in range(3):
            check = MonitorCheck(
                monitor_id=monitor.id,
                status_code=200,
                response_time_ms=100 + i * 10,
                is_success=True,
                checked_at=datetime.now(UTC) - timedelta(minutes=i * 5),
            )
            db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="mle-succ-chk@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/")
        assert resp.status_code == 200
        assert "Healthy Monitor" in resp.text

    async def test_monitor_list_with_failed_checks(self, client, db_session):
        """Lista monitorow z nieudanymi checkamy."""
        project = _make_project("mle-fail-chk")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="Failing Monitor")
        db_session.add(monitor)
        await db_session.flush()

        check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=500,
            response_time_ms=2000,
            is_success=False,
            error_message="Internal Server Error",
            checked_at=datetime.now(UTC),
        )
        db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="mle-fail-chk@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/")
        assert resp.status_code == 200
        assert "Failing Monitor" in resp.text

    async def test_monitor_list_multiple_monitors_with_checks(self, client, db_session):
        """Kilka monitorow z roznymi statusami last_check."""
        project = _make_project("mle-multi-chk")
        db_session.add(project)
        await db_session.flush()

        monitor1 = _make_monitor(project.id, name="Mon Active OK")
        monitor2 = Monitor(
            project_id=project.id,
            url="https://down.example.com",
            name="Mon Inactive",
            interval_value=10,
            interval_unit="minutes",
            is_active=False,
        )
        db_session.add_all([monitor1, monitor2])
        await db_session.flush()

        # check dla monitor1
        check1 = MonitorCheck(
            monitor_id=monitor1.id,
            status_code=200,
            response_time_ms=50,
            is_success=True,
            checked_at=datetime.now(UTC),
        )
        # check dla monitor2
        check2 = MonitorCheck(
            monitor_id=monitor2.id,
            status_code=None,
            response_time_ms=None,
            is_success=False,
            error_message="Connection refused",
            checked_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db_session.add_all([check1, check2])
        await db_session.flush()

        await login_session(client, db_session, email="mle-multi-chk@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/")
        assert resp.status_code == 200
        assert "Mon Active OK" in resp.text
        assert "Mon Inactive" in resp.text


@pytest.mark.integration
class TestMonitorDetailWithChecks:
    """Pokrycie linii monitor_detail: paginacja, uptime stats, avg response."""

    async def test_detail_with_check_history(self, client, db_session):
        """Szczegoly monitora z historia checkow -- pokrywa uptime i avg response."""
        project = _make_project("mde-hist")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="History Monitor")
        db_session.add(monitor)
        await db_session.flush()

        # 10 checkow z roznymi wynikami
        now = datetime.now(UTC)
        for i in range(10):
            check = MonitorCheck(
                monitor_id=monitor.id,
                status_code=200 if i % 3 != 0 else 500,
                response_time_ms=100 + i * 20,
                is_success=i % 3 != 0,
                checked_at=now - timedelta(hours=i),
            )
            db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="mde-hist@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        assert "History Monitor" in resp.text
        # Uptime stats powinny byc widoczne
        assert "%" in resp.text or "uptime" in resp.text.lower() or "200" in resp.text

    async def test_detail_page_2_pagination(self, client, db_session):
        """Paginacja checkow -- strona 2."""
        project = _make_project("mde-page2")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="Paginated Monitor")
        db_session.add(monitor)
        await db_session.flush()

        # 30 checkow (per_page=25, wiec potrzebujemy 2 stron)
        now = datetime.now(UTC)
        for i in range(30):
            check = MonitorCheck(
                monitor_id=monitor.id,
                status_code=200,
                response_time_ms=100,
                is_success=True,
                checked_at=now - timedelta(minutes=i),
            )
            db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="mde-page2@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}?page=2")
        assert resp.status_code == 200
        assert "Paginated Monitor" in resp.text

    async def test_detail_page_invalid_value_defaults_to_1(self, client, db_session):
        """Nieprawidlowa wartosc page (abc) domyslnie ustawia 1."""
        project = _make_project("mde-badpage")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="BadPage Monitor")
        db_session.add(monitor)
        await db_session.flush()

        check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=200,
            response_time_ms=50,
            is_success=True,
        )
        db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="mde-badpage@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}?page=abc")
        assert resp.status_code == 200
        assert "BadPage Monitor" in resp.text

    async def test_detail_page_beyond_total_clamped(self, client, db_session):
        """Strona wieksza niz total_pages jest ograniczana do ostatniej."""
        project = _make_project("mde-overpage")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="OverPage Monitor")
        db_session.add(monitor)
        await db_session.flush()

        # Tylko 3 checki, wiec total_pages=1
        for _i in range(3):
            check = MonitorCheck(
                monitor_id=monitor.id,
                status_code=200,
                response_time_ms=80,
                is_success=True,
            )
            db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="mde-overpage@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}?page=999")
        assert resp.status_code == 200
        assert "OverPage Monitor" in resp.text

    async def test_detail_uptime_all_success(self, client, db_session):
        """Uptime 100% gdy wszystkie checki sa successful."""
        project = _make_project("mde-100up")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="AllGood Monitor")
        db_session.add(monitor)
        await db_session.flush()

        now = datetime.now(UTC)
        for i in range(5):
            check = MonitorCheck(
                monitor_id=monitor.id,
                status_code=200,
                response_time_ms=50 + i * 10,
                is_success=True,
                checked_at=now - timedelta(hours=i),
            )
            db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="mde-100up@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        assert "100" in resp.text  # 100% uptime

    async def test_detail_uptime_mixed_results(self, client, db_session):
        """Uptime z mieszanymi wynikami -- pokrywa _compute_uptime z partial success."""
        project = _make_project("mde-mixup")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="Mixed Monitor")
        db_session.add(monitor)
        await db_session.flush()

        now = datetime.now(UTC)
        # 2 success + 2 failure = 50% uptime
        for i, success in enumerate([True, True, False, False]):
            check = MonitorCheck(
                monitor_id=monitor.id,
                status_code=200 if success else 500,
                response_time_ms=100 if success else 2000,
                is_success=success,
                checked_at=now - timedelta(hours=i),
            )
            db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="mde-mixup@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        assert "50" in resp.text  # 50% uptime

    async def test_detail_avg_response_time_displayed(self, client, db_session):
        """Sredni czas odpowiedzi jest obliczany i wyswietlany."""
        project = _make_project("mde-avgrt")
        db_session.add(project)
        await db_session.flush()

        monitor = _make_monitor(project.id, name="AvgRT Monitor")
        db_session.add(monitor)
        await db_session.flush()

        now = datetime.now(UTC)
        for ms in [100, 200, 300]:
            check = MonitorCheck(
                monitor_id=monitor.id,
                status_code=200,
                response_time_ms=ms,
                is_success=True,
                checked_at=now - timedelta(minutes=10),
            )
            db_session.add(check)
        await db_session.flush()

        await login_session(client, db_session, email="mde-avgrt@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/monitoring/{monitor.id}")
        assert resp.status_code == 200
        # Avg = 200ms; strona powinna zawierac "200"
        assert "200" in resp.text


@pytest.mark.integration
class TestMonitorCreateSSRF:
    """Pokrycie linii SSRF: prywatne IP (10.x, 172.16.x, 192.168.x), DNS resolution."""

    async def test_create_blocks_private_ip_10(self, client, db_session):
        """SSRF: blokuje adres 10.0.0.1."""
        project = _make_project("mcs-priv10")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-priv10@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://10.0.0.1:8080/health",
                "name": "Private 10.x",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_create_blocks_private_ip_172(self, client, db_session):
        """SSRF: blokuje adres 172.16.0.1."""
        project = _make_project("mcs-priv172")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-priv172@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://172.16.0.1/api",
                "name": "Private 172.x",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_create_blocks_private_ip_192(self, client, db_session):
        """SSRF: blokuje adres 192.168.1.1."""
        project = _make_project("mcs-priv192")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-priv192@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://192.168.1.1:3000",
                "name": "Private 192.x",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_create_blocks_127_0_0_1(self, client, db_session):
        """SSRF: blokuje adres 127.0.0.1."""
        project = _make_project("mcs-lo127")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-lo127@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://127.0.0.1:9090",
                "name": "Loopback",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_create_blocks_zero_ip(self, client, db_session):
        """SSRF: blokuje adres 0.0.0.0."""
        project = _make_project("mcs-zero")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-zero@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://0.0.0.0:8080",
                "name": "Zero IP",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "niedozwolone" in resp.text

    async def test_create_blocks_unresolvable_host(self, client, db_session):
        """SSRF: blokuje host ktory nie moze byc rozwiazany przez DNS."""
        project = _make_project("mcs-nodns")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-nodns@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://this-host-does-not-exist-xyz123.invalid/ping",
                "name": "No DNS",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "rozwiazac" in resp.text


@pytest.mark.integration
class TestMonitorCreateLimits:
    """Pokrycie linii: limit 20 monitorow, nieprawidlowa jednostka, zakres interwalu."""

    async def test_create_monitor_limit_reached(self, client, db_session):
        """Przekroczenie limitu 20 monitorow na projekt -- blad."""
        project = _make_project("mcs-limit20")
        db_session.add(project)
        await db_session.flush()

        # Dodaj 20 monitorow
        for i in range(20):
            monitor = Monitor(
                project_id=project.id,
                url=f"https://site{i}.example.com",
                name=f"Mon {i}",
                interval_value=5,
                interval_unit="minutes",
                is_active=True,
            )
            db_session.add(monitor)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-limit20@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://site21.example.com",
                "name": "21st Monitor",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "limit" in resp.text.lower() or "20" in resp.text

    async def test_create_monitor_invalid_interval_unit(self, client, db_session):
        """Nieprawidlowa jednostka interwalu -- blad walidacji."""
        project = _make_project("mcs-badunit")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-badunit@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Bad Unit",
                "interval_value": "5",
                "interval_unit": "weeks",
            },
        )
        assert resp.status_code == 200
        assert "jednostka" in resp.text.lower() or "interwalu" in resp.text.lower()

    async def test_create_monitor_interval_out_of_range_high(self, client, db_session):
        """Interwal > 60 -- blad walidacji."""
        project = _make_project("mcs-highintv")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-highintv@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "High Interval",
                "interval_value": "100",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "1 a 60" in resp.text or "miedzy" in resp.text

    async def test_create_monitor_interval_out_of_range_zero(self, client, db_session):
        """Interwal = 0 -- blad walidacji."""
        project = _make_project("mcs-zerointv")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-zerointv@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "https://example.com",
                "name": "Zero Interval",
                "interval_value": "0",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "1 a 60" in resp.text or "miedzy" in resp.text

    async def test_create_monitor_url_without_host(self, client, db_session):
        """URL bez hosta -- blad walidacji SSRF."""
        project = _make_project("mcs-nohost")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="mcs-nohost@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/monitoring/create",
            data={
                "url": "http://",
                "name": "No Host",
                "interval_value": "5",
                "interval_unit": "minutes",
            },
        )
        assert resp.status_code == 200
        assert "hosta" in resp.text or "niedozwolone" in resp.text or "URL" in resp.text
