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
from monolynx.mcp_server import build_mcp_http_app  # noqa: E402
from monolynx.mcp_server import mcp as mcp_server  # noqa: E402

_mcp_http_app = build_mcp_http_app()
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
            "100 AI-ready tools, semantic wiki search, and Claude Code skills"
            " - Monolynx isn't just another dashboard."
            " It's a knowledge base your AI agent works with as if it were a team member."
        ),
        "modules_title": "Everything you need",
        "modules_sub": "Ten integrated modules that cover the full project lifecycle.",
        "m_500ki": "Error tracking with smart fingerprinting. Catch exceptions from your apps with a lightweight SDK.",
        "m_scrum": "Backlog, Kanban board, sprints, and story points. Everything for agile project management.",
        "m_monitoring": "URL health checks with uptime tracking, response time history, and instant alerts.",
        "m_heartbeat": "Cron job monitoring with dead man's switch. Get alerted when your scheduled tasks stop running.",
        "m_wiki": "Markdown documentation with page hierarchy, image uploads, and AI-powered semantic search.",
        "m_connections": "Interactive dependency graph powered by Neo4j. Visualize how your code modules relate to each other.",
        "m_settlements": "Cross-project billing with status workflow (draft → sent → paid), ticket freezing, and attachment management.",
        "m_reports": "Cross-project work reports with multi-select filters, date ranges, and PDF export for stakeholders.",
        "m_work_plan": "Personal cross-project scheduling on a Gantt chart and monthly calendar, independent of sprint assignment.",
        "m_pipelines": "Observability for AI agent work - pipeline, step and job hierarchy with logs, durations and upward status propagation.",
        "mcp_title": "Connect to Claude",
        "mcp_desc": (
            "Give your AI assistant direct access to Monolynx: add it to Claude.ai as a custom"
            " connector over HTTPS, or wire it into any client that speaks the"
            " <mcp_link>Model Context Protocol</mcp_link>."
            " Manage tickets, search your wiki, and query your dependency graph in natural language."
        ),
        "skills_title": "Claude Code skills - one prompt, zero setup",
        "skills_sub": (
            "The flagship AI tool install_monolynx_skills drops all 12 ticket and wiki"
            " skills into any project connected to Monolynx - no ZIPs,"
            " no manual placeholder replacement."
        ),
        "skills_prompt_label": "Just say it in Claude",
        "skills_prompt": "Install the default Monolynx skills for project acme-inc.",
        "skills_what_label": "You get",
        "skills_what_1": (
            "/monolynx:ticket-create, /monolynx:ticket-review, /monolynx:work, /monolynx:work-simple, /monolynx:sprint-end - the ticket workflow"
        ),
        "skills_what_2": (
            "/monolynx:wiki-init, /monolynx:wiki-ingest, /monolynx:wiki-lint, /monolynx:wiki-sync-merge, /monolynx:search - LLM Wiki method + RAG"
        ),
        "skills_what_3": "/monolynx:create-graph-ci-script, /monolynx:help; placeholders replaced with your project slug automatically",
        "oss_title": "Open Source",
        "oss_desc": (
            "Monolynx is free and open source. Self-host it on your own infrastructure,"
            " customize it to your needs, and keep full ownership of your data."
        ),
        "oss_link": "View on GitLab",
        "footer": "Monolynx — open-source project platform",
        "explain_cta": "Paste into AI - let it explain Monolynx",
    },
    "pl": {
        "title": "Monolynx — Platforma projektowa",
        "login": "Zaloguj się",
        "hero_line1": "Twoje projekty.",
        "hero_line2": "AI-first platforma.",
        "hero_sub": (
            "100 narzędzi AI, semantyczne wyszukiwanie w wiki i umiejętności Claude Code"
            " - Monolynx to nie kolejny dashboard."
            " To baza wiedzy, z którą Twój agent AI pracuje tak, jakby był członkiem zespołu."
        ),
        "modules_title": "Wszystko, czego potrzebujesz",
        "modules_sub": "Dziesięć zintegrowanych modułów obejmujących cały cykl życia projektu.",
        "m_500ki": "Śledzenie błędów z inteligentnym fingerprintingiem. Przechwytuj wyjątki z aplikacji za pomocą lekkiego SDK.",
        "m_scrum": "Backlog, tablica Kanban, sprinty i story pointy. Wszystko do zwinnego zarządzania projektami.",
        "m_monitoring": "Monitorowanie URL z historią uptimeu, czasami odpowiedzi i natychmiastowymi alertami.",
        "m_heartbeat": "Monitoring zadań cron z dead man's switch. Otrzymuj alerty, gdy zaplanowane zadania przestaną działać.",
        "m_wiki": "Dokumentacja w Markdown z hierarchią stron, uploadem obrazów i semantycznym wyszukiwaniem AI.",
        "m_connections": "Interaktywny graf zależności oparty na Neo4j. Wizualizuj powiązania między modułami kodu.",
        "m_settlements": (
            "Rozliczenia cross-project z workflow statusów (draft → wysłane → opłacone), zamrażaniem ticketów i zarządzaniem załącznikami."
        ),
        "m_reports": "Raporty pracy cross-project z multi-select filtrami, zakresem dat i eksportem PDF dla stakeholderów.",
        "m_work_plan": "Osobiste planowanie cross-project na wykresie Gantta i kalendarzu, niezależne od przypisania do sprintu.",
        "m_pipelines": "Obserwowalność pracy agentów AI - hierarchia pipeline, step i job z logami, czasami i propagacją statusu w górę.",
        "mcp_title": "Połącz z Claude",
        "mcp_desc": (
            "Daj swojemu asystentowi AI bezpośredni dostęp do Monolynx: dodaj go do Claude.ai"
            " jako własny konektor po HTTPS albo podłącz go do dowolnego klienta obsługującego"
            " <mcp_link>Model Context Protocol</mcp_link>."
            " Zarządzaj ticketami, przeszukuj wiki i odpytuj graf zależności w języku naturalnym."
        ),
        "skills_title": "Skille Claude Code - jeden prompt, zero konfiguracji",
        "skills_sub": (
            "Flagowe narzędzie AI install_monolynx_skills dorzuca wszystkie 12 skilli"
            " (tickety i wiki) do dowolnego projektu podłączonego do Monolynx - bez ZIP-ów"
            " i ręcznego podmieniania placeholderów."
        ),
        "skills_prompt_label": "Po prostu powiedz Claude'owi",
        "skills_prompt": "Zainstaluj domyślne skille Monolynx dla projektu acme-inc.",
        "skills_what_label": "Co dostajesz",
        "skills_what_1": (
            "/monolynx:ticket-create, /monolynx:ticket-review, /monolynx:work, /monolynx:work-simple, /monolynx:sprint-end - workflow ticketowy"
        ),
        "skills_what_2": (
            "/monolynx:wiki-init, /monolynx:wiki-ingest, /monolynx:wiki-lint, /monolynx:wiki-sync-merge, /monolynx:search - metoda LLM Wiki + RAG"
        ),
        "skills_what_3": "/monolynx:create-graph-ci-script, /monolynx:help; placeholdery automatycznie podmieniane na slug Twojego projektu",
        "oss_title": "Open Source",
        "oss_desc": (
            "Monolynx jest darmowy i open source. Hostuj go na własnej infrastrukturze,"
            " dostosuj do swoich potrzeb i zachowaj pełną kontrolę nad danymi."
        ),
        "oss_link": "Zobacz na GitLab",
        "footer": "Monolynx — platforma projektowa open source",
        "explain_cta": "Wklej do AI - wytłumaczy Ci Monolynx",
    },
}


def _landing_redirect() -> RedirectResponse:
    """Redirect to dashboard (if logged in) or login page."""
    return RedirectResponse(url="/auth/login", status_code=302)


@app.get("/llms.txt", include_in_schema=False)
async def llms_txt() -> Response:
    """Serve llms.txt at site root for LLM consumption (llmstxt.org standard)."""
    llms_path = _STATIC_DIR / "llms.txt"
    if not llms_path.is_file():
        return Response(status_code=404, content="Not Found")
    return Response(content=llms_path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


@app.get("/how-to-use-monolynx.md", include_in_schema=False)
async def how_to_use_monolynx() -> Response:
    """Serve the AI-assistant usage guide as markdown for LLM consumption."""
    guide_path = _STATIC_DIR / "how-to-use-monolynx.md"
    if not guide_path.is_file():
        return Response(status_code=404, content="Not Found")
    return _markdown_response(guide_path.read_text(encoding="utf-8"))


@app.get("/agent-explain.md", include_in_schema=False)
async def agent_explain() -> Response:
    """Serve the AI-agent explainer as markdown for LLM consumption."""
    guide_path = _STATIC_DIR / "agent-explain.md"
    if not guide_path.is_file():
        return Response(status_code=404, content="Not Found")
    return _markdown_response(guide_path.read_text(encoding="utf-8"))


@app.get("/agent-bootstrap.md", include_in_schema=False)
async def agent_bootstrap() -> Response:
    """Serve the AI-agent bootstrap guide as markdown for LLM consumption."""
    guide_path = _STATIC_DIR / "agent-bootstrap.md"
    if not guide_path.is_file():
        return Response(status_code=404, content="Not Found")
    return _markdown_response(guide_path.read_text(encoding="utf-8"))


def _markdown_response(content: str) -> Response:
    return Response(content=content, media_type="text/markdown; charset=utf-8")


def _landing_markdown(lang: str) -> str:
    t = _LANDING_I18N[lang]
    base = "https://monolynx.com"
    modules = [
        ("500ki", "500s" if lang == "en" else "500ki", t["m_500ki"]),
        ("scrum", "Scrum", t["m_scrum"]),
        ("monitoring", "Monitoring", t["m_monitoring"]),
        ("heartbeat", "Heartbeat", t["m_heartbeat"]),
        ("wiki", "Wiki", t["m_wiki"]),
        ("connections", "Connections" if lang == "en" else "Połączenia", t["m_connections"]),
        ("reports", "Reports" if lang == "en" else "Raporty", t["m_reports"]),
        ("settlements", "Settlements" if lang == "en" else "Rozliczenia", t["m_settlements"]),
        ("work_plan", "Work Plan / Gantt" if lang == "en" else "Plan pracy / Gantt", t["m_work_plan"]),
        ("pipelines", "Pipelines", t["m_pipelines"]),
    ]
    mcp_desc = t["mcp_desc"].replace("<mcp_link>", "").replace("</mcp_link>", "")
    is_pl = lang == "pl"
    lines = ["# Monolynx", "", f"> {t['hero_sub']}", "", f"## {t['modules_title']}", "", t["modules_sub"], ""]
    for slug, name, desc in modules:
        lines.append(f"- [{name}]({base}/features/{slug}.md?lang={lang}): {desc}")
    lines += ["", f"## {t['mcp_title']}", "", mcp_desc, ""]

    lines.append("### Custom Connector")
    lines.append("")
    if is_pl:
        lines += [
            "Dodaj Monolynx bezpośrednio w Claude.ai (web lub desktop) jako własny konektor po HTTPS:",
            "",
            "1. W Claude.ai otwórz Ustawienia, Konektory.",
            "2. Kliknij Dodaj własny konektor i wklej adres konektora.",
            "3. Autoryzuj połączenie tokenem Monolynx.",
            "",
            "Adres konektora: `https://your-instance/mcp` (zastąp `your-instance` adresem swojej instancji).",
            "",
        ]
    else:
        lines += [
            "Add Monolynx directly in Claude.ai (web or desktop) as a custom connector over HTTPS:",
            "",
            "1. In Claude.ai open Settings, Connectors.",
            "2. Click Add custom connector and paste the connector URL.",
            "3. Authorize the connection with your Monolynx token.",
            "",
            "Connector URL: `https://your-instance/mcp` (replace `your-instance` with your instance host).",
            "",
        ]

    lines.append("### MCP")
    lines.append("")
    lines.append(
        "Dla Claude Code lub dowolnego klienta MCP dodaj wpis do `.mcp.json`:"
        if is_pl
        else "For Claude Code or any MCP client, add an entry to `.mcp.json`:"
    )
    lines += [
        "",
        "```json",
        "{",
        '  "mcpServers": {',
        '    "monolynx": {',
        '      "type": "http",',
        '      "url": "https://your-instance/mcp",',
        '      "headers": { "Authorization": "Bearer YOUR_TOKEN" }',
        "    }",
        "  }",
        "}",
        "```",
        "",
        (
            "Serwer MCP udostępnia 100 narzędzi we wszystkich modułach (uwierzytelnianie tokenem Bearer)."
            if is_pl
            else "The MCP server exposes 100 tools across all modules (Bearer token authentication)."
        ),
        "",
    ]

    lines.append("### Plugin Claude Code" if is_pl else "### Claude Code plugin")
    lines.append("")
    if is_pl:
        lines += [
            "Zainstaluj Monolynx jako natywny plugin Claude Code (skille, agenci i zdalny serwer MCP):",
            "",
            "```bash",
            "/plugin marketplace add https://gitlab.com/piotrkrych/monolynx.git",
            "/plugin install monolynx@monolynx",
            "```",
            "",
            "W konfiguracji pluginu ustaw token (`mcp_token`, format `osk_...`), opcjonalnie `mcp_endpoint` "
            "(domyślnie `https://monolynx.com/mcp`) i `project_slug`. Po instalacji dostępne są komendy: "
            "`/monolynx:work`, `/monolynx:ticket-create`, `/monolynx:ticket-review`, `/monolynx:search`, "
            "`/monolynx:help`, `/monolynx:create-graph-ci-script`.",
            "",
        ]
    else:
        lines += [
            "Install Monolynx as a native Claude Code plugin (skills, agents, and the remote MCP server):",
            "",
            "```bash",
            "/plugin marketplace add https://gitlab.com/piotrkrych/monolynx.git",
            "/plugin install monolynx@monolynx",
            "```",
            "",
            "In the plugin config set your token (`mcp_token`, `osk_...` format), optionally `mcp_endpoint` "
            "(defaults to `https://monolynx.com/mcp`) and `project_slug`. After install the commands are: "
            "`/monolynx:work`, `/monolynx:ticket-create`, `/monolynx:ticket-review`, `/monolynx:search`, "
            "`/monolynx:help`, `/monolynx:create-graph-ci-script`.",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


@app.get("/index.md", include_in_schema=False)
async def landing_markdown(lang: str = "en") -> Response:
    if lang not in _LANDING_I18N:
        lang = "en"
    return _markdown_response(_landing_markdown(lang))


@app.get("/features/{slug}.md", include_in_schema=False)
async def feature_markdown_page(slug: str, lang: str = "en") -> Response:
    from monolynx.features import feature_markdown

    if lang not in ("en", "pl"):
        lang = "en"
    content = feature_markdown(slug, lang)
    if content is None:
        return Response(status_code=404, content="Not Found")
    return _markdown_response(content)


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
            "md_url": f"/index.md?lang={lang}",
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
            "md_url": f"/features/{slug}.md?lang={lang}",
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
