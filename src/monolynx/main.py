"""Monolynx -- glowny modul FastAPI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from monolynx.config import settings

logger = logging.getLogger("monolynx")

TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logging.basicConfig(level=settings.LOG_LEVEL.upper())
    logger.info("Monolynx starting (env=%s)", settings.ENVIRONMENT)

    try:
        from monolynx.services.minio_client import ensure_bucket

        ensure_bucket()
    except Exception:
        logger.exception("Nie udalo sie zainicjalizowac MinIO bucket")

    # Neo4j graph database
    try:
        from monolynx.services.graph import init_driver, init_schema

        await init_driver()
        await init_schema()
    except Exception:
        logger.exception("Nie udalo sie zainicjalizowac Neo4j")

    checker_task = None
    if settings.ENABLE_MONITOR_LOOP:
        from monolynx.database import async_session_factory
        from monolynx.services.monitor_loop import monitor_checker_loop

        checker_task = asyncio.create_task(monitor_checker_loop(async_session_factory))
    else:
        logger.info("Monitor checker loop disabled (ENABLE_MONITOR_LOOP=false)")

    # Starlette nie wywoluje lifespanow zamontowanych sub-aplikacji,
    # wiec session_manager MCP musi byc uruchomiony tutaj recznie.
    async with mcp_server.session_manager.run():
        yield

    if checker_task is not None:
        checker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await checker_task

    # Close Neo4j
    try:
        from monolynx.services.graph import close_driver

        await close_driver()
    except Exception:
        logger.exception("Blad zamykania Neo4j")

    logger.info("Monolynx shutting down")


app = FastAPI(
    title="Monolynx",
    version="0.1.0",
    lifespan=lifespan,
)

# MCP Server mount
from monolynx.mcp_server import mcp as mcp_server  # noqa: E402

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
    from monolynx.api.events import router as events_router
    from monolynx.api.issues import router as issues_router
    from monolynx.api.oauth import router as oauth_router
    from monolynx.dashboard import router as dashboard_router

    app.include_router(oauth_router)
    app.include_router(events_router)
    app.include_router(issues_router)
    app.include_router(dashboard_router)


_register_routers()
