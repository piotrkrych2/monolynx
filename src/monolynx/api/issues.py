"""Issues API -- PATCH /api/v1/issues/{issue_id}/status.

Wymaga X-Monolynx-Key (issue musi nalezec do projektu klucza) albo sesji
dashboardu z uprawnieniem 500ki:write w projekcie issue.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from monolynx.constants import ISSUE_STATUSES
from monolynx.database import get_db
from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.schemas.issues import StatusUpdate
from monolynx.services.auth import get_current_user, verify_api_key_optional
from monolynx.services.permissions import check_permission

router = APIRouter(prefix="/api/v1", tags=["issues"])


async def _issue_caller(
    request: Request,
    project: Project | None = Depends(verify_api_key_optional),
    db: AsyncSession = Depends(get_db),
) -> tuple[Project | None, User | None]:
    """Poswiadczenia PATCH status: klucz ma pierwszenstwo, potem sesja; brak obu -> 401.

    Jako dependency, zeby 401 wyprzedzal 422 z walidacji sciezki i body.
    """
    if project is not None:
        return project, None
    user = await get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return None, user


@router.patch("/issues/{issue_id}/status")
async def update_issue_status(
    issue_id: uuid.UUID,
    body: StatusUpdate,
    caller: tuple[Project | None, User | None] = Depends(_issue_caller),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if body.status not in set(ISSUE_STATUSES):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Must be one of: {', '.join(ISSUE_STATUSES)}",
        )

    project, user = caller
    # Same 404 body for missing, foreign-project and unreadable issues: no existence oracle.
    not_found = HTTPException(status_code=404, detail="Issue not found")

    if project is not None:
        result = await db.execute(select(Issue).where(Issue.id == issue_id, Issue.project_id == project.id))
        issue = result.scalar_one_or_none()
        if issue is None:
            raise not_found
    else:
        assert user is not None
        issue = await db.get(Issue, issue_id)
        if issue is None or not await check_permission(db, user.id, issue.project_id, "500ki", "read"):
            raise not_found
        if not await check_permission(db, user.id, issue.project_id, "500ki", "write"):
            raise HTTPException(status_code=403, detail="Brak uprawnienia 500ki:write")

    issue.status = body.status
    await db.commit()

    return {"status": issue.status}
