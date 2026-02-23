"""Testy integracyjne -- rozszerzone scrum (filtry, komentarze, sprint update, edge cases)."""

import secrets
import uuid
from datetime import date

import pytest

from monolynx.models.project import Project
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Backlog filters
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBacklogFilters:
    """Filtrowanie backlogu: status, priority, search, assignee, sprint, pagination."""

    async def test_filter_by_status(self, client, db_session):
        project = Project(
            name="BF Status",
            slug="bf-status",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        t1 = Ticket(project_id=project.id, title="Ticket TODO", status="todo", priority="medium")
        t2 = Ticket(project_id=project.id, title="Ticket Backlog", status="backlog", priority="medium")
        db_session.add_all([t1, t2])
        await db_session.flush()

        await login_session(client, db_session, email="bf-status@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog?status=todo")
        assert resp.status_code == 200
        assert "Ticket TODO" in resp.text
        assert "Ticket Backlog" not in resp.text

    async def test_filter_by_priority(self, client, db_session):
        project = Project(
            name="BF Prio",
            slug="bf-prio",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        t_high = Ticket(project_id=project.id, title="High prio ticket", status="backlog", priority="high")
        t_low = Ticket(project_id=project.id, title="Low prio ticket", status="backlog", priority="low")
        db_session.add_all([t_high, t_low])
        await db_session.flush()

        await login_session(client, db_session, email="bf-prio@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog?priority=high")
        assert resp.status_code == 200
        assert "High prio ticket" in resp.text
        assert "Low prio ticket" not in resp.text

    async def test_filter_by_search(self, client, db_session):
        project = Project(
            name="BF Search",
            slug="bf-search",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        t1 = Ticket(project_id=project.id, title="Napraw blad logowania", status="backlog", priority="medium")
        t2 = Ticket(project_id=project.id, title="Dodaj eksport CSV", status="backlog", priority="medium")
        db_session.add_all([t1, t2])
        await db_session.flush()

        await login_session(client, db_session, email="bf-search@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog?search=logowania")
        assert resp.status_code == 200
        assert "Napraw blad logowania" in resp.text
        assert "Dodaj eksport CSV" not in resp.text

    async def test_filter_by_assignee_id(self, client, db_session):
        project = Project(
            name="BF Assignee",
            slug="bf-assignee",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="bf-assignee@test.com")

        # Retrieve the user created by login_session for assignee_id
        from sqlalchemy import select as sa_select

        user_result = await db_session.execute(sa_select(User).where(User.email == "bf-assignee@test.com"))
        user = user_result.scalar_one()

        t_assigned = Ticket(
            project_id=project.id,
            title="Przypisany ticket",
            status="backlog",
            priority="medium",
            assignee_id=user.id,
        )
        t_unassigned = Ticket(
            project_id=project.id,
            title="Nieprzypisany ticket",
            status="backlog",
            priority="medium",
        )
        db_session.add_all([t_assigned, t_unassigned])
        await db_session.flush()

        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog?assignee_id={user.id}")
        assert resp.status_code == 200
        assert "Przypisany ticket" in resp.text
        assert "Nieprzypisany ticket" not in resp.text

    async def test_filter_by_sprint_id(self, client, db_session):
        project = Project(
            name="BF Sprint",
            slug="bf-sprint",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint filtr",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        t_in_sprint = Ticket(
            project_id=project.id,
            title="Ticket w sprincie",
            status="todo",
            priority="medium",
            sprint_id=sprint.id,
        )
        t_no_sprint = Ticket(
            project_id=project.id,
            title="Ticket bez sprintu",
            status="backlog",
            priority="medium",
        )
        db_session.add_all([t_in_sprint, t_no_sprint])
        await db_session.flush()

        await login_session(client, db_session, email="bf-sprint@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog?sprint_id={sprint.id}")
        assert resp.status_code == 200
        assert "Ticket w sprincie" in resp.text
        assert "Ticket bez sprintu" not in resp.text

    async def test_show_completed_sprints_flag(self, client, db_session):
        project = Project(
            name="BF CompSpr",
            slug="bf-compspr",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        completed_sprint = Sprint(
            project_id=project.id,
            name="Zakonczony sprint",
            start_date=date(2026, 2, 1),
            status="completed",
        )
        db_session.add(completed_sprint)
        await db_session.flush()

        t_completed = Ticket(
            project_id=project.id,
            title="Ticket z zakonczonym sprintem",
            status="done",
            priority="medium",
            sprint_id=completed_sprint.id,
        )
        db_session.add(t_completed)
        await db_session.flush()

        await login_session(client, db_session, email="bf-compspr@test.com")

        # Without flag -- ticket from completed sprint hidden
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog")
        assert resp.status_code == 200
        assert "Ticket z zakonczonym sprintem" not in resp.text

        # With flag -- ticket from completed sprint visible
        resp2 = await client.get(f"/dashboard/{project.slug}/scrum/backlog?show_completed_sprints=1")
        assert resp2.status_code == 200
        assert "Ticket z zakonczonym sprintem" in resp2.text

    async def test_backlog_pagination(self, client, db_session):
        project = Project(
            name="BF Page",
            slug="bf-page",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        # Create 25 tickets (per_page=20, so 2 pages)
        for i in range(25):
            t = Ticket(
                project_id=project.id,
                title=f"Ticket paginacji {i:03d}",
                status="backlog",
                priority="medium",
                order=i,
            )
            db_session.add(t)
        await db_session.flush()

        await login_session(client, db_session, email="bf-page@test.com")

        # Page 1
        resp1 = await client.get(f"/dashboard/{project.slug}/scrum/backlog?page=1")
        assert resp1.status_code == 200

        # Page 2
        resp2 = await client.get(f"/dashboard/{project.slug}/scrum/backlog?page=2")
        assert resp2.status_code == 200

    async def test_backlog_invalid_page(self, client, db_session):
        project = Project(
            name="BF InvPage",
            slug="bf-invpage",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="bf-invpage@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog?page=abc")
        assert resp.status_code == 200

    async def test_backlog_combined_filters(self, client, db_session):
        project = Project(
            name="BF Combo",
            slug="bf-combo",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        t1 = Ticket(project_id=project.id, title="Combo match", status="todo", priority="high")
        t2 = Ticket(project_id=project.id, title="Combo miss", status="backlog", priority="low")
        db_session.add_all([t1, t2])
        await db_session.flush()

        await login_session(client, db_session, email="bf-combo@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog?status=todo&priority=high&search=Combo")
        assert resp.status_code == 200
        assert "Combo match" in resp.text
        assert "Combo miss" not in resp.text


# ---------------------------------------------------------------------------
# Board with tickets in different columns
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBoardColumns:
    """Tablica Kanban z ticketami w roznych kolumnach."""

    async def test_board_tickets_in_all_columns(self, client, db_session):
        project = Project(
            name="BC AllCol",
            slug="bc-allcol",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint kolumny",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        tickets = [
            Ticket(project_id=project.id, sprint_id=sprint.id, title="Kolumna TODO", status="todo", priority="medium"),
            Ticket(project_id=project.id, sprint_id=sprint.id, title="Kolumna INPROG", status="in_progress", priority="high"),
            Ticket(project_id=project.id, sprint_id=sprint.id, title="Kolumna REVIEW", status="in_review", priority="low"),
            Ticket(project_id=project.id, sprint_id=sprint.id, title="Kolumna DONE", status="done", priority="medium", story_points=3),
        ]
        db_session.add_all(tickets)
        await db_session.flush()

        await login_session(client, db_session, email="bc-allcol@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/board")
        assert resp.status_code == 200
        assert "Kolumna TODO" in resp.text
        assert "Kolumna INPROG" in resp.text
        assert "Kolumna REVIEW" in resp.text
        assert "Kolumna DONE" in resp.text

    async def test_board_story_points_displayed(self, client, db_session):
        project = Project(
            name="BC SP",
            slug="bc-sp",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint SP",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        t1 = Ticket(project_id=project.id, sprint_id=sprint.id, title="SP ticket", status="todo", priority="medium", story_points=5)
        t2 = Ticket(project_id=project.id, sprint_id=sprint.id, title="SP done", status="done", priority="low", story_points=3)
        db_session.add_all([t1, t2])
        await db_session.flush()

        await login_session(client, db_session, email="bc-sp@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/board")
        assert resp.status_code == 200
        assert "SP ticket" in resp.text

    async def test_board_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="bc-notfound@test.com")
        resp = await client.get("/dashboard/nonexistent-proj/scrum/board")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Ticket comments
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketComments:
    """Tworzenie komentarzy do ticketow."""

    async def test_add_comment_success(self, client, db_session):
        project = Project(
            name="TC Comment",
            slug="tc-comment",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Ticket z komentarzem",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tc-comment@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/comments",
            data={"content": "Moj komentarz testowy"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/scrum/tickets/{ticket.id}#comments" in resp.headers["location"]

    async def test_add_comment_empty_content(self, client, db_session):
        project = Project(
            name="TC EmptyC",
            slug="tc-emptyc",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Ticket pusty komentarz",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tc-emptyc@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/comments",
            data={"content": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/scrum/tickets/{ticket.id}#comments" in resp.headers["location"]

    async def test_add_comment_whitespace_only(self, client, db_session):
        project = Project(
            name="TC WhitespC",
            slug="tc-whitspc",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Ticket whitespace komentarz",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tc-whitspc@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/comments",
            data={"content": "   "},
            follow_redirects=False,
        )
        # Whitespace stripped -> empty -> redirect with error flash
        assert resp.status_code == 303

    async def test_comment_ticket_not_found(self, client, db_session):
        project = Project(
            name="TC CNoT",
            slug="tc-cnot",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tc-cnot@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{fake_id}/comments",
            data={"content": "Komentarz do nieistniejacego"},
        )
        assert resp.status_code == 404

    async def test_comment_requires_auth(self, client, db_session):
        project = Project(
            name="TC CAuth",
            slug="tc-cauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Ticket auth komentarz",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/comments",
            data={"content": "Bez logowania"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_comment_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="tc-cpnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-proj-c/scrum/tickets/{fake_id}/comments",
            data={"content": "Komentarz do nieistniejacego projektu"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Ticket sprint update (PATCH)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketSprintUpdate:
    """PATCH /{slug}/scrum/tickets/{id}/sprint -- zmiana sprintu ticketa."""

    async def test_assign_ticket_to_sprint(self, client, db_session):
        project = Project(
            name="TSU Assign",
            slug="tsu-assign",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint przypisz",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Do przypisania",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tsu-assign@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/sprint",
            json={"sprint_id": str(sprint.id)},
        )
        assert resp.status_code == 200

        # Verify status changed from backlog to todo
        from sqlalchemy import select

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        updated_ticket = result.scalar_one()
        assert updated_ticket.sprint_id == sprint.id
        assert updated_ticket.status == "todo"

    async def test_remove_ticket_from_sprint(self, client, db_session):
        project = Project(
            name="TSU Remove",
            slug="tsu-remove",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint usun",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Do usuniecia ze sprintu",
            status="todo",
            priority="medium",
            sprint_id=sprint.id,
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tsu-remove@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/sprint",
            json={"sprint_id": None},
        )
        assert resp.status_code == 200

        # Verify status changed from todo to backlog and sprint_id cleared
        from sqlalchemy import select

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        updated_ticket = result.scalar_one()
        assert updated_ticket.sprint_id is None
        assert updated_ticket.status == "backlog"

    async def test_assign_to_sprint_no_status_change_if_not_backlog(self, client, db_session):
        project = Project(
            name="TSU NoChg",
            slug="tsu-nochg",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint nochg",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="In progress ticket",
            status="in_progress",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tsu-nochg@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/sprint",
            json={"sprint_id": str(sprint.id)},
        )
        assert resp.status_code == 200

        # Status should remain in_progress (only backlog -> todo)
        from sqlalchemy import select

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        updated_ticket = result.scalar_one()
        assert updated_ticket.status == "in_progress"

    async def test_remove_from_sprint_no_status_change_if_not_todo(self, client, db_session):
        project = Project(
            name="TSU NoChg2",
            slug="tsu-nochg2",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint nochg2",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Done ticket usun sprint",
            status="done",
            priority="medium",
            sprint_id=sprint.id,
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tsu-nochg2@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/sprint",
            json={"sprint_id": None},
        )
        assert resp.status_code == 200

        # Status should remain done (only todo -> backlog)
        from sqlalchemy import select

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        updated_ticket = result.scalar_one()
        assert updated_ticket.status == "done"
        assert updated_ticket.sprint_id is None

    async def test_sprint_update_invalid_sprint_id(self, client, db_session):
        project = Project(
            name="TSU InvSpr",
            slug="tsu-invspr",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Ticket invalid sprint",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tsu-invspr@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/sprint",
            json={"sprint_id": "not-a-uuid"},
        )
        assert resp.status_code == 422

    async def test_sprint_update_completed_sprint_rejected(self, client, db_session):
        project = Project(
            name="TSU CompSpr",
            slug="tsu-compspr",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        completed_sprint = Sprint(
            project_id=project.id,
            name="Zakonczony",
            start_date=date(2026, 2, 1),
            status="completed",
        )
        db_session.add(completed_sprint)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Ticket do zakonczoneog sprintu",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tsu-compspr@test.com")
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/sprint",
            json={"sprint_id": str(completed_sprint.id)},
        )
        # Sprint not found because query filters out completed sprints
        assert resp.status_code == 404

    async def test_sprint_update_requires_auth(self, client, db_session):
        project = Project(
            name="TSU Auth",
            slug="tsu-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Ticket auth sprint",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/sprint",
            json={"sprint_id": None},
        )
        assert resp.status_code == 401

    async def test_sprint_update_ticket_not_found(self, client, db_session):
        project = Project(
            name="TSU TNF",
            slug="tsu-tnf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tsu-tnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{fake_id}/sprint",
            json={"sprint_id": None},
        )
        assert resp.status_code == 404

    async def test_sprint_update_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="tsu-pnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.patch(
            f"/dashboard/nonexistent-proj-su/scrum/tickets/{fake_id}/sprint",
            json={"sprint_id": None},
        )
        assert resp.status_code == 404

    async def test_sprint_update_nonexistent_sprint(self, client, db_session):
        project = Project(
            name="TSU NoSpr",
            slug="tsu-nospr",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Ticket nospr",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="tsu-nospr@test.com")
        fake_sprint_id = uuid.uuid4()
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/sprint",
            json={"sprint_id": str(fake_sprint_id)},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sprint list with filters and pagination
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSprintListExtended:
    """Filtrowanie i paginacja listy sprintow."""

    async def test_sprint_list_status_all(self, client, db_session):
        project = Project(
            name="SLE All",
            slug="sle-all",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        s_planning = Sprint(project_id=project.id, name="Planning spr", start_date=date(2026, 3, 1), status="planning")
        s_completed = Sprint(project_id=project.id, name="Completed spr", start_date=date(2026, 2, 1), status="completed")
        db_session.add_all([s_planning, s_completed])
        await db_session.flush()

        await login_session(client, db_session, email="sle-all@test.com")

        # Default: completed hidden
        resp = await client.get(f"/dashboard/{project.slug}/scrum/sprints")
        assert resp.status_code == 200
        assert "Planning spr" in resp.text
        assert "Completed spr" not in resp.text

        # status=all: show everything
        resp2 = await client.get(f"/dashboard/{project.slug}/scrum/sprints?status=all")
        assert resp2.status_code == 200
        assert "Planning spr" in resp2.text
        assert "Completed spr" in resp2.text

    async def test_sprint_list_filter_by_specific_status(self, client, db_session):
        project = Project(
            name="SLE Filt",
            slug="sle-filt",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        s_active = Sprint(project_id=project.id, name="Active spr filt", start_date=date(2026, 3, 1), status="active")
        s_planning = Sprint(project_id=project.id, name="Planning spr filt", start_date=date(2026, 3, 15), status="planning")
        db_session.add_all([s_active, s_planning])
        await db_session.flush()

        await login_session(client, db_session, email="sle-filt@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/sprints?status=active")
        assert resp.status_code == 200
        assert "Active spr filt" in resp.text
        assert "Planning spr filt" not in resp.text

    async def test_sprint_list_pagination(self, client, db_session):
        project = Project(
            name="SLE Page",
            slug="sle-page",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        # Create 15 sprints (per_page=10, so 2 pages)
        for i in range(15):
            s = Sprint(
                project_id=project.id,
                name=f"Sprint pag {i:03d}",
                start_date=date(2026, 3, 1),
                status="planning",
            )
            db_session.add(s)
        await db_session.flush()

        await login_session(client, db_session, email="sle-page@test.com")

        resp1 = await client.get(f"/dashboard/{project.slug}/scrum/sprints?page=1")
        assert resp1.status_code == 200

        resp2 = await client.get(f"/dashboard/{project.slug}/scrum/sprints?page=2")
        assert resp2.status_code == 200

    async def test_sprint_list_invalid_page(self, client, db_session):
        project = Project(
            name="SLE InvPg",
            slug="sle-invpg",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sle-invpg@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/sprints?page=xyz")
        assert resp.status_code == 200

    async def test_sprint_list_requires_auth(self, client, db_session):
        project = Project(
            name="SLE Auth",
            slug="sle-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/sprints",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_sprint_list_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="sle-pnf@test.com")
        resp = await client.get("/dashboard/nonexistent-proj-sl/scrum/sprints")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sprint create extended
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSprintCreateExtended:
    """Tworzenie sprintow -- brakujace pola, rozne scenariusze."""

    async def test_create_sprint_name_only_no_start_date(self, client, db_session):
        project = Project(
            name="SCE NoDate",
            slug="sce-nodate",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sce-nodate@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/create",
            data={"name": "Sprint bez daty", "start_date": ""},
        )
        assert resp.status_code == 200
        assert "Nazwa i data rozpoczecia sa wymagane" in resp.text

    async def test_create_sprint_with_end_date(self, client, db_session):
        project = Project(
            name="SCE EndDate",
            slug="sce-enddate",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sce-enddate@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/create",
            data={
                "name": "Sprint z data konca",
                "start_date": "2026-03-01",
                "end_date": "2026-03-14",
                "goal": "Cel sprinta",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/scrum/sprints" in resp.headers["location"]

    async def test_create_sprint_without_goal(self, client, db_session):
        project = Project(
            name="SCE NoGoal",
            slug="sce-nogoal",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sce-nogoal@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/create",
            data={
                "name": "Sprint bez celu",
                "start_date": "2026-03-01",
                "goal": "",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_create_sprint_requires_auth(self, client, db_session):
        project = Project(
            name="SCE Auth",
            slug="sce-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/create",
            data={"name": "Test", "start_date": "2026-03-01"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_create_sprint_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="sce-pnf@test.com")
        resp = await client.post(
            "/dashboard/nonexistent-proj-sc/scrum/sprints/create",
            data={"name": "Test", "start_date": "2026-03-01"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sprint start/complete extended
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSprintLifecycleExtended:
    """Sprint start/complete -- error handling, edge cases."""

    async def test_start_sprint_not_found(self, client, db_session):
        project = Project(
            name="SLE StartNF",
            slug="sle-startnf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sle-startnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{fake_id}/start",
        )
        # start_sprint returns error "Sprint nie istnieje", renders page with error
        assert resp.status_code == 200
        assert "Sprint nie istnieje" in resp.text

    async def test_start_already_active_sprint(self, client, db_session):
        project = Project(
            name="SLE StartAct",
            slug="sle-startact",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Juz aktywny",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        await login_session(client, db_session, email="sle-startact@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{sprint.id}/start",
        )
        # Cannot start an already active sprint
        assert resp.status_code == 200
        assert "planowania" in resp.text or "aktywny" in resp.text

    async def test_complete_sprint_not_found(self, client, db_session):
        project = Project(
            name="SLE ComplNF",
            slug="sle-complnf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="sle-complnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{fake_id}/complete",
        )
        assert resp.status_code == 200
        assert "Sprint nie istnieje" in resp.text

    async def test_complete_planning_sprint_fails(self, client, db_session):
        project = Project(
            name="SLE ComplPl",
            slug="sle-complpl",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Planowany kompletny",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        await login_session(client, db_session, email="sle-complpl@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{sprint.id}/complete",
        )
        assert resp.status_code == 200
        assert "aktywny" in resp.text

    async def test_start_sprint_requires_auth(self, client, db_session):
        project = Project(
            name="SLE SAuth",
            slug="sle-sauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Auth start sprint",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{sprint.id}/start",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_complete_sprint_requires_auth(self, client, db_session):
        project = Project(
            name="SLE CAuth",
            slug="sle-cauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Auth complete sprint",
            start_date=date(2026, 3, 1),
            status="active",
        )
        db_session.add(sprint)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/sprints/{sprint.id}/complete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_start_sprint_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="sle-spnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-proj-ss/scrum/sprints/{fake_id}/start",
        )
        assert resp.status_code == 404

    async def test_complete_sprint_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="sle-cpnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-proj-cs/scrum/sprints/{fake_id}/complete",
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Edge cases: project not found, auth redirects on various endpoints
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestScrumEdgeCases:
    """Rozne edge cases: 404, auth, nieistniejace projekty."""

    async def test_backlog_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ec-blnf@test.com")
        resp = await client.get("/dashboard/nonexistent-proj-bl/scrum/backlog")
        assert resp.status_code == 404

    async def test_ticket_create_form_requires_auth(self, client, db_session):
        project = Project(
            name="EC TCAuth",
            slug="ec-tcauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_ticket_create_form_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ec-tcpnf@test.com")
        resp = await client.get("/dashboard/nonexistent-proj-tcf/scrum/tickets/create")
        assert resp.status_code == 404

    async def test_ticket_create_post_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ec-tcppnf@test.com")
        resp = await client.post(
            "/dashboard/nonexistent-proj-tcp/scrum/tickets/create",
            data={"title": "Test", "priority": "medium"},
        )
        assert resp.status_code == 404

    async def test_ticket_detail_requires_auth(self, client, db_session):
        project = Project(
            name="EC TDAuth",
            slug="ec-tdauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(
            project_id=project.id,
            title="Auth detail",
            status="backlog",
            priority="medium",
        )
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_ticket_detail_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ec-tdpnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/nonexistent-proj-td/scrum/tickets/{fake_id}")
        assert resp.status_code == 404

    async def test_ticket_edit_form_requires_auth(self, client, db_session):
        project = Project(
            name="EC TEAuth",
            slug="ec-teauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(project_id=project.id, title="Auth edit", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_ticket_edit_form_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ec-tefpnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/nonexistent-proj-tef/scrum/tickets/{fake_id}/edit")
        assert resp.status_code == 404

    async def test_ticket_edit_form_ticket_not_found(self, client, db_session):
        project = Project(
            name="EC TENotF",
            slug="ec-tenotf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ec-tenotf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{fake_id}/edit")
        assert resp.status_code == 404

    async def test_ticket_edit_post_requires_auth(self, client, db_session):
        project = Project(
            name="EC TEPAuth",
            slug="ec-tepauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(project_id=project.id, title="Auth edit post", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "New title"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_ticket_edit_post_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ec-teppnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-proj-tep/scrum/tickets/{fake_id}/edit",
            data={"title": "New title"},
        )
        assert resp.status_code == 404

    async def test_ticket_edit_post_ticket_not_found(self, client, db_session):
        project = Project(
            name="EC TEPNotF",
            slug="ec-tepnotf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ec-tepnotf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{fake_id}/edit",
            data={"title": "New title"},
        )
        assert resp.status_code == 404

    async def test_ticket_edit_post_empty_title_redirects(self, client, db_session):
        project = Project(
            name="EC TEPNoT",
            slug="ec-tepnot",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(project_id=project.id, title="Edytuj tytul", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        await login_session(client, db_session, email="ec-tepnot@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "", "priority": "medium"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/edit" in resp.headers["location"]

    async def test_ticket_delete_requires_auth(self, client, db_session):
        project = Project(
            name="EC TDlAuth",
            slug="ec-tdlauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(project_id=project.id, title="Auth delete", status="backlog", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_ticket_delete_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ec-tdpnf2@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/nonexistent-proj-dl/scrum/tickets/{fake_id}/delete",
        )
        assert resp.status_code == 404

    async def test_ticket_delete_ticket_not_found(self, client, db_session):
        project = Project(
            name="EC TDlNotF",
            slug="ec-tdlnotf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ec-tdlnotf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{fake_id}/delete",
        )
        assert resp.status_code == 404

    async def test_ticket_status_update_requires_auth(self, client, db_session):
        project = Project(
            name="EC TSUAuth",
            slug="ec-tsuauth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        ticket = Ticket(project_id=project.id, title="Auth status", status="todo", priority="medium")
        db_session.add(ticket)
        await db_session.flush()

        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/status",
            json={"status": "done"},
        )
        assert resp.status_code == 401

    async def test_ticket_status_update_project_not_found(self, client, db_session):
        await login_session(client, db_session, email="ec-tsupnf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.patch(
            f"/dashboard/nonexistent-proj-tsu/scrum/tickets/{fake_id}/status",
            json={"status": "done"},
        )
        assert resp.status_code == 404

    async def test_ticket_status_update_ticket_not_found(self, client, db_session):
        project = Project(
            name="EC TSUNotF",
            slug="ec-tsunotf",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="ec-tsunotf@test.com")
        fake_id = uuid.uuid4()
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{fake_id}/status",
            json={"status": "done"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Ticket create with sprint (status auto-set to "todo")
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketCreateWithSprint:
    """Tworzenie ticketa z przypisanym sprintem -- status auto-zmiana."""

    async def test_create_ticket_with_sprint_sets_todo(self, client, db_session):
        project = Project(
            name="TCS Todo",
            slug="tcs-todo",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        sprint = Sprint(
            project_id=project.id,
            name="Sprint na ticket",
            start_date=date(2026, 3, 1),
            status="planning",
        )
        db_session.add(sprint)
        await db_session.flush()

        await login_session(client, db_session, email="tcs-todo@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={
                "title": "Ticket w sprincie nowy",
                "priority": "high",
                "sprint_id": str(sprint.id),
                "story_points": "5",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_create_ticket_with_assignee(self, client, db_session):
        project = Project(
            name="TCS Assignee",
            slug="tcs-assignee",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tcs-assignee@test.com")

        # Use the user created by login_session as the assignee
        from sqlalchemy import select

        user_result = await db_session.execute(select(User).where(User.email == "tcs-assignee@test.com"))
        user = user_result.scalar_one()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={
                "title": "Ticket z przypisana osoba",
                "priority": "medium",
                "assignee_id": str(user.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_create_ticket_with_invalid_priority_defaults_to_medium(self, client, db_session):
        project = Project(
            name="TCS InvPrio",
            slug="tcs-invprio",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="tcs-invprio@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Ticket invalid prio", "priority": "ultra_mega"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_create_ticket_requires_auth(self, client, db_session):
        project = Project(
            name="TCS Auth",
            slug="tcs-auth",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Bez logowania", "priority": "medium"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]
