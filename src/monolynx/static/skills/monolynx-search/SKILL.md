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
