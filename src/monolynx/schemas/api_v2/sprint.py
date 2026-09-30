"""Schematy Pydantic API v2 dla zasobu Sprints (MON-142) i burndown -- budowane
z ORM Sprint (services/tickets.py, services/sprint.py) oraz z dict zwracanego
przez services/burndown.py::get_burndown_data()."""

from __future__ import annotations

from pydantic import BaseModel

from monolynx.models.sprint import Sprint


class SprintCreateRequest(BaseModel):
    name: str
    start_date: str
    goal: str | None = None
    end_date: str | None = None


class SprintUpdateRequest(BaseModel):
    name: str | None = None
    goal: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class SprintResponse(BaseModel):
    id: str
    name: str
    goal: str | None
    status: str
    start_date: str
    end_date: str | None
    created_at: str

    @classmethod
    def from_domain(cls, sprint: Sprint) -> SprintResponse:
        return cls(
            id=str(sprint.id),
            name=sprint.name,
            goal=sprint.goal,
            status=sprint.status,
            start_date=sprint.start_date.isoformat(),
            end_date=sprint.end_date.isoformat() if sprint.end_date else None,
            created_at=sprint.created_at.isoformat(),
        )


class SprintListItem(SprintResponse):
    ticket_count: int
    story_points_total: int


class BurndownPoint(BaseModel):
    date: str
    remaining_points: float


class BurndownSprintInfo(BaseModel):
    name: str
    start_date: str
    end_date: str
    total_story_points: int


class BurndownResponse(BaseModel):
    sprint: BurndownSprintInfo
    ideal_line: list[BurndownPoint]
    actual_line: list[BurndownPoint]
    current_velocity: float
    forecast_completion: str | None
    on_track: bool
