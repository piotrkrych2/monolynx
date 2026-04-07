"""Testy integracyjne -- dashboard endpointy kryteriow akceptacji."""

import secrets
import uuid

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(suffix: str) -> Project:
    slug = f"ac-dash-{suffix}"
    return Project(
        name=f"AC Dash {suffix}",
        slug=slug,
        code=("P" + secrets.token_hex(3)).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


def _make_ticket(project_id: uuid.UUID, number: int = 1) -> Ticket:
    return Ticket(
        project_id=project_id,
        number=number,
        title=f"AC Dashboard Ticket #{number}",
        status="backlog",
    )


async def _make_criterion(db_session, ticket_id: uuid.UUID, user_id: uuid.UUID, **kwargs) -> TicketAcceptanceCriterion:
    crit = TicketAcceptanceCriterion(
        ticket_id=ticket_id,
        description=kwargs.get("description", "Kryterium testowe"),
        position=kwargs.get("position", 0),
        created_by_user_id=user_id,
        is_completed=kwargs.get("is_completed", False),
    )
    db_session.add(crit)
    await db_session.flush()
    return crit


# ---------------------------------------------------------------------------
# criterion_create
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCriterionCreate:
    async def test_create_criterion_happy_path(self, client, db_session):
        """Dodanie kryterium -- happy path, redirect i kryterium w DB."""
        project = _make_project("cr01")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ac-cr01@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
            data={"description": "Nowe kryterium akceptacji"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}" in resp.headers["location"]

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id))
        criteria = result.scalars().all()
        assert len(criteria) == 1
        assert criteria[0].description == "Nowe kryterium akceptacji"
        assert criteria[0].is_completed is False

    async def test_create_criterion_empty_description_flash_error(self, client, db_session):
        """Pusty opis -- flash error i redirect bez zapisu w DB."""
        project = _make_project("cr02")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ac-cr02@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
            data={"description": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id))
        assert len(result.scalars().all()) == 0

    async def test_create_criterion_whitespace_only_flash_error(self, client, db_session):
        """Opis z samych spacji -- traktowany jako pusty, brak zapisu."""
        project = _make_project("cr03")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ac-cr03@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
            data={"description": "   "},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id))
        assert len(result.scalars().all()) == 0

    async def test_create_criterion_unauthenticated_redirects_to_login(self, client, db_session):
        """Niezalogowany -- redirect do /auth/login."""
        project = _make_project("cr04")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
            data={"description": "Kryterium"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_create_criterion_nonexistent_ticket_returns_404(self, client, db_session):
        """Nieistniejacy ticket -- 404."""
        project = _make_project("cr05")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ac-cr05@test.com")

        fake_ticket_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{fake_ticket_id}/criteria",
            data={"description": "Kryterium"},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_create_criterion_nonexistent_project_returns_404(self, client, db_session):
        """Nieistniejacy projekt -- 404."""
        await login_session(client, db_session, email="ac-cr06@test.com")

        fake_ticket_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-project-xyz/scrum/tickets/{fake_ticket_id}/criteria",
            data={"description": "Kryterium"},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_create_criterion_position_auto_increment(self, client, db_session):
        """Dodanie 3 kryteriow -- position 0, 1, 2."""
        project = _make_project("cr07")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ac-cr07@test.com")

        for i in range(3):
            resp = await client.post(
                f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
                data={"description": f"Kryterium {i}"},
                follow_redirects=False,
            )
            assert resp.status_code == 303

        result = await db_session.execute(
            select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id).order_by(TicketAcceptanceCriterion.position)
        )
        criteria = result.scalars().all()
        assert len(criteria) == 3
        assert [c.position for c in criteria] == [0, 1, 2]

    async def test_create_criterion_created_via_ai_is_false(self, client, db_session):
        """Kryterium tworzone przez dashboard -- created_via_ai=False."""
        project = _make_project("cr08")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ac-cr08@test.com")

        await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
            data={"description": "Human criterion"},
            follow_redirects=False,
        )

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id))
        crit = result.scalar_one()
        assert crit.created_via_ai is False


# ---------------------------------------------------------------------------
# criterion_toggle
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCriterionToggle:
    async def test_toggle_on_marks_completed(self, client, db_session):
        """Toggle na nieukonczone kryterium -- is_completed=True, completed_by_user_id i completed_at ustawione."""
        project = _make_project("tg01")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        user = User(email="ac-tg01@test.com", password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        crit = await _make_criterion(db_session, ticket.id, user.id)

        # Login as user
        await client.post(
            "/auth/login",
            data={"email": "ac-tg01@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{crit.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        updated = result.scalar_one()
        assert updated.is_completed is True
        assert updated.completed_by_user_id == user.id
        assert updated.completed_at is not None

    async def test_toggle_off_clears_completed_fields(self, client, db_session):
        """Toggle na ukonczone kryterium -- is_completed=False, completed fields wyczyszczone."""
        from datetime import UTC, datetime

        project = _make_project("tg02")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        user = User(email="ac-tg02@test.com", password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        crit = TicketAcceptanceCriterion(
            ticket_id=ticket.id,
            description="Ukonczone kryterium",
            position=0,
            created_by_user_id=user.id,
            is_completed=True,
            completed_by_user_id=user.id,
            completed_at=datetime.now(UTC),
        )
        db_session.add(crit)
        await db_session.flush()

        await client.post(
            "/auth/login",
            data={"email": "ac-tg02@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{crit.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        updated = result.scalar_one()
        assert updated.is_completed is False
        assert updated.completed_by_user_id is None
        assert updated.completed_at is None

    async def test_toggle_nonexistent_criterion_returns_404(self, client, db_session):
        """Toggle nieistniejacego kryterium -- 404."""
        project = _make_project("tg03")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ac-tg03@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_toggle_unauthenticated_redirects_to_login(self, client, db_session):
        """Toggle bez logowania -- redirect do login."""
        project = _make_project("tg04")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_toggle_completed_via_ai_stays_false_for_dashboard(self, client, db_session):
        """Toggle przez dashboard nie ustawia completed_via_ai=True."""
        project = _make_project("tg05")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        user = User(email="ac-tg05@test.com", password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        crit = await _make_criterion(db_session, ticket.id, user.id)

        await client.post(
            "/auth/login",
            data={"email": "ac-tg05@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{crit.id}/toggle",
            follow_redirects=False,
        )

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        updated = result.scalar_one()
        assert updated.completed_via_ai is False


# ---------------------------------------------------------------------------
# criterion_edit
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCriterionEdit:
    async def test_edit_criterion_changes_description(self, client, db_session):
        """Edycja opisu -- opis zmieniony w DB."""
        project = _make_project("ed01")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        user = User(email="ac-ed01@test.com", password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        crit = await _make_criterion(db_session, ticket.id, user.id, description="Stary opis")

        await client.post(
            "/auth/login",
            data={"email": "ac-ed01@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{crit.id}/edit",
            data={"description": "Nowy opis kryterium"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        updated = result.scalar_one()
        assert updated.description == "Nowy opis kryterium"

    async def test_edit_criterion_empty_description_flash_error(self, client, db_session):
        """Edycja z pustym opisem -- flash error, opis bez zmian."""
        project = _make_project("ed02")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        user = User(email="ac-ed02@test.com", password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        crit = await _make_criterion(db_session, ticket.id, user.id, description="Oryginalny opis")

        await client.post(
            "/auth/login",
            data={"email": "ac-ed02@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{crit.id}/edit",
            data={"description": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit.id))
        unchanged = result.scalar_one()
        assert unchanged.description == "Oryginalny opis"

    async def test_edit_criterion_nonexistent_returns_404(self, client, db_session):
        """Edycja nieistniejacego kryterium -- 404."""
        project = _make_project("ed03")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ac-ed03@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/edit",
            data={"description": "Nowy opis"},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_edit_criterion_unauthenticated_redirects_to_login(self, client, db_session):
        """Edycja bez logowania -- redirect do login."""
        project = _make_project("ed04")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/edit",
            data={"description": "Nowy opis"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_edit_criterion_redirect_contains_ticket_url(self, client, db_session):
        """Po edycji redirect wskazuje na strone ticketa."""
        project = _make_project("ed05")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        user = User(email="ac-ed05@test.com", password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        crit = await _make_criterion(db_session, ticket.id, user.id)

        await client.post(
            "/auth/login",
            data={"email": "ac-ed05@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{crit.id}/edit",
            data={"description": "Zaktualizowany opis"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}" in resp.headers["location"]


# ---------------------------------------------------------------------------
# criterion_delete
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCriterionDelete:
    async def test_delete_criterion_removes_from_db(self, client, db_session):
        """Usuniecie kryterium -- nie ma go w DB."""
        project = _make_project("del01")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        user = User(email="ac-del01@test.com", password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        crit = await _make_criterion(db_session, ticket.id, user.id)
        crit_id = crit.id

        await client.post(
            "/auth/login",
            data={"email": "ac-del01@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{crit_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == crit_id))
        assert result.scalar_one_or_none() is None

    async def test_delete_criterion_nonexistent_returns_404(self, client, db_session):
        """Usuniecie nieistniejacego kryterium -- 404."""
        project = _make_project("del02")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ac-del02@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_delete_criterion_unauthenticated_redirects_to_login(self, client, db_session):
        """Usuniecie bez logowania -- redirect do login."""
        project = _make_project("del03")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_delete_leaves_other_criteria_intact(self, client, db_session):
        """Usuniecie jednego kryterium nie usuwa innych."""
        project = _make_project("del04")
        db_session.add(project)
        await db_session.flush()

        ticket = _make_ticket(project.id)
        db_session.add(ticket)
        await db_session.flush()

        user = User(email="ac-del04@test.com", password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        crit1 = await _make_criterion(db_session, ticket.id, user.id, description="Pierwsze", position=0)
        crit2 = await _make_criterion(db_session, ticket.id, user.id, description="Drugie", position=1)

        await client.post(
            "/auth/login",
            data={"email": "ac-del04@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{crit1.id}/delete",
            follow_redirects=False,
        )

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket.id))
        remaining = result.scalars().all()
        assert len(remaining) == 1
        assert remaining[0].id == crit2.id

    async def test_delete_criterion_nonexistent_ticket_returns_404(self, client, db_session):
        """Usuniecie kryterium z nieistniejacego ticketa -- 404."""
        project = _make_project("del05")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ac-del05@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_delete_criterion_nonexistent_project_returns_404(self, client, db_session):
        """Usuniecie kryterium z nieistniejacego projektu -- 404."""
        await login_session(client, db_session, email="ac-del06@test.com")

        resp = await client.post(
            f"/dashboard/nonexistent-xyz/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Dodatkowe testy pokrycia sciezek -- nieistniejacy projekt w toggle i edit
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCriterionEdgeCasesProjectNotFound:
    async def test_toggle_nonexistent_project_returns_404(self, client, db_session):
        """Toggle z nieistniejacym projektem -- 404."""
        await login_session(client, db_session, email="ac-tgnp01@test.com")

        resp = await client.post(
            f"/dashboard/nonexistent-project-xyz/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_edit_nonexistent_project_returns_404(self, client, db_session):
        """Edit z nieistniejacym projektem -- 404."""
        await login_session(client, db_session, email="ac-ednp01@test.com")

        resp = await client.post(
            f"/dashboard/nonexistent-project-xyz/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/edit",
            data={"description": "Opis"},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_toggle_nonexistent_ticket_returns_404(self, client, db_session):
        """Toggle z istniejacym projektem ale nieistniejacym ticketem -- 404."""
        project = _make_project("tgnp02")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ac-tgnp02@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_edit_nonexistent_ticket_returns_404(self, client, db_session):
        """Edit z istniejacym projektem ale nieistniejacym ticketem -- 404."""
        project = _make_project("ednp02")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ac-ednp02@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/edit",
            data={"description": "Opis"},
            follow_redirects=False,
        )
        assert resp.status_code == 404
