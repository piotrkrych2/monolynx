# Code Reviewer Memory

## Topic files
- [plugin_packaging.md](plugin_packaging.md) — Claude Code plugin (MON-72): structure, `claude plugin validate`, em-dash scope gap, `${user_config.X}`-in-bash bug. MON-72: 88/100 APPROVED.
- [migration_head_verify.md](migration_head_verify.md) — Alembic review: zawsze sam licz head + sprawdz unikalnosc revision ID, nie wierz ticketowi (MON-73 blocker).
- [wiki_llm_method.md](wiki_llm_method.md) — Metoda LLM Wiki (MON-73): sygnatury wiki/wiki_lint/wiki_bootstrap, marker sprzecznosci regex, RESERVED_SLUGS, patch MinIO gotcha (wiki_lint ma wlasny import get_markdown).

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
- `scalar() or default` anti-pattern: `0 or -1` returns -1 in Python (0 is falsy). When using COALESCE in SQL, don't add Python-side `or` fallback — it breaks on zero values. Seen in MON-53 position calculation.

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
- dark/light mode MON-47: iter1=72/100 REQUEST CHANGES (4 standalone templates missing class="dark"/anti-FOUC/darkMode, landing bg-gray-950 hardcoded, logo always white on auth, status badges dark-only, toasts text-gray-900 on colored bg), iter2=88/100 APPROVED (all blockers fixed; low: status badges dark-only, landing no toggle, minor indent)
- wiki attachments MON-49: 58/100 NEEDS WORK (3 critical: page_detail missing attachments/can_edit context, files.html uses wrong variable name + wrong model attrs, _get_wiki_page missing selectinload; medium: no MIME validation, get_wiki_attachment filename ambiguity, templates.TemplateResponse instead of render_project_page)
- dashboard statusy MON-50: 88/100 APPROVED (bulk project_stats.py + projects.py paginacja/search/sort + projects.html ikonki; medium: issues_pulse logika inna niż sidebar; minor: unused field import, COALESCE inconsistency, no aria-labels on SVG)
- monitoring notifications MON-52: iter1=78/100 REQUEST CHANGES (XSS in email HTML, Slack sync blocking async, missing SSRF on Slack URL save), iter2=90/100 APPROVED (all 4 blockers fixed; db-specialist 92, backend-dev 72→88, frontend-dev 88, qa-tester 85)
- acceptance criteria MON-53: iter1=74/100 REQUEST CHANGES (lint fail B904, position bug `0 or -1`, light mode text-gray-200, dark-only form styling, dead HTMX attrs, no tests), iter2=88/100 APPROVED (all blockers fixed; low: 4x dark-only styling in template, no description length limit)

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

## Dark/Light Mode Patterns (MON-47)
- Tailwind `darkMode: 'class'` configured via inline `tailwind.config` in each standalone template
- Anti-FOUC: inline `<script>` in `<head>` reads localStorage('monolynx-theme'), removes 'dark' class if 'light'
- Default: dark mode (`class="dark"` on `<html>`)
- Toggle: `toggleTheme()` in base.html, button in navbar (sun/moon icons via `hidden dark:block`/`dark:hidden`)
- Logo: dual variant pattern — `monolynx-logo-white.svg` (hidden dark:block) + `monolynx-logo-color.svg` (dark:hidden)
- Standalone templates (login, accept_invite, oauth, landing) each have own anti-FOUC + darkMode config (not inherited from base.html)
- Color pattern: `bg-white dark:bg-gray-800`, `text-gray-900 dark:text-white`, `border-gray-200 dark:border-gray-700`
- Inputs: `bg-gray-100 dark:bg-gray-700`, `border-gray-300 dark:border-gray-600`
- Status badges (bg-green-900 text-green-300 etc.) kept dark-only — accepted as "dark pill" style on both themes
- prose → `prose dark:prose-invert` (3 locations: wiki page_detail, ticket_detail description, ticket_detail comments)

## Wiki Attachment Patterns (MON-49)
- WikiAttachment model: FK to wiki_pages (CASCADE), same structure as TicketAttachment
- WikiFile model: FK to projects (CASCADE), has extra `description` (Text, nullable)
- Storage paths: `{slug}/wiki-attachments/{page_id}/{uuid}.{ext}` and `{slug}/wiki-files/{uuid}.{ext}` — no collision with old `{slug}/attachments/`
- New dashboard endpoints use `run_in_executor` for MinIO (correct), old `wiki_upload`/`wiki_attachment` still blocking
- `upload_object()` in minio_client.py — generic version of `upload_attachment()` without auto-UUID naming
- WikiPage.attachments relationship: `order_by="WikiAttachment.created_at"`, `cascade="all, delete-orphan"`
- No UniqueConstraint on (wiki_page_id, filename) — duplicate filenames possible, MCP get_wiki_attachment vulnerable

## Project Stats / Dashboard List Patterns
- `services/project_stats.py` — bulk stats via 5 GROUP BY queries (issues, uptime, heartbeats, SP, activity)
- `dashboard/projects.py` — paginacja (per_page=10), search (ILIKE name+slug), sort (name/activity asc/desc)
- Sort by activity uses outerjoin on Ticket.updated_at subquery + nulls_last
- `_SORT_OPTIONS` is a set whitelist — safe against injection
- issues_pulse in project_stats uses count>=5 threshold (differs from sidebar.py which uses last_seen recency)
- Ticket import added to projects.py for activity subquery — not a circular import concern

## Notification Module Patterns (MON-52)
- notification_config stored as JSONB on Monitor model — keys: email_enabled, email_recipients, sms_enabled, sms_recipients, slack_enabled, slack_channels
- Debouncing via last_alert_sent_at + ALERT_DEBOUNCE_MINUTES=5
- SMS via lepszesmsy.pl REST API (LEPSZESMSY_LICENSE_KEY in config.py) — ThreadPoolExecutor pattern like email.py
- Slack via incoming webhook — SSRF protection in _is_webhook_url_safe (same logic as _is_url_safe in monitoring.py, duplicated)
- _parse_notification_config in dashboard/monitoring.py — validates email regex, phone regex, webhook URL scheme
- Email/SMS use fire-and-forget executor pattern, Slack webhook is SYNC in async context (needs fix)
- Alert triggered in _check_single_monitor (monitor_loop.py) after failed check — re-queries Monitor from DB
- No "recovery" notification (monitor back up) — only downtime alerts

## render_project_page helper
- Located in `dashboard/helpers.py`
- Adds `sidebar_badges` to context via `get_sidebar_badges(project.id, db)`
- All normal renders should use it; error renders use `templates.TemplateResponse` directly (established pattern)

## RBAC Module (MON-54)
- Role model: per-project roles with JSON permissions dict {module: [actions]}
- ProjectMember.role renamed to role_name + new role_id FK to roles table
- CRITICAL: rename role→role_name breaks 11 places in mcp_server.py, 3 in settings.py, 6 in templates, 25+ tests
- Migration creates 3 system roles (Owner/Admin/Member) per project with data migration
- Uses JSON instead of JSONB (inconsistent with rest of project which uses JSONB everywhere)
- FK role_id missing ondelete="SET NULL"
- Role.members relationship missing cascade
- PERMISSION_MODULES tuple in constants.py covers: 500ki, scrum, monitoring, heartbeat, wiki, connections, settings, reports, users
- RoleCreate schema allows is_system=True from API — needs service-layer guard
- RBAC MON-54: iter1=75/100 REQUEST CHANGES (breaking changes blocker, JSON→JSONB, missing ondelete), iter2=88/100 APPROVED (all blockers fixed; low: RoleUpdate missing empty name check, dead test_is_system test)
- RBAC MON-55: iter1=52, iter2=55, iter3=88/100 APPROVED (backend-dev 90, integrator 88, MCP fixer 90, QA tester 92; 83 require_permission calls in 7 dashboard modules, 5 MCP tools migrated, 23 tests passed; minor: dead _get_membership in wiki.py, lint in tests, scalar_one in get_project)
- RBAC MON-56 UI: iter1=52/100 NEEDS WORK (6 endpoints missing, sidebar not impl), iter2=84/100 REQUEST CHANGES (all 6 endpoints present, sidebar implemented; BLOCKER: role_delete uses settings:write instead of settings:delete; medium: N+1 query in roles_list, no submitted_permissions on error; low: cascade delete-orphan on Role.members contradicts business logic)
- Settlements T1 MON-58: 88/100 APPROVED (backend-dev 90, db-specialist 92, qa-tester 85; 4 modele Settlement/SettlementAttachment/SettlementProject/SettlementTicket, migracja 17420ab13509 z data migration JSONB, RBAC rozszerzony o "rozliczenia" jako 10. modul; minor: redundant ix_settlements_number obok UniqueConstraint, brak ondelete na created_by_id/uploaded_by_id zgodny z wzorcem wiki_page, brak CHECK constraint na status — walidacja w service planowana w T3/T6)
- Settlements T2 MON-59: 84/100 APPROVED (backend-dev 82, frontend-dev 88; lista+detail read-only z RBAC require_permission, _get_settlement joinuje SettlementProject po (id,project_id) — cross-project=404; major: przycisk Edytuj enabled bez endpointu MON-60 → 404; medium: cross-project ticket leak — tytuły z innych projektów bez scrum:read przeciekają, Project.is_active nie filtrowane w selectinload(Settlement.projects); minor: int(page) bez try/except, podwójne query ticketów, brak responsive breakpoint na grid-cols-3)
- Settlements T3 MON-60: 88/100 APPROVED (backend-dev 90, frontend-dev 88, qa-tester 82; retry-on-IntegrityError zaimplementowany, walidacja permissions UNION old+new project_ids w update_settlement, draft-only guards, biezacy project musi byc w project_ids, N+1 check_permission w _get_projects_with_write; minor: brak nowych testow CRUD, duplikacja walidacji create/update, settlement.projects przefiltrowane przez and_(Project.is_active) daje 0 iteracji w delete_settlement gdy same nieaktywne projekty — edge case, noqa B006 na Form default list)
- Settlements T4 MON-61: 84/100 APPROVED (backend-dev 82, frontend-dev 90, qa-tester 75; HIGH: regex `[^\w\s\-.]` przepuszcza CRLF — h11 odrzuci 500ka, NIE security CRLF injection ale DoS; MEDIUM: settlement.projects pusta lista omija auth gdy wszystkie projekty nieaktywne, brak walidacji mime_type z client, brak MAX_ATTACHMENTS limit, N+1 check_permission, brak nosniff header; LOW: get_event_loop deprecated, get_attachment sync w async endpoint blokuje event loop, file.read() do RAM 200MB; QA krytyczny brak testow integration dla uploadu/downloadu/delete — tylko smoke testy)
- Settlements T5 MON-62: iter1=74/100 REQUEST CHANGES (2 blockery: SETTLEMENT_STATES kolizja attachment.state/settlement.status + bulk_update_tickets pomija frozen), iter2=90/100 APPROVED (backend-dev 90, frontend-dev 85, qa-tester 92; fix: SETTLEMENT_ATTACHMENT_STATES jako osobna stala draft/signed + SETTLEMENT_STATES zachowane dla settlement.status; bulk_update_tickets ma selectinload+is_ticket_frozen check trafiajacy do failed[]; 5 testow regresji — 4 state validation z DB assertions + 1 bulk frozen guard; minor: docstring services/settlements.py:288 nieaktualny)
- Settlements T6 MON-63: 90/100 APPROVED (backend-dev 92, frontend-dev 90, qa-tester 88; ALLOWED_SETTLEMENT_TRANSITIONS jako Final[dict[str, frozenset[str]]] — immutable O(1) lookup; change_settlement_status z selektywna logika timestampow (draft->sent=sent_at, sent->paid=paid_at+preserve sent_at, sent->draft=clear both, paid->sent=clear paid_at+preserve sent_at); cross-project permission loop w serwisie; endpoint separuje ValueError (flash) od HTTPException (raise); 20 testow PASSED; minor: HTTP 403 dla "brak aktywnych projektow" powinno byc 409/400, flash success pokazuje angielski status zamiast polskiego labelu, dead `except HTTPException: raise`, brak logger.info() audit trail, brakujace testy paid->draft/empty new_status/end-to-end cross-project)
- WorkPlan MON-67: iter1=85/100 (MAJOR Project.is_active, MINORs query/updated_at/notes-clear/response_model/marker), iter2=88/100 APPROVED (wszystkie 6 fix: sentinel _UNSET + model_fields_set, db.refresh attribute_names, 22/22 PASSED; REGRESJA: 2x ruff F401 unused _UNSET import w test_work_plan_service.py:315,329)
- WorkPlan UI MON-68: 54/100 NEEDS WORK (backend 86 APPROVED — _get_user_projects superuser/member, valid_project_uuids security, _safe_parse_date+90d clamp, /api/tickets/search ilike+selectinload+limit 20, ruff+mypy clean; frontend 22 — template 75 linii to szkielet: BRAK frappe-gantt CDN/init, BRAK CSS Grid kalendarza+"+N wiecej", BRAK modala+autocomplete, BRAK on_date_change PATCH, BRAK linka "Plan" w base.html — plik niezmodyfikowany; major: datepicker bg-gray-700 bez dark: prefix; minor: dead code services today_for_user, entries embedded w script ale nieuzywane, q bez strip/escape)
- WorkPlan integration MON-69: 50/100 NEEDS WORK (backend 85 — correlated subquery prawidlowy w backlog+board z user_id filter+today server-side, endpoint partial scrum.py:686 z require_permission scrum:read; minus: brak now_date w backlog kontekscie. frontend 15 — partial stworzony ALE NIE dolaczony do ticket_detail, BRAK badge w backlog+board, POST/DELETE zwracaja JSON nie HTML→psuje hx-swap, form HTMX wysyla form-encoded a endpoint chce JSON body→422, bg-gray-700 bez dark: prefix)
- HTMX gotcha: endpoint z `body: PydanticModel` przyjmuje TYLKO JSON. Form HTMX wysyla form-encoded→422. Fix: `Form(...)` parametry lub `hx-ext='json-enc'`. Endpoint z hx-swap=outerHTML MUSI zwracac HTML fragment, nie JSON.
- Settlements T7 MON-64: 72/100 NEEDS WORK (backend-dev 76, frontend-dev 58; BLOKER CRITICAL: template settlements_global/list.html:57 uzywa name="project_ids" plural, backend settlements_global.py:74 czyta getlist("project_id") singular — filtr projektow kompletnie nie dziala, pagination tez zle generuje; BLOKER HIGH: add_settlement_attachment docstring "state: draft|sent|paid" + "category: faktura|raport|protokol|inne" vs actual SETTLEMENT_ATTACHMENT_STATES={draft,signed} + SETTLEMENT_CATEGORIES={invoice,report,acceptance_protocol,other} — kolizja semantyczna jak MON-62; medium: N+1 check_permission w _get_user_settlement_project_ids, dead code is_empty w else branch linia 185; low: navbar link bezwarunkowy akceptowalne jak Raporty, asyncio.get_event_loop deprecated, int(page) bez try/except, unlink_ticket sprawdza write tylko na biezacym projekcie wzorzec z dashboard; arch OK: routing order settlements_global_router przed settlements_router w __init__.py:28/36, reuse 8 service functions zero duplikacji, frozen regression MON-62 intact w update/delete/bulk_update_tickets z selectinload(settlements), cross-project validation w create/update poprawnie, empty state OK, superuser path OK, 13 MCP tools CRUD+tickets+attachments)

## Shared Constants Anti-Pattern
- Kolizja semantyczna: `SETTLEMENT_STATES` uzywane do walidacji zarowno `Settlement.status` jak i `SettlementAttachment.state` — dwa rozne pola z roznym slownikiem. Rename stalej zlamal walidacje w drugim miejscu. MON-62 iter2 rozwiazane przez rozdzielenie: `SETTLEMENT_STATES` (draft/sent/paid) dla settlement.status, `SETTLEMENT_ATTACHMENT_STATES` (draft/signed) dla attachment.state. Lesson: jesli dwa pola maja ta sama nazwe "state"/"status" ale rozne slowniki — ZAWSZE osobne stale.

## Settlements Module Patterns (MON-58)
- Settlement: globalny number unique (nie per-project), status workflow draft->sent->paid (walidacja w service)
- Association tables settlement_projects/settlement_tickets: composite PK + ondelete CASCADE — auto-prevents duplicates
- SettlementAttachment: category (String 30) + state (String 10) + filename + storage_path + mime_type + size, FK CASCADE
- get_next_settlement_number: func.coalesce(func.max(number), 0)+1 — race condition obsluga przez UNIQUE+IntegrityError retry w service
- RBAC: rozliczenia jako 10. modul, owner=rwd, admin=rw (extends exclude tuple), member=[]
- Wzorzec created_by_id bez ondelete (zgodne z wiki_page.created_by_id) — NO ACTION blokuje usuwanie usera — akceptowalne
- WorkPlan MCP MON-70: 65/100 NEEDS WORK (backend 88, qa 22 BLOKER — 19/19 testow FAIL bo mocky targetuja zle symbole: `work_plan_svc` vs `work_plan_service`, `_get_user_and_project` vs `_auth`, `_resolve_ticket_uuid` vs `_resolve_ticket_globally`, `project_slug=` przekazywany do tooli ktore go nie maja; writer 92 APPROVED)
- Mocking gotcha (MON-70): zawsze najpierw `grep` na nazwe atrybutu w pliku ktory mockujesz. AttributeError w `patch()` to znak ze testy nie byly nigdy uruchomione przed dostarczeniem.
