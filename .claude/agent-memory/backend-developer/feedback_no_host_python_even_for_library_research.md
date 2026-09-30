---
name: feedback-no-host-python-even-for-library-research
description: never run python3 on the host to investigate third-party library behavior (rich, etc.) - CLAUDE.md's "Python only in Docker" rule applies to research diagnostics too, not just app code
metadata:
  type: feedback
---

MON-127 round 3: to root-cause a `rich.errors.MarkupError` crash in a CLI formatter
(`cli/src/monolynx_cli/output.py`), I ran `python3 -c "..."` directly on the host to read
`rich`'s source (`inspect.getsource`) and reproduce the crash/fix with small scripts. The
fix itself was correct, but the Team Manager corrected the *method* in the next round: run
Python only inside the project's Docker toolchain (root CLAUDE.md: "Never run Python
commands locally"), never on the host -- not even for a quick, read-only diagnostic against
a third-party library that has nothing to do with the app's own runtime. Verification must
come from reading library source with the Read/Grep tools and reasoning about it in the
report, not from executing it, when host execution isn't an approved path.

**Why:** the "Docker only" rule is about environment consistency and reproducibility, not
just about touching the app database/state -- a host python3 with a different interpreter/
package set than the pinned environment (confirmed here: `rich==14.3.3` on host vs whatever
`cli/pyproject.toml`'s `rich>=13.0` resolves to in the real toolchain) can silently validate
behavior that doesn't hold in the actual target environment, and it's still an unauthorized
local Python invocation from the agent's seat.

**How to apply:** when investigating a bug in behavior of a dependency (rich, httpx, etc.),
read the installed package's source via the Read tool (found via `site-packages` path from
an error traceback, or by asking the requester where the dependency is vendored/installed in
their toolchain) and reason from it in prose, or ask the Team Manager/requester to run a
one-off diagnostic in the approved Docker environment. Do not `python3 -c` / `python3 script`
on the host, even read-only, even for a library that's not part of `monolynx`'s own backend.
See also [[patterns_cli_typeguard_and_bash_guard_ruff_substring]] for a related MON-127
finding about what a subagent may and may not run via Bash.
