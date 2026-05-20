---
description: Write operations (INSERT/UPDATE/DELETE) must call db.commit()
globs: "**/*.py"
alwaysApply: false
---

# Database writes require db.commit()

`get_db()` (FastAPI dependency) and `async_session_factory()` (MCP tools, worker) do **NOT** auto-commit. Both only `yield session` / open a context manager with no commit on exit. Once the block exits, an uncommitted transaction is rolled back.

`await db.flush()` sends SQL to the database within the transaction and returns generated IDs (e.g. `entry.id`), but does **NOT** persist the data. Flush without commit means the data is gone once the session closes.

## Rule

Every write operation (`db.add()`, `db.delete()`, mutating a session-tracked object's attributes) must be finalized with `await db.commit()` before the session closes.

Project convention: commit in the service layer (`services/*.py`, e.g. `services/sprint.py`) or in the router handler after calling the service (`dashboard/scrum.py`). Pick one place and stick to it within a module to avoid double commits.

## Pitfall

`flush()` returns a valid ID, so the endpoint / MCP tool **looks successful** (returns `entry_id`), while the data never actually exists. Tests using `conftest.py` (one transaction per test with rollback) will **NOT** catch a missing commit, because everything lives inside a single transaction until the test ends. The bug only surfaces in real usage (UI/MCP) as empty lists despite successful-looking writes.

Regression: MON-71 — the Work Plan module only `flush()`-ed in the service layer; neither the router (`get_db`) nor the MCP tools (`async_session_factory`) committed. `schedule_ticket` returned an `entry_id`, but `list_work_plan` came back empty.

## What to check

- Service does `db.add()`/`db.delete()`/mutation + only `db.flush()` -> add `db.commit()` (in the service or the caller).
- MCP tool with `async with async_session_factory() as db:` performing a write -> `await db.commit()` before leaving the block.
- Router handler calling a service that does not commit -> `await db.commit()` after the service call.
