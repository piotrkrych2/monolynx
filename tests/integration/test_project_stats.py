"""Testy integracyjne -- serwis get_bulk_project_stats i endpoint project_list."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from monolynx.models.heartbeat import Heartbeat
from monolynx.models.issue import Issue
from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from monolynx.services.project_stats import ProjectStats, get_bulk_project_stats
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_project(db_session, *, slug: str | None = None, name: str | None = None) -> Project:
    """Tworzy testowy projekt z unikalnymi wartosciami."""
    _slug = slug or f"ps-{secrets.token_hex(4)}"
    _name = name or f"Project {_slug}"
    project = Project(
        name=_name,
        slug=_slug,
        code="P" + secrets.token_hex(4).upper()[:9],
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _make_issue(
    db_session,
    project_id: uuid.UUID,
    *,
    status: str = "unresolved",
    fingerprint: str | None = None,
) -> Issue:
    fp = fingerprint or secrets.token_hex(8)
    issue = Issue(
        project_id=project_id,
        fingerprint=fp,
        title=f"Error {fp[:6]}",
        status=status,
        last_seen=datetime.now(UTC),
    )
    db_session.add(issue)
    await db_session.flush()
    return issue


async def _make_monitor(db_session, project_id: uuid.UUID, *, is_active: bool = True) -> Monitor:
    monitor = Monitor(
        project_id=project_id,
        url=f"https://example-{secrets.token_hex(4)}.com",
        name=f"Monitor {secrets.token_hex(4)}",
        interval_value=5,
        interval_unit="minutes",
        is_active=is_active,
    )
    db_session.add(monitor)
    await db_session.flush()
    return monitor


async def _make_check(
    db_session,
    monitor_id: uuid.UUID,
    *,
    is_success: bool = True,
    checked_at: datetime | None = None,
) -> MonitorCheck:
    check = MonitorCheck(
        monitor_id=monitor_id,
        status_code=200 if is_success else 500,
        response_time_ms=100,
        is_success=is_success,
        checked_at=checked_at or datetime.now(UTC),
    )
    db_session.add(check)
    await db_session.flush()
    return check


async def _make_heartbeat(
    db_session,
    project_id: uuid.UUID,
    *,
    status: str = "up",
) -> Heartbeat:
    hb = Heartbeat(
        project_id=project_id,
        name=f"hb-{secrets.token_hex(4)}",
        period=60,
        grace=60,
        status=status,
    )
    db_session.add(hb)
    await db_session.flush()
    return hb


async def _make_sprint(
    db_session,
    project_id: uuid.UUID,
    *,
    status: str = "active",
) -> Sprint:
    from datetime import date

    sprint = Sprint(
        project_id=project_id,
        name=f"Sprint {secrets.token_hex(4)}",
        start_date=date.today(),
        status=status,
    )
    db_session.add(sprint)
    await db_session.flush()
    return sprint


async def _make_ticket(
    db_session,
    project_id: uuid.UUID,
    *,
    sprint_id: uuid.UUID | None = None,
    status: str = "backlog",
    story_points: int | None = None,
) -> Ticket:
    # Generuj unikalny numer ticketu per projekt
    from sqlalchemy import func, select

    result = await db_session.execute(select(func.count(Ticket.id)).where(Ticket.project_id == project_id))
    count = result.scalar() or 0

    ticket = Ticket(
        project_id=project_id,
        number=count + 1,
        title=f"Ticket {secrets.token_hex(4)}",
        status=status,
        sprint_id=sprint_id,
        story_points=story_points,
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _login_existing_user(client, email: str) -> None:
    """Loguje juz istniejacego uzytkownika (bez tworzenia)."""
    response = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


# ---------------------------------------------------------------------------
# Testy serwisu get_bulk_project_stats
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetBulkProjectStatsEmpty:
    """Testy dla pustej listy project_ids i projektow bez danych."""

    async def test_empty_project_ids_returns_empty_dict(self, db_session):
        """Pusta lista project_ids -> pusty dict."""
        result = await get_bulk_project_stats([], db_session)
        assert result == {}

    async def test_project_without_data_returns_defaults(self, db_session):
        """Projekt bez zadnych danych -> domyslne wartosci zerowe."""
        project = await _make_project(db_session, slug="ps-empty-proj")

        result = await get_bulk_project_stats([project.id], db_session)

        assert project.id in result
        stats = result[project.id]
        assert stats.issues_count == 0
        assert stats.issues_pulse is False
        assert stats.monitoring_uptime_24h is None
        assert stats.monitoring_pulse is False
        assert stats.heartbeats_down == 0
        assert stats.sp_done == 0
        assert stats.sp_total == 0
        assert stats.last_activity is None

    async def test_nonexistent_project_id_in_list(self, db_session):
        """Nieistniejace project_id -> domyslne wartosci (brak bledow)."""
        fake_id = uuid.uuid4()

        result = await get_bulk_project_stats([fake_id], db_session)

        assert fake_id in result
        stats = result[fake_id]
        assert stats.issues_count == 0
        assert stats.heartbeats_down == 0


@pytest.mark.integration
class TestGetBulkProjectStatsIssues:
    """Testy zliczania issues."""

    async def test_unresolved_issues_counted(self, db_session):
        """Projekt z unresolved issues -> poprawny count."""
        project = await _make_project(db_session, slug="ps-unres-issues")

        await _make_issue(db_session, project.id, status="unresolved")
        await _make_issue(db_session, project.id, status="unresolved")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].issues_count == 2

    async def test_resolved_issues_not_counted(self, db_session):
        """Resolved issues nie sa wliczane do issues_count."""
        project = await _make_project(db_session, slug="ps-res-issues")

        await _make_issue(db_session, project.id, status="resolved")
        await _make_issue(db_session, project.id, status="resolved")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].issues_count == 0

    async def test_mixed_statuses_only_counts_unresolved(self, db_session):
        """Mieszane statusy -> liczone tylko unresolved."""
        project = await _make_project(db_session, slug="ps-mix-issues")

        await _make_issue(db_session, project.id, status="unresolved")
        await _make_issue(db_session, project.id, status="resolved")
        await _make_issue(db_session, project.id, status="ignored")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].issues_count == 1

    async def test_issues_pulse_true_when_count_ge_5(self, db_session):
        """issues_pulse=True gdy issues_count >= 5."""
        project = await _make_project(db_session, slug="ps-pulse-true")

        for i in range(5):
            await _make_issue(db_session, project.id, status="unresolved", fingerprint=f"fp-pulse-{i}")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].issues_count == 5
        assert result[project.id].issues_pulse is True

    async def test_issues_pulse_false_when_count_lt_5(self, db_session):
        """issues_pulse=False gdy issues_count < 5."""
        project = await _make_project(db_session, slug="ps-pulse-false")

        for i in range(4):
            await _make_issue(db_session, project.id, status="unresolved", fingerprint=f"fp-nopulse-{i}")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].issues_count == 4
        assert result[project.id].issues_pulse is False

    async def test_issues_from_other_project_not_counted(self, db_session):
        """Issues z innego projektu nie sa wliczane."""
        project_a = await _make_project(db_session, slug="ps-proj-a-iss")
        project_b = await _make_project(db_session, slug="ps-proj-b-iss")

        await _make_issue(db_session, project_b.id, status="unresolved")

        result = await get_bulk_project_stats([project_a.id], db_session)

        assert result[project_a.id].issues_count == 0


@pytest.mark.integration
class TestGetBulkProjectStatsMonitoring:
    """Testy uptime monitoringu 24h."""

    async def test_no_monitors_uptime_is_none(self, db_session):
        """Brak monitorow -> monitoring_uptime_24h=None."""
        project = await _make_project(db_session, slug="ps-no-mon")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].monitoring_uptime_24h is None

    async def test_all_success_checks_returns_100(self, db_session):
        """Wszystkie checki success -> uptime=100.0."""
        project = await _make_project(db_session, slug="ps-all-succ")
        monitor = await _make_monitor(db_session, project.id)

        now = datetime.now(UTC)
        for i in range(5):
            await _make_check(db_session, monitor.id, is_success=True, checked_at=now - timedelta(hours=i))

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].monitoring_uptime_24h == 100.0

    async def test_all_fail_checks_returns_0(self, db_session):
        """Wszystkie checki fail -> uptime=0.0."""
        project = await _make_project(db_session, slug="ps-all-fail")
        monitor = await _make_monitor(db_session, project.id)

        now = datetime.now(UTC)
        for i in range(4):
            await _make_check(db_session, monitor.id, is_success=False, checked_at=now - timedelta(hours=i))

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].monitoring_uptime_24h == 0.0

    async def test_mixed_checks_calculates_percentage(self, db_session):
        """4 success + 1 fail -> uptime=80.0."""
        project = await _make_project(db_session, slug="ps-mix-mon")
        monitor = await _make_monitor(db_session, project.id)

        now = datetime.now(UTC)
        for i in range(4):
            await _make_check(db_session, monitor.id, is_success=True, checked_at=now - timedelta(hours=i))
        await _make_check(db_session, monitor.id, is_success=False, checked_at=now - timedelta(hours=4))

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].monitoring_uptime_24h == 80.0

    async def test_old_checks_excluded_from_uptime(self, db_session):
        """Checki starsze niz 24h nie wliczone do uptime."""
        project = await _make_project(db_session, slug="ps-old-checks")
        monitor = await _make_monitor(db_session, project.id)

        now = datetime.now(UTC)
        # Success w ciagu 24h
        await _make_check(db_session, monitor.id, is_success=True, checked_at=now - timedelta(hours=1))
        # Fail poza 24h -> nie wliczony
        await _make_check(db_session, monitor.id, is_success=False, checked_at=now - timedelta(hours=25))

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].monitoring_uptime_24h == 100.0

    async def test_inactive_monitor_excluded_from_uptime(self, db_session):
        """Checki nieaktywnych monitorow nie wliczone do uptime."""
        project = await _make_project(db_session, slug="ps-inact-mon")
        active_mon = await _make_monitor(db_session, project.id, is_active=True)
        inactive_mon = await _make_monitor(db_session, project.id, is_active=False)

        now = datetime.now(UTC)
        await _make_check(db_session, active_mon.id, is_success=True, checked_at=now)
        await _make_check(db_session, inactive_mon.id, is_success=False, checked_at=now)

        result = await get_bulk_project_stats([project.id], db_session)

        # Tylko aktywny monitor -> 100%
        assert result[project.id].monitoring_uptime_24h == 100.0

    async def test_monitoring_pulse_true_when_uptime_below_90(self, db_session):
        """monitoring_pulse=True gdy uptime < 90.0."""
        project = await _make_project(db_session, slug="ps-mon-pulse-t")
        monitor = await _make_monitor(db_session, project.id)

        now = datetime.now(UTC)
        # 1 success + 9 fail = 10% uptime -> pulse=True
        await _make_check(db_session, monitor.id, is_success=True, checked_at=now)
        for i in range(1, 10):
            await _make_check(db_session, monitor.id, is_success=False, checked_at=now - timedelta(hours=i))

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].monitoring_pulse is True

    async def test_monitoring_pulse_false_when_uptime_ge_90(self, db_session):
        """monitoring_pulse=False gdy uptime >= 90.0."""
        project = await _make_project(db_session, slug="ps-mon-pulse-f")
        monitor = await _make_monitor(db_session, project.id)

        now = datetime.now(UTC)
        # 9 success + 1 fail = 90% -> pulse=False
        for i in range(9):
            await _make_check(db_session, monitor.id, is_success=True, checked_at=now - timedelta(hours=i))
        await _make_check(db_session, monitor.id, is_success=False, checked_at=now - timedelta(hours=9))

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].monitoring_pulse is False


@pytest.mark.integration
class TestGetBulkProjectStatsHeartbeats:
    """Testy zliczania heartbeats down."""

    async def test_no_heartbeats_returns_zero(self, db_session):
        """Brak heartbeatow -> heartbeats_down=0."""
        project = await _make_project(db_session, slug="ps-no-hb")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].heartbeats_down == 0

    async def test_down_heartbeats_counted(self, db_session):
        """Heartbeaty ze statusem 'down' sa zliczane."""
        project = await _make_project(db_session, slug="ps-hb-down")

        await _make_heartbeat(db_session, project.id, status="down")
        await _make_heartbeat(db_session, project.id, status="down")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].heartbeats_down == 2

    async def test_up_heartbeats_not_counted(self, db_session):
        """Heartbeaty ze statusem 'up' nie sa wliczane do down."""
        project = await _make_project(db_session, slug="ps-hb-up")

        await _make_heartbeat(db_session, project.id, status="up")
        await _make_heartbeat(db_session, project.id, status="pending")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].heartbeats_down == 0

    async def test_mixed_heartbeat_statuses(self, db_session):
        """Mieszane statusy -> liczone tylko 'down'."""
        project = await _make_project(db_session, slug="ps-hb-mix")

        await _make_heartbeat(db_session, project.id, status="down")
        await _make_heartbeat(db_session, project.id, status="up")
        await _make_heartbeat(db_session, project.id, status="pending")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].heartbeats_down == 1

    async def test_heartbeats_from_other_project_not_counted(self, db_session):
        """Heartbeaty z innego projektu nie wplywaja na wyniki."""
        project_a = await _make_project(db_session, slug="ps-hb-proj-a")
        project_b = await _make_project(db_session, slug="ps-hb-proj-b")

        await _make_heartbeat(db_session, project_b.id, status="down")

        result = await get_bulk_project_stats([project_a.id], db_session)

        assert result[project_a.id].heartbeats_down == 0


@pytest.mark.integration
class TestGetBulkProjectStatsScrumSP:
    """Testy sp_total i sp_done z aktywnego sprintu."""

    async def test_no_sprint_returns_zero_sp(self, db_session):
        """Brak sprintu -> sp_total=0, sp_done=0."""
        project = await _make_project(db_session, slug="ps-no-sprint")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].sp_total == 0
        assert result[project.id].sp_done == 0

    async def test_active_sprint_with_sp_totals(self, db_session):
        """Aktywny sprint z ticketami -> poprawne sp_total i sp_done."""
        project = await _make_project(db_session, slug="ps-sprint-sp")
        sprint = await _make_sprint(db_session, project.id, status="active")

        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="done", story_points=3)
        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="done", story_points=5)
        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="in_progress", story_points=2)

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].sp_total == 10
        assert result[project.id].sp_done == 8

    async def test_null_story_points_treated_as_zero(self, db_session):
        """Tickety bez story_points (NULL) sa traktowane jako 0."""
        project = await _make_project(db_session, slug="ps-null-sp")
        sprint = await _make_sprint(db_session, project.id, status="active")

        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="done", story_points=None)
        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="backlog", story_points=None)

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].sp_total == 0
        assert result[project.id].sp_done == 0

    async def test_completed_sprint_not_counted(self, db_session):
        """Skonczony sprint nie jest wliczany."""
        project = await _make_project(db_session, slug="ps-compl-sprint")
        sprint = await _make_sprint(db_session, project.id, status="completed")

        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="done", story_points=8)

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].sp_total == 0
        assert result[project.id].sp_done == 0

    async def test_planning_sprint_not_counted(self, db_session):
        """Sprint w planowaniu nie jest wliczany."""
        project = await _make_project(db_session, slug="ps-plan-sprint")
        sprint = await _make_sprint(db_session, project.id, status="planning")

        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="done", story_points=5)

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].sp_total == 0
        assert result[project.id].sp_done == 0

    async def test_mixed_sp_with_null(self, db_session):
        """Mieszane SP: czesc ma wartosc, czesc NULL -> poprawna suma."""
        project = await _make_project(db_session, slug="ps-mix-sp")
        sprint = await _make_sprint(db_session, project.id, status="active")

        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="done", story_points=5)
        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="done", story_points=None)
        await _make_ticket(db_session, project.id, sprint_id=sprint.id, status="backlog", story_points=3)

        result = await get_bulk_project_stats([project.id], db_session)

        # sp_total = 5 + 0 + 3 = 8, sp_done = 5 + 0 = 5
        assert result[project.id].sp_total == 8
        assert result[project.id].sp_done == 5


@pytest.mark.integration
class TestGetBulkProjectStatsLastActivity:
    """Testy last_activity (max updated_at z ticketow)."""

    async def test_no_tickets_last_activity_is_none(self, db_session):
        """Brak ticketow -> last_activity=None."""
        project = await _make_project(db_session, slug="ps-no-tickets")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].last_activity is None

    async def test_last_activity_from_most_recent_ticket(self, db_session):
        """last_activity to max updated_at z ticketow."""
        project = await _make_project(db_session, slug="ps-last-act")

        await _make_ticket(db_session, project.id, status="backlog")
        await _make_ticket(db_session, project.id, status="in_progress")

        result = await get_bulk_project_stats([project.id], db_session)

        assert result[project.id].last_activity is not None

    async def test_tickets_from_other_project_not_counted(self, db_session):
        """Tickety z innego projektu nie wplywaja na last_activity."""
        project_a = await _make_project(db_session, slug="ps-la-proj-a")
        project_b = await _make_project(db_session, slug="ps-la-proj-b")

        await _make_ticket(db_session, project_b.id, status="backlog")

        result = await get_bulk_project_stats([project_a.id], db_session)

        assert result[project_a.id].last_activity is None


@pytest.mark.integration
class TestGetBulkProjectStatsMultipleProjects:
    """Testy dla wielu projektow naraz."""

    async def test_multiple_projects_correctly_mapped(self, db_session):
        """Statystyki dla wielu projektow sa poprawnie zmapowane per project_id."""
        project_a = await _make_project(db_session, slug="ps-multi-a")
        project_b = await _make_project(db_session, slug="ps-multi-b")

        # Projekt A: 3 unresolved issues
        for i in range(3):
            await _make_issue(db_session, project_a.id, status="unresolved", fingerprint=f"fp-multi-a-{i}")

        # Projekt B: 1 unresolved issue + 1 heartbeat down
        await _make_issue(db_session, project_b.id, status="unresolved", fingerprint="fp-multi-b-0")
        await _make_heartbeat(db_session, project_b.id, status="down")

        result = await get_bulk_project_stats([project_a.id, project_b.id], db_session)

        assert project_a.id in result
        assert project_b.id in result

        assert result[project_a.id].issues_count == 3
        assert result[project_a.id].heartbeats_down == 0

        assert result[project_b.id].issues_count == 1
        assert result[project_b.id].heartbeats_down == 1

    async def test_all_project_ids_present_in_result(self, db_session):
        """Wszystkie project_ids sa obecne w wyniku nawet bez danych."""
        projects = [await _make_project(db_session, slug=f"ps-all-{i}") for i in range(3)]
        project_ids = [p.id for p in projects]

        result = await get_bulk_project_stats(project_ids, db_session)

        for pid in project_ids:
            assert pid in result

    async def test_stats_do_not_bleed_between_projects(self, db_session):
        """Dane jednego projektu nie wplywaja na statystyki innego."""
        project_a = await _make_project(db_session, slug="ps-bleed-a")
        project_b = await _make_project(db_session, slug="ps-bleed-b")

        # Dodaj dane tylko do projektu A
        monitor = await _make_monitor(db_session, project_a.id)
        await _make_check(db_session, monitor.id, is_success=False, checked_at=datetime.now(UTC))
        await _make_heartbeat(db_session, project_a.id, status="down")

        result = await get_bulk_project_stats([project_a.id, project_b.id], db_session)

        # Projekt B nie powinien miec zadnych danych
        assert result[project_b.id].monitoring_uptime_24h is None
        assert result[project_b.id].heartbeats_down == 0

    async def test_project_stats_dataclass_defaults(self):
        """ProjectStats dataclass ma poprawne domyslne wartosci."""
        stats = ProjectStats()
        assert stats.issues_count == 0
        assert stats.issues_pulse is False
        assert stats.monitoring_uptime_24h is None
        assert stats.monitoring_pulse is False
        assert stats.heartbeats_down == 0
        assert stats.sp_done == 0
        assert stats.sp_total == 0
        assert stats.last_activity is None


# ---------------------------------------------------------------------------
# Testy endpointu project_list (GET /dashboard/)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProjectListEndpoint:
    """Testy endpointu GET /dashboard/ -- lista projektow."""

    async def test_unauthenticated_redirects_to_login(self, client):
        """Niezalogowany uzytkownik -> redirect na /auth/login."""
        response = await client.get("/dashboard/", follow_redirects=False)
        assert response.status_code == 303
        assert "/auth/login" in response.headers["location"]

    async def test_authenticated_returns_200(self, client, db_session):
        """Zalogowany uzytkownik -> 200 OK."""
        client = await login_session(client, db_session, email="pl-auth@example.com")

        response = await client.get("/dashboard/", follow_redirects=False)
        assert response.status_code == 200

    async def test_shows_only_user_projects(self, client, db_session):
        """Zwykly user widzi tylko swoje projekty (przez ProjectMember)."""
        project_mine = await _make_project(db_session, slug="pl-mine")
        _project_other = await _make_project(db_session, slug="pl-other")

        # Tworzymy uzytkownika i przypisujemy go do jednego projektu
        user = User(
            email="pl-user@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        member = ProjectMember(project_id=project_mine.id, user_id=user.id, role="member")
        db_session.add(member)
        await db_session.flush()

        await _login_existing_user(client, "pl-user@example.com")

        response = await client.get("/dashboard/")
        assert response.status_code == 200
        assert "pl-mine" in response.text
        assert "pl-other" not in response.text

    async def test_superuser_sees_all_projects(self, client, db_session):
        """Superuser widzi wszystkie aktywne projekty."""
        _project_a = await _make_project(db_session, slug="pl-su-a")
        _project_b = await _make_project(db_session, slug="pl-su-b")

        # Superuser bez czlonkostwa w projektach
        superuser = User(
            email="pl-superuser@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-superuser@example.com")

        response = await client.get("/dashboard/")
        assert response.status_code == 200
        assert "pl-su-a" in response.text
        assert "pl-su-b" in response.text

    async def test_inactive_projects_not_shown(self, client, db_session):
        """Soft-deleted projekty (is_active=False) nie sa pokazywane."""
        _active_project = await _make_project(db_session, slug="pl-active-vis")

        inactive_project = Project(
            name="Inactive Project",
            slug="pl-inactive-vis",
            code="PINV",
            api_key=secrets.token_urlsafe(32),
            is_active=False,
        )
        db_session.add(inactive_project)
        await db_session.flush()

        superuser = User(
            email="pl-inact-vis@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-inact-vis@example.com")

        response = await client.get("/dashboard/")
        assert response.status_code == 200
        assert "pl-active-vis" in response.text
        assert "pl-inactive-vis" not in response.text

    async def test_search_filters_by_name(self, client, db_session):
        """Parametr ?search= filtruje projekty po nazwie (ILIKE)."""
        proj_alpha = Project(
            name="Alpha Project",
            slug="pl-search-alpha",
            code="SALP",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        proj_beta = Project(
            name="Beta Project",
            slug="pl-search-beta",
            code="SBET",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add_all([proj_alpha, proj_beta])
        await db_session.flush()

        superuser = User(
            email="pl-search-name@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-search-name@example.com")

        response = await client.get("/dashboard/?search=Alpha")
        assert response.status_code == 200
        assert "Alpha Project" in response.text
        assert "Beta Project" not in response.text

    async def test_search_filters_by_slug(self, client, db_session):
        """Parametr ?search= filtruje projekty takze po slugu (ILIKE)."""
        proj = Project(
            name="Unique Project Name",
            slug="pl-slugsearch-uniq",
            code="SSUN",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        other = Project(
            name="Other Project Name",
            slug="pl-slugsearch-othr",
            code="SSOT",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add_all([proj, other])
        await db_session.flush()

        superuser = User(
            email="pl-search-slug@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-search-slug@example.com")

        response = await client.get("/dashboard/?search=uniq")
        assert response.status_code == 200
        assert "pl-slugsearch-uniq" in response.text
        assert "pl-slugsearch-othr" not in response.text

    async def test_search_empty_shows_all_projects(self, client, db_session):
        """Pusty search -> superuser widzi swoje projekty (sprawdzamy ze oba sa widoczne)."""
        proj_a = Project(
            name="Search Empty A",
            slug="pl-se-empty-a",
            code="SEEA",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        proj_b = Project(
            name="Search Empty B",
            slug="pl-se-empty-b",
            code="SEEB",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add_all([proj_a, proj_b])
        await db_session.flush()

        superuser = User(
            email="pl-se-empty@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-se-empty@example.com")

        response = await client.get("/dashboard/?search=")
        assert response.status_code == 200
        assert "pl-se-empty-a" in response.text
        assert "pl-se-empty-b" in response.text

    async def test_sort_name_asc_param_accepted(self, client, db_session):
        """Sortowanie ?sort=name_asc zwraca 200 bez bledu."""
        superuser = User(
            email="pl-sort-nasc@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-sort-nasc@example.com")

        response = await client.get("/dashboard/?sort=name_asc")
        assert response.status_code == 200

    async def test_sort_name_desc_param_accepted(self, client, db_session):
        """Sortowanie ?sort=name_desc zwraca 200 bez bledu."""
        superuser = User(
            email="pl-sort-ndesc@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-sort-ndesc@example.com")

        response = await client.get("/dashboard/?sort=name_desc")
        assert response.status_code == 200

    async def test_sort_activity_asc_param_accepted(self, client, db_session):
        """Sortowanie ?sort=activity_asc zwraca 200 bez bledu."""
        superuser = User(
            email="pl-sort-aasc@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-sort-aasc@example.com")

        response = await client.get("/dashboard/?sort=activity_asc")
        assert response.status_code == 200

    async def test_sort_invalid_falls_back_to_activity_desc(self, client, db_session):
        """Nieprawidlowy sort -> fallback do activity_desc (200)."""
        superuser = User(
            email="pl-sort-inv@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-sort-inv@example.com")

        response = await client.get("/dashboard/?sort=invalid_sort_value")
        assert response.status_code == 200

    async def test_pagination_page_param(self, client, db_session):
        """Parametr ?page= jest akceptowany i nie powoduje bledu."""
        superuser = User(
            email="pl-page-param@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-page-param@example.com")

        response = await client.get("/dashboard/?page=1")
        assert response.status_code == 200

    async def test_pagination_invalid_page_falls_back_to_1(self, client, db_session):
        """Nieprawidlowy ?page= -> fallback do page=1 (200)."""
        superuser = User(
            email="pl-page-inv@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-page-inv@example.com")

        response = await client.get("/dashboard/?page=invalid")
        assert response.status_code == 200

    async def test_stats_included_in_response(self, client, db_session):
        """Statystyki projektow sa przekazane do template (html zawiera dane projektu)."""
        proj = Project(
            name="Stats Test Project",
            slug="pl-stats-resp",
            code="STSP",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(proj)
        await db_session.flush()

        # Dodaj 5 issues -> issues_pulse=True
        for i in range(5):
            issue = Issue(
                project_id=proj.id,
                fingerprint=f"fp-stats-resp-{i}",
                title=f"Error {i}",
                status="unresolved",
                last_seen=datetime.now(UTC),
            )
            db_session.add(issue)
        await db_session.flush()

        superuser = User(
            email="pl-stats-resp@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-stats-resp@example.com")

        response = await client.get("/dashboard/")
        assert response.status_code == 200
        # Projekt powinien byc widoczny
        assert "Stats Test Project" in response.text

    async def test_pagination_context_variables(self, client, db_session):
        """Kontekst template zawiera zmienne paginacji (200 i poprawny render)."""
        superuser = User(
            email="pl-pag-ctx@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-pag-ctx@example.com")

        response = await client.get("/dashboard/")
        assert response.status_code == 200

    async def test_search_and_sort_combined(self, client, db_session):
        """Wyszukiwanie i sortowanie moga byc laczone."""
        superuser = User(
            email="pl-comb-srch@example.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        await _login_existing_user(client, "pl-comb-srch@example.com")

        response = await client.get("/dashboard/?search=test&sort=name_asc")
        assert response.status_code == 200
