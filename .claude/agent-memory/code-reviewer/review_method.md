---
name: review-method
description: Metoda weryfikacji krytyka - jak dowodzic znalezisk, pulapki narzedziowe (guard bash, coverage, monitor stabilnosci), adresowanie TM
metadata:
  type: feedback
---

# Metoda review i pulapki narzedziowe

## Gate'y spojnosci pluginu odpalaj SAM (MON-197)
Team Manager podal wszystkie gate'y zielone, a `python3 scripts/sync_skills.py --check` byl w chwili review CZERWONY (`Dystrybucje skilli sa nieaktualne: sprint-run`). Przyczyna: developer poprawil zrodlo `plugin/scripts/sprint_run_tick.py` 57 s PO tym, jak TM zrobil `make sync-skills` - `ls -lT` zrodla i kopii pokazuje to od razu. Pytest tego NIE lapie: `tests/unit/test_plugin_distribution.py` sprawdza tylko OBECNOSC plikow z listy `_BUNDLED`, a `sprint_run_tick.py` nawet tam nie ma.
**How to apply:** przy kazdym review pluginu odpal osobno `sync_skills.py --check`, `sync_agents.py --check`, `validate_plugin_consistency.py` oraz `diff zrodlo kopia` dla zmienionych skryptow. Drift w kopiach = bloker zespolowy dla TM (pliki generowane), nie odjecie dla developera.

## Stub zewnetrznego narzedzia nie zna nowych argumentow (MON-197)
fakebin `git` w `tests/unit/test_sprint_run_tick.py` (GIT_SCRIPT_TEMPLATE) ma galaz `*) exit 128`. Nowe wywolania w preflighcie (`symbolic-ref`, `rev-parse --abbrev-ref HEAD`, `fetch`, `rev-parse origin/X`) trafiaja w nia i ida sciezka bledu, a `test_full_environment_exits_zero_and_reports_all_keys` przechodzi z pustymi nowymi kluczami, bo ich NIE asertuje.
**How to apply:** gdy ticket dokłada wywolanie zewnetrznego narzedzia do skryptu testowanego przez subprocess, sprawdz stub - "testy zielone" nie znaczy "nowy kod wykonany". Infrastruktura `tick_sandbox` / `run_preflight` / `full_flags_env` jest gotowa do reuzycia; "duplikowanie fakebina byloby kosztowne" to zle uzasadnienie braku testu.

## Test na literal w pliku zrodlowym to nie test kodu (MON-197)
`re.search(r'"BASE_BRANCH"', content)` na tresci skryptu dokumentuje ksztalt pliku, nie chroni zachowania: gdyby funkcja zwracala zawsze `""`, test byl by zielony. Dowod luki bierz z kodu testu (czy importuje/uruchamia modul, czy tylko `read_text()`), nie z deklaracji autora.

## Mutant, ktory sie wywala, wyglada jak mutant zabity (MON-197)
Kopia `plugin/scripts/sprint_run_tick.py` uruchomiona spoza tego katalogu pada na `import sprint_run_state` - preflight nie wypisuje NICZEGO, wiec kazda asercja "wartosc != oczekiwana" wychodzi na zielono i probe raportuje "wszystkie mutanty zabite". Fałszywy dowod.
**How to apply:** kazdy przebieg mutanta musi najpierw pokazac, ze BAZOWA wersja w tym samym srodowisku daje oczekiwane wartosci; skrypt z lokalnym importem uruchamiaj z `PYTHONPATH` wskazujacym jego katalog. Jesli mutant i baseline daja ten sam "brak wyniku" - to nie jest dowod, to crash.

## Odtworzenie sandboxa testowego do recznej proby (MON-197)
Fixture z fakebinem da sie odtworzyc bez pytest: wyciagnij `GIT_SCRIPT_TEMPLATE` / `CLAUDE_SCRIPT` regexem z pliku testowego, zapisz do `fakebin`, `chmod 0o755`, zbuduj env `{"PATH": fakebin:..., **flagi}` i odpal skrypt przez `subprocess.run`. Do sprawdzenia, czy test faktycznie wykonuje nowy kod, loguj obecnosc komunikatu catch-all stuba ("nieobslugiwane argumenty") w stderr - `False` we wszystkich przypadkach dowodzi, ze galezie stuba sa trafiane.

## Weryfikacja mutantem (MON-191, MON-149)
Zanim uznasz test za chroniacy: wylacz w kopii skryptu (w tmp joba) linie, ktora test ma pilnowac, i sprawdz czy asercje padaja. W MON-191 fixture sondy niosl literal z ECHA PROMPTU, wiec `any("<marker>" in line)` przechodzilo bez kodu. Policz tez sam galezie, ktorych zaden fixture nie dotyka (MON-149: zaden z 8 fixture'ow nie mial `total == 0`, wiec galaz `score is null` nie byla wykonywana).

## Nie wierz komentarzowi "Zaobserwowane w fixture X" (MON-149)
Przelicz literaly w fixture osobnym skryptem (`collections.Counter`) i porownaj ze zbiorami stalych. Falszywa atrybucja = -5 za konwencje, nie -25 za regule, gdy literal byl nakazany kontraktem TM.

## Guard Bash w sesji krytyka
Blokuje: nazwe runnera testow w DOWOLNYM miejscu linii (takze w grepie i heredocu), kasowanie z flaga force, kazdy potok/heredoc/podstawienie karmiace `python`, oraz komendy z programem lub argumentem wyliczanym w runtime (petla `for`, `$S --report "$rep"`).
**How to apply:** skrypt zapisz do `$CLAUDE_JOB_DIR/tmp/*.py` z literalnymi sciezkami i odpal `python3 <sciezka>`; stdin podawaj przekierowaniem z pliku, nie potokiem; plik testowy grepuj po `def test_` albo `mark.`; notatke z zakazanym slowem pisz Editem, nie heredociem. Recznego przebiegu nie odpuszczaj - to on wylapuje rzeczy, ktorych testy nie sprawdzaja (MON-151: raport klamal "Nowy baseline zapisany" przy `--dry-run`).

## PULAPKA: coverage (--cov) crashuje w kontenerze app
`pytest --cov` i `coverage run` w kontenerze `app` daja `ImportError: numpy cannot load module more than once per process` (instrumentacja pre-importuje modul -> modele -> pgvector -> numpy, potem conftest importuje ponownie). `COVERAGE_CORE=sysmon` nie pomaga, pre-import przez sitecustomize -> SEGFAULT. Skutek: nie da sie zreprodukowac % coverage; weryfikuj JAKOSCIOWO (patch-targety, trafione endpointy, stan DB), liczbe z raportu qa przyjmuj z zastrzezeniem.

## PULAPKA: nie oceniaj NIEDOKONCZONEJ wersji pliku agenta
Monitor "plik stabilny przez 60s" moze odpalic w pauzie agenta miedzy zapisami - dalo to FALSZYWE 24 failures na polowicznym pliku. Zanim orzekniesz regresje: potwierdz finalnosc (okno 40-60 s + liczba testow/hash + `git diff --stat`), a przy podejrzeniu cross-file pollution uruchom plik SOLO vs w kombinacji i baseline vs praca agenta.
- `DROP TABLE work_plan_entries` w session-scope teardownie pochodzi od PRE-EXISTING `test_e2e_work_plan.py::test_mcp_schedule_ticket_persists_to_separate_session` (osobny `async_sessionmaker` + realne commity). Same 2 pre-existing testy daja `2 passed, 1 error` - to NIE regresja.

## PULAPKA: git checkout kasuje prace agentow z working tree
Agenci zostawiaja zmiany NIEZACOMMITOWANE. `git checkout <plik>` przywraca HEAD i kasuje ich prace. Przed jakakolwiek manipulacja zrob kopie; po niej `git diff --stat` musi zgadzac sie z oryginalnym diffem.

## Adresowanie Team Managera w SendMessage
Adres z promptu ("ADRES TEAM MANAGERA", np. `monolynx-MON-197-opus`) bywa nazwa WLASNEJ sesji - SendMessage zwraca "also addresses this session". Team Manager jest osiagalny jako `team-lead`. Nie zgaduj wariantow nazwy ticketa.

## Stacking branchy i weryfikacja bazy (MON-117)
Gdy dwa tickety stoja na tym samym commicie, `git diff <branch-spod>` NIE jest czysty. Weryfikuj: `git show base:plik | grep -c symbol` = 0 ORAZ `git log base..HEAD` pusty. Grepuj symbol w base, nie ufaj nazwie brancha.

## Snapshot statusu z promptu bywa niepelny (MON-79)
`git status` podany w prompcie moze nie pokazywac plikow ze zmianami UNSTAGED. Zawsze rob swiezy `git diff <plik>` per docelowy plik.

## Nie ucinaj `head -N` grepowi, ktory ma DOWODZIC BRAKU (MON-188)
Twierdzenie "zaden test nie pinuje realnej wersji pluginu" oparlem na `grep -rn '1\.19\.\|plugin.json' tests/ | head -10` - dziesiec pierwszych trafien wypelnil jeden plik i `tests/unit/test_plugin_distribution.py` wypadlo poza okno. Test jednak pinowal (rownosc wersji dwoch manifestow), wiec teza byla falszywa.
**How to apply:** grep dowodzacy nieistnienia konczy sie `| wc -l` albo pelnym wyjsciem, nigdy `head`. Gdy trafien jest duzo, zawez wzorzec (`grep -rln`, lista plikow), a nie liczbe linii.

## "Port zamkniety" przez `raises(URLError)` nic nie dowodzi (MON-143)
`urllib.error.HTTPError` dziedziczy po `URLError`, wiec asercja przechodzi takze, gdy serwer dalej zyje i odpowiada 4xx (409 na drugi callback, 400 bez state). Mutant "usun zamykanie serwera w finally" przezywal we wszystkich testach zamykania.
**How to apply:** test zamkniecia zasobu musi odroznic "brak zasobu" od "odpowiedz bledem": `ConnectionRefusedError` z `socket.create_connection` albo `not isinstance(exc, HTTPError)`. Przy kazdym `raises(<klasa bazowa>)` sprawdz, czy zywy kod nie rzuca podklasy.
