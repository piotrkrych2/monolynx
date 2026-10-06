"""API v2 -- Blog: 4 endpointy wolajace `services/blog.py` i `services/blog_lint.py` (MON-261, MON-262).

Routery cienkie jak toole MCP (`mcp_server.py`, sekcja "--- Blog ---"). Uprawnienia
sprawdza serwis jako pierwszy krok (`wiki:read` dla odczytow i lintu, `wiki:write` dla
`set_post_meta`), przed lookupem strony, wiec rola bez uprawnienia dostaje 403, nie 404.
Commit zapisu robi `set_post_meta`. Szczegoly wpisu i lint czytaja tresc z MinIO, wiec blad storage
wraca jako 503 problem+json, nie 500.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.api.v2.common import DEFAULT_PER_PAGE, MAX_PER_PAGE, not_found, paginate, service_error
from monolynx.api.v2.deps import get_api_user_and_project
from monolynx.api.v2.errors import ApiV2Error
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.schemas.api_v2.blog import BlogLintResponse, BlogPostDetailResponse, BlogPostMetaUpdateRequest, BlogPostResponse
from monolynx.schemas.api_v2.pagination import PaginatedResponse
from monolynx.services import blog as blog_service
from monolynx.services import blog_lint as blog_lint_service

logger = logging.getLogger("monolynx.api.v2.blog")

router = APIRouter(tags=["blog"])


@router.get("/projects/{project_slug}/blog/posts", response_model=PaginatedResponse[BlogPostResponse])
async def list_blog_posts(
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
) -> PaginatedResponse[BlogPostResponse]:
    user, project = user_and_project
    try:
        posts = await blog_service.list_posts(db, user, project, status=status)
    except ValueError as exc:
        raise service_error(exc) from exc
    return paginate([BlogPostResponse.model_validate(p) for p in posts], page, per_page)


@router.get("/projects/{project_slug}/blog/posts/{page_id}", response_model=BlogPostDetailResponse)
async def get_blog_post(
    page_id: uuid.UUID,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> BlogPostDetailResponse:
    user, project = user_and_project
    try:
        detail = await blog_service.get_post(db, user, project, page_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    except Exception as exc:
        logger.exception("Odczyt tresci wpisu bloga nie powiodl sie (page_id=%s)", page_id)
        raise ApiV2Error(status=503, title="Service Unavailable", detail="Treść wpisu jest chwilowo niedostępna") from exc
    return BlogPostDetailResponse.model_validate(detail)


@router.get("/projects/{project_slug}/blog/posts/{page_id}/lint", response_model=BlogLintResponse)
async def lint_blog_post(
    page_id: uuid.UUID,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> BlogLintResponse:
    user, project = user_and_project
    try:
        result = await blog_lint_service.lint_post_by_id(db, user, project, page_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    except Exception as exc:
        logger.exception("Lint wpisu bloga nie powiodl sie (page_id=%s)", page_id)
        raise ApiV2Error(status=503, title="Service Unavailable", detail="Treść wpisu jest chwilowo niedostępna") from exc
    return BlogLintResponse.model_validate(blog_lint_service.lint_result_dict(page_id, result))


@router.patch("/projects/{project_slug}/blog/posts/{page_id}", response_model=BlogPostResponse)
async def set_blog_post_meta(
    page_id: uuid.UUID,
    body: BlogPostMetaUpdateRequest,
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> BlogPostResponse:
    user, project = user_and_project
    try:
        post = await blog_service.set_post_meta(db, user, project, page_id, **body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise service_error(exc) from exc
    page = await db.get(WikiPage, post.page_id)
    if page is None:
        raise not_found("Strona wiki nie istnieje")
    return BlogPostResponse.model_validate(blog_service.build_post_view(post, page))
