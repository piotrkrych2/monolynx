# QA Tester Memory

## Key memory files
- [patterns.md](patterns.md) — testing patterns, fixtures, common gotchas

## Quick reference
- Test DB: `open_sentry_test`
- All tests run inside Docker: `docker compose exec app python -m pytest ...`
- `EXPECTED_TOOLS` list in `test_mcp_server.py` must be updated when new MCP tools are added (currently 115 tools after Pipelines module: +8 tools: create_pipeline, create_pipeline_job, update_pipeline_job, finish_pipeline, list_pipelines, get_pipeline, get_pipeline_job_log, append_job_log)
- `mock_factory` fixture replaces `commit()` with `flush()` — critical for test isolation with outer transaction rollback
- Always patch `monolynx.mcp_server._is_url_safe` (return_value=None) for happy-path monitor tests to bypass SSRF check
- For tools using `_get_user_member_and_project`: patch both `async_session_factory` AND `verify_mcp_token` — the helper calls both internally
- When testing `invite_member` permission checks (member role): create a separate `AsyncMock(return_value=regular_user)` — don't reuse `mock_verify` which returns the owner
- `send_invitation_email` import path: `monolynx.mcp_server.send_invitation_email` — patch there, not in `services.email`
- Neo4j driver mocking: patch `monolynx.services.graph._driver`, configure `__aenter__`/`__aexit__` on `driver.session.return_value.__aenter__ = AsyncMock(return_value=session)`
- Empty dict `{}` is falsy — `depth_map={}` triggers backwards-compatible path in `_format_graph_dsl` (no Depth headers)
- `_format_graph_dsl` importable directly: `from monolynx.mcp_server import _format_graph_dsl`
- Async iteration mocking for Neo4j: define class with `__aiter__` returning self, `async __anext__` raising StopAsyncIteration; assign `result_mock.__aiter__ = lambda self: AsyncIterEmpty()`
- `login_session()` always creates a new user — never call it for an existing user (UniqueViolationError on email). Instead create user manually + use `_login_existing_user(client, email)` helper that only calls POST /auth/login
- For endpoint tests: create User with `is_superuser=True` manually + `await db_session.flush()` + `await _login_existing_user(client, email)` — never use `login_session` when user already exists in session
- `sms_client._send_sms_sync`: patch `monolynx.services.sms_client.urllib.request.urlopen`; set `mock_resp.status = 200` to avoid `%d` format error in logger.info
- `notifications.send_monitor_alert(monitor, check, db)` — 3 args (not 2!); config uses lists: `email_recipients`, `sms_recipients`, `slack_channels`; patch email/SMS at origin (`monolynx.services.email.send_email`, `monolynx.services.sms_client.send_sms`); Slack via `monolynx.services.notifications._send_slack_webhook_sync`
- HTMX PATCH `/status` endpoint expects JSON body (`json={"status": "..."}` in httpx), NOT form data — endpoint calls `await request.json()`
- Settlements RBAC: `member` role has `rozliczenia: []` (no access); `owner` and `admin` have `rozliczenia: ["read", "write"]`; superuser bypasses all checks
- Settlement M2M setup in tests: create Settlement + SettlementProject manually; for linking ticket use ORM `settlement.tickets.append(ticket)` after `selectinload(Settlement.tickets)`
- `_login_existing_user(client, email)` pattern confirmed as canonical approach — reused in `test_settlements_scrum_integration.py`
- 500ki issue list (MON-65): default filter is `unresolved` — tests using `?status=all` to see all statuses; empty state with default filter shows "Brak issues w wybranym statusie", NOT "Brak błędów" (that appears only with `?status=all`); test_sentry_issues.py and test_sentry_coverage.py were updated with `?status=resolved` / `?status=ignored` accordingly
- work_plan MCP tools (MON-70): patch `monolynx.mcp_server._auth` (not `_get_user_and_project`), `monolynx.mcp_server.work_plan_service` (not `work_plan_svc`), `monolynx.mcp_server._resolve_ticket_globally` (not `_resolve_ticket_uuid`); tools have no `project_slug` param (schedule/update/delete/get_ticket_schedule); `list_work_plan` and `get_today_tasks` have optional `project_slug`; also patch `WorkPlanEntryResponse` to control `.model_dump()` output; `EXPECTED_TOOLS` in test_mcp_server.py needs +6 entries when tools are released
- Wiki LLM (MON-73): MinIO mock pattern `@patch("monolynx.services.wiki.upload_markdown")` + `@patch("monolynx.services.wiki.get_markdown")`; dla bootstrap minio_store dict (path->content) + `side_effect=lambda path: minio_store.get(path, default)` spójnie symuluje MinIO; `_find_dead_links` i `_find_contradictions` to synchroniczne `def` (nie async) - test bez `await`; `is_wiki_llm_enabled` przyjmuje Any z `wiki_llm_enabled`, SimpleNamespace działa bez type: ignore (mypy OK)
- Wiki LLM MCP tools (MON-73 Warstwa 5): `lint_wiki` czyta MinIO przez `monolynx.services.minio_client.get_markdown` (bezpośredni import w wiki_lint.py) - patchuj TAM nie w services.wiki; `mcp_member` rola `owner` ma settings:write przez DEFAULT_ROLE_PERMISSIONS - nie trzeba patchowac check_permission; EXPECTED_TOOLS ma 107 narzędzi po dodaniu 7 wiki LLM tools
- Ruff I001 w testach: kolejność importów wewnątrz metod - third-party (sqlalchemy) przed first-party (monolynx), bez pustej linii między nimi (albo z pustą linią tylko między sekcjami stdlib/third-party/first-party)
- `delete_role` z member przypisanym: ustawienie `mcp_member.role_id = role.id` gdzie role.permissions={} sprawia że check_permission zwraca False (RBAC zamiast legacy). Obejście: patch `monolynx.mcp_server.check_permission` AsyncMock(return_value=True)
- Format helpers importowalne bezpośrednio: `_build_allowed_hosts`, `_interval_human`, `_format_board`, `_format_ticket_detail`, `_format_monitors_table`, `_resolve_ticket_globally` - wszystkie z `monolynx.mcp_server`
- `update_settlement` MCP: `check_permission` patchowalny przez `monolynx.mcp_server.check_permission`; `_get_settlement_for_mcp` wymaga zarówno Settlement jak i SettlementProject (M2M) w DB + selectinload(Settlement.created_by)
- Pipelines dashboard tests (MON-96): twórz Pipeline/PipelineStep/PipelineJob bezpośrednio przez ORM + flush (nie przez svc.create_pipeline które commituje) — zachowuje outer transaction rollback; append_job_log wymaga patch upload_markdown + get_markdown + sync_backlinks + update_page_embeddings
- `test_embeddings_service.py` 2 testy red (pre-existing): test_empty_content i test_success oczekują 1x execute, ale `exclude_from_embeddings` check (MON-90) dodaje dodatkowe execute — nie naprawiać bez potwierdzenia
