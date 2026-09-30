---
name: patterns-tomllib-tomli-ruff-up036
description: sys.version_info >= (3, 11) tomllib/tomli fallback triggers ruff UP036 false positive under root's target-version; needs noqa, not removal (MON-125)
metadata:
  type: project
---

`cli/` (monolynx-cli) declares `requires-python = ">=3.10"` in its own
`cli/pyproject.toml`, but per [[patterns_monorepo_subpackage_pyproject]] it
inherits ruff config from the repo root (no `[tool.ruff]` in `cli/pyproject.toml`).
Root's `target-version` is set for the backend package (higher than 3.10), so
ruff's `UP036` ("outdated-version-block") flags the standard compat shim

```python
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
```

as dead code, even though cli/ genuinely needs it (tomllib is stdlib only from
3.11+, and MON-125's interface.md mandates exactly this construct literally).

**Why:** ruff resolves target-version from the nearest ancestor config it uses
for linting (root), not from the sub-package's own `requires-python` - the same
asymmetry as the Ruff config-inheritance gotcha, just showing up as a false
positive instead of a missing rule.

**How to apply:** when a `cli/` module needs a `sys.version_info` branch to
support Python 3.10 (older than what root's target-version implies), keep the
branch and suppress with `# noqa: UP036` plus an inline comment stating why
(cli supports 3.10, stdlib lacks tomllib there) - do not delete the branch or
the `import tomli as tomllib` fallback just to satisfy ruff, that breaks 3.10
support which Team Manager pytest verifies separately with a 3.10 runtime.

See also [[patterns_monorepo_subpackage_pyproject]] (same root cause: ruff
config resolution not matching a sub-package's actual constraints).
