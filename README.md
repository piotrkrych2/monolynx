# Monolynx

Self-hosted project platform — error tracking, Scrum, uptime monitoring, wiki with AI search, and code dependency graphs. All in one tool.

## Modules

| Module | Description |
|---|---|
| **500ki** | Error tracking with smart fingerprinting and a lightweight Python SDK |
| **Scrum** | Backlog, Kanban board, sprints, story points, time tracking |
| **Monitoring** | URL health checks with uptime history and response times |
| **Wiki** | Markdown pages with hierarchy, image uploads, and RAG semantic search (pgvector + OpenAI) |
| **Connections** | Interactive code dependency graph powered by Neo4j and Cytoscape.js |
| **Heartbeat** | Reverse monitoring for cron jobs and workers — alerts when expected ping doesn't arrive on time |

## Tech stack

- **Backend**: Python 3.12, FastAPI (async), SQLAlchemy 2, Alembic
- **Database**: PostgreSQL 16 (pgvector), Neo4j 5, MinIO
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN), HTMX
- **AI**: MCP server (70 tools), OpenAI embeddings for wiki search
- **Infrastructure**: Docker multi-stage build, Traefik-ready

## Quick start (development)

```bash
# 1. Clone and configure
git clone https://gitlab.com/monolynx/monolynx.git
cd monolynx
cp .env.example .env
# Edit .env — set SECRET_KEY (required)

# 2. Start everything (PostgreSQL, Neo4j, MinIO, app with hot reload)
make dev

# 3. Run migrations and create admin user
make migrate
make createsuperuser

# 4. Open http://localhost:8000
```

### Useful commands

```bash
make dev                  # Start dev environment
make down                 # Stop all services
make logs                 # Tail app logs
make lint                 # ruff check --fix + ruff format + mypy
make test                 # Run tests with coverage
make migrate              # Run pending Alembic migrations
make migration msg="..."  # Generate new migration
make createsuperuser      # Create admin user
make worker               # Start monitoring worker separately
make setup                # Configure local dev environment
make backfill-embeddings  # Generate wiki search embeddings
make sync-graph           # Sync code dependency graph
```

All Python commands run inside Docker — never run them locally.

## Production deployment

Monolynx ships with a production-ready `docker-compose.prod.yml` that includes:

- **app** — FastAPI with Uvicorn (Traefik labels for HTTPS)
- **worker** — standalone monitoring checker loop
- **db** — PostgreSQL 16 with pgvector
- **neo4j** — Neo4j 5 Community
- **minio** — object storage for wiki content

```bash
# Build the production image
docker build -t monolynx:latest .

# Or pull from GitLab registry
docker pull registry.gitlab.com/piotrkrych/monolynx:latest

# Start production stack
docker compose -f docker-compose.prod.yml up -d
```

### Required environment variables

| Variable | Description |
|---|---|
| `SECRET_KEY` | Session signing key. Generate: `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `POSTGRES_PASSWORD` | Database password |
| `APP_URL` | Public URL (e.g. `https://monolynx.example.com`) |

### Optional environment variables

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_DB` | `open_sentry` | Database name |
| `POSTGRES_USER` | `sentry` | Database user |
| `ENVIRONMENT` | `production` | `development` or `production` |
| `LOG_LEVEL` | `info` | Logging level |
| `SMTP_HOST` | _(empty = disabled)_ | SMTP server for email invitations |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` / `SMTP_PASSWORD` | | SMTP credentials |
| `SMTP_FROM_EMAIL` | `noreply@monolynx.local` | Sender address |
| `MCP_ALLOWED_HOSTS` | _(empty)_ | Comma-separated allowed MCP hosts |
| `OPENAI_API_KEY` | _(empty = disabled)_ | Enables wiki RAG search |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI embedding model |
| `ENABLE_GRAPH_DB` | `true` | Enable/disable Neo4j integration |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `neo4j_dev` | Neo4j credentials |
| `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` | `minioadmin` / `minioadmin` | MinIO credentials |
| `ENABLE_MONITOR_LOOP` | `true` (dev) / `false` (prod) | Enable in-process monitor checker loop |
| `SKIP_LANDING_PAGE` | `true` | Skip landing page, redirect straight to dashboard |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | _(empty = disabled)_ | Google OAuth login |

### Traefik integration

The `app` service includes Traefik labels. Configure these env vars:

```env
TRAEFIK_NETWORK_NAME=traefik-external
TRAEFIK_ROUTE_NAME=monolynx
TRAEFIK_APP_HOSTNAME=monolynx.example.com
TRAEFIK_APP_ENTRYPOINT=websecure
TRAEFIK_APP_CERTRESOLVER=letsencrypt
```

## MCP (Model Context Protocol)

Monolynx exposes 70 tools via MCP — manage tickets, search wiki, query graphs, and more through Claude Desktop or any MCP client.

```json
{
  "mcpServers": {
    "monolynx": {
      "type": "streamable-http",
      "url": "https://your-instance/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

Generate API tokens at `/dashboard/profile/tokens`.

## How to work (Claude Code skills)

Monolynx ships with Claude Code skills that define a complete ticket workflow — from idea to implementation. Install them from `.claude/skills/` or download from the project website.

### Workflow: create → review → work

#### 1. `/monolynx-ticket-create [short task description]`

Creates a new ticket. The skill automatically gathers context from wiki, codebase, and the code dependency graph, then generates a full ticket description (goal, context, scope, acceptance criteria, dependencies). The ticket lands in a sprint or backlog.

```
/monolynx-ticket-create add PDF export to wiki pages
```

**What happens**: searches wiki for related docs, queries the dependency graph for affected modules, explores the codebase for existing code, checks for duplicate tickets — then proposes a complete ticket for your approval before creating it.

#### 2. `/monolynx-ticket-review [ticket-id or key, e.g. MNX-12]`

Reviews a ticket before you start working on it. The skill checks ticket form (clarity, acceptance criteria, scope), verifies assumptions against wiki and code, and generates a report with a table of findings.

```
/monolynx-ticket-review MNX-12
```

**What happens**: evaluates 6 form criteria (OK / WEAK / MISSING), cross-checks every technical assumption against wiki and code with triple verification for conflicts, then proposes fixes. Repeat the *review → fix → review* cycle until the report shows all "OK" and "MATCHING".

#### 3. `/monolynx-work [ticket-id or key, e.g. MNX-12]`

Picks up a ticket for implementation. The skill validates your git branch, runs a Researcher agent for deep analysis, assembles a team of specialized agents (backend, frontend, database, QA, DevOps — as needed), and runs them in parallel with a mandatory code reviewer (critic).

```
/monolynx-work MNX-12
```

**What happens**: validates branch naming (`feature-<number>-<slug>`), runs Researcher (wiki + graph + code analysis), selects the minimal agent team, posts a work plan as a ticket comment, runs all agents + critic in parallel, logs time for each agent, and sets ticket status to `in_review` when done.

### Additional skill

#### `/monolynx-create-graph-ci-script`

Generates a CI script (`cicd/sync_graph.py`) and a GitLab CI stage that automatically syncs the code dependency graph with Monolynx. The script analyzes your Python project (Django, FastAPI, Flask, etc.) via AST, maps files, classes, functions, and their relationships, then pushes the graph to Monolynx via MCP API. The graph database is then used by `/monolynx-work` — the Researcher queries it to understand code dependencies and suggest the right scope of changes.

```
/monolynx-create-graph-ci-script https://your-instance
```

Run once per project — CI handles the rest after every merge to main.

### Getting help

```
/monolynx-help
```

Shows the full skill guide with usage details.

## SDK (error tracking)

Install the Python SDK in your Django app to send exceptions to 500ki:

```bash
pip install monolynx-sdk
```

```python
# settings.py
MONOLYNX_DSN = "https://your-api-key@your-instance"
INSTALLED_APPS = [...]
MIDDLEWARE = [
    "monolynx_sdk.django.MonolynxMiddleware",
    ...
]
```

## Project structure

```
src/monolynx/
├── main.py              # FastAPI app, lifespan, MCP mount
├── config.py            # pydantic-settings configuration
├── database.py          # async SQLAlchemy session
├── models/              # 21 SQLAlchemy models
├── schemas/             # Pydantic validation models
├── api/                 # REST API (events ingestion, issues, OAuth)
├── dashboard/           # Web UI routes (all modules)
├── services/            # Business logic (auth, fingerprint, monitoring, wiki, graph...)
├── templates/           # Jinja2 templates
├── mcp_server.py        # FastMCP server (70 tools)
├── cli.py               # CLI commands (graph sync, maintenance)
└── worker.py            # Standalone monitoring worker
sdk/                     # Django SDK package
alembic/                 # Database migrations
tests/                   # pytest (unit + integration)
```

## License

MIT
