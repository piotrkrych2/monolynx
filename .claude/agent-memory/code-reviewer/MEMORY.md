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
- Dashboard modules lack per-project resource limits (monitoring has MAX_MONITORS_PER_PROJECT=20, heartbeat has MAX_HEARTBEATS_PER_PROJECT=50, attachments have NO limit)
- Blocking MinIO calls in async endpoints (wiki.py:532, scrum.py:658) — established pattern, no run_in_executor. MCP tool uses ThreadPoolExecutor correctly.
- Filename sanitization: scrum.py attachment uses os.path.basename + regex + chr(34)/chr(92) replace for Content-Disposition. Wiki still has no sanitization.
- Server-side MIME type validation missing on scrum attachment upload — client-side FilePond only. Stored XSS risk via text/html content-type.
- Constants defined in mcp_server.py instead of constants.py (LABEL_COLOR_PALETTE, ACTIVITY_ENTITY_TYPES) — recurring pattern drift
- New features added to some ticket tools but not all (labels in list_tickets/get_ticket but not search_tickets/create/update responses) — consistency gap

## Ticket ID Lookup
- Monolynx MCP uses UUID ticket IDs, not key strings like "MON-20". Must search by title/key first.

## Review History
- Heartbeat MON-19: iter1=62/100 (IDOR, clock skew, dead code), iter2=78/100 (all blockers fixed, needs constants+tests+error handling)
- Heartbeat MON-20: 76/100 REQUEST CHANGES (IntegrityError blocker, validation overwrites, no resource limit, no name length check)
- Heartbeat MON-23: 88/100 APPROVED (MCP tools — missing IntegrityError handling on create, minor double-query in delete)
- Heartbeat MON-24: 88/100 APPROVED (integration tests — 18 tests, all 6 required covered, minor: no token assertion in create test)
- MCP create_project MON-25: iter1=72/100 REQUEST CHANGES (description not saved, _code_from_slug 1-char bug, no empty name validation), iter2=90/100 APPROVED (all blockers fixed, minor: magic string role, no description strip)
- MCP update_project MON-26: iter1=78/100 REQUEST CHANGES (IntegrityError race on slug, updated_at uses created_at, no length validation), iter2=90/100 APPROVED (all 3 blockers fixed)
- MCP delete_project MON-27: 90/100 APPROVED (soft-delete, owner-only, confirm guard; minor: no logger, no updated_at on Project model)
- MCP get_project MON-28: 88/100 APPROVED (full project details; minor: scalar_one race, double ProjectMember query, active_sprint duplication with get_project_summary)
- MCP create_monitor MON-29: 82/100 REQUEST CHANGES (missing name/URL length validation vs DB String(255)/String(2048), missing url.strip(), magic number 20 instead of constant)
- MCP update_monitor MON-30: 86/100 APPROVED (PATCH semantics correct, SSRF protection on URL change; minor: no name/url length validation, no url.strip() — same as create_monitor)
- MCP delete_monitor MON-31: 90/100 APPROVED (owner/admin role check, cascade delete via model, 3 tests; minor: no UUID format validation, no DB assertion in test)
- due_date MON-32: 85/100 APPROVED (clean migration, ISO date validation, 3 filters, 12 tests; medium: overdue badge on done tickets in board.html/ticket_detail.html; low: silent fallback on invalid date in dashboard forms)
- search_tickets MON-33: 85/100 APPROVED (ILIKE query+description, 6 filters, 11 tests; medium: inconsistent response format vs list_tickets, no UUID validation on sprint_id; low: incomplete docstring, missing tests for due/sprint filters)
- update_sprint MON-35: 86/100 APPROVED (PATCH semantics, date validation, completed blocker; medium: no name length validation; low: no end_date clear, no tests)
- list_members MON-36: 90/100 APPROVED (clean JOIN+case ORDER BY, email fallback; low: no User.is_active filter, no alphabetical sort test within role)
- invite_member MON-37: iter1=72/100 REQUEST CHANGES (no tests blocker, no email format validation, magic number INVITATION_DAYS, inactive user edge case)
- remove_member MON-38: 88/100 APPROVED (correct authz, owner protection, 7 tests; medium: no email format validation inconsistent with invite_member; low: no self-removal guard, no DB assertion in test)
- create_issue MON-42: iter1=62/100 REQUEST CHANGES (data discarded — description/environment/traceback built into dict but never persisted, no tests, no title length validation), iter2=88/100 APPROVED (Event created with JSONB, 17 tests, title validation; minor: source missing from list/get_issue response)
- labels MON-39: iter1=82/100 REQUEST CHANGES (IntegrityError race on create_label, search_tickets missing labels support, create/update_ticket responses lack labels), iter2=82/100 REQUEST CHANGES (frontend badges — cross-project label injection via unvalidated label_ids, no UUID dedup)
- add_attachment MON-40: 80/100 APPROVED (model+migration clean, base64/size validation, MinIO reuse; high: filename not sanitized for path traversal/header injection; medium: Content-Disposition injection; low: no attachment count limit)
- get_activity_log MON-41: 82/100 APPROVED (model+migration+service+MCP tool; medium: two DB sessions instead of one, ACTIVITY_ENTITY_TYPES in mcp_server.py; low: redundant project_id index, no dedicated tests, log_activity not called anywhere yet)
- get_burndown MON-43: 72/100 REQUEST CHANGES (no tests blocker, updated_at unreliable for actual line, forecast_completion edge case, negative days_elapsed for future sprints)
- UI attachments MON-44: 80/100 APPROVED (FilePond upload+HTMX delete, membership check on write, filename sanitized; medium: no server-side MIME validation, no attachment count limit)
- get_graph_node MON-45: 86/100 APPROVED (Cypher filters + depth_map + grouped DSL output; medium: start node depth never 0; low: no tests for new filters)
- get_graph_node testy MON-45: 88/100 APPROVED (34 testy, 5 klas; medium: wrong @pytest.mark.integration marker; low: redundancja z test_mcp_server.py TestFormatGraphDsl)

## Test Patterns Confirmed
- Test fixture: connection-level transaction with rollback, `expire_on_commit=False` — services calling `db.commit()` work on savepoints
- `_make_project` helper in test_heartbeat.py is better DRY than test_monitoring_dashboard.py (which repeats Project() inline)
- `secrets.token_urlsafe(16)` produces 22 chars — matches `^hb_[A-Za-z0-9_-]{20,30}$` regex
- Neo4j async iterator mock pattern: `result_mock.__aiter__ = lambda self: AsyncIterMock(records)` — established in test_graph_service.py, reused in test_format_graph_dsl.py
- Neo4j driver mock: `_make_mock_graph_driver(session)` sets `__aenter__`/`__aexit__` on `driver.session.return_value` — correct for `async with _driver.session()` pattern
- MCP tool direct import+await in tests: `from monolynx.mcp_server import get_graph_node; await get_graph_node(ctx, ...)` — works because `@mcp.tool()` preserves callable

## MCP Response Format Inconsistency
- `list_tickets` returns `list[dict]` with `_meta` as last element — older pattern
- `search_tickets` returns `dict` with `results`, `total`, `page`, `total_pages` — newer, cleaner pattern
- Future tools should use the dict pattern; consider migrating list_tickets

## MCP Server Patterns
- MCP tools use `_get_user_and_project(ctx, slug)` for auth + project access
- Session via `async_session_factory()` — ORM objects accessed outside session block is established pattern (works for scalar columns, no lazy loads)
- Monitoring now has full CRUD MCP tools (list/get/create/update/delete); heartbeat has full CRUD
- `_is_url_safe` imported from `dashboard.monitoring` into `mcp_server.py` — private function cross-module import (code smell, works, no circular import)
- IntegrityError handling now in both create_project (flush+catch) and update_project (commit+catch)
- `create_project` description bug fixed in iter2 — now saved to model (mcp_server.py:353)
- `_auth(ctx)` used for project-level tools (list/create), `_get_user_and_project` for project-scoped tools
- `_slugify` and `_code_from_slug` are pure helpers in mcp_server.py (could be extracted)
- `delete_project` uses owner-only check (more restrictive than update_project's owner+admin) — correct for destructive ops
- `delete_monitor` uses owner/admin check — more restrictive than dashboard (which allows any logged-in user). `delete_heartbeat` has NO role check. Inconsistency across delete tools.
- `_get_user_and_project` queries ProjectMember but discards role — causes redundant query in every tool that needs role (get_project, update_project, delete_project). Refactoring candidate.
- `_get_user_member_and_project` added for invite_member — returns (User, ProjectMember, Project) tuple. Used by invite_member and remove_member. Could replace redundant role queries in delete_project etc.
- Project model lacks `updated_at` column — soft-delete timestamp exists only in MCP response, not persisted
- Sprint model lacks `updated_at` column — update_sprint returns `created_at` instead
- `create_sprint` does NOT validate end_date > start_date (update_sprint does) — inconsistency
- period/grace: MCP API uses minutes, DB stores seconds — conversion `*60` / `//60`
- ActivityLog model: services/activity.py has log_activity (flush, no commit) + get_activity_log (read-only). log_activity designed to be called within existing transactions.
- MCP tools sometimes open multiple async_session_factory() sessions in one tool — wasteful, should consolidate
- Ticket.updated_at has `onupdate=func.now()` — any edit resets it, unreliable for tracking status change dates. Burndown actual line uses this (approximation). Proper fix requires activity log (MON-41) integration.

## 500ki Module Patterns
- Issue model has NO description/exception_data column — exception data lives on Event model (JSONB columns: exception, request_data, environment)
- Manual issues (source="manual") now created with event_count=1 and associated Event (fixed in iter2)
- Issue.source column: String(20), server_default='auto', added via migration 1646e3dd1199
- Issue.level maps to "severity" in MCP API — established naming inconsistency
- Issue.fingerprint: auto issues use SHA256 hex (64 chars), manual issues use "manual-{uuid4.hex}" prefix — no collision risk

## Label Module Patterns
- Label model: `String(7)` for color (hex), `String(100)` for name, UniqueConstraint on (project_id, name)
- TicketLabel: composite PK (ticket_id, label_id) with CASCADE on both FKs
- Ticket.labels relationship uses `lazy="selectin"` — auto-eager-loads on every query, explicit selectinload is redundant
- Dashboard label sync pattern: delete-all + re-insert (sa_delete + loop) — no partial update
- Dashboard form label_ids via `form.getlist("label_ids")` — multi-checkbox pattern
- Label validation in MON-44: `_parse_valid_label_ids` validates against project labels — cross-project injection FIXED

## render_project_page helper
- Located in `dashboard/helpers.py`
- Adds `sidebar_badges` to context via `get_sidebar_badges(project.id, db)`
- All normal renders should use it; error renders use `templates.TemplateResponse` directly (established pattern)
