"""Serwis burndown chart -- obliczenia dla sprintow Scrum."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket


async def get_burndown_data(
    db: AsyncSession,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    if sprint_id is not None:
        result = await db.execute(select(Sprint).where(Sprint.id == sprint_id, Sprint.project_id == project_id))
        sprint = result.scalar_one_or_none()
        if sprint is None:
            raise ValueError("Sprint nie istnieje lub nie należy do projektu")
    else:
        result = await db.execute(
            select(Sprint).where(
                Sprint.project_id == project_id,
                Sprint.status == "active",
            )
        )
        sprint = result.scalar_one_or_none()
        if sprint is None:
            raise ValueError("Brak aktywnego sprintu dla projektu")

    end_date = sprint.end_date if sprint.end_date is not None else sprint.start_date + timedelta(days=14)
    start_date = sprint.start_date
    today = date.today()

    tickets_result = await db.execute(select(Ticket).where(Ticket.sprint_id == sprint.id))
    tickets = tickets_result.scalars().all()

    total_story_points = sum(t.story_points or 0 for t in tickets)

    done_tickets = [t for t in tickets if t.status == "done"]

    # Pre-compute cumulative burned points per day (single pass)
    burned_by_date: dict[date, float] = {}
    done_points = 0.0
    for t in done_tickets:
        pts = t.story_points or 0
        done_points += pts
        completed_date = t.updated_at.date() if t.updated_at else start_date
        burned_by_date[completed_date] = burned_by_date.get(completed_date, 0) + pts

    # ideal line + build lookup for on_track check
    days_total = (end_date - start_date).days
    ideal_line = []
    ideal_by_date: dict[date, float] = {}
    for i in range(days_total + 1):
        d = start_date + timedelta(days=i)
        remaining = round(total_story_points * (1 - i / days_total), 1) if days_total > 0 else 0.0
        ideal_line.append({"date": d.isoformat(), "remaining_points": remaining})
        ideal_by_date[d] = remaining

    # actual line (using cumulative sum from burned_by_date)
    actual_end = min(today, end_date)
    actual_days = (actual_end - start_date).days
    actual_line = []
    cumulative_burned = 0.0
    for i in range(actual_days + 1):
        d = start_date + timedelta(days=i)
        cumulative_burned += burned_by_date.get(d, 0)
        remaining = round(total_story_points - cumulative_burned, 1)
        actual_line.append({"date": d.isoformat(), "remaining_points": remaining})

    # current velocity
    days_elapsed = (end_date - start_date).days if sprint.status == "completed" else (today - start_date).days
    current_velocity = round(done_points / days_elapsed, 1) if days_elapsed > 0 else 0.0

    # remaining points as of today
    burned_today = sum(burned_by_date.get(d, 0) for d in burned_by_date if d <= today)
    remaining_today = total_story_points - burned_today

    # forecast completion
    if remaining_today <= 0:
        if sprint.status == "completed":
            forecast_completion: str | None = end_date.isoformat()
        else:
            forecast_completion = today.isoformat()
    elif current_velocity > 0:
        days_remaining = remaining_today / current_velocity
        forecast_date = today + timedelta(days=int(days_remaining))
        forecast_completion = forecast_date.isoformat()
    else:
        forecast_completion = None

    # on_track (O(1) lookup instead of linear search)
    if today in ideal_by_date:
        ideal_today = ideal_by_date[today]
    elif today > end_date:
        ideal_today = 0.0
    else:
        ideal_today = float(total_story_points)

    on_track = remaining_today <= ideal_today

    return {
        "sprint": {
            "name": sprint.name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "total_story_points": total_story_points,
        },
        "ideal_line": ideal_line,
        "actual_line": actual_line,
        "current_velocity": current_velocity,
        "forecast_completion": forecast_completion,
        "on_track": on_track,
    }
