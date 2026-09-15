---
paths:
  - "plugin/README.md"
  - "plugin/skills/**/SKILL.md"
  - "src/monolynx/static/skills/**/SKILL.md"
  - "src/monolynx/static/*.md"
  - "src/monolynx/static/llms.txt"
  - "src/monolynx/templates/dashboard/profile/mcp_guide.html"
  - "CLAUDE.md"
---

# Komendy i argumenty w dokumentacji: sprawdzone wobec źródła prawdy

Każda komenda, flaga i argument, które trafiają do dokumentacji dla człowieka albo do promptu skilla, są sprawdzone wobec interfejsu, który je przyjmuje. Dokumentacja opisująca interfejs z pamięci jest gorsza niż jej brak: czytelnik wkleja komendę i dostaje coś innego, niż obiecano.

## Rule

1. **Argumenty skilla** pochodzą z `argument-hint` i z KROK-u, który je czyta, w `plugin/skills/<nazwa>/SKILL.md`. Opis użycia w README, help, `mcp_guide.html`, `how-to-use-monolynx.md` nie wymienia argumentu, którego skill nie zna.
2. **Flagi CLI** (docker, git, glab, claude, narzędzia mutacyjne) są sprawdzone wobec `--help` tego narzędzia w tym samym tickecie, łącznie z **kolejnością**: flaga globalna przed podkomendą i ta sama litera po podkomendzie to często dwie różne rzeczy (`docker compose -p X run` = nazwa projektu, `docker compose run -p X` = publikacja portu).
3. **Komenda dla człowieka jest wklejalna w zwykłym shellu.** Zmienne istniejące tylko wewnątrz sesji Claude Code (`${CLAUDE_PLUGIN_ROOT}`, `$CLAUDE_JOB_DIR`) nie trafiają do komend tmux/cron/README; skill rozwija je przez `echo` i wypisuje ścieżkę literalnie.
4. **Sygnatury tooli MCP** w SKILL.md obowiązuje `.claude/rules/skill-mcp-calls.md`; ta reguła dotyczy wszystkiego, co nie jest toolem MCP.

## Pitfall

Poprawna komenda jest w trzech plikach, a czwarty (changelog, kafelek, wiersz tabeli) dostał ją "z głowy" w innej kolejności flag albo z dodatkowym argumentem. Walidator pluginu sprawdza obecność nazw skilli, nie treść komend, więc rozjazd przechodzi CI i wychodzi dopiero u użytkownika.

## Regression

- MON-150: `mcp_guide.html` obiecywał `/monolynx:mutation-check [ticket-id | ścieżka-modułu]`, a skill zna tylko ścieżkę modułu (-5).
- MON-166: changelog README zapisał `docker compose run --rm --no-deps -p <projekt>`; po `run` `-p` to `--publish`, komenda z README stawiałaby w worktree duplikat stacku (-5).
- MON-172: komendy tmux w SKILL.md, README i help zawierały literał `${CLAUDE_PLUGIN_ROOT}`, który w terminalu rozwija się do pustego stringu.

## What to check

- Nowy lub zmieniony opis użycia skilla -> porównaj z `argument-hint` i KROK czytającym argumenty w SKILL.md.
- Nowa komenda CLI w dokumentacji -> `<narzędzie> <podkomenda> --help` w tym tickecie; kolejność flag zgodna z sekcją USAGE.
- Komenda w bloku "do wklejenia" zawiera `${CLAUDE_...}` albo `$CLAUDE_...` -> zamień na rozwiniętą ścieżkę albo instrukcję `echo`.
- Ta sama komenda w kilku plikach -> `grep` po jej rdzeniu, wszystkie wystąpienia identyczne.
