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

## Tech stack

- **Backend**: Python 3.12, FastAPI (async), SQLAlchemy 2, Alembic
- **Database**: PostgreSQL 16 (pgvector), Neo4j 5, MinIO
- **Frontend**: Jinja2 templates, Tailwind CSS (CDN), HTMX
- **AI**: MCP server (30+ tools), OpenAI embeddings for wiki search
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
docker pull registry.gitlab.com/monolynx/monolynx:latest

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

Monolynx exposes 30+ tools via MCP — manage tickets, search wiki, query graphs, and more through Claude Desktop or any MCP client.

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
├── models/              # 14 SQLAlchemy models
├── schemas/             # Pydantic validation models
├── api/                 # REST API (events ingestion, issues, OAuth)
├── dashboard/           # Web UI routes (all modules)
├── services/            # Business logic (auth, fingerprint, monitoring, wiki, graph...)
├── templates/           # Jinja2 templates
├── mcp_server.py        # FastMCP server (30+ tools)
└── worker.py            # Standalone monitoring worker
sdk/                     # Django SDK package
alembic/                 # Database migrations
tests/                   # pytest (unit + integration)
```

## License

MIT
