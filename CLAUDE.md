# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is this project?

Monolynx is a multi-module project platform. It started as a minimalist error-tracking system (500ki module — named after HTTP 500 errors) and now includes a Scrum module (backlog, Kanban board, sprints, story points), a Monitoring module (URL health checks with uptime tracking), a Wiki module (markdown pages with semantic RAG search via pgvector, plus an opt-in "LLM Wiki" method with backlinks, system pages, and INGEST/QUERY/LINT operations), a Connections module (code dependency graph visualization using Neo4j), a Work Plan / Gantt module (personal per-user cross-project scheduling with Gantt and calendar views), and a Pipelines module (observability over AI agent work, modeled on GitLab CI/CD; pipeline -> step -> job hierarchy where job logs are stored as wiki pages). The architecture supports adding new modules via the sidebar navigation.

## Commands

All commands run inside Docker. Never run Python commands locally — always use `docker compose exec app <command>`.

```bash
# Development environment (Docker Compose: PostgreSQL + FastAPI with hot reload)
make dev                              # Start dev env (port 8000, configurable via APP_PORT)
make down                             # Stop dev env (app + worker)
make worker                           # Start monitor worker separately (dev)
make logs                             # Tail app logs
make setup                            # Configure local dev environment
make help                             # Show available commands

# Code quality
make lint                             # ruff check --fix + ruff format + mypy (auto-fixes!)
make fmt                              # ruff check --fix + ruff format (same as lint, without mypy)

# Testing (inside Docker)
make test                             # All tests with coverage
docker compose exec app python -m pytest tests/unit/ -v                 # Unit tests only
docker compose exec app python -m pytest tests/integration/ -v          # Integration tests
docker compose exec app python -m pytest tests/unit/test_fingerprint.py::TestFingerprintGeneration::test_same_exception_same_location_same_fingerprint -v  # Single test

# Database migrations
make migrate                          # Run pending migrations
make migration msg="description"      # Generate new migration

# Admin
make createsuperuser                  # Create admin user (interactive prompt)

# Wiki RAG
make backfill-embeddings              # Generate embeddings for existing wiki pages
make backfill-backlinks               # Generate backlinks for existing wiki pages (LLM Wiki)

# Build
make build                            # Build production Docker image
```

**Important**: When generating Alembic migrations with autogenerate, always review the output — it may include tables from a previous migration if the DB was empty at generation time. Each migration must only contain its own new/changed tables.

## Architecture

Two separate packages in one repo:

**Backend** (`src/monolynx/`) — FastAPI async server:
- `main.py` registers routers lazily via `_register_routers()` to avoid circular imports; lifespan optionally starts monitor checker loop (controlled by `ENABLE_MONITOR_LOOP`, default true for dev, false in prod)
- `config.py` uses pydantic-settings, reads from env vars / `.env` file (see `.env.example`)
- `database.py` provides async SQLAlchemy session via `get_db()` FastAPI dependency
- `constants.py` — shared constants for Scrum (ticket statuses, priorities, sprint statuses, member roles, label mappings), Monitoring (interval units, Polish labels), Time Tracking (entry statuses, report defaults), and Graph (node types, edge types, Polish labels)

**Dashboard module system** (`dashboard/`):
- `dashboard/__init__.py` — combines all sub-routers into one `router`; ordering matters: static routes (users, settings, profile) before dynamic `{slug}` routes to avoid slug collision
- `dashboard/helpers.py` — shared `_get_user_id()`, `SLUG_PATTERN`, `templates` instance, `flash()` helper for session-based flash messages
- `dashboard/auth.py` — login/logout (`/auth/*`), invitation acceptance (`/auth/accept-invite/{token}`)
- `dashboard/projects.py` — project list, create (`/dashboard/`, `/dashboard/create-project`)
- `dashboard/profile.py` — user API token management for MCP access (`/dashboard/profile/*`)
- `dashboard/users.py` — user management, superuser-only (`/dashboard/users/*`); invitation system with token generation and email
- `dashboard/sentry.py` — error tracking module "500ki": issues list, issue detail, SDK setup guide (`/dashboard/{slug}/500ki/*`)
- `dashboard/scrum.py` — Scrum module: backlog (with pagination + filtering), Kanban board, ticket CRUD with comments, sprints with status filtering (`/dashboard/{slug}/scrum/*`)
- `dashboard/monitoring.py` — URL monitoring module: monitor CRUD, check history with pagination, toggle on/off (`/dashboard/{slug}/monitoring/*`); includes SSRF protection (blocks localhost, private IPs), limit 20 monitors per project
- `dashboard/wiki.py` — Wiki module: page CRUD, tree hierarchy, markdown rendering, image upload to MinIO, semantic search via pgvector (`/dashboard/{slug}/wiki/*`); page detail shows an "incoming links" (backlinks) panel, gated on `wiki_llm_enabled`
- `dashboard/connections.py` — Connections module: graph visualization with Cytoscape.js, node/edge CRUD forms, graph API endpoint (`/dashboard/{slug}/connections/*`); graceful degradation when Neo4j unavailable
- `dashboard/settings.py` — project settings, member management (`/dashboard/{slug}/settings`)
- `dashboard/reports.py` — global cross-project work reports with multi-select filtering (project, user, sprint), date range, and PDF export via weasyprint (`/dashboard/reports`)
- `dashboard/work_plan.py`: Work Plan module (prefix `/dashboard/plan`): personal per-user cross-project scheduling via junction model (`User x Ticket x Date`), Gantt view (frappe-gantt) and monthly calendar, REST endpoints for entries CRUD plus JSON data and ticket autocomplete; independent of sprint assignment
- `dashboard/pipelines.py` - Pipelines module: pipeline list (with status/type filtering + pagination), pipeline detail (step columns research/coding/wrap-up with job cards), job detail (markdown log rendered from wiki), JSON API endpoints and HTMX partials for 15s live polling (`/dashboard/{slug}/pipelines/*`); session auth, read-only UI (pipelines are created by skills via MCP, not from the dashboard)

**Models** (`models/`) — 26 SQLAlchemy models: Project, Issue, Event, User, UserApiToken, ProjectMember, Sprint, Ticket, TicketComment, TicketAttachment, Monitor, MonitorCheck, TimeTrackingEntry, WorkPlanEntry, WikiPage, WikiEmbedding, WikiAttachment, WikiFile, WikiBacklink, ActivityLog, Heartbeat, Label, TicketLabel, Pipeline, PipelineStep, PipelineJob + OAuth models (OAuthClient, OAuthAuthorizationCode, OAuthAccessToken, OAuthRefreshToken) + Base

**Services**:
- `services/fingerprint.py` — SHA256 of exception type + app-frame filenames:functions
- `services/event_processor.py` — finds-or-creates Issue by fingerprint, increments event_count
- `services/auth.py` — API key validation with in-memory cache (TTL 60s), bcrypt passwords, `get_current_user()` helper for session-based auth; header `X-Monolynx-Key`
- `services/email.py` — SMTP email delivery via `ThreadPoolExecutor(max_workers=1)`; never crashes application; logs warning if SMTP not configured
- `services/sprint.py` — sprint lifecycle (start checks no other active sprint; complete moves non-done tickets to backlog)
- `services/monitoring.py` — async `check_url()` using `ThreadPoolExecutor` with configurable timeout
- `services/monitor_loop.py` — extracted monitor checker loop with concurrent checks (`asyncio.gather`), proper advisory lock via dedicated connection, reusable by both `main.py` lifespan and standalone `worker.py`
- `services/mcp_auth.py` — MCP token generation (`osk_<random>` prefix), SHA256 hashing, verification with `last_used_at` tracking
- `services/sidebar.py` — `SidebarBadges` dataclass providing issue counts, failing monitors, 24h uptime percentage for sidebar indicators
- `services/time_tracking.py` — time tracking CRUD and aggregation for work reports
- `services/ticket_numbering.py` — auto-incrementing ticket numbers per project
- `services/wiki.py` — Wiki CRUD, markdown rendering (`render_markdown_html()`), page tree, breadcrumbs; content stored in MinIO, metadata in DB. LLM Wiki additions: backlink parser (`extract_wiki_links`, `sync_backlinks`, `get_backlinks`, `get_outlinks`) and system-page helpers (`ensure_system_page`, `regenerate_index`, `append_log`); `RESERVED_SLUGS` constant guards the reserved system slugs (`wiki-index`, `wiki-log`, `wiki-schema`)
- `services/wiki_lint.py` - LLM Wiki audit: detects orphans, dead_links, contradictions, gaps
- `services/wiki_bootstrap.py` - `bootstrap_wiki_llm` idempotent setup: creates the system pages and seeds the default schema page
- `services/wiki_templates.py` - `DEFAULT_WIKI_SCHEMA`: the editable LLM Wiki method "rulebook" used to seed `wiki-schema`
- `services/embeddings.py` — RAG search: text chunking via tiktoken, OpenAI embeddings (`text-embedding-3-small`), pgvector cosine similarity search; ThreadPoolExecutor for async; graceful degradation when `OPENAI_API_KEY` not set
- `services/minio_client.py` — MinIO object storage for wiki markdown files and attachments
- `services/graph.py` — Neo4j graph database: async driver singleton, CRUD for nodes/edges, query operations (get_graph, find_path, get_neighbors, get_stats); graceful degradation pattern (like embeddings.py) — `is_enabled()`, try/except, None fallback when `ENABLE_GRAPH_DB=false`
- `services/work_plan.py`: Work Plan scheduling: `schedule()`, `update()` (uses `_UNSET` sentinel for partial PATCH of `notes`), `unschedule()`, `list_for_user_range()` (max 90 days, eager loads ticket+project), `today_for_user()`, `schedule_for_ticket()`; validates project membership and entry ownership, handles `IntegrityError` from the unique constraint
- `services/pipelines.py` - Pipelines lifecycle: `create_pipeline()` (seeds the 3 `ticket_work` steps), `create_job()`, `update_job_by_id()` (status transitions set timestamps and propagate up: job -> step via `_update_step_status` -> pipeline), `finish_pipeline()` (aggregates final status from steps when not given), `list_pipelines()`, `get_pipeline()` (full tree), `is_stale()` (running + no update >6h, computed at read time, no cron). Wiki log integration: `ensure_pipeline_logs_parent()` (idempotent `pipeline-logi` parent page, excluded from embeddings), `append_job_log()` (creates/appends the job's wiki log page, links `wiki_page_id`), `maybe_log_pipeline_to_wiki_log()` (best-effort `wiki-log` entry when LLM Wiki enabled). Commits in the service layer

**Worker** (`worker.py`):
- Standalone entry point (`python -m monolynx.worker`) — runs monitor checker loop without web server
- Graceful shutdown via `SIGTERM`/`SIGINT`; healthcheck via `/tmp/worker-healthy` file touch
- In production: separate Docker service; in dev: optional via `make worker` or `--profile worker`

**MCP Server** (`mcp_server.py`):
- FastMCP-based server mounted at `/mcp` in the main app
- 116 tools across all modules: projects, 500ki issues, monitoring, Scrum (tickets, sprints, board, comments), Wiki (CRUD, semantic search), Wiki LLM method (`get_wiki_config`, `set_wiki_config`, `bootstrap_wiki_llm`, `lint_wiki`, `get_wiki_backlinks`, `regenerate_wiki_index`, `append_wiki_log`), Graph (node/edge CRUD, bulk operations, query, path finding, stats), Work Plan (`schedule_ticket`, `update_work_plan_entry`, `delete_work_plan_entry`, `list_work_plan`, `get_today_tasks`, `get_ticket_schedule`), Pipelines (`create_pipeline`, `create_pipeline_job`, `update_pipeline_job`, `append_job_log`, `finish_pipeline`, `list_pipelines`, `get_pipeline`, `get_pipeline_job_log`, `clean_pipeline_logs`), project summary
- Bearer token auth via `Authorization` header (tokens managed in `/dashboard/profile/tokens`)
- `.mcp.json` at project root configures Claude Code connection (env var `MONOLYNX_MCP_TOKEN`)
- `install_monolynx_skills` tool serves skill copies from `static/skills/` for manual install into a project's `.claude/skills/` (used by claude.ai web and environments without plugin support)

**Claude Code plugin** (`plugin/`):
- Bundles skills (`/monolynx:*` commands), 7 role agents, and remote MCP access into one installable plugin; marketplace manifest at `.claude-plugin/marketplace.json` (root), plugin manifest at `plugin/.claude-plugin/plugin.json`
- `userConfig`: `mcp_token` (sensitive, keychain), `mcp_endpoint` (default `https://monolynx.com/mcp`), `project_slug` (optional fallback)
- Skill project slug resolution order: `MONOLYNX_PROJECT_SLUG` from project `.env` → `user_config.project_slug` → `"monolynx"`; works cross-project
- Preferred path for Claude Code CLI users; `install_monolynx_skills` remains the manual/fallback path. Plugin only declares access to the existing MCP server; `mcp_server.py` is unchanged. See `plugin/README.md`
- LLM Wiki skills added in plugin 1.1.1: `wiki-init` (runs `bootstrap_wiki_llm`), `wiki-ingest` (the INGEST workflow), `wiki-lint` (the audit workflow); the existing `search` skill is extended with QUERY (writes the answer back into the wiki)

**Template layout system**:
- `layouts/base.html` — base layout (login, project list)
- `layouts/base.html` uses Tailwind CDN with typography plugin (`?plugins=typography`) for markdown `prose` styling
- `layouts/project.html` — extends base, adds sidebar with modules (500ki, Scrum, Monitoring, Wiki, Połączenia, Ustawienia); uses `active_module` context variable for highlighting
- Module templates extend `project.html` and use `{% block module_content %}`
- `dashboard/scrum/_nav.html` — shared partial with 4 always-visible buttons (Backlog, Tablica, Sprinty, Nowy ticket), included in all Scrum pages

**SDK** (`sdk/src/monolynx_sdk/`) — standalone Django middleware package:
- Zero external dependencies (stdlib only)
- Rule: SDK must NEVER crash the host application — every public function wrapped in try/except
- `transport.py` sends events via `ThreadPoolExecutor(max_workers=2)` using `urllib.request`
- Django settings: `MONOLYNX_DSN` or `MONOLYNX_URL` + `MONOLYNX_API_KEY`

**Schemas** (`schemas/`) — Pydantic models for validation: `events.py`, `issues.py`, `scrum.py`, `time_tracking.py` (includes `WorkReportResult` for aggregated reports), `graph.py` (GraphNodeCreate/Update/Response, GraphEdgeCreate/Response, GraphSearchResult), `wiki.py` (`WikiBacklinkResponse`), `pipelines.py` (list-item and full-tree response builders for the live JSON API: `build_list_item`, `build_pipeline_response`)

**Data flow**: Django error → SDK middleware `process_exception()` → background thread POST → FastAPI ingests → fingerprint → find/create Issue → store Event (JSONB)

## URL structure

```
/auth/login, /auth/logout
/auth/accept-invite/{token}                    — set password from invitation
/dashboard/                                    — project list
/dashboard/create-project                      — new project form
/dashboard/profile/tokens                      — user API tokens list
/dashboard/profile/tokens/create               — generate new token (POST)
/dashboard/profile/tokens/{id}/revoke          — revoke token (POST)
/dashboard/profile/mcp-guide                   — MCP setup instructions
/dashboard/users                               — user list (superuser only)
/dashboard/users/create                        — invite new user (superuser only)
/dashboard/users/{id}/resend-invite            — resend invitation email (POST)
/dashboard/{slug}/500ki/issues                 — error issue list
/dashboard/{slug}/500ki/issues/{id}            — error issue detail
/dashboard/{slug}/500ki/setup-guide            — SDK installation instructions
/dashboard/{slug}/scrum/backlog                — ticket list
/dashboard/{slug}/scrum/board                  — Kanban board (active sprint)
/dashboard/{slug}/scrum/tickets/create         — new ticket
/dashboard/{slug}/scrum/tickets/{id}           — ticket detail
/dashboard/{slug}/scrum/tickets/{id}/edit      — edit ticket
/dashboard/{slug}/scrum/tickets/{id}/delete    — delete ticket (POST)
/dashboard/{slug}/scrum/tickets/{id}/status    — HTMX status update (PATCH)
/dashboard/{slug}/scrum/tickets/{id}/comments  — add comment (POST)
/dashboard/{slug}/scrum/sprints                — sprint list + create form
/dashboard/{slug}/scrum/sprints/{id}/start     — start sprint (POST)
/dashboard/{slug}/scrum/sprints/{id}/complete  — complete sprint (POST)
/dashboard/{slug}/monitoring/                   — monitor list
/dashboard/{slug}/monitoring/create             — create monitor form
/dashboard/{slug}/monitoring/{id}               — monitor detail with check history + pagination
/dashboard/{slug}/monitoring/{id}/toggle        — enable/disable monitor (POST)
/dashboard/{slug}/monitoring/{id}/delete        — delete monitor (POST)
/dashboard/{slug}/settings                     — project settings + members
/dashboard/{slug}/settings/delete              — soft delete project (POST)
/dashboard/{slug}/settings/members/add         — add member (POST)
/dashboard/{slug}/settings/members/{id}/remove — remove member (POST)
/dashboard/{slug}/settings/members/{id}/role   — change role (POST)
/api/v1/events                                 — ingest events (POST, API key auth)
/api/v1/issues/{id}/status                     — update issue status (PATCH)
/api/v1/health                                 — health check
/dashboard/{slug}/connections/                   — graph visualization (Cytoscape.js)
/dashboard/{slug}/connections/nodes              — node list with type/search filtering
/dashboard/{slug}/connections/nodes/create       — create node form (GET) + create (POST)
/dashboard/{slug}/connections/nodes/{id}/delete  — delete node (POST)
/dashboard/{slug}/connections/edges/create       — create edge form (GET) + create (POST)
/dashboard/{slug}/connections/api/graph          — graph data JSON API (for Cytoscape.js)
/dashboard/{slug}/wiki/                         — wiki page tree
/dashboard/{slug}/wiki/search?q=               — semantic wiki search (RAG)
/dashboard/{slug}/wiki/pages/create            — new wiki page
/dashboard/{slug}/wiki/pages/{id}              — wiki page detail (rendered markdown)
/dashboard/{slug}/wiki/pages/{id}/edit         — edit wiki page (EasyMDE editor)
/dashboard/{slug}/wiki/pages/{id}/create       — new child page
/dashboard/{slug}/wiki/pages/{id}/delete       — delete page + children (POST)
/dashboard/{slug}/wiki/upload                  — image upload for EasyMDE (POST)
/dashboard/{slug}/wiki/attachments/{filename}  — serve wiki attachments
/dashboard/reports                              — global work reports (cross-project)
/dashboard/reports/pdf                          — PDF export of work reports
/dashboard/plan/                                : Work Plan view (Gantt or calendar mode)
/dashboard/plan/api/data                        : JSON entries in date range (GET, max 90 days)
/dashboard/plan/api/tickets/search              : ticket autocomplete (GET, limit 20)
/dashboard/plan/entries                         : create entry (POST)
/dashboard/plan/entries/{entry_id}              : update entry (PATCH)
/dashboard/plan/entries/{entry_id}              : delete entry (DELETE, owner only)
/dashboard/{slug}/pipelines/                     - pipeline list (HTML, HTMX polling)
/dashboard/{slug}/pipelines/api/list             - pipeline list JSON (status/type filter, page)
/dashboard/{slug}/pipelines/api/{pipeline_id}    - full pipeline tree JSON (steps + jobs)
/dashboard/{slug}/pipelines/partial/list         - HTML partial of list rows (15s polling)
/dashboard/{slug}/pipelines/{pipeline_id}        - pipeline detail (step columns + job cards)
/dashboard/{slug}/pipelines/{pipeline_id}/partial/tree  - HTML partial of step/job tree (15s polling)
/dashboard/{slug}/pipelines/{pipeline_id}/jobs/{job_id} - job detail (metadata + markdown log from wiki)
/mcp                                           — MCP server (Bearer token auth)
```

## Key technical decisions

- All DB columns storing error data use PostgreSQL JSONB (exception, request_data, environment)
- Issue grouping: `UniqueConstraint("project_id", "fingerprint")` — fingerprint ignores line numbers for stability
- `event_count` is denormalized on Issue to avoid COUNT(*) queries
- UUID primary keys everywhere (no auto-increment)
- Session middleware from Starlette (cookie-based, signed with SECRET_KEY)
- Alembic configured for async via `asyncio.run()` in `env.py`
- Slug validation: `^[a-z0-9]+(?:-[a-z0-9]+)*$`
- Project deletion is soft delete (`is_active = False`), filtered in all dashboard queries
- After `db.rollback()` in views, always re-query objects before passing them to Jinja2 templates (avoids MissingGreenlet from lazy loading in sync rendering)
- User invitation flow: superuser creates user → `invitation_token` (UUID4) + 7-day expiry → optional email with link → user sets password via `/auth/accept-invite/{token}` → token cleared
- `is_superuser` flag stored in session at login for navbar visibility (`request.session.get('is_superuser')` in templates); users with `password_hash=None` cannot log in
- SMTP configuration optional (`SMTP_HOST=""` disables email); `APP_URL` used for building invitation links
- Pagination pattern: query param `page` (int, default=1), fixed `per_page`; count total with `func.count()`, then LIMIT/OFFSET; pass `page`, `total_pages`, `has_next`, `has_prev` to template
- Lists default to hiding completed/closed items (completed sprints, tickets from completed sprints); toggle via query params (`status=all`, `show_completed_sprints=1`)
- Flash messages via `flash(request, message, type)` stored in `request.session["_flash_messages"]`
- MCP tokens use `osk_` prefix with SHA256 hash stored in DB; raw token shown only once at creation
- Database name is `open_sentry` (historical, kept for backwards compatibility)
- Time tracking entries have a status workflow: draft → submitted → approved/rejected
- PDF reports generated server-side with weasyprint
- Wiki content stored in MinIO (markdown files), metadata in PostgreSQL; `minio_path` column links to object storage
- Wiki RAG search uses pgvector extension (HNSW index, cosine similarity); chunked embeddings (~500 tokens per chunk with overlap) via OpenAI `text-embedding-3-small`; `OPENAI_API_KEY=""` disables embeddings gracefully
- LLM Wiki method (Karpathy-style): opt-in per project via `Project.wiki_llm_enabled` (bool, default False); gated like `embeddings.is_enabled()` - when OFF the tools/skills return a clear no-op/ValueError. Treats the wiki as a growing artifact (vs RAG from scratch) curated through INGEST/QUERY/LINT operations. Reserved system-page slugs `wiki-index`, `wiki-log`, `wiki-schema` (prefix `wiki-`, since `SLUG_PATTERN` forbids underscores); the schema is itself an editable page seeded from `DEFAULT_WIKI_SCHEMA`. Backlinks are stored in PostgreSQL via the `WikiBacklink` model (NOT in Neo4j - Neo4j stays exclusively for the Connections/code module); backfill via `make backfill-backlinks`. Contradictions are flagged inline with the marker `> **Sprzeczność [date]:**`
- Markdown rendering shared via `render_markdown_html()` from `services/wiki.py` — used in wiki pages, ticket descriptions, and comments; frontend uses Tailwind `prose prose-invert` classes
- EasyMDE (WYSIWYG markdown editor) used in wiki page forms and ticket create/edit forms; dark theme via inline CSS overrides
- Docker uses `pgvector/pgvector:pg16` image for PostgreSQL with vector extension support
- Neo4j graph database for Connections module; node types: File, Class, Method, Function, Const, Module; edge types: CONTAINS, CALLS, IMPORTS, INHERITS, USES, IMPLEMENTS; data isolated per project via `project_id` property; graceful degradation when `ENABLE_GRAPH_DB=false`
- Cytoscape.js (CDN v3.30.4) for interactive graph visualization; force-directed layout (cose); node/edge coloring by type; filtering via checkboxes; side panel with node details on click
- Work Plan uses a junction model `WorkPlanEntry` (`user_id`, `ticket_id`, `scheduled_date`) with `UniqueConstraint(user_id, ticket_id, scheduled_date)`; enables per-user cross-project scheduling independent of sprint assignment; the cross-project list returns only entries owned by the current user from projects where they have membership (no cross-project data leak); frappe-gantt renders the Gantt view, calendar mode is a custom monthly grid
- Pipelines model AI agent work as observability (not execution): hierarchy `Pipeline -> PipelineStep -> PipelineJob` in PostgreSQL (not Neo4j). `pipeline_type` is `ticket_work` (the only type implemented; steps `research`, `coding`, `wrap-up`) or `sprint_close` (reserved in the model/enum, steps and skill deferred to a future sprint so no migration is needed later). Durations are not stored - computed on the fly (`finished_at - started_at`, or `now() - started_at` for running, with a JS timer ticking between 15s polls). Status propagates upward in the service: a failed job -> failed step -> failed pipeline. `is_stale()` flags pipelines stuck in `running` >6h, computed at read time (no cron)
- Pipeline job logs are stored as wiki pages under the `pipeline-logi` parent (content in MinIO, metadata in PostgreSQL), linked via `PipelineJob.wiki_page_id`. These pages set `WikiPage.exclude_from_embeddings=True` so the high volume of agent logs (roughly 6-8 pages per ticket) does not pollute RAG `search_wiki` results. When LLM Wiki is enabled, finishing a pipeline appends a `wiki-log` entry. Skills report to pipelines best-effort: a pipeline MCP error never fails the ticket work (pipelines are observability, not a gate). Every pipeline write MCP tool must `await db.commit()` (MON-71 regression)
- Landing page content lives in `src/monolynx/features.py` (Python builders `_feature_<module>(lang)` returning `features[]`, `steps[]`, `mcp_tools[]`, `tech_details[]` per module, PL + EN), rendered to `/features/<slug>` by `main.py`; the template only renders. Any new MCP tool / module / user-visible feature must be added to `features.py` in BOTH languages (and the `ai_intro` "N MCP tools" counter kept in sync) as part of the same change — see `.claude/rules/landing-page-features.md`

## Test patterns

- `conftest.py` creates real async SQLAlchemy engine (`scope="session"`, `loop_scope="session"`), wraps each test in a connection-level transaction with rollback
- `client` fixture uses `httpx.AsyncClient` with `ASGITransport` and dependency overrides for `get_db`
- `login_session(client, db_session, email=...)` helper creates a User and logs in — each test uses a unique email to avoid conflicts
- Markers: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`
- pytest-asyncio in auto mode with `asyncio_default_fixture_loop_scope = "session"` and `asyncio_default_test_loop_scope = "session"` in pyproject.toml

## Adding a new module

1. Create `dashboard/<module>.py` with its own `router = APIRouter(prefix="/dashboard", tags=[...])`
2. Add router to `dashboard/__init__.py` (respect ordering: static routes before dynamic `{slug}` routes)
3. Create templates in `templates/dashboard/<module>/`, extending `layouts/project.html` with `{% block module_content %}`
4. Pass `active_module: "<module>"` in template context for sidebar highlighting
5. Add module link to sidebar in `layouts/project.html`
6. Add a `_feature_<module>(lang)` builder to `src/monolynx/features.py` (PL + EN), register it in `_FEATURES` and `_other_modules()`, and add a tile to `templates/landing.html` — see `.claude/rules/landing-page-features.md`

## Infrastructure

- **Docker**: Multi-stage Dockerfile (builder → dev → runtime). Dev target has hot reload, runtime uses non-root user with 2 workers
- **Docker Compose (dev)**: `dev` profile with PostgreSQL 16 (pgvector/pgvector:pg16) + Neo4j 5 (neo4j:5-community) + MinIO + app (monitor loop runs in-process by default). Optional `worker` profile runs monitor loop as separate service (`make worker`)
- **Docker Compose (prod)**: `app` service with `ENABLE_MONITOR_LOOP=false` + separate `worker` service running `python -m monolynx.worker`. Worker has no ports/Traefik — only DB access. Advisory lock ensures only one worker runs checks at a time
- **CI**: `.gitlab-ci.yml` — lint → test (coverage goal 50%) → build (main only) → deploy (manual)
- **Pre-commit**: ruff (check + format) and mypy with pydantic plugin

## Project language

Planning docs, comments, and UI text are in Polish.
