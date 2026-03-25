"""Testy jednostkowe MCP tools dla kryteriow akceptacji."""

import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.mcp_server import (
    _format_ticket_detail,
    add_acceptance_criterion,
    create_ticket,
    delete_acceptance_criterion,
    list_acceptance_criteria,
    update_acceptance_criterion,
)
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.user import User
from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(token: str = "test-token") -> MagicMock:
    """Mock MCP Context z Bearer token."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def mcp_user(db_session):
    user = User(
        email=f"mcp-ac-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def mcp_project(db_session):
    _slug = f"mcp-ac-{uuid.uuid4().hex[:8]}"
    project = Project(
        name="MCP AC Project",
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
    """Zamienia commit() na flush() dla izolacji testow."""
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
    return AsyncMock(return_value=mcp_user)


@pytest.fixture
async def mcp_ticket(db_session, mcp_project):
    ticket = Ticket(
        project_id=mcp_project.id,
        number=1,
        title="AC MCP Test Ticket",
        status="backlog",
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


# ---------------------------------------------------------------------------
# create_ticket z acceptance_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTicketWithAcceptanceCriteria:
    async def test_create_ticket_with_criteria_stores_them(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """create_ticket z acceptance_criteria -- kryteria zapisane w DB."""
        from sqlalchemy import select

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(
                ctx,
                mcp_project.slug,
                title="Ticket z kryteriami",
                acceptance_criteria=["Kryterium 1", "Kryterium 2", "Kryterium 3"],
            )

        assert result["acceptance_criteria_count"] == 3

        # Verify in DB
        ticket_id = uuid.UUID(result["id"])
        db_result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket_id))
        criteria = db_result.scalars().all()
        assert len(criteria) == 3

    async def test_create_ticket_criteria_are_created_via_ai(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Kryteria z create_ticket maja created_via_ai=True."""
        from sqlalchemy import select

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(
                ctx,
                mcp_project.slug,
                title="Ticket AI criteria",
                acceptance_criteria=["AI Kryterium"],
            )

        ticket_id = uuid.UUID(result["id"])
        db_result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket_id))
        crit = db_result.scalar_one()
        assert crit.created_via_ai is True

    async def test_create_ticket_criteria_position_incremental(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Kryteria maja position 0, 1, 2 (idx w liscie)."""
        from sqlalchemy import select

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(
                ctx,
                mcp_project.slug,
                title="Ticket positions",
                acceptance_criteria=["Pierwszy", "Drugi", "Trzeci"],
            )

        ticket_id = uuid.UUID(result["id"])
        db_result = await db_session.execute(
            select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket_id).order_by(TicketAcceptanceCriterion.position)
        )
        criteria = db_result.scalars().all()
        assert [c.position for c in criteria] == [0, 1, 2]

    async def test_create_ticket_empty_criteria_list_zero_count(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """create_ticket bez acceptance_criteria -- count=0."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(
                ctx,
                mcp_project.slug,
                title="Ticket bez kryteriow",
            )

        assert result["acceptance_criteria_count"] == 0

    async def test_create_ticket_criteria_whitespace_only_skipped(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Kryteria z samych spacji sa pomijane."""

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(
                ctx,
                mcp_project.slug,
                title="Ticket puste kryteria",
                acceptance_criteria=["Dobre kryterium", "   ", ""],
            )

        assert result["acceptance_criteria_count"] == 1


# ---------------------------------------------------------------------------
# add_acceptance_criterion
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAddAcceptanceCriterion:
    async def test_add_criterion_happy_path(self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify):
        """add_acceptance_criterion -- kryterium zapisane, created_via_ai=True."""
        from sqlalchemy import select

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await add_acceptance_criterion(
                ctx,
                mcp_project.slug,
                str(mcp_ticket.id),
                "Nowe kryterium via MCP",
            )

        assert "id" in result
        assert result["created_via_ai"] is True
        assert result["message"] == "Kryterium akceptacji dodane"
        assert result["position"] == 0

        # Verify in DB
        crit_id = uuid.UUID(result["id"])
        db_result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit_id))
        saved = db_result.scalar_one()
        assert saved.description == "Nowe kryterium via MCP"
        assert saved.created_via_ai is True

    async def test_add_criterion_empty_description_raises_value_error(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """Pusty opis -- ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="pusty"),
        ):
            await add_acceptance_criterion(ctx, mcp_project.slug, str(mcp_ticket.id), "   ")

    async def test_add_criterion_whitespace_description_raises_value_error(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """Opis z samych spacji -- ValueError (sprawdzenie przed sesja DB)."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="pusty"),
        ):
            await add_acceptance_criterion(ctx, mcp_project.slug, str(mcp_ticket.id), "")

    async def test_add_criterion_nonexistent_ticket_raises_value_error(
        self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify
    ):
        """Nieistniejacy ticket -- ValueError."""
        ctx = _make_ctx()
        fake_id = str(uuid.uuid4())
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await add_acceptance_criterion(ctx, mcp_project.slug, fake_id, "Kryterium")

    async def test_add_multiple_criteria_increments_position(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """Kolejne kryteria maja rosnace position."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            r1 = await add_acceptance_criterion(ctx, mcp_project.slug, str(mcp_ticket.id), "Pierwsze")
            r2 = await add_acceptance_criterion(ctx, mcp_project.slug, str(mcp_ticket.id), "Drugie")
            r3 = await add_acceptance_criterion(ctx, mcp_project.slug, str(mcp_ticket.id), "Trzecie")

        assert r1["position"] == 0
        assert r2["position"] == 1
        assert r3["position"] == 2


# ---------------------------------------------------------------------------
# update_acceptance_criterion
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateAcceptanceCriterion:
    async def _create_criterion(self, db_session, mcp_ticket, mcp_user):
        crit = TicketAcceptanceCriterion(
            ticket_id=mcp_ticket.id,
            description="Poczatkowy opis",
            position=0,
            created_by_user_id=mcp_user.id,
        )
        db_session.add(crit)
        await db_session.flush()
        return crit

    async def test_update_description(self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify):
        """Zmiana opisu -- opis zaktualizowany."""
        crit = await self._create_criterion(db_session, mcp_ticket, mcp_user)

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_acceptance_criterion(
                ctx,
                mcp_project.slug,
                str(mcp_ticket.id),
                str(crit.id),
                description="Zaktualizowany opis",
            )

        assert result["description"] == "Zaktualizowany opis"
        assert result["message"] == "Kryterium akceptacji zaktualizowane"

    async def test_toggle_is_completed_true_sets_completed_via_ai(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """is_completed=True --> completed_via_ai=True, completed_by_user_id ustawiony."""
        from sqlalchemy import select

        crit = await self._create_criterion(db_session, mcp_ticket, mcp_user)

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_acceptance_criterion(
                ctx,
                mcp_project.slug,
                str(mcp_ticket.id),
                str(crit.id),
                is_completed=True,
            )

        assert result["is_completed"] is True

        db_result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        updated = db_result.scalar_one()
        assert updated.completed_via_ai is True
        assert updated.completed_by_user_id == mcp_user.id
        assert updated.completed_at is not None

    async def test_toggle_is_completed_false_resets_fields(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """is_completed=False na ukonczone kryterium -- reset pol completed."""
        from sqlalchemy import select

        crit = TicketAcceptanceCriterion(
            ticket_id=mcp_ticket.id,
            description="Ukonczone",
            position=0,
            created_by_user_id=mcp_user.id,
            is_completed=True,
            completed_by_user_id=mcp_user.id,
            completed_at=datetime.now(UTC),
            completed_via_ai=True,
        )
        db_session.add(crit)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_acceptance_criterion(
                ctx,
                mcp_project.slug,
                str(mcp_ticket.id),
                str(crit.id),
                is_completed=False,
            )

        assert result["is_completed"] is False

        db_result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        updated = db_result.scalar_one()
        assert updated.completed_via_ai is False
        assert updated.completed_by_user_id is None
        assert updated.completed_at is None

    async def test_update_nonexistent_criterion_raises_value_error(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """Nieistniejace kryterium -- ValueError."""
        ctx = _make_ctx()
        fake_id = str(uuid.uuid4())
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await update_acceptance_criterion(
                ctx,
                mcp_project.slug,
                str(mcp_ticket.id),
                fake_id,
                description="Cos",
            )

    async def test_update_invalid_criterion_id_format_raises_value_error(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """Bledny format criterion_id -- ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="UUID"),
        ):
            await update_acceptance_criterion(
                ctx,
                mcp_project.slug,
                str(mcp_ticket.id),
                "not-a-uuid",
                description="Cos",
            )

    async def test_update_empty_description_raises_value_error(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """Pusty opis w update -- ValueError."""
        crit = await self._create_criterion(db_session, mcp_ticket, mcp_user)

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="pusty"),
        ):
            await update_acceptance_criterion(
                ctx,
                mcp_project.slug,
                str(mcp_ticket.id),
                str(crit.id),
                description="   ",
            )

    async def test_update_nonexistent_ticket_uuid_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """update_acceptance_criterion z nieistniejacym ticket UUID -- ValueError (linia 4495)."""
        ctx = _make_ctx()
        fake_ticket_id = str(uuid.uuid4())
        fake_criterion_id = str(uuid.uuid4())
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await update_acceptance_criterion(ctx, mcp_project.slug, fake_ticket_id, fake_criterion_id, description="Nowy opis")

    async def test_update_already_completed_with_true_no_change(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """is_completed=True na juz ukonczone kryterium -- brak zmiany completed_at."""
        from sqlalchemy import select

        fixed_time = datetime.now(UTC)
        crit = TicketAcceptanceCriterion(
            ticket_id=mcp_ticket.id,
            description="Juz ukonczone",
            position=0,
            created_by_user_id=mcp_user.id,
            is_completed=True,
            completed_by_user_id=mcp_user.id,
            completed_at=fixed_time,
            completed_via_ai=True,
        )
        db_session.add(crit)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_acceptance_criterion(
                ctx,
                mcp_project.slug,
                str(mcp_ticket.id),
                str(crit.id),
                is_completed=True,
            )

        # Flaga is_completed nadal True, brak duplikacji
        assert result["is_completed"] is True

        db_result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        updated = db_result.scalar_one()
        # completed_at nie zmienione (warunek sprawdza nie criterion.is_completed)
        assert updated.is_completed is True


# ---------------------------------------------------------------------------
# delete_acceptance_criterion
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteAcceptanceCriterion:
    async def test_delete_criterion_happy_path(self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify):
        """delete_acceptance_criterion -- kryterium usuniete z DB."""
        from sqlalchemy import select

        crit = TicketAcceptanceCriterion(
            ticket_id=mcp_ticket.id,
            description="Do usuniecia",
            position=0,
            created_by_user_id=mcp_user.id,
        )
        db_session.add(crit)
        await db_session.flush()
        crit_id = crit.id

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await delete_acceptance_criterion(
                ctx,
                mcp_project.slug,
                str(mcp_ticket.id),
                str(crit_id),
            )

        assert result["message"] == "Kryterium akceptacji usunięte"
        assert result["id"] == str(crit_id)

        db_result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit_id))
        assert db_result.scalar_one_or_none() is None

    async def test_delete_nonexistent_criterion_raises_value_error(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """Usuniecie nieistniejacego kryterium -- ValueError."""
        ctx = _make_ctx()
        fake_id = str(uuid.uuid4())
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await delete_acceptance_criterion(ctx, mcp_project.slug, str(mcp_ticket.id), fake_id)

    async def test_delete_invalid_criterion_id_raises_value_error(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """Bledny format criterion_id -- ValueError."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="UUID"),
        ):
            await delete_acceptance_criterion(ctx, mcp_project.slug, str(mcp_ticket.id), "invalid-uuid")

    async def test_delete_criterion_wrong_ticket_raises_value_error(
        self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify
    ):
        """Kryterium nalezy do innego ticketa -- ValueError."""
        # Create a second ticket and criterion on it
        other_ticket = Ticket(
            project_id=mcp_project.id,
            number=99,
            title="Other ticket",
            status="backlog",
        )
        db_session.add(other_ticket)
        await db_session.flush()

        crit = TicketAcceptanceCriterion(
            ticket_id=other_ticket.id,
            description="Kryterium innego ticketa",
            position=0,
            created_by_user_id=mcp_user.id,
        )
        db_session.add(crit)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            # Try to delete using mcp_ticket.id but criterion belongs to other_ticket
            await delete_acceptance_criterion(ctx, mcp_project.slug, str(mcp_ticket.id), str(crit.id))

    async def test_delete_nonexistent_ticket_uuid_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """delete_acceptance_criterion z nieistniejacym ticket UUID -- ValueError (linia 4554)."""
        ctx = _make_ctx()
        # Prawidlowy UUID formatu, ale ticket nie istnieje w DB
        fake_ticket_id = str(uuid.uuid4())
        fake_criterion_id = str(uuid.uuid4())
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await delete_acceptance_criterion(ctx, mcp_project.slug, fake_ticket_id, fake_criterion_id)


# ---------------------------------------------------------------------------
# list_acceptance_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListAcceptanceCriteria:
    async def test_empty_list_returns_message(self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify):
        """Brak kryteriow -- zwraca komunikat o braku."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_acceptance_criteria(ctx, mcp_project.slug, str(mcp_ticket.id))

        assert "Brak kryteriów" in result

    async def test_list_returns_criteria(self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify):
        """Lista z kryteriami -- zwraca tabele."""
        for i in range(2):
            crit = TicketAcceptanceCriterion(
                ticket_id=mcp_ticket.id,
                description=f"Kryterium {i + 1}",
                position=i,
                created_by_user_id=mcp_user.id,
            )
            db_session.add(crit)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_acceptance_criteria(ctx, mcp_project.slug, str(mcp_ticket.id))

        assert "2 pozycji" in result
        assert "Kryterium 1" in result
        assert "Kryterium 2" in result

    async def test_list_shows_completed_status(self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify):
        """Ukonczone kryterium -- checkbox [x] w output."""
        crit = TicketAcceptanceCriterion(
            ticket_id=mcp_ticket.id,
            description="Ukonczone kryterium",
            position=0,
            created_by_user_id=mcp_user.id,
            is_completed=True,
            completed_by_user_id=mcp_user.id,
            completed_at=datetime.now(UTC),
        )
        db_session.add(crit)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_acceptance_criteria(ctx, mcp_project.slug, str(mcp_ticket.id))

        assert "[x]" in result
        assert "ukonczone" in result

    async def test_list_shows_uncompleted_status(self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify):
        """Nieukonczone kryterium -- checkbox [ ] w output."""
        crit = TicketAcceptanceCriterion(
            ticket_id=mcp_ticket.id,
            description="Nieukonczone kryterium",
            position=0,
            created_by_user_id=mcp_user.id,
        )
        db_session.add(crit)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_acceptance_criteria(ctx, mcp_project.slug, str(mcp_ticket.id))

        assert "[ ]" in result

    async def test_list_shows_ai_flag(self, db_session, mcp_user, mcp_project, mcp_member, mcp_ticket, mock_factory, mock_verify):
        """Kryterium z created_via_ai=True -- flaga (AI) w output."""
        crit = TicketAcceptanceCriterion(
            ticket_id=mcp_ticket.id,
            description="AI kryterium",
            position=0,
            created_by_user_id=mcp_user.id,
            created_via_ai=True,
        )
        db_session.add(crit)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_acceptance_criteria(ctx, mcp_project.slug, str(mcp_ticket.id))

        assert "(AI)" in result

    async def test_list_nonexistent_ticket_raises_value_error(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        """Nieistniejacy ticket -- ValueError."""
        ctx = _make_ctx()
        fake_id = str(uuid.uuid4())
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await list_acceptance_criteria(ctx, mcp_project.slug, fake_id)


# ---------------------------------------------------------------------------
# _format_ticket_detail z sekcja Acceptance Criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatTicketDetailAcceptanceCriteria:
    """Testy sekcji Acceptance Criteria w _format_ticket_detail."""

    def _make_ticket_mock(self, criteria: list) -> MagicMock:
        """Tworzy mock ticketa z potrzebnymi polami."""
        ticket = MagicMock(spec=Ticket)
        ticket.number = 1
        ticket.title = "Test Ticket"
        ticket.status = "backlog"
        ticket.priority = "medium"
        ticket.sprint = None
        ticket.assignee = None
        ticket.due_date = None
        ticket.labels = []
        ticket.story_points = None
        ticket.created_at = MagicMock()
        ticket.created_at.date.return_value.isoformat.return_value = "2026-03-01"
        ticket.updated_at = MagicMock()
        ticket.updated_at.date.return_value.isoformat.return_value = "2026-03-10"
        ticket.created_via_ai = False
        ticket.id = uuid.uuid4()
        ticket.description = None
        ticket.attachments = []
        ticket.comments = []
        ticket.acceptance_criteria = criteria
        return ticket

    def _make_criterion_mock(
        self,
        description: str,
        is_completed: bool = False,
        created_via_ai: bool = False,
    ) -> MagicMock:
        crit = MagicMock(spec=TicketAcceptanceCriterion)
        crit.description = description
        crit.is_completed = is_completed
        crit.created_via_ai = created_via_ai
        return crit

    def test_format_no_criteria_no_section(self):
        """Brak kryteriow -- brak sekcji Acceptance Criteria."""
        ticket = self._make_ticket_mock(criteria=[])
        result = _format_ticket_detail(ticket, "TST")
        assert "Acceptance Criteria" not in result

    def test_format_with_criteria_shows_section(self):
        """Z kryteriami -- sekcja Acceptance Criteria pojawia sie."""
        crit = self._make_criterion_mock("Kryterium testowe")
        ticket = self._make_ticket_mock(criteria=[crit])
        result = _format_ticket_detail(ticket, "TST")
        assert "## Acceptance Criteria (1)" in result
        assert "Kryterium testowe" in result

    def test_format_completed_criterion_checkbox_x(self):
        """Ukonczone kryterium -- [x] w output."""
        crit = self._make_criterion_mock("Ukonczone", is_completed=True)
        ticket = self._make_ticket_mock(criteria=[crit])
        result = _format_ticket_detail(ticket, "TST")
        assert "[x] Ukonczone" in result

    def test_format_uncompleted_criterion_checkbox_empty(self):
        """Nieukonczone kryterium -- [ ] w output."""
        crit = self._make_criterion_mock("Nieukonczone", is_completed=False)
        ticket = self._make_ticket_mock(criteria=[crit])
        result = _format_ticket_detail(ticket, "TST")
        assert "[ ] Nieukonczone" in result

    def test_format_ai_criterion_shows_ai_flag(self):
        """Kryterium AI -- flaga (AI) w output."""
        crit = self._make_criterion_mock("AI kryterium", created_via_ai=True)
        ticket = self._make_ticket_mock(criteria=[crit])
        result = _format_ticket_detail(ticket, "TST")
        assert "(AI)" in result

    def test_format_non_ai_criterion_no_ai_flag(self):
        """Kryterium bez AI -- brak flagi (AI)."""
        crit = self._make_criterion_mock("Human kryterium", created_via_ai=False)
        ticket = self._make_ticket_mock(criteria=[crit])
        result = _format_ticket_detail(ticket, "TST")
        assert "(AI)" not in result

    def test_format_multiple_criteria_shows_count(self):
        """Wiele kryteriow -- poprawna liczba w naglowku sekcji."""
        criteria = [self._make_criterion_mock(f"Kryterium {i}") for i in range(4)]
        ticket = self._make_ticket_mock(criteria=criteria)
        result = _format_ticket_detail(ticket, "TST")
        assert "## Acceptance Criteria (4)" in result

    def test_format_none_criteria_no_section(self):
        """acceptance_criteria=None -- brak sekcji."""
        ticket = self._make_ticket_mock(criteria=None)
        # None jest falsy -- list() na None rzuci blad, ale kod sprawdza falsiness
        # Sprawdzamy ze nie rzuca wyjatku
        ticket.acceptance_criteria = None
        result = _format_ticket_detail(ticket, "TST")
        assert "Acceptance Criteria" not in result
