"""Ingest API -- POST /api/v1/events."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from open_sentry.database import get_db
from open_sentry.models.project import Project
from open_sentry.schemas.events import EventPayload, EventResponse
from open_sentry.services.auth import verify_api_key
from open_sentry.services.event_processor import process_event

router = APIRouter(prefix="/api/v1", tags=["ingest"])


@router.post("/events", response_model=EventResponse, status_code=202)
async def ingest_event(
    payload: EventPayload,
    project: Project = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    event_id = await process_event(payload, project, db)
    return JSONResponse(
        status_code=202,
        content={"id": str(event_id)},
    )
