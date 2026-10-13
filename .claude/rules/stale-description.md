---
paths:
  - "src/**"
  - "plugin/**"
  - "scripts/**"
  - "tests/**"
  - "cli/**"
  - ".gitlab-ci.yml"
---

# Opis zmienionego elementu aktualizujesz w tym samym diffie

Docstring modułu, docstring funkcji, docstring testu, podsumowanie sekcji skilla i tabela "Ważne zasady" deklarują zachowanie. Po zmianie zachowania stara deklaracja kłamie i wprowadza w błąd następnego agenta, który czyta opis zamiast kodu.

## Rule

1. Zmieniając zachowanie funkcji, modułu, testu albo skilla, w tym samym diffie aktualizujesz każdy opis, który to zachowanie deklaruje: docstring modułu i funkcji, docstring testu, podsumowanie sekcji, wiersz tabeli zasad.
2. Przed raportem grepujesz w zmienianym pliku starą nazwę, stary parametr albo stare zachowanie. Każde trafienie w opisie poprawiasz albo uzasadniasz w raporcie.
3. Nowy router, endpoint albo sekcja dopisana do istniejącego modułu wymaga aktualizacji docstringu modułu, jeśli ten wylicza, co moduł zawiera. Dotyczy też nowej opcji CLI albo podkomendy skryptu, gdy docstring modułu wylicza sygnatury.
4. Testy pisane w TDD przed implementacją: gdy implementacja jest gotowa, docstringi i komentarze testów nie mówią już „implementacja jeszcze nie istnieje” ani „test ma padać”. Nazwa testu i jego komentarz obiecują tylko to, co sprawdzają asercje.
5. Komentarz opisuje automatyzację, która istnieje w repo w tym samym diffie, a nie tę, która dopiero ma powstać. Gdy stan repo zmienia się w trakcie ticketu, komentarze typu „czego jeszcze nie ma” grepujesz i poprawiasz przed oddaniem.

## Pitfall

Diff zmienia ciało funkcji, a docstring kilkadziesiąt linii wyżej zostaje poza kontekstem edycji. Testy tego nie łapią, lint też nie. Rozjazd widać dopiero w review albo gdy kolejny agent zaufa opisowi.

## Regression

- MON-202: docstring `src/monolynx/blog.py:1` opisywał tylko render pojedynczego wpisu po dodaniu listy `/blog/` (-5).
- MON-197: docstring zmienionej funkcji po zmianie brancha bazowego zostawiony jako follow-up (-5).
- MON-191: docstring testu opisywał inne zachowanie niż asercja.
- Retro sprintu „CLI monolynx - fundament + REST API v2”: reguła łamana w 7 ticketach, w tym w `cli/` i `.gitlab-ci.yml`, których `paths` wcześniej nie obejmowały (dopisane).
  - MON-133: docstring `cli/src/monolynx_cli/main.py` wymieniał MON-133 jako ticket, który dopiero doda komendy wiki (-5).
  - MON-138: komentarz w `.gitlab-ci.yml` opisywał nieistniejący job, a po jego dodaniu został nieaktualny (-5, dwie rundy).
  - MON-142: docstringi testów w 5 plikach `tests/integration/api_v2/` nadal mówiły, że implementacja nie istnieje (-5).
  - MON-137, MON-139: komentarz testu obiecywał więcej niż asercje albo był sprzeczny z seedem (-5, -5).
  - MON-127: nazwy testów `config list` niezgodne z asercjami (-5).
  - MON-222: docstring `plugin/scripts/sprint_run_tick.py` bez `--slots`/`--max-waiting`, klucza `dispatch` i kodu 64 (-25, reguła z pamięci roli wracała).
- Retro sprintu „Fix uprawnień”: reguła łamana w 5 ticketach, za każdym razem wychwycona dopiero w review.
  - MON-239: docstring `schedule_for_ticket` mówił „wszystkie wpisy użytkownika” po dodaniu filtra członkostwa (-5).
  - MON-241: docstringi 15 narzędzi MCP bez wymaganego uprawnienia, nieścisły opis `_auth` w `CLAUDE.md` (-5, -5).
  - MON-242: `CLAUDE.md` z nowym wpisem sprzecznym ze starym opisem kilkadziesiąt linii niżej (-5).
  - MON-245: komentarz nad `REQUIRED_FLAG_PARAMS` i changelog z nieprawdziwym twierdzeniem o wersji CLI i o parametrach MCP.
  - MON-252: docstring modułu bez `[--state-dir D]`, README sprzeczne z zasadą skilla i z kodem (-5, -5).

## What to check

- Dopisujesz akapit do `CLAUDE.md` albo README: grep tego samego tematu w reszcie pliku, czy starszy opis nie mówi czegoś przeciwnego.
- Diff zmienia sygnaturę albo zachowanie, a docstring tej funkcji lub modułu nie jest w diffie.
- Grep starej nazwy w zmienianym pliku zwraca trafienia w komentarzach lub docstringach.
- Sekcja "Ważne zasady" skilla sprzeczna z krokiem, który diff zmienił.
- Grep „jeszcze nie istnieje”, „not yet implemented”, „ma padać”, „TODO: MON-” w docstringach testów i modułów zmienianych w diffie.
- Komentarz w CI albo skrypcie odwołuje się do joba, pliku lub kroku, którego nie ma w repo po tym diffie.
- Nazwa testu (`test_..._lists_all`) sprawdzana z asercjami: czy asercje dowodzą tego, co mówi nazwa.
