"""Testy integracyjne -- serwis badge'ow sidebarowych (get_sidebar_badges)."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from monolynx.models.issue import Issue
from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.project import Project
from monolynx.services.sidebar import SidebarBadges, get_sidebar_badges


async def _create_project(db_session, slug=None):
    """Helper: tworzy projekt testowy z unikalnym slugiem."""
    if slug is None:
        slug = f"sb-{secrets.token_hex(4)}"
    project = Project(
        name=f"Sidebar Test {slug}",
        slug=slug,
        code="P" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.mark.integration
class TestSidebarBadgesDataclass:
    """Testy dataklasy SidebarBadges."""

    def test_default_values(self):
        """Domyslne wartosci: wszystko zerowe/None/False."""
        badges = SidebarBadges()
        assert badges.issues_count == 0
        assert badges.issues_pulse is False
        assert badges.monitors_failing_count == 0
        assert badges.monitors_failing_pulse is False
        assert badges.monitoring_uptime_24h is None

    def test_custom_values(self):
        """SidebarBadges przyjmuje niestandardowe wartosci."""
        badges = SidebarBadges(
            issues_count=5,
            issues_pulse=True,
            monitors_failing_count=2,
            monitors_failing_pulse=True,
            monitoring_uptime_24h=99.5,
        )
        assert badges.issues_count == 5
        assert badges.issues_pulse is True
        assert badges.monitors_failing_count == 2
        assert badges.monitors_failing_pulse is True
        assert badges.monitoring_uptime_24h == 99.5

    def test_frozen(self):
        """SidebarBadges jest frozen -- nie mozna zmieniac atrybutow."""
        badges = SidebarBadges()
        with pytest.raises(AttributeError):
            badges.issues_count = 10  # type: ignore[misc]


@pytest.mark.integration
class TestGetSidebarBadgesEmpty:
    """Testy get_sidebar_badges dla pustego projektu."""

    async def test_empty_project_returns_zeros(self, db_session):
        """Pusty projekt -> wszystkie badge'e zerowe/None."""
        project = await _create_project(db_session, slug="sb-empty")

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.issues_count == 0
        assert badges.issues_pulse is False
        assert badges.monitors_failing_count == 0
        assert badges.monitors_failing_pulse is False
        assert badges.monitoring_uptime_24h is None

    async def test_nonexistent_project_returns_zeros(self, db_session):
        """Nieistniejacy projekt -> zerowe badge'e (brak bledow)."""
        fake_id = uuid.uuid4()

        badges = await get_sidebar_badges(fake_id, db_session)

        assert badges.issues_count == 0
        assert badges.issues_pulse is False
        assert badges.monitors_failing_count == 0
        assert badges.monitors_failing_pulse is False
        assert badges.monitoring_uptime_24h is None


@pytest.mark.integration
class TestGetSidebarBadgesIssues:
    """Testy issues badge'ow."""

    async def test_unresolved_issues_counted(self, db_session):
        """Nierozwiazane issues sa liczone."""
        project = await _create_project(db_session, slug="sb-issues")

        issue1 = Issue(
            project_id=project.id,
            fingerprint="fp-001",
            title="Error 1",
            status="unresolved",
            last_seen=datetime.now(UTC),
        )
        issue2 = Issue(
            project_id=project.id,
            fingerprint="fp-002",
            title="Error 2",
            status="unresolved",
            last_seen=datetime.now(UTC),
        )
        db_session.add_all([issue1, issue2])
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.issues_count == 2

    async def test_resolved_issues_not_counted(self, db_session):
        """Rozwiazane issues nie sa liczone."""
        project = await _create_project(db_session, slug="sb-resolved")

        issue = Issue(
            project_id=project.id,
            fingerprint="fp-resolved",
            title="Resolved Error",
            status="resolved",
            last_seen=datetime.now(UTC),
        )
        db_session.add(issue)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.issues_count == 0

    async def test_issues_pulse_true_when_recent(self, db_session):
        """issues_pulse=True gdy sa issues z last_seen < 7 dni."""
        project = await _create_project(db_session, slug="sb-pulse-true")

        issue = Issue(
            project_id=project.id,
            fingerprint="fp-recent",
            title="Recent Error",
            status="unresolved",
            last_seen=datetime.now(UTC) - timedelta(days=1),
        )
        db_session.add(issue)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.issues_count == 1
        assert badges.issues_pulse is True

    async def test_issues_pulse_false_when_old(self, db_session):
        """issues_pulse=False gdy wszystkie issues starsze niz 7 dni."""
        project = await _create_project(db_session, slug="sb-pulse-false")

        issue = Issue(
            project_id=project.id,
            fingerprint="fp-old",
            title="Old Error",
            status="unresolved",
            last_seen=datetime.now(UTC) - timedelta(days=10),
        )
        db_session.add(issue)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.issues_count == 1
        assert badges.issues_pulse is False

    async def test_mixed_statuses_only_counts_unresolved(self, db_session):
        """Mieszane statusy -- liczone tylko unresolved."""
        project = await _create_project(db_session, slug="sb-mixed-status")

        issue_unresolved = Issue(
            project_id=project.id,
            fingerprint="fp-mix-unres",
            title="Unresolved",
            status="unresolved",
            last_seen=datetime.now(UTC),
        )
        issue_resolved = Issue(
            project_id=project.id,
            fingerprint="fp-mix-res",
            title="Resolved",
            status="resolved",
            last_seen=datetime.now(UTC),
        )
        db_session.add_all([issue_unresolved, issue_resolved])
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.issues_count == 1

    async def test_issues_from_other_project_not_counted(self, db_session):
        """Issues z innego projektu nie sa liczone."""
        project_a = await _create_project(db_session, slug="sb-proj-a")
        project_b = await _create_project(db_session, slug="sb-proj-b")

        issue = Issue(
            project_id=project_b.id,
            fingerprint="fp-other-proj",
            title="Other Project Issue",
            status="unresolved",
            last_seen=datetime.now(UTC),
        )
        db_session.add(issue)
        await db_session.flush()

        badges = await get_sidebar_badges(project_a.id, db_session)

        assert badges.issues_count == 0


@pytest.mark.integration
class TestGetSidebarBadgesMonitorsFailingCount:
    """Testy monitors_failing_count i monitors_failing_pulse."""

    async def test_no_monitors_zero_failing(self, db_session):
        """Brak monitorow -> monitors_failing_count=0."""
        project = await _create_project(db_session, slug="sb-no-mon")

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitors_failing_count == 0
        assert badges.monitors_failing_pulse is False

    async def test_active_monitor_with_failing_check(self, db_session):
        """Aktywny monitor z nieudanym ostatnim checkiem -> failing_count=1."""
        project = await _create_project(db_session, slug="sb-fail-mon")

        monitor = Monitor(
            project_id=project.id,
            url="https://failing.com",
            name="Failing Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=500,
            response_time_ms=200,
            is_success=False,
            error_message="Internal Server Error",
            checked_at=datetime.now(UTC),
        )
        db_session.add(check)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitors_failing_count == 1

    async def test_active_monitor_with_success_check_not_counted(self, db_session):
        """Aktywny monitor z udanym ostatnim checkiem -> failing_count=0."""
        project = await _create_project(db_session, slug="sb-succ-mon")

        monitor = Monitor(
            project_id=project.id,
            url="https://healthy.com",
            name="Healthy Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=200,
            response_time_ms=100,
            is_success=True,
            checked_at=datetime.now(UTC),
        )
        db_session.add(check)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitors_failing_count == 0

    async def test_inactive_monitor_not_counted(self, db_session):
        """Nieaktywny monitor z nieudanym checkiem -> nie liczony jako failing."""
        project = await _create_project(db_session, slug="sb-inact-mon")

        monitor = Monitor(
            project_id=project.id,
            url="https://inactive.com",
            name="Inactive Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=False,
        )
        db_session.add(monitor)
        await db_session.flush()

        check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=500,
            response_time_ms=200,
            is_success=False,
            checked_at=datetime.now(UTC),
        )
        db_session.add(check)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitors_failing_count == 0

    async def test_failing_pulse_true_when_recent(self, db_session):
        """monitors_failing_pulse=True gdy nieudany check < 7 dni."""
        project = await _create_project(db_session, slug="sb-fail-pulse")

        monitor = Monitor(
            project_id=project.id,
            url="https://failing-recent.com",
            name="Recent Fail",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=500,
            response_time_ms=200,
            is_success=False,
            checked_at=datetime.now(UTC) - timedelta(days=1),
        )
        db_session.add(check)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitors_failing_count == 1
        assert badges.monitors_failing_pulse is True

    async def test_failing_pulse_false_when_old(self, db_session):
        """monitors_failing_pulse=False gdy nieudany check > 7 dni."""
        project = await _create_project(db_session, slug="sb-fail-old")

        monitor = Monitor(
            project_id=project.id,
            url="https://failing-old.com",
            name="Old Fail",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=500,
            response_time_ms=200,
            is_success=False,
            checked_at=datetime.now(UTC) - timedelta(days=10),
        )
        db_session.add(check)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitors_failing_count == 1
        assert badges.monitors_failing_pulse is False

    async def test_latest_check_used_not_old_one(self, db_session):
        """Tylko najnowszy check jest brany pod uwage, nie starszy."""
        project = await _create_project(db_session, slug="sb-latest-check")

        monitor = Monitor(
            project_id=project.id,
            url="https://recovered.com",
            name="Recovered Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        # Starszy check -- failing
        old_check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=500,
            response_time_ms=200,
            is_success=False,
            checked_at=datetime.now(UTC) - timedelta(hours=2),
        )
        # Nowszy check -- success
        new_check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=200,
            response_time_ms=100,
            is_success=True,
            checked_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        db_session.add_all([old_check, new_check])
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        # Najnowszy check jest success -> nie jest failing
        assert badges.monitors_failing_count == 0

    async def test_multiple_monitors_mixed(self, db_session):
        """Dwa monitory: jeden failing, jeden ok -> failing_count=1."""
        project = await _create_project(db_session, slug="sb-multi-mon")

        monitor_ok = Monitor(
            project_id=project.id,
            url="https://ok.com",
            name="OK Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        monitor_fail = Monitor(
            project_id=project.id,
            url="https://fail.com",
            name="Fail Monitor",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add_all([monitor_ok, monitor_fail])
        await db_session.flush()

        check_ok = MonitorCheck(
            monitor_id=monitor_ok.id,
            status_code=200,
            response_time_ms=100,
            is_success=True,
            checked_at=datetime.now(UTC),
        )
        check_fail = MonitorCheck(
            monitor_id=monitor_fail.id,
            status_code=503,
            response_time_ms=500,
            is_success=False,
            checked_at=datetime.now(UTC),
        )
        db_session.add_all([check_ok, check_fail])
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitors_failing_count == 1


@pytest.mark.integration
class TestGetSidebarBadgesUptime:
    """Testy monitoring_uptime_24h."""

    async def test_no_checks_returns_none(self, db_session):
        """Brak checkow w 24h -> uptime=None."""
        project = await _create_project(db_session, slug="sb-no-checks")

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="No Checks",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitoring_uptime_24h is None

    async def test_all_success_returns_100(self, db_session):
        """Wszystkie checki udane -> uptime=100.0."""
        project = await _create_project(db_session, slug="sb-all-success")

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="All Success",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        now = datetime.now(UTC)
        checks = []
        for i in range(10):
            checks.append(
                MonitorCheck(
                    monitor_id=monitor.id,
                    status_code=200,
                    response_time_ms=100,
                    is_success=True,
                    checked_at=now - timedelta(hours=i),
                )
            )
        db_session.add_all(checks)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitoring_uptime_24h == 100.0

    async def test_all_failure_returns_0(self, db_session):
        """Wszystkie checki nieudane -> uptime=0.0."""
        project = await _create_project(db_session, slug="sb-all-fail")

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="All Fail",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        now = datetime.now(UTC)
        checks = []
        for i in range(5):
            checks.append(
                MonitorCheck(
                    monitor_id=monitor.id,
                    status_code=500,
                    response_time_ms=200,
                    is_success=False,
                    checked_at=now - timedelta(hours=i),
                )
            )
        db_session.add_all(checks)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitoring_uptime_24h == 0.0

    async def test_mixed_checks_calculates_percentage(self, db_session):
        """Mieszane checki -> uptime obliczony procentowo."""
        project = await _create_project(db_session, slug="sb-mixed-checks")

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Mixed Checks",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        now = datetime.now(UTC)
        # 8 success, 2 failure = 80% uptime
        checks = []
        for i in range(8):
            checks.append(
                MonitorCheck(
                    monitor_id=monitor.id,
                    status_code=200,
                    response_time_ms=100,
                    is_success=True,
                    checked_at=now - timedelta(hours=i),
                )
            )
        for i in range(2):
            checks.append(
                MonitorCheck(
                    monitor_id=monitor.id,
                    status_code=500,
                    response_time_ms=200,
                    is_success=False,
                    checked_at=now - timedelta(hours=8 + i),
                )
            )
        db_session.add_all(checks)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitoring_uptime_24h == 80.0

    async def test_old_checks_excluded_from_uptime(self, db_session):
        """Checki starsze niz 24h nie wliczone do uptime."""
        project = await _create_project(db_session, slug="sb-old-uptime")

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Old Checks",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        # Jeden check w ciagu 24h (success), jeden poza (failure)
        now = datetime.now(UTC)
        recent_check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=200,
            response_time_ms=100,
            is_success=True,
            checked_at=now - timedelta(hours=1),
        )
        old_check = MonitorCheck(
            monitor_id=monitor.id,
            status_code=500,
            response_time_ms=200,
            is_success=False,
            checked_at=now - timedelta(hours=25),
        )
        db_session.add_all([recent_check, old_check])
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        # Tylko recent_check (success) -> 100%
        assert badges.monitoring_uptime_24h == 100.0

    async def test_inactive_monitor_checks_excluded_from_uptime(self, db_session):
        """Checki nieaktywnych monitorow nie wliczone do uptime."""
        project = await _create_project(db_session, slug="sb-inact-uptime")

        active_monitor = Monitor(
            project_id=project.id,
            url="https://active.com",
            name="Active",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        inactive_monitor = Monitor(
            project_id=project.id,
            url="https://inactive.com",
            name="Inactive",
            interval_value=5,
            interval_unit="minutes",
            is_active=False,
        )
        db_session.add_all([active_monitor, inactive_monitor])
        await db_session.flush()

        now = datetime.now(UTC)
        # Active monitor: success
        active_check = MonitorCheck(
            monitor_id=active_monitor.id,
            status_code=200,
            response_time_ms=100,
            is_success=True,
            checked_at=now,
        )
        # Inactive monitor: failure (should be excluded)
        inactive_check = MonitorCheck(
            monitor_id=inactive_monitor.id,
            status_code=500,
            response_time_ms=200,
            is_success=False,
            checked_at=now,
        )
        db_session.add_all([active_check, inactive_check])
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        # Only active monitor counted -> 100%
        assert badges.monitoring_uptime_24h == 100.0

    async def test_uptime_rounded_to_one_decimal(self, db_session):
        """Uptime jest zaokraglony do 1 miejsca po przecinku."""
        project = await _create_project(db_session, slug="sb-round-uptime")

        monitor = Monitor(
            project_id=project.id,
            url="https://example.com",
            name="Rounding Test",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add(monitor)
        await db_session.flush()

        now = datetime.now(UTC)
        # 2 success, 1 failure = 66.666...% -> 66.7%
        checks = [
            MonitorCheck(
                monitor_id=monitor.id,
                status_code=200,
                response_time_ms=100,
                is_success=True,
                checked_at=now - timedelta(hours=1),
            ),
            MonitorCheck(
                monitor_id=monitor.id,
                status_code=200,
                response_time_ms=100,
                is_success=True,
                checked_at=now - timedelta(hours=2),
            ),
            MonitorCheck(
                monitor_id=monitor.id,
                status_code=500,
                response_time_ms=200,
                is_success=False,
                checked_at=now - timedelta(hours=3),
            ),
        ]
        db_session.add_all(checks)
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitoring_uptime_24h == 66.7

    async def test_multiple_monitors_combined_uptime(self, db_session):
        """Uptime obejmuje checki ze wszystkich aktywnych monitorow."""
        project = await _create_project(db_session, slug="sb-multi-uptime")

        monitor_a = Monitor(
            project_id=project.id,
            url="https://a.com",
            name="Monitor A",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        monitor_b = Monitor(
            project_id=project.id,
            url="https://b.com",
            name="Monitor B",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add_all([monitor_a, monitor_b])
        await db_session.flush()

        now = datetime.now(UTC)
        # Monitor A: 1 success
        # Monitor B: 1 failure
        # Total: 1/2 = 50%
        check_a = MonitorCheck(
            monitor_id=monitor_a.id,
            status_code=200,
            response_time_ms=100,
            is_success=True,
            checked_at=now,
        )
        check_b = MonitorCheck(
            monitor_id=monitor_b.id,
            status_code=500,
            response_time_ms=200,
            is_success=False,
            checked_at=now,
        )
        db_session.add_all([check_a, check_b])
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.monitoring_uptime_24h == 50.0


@pytest.mark.integration
class TestGetSidebarBadgesCombined:
    """Testy laczace rozne aspekty badge'ow."""

    async def test_project_with_issues_and_monitors(self, db_session):
        """Projekt z issues i monitorami zwraca poprawne badge'e."""
        project = await _create_project(db_session, slug="sb-combined")

        # 2 unresolved issues (1 recent)
        now = datetime.now(UTC)
        issue_recent = Issue(
            project_id=project.id,
            fingerprint="fp-comb-recent",
            title="Recent Issue",
            status="unresolved",
            last_seen=now - timedelta(days=1),
        )
        issue_old = Issue(
            project_id=project.id,
            fingerprint="fp-comb-old",
            title="Old Issue",
            status="unresolved",
            last_seen=now - timedelta(days=10),
        )
        db_session.add_all([issue_recent, issue_old])
        await db_session.flush()

        # 1 failing monitor, 1 healthy monitor
        monitor_fail = Monitor(
            project_id=project.id,
            url="https://fail-combined.com",
            name="Fail",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        monitor_ok = Monitor(
            project_id=project.id,
            url="https://ok-combined.com",
            name="OK",
            interval_value=5,
            interval_unit="minutes",
            is_active=True,
        )
        db_session.add_all([monitor_fail, monitor_ok])
        await db_session.flush()

        check_fail = MonitorCheck(
            monitor_id=monitor_fail.id,
            status_code=500,
            response_time_ms=200,
            is_success=False,
            checked_at=now,
        )
        check_ok = MonitorCheck(
            monitor_id=monitor_ok.id,
            status_code=200,
            response_time_ms=100,
            is_success=True,
            checked_at=now,
        )
        db_session.add_all([check_fail, check_ok])
        await db_session.flush()

        badges = await get_sidebar_badges(project.id, db_session)

        assert badges.issues_count == 2
        assert badges.issues_pulse is True  # recent issue exists
        assert badges.monitors_failing_count == 1
        assert badges.monitors_failing_pulse is True  # recent failing check
        assert badges.monitoring_uptime_24h == 50.0  # 1 success / 2 total
