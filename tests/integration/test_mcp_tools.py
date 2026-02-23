"""Testy integracyjne narzedzi MCP Scrum."""

import uuid

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.services.auth import hash_password
from monolynx.services.mcp_auth import generate_api_token, hash_token


@pytest.fixture
async def mcp_setup(db_session):
    """Setup: user + token + project + membership."""
    user = User(
        email=f"mcp-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass"),
    )
    db_session.add(user)
    await db_session.flush()

    raw_token, token_hash = generate_api_token()
    api_token = UserApiToken(
        user_id=user.id,
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        name="Test MCP Token",
    )
    db_session.add(api_token)

    project = Project(
        name="MCP Test Project",
        slug=f"mcp-test-{uuid.uuid4().hex[:8]}",
        api_key=f"key-{uuid.uuid4().hex}",
    )
    db_session.add(project)
    await db_session.flush()

    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role="owner",
    )
    db_session.add(member)
    await db_session.flush()

    return user, raw_token, project


@pytest.mark.integration
class TestMcpAuth:
    async def test_verify_valid_token(self, db_session, mcp_setup):
        """verify_mcp_token z prawidlowym tokenem -- token istnieje w DB."""
        _user, raw_token, _project = mcp_setup
        hashed = hash_token(raw_token)
        result = await db_session.execute(select(UserApiToken).where(UserApiToken.token_hash == hashed))
        token_obj = result.scalar_one_or_none()
        assert token_obj is not None
        assert token_obj.is_active is True

    async def test_token_belongs_to_user(self, db_session, mcp_setup):
        """Token jest powiazany z wlasciwym userem."""
        user, raw_token, _project = mcp_setup
        hashed = hash_token(raw_token)
        result = await db_session.execute(select(UserApiToken).where(UserApiToken.token_hash == hashed))
        token_obj = result.scalar_one()
        assert token_obj.user_id == user.id

    async def test_inactive_token_not_found(self, db_session, mcp_setup):
        """Dezaktywowany token nie powinien byc uznany za aktywny."""
        _user, raw_token, _project = mcp_setup
        hashed = hash_token(raw_token)
        result = await db_session.execute(
            select(UserApiToken).where(
                UserApiToken.token_hash == hashed,
                UserApiToken.is_active.is_(True),
            )
        )
        token_obj = result.scalar_one()
        token_obj.is_active = False
        await db_session.flush()

        result2 = await db_session.execute(
            select(UserApiToken).where(
                UserApiToken.token_hash == hashed,
                UserApiToken.is_active.is_(True),
            )
        )
        assert result2.scalar_one_or_none() is None


@pytest.mark.integration
class TestTicketAiFlag:
    async def test_ticket_created_via_ai(self, db_session, mcp_setup):
        """Ticket z created_via_ai=True poprawnie sie zapisuje."""
        _user, _, project = mcp_setup
        ticket = Ticket(
            project_id=project.id,
            title="AI Ticket",
            created_via_ai=True,
        )
        db_session.add(ticket)
        await db_session.flush()

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        t = result.scalar_one()
        assert t.created_via_ai is True

    async def test_ticket_default_not_ai(self, db_session, mcp_setup):
        """Domyslnie created_via_ai jest False."""
        _user, _, project = mcp_setup
        ticket = Ticket(
            project_id=project.id,
            title="Normal Ticket",
        )
        db_session.add(ticket)
        await db_session.flush()

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        t = result.scalar_one()
        assert t.created_via_ai is False


@pytest.mark.integration
class TestProjectMembership:
    async def test_user_is_member(self, db_session, mcp_setup):
        """User z mcp_setup jest memberem projektu."""
        user, _, project = mcp_setup
        result = await db_session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        member = result.scalar_one_or_none()
        assert member is not None
        assert member.role == "owner"

    async def test_non_member_not_found(self, db_session, mcp_setup):
        """Obcy user nie jest memberem projektu."""
        _, _, project = mcp_setup
        other_user = User(
            email=f"other-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
        )
        db_session.add(other_user)
        await db_session.flush()

        result = await db_session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == other_user.id,
            )
        )
        assert result.scalar_one_or_none() is None
