"""Monolynx -- glowny modul FastAPI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import Response

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

_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

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
        "hero_line2": "AI-first platform.",
        "hero_sub": (
            "30+ MCP tools, semantic wiki search, and Claude Code skills"
            " — Monolynx isn't just another dashboard."
            " It's a knowledge base your AI agent works with as if it were a team member."
        ),
        "modules_title": "Everything you need",
        "modules_sub": "Six integrated modules that cover the full project lifecycle.",
        "m_500ki": "Error tracking with smart fingerprinting. Catch exceptions from your apps with a lightweight SDK.",
        "m_scrum": "Backlog, Kanban board, sprints, and story points. Everything for agile project management.",
        "m_monitoring": "URL health checks with uptime tracking, response time history, and instant alerts.",
        "m_heartbeat": "Cron job monitoring with dead man's switch. Get alerted when your scheduled tasks stop running.",
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
        "login": "Zaloguj się",
        "hero_line1": "Twoje projekty.",
        "hero_line2": "AI-first platforma.",
        "hero_sub": (
            "30+ narzędzi MCP, semantyczne wyszukiwanie w wiki i umiejętności Claude Code"
            " — Monolynx to nie kolejny dashboard."
            " To baza wiedzy, z którą Twój agent AI pracuje tak, jakby był członkiem zespołu."
        ),
        "modules_title": "Wszystko, czego potrzebujesz",
        "modules_sub": "Sześć zintegrowanych modułów obejmujących cały cykl życia projektu.",
        "m_500ki": "Śledzenie błędów z inteligentnym fingerprintingiem. Przechwytuj wyjątki z aplikacji za pomocą lekkiego SDK.",
        "m_scrum": "Backlog, tablica Kanban, sprinty i story pointy. Wszystko do zwinnego zarządzania projektami.",
        "m_monitoring": "Monitorowanie URL z historią uptimeu, czasami odpowiedzi i natychmiastowymi alertami.",
        "m_heartbeat": "Monitoring zadań cron z dead man's switch. Otrzymuj alerty, gdy zaplanowane zadania przestaną działać.",
        "m_wiki": "Dokumentacja w Markdown z hierarchią stron, uploadem obrazów i semantycznym wyszukiwaniem AI.",
        "m_connections": "Interaktywny graf zależności oparty na Neo4j. Wizualizuj powiązania między modułami kodu.",
        "mcp_title": "AI-native z MCP",
        "mcp_desc": (
            "Monolynx obsługuje <mcp_link>Model Context Protocol</mcp_link>."
            " Połącz Claude Desktop lub dowolnego klienta MCP, aby zarządzać ticketami,"
            " przeszukiwać wiki, odpytywać graf zależności"
            " i wiele więcej — wszystko w języku naturalnym."
        ),
        "oss_title": "Open Source",
        "oss_desc": (
            "Monolynx jest darmowy i open source. Hostuj go na własnej infrastrukturze,"
            " dostosuj do swoich potrzeb i zachowaj pełną kontrolę nad danymi."
        ),
        "oss_link": "Zobacz na GitLab",
        "footer": "Monolynx — platforma projektowa open source",
    },
}


def _landing_redirect() -> RedirectResponse:
    """Redirect to dashboard (if logged in) or login page."""
    return RedirectResponse(url="/auth/login", status_code=302)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page(request: Request, lang: str = "en") -> Response:
    if settings.SKIP_LANDING_PAGE:
        return _landing_redirect()
    if lang not in _LANDING_I18N:
        lang = "en"
    return _templates.TemplateResponse(
        request,
        "landing.html",
        {
            "t": _LANDING_I18N[lang],
            "lang": lang,
            "active_page": "home",
            "lang_switch_url": "/?lang=pl",
            "lang_switch_url_en": "/?lang=en",
            "app_url": settings.APP_URL,
        },
    )


@app.get("/features/{slug}", response_class=HTMLResponse, include_in_schema=False)
async def feature_page(request: Request, slug: str, lang: str = "en") -> Response:
    if settings.SKIP_LANDING_PAGE:
        return _landing_redirect()

    from monolynx.features import get_feature_content

    if lang not in ("en", "pl"):
        lang = "en"
    content = get_feature_content(slug, lang)
    if content is None:
        return HTMLResponse(status_code=404, content="Not Found")
    return _templates.TemplateResponse(
        request,
        "features/feature.html",
        {
            "f": content,
            "lang": lang,
            "active_page": "feature",
            "lang_switch_url": f"/features/{slug}?lang=pl",
            "lang_switch_url_en": f"/features/{slug}?lang=en",
            "app_url": settings.APP_URL,
        },
    )


@app.get("/contact", response_class=HTMLResponse, include_in_schema=False)
async def contact_page(request: Request, lang: str = "en") -> Response:
    if settings.SKIP_LANDING_PAGE:
        return _landing_redirect()
    if lang not in ("en", "pl"):
        lang = "en"
    return _templates.TemplateResponse(
        request,
        "contact.html",
        {
            "lang": lang,
            "active_page": "contact",
            "lang_switch_url": "/contact?lang=pl",
            "lang_switch_url_en": "/contact?lang=en",
            "app_url": settings.APP_URL,
        },
    )


@app.get("/api/v1/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


# Routery ladowane w osobnych modulach (api/, dashboard/)
# Importowane tutaj aby uniknac circular imports
def _register_routers() -> None:
    from monolynx.api.events import router as events_router
    from monolynx.api.heartbeat import router as heartbeat_router
    from monolynx.api.issues import router as issues_router
    from monolynx.api.oauth import router as oauth_router
    from monolynx.dashboard import router as dashboard_router

    app.include_router(oauth_router)
    app.include_router(events_router)
    app.include_router(issues_router)
    app.include_router(heartbeat_router)
    app.include_router(dashboard_router)


_register_routers()

# MCP takze na root "/" -- Claude Desktop laczy sie na APP_URL (bez /mcp/).
# Routery FastAPI maja priorytet nad mountami, wiec /auth/*, /dashboard/*, /api/* itp.
# nie sa zasloniete. Mount "/" lapie tylko POST/GET / ktore nie pasuja do zadnego routera.
app.mount("/", _mcp_http_app)
