---
name: patterns-test-session-rollback-safety
description: Why calling db.rollback() (but not db.commit()) inside production code is safe on the shared conftest.py db_session fixture - SQLAlchemy join_transaction_mode="rollback_only" internals (MON-140)
metadata:
  type: reference
---

`tests/conftest.py::db_session` binds an `AsyncSession` directly to a `Connection` that already has
an externally-started transaction (`transaction = await connection.begin()`), then rolls that single
transaction back at teardown. This is SQLAlchemy's documented "join a Session into an external
transaction for test suites" recipe.

Because the connection is already `in_transaction()` and it's not a nested/savepoint transaction,
`Session.join_transaction_mode` (default `"conditional_savepoint"`) resolves to `"rollback_only"` at
first use (`SessionTransaction._connection_for_bind` in `sqlalchemy/orm/session.py`). Under
`"rollback_only"`:
- `should_commit = False` is stored for that connection. `Session.commit()` therefore SKIPS the real
  `trans.commit()` call entirely - it behaves like a flush and never actually ends the shared test
  transaction. This is why the MCP unit-test fixture `mock_factory` (`tests/unit/test_mcp_server.py`)
  patches `db_session.commit` to flush - defensive, but not strictly required for the rollback-safety
  reasoning below.
- `Session.rollback()` has NO equivalent guard - `SessionTransaction.rollback()` unconditionally calls
  `t[1].rollback()` on every connection's real transaction object. So a production code path that calls
  `await db.rollback()` on this shared session (e.g. `services/mcp_auth.py::verify_bearer_token`
  rolling back after a failed OAuth lookup, MON-140) DOES roll back the real, shared test transaction -
  discarding any not-yet-committed fixture data flushed earlier in the same test.
- This double-rollback (once from application code, once from the fixture's own teardown
  `await transaction.rollback()`) is still SAFE: `RootTransaction._close_impl` in
  `sqlalchemy/engine/base.py` guards with `if self.is_active: ...` before issuing the real DB
  rollback, so the second call is a no-op, no exception.
- Consequence for assertions: any test that reads DB state (a query on the same session) AFTER a
  mid-test rollback will find fixture rows gone (transaction really did unwind). But comparing an
  already-loaded Python attribute (e.g. `user.id` on an object created via `db_session.add()` +
  `flush()`, with `expire_on_commit=False` as this fixture sets) is unaffected - the attribute value
  lives in the object's `__dict__` regardless of the DB row's fate.

Verified by reading the actual installed SQLAlchemy 2.0.48/2.11 source (`Session.rollback`,
`SessionTransaction.rollback`/`.commit`, `RootTransaction._close_impl`), not from memory of older
SQLAlchemy versions - the "join external transaction" machinery changed significantly across 1.4/2.0
and pydantic-generation-adjacent doc pages are easy to misquote. Before asserting "rollback on the
shared test session is safe" in a future ticket, re-verify against the SQLAlchemy version actually
pinned (`pyproject.toml: sqlalchemy[asyncio]>=2.0`) rather than citing this note blindly - the analysis
holds for 2.0.x/2.11.x's `join_transaction_mode` implementation, which could change in a future major
version bump.
