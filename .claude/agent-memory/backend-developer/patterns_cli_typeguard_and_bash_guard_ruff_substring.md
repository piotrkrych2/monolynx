---
name: patterns-cli-typeguard-and-bash-guard-ruff-substring
description: TypeGuard for mypy --strict narrowing on isinstance+dict.get compound checks; bash_guard blocks any Bash command containing the substring "ruff", even a plain grep/sed with no lint intent
metadata:
  type: patterns
---

MON-127 (cli/src/monolynx_cli/output.py): a helper like
`isinstance(data, dict) and isinstance(data.get("items"), list)` narrows correctly
inside a single boolean expression under mypy, but returning that as a plain `bool`
from a helper function loses the narrowing for callers. Declare the helper's return
type as `typing.TypeGuard[dict[str, Any]]` (stdlib since Python 3.10, matches this
project's `requires-python = ">=3.10"`) so that `if _is_envelope(data): data["items"]`
type-checks without a redundant `isinstance` at the call site. Caveat: TypeGuard only
narrows the *positive* branch (unlike PEP 742 `TypeIs`) — code after an
`if _is_envelope(data): ... return` still needs a real `isinstance(data, dict)` check
before treating `data` as anything narrower than its original declared union.

Separately: the plugin's `bash_guard.py` PreToolUse hook denied a plain
`sed -n '/\[tool.ruff\]/,/^\[tool\./p' pyproject.toml` (read-only, no ruff execution)
with "Subagent nie odpala lintu/testow" — the guard appears to match on the substring
"ruff" anywhere in the command line, not just on an actual `ruff` invocation. The
ticket's read-only lint allowance (MON-176/MON-207, see root CLAUDE.md) only covers
`ruff check --no-fix` / `ruff format --check|--diff` run **through
`docker compose exec [-T] app`**, not arbitrary host-side commands that merely mention
"ruff" in a string/pattern.

**How to apply:** when told "you may only run read-only ruff check/format" as a
subagent, don't try to inspect ruff config or run ruff-adjacent commands (grep for
`[tool.ruff]`, `sed`, etc.) via Bash at all — read the config file with the Read tool
instead, and only invoke actual `ruff` through the exact
`docker compose exec [-T] app ruff check --no-fix ...` / `... ruff format --check ...`
form. If that's unavailable (no Docker, hook still denies), skip verification and say
so plainly in the report rather than working around the guard.
