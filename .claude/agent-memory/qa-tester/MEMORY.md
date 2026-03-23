# QA Tester Memory

## Key memory files
- [patterns.md](patterns.md) — testing patterns, fixtures, common gotchas

## Quick reference
- Test DB: `open_sentry_test`
- All tests run inside Docker: `docker compose exec app python -m pytest ...`
- `EXPECTED_TOOLS` list in `test_mcp_server.py` must be updated when new MCP tools are added
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
