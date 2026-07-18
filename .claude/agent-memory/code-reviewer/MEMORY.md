# Code Reviewer Memory

## Topic files
- [plugin_packaging.md](plugin_packaging.md) — Claude Code plugin (MON-72): structure, `claude plugin validate`, em-dash scope gap, `${user_config.X}`-in-bash bug. MON-72: 88/100 APPROVED.
- [migration_head_verify.md](migration_head_verify.md) — Alembic review: zawsze sam licz head + sprawdz unikalnosc revision ID, nie wierz ticketowi (MON-73 blocker).
- [review_history.md](review_history.md) — Historia ocen ticketow MON-* (score/iteracje/znaleziska per ticket). MON-84 pokrycie scrum.py: 92/100 APPROVED.
- [wiki_llm_method.md](wiki_llm_method.md) — Metoda LLM Wiki (MON-73): sygnatury wiki/wiki_lint/wiki_bootstrap, marker sprzecznosci regex, RESERVED_SLUGS, patch MinIO gotcha (wiki_lint ma wlasny import get_markdown).
- [graphify_sync.md](graphify_sync.md) — Graf graphify->Monolynx (MON-115/116/118): kontrakt replace_graph (pola/typy/limity), mapowanie taksonomii, transport MCP wzorzec, em-dash pitfall w SKILL.md. MON-118: 87/100 APPROVED.

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

## MCP Transport Auth (MON-103)
- `_MCPBearerAuthMiddleware` (plain ASGI, owija `mcp.streamable_http_app()`) + `build_mcp_http_app()` w mcp_server.py. main.py mountuje TEN SAM owiniety obiekt pod `/mcp` ORAZ `/` (root mount dla Claude Desktop na APP_URL). Oba chronione, bo to ta sama instancja.
- Bramka reuzywa `_verify_token` (OAuth-first via verify_oauth_access_token, fallback verify_mcp_token dla osk_*). Per-tool `_auth(ctx)` zostaje jako defense-in-depth.
- OAuth router (api/oauth.py, APIRouter bez prefiksu, include_router) ma priorytet nad mount `/` -> .well-known/register/authorize/token publiczne. Backend swiadomie NIE uzyl FastMCP(auth=) by uniknac podwojnego .well-known pod /mcp.
- KRYTYCZNA PULAPKA TESTOWA: pod ASGITransport BEZ lifespan (conftest `client` fixture) MCP session manager nie jest zainicjalizowany -> request `/mcp` moze dac 404 ZANIM dotrze do middleware. Testy 401 staja sie FLAKY/order-dependent: wczesniejszy test (np. discovery albo patch _verify_token) inicjalizuje stan i zmienia 404->401. ZAWSZE weryfikuj bramke MCP pod TestClient (lifespan ON) trace'ujac `_MCPBearerAuthMiddleware.__call__` (MW_HITS) + sprawdzajac `/mcp` ORAZ `/mcp/` (trailing slash). Pod poprawnym kodem+lifespan: 401 z WWW-Authenticate:Bearer dla obu.
- Weryfikacja regresji: usun middleware (build->streamable_http_app) i potwierdz ze test failuje (404) -> dowodzi ze gate ON/OFF jest rozrozniany, test nie jest falszywie zielony.

## PULAPKA: coverage (--cov) crashuje w kontenerze app (numpy double-load)
- `pytest --cov=...` ORAZ `coverage run` w kontenerze `app` daja `ImportError: numpy cannot load module more than once per process` (instrumentacja coverage pre-importuje modul -> lancuch modeli -> pgvector -> numpy 2.5.1, potem conftest importuje numpy ponownie). `COVERAGE_CORE=sysmon` nie pomaga; pre-import numpy przez sitecustomize -> SEGFAULT. Dotyczy KAZDEGO uruchomienia z coverage. Plain `python -c "import numpy"` dziala (2.5.1), wiec problem tylko pod coverage tracing.
- Skutek: nie da sie niezaleznie zreprodukowac % coverage w tym env. Weryfikuj JAKOSCIOWO (czy testy trafiaja docelowe endpointy, mock patch-targety, weryfikacja stanu DB) + przebieg testow BEZ --cov. Liczbe z raportu qa akceptuj z zastrzezeniem.

## PULAPKA: nie oceniaj NIEDOKONCZONEJ wersji pliku agenta (monitor stabilnosci)
- Monitor "plik stabilny przez 60s" MOZE odpalic w czasie dluzszej pauzy agenta miedzy zapisami. Testowanie takiej polowicznej wersji dalo FALSZYWE 24 failures (niekompletny plik). Finalna wersja (wiecej testow) byla zielona (162 passed).
- Zanim orzekniesz regresje z liczby failures: potwierdz ze plik to FINALNA praca (dluzsze okno stabilnosci ~40-60s + porownaj liczbe testow/hash + `git diff --stat`), a jesli podejrzewasz cross-file pollution - uruchom plik SOLO vs w kombinacji, i baseline (stash) vs z praca agenta.
- test_scrum session-scope teardown flake: `DROP TABLE work_plan_entries` DBAPIError na OSTATNIM tescie przy uruchomieniu wielu plikow scrum razem (220 passed, 1 error) - pre-existing, nie wina zmian testowych.
- POTWIERDZONE (MON-85): `DROP TABLE work_plan_entries` DeadlockDetectedError w session-scope teardownie pochodzi od PRE-EXISTING `test_e2e_work_plan.py::test_mcp_schedule_ticket_persists_to_separate_session` (osobny `async_sessionmaker(engine)` + realne commity/delete na work_plan_entries). Uruchomienie SAMYCH 2 pre-existing testow (cross_project + persists_to_separate_session) juz daje `2 passed, 1 error`. Przy ocenie nowych testow work_plan: ten teardown error to NIE regresja - izoluj uruchamiajac same pre-existing testy.

## PULAPKA: git checkout kasuje prace agentow z working tree
- Agenci w pipeline czesto zostawiaja zmiany NIEZACOMMITOWANE (tylko working tree). `git checkout <plik>` (np. przy cofaniu wlasnego sanity-sed) PRZYWRACA plik do HEAD i KASUJE prace agenta. Objaw: `isinstance` mountowanej app nie zgadza sie z definicja w diffie. Zawsze: przed `git checkout` zrob kopie, albo uzyj `git stash`/odtworz zmiane recznie. Po jakiejkolwiek manipulacji plikami docelowymi - `git diff --stat` musi zgadzac sie z oryginalnym diffem pracy agentow.

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
