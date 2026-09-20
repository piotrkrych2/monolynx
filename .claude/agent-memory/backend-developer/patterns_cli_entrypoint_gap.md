---
name: patterns-cli-entrypoint-gap
description: adapter module with start()/stop() functions is useless without a CLI subcommand calling it - verify who actually invokes the new function before declaring a ticket done
metadata:
  type: feedback
---

MON-185 (sprint_run_backend.py + sprint_run_tick.py): iteration 1 implemented a full
Claude/Codex session adapter (`start()`, `stop()`, `resume()`, `list_sessions()`,
`find_active_session()`) passing all qa-tester unit tests, and reported the ticket
done. Iteration 2 was spawned only because technical-writer noticed nothing outside
the test suite ever calls `sprint_run_backend.start()` - no CLI subcommand, no shell
wiring. With `MONOLYNX_SPRINT_RUNTIME=codex` set, zero Codex sessions ever actually
started; the acceptance criteria were only true at the library level.

**Why:** unit tests exercising a function directly prove the function's contract,
not that anything in the running system reaches it. A green test suite for an
"adapter" or "service" layer is not evidence of an end-to-end path unless a test
also drives it through the actual entry point (CLI argv, HTTP route, MCP tool).

**How to apply:** when adding a new module that exposes operations meant to be
triggered from a script/shell/CLI (not just imported by other Python), before
reporting the ticket/AC done, grep for the call site: is there a subcommand, route,
or wiring that actually invokes it outside of tests? If not, flag it explicitly
to the requester rather than assuming "tests pass" implies "wired up" - this is
the same class of gap as [[project_db_commit_pitfall]] (flush looks like success,
commit missing) but at the "is this code reachable at all" level instead of the
persistence level.

Design pattern that worked well for the CLI entry point once added: when the
contract requires "zero side effects on failure" (e.g. a preflight check gating a
spawn), run the check that can fail BEFORE any filesystem mkdir/registry read/write
- not after. A preflight-first ordering means an early-return failure path never
needs to distinguish "failed before touching disk" from "failed after touching
disk", which is what a "must hold true even when the registry already exists"
requirement is really asking for.

See also [[patterns_os_kill_self_test_landmine]] for a related MON-185 finding
about the same test suite.
