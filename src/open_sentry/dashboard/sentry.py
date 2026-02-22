"""Dashboard -- modul 500ki (issues, events)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from open_sentry.database import get_db
from open_sentry.models.issue import Issue
from open_sentry.models.project import Project

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

    result = await db.execute(select(Issue).where(Issue.project_id == project.id).order_by(Issue.last_seen.desc()))
    issues = result.scalars().all()

    return await render_project_page(
        request,
        "dashboard/sentry/issues.html",
        {"project": project, "issues": issues, "active_module": "500ki"},
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

    result = await db.execute(select(Issue).options(selectinload(Issue.events)).where(Issue.id == issue_id, Issue.project_id == project.id))
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

    return await render_project_page(
        request,
        "dashboard/sentry/setup_guide.html",
        {"project": project, "active_module": "500ki"},
        db=db,
    )
