"""Testy integracyjne -- dashboard raportow pracy (cross-project)."""

import secrets
from datetime import date

import pytest

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.time_tracking_entry import TimeTrackingEntry
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_project(db_session, slug, code=None, name=None):
    """Tworzy projekt z unikalnym kodem."""
    project = Project(
        name=name or f"Project {slug}",
        slug=slug,
        code=code or ("R" + secrets.token_hex(3).upper()),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _create_user(db_session, email, *, is_superuser=False):
    """Tworzy uzytkownika z podanym emailem."""
    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
        is_superuser=is_superuser,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _add_member(db_session, project, user, role="member"):
    """Dodaje uzytkownika jako czlonka projektu."""
    member = ProjectMember(project_id=project.id, user_id=user.id, role=role)
    db_session.add(member)
    await db_session.flush()
    return member


async def _login(client, email):
    """Loguje uzytkownika na istniejacym koncie."""
    resp = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client


async def _create_sprint(db_session, project, name="Sprint 1", status="active"):
    """Tworzy sprint w projekcie."""
    sprint = Sprint(
        project_id=project.id,
        name=name,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 14),
        status=status,
    )
    db_session.add(sprint)
    await db_session.flush()
    return sprint


async def _create_ticket(db_session, project, sprint=None, title="Test Ticket", number=1):
    """Tworzy ticket w projekcie."""
    ticket = Ticket(
        project_id=project.id,
        title=title,
        description="Test",
        status="todo",
        priority="medium",
        sprint_id=sprint.id if sprint else None,
        number=number,
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _create_time_entry(
    db_session,
    ticket,
    user,
    sprint=None,
    duration_minutes=120,
    date_logged=None,
    description="Dev work",
    created_via_ai=False,
):
    """Tworzy wpis czasu pracy."""
    entry = TimeTrackingEntry(
        ticket_id=ticket.id,
        user_id=user.id,
        sprint_id=sprint.id if sprint else None,
        project_id=ticket.project_id,
        duration_minutes=duration_minutes,
        date_logged=date_logged or date(2026, 1, 5),
        description=description,
        status="draft",
        created_via_ai=created_via_ai,
    )
    db_session.add(entry)
    await db_session.flush()
    return entry


async def _setup_full_scenario(db_session, suffix):
    """Tworzy pelny scenariusz: projekt, user, member, sprint, ticket, time entry."""
    project = await _create_project(db_session, f"rpt-{suffix}")
    user = await _create_user(db_session, f"rpt-{suffix}@test.com")
    await _add_member(db_session, project, user, role="owner")
    sprint = await _create_sprint(db_session, project)
    ticket = await _create_ticket(db_session, project, sprint)
    entry = await _create_time_entry(db_session, ticket, user, sprint)
    return {
        "project": project,
        "user": user,
        "sprint": sprint,
        "ticket": ticket,
        "entry": entry,
    }


# ===========================================================================
# GET /dashboard/reports -- main reports page
# ===========================================================================


@pytest.mark.integration
class TestGlobalReportsPage:
    async def test_not_logged_in_redirects(self, client, db_session):
        """GET /dashboard/reports bez sesji -> redirect na login."""
        resp = await client.get("/dashboard/reports", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_logged_in_no_data(self, client, db_session):
        """GET /dashboard/reports zalogowany, brak danych -> 200 z pustym raportem."""
        await login_session(client, db_session, email="rpt-nodata@test.com")
        resp = await client.get("/dashboard/reports")
        assert resp.status_code == 200

    async def test_logged_in_with_time_entries(self, client, db_session):
        """GET /dashboard/reports zalogowany z wpisami -> 200 z danymi raportu."""
        data = await _setup_full_scenario(db_session, "withdata")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert "Dev work" in resp.text

    async def test_with_project_id_filter(self, client, db_session):
        """GET z project_id filtruje po projekcie."""
        data = await _setup_full_scenario(db_session, "filtproj")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={
                "project_id": str(data["project"].id),
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        assert "Dev work" in resp.text

    async def test_with_invalid_project_id_ignored(self, client, db_session):
        """GET z nieprawidlowym project_id nie powoduje bledu."""
        data = await _setup_full_scenario(db_session, "filtinvproj")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={
                "project_id": "not-a-uuid",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200

    async def test_with_unauthorized_project_id_filtered_out(self, client, db_session):
        """GET z project_id do ktorego user nie ma dostepu -- filtrowany."""
        data = await _setup_full_scenario(db_session, "filtunauth")
        other_project = await _create_project(db_session, "rpt-other-proj")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={
                "project_id": str(other_project.id),
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        # Entry from own project should not appear when filtering by unauthorized project
        # (the filter rejects unauthorized project_ids, so no project_ids remain in filter,
        # and effective_project_ids falls back to all allowed -- which includes own project)

    async def test_with_user_id_filter(self, client, db_session):
        """GET z user_id filtruje po uzytkowniku."""
        data = await _setup_full_scenario(db_session, "filtuser")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={
                "user_id": str(data["user"].id),
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        assert "Dev work" in resp.text

    async def test_with_sprint_id_filter(self, client, db_session):
        """GET z sprint_id filtruje po sprincie."""
        data = await _setup_full_scenario(db_session, "filtsprint")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={
                "sprint_id": str(data["sprint"].id),
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        assert "Dev work" in resp.text

    async def test_with_date_range_filter(self, client, db_session):
        """GET z date_from i date_to filtruje po dacie."""
        data = await _setup_full_scenario(db_session, "filtdate")
        await _login(client, data["user"].email)

        # Entry is on 2026-01-05, filter outside that range
        resp = await client.get(
            "/dashboard/reports",
            params={"date_from": "2026-02-01", "date_to": "2026-02-28"},
        )
        assert resp.status_code == 200
        assert "Dev work" not in resp.text

    async def test_with_date_range_includes_entry(self, client, db_session):
        """GET z date_from/date_to obejmujacym wpis -> wyswietla go."""
        data = await _setup_full_scenario(db_session, "filtdatein")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert "Dev work" in resp.text

    async def test_default_date_range_applied(self, client, db_session):
        """GET bez parametrow dat stosuje domyslny zakres (ostatnie 30 dni)."""
        data = await _setup_full_scenario(db_session, "filtdefdate")
        await _login(client, data["user"].email)

        resp = await client.get("/dashboard/reports")
        assert resp.status_code == 200
        # The default range is last 30 days from today, our entry is from 2026-01-05
        # so it may or may not appear depending on "today" -- just check page works.

    async def test_with_page_param(self, client, db_session):
        """GET z page param -- paginacja dziala."""
        data = await _setup_full_scenario(db_session, "filtpage")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"page": "1", "date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200

    async def test_with_page_param_too_high(self, client, db_session):
        """GET z page wiekszym niz total_pages -> 200 z pustymi wpisami."""
        data = await _setup_full_scenario(db_session, "filtpagehigh")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"page": "999", "date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200

    async def test_with_invalid_page_param(self, client, db_session):
        """GET z nieprawidlowym page param -> fallback do 1."""
        data = await _setup_full_scenario(db_session, "filtpageinv")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"page": "abc", "date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200

    async def test_with_sort_date(self, client, db_session):
        """GET z sort=date -> sortowanie po dacie."""
        data = await _setup_full_scenario(db_session, "sortdate")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"sort": "date", "date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200

    async def test_with_sort_hours(self, client, db_session):
        """GET z sort=hours -> sortowanie po godzinach."""
        data = await _setup_full_scenario(db_session, "sorthours")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"sort": "hours", "date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200

    async def test_with_sort_user(self, client, db_session):
        """GET z sort=user -> sortowanie po uzytkowniku."""
        data = await _setup_full_scenario(db_session, "sortuser")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"sort": "user", "date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200

    async def test_with_invalid_sort_ignored(self, client, db_session):
        """GET z nieprawidlowym sort param -> fallback do domyslnego."""
        data = await _setup_full_scenario(db_session, "sortinv")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"sort": "invalid", "date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200

    async def test_with_ai_filter_true(self, client, db_session):
        """GET z ai=1 -> tylko wpisy AI."""
        data = await _setup_full_scenario(db_session, "filtai1")
        # Add an AI entry
        await _create_time_entry(
            db_session,
            data["ticket"],
            data["user"],
            data["sprint"],
            duration_minutes=60,
            date_logged=date(2026, 1, 6),
            description="AI generated work",
            created_via_ai=True,
        )
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"ai": "1", "date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert "AI generated work" in resp.text
        # Non-AI entry should not appear
        assert "Dev work" not in resp.text

    async def test_with_ai_filter_false(self, client, db_session):
        """GET z ai=0 -> tylko wpisy reczne (nie-AI)."""
        data = await _setup_full_scenario(db_session, "filtai0")
        # Add an AI entry
        await _create_time_entry(
            db_session,
            data["ticket"],
            data["user"],
            data["sprint"],
            duration_minutes=60,
            date_logged=date(2026, 1, 6),
            description="AI generated entry",
            created_via_ai=True,
        )
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"ai": "0", "date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert "Dev work" in resp.text
        assert "AI generated entry" not in resp.text

    async def test_superuser_sees_all_projects(self, client, db_session):
        """Superuser widzi wpisy ze wszystkich projektow."""
        # Setup: two projects, one with entries, superuser not a member of either
        project1 = await _create_project(db_session, "rpt-su-p1")
        project2 = await _create_project(db_session, "rpt-su-p2")

        user1 = await _create_user(db_session, "rpt-su-u1@test.com")
        await _add_member(db_session, project1, user1)
        sprint1 = await _create_sprint(db_session, project1, name="Sprint SU1")
        ticket1 = await _create_ticket(db_session, project1, sprint1)
        await _create_time_entry(
            db_session,
            ticket1,
            user1,
            sprint1,
            description="Work P1",
            date_logged=date(2026, 1, 5),
        )

        user2 = await _create_user(db_session, "rpt-su-u2@test.com")
        await _add_member(db_session, project2, user2)
        sprint2 = await _create_sprint(db_session, project2, name="Sprint SU2")
        ticket2 = await _create_ticket(db_session, project2, sprint2)
        await _create_time_entry(
            db_session,
            ticket2,
            user2,
            sprint2,
            description="Work P2",
            date_logged=date(2026, 1, 6),
        )

        # Create and login as superuser
        superuser = await _create_user(db_session, "rpt-superadmin@test.com", is_superuser=True)
        await _login(client, superuser.email)

        resp = await client.get(
            "/dashboard/reports",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert "Work P1" in resp.text
        assert "Work P2" in resp.text

    async def test_normal_user_sees_only_own_projects(self, client, db_session):
        """Normalny user widzi tylko wpisy z projektow, w ktorych jest czlonkiem."""
        project_mine = await _create_project(db_session, "rpt-own-mine")
        project_other = await _create_project(db_session, "rpt-own-other")

        user = await _create_user(db_session, "rpt-own-user@test.com")
        await _add_member(db_session, project_mine, user)

        sprint_mine = await _create_sprint(db_session, project_mine, name="My Sprint")
        ticket_mine = await _create_ticket(db_session, project_mine, sprint_mine)
        await _create_time_entry(
            db_session,
            ticket_mine,
            user,
            sprint_mine,
            description="My Work",
            date_logged=date(2026, 1, 5),
        )

        other_user = await _create_user(db_session, "rpt-own-other@test.com")
        await _add_member(db_session, project_other, other_user)
        sprint_other = await _create_sprint(db_session, project_other, name="Other Sprint")
        ticket_other = await _create_ticket(db_session, project_other, sprint_other)
        await _create_time_entry(
            db_session,
            ticket_other,
            other_user,
            sprint_other,
            description="Other Work",
            date_logged=date(2026, 1, 6),
        )

        await _login(client, user.email)

        resp = await client.get(
            "/dashboard/reports",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert "My Work" in resp.text
        assert "Other Work" not in resp.text

    async def test_shows_ticket_key(self, client, db_session):
        """Raport wyswietla klucz ticketu (CODE-NUM)."""
        project = await _create_project(db_session, "rpt-tkey", code="TKY")
        user = await _create_user(db_session, "rpt-tkey@test.com")
        await _add_member(db_session, project, user, role="owner")
        sprint = await _create_sprint(db_session, project)
        ticket = await _create_ticket(db_session, project, sprint, title="Key Ticket", number=42)
        await _create_time_entry(
            db_session,
            ticket,
            user,
            sprint,
            description="Key Work",
            date_logged=date(2026, 1, 5),
        )

        await _login(client, user.email)

        resp = await client.get(
            "/dashboard/reports",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert "TKY-42" in resp.text

    async def test_multiple_entries_stats(self, client, db_session):
        """Raport z wieloma wpisami wyswietla poprawne statystyki."""
        data = await _setup_full_scenario(db_session, "multistats")
        # Add second entry
        await _create_time_entry(
            db_session,
            data["ticket"],
            data["user"],
            data["sprint"],
            duration_minutes=60,
            date_logged=date(2026, 1, 7),
            description="Extra work",
        )
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        # 120 + 60 = 180 min = 3h total
        assert "Dev work" in resp.text
        assert "Extra work" in resp.text


# ===========================================================================
# Chart data endpoints
# ===========================================================================


@pytest.mark.integration
class TestChartSprintHours:
    async def test_not_logged_in_returns_401(self, client, db_session):
        """GET /dashboard/reports/data/sprint-hours bez sesji -> 401."""
        resp = await client.get("/dashboard/reports/data/sprint-hours")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Unauthorized"

    async def test_returns_sprint_hours_data(self, client, db_session):
        """GET z danymi -> JSON z godzinami na sprint."""
        data = await _setup_full_scenario(db_session, "chsprint")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports/data/sprint-hours",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        json_data = resp.json()
        assert isinstance(json_data, list)
        assert len(json_data) >= 1
        item = json_data[0]
        assert "sprint_id" in item
        assert "sprint_name" in item
        assert "total_hours" in item
        assert item["sprint_name"] == "Sprint 1"
        assert item["total_hours"] == 2.0  # 120 min

    async def test_empty_data_returns_empty_list(self, client, db_session):
        """GET bez wpisow -> pusta lista."""
        await login_session(client, db_session, email="chsprint-empty@test.com")

        resp = await client.get(
            "/dashboard/reports/data/sprint-hours",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.integration
class TestChartUserHours:
    async def test_not_logged_in_returns_401(self, client, db_session):
        """GET /dashboard/reports/data/user-hours bez sesji -> 401."""
        resp = await client.get("/dashboard/reports/data/user-hours")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Unauthorized"

    async def test_returns_user_hours_data(self, client, db_session):
        """GET z danymi -> JSON z godzinami na uzytkownika."""
        data = await _setup_full_scenario(db_session, "chuser")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports/data/user-hours",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        json_data = resp.json()
        assert isinstance(json_data, list)
        assert len(json_data) >= 1
        item = json_data[0]
        assert "user_id" in item
        assert "user_name" in item
        assert "total_hours" in item
        assert "percentage" in item
        assert item["total_hours"] == 2.0
        assert item["percentage"] == 100.0

    async def test_empty_data_returns_empty_list(self, client, db_session):
        """GET bez wpisow -> pusta lista."""
        await login_session(client, db_session, email="chuser-empty@test.com")

        resp = await client.get(
            "/dashboard/reports/data/user-hours",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.integration
class TestChartProjectHours:
    async def test_not_logged_in_returns_401(self, client, db_session):
        """GET /dashboard/reports/data/project-hours bez sesji -> 401."""
        resp = await client.get("/dashboard/reports/data/project-hours")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Unauthorized"

    async def test_returns_project_hours_data(self, client, db_session):
        """GET z danymi -> JSON z godzinami na projekt."""
        data = await _setup_full_scenario(db_session, "chproj")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports/data/project-hours",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        json_data = resp.json()
        assert isinstance(json_data, list)
        assert len(json_data) >= 1
        item = json_data[0]
        assert "project_id" in item
        assert "project_name" in item
        assert "total_hours" in item
        assert item["total_hours"] == 2.0

    async def test_empty_data_returns_empty_list(self, client, db_session):
        """GET bez wpisow -> pusta lista."""
        await login_session(client, db_session, email="chproj-empty@test.com")

        resp = await client.get(
            "/dashboard/reports/data/project-hours",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_multiple_projects(self, client, db_session):
        """Superuser z wpisami w wielu projektach -> dane z obu projektow."""
        project1 = await _create_project(db_session, "chproj-mp1")
        project2 = await _create_project(db_session, "chproj-mp2")

        user1 = await _create_user(db_session, "chproj-mp-u1@test.com")
        await _add_member(db_session, project1, user1)
        sprint1 = await _create_sprint(db_session, project1, name="S1")
        ticket1 = await _create_ticket(db_session, project1, sprint1)
        await _create_time_entry(
            db_session,
            ticket1,
            user1,
            sprint1,
            duration_minutes=60,
            date_logged=date(2026, 1, 5),
        )

        user2 = await _create_user(db_session, "chproj-mp-u2@test.com")
        await _add_member(db_session, project2, user2)
        sprint2 = await _create_sprint(db_session, project2, name="S2")
        ticket2 = await _create_ticket(db_session, project2, sprint2)
        await _create_time_entry(
            db_session,
            ticket2,
            user2,
            sprint2,
            duration_minutes=90,
            date_logged=date(2026, 1, 6),
        )

        superuser = await _create_user(db_session, "chproj-mp-su@test.com", is_superuser=True)
        await _login(client, superuser.email)

        resp = await client.get(
            "/dashboard/reports/data/project-hours",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        json_data = resp.json()
        # At least 2 projects should be in the chart data
        project_ids = {item["project_id"] for item in json_data}
        assert str(project1.id) in project_ids
        assert str(project2.id) in project_ids


# ===========================================================================
# CSV/PDF export
# ===========================================================================


@pytest.mark.integration
class TestExportReport:
    async def test_not_logged_in_returns_401(self, client, db_session):
        """GET /dashboard/reports/export bez sesji -> 401."""
        resp = await client.get("/dashboard/reports/export")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Unauthorized"

    async def test_csv_export_returns_csv(self, client, db_session):
        """GET z format=csv -> CSV z poprawnym content-type."""
        data = await _setup_full_scenario(db_session, "expcsv")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports/export",
            params={
                "format": "csv",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert "global-work-report-" in resp.headers["content-disposition"]
        assert ".csv" in resp.headers["content-disposition"]

    async def test_csv_export_contains_headers(self, client, db_session):
        """CSV zawiera naglowki kolumn."""
        data = await _setup_full_scenario(db_session, "expcsv-hdr")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports/export",
            params={
                "format": "csv",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        content = resp.text
        first_line = content.split("\n")[0]
        assert "Ticket" in first_line
        assert "Project" in first_line
        assert "User Name" in first_line
        assert "Hours" in first_line
        assert "Date" in first_line
        assert "Description" in first_line

    async def test_csv_export_contains_entry_data(self, client, db_session):
        """CSV zawiera dane wpisow."""
        data = await _setup_full_scenario(db_session, "expcsv-data")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports/export",
            params={
                "format": "csv",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        content = resp.text
        assert "Dev work" in content
        assert "2026-01-05" in content
        # 120 min = 2.0 hours
        assert "2.0" in content

    async def test_csv_export_no_pagination(self, client, db_session):
        """CSV eksportuje wszystkie wpisy bez paginacji."""
        project = await _create_project(db_session, "rpt-expcsv-all")
        user = await _create_user(db_session, "rpt-expcsv-all@test.com")
        await _add_member(db_session, project, user, role="owner")
        sprint = await _create_sprint(db_session, project)
        ticket = await _create_ticket(db_session, project, sprint)

        # Create 25 entries (more than default per_page=20)
        for i in range(25):
            await _create_time_entry(
                db_session,
                ticket,
                user,
                sprint,
                duration_minutes=30,
                date_logged=date(2026, 1, 1 + (i % 28)),
                description=f"Entry {i}",
            )

        await _login(client, user.email)

        resp = await client.get(
            "/dashboard/reports/export",
            params={
                "format": "csv",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        # Header + 25 data lines
        assert len(lines) == 26

    async def test_csv_export_default_format(self, client, db_session):
        """GET bez format param -> CSV domyslnie."""
        data = await _setup_full_scenario(db_session, "expcsv-def")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports/export",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/csv; charset=utf-8"

    async def test_csv_export_with_ai_entry(self, client, db_session):
        """CSV oznacza wpisy AI."""
        data = await _setup_full_scenario(db_session, "expcsv-ai")
        await _create_time_entry(
            db_session,
            data["ticket"],
            data["user"],
            data["sprint"],
            duration_minutes=60,
            date_logged=date(2026, 1, 8),
            description="AI task",
            created_via_ai=True,
        )
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports/export",
            params={
                "format": "csv",
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        lines = resp.text.strip().split("\n")
        # Find the AI entry line
        ai_lines = [line for line in lines if "AI task" in line]
        assert len(ai_lines) == 1
        assert ",AI" in ai_lines[0] or ',"AI"' in ai_lines[0] or ai_lines[0].endswith("AI")

    async def test_csv_export_with_filters(self, client, db_session):
        """CSV respektuje filtry (project_id, user_id, sprint_id)."""
        data = await _setup_full_scenario(db_session, "expcsv-filt")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports/export",
            params={
                "format": "csv",
                "project_id": str(data["project"].id),
                "user_id": str(data["user"].id),
                "sprint_id": str(data["sprint"].id),
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        assert "Dev work" in resp.text


# ===========================================================================
# Search endpoints (Tom Select AJAX)
# ===========================================================================


@pytest.mark.integration
class TestSearchProjects:
    async def test_not_logged_in_returns_401(self, client, db_session):
        """GET /dashboard/reports/search/projects bez sesji -> 401."""
        resp = await client.get("/dashboard/reports/search/projects")
        assert resp.status_code == 401

    async def test_returns_user_projects(self, client, db_session):
        """GET zwraca projekty uzytkownika."""
        project = await _create_project(db_session, "srch-proj-mine", name="My Search Project")
        user = await _create_user(db_session, "srch-proj-mine@test.com")
        await _add_member(db_session, project, user)
        await _login(client, user.email)

        resp = await client.get("/dashboard/reports/search/projects")
        assert resp.status_code == 200
        json_data = resp.json()
        assert isinstance(json_data, list)
        project_ids = {item["value"] for item in json_data}
        assert str(project.id) in project_ids
        # Check format
        item = next(i for i in json_data if i["value"] == str(project.id))
        assert item["text"] == "My Search Project"

    async def test_search_with_query(self, client, db_session):
        """GET z q filtruje po nazwie projektu."""
        project1 = await _create_project(db_session, "srch-proj-q1", name="Alpha Project")
        project2 = await _create_project(db_session, "srch-proj-q2", name="Beta Project")
        user = await _create_user(db_session, "srch-proj-q@test.com")
        await _add_member(db_session, project1, user)
        await _add_member(db_session, project2, user)
        await _login(client, user.email)

        resp = await client.get("/dashboard/reports/search/projects", params={"q": "Alpha"})
        assert resp.status_code == 200
        json_data = resp.json()
        names = [item["text"] for item in json_data]
        assert "Alpha Project" in names
        assert "Beta Project" not in names

    async def test_search_case_insensitive(self, client, db_session):
        """Wyszukiwanie jest case-insensitive."""
        project = await _create_project(db_session, "srch-proj-ci", name="CaseTest Project")
        user = await _create_user(db_session, "srch-proj-ci@test.com")
        await _add_member(db_session, project, user)
        await _login(client, user.email)

        resp = await client.get("/dashboard/reports/search/projects", params={"q": "casetest"})
        assert resp.status_code == 200
        json_data = resp.json()
        assert len(json_data) >= 1
        assert any(item["text"] == "CaseTest Project" for item in json_data)

    async def test_empty_results(self, client, db_session):
        """GET z q niemajacym dopasowania -> pusta lista."""
        project = await _create_project(db_session, "srch-proj-empty", name="Existing Project")
        user = await _create_user(db_session, "srch-proj-empty@test.com")
        await _add_member(db_session, project, user)
        await _login(client, user.email)

        resp = await client.get(
            "/dashboard/reports/search/projects",
            params={"q": "NonExistentXYZ123"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_does_not_show_other_users_projects(self, client, db_session):
        """Normalny user nie widzi projektow, w ktorych nie jest czlonkiem."""
        project_mine = await _create_project(db_session, "srch-proj-onlyme", name="Only Mine")
        project_other = await _create_project(db_session, "srch-proj-notme", name="Not Mine")

        user = await _create_user(db_session, "srch-proj-onlyme@test.com")
        await _add_member(db_session, project_mine, user)

        other_user = await _create_user(db_session, "srch-proj-notme@test.com")
        await _add_member(db_session, project_other, other_user)

        await _login(client, user.email)

        resp = await client.get("/dashboard/reports/search/projects")
        assert resp.status_code == 200
        json_data = resp.json()
        project_ids = {item["value"] for item in json_data}
        assert str(project_mine.id) in project_ids
        assert str(project_other.id) not in project_ids

    async def test_superuser_sees_all_projects(self, client, db_session):
        """Superuser widzi wszystkie aktywne projekty."""
        project1 = await _create_project(db_session, "srch-proj-su1", name="SU Proj 1")
        project2 = await _create_project(db_session, "srch-proj-su2", name="SU Proj 2")
        # Nobody is member of these projects
        superuser = await _create_user(db_session, "srch-proj-su@test.com", is_superuser=True)
        await _login(client, superuser.email)

        resp = await client.get("/dashboard/reports/search/projects")
        assert resp.status_code == 200
        json_data = resp.json()
        project_ids = {item["value"] for item in json_data}
        assert str(project1.id) in project_ids
        assert str(project2.id) in project_ids

    async def test_no_projects_returns_empty(self, client, db_session):
        """User bez projektow -> pusta lista."""
        await _create_user(db_session, "srch-proj-noproj@test.com")
        await _login(client, "srch-proj-noproj@test.com")

        resp = await client.get("/dashboard/reports/search/projects")
        assert resp.status_code == 200
        assert resp.json() == []


@pytest.mark.integration
class TestSearchUsers:
    async def test_not_logged_in_returns_401(self, client, db_session):
        """GET /dashboard/reports/search/users bez sesji -> 401."""
        resp = await client.get("/dashboard/reports/search/users")
        assert resp.status_code == 401

    async def test_returns_project_members(self, client, db_session):
        """GET zwraca uzytkownikow z projektow usera."""
        project = await _create_project(db_session, "srch-user-mem")
        user1 = await _create_user(db_session, "srch-user-mem1@test.com")
        user2 = await _create_user(db_session, "srch-user-mem2@test.com")
        await _add_member(db_session, project, user1)
        await _add_member(db_session, project, user2)
        await _login(client, user1.email)

        resp = await client.get("/dashboard/reports/search/users")
        assert resp.status_code == 200
        json_data = resp.json()
        user_ids = {item["value"] for item in json_data}
        assert str(user1.id) in user_ids
        assert str(user2.id) in user_ids

    async def test_search_with_query(self, client, db_session):
        """GET z q filtruje po emailu uzytkownika."""
        project = await _create_project(db_session, "srch-user-q")
        user_alpha = await _create_user(db_session, "srch-alpha-user@test.com")
        user_beta = await _create_user(db_session, "srch-beta-user@test.com")
        await _add_member(db_session, project, user_alpha)
        await _add_member(db_session, project, user_beta)
        await _login(client, user_alpha.email)

        resp = await client.get(
            "/dashboard/reports/search/users",
            params={"q": "alpha"},
        )
        assert resp.status_code == 200
        json_data = resp.json()
        # Should find alpha, not beta
        user_ids = {item["value"] for item in json_data}
        assert str(user_alpha.id) in user_ids
        assert str(user_beta.id) not in user_ids

    async def test_scoped_by_project_id(self, client, db_session):
        """GET z project_id ogranicza do uzytkownikow z tego projektu."""
        project1 = await _create_project(db_session, "srch-user-scope1")
        project2 = await _create_project(db_session, "srch-user-scope2")

        user_main = await _create_user(db_session, "srch-user-scope-main@test.com")
        await _add_member(db_session, project1, user_main)
        await _add_member(db_session, project2, user_main)

        user_p1 = await _create_user(db_session, "srch-user-scope-p1@test.com")
        await _add_member(db_session, project1, user_p1)

        user_p2 = await _create_user(db_session, "srch-user-scope-p2@test.com")
        await _add_member(db_session, project2, user_p2)

        await _login(client, user_main.email)

        # Scope to project1 only
        resp = await client.get(
            "/dashboard/reports/search/users",
            params={"project_id": str(project1.id)},
        )
        assert resp.status_code == 200
        json_data = resp.json()
        user_ids = {item["value"] for item in json_data}
        assert str(user_p1.id) in user_ids
        assert str(user_p2.id) not in user_ids

    async def test_empty_results(self, client, db_session):
        """GET z q niemajacym dopasowania -> pusta lista."""
        project = await _create_project(db_session, "srch-user-nores")
        user = await _create_user(db_session, "srch-user-nores@test.com")
        await _add_member(db_session, project, user)
        await _login(client, user.email)

        resp = await client.get(
            "/dashboard/reports/search/users",
            params={"q": "nonexistentuser9999"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_no_projects_returns_empty(self, client, db_session):
        """User bez projektow -> pusta lista uzytkownikow."""
        await _create_user(db_session, "srch-user-noproj@test.com")
        await _login(client, "srch-user-noproj@test.com")

        resp = await client.get("/dashboard/reports/search/users")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_email_prefix_as_text(self, client, db_session):
        """Text zwracany dla uzytkownika to prefix emaila."""
        project = await _create_project(db_session, "srch-user-prefix")
        user = await _create_user(db_session, "srch-user-prefix-john@test.com")
        await _add_member(db_session, project, user)
        await _login(client, user.email)

        resp = await client.get("/dashboard/reports/search/users")
        assert resp.status_code == 200
        json_data = resp.json()
        item = next(i for i in json_data if i["value"] == str(user.id))
        assert item["text"] == "srch-user-prefix-john"


@pytest.mark.integration
class TestSearchSprints:
    async def test_not_logged_in_returns_401(self, client, db_session):
        """GET /dashboard/reports/search/sprints bez sesji -> 401."""
        resp = await client.get("/dashboard/reports/search/sprints")
        assert resp.status_code == 401

    async def test_returns_project_sprints(self, client, db_session):
        """GET zwraca sprinty z projektow usera."""
        project = await _create_project(db_session, "srch-sprint-ret", name="Sprint Project")
        user = await _create_user(db_session, "srch-sprint-ret@test.com")
        await _add_member(db_session, project, user)
        sprint = await _create_sprint(db_session, project, name="Search Sprint")
        await _login(client, user.email)

        resp = await client.get("/dashboard/reports/search/sprints")
        assert resp.status_code == 200
        json_data = resp.json()
        sprint_ids = {item["value"] for item in json_data}
        assert str(sprint.id) in sprint_ids
        # text should include sprint name and project name
        item = next(i for i in json_data if i["value"] == str(sprint.id))
        assert "Search Sprint" in item["text"]
        assert "Sprint Project" in item["text"]

    async def test_search_with_query(self, client, db_session):
        """GET z q filtruje po nazwie sprintu."""
        project = await _create_project(db_session, "srch-sprint-q")
        user = await _create_user(db_session, "srch-sprint-q@test.com")
        await _add_member(db_session, project, user)
        sprint_alpha = await _create_sprint(db_session, project, name="Alpha Sprint")
        sprint_beta = await _create_sprint(db_session, project, name="Beta Sprint")
        await _login(client, user.email)

        resp = await client.get(
            "/dashboard/reports/search/sprints",
            params={"q": "Alpha"},
        )
        assert resp.status_code == 200
        json_data = resp.json()
        sprint_ids = {item["value"] for item in json_data}
        assert str(sprint_alpha.id) in sprint_ids
        assert str(sprint_beta.id) not in sprint_ids

    async def test_scoped_by_project_id(self, client, db_session):
        """GET z project_id ogranicza do sprintow z tego projektu."""
        project1 = await _create_project(db_session, "srch-sprint-sc1")
        project2 = await _create_project(db_session, "srch-sprint-sc2")

        user = await _create_user(db_session, "srch-sprint-sc@test.com")
        await _add_member(db_session, project1, user)
        await _add_member(db_session, project2, user)

        sprint_p1 = await _create_sprint(db_session, project1, name="P1 Sprint")
        sprint_p2 = await _create_sprint(db_session, project2, name="P2 Sprint")

        await _login(client, user.email)

        resp = await client.get(
            "/dashboard/reports/search/sprints",
            params={"project_id": str(project1.id)},
        )
        assert resp.status_code == 200
        json_data = resp.json()
        sprint_ids = {item["value"] for item in json_data}
        assert str(sprint_p1.id) in sprint_ids
        assert str(sprint_p2.id) not in sprint_ids

    async def test_unauthorized_project_scope_falls_back(self, client, db_session):
        """GET z project_id do ktorego user nie ma dostepu -> fallback do allowed."""
        project_mine = await _create_project(db_session, "srch-sprint-fb1")
        project_other = await _create_project(db_session, "srch-sprint-fb2")

        user = await _create_user(db_session, "srch-sprint-fb@test.com")
        await _add_member(db_session, project_mine, user)
        sprint_mine = await _create_sprint(db_session, project_mine, name="Mine Sprint")

        other_user = await _create_user(db_session, "srch-sprint-fb-oth@test.com")
        await _add_member(db_session, project_other, other_user)
        await _create_sprint(db_session, project_other, name="Other Sprint")

        await _login(client, user.email)

        # Scope to unauthorized project -- should fall back to allowed projects
        resp = await client.get(
            "/dashboard/reports/search/sprints",
            params={"project_id": str(project_other.id)},
        )
        assert resp.status_code == 200
        json_data = resp.json()
        sprint_ids = {item["value"] for item in json_data}
        # Falls back to allowed (project_mine)
        assert str(sprint_mine.id) in sprint_ids

    async def test_empty_results(self, client, db_session):
        """GET z q niemajacym dopasowania -> pusta lista."""
        project = await _create_project(db_session, "srch-sprint-nores")
        user = await _create_user(db_session, "srch-sprint-nores@test.com")
        await _add_member(db_session, project, user)
        await _create_sprint(db_session, project, name="Real Sprint")
        await _login(client, user.email)

        resp = await client.get(
            "/dashboard/reports/search/sprints",
            params={"q": "nonexistentxyz"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_no_projects_returns_empty(self, client, db_session):
        """User bez projektow -> pusta lista sprintow."""
        await _create_user(db_session, "srch-sprint-noproj@test.com")
        await _login(client, "srch-sprint-noproj@test.com")

        resp = await client.get("/dashboard/reports/search/sprints")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_ordered_by_start_date_desc(self, client, db_session):
        """Sprinty sa posortowane po start_date malejaco."""
        project = await _create_project(db_session, "srch-sprint-ord")
        user = await _create_user(db_session, "srch-sprint-ord@test.com")
        await _add_member(db_session, project, user)

        sprint_old = Sprint(
            project_id=project.id,
            name="Old Sprint",
            start_date=date(2025, 1, 1),
            status="completed",
        )
        db_session.add(sprint_old)

        sprint_new = Sprint(
            project_id=project.id,
            name="New Sprint",
            start_date=date(2026, 6, 1),
            status="planning",
        )
        db_session.add(sprint_new)
        await db_session.flush()

        await _login(client, user.email)

        resp = await client.get("/dashboard/reports/search/sprints")
        assert resp.status_code == 200
        json_data = resp.json()
        names = [item["text"] for item in json_data]
        # New Sprint should appear before Old Sprint
        new_idx = next(i for i, name in enumerate(names) if "New Sprint" in name)
        old_idx = next(i for i, name in enumerate(names) if "Old Sprint" in name)
        assert new_idx < old_idx


# ===========================================================================
# Edge cases and combined filters
# ===========================================================================


@pytest.mark.integration
class TestReportsEdgeCases:
    async def test_inactive_project_excluded(self, client, db_session):
        """Nieaktywny projekt nie pojawia sie w raportach."""
        project = await _create_project(db_session, "rpt-inactive")
        project.is_active = False
        await db_session.flush()

        superuser = await _create_user(db_session, "rpt-inactive-su@test.com", is_superuser=True)
        await _login(client, superuser.email)

        resp = await client.get("/dashboard/reports/search/projects")
        assert resp.status_code == 200
        json_data = resp.json()
        project_ids = {item["value"] for item in json_data}
        assert str(project.id) not in project_ids

    async def test_combined_filters(self, client, db_session):
        """Raport z wieloma filtrami jednoczesnie."""
        data = await _setup_full_scenario(db_session, "combined")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={
                "project_id": str(data["project"].id),
                "user_id": str(data["user"].id),
                "sprint_id": str(data["sprint"].id),
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
                "sort": "hours",
                "page": "1",
            },
        )
        assert resp.status_code == 200
        assert "Dev work" in resp.text

    async def test_entry_without_sprint(self, client, db_session):
        """Wpis bez sprintu pojawia sie w raporcie."""
        project = await _create_project(db_session, "rpt-nosprint")
        user = await _create_user(db_session, "rpt-nosprint@test.com")
        await _add_member(db_session, project, user, role="owner")
        ticket = await _create_ticket(db_session, project, sprint=None, title="No Sprint Ticket")
        await _create_time_entry(
            db_session,
            ticket,
            user,
            sprint=None,
            description="No sprint work",
            date_logged=date(2026, 1, 10),
        )
        await _login(client, user.email)

        resp = await client.get(
            "/dashboard/reports",
            params={"date_from": "2026-01-01", "date_to": "2026-01-31"},
        )
        assert resp.status_code == 200
        assert "No sprint work" in resp.text

    async def test_chart_data_with_filters(self, client, db_session):
        """Chart data respektuje filtry z query params."""
        data = await _setup_full_scenario(db_session, "chartfilt")
        await _login(client, data["user"].email)

        # Filter to date range that includes the entry
        resp = await client.get(
            "/dashboard/reports/data/user-hours",
            params={
                "project_id": str(data["project"].id),
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        json_data = resp.json()
        assert len(json_data) >= 1

        # Filter to date range that excludes the entry
        resp = await client.get(
            "/dashboard/reports/data/user-hours",
            params={"date_from": "2026-06-01", "date_to": "2026-06-30"},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_invalid_date_params_ignored(self, client, db_session):
        """Nieprawidlowe daty w query params sa ignorowane."""
        data = await _setup_full_scenario(db_session, "invdate")
        await _login(client, data["user"].email)

        resp = await client.get(
            "/dashboard/reports",
            params={"date_from": "not-a-date", "date_to": "also-not-a-date"},
        )
        assert resp.status_code == 200

    async def test_multiple_project_ids_filter(self, client, db_session):
        """Raport z wieloma project_id w filtrze."""
        project1 = await _create_project(db_session, "rpt-multi-p1")
        project2 = await _create_project(db_session, "rpt-multi-p2")

        user = await _create_user(db_session, "rpt-multi-p@test.com")
        await _add_member(db_session, project1, user)
        await _add_member(db_session, project2, user)

        sprint1 = await _create_sprint(db_session, project1, name="MS1")
        ticket1 = await _create_ticket(db_session, project1, sprint1)
        await _create_time_entry(
            db_session,
            ticket1,
            user,
            sprint1,
            description="Multi P1 Work",
            date_logged=date(2026, 1, 5),
        )

        sprint2 = await _create_sprint(db_session, project2, name="MS2")
        ticket2 = await _create_ticket(db_session, project2, sprint2)
        await _create_time_entry(
            db_session,
            ticket2,
            user,
            sprint2,
            description="Multi P2 Work",
            date_logged=date(2026, 1, 6),
        )

        await _login(client, user.email)

        # Pass both project_ids
        resp = await client.get(
            "/dashboard/reports",
            params={
                "project_id": [str(project1.id), str(project2.id)],
                "date_from": "2026-01-01",
                "date_to": "2026-01-31",
            },
        )
        assert resp.status_code == 200
        assert "Multi P1 Work" in resp.text
        assert "Multi P2 Work" in resp.text
