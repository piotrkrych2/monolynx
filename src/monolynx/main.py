"""Monolynx -- glowny modul FastAPI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
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

_mcp_http_app = mcp_server.streamable_http_app()
app.mount("/mcp", _mcp_http_app)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie=settings.SESSION_COOKIE_NAME,
    max_age=settings.SESSION_MAX_AGE,
    same_site="lax",
    https_only=settings.ENVIRONMENT == "production",
)


_templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_LANDING_I18N: dict[str, dict[str, str]] = {
    "en": {
        "title": "Monolynx — Project Platform",
        "login": "Log in",
        "hero_line1": "Your projects.",
        "hero_line2": "One platform.",
        "hero_sub": ("Error tracking, Scrum boards, uptime monitoring, wiki, and dependency graphs — all in a single, self-hosted tool."),
        "modules_title": "Everything you need",
        "modules_sub": "Five integrated modules that cover the full project lifecycle.",
        "m_500ki": "Error tracking with smart fingerprinting. Catch exceptions from your apps with a lightweight SDK.",
        "m_scrum": "Backlog, Kanban board, sprints, and story points. Everything for agile project management.",
        "m_monitoring": "URL health checks with uptime tracking, response time history, and instant alerts.",
        "m_wiki": "Markdown documentation with page hierarchy, image uploads, and AI-powered semantic search.",
        "m_connections": "Interactive dependency graph powered by Neo4j. Visualize how your code modules relate to each other.",
        "mcp_title": "AI-native with MCP",
        "mcp_desc": (
            "Monolynx speaks <mcp_link>Model Context Protocol</mcp_link>."
            " Connect Claude Desktop or any MCP client to manage tickets,"
            " search your wiki, query your dependency graph,"
            " and more — all through natural language."
        ),
        "oss_title": "Open Source",
        "oss_desc": (
            "Monolynx is free and open source. Self-host it on your own infrastructure,"
            " customize it to your needs, and keep full ownership of your data."
        ),
        "oss_link": "View on GitLab",
        "footer": "Monolynx — open-source project platform",
    },
    "pl": {
        "title": "Monolynx — Platforma projektowa",
        "login": "Zaloguj sie",
        "hero_line1": "Twoje projekty.",
        "hero_line2": "Jedna platforma.",
        "hero_sub": ("Sledzenie bledow, tablice Scrum, monitoring, wiki i grafy zaleznosci — wszystko w jednym, self-hosted narzedziu."),
        "modules_title": "Wszystko, czego potrzebujesz",
        "modules_sub": "Piec zintegrowanych modulow obejmujacych caly cykl zycia projektu.",
        "m_500ki": "Sledzenie bledow z inteligentnym fingerprintingiem. Przechwytuj wyjatki z aplikacji za pomoca lekkiego SDK.",
        "m_scrum": "Backlog, tablica Kanban, sprinty i story pointy. Wszystko do zwinnego zarzadzania projektami.",
        "m_monitoring": "Monitorowanie URL z historia uptimeu, czasami odpowiedzi i natychmiastowymi alertami.",
        "m_wiki": "Dokumentacja w Markdown z hierarchia stron, uploadem obrazow i semantycznym wyszukiwaniem AI.",
        "m_connections": "Interaktywny graf zaleznosci oparty na Neo4j. Wizualizuj powiazania miedzy modulami kodu.",
        "mcp_title": "AI-native z MCP",
        "mcp_desc": (
            "Monolynx obsluguje <mcp_link>Model Context Protocol</mcp_link>."
            " Polacz Claude Desktop lub dowolnego klienta MCP, aby zarzadzac ticketami,"
            " przeszukiwac wiki, odpytywac graf zaleznosci"
            " i wiele wiecej — wszystko w jezyku naturalnym."
        ),
        "oss_title": "Open Source",
        "oss_desc": (
            "Monolynx jest darmowy i open source. Hostuj go na wlasnej infrastrukturze,"
            " dostosuj do swoich potrzeb i zachowaj pelna kontrole nad danymi."
        ),
        "oss_link": "Zobacz na GitLab",
        "footer": "Monolynx — platforma projektowa open source",
    },
}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page(request: Request, lang: str = "en") -> HTMLResponse:
    if lang not in _LANDING_I18N:
        lang = "en"
    return _templates.TemplateResponse(request, "landing.html", {"t": _LANDING_I18N[lang], "lang": lang})


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

# MCP takze na root "/" -- Claude Desktop laczy sie na APP_URL (bez /mcp/).
# Routery FastAPI maja priorytet nad mountami, wiec /auth/*, /dashboard/*, /api/* itp.
# nie sa zasloniete. Mount "/" lapie tylko POST/GET / ktore nie pasuja do zadnego routera.
app.mount("/", _mcp_http_app)
