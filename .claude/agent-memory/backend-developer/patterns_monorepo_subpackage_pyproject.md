---
name: patterns-monorepo-subpackage-pyproject
description: Adding a new installable sub-package (cli/, sdk/) to the monorepo - Ruff config inheritance and pytest --strict-markers gotcha
metadata:
  type: project
---

Monolynx is a monorepo with independent installable packages (`sdk/`, and since MON-129 `cli/`) that each get their own `pyproject.toml` but share the root's tooling where possible.

**Ruff config discovery is hierarchical, not merged.** Ruff picks the *nearest ancestor* `pyproject.toml`/`ruff.toml` containing a `[tool.ruff]` table for each file it lints - it does NOT merge settings from multiple config files up the tree. So if a sub-package's `pyproject.toml` (e.g. `cli/pyproject.toml`) adds its own `[tool.ruff]` section, root's `select`/`line-length`/`isort` settings stop applying to that sub-package entirely. To keep a new sub-package under the root's lint rules (as instructed for MON-129's `cli/pyproject.toml`), simply omit `[tool.ruff]` from the sub-package's `pyproject.toml` - Ruff will keep walking up and find root's config.

**Why:** ticket MON-129 explicitly required `cli/*.py` to be linted with root's `select = [E,W,F,I,N,UP,B,SIM,RUF]` and `line-length = 150` via pre-commit, without duplicating that config in `cli/pyproject.toml`.

**How to apply:** when scaffolding a new sub-package's `pyproject.toml` under this repo, never add a `[tool.ruff]` table unless the sub-package genuinely needs different lint rules than root - adding one silently opts it out of root's config.

**pytest `--strict-markers` gotcha:** if `[tool.pytest.ini_options] addopts = "--strict-markers"` is set and any test file in that package uses `pytestmark = pytest.mark.<name>` or `@pytest.mark.<name>`, that marker MUST be registered in the same `markers = [...]` list, or the entire test file errors out at collection ("marker not found"). The root pyproject.toml registers `unit`/`integration`/`e2e`; a new sub-package's own `pyproject.toml` needs its own `markers` list covering whatever its tests actually use - check the test files first, don't just copy the ticket's literal wording (a ticket describing pyproject requirements may only mention `e2e` while the qa-tester's test file already uses `unit`).

**Regression avoided:** MON-129 - `cli/tests/test_main.py` had `pytestmark = pytest.mark.unit` from the start (TDD, tests written before implementation); ticket text for `cli/pyproject.toml` only mentioned registering `e2e`. Registered `unit` too after reading the test file, otherwise `--strict-markers` would have failed the whole file. See [[patterns_partial_unique_index_no_project_scope]] for another "read the actual constraint/test before trusting the ticket's summary" case.

**Subagent lint hook blocks even explicitly-permitted lint commands.** The ticket said I could run `ruff check --no-fix` / `ruff format --check` read-only on my assigned files, but `bash_guard.py` still denied it for a subagent ("Subagent nie odpala lintu/testow - robi to Team Manager"), regardless of the ticket's wording. Don't try to work around it (e.g. via `docker run` without compose, or other phrasing) - report unverified-by-lint status in the final report instead.
