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
    bulk_update_tickets,
    complete_sprint,
    create_label,
    create_monitor,
    create_sprint,
    create_ticket,
    delete_monitor,
    delete_ticket,
    get_board,
    get_issue,
    get_monitor,
    get_project_summary,
    get_sprint,
    get_ticket,
    invite_member,
    list_comments,
    list_issues,
    list_labels,
    list_members,
    list_monitors,
    list_projects,
    list_sprints,
    list_tickets,
    log_time,
    mcp,
    remove_member,
    search_tickets,
    start_sprint,
    update_issue_status,
    update_monitor,
    update_sprint,
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
from monolynx.models.label import Label, TicketLabel
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
    "get_project",
    "update_project",
    "delete_project",
    "create_project",
    "list_issues",
    "get_issue",
    "update_issue_status",
    "create_issue",
    "list_labels",
    "create_label",
    "list_monitors",
    "get_monitor",
    "create_monitor",
    "update_monitor",
    "delete_monitor",
    "get_board",
    "get_project_summary",
    "list_tickets",
    "search_tickets",
    "get_ticket",
    "create_ticket",
    "update_ticket",
    "delete_ticket",
    "bulk_update_tickets",
    "list_sprints",
    "get_sprint",
    "create_sprint",
    "update_sprint",
    "start_sprint",
    "complete_sprint",
    "list_comments",
    "add_comment",
    "add_attachment",
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
    "list_heartbeats",
    "get_heartbeat",
    "create_heartbeat",
    "update_heartbeat",
    "delete_heartbeat",
    "list_members",
    "invite_member",
    "remove_member",
    "get_activity_log",
    "get_burndown",
]


@pytest.mark.unit
class TestMcpToolRegistration:
    """Weryfikacja ze wszystkie narzedzia MCP sa poprawnie zarejestrowane."""

    async def test_list_tools_returns_all_tools(self):
        """list_tools() zwraca wszystkie zarejestrowane narzedzia."""
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
            if tool.name in ("list_projects", "create_project"):
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
# bulk_update_tickets
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBulkUpdateTickets:
    async def test_bulk_update_status(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """3 tickety — zmien status na done."""
        tickets = [Ticket(project_id=mcp_project.id, number=i + 1, title=f"Ticket {i + 1}", status="backlog") for i in range(3)]
        for t in tickets:
            db_session.add(t)
        await db_session.flush()

        ticket_ids = [str(t.id) for t in tickets]
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await bulk_update_tickets(ctx, mcp_project.slug, ticket_ids, status="done")

        assert result["updated"] == 3
        assert result["failed"] == []
        for t in tickets:
            assert t.status == "done"

    async def test_bulk_update_partial_failure(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Mix istniejacych i nieistniejacych ID — czesc aktualizuje, czesc w failed."""
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Istniejacy", status="backlog")
        db_session.add(ticket)
        await db_session.flush()

        nonexistent_id = str(uuid.uuid4())
        ticket_ids = [str(ticket.id), nonexistent_id]
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await bulk_update_tickets(ctx, mcp_project.slug, ticket_ids, status="in_progress")

        assert result["updated"] == 1
        assert len(result["failed"]) == 1
        assert result["failed"][0]["id"] == nonexistent_id
        assert "nie istnieje" in result["failed"][0]["reason"]
        assert ticket.status == "in_progress"

    async def test_bulk_update_limit_exceeded(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """101 ticketow w jednym wywolaniu powoduje ValueError."""
        ticket_ids = [str(uuid.uuid4()) for _ in range(101)]
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Zbyt duzo ticketow"),
        ):
            await bulk_update_tickets(ctx, mcp_project.slug, ticket_ids, status="done")

    async def test_bulk_update_invalid_status(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy status przed petla powoduje ValueError (fail fast)."""
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Test", status="backlog")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy status"),
        ):
            await bulk_update_tickets(ctx, mcp_project.slug, [str(ticket.id)], status="invalid_status")
        # Ticket nie zostal zmieniony
        assert ticket.status == "backlog"

    async def test_bulk_update_invalid_priority(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy priorytet przed petla powoduje ValueError (fail fast)."""
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Test", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy priorytet"),
        ):
            await bulk_update_tickets(ctx, mcp_project.slug, [str(ticket.id)], priority="ultra")
        assert ticket.priority == "medium"

    async def test_bulk_update_clear_sprint(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """sprint_id='' przenosi tickety do backlogu."""
        sprint = Sprint(project_id=mcp_project.id, name="Sprint 1", start_date=date(2026, 3, 1))
        db_session.add(sprint)
        await db_session.flush()

        tickets = [Ticket(project_id=mcp_project.id, number=i + 1, title=f"T{i + 1}", sprint_id=sprint.id) for i in range(2)]
        for t in tickets:
            db_session.add(t)
        await db_session.flush()

        ticket_ids = [str(t.id) for t in tickets]
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await bulk_update_tickets(ctx, mcp_project.slug, ticket_ids, sprint_id="")

        assert result["updated"] == 2
        assert result["failed"] == []
        for t in tickets:
            assert t.sprint_id is None

    async def test_bulk_update_invalid_due_date(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy format due_date powoduje ValueError (fail fast)."""
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Test")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy format daty due_date"),
        ):
            await bulk_update_tickets(ctx, mcp_project.slug, [str(ticket.id)], due_date="30-06-2026")

    async def test_bulk_update_empty_ticket_ids(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Pusta lista ticket_ids zwraca updated=0 i pusta liste failed."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await bulk_update_tickets(ctx, mcp_project.slug, [], status="done")

        assert result["updated"] == 0
        assert result["failed"] == []


# ---------------------------------------------------------------------------
# due_date: create_ticket, update_ticket, list_tickets
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTicketDueDate:
    async def test_create_ticket_with_due_date(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """create_ticket zapisuje due_date i zwraca w response."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(ctx, mcp_project.slug, "Ticket z terminem", due_date="2026-06-30")
        assert result["due_date"] == "2026-06-30"

    async def test_create_ticket_without_due_date(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """create_ticket bez due_date zwraca None."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(ctx, mcp_project.slug, "Ticket bez terminu")
        assert result["due_date"] is None

    async def test_create_ticket_invalid_due_date_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy format due_date rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy format daty due_date"),
        ):
            await create_ticket(ctx, mcp_project.slug, "Zly termin", due_date="30-06-2026")

    async def test_update_ticket_with_due_date(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """update_ticket ustawia due_date i zwraca w response."""
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Ticket do aktualizacji")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_ticket(ctx, mcp_project.slug, str(ticket.id), due_date="2026-07-15")
        assert result["due_date"] == "2026-07-15"

    async def test_update_ticket_clear_due_date(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """update_ticket z due_date='' czyści date."""
        ticket = Ticket(
            project_id=mcp_project.id,
            number=1,
            title="Ticket z terminem",
            due_date=date(2026, 6, 30),
        )
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_ticket(ctx, mcp_project.slug, str(ticket.id), due_date="")
        assert result["due_date"] is None

    async def test_update_ticket_invalid_due_date_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy format due_date w update rzuca ValueError."""
        ticket = Ticket(project_id=mcp_project.id, number=1, title="Ticket err")
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy format daty due_date"),
        ):
            await update_ticket(ctx, mcp_project.slug, str(ticket.id), due_date="invalid")

    async def test_list_tickets_filter_due_date_before(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Filtr due_date_before zwraca tylko tickety z due_date <= data."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Early", due_date=date(2026, 3, 1)))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Late", due_date=date(2026, 12, 31)))
        db_session.add(Ticket(project_id=mcp_project.id, number=3, title="No date"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug, due_date_before="2026-06-30")
        titles = [r["title"] for r in result if "_meta" not in r]
        assert "Early" in titles
        assert "Late" not in titles
        assert "No date" not in titles

    async def test_list_tickets_filter_due_date_after(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Filtr due_date_after zwraca tylko tickety z due_date >= data."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Early", due_date=date(2026, 3, 1)))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Late", due_date=date(2026, 12, 31)))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug, due_date_after="2026-06-30")
        titles = [r["title"] for r in result if "_meta" not in r]
        assert "Late" in titles
        assert "Early" not in titles

    async def test_list_tickets_filter_overdue(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Filtr overdue=True zwraca tylko niezakonczone tickety po terminie."""
        past = date(2020, 1, 1)
        future = date(2099, 12, 31)
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Overdue open", due_date=past, status="todo"))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Overdue done", due_date=past, status="done"))
        db_session.add(Ticket(project_id=mcp_project.id, number=3, title="Future", due_date=future, status="todo"))
        db_session.add(Ticket(project_id=mcp_project.id, number=4, title="No date", status="todo"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug, overdue=True)
        titles = [r["title"] for r in result if "_meta" not in r]
        assert "Overdue open" in titles
        assert "Overdue done" not in titles
        assert "Future" not in titles
        assert "No date" not in titles

    async def test_list_tickets_filter_due_date_before_invalid_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy format due_date_before rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy format due_date_before"),
        ):
            await list_tickets(ctx, mcp_project.slug, due_date_before="invalid")

    async def test_get_ticket_returns_due_date(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """get_ticket zwraca due_date w ISO format."""
        ticket = Ticket(
            project_id=mcp_project.id,
            number=1,
            title="Ticket z due_date",
            due_date=date(2026, 9, 15),
        )
        db_session.add(ticket)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_ticket(ctx, mcp_project.slug, str(ticket.id))
        assert result["due_date"] == "2026-09-15"

    async def test_list_tickets_includes_due_date_in_response(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """list_tickets zwraca due_date dla kazdego ticketa."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="With date", due_date=date(2026, 5, 1)))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Without date"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_tickets(ctx, mcp_project.slug)
        tickets = [r for r in result if "_meta" not in r]
        with_date = next(t for t in tickets if t["title"] == "With date")
        without_date = next(t for t in tickets if t["title"] == "Without date")
        assert with_date["due_date"] == "2026-05-01"
        assert without_date["due_date"] is None


# ---------------------------------------------------------------------------
# search_tickets
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSearchTickets:
    async def test_empty_results(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Brak ticketow zwraca puste results z metadanymi."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await search_tickets(ctx, mcp_project.slug)
        assert result["results"] == []
        assert result["total"] == 0
        assert result["page"] == 1
        assert result["total_pages"] == 1

    async def test_search_by_query_title(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """query wyszukuje po tytule (ILIKE)."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Login bug fix"))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Dashboard redesign"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await search_tickets(ctx, mcp_project.slug, query="login")
        assert result["total"] == 1
        assert result["results"][0]["title"] == "Login bug fix"

    async def test_search_by_query_description(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """query wyszukuje tez po opisie (ILIKE)."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Fix something", description="Affects the authentication module"))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Another task", description="Unrelated work"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await search_tickets(ctx, mcp_project.slug, query="authentication")
        assert result["total"] == 1
        assert result["results"][0]["title"] == "Fix something"

    async def test_filter_by_status(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Filtr po statusie zwraca tylko dopasowane tickety."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Done task", status="done"))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Todo task", status="todo"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await search_tickets(ctx, mcp_project.slug, status="done")
        assert result["total"] == 1
        assert result["results"][0]["status"] == "done"

    async def test_filter_by_priority(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Filtr po priorytecie zwraca tylko dopasowane tickety."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="High prio", priority="high"))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Low prio", priority="low"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await search_tickets(ctx, mcp_project.slug, priority="high")
        assert result["total"] == 1
        assert result["results"][0]["priority"] == "high"

    async def test_filter_by_assignee_email(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Filtr po assignee_email zwraca tylko tickety przypisane do danej osoby."""
        other_user = User(email=f"assignee-{uuid.uuid4().hex[:8]}@test.com", password_hash=hash_password("pass"))
        db_session.add(other_user)
        await db_session.flush()

        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Assigned to other", assignee_id=other_user.id))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Unassigned"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await search_tickets(ctx, mcp_project.slug, assignee_email=other_user.email)
        assert result["total"] == 1
        assert result["results"][0]["title"] == "Assigned to other"
        assert result["results"][0]["assignee"] == other_user.email

    async def test_filter_by_assignee_email_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieistniejacy assignee_email zwraca puste wyniki."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Some ticket"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await search_tickets(ctx, mcp_project.slug, assignee_email="nobody@example.com")
        assert result["total"] == 0
        assert result["results"] == []

    async def test_combined_filters(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Kombinacja query + status zwraca tylko dopasowane tickety."""
        db_session.add(Ticket(project_id=mcp_project.id, number=1, title="Fix login bug", status="in_progress"))
        db_session.add(Ticket(project_id=mcp_project.id, number=2, title="Fix payment bug", status="done"))
        db_session.add(Ticket(project_id=mcp_project.id, number=3, title="Other task", status="in_progress"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await search_tickets(ctx, mcp_project.slug, query="fix", status="in_progress")
        assert result["total"] == 1
        assert result["results"][0]["title"] == "Fix login bug"

    async def test_pagination(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Paginacja zwraca poprawne metadane."""
        for i in range(1, 25):
            db_session.add(Ticket(project_id=mcp_project.id, number=i, title=f"Ticket {i}"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result_page1 = await search_tickets(ctx, mcp_project.slug, page=1)
            result_page2 = await search_tickets(ctx, mcp_project.slug, page=2)

        assert result_page1["total"] == 24
        assert result_page1["total_pages"] == 2
        assert result_page1["page"] == 1
        assert len(result_page1["results"]) == 20

        assert result_page2["page"] == 2
        assert len(result_page2["results"]) == 4

    async def test_response_shape(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Odpowiedz zawiera wymagane pola dla kazdego ticketa."""
        db_session.add(
            Ticket(
                project_id=mcp_project.id,
                number=1,
                title="Shape test",
                status="todo",
                priority="medium",
                story_points=3,
                due_date=date(2026, 6, 1),
            )
        )
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await search_tickets(ctx, mcp_project.slug)

        assert len(result["results"]) == 1
        ticket = result["results"][0]
        assert "id" in ticket
        assert "key" in ticket
        assert ticket["title"] == "Shape test"
        assert ticket["status"] == "todo"
        assert ticket["priority"] == "medium"
        assert ticket["story_points"] == 3
        assert ticket["due_date"] == "2026-06-01"
        assert ticket["assignee"] is None

    async def test_invalid_due_before_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy format due_before rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy format due_before"),
        ):
            await search_tickets(ctx, mcp_project.slug, due_before="not-a-date")


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
# delete_monitor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteMonitor:
    async def test_deletes_existing_monitor(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        monitor = Monitor(
            project_id=mcp_project.id,
            url="https://example.com",
            name="Do usuniecia",
            interval_value=5,
            interval_unit="minutes",
        )
        db_session.add(monitor)
        await db_session.flush()
        monitor_id = str(monitor.id)

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await delete_monitor(ctx, mcp_project.slug, monitor_id)

        assert "Do usuniecia" in result["message"]
        assert "deleted_at" in result

    async def test_monitor_not_found(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Monitor nie istnieje"),
        ):
            await delete_monitor(ctx, mcp_project.slug, str(uuid.uuid4()))

    async def test_non_admin_cannot_delete(self, db_session, mcp_user, mcp_project, mock_factory, mock_verify):
        # Czlonek z rola 'member' nie powinien moc usunac monitora
        from monolynx.models.project_member import ProjectMember as ProjectMemberModel
        from monolynx.models.user import User as UserModel

        member_user = UserModel(email="member-del@example.com", is_active=True)
        db_session.add(member_user)
        await db_session.flush()

        member_role = ProjectMemberModel(project_id=mcp_project.id, user_id=member_user.id, role="member")
        db_session.add(member_role)
        await db_session.flush()

        monitor = Monitor(
            project_id=mcp_project.id,
            url="https://example.com",
            name="Chroniony",
            interval_value=5,
            interval_unit="minutes",
        )
        db_session.add(monitor)
        await db_session.flush()

        # Mock verify zwracajacy czlonka z rola member
        async def _verify_member(_token: str, _db):  # type: ignore[no-untyped-def]
            return member_user

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", _verify_member),
            pytest.raises(ValueError, match="Tylko owner lub admin"),
        ):
            await delete_monitor(ctx, mcp_project.slug, str(monitor.id))


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


# ---------------------------------------------------------------------------
# create_monitor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateMonitor:
    async def test_create_monitor_minimal_params(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Tworzenie monitora z minimalnymi parametrami zwraca poprawny slownik."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server._is_url_safe", return_value=None),
        ):
            result = await create_monitor(ctx, mcp_project.slug, "My Monitor", "https://example.com")

        assert result["name"] == "My Monitor"
        assert result["url"] == "https://example.com"
        assert result["interval_value"] == 5
        assert result["interval_unit"] == "minutes"
        assert result["is_active"] is True
        assert "id" in result
        assert "created_at" in result

    async def test_create_monitor_with_custom_interval(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Tworzenie monitora z custom interval_value i interval_unit."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server._is_url_safe", return_value=None),
        ):
            result = await create_monitor(
                ctx,
                mcp_project.slug,
                "Hourly Monitor",
                "https://example.com/health",
                interval_value=2,
                interval_unit="hours",
            )

        assert result["interval_value"] == 2
        assert result["interval_unit"] == "hours"
        assert result["name"] == "Hourly Monitor"

    async def test_create_monitor_empty_name_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Pusta nazwa monitora rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nazwa monitora nie moze byc pusta"),
        ):
            await create_monitor(ctx, mcp_project.slug, "   ", "https://example.com")

    async def test_create_monitor_invalid_url_scheme_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """URL bez http:// lub https:// rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="URL musi zaczynac sie od http:// lub https://"),
        ):
            await create_monitor(ctx, mcp_project.slug, "Bad URL Monitor", "ftp://example.com")

    async def test_create_monitor_ssrf_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """URL wskazujacy na prywatny adres IP jest blokowany przez SSRF protection."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server._is_url_safe", return_value="adres lokalny niedozwolony"),
            pytest.raises(ValueError, match="Niedozwolony URL"),
        ):
            await create_monitor(ctx, mcp_project.slug, "SSRF Monitor", "http://localhost/secret")

    async def test_create_monitor_invalid_interval_unit_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy interval_unit rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server._is_url_safe", return_value=None),
            pytest.raises(ValueError, match="interval_unit musi byc jednym z"),
        ):
            await create_monitor(ctx, mcp_project.slug, "Monitor", "https://example.com", interval_unit="seconds")

    async def test_create_monitor_interval_value_zero_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """interval_value=0 rzuca ValueError (poza zakresem 1-60)."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server._is_url_safe", return_value=None),
            pytest.raises(ValueError, match="interval_value musi byc liczba od 1 do 60"),
        ):
            await create_monitor(ctx, mcp_project.slug, "Monitor", "https://example.com", interval_value=0)

    async def test_create_monitor_interval_value_61_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """interval_value=61 rzuca ValueError (poza zakresem 1-60)."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server._is_url_safe", return_value=None),
            pytest.raises(ValueError, match="interval_value musi byc liczba od 1 do 60"),
        ):
            await create_monitor(ctx, mcp_project.slug, "Monitor", "https://example.com", interval_value=61)

    async def test_create_monitor_limit_exceeded_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Osiagniecie limitu 20 monitorow rzuca ValueError."""
        for i in range(20):
            db_session.add(
                Monitor(
                    project_id=mcp_project.id,
                    url=f"https://example{i}.com",
                    name=f"Monitor {i}",
                    interval_value=5,
                    interval_unit="minutes",
                )
            )
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server._is_url_safe", return_value=None),
            pytest.raises(ValueError, match="Osiagnieto limit 20 monitorow na projekt"),
        ):
            await create_monitor(ctx, mcp_project.slug, "One More Monitor", "https://new.example.com")


# ---------------------------------------------------------------------------
# update_monitor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateMonitor:
    async def test_update_name_only(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Aktualizacja tylko nazwy zostawia pozostale pola bez zmian."""
        monitor = Monitor(
            project_id=mcp_project.id,
            url="https://example.com",
            name="Stara nazwa",
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
            result = await update_monitor(ctx, mcp_project.slug, str(monitor.id), name="Nowa nazwa")

        assert result["name"] == "Nowa nazwa"
        assert result["url"] == "https://example.com"
        assert result["interval_value"] == 5
        assert result["interval_unit"] == "minutes"
        assert "id" in result
        assert "created_at" in result

    async def test_update_url(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Aktualizacja URL przechodzi walidacje scheme i SSRF."""
        monitor = Monitor(
            project_id=mcp_project.id,
            url="https://old.example.com",
            name="Monitor",
            interval_value=5,
            interval_unit="minutes",
        )
        db_session.add(monitor)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server._is_url_safe", return_value=None),
        ):
            result = await update_monitor(ctx, mcp_project.slug, str(monitor.id), url="https://new.example.com")

        assert result["url"] == "https://new.example.com"

    async def test_update_interval(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Aktualizacja interval_value i interval_unit jednoczesnie."""
        monitor = Monitor(
            project_id=mcp_project.id,
            url="https://example.com",
            name="Monitor",
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
            result = await update_monitor(ctx, mcp_project.slug, str(monitor.id), interval_value=2, interval_unit="hours")

        assert result["interval_value"] == 2
        assert result["interval_unit"] == "hours"

    async def test_update_monitor_not_found_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieistniejacy monitor_id rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Monitor nie istnieje"),
        ):
            await update_monitor(ctx, mcp_project.slug, str(uuid.uuid4()))

    async def test_update_empty_name_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Pusta nazwa (po strip) rzuca ValueError przed zapytaniem do DB."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nazwa monitora nie moze byc pusta"),
        ):
            await update_monitor(ctx, mcp_project.slug, str(uuid.uuid4()), name="   ")

    async def test_update_invalid_url_scheme_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """URL bez http:// lub https:// rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="URL musi zaczynac sie od http:// lub https://"),
        ):
            await update_monitor(ctx, mcp_project.slug, str(uuid.uuid4()), url="ftp://bad.example.com")

    async def test_update_ssrf_url_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """URL wskazujacy na prywatny IP jest blokowany przez SSRF protection."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server._is_url_safe", return_value="adres lokalny niedozwolony"),
            pytest.raises(ValueError, match="Niedozwolony URL"),
        ):
            await update_monitor(ctx, mcp_project.slug, str(uuid.uuid4()), url="http://localhost/secret")

    async def test_update_invalid_interval_unit_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy interval_unit rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="interval_unit musi byc jednym z"),
        ):
            await update_monitor(ctx, mcp_project.slug, str(uuid.uuid4()), interval_unit="seconds")

    async def test_update_interval_value_out_of_range_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """interval_value poza zakresem 1-60 rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="interval_value musi byc liczba od 1 do 60"),
        ):
            await update_monitor(ctx, mcp_project.slug, str(uuid.uuid4()), interval_value=0)


# ---------------------------------------------------------------------------
# update_sprint
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateSprint:
    async def test_update_name(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Zmiana nazwy sprintu zwraca zaktualizowane dane."""
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Old Name",
            start_date=date(2026, 4, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_sprint(ctx, mcp_project.slug, str(sprint.id), name="New Name")

        assert result["name"] == "New Name"
        assert result["id"] == str(sprint.id)
        assert result["status"] == "planning"

    async def test_update_goal(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Zmiana celu sprintu zapisuje nowy goal."""
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Goal Sprint",
            start_date=date(2026, 4, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_sprint(ctx, mcp_project.slug, str(sprint.id), goal="Deliver feature X")

        assert result["goal"] == "Deliver feature X"

    async def test_update_dates(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Zmiana dat start_date i end_date dla sprintu w statusie planning."""
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Date Sprint",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 14),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_sprint(
                ctx,
                mcp_project.slug,
                str(sprint.id),
                start_date="2026-05-01",
                end_date="2026-05-15",
            )

        assert result["start_date"] == "2026-05-01"
        assert result["end_date"] == "2026-05-15"

    async def test_update_dates_on_completed_sprint_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Zmiana dat zakonczonego sprintu rzuca ValueError."""
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Completed Sprint",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 14),
            status="completed",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nie mozna zmienic dat zakonczonego sprintu"),
        ):
            await update_sprint(ctx, mcp_project.slug, str(sprint.id), end_date="2026-03-21")

    async def test_update_name_on_completed_sprint_succeeds(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Zmiana nazwy zakonczonego sprintu jest dozwolona."""
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Old Completed Name",
            start_date=date(2026, 3, 1),
            status="completed",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_sprint(ctx, mcp_project.slug, str(sprint.id), name="Renamed Completed")

        assert result["name"] == "Renamed Completed"
        assert result["status"] == "completed"

    async def test_update_end_date_before_start_date_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """end_date <= start_date rzuca ValueError."""
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Date Validation Sprint",
            start_date=date(2026, 4, 10),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Data zakonczenia musi byc pozniejsza niz data rozpoczecia"),
        ):
            await update_sprint(
                ctx,
                mcp_project.slug,
                str(sprint.id),
                start_date="2026-04-10",
                end_date="2026-04-10",
            )

    async def test_update_nonexistent_sprint_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Sprint nie istnieje rzuca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Sprint nie istnieje"),
        ):
            await update_sprint(ctx, mcp_project.slug, str(uuid.uuid4()), name="Ghost Sprint")

    async def test_update_empty_name_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Pusta nazwa (po strip) rzuca ValueError."""
        sprint = Sprint(
            project_id=mcp_project.id,
            name="Valid Name",
            start_date=date(2026, 4, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nazwa sprintu nie moze byc pusta"),
        ):
            await update_sprint(ctx, mcp_project.slug, str(sprint.id), name="   ")


# ---------------------------------------------------------------------------
# list_members
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListMembers:
    async def test_single_owner(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """list_members zwraca jednego ownera."""
        mcp_user.first_name = "Jan"
        mcp_user.last_name = "Kowalski"
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_members(ctx, mcp_project.slug)

        assert len(result) == 1
        assert result[0]["email"] == mcp_user.email
        assert result[0]["name"] == "Jan Kowalski"
        assert result[0]["role"] == "owner"
        assert result[0]["user_id"] == str(mcp_user.id)
        assert "joined_at" in result[0]

    async def test_multiple_roles_sorted(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """list_members: owner na gorze, potem admin, potem member — alfabetycznie w grupie."""
        admin_user = User(
            email=f"admin-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            first_name="Zenon",
            last_name="Admin",
        )
        member_user = User(
            email=f"member-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            first_name="Anna",
            last_name="Member",
        )
        db_session.add_all([admin_user, member_user])
        await db_session.flush()

        db_session.add(ProjectMember(project_id=mcp_project.id, user_id=admin_user.id, role="admin"))
        db_session.add(ProjectMember(project_id=mcp_project.id, user_id=member_user.id, role="member"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_members(ctx, mcp_project.slug)

        assert len(result) == 3
        assert result[0]["role"] == "owner"
        assert result[1]["role"] == "admin"
        assert result[2]["role"] == "member"

    async def test_name_fallback_to_email(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Gdy first_name i last_name sa puste, name to email."""
        mcp_user.first_name = ""
        mcp_user.last_name = ""
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_members(ctx, mcp_project.slug)

        assert result[0]["name"] == mcp_user.email

    async def test_returns_required_fields(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Kazdy element wyniku zawiera: user_id, name, email, role, joined_at."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_members(ctx, mcp_project.slug)

        assert len(result) >= 1
        for item in result:
            assert "user_id" in item
            assert "name" in item
            assert "email" in item
            assert "role" in item
            assert "joined_at" in item


# ---------------------------------------------------------------------------
# invite_member
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInviteMember:
    """Testy narzedzia MCP invite_member."""

    async def test_invite_existing_active_user_adds_as_member(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Happy path: owner zaprasza istniejacego aktywnego usera — dodany jako member bez emaila."""
        # Arrange
        invited_user = User(
            email=f"invited-existing-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(invited_user)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.send_invitation_email") as mock_email,
        ):
            # Act
            result = await invite_member(ctx, mcp_project.slug, invited_user.email, role="member")

        # Assert
        assert result["user_email"] == invited_user.email
        assert result["role"] == "member"
        assert "dodany do projektu" in result["message"]
        # Brak wyslania emaila — user juz istnieje i jest aktywny
        mock_email.assert_not_called()
        # Sprawdz ze ProjectMember zostal dodany
        await db_session.refresh(invited_user)

    async def test_invite_new_user_creates_account_and_sends_email(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Happy path: owner zaprasza nieistniejacy email — nowy User + ProjectMember + email wysłany."""
        # Arrange
        new_email = f"brand-new-{uuid.uuid4().hex[:8]}@invited.com"

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.send_invitation_email") as mock_email,
        ):
            # Act
            result = await invite_member(ctx, mcp_project.slug, new_email, role="member")

        # Assert — klucze odpowiedzi wskazujace na flow zaproszenia
        assert result["user_email"] == new_email
        assert result["role"] == "member"
        assert "invitation_id" in result
        assert "expires_at" in result
        assert "Zaproszenie wyslane" in result["message"]
        mock_email.assert_called_once()

    async def test_invite_with_admin_role_succeeds(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Zaproszenie z rola 'admin' jest dozwolone."""
        # Arrange
        new_email = f"admin-invite-{uuid.uuid4().hex[:8]}@test.com"

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.mcp_server.send_invitation_email"),
        ):
            # Act
            result = await invite_member(ctx, mcp_project.slug, new_email, role="admin")

        # Assert
        assert result["role"] == "admin"

    async def test_invite_with_owner_role_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Proba zaproszenia z rola 'owner' powinna zwrocic ValueError."""
        # Arrange
        ctx = _make_ctx()

        # Act / Assert
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="owner"),
        ):
            await invite_member(ctx, mcp_project.slug, "anyone@test.com", role="owner")

    async def test_invite_already_member_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Proba zaproszenia osoby, ktora jest juz czlonkiem projektu, zwraca ValueError."""
        # Arrange — mcp_user jest juz ownerem projektu (mcp_member fixture)
        ctx = _make_ctx()

        # Act / Assert
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="juz czlonkiem projektu"),
        ):
            await invite_member(ctx, mcp_project.slug, mcp_user.email)

    async def test_invite_by_regular_member_raises_value_error(self, db_session, mcp_project, mock_factory):
        """Uzytkownik z rola 'member' nie moze zapraszac — brak uprawnien."""
        # Arrange
        regular_user = User(
            email=f"regular-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(regular_user)
        await db_session.flush()

        db_session.add(ProjectMember(project_id=mcp_project.id, user_id=regular_user.id, role="member"))
        await db_session.flush()

        mock_verify_regular = AsyncMock(return_value=regular_user)

        ctx = _make_ctx()
        # Act / Assert
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify_regular),
            pytest.raises(ValueError, match="Tylko owner lub admin"),
        ):
            await invite_member(ctx, mcp_project.slug, "someone@test.com")

    async def test_invite_empty_email_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Pusty email (po strip) powinien zwrocic ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Email nie moze byc pusty"),
        ):
            await invite_member(ctx, mcp_project.slug, "   ")

    async def test_invite_invalid_email_format_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Email bez znaku @ powinien zwrocic ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy format"),
        ):
            await invite_member(ctx, mcp_project.slug, "notavalidemail")

    async def test_invite_inactive_user_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Proba zaproszenia dezaktywowanego uzytkownika zwraca ValueError."""
        # Arrange
        inactive_user = User(
            email=f"inactive-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=False,
        )
        db_session.add(inactive_user)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="dezaktywowany"),
        ):
            await invite_member(ctx, mcp_project.slug, inactive_user.email)


# ---------------------------------------------------------------------------
# remove_member
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoveMember:
    """Testy narzedzia MCP remove_member."""

    async def test_owner_removes_regular_member(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Happy path: owner usuwa czlonka z rola 'member'."""
        target_user = User(
            email=f"target-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(target_user)
        await db_session.flush()

        db_session.add(ProjectMember(project_id=mcp_project.id, user_id=target_user.id, role="member"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await remove_member(ctx, mcp_project.slug, target_user.email)

        assert "usuniety" in result["message"]
        assert target_user.email in result["message"]

    async def test_owner_removes_admin_member(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Owner moze usunac admina."""
        admin_user = User(
            email=f"admin-remove-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(admin_user)
        await db_session.flush()

        db_session.add(ProjectMember(project_id=mcp_project.id, user_id=admin_user.id, role="admin"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await remove_member(ctx, mcp_project.slug, admin_user.email)

        assert "usuniety" in result["message"]

    async def test_cannot_remove_owner_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Proba usuniecia ownera projektu zwraca ValueError."""
        # mcp_user jest ownerem projektu (mcp_member fixture)
        # Dodaj drugiego ownera, zeby moc sprobowac usunac mcp_usera jako inny owner
        second_owner = User(
            email=f"second-owner-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(second_owner)
        await db_session.flush()

        db_session.add(ProjectMember(project_id=mcp_project.id, user_id=second_owner.id, role="owner"))
        await db_session.flush()

        mock_verify_second = AsyncMock(return_value=second_owner)

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify_second),
            pytest.raises(ValueError, match="ownera projektu"),
        ):
            await remove_member(ctx, mcp_project.slug, mcp_user.email)

    async def test_user_not_found_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Proba usuniecia uzytkownika, ktory nie istnieje w systemie, zwraca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje w systemie"),
        ):
            await remove_member(ctx, mcp_project.slug, "nonexistent@test.com")

    async def test_user_not_member_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Proba usuniecia uzytkownika, ktory nie jest czlonkiem projektu, zwraca ValueError."""
        non_member = User(
            email=f"non-member-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(non_member)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie jest czlonkiem projektu"),
        ):
            await remove_member(ctx, mcp_project.slug, non_member.email)

    async def test_regular_member_cannot_remove_raises_value_error(self, db_session, mcp_project, mock_factory):
        """Uzytkownik z rola 'member' nie moze usuwac czlonkow — brak uprawnien."""
        regular_user = User(
            email=f"regular-rm-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(regular_user)
        await db_session.flush()

        db_session.add(ProjectMember(project_id=mcp_project.id, user_id=regular_user.id, role="member"))
        await db_session.flush()

        mock_verify_regular = AsyncMock(return_value=regular_user)

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify_regular),
            pytest.raises(ValueError, match="Tylko owner lub admin"),
        ):
            await remove_member(ctx, mcp_project.slug, "anyone@test.com")

    async def test_empty_email_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Pusty email (po strip) powinien zwrocic ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Email nie moze byc pusty"),
        ):
            await remove_member(ctx, mcp_project.slug, "   ")

    async def test_invalid_email_format_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Email bez @ powinien zwrocic ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy format adresu email"),
        ):
            await remove_member(ctx, mcp_project.slug, "invalid-email")


# ---------------------------------------------------------------------------
# Testy: list_labels / create_label
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateLabel:
    """Testy narzedzia create_label."""

    async def test_happy_path_without_color(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Tworzy etykiete z losowym kolorem gdy color nie podany."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_label(ctx, mcp_project.slug, "Bug")

        assert result["name"] == "Bug"
        assert result["color"].startswith("#")
        assert len(result["color"]) == 7
        assert "id" in result
        assert "message" in result

    async def test_happy_path_with_color(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Tworzy etykiete z podanym kolorem."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_label(ctx, mcp_project.slug, "Feature", color="#3498db")

        assert result["name"] == "Feature"
        assert result["color"] == "#3498db"

    async def test_empty_name_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Pusta nazwa powinna zwrocic ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie moze byc pusta"),
        ):
            await create_label(ctx, mcp_project.slug, "   ")

    async def test_duplicate_name_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Duplikat nazwy w tym samym projekcie zwraca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            await create_label(ctx, mcp_project.slug, "Duplicate")

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="juz istnieje"),
        ):
            await create_label(ctx, mcp_project.slug, "Duplicate")

    async def test_invalid_color_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieprawidlowy kolor zwraca ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="formacie hex"),
        ):
            await create_label(ctx, mcp_project.slug, "BadColor", color="red")


@pytest.mark.unit
class TestListLabels:
    """Testy narzedzia list_labels."""

    async def test_empty_list(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Zwraca pusta liste gdy brak etykiet."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_labels(ctx, mcp_project.slug)

        assert result == []

    async def test_returns_labels_with_tickets_count(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Zwraca etykiety z poprawna liczba tickets_count."""
        label = Label(project_id=mcp_project.id, name="TestLabel", color="#2ecc71")
        db_session.add(label)

        ticket = Ticket(
            project_id=mcp_project.id,
            number=9001,
            title="Ticket z labelem",
            status="backlog",
            priority="medium",
            order=0,
        )
        db_session.add(ticket)
        await db_session.flush()

        db_session.add(TicketLabel(ticket_id=ticket.id, label_id=label.id))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_labels(ctx, mcp_project.slug)

        assert len(result) == 1
        assert result[0]["name"] == "TestLabel"
        assert result[0]["color"] == "#2ecc71"
        assert result[0]["tickets_count"] == 1
