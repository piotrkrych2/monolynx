"""API v2 -- Projects: 11 endpointow wolajace `services/projects.py` (MON-137).

Routery cienkie jak toole MCP (`mcp_server.py`, sekcja "--- Projekty ---") --
zadnej logiki biznesowej tutaj, tylko auth/uprawnienia -> serwis -> mapowanie
na schemat. `require_permission()` wolane jawnie tylko tam, gdzie odpowiadajacy
tool MCP (przez swoj serwis) sprawdza uprawnienie -- parytet z MCP (MON-131)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.api.v2.common import DEFAULT_PER_PAGE, MAX_PER_PAGE, not_found, paginate, require_permission, service_error
from monolynx.api.v2.deps import get_api_user, get_api_user_and_project
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.schemas.api_v2.pagination import PaginatedResponse
from monolynx.schemas.api_v2.project import (
    InviteMemberRequest,
    InviteMemberResponse,
    LabelCreateRequest,
    LabelResponse,
    LabelSummaryResponse,
    MemberResponse,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectListItemResponse,
    ProjectResponse,
    ProjectSummaryResponse,
    ProjectUpdateRequest,
)
from monolynx.services import projects as projects_service
from monolynx.services.email import send_invitation_email

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=PaginatedResponse[ProjectListItemResponse])
async def list_projects(
    user: User = Depends(get_api_user),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
) -> PaginatedResponse[ProjectListItemResponse]:
    items = await projects_service.list_projects(db, user)
    return paginate([ProjectListItemResponse.from_domain(item) for item in items], page, per_page)


@router.get("/projects/{project_slug}", response_model=ProjectDetailResponse)
async def get_project(
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project), db: AsyncSession = Depends(get_db)
) -> ProjectDetailResponse:
    user, project = user_and_project
    result = await projects_service.get_project(db, user, project)
    return ProjectDetailResponse.from_domain(result)


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreateRequest, user: User = Depends(get_api_user), db: AsyncSession = Depends(get_db)) -> ProjectResponse:
    try:
        project = await projects_service.create_project(db, user, body.name, slug=body.slug, description=body.description, code=body.code)
    except ValueError as exc:
        raise service_error(exc) from exc
    return ProjectResponse.model_validate(project)


@router.patch("/projects/{project_slug}", response_model=ProjectResponse)
async def update_project(
    body: ProjectUpdateRequest,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    user, project = user_and_project
    await require_permission(db, user, project, "settings", "write")
    try:
        updated = await projects_service.update_project(db, user, project, name=body.name, description=body.description, new_slug=body.slug)
    except ValueError as exc:
        raise service_error(exc) from exc
    return ProjectResponse.model_validate(updated)


@router.delete("/projects/{project_slug}", status_code=204, response_model=None)
async def delete_project(user_and_project: tuple[User, Project] = Depends(get_api_user_and_project), db: AsyncSession = Depends(get_db)) -> Response:
    user, project = user_and_project
    await require_permission(db, user, project, "settings", "delete")
    await projects_service.delete_project(db, user, project)
    return Response(status_code=204)


@router.post("/projects/{project_slug}/members", response_model=InviteMemberResponse, status_code=201)
async def invite_member(
    body: InviteMemberRequest,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> InviteMemberResponse:
    user, project = user_and_project
    await require_permission(db, user, project, "users", "write")
    try:
        invite = await projects_service.invite_member(db, user, project, body.email, role=body.role)
    except ValueError as exc:
        raise service_error(exc) from exc

    if invite.is_new_user:
        assert invite.invitation_token is not None
        send_invitation_email(invite.email, "", invite.invitation_token)

    return InviteMemberResponse.model_validate(invite)


@router.get("/projects/{project_slug}/members", response_model=PaginatedResponse[MemberResponse])
async def list_members(
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
) -> PaginatedResponse[MemberResponse]:
    user, project = user_and_project
    members = await projects_service.list_members(db, user, project)
    return paginate([MemberResponse.from_domain(m) for m in members], page, per_page)


@router.delete("/projects/{project_slug}/members/{member_id}", status_code=204, response_model=None)
async def remove_member(
    member_id: uuid.UUID,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user, project = user_and_project

    await require_permission(db, user, project, "users", "delete")

    members = await projects_service.list_members(db, user, project)
    target = next((m for m in members if m.user_id == member_id), None)
    if target is None:
        raise not_found(f"Czlonek '{member_id}' nie istnieje w projekcie '{project.slug}'")

    try:
        await projects_service.remove_member_by_user_id(db, user, project, target.user_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    return Response(status_code=204)


@router.get("/projects/{project_slug}/summary", response_model=ProjectSummaryResponse)
async def get_project_summary(
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project), db: AsyncSession = Depends(get_db)
) -> ProjectSummaryResponse:
    user, project = user_and_project
    summary = await projects_service.get_project_summary(db, user, project)
    return ProjectSummaryResponse.model_validate(summary)


@router.get("/projects/{project_slug}/labels", response_model=PaginatedResponse[LabelSummaryResponse])
async def list_labels(
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
) -> PaginatedResponse[LabelSummaryResponse]:
    user, project = user_and_project
    labels = await projects_service.list_labels(db, user, project)
    return paginate([LabelSummaryResponse.from_domain(lb) for lb in labels], page, per_page)


@router.post("/projects/{project_slug}/labels", response_model=LabelResponse, status_code=201)
async def create_label(
    body: LabelCreateRequest,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> LabelResponse:
    user, project = user_and_project
    try:
        label = await projects_service.create_label(db, user, project, body.name, color=body.color)
    except ValueError as exc:
        raise service_error(exc) from exc
    return LabelResponse.model_validate(label)
