"""Open Sentry -- glowny modul FastAPI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from open_sentry.config import settings

logger = logging.getLogger("open_sentry")

TEMPLATES_DIR = Path(__file__).parent / "templates"

INTERVAL_SECONDS = {
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
}


async def _monitor_checker_loop() -> None:
    """Petla sprawdzajaca monitory w tle. Uruchamiana co 60s."""
    from open_sentry.database import async_session_factory
    from open_sentry.models.monitor import Monitor
    from open_sentry.models.monitor_check import MonitorCheck
    from open_sentry.services.monitoring import check_url

    logger.info("Monitor checker loop started")

    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.now(UTC)

            async with async_session_factory() as db:
                result = await db.execute(select(Monitor).where(Monitor.is_active.is_(True)))
                monitors = list(result.scalars().all())

                for monitor in monitors:
                    # Oblicz interwal w sekundach
                    interval_s = monitor.interval_value * INTERVAL_SECONDS.get(monitor.interval_unit, 60)
                    # Sprawdz ostatni check
                    last_check_result = await db.execute(
                        select(MonitorCheck.checked_at).where(MonitorCheck.monitor_id == monitor.id).order_by(MonitorCheck.checked_at.desc()).limit(1)
                    )
                    last_checked_at = last_check_result.scalar_one_or_none()

                    if last_checked_at is not None:
                        next_check = last_checked_at + timedelta(seconds=interval_s)
                        if now < next_check:
                            continue

                    # Czas na sprawdzenie
                    try:
                        result_data = await check_url(monitor.url)
                    except Exception:
                        logger.exception("Blad sprawdzania monitora %s", monitor.id)
                        result_data = {
                            "status_code": None,
                            "response_time_ms": None,
                            "is_success": False,
                            "error_message": "Internal checker error",
                        }

                    check = MonitorCheck(
                        monitor_id=monitor.id,
                        status_code=result_data["status_code"],
                        response_time_ms=result_data["response_time_ms"],
                        is_success=result_data["is_success"],
                        error_message=result_data.get("error_message"),
                    )
                    db.add(check)
                    await db.commit()

        except asyncio.CancelledError:
            logger.info("Monitor checker loop cancelled")
            break
        except Exception:
            logger.exception("Nieobsluzony blad w petli monitoringu")
            await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logging.basicConfig(level=settings.LOG_LEVEL.upper())
    logger.info("Open Sentry starting (env=%s)", settings.ENVIRONMENT)
    checker_task = asyncio.create_task(_monitor_checker_loop())
    yield
    checker_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await checker_task
    logger.info("Open Sentry shutting down")


app = FastAPI(
    title="Open Sentry",
    version="0.1.0",
    lifespan=lifespan,
)

# MCP Server mount
from open_sentry.mcp_server import mcp as mcp_server  # noqa: E402

app.mount("/mcp", mcp_server.streamable_http_app())

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie=settings.SESSION_COOKIE_NAME,
    max_age=settings.SESSION_MAX_AGE,
    same_site="lax",
    https_only=settings.ENVIRONMENT == "production",
)


@app.get("/api/v1/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


# Routery ladowane w osobnych modulach (api/, dashboard/)
# Importowane tutaj aby uniknac circular imports
def _register_routers() -> None:
    from open_sentry.api.events import router as events_router
    from open_sentry.api.issues import router as issues_router
    from open_sentry.dashboard import router as dashboard_router

    app.include_router(events_router)
    app.include_router(issues_router)
    app.include_router(dashboard_router)


_register_routers()
