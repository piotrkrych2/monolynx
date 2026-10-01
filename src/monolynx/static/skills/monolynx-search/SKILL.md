---
name: monolynx-search
description: Szukaj informacji w wiki projektu na platformie Monolynx. Użyj gdy użytkownik pyta o dokumentację projektu, architekturę, API, integracje, standardy kodu lub inne informacje zapisane w wiki Monolynx. Trigger na słowa "monolynx", "wiki", "szukaj w wiki", "sprawdź w monolynx", "co mamy w wiki", "jak działa" (w kontekście dokumentacji projektu).
---

# Wyszukiwanie w Wiki Monolynx

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

## Kiedy użyć

Użyj tego Skill'a gdy użytkownik:
- Pyta "sprawdź w monolynx", "szukaj w wiki", "co mamy w wiki"
- Pyta o dokumentację projektu (architektura, API, integracje, standardy)
- Chce wiedzieć jak coś działa w projekcie i informacja może być w wiki
- Wspomina "monolynx", "wiki", "dokumentacja projektu"

## Ustalenie slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika, następnie `MONOLYNX_PROJECT_SLUG` w środowisku procesu, następnie jawnie odczytana konfiguracja klienta lub `.env` projektu. Konfigurację czytaj jako dane, bez `source` i bez wypisywania sekretów. Brak sluga: zapytaj użytkownika i zatrzymaj operacje projektowe; nie zgaduj go. W przykładach MCP używaj `<PROJECT-SLUG>`.

## Transport: CLI albo MCP

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

## Proces

### Krok 1: Ustal projekt

Użyj sluga ustalonego według kontraktu runtime. Brak sluga wymaga odpowiedzi użytkownika; pojedynczy projekt z listy nie jest zgodą na jego wybór.

### Krok 2: Wyszukaj w wiki

Użyj `mcp__monolynx__search_wiki` z:
- `project_slug`: ustalony slug projektu
- `query`: pytanie użytkownika (w naturalnym języku)
- `limit`: 5 (domyślnie)

### Krok 3: Pobierz szczegóły

Jeśli wyniki wyszukiwania semantycznego nie wystarczają do pełnej odpowiedzi:
1. Użyj `mcp__monolynx__get_wiki_page` aby pobrać pełną treść najlepiej dopasowanej strony
2. Jeśli potrzeba - pobierz dodatkowe strony

### Krok 4: Odpowiedz

Podaj:
- Bezpośrednią odpowiedź na pytanie użytkownika
- Kluczowe fragmenty z wiki (cytaty, tabele, diagramy)
- Nazwę strony wiki, z której pochodzi informacja
- Jeśli informacja nie została znaleziona - powiedz o tym jasno

### Krok 5: Zapisz odpowiedz z powrotem (QUERY)

To kluczowa zasada metody LLM Wiki (wg Karpathy'ego): dobra synteza wraca do wiki jako trwala strona, zamiast ginac w oknie czatu. Dzieki temu eksploracje sie kumuluja - nastepne pytanie startuje z lepszego miejsca.

**Warunek**: ten krok dziala tylko, gdy metoda LLM Wiki jest wlaczona dla projektu. Sprawdz:

```
mcp__monolynx__get_wiki_config(project_slug="<PROJECT-SLUG>")
```

- **Jesli `wiki_llm_enabled` jest `false`** - pomin ten krok. Zwykle wyszukiwanie (Kroki 1-4) dziala dalej bez zmian.
- **Jesli `true` i odpowiedz jest wartosciowa** (przekrojowa synteza, a nie trywialny lookup) - zapytaj uzytkownika (przez dostępne narzędzie pytań albo wiadomość): _"Zapisac te odpowiedz jako strone typu synteza w wiki?"_

**Jesli uzytkownik sie zgodzi**:

Zanim cokolwiek zapiszesz, sprawdz branch - zapis syntezy do wiki rob wylacznie z brancha `main` lub `master`:

```bash
git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(brak repozytorium git)"
```

Jesli to nie `main`/`master` (albo brak repozytorium git), NIE zapisuj automatycznie. Zapytaj uzytkownika (przez dostępne narzędzie pytań albo wiadomość): _"Jestes na branchu `<branch>`, nie na main/master. Zapis do wiki zalecany dopiero po merge do main. Zapisac synteze mimo to?"_ z opcjami **Nie, przerwij (zalecane)** oraz **Tak, zapisz mimo to**. Rekomendujesz **Nie**; bez wyraznej zgody pomin zapis (odpowiedz z Krokow 1-4 zostala juz udzielona).

1. Utworz strone syntezy. Typ ustalasz przez frontmatter YAML na poczatku `content` (slug generuje sie automatycznie z tytulu):

   ```
   mcp__monolynx__create_wiki_page(
     project_slug="<PROJECT-SLUG>",
     title="<pytanie lub temat>",
     content="---\ntype: synteza\nstatus: aktywna\nostatni_przeglad: <YYYY-MM-DD>\ntagi: [...]\n---\n\n<1-2 zdania summary>\n\n<synteza z cytatami i wikilinkami do stron zrodlowych>"
   )
   ```

   Linkuj wikilinkami ze slugami stron zrodlowych (nigdy pelne URL).

2. Odswiez katalog:

   ```
   mcp__monolynx__regenerate_wiki_index(project_slug="<PROJECT-SLUG>")
   ```

3. Dopisz wpis do dziennika:

   ```
   mcp__monolynx__append_wiki_log(project_slug="<PROJECT-SLUG>", entry="QUERY: zapisano synteze - <temat>")
   ```

4. Pokaz uzytkownikowi link/tytul nowej strony syntezy.

## Wskazówki

- Wyszukiwanie semantyczne (`search_wiki`) jest najlepsze do szerokich pytań
- Do przeglądania struktury wiki użyj `list_wiki_pages`
- Odpowiadaj w języku, w którym pyta użytkownik
- Nie kopiuj całych stron - wyciągaj istotne fragmenty
- Jeśli wiki nie zawiera odpowiedzi, zaproponuj przeszukanie kodu źródłowego
