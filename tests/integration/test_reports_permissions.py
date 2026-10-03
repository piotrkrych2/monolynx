"""Testy integracyjne -- raporty pracy widoczne tylko dla projektow z reports:read (MON-234)."""

import csv
import io
import uuid
from unittest.mock import MagicMock, patch

import pytest

from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from tests.integration.test_reports_dashboard import (
    _add_member,
    _create_project,
    _create_sprint,
    _create_ticket,
    _create_time_entry,
    _create_user,
    _login,
)

PARAMS = {"date_from": "2026-01-01", "date_to": "2026-01-31"}
CSV_HEADER = ["Ticket", "Ticket Title", "Project", "User Name", "Hours", "Date", "Description", "AI"]


async def _seed_project(db_session, tag, *, author_member_role="owner"):
    """Projekt z autorem, sprintem, ticketem i jednym wpisem czasu o rozpoznawalnych nazwach."""
    project = await _create_project(db_session, f"p{tag}", name=f"Projekt-{tag}")
    author = await _create_user(db_session, f"autor-{tag}@test.com")
    await _add_member(db_session, project, author, role=author_member_role)
    sprint = await _create_sprint(db_session, project, name=f"Sprint-{tag}")
    ticket = await _create_ticket(db_session, project, sprint, title=f"Ticket-{tag}")
    entry = await _create_time_entry(db_session, ticket, author, sprint, description=f"Opis-wpisu-{tag}")
    return {"project": project, "author": author, "sprint": sprint, "ticket": ticket, "entry": entry, "tag": tag}


async def _add_member_with_role(db_session, project, user, permissions):
    role = Role(name=f"Rola-{uuid.uuid4().hex[:6]}", project_id=project.id, permissions=permissions, is_system=False)
    db_session.add(role)
    await db_session.flush()
    member = ProjectMember(project_id=project.id, user_id=user.id, role="member", role_id=role.id)
    db_session.add(member)
    await db_session.flush()
    return member


def _assert_no_leak(text, data):
    """Zaden slad projektu: nazwa, sprint, ticket, opis wpisu, autor."""
    tag = data["tag"]
    for needle in (f"Projekt-{tag}", f"Sprint-{tag}", f"Ticket-{tag}", f"Opis-wpisu-{tag}", f"autor-{tag}"):
        assert needle not in text, needle


def _assert_visible(text, data):
    tag = data["tag"]
    assert f"Opis-wpisu-{tag}" in text
    assert f"Projekt-{tag}" in text


def _csv_rows(resp):
    return list(csv.reader(io.StringIO(resp.text)))


# ===========================================================================
# C. Uzytkownik bez dozwolonych projektow
# ===========================================================================


@pytest.mark.integration
class TestUserWithoutAllowedProjects:
    async def _setup(self, client, db_session, tag):
        other = await _seed_project(db_session, f"c{tag}")
        outsider = await _create_user(db_session, f"outsider-{tag}@test.com")
        await _login(client, outsider.email)
        return other

    async def test_reports_page_shows_no_foreign_data(self, client, db_session):
        other = await self._setup(client, db_session, "page")

        resp = await client.get("/dashboard/reports", params=PARAMS)

        assert resp.status_code == 200
        _assert_no_leak(resp.text, other)

    async def test_csv_export_has_header_only(self, client, db_session):
        other = await self._setup(client, db_session, "csv")

        resp = await client.get("/dashboard/reports/export", params={**PARAMS, "format": "csv"})

        assert resp.status_code == 200
        assert _csv_rows(resp) == [CSV_HEADER]
        _assert_no_leak(resp.text, other)

    async def test_pdf_export_does_not_render_foreign_data(self, client, db_session):
        other = await self._setup(client, db_session, "pdf")

        pdf_html = MagicMock()
        pdf_html.return_value.write_pdf.return_value = b"%PDF-fake"
        with patch("weasyprint.HTML", pdf_html):
            resp = await client.get("/dashboard/reports/export", params={**PARAMS, "format": "pdf"})

        assert resp.status_code == 200
        assert resp.content == b"%PDF-fake"
        rendered = pdf_html.call_args.kwargs["string"]
        _assert_no_leak(rendered, other)

    @pytest.mark.parametrize("endpoint", ["user-hours", "project-hours", "sprint-hours"])
    async def test_chart_data_is_empty(self, client, db_session, endpoint):
        await self._setup(client, db_session, f"chart-{endpoint}")

        resp = await client.get(f"/dashboard/reports/data/{endpoint}", params=PARAMS)

        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.parametrize("endpoint", ["projects", "users", "sprints"])
    async def test_search_is_empty(self, client, db_session, endpoint):
        await self._setup(client, db_session, f"search-{endpoint}")

        resp = await client.get(f"/dashboard/reports/search/{endpoint}")

        assert resp.status_code == 200
        assert resp.json() == []


# ===========================================================================
# D. Czlonek projektu A nie widzi projektu B
# ===========================================================================


@pytest.mark.integration
class TestMemberSeesOnlyOwnProject:
    async def _setup(self, client, db_session, tag):
        a = await _seed_project(db_session, f"a{tag}")
        b = await _seed_project(db_session, f"b{tag}")
        viewer = await _create_user(db_session, f"viewer-{tag}@test.com")
        await _add_member(db_session, a["project"], viewer, role="member")
        await _login(client, viewer.email)
        return a, b

    async def test_page_shows_own_project_and_hides_other(self, client, db_session):
        a, b = await self._setup(client, db_session, "page")

        resp = await client.get("/dashboard/reports", params=PARAMS)

        assert resp.status_code == 200
        _assert_visible(resp.text, a)
        _assert_no_leak(resp.text, b)

    async def test_own_sprint_filter_shows_own_sprint_and_entry(self, client, db_session):
        a, b = await self._setup(client, db_session, "ownspr")

        resp = await client.get("/dashboard/reports", params={**PARAMS, "sprint_id": str(a["sprint"].id)})

        assert resp.status_code == 200
        assert f"Sprint-{a['tag']}" in resp.text
        _assert_visible(resp.text, a)
        _assert_no_leak(resp.text, b)

    async def test_csv_export_contains_only_own_project(self, client, db_session):
        a, b = await self._setup(client, db_session, "csv")

        resp = await client.get("/dashboard/reports/export", params={**PARAMS, "format": "csv"})

        rows = _csv_rows(resp)
        assert len(rows) == 2
        assert rows[1][2] == f"Projekt-{a['tag']}"
        assert rows[1][6] == f"Opis-wpisu-{a['tag']}"
        _assert_no_leak(resp.text, b)

    async def test_pdf_export_does_not_render_other_project(self, client, db_session):
        a, b = await self._setup(client, db_session, "pdf")

        pdf_html = MagicMock()
        pdf_html.return_value.write_pdf.return_value = b"%PDF-fake"
        with patch("weasyprint.HTML", pdf_html):
            resp = await client.get("/dashboard/reports/export", params={**PARAMS, "format": "pdf"})

        assert resp.status_code == 200
        rendered = pdf_html.call_args.kwargs["string"]
        _assert_visible(rendered, a)
        _assert_no_leak(rendered, b)

    async def test_user_id_from_other_project_gives_empty_report(self, client, db_session):
        a, b = await self._setup(client, db_session, "uid")

        resp = await client.get("/dashboard/reports", params={**PARAMS, "user_id": str(b["author"].id)})

        assert resp.status_code == 200
        _assert_no_leak(resp.text, b)
        assert f"Opis-wpisu-{a['tag']}" not in resp.text

    async def test_sprint_id_from_other_project_gives_empty_report(self, client, db_session):
        a, b = await self._setup(client, db_session, "sid")

        resp = await client.get("/dashboard/reports", params={**PARAMS, "sprint_id": str(b["sprint"].id)})

        assert resp.status_code == 200
        _assert_no_leak(resp.text, b)
        assert f"Opis-wpisu-{a['tag']}" not in resp.text

    async def test_project_id_from_other_project_falls_back_to_own_projects(self, client, db_session):
        a, b = await self._setup(client, db_session, "pid")

        resp = await client.get("/dashboard/reports", params={**PARAMS, "project_id": str(b["project"].id)})

        assert resp.status_code == 200
        _assert_visible(resp.text, a)
        _assert_no_leak(resp.text, b)

    async def test_chart_user_hours_with_other_users_id_is_empty(self, client, db_session):
        _a, b = await self._setup(client, db_session, "cuid")

        resp = await client.get("/dashboard/reports/data/user-hours", params={**PARAMS, "user_id": str(b["author"].id)})

        assert resp.json() == []

    async def test_chart_sprint_hours_with_other_sprint_is_empty(self, client, db_session):
        _a, b = await self._setup(client, db_session, "csid")

        resp = await client.get("/dashboard/reports/data/sprint-hours", params={**PARAMS, "sprint_id": str(b["sprint"].id)})

        assert resp.json() == []

    async def test_user_id_of_former_member_is_dropped_by_handler(self, client, db_session):
        # Wpis X siedzi w projekcie A, ale X nie jest czlonkiem A: filtr projektu go nie wyklucza,
        # wiec tylko walidacja user_ids w handlerze moze zwrocic pusty wynik.
        a, _b = await self._setup(client, db_session, "former")
        former = await _create_user(db_session, "former-member@test.com")
        await _create_time_entry(db_session, a["ticket"], former, a["sprint"], description="Opis-bylego-czlonka")

        control = await client.get("/dashboard/reports", params=PARAMS)
        control_chart = await client.get("/dashboard/reports/data/user-hours", params=PARAMS)
        page = await client.get("/dashboard/reports", params={**PARAMS, "user_id": str(former.id)})
        chart = await client.get("/dashboard/reports/data/user-hours", params={**PARAMS, "user_id": str(former.id)})

        assert "Opis-bylego-czlonka" in control.text
        assert str(former.id) in [row["user_id"] for row in control_chart.json()]
        assert "Opis-bylego-czlonka" not in page.text
        assert f"Opis-wpisu-{a['tag']}" not in page.text
        assert chart.json() == []

    async def test_sprint_id_of_other_project_is_dropped_by_handler(self, client, db_session):
        # Wpis w projekcie A z sprintem B: filtr projektu go nie wyklucza,
        # wiec tylko walidacja sprint_ids w handlerze moze zwrocic pusty wynik.
        a, b = await self._setup(client, db_session, "xsprint")
        await _create_time_entry(db_session, a["ticket"], a["author"], b["sprint"], description="Opis-sprintu-obcego")

        control = await client.get("/dashboard/reports", params=PARAMS)
        control_chart = await client.get("/dashboard/reports/data/sprint-hours", params=PARAMS)
        page = await client.get("/dashboard/reports", params={**PARAMS, "sprint_id": str(b["sprint"].id)})
        chart = await client.get("/dashboard/reports/data/sprint-hours", params={**PARAMS, "sprint_id": str(b["sprint"].id)})

        assert "Opis-sprintu-obcego" in control.text
        assert str(b["sprint"].id) in [row["sprint_id"] for row in control_chart.json()]
        assert "Opis-sprintu-obcego" not in page.text
        assert chart.json() == []

    async def test_chart_user_hours_returns_author_name_and_hours(self, client, db_session):
        a, _b = await self._setup(client, db_session, "uname")

        resp = await client.get("/dashboard/reports/data/user-hours", params=PARAMS)

        assert resp.json() == [
            {
                "user_id": str(a["author"].id),
                "user_name": f"autor-{a['tag']}",
                "total_hours": 2.0,
                "percentage": 100.0,
            }
        ]

    async def test_chart_project_hours_lists_only_own_project(self, client, db_session):
        a, b = await self._setup(client, db_session, "cpid")

        resp = await client.get("/dashboard/reports/data/project-hours", params={**PARAMS, "project_id": str(b["project"].id)})

        names = [row["project_name"] for row in resp.json()]
        assert names == [f"Projekt-{a['tag']}"]

    async def test_search_projects_lists_only_own_project(self, client, db_session):
        a, b = await self._setup(client, db_session, "sprj")

        resp = await client.get("/dashboard/reports/search/projects")

        assert [row["text"] for row in resp.json()] == [f"Projekt-{a['tag']}"]
        assert f"Projekt-{b['tag']}" not in resp.text

    async def test_search_users_with_other_project_id_lists_only_own_project_users(self, client, db_session):
        a, b = await self._setup(client, db_session, "susr")

        resp = await client.get("/dashboard/reports/search/users", params={"project_id": str(b["project"].id)})

        texts = [row["text"] for row in resp.json()]
        assert f"autor-{a['tag']}" in texts
        assert f"autor-{b['tag']}" not in texts

    async def test_search_sprints_with_other_project_id_lists_only_own_sprints(self, client, db_session):
        a, b = await self._setup(client, db_session, "sspr")

        resp = await client.get("/dashboard/reports/search/sprints", params={"project_id": str(b["project"].id)})

        texts = [row["text"] for row in resp.json()]
        assert texts == [f"Sprint-{a['tag']} (Projekt-{a['tag']})"]


# ===========================================================================
# E. Rola bez uprawnienia reports:read
# ===========================================================================


@pytest.mark.integration
class TestRoleWithoutReportsPermission:
    async def _setup(self, client, db_session, tag, permissions):
        data = await _seed_project(db_session, f"e{tag}")
        viewer = await _create_user(db_session, f"roleviewer-{tag}@test.com")
        await _add_member_with_role(db_session, data["project"], viewer, permissions)
        await _login(client, viewer.email)
        return data

    async def test_page_hides_project_without_reports_permission(self, client, db_session):
        data = await self._setup(client, db_session, "page", {"scrum": ["read", "write"], "wiki": ["read"]})

        resp = await client.get("/dashboard/reports", params=PARAMS)

        assert resp.status_code == 200
        _assert_no_leak(resp.text, data)

    async def test_csv_export_is_header_only(self, client, db_session):
        data = await self._setup(client, db_session, "csv", {"scrum": ["read"]})

        resp = await client.get("/dashboard/reports/export", params={**PARAMS, "format": "csv"})

        assert _csv_rows(resp) == [CSV_HEADER]
        _assert_no_leak(resp.text, data)

    @pytest.mark.parametrize("endpoint", ["user-hours", "project-hours", "sprint-hours"])
    async def test_chart_data_is_empty(self, client, db_session, endpoint):
        await self._setup(client, db_session, f"chart-{endpoint}", {"scrum": ["read"]})

        resp = await client.get(f"/dashboard/reports/data/{endpoint}", params=PARAMS)

        assert resp.json() == []

    async def test_search_projects_omits_project(self, client, db_session):
        data = await self._setup(client, db_session, "sprj", {"scrum": ["read"]})

        resp = await client.get("/dashboard/reports/search/projects")

        assert resp.json() == []
        assert f"Projekt-{data['tag']}" not in resp.text

    async def test_role_with_reports_read_sees_project_data(self, client, db_session):
        data = await self._setup(client, db_session, "pos", {"reports": ["read"]})

        page = await client.get("/dashboard/reports", params=PARAMS)
        search = await client.get("/dashboard/reports/search/projects")

        _assert_visible(page.text, data)
        assert [row["text"] for row in search.json()] == [f"Projekt-{data['tag']}"]

    async def test_mixed_memberships_report_only_permitted_project(self, client, db_session):
        denied = await _seed_project(db_session, "emixd")
        allowed = await _seed_project(db_session, "emixa")
        viewer = await _create_user(db_session, "roleviewer-mixed@test.com")
        await _add_member_with_role(db_session, denied["project"], viewer, {"scrum": ["read"]})
        await _add_member(db_session, allowed["project"], viewer, role="member")
        await _login(client, viewer.email)

        page = await client.get("/dashboard/reports", params=PARAMS)
        search = await client.get("/dashboard/reports/search/projects")
        csv_resp = await client.get("/dashboard/reports/export", params={**PARAMS, "format": "csv"})

        _assert_visible(page.text, allowed)
        _assert_no_leak(page.text, denied)
        assert [row["text"] for row in search.json()] == [f"Projekt-{allowed['tag']}"]
        rows = _csv_rows(csv_resp)
        assert [r[6] for r in rows[1:]] == [f"Opis-wpisu-{allowed['tag']}"]
        _assert_no_leak(csv_resp.text, denied)

    async def test_inactive_project_membership_is_not_reported(self, client, db_session):
        data = await _seed_project(db_session, "einact")
        viewer = await _create_user(db_session, "roleviewer-inactive@test.com")
        await _add_member(db_session, data["project"], viewer, role="member")
        data["project"].is_active = False
        await db_session.flush()
        await _login(client, viewer.email)

        resp = await client.get("/dashboard/reports", params=PARAMS)

        _assert_no_leak(resp.text, data)
