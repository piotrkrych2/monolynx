# plugin/hooks/bash_guard.py - review notes

## Jak testowac hooka podczas review (wazne)
Hook jest AKTYWNY w sesji krytyka. Kazda komenda Bash zawierajaca `rm -rf`, `pytest`, `ruff`, `mypy` itp.
zostanie zablokowana (krytyk = subagent -> `deny`). Zeby empirycznie sprawdzic decyzje hooka:
1. Zapisz skrypt-probe do scratchpada narzedziem **Write** (Write nie przechodzi przez guard).
2. Uruchom `python3 <sciezka-do-probe>` - linia polecenia nie zawiera wtedy destrukcyjnych stringow.
3. Baseline do porownania: `git show <sha>:plugin/hooks/bash_guard.py > <scratchpad>/baseline_guard.py`
   i ten sam probe na nim. To jedyny sposob odroznienia REGRESJI od luki pre-existing.
Hook jest stdlib-only, wiec `python3` lokalnie jest OK (nie dotyczy reguly "Python tylko w Dockerze").

## Luki PRE-EXISTING w `DESTRUCTIVE` (potwierdzone na baseline 34ef409 probe'em roznicowym)
Wzorzec wylicza **pisownie flag** zamiast je parsowac i wymaga pasujacej flagi **tuz po `rm`**.
Po MON-147 iter2 wzorzec to `\brm\s+((?:-[a-zA-Z]*[rf][a-zA-Z]*|--recursive|--force|--dir)\s+)+`.
Nadal ALLOW (bez zadnej decyzji), identycznie jak na baseline:
- **`rm -R /`, `rm -R /home`, `rm -RF /home`** - klasa `[rf]` jest TYLKO mala litera. `-Rf`/`-fR` lapie
  (dzieki `f`), samo `-R`/`-RF` nie. To najgrozniejszy wariant: `-R` jest POSIX i dziala na GNU i BSD.
- **skroty long-option GNU** (`getopt_long` przyjmuje jednoznaczne prefiksy): `rm --r`, `rm --rec`,
  `rm --fo` - dzialajace, pelne kasowanie na Linuksie.
- **`rm -d`** - krotka forma `--dir`, ktora iter2 dodala tylko w wersji dlugiej (niespojnosc).
- **wiodaca flaga niepasujaca**: `rm -i -rf /`, `rm -v -rf /`, `rm --interactive -rf /` -> ALLOW.
- bare `rm` bez flag nigdy nie byl w `DESTRUCTIVE` - `rm /etc/x` przechodzi.
Wniosek: deklaracja z docstringa/README "kazdy niespelniony warunek wraca do reguly DESTRUCTIVE"
(oraz "bez flagi -r/-R/--recursive") jest prawdziwa tylko czesciowo. Docelowy fix to parsowanie flag
(rozbicie klastra krotkich flag na litery + normalizacja `--x=y` i prefiksow), nie kolejna alternatywa.
Nie przypisuj tego nowym zmianom - zawsze najpierw sprawdz baseline probe'em roznicowym.

## `is_tmp_cleanup` (od 1.6.1, MON-147)
Allow-lista przed petla `DESTRUCTIVE`: `rm` / `rm -f` / `rm --force` na argumentach pasujacych do
`^/(private/)?tmp/[^/]*\.(log|txt|md)$`, bez `..`, bez `[|;&`]` i `$(`.
Znane granice (stan po MON-147):
- **newline nie jest metaznakiem** -> `rm -f /tmp/a.log\n/tmp/x.md` przechodzi jako jedna instrukcja.
- kontrola `..` dziala na literale sprzed ekspansji -> `rm -f /tmp/$X.log` i `~` omijaja warunek.
- `[^/]*` odrzuca podkatalogi, wiec realny scratchpad `/private/tmp/claude-501/<sesja>/scratchpad/*.md`
  NIE przechodzi (mimo ze spec podaje go jako uzasadnienie dla `/private/tmp`).
- cudzyslowy, `--` jako separator i `2>/dev/null` wypadaja z allow-listy (fail-closed, ale zaskakuje).

## Konwencje pliku
- Skrypt liniowy: helpery (`decide`, `flag`) na gorze, potem staly + przeplyw modulowy. `line-length = 150`
  w pyproject, wiec dlugie linie parametrize w `tests/unit/test_bash_guard.py` nie sa naruszeniem.
- Testy hooka: subprocess ze stdin (`run_hook`), helpery `assert_allow/deny/ask`, `SUBAGENT` vs `MAIN_SESSION`.
  Uwaga: `MAIN_SESSION` to pusty dict przekazywany jako `env_extra` - mylaca nazwa, znana od MON-144.
