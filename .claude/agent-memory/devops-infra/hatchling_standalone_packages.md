---
name: hatchling-standalone-packages
description: Wzorzec build-backend dla samodzielnych, publikowalnych paczek w monorepo Monolynx (sdk/, cli/) to hatchling, nie setuptools
metadata:
  type: project
---

Root `pyproject.toml` (pakiet backendu `monolynx`) uzywa `setuptools>=68.0` + `setuptools-scm`. Ale `sdk/pyproject.toml` (pakiet `monolynx_sdk`, standalone Django middleware) uzywa `hatchling` (`[build-system] requires = ["hatchling"]`). Ticket MON-129 ("CLI: szkielet paczki cli/") ustalil, ze `cli/pyproject.toml` (pakiet `monolynx-cli`) idzie tym samym wzorcem co `sdk/` - hatchling, nie setuptools.

**Why:** samodzielne paczki instalowalne bez repo backendu (SDK dla klientow Django, CLI dla uzytkownikow koncowych) sa w tym repo swiadomie odseparowane od build-systemu backendu; hatchling jest lzejszy do takich malych, niezaleznie wersjonowanych paczek.

**How to apply:** przy kazdym nowym tickecie/zmianie dotyczacej `cli/pyproject.toml` lub `sdk/pyproject.toml` (wersjonowanie, dependency management, publikacja) zakladaj hatchling jako build-backend, NIE kopiuj wzorca setuptools+setuptools-scm z roota. Dynamiczne wersjonowanie z pliku `version.py` w takich paczkach robi sie przez `[tool.hatch.version] path = "..."`, nie przez setuptools-scm (git tags) ani `[tool.setuptools.dynamic]`. Zobacz [[waf-mcp-ticket-yaml-block]] dla kontekstu sprintu "CLI monolynx - fundament + REST API v2" w ktorym to ustalono (MON-129, MON-136).
