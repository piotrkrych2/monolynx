"""API v2 -- Wiki: 8 endpointow wolajace `services/wiki.py` (MON-139) i
`services/embeddings.py::search_wiki_pages`.

Routery cienkie jak toole MCP (`mcp_server.py`, sekcja "--- Wiki ---") -- zadnej
logiki biznesowej tutaj. Uprawnienia roli (MON-241): odczyty (lista, szczegoly, config)
sprawdza serwis (`wiki:read`), a router jawnie `require_permission` dla funkcji serwisu
bez uzytkownika: create/update strony `wiki:write` (`is_public` wymaga dodatkowo uprawnienia konta `blog:write`,
MON-270: serwis rzuca `ValueError`, `service_error` daje 403), delete
`wiki:delete`, search `wiki:read`, `set_wiki_config` `settings:write`. Check idzie przed
lookupem strony, wiec rola bez uprawnienia dostaje 403, nie 404.
`get_api_user_and_project` sprawdza tylko czlonkostwo."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.api.v2.common import DEFAULT_PER_PAGE, MAX_PER_PAGE, not_found, paginate, require_permission, service_error
from monolynx.api.v2.deps import get_api_user_and_project
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.schemas.api_v2.pagination import PaginatedResponse
from monolynx.schemas.api_v2.wiki import (
    WikiConfigResponse,
    WikiConfigUpdateRequest,
    WikiConfigUpdateResponse,
    WikiPageCreateRequest,
    WikiPageDetailResponse,
    WikiPageListItemResponse,
    WikiPageResponse,
    WikiPageUpdateRequest,
    WikiSearchResultResponse,
)
from monolynx.services import wiki as wiki_service

router = APIRouter(tags=["wiki"])


@router.get("/projects/{project_slug}/wiki/pages", response_model=PaginatedResponse[WikiPageListItemResponse])
async def list_wiki_pages(
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
) -> PaginatedResponse[WikiPageListItemResponse]:
    user, project = user_and_project
    try:
        pages = await wiki_service.list_pages_flat(db, user, project)
    except ValueError as exc:
        raise service_error(exc) from exc
    return paginate([WikiPageListItemResponse.from_domain(p) for p in pages], page, per_page)


@router.get("/projects/{project_slug}/wiki/pages/{page_id}", response_model=WikiPageDetailResponse)
async def get_wiki_page(
    page_id: uuid.UUID,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> WikiPageDetailResponse:
    user, project = user_and_project
    try:
        detail = await wiki_service.get_wiki_page_detail(db, user, project, str(page_id))
    except ValueError as exc:
        raise service_error(exc) from exc
    return WikiPageDetailResponse.model_validate(detail)


@router.post("/projects/{project_slug}/wiki/pages", response_model=WikiPageResponse, status_code=201)
async def create_wiki_page(
    body: WikiPageCreateRequest,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> WikiPageResponse:
    user, project = user_and_project
    await require_permission(db, user, project, "wiki", "write")
    # Parity with the MCP tool, which rejects a blank title before calling the service (MON-131).
    if not body.title.strip():
        raise service_error(ValueError("Tytul jest wymagany"))
    try:
        page = await wiki_service.create_wiki_page(
            project_id=project.id,
            project_slug=project.slug,
            title=body.title,
            content=body.content,
            user_id=user.id,
            parent_id=body.parent_id,
            position=body.position,
            is_ai=False,
            is_public=body.is_public,
            db=db,
        )
    except ValueError as exc:
        raise service_error(exc) from exc
    return WikiPageResponse.model_validate(page)


async def _get_page_or_404(db: AsyncSession, project: Project, page_id: uuid.UUID) -> WikiPage:
    result = await db.execute(select(WikiPage).where(WikiPage.id == page_id, WikiPage.project_id == project.id))
    page = result.scalar_one_or_none()
    if page is None:
        raise not_found("Strona wiki nie istnieje")
    return page


@router.patch("/projects/{project_slug}/wiki/pages/{page_id}", response_model=WikiPageResponse)
async def update_wiki_page(
    page_id: uuid.UUID,
    body: WikiPageUpdateRequest,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> WikiPageResponse:
    user, project = user_and_project
    await require_permission(db, user, project, "wiki", "write")
    page = await _get_page_or_404(db, project, page_id)
    try:
        page = await wiki_service.update_wiki_page(
            page=page,
            project_slug=project.slug,
            title=body.title,
            content=body.content,
            position=body.position,
            user_id=user.id,
            is_ai=False,
            is_public=body.is_public,
            db=db,
        )
    except ValueError as exc:
        raise service_error(exc) from exc
    return WikiPageResponse.model_validate(page)


@router.delete("/projects/{project_slug}/wiki/pages/{page_id}", status_code=204, response_model=None)
async def delete_wiki_page(
    page_id: uuid.UUID,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user, project = user_and_project
    await require_permission(db, user, project, "wiki", "delete")
    page = await _get_page_or_404(db, project, page_id)
    await wiki_service.delete_wiki_page(page, db)
    return Response(status_code=204)


@router.get("/projects/{project_slug}/wiki/search", response_model=list[WikiSearchResultResponse])
async def search_wiki(
    query: str = Query(...),
    limit: int = Query(10),
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> list[WikiSearchResultResponse]:
    user, project = user_and_project
    await require_permission(db, user, project, "wiki", "read")

    from monolynx.services.embeddings import search_wiki_pages

    results = await search_wiki_pages(project.id, query, db, limit=limit)
    return [WikiSearchResultResponse.model_validate(r) for r in results]


@router.get("/projects/{project_slug}/wiki/config", response_model=WikiConfigResponse)
async def get_wiki_config(
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project), db: AsyncSession = Depends(get_db)
) -> WikiConfigResponse:
    user, project = user_and_project
    try:
        config = await wiki_service.get_wiki_config(db, user, project)
    except ValueError as exc:
        raise service_error(exc) from exc
    return WikiConfigResponse.model_validate(config)


@router.patch("/projects/{project_slug}/wiki/config", response_model=WikiConfigUpdateResponse)
async def set_wiki_config(
    body: WikiConfigUpdateRequest,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> WikiConfigUpdateResponse:
    user, project = user_and_project
    await require_permission(db, user, project, "settings", "write")
    try:
        proj = await wiki_service.set_wiki_config(db, user, project, body.enabled)
    except ValueError as exc:
        raise service_error(exc) from exc
    return WikiConfigUpdateResponse(wiki_llm_enabled=proj.wiki_llm_enabled)
