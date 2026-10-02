---
name: monolynx-graph-sync
description: "Zsynchronizuj lokalnie graf zaleznosci kodu biezacego projektu z Monolynx (modul Polaczenia). Prowadzi za reke: wykrywa graphify (instrukcje instalacji dla macOS/Linux/Windows), generuje .graphifyignore, uruchamia ekstrakcje offline i wypycha graf przez cicd/sync_graph.py (replace_graph). Uzyj gdy chcesz zasilic lub odswiezyc graf bez CI - np. przy pierwszym setupie projektu albo ad hoc."
user-invocable: true
argument-hint: []
---

# Lokalna synchronizacja grafu kodu z Monolynx (graphify)

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Zasoby tego skilla: ustal absolutny katalog otrzymanego pliku `SKILL.md`.
Odnośniki do dołączonych `scripts/`, `references/` i pomocniczych plików
Markdown rozwiązuj względem tego katalogu, nie katalogu roboczego projektu.
Ścieżki plików tworzonych w projekcie pozostają względem projektu. Przed komendami
z `MONOLYNX_SKILL_DIR` przypisz tej lokalnej zmiennej powłoki ustaloną
literalną ścieżkę katalogu skilla; nie oczekuj jej w środowisku systemowym.

Prowadzisz uzytkownika za reke przez lokalna synchronizacje grafu zaleznosci **biezacego projektu** z modulem Polaczenia. To lokalny odpowiednik CI-owego skilla `/monolynx:create-graph-ci-script` - uzywa TEJ SAMEJ logiki mapowania (`cicd/sync_graph.py`) i tego samego toola `replace_graph` (pelna, idempotentna podmiana grafu).

**Prywatnosc**: ekstrakcja graphify to czysty AST (tree-sitter), dziala w pelni offline - kod NIE opuszcza maszyny. Do Monolynx trafia tylko zmapowany graf (nazwy plikow/klas/funkcji i relacje).

---

## KROK 0: Ustal konfiguracje

1. **Slug projektu**: jawny argument użytkownika > `MONOLYNX_PROJECT_SLUG` w środowisku > jawnie odczytana konfiguracja klienta lub `.env`, zgodnie z kontraktem runtime. Czytaj dane bez wykonywania kodu i bez ujawniania sekretów. Brak: zapytaj i zatrzymaj zależne działania.

2. **URL i token**: `MONOLYNX_URL` (default `https://monolynx.com`), `MONOLYNX_GRAPH_TOKEN` (uzytkownik generuje w Monolynx -> Profil -> Tokeny API). Jesli token nie jest ustawiony w srodowisku/.env - popros uzytkownika, zeby wyeksportowal go w terminalu przed KROK 4. **NIGDY nie pros o wklejenie tokenu do czatu i nie wypisuj jego wartosci.**

W przykladach uzywaj placeholderow `<PROJECT-SLUG>` / `<MONOLYNX_URL>`.

### Transport: CLI albo MCP

Dotyczy wylacznie opcjonalnego odczytu `get_graph_stats` w KROK 5; sam graf wypycha `cicd/sync_graph.py`, niezaleznie od transportu.

<!-- shared:transport -->
Operacje na platformie Monolynx wykonujesz jednym z dwoch transportow: CLI `monolynx` (zawsze przez wrapper) albo tools MCP. Zapis `mcp__monolynx__<tool>(...)` w tym skillu oznacza operacje `<tool>` z podanymi argumentami, niezaleznie od transportu. Regula jest ta sama w Claude Code i w Codex.

**Preflight - raz, na starcie skilla** (nie przed kazda operacja):

```bash
python3 <katalog skilla>/scripts/monolynx_transport.py
```

`<katalog skilla>` to absolutna sciezka katalogu tego pliku, wpisana literalnie. Komendy obu skryptow pisz bez zmiennych powloki, bez `~`, bez pipe, bez przekierowan i bez nawiasow `(` `)`: hook pluginu i sesje w worktree odrzucaja takie formy. Ostatnia linia wyniku jest zamrozona:

`TRANSPORT: mode=cli|mcp cli=<wersja|none> auth=ok|missing latest=<wersja|unknown> update=yes|no install=brew|pipx|uv|unknown|none`

| Wynik preflightu | Postepowanie |
|---|---|
| `mode=cli` | Operacje wykonujesz wrapperem `monolynx_op.py` (nizej). |
| `mode=mcp` | Tylko tools MCP, wrappera nie wolasz. |
| Jest linia `TRANSPORT-HINT:` (brak CLI, CLI niezalogowane albo nowsza wersja) | Przepisz ja uzytkownikowi jedna linia i pracuj dalej wedlug `mode`. Nie pytaj, nie czekaj, niczego nie instaluj. |
| `mode=mcp` i runtime nie udostepnia tooli MCP Monolynx | STOP, bez zgadywania. Podaj instrukcje: `pipx install monolynx-cli` i `monolynx auth login`, albo podpiecie connectora MCP Monolynx. |
| Preflight sie nie uruchomil (brak `python3`, odmowa uprawnienia) | Pracuj jak przy `mode=mcp`. |

**Operacja przy `mode=cli`** - pierwszy argument to nazwa toola, flagi to dokladne nazwy jego parametrow:

```bash
python3 <katalog skilla>/scripts/monolynx_op.py get_ticket --project_slug <PROJECT-SLUG> --ticket_id <KEY>
```

Wartosc bool to `true` albo `false`, lista to tablica JSON w jednej wartosci. Dluga tresc (komentarz, opis, strona wiki) i kazda wartosc ze znakami specjalnymi powloki: zapisz do pliku i podaj jako `@<sciezka pliku>`. Komend `monolynx ...` nie skladaj samodzielnie - mapowanie operacji na komendy CLI zyje we wrapperze.

Kody wyjscia wrappera:

- `0` - stdout to JSON API v2 z CLI. Niesie te same dane co tekstowa odpowiedz toola MCP, pod nazwami pol JSON.
- `3` z ostatnia linia `OP-FALLBACK: mcp tool=<nazwa>` - wywolaj ten tool MCP z tymi samymi argumentami; jego aktualny schemat poznaj mechanizmem runtime przed wywolaniem. To zwykla sciezka operacji bez komendy CLI (Pipelines, `log_time`, graf, LLM Wiki). Brak tego toola w runtime: stop z nazwa brakujacej operacji.
- `2` - blad operacji (API, siec, przekroczony czas). Przy zapisie nie ponawiaj na slepo przez MCP: najpierw odczytem sprawdz, czy zapis doszedl.
- `64` - blad uzycia wrappera, CLI nie bylo wolane. Popraw argumenty.

`get_ticket` przez CLI nie zwraca `spec_page_id` (linia `OP-NOTE:` na stderr) - gdy pole jest potrzebne, doczytaj je toolem MCP `get_ticket`.

Subagent pracuje przez MCP, chyba ze dostal w prompcie wynik `mode=cli` i literalna sciezke wrappera. `MONOLYNX_TRANSPORT=mcp` w srodowisku wymusza MCP w preflighcie i we wrapperze (zachowanie sprzed wprowadzenia CLI).
<!-- /shared:transport -->

---

## KROK 1: Wykryj graphify

Sprawdz czy graphify jest w PATH (komenda zalezna od systemu):

```bash
# macOS / Linux (takze Git Bash na Windows):
command -v graphify || echo "BRAK"
```

Na Windows PowerShell odpowiednik to `Get-Command graphify` lub `where.exe graphify`.

**Jesli graphify JEST** - przejdz do KROK 2.

**Jesli BRAK** - zapytaj uzytkownika (przez dostępne narzędzie pytań albo wiadomość) o preferowana metode instalacji i podaj instrukcje dla jego systemu. Pakiet PyPI to **`graphifyy`** (podwojne "y"), komenda to `graphify`:

| System | Instalacja |
|---|---|
| macOS / Linux | `uv tool install graphifyy` (rekomendowane) lub `pipx install graphifyy` |
| Windows (PowerShell) | `uv tool install graphifyy` lub `pipx install graphifyy`; po instalacji **zrestartuj terminal** (PATH) |

Troubleshooting `command not found` po instalacji:
- sprawdz czy `~/.local/bin` (uv) jest w PATH: `echo $PATH`
- uv: `uv tool update-shell` i nowy terminal
- pelna dokumentacja: https://github.com/Graphify-Labs/graphify (sekcja Troubleshooting)

Po instalacji zweryfikuj: `graphify --version`. Jesli uzytkownik nie chce instalowac - zakoncz skill z informacja, ze graf mozna tez zasilic przez CI (`/monolynx:create-graph-ci-script`) albo recznie (formularze modulu Polaczenia).

---

## KROK 2: Zweryfikuj / wygeneruj `.graphifyignore`

Sprawdz czy w korzeniu repo istnieje `.graphifyignore`:

- **Istnieje** - przeczytaj go i ocen, czy wyklucza testy/migracje/vendored code. Jesli wyglada sensownie - zostaw.
- **Brak** - utworz na bazie struktury projektu (dostosuj katalogi!):

```
tests/
test/
migrations/
alembic/
docs/
scripts/
node_modules/
vendor/
static/
templates/
.venv/
*.md
*.png
*.svg
*.ico
```

Uzasadnienie dla uzytkownika: bez ignore graf bywa 5x wiekszy, a 70-80% wezlow to testy; limity `replace_graph` to 20 000 wezlow / 60 000 krawedzi.

---

## KROK 3: Ekstrakcja

```bash
graphify update .
```

Poinformuj uzytkownika: sam AST, offline, bez LLM i bez kosztow API. Po zakonczeniu zweryfikuj, ze powstal `graphify-out/graph.json` i podaj jego rozmiar.

---

## KROK 4: Mapowanie i push

1. **Sprawdz czy istnieje `cicd/sync_graph.py`** (wygenerowany przez `/monolynx:create-graph-ci-script`):
   - **Istnieje** - uzyj go (jedna logika mapowania dla CI i lokalnie, zero dryfu).
   - **Brak** - wygeneruj go zgodnie z lokalną specyfikacją `references/graph-mapper.md` (kopia KROK 3 skilla `create-graph-ci-script`) (cienki mapper graph.json -> `replace_graph`, stdlib only, mapowanie wg wiki "Mapowanie taksonomii Graphify -> Monolynx"). NIE wymyslaj wlasnego mapowania.

2. **Najpierw dry-run** (pokaz uzytkownikowi liczby):

```bash
python cicd/sync_graph.py --dry-run
```

3. **Push** - poinformuj uzytkownika, ze `replace_graph` PODMIENIA caly graf projektu (kasuje stary, wstawia nowy) i poczekaj na potwierdzenie:

```bash
MONOLYNX_URL=<MONOLYNX_URL> MONOLYNX_PROJECT_SLUG=<PROJECT-SLUG> python cicd/sync_graph.py
```

(`MONOLYNX_GRAPH_TOKEN` musi byc w srodowisku - patrz KROK 0.)

Windows PowerShell: zmienne ustawia sie przez `$env:MONOLYNX_GRAPH_TOKEN="osk_..."` przed wywolaniem.

---

## KROK 5: Raport koncowy

1. Odczytaj statystyki z outputu skryptu (`deleted/inserted nodes+edges, skipped_edges`).
2. Opcjonalnie potwierdz stan serwera: `mcp__monolynx__get_graph_stats(project_slug="<PROJECT-SLUG>")`.
3. Wyswietl podsumowanie:

```
=== Graf zsynchronizowany ===
Wgrano:    <N> wezlow / <M> krawedzi
Pominieto: <K> krawedzi (brakujacy endpoint / relacje poza zakresem v1)
Podglad:   <MONOLYNX_URL>/dashboard/<PROJECT-SLUG>/connections/
Nastepnym razem wystarczy: graphify update . && python cicd/sync_graph.py
Automatyzacja po merge do main: /monolynx:create-graph-ci-script
```

---

## WAZNE ZASADY

1. **Nie instaluj niczego bez zgody uzytkownika** - instalacja graphify zawsze po potwierdzeniu (przez dostępne narzędzie pytań albo wiadomość).
2. **Token nigdy w czacie** - tylko zmienna srodowiskowa; nie wypisuj wartosci.
3. **Jedna logika mapowania** - zawsze przez `cicd/sync_graph.py` (istniejacy lub wygenerowany wg lokalnej specyfikacji `references/graph-mapper.md`). Zrodlem prawdy mapowania jest wiki "Mapowanie taksonomii Graphify -> Monolynx".
4. **Dry-run przed pushem** - uzytkownik widzi liczby i potwierdza podmiane grafu.
5. **Graf code-only (v1)** - rationale/docs/pakiety/symbole zewnetrzne sa pomijane; brak krawedzi INHERITS po filtrze to nie blad.
6. **Cross-OS** - na Windows uzywaj `Get-Command`/`where.exe` i `$env:` zamiast unixowych odpowiednikow.
7. **Blad syncu nie jest krytyczny** - graf to warstwa dodatkowa; przy bledzie pokaz komunikat skryptu i zaproponuj ponowienie, nie eskaluj.
