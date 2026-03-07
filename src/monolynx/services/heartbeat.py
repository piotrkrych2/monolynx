"""Serwis heartbeat -- logika statusu i CRUD."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.heartbeat import Heartbeat

logger = logging.getLogger("monolynx.heartbeat")


def get_heartbeat_status(heartbeat: Heartbeat) -> str:
    """Oblicza aktualny status heartbeatu na podstawie last_ping_at."""
    if heartbeat.last_ping_at is None:
        return "pending"
    deadline = heartbeat.period + heartbeat.grace
    now = datetime.now(UTC)
    last_ping = heartbeat.last_ping_at
    if last_ping.tzinfo is None:
        last_ping = last_ping.replace(tzinfo=UTC)
    elapsed = (now - last_ping).total_seconds()
    if elapsed <= deadline:
        return "up"
    return "down"


async def check_heartbeat_statuses(db: AsyncSession) -> None:
    """Aktualizuje status=down dla przeterminowanych heartbeatów."""
    try:
        result = await db.execute(select(Heartbeat).where(Heartbeat.last_ping_at.isnot(None), Heartbeat.status == "up"))
        heartbeats = result.scalars().all()
        now = datetime.now(UTC)
        updated = 0
        for hb in heartbeats:
            last_ping = hb.last_ping_at
            if last_ping is None:
                continue
            if last_ping.tzinfo is None:
                last_ping = last_ping.replace(tzinfo=UTC)
            elapsed = (now - last_ping).total_seconds()
            deadline = hb.period + hb.grace
            if elapsed > deadline:
                hb.status = "down"
                updated += 1
        if updated:
            await db.commit()
            logger.info("Zaktualizowano %d heartbeatów na status=down", updated)
    except Exception:
        logger.exception("Błąd podczas sprawdzania statusów heartbeatów")
        await db.rollback()


async def create_heartbeat(db: AsyncSession, project_id: uuid.UUID, data: dict[str, Any]) -> Heartbeat:
    """Tworzy nowy heartbeat dla projektu."""
    heartbeat = Heartbeat(
        project_id=project_id,
        name=data["name"],
        period=data["period"],
        grace=data.get("grace", 60),
    )
    db.add(heartbeat)
    await db.commit()
    await db.refresh(heartbeat)
    return heartbeat


async def update_heartbeat(db: AsyncSession, project_id: uuid.UUID, heartbeat_id: uuid.UUID, data: dict[str, Any]) -> Heartbeat:
    """Aktualizuje heartbeat."""
    result = await db.execute(select(Heartbeat).where(Heartbeat.id == heartbeat_id, Heartbeat.project_id == project_id))
    heartbeat = result.scalar_one()
    for field in ("name", "period", "grace"):
        if field in data:
            setattr(heartbeat, field, data[field])
    await db.commit()
    await db.refresh(heartbeat)
    return heartbeat


async def delete_heartbeat(db: AsyncSession, project_id: uuid.UUID, heartbeat_id: uuid.UUID) -> None:
    """Usuwa heartbeat."""
    result = await db.execute(select(Heartbeat).where(Heartbeat.id == heartbeat_id, Heartbeat.project_id == project_id))
    heartbeat = result.scalar_one()
    await db.delete(heartbeat)
    await db.commit()


async def get_heartbeat_by_token(db: AsyncSession, token: str) -> Heartbeat | None:
    """Zwraca heartbeat po tokenie lub None."""
    result = await db.execute(select(Heartbeat).where(Heartbeat.token == token))
    return result.scalar_one_or_none()
