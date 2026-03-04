"""Testy jednostkowe MCP Server -- narzedzia Scrum, 500ki, Monitoring, Wiki."""

import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.mcp_server import (
    _auth,
    _get_auth_header,
    _get_user_and_project,
    add_comment,
    complete_sprint,
    create_sprint,
    create_ticket,
    delete_ticket,
    get_board,
    get_issue,
    get_monitor,
    get_project_summary,
    get_sprint,
    get_ticket,
    list_comments,
    list_issues,
    list_monitors,
    list_projects,
    list_sprints,
    list_tickets,
    log_time,
    mcp,
    start_sprint,
    update_issue_status,
    update_ticket,
)
from monolynx.mcp_server import create_wiki_page as mcp_create_wiki_page
from monolynx.mcp_server import delete_wiki_page as mcp_delete_wiki_page
from monolynx.mcp_server import get_wiki_page as mcp_get_wiki_page
from monolynx.mcp_server import list_wiki_pages as mcp_list_wiki_pages
from monolynx.mcp_server import search_wiki as mcp_search_wiki
from monolynx.mcp_server import update_wiki_page as mcp_update_wiki_page
from monolynx.models.event import Event
from monolynx.models.issue import Issue
from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(token: str = "test-token") -> MagicMock:
    """Mock MCP Context z Bearer token w naglowku."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


def _make_ctx_no_request() -> MagicMock:
    """Mock MCP Context bez request_context."""
    ctx = MagicMock()
    ctx.request_context = None
    return ctx


def _make_ctx_no_http_request() -> MagicMock:
    """Mock MCP Context bez HTTP request."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = None
    return ctx


def _make_ctx_no_bearer() -> MagicMock:
    """Mock MCP Context bez tokenu Bearer."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": "Basic abc"}
    return ctx


@pytest.fixture
async def mcp_user(db_session):
    """Testowy uzytkownik MCP."""
    user = User(
        email=f"mcp-unit-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def mcp_project(db_session):
    """Testowy projekt."""
    _slug = f"mcp-unit-{uuid.uuid4().hex[:8]}"
    project = Project(
        name="MCP Unit Project",
        slug=_slug,
        code=_slug.replace("-", "").upper()[:5],
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.fixture
async def mcp_member(db_session, mcp_user, mcp_project):
    """Czlonkostwo w projekcie."""
    member = ProjectMember(
        project_id=mcp_project.id,
        user_id=mcp_user.id,
        role="owner",
    )
    db_session.add(member)
    await db_session.flush()
    return member


@pytest.fixture
def mock_factory(db_session):
    """Zwraca async context manager zastepujacy async_session_factory.

    Podmienia commit() na flush(), zeby nie commitowac outer transakcji
    testowej (db_session). Dzieki temu db_session.rollback() na teardown
    nadal dziala.
    """
    original_commit = db_session.commit

    async def _flush_instead():
        await db_session.flush()

    @asynccontextmanager
    async def _factory():
        db_session.commit = _flush_instead
        try:
            yield db_session
        finally:
            db_session.commit = original_commit

    return _factory


@pytest.fixture
def mock_verify(mcp_user):
    """AsyncMock verify_mcp_token zwracajacy test usera."""
    return AsyncMock(return_value=mcp_user)


# ---------------------------------------------------------------------------
# Rejestracja narzedzi MCP i konfiguracja serwera
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = [
    "list_projects",
    "list_issues",
    "get_issue",
    "update_issue_status",
    "list_monitors",
    "get_monitor",
    "get_board",
    "get_project_summary",
    "list_tickets",
    "get_ticket",
    "create_ticket",
    "update_ticket",
    "delete_ticket",
    "list_sprints",
    "get_sprint",
    "create_sprint",
    "start_sprint",
    "complete_sprint",
    "list_comments",
    "add_comment",
    "log_time",
    "list_wiki_pages",
    "get_wiki_page",
    "create_wiki_page",
    "update_wiki_page",
    "delete_wiki_page",
    "search_wiki",
    "create_graph_node",
    "list_graph_nodes",
    "get_graph_node",
    "delete_graph_node",
    "create_graph_edge",
    "delete_graph_edge",
    "query_graph",
    "find_graph_path",
    "get_graph_stats",
    "bulk_create_graph_nodes",
    "bulk_create_graph_edges",
    "create_ticket_from_issue",
]


@pytest.mark.unit
class TestMcpToolRegistration:
    """Weryfikacja ze wszystkie narzedzia MCP sa poprawnie zarejestrowane."""

    async def test_list_tools_returns_all_tools(self):
        """list_tools() zwraca wszystkie 38 narzedzi."""
        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]
        for name in EXPECTED_TOOLS:
            assert name in tool_names, f"Brak narzedzia: {name}"
        assert len(tools) == len(EXPECTED_TOOLS)

    async def test_all_tools_have_description(self):
        """Kazde narzedzie ma opis (description)."""
        tools = await mcp.list_tools()
        for tool in tools:
            assert tool.description, f"{tool.name} nie ma opisu"

    async def test_all_tools_have_input_schema(self):
        """Kazde narzedzie ma schemat parametrow (inputSchema)."""
        tools = await mcp.list_tools()
        for tool in tools:
            assert tool.inputSchema is not None, f"{tool.name} nie ma schematu"
            assert "properties" in tool.inputSchema, f"{tool.name} brak properties w schemacie"

    async def test_all_tools_require_project_slug_except_list_projects(self):
        """Wszystkie narzedzia poza list_projects wymagaja project_slug."""
        tools = await mcp.list_tools()
        for tool in tools:
            props = tool.inputSchema.get("properties", {})
            if tool.name == "list_projects":
                assert "project_slug" not in props
            else:
                assert "project_slug" in props, f"{tool.name} nie ma parametru project_slug"


@pytest.mark.unit
class TestMcpServerConfig:
    """Weryfikacja konfiguracji serwera MCP."""

    def test_json_response_enabled(self):
        """json_response=True -- odpowiedzi JSON zamiast SSE (kompatybilnosc z proxy)."""
        assert mcp.settings.json_response is True

    def test_streamable_http_path(self):
        """Sciezka HTTP transportu to /."""
        assert mcp.settings.streamable_http_path == "/"

    def test_dns_rebinding_protection_enabled(self):
        """Ochrona DNS rebinding jest wlaczona."""
        assert mcp.settings.transport_security is not None
        assert mcp.settings.transport_security.enable_dns_rebinding_protection is True


# ---------------------------------------------------------------------------
# _auth / _get_auth_header
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuthHelpers:
    async def test_auth_no_request_context(self):
        ctx = _make_ctx_no_request()
        with pytest.raises(ValueError, match="Brak kontekstu HTTP"):
            await _auth(ctx)

    async def test_auth_no_http_request(self):
        ctx = _make_ctx_no_http_request()
        with pytest.raises(ValueError, match="Brak kontekstu HTTP request"):
            await _auth(ctx)

    async def test_auth_no_bearer(self):
        ctx = _make_ctx_no_bearer()
        with pytest.raises(ValueError, match="Brak tokenu Bearer"):
            await _auth(ctx)

    async def test_auth_invalid_token(self, mock_factory):
        ctx = _make_ctx("invalid-token")
        mock_verify_none = AsyncMock(return_value=None)
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify_none),
            pytest.raises(ValueError, match="Nieprawidlowy lub nieaktywny token"),
        ):
            await _auth(ctx)

    async def test_auth_valid_token(self, mcp_user, mock_factory, mock_verify):
        ctx = _make_ctx("valid-token")
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            user = await _auth(ctx)
            assert user.id == mcp_user.id

    async def test_get_auth_header_no_request(self):
        ctx = _make_ctx_no_request()
        with pytest.raises(ValueError, match="Brak kontekstu HTTP"):
            await _get_auth_header(ctx)

    async def test_get_auth_header_no_http_request(self):
        ctx = _make_ctx_no_http_request()
        with pytest.raises(ValueError, match="Brak kontekstu HTTP request"):
            await _get_auth_header(ctx)

    async def test_get_auth_header_no_bearer(self):
        ctx = _make_ctx_no_bearer()
        with pytest.raises(ValueError, match="Brak tokenu Bearer"):
            await _get_auth_header(ctx)

    async def test_get_auth_header_extracts_token(self):
        ctx = _make_ctx("my-secret-token")
        result = await _get_auth_header(ctx)
        assert result == "my-secret-token"


# ---------------------------------------------------------------------------
# _get_user_and_project
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetUserAndProject:
    async def test_valid_access(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            user, project = await _get_user_and_project(ctx, mcp_project.slug)
            assert user.id == mcp_user.id
            assert project.id == mcp_project.id

    async def test_project_not_found(self, db_session, mcp_user, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await _get_user_and_project(ctx, "nonexistent-slug")

    async def test_user_not_member(self, db_session, mcp_user, mcp_project, mock_factory, mock_verify):
        """Uzytkownik istnieje, projekt istnieje, ale brak membership."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie jest czlonkiem"),
        ):
            await _get_user_and_project(ctx, mcp_project.slug)

    async def test_inactive_project_not_found(self, db_session, mcp_user, mock_factory, mock_verify):
        """Projekt z is_active=False nie powinien byc dostepny."""
        _slug = f"inactive-{uuid.uuid4().hex[:8]}"
        project = Project(
            name="Inactive Project",
            slug=_slug,
            code=_slug.replace("-", "").upper()[:5],
            api_key=secrets.token_urlsafe(32),
            is_active=False,
        )
        db_session.add(project)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await _get_user_and_project(ctx, project.slug)

    async def test_invalid_token_rejected(self, db_session, mock_factory):
        ctx = _make_ctx("bad-token")
        mock_verify_none = AsyncMock(return_value=None)
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify_none),
            pytest.raises(ValueError, match="Nieprawidlowy lub nieaktywny token"),
        ):
            await _get_user_and_project(ctx, "any-slug")


# ---------------------------------------------------------------------------
# list_tickets
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTickets:
    async def test_empty_list(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug)
        # Ostatni element to _meta
        assert len(result) == 1
        assert result[0]["_meta"]["total"] == 0

    async def test_returns_tickets(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(
            project_id=mcp_project.id,
            number=1,
            title="Test ticket",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug)
        # 1 ticket + _meta
        assert len(result) == 2
        assert result[0]["title"] == "Test ticket"
        assert result[0]["status"] == "backlog"
        assert result[1]["_meta"]["total"] == 1

    async def test_filter_by_status(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        for i, status in enumerate(("backlog", "todo", "done"), start=1):
            db_session.add(
                Ticket(
                    project_id=mcp_project.id,
                    number=i,
                    title=f"Ticket {status}",
                    status=status,
                )
            )
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug, status="done")
        assert result[-1]["_meta"]["total"] == 1
        assert result[0]["status"] == "done"

    async def test_filter_by_priority(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="High", priority="high"))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Low", priority="low"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug, priority="high")
        assert result[-1]["_meta"]["total"] == 1
        assert result[0]["priority"] == "high"

    async def test_filter_by_search(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Login bug"))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Dashboard feature"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug, search="Login")
        assert result[-1]["_meta"]["total"] == 1
        assert "Login" in result[0]["title"]

    async def test_filter_by_sprint(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Sprint 1",
            start_date=date(2026, 3, 1),
        )
        db_session.add(sprint)
        await db_session.flush()

        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="In sprint", sprint_id=sprint.id))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="No sprint"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug, sprint_id=str(sprint.id))
        assert result[-1]["_meta"]["total"] == 1
        assert result[0]["title"] == "In sprint"

    async def test_invalid_status_ignored(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy status jest ignorowany -- zwraca wszystkie tickety."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Any ticket"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug, status="invalid_status")
        assert result[-1]["_meta"]["total"] == 1


# ---------------------------------------------------------------------------
# get_ticket
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTicket:
    async def test_returns_ticket_details(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(
            project_id=mcp_project.id,
            number=1,
            title="Detail ticket",
            description="Some description",
            status="todo",
            priority="high",
            story_points=5,
        )
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_ticket(ctx, mcp_project.slug, str(ticket.id))
        assert result["title"] == "Detail ticket"
        assert result["description"] == "Some description"
        assert result["status"] == "todo"
        assert result["priority"] == "high"
        assert result["story_points"] == 5
        assert result["comments"] == []

    async def test_ticket_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Ticket nie istnieje"),
        ):
            await get_ticket(ctx, mcp_project.slug, str(uuid.uuid4()))

    async def test_ticket_with_comments(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Commented ticket")
        db_session.add(ticket)
        await db_session.flush()

        comment = TicketComment(
            ticket_id=ticket.id,
            user_id=mcp_user.id,
            content="A comment",
        )
        db_session.add(comment)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_ticket(ctx, mcp_project.slug, str(ticket.id))
        assert len(result["comments"]) == 1
        assert result["comments"][0]["content"] == "A comment"
        assert result["comments"][0]["author"] == mcp_user.email

    async def test_ticket_with_assignee(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(
            project_id=mcp_project.id,
            number=1,
            title="Assigned ticket",
            assignee_id=mcp_user.id,
        )
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_ticket(ctx, mcp_project.slug, str(ticket.id))
        assert result["assignee"] == mcp_user.email


# ---------------------------------------------------------------------------
# create_ticket
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTicket:
    async def test_create_basic(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(ctx, mcp_project.slug, "New ticket")
        assert result["title"] == "New ticket"
        assert result["status"] == "backlog"
        assert result["created_via_ai"] is True
        assert "id" in result

    async def test_create_with_all_fields(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Sprint for create",
            start_date=date(2026, 3, 1),
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(
                ctx,
                mcp_project.slug,
                "Full ticket",
                description="Description",
                priority="high",
                story_points=8,
                sprint_id=str(sprint.id),
                assignee_email=mcp_user.email,
            )
        assert result["title"] == "Full ticket"
        # Ticket z sprint_id dostaje status "todo"
        assert result["status"] == "todo"

    async def test_create_empty_title_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Tytul jest wymagany"),
        ):
            await create_ticket(ctx, mcp_project.slug, "   ")

    async def test_create_invalid_priority_defaults_to_medium(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(ctx, mcp_project.slug, "Bad priority", priority="invalid")
        # Sprawdzamy ze ticket zostal utworzony (invalid priority -> medium)
        assert "id" in result

    async def test_create_strips_whitespace(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(ctx, mcp_project.slug, "  Trimmed  ", description="  Desc  ")
        assert result["title"] == "Trimmed"

    async def test_create_without_sprint_gets_backlog_status(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(ctx, mcp_project.slug, "No sprint ticket")
        assert result["status"] == "backlog"

    async def test_create_nonexistent_assignee_ignored(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieistniejacy email assignee nie powoduje bledu -- assignee_id = None."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(
                ctx,
                mcp_project.slug,
                "No assignee",
                assignee_email="nobody@example.com",
            )
        assert "id" in result


# ---------------------------------------------------------------------------
# update_ticket
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateTicket:
    async def test_update_title(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Old title")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_ticket(ctx, mcp_project.slug, str(ticket.id), title="New title")
        assert result["title"] == "New title"

    async def test_update_status(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Status ticket", status="backlog")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_ticket(ctx, mcp_project.slug, str(ticket.id), status="in_progress")
        assert result["status"] == "in_progress"

    async def test_update_ticket_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Ticket nie istnieje"),
        ):
            await update_ticket(ctx, mcp_project.slug, str(uuid.uuid4()), title="X")

    async def test_update_empty_title_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Keep me")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Tytul nie moze byc pusty"),
        ):
            await update_ticket(ctx, mcp_project.slug, str(ticket.id), title="   ")

    async def test_update_invalid_status_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Status err")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy status"),
        ):
            await update_ticket(ctx, mcp_project.slug, str(ticket.id), status="nonexistent")

    async def test_update_invalid_priority_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Priority err")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy priorytet"),
        ):
            await update_ticket(ctx, mcp_project.slug, str(ticket.id), priority="ultra")

    async def test_update_clear_sprint(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """sprint_id="" czyści sprint z ticketa."""
        sprint = Sprint(project_id=mcp_project.id, name="S1", start_date=date(2026, 3, 1))
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(project_id=mcp_project.id, number=1, title="In sprint", sprint_id=sprint.id)
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_ticket(ctx, mcp_project.slug, str(ticket.id), sprint_id="")
        assert "id" in result

    async def test_update_clear_assignee(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """assignee_email="" czyści assignee."""
        ticket = Ticket(
            project_id=mcp_project.id,
            number=1,
            title="Assigned",
            assignee_id=mcp_user.id,
        )
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_ticket(ctx, mcp_project.slug, str(ticket.id), assignee_email="")
        assert "id" in result

    async def test_update_story_points(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="SP ticket")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_ticket(ctx, mcp_project.slug, str(ticket.id), story_points=13)
        assert result["message"].endswith("zaktualizowany")


# ---------------------------------------------------------------------------
# delete_ticket
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteTicket:
    async def test_delete_existing(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Delete me")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await delete_ticket(ctx, mcp_project.slug, str(ticket.id))
        assert "Delete me" in result["message"]
        assert "usuniety" in result["message"]

    async def test_delete_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Ticket nie istnieje"),
        ):
            await delete_ticket(ctx, mcp_project.slug, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# list_sprints
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListSprints:
    async def test_empty_list(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_sprints(ctx, mcp_project.slug)
        assert result == []

    async def test_returns_sprints_with_stats(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Sprint 1",
            start_date=date(2026, 3, 1),
            goal="Cel sprintu",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=mcp_project.id,
            number=1,
            title="Sprint ticket",
            sprint_id=sprint.id,
            story_points=5,
        )
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_sprints(ctx, mcp_project.slug)
        assert len(result) == 1
        assert result[0]["name"] == "Sprint 1"
        assert result[0]["goal"] == "Cel sprintu"
        assert result[0]["ticket_count"] == 1
        assert result[0]["story_points_total"] == 5
        assert result[0]["status"] == "planning"

    async def test_filter_by_status(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        for name, status in [("S1", "planning"), ("S2", "active"), ("S3", "completed")]:
            s = Sprint(
                project_id=mcp_project.id,
                name=name,
                start_date=date(2026, 3, 1),
                status=status,
            )
            db_session.add(s)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_sprints(ctx, mcp_project.slug, status="active")
        assert len(result) == 1
        assert result[0]["name"] == "S2"


# ---------------------------------------------------------------------------
# get_sprint
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSprint:
    async def test_returns_sprint_with_tickets(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Detail sprint",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 14),
            goal="Sprint goal",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=mcp_project.id,
            number=1,
            title="Sprint ticket",
            sprint_id=sprint.id,
            priority="high",
            story_points=3,
        )
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_sprint(ctx, mcp_project.slug, str(sprint.id))
        assert result["name"] == "Detail sprint"
        assert result["goal"] == "Sprint goal"
        assert result["end_date"] == "2026-03-14"
        assert len(result["tickets"]) == 1
        assert result["tickets"][0]["title"] == "Sprint ticket"

    async def test_sprint_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Sprint nie istnieje"),
        ):
            await get_sprint(ctx, mcp_project.slug, str(uuid.uuid4()))

    async def test_sprint_no_end_date(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="No end",
            start_date=date(2026, 3, 1),
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_sprint(ctx, mcp_project.slug, str(sprint.id))
        assert result["end_date"] is None


# ---------------------------------------------------------------------------
# create_sprint
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateSprint:
    async def test_create_basic(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_sprint(ctx, mcp_project.slug, "New Sprint", "2026-04-01")
        assert result["name"] == "New Sprint"
        assert result["status"] == "planning"
        assert "id" in result

    async def test_create_with_goal_and_end_date(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_sprint(
                ctx,
                mcp_project.slug,
                "Goal Sprint",
                "2026-04-01",
                goal="Deliver feature X",
                end_date="2026-04-14",
            )
        assert result["name"] == "Goal Sprint"

    async def test_create_empty_name_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nazwa sprintu jest wymagana"),
        ):
            await create_sprint(ctx, mcp_project.slug, "   ", "2026-04-01")

    async def test_create_invalid_date_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError),
        ):
            await create_sprint(ctx, mcp_project.slug, "Bad date", "not-a-date")


# ---------------------------------------------------------------------------
# start_sprint
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartSprint:
    async def test_start_planning_sprint(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Start me",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await start_sprint(ctx, mcp_project.slug, str(sprint.id))
        assert result["message"] == "Sprint rozpoczety"
        assert result["sprint_id"] == str(sprint.id)

    async def test_start_nonexistent_sprint(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Sprint nie istnieje"),
        ):
            await start_sprint(ctx, mcp_project.slug, str(uuid.uuid4()))

    async def test_start_already_active_sprint_blocked(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nie mozna wystartowac sprintu gdy juz jest aktywny sprint."""
        active = Sprint(
            project_id=mcp_project.id,
            name="Active",
            start_date=date(2026, 3, 1),
            status="active",
        )
        planning = Sprint(
            project_id=mcp_project.id,
            name="Planning",
            start_date=date(2026, 4, 1),
            status="planning",
        )
        db_session.add_all([active, planning])
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="aktywny sprint"),
        ):
            await start_sprint(ctx, mcp_project.slug, str(planning.id))

    async def test_start_completed_sprint_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Done",
            start_date=date(2026, 3, 1),
            status="completed",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="planowania"),
        ):
            await start_sprint(ctx, mcp_project.slug, str(sprint.id))


# ---------------------------------------------------------------------------
# complete_sprint
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompleteSprint:
    async def test_complete_active_sprint(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Complete me",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await complete_sprint(ctx, mcp_project.slug, str(sprint.id))
        assert result["message"] == "Sprint zakonczony"

    async def test_complete_planning_sprint_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Still planning",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="aktywny sprint"),
        ):
            await complete_sprint(ctx, mcp_project.slug, str(sprint.id))

    async def test_complete_nonexistent_sprint(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Sprint nie istnieje"),
        ):
            await complete_sprint(ctx, mcp_project.slug, str(uuid.uuid4()))

    async def test_complete_moves_undone_tickets_to_backlog(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Zakonczone tickety (done) zostaja, reszta wraca do backloga."""
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Moving sprint",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        done_ticket = Ticket(
            project_id=mcp_project.id,
            number=1,
            title="Done ticket",
            sprint_id=sprint.id,
            status="done",
        )
        undone_ticket = Ticket(
            project_id=mcp_project.id,
            number=2,
            title="Undone ticket",
            sprint_id=sprint.id,
            status="in_progress",
        )
        db_session.add_all([done_ticket, undone_ticket])
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            await complete_sprint(ctx, mcp_project.slug, str(sprint.id))

        # Refresh to see the changes
        await db_session.refresh(done_ticket)
        await db_session.refresh(undone_ticket)
        # done ticket stays in sprint
        assert done_ticket.sprint_id == sprint.id
        assert done_ticket.status == "done"
        # undone ticket moved to backlog
        assert undone_ticket.sprint_id is None
        assert undone_ticket.status == "backlog"


# ---------------------------------------------------------------------------
# list_comments
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListComments:
    async def test_empty_comments(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="No comments")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_comments(ctx, mcp_project.slug, str(ticket.id))
        assert result == []

    async def test_returns_comments(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="With comments")
        db_session.add(ticket)
        await db_session.flush()

        comment = TicketComment(
            ticket_id=ticket.id,
            user_id=mcp_user.id,
            content="Hello",
            created_via_ai=True,
        )
        db_session.add(comment)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_comments(ctx, mcp_project.slug, str(ticket.id))
        assert len(result) == 1
        assert result[0]["content"] == "Hello"
        assert result[0]["author"] == mcp_user.email
        assert result[0]["created_via_ai"] is True

    async def test_ticket_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Ticket nie istnieje"),
        ):
            await list_comments(ctx, mcp_project.slug, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAddComment:
    async def test_add_comment(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Comment target")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await add_comment(ctx, mcp_project.slug, str(ticket.id), "My comment")
        assert result["message"] == "Komentarz dodany"
        assert result["created_via_ai"] is True
        assert "id" in result

    async def test_add_empty_comment_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Empty comment target")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Tresc komentarza nie moze byc pusta"),
        ):
            await add_comment(ctx, mcp_project.slug, str(ticket.id), "   ")

    async def test_add_comment_ticket_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Ticket nie istnieje"),
        ):
            await add_comment(ctx, mcp_project.slug, str(uuid.uuid4()), "Lost comment")

    async def test_add_comment_strips_whitespace(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Strip target")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await add_comment(ctx, mcp_project.slug, str(ticket.id), "  Trimmed content  ")
        assert "id" in result


# ---------------------------------------------------------------------------
# list_projects
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListProjects:
    async def test_returns_user_projects(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_projects(ctx)
        assert len(result) == 1
        assert result[0]["name"] == mcp_project.name
        assert result[0]["slug"] == mcp_project.slug
        assert result[0]["role"] == "owner"

    async def test_excludes_inactive_projects(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        _slug = f"inactive-{uuid.uuid4().hex[:8]}"
        inactive = Project(
            name="Inactive",
            slug=_slug,
            code=_slug.replace("-", "").upper()[:5],
            api_key=secrets.token_urlsafe(32),
            is_active=False,
        )
        db_session.add(inactive)
        await db_session.flush()
        db_session.add(ProjectMember(project_id=inactive.id, user_id=mcp_user.id, role="member"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_projects(ctx)
        slugs = [p["slug"] for p in result]
        assert inactive.slug not in slugs


# ---------------------------------------------------------------------------
# 500ki: list_issues, get_issue, update_issue_status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListIssues:
    async def test_empty_list(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_issues(ctx, mcp_project.slug)
        assert len(result) == 1
        assert result[0]["_meta"]["total"] == 0

    async def test_returns_unresolved_by_default(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        db_session.add(Issue(project_id=mcp_project.id, title="Bug 1", fingerprint="fp1", status="unresolved"))
        db_session.add(Issue(project_id=mcp_project.id, title="Bug 2", fingerprint="fp2", status="resolved"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_issues(ctx, mcp_project.slug)
        assert result[-1]["_meta"]["total"] == 1
        assert result[0]["status"] == "unresolved"

    async def test_filter_resolved(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        db_session.add(Issue(project_id=mcp_project.id, title="Bug", fingerprint="fp1", status="unresolved"))
        db_session.add(Issue(project_id=mcp_project.id, title="Fixed", fingerprint="fp2", status="resolved"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_issues(ctx, mcp_project.slug, status="resolved")
        assert result[-1]["_meta"]["total"] == 1
        assert result[0]["title"] == "Fixed"

    async def test_search_filter(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        db_session.add(Issue(project_id=mcp_project.id, title="ValueError in views", fingerprint="fp1"))
        db_session.add(Issue(project_id=mcp_project.id, title="TypeError in models", fingerprint="fp2"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_issues(ctx, mcp_project.slug, search="ValueError")
        assert result[-1]["_meta"]["total"] == 1
        assert "ValueError" in result[0]["title"]


@pytest.mark.unit
class TestGetIssue:
    async def test_returns_issue_with_events(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        issue = Issue(project_id=mcp_project.id, title="Test bug", fingerprint="fp1", status="unresolved", event_count=1)
        db_session.add(issue)
        await db_session.flush()

        event = Event(issue_id=issue.id, timestamp=datetime.now(UTC), exception={"type": "ValueError"})
        db_session.add(event)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_issue(ctx, mcp_project.slug, str(issue.id))
        assert result["title"] == "Test bug"
        assert result["status"] == "unresolved"
        assert len(result["events"]) == 1
        assert result["events"][0]["exception"] == {"type": "ValueError"}

    async def test_issue_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Issue nie istnieje"),
        ):
            await get_issue(ctx, mcp_project.slug, str(uuid.uuid4()))


@pytest.mark.unit
class TestUpdateIssueStatus:
    async def test_resolve_issue(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        issue = Issue(project_id=mcp_project.id, title="To resolve", fingerprint="fp1", status="unresolved")
        db_session.add(issue)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_issue_status(ctx, mcp_project.slug, str(issue.id), "resolved")
        assert result["status"] == "resolved"

    async def test_invalid_status_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        issue = Issue(project_id=mcp_project.id, title="Bad status", fingerprint="fp1")
        db_session.add(issue)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Status musi byc"),
        ):
            await update_issue_status(ctx, mcp_project.slug, str(issue.id), "invalid")


# ---------------------------------------------------------------------------
# Monitoring: list_monitors, get_monitor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListMonitors:
    async def test_empty_list(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_monitors(ctx, mcp_project.slug)
        assert result == []

    async def test_returns_monitors(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        monitor = Monitor(
            project_id=mcp_project.id,
            url="https://example.com",
            name="Example",
            interval_value=5,
            interval_unit="minutes",
        )
        db_session.add(monitor)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_monitors(ctx, mcp_project.slug)
        assert len(result) == 1
        assert result[0]["name"] == "Example"
        assert result[0]["url"] == "https://example.com"
        assert result[0]["is_active"] is True

    async def test_monitor_with_checks(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        monitor = Monitor(
            project_id=mcp_project.id,
            url="https://test.com",
            interval_value=1,
            interval_unit="minutes",
        )
        db_session.add(monitor)
        await db_session.flush()

        check = MonitorCheck(
            monitor_id=monitor.id,
            is_success=True,
            status_code=200,
            response_time_ms=150,
        )
        db_session.add(check)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_monitors(ctx, mcp_project.slug)
        assert result[0]["last_check"]["is_success"] is True
        assert result[0]["last_check"]["status_code"] == 200


@pytest.mark.unit
class TestGetMonitor:
    async def test_returns_monitor_with_checks(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        monitor = Monitor(
            project_id=mcp_project.id,
            url="https://example.com",
            name="Detail",
            interval_value=5,
            interval_unit="minutes",
        )
        db_session.add(monitor)
        await db_session.flush()

        check = MonitorCheck(monitor_id=monitor.id, is_success=True, status_code=200, response_time_ms=100)
        db_session.add(check)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_monitor(ctx, mcp_project.slug, str(monitor.id))
        assert result["name"] == "Detail"
        assert result["url"] == "https://example.com"
        assert len(result["checks"]) == 1

    async def test_monitor_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Monitor nie istnieje"),
        ):
            await get_monitor(ctx, mcp_project.slug, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# get_board
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBoard:
    async def test_no_active_sprint(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_board(ctx, mcp_project.slug)
        assert result["message"] == "Brak aktywnego sprintu"
        assert result["columns"] == {}

    async def test_returns_board_with_tickets(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Active sprint",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        for i, status in enumerate(("todo", "in_progress", "done"), start=1):
            db_session.add(Ticket(project_id=mcp_project.id, number=i, title=f"Ticket {status}", sprint_id=sprint.id, status=status))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_board(ctx, mcp_project.slug)
        assert result["sprint"]["name"] == "Active sprint"
        assert len(result["columns"]["todo"]) == 1
        assert len(result["columns"]["in_progress"]) == 1
        assert len(result["columns"]["done"]) == 1


# ---------------------------------------------------------------------------
# get_project_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProjectSummary:
    async def test_empty_project(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_project_summary(ctx, mcp_project.slug)
        assert result["project"]["slug"] == mcp_project.slug
        assert result["issues_unresolved"] == 0
        assert result["monitors_failing"] == 0
        assert result["uptime_24h"] is None
        assert result["active_sprint"] is None
        assert result["backlog_count"] == 0

    async def test_counts_unresolved_issues(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        db_session.add(Issue(project_id=mcp_project.id, title="Open 1", fingerprint="fp1", status="unresolved"))
        db_session.add(Issue(project_id=mcp_project.id, title="Open 2", fingerprint="fp2", status="unresolved"))
        db_session.add(Issue(project_id=mcp_project.id, title="Fixed", fingerprint="fp3", status="resolved"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_project_summary(ctx, mcp_project.slug)
        assert result["issues_unresolved"] == 2

    async def test_counts_backlog_tickets(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Backlog 1", status="backlog"))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Backlog 2", status="backlog"))
        db_session.add(Ticket(project_id=mcp_project.id, number=3, title="In progress", status="in_progress"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_project_summary(ctx, mcp_project.slug)
        assert result["backlog_count"] == 2


# ---------------------------------------------------------------------------
# log_time
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogTime:
    async def test_log_time_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Time ticket")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await log_time(ctx, mcp_project.slug, str(ticket.id), 90, "2026-02-20", "Praca dev")
        assert result["duration_minutes"] == 90
        assert result["created_via_ai"] is True
        assert result["description"] == "Praca dev"
        assert "id" in result

    async def test_log_time_zero_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="wiekszy niz 0"),
        ):
            await log_time(ctx, mcp_project.slug, str(uuid.uuid4()), 0, "2026-02-20")

    async def test_log_time_negative_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="wiekszy niz 0"),
        ):
            await log_time(ctx, mcp_project.slug, str(uuid.uuid4()), -10, "2026-02-20")

    async def test_log_time_ticket_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Ticket nie istnieje"),
        ):
            await log_time(ctx, mcp_project.slug, str(uuid.uuid4()), 60, "2026-02-20")

    async def test_log_time_no_description(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = Ticket(project_id=mcp_project.id, number=1, title="No desc ticket")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await log_time(ctx, mcp_project.slug, str(ticket.id), 30, "2026-02-20")
        assert result["description"] is None


# ---------------------------------------------------------------------------
# Wiki: list_wiki_pages, get_wiki_page, create, update, delete, search
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListWikiPages:
    async def test_empty_wiki(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await mcp_list_wiki_pages(ctx, mcp_project.slug)
        assert result == []

    async def test_returns_pages(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        page = WikiPage(
            project_id=mcp_project.id,
            title="Strona testowa",
            slug="strona-testowa",
            position=0,
            minio_path=f"{mcp_project.slug}/{uuid.uuid4()}.md",
            created_by_id=mcp_user.id,
            last_edited_by_id=mcp_user.id,
        )
        db_session.add(page)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await mcp_list_wiki_pages(ctx, mcp_project.slug)
        assert len(result) == 1
        assert result[0]["title"] == "Strona testowa"
        assert result[0]["depth"] == 0
        assert result[0]["created_by"] == mcp_user.email

    async def test_returns_nested_pages(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        parent = WikiPage(
            project_id=mcp_project.id,
            title="Parent",
            slug="parent",
            position=0,
            minio_path=f"{mcp_project.slug}/{uuid.uuid4()}.md",
            created_by_id=mcp_user.id,
            last_edited_by_id=mcp_user.id,
        )
        db_session.add(parent)
        await db_session.flush()

        child = WikiPage(
            project_id=mcp_project.id,
            title="Child",
            slug="child",
            position=0,
            parent_id=parent.id,
            minio_path=f"{mcp_project.slug}/{uuid.uuid4()}.md",
            created_by_id=mcp_user.id,
            last_edited_by_id=mcp_user.id,
        )
        db_session.add(child)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await mcp_list_wiki_pages(ctx, mcp_project.slug)
        assert len(result) == 2
        assert result[0]["title"] == "Parent"
        assert result[0]["depth"] == 0
        assert result[1]["title"] == "Child"
        assert result[1]["depth"] == 1
        assert result[1]["parent_id"] == str(parent.id)


@pytest.mark.unit
class TestGetWikiPage:
    async def test_returns_page_with_content(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        page = WikiPage(
            project_id=mcp_project.id,
            title="Detail page",
            slug="detail-page",
            position=0,
            minio_path=f"{mcp_project.slug}/{uuid.uuid4()}.md",
            created_by_id=mcp_user.id,
            last_edited_by_id=mcp_user.id,
        )
        db_session.add(page)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.get_page_content", return_value="# Hello World"),
        ):
            result = await mcp_get_wiki_page(ctx, mcp_project.slug, str(page.id))
        assert result["title"] == "Detail page"
        assert result["content"] == "# Hello World"
        assert result["created_by"] == mcp_user.email

    async def test_page_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Strona wiki nie istnieje"),
        ):
            await mcp_get_wiki_page(ctx, mcp_project.slug, str(uuid.uuid4()))


@pytest.mark.unit
class TestCreateWikiPage:
    async def test_create_page(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.services.wiki.upload_markdown", return_value=f"{mcp_project.slug}/test.md"),
            patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock),
        ):
            result = await mcp_create_wiki_page(ctx, mcp_project.slug, "Nowa strona", "Tresc strony")
        assert result["title"] == "Nowa strona"
        assert result["is_ai_touched"] is True
        assert "id" in result

    async def test_create_page_empty_title_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Tytul jest wymagany"),
        ):
            await mcp_create_wiki_page(ctx, mcp_project.slug, "   ", "content")

    async def test_create_child_page(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        parent = WikiPage(
            project_id=mcp_project.id,
            title="Parent page",
            slug="parent-page",
            position=0,
            minio_path=f"{mcp_project.slug}/{uuid.uuid4()}.md",
            created_by_id=mcp_user.id,
            last_edited_by_id=mcp_user.id,
        )
        db_session.add(parent)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.services.wiki.upload_markdown", return_value=f"{mcp_project.slug}/child.md"),
            patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock),
        ):
            result = await mcp_create_wiki_page(ctx, mcp_project.slug, "Child page", "content", parent_id=str(parent.id))
        assert result["title"] == "Child page"


@pytest.mark.unit
class TestUpdateWikiPage:
    async def test_update_title_and_content(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        page = WikiPage(
            project_id=mcp_project.id,
            title="Old title",
            slug="old-title",
            position=0,
            minio_path=f"{mcp_project.slug}/{uuid.uuid4()}.md",
            created_by_id=mcp_user.id,
            last_edited_by_id=mcp_user.id,
        )
        db_session.add(page)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.services.wiki.upload_markdown", return_value=f"{mcp_project.slug}/updated.md"),
            patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock),
        ):
            result = await mcp_update_wiki_page(ctx, mcp_project.slug, str(page.id), title="New title", content="New content")
        assert result["title"] == "New title"
        assert result["is_ai_touched"] is True

    async def test_update_page_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Strona wiki nie istnieje"),
        ):
            await mcp_update_wiki_page(ctx, mcp_project.slug, str(uuid.uuid4()), title="X")

    async def test_update_position_only(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        page = WikiPage(
            project_id=mcp_project.id,
            title="Position page",
            slug="position-page",
            position=0,
            minio_path=f"{mcp_project.slug}/{uuid.uuid4()}.md",
            created_by_id=mcp_user.id,
            last_edited_by_id=mcp_user.id,
        )
        db_session.add(page)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await mcp_update_wiki_page(ctx, mcp_project.slug, str(page.id), position=5)
        assert result["title"] == "Position page"


@pytest.mark.unit
class TestDeleteWikiPage:
    async def test_delete_page(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        page = WikiPage(
            project_id=mcp_project.id,
            title="Delete me",
            slug="delete-me",
            position=0,
            minio_path=f"{mcp_project.slug}/{uuid.uuid4()}.md",
            created_by_id=mcp_user.id,
            last_edited_by_id=mcp_user.id,
        )
        db_session.add(page)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.services.wiki.delete_object"),
        ):
            result = await mcp_delete_wiki_page(ctx, mcp_project.slug, str(page.id))
        assert "Delete me" in result["message"]

    async def test_delete_page_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Strona wiki nie istnieje"),
        ):
            await mcp_delete_wiki_page(ctx, mcp_project.slug, str(uuid.uuid4()))


@pytest.mark.unit
class TestSearchWiki:
    async def test_search_returns_results(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        mock_results = [{"id": str(uuid.uuid4()), "title": "Test page", "slug": "test", "snippet": "fragment", "similarity": 0.85}]
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.services.embeddings.search_wiki_pages", new_callable=AsyncMock, return_value=mock_results),
        ):
            result = await mcp_search_wiki(ctx, mcp_project.slug, "test query")
        assert len(result) == 1
        assert result[0]["title"] == "Test page"

    async def test_search_empty_results(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.services.embeddings.search_wiki_pages", new_callable=AsyncMock, return_value=[]),
        ):
            result = await mcp_search_wiki(ctx, mcp_project.slug, "nonexistent")
        assert result == []

    async def test_search_with_limit(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.services.embeddings.search_wiki_pages", new_callable=AsyncMock, return_value=[]) as mock_search,
        ):
            await mcp_search_wiki(ctx, mcp_project.slug, "query", limit=5)
        mock_search.assert_called_once()
        call_args = mock_search.call_args
        assert call_args.kwargs.get("limit") == 5


# ---------------------------------------------------------------------------
# _auth: OAuth access token path (line 111)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAuthOAuth:
    """Testy sciezki OAuth w _auth (verify_oauth_access_token)."""

    async def test_auth_oauth_token_success(self, mcp_user, mock_factory):
        """Jesli verify_oauth_access_token zwraca usera, _auth zwraca go od razu."""
        ctx = _make_ctx("oauth-access-token")
        mock_verify_oauth = AsyncMock(return_value=mcp_user)
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_oauth_access_token", mock_verify_oauth, create=True),
            patch("monolynx.services.oauth.verify_oauth_access_token", mock_verify_oauth),
        ):
            user = await _auth(ctx)
        assert user.id == mcp_user.id

    async def test_auth_oauth_returns_none_falls_back_to_legacy(self, mcp_user, mock_factory, mock_verify):
        """Jesli OAuth zwraca None, fallback na verify_mcp_token."""
        ctx = _make_ctx("legacy-token")
        mock_verify_oauth = AsyncMock(return_value=None)
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.services.oauth.verify_oauth_access_token", mock_verify_oauth),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            user = await _auth(ctx)
        assert user.id == mcp_user.id

    async def test_auth_both_return_none_raises(self, mock_factory):
        """Jesli oba OAuth i legacy zwracaja None, rzuca ValueError."""
        ctx = _make_ctx("bad-token")
        mock_verify_oauth = AsyncMock(return_value=None)
        mock_verify_legacy = AsyncMock(return_value=None)
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.services.oauth.verify_oauth_access_token", mock_verify_oauth),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify_legacy),
            pytest.raises(ValueError, match="Nieprawidlowy lub nieaktywny token"),
        ):
            await _auth(ctx)


# ---------------------------------------------------------------------------
# _get_auth_header edge cases (lines 62-65)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAuthHeaderEdgeCases:
    """Dodatkowe testy _get_auth_header -- puste Authorization, brak naglowka."""

    async def test_no_authorization_header(self):
        """Headers bez klucza 'authorization' -- zwraca pusty string, brak Bearer prefix."""
        ctx = MagicMock()
        ctx.request_context = MagicMock()
        ctx.request_context.request = MagicMock()
        ctx.request_context.request.headers = {}
        with pytest.raises(ValueError, match="Brak tokenu Bearer"):
            await _get_auth_header(ctx)

    async def test_empty_authorization_header(self):
        """Pusty naglowek authorization."""
        ctx = MagicMock()
        ctx.request_context = MagicMock()
        ctx.request_context.request = MagicMock()
        ctx.request_context.request.headers = {"authorization": ""}
        with pytest.raises(ValueError, match="Brak tokenu Bearer"):
            await _get_auth_header(ctx)

    async def test_non_bearer_scheme(self):
        """Inny scheme niz Bearer (np. Token, Basic)."""
        ctx = MagicMock()
        ctx.request_context = MagicMock()
        ctx.request_context.request = MagicMock()
        ctx.request_context.request.headers = {"authorization": "Token abc123"}
        with pytest.raises(ValueError, match="Brak tokenu Bearer"):
            await _get_auth_header(ctx)


# ---------------------------------------------------------------------------
# update_issue_status: issue nie istnieje (line 309)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateIssueStatusNotFound:
    async def test_update_nonexistent_issue(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Issue nie istnieje"),
        ):
            await update_issue_status(ctx, mcp_project.slug, str(uuid.uuid4()), "resolved")


# ---------------------------------------------------------------------------
# create_ticket_from_issue (lines 884-997)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTicketFromIssue:
    """Testy narzedzia create_ticket_from_issue."""

    async def test_create_ticket_from_issue_basic(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Podstawowe tworzenie ticketa z issue bez eventow."""
        from monolynx.mcp_server import create_ticket_from_issue

        issue = Issue(
            project_id=mcp_project.id,
            title="ValueError: invalid literal",
            fingerprint="fp-create-1",
            status="unresolved",
            event_count=3,
        )
        db_session.add(issue)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket_from_issue(ctx, mcp_project.slug, str(issue.id))
        assert "ticket_id" in result
        assert "ticket_key" in result
        assert result["url"].startswith("/dashboard/")

    async def test_create_ticket_from_issue_with_event_traceback(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Ticket z issue ktory ma event z traceback -- sprawdza generowanie opisu."""
        from monolynx.mcp_server import create_ticket_from_issue

        issue = Issue(
            project_id=mcp_project.id,
            title="TypeError: 'NoneType' has no len",
            fingerprint="fp-create-2",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime.now(UTC),
            exception={
                "type": "TypeError",
                "value": "'NoneType' has no len",
                "stacktrace": {
                    "frames": [
                        {
                            "filename": "app/views.py",
                            "function": "get_list",
                            "lineno": 42,
                            "context_line": "return len(result)",
                        },
                        {
                            "filename": "app/models.py",
                            "function": "query",
                            "lineno": 10,
                        },
                    ]
                },
            },
            request_data={"url": "https://example.com/api", "method": "GET"},
            environment={"environment": "production"},
        )
        db_session.add(event)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket_from_issue(ctx, mcp_project.slug, str(issue.id))
        assert "ticket_id" in result

    async def test_create_ticket_from_issue_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Issue nie istnieje -- ValueError."""
        from monolynx.mcp_server import create_ticket_from_issue

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Issue nie istnieje"),
        ):
            await create_ticket_from_issue(ctx, mcp_project.slug, str(uuid.uuid4()))

    async def test_create_ticket_from_issue_already_has_ticket(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Issue z powiazanym ticketem -- ValueError."""
        from monolynx.mcp_server import create_ticket_from_issue

        issue = Issue(
            project_id=mcp_project.id,
            title="Existing issue",
            fingerprint="fp-create-3",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        ticket = Ticket(
            project_id=mcp_project.id,
            number=99,
            title="Existing ticket",
            issue_id=issue.id,
        )
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="already has a linked ticket"),
        ):
            await create_ticket_from_issue(ctx, mcp_project.slug, str(issue.id))

    async def test_create_ticket_from_issue_with_sprint(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Ticket tworzony z sprint_id dostaje status 'todo'."""
        from monolynx.mcp_server import create_ticket_from_issue

        issue = Issue(
            project_id=mcp_project.id,
            title="Sprint issue",
            fingerprint="fp-create-4",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        sprint = Sprint(
            project_id=mcp_project.id,
            name="Sprint for issue",
            start_date=date(2026, 3, 1),
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket_from_issue(
                ctx,
                mcp_project.slug,
                str(issue.id),
                sprint_id=str(sprint.id),
                priority="high",
                story_points=5,
            )
        assert "ticket_id" in result

    async def test_create_ticket_from_issue_invalid_priority_defaults(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy priority zamienia sie na medium."""
        from monolynx.mcp_server import create_ticket_from_issue

        issue = Issue(
            project_id=mcp_project.id,
            title="Priority issue",
            fingerprint="fp-create-5",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket_from_issue(
                ctx,
                mcp_project.slug,
                str(issue.id),
                priority="ultra",
            )
        assert "ticket_id" in result

    async def test_create_ticket_from_issue_with_traceback_key(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Event z exc_data ktory ma 'traceback' zamiast 'stacktrace'."""
        from monolynx.mcp_server import create_ticket_from_issue

        issue = Issue(
            project_id=mcp_project.id,
            title="Traceback key issue",
            fingerprint="fp-create-6",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime.now(UTC),
            exception={
                "traceback": "Traceback (most recent call last):\n  File ...",
                "stacktrace": {},
            },
            request_data={"url": "https://test.com", "method": "POST"},
            environment={"hostname": "server-01"},
        )
        db_session.add(event)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket_from_issue(ctx, mcp_project.slug, str(issue.id))
        assert "ticket_id" in result

    async def test_create_ticket_from_issue_with_type_value_exc(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Event z exc_data z type/value bez stacktrace frames."""
        from monolynx.mcp_server import create_ticket_from_issue

        issue = Issue(
            project_id=mcp_project.id,
            title="Type value issue",
            fingerprint="fp-create-7",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime.now(UTC),
            exception={
                "type": "RuntimeError",
                "value": "Something broke",
            },
            request_data={},
            environment={},
        )
        db_session.add(event)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket_from_issue(ctx, mcp_project.slug, str(issue.id))
        assert "ticket_id" in result

    async def test_create_ticket_from_issue_long_title_truncated(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Ticket z dlugim tytulem jest obcinany do 512 znakow.

        Issue title = 510 znakow, po dodaniu prefiksu [500ki] (8 znakow)
        wynik = 518 > 512 -> obcinany do 509 + '...'
        """
        from monolynx.mcp_server import create_ticket_from_issue

        long_title = "X" * 510  # 510 chars fits in issue.title varchar(512)
        issue = Issue(
            project_id=mcp_project.id,
            title=long_title,
            fingerprint="fp-create-8",
            status="unresolved",
            event_count=1,
        )
        db_session.add(issue)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket_from_issue(ctx, mcp_project.slug, str(issue.id))
        assert "ticket_id" in result


# ---------------------------------------------------------------------------
# Graph tools (lines 1507-1793)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateGraphNode:
    """Testy create_graph_node."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import create_graph_node

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await create_graph_node(ctx, mcp_project.slug, "File", "test.py")

    async def test_invalid_node_type(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import create_graph_node

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Nieznany typ node'a"),
        ):
            mock_gs.is_enabled.return_value = True
            await create_graph_node(ctx, mcp_project.slug, "InvalidType", "test.py")

    async def test_create_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import create_graph_node

        ctx = _make_ctx()
        mock_node = {"id": "node-1", "type": "File", "name": "test.py"}
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_node = AsyncMock(return_value=mock_node)
            result = await create_graph_node(ctx, mcp_project.slug, "File", "test.py")
        assert result["name"] == "test.py"
        assert "message" in result


@pytest.mark.unit
class TestListGraphNodes:
    """Testy list_graph_nodes."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import list_graph_nodes

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await list_graph_nodes(ctx, mcp_project.slug)

    async def test_list_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import list_graph_nodes

        ctx = _make_ctx()
        mock_nodes = [{"id": "n1", "type": "File", "name": "a.py"}, {"id": "n2", "type": "Class", "name": "Foo"}]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.list_nodes = AsyncMock(return_value=mock_nodes)
            result = await list_graph_nodes(ctx, mcp_project.slug)
        assert len(result) == 2
        assert result[0]["name"] == "a.py"


@pytest.mark.unit
class TestGetGraphNode:
    """Testy get_graph_node."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import get_graph_node

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await get_graph_node(ctx, mcp_project.slug, "node-1")

    async def test_node_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import get_graph_node

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Node nie istnieje"),
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_node = AsyncMock(return_value=None)
            await get_graph_node(ctx, mcp_project.slug, "nonexistent")

    async def test_get_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import get_graph_node

        ctx = _make_ctx()
        mock_node = {"id": "node-1", "type": "File", "name": "main.py"}
        mock_neighbors = [{"id": "node-2", "type": "Class", "name": "App"}]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_node = AsyncMock(return_value=mock_node)
            mock_gs.get_neighbors = AsyncMock(return_value=mock_neighbors)
            result = await get_graph_node(ctx, mcp_project.slug, "node-1")
        assert result["name"] == "main.py"
        assert result["neighbors"] == mock_neighbors


@pytest.mark.unit
class TestDeleteGraphNode:
    """Testy delete_graph_node."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import delete_graph_node

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await delete_graph_node(ctx, mcp_project.slug, "node-1")

    async def test_node_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import delete_graph_node

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Node nie istnieje"),
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.delete_node = AsyncMock(return_value=False)
            await delete_graph_node(ctx, mcp_project.slug, "node-1")

    async def test_delete_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import delete_graph_node

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.delete_node = AsyncMock(return_value=True)
            result = await delete_graph_node(ctx, mcp_project.slug, "node-1")
        assert result["message"] == "Node usuniety"
        assert result["node_id"] == "node-1"


@pytest.mark.unit
class TestCreateGraphEdge:
    """Testy create_graph_edge."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import create_graph_edge

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await create_graph_edge(ctx, mcp_project.slug, "s1", "t1", "CALLS")

    async def test_invalid_edge_type(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import create_graph_edge

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Nieznany typ krawedzi"),
        ):
            mock_gs.is_enabled.return_value = True
            await create_graph_edge(ctx, mcp_project.slug, "s1", "t1", "INVALID_TYPE")

    async def test_nodes_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import create_graph_edge

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Nie znaleziono node'ow"),
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_edge = AsyncMock(return_value=None)
            await create_graph_edge(ctx, mcp_project.slug, "s1", "t1", "CALLS")

    async def test_create_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import create_graph_edge

        ctx = _make_ctx()
        mock_edge = {"source_id": "s1", "target_id": "t1", "type": "CALLS"}
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_edge = AsyncMock(return_value=mock_edge)
            result = await create_graph_edge(ctx, mcp_project.slug, "s1", "t1", "CALLS")
        assert result["type"] == "CALLS"
        assert "message" in result


@pytest.mark.unit
class TestDeleteGraphEdge:
    """Testy delete_graph_edge."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import delete_graph_edge

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await delete_graph_edge(ctx, mcp_project.slug, "s1", "t1", "CALLS")

    async def test_edge_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import delete_graph_edge

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Krawedz nie istnieje"),
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.delete_edge = AsyncMock(return_value=False)
            await delete_graph_edge(ctx, mcp_project.slug, "s1", "t1", "CALLS")

    async def test_delete_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import delete_graph_edge

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.delete_edge = AsyncMock(return_value=True)
            result = await delete_graph_edge(ctx, mcp_project.slug, "s1", "t1", "CALLS")
        assert result["message"] == "Krawedz usunieta"
        assert result["source_id"] == "s1"
        assert result["target_id"] == "t1"


@pytest.mark.unit
class TestQueryGraph:
    """Testy query_graph."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import query_graph

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await query_graph(ctx, mcp_project.slug)

    async def test_query_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import query_graph

        ctx = _make_ctx()
        mock_graph = {"nodes": [{"id": "n1"}], "edges": []}
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_graph = AsyncMock(return_value=mock_graph)
            result = await query_graph(ctx, mcp_project.slug)
        assert result["nodes"] == [{"id": "n1"}]


@pytest.mark.unit
class TestFindGraphPath:
    """Testy find_graph_path."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import find_graph_path

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await find_graph_path(ctx, mcp_project.slug, "s1", "t1")

    async def test_find_path_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import find_graph_path

        ctx = _make_ctx()
        mock_path = {"path": [{"id": "s1"}, {"id": "t1"}], "length": 1}
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.find_path = AsyncMock(return_value=mock_path)
            result = await find_graph_path(ctx, mcp_project.slug, "s1", "t1")
        assert result["length"] == 1


@pytest.mark.unit
class TestGetGraphStats:
    """Testy get_graph_stats."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import get_graph_stats

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await get_graph_stats(ctx, mcp_project.slug)

    async def test_stats_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import get_graph_stats

        ctx = _make_ctx()
        mock_stats = {"total_nodes": 10, "total_edges": 15, "nodes_by_type": {"File": 5}}
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_stats = AsyncMock(return_value=mock_stats)
            result = await get_graph_stats(ctx, mcp_project.slug)
        assert result["total_nodes"] == 10
        assert result["total_edges"] == 15


@pytest.mark.unit
class TestBulkCreateGraphNodes:
    """Testy bulk_create_graph_nodes."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import bulk_create_graph_nodes

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await bulk_create_graph_nodes(ctx, mcp_project.slug, [])

    async def test_bulk_create_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import bulk_create_graph_nodes

        ctx = _make_ctx()
        mock_node = {"id": "n1", "type": "File", "name": "a.py"}
        nodes_input = [
            {"type": "File", "name": "a.py"},
            {"type": "Class", "name": "Foo"},
        ]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_node = AsyncMock(return_value=mock_node)
            result = await bulk_create_graph_nodes(ctx, mcp_project.slug, nodes_input)
        assert result["created"] == 2
        assert result["errors"] == []

    async def test_bulk_create_missing_fields(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Node bez wymaganych pol (name/type) generuje error."""
        from monolynx.mcp_server import bulk_create_graph_nodes

        ctx = _make_ctx()
        nodes_input = [{"name": "missing_type"}, {"type": "File"}]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            result = await bulk_create_graph_nodes(ctx, mcp_project.slug, nodes_input)
        assert result["created"] == 0
        assert len(result["errors"]) == 2

    async def test_bulk_create_invalid_type(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Node z nieprawidlowym typem generuje error."""
        from monolynx.mcp_server import bulk_create_graph_nodes

        ctx = _make_ctx()
        nodes_input = [{"type": "InvalidType", "name": "bad"}]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            result = await bulk_create_graph_nodes(ctx, mcp_project.slug, nodes_input)
        assert result["created"] == 0
        assert len(result["errors"]) == 1
        assert "Nieznany typ" in result["errors"][0]

    async def test_bulk_create_exception_in_node(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Wyjatek z graph_service.create_node jest przechwytywany."""
        from monolynx.mcp_server import bulk_create_graph_nodes

        ctx = _make_ctx()
        nodes_input = [{"type": "File", "name": "error.py"}]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_node = AsyncMock(side_effect=RuntimeError("Neo4j error"))
            result = await bulk_create_graph_nodes(ctx, mcp_project.slug, nodes_input)
        assert result["created"] == 0
        assert len(result["errors"]) == 1
        assert "Neo4j error" in result["errors"][0]


@pytest.mark.unit
class TestBulkCreateGraphEdges:
    """Testy bulk_create_graph_edges."""

    async def test_graph_disabled(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import bulk_create_graph_edges

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
            pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"),
        ):
            mock_gs.is_enabled.return_value = False
            await bulk_create_graph_edges(ctx, mcp_project.slug, [])

    async def test_bulk_create_edges_success(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        from monolynx.mcp_server import bulk_create_graph_edges

        ctx = _make_ctx()
        mock_edge = {"source_id": "s1", "target_id": "t1", "type": "CALLS"}
        edges_input = [
            {"source_id": "s1", "target_id": "t1", "type": "CALLS"},
            {"source_id": "s2", "target_id": "t2", "type": "IMPORTS"},
        ]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_edge = AsyncMock(return_value=mock_edge)
            result = await bulk_create_graph_edges(ctx, mcp_project.slug, edges_input)
        assert result["created"] == 2
        assert result["skipped"] == 0

    async def test_bulk_create_edges_missing_fields(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Edge bez wymaganych pol generuje error."""
        from monolynx.mcp_server import bulk_create_graph_edges

        ctx = _make_ctx()
        edges_input = [
            {"source_id": "s1", "target_id": "t1"},  # brak type
            {"source_id": "s1"},  # brak target_id i type
        ]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            result = await bulk_create_graph_edges(ctx, mcp_project.slug, edges_input)
        assert result["created"] == 0
        assert len(result["errors"]) == 2

    async def test_bulk_create_edges_invalid_type(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Edge z nieprawidlowym typem generuje error."""
        from monolynx.mcp_server import bulk_create_graph_edges

        ctx = _make_ctx()
        edges_input = [{"source_id": "s1", "target_id": "t1", "type": "BAD_TYPE"}]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            result = await bulk_create_graph_edges(ctx, mcp_project.slug, edges_input)
        assert result["created"] == 0
        assert len(result["errors"]) == 1
        assert "Nieznany typ" in result["errors"][0]

    async def test_bulk_create_edges_nodes_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Edge gdzie create_edge zwraca None (node nie znaleziony) jest skippowany."""
        from monolynx.mcp_server import bulk_create_graph_edges

        ctx = _make_ctx()
        edges_input = [{"source_id": "s1", "target_id": "t1", "type": "CALLS"}]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_edge = AsyncMock(return_value=None)
            result = await bulk_create_graph_edges(ctx, mcp_project.slug, edges_input)
        assert result["created"] == 0
        assert result["skipped"] == 1

    async def test_bulk_create_edges_exception(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Wyjatek z graph_service.create_edge jest przechwytywany."""
        from monolynx.mcp_server import bulk_create_graph_edges

        ctx = _make_ctx()
        edges_input = [{"source_id": "s1", "target_id": "t1", "type": "CALLS"}]
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_edge = AsyncMock(side_effect=RuntimeError("DB error"))
            result = await bulk_create_graph_edges(ctx, mcp_project.slug, edges_input)
        assert result["created"] == 0
        assert len(result["errors"]) == 1
        assert "DB error" in result["errors"][0]
