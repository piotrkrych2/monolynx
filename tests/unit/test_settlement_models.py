"""Testy jednostkowe dla modeli Settlement i serwisu settlements."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Import modeli
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_settlement_models_importable():
    from monolynx.models import (
        Settlement,
        SettlementAttachment,
        SettlementProject,
        SettlementTicket,
    )

    assert Settlement is not None
    assert SettlementAttachment is not None
    assert SettlementProject is not None
    assert SettlementTicket is not None


# ---------------------------------------------------------------------------
# M2M relationship attributes na modelach
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ticket_has_settlements_relationship():
    from monolynx.models import Ticket

    assert hasattr(Ticket, "settlements")


@pytest.mark.unit
def test_project_has_settlements_relationship():
    from monolynx.models import Project

    assert hasattr(Project, "settlements")


# ---------------------------------------------------------------------------
# get_next_settlement_number — pusta baza
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_next_settlement_number_empty(db_session):
    """Na pustej tabeli settlements zwraca 1."""
    from monolynx.services.settlements import get_next_settlement_number

    number = await get_next_settlement_number(db_session)
    assert number == 1


# ---------------------------------------------------------------------------
# get_next_settlement_number — po insercie
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_next_settlement_number_increments(db_session):
    """Zwraca MAX(number)+1 po wstawieniu rekordów."""
    from monolynx.models import Settlement, User
    from monolynx.services.settlements import get_next_settlement_number

    user = (await db_session.execute(select(User).limit(1))).scalar_one_or_none()
    if user is None:
        user = User(
            email="test_settle_incr@example.com",
            password_hash=hash_password("test"),
        )
        db_session.add(user)
        await db_session.flush()

    s1 = Settlement(
        number=1,
        name="Test1",
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        created_by_id=user.id,
    )
    db_session.add(s1)
    await db_session.flush()

    next_num = await get_next_settlement_number(db_session)
    assert next_num == 2

    s2 = Settlement(
        number=5,
        name="Test5",
        period_from=date(2026, 2, 1),
        period_to=date(2026, 2, 28),
        created_by_id=user.id,
    )
    db_session.add(s2)
    await db_session.flush()

    next_num = await get_next_settlement_number(db_session)
    assert next_num == 6  # MAX(1, 5) + 1


# ---------------------------------------------------------------------------
# M2M zapis i odczyt — settlement.tickets i settlement.projects
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_settlement_m2m_tickets_and_projects(db_session):
    """Settlement poprawnie przechowuje i odczytuje M2M do Ticket i Project."""
    from monolynx.models import Project, Settlement, Ticket, User

    user = (await db_session.execute(select(User).limit(1))).scalar_one_or_none()
    if user is None:
        user = User(
            email="test_m2m_settle@example.com",
            password_hash=hash_password("x"),
        )
        db_session.add(user)
        await db_session.flush()

    project = (await db_session.execute(select(Project).limit(1))).scalar_one_or_none()
    if project is None:
        import secrets

        project = Project(
            name="Settlement Test Project",
            slug="settlement-test-project",
            code="STP",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

    ticket = Ticket(
        project_id=project.id,
        number=99999,
        title="M2M settlement test",
        status="todo",
        priority="medium",
    )
    db_session.add(ticket)
    await db_session.flush()

    s = Settlement(
        number=88888,
        name="M2M Settlement",
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        created_by_id=user.id,
    )
    s.projects.append(project)
    s.tickets.append(ticket)
    db_session.add(s)
    await db_session.flush()

    # Przeładuj z eagerly loaded relacjami
    s_loaded = (
        await db_session.execute(
            select(Settlement).where(Settlement.id == s.id).options(selectinload(Settlement.projects), selectinload(Settlement.tickets))
        )
    ).scalar_one()

    assert len(s_loaded.projects) == 1
    assert s_loaded.projects[0].id == project.id
    assert len(s_loaded.tickets) == 1
    assert s_loaded.tickets[0].id == ticket.id
