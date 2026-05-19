"""Dashboard -- modul 500ki (issues, events)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import ISSUE_SORT_FIELDS, ISSUE_SORT_ORDERS, ISSUE_STATUSES
from monolynx.database import get_db
from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.services.permissions import require_permission

from .helpers import _get_user_id, render_project_page

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/{slug}/500ki/issues", response_class=HTMLResponse, response_model=None)
async def issue_list(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "500ki", "read")

    status = request.query_params.get("status", "unresolved")
    sort = request.query_params.get("sort", "last_seen")
    order = request.query_params.get("order", "desc")

    if status not in ISSUE_STATUSES and status != "all":
        status = "unresolved"
    if sort not in ISSUE_SORT_FIELDS:
        sort = "last_seen"
    if order not in ISSUE_SORT_ORDERS:
        order = "desc"

    sort_col = Issue.last_seen if sort == "last_seen" else Issue.event_count
    order_fn = desc if order == "desc" else asc

    query = select(Issue).where(Issue.project_id == project.id)
    if status != "all":
        query = query.where(Issue.status == status)
    query = query.order_by(order_fn(sort_col))

    result = await db.execute(query)
    issues = result.scalars().all()

    filters = {"status": status, "sort": sort, "order": order}

    return await render_project_page(
        request,
        "dashboard/sentry/issues.html",
        {"project": project, "issues": issues, "filters": filters, "active_module": "500ki"},
        db=db,
    )


@router.get(
    "/{slug}/500ki/issues/{issue_id}",
    response_class=HTMLResponse,
    response_model=None,
)
async def issue_detail(
    request: Request,
    slug: str,
    issue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "500ki", "read")

    result = await db.execute(
        select(Issue).options(selectinload(Issue.events), selectinload(Issue.tickets)).where(Issue.id == issue_id, Issue.project_id == project.id)
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        return HTMLResponse("Issue not found", status_code=404)

    return await render_project_page(
        request,
        "dashboard/sentry/issue_detail.html",
        {"project": project, "issue": issue, "active_module": "500ki"},
        db=db,
    )


@router.get(
    "/{slug}/500ki/setup-guide",
    response_class=HTMLResponse,
    response_model=None,
)
async def setup_guide(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    result = await db.execute(select(Project).where(Project.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    await require_permission(db, user_id, project.id, "500ki", "read")

    return await render_project_page(
        request,
        "dashboard/sentry/setup_guide.html",
        {"project": project, "active_module": "500ki"},
        db=db,
    )
