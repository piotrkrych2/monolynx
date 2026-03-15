"""Serwis activity log -- logowanie i odczyt historii zmian w projekcie."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.activity_log import ActivityLog


async def log_activity(
    db: AsyncSession,
    project_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: str,
    entity_title: str | None = None,
    actor_id: uuid.UUID | None = None,
    actor_type: str = "user",
    changes: dict[str, Any] | None = None,
) -> ActivityLog:
    """Zapisz wpis do activity logu."""
    entry = ActivityLog(
        project_id=project_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_title=entity_title,
        changes=changes,
    )
    db.add(entry)
    await db.flush()
    return entry


async def get_activity_log(
    db: AsyncSession,
    project_id: uuid.UUID,
    limit: int = 50,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: uuid.UUID | None = None,
    actor_type_filter: str | None = None,
) -> list[ActivityLog]:
    """Pobierz log aktywnosci z filtrami."""
    stmt = select(ActivityLog).where(ActivityLog.project_id == project_id)

    if entity_type is not None:
        stmt = stmt.where(ActivityLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(ActivityLog.entity_id == entity_id)
    if actor_id is not None:
        stmt = stmt.where(ActivityLog.actor_id == actor_id)
    if actor_type_filter is not None:
        stmt = stmt.where(ActivityLog.actor_type == actor_type_filter)

    stmt = stmt.order_by(ActivityLog.created_at.desc()).limit(limit)

    result = await db.execute(stmt)
    return list(result.scalars().all())
