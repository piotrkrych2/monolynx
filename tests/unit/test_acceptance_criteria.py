"""Testy jednostkowe modelu TicketAcceptanceCriterion."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.user import User
from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(suffix: str) -> User:
    return User(
        email=f"ac-unit-{suffix}@test.com",
        password_hash=hash_password("testpass"),
    )


def _make_project(suffix: str) -> Project:
    return Project(
        name=f"AC Project {suffix}",
        slug=f"ac-project-{suffix}",
        code=f"ACP{suffix[:3].upper()}",
        api_key=f"key-{uuid.uuid4().hex}",
        is_active=True,
    )


def _make_ticket(project_id: uuid.UUID, number: int = 1) -> Ticket:
    return Ticket(
        project_id=project_id,
        number=number,
        title=f"AC Test Ticket #{number}",
        status="backlog",
    )


def _make_criterion(ticket_id: uuid.UUID, user_id: uuid.UUID, **kwargs) -> TicketAcceptanceCriterion:
    return TicketAcceptanceCriterion(
        ticket_id=ticket_id,
        description=kwargs.get("description", "Kryterium testowe"),
        position=kwargs.get("position", 0),
        created_by_user_id=user_id,
        is_completed=kwargs.get("is_completed", False),
        created_via_ai=kwargs.get("created_via_ai", False),
    )


# ---------------------------------------------------------------------------
# Testy modelu
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTicketAcceptanceCriterionModel:
    """Testy instancji modelu -- wartosci jawnie ustawione i DB defaults po flushu."""

    # --- Testy jawnych wartosci (nie wymagaja DB) ---

    def test_explicit_is_completed_false(self):
        """Jawnie ustawione is_completed=False."""
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="Test",
            created_by_user_id=uuid.uuid4(),
            is_completed=False,
        )
        assert crit.is_completed is False

    def test_explicit_is_completed_true(self):
        """Jawnie ustawione is_completed=True."""
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="Test",
            created_by_user_id=uuid.uuid4(),
            is_completed=True,
        )
        assert crit.is_completed is True

    def test_explicit_position(self):
        """Jawnie ustawiony position."""
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="Test",
            created_by_user_id=uuid.uuid4(),
            position=5,
        )
        assert crit.position == 5

    def test_explicit_created_via_ai_false(self):
        """Jawnie ustawione created_via_ai=False."""
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="Test",
            created_by_user_id=uuid.uuid4(),
            created_via_ai=False,
        )
        assert crit.created_via_ai is False

    def test_explicit_created_via_ai_true(self):
        """Jawnie ustawione created_via_ai=True."""
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="AI criterion",
            created_by_user_id=uuid.uuid4(),
            created_via_ai=True,
        )
        assert crit.created_via_ai is True

    def test_explicit_completed_via_ai_false(self):
        """Jawnie ustawione completed_via_ai=False."""
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="Test",
            created_by_user_id=uuid.uuid4(),
            completed_via_ai=False,
        )
        assert crit.completed_via_ai is False

    def test_default_completed_by_user_id_is_none(self):
        """Domyslnie completed_by_user_id=None."""
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="Test",
            created_by_user_id=uuid.uuid4(),
        )
        assert crit.completed_by_user_id is None

    def test_default_completed_at_is_none(self):
        """Domyslnie completed_at=None."""
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="Test",
            created_by_user_id=uuid.uuid4(),
        )
        assert crit.completed_at is None

    def test_explicit_id_assigned(self):
        """UUID id jest ustawiany gdy podany jawnie."""
        explicit_id = uuid.uuid4()
        crit = TicketAcceptanceCriterion(
            id=explicit_id,
            ticket_id=uuid.uuid4(),
            description="Test",
            created_by_user_id=uuid.uuid4(),
        )
        assert crit.id == explicit_id
        assert isinstance(crit.id, uuid.UUID)

    def test_set_description(self):
        """Opis jest ustawiany prawidlowo."""
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="Moje kryterium",
            created_by_user_id=uuid.uuid4(),
        )
        assert crit.description == "Moje kryterium"

    def test_completed_fields_set_together(self):
        """Ustawienie pol completed razem z is_completed=True."""
        user_id = uuid.uuid4()
        now = datetime.now(UTC)
        crit = TicketAcceptanceCriterion(
            ticket_id=uuid.uuid4(),
            description="Test",
            created_by_user_id=uuid.uuid4(),
            is_completed=True,
            completed_by_user_id=user_id,
            completed_at=now,
            completed_via_ai=True,
        )
        assert crit.is_completed is True
        assert crit.completed_by_user_id == user_id
        assert crit.completed_at == now
        assert crit.completed_via_ai is True

    # --- Testy wartosci domyslnych z DB (server_default) ---

    async def test_db_default_is_completed_false(self, db_session):
        """Po flushu server_default is_completed=False."""
        from sqlalchemy import select

        user = _make_user("dbdv01")
        project = _make_project("dbdv01")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        crit = TicketAcceptanceCriterion(
            ticket_id=ticket.id,
            description="Test defaults",
            created_by_user_id=user.id,
        )
        db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        saved = result.scalar_one()
        assert saved.is_completed is False

    async def test_db_default_created_via_ai_false(self, db_session):
        """Po flushu server_default created_via_ai=False."""
        from sqlalchemy import select

        user = _make_user("dbdv02")
        project = _make_project("dbdv02")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        crit = TicketAcceptanceCriterion(
            ticket_id=ticket.id,
            description="Test defaults",
            created_by_user_id=user.id,
        )
        db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        saved = result.scalar_one()
        assert saved.created_via_ai is False

    async def test_db_default_completed_via_ai_false(self, db_session):
        """Po flushu server_default completed_via_ai=False."""
        from sqlalchemy import select

        user = _make_user("dbdv03")
        project = _make_project("dbdv03")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        crit = TicketAcceptanceCriterion(
            ticket_id=ticket.id,
            description="Test defaults",
            created_by_user_id=user.id,
        )
        db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        saved = result.scalar_one()
        assert saved.completed_via_ai is False

    async def test_db_default_position_zero(self, db_session):
        """Po flushu default position=0."""
        from sqlalchemy import select

        user = _make_user("dbdv04")
        project = _make_project("dbdv04")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        crit = TicketAcceptanceCriterion(
            ticket_id=ticket.id,
            description="Test defaults",
            created_by_user_id=user.id,
        )
        db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        saved = result.scalar_one()
        assert saved.position == 0

    async def test_db_id_auto_generated(self, db_session):
        """UUID id jest automatycznie generowane przez default=uuid.uuid4."""
        from sqlalchemy import select

        user = _make_user("dbdv05")
        project = _make_project("dbdv05")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        crit = TicketAcceptanceCriterion(
            ticket_id=ticket.id,
            description="Test",
            created_by_user_id=user.id,
        )
        db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        saved = result.scalar_one()
        assert saved.id is not None
        assert isinstance(saved.id, uuid.UUID)


# ---------------------------------------------------------------------------
# Testy DB: persystencja i relacje
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTicketAcceptanceCriterionPersistence:
    """Testy zapisu i odczytu z bazy danych."""

    async def test_criterion_saved_to_db(self, db_session):
        """Kryterium jest zapisywane do bazy."""
        user = _make_user("save01")
        project = _make_project("s01")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id, 1)
        db_session.add(ticket)
        await db_session.flush()

        crit = _make_criterion(ticket.id, user.id)
        db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        saved = result.scalar_one_or_none()
        assert saved is not None
        assert saved.description == "Kryterium testowe"

    async def test_criterion_default_is_completed_db(self, db_session):
        """Po zapisie do DB is_completed jest False."""
        user = _make_user("save02")
        project = _make_project("s02")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id, 1)
        db_session.add(ticket)
        await db_session.flush()

        crit = _make_criterion(ticket.id, user.id)
        db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        saved = result.scalar_one()
        assert saved.is_completed is False

    async def test_criterion_created_via_ai_saved(self, db_session):
        """created_via_ai=True jest zapisywane do DB."""
        user = _make_user("save03")
        project = _make_project("s03")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id, 1)
        db_session.add(ticket)
        await db_session.flush()

        crit = _make_criterion(ticket.id, user.id, created_via_ai=True)
        db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        saved = result.scalar_one()
        assert saved.created_via_ai is True

    async def test_multiple_criteria_for_ticket(self, db_session):
        """Ticket moze miec wiele kryteriow."""
        user = _make_user("save04")
        project = _make_project("s04")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id, 1)
        db_session.add(ticket)
        await db_session.flush()

        for i in range(3):
            crit = _make_criterion(ticket.id, user.id, description=f"Kryterium {i}", position=i)
            db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id))
        criteria = result.scalars().all()
        assert len(criteria) == 3

    async def test_criteria_ordered_by_position(self, db_session):
        """Kryteria sa posortowane po position."""
        user = _make_user("save05")
        project = _make_project("s05")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id, 1)
        db_session.add(ticket)
        await db_session.flush()

        for i in [2, 0, 1]:
            crit = _make_criterion(ticket.id, user.id, description=f"Pos {i}", position=i)
            db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(
            select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id).order_by(TicketAcceptanceCriterion.position)
        )
        criteria = result.scalars().all()
        positions = [c.position for c in criteria]
        assert positions == [0, 1, 2]

    async def test_cascade_delete_with_ticket(self, db_session):
        """Usuniecie ticketa usuwa jego kryteria (CASCADE)."""
        user = _make_user("cascade01")
        project = _make_project("cas01")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id, 1)
        db_session.add(ticket)
        await db_session.flush()

        for i in range(2):
            crit = _make_criterion(ticket.id, user.id, position=i)
            db_session.add(crit)
        await db_session.flush()

        # Verify criteria exist
        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id))
        assert len(result.scalars().all()) == 2

        # Delete ticket
        await db_session.delete(ticket)
        await db_session.flush()

        # Criteria should be gone
        result2 = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id))
        assert len(result2.scalars().all()) == 0

    async def test_completed_fields_persisted(self, db_session):
        """Pola completed sa zapisywane do DB."""
        user = _make_user("comp01")
        project = _make_project("cp01")
        db_session.add(user)
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id, 1)
        db_session.add(ticket)
        await db_session.flush()

        now = datetime.now(UTC)
        crit = _make_criterion(ticket.id, user.id)
        crit.is_completed = True
        crit.completed_by_user_id = user.id
        crit.completed_at = now
        crit.completed_via_ai = True
        db_session.add(crit)
        await db_session.flush()

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        saved = result.scalar_one()
        assert saved.is_completed is True
        assert saved.completed_by_user_id == user.id
        assert saved.completed_via_ai is True
        assert saved.completed_at is not None
