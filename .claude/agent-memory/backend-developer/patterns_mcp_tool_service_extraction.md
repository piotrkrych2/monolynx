---
name: patterns-mcp-tool-service-extraction
description: How to safely extract MCP tool logic (mcp_server.py) into services/*.py without breaking selectinload sets or test patches (MON-137)
metadata:
  type: project
---

Monolynx is mid-refactor moving MCP tool business logic from `src/monolynx/mcp_server.py` into
`services/<module>.py`, so a future REST API v2 (MON-142) can reuse the same service functions.
Pattern established in MON-137 (`services/tickets.py`, ticket/board/sprint/comment/acceptance-criteria
tools) and expected to repeat for other MCP modules.

**Wrapper/service split:**
- MCP tool becomes a thin wrapper: `_get_user_and_project()` -> `async with async_session_factory() as db:` -> call ONE service function with `db` passed in -> format/return. The wrapper never opens a second session or does its own queries for logic that belongs to the service.
- Service function signature: `async def <name>(db: AsyncSession, user: User, project: Project, ...) -> <ORM model | list[ORM] | @dataclass>`. Never return bare `dict`/`list[dict]` as the *top-level* type — a dataclass field CAN hold `list[dict[str, Any]]` when the dict-shaping itself is business logic (e.g. `search_tickets`'s permission-based settlement redaction in `_ticket_row`/`_settlement_dict`) that the ticket explicitly says to move into the service.
- Errors: `raise ValueError("<verbatim original message>")` — copy the exact Polish string, tests use `pytest.raises(match=...)`.
- Commit stays in the service (`.claude/rules/db-commit.md`); wrapper never commits.

**Helper functions like `_resolve_ticket_uuid(ticket_id, project_id)`:**
Original version opened its OWN session (`async_session_factory()`). When moving to a service, change
the signature to accept `db: AsyncSession` as a parameter (`resolve_ticket_uuid(ticket_id, project_id, db)`)
and call it *inside* the caller's already-open `async with` block. For MCP tools outside your ticket's
scope that also call the old helper (e.g. `add_attachment`, `log_time`, `get_attachment`, `create_pipeline`
in MON-137), the correct minimal change is: swap the call to the new service function, and if the call
used to happen *before* `async with async_session_factory()`, physically move it *inside* the block —
do not touch anything else in that tool.

**Verify against ground truth, not memory of an earlier Read:** when a large file is read in multiple
chunks (`offset`/`limit`) across several tool calls, don't trust your recollection of where one chunk
ended and the next began — line numbers can be off by one screen. Before writing the service file,
grep the target function names in the *current* file and re-read each one fully in one shot to build
the actual field/selectinload list. MON-137 caught a real bug this way: the first draft of `get_ticket`
in `services/tickets.py` was missing `selectinload(Ticket.labels)`, which `_format_ticket_detail`
reads for the "Labels: ..." line — would have caused `MissingGreenlet` in production (test fixtures'
identity map can hide this). Confirmed only by re-reading the ground-truth `mcp_server.py` function
body end-to-end before finishing.

**Test compatibility check before editing:** grep `tests/` for `monolynx.mcp_server\.<name>` and
`from monolynx.mcp_server import <name>` for every helper you plan to delete/rename. In MON-137 the
only patches that mattered were `monolynx.mcp_server.async_session_factory` and
`monolynx.mcp_server.verify_mcp_token` (the `mock_factory`/`mock_verify` fixtures) — both untouched
since the wrapper still opens the session via that name. Private helpers (`_resolve_ticket_uuid`,
`_TICKET_KEY_RE`, `_can_read_settlements`) had zero test references, so they were safe to delete
entirely rather than keep as re-exports.

**Unused imports after extraction:** moving a tool's body into a service leaves the old
`mcp_server.py` imports dangling (models like `TicketComment`, `TicketAcceptanceCriterion`, constants
like `TICKET_STATUSES`/`BOARD_STATUSES`, service helpers like `is_ticket_frozen`). Don't guess —
`docker compose -p monolynx --profile dev run --rm --no-deps -v <worktree>:/app app ruff check --no-fix <files>`
is the allowed read-only lint call for subagents and reliably flags exactly which imports became
unused (F401) or which remained genuinely in use by an out-of-scope tool in the same file.

**Side effects the tests patch on the wrapper module (e.g. `send_invitation_email`) stay in the wrapper:**
When a tool's test suite does `patch("monolynx.mcp_server.send_invitation_email")`, that side effect must still be
called from `mcp_server.py`, not from the service — patching a name in one module never affects a different
module's own reference to the same underlying function. Confirmed in MON-137's `invite_member`: the service
(`services/projects.py`) does the DB write and returns a small discriminated dataclass result
(`InviteMemberResult(email, role, is_new_user, invitation_token=None, expires_at=None)`); the wrapper checks
`is_new_user` and calls `send_invitation_email(...)` itself only in that branch. This "service returns a
discriminated result, wrapper performs the patched side effect conditionally" shape is reusable for any other
create-or-invite flow with an optional email/notification step.

Related: [[patterns_edit_tool]], [[feedback_storage_calls_need_exception_mapping]].
