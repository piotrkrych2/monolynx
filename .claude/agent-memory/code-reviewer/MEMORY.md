# Code Reviewer Memory

## Project Patterns Confirmed
- Services use module-level `logger = logging.getLogger("monolynx.<module>")` pattern
- `monitoring.py` is a pure utility service (no CRUD, no DB) — just URL checking logic
- CRUD services (e.g., `services/wiki.py`, `services/sprint.py`) handle DB operations directly
- Model uses `secrets.token_urlsafe` for token generation with prefixes (e.g., `hb_`, `osk_`)
- Heartbeat module: model in `models/heartbeat.py`, API ping endpoint in `api/heartbeat.py`, service in `services/heartbeat.py`
- Heartbeat router registered in `main.py` at `/hb` prefix (unauthenticated ping endpoint)
- Project relationship: `Project.heartbeats` with `cascade="all, delete-orphan"`
- Dashboard modules do NOT check ProjectMember — any logged-in user can access any project by slug (established pattern across monitoring, heartbeat, etc.)
- Error validation in dashboard forms uses single `error` variable that gets overwritten — established but flawed pattern
- On validation error, dashboard uses `templates.TemplateResponse` directly (skipping `render_project_page` and sidebar badges) — accepted pattern

## Token Patterns
- MCP tokens: `osk_` prefix + SHA256 hash stored in DB (secure)
- Heartbeat tokens: `hb_` prefix + plain text stored in DB (accepted tradeoff for narrow scope — ping only)

## Recurring Issues to Watch
- Hard delete in heartbeat service (`db.delete()`) — accepted for ephemeral resources
- `check_heartbeat_statuses` bare `except Exception` with rollback — correct per CLAUDE.md
- Missing `constants.py` entries for heartbeat statuses ("pending", "up", "down")
- Missing Pydantic schemas for heartbeat CRUD — uses raw `dict[str, Any]`
- `scalar_one()` vs `scalar_one_or_none()` — services should handle not-found gracefully
- UniqueConstraint on models requires IntegrityError handling in dashboard — heartbeat has this, monitoring does not
- Dashboard modules lack per-project resource limits (monitoring has MAX_MONITORS_PER_PROJECT=20, heartbeat has MAX_HEARTBEATS_PER_PROJECT=50)

## Ticket ID Lookup
- Monolynx MCP uses UUID ticket IDs, not key strings like "MON-20". Must search by title/key first.

## Review History
- Heartbeat MON-19: iter1=62/100 (IDOR, clock skew, dead code), iter2=78/100 (all blockers fixed, needs constants+tests+error handling)
- Heartbeat MON-20: 76/100 REQUEST CHANGES (IntegrityError blocker, validation overwrites, no resource limit, no name length check)
- Heartbeat MON-23: 88/100 APPROVED (MCP tools — missing IntegrityError handling on create, minor double-query in delete)
- Heartbeat MON-24: 88/100 APPROVED (integration tests — 18 tests, all 6 required covered, minor: no token assertion in create test)

## Test Patterns Confirmed
- Test fixture: connection-level transaction with rollback, `expire_on_commit=False` — services calling `db.commit()` work on savepoints
- `_make_project` helper in test_heartbeat.py is better DRY than test_monitoring_dashboard.py (which repeats Project() inline)
- `secrets.token_urlsafe(16)` produces 22 chars — matches `^hb_[A-Za-z0-9_-]{20,30}$` regex

## MCP Server Patterns
- MCP tools use `_get_user_and_project(ctx, slug)` for auth + project access
- Session via `async_session_factory()` — ORM objects accessed outside session block is established pattern (works for scalar columns, no lazy loads)
- Monitoring has only list/get MCP tools; heartbeat adds full CRUD — first module with create/update/delete MCP tools
- No IntegrityError handling anywhere in mcp_server.py — recurring gap
- period/grace: MCP API uses minutes, DB stores seconds — conversion `*60` / `//60`

## render_project_page helper
- Located in `dashboard/helpers.py`
- Adds `sidebar_badges` to context via `get_sidebar_badges(project.id, db)`
- All normal renders should use it; error renders use `templates.TemplateResponse` directly (established pattern)
