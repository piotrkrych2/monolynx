"""Publiczny endpoint ping dla heartbeat monitoring."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.database import get_db
from monolynx.models.heartbeat import Heartbeat

router = APIRouter(prefix="/hb", tags=["heartbeat"])


_TOKEN_PATTERN = r"^hb_[A-Za-z0-9_-]{20,30}$"


async def _handle_ping(token: str, db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(Heartbeat).where(Heartbeat.token == token))
    heartbeat = result.scalar_one_or_none()
    if heartbeat is None:
        raise HTTPException(status_code=404, detail="Not found")
    heartbeat.last_ping_at = datetime.now(UTC)
    heartbeat.status = "up"
    await db.commit()
    return {"status": "ok"}


@router.get("/{token}")
async def ping_get(token: str = Path(pattern=_TOKEN_PATTERN), db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    return await _handle_ping(token, db)


@router.post("/{token}")
async def ping_post(token: str = Path(pattern=_TOKEN_PATTERN), db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    return await _handle_ping(token, db)
