"""Testy jednostkowe MCP Server -- narzedzia Scrum, 500ki, Monitoring."""

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
    mcp,
    start_sprint,
    update_issue_status,
    update_ticket,
)
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
from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(token: str = "test-token") -> MagicMock:
    """Mock MCP Context z Bearer token w naglowku."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.transport = MagicMock()
    ctx.request_context.transport.headers = {"authorization": f"Bearer {token}"}
    return ctx


def _make_ctx_no_request() -> MagicMock:
    """Mock MCP Context bez request_context."""
    ctx = MagicMock()
    ctx.request_context = None
    return ctx


def _make_ctx_no_transport() -> MagicMock:
    """Mock MCP Context bez transportu."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.transport = None
    return ctx


def _make_ctx_no_bearer() -> MagicMock:
    """Mock MCP Context bez tokenu Bearer."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.transport = MagicMock()
    ctx.request_context.transport.headers = {"authorization": "Basic abc"}
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
    project = Project(
        name="MCP Unit Project",
        slug=f"mcp-unit-{uuid.uuid4().hex[:8]}",
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
]


@pytest.mark.unit
class TestMcpToolRegistration:
    """Weryfikacja ze wszystkie narzedzia MCP sa poprawnie zarejestrowane."""

    async def test_list_tools_returns_all_tools(self):
        """list_tools() zwraca wszystkie 20 narzedzi."""
        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]
        assert len(tools) == len(EXPECTED_TOOLS)
        for name in EXPECTED_TOOLS:
            assert name in tool_names, f"Brak narzedzia: {name}"

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

    async def test_auth_no_transport(self):
        ctx = _make_ctx_no_transport()
        with pytest.raises(ValueError, match="Brak transportu HTTP"):
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

    async def test_get_auth_header_no_transport(self):
        ctx = _make_ctx_no_transport()
        with pytest.raises(ValueError, match="Brak transportu HTTP"):
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
        project = Project(
            name="Inactive Project",
            slug=f"inactive-{uuid.uuid4().hex[:8]}",
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
        for status in ("backlog", "todo", "done"):
            db_session.add(
                Ticket(
                    project_id=mcp_project.id,
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
        db_session.add(Ticket(project_id=mcp_project.id, title="High", priority="high"))
        db_session.add(Ticket(project_id=mcp_project.id, title="Low", priority="low"))
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
        db_session.add(Ticket(project_id=mcp_project.id, title="Login bug"))
        db_session.add(Ticket(project_id=mcp_project.id, title="Dashboard feature"))
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

        db_session.add(Ticket(project_id=mcp_project.id, title="In sprint", sprint_id=sprint.id))
        db_session.add(Ticket(project_id=mcp_project.id, title="No sprint"))
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
        db_session.add(Ticket(project_id=mcp_project.id, title="Any ticket"))
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
        ticket = Ticket(project_id=mcp_project.id, title="Commented ticket")
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
        ticket = Ticket(project_id=mcp_project.id, title="Old title")
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
        ticket = Ticket(project_id=mcp_project.id, title="Status ticket", status="backlog")
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
        ticket = Ticket(project_id=mcp_project.id, title="Keep me")
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
        ticket = Ticket(project_id=mcp_project.id, title="Status err")
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
        ticket = Ticket(project_id=mcp_project.id, title="Priority err")
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

        ticket = Ticket(project_id=mcp_project.id, title="In sprint", sprint_id=sprint.id)
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
        ticket = Ticket(project_id=mcp_project.id, title="SP ticket")
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
        ticket = Ticket(project_id=mcp_project.id, title="Delete me")
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
            title="Done ticket",
            sprint_id=sprint.id,
            status="done",
        )
        undone_ticket = Ticket(
            project_id=mcp_project.id,
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
        ticket = Ticket(project_id=mcp_project.id, title="No comments")
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
        ticket = Ticket(project_id=mcp_project.id, title="With comments")
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
        ticket = Ticket(project_id=mcp_project.id, title="Comment target")
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
        ticket = Ticket(project_id=mcp_project.id, title="Empty comment target")
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
        ticket = Ticket(project_id=mcp_project.id, title="Strip target")
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
        inactive = Project(
            name="Inactive",
            slug=f"inactive-{uuid.uuid4().hex[:8]}",
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

        for status in ("todo", "in_progress", "done"):
            db_session.add(Ticket(project_id=mcp_project.id, title=f"Ticket {status}", sprint_id=sprint.id, status=status))
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
        db_session.add(Ticket(project_id=mcp_project.id, title="Backlog 1", status="backlog"))
        db_session.add(Ticket(project_id=mcp_project.id, title="Backlog 2", status="backlog"))
        db_session.add(Ticket(project_id=mcp_project.id, title="In progress", status="in_progress"))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_project_summary(ctx, mcp_project.slug)
        assert result["backlog_count"] == 2
