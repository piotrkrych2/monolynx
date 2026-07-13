---
name: module-patterns
description: Wzorce per modul Monolynx (500ki, Label, Dark/Light, Wiki Attachment, Project Stats, Notifications, RBAC, Settlements, render_project_page) obserwowane w review
metadata:
  type: project
---

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
- Ticket spec_page_id MON-76: 92/100 APPROVED (backend-dev 92, db-specialist 95; model kolumna FK wiki_pages ondelete=SET NULL nullable index + relacja spec_page foreign_keys+lazy=selectin bez back_populates wzor jak issue; schemas TicketCreate/Update; mcp create_ticket param+commit+response, get_ticket via _format_ticket_detail Spec line; migracja b8e3f1a2c4d9 down_revision f2a3b4c5d6e7 = realny single head potwierdzony, ID unikalny, downgrade kompletny; medium: brak walidacji ze spec_page nalezy do tego projektu — cross-project FK mozliwy ALE zgodny z istniejacym wzorcem sprint_id; low: get_ticket nie ma jawnego selectinload(spec_page) — polega na model-level lazy=selectin, dziala bo selectin loaduje przy execute, niespojne z reszta jawnie ladowanych relacji)
- ALEBMIC HEAD COMPUTE GOTCHA: prosta heurystyka comm -23 revs downs DAJE FALSE POSITIVE na merge migracjach (down_revision to KROTKA np `(7bb77af09979, g7b8c9d0e1f2)`). Trzeba rozbic krotke: `tr -d "()'\" " | tr ',' '\n'` przed sort/comm. Bez tego merge revision wyglada jak head.
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
- pokrycie wiki MON-82: 90/100 APPROVED (qa-tester +855 lin integration / +584 unit, 15 nowych klas). Pokryte: page attachments upload/serve/delete (auth/404/sanitize/size/minio-err/happy+DB), wiki files 5 endpointów (wszystkie gałęzie), page-detail backlinks gating (enabled panel + disabled get_backlinks assert_not_called), extract_wiki_links 19 wariantów, sync_backlinks (slug/uuid/self-skip/anchor/unresolved/empty-delete), RESERVED_SLUGS ValueError. Mocki: dashboard.wiki.minio_* + services.wiki.get_markdown (bo get_page_content woła module-level get_markdown) + dashboard.wiki.get_backlinks - wszystkie poprawny namespace. RBAC: login_session domyślnie is_superuser=True -> bypass require_permission, więc delete-success testy przechodzą mimo że member nie ma wiki:delete; ProjectMember(role=member) dekoracyjny, egzekwowanie 403 poza scope (permissions.py). DROBNE: (1) test_upload_empty_safe_filename_defaults_to_file - nazwa "!!!.txt" sanityzuje do "___.txt" NIE pustej (`_` jest w \w regex [^\w\s\-.]), więc gałąź if not safe_filename:="file" NIE trafiona = fałszywe pokrycie; (2) assert "—" in resp.text or "—" in resp.text - tautologia copy-paste. LEKCJA: przy sanityzacji nazw `[^\w\s\-.]`->`_` wynik nigdy nie jest pusty dla znaków ASCII-special (->_), tylko whitespace-only daje pusty po strip - testy "empty default" często nie trafiają tej gałęzi.
