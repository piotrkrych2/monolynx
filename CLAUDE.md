# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is this project?

Monolynx is a multi-module project platform. It started as a minimalist error-tracking system (500ki module — named after HTTP 500 errors) and now includes a Scrum module (backlog, Kanban board, sprints, story points) and a Monitoring module (URL health checks with uptime tracking). The architecture supports adding new modules via the sidebar navigation.

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
- `constants.py` — shared constants for Scrum (ticket statuses, priorities, sprint statuses, member roles, label mappings) and Monitoring (interval units, Polish labels)

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
- `dashboard/settings.py` — project settings, member management (`/dashboard/{slug}/settings`)

**Models** (`models/`) — 12 SQLAlchemy models: Project, Issue, Event, User, UserApiToken, ProjectMember, Sprint, Ticket, TicketComment, Monitor, MonitorCheck + Base

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

**Worker** (`worker.py`):
- Standalone entry point (`python -m monolynx.worker`) — runs monitor checker loop without web server
- Graceful shutdown via `SIGTERM`/`SIGINT`; healthcheck via `/tmp/worker-healthy` file touch
- In production: separate Docker service; in dev: optional via `make worker` or `--profile worker`

**MCP Server** (`mcp_server.py`):
- FastMCP-based server mounted at `/mcp` in the main app
- 11 tools for Scrum management: ticket CRUD, sprint lifecycle, comments
- Bearer token auth via `Authorization` header (tokens managed in `/dashboard/profile/tokens`)
- `.mcp.json` at project root configures Claude Code connection (env var `MONOLYNX_MCP_TOKEN`)

**Template layout system**:
- `layouts/base.html` — base layout (login, project list)
- `layouts/project.html` — extends base, adds sidebar with modules (500ki, Scrum, Monitoring, Ustawienia); uses `active_module` context variable for highlighting
- Module templates extend `project.html` and use `{% block module_content %}`
- `dashboard/scrum/_nav.html` — shared partial with 4 always-visible buttons (Backlog, Tablica, Sprinty, Nowy ticket), included in all Scrum pages

**SDK** (`sdk/src/monolynx_sdk/`) — standalone Django middleware package:
- Zero external dependencies (stdlib only)
- Rule: SDK must NEVER crash the host application — every public function wrapped in try/except
- `transport.py` sends events via `ThreadPoolExecutor(max_workers=2)` using `urllib.request`
- Django settings: `MONOLYNX_DSN` or `MONOLYNX_URL` + `MONOLYNX_API_KEY`

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

## Infrastructure

- **Docker**: Multi-stage Dockerfile (builder → dev → runtime). Dev target has hot reload, runtime uses non-root user with 2 workers
- **Docker Compose (dev)**: `dev` profile with PostgreSQL 16 + app (monitor loop runs in-process by default). Optional `worker` profile runs monitor loop as separate service (`make worker`)
- **Docker Compose (prod)**: `app` service with `ENABLE_MONITOR_LOOP=false` + separate `worker` service running `python -m monolynx.worker`. Worker has no ports/Traefik — only DB access. Advisory lock ensures only one worker runs checks at a time
- **CI**: `.gitlab-ci.yml` — lint → test (coverage goal 50%) → build (main only) → deploy (manual)
- **Pre-commit**: ruff (check + format) and mypy with pydantic plugin

## Project language

Planning docs, comments, and UI text are in Polish.
