"""Smoke testy modelu WorkPlanEntry — weryfikacja ze model i migracja dzialaja na poziomie DB."""

from __future__ import annotations

import secrets
import uuid
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Import + rejestracja w __all__
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_model_importable_and_in_all():
    """WorkPlanEntry jest importowalny i widnieje w models.__all__."""
    import monolynx.models as m
    from monolynx.models import WorkPlanEntry

    assert WorkPlanEntry is not None
    assert "WorkPlanEntry" in m.__all__


@pytest.mark.unit
def test_model_has_expected_columns():
    """WorkPlanEntry eksponuje wszystkie wymagane kolumny."""
    from monolynx.models import WorkPlanEntry

    mapper = WorkPlanEntry.__mapper__
    column_names = {c.key for c in mapper.columns}

    expected = {"id", "user_id", "ticket_id", "scheduled_date", "position", "notes", "created_at", "updated_at"}
    assert expected.issubset(column_names)


@pytest.mark.unit
def test_model_has_relationships():
    """WorkPlanEntry ma relacje user i ticket."""
    from monolynx.models import WorkPlanEntry

    mapper = WorkPlanEntry.__mapper__
    relationship_names = {r.key for r in mapper.relationships}

    assert "user" in relationship_names
    assert "ticket" in relationship_names


# ---------------------------------------------------------------------------
# Helpers — tworzenie minimalnych encji potrzebnych do testow
# ---------------------------------------------------------------------------


async def _make_user(db, suffix: str):
    from monolynx.models import User

    user = User(
        email=f"work_plan_{suffix}@example.com",
        password_hash=hash_password("test"),
    )
    db.add(user)
    await db.flush()
    return user


async def _make_project(db, suffix: str):
    from monolynx.models import Project

    project = Project(
        name=f"WP Project {suffix}",
        slug=f"wp-project-{suffix}",
        code="WPT",
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db.add(project)
    await db.flush()
    return project


async def _make_ticket(db, project, number: int):
    from monolynx.models import Ticket

    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=f"Ticket #{number}",
        status="todo",
        priority="medium",
    )
    db.add(ticket)
    await db.flush()
    return ticket


# ---------------------------------------------------------------------------
# Happy path — podstawowy zapis
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_can_create_entry(db_session):
    """Happy path: tworzy WorkPlanEntry i weryfikuje pola."""
    from monolynx.models import WorkPlanEntry

    user = await _make_user(db_session, "create")
    project = await _make_project(db_session, "create")
    ticket = await _make_ticket(db_session, project, number=10001)

    entry = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 20),
    )
    db_session.add(entry)
    await db_session.flush()

    assert entry.id is not None
    assert isinstance(entry.id, uuid.UUID)
    assert entry.position == 0
    assert entry.notes is None
    assert entry.created_at is not None
    assert entry.updated_at is not None
    assert entry.scheduled_date == date(2026, 5, 20)


@pytest.mark.integration
async def test_position_and_notes_persist(db_session):
    """Entry z position=5 i notes='test' zapisuje sie poprawnie."""
    from monolynx.models import WorkPlanEntry

    user = await _make_user(db_session, "notes")
    project = await _make_project(db_session, "notes")
    ticket = await _make_ticket(db_session, project, number=10002)

    entry = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 21),
        position=5,
        notes="Notatka testowa",
    )
    db_session.add(entry)
    await db_session.flush()

    loaded = (await db_session.execute(select(WorkPlanEntry).where(WorkPlanEntry.id == entry.id))).scalar_one()

    assert loaded.position == 5
    assert loaded.notes == "Notatka testowa"


# ---------------------------------------------------------------------------
# Unique constraint (user_id, ticket_id, scheduled_date)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_unique_constraint_same_user_ticket_date_raises(db_session):
    """Drugi entry z tym samym (user, ticket, date) rzuca IntegrityError."""
    from monolynx.models import WorkPlanEntry

    user = await _make_user(db_session, "uniq1")
    project = await _make_project(db_session, "uniq1")
    ticket = await _make_ticket(db_session, project, number=10003)

    entry1 = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 22),
    )
    db_session.add(entry1)
    await db_session.flush()

    entry2 = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 22),  # ten sam (user, ticket, date)
    )
    db_session.add(entry2)

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.integration
async def test_unique_allows_different_date(db_session):
    """Ten sam (user, ticket) ale inna data — nie narusza uniq constraint."""
    from monolynx.models import WorkPlanEntry

    user = await _make_user(db_session, "uniq2")
    project = await _make_project(db_session, "uniq2")
    ticket = await _make_ticket(db_session, project, number=10004)

    entry1 = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 23),
    )
    db_session.add(entry1)
    await db_session.flush()

    entry2 = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 24),  # inna data
    )
    db_session.add(entry2)
    await db_session.flush()  # nie powinno rzucic wyjatku

    assert entry2.id is not None


@pytest.mark.integration
async def test_unique_allows_different_user(db_session):
    """Inny user, ten sam (ticket, date) — nie narusza uniq constraint."""
    from monolynx.models import WorkPlanEntry

    user1 = await _make_user(db_session, "uniq3a")
    user2 = await _make_user(db_session, "uniq3b")
    project = await _make_project(db_session, "uniq3")
    ticket = await _make_ticket(db_session, project, number=10005)

    entry1 = WorkPlanEntry(
        user_id=user1.id,
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 25),
    )
    db_session.add(entry1)
    await db_session.flush()

    entry2 = WorkPlanEntry(
        user_id=user2.id,  # inny user
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 25),
    )
    db_session.add(entry2)
    await db_session.flush()  # nie powinno rzucic wyjatku

    assert entry2.id is not None


@pytest.mark.integration
async def test_unique_allows_different_ticket(db_session):
    """Ten sam (user, date) ale inny ticket — nie narusza uniq constraint."""
    from monolynx.models import WorkPlanEntry

    user = await _make_user(db_session, "uniq4")
    project = await _make_project(db_session, "uniq4")
    ticket1 = await _make_ticket(db_session, project, number=10006)
    ticket2 = await _make_ticket(db_session, project, number=10007)

    entry1 = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket1.id,
        scheduled_date=date(2026, 5, 26),
    )
    db_session.add(entry1)
    await db_session.flush()

    entry2 = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket2.id,  # inny ticket
        scheduled_date=date(2026, 5, 26),
    )
    db_session.add(entry2)
    await db_session.flush()

    assert entry2.id is not None


# ---------------------------------------------------------------------------
# CASCADE DELETE
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_cascade_user_delete_removes_entry(db_session):
    """Usuniecie Usera kaskadowo usuwa jego WorkPlanEntry."""
    from monolynx.models import WorkPlanEntry

    user = await _make_user(db_session, "casc_user")
    project = await _make_project(db_session, "casc_user")
    ticket = await _make_ticket(db_session, project, number=10008)

    entry = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 27),
    )
    db_session.add(entry)
    await db_session.flush()
    entry_id = entry.id

    await db_session.delete(user)
    await db_session.flush()

    result = (await db_session.execute(select(WorkPlanEntry).where(WorkPlanEntry.id == entry_id))).scalar_one_or_none()
    assert result is None, "WorkPlanEntry powinien zostac usuniety po usunieciu Usera"


@pytest.mark.integration
async def test_cascade_ticket_delete_removes_entry(db_session):
    """Usuniecie Ticketa kaskadowo usuwa powiazane WorkPlanEntry."""
    from monolynx.models import WorkPlanEntry

    user = await _make_user(db_session, "casc_ticket")
    project = await _make_project(db_session, "casc_ticket")
    ticket = await _make_ticket(db_session, project, number=10009)

    entry = WorkPlanEntry(
        user_id=user.id,
        ticket_id=ticket.id,
        scheduled_date=date(2026, 5, 28),
    )
    db_session.add(entry)
    await db_session.flush()
    entry_id = entry.id

    await db_session.delete(ticket)
    await db_session.flush()

    result = (await db_session.execute(select(WorkPlanEntry).where(WorkPlanEntry.id == entry_id))).scalar_one_or_none()
    assert result is None, "WorkPlanEntry powinien zostac usuniety po usunieciu Ticketa"
