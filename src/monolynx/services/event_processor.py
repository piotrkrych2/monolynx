"""EventProcessor -- przetwarzanie i grupowanie eventow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.event import Event
from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.schemas.events import EventPayload
from monolynx.services.fingerprint import compute_fingerprint


async def process_event(
    payload: EventPayload,
    project: Project,
    db: AsyncSession,
) -> uuid.UUID:
    """Przetwarza event: oblicza fingerprint, grupuje, zapisuje do DB.

    Zwraca UUID nowo utworzonego Event.
    """
    exception_dict = payload.exception.model_dump()

    fingerprint = payload.fingerprint or compute_fingerprint(exception_dict)

    now = datetime.now(UTC)
    timestamp = payload.timestamp or now

    # Szukaj istniejacego Issue z tym fingerprintem
    result = await db.execute(
        select(Issue).where(
            Issue.project_id == project.id,
            Issue.fingerprint == fingerprint,
        )
    )
    issue = result.scalar_one_or_none()

    if issue is not None:
        # Istniejacy Issue -- inkrementuj licznik
        issue.event_count += 1
        issue.last_seen = now
    else:
        # Nowy Issue
        title = f"{payload.exception.type}: {payload.exception.value}"
        if len(title) > 512:
            title = title[:509] + "..."

        culprit = None
        frames = payload.exception.stacktrace.frames
        if frames:
            last_frame = frames[-1]
            culprit = f"{last_frame.filename} in {last_frame.function}"

        issue = Issue(
            project_id=project.id,
            fingerprint=fingerprint,
            title=title,
            culprit=culprit,
            level=payload.level,
            status="unresolved",
            event_count=1,
            first_seen=now,
            last_seen=now,
        )
        db.add(issue)
        await db.flush()

    # Nowy Event
    event = Event(
        issue_id=issue.id,
        timestamp=timestamp,
        exception=exception_dict,
        request_data=payload.request.model_dump() if payload.request else None,
        environment=payload.server.model_dump() if payload.server else None,
    )
    db.add(event)
    await db.commit()

    return event.id
