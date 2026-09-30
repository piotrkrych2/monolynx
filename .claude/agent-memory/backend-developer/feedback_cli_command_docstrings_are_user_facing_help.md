---
name: feedback-cli-command-docstrings-are-user-facing-help
description: a Typer @app.command function's docstring becomes --help text (shown to users), so it needs full Polish diacritics like main.py, not the ASCII-only style of conftest.py (MON-125)
metadata:
  type: feedback
---

In `cli/` (monolynx-cli, Typer-based), a command function's docstring is not
just internal documentation - Typer renders it verbatim as that subcommand's
`--help` text. On MON-125 I followed a task brief that said "docstrings po
polsku bez diakrytykow w cli/, jak w cli/tests/conftest.py i main.py" and
wrote every new docstring/echo string without Polish diacritics. The krytyk
review (-5) flagged this as inconsistent with `main.py`'s actual style, which
DOES use full diacritics throughout (`"Wypisz wersję ... i zakończ."`,
`"CLI do zarządzania platformą Monolynx."`) - I had misread `main.py` on a
first pass, and `conftest.py`'s ASCII-only test-helper docstrings are not a
representative example since they're never shown to a CLI user.

**Why:** `conftest.py` docstrings and `tests/*.py` docstrings are internal to
the test suite and never rendered anywhere a human sees them, so ASCII-only
Polish there is harmless. A `@app.command()` function's docstring, its
`typer.Option(..., help=...)` strings, and every `typer.echo(...)` message
ARE user-facing output - the same class of text the user's own global
CLAUDE.md mandates full diacritics for ("Pelne diakrytyki PL w kazdym tekscie
ktory ja widze lub wysylam dalej").

**How to apply:** in `cli/src/monolynx_cli/`, use full Polish diacritics
(ą, ć, ę, ł, ń, ó, ś, ź, ż) in: `@app.command()` docstrings, `typer.Option`
`help=` strings, every `typer.echo()` message, and module-level docstrings
of files under `commands/` and the top of `main.py`/`config.py` (for
consistency, low risk either way). Internal WHY-comments explaining a
non-obvious implementation choice can stay ASCII-only if that's the existing
file's established style, but when in doubt, add diacritics - the exact
string literals a test asserts against (e.g. `"Zalogowano jako <email>
(profil <nazwa>)"`) happen to need none in this ticket's case, so check the
test's literal `==` assertions before changing an echoed string's spelling.
