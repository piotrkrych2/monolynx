---
paths:
  - "plugin/skills/**/*.md"
  - "plugin/references/transport.md"
  - "plugin/scripts/monolynx_op.py"
  - "plugin/scripts/monolynx_transport.py"
  - "scripts/sync_skills.py"
---

# Skill rozmawia z platformą przez blok transportu, nie przez własne komendy CLI

Zapis `mcp__monolynx__<tool>(...)` w SKILL.md oznacza operację, nie kanał. O tym,
czy pójdzie ona przez CLI `monolynx`, czy przez tool MCP, decyduje wspólny blok
`shared:transport` i dwa skrypty: preflight `monolynx_transport.py` i wrapper
`monolynx_op.py`. Skill, który omija ten mechanizm, działa tylko u autora.

## Rule

1. **Para znaczników w każdym skillu rozmawiającym z platformą.** `SKILL.md`,
   który zawiera napis `mcp__monolynx__`, ma dokładnie jedną parę
   `<!-- shared:transport -->` / `<!-- /shared:transport -->`, każdy znacznik
   w osobnej linii, poza innymi blokami `shared:*`. Skill bez tego napisu
   znaczników nie dostaje. Literał znacznika nie pojawia się w pliku drugi raz,
   także w prozie.
2. **Blok edytujesz tylko w `plugin/references/transport.md`.** Treść między
   znacznikami jest generowana: po zmianie źródła uruchamiasz
   `python3 scripts/sync_skills.py`, a potem `python3 scripts/sync_skills.py --check`.
   Ręczna poprawka w jednym skillu znika przy następnej synchronizacji albo
   rozjeżdża `--check`.
3. **Skill nie składa komend `monolynx ...` sam.** Operację wykonuje wyłącznie
   `python3 <katalog skilla>/scripts/monolynx_op.py <op> --project_slug <slug> [--<param> <wartość>]`,
   gdzie `<op>` to nazwa toola MCP, a flagi to nazwy jego parametrów. Komenda
   ma literalną ścieżkę, bez zmiennych, `~`, pipe, przekierowań i nawiasów `(` `)`, bo tylko taką
   formę hook przepuszcza bez własnej decyzji.
4. **Nowa operacja CLI trafia do `OPS` w `plugin/scripts/monolynx_op.py`**
   razem z testem dryfu w `tests/unit/test_monolynx_op.py`: komenda istnieje
   w drzewie `--help`, a każda flaga w `<grupa> <komenda> --help`, sprawdzone
   wobec fixture z przechwycenia w `tests/fixtures/monolynx/`
   (`.claude/rules/external-tool-output.md`). Operacja tylko do odczytu trafia
   też do `READ_OPS` i do kopii `MONOLYNX_READ_OPS` w `plugin/hooks/bash_guard.py`.
5. **Operacja bez komendy CLI zostaje przy MCP.** Pipelines, `log_time`,
   `get_work_report`, graf i narzędzia metody LLM Wiki nie mają wpisu w `OPS`;
   wrapper zwraca dla nich kod 3 i skill woła tool MCP. Nie dopisuj do `OPS`
   operacji, której CLI nie ma.
6. **Pola, nie tekst.** Wynik z CLI to JSON z API v2. Skill, który polega na
   konkretnym fragmencie tekstowej odpowiedzi MCP (sekcja, linia, kolumna),
   nazywa obok odpowiadające mu pole JSON, poza blokiem transportu i poza
   cudzymi blokami `shared:*`.
7. **Zamrożone linie są kontraktem.** `TRANSPORT: mode=...`,
   `TRANSPORT-HINT: ...` i `OP-FALLBACK: mcp tool=<op>` oraz kody wyjścia
   wrappera (0, 3, 2, 64) zmieniasz razem ze skryptem, blokiem, README i testami,
   w jednym tickecie.

## Pitfall

Autor skilla ma CLI zainstalowane i zalogowane, więc wpisuje wprost
`monolynx ticket get ...`. U użytkownika bez CLI skill nie ma fallbacku,
u użytkownika ze starszym CLI flaga nie istnieje, a w sesji w tle komenda
złożona z pipe dostaje pytanie o zgodę, na które nikt nie odpowie. Druga
pułapka: poprawka wklejona do bloku w jednym `SKILL.md` wygląda na działającą,
dopóki `sync_skills.py` jej nie nadpisze.

## Regression

- MON-232: wprowadzenie transportu. Reguła zapisana razem z mechanizmem, zanim
  powstał pierwszy skill pisany już po nim: blok w 18 skillach ma jedno źródło,
  a fixture CLI 0.1.0 pokazały, że `version --check` kończy się tam kodem 2
  (`No such command`), czyli komenda z nowszego CLI nie istnieje u użytkownika
  ze starszym.

## What to check

- Nowy albo zmieniony `SKILL.md` z `mcp__monolynx__` bez pary znaczników, z dwiema
  parami albo ze znacznikiem wewnątrz innego bloku `shared:*` -> punkt 1
  (`tests/unit/test_skill_transport_block.py`).
- Diff zmienia treść między znacznikami, a nie zmienia
  `plugin/references/transport.md` -> punkt 2.
- Komenda `monolynx <grupa> ...` wpisana wprost w kroku skilla -> punkt 3.
- Nowy klucz w `OPS` bez przypadku w teście dryfu albo bez fixture `--help` -> punkt 4.
- Nowy odczyt w `READ_OPS` bez tej samej nazwy w `MONOLYNX_READ_OPS` hooka -> punkt 4.
- Skill czyta z odpowiedzi "sekcję" albo "linię" i nie podaje pola JSON -> punkt 6.
- `python3 scripts/sync_skills.py --check` i `python3 scripts/validate_plugin_consistency.py`
  kończą się kodem innym niż 0 -> blok albo kopie skryptów są rozjechane.
