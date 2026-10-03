"""Testy integracyjne -- tozsamosc uzytkownika dashboardu pochodzi z bazy, nie z cookie sesji.

Kontrakt:
- konto z `is_active == False` traci dostep od razu mimo waznego cookie, a sesja zostaje wyczyszczona;
- status superusera jest czytany z `User.is_superuser` w bazie, nie z `session["is_superuser"]`.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from monolynx.models.pipeline import Pipeline, PipelineJob, PipelineStep
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_project import SettlementProject
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.models.work_plan import WorkPlanEntry
from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db_session, email: str, *, is_superuser: bool) -> User:
    user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=is_superuser)
    db_session.add(user)
    await db_session.flush()
    return user


async def _login(client, email: str) -> None:
    response = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


async def _make_project(db_session, tag: str, owner: User | None = None) -> Project:
    project = Project(
        name=f"Sesja Projekt {tag}",
        slug=f"sesja-{tag}",
        code=("S" + secrets.token_hex(3)).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    if owner is not None:
        db_session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="owner"))
        await db_session.flush()
    return project


async def _add_ticket(db_session, project: Project, title: str) -> Ticket:
    ticket = Ticket(project_id=project.id, number=1, title=title, status="backlog", priority="medium")
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _update_user(db_session, email: str, **fields) -> None:
    """Zmienia pola uzytkownika w bazie po zalogowaniu (cookie sesji zostaje bez zmian)."""
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    for name, value in fields.items():
        setattr(user, name, value)
    await db_session.flush()


async def _logged_in_user(client, db_session, email: str, *, is_superuser: bool) -> User:
    user = await _make_user(db_session, email, is_superuser=is_superuser)
    await _login(client, email)
    return user


def _assert_login_redirect(response) -> None:
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"


def _option(project: Project) -> str:
    return f'value="{project.id}"'


# ---------------------------------------------------------------------------
# A. Uzytkownik dezaktywowany po zalogowaniu
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeactivatedUserHtmlRoutes:
    async def test_reports_redirects_to_login(self, client, db_session):
        email = "sesja-a-reports@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/reports", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_plan_view_redirects_to_login(self, client, db_session):
        email = "sesja-a-plan@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/plan/", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_pipelines_list_redirects_to_login_for_member(self, client, db_session):
        email = "sesja-a-pipelines@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "a-pip", owner=user)
        await _update_user(db_session, email, is_active=False)

        response = await client.get(f"/dashboard/{project.slug}/pipelines/", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_tokens_list_redirects_to_login(self, client, db_session):
        email = "sesja-a-tokens@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/profile/tokens", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_mcp_guide_redirects_to_login(self, client, db_session):
        email = "sesja-a-mcpguide@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/profile/mcp-guide", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_cli_guide_redirects_to_login(self, client, db_session):
        email = "sesja-a-cliguide@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/profile/cli-guide", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_project_list_redirects_to_login(self, client, db_session):
        email = "sesja-a-projlist@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_create_project_form_redirects_to_login(self, client, db_session):
        email = "sesja-a-cpform@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/create-project", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_settlements_global_redirects_to_login(self, client, db_session):
        email = "sesja-a-rozlglob@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/rozliczenia", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"

    async def test_settlement_create_form_redirects_to_login_for_member(self, client, db_session):
        email = "sesja-a-rozlform@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "a-rozl", owner=user)
        await _update_user(db_session, email, is_active=False)

        response = await client.get(f"/dashboard/{project.slug}/rozliczenia/create", follow_redirects=False)

        _assert_login_redirect(response)


@pytest.mark.integration
class TestDeactivatedUserWrites:
    async def test_create_project_post_redirects_and_creates_nothing(self, client, db_session):
        email = "sesja-a-cpost@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Zablokowany Projekt Sesja", "slug": "zablokowany-sesja", "code": "ZBS"},
            follow_redirects=False,
        )

        _assert_login_redirect(response)
        created = (await db_session.execute(select(Project).where(Project.slug == "zablokowany-sesja"))).scalar_one_or_none()
        assert created is None

    async def test_token_create_redirects_and_creates_nothing(self, client, db_session):
        email = "sesja-a-tokcreate@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "zablokowany-token-sesja"},
            follow_redirects=False,
        )

        _assert_login_redirect(response)
        tokens = (await db_session.execute(select(UserApiToken).where(UserApiToken.user_id == user.id))).scalars().all()
        assert tokens == []

    async def test_token_revoke_redirects_to_login_and_keeps_token(self, client, db_session):
        email = "sesja-a-tokrevoke@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        token = UserApiToken(user_id=user.id, token_hash=secrets.token_hex(32), token_prefix="osk_abcd", name="revoke-sesja")
        db_session.add(token)
        await db_session.flush()
        await _update_user(db_session, email, is_active=False)

        response = await client.post(f"/dashboard/profile/tokens/{token.id}/revoke", follow_redirects=False)

        _assert_login_redirect(response)
        reloaded = (await db_session.execute(select(UserApiToken).where(UserApiToken.id == token.id))).scalar_one()
        assert reloaded.is_active is True


@pytest.mark.integration
class TestDeactivatedUserJsonEndpoints:
    async def test_reports_search_projects_returns_401(self, client, db_session):
        email = "sesja-a-rsp@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/reports/search/projects")

        assert response.status_code == 401

    async def test_reports_search_users_returns_401(self, client, db_session):
        email = "sesja-a-rsu@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/reports/search/users")

        assert response.status_code == 401

    async def test_reports_search_sprints_returns_401(self, client, db_session):
        email = "sesja-a-rss@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/reports/search/sprints")

        assert response.status_code == 401

    async def test_plan_api_data_returns_401(self, client, db_session):
        email = "sesja-a-pdata@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/plan/api/data?start=2026-10-01&end=2026-10-31")

        assert response.status_code == 401

    async def test_plan_api_tickets_search_returns_401(self, client, db_session):
        email = "sesja-a-psearch@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        response = await client.get("/dashboard/plan/api/tickets/search")

        assert response.status_code == 401

    async def test_plan_entry_create_returns_401(self, client, db_session):
        email = "sesja-a-pentry@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "a-pentry", owner=user)
        ticket = await _add_ticket(db_session, project, "Ticket wpisu sesja")
        await _update_user(db_session, email, is_active=False)

        response = await client.post(
            "/dashboard/plan/entries",
            json={"ticket_id": str(ticket.id), "scheduled_date": date(2026, 10, 5).isoformat()},
        )

        assert response.status_code == 401

    async def test_pipelines_api_list_returns_401_for_member(self, client, db_session):
        email = "sesja-a-papi@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "a-papi", owner=user)
        await _update_user(db_session, email, is_active=False)

        response = await client.get(f"/dashboard/{project.slug}/pipelines/api/list")

        assert response.status_code == 401

    async def test_pipelines_partial_list_returns_401_for_member(self, client, db_session):
        email = "sesja-a-ppart@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "a-ppart", owner=user)
        await _update_user(db_session, email, is_active=False)

        response = await client.get(f"/dashboard/{project.slug}/pipelines/partial/list")

        assert response.status_code == 401


@pytest.mark.integration
class TestDeactivationClearsSession:
    async def test_reactivated_user_still_redirected_because_session_was_cleared(self, client, db_session):
        email = "sesja-a-clear@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        first = await client.get("/dashboard/reports", follow_redirects=False)
        await _update_user(db_session, email, is_active=True)
        second = await client.get("/dashboard/reports", follow_redirects=False)

        _assert_login_redirect(first)
        _assert_login_redirect(second)

    async def test_reactivated_user_gets_401_on_json_endpoint_because_session_was_cleared(self, client, db_session):
        email = "sesja-a-clearjson@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)
        await _update_user(db_session, email, is_active=False)

        first = await client.get("/dashboard/reports/search/projects")
        await _update_user(db_session, email, is_active=True)
        second = await client.get("/dashboard/reports/search/projects")

        assert first.status_code == 401
        assert second.status_code == 401


# ---------------------------------------------------------------------------
# C. Kontrola pozytywna: aktywny uzytkownik zachowuje dostep
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestActiveUserKeepsAccess:
    @pytest.mark.parametrize(
        "path",
        [
            "/dashboard/",
            "/dashboard/reports",
            "/dashboard/plan/",
            "/dashboard/profile/tokens",
            "/dashboard/profile/mcp-guide",
            "/dashboard/profile/cli-guide",
            "/dashboard/create-project",
            "/dashboard/rozliczenia",
        ],
    )
    async def test_active_regular_user_gets_200(self, client, db_session, path):
        email = f"sesja-c-active-{uuid.uuid4().hex[:10]}@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)

        response = await client.get(path, follow_redirects=False)

        assert response.status_code == 200

    async def test_active_member_gets_200_on_pipelines(self, client, db_session):
        email = "sesja-c-pipelines@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "c-pip", owner=user)

        response = await client.get(f"/dashboard/{project.slug}/pipelines/", follow_redirects=False)

        assert response.status_code == 200

    async def test_active_user_creates_project(self, client, db_session):
        email = "sesja-c-createproj@test.com"
        await _logged_in_user(client, db_session, email, is_superuser=False)

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Aktywny Projekt Sesja", "slug": "aktywny-sesja", "code": "AKS"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard/"
        created = (await db_session.execute(select(Project).where(Project.slug == "aktywny-sesja"))).scalar_one_or_none()
        assert created is not None

    async def test_active_user_creates_token(self, client, db_session):
        email = "sesja-c-createtok@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)

        response = await client.post("/dashboard/profile/tokens/create", data={"name": "aktywny-token-sesja"})

        assert response.status_code == 200
        tokens = (await db_session.execute(select(UserApiToken).where(UserApiToken.user_id == user.id))).scalars().all()
        assert [t.name for t in tokens] == ["aktywny-token-sesja"]


# ---------------------------------------------------------------------------
# B / C / D. Zrodlo flagi superusera: baza, nie sesja
# ---------------------------------------------------------------------------


async def _two_projects(db_session, tag: str, member: User) -> tuple[Project, Project]:
    """Projekt z czlonkostwem usera i projekt bez czlonkostwa."""
    mine = await _make_project(db_session, f"{tag}-mine", owner=member)
    other = await _make_project(db_session, f"{tag}-other")
    return mine, other


@pytest.mark.integration
class TestDemotedSuperuserSeesOnlyMemberProjects:
    async def test_reports_search_projects_lists_only_member_project(self, client, db_session):
        email = "sesja-b-rsp@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "b-rsp", user)
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get("/dashboard/reports/search/projects")

        assert response.status_code == 200
        values = [p["value"] for p in response.json()]
        assert values == [str(mine.id)]
        assert str(other.id) not in values

    async def test_reports_page_hides_project_without_membership(self, client, db_session):
        email = "sesja-b-rpage@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "b-rpage", user)
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get(f"/dashboard/reports?project_id={mine.id}&project_id={other.id}")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) not in response.text

    async def test_plan_ticket_autocomplete_returns_only_member_project_tickets(self, client, db_session):
        email = "sesja-b-pticket@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "b-pticket", user)
        mine_ticket = await _add_ticket(db_session, mine, "Zadanie widoczne Zefir")
        await _add_ticket(db_session, other, "Zadanie ukryte Kondor")
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get("/dashboard/plan/api/tickets/search")

        assert response.status_code == 200
        assert [t["id"] for t in response.json()] == [str(mine_ticket.id)]

    async def test_plan_page_dropdown_hides_project_without_membership(self, client, db_session):
        email = "sesja-b-ppage@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "b-ppage", user)
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get("/dashboard/plan/")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) not in response.text

    async def test_settlements_global_lists_only_member_project(self, client, db_session):
        email = "sesja-b-rozlglob@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "b-rozlglob", user)
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get("/dashboard/rozliczenia")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) not in response.text

    async def test_settlement_create_form_offers_only_member_project(self, client, db_session):
        email = "sesja-b-rozlform@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "b-rozlform", user)
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get(f"/dashboard/{mine.slug}/rozliczenia/create")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) not in response.text

    async def test_settlement_edit_form_offers_only_member_project(self, client, db_session):
        email = "sesja-b-rozledit@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "b-rozledit", user)
        settlement = Settlement(
            number=int(uuid.uuid4().int % 1_000_000_000) + 1_000_000,
            name="Rozliczenie edycja sesja",
            period_from=date(2026, 9, 1),
            period_to=date(2026, 9, 30),
            status="draft",
            created_by_id=user.id,
        )
        db_session.add(settlement)
        await db_session.flush()
        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=mine.id))
        await db_session.flush()
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get(f"/dashboard/{mine.slug}/rozliczenia/{settlement.id}/edit")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) not in response.text


@pytest.mark.integration
class TestActiveSuperuserSeesAllProjects:
    async def test_reports_search_projects_lists_both_projects(self, client, db_session):
        email = "sesja-c-rsp@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "c-rsp", user)

        response = await client.get("/dashboard/reports/search/projects")

        assert response.status_code == 200
        assert {p["value"] for p in response.json()} >= {str(mine.id), str(other.id)}

    async def test_plan_ticket_autocomplete_returns_both_projects_tickets(self, client, db_session):
        email = "sesja-c-pticket@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "c-pticket", user)
        mine_ticket = await _add_ticket(db_session, mine, "Zadanie moje Zefir")
        other_ticket = await _add_ticket(db_session, other, "Zadanie cudze Kondor")

        response = await client.get("/dashboard/plan/api/tickets/search")

        assert response.status_code == 200
        assert {t["id"] for t in response.json()} >= {str(mine_ticket.id), str(other_ticket.id)}

    async def test_settlements_global_lists_both_projects(self, client, db_session):
        email = "sesja-c-rozlglob@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "c-rozlglob", user)

        response = await client.get("/dashboard/rozliczenia")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) in response.text

    async def test_settlement_create_form_offers_both_projects(self, client, db_session):
        email = "sesja-c-rozlform@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "c-rozlform", user)

        response = await client.get(f"/dashboard/{mine.slug}/rozliczenia/create")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) in response.text


@pytest.mark.integration
class TestPromotedUserSeesAllProjects:
    """Superuser nadany w bazie po zalogowaniu dziala od razu, mimo cookie z `is_superuser=False`."""

    async def test_reports_search_projects_lists_both_projects(self, client, db_session):
        email = "sesja-d-rsp@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        mine, other = await _two_projects(db_session, "d-rsp", user)
        await _update_user(db_session, email, is_superuser=True)

        response = await client.get("/dashboard/reports/search/projects")

        assert response.status_code == 200
        assert {p["value"] for p in response.json()} >= {str(mine.id), str(other.id)}

    async def test_reports_page_shows_project_without_membership(self, client, db_session):
        email = "sesja-d-rpage@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        mine, other = await _two_projects(db_session, "d-rpage", user)
        await _update_user(db_session, email, is_superuser=True)

        response = await client.get(f"/dashboard/reports?project_id={mine.id}&project_id={other.id}")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) in response.text

    async def test_plan_ticket_autocomplete_returns_both_projects_tickets(self, client, db_session):
        email = "sesja-d-pticket@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        mine, other = await _two_projects(db_session, "d-pticket", user)
        mine_ticket = await _add_ticket(db_session, mine, "Zadanie moje Zefir")
        other_ticket = await _add_ticket(db_session, other, "Zadanie cudze Kondor")
        await _update_user(db_session, email, is_superuser=True)

        response = await client.get("/dashboard/plan/api/tickets/search")

        assert response.status_code == 200
        assert {t["id"] for t in response.json()} >= {str(mine_ticket.id), str(other_ticket.id)}

    async def test_settlements_global_lists_both_projects(self, client, db_session):
        email = "sesja-d-rozlglob@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        mine, other = await _two_projects(db_session, "d-rozlglob", user)
        await _update_user(db_session, email, is_superuser=True)

        response = await client.get("/dashboard/rozliczenia")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) in response.text

    async def test_settlement_create_form_offers_both_projects(self, client, db_session):
        email = "sesja-d-rozlform@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        mine, other = await _two_projects(db_session, "d-rozlform", user)
        await _update_user(db_session, email, is_superuser=True)

        response = await client.get(f"/dashboard/{mine.slug}/rozliczenia/create")

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) in response.text


# ---------------------------------------------------------------------------
# Dodatkowe helpery do seedow
# ---------------------------------------------------------------------------


async def _add_member(db_session, project: Project, user: User, role: str = "owner") -> None:
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    await db_session.flush()


async def _make_pipeline_tree(db_session, project: Project) -> tuple[Pipeline, PipelineJob]:
    pipeline = Pipeline(project_id=project.id, pipeline_type="ticket_work", status="created", meta={})
    db_session.add(pipeline)
    await db_session.flush()
    step = PipelineStep(pipeline_id=pipeline.id, name="research", position=0, status="running")
    db_session.add(step)
    await db_session.flush()
    job = PipelineJob(step_id=step.id, name="Job sesja", agent_type="researcher", status="running")
    db_session.add(job)
    await db_session.flush()
    return pipeline, job


async def _make_settlement(db_session, project: Project, creator: User, name: str) -> Settlement:
    settlement = Settlement(
        number=int(uuid.uuid4().int % 1_000_000_000) + 1_000_000,
        name=name,
        period_from=date(2026, 9, 1),
        period_to=date(2026, 9, 30),
        status="draft",
        created_by_id=creator.id,
    )
    db_session.add(settlement)
    await db_session.flush()
    db_session.add(SettlementProject(settlement_id=settlement.id, project_id=project.id))
    await db_session.flush()
    return settlement


async def _settlement_name(db_session, settlement_id: uuid.UUID) -> str:
    return (await db_session.execute(select(Settlement.name).where(Settlement.id == settlement_id))).scalar_one()


# ---------------------------------------------------------------------------
# Raporty: search/users i search/sprints czytaja superusera z bazy
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestReportsSearchUsersAndSprintsUseDatabaseSuperuser:
    async def _seed(self, db_session, tag: str, user: User):
        mine, other = await _two_projects(db_session, tag, user)
        mine_mate = await _make_user(db_session, f"sesja-{tag}-mine-mate@test.com", is_superuser=False)
        other_mate = await _make_user(db_session, f"sesja-{tag}-other-mate@test.com", is_superuser=False)
        await _add_member(db_session, mine, mine_mate, "member")
        await _add_member(db_session, other, other_mate, "member")
        mine_sprint = Sprint(project_id=mine.id, name=f"Sprint widoczny {tag}", start_date=date(2026, 9, 1))
        other_sprint = Sprint(project_id=other.id, name=f"Sprint ukryty {tag}", start_date=date(2026, 9, 1))
        db_session.add_all([mine_sprint, other_sprint])
        await db_session.flush()
        return mine_mate, other_mate, mine_sprint, other_sprint

    async def test_demoted_superuser_search_users_returns_only_member_project_users(self, client, db_session):
        email = "sesja-b-rsu@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine_mate, other_mate, _mine_sprint, _other_sprint = await self._seed(db_session, "b-rsu", user)
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get("/dashboard/reports/search/users")

        assert response.status_code == 200
        values = {u["value"] for u in response.json()}
        assert values == {str(user.id), str(mine_mate.id)}
        assert str(other_mate.id) not in values

    async def test_demoted_superuser_search_sprints_returns_only_member_project_sprints(self, client, db_session):
        email = "sesja-b-rss@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        _mine_mate, _other_mate, mine_sprint, other_sprint = await self._seed(db_session, "b-rss", user)
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get("/dashboard/reports/search/sprints")

        assert response.status_code == 200
        values = [s["value"] for s in response.json()]
        assert values == [str(mine_sprint.id)]
        assert str(other_sprint.id) not in values

    async def test_active_superuser_search_users_returns_users_of_both_projects(self, client, db_session):
        email = "sesja-c-rsu@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine_mate, other_mate, _mine_sprint, _other_sprint = await self._seed(db_session, "c-rsu", user)

        response = await client.get("/dashboard/reports/search/users")

        assert response.status_code == 200
        assert {u["value"] for u in response.json()} >= {str(mine_mate.id), str(other_mate.id)}

    async def test_active_superuser_search_sprints_returns_sprints_of_both_projects(self, client, db_session):
        email = "sesja-c-rss@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        _mine_mate, _other_mate, mine_sprint, other_sprint = await self._seed(db_session, "c-rss", user)

        response = await client.get("/dashboard/reports/search/sprints")

        assert response.status_code == 200
        assert {s["value"] for s in response.json()} >= {str(mine_sprint.id), str(other_sprint.id)}

    async def test_promoted_user_search_users_returns_users_of_both_projects(self, client, db_session):
        email = "sesja-d-rsu@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        mine_mate, other_mate, _mine_sprint, _other_sprint = await self._seed(db_session, "d-rsu", user)
        await _update_user(db_session, email, is_superuser=True)

        response = await client.get("/dashboard/reports/search/users")

        assert response.status_code == 200
        assert {u["value"] for u in response.json()} >= {str(mine_mate.id), str(other_mate.id)}

    async def test_promoted_user_search_sprints_returns_sprints_of_both_projects(self, client, db_session):
        email = "sesja-d-rss@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        _mine_mate, _other_mate, mine_sprint, other_sprint = await self._seed(db_session, "d-rss", user)
        await _update_user(db_session, email, is_superuser=True)

        response = await client.get("/dashboard/reports/search/sprints")

        assert response.status_code == 200
        assert {s["value"] for s in response.json()} >= {str(mine_sprint.id), str(other_sprint.id)}


# ---------------------------------------------------------------------------
# Plan pracy: PATCH i DELETE wpisu przez dezaktywowanego uzytkownika
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeactivatedUserPlanEntryWrites:
    async def _seed_entry(self, db_session, user: User, tag: str) -> WorkPlanEntry:
        project = await _make_project(db_session, tag, owner=user)
        ticket = await _add_ticket(db_session, project, f"Ticket wpisu {tag}")
        entry = WorkPlanEntry(user_id=user.id, ticket_id=ticket.id, scheduled_date=date(2026, 10, 5), position=0)
        db_session.add(entry)
        await db_session.flush()
        return entry

    async def test_patch_entry_returns_401_and_keeps_entry(self, client, db_session):
        email = "sesja-a-ppatch@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        entry = await self._seed_entry(db_session, user, "a-ppatch")
        await _update_user(db_session, email, is_active=False)

        response = await client.patch(f"/dashboard/plan/entries/{entry.id}", json={"position": 7})

        assert response.status_code == 401
        position = (await db_session.execute(select(WorkPlanEntry.position).where(WorkPlanEntry.id == entry.id))).scalar_one()
        assert position == 0

    async def test_delete_entry_returns_401_and_keeps_entry(self, client, db_session):
        email = "sesja-a-pdelete@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        entry = await self._seed_entry(db_session, user, "a-pdelete")
        await _update_user(db_session, email, is_active=False)

        response = await client.delete(f"/dashboard/plan/entries/{entry.id}")

        assert response.status_code == 401
        remaining = (await db_session.execute(select(WorkPlanEntry.id).where(WorkPlanEntry.id == entry.id))).scalar_one_or_none()
        assert remaining == entry.id

    async def test_active_user_patches_own_entry(self, client, db_session):
        email = "sesja-c-ppatch@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        entry = await self._seed_entry(db_session, user, "c-ppatch")

        response = await client.patch(f"/dashboard/plan/entries/{entry.id}", json={"position": 7})

        assert response.status_code == 200
        assert response.json()["position"] == 7

    async def test_active_user_deletes_own_entry(self, client, db_session):
        email = "sesja-c-pdelete@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        entry = await self._seed_entry(db_session, user, "c-pdelete")

        response = await client.delete(f"/dashboard/plan/entries/{entry.id}")

        assert response.status_code == 200
        remaining = (await db_session.execute(select(WorkPlanEntry.id).where(WorkPlanEntry.id == entry.id))).scalar_one_or_none()
        assert remaining is None


# ---------------------------------------------------------------------------
# Pipelines: pozostale handlery dla dezaktywowanego czlonka
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeactivatedMemberPipelineDetailRoutes:
    async def _seed(self, client, db_session, tag: str):
        email = f"sesja-a-{tag}@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, f"a-{tag}", owner=user)
        pipeline, job = await _make_pipeline_tree(db_session, project)
        await _update_user(db_session, email, is_active=False)
        return project, pipeline, job

    async def test_api_get_pipeline_returns_401(self, client, db_session):
        project, pipeline, _job = await self._seed(client, db_session, "pgetapi")

        response = await client.get(f"/dashboard/{project.slug}/pipelines/api/{pipeline.id}")

        assert response.status_code == 401

    async def test_partial_tree_returns_401(self, client, db_session):
        project, pipeline, _job = await self._seed(client, db_session, "ptree")

        response = await client.get(f"/dashboard/{project.slug}/pipelines/{pipeline.id}/partial/tree")

        assert response.status_code == 401

    async def test_pipeline_detail_redirects_to_login(self, client, db_session):
        project, pipeline, _job = await self._seed(client, db_session, "pdetail")

        response = await client.get(f"/dashboard/{project.slug}/pipelines/{pipeline.id}", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_job_detail_redirects_to_login(self, client, db_session):
        project, pipeline, job = await self._seed(client, db_session, "pjob")

        response = await client.get(f"/dashboard/{project.slug}/pipelines/{pipeline.id}/jobs/{job.id}", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_active_member_gets_200_on_all_detail_routes(self, client, db_session):
        email = "sesja-c-pdetails@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "c-pdetails", owner=user)
        pipeline, job = await _make_pipeline_tree(db_session, project)
        base = f"/dashboard/{project.slug}/pipelines"

        statuses = [
            (await client.get(f"{base}/api/{pipeline.id}")).status_code,
            (await client.get(f"{base}/{pipeline.id}/partial/tree")).status_code,
            (await client.get(f"{base}/{pipeline.id}")).status_code,
            (await client.get(f"{base}/{pipeline.id}/jobs/{job.id}")).status_code,
        ]

        assert statuses == [200, 200, 200, 200]


# ---------------------------------------------------------------------------
# Rozliczenia: dezaktywacja (edit GET/POST) i zdegradowany superuser (POST create/edit)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeactivatedUserSettlementEdit:
    async def test_edit_form_redirects_to_login(self, client, db_session):
        email = "sesja-a-editform@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "a-editform", owner=user)
        settlement = await _make_settlement(db_session, project, user, "Rozliczenie edit form sesja")
        await _update_user(db_session, email, is_active=False)

        response = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit", follow_redirects=False)

        _assert_login_redirect(response)

    async def test_edit_post_redirects_to_login_and_keeps_settlement(self, client, db_session):
        email = "sesja-a-editpost@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "a-editpost", owner=user)
        settlement = await _make_settlement(db_session, project, user, "Rozliczenie edit post sesja")
        await _update_user(db_session, email, is_active=False)

        response = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit",
            data={
                "name": "Nazwa po edycji sesja",
                "period_from": "2026-09-01",
                "period_to": "2026-09-30",
                "project_ids": [str(project.id)],
            },
            follow_redirects=False,
        )

        _assert_login_redirect(response)
        assert await _settlement_name(db_session, settlement.id) == "Rozliczenie edit post sesja"

    async def test_create_post_redirects_to_login_and_creates_nothing(self, client, db_session):
        email = "sesja-a-createpost@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "a-createpost", owner=user)
        await _update_user(db_session, email, is_active=False)

        response = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/create",
            data={
                "name": "Rozliczenie nie powinno powstac sesja",
                "period_from": "2026-09-01",
                "period_to": "2026-09-30",
                "project_ids": [str(project.id)],
            },
            follow_redirects=False,
        )

        _assert_login_redirect(response)
        created = (
            await db_session.execute(select(Settlement.id).where(Settlement.name == "Rozliczenie nie powinno powstac sesja"))
        ).scalar_one_or_none()
        assert created is None


@pytest.mark.integration
class TestSettlementPostsUseDatabaseSuperuser:
    """POST bez biezacego projektu na liscie renderuje formularz z `all_projects` zaleznym od flagi superusera."""

    async def test_demoted_superuser_create_post_rerenders_only_member_project(self, client, db_session):
        email = "sesja-b-createpost@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "b-createpost", user)
        await _update_user(db_session, email, is_superuser=False)

        response = await client.post(
            f"/dashboard/{mine.slug}/rozliczenia/create",
            data={"name": "Rozliczenie odrzucone sesja", "period_from": "2026-09-01", "period_to": "2026-09-30"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) not in response.text
        created = (await db_session.execute(select(Settlement.id).where(Settlement.name == "Rozliczenie odrzucone sesja"))).scalar_one_or_none()
        assert created is None

    async def test_demoted_superuser_edit_post_rerenders_only_member_project(self, client, db_session):
        email = "sesja-b-editpost@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "b-editpost", user)
        settlement = await _make_settlement(db_session, mine, user, "Rozliczenie edit post b sesja")
        await _update_user(db_session, email, is_superuser=False)

        response = await client.post(
            f"/dashboard/{mine.slug}/rozliczenia/{settlement.id}/edit",
            data={"name": "Nazwa odrzucona sesja", "period_from": "2026-09-01", "period_to": "2026-09-30"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) not in response.text
        assert await _settlement_name(db_session, settlement.id) == "Rozliczenie edit post b sesja"

    async def test_active_superuser_create_post_rerenders_both_projects(self, client, db_session):
        email = "sesja-c-createpost@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        mine, other = await _two_projects(db_session, "c-createpost", user)

        response = await client.post(
            f"/dashboard/{mine.slug}/rozliczenia/create",
            data={"name": "Rozliczenie odrzucone c sesja", "period_from": "2026-09-01", "period_to": "2026-09-30"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) in response.text

    async def test_promoted_user_edit_post_rerenders_both_projects(self, client, db_session):
        email = "sesja-d-editpost@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        mine, other = await _two_projects(db_session, "d-editpost", user)
        settlement = await _make_settlement(db_session, mine, user, "Rozliczenie edit post d sesja")
        await _update_user(db_session, email, is_superuser=True)

        response = await client.post(
            f"/dashboard/{mine.slug}/rozliczenia/{settlement.id}/edit",
            data={"name": "Nazwa odrzucona d sesja", "period_from": "2026-09-01", "period_to": "2026-09-30"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) in response.text

    async def test_promoted_user_create_post_rerenders_both_projects(self, client, db_session):
        email = "sesja-d-createpost@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        mine, other = await _two_projects(db_session, "d-createpost", user)
        await _update_user(db_session, email, is_superuser=True)

        response = await client.post(
            f"/dashboard/{mine.slug}/rozliczenia/create",
            data={"name": "Rozliczenie odrzucone d sesja", "period_from": "2026-09-01", "period_to": "2026-09-30"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert _option(mine) in response.text
        assert _option(other) in response.text


# ---------------------------------------------------------------------------
# render_project_page: uprawnienia do szablonu z bazy, nie z sesji
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestProjectPagePermissionsUseDatabaseSuperuser:
    """Rola `member` nie ma `rozliczenia`, wiec sidebar nie pokazuje linku Rozliczenia, chyba ze user jest superuserem w bazie."""

    @staticmethod
    def _link(project: Project) -> str:
        return f'href="/dashboard/{project.slug}/rozliczenia/"'

    async def test_demoted_superuser_member_does_not_see_settlements_link(self, client, db_session):
        email = "sesja-b-sidebar@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        project = await _make_project(db_session, "b-sidebar")
        await _add_member(db_session, project, user, "member")
        await _update_user(db_session, email, is_superuser=False)

        response = await client.get(f"/dashboard/{project.slug}/pipelines/")

        assert response.status_code == 200
        assert self._link(project) not in response.text

    async def test_active_superuser_member_sees_settlements_link(self, client, db_session):
        email = "sesja-c-sidebar@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=True)
        project = await _make_project(db_session, "c-sidebar")
        await _add_member(db_session, project, user, "member")

        response = await client.get(f"/dashboard/{project.slug}/pipelines/")

        assert response.status_code == 200
        assert self._link(project) in response.text

    async def test_promoted_member_sees_settlements_link(self, client, db_session):
        email = "sesja-d-sidebar@test.com"
        user = await _logged_in_user(client, db_session, email, is_superuser=False)
        project = await _make_project(db_session, "d-sidebar")
        await _add_member(db_session, project, user, "member")
        await _update_user(db_session, email, is_superuser=True)

        response = await client.get(f"/dashboard/{project.slug}/pipelines/")

        assert response.status_code == 200
        assert self._link(project) in response.text
