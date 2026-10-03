"""Referencje po ID zawężone do projektu operacji (MON-238).

Członek projektu A mógł podać UUID sprintu, strony wiki albo użytkownika z projektu B, a odczyty po
`Ticket.sprint_id` / `WikiPage.parent_id` nie sprawdzały projektu. Plik pilnuje obu stron:

1. zapis: dla każdej referencji (`sprint_id`, `spec_page_id`, `parent_id` strony wiki, assignee) slug
   własnego projektu + ID obiektu z cudzego projektu daje błąd (MCP: `ValueError` z tekstem, API v2:
   422 problem+json z tym samym `detail`, dashboard: 422 z tekstem w HTML) i NIE zostawia zapisu,
2. odczyt: ticket z ręcznie ustawionym w bazie cudzym `sprint_id` nie trafia na tablicę, do burndownu,
   liczników sprintu ani do `complete_sprint` projektu, do którego sprint należy; `get_breadcrumbs`
   i `_collect_descendants` nie przechodzą przez granicę projektu.

Każdy test odrzucenia ma kontrolę pozytywną (obiekt z WŁASNEGO projektu przechodzi), bez niej przeszedłby
na implementacji odrzucającej wszystko. Test zapisu sprawdza stan bazy zapytaniem o kolumny po wywołaniu
(autoflush wyciąga ewentualny częściowy zapis sprzed walidacji), nie echo wejścia.

Testy kanału MCP i API v2 używają środowiska z `test_api_v2_parity.py` (jeden użytkownik, jeden token,
MinIO w pamięci). Kanał dashboardu loguje tego samego użytkownika przez `/auth/login`.

Poza zakresem: `update_wiki_page` nie ma parametru `parent_id` w żadnym kanale, a `update_ticket` nie ma
`spec_page_id`, więc tych referencji nie da się zmienić po utworzeniu.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import func, select

from monolynx.api.v2.common import service_error
from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services import tickets as tickets_service
from monolynx.services.wiki import _collect_descendants, delete_wiki_page, get_breadcrumbs
from tests.integration.api_v2.conftest import add_member, make_project
from tests.integration.test_api_v2_parity import (  # noqa: F401
    ParityEnv,
    _assert_problem,
    _extra_project,
    _new_user,
    _parse_board,
    _parse_sprint,
    _sprint,
    _ticket,
    _ticket_state,
    _wiki_page,
    _wiki_state,
    side_effects,
)
from tests.integration.test_api_v2_parity import env as parity_env  # noqa: F401

pytestmark = pytest.mark.integration

SPRINT_MSG = "Wskazany sprint nie istnieje w tym projekcie"
SPEC_MSG = "Wskazana strona wiki (spec_page_id) nie istnieje w tym projekcie"
PARENT_MSG = "Wskazana strona nadrzędna (parent_id) nie istnieje w tym projekcie"
ASSIGNEE_MSG = "Wskazany użytkownik nie jest członkiem projektu"


@pytest.fixture
async def env(parity_env: ParityEnv) -> ParityEnv:  # noqa: F811
    """Środowisko parytetu z `test_api_v2_parity.py` (użytkownik-owner projektu A, MCP + v2, MinIO w pamięci)."""
    return parity_env


@dataclass
class Foreign:
    """Obiekty projektu B, do którego użytkownik testowy NIE należy (projekt A to `env.project`)."""

    project: Project
    sprint: Sprint
    page: WikiPage
    member: User


@pytest.fixture
async def foreign(env: ParityEnv) -> Foreign:
    other = await make_project(env.db, "xref-foreign")
    member = await _new_user(env, "foreign-member")
    await add_member(env.db, other, member, role="member")
    sprint = await _sprint(env, "Cudzy sprint", project=other)
    page = await _wiki_page(env, "Cudza strona", "cudza-strona", project=other)
    return Foreign(project=other, sprint=sprint, page=page, member=member)


async def _login(env: ParityEnv) -> None:
    response = await env.client.post(
        "/auth/login",
        data={"email": env.actor.user.email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


async def _count_tickets(env: ParityEnv) -> int:
    return (await env.db.execute(select(func.count(Ticket.id)).where(Ticket.project_id == env.project.id))).scalar_one()


async def _count_pages(env: ParityEnv, title: str) -> int:
    return (await env.db.execute(select(func.count(WikiPage.id)).where(WikiPage.project_id == env.project.id, WikiPage.title == title))).scalar_one()


async def _own_member(env: ParityEnv, tag: str) -> User:
    user = await _new_user(env, tag)
    await add_member(env.db, env.project, user, role="member")
    return user


def _create_form(**fields: str) -> dict[str, str]:
    return {"title": "Nowy z formularza", "priority": "medium", **fields}


# ---------------------------------------------------------------------------
# Helpery serwisu
# ---------------------------------------------------------------------------


class TestServiceHelpers:
    async def test_ensure_sprint_in_project_rejects_sprint_of_other_project(self, env, foreign):
        with pytest.raises(ValueError) as info:
            await tickets_service.ensure_sprint_in_project(env.db, env.project.id, foreign.sprint.id)

        assert str(info.value) == SPRINT_MSG

    async def test_ensure_sprint_in_project_rejects_unknown_sprint(self, env):
        with pytest.raises(ValueError) as info:
            await tickets_service.ensure_sprint_in_project(env.db, env.project.id, uuid.uuid4())

        assert str(info.value) == SPRINT_MSG

    async def test_ensure_sprint_in_project_accepts_own_sprint(self, env):
        own = await _sprint(env, "Wlasny sprint")

        assert await tickets_service.ensure_sprint_in_project(env.db, env.project.id, own.id) is None

    async def test_ensure_member_of_project_rejects_user_of_other_project(self, env, foreign):
        with pytest.raises(ValueError) as info:
            await tickets_service.ensure_member_of_project(env.db, env.project.id, foreign.member.id)

        assert str(info.value) == ASSIGNEE_MSG

    async def test_ensure_member_of_project_rejects_unknown_user(self, env):
        with pytest.raises(ValueError) as info:
            await tickets_service.ensure_member_of_project(env.db, env.project.id, uuid.uuid4())

        assert str(info.value) == ASSIGNEE_MSG

    async def test_ensure_member_of_project_accepts_member(self, env):
        member = await _own_member(env, "helper-member")

        assert await tickets_service.ensure_member_of_project(env.db, env.project.id, member.id) is None

    @pytest.mark.parametrize("message", [SPRINT_MSG, SPEC_MSG, PARENT_MSG, ASSIGNEE_MSG])
    def test_service_error_maps_reference_messages_to_422(self, message):
        error = service_error(ValueError(message))

        assert error.status == 422
        assert error.detail == message


# ---------------------------------------------------------------------------
# sprint_id: create_ticket
# ---------------------------------------------------------------------------


class TestSprintOnCreate:
    async def test_mcp_create_ticket_rejects_sprint_of_other_project(self, env, foreign):
        message = await env.mcp_error("create_ticket", env.slug, "Ticket z cudzym sprintem", sprint_id=str(foreign.sprint.id))

        assert message == SPRINT_MSG
        assert await _count_tickets(env) == 0

    async def test_v2_create_ticket_rejects_sprint_of_other_project(self, env, foreign):
        response = await env.v2("create_ticket", json={"title": "Ticket z cudzym sprintem", "sprint_id": str(foreign.sprint.id)})

        _assert_problem(response, 422, SPRINT_MSG)
        assert await _count_tickets(env) == 0

    async def test_mcp_create_ticket_rejects_unknown_sprint(self, env):
        message = await env.mcp_error("create_ticket", env.slug, "Ticket z nieznanym sprintem", sprint_id=str(uuid.uuid4()))

        assert message == SPRINT_MSG
        assert await _count_tickets(env) == 0

    async def test_create_ticket_accepts_own_sprint_in_both_channels(self, env):
        own = await _sprint(env, "Wlasny sprint")

        mcp_out = await env.mcp("create_ticket", env.slug, "Ticket MCP", sprint_id=str(own.id))
        response = await env.v2("create_ticket", json={"title": "Ticket v2", "sprint_id": str(own.id)})

        assert response.status_code == 201
        assert (await _ticket_state(env, mcp_out["id"]))["sprint_id"] == own.id
        assert (await _ticket_state(env, response.json()["id"]))["sprint_id"] == own.id

    async def test_dashboard_create_ticket_rejects_sprint_of_other_project(self, env, foreign):
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/create",
            data=_create_form(sprint_id=str(foreign.sprint.id)),
            follow_redirects=False,
        )

        assert response.status_code == 422
        assert SPRINT_MSG in response.text
        assert await _count_tickets(env) == 0

    async def test_dashboard_create_ticket_accepts_own_sprint(self, env):
        own = await _sprint(env, "Wlasny sprint")
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/create",
            data=_create_form(sprint_id=str(own.id)),
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert (await env.db.execute(select(Ticket.sprint_id).where(Ticket.project_id == env.project.id))).scalar_one() == own.id


# ---------------------------------------------------------------------------
# sprint_id: update_ticket, bulk_update_tickets, edycja w dashboardzie
# ---------------------------------------------------------------------------


class TestSprintOnUpdate:
    async def test_mcp_update_ticket_rejects_sprint_of_other_project_without_partial_write(self, env, foreign):
        ticket = await _ticket(env, "Bazowy", description="stary opis", priority="low")
        before = await _ticket_state(env, ticket.id)

        message = await env.mcp_error("update_ticket", env.slug, str(ticket.id), title="Zmieniony", sprint_id=str(foreign.sprint.id))

        assert message == SPRINT_MSG
        assert await _ticket_state(env, ticket.id) == before

    async def test_v2_update_ticket_rejects_sprint_of_other_project_without_partial_write(self, env, foreign):
        ticket = await _ticket(env, "Bazowy", description="stary opis", priority="low")
        before = await _ticket_state(env, ticket.id)

        response = await env.v2("update_ticket", ticket_id=ticket.id, json={"title": "Zmieniony", "sprint_id": str(foreign.sprint.id)})

        _assert_problem(response, 422, SPRINT_MSG)
        assert await _ticket_state(env, ticket.id) == before

    async def test_update_ticket_accepts_own_sprint_in_both_channels(self, env):
        own = await _sprint(env, "Wlasny sprint")
        ticket_a = await _ticket(env, "Do zmiany MCP")
        ticket_b = await _ticket(env, "Do zmiany v2")

        await env.mcp("update_ticket", env.slug, str(ticket_a.id), sprint_id=str(own.id))
        response = await env.v2("update_ticket", ticket_id=ticket_b.id, json={"sprint_id": str(own.id)})

        assert response.status_code == 200
        assert (await _ticket_state(env, ticket_a.id))["sprint_id"] == own.id
        assert (await _ticket_state(env, ticket_b.id))["sprint_id"] == own.id

    async def test_mcp_bulk_update_rejects_sprint_of_other_project_for_whole_call(self, env, foreign):
        tickets = [await _ticket(env, f"Masowy {i}") for i in range(2)]
        before = [await _ticket_state(env, t.id) for t in tickets]

        message = await env.mcp_error("bulk_update_tickets", env.slug, [str(t.id) for t in tickets], status="done", sprint_id=str(foreign.sprint.id))

        assert message == SPRINT_MSG
        assert [await _ticket_state(env, t.id) for t in tickets] == before

    async def test_v2_bulk_update_rejects_sprint_of_other_project_for_whole_call(self, env, foreign):
        tickets = [await _ticket(env, f"Masowy {i}") for i in range(2)]
        before = [await _ticket_state(env, t.id) for t in tickets]

        body = {"ticket_ids": [str(t.id) for t in tickets], "status": "done", "sprint_id": str(foreign.sprint.id)}

        response = await env.v2("bulk_update_tickets", json=body)

        _assert_problem(response, 422, SPRINT_MSG)
        assert [await _ticket_state(env, t.id) for t in tickets] == before

    async def test_bulk_update_accepts_own_sprint_in_both_channels(self, env):
        own = await _sprint(env, "Wlasny sprint")
        via_mcp = await _ticket(env, "Masowy MCP")
        via_v2 = await _ticket(env, "Masowy v2")

        mcp_out = await env.mcp("bulk_update_tickets", env.slug, [str(via_mcp.id)], sprint_id=str(own.id))
        response = await env.v2("bulk_update_tickets", json={"ticket_ids": [str(via_v2.id)], "sprint_id": str(own.id)})

        assert response.status_code == 200
        assert mcp_out["updated"] == response.json()["updated"] == 1
        assert (await _ticket_state(env, via_mcp.id))["sprint_id"] == own.id
        assert (await _ticket_state(env, via_v2.id))["sprint_id"] == own.id

    async def test_dashboard_edit_rejects_sprint_of_other_project_without_partial_write(self, env, foreign):
        ticket = await _ticket(env, "Bazowy", description="stary opis", priority="low")
        before = await _ticket_state(env, ticket.id)
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Zmieniony", "priority": "low", "status": "backlog", "sprint_id": str(foreign.sprint.id)},
            follow_redirects=False,
        )

        assert response.status_code == 422
        assert SPRINT_MSG in response.text
        assert await _ticket_state(env, ticket.id) == before

    async def test_dashboard_edit_accepts_own_sprint(self, env):
        own = await _sprint(env, "Wlasny sprint")
        ticket = await _ticket(env, "Bazowy")
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Zmieniony", "priority": "low", "status": "todo", "sprint_id": str(own.id)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        state = await _ticket_state(env, ticket.id)
        assert (state["title"], state["sprint_id"]) == ("Zmieniony", own.id)

    async def test_dashboard_edit_with_unchanged_foreign_sprint_still_saves(self, env, foreign):
        # Dane sprzed MON-238 mogą mieć cudzy sprint; walidacja dotyczy tylko zmiany wartości.
        ticket = await _ticket(env, "Bazowy", sprint_id=foreign.sprint.id, status="todo")
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Zmieniony", "priority": "low", "status": "todo", "sprint_id": str(foreign.sprint.id)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        state = await _ticket_state(env, ticket.id)
        assert (state["title"], state["sprint_id"]) == ("Zmieniony", foreign.sprint.id)


# ---------------------------------------------------------------------------
# sprint_id: create_ticket_from_issue
# ---------------------------------------------------------------------------


class TestSprintOnCreateFromIssue:
    async def _issue(self, env: ParityEnv) -> Issue:
        issue = Issue(project_id=env.project.id, fingerprint=uuid.uuid4().hex, title="ValueError: boom", status="unresolved")
        env.db.add(issue)
        await env.db.flush()
        return issue

    async def test_mcp_create_ticket_from_issue_rejects_sprint_of_other_project(self, env, foreign):
        issue = await self._issue(env)

        message = await env.mcp_error("create_ticket_from_issue", env.slug, str(issue.id), sprint_id=str(foreign.sprint.id))

        assert message == SPRINT_MSG
        assert (await env.db.execute(select(func.count(Ticket.id)).where(Ticket.issue_id == issue.id))).scalar_one() == 0

    async def test_mcp_create_ticket_from_issue_accepts_own_sprint(self, env):
        issue = await self._issue(env)
        own = await _sprint(env, "Wlasny sprint")

        result = await env.mcp("create_ticket_from_issue", env.slug, str(issue.id), sprint_id=str(own.id))

        assert (await _ticket_state(env, result["ticket_id"]))["sprint_id"] == own.id


# ---------------------------------------------------------------------------
# spec_page_id (tylko create_ticket: update_ticket nie ma tego parametru)
# ---------------------------------------------------------------------------


class TestSpecPage:
    async def test_mcp_create_ticket_rejects_spec_page_of_other_project(self, env, foreign):
        message = await env.mcp_error("create_ticket", env.slug, "Ticket z cudzym specem", spec_page_id=str(foreign.page.id))

        assert message == SPEC_MSG
        assert await _count_tickets(env) == 0

    async def test_v2_create_ticket_rejects_spec_page_of_other_project(self, env, foreign):
        response = await env.v2("create_ticket", json={"title": "Ticket z cudzym specem", "spec_page_id": str(foreign.page.id)})

        _assert_problem(response, 422, SPEC_MSG)
        assert await _count_tickets(env) == 0

    async def test_mcp_create_ticket_rejects_unknown_spec_page(self, env):
        message = await env.mcp_error("create_ticket", env.slug, "Ticket z nieznanym specem", spec_page_id=str(uuid.uuid4()))

        assert message == SPEC_MSG
        assert await _count_tickets(env) == 0

    async def test_create_ticket_accepts_own_spec_page_in_both_channels(self, env):
        spec = await _wiki_page(env, "Wlasna specyfikacja", "wlasna-specyfikacja")

        mcp_out = await env.mcp("create_ticket", env.slug, "Ticket MCP", spec_page_id=str(spec.id))
        response = await env.v2("create_ticket", json={"title": "Ticket v2", "spec_page_id": str(spec.id)})

        assert response.status_code == 201
        assert (await _ticket_state(env, mcp_out["id"]))["spec_page_id"] == spec.id
        assert (await _ticket_state(env, response.json()["id"]))["spec_page_id"] == spec.id


# ---------------------------------------------------------------------------
# parent_id strony wiki (tylko create_wiki_page: update_wiki_page nie ma tego parametru)
# ---------------------------------------------------------------------------


class TestWikiParent:
    async def test_mcp_create_wiki_page_rejects_parent_of_other_project_before_upload(self, env, foreign):
        stored = dict(env.fx.minio)

        message = await env.mcp_error("create_wiki_page", env.slug, "Dziecko cudzego", "tresc", parent_id=str(foreign.page.id))

        assert message == PARENT_MSG
        assert await _count_pages(env, "Dziecko cudzego") == 0
        assert env.fx.minio == stored

    async def test_v2_create_wiki_page_rejects_parent_of_other_project_before_upload(self, env, foreign):
        stored = dict(env.fx.minio)

        response = await env.v2("create_wiki_page", json={"title": "Dziecko cudzego", "content": "tresc", "parent_id": str(foreign.page.id)})

        _assert_problem(response, 422, PARENT_MSG)
        assert await _count_pages(env, "Dziecko cudzego") == 0
        assert env.fx.minio == stored

    async def test_mcp_create_wiki_page_rejects_unknown_parent(self, env):
        message = await env.mcp_error("create_wiki_page", env.slug, "Dziecko nieznanego", "tresc", parent_id=str(uuid.uuid4()))

        assert message == PARENT_MSG
        assert await _count_pages(env, "Dziecko nieznanego") == 0

    async def test_v2_create_wiki_page_rejects_unknown_parent(self, env):
        response = await env.v2("create_wiki_page", json={"title": "Dziecko nieznanego", "content": "tresc", "parent_id": str(uuid.uuid4())})

        _assert_problem(response, 422, PARENT_MSG)
        assert await _count_pages(env, "Dziecko nieznanego") == 0

    async def test_create_wiki_page_accepts_own_parent_in_both_channels(self, env):
        parent = await _wiki_page(env, "Wlasny rodzic", "wlasny-rodzic")

        mcp_out = await env.mcp("create_wiki_page", env.slug, "Dziecko MCP", "tresc", parent_id=str(parent.id))
        response = await env.v2("create_wiki_page", json={"title": "Dziecko v2", "content": "tresc", "parent_id": str(parent.id)})

        assert response.status_code == 201
        assert (await _wiki_state(env, mcp_out["id"]))["parent_id"] == parent.id
        assert (await _wiki_state(env, response.json()["id"]))["parent_id"] == parent.id


# ---------------------------------------------------------------------------
# assignee: create_ticket / update_ticket / bulk_update_tickets / dashboard
# ---------------------------------------------------------------------------


def _unknown_email() -> str:
    return f"nobody-{uuid.uuid4().hex[:8]}@example.com"


class TestAssigneeOnCreate:
    async def test_mcp_create_ticket_rejects_user_outside_project(self, env, foreign):
        message = await env.mcp_error("create_ticket", env.slug, "Ticket z obcym", assignee_email=foreign.member.email)

        assert message == ASSIGNEE_MSG
        assert await _count_tickets(env) == 0

    async def test_mcp_create_ticket_rejects_unknown_email(self, env):
        message = await env.mcp_error("create_ticket", env.slug, "Ticket z nieznanym", assignee_email=_unknown_email())

        assert message == ASSIGNEE_MSG
        assert await _count_tickets(env) == 0

    async def test_v2_create_ticket_rejects_user_outside_project(self, env, foreign):
        response = await env.v2("create_ticket", json={"title": "Ticket z obcym", "assignee_email": foreign.member.email})

        _assert_problem(response, 422, ASSIGNEE_MSG)
        assert await _count_tickets(env) == 0

    async def test_v2_create_ticket_rejects_unknown_email(self, env):
        response = await env.v2("create_ticket", json={"title": "Ticket z nieznanym", "assignee_email": _unknown_email()})

        _assert_problem(response, 422, ASSIGNEE_MSG)
        assert await _count_tickets(env) == 0

    async def test_create_ticket_accepts_project_member_in_both_channels(self, env):
        member = await _own_member(env, "create-member")

        mcp_out = await env.mcp("create_ticket", env.slug, "Ticket MCP", assignee_email=member.email)
        response = await env.v2("create_ticket", json={"title": "Ticket v2", "assignee_email": member.email})

        assert response.status_code == 201
        assert (await _ticket_state(env, mcp_out["id"]))["assignee_id"] == member.id
        assert (await _ticket_state(env, response.json()["id"]))["assignee_id"] == member.id


class TestAssigneeOnUpdate:
    @pytest.mark.parametrize("email_kind", ["outside", "unknown"])
    async def test_mcp_update_ticket_rejects_non_member_without_partial_write(self, email_kind, env, foreign):
        email = foreign.member.email if email_kind == "outside" else _unknown_email()
        ticket = await _ticket(env, "Bazowy", priority="low")
        before = await _ticket_state(env, ticket.id)

        message = await env.mcp_error("update_ticket", env.slug, str(ticket.id), title="Zmieniony", assignee_email=email)

        assert message == ASSIGNEE_MSG
        assert await _ticket_state(env, ticket.id) == before

    @pytest.mark.parametrize("email_kind", ["outside", "unknown"])
    async def test_v2_update_ticket_rejects_non_member_without_partial_write(self, email_kind, env, foreign):
        email = foreign.member.email if email_kind == "outside" else _unknown_email()
        ticket = await _ticket(env, "Bazowy", priority="low")
        before = await _ticket_state(env, ticket.id)

        response = await env.v2("update_ticket", ticket_id=ticket.id, json={"title": "Zmieniony", "assignee_email": email})

        _assert_problem(response, 422, ASSIGNEE_MSG)
        assert await _ticket_state(env, ticket.id) == before

    async def test_update_ticket_accepts_project_member_in_both_channels(self, env):
        member = await _own_member(env, "update-member")
        ticket_a = await _ticket(env, "Do zmiany MCP")
        ticket_b = await _ticket(env, "Do zmiany v2")

        await env.mcp("update_ticket", env.slug, str(ticket_a.id), assignee_email=member.email)
        response = await env.v2("update_ticket", ticket_id=ticket_b.id, json={"assignee_email": member.email})

        assert response.status_code == 200
        assert (await _ticket_state(env, ticket_a.id))["assignee_id"] == member.id
        assert (await _ticket_state(env, ticket_b.id))["assignee_id"] == member.id

    async def test_update_ticket_with_empty_assignee_still_clears_it(self, env):
        member = await _own_member(env, "clear-member")
        ticket = await _ticket(env, "Z przypisaniem", assignee_id=member.id)

        await env.mcp("update_ticket", env.slug, str(ticket.id), assignee_email="")

        assert (await _ticket_state(env, ticket.id))["assignee_id"] is None


class TestAssigneeOnBulk:
    @pytest.mark.parametrize("email_kind", ["outside", "unknown"])
    async def test_mcp_bulk_update_rejects_non_member_for_whole_call(self, email_kind, env, foreign):
        email = foreign.member.email if email_kind == "outside" else _unknown_email()
        tickets = [await _ticket(env, f"Masowy {i}") for i in range(2)]
        before = [await _ticket_state(env, t.id) for t in tickets]

        message = await env.mcp_error("bulk_update_tickets", env.slug, [str(t.id) for t in tickets], status="done", assignee_email=email)

        assert message == ASSIGNEE_MSG
        assert [await _ticket_state(env, t.id) for t in tickets] == before

    @pytest.mark.parametrize("email_kind", ["outside", "unknown"])
    async def test_v2_bulk_update_rejects_non_member_for_whole_call(self, email_kind, env, foreign):
        email = foreign.member.email if email_kind == "outside" else _unknown_email()
        tickets = [await _ticket(env, f"Masowy {i}") for i in range(2)]
        before = [await _ticket_state(env, t.id) for t in tickets]

        response = await env.v2("bulk_update_tickets", json={"ticket_ids": [str(t.id) for t in tickets], "status": "done", "assignee_email": email})

        _assert_problem(response, 422, ASSIGNEE_MSG)
        assert [await _ticket_state(env, t.id) for t in tickets] == before

    async def test_bulk_update_accepts_project_member_in_both_channels(self, env):
        member = await _own_member(env, "bulk-member")
        via_mcp = await _ticket(env, "Masowy MCP")
        via_v2 = await _ticket(env, "Masowy v2")

        mcp_out = await env.mcp("bulk_update_tickets", env.slug, [str(via_mcp.id)], assignee_email=member.email)
        response = await env.v2("bulk_update_tickets", json={"ticket_ids": [str(via_v2.id)], "assignee_email": member.email})

        assert response.status_code == 200
        assert mcp_out["updated"] == response.json()["updated"] == 1
        assert (await _ticket_state(env, via_mcp.id))["assignee_id"] == member.id
        assert (await _ticket_state(env, via_v2.id))["assignee_id"] == member.id


class TestAssigneeInDashboardForms:
    @pytest.mark.parametrize("target", ["outside", "unknown"])
    async def test_create_rejects_non_member(self, target, env, foreign):
        assignee_id = foreign.member.id if target == "outside" else uuid.uuid4()
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/create",
            data=_create_form(assignee_id=str(assignee_id)),
            follow_redirects=False,
        )

        assert response.status_code == 422
        assert ASSIGNEE_MSG in response.text
        assert await _count_tickets(env) == 0

    async def test_create_accepts_project_member(self, env):
        member = await _own_member(env, "form-member")
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/create",
            data=_create_form(assignee_id=str(member.id)),
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert (await env.db.execute(select(Ticket.assignee_id).where(Ticket.project_id == env.project.id))).scalar_one() == member.id

    async def test_edit_rejects_user_outside_project_without_partial_write(self, env, foreign):
        ticket = await _ticket(env, "Bazowy", priority="low")
        before = await _ticket_state(env, ticket.id)
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Zmieniony", "priority": "low", "status": "backlog", "assignee_id": str(foreign.member.id)},
            follow_redirects=False,
        )

        assert response.status_code == 422
        assert ASSIGNEE_MSG in response.text
        assert await _ticket_state(env, ticket.id) == before

    async def test_edit_accepts_project_member(self, env):
        member = await _own_member(env, "edit-member")
        ticket = await _ticket(env, "Bazowy")
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Zmieniony", "priority": "low", "status": "backlog", "assignee_id": str(member.id)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        state = await _ticket_state(env, ticket.id)
        assert (state["title"], state["assignee_id"]) == ("Zmieniony", member.id)

    async def test_edit_with_unchanged_non_member_assignee_still_saves(self, env, foreign):
        # Dane sprzed MON-238 mogą mieć obcego assignee; walidacja dotyczy tylko zmiany wartości.
        ticket = await _ticket(env, "Bazowy", assignee_id=foreign.member.id)
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{env.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Zmieniony", "priority": "low", "status": "backlog", "assignee_id": str(foreign.member.id)},
            follow_redirects=False,
        )

        assert response.status_code == 303
        state = await _ticket_state(env, ticket.id)
        assert (state["title"], state["assignee_id"]) == ("Zmieniony", foreign.member.id)


# ---------------------------------------------------------------------------
# Odczyty: ticket projektu A z ręcznie ustawionym sprintem projektu B
# ---------------------------------------------------------------------------


@dataclass
class StraySetup:
    """Aktywny sprint projektu B z jednym własnym ticketem (3 SP) i jednym cudzym z projektu A (5 SP)."""

    project: Project
    sprint: Sprint
    own: Ticket
    stray: Ticket


@pytest.fixture
async def stray(env: ParityEnv) -> StraySetup:
    project_b = await _extra_project(env, "stray-b")
    sprint = await _sprint(env, "Sprint projektu B", project=project_b, status="active")
    own = await _ticket(env, "Wlasciwy ticket", project=project_b, sprint_id=sprint.id, status="todo", story_points=3)
    stray_ticket = await _ticket(env, "STRAY z projektu A", sprint_id=sprint.id, status="in_progress", story_points=5)
    return StraySetup(project=project_b, sprint=sprint, own=own, stray=stray_ticket)


class TestStrayTicketReads:
    async def test_mcp_get_board_skips_ticket_of_other_project(self, env, stray):
        text = await env.mcp("get_board", stray.project.slug)

        assert "Wlasciwy ticket" in text
        assert "STRAY" not in text
        assert _parse_board(text)["sprint"][3] == 3

    async def test_v2_get_board_skips_ticket_of_other_project(self, env, stray):
        response = await env.v2("get_board", slug=stray.project.slug)

        assert response.status_code == 200
        assert "Wlasciwy ticket" in response.text
        assert "STRAY" not in response.text

    async def test_dashboard_board_skips_ticket_of_other_project(self, env, stray):
        await _login(env)

        response = await env.client.get(f"/dashboard/{stray.project.slug}/scrum/board")

        assert response.status_code == 200
        assert "Wlasciwy ticket" in response.text
        assert "STRAY" not in response.text

    async def test_mcp_get_burndown_counts_only_tickets_of_the_sprint_project(self, env, stray):
        data = await env.mcp("get_burndown", stray.project.slug, sprint_id=str(stray.sprint.id))

        assert data["sprint"]["total_story_points"] == 3

    async def test_v2_get_burndown_counts_only_tickets_of_the_sprint_project(self, env, stray):
        response = await env.v2("get_burndown", slug=stray.project.slug, params={"sprint_id": str(stray.sprint.id)})

        assert response.status_code == 200
        assert response.json()["sprint"]["total_story_points"] == 3

    async def test_mcp_list_sprints_counts_only_tickets_of_the_sprint_project(self, env, stray):
        items = await env.mcp("list_sprints", stray.project.slug)

        item = next(i for i in items if i["id"] == str(stray.sprint.id))
        assert (item["ticket_count"], item["story_points_total"]) == (1, 3)

    async def test_v2_list_sprints_counts_only_tickets_of_the_sprint_project(self, env, stray):
        response = await env.v2("list_sprints", slug=stray.project.slug)

        assert response.status_code == 200
        item = next(i for i in response.json()["items"] if i["id"] == str(stray.sprint.id))
        assert (item["ticket_count"], item["story_points_total"]) == (1, 3)

    async def test_dashboard_sprint_list_counts_only_tickets_of_the_sprint_project(self, env, stray):
        await _login(env)

        response = await env.client.get(f"/dashboard/{stray.project.slug}/scrum/sprints")

        assert response.status_code == 200
        # Z filtrem projektu: 1 ticket i 3 SP (własny). Bez filtra byłoby "2 ticketow" i 8 SP.
        assert "1 ticket &middot; 3 SP" in response.text
        assert "2 ticketow" not in response.text
        assert "8 SP" not in response.text

    async def test_mcp_get_sprint_skips_ticket_of_other_project(self, env, stray):
        text = await env.mcp("get_sprint", stray.project.slug, str(stray.sprint.id))

        assert "STRAY" not in text
        assert _parse_sprint(text)["total_sp"] == 3

    async def test_mcp_complete_sprint_leaves_ticket_of_other_project_untouched(self, env, stray):
        await env.mcp("complete_sprint", stray.project.slug, str(stray.sprint.id))

        await self._assert_only_own_ticket_returned_to_backlog(env, stray)

    async def test_v2_complete_sprint_leaves_ticket_of_other_project_untouched(self, env, stray):
        response = await env.v2("complete_sprint", slug=stray.project.slug, sprint_id=stray.sprint.id)

        assert response.status_code == 200
        await self._assert_only_own_ticket_returned_to_backlog(env, stray)

    async def test_dashboard_complete_sprint_leaves_ticket_of_other_project_untouched(self, env, stray):
        await _login(env)

        response = await env.client.post(
            f"/dashboard/{stray.project.slug}/scrum/sprints/{stray.sprint.id}/complete",
            follow_redirects=False,
        )

        assert response.status_code == 303
        await self._assert_only_own_ticket_returned_to_backlog(env, stray)

    @staticmethod
    async def _assert_only_own_ticket_returned_to_backlog(env: ParityEnv, stray: StraySetup) -> None:
        # Oba tickety są niedokończone: własny wraca do backlogu (dowód, że UPDATE się wykonał),
        # cudzy zostaje nietknięty (bez filtra projektu też by wrócił).
        own = await _ticket_state(env, stray.own.id)
        foreign_ticket = await _ticket_state(env, stray.stray.id)
        assert (own["status"], own["sprint_id"]) == ("backlog", None)
        assert (foreign_ticket["status"], foreign_ticket["sprint_id"]) == ("in_progress", stray.sprint.id)


# ---------------------------------------------------------------------------
# Wiki: breadcrumbs i potomkowie nie przekraczają granicy projektu
# ---------------------------------------------------------------------------


class TestWikiProjectBoundary:
    async def test_get_breadcrumbs_stops_at_parent_of_other_project(self, env, foreign):
        page = await _wiki_page(env, "Dziecko", "dziecko-cudzego", parent=foreign.page)

        crumbs = await get_breadcrumbs(page, env.db)

        assert crumbs == [page]

    async def test_get_breadcrumbs_keeps_own_chain_and_drops_foreign_ancestor(self, env, foreign):
        # Łańcuch: wnuk (A) -> rodzic (A) -> przodek (B). Własny rodzic zostaje, cudzy przodek odpada.
        parent = await _wiki_page(env, "Rodzic", "rodzic-lancuch", parent=foreign.page)
        page = await _wiki_page(env, "Wnuk", "wnuk-lancuch", parent=parent)

        crumbs = await get_breadcrumbs(page, env.db)

        assert crumbs == [parent, page]

    async def test_get_breadcrumbs_returns_full_chain_inside_one_project(self, env):
        root = await _wiki_page(env, "Korzen", "korzen-lancuch")
        parent = await _wiki_page(env, "Rodzic", "rodzic-wlasny", parent=root)
        page = await _wiki_page(env, "Dziecko", "dziecko-wlasne", parent=parent)

        crumbs = await get_breadcrumbs(page, env.db)

        assert crumbs == [root, parent, page]

    async def test_collect_descendants_skips_child_of_other_project(self, env, foreign):
        parent = await _wiki_page(env, "Rodzic", "rodzic-potomkowie")
        own_child = await _wiki_page(env, "Wlasne dziecko", "wlasne-dziecko", parent=parent)
        own_grandchild = await _wiki_page(env, "Wlasny wnuk", "wlasny-wnuk", parent=own_child)
        foreign_child = await _wiki_page(env, "Cudze dziecko", "cudze-dziecko", project=foreign.project, parent=parent)
        foreign_grandchild = await _wiki_page(env, "Cudzy wnuk", "cudzy-wnuk", project=foreign.project, parent=foreign_child)

        found = await _collect_descendants(parent.id, env.db, project_id=parent.project_id)

        assert {p.id for p in found} == {own_child.id, own_grandchild.id}
        assert foreign_child.id not in {p.id for p in found}
        assert foreign_grandchild.id not in {p.id for p in found}

    async def test_delete_wiki_page_does_not_remove_storage_of_child_from_other_project(self, env, foreign):
        parent = await _wiki_page(env, "Rodzic do usuniecia", "rodzic-do-usuniecia")
        own_child = await _wiki_page(env, "Wlasne dziecko", "dziecko-do-usuniecia", parent=parent)
        foreign_child = await _wiki_page(env, "Cudze dziecko", "cudze-dziecko-usuwanie", project=foreign.project, parent=parent)
        own_path, parent_path, foreign_path = own_child.minio_path, parent.minio_path, foreign_child.minio_path

        await delete_wiki_page(parent, env.db)

        assert parent_path in env.fx.deleted_paths
        assert own_path in env.fx.deleted_paths
        assert foreign_path not in env.fx.deleted_paths
