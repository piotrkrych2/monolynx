---
paths:
  - "src/monolynx/dashboard/**"
  - "src/monolynx/templates/dashboard/**"
  - "src/monolynx/templates/layouts/**"
---

# Architecture: dashboard routers and templates

Detail moved out of CLAUDE.md. Wiki form and blog authoring: `arch-wiki.md`.

## Dashboard module system (`dashboard/`)

- `dashboard/__init__.py` - combines all sub-routers into one `router`; ordering matters: static routes (users, settings, profile) before dynamic `{slug}` routes to avoid slug collision
- `dashboard/helpers.py` - shared `_get_user_id()`, `SLUG_PATTERN`, `templates` instance, `flash()` helper for session-based flash messages
- `dashboard/auth.py` - login/logout (`/auth/*`), invitation acceptance (`/auth/accept-invite/{token}`)
- `dashboard/projects.py` - project list, create (`/dashboard/`, `/dashboard/create-project`)
- `dashboard/profile.py` - user API token management for MCP access (`/dashboard/profile/*`)
- `dashboard/users.py` - user management, superuser-only (`/dashboard/users/*`); invitation system with token generation and email. Blog permission switch (MON-270): `POST /dashboard/users/{user_id}/blog-permission` with the form field `enabled` (`true` / `false`) sets `User.can_edit_blog` and answers 303; the form lives on the account edit page (`GET /dashboard/users/{user_id}`, `templates/dashboard/users/edit.html`), while the user list shows only a "Blog" badge for an account that has the permission and is not a superuser; like every route of this module it is superuser-only (403 otherwise), and it is the only place where the permission is granted or revoked
- `dashboard/sentry.py` - error tracking module "500ki": issues list, issue detail, SDK setup guide (`/dashboard/{slug}/500ki/*`)
- `dashboard/scrum.py` - Scrum module: backlog (with pagination + filtering), Kanban board, ticket CRUD with comments, sprints with status filtering (`/dashboard/{slug}/scrum/*`)
- `dashboard/monitoring.py` - URL monitoring module: monitor CRUD, check history with pagination, toggle on/off (`/dashboard/{slug}/monitoring/*`); includes SSRF protection (blocks localhost, private IPs), limit 20 monitors per project
- `dashboard/connections.py` - Connections module: graph visualization with Cytoscape.js, node/edge CRUD forms, graph API endpoint, setup guide page (`/dashboard/{slug}/connections/*`); graceful degradation when Neo4j unavailable. The graph is populated by the external [graphify](https://github.com/Graphify-Labs/graphify) extractor (offline tree-sitter AST, installed by the runner owner, never by CI) via `cicd/sync_graph.py` -> `replace_graph`; skills `/monolynx:create-graph-ci-script` (CI) and `/monolynx:graph-sync` (local, guided). Edge metadata carries `confidence` (EXTRACTED/INFERRED/AMBIGUOUS) and `source_relation`; taxonomy mapping lives on the wiki page "Mapowanie taksonomii Graphify -> Monolynx"
- `dashboard/settings.py` - project settings, member management (`/dashboard/{slug}/settings`)
- `dashboard/reports.py` - global cross-project work reports with multi-select filtering (project, user, sprint), date range, and PDF export via weasyprint (`/dashboard/reports`)
- `dashboard/work_plan.py`: Work Plan module (prefix `/dashboard/plan`): personal per-user cross-project scheduling via junction model (`User x Ticket x Date`), Gantt view (frappe-gantt) and monthly calendar, REST endpoints for entries CRUD plus JSON data and ticket autocomplete; independent of sprint assignment
- `dashboard/pipelines.py` - Pipelines module: pipeline list (with status/type filtering + pagination), pipeline detail (step columns research/coding/wrap-up with job cards), job detail (markdown log rendered from wiki), JSON API endpoints and HTMX partials for 15s live polling (`/dashboard/{slug}/pipelines/*`); session auth plus `scrum:read` (superuser bypass, no membership needed), read-only UI (pipelines are created by skills via MCP, not from the dashboard)

## Template layout system

- `layouts/base.html` - base layout (login, project list)
- `layouts/base.html` uses Tailwind CDN with typography plugin (`?plugins=typography`) for markdown `prose` styling
- `layouts/project.html` - extends base, adds sidebar with modules (500ki, Scrum, Monitoring, Wiki, Połączenia, Ustawienia); uses `active_module` context variable for highlighting
- Module templates extend `project.html` and use `{% block module_content %}`
- `dashboard/scrum/_nav.html` - shared partial with 4 always-visible buttons (Backlog, Tablica, Sprinty, Nowy ticket), included in all Scrum pages
