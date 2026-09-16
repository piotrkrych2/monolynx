---
name: monolynx-help
description: "Wyświetl mapę skilli Monolynx: 6 grup (Setup, Planowanie, Praca, Integracja, Sprint, Wiki), każdy skill w jednej linii, oraz dwie ścieżki pracy - ręczną i autopilot. Zmienne, runtime'y i pełne opisy skilli są w README pluginu. Użyj gdy chcesz wiedzieć, który skill wybrać."
user-invocable: true
argument-hint: ""
allowed-tools: ""
---

# Monolynx - mapa skilli

Wyświetl poniższą mapę użytkownikowi i zakończ. Nie wykonuj żadnych dodatkowych akcji.

---

**Zapamiętaj `/monolynx:next`** - odczytuje stan projektu i wskazuje 1-3 uzasadnione komendy; niczego nie uruchamia.

**Nowy projekt? `/monolynx:setup`** - checklista konfiguracji, zanim weźmiesz pierwszy ticket.

## Setup

- `/monolynx:setup` - checklista projektu (slug, strona wiki `toolchain`, LLM Wiki, graf i mutacje w CI, flagi `MONOLYNX_*`): OK/BRAK, za zgodą uruchamia skill naprawczy, flagi tylko raportuje
- `/monolynx:project-toolchain` - wykrywa komendy lint i testów (opcjonalnie narzędzie mutacyjne) i zapisuje stronę wiki `toolchain`, którą czytają `work` i `work-simple`
- `/monolynx:wiki-init` - włącza metodę LLM Wiki: strony systemowe `wiki-schema`, `wiki-index`, `wiki-log` (opcjonalnie)
- `/monolynx:graph-sync` - lokalna synchronizacja grafu kodu przez graphify, bez CI
- `/monolynx:create-graph-ci-script [monolynx-url]` - nieblokujący krok CI synchronizujący graf kodu (graphify instaluje właściciel runnera)
- `/monolynx:create-mutation-ci-script` - nieblokujący krok CI z testami mutacyjnymi na MR/PR i zapadką baseline

## Planowanie

- `/monolynx:ticket-create [krotki opis zadania]` - ticket z kontekstem z wiki, kodu i grafu (cel, zakres, nie ruszać, odwracalność, kryteria)
- `/monolynx:ticket-review [ticket-id lub klucz np. MNX-12]` - recenzja formy i zgodności z wiki oraz kodem; powtarzaj, aż raport pokaże same OK
- `/monolynx:brief [goly opis zadania, np. "napraw build na CI"]` - kontrakt pracy bez ticketu (gotowe / nie ruszać / stop przed), ENTER = start

## Praca

- `/monolynx:next` - router read-only: git, ticket, sprint, sesje, MR i konfiguracja -> 1-3 komendy z uzasadnieniem oraz `NEXT: <komenda>`

- `/monolynx:work [ticket-id lub klucz np. PROJ-12]` - ticket powyżej 3 SP: Researcher, zespół agentów, krytyk, lint i testy przed `in_review`
- `/monolynx:work-simple [ticket-id]` - ticket do 3 SP: 1 dev + krytyk; eskaluje do `work` przy `SCOPE GREW`
- `/monolynx:resume [ticket-id lub klucz np. PROJ-12; puste = moje tickety in_progress]` - stan pracy po `/clear` albo następnego dnia; read-only
- `/monolynx:mutation-check [sciezka modulu (opcjonalnie)]` - testy mutacyjne; przeżywające mutanty jako kryteria akceptacji albo tickety
- `/monolynx:help` - ta mapa

## Integracja

- `/monolynx:mr-queue [!iid | #pr | URL ...]` - kolejka merge MR/PR: jeden tick, jedno działanie (CI -> konflikt -> approve -> merge)
- `/monolynx:wiki-sync-merge [ticket-id lub lista ticket-id, np. MON-75 MON-76]` - po merge do `main`: INGEST wiedzy z ticketów do wiki

## Sprint

- `/monolynx:sprint-run [sloty]` - dyspozytor: jeden tick startuje sesje `work` w tle i zbiera zakończone; sam nie pracuje nad ticketem
- `/monolynx:sprint-end [nazwa sprintu (opcjonalnie)]` - zamknięcie sprintu: INGEST logów, LINT wiki, przebieg mutacyjny, opcjonalne `retro`, `complete_sprint`
- `/monolynx:retro [nazwa sprintu (opcjonalnie; domyslnie aktywny albo ostatnio zamkniety)]` - powtarzające się korekty jako reguły, wiersze rubryki, wpisy `constitution`

## Wiki

- `/monolynx:search` - wyszukiwanie semantyczne w wiki; zwykle bez wpisywania komendy, skill włącza się sam przy pytaniach o dokumentację projektu
- `/monolynx:wiki-ingest [sciezka pliku / URL / temat zrodla]` - włącza nowe źródło do wiki (wymaga LLM Wiki)
- `/monolynx:wiki-lint` - audyt wiki: sieroty, martwe linki, sprzeczności, luki

---

## Dwie ścieżki pracy

**Ręczna - jeden ticket:**

1. `/monolynx:ticket-create`, potem `/monolynx:ticket-review`, aż raport pokaże same OK.
2. `/monolynx:work <KEY>` (do 3 SP: `/monolynx:work-simple <KEY>`). Przerwa w pracy: `/clear`, potem `/monolynx:resume <KEY>`.
3. Commit i push: komendę wypisze `work`, a flagi `MONOLYNX_AUTOCOMMIT` / `MONOLYNX_AUTOPUSH` robią to same.
4. Po merge do `main`: `/monolynx:wiki-sync-merge <KEY>`.

**Autopilot - cały sprint:**

1. W śledzonym `.claude/settings.json` (pole `env`): `MONOLYNX_AUTOTEST`, `MONOLYNX_AUTOCOMMIT`, `MONOLYNX_AUTOPUSH` ustawione na `true`.
2. `/loop 15m /monolynx:sprint-run` - każdy ticket dostaje sesję `work` w tle i własny worktree.
3. `/loop 10m /monolynx:mr-queue` - MR/PR mergowane po kolei, każdy po zielonym CI; approve daje zawsze człowiek.
4. Sprint przerobiony: `/monolynx:sprint-end`.

Bez sesji interaktywnej (tmux, cron) pętlę robi skrypt `sprint_run.sh` z pluginu.

## Więcej

Tabela zmiennych `MONOLYNX_*`, porównanie `work` i `work-simple`, pełne opisy skilli, modele, hooki,
pętla `sprint_run.sh`, Codex i Cursor: README pluginu (`plugin/README.md` w repozytorium Monolynx).
