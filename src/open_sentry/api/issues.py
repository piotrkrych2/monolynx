"""Issues API -- PATCH /api/v1/issues/{issue_id}/status."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from open_sentry.database import get_db
from open_sentry.models.issue import Issue
from open_sentry.schemas.issues import StatusUpdate

router = APIRouter(prefix="/api/v1", tags=["issues"])

VALID_STATUSES = {"unresolved", "resolved", "ignored"}


@router.patch("/issues/{issue_id}/status")
async def update_issue_status(
    issue_id: uuid.UUID,
    body: StatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    if body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}",
        )

    issue = await db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    issue.status = body.status
    await db.commit()

    return {"status": issue.status}
