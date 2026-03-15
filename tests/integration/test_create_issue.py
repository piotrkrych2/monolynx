"""Testy integracyjne -- narzedzie MCP create_issue."""

import secrets
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from monolynx.mcp_server import create_issue
from monolynx.models.event import Event
from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.models.user import User
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


async def _create_project_and_user(db_session):
    """Tworzy uzytkownika i projekt testowy."""
    user = User(
        email=f"ci-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass"),
    )
    db_session.add(user)
    await db_session.flush()

    slug = f"ci-{secrets.token_hex(4)}"
    project = Project(
        name=f"Create Issue Test {slug}",
        slug=slug,
        code="CI" + secrets.token_hex(3).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return user, project


def _make_mock_factory(db_session):
    """Zwraca async context manager zastepcza dla async_session_factory.

    Podmienia commit() na flush(), zeby nie przerywac testowej transakcji.
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


# ---------------------------------------------------------------------------
# Happy path -- minimalne dane
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateIssueMinimal:
    async def test_creates_issue_with_title_only(self, db_session):
        """create_issue z samym title tworzy Issue ze statusem unresolved."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(ctx, project.slug, title="NullPointerException")

        assert result["title"] == "NullPointerException"
        assert result["status"] == "unresolved"
        assert result["severity"] == "medium"
        assert result["source"] == "manual"
        assert "id" in result
        assert "created_at" in result

    async def test_creates_issue_in_database(self, db_session):
        """Issue jest faktycznie zapisany w bazie danych."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(ctx, project.slug, title="DatabaseTestError")

        issue_id = uuid.UUID(result["id"])
        db_result = await db_session.execute(select(Issue).where(Issue.id == issue_id))
        issue = db_result.scalar_one_or_none()
        assert issue is not None
        assert issue.title == "DatabaseTestError"
        assert issue.source == "manual"
        assert issue.status == "unresolved"
        assert issue.event_count == 1

    async def test_creates_associated_event(self, db_session):
        """Po stworzeniu Issue istnieje powiazany Event."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(ctx, project.slug, title="EventCheckError")

        issue_id = uuid.UUID(result["id"])
        ev_result = await db_session.execute(select(Event).where(Event.issue_id == issue_id))
        events = ev_result.scalars().all()
        assert len(events) == 1
        event = events[0]
        assert event.exception["type"] == "EventCheckError"


# ---------------------------------------------------------------------------
# Happy path -- pelne dane
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateIssueFullData:
    async def test_creates_issue_with_all_params(self, db_session):
        """create_issue z description, severity, environment, traceback."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(
                ctx,
                project.slug,
                title="CriticalDatabaseError",
                description="Baza danych nie odpowiada",
                severity="critical",
                environment="production",
                traceback="Traceback (most recent call last):\n  File 'db.py', line 10",
            )

        assert result["severity"] == "critical"

        issue_id = uuid.UUID(result["id"])
        db_result = await db_session.execute(select(Issue).where(Issue.id == issue_id))
        issue = db_result.scalar_one_or_none()
        assert issue is not None
        assert issue.level == "critical"
        assert issue.event_count == 1

    async def test_event_stores_description_in_exception_jsonb(self, db_session):
        """Pole description jest zapisane w Event.exception['value']."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(
                ctx,
                project.slug,
                title="DescriptionTest",
                description="Szczegolowy opis bledu",
            )

        issue_id = uuid.UUID(result["id"])
        ev_result = await db_session.execute(select(Event).where(Event.issue_id == issue_id))
        event = ev_result.scalar_one()
        assert event.exception["value"] == "Szczegolowy opis bledu"
        assert event.exception["type"] == "DescriptionTest"

    async def test_event_stores_traceback_in_exception_jsonb(self, db_session):
        """Pole traceback jest zapisane w Event.exception['traceback']."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        tb = "Traceback:\n  File test.py, line 42"
        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(
                ctx,
                project.slug,
                title="TracebackTest",
                traceback=tb,
            )

        issue_id = uuid.UUID(result["id"])
        ev_result = await db_session.execute(select(Event).where(Event.issue_id == issue_id))
        event = ev_result.scalar_one()
        assert event.exception["traceback"] == tb

    async def test_event_stores_environment_in_jsonb(self, db_session):
        """Pole environment jest zapisane w Event.environment jako dict."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(
                ctx,
                project.slug,
                title="EnvTest",
                environment="staging",
            )

        issue_id = uuid.UUID(result["id"])
        ev_result = await db_session.execute(select(Event).where(Event.issue_id == issue_id))
        event = ev_result.scalar_one()
        assert event.environment == {"environment": "staging"}

    async def test_event_environment_none_when_not_provided(self, db_session):
        """Brak parametru environment -> Event.environment jest None."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(ctx, project.slug, title="NoEnvTest")

        issue_id = uuid.UUID(result["id"])
        ev_result = await db_session.execute(select(Event).where(Event.issue_id == issue_id))
        event = ev_result.scalar_one()
        assert event.environment is None


# ---------------------------------------------------------------------------
# Walidacja -- pusty title
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateIssueValidation:
    async def test_empty_title_raises_value_error(self, db_session):
        """Pusty title -> ValueError."""
        _user, project = await _create_project_and_user(db_session)
        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth), pytest.raises(ValueError, match="pusty"):
            await create_issue(ctx, project.slug, title="")

    async def test_whitespace_only_title_raises_value_error(self, db_session):
        """Title tylko z bialych znakow -> ValueError."""
        _user, project = await _create_project_and_user(db_session)
        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth), pytest.raises(ValueError, match="pusty"):
            await create_issue(ctx, project.slug, title="   ")

    async def test_title_too_long_raises_value_error(self, db_session):
        """Title dluzszy niz 512 znakow -> ValueError."""
        _user, project = await _create_project_and_user(db_session)
        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        long_title = "A" * 513
        with patch("monolynx.mcp_server._get_user_and_project", mock_auth), pytest.raises(ValueError, match="512"):
            await create_issue(ctx, project.slug, title=long_title)

    async def test_title_exactly_512_chars_is_accepted(self, db_session):
        """Title o dlugosci dokladnie 512 znakow jest akceptowany."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        title_512 = "A" * 512
        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(ctx, project.slug, title=title_512)

        assert result["title"] == title_512

    async def test_invalid_severity_raises_value_error(self, db_session):
        """Nieprawidlowy severity -> ValueError."""
        _user, project = await _create_project_and_user(db_session)
        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth), pytest.raises(ValueError, match="Severity"):
            await create_issue(ctx, project.slug, title="Test", severity="extreme")

    async def test_invalid_environment_raises_value_error(self, db_session):
        """Nieprawidlowy environment -> ValueError."""
        _user, project = await _create_project_and_user(db_session)
        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth), pytest.raises(ValueError, match="Environment"):
            await create_issue(ctx, project.slug, title="Test", environment="local")


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateIssueReturnShape:
    async def test_return_shape_has_all_required_keys(self, db_session):
        """Zwrocony dict zawiera wszystkie wymagane klucze."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(ctx, project.slug, title="ShapeTest")

        required_keys = {"id", "title", "status", "severity", "source", "created_at"}
        assert required_keys <= set(result.keys())

    async def test_id_is_valid_uuid_string(self, db_session):
        """Pole id jest poprawnym stringiem UUID."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(ctx, project.slug, title="UuidTest")

        uuid.UUID(result["id"])  # nie rzuca wyjatku

    async def test_source_is_manual(self, db_session):
        """Pole source ma wartosc 'manual'."""
        _user, project = await _create_project_and_user(db_session)
        mock_factory = _make_mock_factory(db_session)

        mock_auth = AsyncMock(return_value=(_user, project))
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth),
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
        ):
            result = await create_issue(ctx, project.slug, title="SourceTest")

        assert result["source"] == "manual"
