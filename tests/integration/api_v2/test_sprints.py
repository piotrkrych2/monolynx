"""Testy integracyjne API v2 -- Sprints (MON-142).

Pokrywa 7 endpointów `src/monolynx/api/v2/sprints.py`: list/get/create/update
sprintu, start/complete, burndown. Wołane serwisy: `services/tickets.py`
(list/get/create/update sprint), `services/sprint.py` (start/complete, zwraca
`str | None` -- router mapuje niepusty komunikat na błąd przez
`service_error`), `services/burndown.py` (`get_burndown_data`).
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket

from .conftest import make_actor, make_owner_project

pytestmark = pytest.mark.integration


async def _make_sprint(db_session, project, **kwargs) -> Sprint:
    sprint = Sprint(
        project_id=project.id,
        name=kwargs.pop("name", "Sprint testowy"),
        start_date=kwargs.pop("start_date", date(2026, 1, 1)),
        status=kwargs.pop("status", "planning"),
        **kwargs,
    )
    db_session.add(sprint)
    await db_session.flush()
    return sprint


async def _make_ticket(db_session, project, number: int, **kwargs) -> Ticket:
    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=kwargs.pop("title", f"Ticket #{number}"),
        status=kwargs.pop("status", "todo"),
        priority=kwargs.pop("priority", "medium"),
        **kwargs,
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/sprints
# ---------------------------------------------------------------------------


class TestListSprints:
    async def test_happy_path_includes_stats(self, client, db_session):
        actor, project = await make_owner_project(db_session, "list-sprints")
        sprint = await _make_sprint(db_session, project, name="Sprint 1")
        await _make_ticket(db_session, project, 1, sprint_id=sprint.id, story_points=3)
        await _make_ticket(db_session, project, 2, sprint_id=sprint.id, story_points=2)

        response = await client.get(f"/api/v2/projects/{project.slug}/sprints", headers=actor.headers)

        assert response.status_code == 200
        item = next(s for s in response.json()["items"] if s["id"] == str(sprint.id))
        assert item["name"] == "Sprint 1"
        assert item["status"] == "planning"
        assert item["ticket_count"] == 2
        assert item["story_points_total"] == 5

    async def test_non_member_returns_403(self, client, db_session):
        _actor, project = await make_owner_project(db_session, "list-sprints-403")
        outsider = await make_actor(db_session, "list-sprints-403-out")

        response = await client.get(f"/api/v2/projects/{project.slug}/sprints", headers=outsider.headers)

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/sprints/{sprint_id}
# ---------------------------------------------------------------------------


class TestGetSprint:
    async def test_happy_path(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-sprint")
        sprint = await _make_sprint(db_session, project, name="Do podejrzenia", goal="Cel sprintu")

        response = await client.get(f"/api/v2/projects/{project.slug}/sprints/{sprint.id}", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Do podejrzenia"
        assert body["goal"] == "Cel sprintu"
        assert body["start_date"] == "2026-01-01"

    async def test_nonexistent_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "get-sprint-404")

        response = await client.get(f"/api/v2/projects/{project.slug}/sprints/{uuid.uuid4()}", headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# POST /api/v2/projects/{slug}/sprints
# ---------------------------------------------------------------------------


class TestCreateSprint:
    async def test_happy_path(self, client, db_session):
        actor, project = await make_owner_project(db_session, "create-sprint")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/sprints",
            headers=actor.headers,
            json={"name": "Sprint z API v2", "start_date": "2026-03-01", "goal": "Dowiezc feature"},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Sprint z API v2"
        assert body["status"] == "planning"
        assert body["goal"] == "Dowiezc feature"

        db_sprint = (await db_session.execute(select(Sprint).where(Sprint.id == uuid.UUID(body["id"])))).scalar_one()
        assert db_sprint.start_date.isoformat() == "2026-03-01"

    async def test_missing_name_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "create-sprint-invalid")

        response = await client.post(
            f"/api/v2/projects/{project.slug}/sprints",
            headers=actor.headers,
            json={"start_date": "2026-03-01"},
        )

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/v2/projects/{slug}/sprints/{sprint_id}
# ---------------------------------------------------------------------------


class TestUpdateSprint:
    async def test_happy_path_updates_goal(self, client, db_session):
        actor, project = await make_owner_project(db_session, "update-sprint")
        sprint = await _make_sprint(db_session, project, goal="Stary cel")

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/sprints/{sprint.id}",
            headers=actor.headers,
            json={"goal": "Nowy cel"},
        )

        assert response.status_code == 200
        assert response.json()["goal"] == "Nowy cel"
        await db_session.refresh(sprint)
        assert sprint.goal == "Nowy cel"

    async def test_end_date_before_start_date_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "update-sprint-invalid-dates")
        sprint = await _make_sprint(db_session, project, start_date=date(2026, 5, 1))

        response = await client.patch(
            f"/api/v2/projects/{project.slug}/sprints/{sprint.id}",
            headers=actor.headers,
            json={"end_date": "2026-04-01"},
        )

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"


# ---------------------------------------------------------------------------
# POST /api/v2/projects/{slug}/sprints/{sprint_id}/start
# ---------------------------------------------------------------------------


class TestStartSprint:
    async def test_happy_path(self, client, db_session):
        actor, project = await make_owner_project(db_session, "start-sprint")
        sprint = await _make_sprint(db_session, project, status="planning")

        response = await client.post(f"/api/v2/projects/{project.slug}/sprints/{sprint.id}/start", headers=actor.headers)

        assert response.status_code == 200
        assert response.json()["status"] == "active"
        await db_session.refresh(sprint)
        assert sprint.status == "active"

    async def test_second_active_sprint_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "start-sprint-conflict")
        await _make_sprint(db_session, project, name="Juz aktywny", status="active")
        candidate = await _make_sprint(db_session, project, name="Kandydat", status="planning")

        response = await client.post(f"/api/v2/projects/{project.slug}/sprints/{candidate.id}/start", headers=actor.headers)

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        await db_session.refresh(candidate)
        assert candidate.status == "planning"


# ---------------------------------------------------------------------------
# POST /api/v2/projects/{slug}/sprints/{sprint_id}/complete
# ---------------------------------------------------------------------------


class TestCompleteSprint:
    async def test_happy_path_moves_unfinished_tickets_to_backlog(self, client, db_session):
        actor, project = await make_owner_project(db_session, "complete-sprint")
        sprint = await _make_sprint(db_session, project, status="active")
        unfinished = await _make_ticket(db_session, project, 1, status="in_progress", sprint_id=sprint.id)
        done = await _make_ticket(db_session, project, 2, status="done", sprint_id=sprint.id)

        response = await client.post(f"/api/v2/projects/{project.slug}/sprints/{sprint.id}/complete", headers=actor.headers)

        assert response.status_code == 200
        assert response.json()["status"] == "completed"

        await db_session.refresh(unfinished)
        await db_session.refresh(done)
        assert unfinished.status == "backlog"
        assert unfinished.sprint_id is None
        assert done.status == "done"
        assert done.sprint_id == sprint.id

    async def test_completing_non_active_sprint_returns_422(self, client, db_session):
        actor, project = await make_owner_project(db_session, "complete-sprint-invalid")
        sprint = await _make_sprint(db_session, project, status="planning")

        response = await client.post(f"/api/v2/projects/{project.slug}/sprints/{sprint.id}/complete", headers=actor.headers)

        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/burndown
# ---------------------------------------------------------------------------


class TestGetBurndown:
    async def test_with_explicit_sprint_id(self, client, db_session):
        actor, project = await make_owner_project(db_session, "burndown-explicit")
        sprint = await _make_sprint(db_session, project, name="Sprint burndown", end_date=date(2026, 1, 15))
        await _make_ticket(db_session, project, 1, sprint_id=sprint.id, story_points=5, status="done")

        response = await client.get(f"/api/v2/projects/{project.slug}/burndown?sprint_id={sprint.id}", headers=actor.headers)

        assert response.status_code == 200
        body = response.json()
        assert body["sprint"]["name"] == "Sprint burndown"
        assert body["sprint"]["total_story_points"] == 5

    async def test_without_sprint_id_uses_active_sprint(self, client, db_session):
        actor, project = await make_owner_project(db_session, "burndown-active")
        active_sprint = await _make_sprint(db_session, project, name="Aktywny", status="active")

        response = await client.get(f"/api/v2/projects/{project.slug}/burndown", headers=actor.headers)

        assert response.status_code == 200
        assert response.json()["sprint"]["name"] == active_sprint.name

    async def test_without_sprint_id_and_no_active_sprint_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "burndown-no-active")
        await _make_sprint(db_session, project, status="planning")

        response = await client.get(f"/api/v2/projects/{project.slug}/burndown", headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

    async def test_nonexistent_sprint_id_returns_404(self, client, db_session):
        actor, project = await make_owner_project(db_session, "burndown-404")

        response = await client.get(f"/api/v2/projects/{project.slug}/burndown?sprint_id={uuid.uuid4()}", headers=actor.headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
