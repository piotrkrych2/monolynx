"""API v2 -- Sprints (MON-142): endpointy 1:1 na toole MCP list_sprints/get_sprint/
create_sprint/update_sprint/start_sprint/complete_sprint/get_burndown.

Router cienki jak toole: wola services/tickets.py (sprinty), services/sprint.py
(start/complete, zwraca `str | None` -- niepusty string to blad) i
services/burndown.py (dane burndown). Uprawnienia: czlonkostwo przez
`get_api_user_and_project`, a uprawnienie roli (scrum:read|write|delete) sprawdzaja
funkcje services/tickets.py (list/get/create/update sprint). start/complete (scrum:write,
plus scrum:read przed zapisem, bo odpowiedz czyta sprint po commit) i burndown (scrum:read)
nie maja usera w serwisie, wiec router wola `require_permission` jawnie, przed parsowaniem
sprint_id i lookupem."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.api.v2.common import DEFAULT_PER_PAGE, MAX_PER_PAGE, paginate, require_permission, service_error
from monolynx.api.v2.deps import get_api_user_and_project
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.schemas.api_v2.pagination import PaginatedResponse
from monolynx.schemas.api_v2.sprint import (
    BurndownResponse,
    SprintCreateRequest,
    SprintListItem,
    SprintResponse,
    SprintUpdateRequest,
)
from monolynx.services import sprint as sprint_service
from monolynx.services import tickets as tickets_service
from monolynx.services.burndown import get_burndown_data

router = APIRouter(tags=["sprints"])


@router.get("/projects/{project_slug}/sprints", response_model=PaginatedResponse[SprintListItem])
async def list_sprints_endpoint(
    project_slug: str,
    status: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[SprintListItem]:
    user, project = user_project
    try:
        summaries = await tickets_service.list_sprints(db, user, project, status)
    except ValueError as exc:
        raise service_error(exc) from exc
    items = [
        SprintListItem(
            **SprintResponse.from_domain(item.sprint).model_dump(),
            ticket_count=item.ticket_count,
            story_points_total=item.story_points_total,
        )
        for item in summaries
    ]
    return paginate(items, page, per_page)


@router.get("/projects/{project_slug}/sprints/{sprint_id}", response_model=SprintResponse)
async def get_sprint_endpoint(
    project_slug: str,
    sprint_id: str,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> SprintResponse:
    user, project = user_project
    try:
        sprint = await tickets_service.get_sprint(db, user, project, sprint_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    return SprintResponse.from_domain(sprint)


@router.post("/projects/{project_slug}/sprints", response_model=SprintResponse, status_code=201)
async def create_sprint_endpoint(
    project_slug: str,
    body: SprintCreateRequest,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> SprintResponse:
    user, project = user_project
    try:
        sprint = await tickets_service.create_sprint(db, user, project, body.name, body.start_date, goal=body.goal, end_date=body.end_date)
    except ValueError as exc:
        raise service_error(exc) from exc
    return SprintResponse.from_domain(sprint)


@router.patch("/projects/{project_slug}/sprints/{sprint_id}", response_model=SprintResponse)
async def update_sprint_endpoint(
    project_slug: str,
    sprint_id: str,
    body: SprintUpdateRequest,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> SprintResponse:
    user, project = user_project
    fields = body.model_dump(exclude_unset=True)
    try:
        sprint = await tickets_service.update_sprint(db, user, project, sprint_id, **fields)
    except ValueError as exc:
        raise service_error(exc) from exc
    return SprintResponse.from_domain(sprint)


@router.post("/projects/{project_slug}/sprints/{sprint_id}/start", response_model=SprintResponse)
async def start_sprint_endpoint(
    project_slug: str,
    sprint_id: str,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> SprintResponse:
    user, project = user_project
    # read sprawdzany przed zapisem: get_sprint po commit wymaga scrum:read
    await require_permission(db, user, project, "scrum", "write")
    await require_permission(db, user, project, "scrum", "read")
    try:
        sprint_uuid = uuid.UUID(sprint_id)
    except ValueError as exc:
        raise service_error(exc) from exc

    error = await sprint_service.start_sprint(sprint_uuid, project.id, db)
    if error:
        raise service_error(ValueError(error))

    try:
        sprint = await tickets_service.get_sprint(db, user, project, sprint_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    return SprintResponse.from_domain(sprint)


@router.post("/projects/{project_slug}/sprints/{sprint_id}/complete", response_model=SprintResponse)
async def complete_sprint_endpoint(
    project_slug: str,
    sprint_id: str,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> SprintResponse:
    user, project = user_project
    # read sprawdzany przed zapisem: get_sprint po commit wymaga scrum:read
    await require_permission(db, user, project, "scrum", "write")
    await require_permission(db, user, project, "scrum", "read")
    try:
        sprint_uuid = uuid.UUID(sprint_id)
    except ValueError as exc:
        raise service_error(exc) from exc

    error = await sprint_service.complete_sprint(sprint_uuid, project.id, db)
    if error:
        raise service_error(ValueError(error))

    try:
        sprint = await tickets_service.get_sprint(db, user, project, sprint_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    return SprintResponse.from_domain(sprint)


@router.get("/projects/{project_slug}/burndown", response_model=BurndownResponse)
async def get_burndown_endpoint(
    project_slug: str,
    sprint_id: str | None = None,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> BurndownResponse:
    user, project = user_project
    await require_permission(db, user, project, "scrum", "read")
    sprint_uuid: uuid.UUID | None = None
    if sprint_id is not None:
        try:
            sprint_uuid = uuid.UUID(sprint_id)
        except ValueError as exc:
            raise service_error(exc) from exc

    try:
        data = await get_burndown_data(db, project.id, sprint_uuid)
    except ValueError as exc:
        raise service_error(exc) from exc
    return BurndownResponse.model_validate(data)
