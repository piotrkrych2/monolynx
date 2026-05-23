---
description: Makefile targets must not embed scripts; put logic in monolynx.cli and execute it
globs: "Makefile"
alwaysApply: false
---

# Makefile delegates logic to monolynx.cli

A Makefile recipe must NOT contain program logic. Anything beyond a single shell/CLI command (especially Python via `python -c`) belongs in `src/monolynx/cli.py` as a named command; the Makefile only executes it.

`python -c "..."` with backslash-continued lines is a trap: Make joins the lines, the shell collapses the `\<newline>`, and the whole program ends up on ONE physical line. Any compound statement (`async def`, `for`, `with`, `if`) then raises `SyntaxError: invalid syntax`. Quoting (nested `"`/`'`, f-strings) is fragile on top of that.

## Rule

When a Make target needs more than a trivial one-liner:

1. Add an `async def <name>_cmd()` (no args) to `src/monolynx/cli.py` and register it in the `COMMANDS` dict (key = the CLI subcommand, hyphens allowed).
2. Make the target a single line: `docker compose --profile dev exec app python -m monolynx.cli <command>`.

Keep imports needed only by one command local to that function (lazy import) so the CLI stays cheap to load. The shared `async_session_factory` + `asyncio.run` plumbing already lives in `cli.py` (`main()` runs the coroutine); commands are plain `async def` taking no args, like `createsuperuser`.

## Pitfall

`make backfill-backlinks` / `make backfill-embeddings` originally inlined an `async def` via `python -c` with `\`-continuations. Both collapsed to one line and failed with `SyntaxError` (`async def backfill():     async with ...` on a single line). The bug is invisible until the target is actually run on a server.

Regression: MON-73 - the Wiki backfill targets shipped as inline `python -c` and broke on first prod run. Fixed by moving the logic into `backfill_backlinks_cmd` / `backfill_embeddings_cmd` in `monolynx.cli` and reducing the targets to `python -m monolynx.cli <command>`.

## What to check

- New/edited Make target runs `python -c "..."` (or a multi-line shell script) -> move the body into `src/monolynx/cli.py` as a command and call `python -m monolynx.cli <command>`.
- Python commands run inside Docker: `docker compose --profile dev exec app python -m monolynx.cli ...` (never run Python locally - see CLAUDE.md).
- Need stdin/heredoc as a stopgap on a server that lacks the CLI command yet -> use `python - <<'PY'` with `exec -T` (one-off only; the durable fix is still a `cli.py` command).
