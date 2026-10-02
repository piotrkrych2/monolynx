---
name: venv-realpath-and-no-decision-tests
description: Dwie pułapki z MON-232 - realpath interpretera w venv gubi instalator, a test "hook nie podejmuje decyzji" bez frazy ryzykownej nie zabija mutanta
metadata:
  type: feedback
---

1. Wykrywanie instalatora CLI (brew/pipx/uv) po `os.path.realpath(sys.executable)` jest błędne: `bin/python` w venv to symlink do interpretera BAZOWEGO, więc realpath wychodzi poza venv i gubi `/pipx/venvs/` oraz `/uv/tools/`. Poprawnie: `sys.prefix` bez realpath albo realpath skryptu wejściowego (binarki `monolynx`).

**Why:** MON-232, `cli/src/monolynx_cli/commands/version.py` - testy używały nieistniejących ścieżek-literałów, na których realpath nic nie zmienia, więc były zielone. Dla brew wynik wychodził poprawny przypadkiem (bazowy Python też leży w `/Cellar/`).

**How to apply:** przy każdym kodzie klasyfikującym ścieżkę po realpath sprawdź na dysku realny układ symlinków (`ls -l` w `libexec/bin`), a od testów wymagaj symlinku w `tmp_path`, nie literału.

2. Test "komenda X nie dostaje wyjątku w hooku" (asercja: brak decyzji / stare reguły) musi nieść w argumencie frazę blokowaną przez stare reguły (np. `"git push origin main"`) i asertować deny/ask. Bez niej przechodzi także po usunięciu gałęzi, którą ma chronić.

**Why:** MON-232, `tests/unit/test_bash_guard.py` - przypadki `auth login`, `config set`, `--yes` bez frazy ryzykownej; mutant usunięcia `_MONOLYNX_RISKY_WORDS` przeżywał. Kod był szczelny, potwierdziła to dopiero własna sonda hooka.

**How to apply:** przy review hooka napisz sondę (payload JSON na stdin, `agent_id` ustawione) z frazą ryzykowną w każdym wariancie; patrz też [[bash_guard]] i [[review_method]].
