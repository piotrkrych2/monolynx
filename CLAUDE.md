# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is this project?

Monolynx is a multi-module project platform. It started as a minimalist error-tracking system (500ki module - named after HTTP 500 errors) and now includes a Scrum module (backlog, Kanban board, sprints, story points), a Monitoring module (URL health checks with uptime tracking), a Wiki module (markdown pages with semantic RAG search via pgvector, plus an opt-in "LLM Wiki" method with backlinks, system pages, and INGEST/QUERY/LINT operations), a Connections module (code dependency graph visualization using Neo4j), a Work Plan / Gantt module (personal per-user cross-project scheduling with Gantt and calendar views), a Pipelines module (observability over AI agent work, modeled on GitLab CI/CD; pipeline -> step -> job hierarchy where job logs are stored as wiki pages), and a public blog (wiki pages published at `/blog/` as HTML with a markdown twin for AI readers, behind an AI-readiness lint). The architecture supports adding new modules via the sidebar navigation.

## Commands

All commands run inside Docker. Never run Python commands locally - always use `docker compose exec app <command>`. Exception: stdlib-only repo scripts in `scripts/` and `plugin/scripts/` (`sync_skills.py`, `validate_plugin_consistency.py`, `*_state.py`) run on the host with `python3`, exactly as the `Makefile` targets `sync-skills`, `sync-skills-check` and `validate-plugin` do. Application code under `src/`, `pytest`, `ruff` and `mypy` still run only in Docker.

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
make prune-test-dbs                   # Drop leftover per-run test databases (open_sentry_test_*) with no active connections

# Database migrations
make migrate                          # Run pending migrations
make migration msg="description"      # Generate new migration

# Admin
make createsuperuser                  # Create admin user (interactive prompt)

# Wiki RAG
make backfill-embeddings              # Generate embeddings for existing wiki pages
make backfill-backlinks               # Generate backlinks for existing wiki pages (LLM Wiki)
make backfill-dependencies            # Move prose ticket dependencies into blocked_by_ids (dry-run; ARGS=--apply writes)

# Build
make build                            # Build production Docker image

# CLI (cli/, package monolynx-cli): build and run in a throwaway container, from the repo root.
# The mount is read-only and the package is copied inside the container, because pip install
# as root would write build/ and *.egg-info into the repo.
docker run --rm -v "$PWD/cli":/src:ro python:3.10-slim sh -c "cp -r /src /cli && pip install -q /cli && monolynx --help"
```

**Important**: When generating Alembic migrations with autogenerate, always review the output - it may include tables from a previous migration if the DB was empty at generation time. Each migration must only contain its own new/changed tables.

## Architecture

Three separate packages in one repo: the backend (`src/monolynx/`), the Django SDK (`sdk/`) and the command-line client (`cli/`).

This file is the map. Per-area detail (function contracts, error texts, ticket history) lives in path-scoped files `.claude/rules/arch-*.md`, loaded automatically when a matching file is read. Before changing an area, read its file from the table below if it is not in context yet.

| Area | Detail file |
|------|-------------|
| Dashboard routers, layouts | `arch-dashboard.md` |
| Wiki module, LLM Wiki, blog authoring form | `arch-wiki.md` |
| Public blog routes, markdown twins, feeds, landing | `arch-blog-public.md` |
| `services/blog.py`, `blog_posts` model | `arch-blog-service.md` |
| Blog blocks, chart SVG, post lint | `arch-blog-blocks.md` |
| API v2 routers, schemas, MCP parity map | `arch-api-v2.md` |
| MCP server, RBAC, blog permission | `arch-rbac-mcp.md` |
| Other services (tickets, projects, pipelines, work plan, graph) | `arch-services.md` |
| `monolynx` CLI | `arch-cli.md` |
| Full URL list | `arch-urls.md` |
| Plugin: overview, skills, versioning | `arch-plugin.md` |
| Plugin: hooks | `arch-plugin-hooks.md` |
| Plugin: `sprint-run`, `work`, test queue | `arch-plugin-sprint.md` |
| Plugin: `mr-queue` | `arch-plugin-mr-queue.md` |
| Plugin: skill transport (CLI or MCP) | `arch-plugin-transport.md` |

**Backend** (`src/monolynx/`) - FastAPI async server:
- `main.py` registers routers lazily via `_register_routers()` to avoid circular imports; lifespan optionally starts monitor checker loop (controlled by `ENABLE_MONITOR_LOOP`, default true for dev, false in prod)
- `config.py` uses pydantic-settings, reads from env vars / `.env` file (see `.env.example`)
- `database.py` provides async SQLAlchemy session via `get_db()` FastAPI dependency
- `constants.py` - shared constants for Scrum (ticket statuses, priorities, sprint statuses, member roles, label mappings), Monitoring (interval units, Polish labels), Time Tracking (entry statuses, report defaults), and Graph (node types, edge types, Polish labels)

**Dashboard** (`dashboard/`): one router per module, combined in `dashboard/__init__.py`. Ordering matters: static routes (users, settings, profile) before dynamic `{slug}` routes. `helpers.py` holds `_get_user_id()`, `SLUG_PATTERN`, the `templates` instance and `flash()`. Modules: `auth`, `projects`, `profile` (API tokens), `users` (superuser only, invitations, blog permission switch), `sentry` (500ki), `scrum`, `monitoring` (SSRF protection, 20 monitors per project), `wiki`, `connections` (Cytoscape.js over Neo4j, populated by the external graphify extractor via `cicd/sync_graph.py`), `settings`, `reports` (PDF via weasyprint), `work_plan` (prefix `/dashboard/plan`), `pipelines` (read-only UI, 15s HTMX polling).

**Templates**: `layouts/base.html` (Tailwind CDN + typography) -> `layouts/project.html` (sidebar, `active_module` for highlighting) -> module templates with `{% block module_content %}`. `layouts/landing.html` serves the landing page, feature pages and the blog.

**Public blog** (`blog.py`, `blog_ai.py`): routers with prefix `/blog`, no session, no RBAC. A post is a wiki page with `is_public=True` plus its `BlogPost` row; anything else is 404, never 403. `blog_ai.py` (markdown twins `/blog/{slug}.md`, `/blog/index.md`, feeds, `/llms-full.txt`) must be registered BEFORE `blog.py`, because `/blog/{slug}` also matches `x.md`. Post content goes through `render_post_html()` from `services/blog_blocks.py`.

**API v2** (`api/v2/`): REST API under `/api/v2` for the CLI and integrations. Bearer token shared with MCP (`verify_bearer_token()`), every error is `application/problem+json`. Routers are thin adapters over `services/`; `service_error()` in `api/v2/common.py` maps a service `ValueError` to 404 / 403 / 422. Domain routers are included BEFORE the catch-all.

**Models** (`models/`) - 34 SQLAlchemy models: Project, Issue, Event, User, UserApiToken, ProjectMember, Role, Sprint, Ticket, TicketComment, TicketAttachment, TicketAcceptanceCriterion, TicketDependency, Monitor, MonitorCheck, TimeTrackingEntry, WorkPlanEntry, WikiPage, WikiEmbedding, WikiAttachment, WikiFile, WikiBacklink, BlogPost, ActivityLog, Heartbeat, Label, TicketLabel, Pipeline, PipelineStep, PipelineJob, Settlement, SettlementProject, SettlementTicket, SettlementAttachment + OAuth models (OAuthClient, OAuthAuthorizationCode, OAuthAccessToken, OAuthRefreshToken) + Base

**Services** (`services/`): business logic lives here, once. Convention for functions behind MCP and API v2 (`projects.py`, `tickets.py`, `wiki.py`, `wiki_attachments.py`, `blog.py`): signature `(db, user, project, ...)`, result is an ORM model or a `@dataclass` (never a bare `dict`), business error is `ValueError`, commit inside the service. A function taking a user opens with `ensure_permission()` before the resource lookup. Low-level functions without a user (`create_wiki_page`, `sprint.py`, `burndown.py`) check no role permission; the adapter does. Optional backends degrade gracefully: `embeddings.py` (`OPENAI_API_KEY=""`), `graph.py` (`ENABLE_GRAPH_DB=false`), `email.py` (`SMTP_HOST=""`).

**Worker** (`worker.py`):
- Standalone entry point (`python -m monolynx.worker`) - runs monitor checker loop without web server
- Graceful shutdown via `SIGTERM`/`SIGINT`; healthcheck via `/tmp/worker-healthy` file touch
- In production: separate Docker service; in dev: optional via `make worker` or `--profile worker`

**MCP Server** (`mcp_server.py`): FastMCP mounted at `/mcp`, 125 tools, Bearer auth (OAuth access token or legacy `osk_*`). `_auth(ctx)` resolves the user, `_get_user_and_project()` adds membership, `_require(db, user, project, module, action)` checks the role permission as the first step. Every tool declares `ToolAnnotations` through `READ_ONLY`, `WRITE` or `DESTRUCTIVE` (a test fails otherwise). Every write tool must `await db.commit()`.

**Claude Code plugin** (`plugin/`): 24 skills (`/monolynx:*`), 7 role agents, hooks (`bash_guard.py`, `brief_guard.py`) and the remote MCP connector. Marketplace manifest at `.claude-plugin/marketplace.json`, plugin manifest at `plugin/.claude-plugin/plugin.json`. The connector key `monolynx` is part of every tool name (`mcp__monolynx__*`), never rename it. Skill copies under `src/monolynx/static/skills/` and the transport block in each `SKILL.md` are generated by `scripts/sync_skills.py`, never edited by hand. A ticket never bumps the plugin version: it adds a `### Unreleased` changelog entry in `plugin/README.md` (`.claude/rules/plugin-version-bump.md`). Skill-count phrases must equal the number of `plugin/skills/*/SKILL.md` directories (`tests/unit/test_skill_counters.py`).

**SDK** (`sdk/src/monolynx_sdk/`) - standalone Django middleware package:
- Zero external dependencies (stdlib only)
- Rule: SDK must NEVER crash the host application - every public function wrapped in try/except
- `transport.py` sends events via `ThreadPoolExecutor(max_workers=2)` using `urllib.request`
- Django settings: `MONOLYNX_DSN` or `MONOLYNX_URL` + `MONOLYNX_API_KEY`

**CLI** (`cli/src/monolynx_cli/`) - PyPI package `monolynx-cli`, Typer app with entry points `monolynx` and `mnx`, `requires-python = ">=3.10"`. Depends only on HTTP API v2: it imports nothing from `src/monolynx/` and the backend imports nothing from `cli/`. Not to be confused with `src/monolynx/cli.py` (`python -m monolynx.cli <command>`), the admin CLI inside the backend container that the `Makefile` targets call. Commands pass data to `emit(ctx, data)` and never print it themselves. Exit codes: 0 success, 1 API or local error, 2 usage, 3 auth (401/403), 4 network. Help texts in Polish, tests in `cli/tests/`.

**Schemas** (`schemas/`) - Pydantic models: `events.py`, `issues.py`, `scrum.py`, `time_tracking.py`, `graph.py`, `wiki.py`, `pipelines.py`; `schemas/api_v2/` holds the v2 request/response schemas and `PaginatedResponse[T]`.

**Data flow**: Django error → SDK middleware `process_exception()` → background thread POST → FastAPI ingests → fingerprint → find/create Issue → store Event (JSONB)

## Cross-cutting rules

- **One service layer, three channels.** MCP tools and API v2 routers are thin adapters over the same service functions; the CLI sits on top of v2. A new or changed MCP tool comes with its v2 endpoint and CLI command in the same ticket, or with a reason in `INTENTIONALLY_UNMAPPED` (`tests/integration/tool_endpoint_map.py`). See `.claude/rules/mcp-cli-parity.md`.
- **RBAC has one source of truth**, `check_permission()` in `services/permissions.py`: the dashboard calls `require_permission()` (`HTTPException(403)`), services and MCP tools call `ensure_permission()` (`ValueError("Brak uprawnienia {module}:{action}")`), API v2 maps that `ValueError` to 403. The check comes first, before the resource lookup, so a refusal writes nothing and 404 cannot be told from 403. Project membership alone grants no module access through a token. The default `member` role has `scrum` and `wiki` read + write but no delete.
- **Blog publishing is an account-level permission** (`User.can_edit_blog`, superuser always has it), not a role permission: `has_blog_permission()` / `ensure_blog_permission()`, refusal `Brak uprawnienia blog:write`. Granted only by a superuser on `/dashboard/users/{user_id}`; no MCP tool, v2 endpoint or CLI command grants it. Gated: post metadata, publish, unpublish, any change of `is_public`, editing the title or content of a public page, uploading a file to a public page, deleting a public page or one with a public descendant.
- **`WikiPage.is_public` is the only exposure switch.** A `BlogPost` row alone publishes nothing. The post slug is set once and is independent of the page title; an explicit change keeps the old value in `previous_slugs` (301).
- **Blog author content never injects HTML, CSS or JS.** One `nh3` pass with no `class` and no `id`; block HTML comes only from renderers in `services/blog_blocks.py`. The `blog-*` class names in templates are a contract asserted by tests.
- **Database writes need `await db.commit()`** (`.claude/rules/db-commit.md`).
- **Landing content** lives in `src/monolynx/features.py` (PL + EN). Any new MCP tool, module or user-visible feature updates it in both languages, together with the "N MCP tools" counter (`.claude/rules/landing-page-features.md`).
- **Graph writes** have no v2 endpoint and no CLI command: the only writer is `cicd/sync_graph.py` through MCP.

## URL structure

Prefix map; the full route list is in `.claude/rules/arch-urls.md`.

```
/auth/*                              - login, logout, invitation acceptance
/dashboard/                          - project list, create project
/dashboard/profile/*                 - API tokens, MCP guide
/dashboard/users/*                   - user management (superuser only)
/dashboard/reports, /dashboard/plan  - cross-project reports and Work Plan
/dashboard/{slug}/500ki/*            - error tracking
/dashboard/{slug}/scrum/*            - backlog, board, tickets, sprints
/dashboard/{slug}/monitoring/*       - URL monitors
/dashboard/{slug}/wiki/*             - wiki pages, search, upload, blog preview
/dashboard/{slug}/connections/*      - code graph
/dashboard/{slug}/pipelines/*        - pipelines (read-only)
/dashboard/{slug}/settings/*         - project settings, members
/blog/, /blog/{slug}, /blog/{slug}.md, /blog/index.md, /blog/feed.xml|rss.xml|feed.json  - public blog
/llms.txt, /llms-full.txt, /blog-authoring.md, /sitemap.xml, /robots.txt   - for crawlers and AI readers
/api/v1/events, /api/v1/health, /api/v1/issues/{id}/status   - SDK ingest (X-Monolynx-Key)
/api/v2/*                            - REST API v2 (Bearer, problem+json), docs at /api/v2/docs
/mcp                                 - MCP server (Bearer)
```

## Key technical decisions

- All DB columns storing error data use PostgreSQL JSONB (exception, request_data, environment)
- Issue grouping: `UniqueConstraint("project_id", "fingerprint")` - fingerprint ignores line numbers for stability
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
- Markdown rendering shared via `render_markdown_html()` from `services/wiki.py` - used in wiki pages, ticket descriptions, and comments; frontend uses Tailwind `prose prose-invert` classes
- EasyMDE (WYSIWYG markdown editor) used in wiki page forms and ticket create/edit forms; dark theme via inline CSS overrides
- Docker uses `pgvector/pgvector:pg16` image for PostgreSQL with vector extension support
- Neo4j graph database for Connections module; node types: File, Class, Method, Function, Const, Module; edge types: CONTAINS, CALLS, IMPORTS, INHERITS, USES, IMPLEMENTS; data isolated per project via `project_id` property; graceful degradation when `ENABLE_GRAPH_DB=false`
- Cytoscape.js (CDN v3.30.4) for interactive graph visualization; force-directed layout (cose); node/edge coloring by type; filtering via checkboxes; side panel with node details on click
- Landing page content lives in `src/monolynx/features.py` (Python builders `_feature_<module>(lang)` returning `features[]`, `steps[]`, `mcp_tools[]`, `tech_details[]` per module, PL + EN), rendered to `/features/<slug>` by `main.py`; the template only renders. Any new MCP tool / module / user-visible feature must be added to `features.py` in BOTH languages (and the `ai_intro` "N MCP tools" counter kept in sync) as part of the same change - see `.claude/rules/landing-page-features.md`
- LLM Wiki method (Karpathy-style): opt-in per project via `Project.wiki_llm_enabled`; reserved system-page slugs `wiki-index`, `wiki-log`, `wiki-schema`; backlinks stored in PostgreSQL (`WikiBacklink`), never in Neo4j
- Work Plan: junction model `WorkPlanEntry` (`user_id`, `ticket_id`, `scheduled_date`), per-user and cross-project, independent of sprint assignment; reads show only entries from projects where the user is currently a member
- Pipelines are observability, not execution: `Pipeline -> PipelineStep -> PipelineJob` in PostgreSQL; job logs are wiki pages under `pipeline-logi` with `exclude_from_embeddings=True`; a pipeline MCP error never fails the ticket work
- Three markdown renderers coexist: `render_markdown_html()` (dashboard, tickets, comments; no sanitization), `render_markdown_public()` (sanitized, no caller in `src/`) and `render_post_html()` (blog posts)

## Test patterns

- `conftest.py` creates real async SQLAlchemy engine (`scope="session"`, `loop_scope="session"`), wraps each test in a connection-level transaction with rollback
- Test database is per pytest run (MON-250), so parallel runs (e.g. sprint sessions in separate git worktrees) never share tables; before, every run used one `open_sentry_test` database and the `drop_all` at the end of one run removed the tables of another. The `engine` fixture creates its own database `open_sentry_test_<8 random hex chars>` (`CREATE DATABASE` + `CREATE EXTENSION IF NOT EXISTS vector` + `Base.metadata.create_all`) and drops it at session end (`DROP DATABASE ... WITH (FORCE)` after `engine.dispose()`); there is no `drop_all` on a shared database any more
- `TEST_DATABASE_NAME` overrides the name (e.g. `-e TEST_DATABASE_NAME=open_sentry_test_MON-250`). It is validated by `validate_test_db_name()` from `src/monolynx/cli.py`: it must start with `open_sentry_test_`, followed only by letters, digits, `_` and `-`, max 63 characters, otherwise `ValueError` at session start. The forced prefix keeps the dev database (`open_sentry`) from being dropped. An existing database with that name (left by an interrupted run) is dropped before creation; two concurrent runs with the SAME manual name still collide
- `make prune-test-dbs` (`python -m monolynx.cli prune-test-dbs`) removes leftovers of interrupted runs: `open_sentry_test_*` databases with no active connections (databases of runs in progress are skipped, no `FORCE`); the old shared `open_sentry_test` database without a suffix is left alone. `make test` and the CI `clean-code` job run with unchanged commands
- `client` fixture uses `httpx.AsyncClient` with `ASGITransport` and dependency overrides for `get_db`
- `login_session(client, db_session, email=...)` helper creates a User and logs in - each test uses a unique email to avoid conflicts
- Markers: `@pytest.mark.unit`, `@pytest.mark.integration`, `@pytest.mark.e2e`
- pytest-asyncio in auto mode with `asyncio_default_fixture_loop_scope = "session"` and `asyncio_default_test_loop_scope = "session"` in pyproject.toml

## Adding a new module

1. Create `dashboard/<module>.py` with its own `router = APIRouter(prefix="/dashboard", tags=[...])`
2. Add router to `dashboard/__init__.py` (respect ordering: static routes before dynamic `{slug}` routes)
3. Create templates in `templates/dashboard/<module>/`, extending `layouts/project.html` with `{% block module_content %}`
4. Pass `active_module: "<module>"` in template context for sidebar highlighting
5. Add module link to sidebar in `layouts/project.html`
6. Add a `_feature_<module>(lang)` builder to `src/monolynx/features.py` (PL + EN), register it in `_FEATURES` and `_other_modules()`, and add a tile to `templates/landing.html` - see `.claude/rules/landing-page-features.md`

## Infrastructure

- **Docker**: Multi-stage Dockerfile (builder → dev → runtime). Dev target has hot reload, runtime uses non-root user with 2 workers
- **Docker Compose (dev)**: `dev` profile with PostgreSQL 16 (pgvector/pgvector:pg16) + Neo4j 5 (neo4j:5-community) + MinIO + app (monitor loop runs in-process by default). Optional `worker` profile runs monitor loop as separate service (`make worker`)
- **Docker Compose (prod)**: `app` service with `ENABLE_MONITOR_LOOP=false` + separate `worker` service running `python -m monolynx.worker`. Worker has no ports/Traefik - only DB access. Advisory lock ensures only one worker runs checks at a time
- **CI**: `.gitlab-ci.yml` - lint → test (coverage goal 50%) → build (main only) → deploy (manual). Job `dockle-scan` (dockle 0.4.14, obraz `:test`, `--exit-code 1`) biegnie na MR zmieniającym `Dockerfile` albo `.gitlab-ci.yml`; wyjątki: `-af settings.py` (CIS-DI-0010, fałszywy alarm po nazwie pliku, dopasowanie po nazwie bazowej, więc nowy `settings.py` sprawdzić ręcznie) i `-i DKL-DI-0005` (warstwa obrazu bazowego `python:3.12-slim` z `apt-get dist-clean`, którego dockle 0.4.14 i 0.4.15 nie rozpoznają); powody w komentarzu nad jobem
- **Pre-commit**: ruff (check + format) and mypy with pydantic plugin

## Merge requests: pipeline must be green

Every MR is followed to the end, never fire-and-forget. After `glab mr create` (or any push to an MR branch):
1. Monitor the branch pipeline until it reaches a terminal state (`glab ci status --branch <branch>` / `glab ci get --pipeline-id <id> -F json`; poll in the background, roughly every 60s; a full `clean-code` round takes 15-20 min).
2. Merge only when the pipeline is `success`. Red pipeline: read the failing job trace (`glab ci trace <job_id>`), fix the cause on the branch, push, monitor again. `exit status 137` in `clean-code` with no failed test means the test container was SIGKILLed: before MON-175 a concurrent `clean-code` job on the same runner ran `docker rm -f db test-runner` on fixed container names and killed the other job's containers; since MON-175 the job has `resource_group: clean-code` and per-job names (`db-$CI_JOB_ID`, `test-runner-$CI_JOB_ID`, `test-net-$CI_JOB_ID`). If 137 still appears, retry the job (`glab ci retry <job_id>`) and check runner memory; it is not a code failure.
3. After merge, monitor the pipeline on `main` the same way. `deploy-staging` is a manual job and stays `manual`; every other job must be `success`. Retry on 137, fix forward on a real failure.
4. Report the outcome with pipeline ids and job statuses. A ticket goes to `done` only after the `main` pipeline is green.

## Project language

Planning docs, comments, and UI text are in Polish.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
