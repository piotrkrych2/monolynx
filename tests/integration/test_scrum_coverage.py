"""Testy integracyjne -- pokrycie brakujacych sciezek w dashboard/scrum.py.

Skupia sie na success paths ktore nie sa pokryte przez istniejace testy:
- formularz tworzenia z memberami i sprintami w kontekscie
- tworzenie ticketa ze wszystkimi opcjonalnymi polami
- ticket detail z komentarzami
- formularz edycji z memberami i sprintami
- edycja ticketa ze wszystkimi polami (sprint_id, assignee_id, description, story_points)
- usuwanie ticketa z weryfikacja w DB
- zmiana statusu z weryfikacja w DB
- zmiana sprintu ticketa (backlog->todo, todo->backlog)
- tablica z aktywnymi sprintami i ticketami w wielu kolumnach
- start sprintu SUCCESS (planning -> active, redirect to board)
- zakonczenie sprintu SUCCESS (active -> completed, redirect to sprints)
- backlog z show_completed_sprints=1, filtrami, sumy SP
"""

import secrets
import uuid
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.models.event import Event
from monolynx.models.issue import Issue
from monolynx.models.label import Label, TicketLabel
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_project import SettlementProject
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.ticket_attachment import TicketAttachment
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(name: str, slug: str) -> Project:
    return Project(
        name=name,
        slug=slug,
        code="P" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Ticket create form -- with members and sprints in context
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketCreateFormWithContext:
    """GET ticket_create_form renders members and sprints in the form."""

    async def test_create_form_shows_members_and_sprints(self, client, db_session):
        project = _make_project("TCF Members", "tcf-members")
        db_session.add(project)
        await db_session.flush()

        # Create a member user for the project
        member_user = User(
            email="tcf-member-user@test.com",
            password_hash=hash_password("testpass123"),
            first_name="Jan",
            last_name="Kowalski",
        )
        db_session.add(member_user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=member_user.id,
            role="member",
        )
        db_session.add(member)

        sprint = Sprint(
            project_id=project.id,
            name="Sprint Formularz",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        await login_session(client, db_session, email="tcf-members@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/create")
        assert resp.status_code == 200
        # Form should contain the sprint name for selection
        assert "Sprint Formularz" in resp.text
        # Member user should appear in assignee dropdown
        assert "Jan" in resp.text or str(member_user.id) in resp.text


# ---------------------------------------------------------------------------
# Ticket create POST -- all optional fields, description, empty title error
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketCreateAllFields:
    """POST ticket_create with all optional fields filled."""

    async def test_create_ticket_with_all_fields(self, client, db_session):
        project = _make_project("TCA AllF", "tca-allf")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint AllF",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        # Create assignee
        assignee = User(
            email="tca-assignee@test.com",
            password_hash=hash_password("testpass123"),
        )
        db_session.add(assignee)
        await db_session.flush()

        await login_session(client, db_session, email="tca-allf@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={
                "title": "Ticket ze wszystkimi polami",
                "description": "Szczegolowy opis ticketa testowego",
                "priority": "critical",
                "story_points": "8",
                "sprint_id": str(sprint.id),
                "assignee_id": str(assignee.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/backlog" in resp.headers["location"]

        # Verify ticket was created with all fields in DB
        result = await db_session.execute(
            select(Ticket).where(
                Ticket.project_id == project.id,
                Ticket.title == "Ticket ze wszystkimi polami",
            )
        )
        ticket = result.scalar_one()
        assert ticket.description == "Szczegolowy opis ticketa testowego"
        assert ticket.priority == "critical"
        assert ticket.story_points == 8
        assert ticket.sprint_id == sprint.id
        assert ticket.assignee_id == assignee.id
        # When sprint_id is set, status should be "todo" (not "backlog")
        assert ticket.status == "todo"

    async def test_create_ticket_with_description_no_sprint(self, client, db_session):
        project = _make_project("TCA Desc", "tca-desc")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tca-desc@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={
                "title": "Ticket z opisem",
                "description": "Opis bez sprintu",
                "priority": "low",
                "story_points": "3",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Verify status is "backlog" when no sprint
        result = await db_session.execute(
            select(Ticket).where(
                Ticket.project_id == project.id,
                Ticket.title == "Ticket z opisem",
            )
        )
        ticket = result.scalar_one()
        assert ticket.status == "backlog"
        assert ticket.description == "Opis bez sprintu"
        assert ticket.story_points == 3
        assert ticket.sprint_id is None

    async def test_create_ticket_empty_title_shows_error_with_context(self, client, db_session):
        """Empty title should re-render the form with members and sprints."""
        project = _make_project("TCA EmptyCtx", "tca-emptyctx")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint EmptyCtx",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        await login_session(client, db_session, email="tca-emptyctx@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "", "priority": "medium"},
        )
        assert resp.status_code == 200
        assert "Tytul jest wymagany" in resp.text
        # Sprint should be available in the re-rendered form
        assert "Sprint EmptyCtx" in resp.text

    async def test_create_ticket_invalid_story_points_ignored(self, client, db_session):
        project = _make_project("TCA InvSP", "tca-invsp")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tca-invsp@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={
                "title": "Ticket invalid SP",
                "priority": "medium",
                "story_points": "abc",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(
            select(Ticket).where(
                Ticket.project_id == project.id,
                Ticket.title == "Ticket invalid SP",
            )
        )
        ticket = result.scalar_one()
        assert ticket.story_points is None

    async def test_create_ticket_invalid_sprint_id_ignored(self, client, db_session):
        project = _make_project("TCA InvSprID", "tca-invsprid")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tca-invsprid@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={
                "title": "Ticket invalid sprint ID",
                "priority": "medium",
                "sprint_id": "not-a-uuid",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(
            select(Ticket).where(
                Ticket.project_id == project.id,
                Ticket.title == "Ticket invalid sprint ID",
            )
        )
        ticket = result.scalar_one()
        assert ticket.sprint_id is None
        assert ticket.status == "backlog"

    async def test_create_ticket_invalid_assignee_id_ignored(self, client, db_session):
        project = _make_project("TCA InvAssID", "tca-invassid")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tca-invassid@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={
                "title": "Ticket invalid assignee",
                "priority": "medium",
                "assignee_id": "not-a-uuid",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(
            select(Ticket).where(
                Ticket.project_id == project.id,
                Ticket.title == "Ticket invalid assignee",
            )
        )
        ticket = result.scalar_one()
        assert ticket.assignee_id is None


# ---------------------------------------------------------------------------
# Ticket detail -- with comments
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketDetailWithComments:
    """GET ticket_detail with ticket that has comments from a user."""

    async def test_ticket_detail_shows_comments(self, client, db_session):
        project = _make_project("TDC Comments", "tdc-comments")
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket z komentarzami",
            description="Opis ticketa z komentarzem",
            status="in_progress",
            priority="high",
            story_points=5,
        )
        db_session.add(ticket)
        await db_session.flush()

        # Create a user for the comment author
        comment_author = User(
            email="tdc-author@test.com",
            password_hash=hash_password("testpass123"),
            first_name="Anna",
            last_name="Nowak",
        )
        db_session.add(comment_author)
        await db_session.flush()

        comment = TicketComment(
            ticket_id=ticket.id,
            user_id=comment_author.id,
            content="To jest komentarz testowy do ticketa",
        )
        db_session.add(comment)
        await db_session.flush()

        await login_session(client, db_session, email="tdc-comments@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")
        assert resp.status_code == 200
        assert "Ticket z komentarzami" in resp.text
        assert "Opis ticketa z komentarzem" in resp.text
        assert "To jest komentarz testowy do ticketa" in resp.text

    async def test_ticket_detail_with_assignee_and_sprint(self, client, db_session):
        project = _make_project("TDC Full", "tdc-full")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint Detail",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        assignee = User(
            email="tdc-assignee@test.com",
            password_hash=hash_password("testpass123"),
        )
        db_session.add(assignee)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket pelny detail",
            description="Opis pelny",
            status="todo",
            priority="critical",
            story_points=13,
            sprint_id=sprint.id,
            assignee_id=assignee.id,
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tdc-full@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")
        assert resp.status_code == 200
        assert "Ticket pelny detail" in resp.text
        assert "Sprint Detail" in resp.text


# ---------------------------------------------------------------------------
# Ticket edit form -- with members and sprints loaded
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketEditFormWithContext:
    """GET ticket_edit_form renders members and sprints for selection."""

    async def test_edit_form_shows_members_and_sprints(self, client, db_session):
        project = _make_project("TEF Context", "tef-context")
        db_session.add(project)
        await db_session.flush()

        member_user = User(
            email="tef-member@test.com",
            password_hash=hash_password("testpass123"),
            first_name="Piotr",
            last_name="Wisniewski",
        )
        db_session.add(member_user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=member_user.id,
            role="member",
        )
        db_session.add(member)

        sprint = Sprint(
            project_id=project.id,
            name="Sprint Edycji",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket do edycji z kontekstem",
            description="Opis do edycji",
            status="todo",
            priority="high",
            story_points=5,
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tef-context@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit")
        assert resp.status_code == 200
        # Existing ticket data should be pre-filled
        assert "Ticket do edycji z kontekstem" in resp.text
        assert "Opis do edycji" in resp.text
        # Sprint and member should be available in dropdowns
        assert "Sprint Edycji" in resp.text
        assert "Piotr" in resp.text or str(member_user.id) in resp.text


# ---------------------------------------------------------------------------
# Ticket edit POST -- update ALL fields
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketEditAllFields:
    """POST ticket_edit with all fields changed, including sprint and assignee."""

    async def test_edit_ticket_all_fields(self, client, db_session):
        project = _make_project("TEA AllF", "tea-allf")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint Edycja",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        assignee = User(
            email="tea-newassignee@test.com",
            password_hash=hash_password("testpass123"),
        )
        db_session.add(assignee)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Stary tytul edycja",
            description="Stary opis",
            status="backlog",
            priority="low",
            story_points=1,
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tea-allf@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/edit",
            data={
                "title": "Nowy tytul po edycji",
                "description": "Nowy opis po edycji",
                "priority": "critical",
                "status": "in_progress",
                "story_points": "13",
                "sprint_id": str(sprint.id),
                "assignee_id": str(assignee.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/scrum/tickets/{ticket_id}" in resp.headers["location"]

        # Verify all fields updated in DB
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        updated = result.scalar_one()
        assert updated.title == "Nowy tytul po edycji"
        assert updated.description == "Nowy opis po edycji"
        assert updated.priority == "critical"
        assert updated.status == "in_progress"
        assert updated.story_points == 13
        assert updated.sprint_id == sprint.id
        assert updated.assignee_id == assignee.id

    async def test_edit_ticket_clear_optional_fields(self, client, db_session):
        """Edit a ticket to clear description, story_points, sprint, assignee."""
        project = _make_project("TEA Clear", "tea-clear")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint Clear",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        assignee = User(
            email="tea-clearass@test.com",
            password_hash=hash_password("testpass123"),
        )
        db_session.add(assignee)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket do wyczyszczenia",
            description="Opis do usuniecia",
            status="todo",
            priority="high",
            story_points=5,
            sprint_id=sprint.id,
            assignee_id=assignee.id,
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tea-clear@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/edit",
            data={
                "title": "Ticket wyczyszczony",
                "description": "",
                "priority": "medium",
                "status": "backlog",
                "story_points": "",
                "sprint_id": "",
                "assignee_id": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        updated = result.scalar_one()
        assert updated.title == "Ticket wyczyszczony"
        assert updated.description is None
        assert updated.priority == "medium"
        assert updated.status == "backlog"
        assert updated.story_points is None
        assert updated.sprint_id is None
        assert updated.assignee_id is None

    async def test_edit_ticket_invalid_story_points_clears(self, client, db_session):
        project = _make_project("TEA InvSP", "tea-invsp")
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket inv SP edit",
            status="backlog",
            priority="medium",
            story_points=5,
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tea-invsp@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/edit",
            data={
                "title": "Ticket inv SP edit",
                "priority": "medium",
                "story_points": "abc",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        updated = result.scalar_one()
        # Invalid story_points -> None
        assert updated.story_points is None

    async def test_edit_ticket_invalid_priority_defaults_medium(self, client, db_session):
        project = _make_project("TEA InvPrio", "tea-invprio")
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket inv prio edit",
            status="backlog",
            priority="high",
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tea-invprio@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/edit",
            data={
                "title": "Ticket inv prio edit",
                "priority": "nonexistent",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        updated = result.scalar_one()
        assert updated.priority == "medium"

    async def test_edit_ticket_invalid_status_keeps_old(self, client, db_session):
        project = _make_project("TEA InvStat", "tea-invstat")
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket inv status edit",
            status="todo",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tea-invstat@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/edit",
            data={
                "title": "Ticket inv status edit",
                "priority": "medium",
                "status": "invalid_status",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        updated = result.scalar_one()
        # Status stays "todo" because "invalid_status" not in TICKET_STATUSES
        assert updated.status == "todo"


# ---------------------------------------------------------------------------
# Ticket delete -- verify DB deletion
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketDeleteVerifyDB:
    """POST ticket_delete -- verify ticket removed from DB."""

    async def test_delete_ticket_removes_from_db(self, client, db_session):
        project = _make_project("TDV Delete", "tdv-delete")
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket do usuniecia DB",
            status="backlog",
            priority="low",
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tdv-delete@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/backlog" in resp.headers["location"]

        # Verify ticket no longer exists in DB
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# Ticket status update -- verify DB change
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketStatusUpdateVerifyDB:
    """PATCH ticket_status_update -- verify status persisted in DB."""

    async def test_status_update_persists_in_db(self, client, db_session):
        project = _make_project("TSV Persist", "tsv-persist")
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket status persist",
            status="todo",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tsv-persist@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/status",
            json={"status": "done"},
        )
        assert resp.status_code == 200
        assert resp.text == "OK"

        # Verify in DB
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        updated = result.scalar_one()
        assert updated.status == "done"

    async def test_status_update_to_in_review(self, client, db_session):
        project = _make_project("TSV Review", "tsv-review")
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket review status",
            status="in_progress",
            priority="high",
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tsv-review@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/status",
            json={"status": "in_review"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        updated = result.scalar_one()
        assert updated.status == "in_review"


# ---------------------------------------------------------------------------
# Board -- no active sprint, active sprint with SP computation
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBoardSPComputation:
    """Board with story points in multiple columns."""

    async def test_board_sp_per_column(self, client, db_session):
        project = _make_project("BSP Comp", "bsp-comp")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint SP Comp",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        t_todo = Ticket(
            project_id=project.id,
            number=1,
            sprint_id=sprint.id,
            title="Board SP Todo",
            status="todo",
            priority="medium",
            story_points=3,
        )
        t_progress = Ticket(
            project_id=project.id,
            number=2,
            sprint_id=sprint.id,
            title="Board SP InProgress",
            status="in_progress",
            priority="high",
            story_points=5,
        )
        t_review = Ticket(
            project_id=project.id,
            number=3,
            sprint_id=sprint.id,
            title="Board SP Review",
            status="in_review",
            priority="low",
            story_points=2,
        )
        t_done = Ticket(
            project_id=project.id,
            number=4,
            sprint_id=sprint.id,
            title="Board SP Done",
            status="done",
            priority="medium",
            story_points=8,
        )
        db_session.add_all([t_todo, t_progress, t_review, t_done])
        await db_session.flush()

        await login_session(client, db_session, email="bsp-comp@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/board")
        assert resp.status_code == 200
        # All tickets should appear on the board
        assert "Board SP Todo" in resp.text
        assert "Board SP InProgress" in resp.text
        assert "Board SP Review" in resp.text
        assert "Board SP Done" in resp.text
        # Sprint name should be displayed
        assert "Sprint SP Comp" in resp.text

    async def test_board_with_ticket_no_story_points(self, client, db_session):
        """Tickets without story_points should not break SP computation."""
        project = _make_project("BSP NoSP", "bsp-nosp")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint NoSP",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        t_with_sp = Ticket(
            project_id=project.id,
            number=1,
            sprint_id=sprint.id,
            title="Board ticket z SP",
            status="todo",
            priority="medium",
            story_points=5,
        )
        t_without_sp = Ticket(
            project_id=project.id,
            number=2,
            sprint_id=sprint.id,
            title="Board ticket bez SP",
            status="in_progress",
            priority="medium",
            story_points=None,
        )
        db_session.add_all([t_with_sp, t_without_sp])
        await db_session.flush()

        await login_session(client, db_session, email="bsp-nosp@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/board")
        assert resp.status_code == 200
        assert "Board ticket z SP" in resp.text
        assert "Board ticket bez SP" in resp.text


# ---------------------------------------------------------------------------
# Sprint start SUCCESS -- planning -> active, redirect to board
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSprintStartSuccess:
    """POST sprint_start -- success path: planning -> active, 303 redirect to board."""

    async def test_start_planning_sprint_redirects_to_board(self, client, db_session):
        project = _make_project("SSS Start", "sss-start")
        db_session.add(project)
        await db_session.flush()

        # CRITICAL: Sprint must be in "planning" status and no other active sprint exists
        sprint = Sprint(
            project_id=project.id,
            name="Sprint do startu success",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()
        sprint_id = sprint.id

        await login_session(client, db_session, email="sss-start@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{sprint_id}/start",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/board" in resp.headers["location"]

        # Verify sprint status changed to "active" in DB
        result = await db_session.execute(select(Sprint).where(Sprint.id == sprint_id))
        updated_sprint = result.scalar_one()
        assert updated_sprint.status == "active"


# ---------------------------------------------------------------------------
# Sprint complete SUCCESS -- active -> completed, redirect to sprints
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSprintCompleteSuccess:
    """POST sprint_complete -- success path: active -> completed, 303 redirect to sprints."""

    async def test_complete_active_sprint_redirects_to_sprints(self, client, db_session):
        project = _make_project("SCS Complete", "scs-complete")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint do zakonczenia success",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()
        sprint_id = sprint.id

        await login_session(client, db_session, email="scs-complete@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{sprint_id}/complete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/sprints" in resp.headers["location"]

        # Verify sprint status changed to "completed" in DB
        result = await db_session.execute(select(Sprint).where(Sprint.id == sprint_id))
        updated_sprint = result.scalar_one()
        assert updated_sprint.status == "completed"

    async def test_complete_sprint_with_tickets_moves_undone_to_backlog(self, client, db_session):
        project = _make_project("SCS Tickets", "scs-tickets")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint tickets complete",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()
        sprint_id = sprint.id

        t_done = Ticket(
            project_id=project.id,
            number=1,
            sprint_id=sprint.id,
            title="Ticket done complete",
            status="done",
            priority="medium",
        )
        t_in_progress = Ticket(
            project_id=project.id,
            number=2,
            sprint_id=sprint.id,
            title="Ticket in_progress complete",
            status="in_progress",
            priority="high",
        )
        t_todo = Ticket(
            project_id=project.id,
            number=3,
            sprint_id=sprint.id,
            title="Ticket todo complete",
            status="todo",
            priority="low",
        )
        db_session.add_all([t_done, t_in_progress, t_todo])
        await db_session.flush()
        t_done_id = t_done.id
        t_in_progress_id = t_in_progress.id
        t_todo_id = t_todo.id

        await login_session(client, db_session, email="scs-tickets@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{sprint_id}/complete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Done ticket stays in sprint
        result = await db_session.execute(select(Ticket).where(Ticket.id == t_done_id))
        done_ticket = result.scalar_one()
        assert done_ticket.status == "done"
        assert done_ticket.sprint_id == sprint_id

        # In-progress ticket moves to backlog
        result = await db_session.execute(select(Ticket).where(Ticket.id == t_in_progress_id))
        ip_ticket = result.scalar_one()
        assert ip_ticket.status == "backlog"
        assert ip_ticket.sprint_id is None

        # Todo ticket moves to backlog
        result = await db_session.execute(select(Ticket).where(Ticket.id == t_todo_id))
        todo_ticket = result.scalar_one()
        assert todo_ticket.status == "backlog"
        assert todo_ticket.sprint_id is None


# ---------------------------------------------------------------------------
# Backlog -- SP total display, combined filters with show_completed_sprints
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBacklogSPAndFilters:
    """Backlog with story points total display and combined filters."""

    async def test_backlog_sp_total_displayed(self, client, db_session):
        project = _make_project("BLF SP", "blf-sp")
        db_session.add(project)
        await db_session.flush()

        t1 = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket SP 3",
            status="backlog",
            priority="medium",
            story_points=3,
        )
        t2 = Ticket(
            project_id=project.id,
            number=2,
            title="Ticket SP 5",
            status="todo",
            priority="high",
            story_points=5,
        )
        t3 = Ticket(
            project_id=project.id,
            number=3,
            title="Ticket no SP",
            status="backlog",
            priority="low",
            story_points=None,
        )
        db_session.add_all([t1, t2, t3])
        await db_session.flush()

        await login_session(client, db_session, email="blf-sp@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog")
        assert resp.status_code == 200
        # SP total = 3 + 5 = 8
        assert "8" in resp.text
        assert "Ticket SP 3" in resp.text
        assert "Ticket SP 5" in resp.text
        assert "Ticket no SP" in resp.text

    async def test_backlog_show_completed_sprints_with_sp(self, client, db_session):
        """show_completed_sprints=1 should include tickets from completed sprints in SP total."""
        project = _make_project("BLF CompSP", "blf-compsp")
        db_session.add(project)
        await db_session.flush()

        completed_sprint = Sprint(
            project_id=project.id,
            name="Zakonczony SP sprint",
            start_date=date(2026, 2, 1),
            status="completed",
        )
        db_session.add(completed_sprint)
        await db_session.flush()

        t_completed = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket completed sprint SP",
            status="done",
            priority="medium",
            story_points=10,
            sprint_id=completed_sprint.id,
        )
        t_backlog = Ticket(
            project_id=project.id,
            number=2,
            title="Ticket backlog SP",
            status="backlog",
            priority="low",
            story_points=3,
        )
        db_session.add_all([t_completed, t_backlog])
        await db_session.flush()

        await login_session(client, db_session, email="blf-compsp@test.com")

        # Without flag -- completed sprint ticket hidden, SP = 3
        resp1 = await client.get(f"/dashboard/{project.slug}/scrum/backlog")
        assert resp1.status_code == 200
        assert "Ticket completed sprint SP" not in resp1.text
        assert "Ticket backlog SP" in resp1.text

        # With flag -- both visible, SP = 13
        resp2 = await client.get(f"/dashboard/{project.slug}/scrum/backlog?show_completed_sprints=1")
        assert resp2.status_code == 200
        assert "Ticket completed sprint SP" in resp2.text
        assert "Ticket backlog SP" in resp2.text

    async def test_backlog_all_filters_combined_with_show_completed(self, client, db_session):
        """Combine status, priority, search, and show_completed_sprints filters."""
        project = _make_project("BLF AllFilt", "blf-allfilt")
        db_session.add(project)
        await db_session.flush()

        completed_sprint = Sprint(
            project_id=project.id,
            name="Completed filter sprint",
            start_date=date(2026, 2, 1),
            status="completed",
        )
        db_session.add(completed_sprint)
        await db_session.flush()

        # Create a user for assignee filter
        await login_session(client, db_session, email="blf-allfilt@test.com")

        user_result = await db_session.execute(select(User).where(User.email == "blf-allfilt@test.com"))
        user = user_result.scalar_one()

        # Ticket that should match all filters
        t_match = Ticket(
            project_id=project.id,
            number=1,
            title="Filtrowany ticket match",
            status="done",
            priority="high",
            story_points=5,
            sprint_id=completed_sprint.id,
            assignee_id=user.id,
        )
        # Ticket that should NOT match (different status)
        t_miss = Ticket(
            project_id=project.id,
            number=2,
            title="Filtrowany ticket miss",
            status="backlog",
            priority="low",
        )
        db_session.add_all([t_match, t_miss])
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/backlog"
            f"?status=done&priority=high&search=match"
            f"&assignee_id={user.id}&sprint_id={completed_sprint.id}"
            f"&show_completed_sprints=1"
        )
        assert resp.status_code == 200
        assert "Filtrowany ticket match" in resp.text
        assert "Filtrowany ticket miss" not in resp.text


# ---------------------------------------------------------------------------
# Ticket sprint update -- status changes (backlog->todo, todo->backlog)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketSprintUpdateStatusChange:
    """PATCH ticket_sprint_update with verified status transitions."""

    async def test_assign_to_sprint_changes_backlog_to_todo_db(self, client, db_session):
        project = _make_project("TSUC Assign", "tsuc-assign")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint assign verify",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket backlog assign verify",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tsuc-assign@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/sprint",
            json={"sprint_id": str(sprint.id)},
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        updated = result.scalar_one()
        assert updated.sprint_id == sprint.id
        assert updated.status == "todo"

    async def test_remove_from_sprint_changes_todo_to_backlog_db(self, client, db_session):
        project = _make_project("TSUC Remove", "tsuc-remove")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint remove verify",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket todo remove verify",
            status="todo",
            priority="medium",
            sprint_id=sprint.id,
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tsuc-remove@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/sprint",
            json={"sprint_id": None},
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        updated = result.scalar_one()
        assert updated.sprint_id is None
        assert updated.status == "backlog"


# ---------------------------------------------------------------------------
# Ticket comment creation -- verify in DB
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketCommentVerifyDB:
    """POST ticket_comment_create -- verify comment persisted in DB."""

    async def test_comment_persisted_in_db(self, client, db_session):
        project = _make_project("TCV Comment", "tcv-comment")
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="Ticket komentarz DB",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tcv-comment@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_id}/comments",
            data={"content": "Komentarz do weryfikacji w DB"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Verify comment in DB
        result = await db_session.execute(select(TicketComment).where(TicketComment.ticket_id == ticket_id))
        comment = result.scalar_one()
        assert comment.content == "Komentarz do weryfikacji w DB"
        assert comment.user_id is not None


# ---------------------------------------------------------------------------
# Ticket create -- etykiety i due_date (MON-84)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketCreateLabelsAndDueDate:
    async def test_create_ticket_with_valid_labels(self, client, db_session):
        project = _make_project("TCL Labels", "tcl-labels")
        db_session.add(project)
        await db_session.flush()

        label = Label(project_id=project.id, name="bug", color="#ff0000")
        db_session.add(label)
        await db_session.flush()

        await login_session(client, db_session, email="tcl-labels@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Ticket z etykieta", "label_ids": [str(label.id)]},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.project_id == project.id))
        ticket = result.scalar_one()
        tl_result = await db_session.execute(select(TicketLabel).where(TicketLabel.ticket_id == ticket.id))
        ticket_labels = tl_result.scalars().all()
        assert len(ticket_labels) == 1
        assert ticket_labels[0].label_id == label.id

    async def test_create_ticket_with_invalid_label_id_ignored(self, client, db_session):
        project = _make_project("TCL Invalid", "tcl-invalid")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tcl-invalid@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Ticket zla etykieta", "label_ids": ["not-a-uuid"]},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.project_id == project.id))
        ticket = result.scalar_one()
        tl_result = await db_session.execute(select(TicketLabel).where(TicketLabel.ticket_id == ticket.id))
        assert tl_result.scalars().all() == []

    async def test_create_ticket_with_due_date(self, client, db_session):
        project = _make_project("TCL DueDate", "tcl-duedate")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tcl-duedate@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Ticket z terminem", "due_date": "2026-04-01"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.project_id == project.id))
        ticket = result.scalar_one()
        assert ticket.due_date == date(2026, 4, 1)

    async def test_create_ticket_with_invalid_due_date_ignored(self, client, db_session):
        project = _make_project("TCL BadDate", "tcl-baddate")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tcl-baddate@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Ticket zla data", "due_date": "not-a-date"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.project_id == project.id))
        ticket = result.scalar_one()
        assert ticket.due_date is None


# ---------------------------------------------------------------------------
# ticket_create_from_issue -- edge cases (dlugi tytul, roznorodne ksztalty Event)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketCreateFromIssueEdgeCases:
    async def test_title_truncated_when_too_long(self, client, db_session):
        project = _make_project("TFI LongTitle", "tfi-longtitle")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint=secrets.token_hex(32),
            title="X" * 505,
            event_count=1,
            first_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            last_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        )
        db_session.add(issue)
        await db_session.flush()

        await login_session(client, db_session, email="tfi-longtitle@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.issue_id == issue.id))
        ticket = result.scalar_one()
        assert len(ticket.title) == 512
        assert ticket.title.endswith("...")

    async def test_exception_with_traceback_key(self, client, db_session):
        project = _make_project("TFI Traceback", "tfi-traceback")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint=secrets.token_hex(32),
            title="KeyError: missing",
            event_count=1,
            first_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            last_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            exception={"traceback": "Traceback (most recent call last):\n  raise KeyError"},
        )
        db_session.add(event)
        await db_session.flush()

        await login_session(client, db_session, email="tfi-traceback@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.issue_id == issue.id))
        ticket = result.scalar_one()
        assert "raise KeyError" in ticket.description

    async def test_exception_with_type_and_value_only(self, client, db_session):
        project = _make_project("TFI TypeValue", "tfi-typevalue")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint=secrets.token_hex(32),
            title="TypeError: oops",
            event_count=1,
            first_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            last_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            exception={"type": "TypeError", "value": "oops"},
        )
        db_session.add(event)
        await db_session.flush()

        await login_session(client, db_session, email="tfi-typevalue@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.issue_id == issue.id))
        ticket = result.scalar_one()
        assert "TypeError: oops" in ticket.description

    async def test_exception_not_a_dict(self, client, db_session):
        project = _make_project("TFI NotDict", "tfi-notdict")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint=secrets.token_hex(32),
            title="Error: something",
            event_count=1,
            first_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            last_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            exception="not-a-dict",
            request_data="not-a-dict-either",
            environment="also-not-a-dict",
        )
        db_session.add(event)
        await db_session.flush()

        await login_session(client, db_session, email="tfi-notdict@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.issue_id == issue.id))
        ticket = result.scalar_one()
        assert "## Traceback" in ticket.description

    async def test_frame_not_dict_and_frame_without_context_line(self, client, db_session):
        project = _make_project("TFI FrameEdge", "tfi-frameedge")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint=secrets.token_hex(32),
            title="ValueError: frame edge",
            event_count=1,
            first_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            last_seen=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        )
        db_session.add(issue)
        await db_session.flush()

        event = Event(
            issue_id=issue.id,
            timestamp=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
            exception={
                "type": "ValueError",
                "value": "frame edge",
                "stacktrace": {
                    "frames": [
                        "not-a-dict-frame",
                        {"filename": "app/views.py", "function": "handler", "lineno": None},
                    ]
                },
            },
        )
        db_session.add(event)
        await db_session.flush()

        await login_session(client, db_session, email="tfi-frameedge@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create-from-issue/{issue.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.issue_id == issue.id))
        ticket = result.scalar_one()
        assert 'File "app/views.py", in handler' in ticket.description


# ---------------------------------------------------------------------------
# Board -- aktywny sprint bez ticketow (partial branch coverage)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBoardActiveSprintNoTickets:
    async def test_board_active_sprint_without_tickets(self, client, db_session):
        project = _make_project("Board Empty", "board-empty-sprint")
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint Pusty",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        await login_session(client, db_session, email="board-empty@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/board")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ticket_work_plan_section -- fragment HTMX (brak jakiegokolwiek pokrycia)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketWorkPlanSection:
    async def test_requires_auth(self, client, db_session):
        project = _make_project("WPS Auth", "wps-auth")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/work-plan")
        assert resp.status_code == 401

    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="wps-noproj@test.com")
        resp = await client.get(f"/dashboard/nonexistent-slug/scrum/tickets/{uuid.uuid4()}/work-plan")
        assert resp.status_code == 404

    async def test_ticket_not_found(self, client, db_session):
        project = _make_project("WPS NoTicket", "wps-noticket")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="wps-noticket@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/work-plan")
        assert resp.status_code == 404

    async def test_success_returns_fragment(self, client, db_session):
        project = _make_project("WPS Success", "wps-success")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T z planem", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="wps-success@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/work-plan")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ticket_attachment_serve -- serwowanie zalacznikow (brak pokrycia)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketAttachmentServe:
    async def test_requires_auth(self, client, db_session):
        project = _make_project("TAS Auth", "tas-auth")
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/attachments/{uuid.uuid4()}/f.png",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="tas-noproj@test.com")
        resp = await client.get(
            f"/dashboard/nonexistent-slug/scrum/tickets/{uuid.uuid4()}/attachments/{uuid.uuid4()}/f.png",
        )
        assert resp.status_code == 404

    async def test_attachment_not_found(self, client, db_session):
        project = _make_project("TAS NoAtt", "tas-noatt")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tas-noatt@test.com")
        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/{uuid.uuid4()}/f.png",
        )
        assert resp.status_code == 404

    @patch("monolynx.dashboard.scrum.minio_get_attachment", side_effect=Exception("boom"))
    async def test_minio_error_returns_500(self, mock_get, client, db_session):
        project = _make_project("TAS Error", "tas-error")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        attachment = TicketAttachment(
            ticket_id=ticket.id,
            filename="doc.pdf",
            storage_path=f"{project.slug}/attachments/doc.pdf",
            mime_type="application/pdf",
            size=100,
        )
        db_session.add(attachment)
        await db_session.flush()

        await login_session(client, db_session, email="tas-error@test.com")
        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/{attachment.id}/doc.pdf",
        )
        assert resp.status_code == 500

    @patch("monolynx.dashboard.scrum.minio_get_attachment", return_value=(b"PDF-CONTENT", "application/pdf"))
    async def test_success_streams_file(self, mock_get, client, db_session):
        project = _make_project("TAS Success", "tas-success")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        attachment = TicketAttachment(
            ticket_id=ticket.id,
            filename="doc.pdf",
            storage_path=f"{project.slug}/attachments/doc.pdf",
            mime_type="application/pdf",
            size=100,
        )
        db_session.add(attachment)
        await db_session.flush()

        await login_session(client, db_session, email="tas-success@test.com")
        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/{attachment.id}/doc.pdf",
        )
        assert resp.status_code == 200
        assert resp.content == b"PDF-CONTENT"
        assert resp.headers["content-type"] == "application/pdf"
        mock_get.assert_called_once_with(attachment.storage_path)


# ---------------------------------------------------------------------------
# ticket_attachment_upload -- upload FilePond (brak pokrycia)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketAttachmentUpload:
    async def test_requires_auth(self, client, db_session):
        project = _make_project("TAU Auth", "tau-auth")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/upload",
            files={"filepond": ("f.png", b"data", "image/png")},
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "Unauthorized"

    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="tau-noproj@test.com")
        resp = await client.post(
            f"/dashboard/nonexistent-slug/scrum/tickets/{uuid.uuid4()}/attachments/upload",
            files={"filepond": ("f.png", b"data", "image/png")},
        )
        assert resp.status_code == 404

    async def test_ticket_not_found(self, client, db_session):
        project = _make_project("TAU NoTicket", "tau-noticket")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tau-noticket@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/attachments/upload",
            files={"filepond": ("f.png", b"data", "image/png")},
        )
        assert resp.status_code == 404

    @patch("monolynx.dashboard.scrum.minio_upload_attachment", return_value="proj/attachments/attachment")
    async def test_filename_sanitized_to_empty_falls_back(self, mock_upload, client, db_session):
        """Nazwa pliku zlozona wylacznie ze spacji -> po strip() pusta -> fallback 'attachment'."""
        project = _make_project("TAU AllSpecial", "tau-allspecial")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tau-allspecial@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/upload",
            files={"filepond": ("   ", b"data", "application/octet-stream")},
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket_id))
        attachment = result.scalar_one()
        assert attachment.filename == "attachment"

    async def test_too_large_returns_400(self, client, db_session):
        project = _make_project("TAU Large", "tau-large")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tau-large@test.com")
        large_data = b"\x00" * (200 * 1024 * 1024 + 1)
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/upload",
            files={"filepond": ("huge.bin", large_data, "application/octet-stream")},
        )
        assert resp.status_code == 400
        assert "za duzy" in resp.json()["error"]

    @patch("monolynx.dashboard.scrum.minio_upload_attachment", side_effect=Exception("minio down"))
    async def test_minio_error_returns_500(self, mock_upload, client, db_session):
        project = _make_project("TAU Error", "tau-error")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tau-error@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/upload",
            files={"filepond": ("f.png", b"data", "image/png")},
        )
        assert resp.status_code == 500
        assert "Blad uploadu" in resp.json()["error"]

    @patch("monolynx.dashboard.scrum.minio_upload_attachment", return_value="proj/attachments/f-sanitized.png")
    async def test_success_sanitizes_filename_and_persists(self, mock_upload, client, db_session):
        project = _make_project("TAU Success", "tau-success")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tau-success@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/upload",
            files={"filepond": ("../weird name!@#.png", b"pngdata", "image/png")},
        )
        assert resp.status_code == 200
        attachment_id = uuid.UUID(resp.text.strip())

        result = await db_session.execute(select(TicketAttachment).where(TicketAttachment.ticket_id == ticket_id))
        attachment = result.scalar_one()
        assert attachment.id == attachment_id
        assert attachment.filename != "../weird name!@#.png"
        assert "/" not in attachment.filename
        assert attachment.size == len(b"pngdata")
        mock_upload.assert_called_once()


# ---------------------------------------------------------------------------
# ticket_attachment_delete -- usuwanie zalacznika (brak pokrycia)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketAttachmentDelete:
    async def test_requires_auth(self, client, db_session):
        project = _make_project("TAD Auth", "tad-auth")
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/attachments/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="tad-noproj@test.com")
        resp = await client.post(
            f"/dashboard/nonexistent-slug/scrum/tickets/{uuid.uuid4()}/attachments/{uuid.uuid4()}/delete",
        )
        assert resp.status_code == 404

    async def test_attachment_not_found(self, client, db_session):
        project = _make_project("TAD NoAtt", "tad-noatt")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tad-noatt@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/{uuid.uuid4()}/delete",
        )
        assert resp.status_code == 404

    @patch("monolynx.dashboard.scrum.minio_delete_object", side_effect=Exception("minio down"))
    async def test_success_even_if_minio_delete_fails(self, mock_delete, client, db_session):
        """contextlib.suppress(Exception) na minio_delete_object nie blokuje usuniecia z DB."""
        project = _make_project("TAD MinioFail", "tad-miniofail")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        attachment = TicketAttachment(
            ticket_id=ticket.id,
            filename="doc.pdf",
            storage_path=f"{project.slug}/attachments/doc.pdf",
            mime_type="application/pdf",
            size=100,
        )
        db_session.add(attachment)
        await db_session.flush()
        attachment_id = attachment.id

        await login_session(client, db_session, email="tad-miniofail@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/attachments/{attachment_id}/delete",
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(TicketAttachment).where(TicketAttachment.id == attachment_id))
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# ticket_edit_form -- galaz z dostepnymi rozliczeniami (branch 981->995)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketEditFormWithSettlements:
    async def test_edit_form_shows_available_settlements(self, client, db_session):
        project = _make_project("TEF Settlement", "tef-settlement")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T z rozliczeniem", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tef-settlement@test.com")
        result = await db_session.execute(select(User).where(User.email == "tef-settlement@test.com"))
        user = result.scalar_one()

        settlement = Settlement(
            number=90001,
            name="Rozliczenie TEF",
            period_from=date(2026, 3, 1),
            period_to=date(2026, 3, 31),
            status="draft",
            is_active=True,
            created_by_id=user.id,
        )
        db_session.add(settlement)
        await db_session.flush()
        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=project.id))
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit")
        assert resp.status_code == 200
        assert "Rozliczenie TEF" in resp.text


# ---------------------------------------------------------------------------
# ticket_edit -- etykiety i rozliczenia (branch 1092, 1096->1119, 1100-1115)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketEditLabelsAndSettlements:
    async def test_edit_ticket_syncs_valid_labels(self, client, db_session):
        project = _make_project("TEL Labels", "tel-labels")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        label = Label(project_id=project.id, name="feature", color="#00ff00")
        db_session.add(label)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tel-labels@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "T edytowany", "label_ids": [str(label.id)]},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        tl_result = await db_session.execute(select(TicketLabel).where(TicketLabel.ticket_id == ticket_id))
        ticket_labels = tl_result.scalars().all()
        assert len(ticket_labels) == 1
        assert ticket_labels[0].label_id == label.id

    async def test_edit_ticket_syncs_settlements_valid_and_invalid(self, client, db_session):
        project = _make_project("TEL Settlements", "tel-settlements")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tel-settlements@test.com")
        result = await db_session.execute(select(User).where(User.email == "tel-settlements@test.com"))
        user = result.scalar_one()

        settlement = Settlement(
            number=90002,
            name="Rozliczenie TEL",
            period_from=date(2026, 3, 1),
            period_to=date(2026, 3, 31),
            status="draft",
            is_active=True,
            created_by_id=user.id,
        )
        db_session.add(settlement)
        await db_session.flush()
        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=project.id))
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={
                "title": "T z rozliczeniem",
                "settlement_ids": [str(settlement.id), "not-a-uuid", str(uuid.uuid4())],
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).options(selectinload(Ticket.settlements)).where(Ticket.id == ticket_id))
        ticket = result.scalar_one()
        assert len(ticket.settlements) == 1
        assert ticket.settlements[0].id == settlement.id

    async def test_edit_ticket_settlement_link_rejected_wrong_project(self, client, db_session):
        """validate_settlement_ticket_link rzuca ValueError -> flash + continue (settlement pominiete)."""
        project = _make_project("TEL WrongProj", "tel-wrongproj")
        db_session.add(project)
        await db_session.flush()
        other_project = _make_project("TEL Other", "tel-other")
        db_session.add(other_project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tel-wrongproj@test.com")
        result = await db_session.execute(select(User).where(User.email == "tel-wrongproj@test.com"))
        user = result.scalar_one()

        settlement = Settlement(
            number=90003,
            name="Rozliczenie Other",
            period_from=date(2026, 3, 1),
            period_to=date(2026, 3, 31),
            status="draft",
            is_active=True,
            created_by_id=user.id,
        )
        db_session.add(settlement)
        await db_session.flush()
        # Powiazane z innym projektem niz ticket
        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=other_project.id))
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "T bez rozliczenia", "settlement_ids": [str(settlement.id)]},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).options(selectinload(Ticket.settlements)).where(Ticket.id == ticket_id))
        ticket = result.scalar_one()
        assert ticket.settlements == []

    async def test_edit_ticket_member_role_cannot_touch_settlements(self, client, db_session):
        """Member (bez rozliczenia:write) nie synchronizuje rozliczen -- branch 1096->1119 False."""
        project = _make_project("TEL Member", "tel-member")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="tel-member@test.com", is_superuser=False)
        result = await db_session.execute(select(User).where(User.email == "tel-member@test.com"))
        user = result.scalar_one()
        db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="member"))
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "T edytowany przez membera", "settlement_ids": [str(uuid.uuid4())]},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).options(selectinload(Ticket.settlements)).where(Ticket.id == ticket_id))
        ticket = result.scalar_one()
        assert ticket.title == "T edytowany przez membera"
        assert ticket.settlements == []


# ---------------------------------------------------------------------------
# time_tracking_log / time_tracking_delete -- brakujace galezie
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTimeTrackingLogMissingBranches:
    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ttl-noproj@test.com")
        resp = await client.post(
            "/dashboard/nonexistent-slug/scrum/time-tracking/log",
            json={"ticket_id": str(uuid.uuid4()), "duration_minutes": 60, "date_logged": "2026-03-01"},
        )
        assert resp.status_code == 404

    async def test_invalid_json_body(self, client, db_session):
        project = _make_project("TTL BadJSON", "ttl-badjson")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ttl-badjson@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            content=b"not-json{{{",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert "Invalid JSON" in resp.json()["error"]

    async def test_invalid_ticket_id_format(self, client, db_session):
        project = _make_project("TTL BadTicket", "ttl-badticket")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ttl-badticket@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={"ticket_id": "not-a-uuid", "duration_minutes": 60, "date_logged": "2026-03-01"},
        )
        assert resp.status_code == 400
        assert "Nieprawidlowy ticket_id" in resp.json()["error"]

    async def test_ticket_belongs_to_different_project(self, client, db_session):
        project = _make_project("TTL Proj A", "ttl-proj-a")
        db_session.add(project)
        other_project = _make_project("TTL Proj B", "ttl-proj-b")
        db_session.add(other_project)
        await db_session.flush()
        ticket = Ticket(project_id=other_project.id, number=1, title="T innego projektu", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ttl-crossproj@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={"ticket_id": str(ticket.id), "duration_minutes": 60, "date_logged": "2026-03-01"},
        )
        assert resp.status_code == 400
        assert "nie nalezy do tego projektu" in resp.json()["error"]

    async def test_user_not_project_member(self, client, db_session):
        """Superuser bez wpisu ProjectMember -- add_time_entry zwraca blad 400."""
        project = _make_project("TTL NoMember", "ttl-nomember")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ttl-nomember@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/time-tracking/log",
            json={"ticket_id": str(ticket.id), "duration_minutes": 60, "date_logged": "2026-03-01"},
        )
        assert resp.status_code == 400
        assert "nie jest czlonkiem projektu" in resp.json()["error"]


@pytest.mark.integration
class TestTimeTrackingDeleteMissingBranches:
    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ttd-noproj@test.com")
        resp = await client.delete(f"/dashboard/nonexistent-slug/scrum/time-tracking/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_entry_belongs_to_different_project(self, client, db_session):
        from monolynx.services.time_tracking import add_time_entry

        project = _make_project("TTD Proj A", "ttd-proj-a")
        db_session.add(project)
        other_project = _make_project("TTD Proj B", "ttd-proj-b")
        db_session.add(other_project)
        await db_session.flush()
        other_ticket = Ticket(project_id=other_project.id, number=1, title="T innego projektu", status="backlog", priority="medium")
        db_session.add(other_ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ttd-crossproj@test.com")
        result = await db_session.execute(select(User).where(User.email == "ttd-crossproj@test.com"))
        user = result.scalar_one()
        db_session.add(ProjectMember(project_id=other_project.id, user_id=user.id, role="member"))
        await db_session.flush()

        entry = await add_time_entry(other_ticket.id, user.id, 60, date(2026, 3, 1), None, db_session)
        assert not isinstance(entry, str)

        resp = await client.delete(f"/dashboard/{project.slug}/scrum/time-tracking/{entry.id}")
        assert resp.status_code == 403
        assert "nie nalezy do tego projektu" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Kryteria akceptacji -- CRUD (ZERO pokrycia przed MON-84)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCriterionCreate:
    async def test_requires_auth(self, client, db_session):
        project = _make_project("CC Auth", "cc-auth")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
            data={"description": "Cos"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="cc-noproj@test.com")
        resp = await client.post(
            f"/dashboard/nonexistent-slug/scrum/tickets/{uuid.uuid4()}/criteria",
            data={"description": "Cos"},
        )
        assert resp.status_code == 404

    async def test_ticket_not_found(self, client, db_session):
        project = _make_project("CC NoTicket", "cc-noticket")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cc-noticket@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/criteria",
            data={"description": "Cos"},
        )
        assert resp.status_code == 404

    async def test_empty_description_shows_error(self, client, db_session):
        project = _make_project("CC Empty", "cc-empty")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="cc-empty@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
            data={"description": "   "},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/scrum/tickets/{ticket_id}" in resp.headers["location"]

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket_id))
        assert result.scalars().all() == []

    async def test_success_creates_with_incrementing_position(self, client, db_session):
        project = _make_project("CC Success", "cc-success")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()
        ticket_id = ticket.id

        await login_session(client, db_session, email="cc-success@test.com")

        resp1 = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
            data={"description": "Pierwsze kryterium"},
            follow_redirects=False,
        )
        assert resp1.status_code == 303

        resp2 = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria",
            data={"description": "Drugie kryterium"},
            follow_redirects=False,
        )
        assert resp2.status_code == 303

        result = await db_session.execute(
            select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket_id).order_by(TicketAcceptanceCriterion.position)
        )
        criteria = result.scalars().all()
        assert len(criteria) == 2
        assert criteria[0].description == "Pierwsze kryterium"
        assert criteria[0].position == 0
        assert criteria[1].description == "Drugie kryterium"
        assert criteria[1].position == 1
        assert criteria[0].created_via_ai is False


async def _make_ticket_with_criterion(
    db_session: AsyncSession, project: Project, description: str = "Kryterium testowe"
) -> tuple[Ticket, TicketAcceptanceCriterion]:
    ticket = Ticket(project_id=project.id, number=1, title="T z kryterium", status="backlog", priority="medium")
    db_session.add(ticket)
    await db_session.flush()
    result = await db_session.execute(select(User).limit(1))
    user = result.scalars().first()
    criterion = TicketAcceptanceCriterion(
        ticket_id=ticket.id,
        description=description,
        position=0,
        created_by_user_id=user.id if user else None,
        created_via_ai=False,
    )
    db_session.add(criterion)
    await db_session.flush()
    return ticket, criterion


@pytest.mark.integration
class TestCriterionToggle:
    async def test_requires_auth(self, client, db_session):
        project = _make_project("CT Auth", "ct-auth")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ct-noproj@test.com")
        resp = await client.post(
            f"/dashboard/nonexistent-slug/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/toggle",
        )
        assert resp.status_code == 404

    async def test_ticket_not_found(self, client, db_session):
        project = _make_project("CT NoTicket", "ct-noticket")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ct-noticket@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/toggle",
        )
        assert resp.status_code == 404

    async def test_criterion_not_found(self, client, db_session):
        project = _make_project("CT NoCrit", "ct-nocrit")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ct-nocrit@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/toggle",
        )
        assert resp.status_code == 404

    async def test_toggle_marks_completed_then_uncompleted(self, client, db_session):
        project = _make_project("CT Toggle", "ct-toggle")
        db_session.add(project)
        await db_session.flush()
        await login_session(client, db_session, email="ct-toggle@test.com")
        ticket, criterion = await _make_ticket_with_criterion(db_session, project)
        criterion_id = criterion.id

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{criterion.id}/toggle",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == criterion_id))
        updated = result.scalar_one()
        assert updated.is_completed is True
        assert updated.completed_by_user_id is not None
        assert updated.completed_at is not None

        resp2 = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{criterion_id}/toggle",
            follow_redirects=False,
        )
        assert resp2.status_code == 303

        await db_session.refresh(updated)
        assert updated.is_completed is False
        assert updated.completed_by_user_id is None
        assert updated.completed_at is None


@pytest.mark.integration
class TestCriterionEdit:
    async def test_requires_auth(self, client, db_session):
        project = _make_project("CE Auth", "ce-auth")
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/edit",
            data={"description": "Cos"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ce-noproj@test.com")
        resp = await client.post(
            f"/dashboard/nonexistent-slug/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/edit",
            data={"description": "Cos"},
        )
        assert resp.status_code == 404

    async def test_ticket_not_found(self, client, db_session):
        project = _make_project("CE NoTicket", "ce-noticket")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ce-noticket@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/edit",
            data={"description": "Cos"},
        )
        assert resp.status_code == 404

    async def test_criterion_not_found(self, client, db_session):
        project = _make_project("CE NoCrit", "ce-nocrit")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ce-nocrit@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/edit",
            data={"description": "Cos"},
        )
        assert resp.status_code == 404

    async def test_empty_description_shows_error(self, client, db_session):
        project = _make_project("CE Empty", "ce-empty")
        db_session.add(project)
        await db_session.flush()
        await login_session(client, db_session, email="ce-empty@test.com")
        ticket, criterion = await _make_ticket_with_criterion(db_session, project, description="Oryginalny opis")
        criterion_id = criterion.id

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{criterion.id}/edit",
            data={"description": "   "},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == criterion_id))
        unchanged = result.scalar_one()
        assert unchanged.description == "Oryginalny opis"

    async def test_success_updates_description(self, client, db_session):
        project = _make_project("CE Success", "ce-success")
        db_session.add(project)
        await db_session.flush()
        await login_session(client, db_session, email="ce-success@test.com")
        ticket, criterion = await _make_ticket_with_criterion(db_session, project, description="Stary opis")
        criterion_id = criterion.id

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{criterion.id}/edit",
            data={"description": "Nowy zaktualizowany opis"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == criterion_id))
        updated = result.scalar_one()
        assert updated.description == "Nowy zaktualizowany opis"


@pytest.mark.integration
class TestCriterionDelete:
    async def test_requires_auth(self, client, db_session):
        project = _make_project("CD Auth", "cd-auth")
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="cd-noproj@test.com")
        resp = await client.post(
            f"/dashboard/nonexistent-slug/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/delete",
        )
        assert resp.status_code == 404

    async def test_ticket_not_found(self, client, db_session):
        project = _make_project("CD NoTicket", "cd-noticket")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="cd-noticket@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{uuid.uuid4()}/criteria/{uuid.uuid4()}/delete",
        )
        assert resp.status_code == 404

    async def test_criterion_not_found(self, client, db_session):
        project = _make_project("CD NoCrit", "cd-nocrit")
        db_session.add(project)
        await db_session.flush()
        ticket = Ticket(project_id=project.id, number=1, title="T", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="cd-nocrit@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{uuid.uuid4()}/delete",
        )
        assert resp.status_code == 404

    async def test_success_removes_from_db(self, client, db_session):
        project = _make_project("CD Success", "cd-success")
        db_session.add(project)
        await db_session.flush()
        await login_session(client, db_session, email="cd-success@test.com")
        ticket, criterion = await _make_ticket_with_criterion(db_session, project)
        criterion_id = criterion.id

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/criteria/{criterion.id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == criterion_id))
        assert result.scalar_one_or_none() is None
