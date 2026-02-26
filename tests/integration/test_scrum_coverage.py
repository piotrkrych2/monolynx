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
from datetime import date

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
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
