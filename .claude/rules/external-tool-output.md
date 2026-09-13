---
paths:
  - "plugin/scripts/**"
  - "plugin/hooks/**"
  - "cicd/**"
  - "plugin/skills/**/SKILL.md"
  - "src/monolynx/static/skills/**/SKILL.md"
---

# Wyjście zewnętrznego narzędzia: fixture z realnego przebiegu, nie z wyobrażenia

Kod, który parsuje wyjście zewnętrznego narzędzia (`claude agents --json`, `claude logs`, `git`, `glab`, `graphify`, narzędzia mutacyjne) albo woła jego API (tool MCP, podkomenda CLI), powstaje na podstawie **przechwyconego realnego wyjścia**, nigdy na podstawie tego, jak autor sądzi, że to wyjście wygląda.

## Rule

1. **Fixture z przechwycenia.** Test parsera używa pliku `tests/fixtures/<narzędzie>/<komenda>.<wersja>.<ext>` zapisanego wprost z realnego uruchomienia. Pierwsza linia (albo nagłówek JSON `_meta`) podaje: dokładną komendę, wersję narzędzia, datę. Ręcznie napisany JSON w teście dowodzi tylko, że kod zgadza się z wyobrażeniem autora, nie z narzędziem.
2. **Listy literałów wyprowadzane z fixture.** Zbiory stanów, kodów, nazw pól (np. `_LIVE_SESSION_STATES`) mają w komentarzu WHY odwołanie do fixture, z którego pochodzą. Literał, którego nie ma w żadnym fixture, nie trafia do listy "na wszelki wypadek".
3. **Nieznany literał mapuje się na wariant odwracalny.** Gdy narzędzie zwróci wartość spoza listy, kod wybiera skutek, który da się cofnąć następnym przebiegiem (np. sesja "żywa" -> opóźnienie o tick), nie skutek trwały (np. "martwa" -> auto-wznowienie i duplikat sesji). Decyzja jest wpisana w kod komentarzem WHY.
4. **Wyjście terminalowe to nie tekst.** Komendy renderujące TUI (`claude logs`, `claude attach`) zwracają kody ANSI i przerysowania ekranu. Przed dopasowaniem wzorca kod zdejmuje sekwencje sterujące i dopiero potem dzieli na linie.
5. **Sygnatura z kodu, nie z pamięci.** Wywołanie toola MCP w SKILL.md albo podkomendy CLI w skrypcie jest zgodne z aktualną sygnaturą (`mcp_server.py` dla MCP, `<narzędzie> <komenda> --help` dla CLI), sprawdzoną w tym samym tickecie. Dla MCP obowiązuje dodatkowo `.claude/rules/skill-mcp-calls.md`.

## Pitfall

Narzędzie ma dwa identyfikatory tego samego obiektu (`id` i `sessionId`), kilka literałów stanu widocznych tylko w różnych fazach życia (`blocked`, `working`, `done`), albo wyjście, które wygląda jak tekst, a jest renderem terminala. Jednorazowa obserwacja w jednej fazie daje kod, który przechodzi ręcznie pisane testy i pada przy pierwszym realnym przebiegu, cicho: brak wyniku zamiast błędu.

Właściwy identyfikator to ten, którym potem wołasz narzędzie (`claude logs|attach|rm <id>`), i ta sama wartość idzie do nazw plików pośrednich (`<id>.log`).

## Regression

- MON-148: `create_wiki_page(..., slug="toolchain")` z parametrem, którego tool nie ma; po usunięciu slug powstawał z tytułu i strona stawała się niewidoczna dla `work`.
- MON-167: `build_report` brał `sessionId` (UUID) zamiast `id` (8 znaków), więc plik `<id>.log` nigdy nie pasował i werdykt `waiting` był nieosiągalny; wykryte w review (-15).
- MON-172: pierwszy realny przebieg dyspozytora: stan `working` poza whitelistą (pracująca sesja uznana za martwą, auto-wznowienie i duplikat), `claude logs` z kodami ANSI, `[ -t 0 ]` fałszywe w narzędziu Bash także interaktywnie, sesje `--bg` z `acceptEdits` pytające o każdy Bash.

## What to check

- Nowy parser / klasyfikator wyjścia narzędzia bez pliku w `tests/fixtures/<narzędzie>/` -> brak fixture z przechwycenia (-25).
- Lista literałów (stany, kody, pola) bez odwołania do fixture w komentarzu WHY -> literały z wyobrażenia.
- Gałąź "nieznana wartość" prowadzi do operacji trwałej (start sesji, `rm`, komentarz, push) -> zły wariant domyślny.
- Dopasowanie regex/substring na wyjściu komendy TUI bez zdjęcia ANSI -> punkt 4.
- Wywołanie CLI/MCP, którego sygnatura nie została sprawdzona w tym tickecie (`--help` / `mcp_server.py`) -> punkt 5.
