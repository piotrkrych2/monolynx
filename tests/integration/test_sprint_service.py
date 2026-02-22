"""Testy integracyjne -- serwis sprintow (start_sprint, complete_sprint)."""

import secrets
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from open_sentry.models.project import Project
from open_sentry.models.sprint import Sprint
from open_sentry.models.ticket import Ticket
from open_sentry.services.sprint import complete_sprint, start_sprint


async def _create_project(db_session, slug=None):
    if slug is None:
        slug = f"ss-{secrets.token_hex(4)}"
    project = Project(
        name=f"Sprint Svc {slug}",
        slug=slug,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.mark.integration
class TestStartSprint:
    async def test_start_planning_sprint_succeeds(self, db_session):
        """Startuje sprint w fazie planowania -- brak bledu."""
        project = await _create_project(db_session, slug="ss-start-ok")
        sprint = Sprint(
            project_id=project.id,
            name="Do startu",
            start_date=date(2026, 4, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        error = await start_sprint(sprint.id, project.id, db_session)
        assert error is None

        await db_session.refresh(sprint)
        assert sprint.status == "active"

    async def test_start_sprint_already_active_returns_error(self, db_session):
        """Nie mozna wystartowac sprintu jesli inny jest aktywny."""
        project = await _create_project(db_session, slug="ss-start-dup")
        active = Sprint(
            project_id=project.id,
            name="Aktywny sprint",
            start_date=date(2026, 4, 1),
            status="active",
        )
        planning = Sprint(
            project_id=project.id,
            name="Planowany sprint",
            start_date=date(2026, 4, 15),
            status="planning",
        )
        db_session.add_all([active, planning])
        await db_session.flush()

        error = await start_sprint(planning.id, project.id, db_session)
        assert error is not None
        assert "aktywny sprint" in error

    async def test_start_sprint_nonexistent_returns_error(self, db_session):
        """Startowanie nieistniejacego sprintu zwraca blad."""
        project = await _create_project(db_session, slug="ss-start-noid")
        fake_id = uuid.uuid4()

        error = await start_sprint(fake_id, project.id, db_session)
        assert error is not None
        assert "nie istnieje" in error

    async def test_start_sprint_not_in_planning_returns_error(self, db_session):
        """Nie mozna wystartowac sprintu ktory nie jest w fazie planowania."""
        project = await _create_project(db_session, slug="ss-start-notpl")
        sprint = Sprint(
            project_id=project.id,
            name="Zakonczony sprint",
            start_date=date(2026, 4, 1),
            status="completed",
        )
        db_session.add(sprint)
        await db_session.flush()

        error = await start_sprint(sprint.id, project.id, db_session)
        assert error is not None
        assert "planowania" in error

    async def test_start_active_sprint_returns_error(self, db_session):
        """Nie mozna ponownie wystartowac aktywnego sprintu."""
        project = await _create_project(db_session, slug="ss-start-act")
        sprint = Sprint(
            project_id=project.id,
            name="Juz aktywny",
            start_date=date(2026, 4, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        error = await start_sprint(sprint.id, project.id, db_session)
        assert error is not None


@pytest.mark.integration
class TestCompleteSprint:
    async def test_complete_active_sprint_succeeds(self, db_session):
        """Zakonczenie aktywnego sprintu ustawia status na completed."""
        project = await _create_project(db_session, slug="ss-compl-ok")
        sprint = Sprint(
            project_id=project.id,
            name="Do zakonczenia",
            start_date=date(2026, 4, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        error = await complete_sprint(sprint.id, project.id, db_session)
        assert error is None

        await db_session.refresh(sprint)
        assert sprint.status == "completed"

    async def test_complete_sprint_nonexistent_returns_error(self, db_session):
        """Zakonczenie nieistniejacego sprintu zwraca blad."""
        project = await _create_project(db_session, slug="ss-compl-noid")
        fake_id = uuid.uuid4()

        error = await complete_sprint(fake_id, project.id, db_session)
        assert error is not None
        assert "nie istnieje" in error

    async def test_complete_sprint_not_active_returns_error(self, db_session):
        """Nie mozna zakonczyc sprintu ktory nie jest aktywny."""
        project = await _create_project(db_session, slug="ss-compl-notact")
        sprint = Sprint(
            project_id=project.id,
            name="Planowany",
            start_date=date(2026, 4, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        error = await complete_sprint(sprint.id, project.id, db_session)
        assert error is not None
        assert "aktywny sprint" in error

    async def test_complete_sprint_moves_unfinished_tickets_to_backlog(self, db_session):
        """Niedokonczone tickety wracaja do backloga po zakonczeniu sprintu."""
        project = await _create_project(db_session, slug="ss-compl-tickets")
        sprint = Sprint(
            project_id=project.id,
            name="Sprint z ticketami",
            start_date=date(2026, 4, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket_done = Ticket(
            project_id=project.id,
            sprint_id=sprint.id,
            title="Zrobiony ticket",
            status="done",
            priority="medium",
        )
        ticket_todo = Ticket(
            project_id=project.id,
            sprint_id=sprint.id,
            title="Todo ticket",
            status="todo",
            priority="high",
        )
        ticket_in_progress = Ticket(
            project_id=project.id,
            sprint_id=sprint.id,
            title="W trakcie ticket",
            status="in_progress",
            priority="low",
        )
        db_session.add_all([ticket_done, ticket_todo, ticket_in_progress])
        await db_session.flush()

        error = await complete_sprint(sprint.id, project.id, db_session)
        assert error is None

        # Refresh tickets to see changes
        result = await db_session.execute(
            select(Ticket).where(Ticket.id == ticket_done.id)
        )
        done = result.scalar_one()
        assert done.status == "done"
        assert done.sprint_id == sprint.id

        result = await db_session.execute(
            select(Ticket).where(Ticket.id == ticket_todo.id)
        )
        todo = result.scalar_one()
        assert todo.status == "backlog"
        assert todo.sprint_id is None

        result = await db_session.execute(
            select(Ticket).where(Ticket.id == ticket_in_progress.id)
        )
        ip = result.scalar_one()
        assert ip.status == "backlog"
        assert ip.sprint_id is None

    async def test_complete_already_completed_sprint_returns_error(self, db_session):
        """Nie mozna zakonczyc juz zakonczonego sprintu."""
        project = await _create_project(db_session, slug="ss-compl-done")
        sprint = Sprint(
            project_id=project.id,
            name="Zakonczony",
            start_date=date(2026, 4, 1),
            status="completed",
        )
        db_session.add(sprint)
        await db_session.flush()

        error = await complete_sprint(sprint.id, project.id, db_session)
        assert error is not None
        assert "aktywny sprint" in error
