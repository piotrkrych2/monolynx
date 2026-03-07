"""Petla sprawdzajaca monitory w tle -- wyodrebniona z main.py."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger("monolynx.monitor_loop")

INTERVAL_SECONDS = {
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
}

MONITOR_ADVISORY_LOCK_ID = 738_201  # arbitrary unique ID for pg_advisory_lock

HEALTHCHECK_FILE = Path("/tmp/worker-healthy")


async def _check_single_monitor(
    monitor_id: object,
    monitor_url: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sprawdz pojedynczy monitor i zapisz wynik."""
    from monolynx.models.monitor_check import MonitorCheck
    from monolynx.services.monitoring import check_url

    try:
        result_data = await check_url(monitor_url)
    except Exception:
        logger.exception("Blad sprawdzania monitora %s", monitor_id)
        result_data = {
            "status_code": None,
            "response_time_ms": None,
            "is_success": False,
            "error_message": "Internal checker error",
        }

    async with session_factory() as db:
        check = MonitorCheck(
            monitor_id=monitor_id,
            status_code=result_data["status_code"],
            response_time_ms=result_data["response_time_ms"],
            is_success=result_data["is_success"],
            error_message=result_data.get("error_message"),
        )
        db.add(check)
        await db.commit()


async def run_monitor_checks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Pojedyncza iteracja -- znajdz due monitory, sprawdz concurrently."""
    from monolynx.models.monitor import Monitor
    from monolynx.models.monitor_check import MonitorCheck

    now = datetime.now(UTC)

    async with session_factory() as db:
        result = await db.execute(select(Monitor).where(Monitor.is_active.is_(True)))
        monitors = list(result.scalars().all())

    tasks = []
    for monitor in monitors:
        interval_s = monitor.interval_value * INTERVAL_SECONDS.get(monitor.interval_unit, 60)

        async with session_factory() as db:
            last_check_result = await db.execute(
                select(MonitorCheck.checked_at).where(MonitorCheck.monitor_id == monitor.id).order_by(MonitorCheck.checked_at.desc()).limit(1)
            )
            last_checked_at = last_check_result.scalar_one_or_none()

        if last_checked_at is not None:
            next_check = last_checked_at + timedelta(seconds=interval_s)
            if now < next_check:
                continue

        tasks.append(_check_single_monitor(monitor.id, monitor.url, session_factory))

    if tasks:
        await asyncio.gather(*tasks)
        logger.info("Sprawdzono %d monitorow", len(tasks))


async def monitor_checker_loop(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    acquire_lock: bool = True,
    sleep_interval: int = 60,
    startup_delay: int = 5,
) -> None:
    """Glowna petla monitoringu. Parametry umozliwiaja reuse w testach i workerze."""
    from monolynx.config import settings
    from monolynx.services.heartbeat import check_heartbeat_statuses

    if startup_delay > 0:
        await asyncio.sleep(startup_delay)

    lock_conn = None
    lock_engine = None

    try:
        if acquire_lock:
            lock_engine = create_async_engine(settings.DATABASE_URL, echo=False)
            lock_conn = await lock_engine.connect()
            result = await lock_conn.execute(text(f"SELECT pg_try_advisory_lock({MONITOR_ADVISORY_LOCK_ID})"))
            acquired = bool(result.scalar())
            if not acquired:
                logger.info("Monitor checker loop: inny worker trzyma lock -- pomijam")
                await lock_conn.close()
                await lock_engine.dispose()
                return
            logger.info("Monitor checker loop started (advisory lock acquired)")
        else:
            logger.info("Monitor checker loop started (no lock)")

        while True:
            try:
                await asyncio.sleep(sleep_interval)
                await run_monitor_checks(session_factory)
                try:
                    async with session_factory() as db:
                        await check_heartbeat_statuses(db)
                except Exception:
                    logger.warning("Blad sprawdzania heartbeatow", exc_info=True)
                HEALTHCHECK_FILE.touch()
            except asyncio.CancelledError:
                logger.info("Monitor checker loop cancelled")
                break
            except Exception:
                logger.exception("Nieobsluzony blad w petli monitoringu")
                await asyncio.sleep(10)

    finally:
        if lock_conn is not None:
            await lock_conn.close()
        if lock_engine is not None:
            await lock_engine.dispose()
        logger.info("Monitor checker loop stopped")
