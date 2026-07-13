"""Testy jednostkowe serwisu burndown -- get_burndown_data().

Uzywa realnej bazy (db_session, fixture z rollbackiem) i realnych obiektow
Sprint/Ticket -- bez mockowania ORM.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import pytest

from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.services.burndown import get_burndown_data


async def _make_sprint(
    db_session,
    project_id: uuid.UUID,
    *,
    name: str = "Sprint 1",
    start_date: date,
    end_date: date | None,
    status: str = "active",
) -> Sprint:
    sprint = Sprint(
        project_id=project_id,
        name=name,
        start_date=start_date,
        end_date=end_date,
        status=status,
    )
    db_session.add(sprint)
    await db_session.flush()
    return sprint


async def _make_ticket(
    db_session,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    *,
    number: int,
    status: str = "backlog",
    story_points: int | None = None,
    updated_at=None,
) -> Ticket:
    ticket = Ticket(
        project_id=project_id,
        number=number,
        sprint_id=sprint_id,
        title=f"Ticket {number}",
        status=status,
        story_points=story_points,
    )
    if updated_at is not None:
        ticket.updated_at = updated_at
    db_session.add(ticket)
    await db_session.flush()
    return ticket


@pytest.mark.unit
class TestBurndownNoTickets:
    async def test_sprint_without_tickets_returns_zero_totals(self, db_session, test_project):
        today = date.today()
        sprint = await _make_sprint(db_session, test_project.id, start_date=today, end_date=today + timedelta(days=10))

        result = await get_burndown_data(db_session, test_project.id, sprint.id)

        assert result["sprint"]["name"] == "Sprint 1"
        assert result["sprint"]["total_story_points"] == 0
        assert result["ideal_line"][0]["remaining_points"] == 0.0
        assert result["on_track"] is True
        # remaining_today <= 0 and sprint not completed -> forecast is today
        assert result["forecast_completion"] == today.isoformat()


@pytest.mark.unit
class TestBurndownWithTickets:
    async def test_mixed_done_and_in_progress_tickets_on_track(self, db_session, test_project):
        today = date.today()
        start_date = today - timedelta(days=5)
        end_date = today + timedelta(days=5)
        sprint = await _make_sprint(db_session, test_project.id, start_date=start_date, end_date=end_date)

        # done ticket burned early
        await _make_ticket(
            db_session,
            test_project.id,
            sprint.id,
            number=1,
            status="done",
            story_points=3,
            updated_at=start_date + timedelta(days=1),
        )
        # in progress ticket -- not counted as done
        await _make_ticket(
            db_session,
            test_project.id,
            sprint.id,
            number=2,
            status="in_progress",
            story_points=5,
        )
        # done ticket burned today
        await _make_ticket(
            db_session,
            test_project.id,
            sprint.id,
            number=3,
            status="done",
            story_points=2,
            updated_at=today,
        )

        result = await get_burndown_data(db_session, test_project.id, sprint.id)

        assert result["sprint"]["total_story_points"] == 10
        # velocity: 5 done points / 5 days elapsed
        assert result["current_velocity"] == 1.0
        # remaining today: 10 - 5 = 5, ideal at day 5 (midpoint of 10) = 5.0 -> on_track
        assert result["on_track"] is True
        # remaining_today > 0 and velocity > 0 -> forecast computed
        assert result["forecast_completion"] == end_date.isoformat()

    async def test_no_done_tickets_not_on_track(self, db_session, test_project):
        today = date.today()
        start_date = today - timedelta(days=5)
        end_date = today + timedelta(days=5)
        sprint = await _make_sprint(db_session, test_project.id, start_date=start_date, end_date=end_date)

        await _make_ticket(
            db_session,
            test_project.id,
            sprint.id,
            number=1,
            status="in_progress",
            story_points=10,
        )

        result = await get_burndown_data(db_session, test_project.id, sprint.id)

        # nothing burned, remaining_today == total (10) > ideal_today (5.0 at midpoint)
        assert result["on_track"] is False
        assert result["current_velocity"] == 0.0
        # velocity is 0 -> no forecast possible
        assert result["forecast_completion"] is None


@pytest.mark.unit
class TestBurndownEndDateFallback:
    async def test_sprint_without_end_date_defaults_to_14_days(self, db_session, test_project):
        today = date.today()
        sprint = await _make_sprint(db_session, test_project.id, start_date=today, end_date=None)

        result = await get_burndown_data(db_session, test_project.id, sprint.id)

        expected_end = today + timedelta(days=14)
        assert result["sprint"]["end_date"] == expected_end.isoformat()


@pytest.mark.unit
class TestBurndownCompletedSprint:
    async def test_completed_sprint_uses_fixed_days_elapsed_and_end_date_forecast(self, db_session, test_project):
        today = date.today()
        start_date = today - timedelta(days=10)
        end_date = today - timedelta(days=3)
        sprint = await _make_sprint(db_session, test_project.id, start_date=start_date, end_date=end_date, status="completed")

        await _make_ticket(
            db_session,
            test_project.id,
            sprint.id,
            number=1,
            status="done",
            story_points=5,
            updated_at=end_date,
        )

        result = await get_burndown_data(db_session, test_project.id, sprint.id)

        # days_elapsed fixed to (end_date - start_date), not (today - start_date)
        assert result["current_velocity"] == round(5 / 7, 1)
        # remaining_today <= 0 and sprint completed -> forecast is end_date
        assert result["forecast_completion"] == end_date.isoformat()
        # today > end_date -> ideal_today == 0.0, remaining_today == 0 -> on_track
        assert result["on_track"] is True


@pytest.mark.unit
class TestBurndownSprintLookup:
    async def test_sprint_id_none_finds_active_sprint(self, db_session, test_project):
        today = date.today()
        await _make_sprint(
            db_session,
            test_project.id,
            name="Planning Sprint",
            start_date=today,
            end_date=today + timedelta(days=10),
            status="planning",
        )
        active_sprint = await _make_sprint(
            db_session,
            test_project.id,
            name="Active Sprint",
            start_date=today,
            end_date=today + timedelta(days=10),
            status="active",
        )

        result = await get_burndown_data(db_session, test_project.id, sprint_id=None)

        assert result["sprint"]["name"] == "Active Sprint"
        assert active_sprint.status == "active"

    async def test_no_active_sprint_raises_value_error(self, db_session, test_project):
        with pytest.raises(ValueError, match="Brak aktywnego sprintu"):
            await get_burndown_data(db_session, test_project.id, sprint_id=None)

    async def test_nonexistent_sprint_id_raises_value_error(self, db_session, test_project):
        with pytest.raises(ValueError, match="Sprint nie istnieje"):
            await get_burndown_data(db_session, test_project.id, sprint_id=uuid.uuid4())


@pytest.mark.unit
class TestBurndownZeroDaysTotal:
    async def test_start_equals_end_date_gives_zero_remaining_ideal(self, db_session, test_project):
        today = date.today()
        sprint = await _make_sprint(db_session, test_project.id, start_date=today, end_date=today)

        await _make_ticket(
            db_session,
            test_project.id,
            sprint.id,
            number=1,
            status="backlog",
            story_points=8,
        )

        result = await get_burndown_data(db_session, test_project.id, sprint.id)

        # days_total == 0 -> ideal remaining always 0.0 regardless of total points
        assert len(result["ideal_line"]) == 1
        assert result["ideal_line"][0]["remaining_points"] == 0.0
        # days_elapsed == 0 -> velocity falls back to 0.0
        assert result["current_velocity"] == 0.0
