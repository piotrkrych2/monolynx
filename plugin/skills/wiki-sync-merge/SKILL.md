---
name: wiki-sync-merge
description: >
  Post-merge INGEST do wiki metodą LLM Wiki. Odpala człowiek po merge
  ticketów/PR do main. Gated na wiki_llm_enabled. Używaj plain [[slug]].
user-invocable: true
argument-hint: [ticket-id lub lista ticket-id, np. MON-75 MON-76]
---

# wiki-sync-merge - INGEST po merge do main

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Realizujesz **post-merge INGEST** metody LLM Wiki: bierzesz zamknięte tickety (zmergowane do main) i wpisujesz ich wiedzę trwale w wiki projektu. Skill odpala **człowiek ręcznie** po merge, nigdy automatycznie w trakcie pracy nad branchy.

**Zasada nadrzędna**: ZAWSZE używaj plain `[[slug]]` w wikilinkach. NIGDY `[[slug|label]]` - parser aliasów (MON-74) nie jest jeszcze wdrożony.

---

## Ustalenie slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika, następnie `MONOLYNX_PROJECT_SLUG` w środowisku procesu, następnie jawnie odczytana konfiguracja klienta lub `.env` projektu. Konfigurację czytaj jako dane, bez `source` i bez wypisywania sekretów. Brak sluga: zapytaj użytkownika i zatrzymaj operacje projektowe; nie zgaduj go. W przykładach MCP używaj `<PROJECT_SLUG>`.

## Warunek wstępny: branch integracyjny z konfiguracji

Zapis do wiki wykonuj wyłącznie z brancha integracyjnego projektu, nie z literału `main`/`master`.

Ustal branch integracyjny (`<branch_integracyjny>`) w tej kolejności, każdą komendę uruchamiając OSOBNO i wpisując wynik do kolejnych komend jako literał (bez `$VAR` i `$(...)` - sesja w worktree je odrzuca):

a) `MONOLYNX_BASE_BRANCH` z linii konfiguracji niepusta -> ta wartość.
b) `MONOLYNX_MR_TARGET` niepusta -> ta wartość. Branch integracyjny jest domyślnie też bazowym - jedna konfiguracja zamiast dwóch.
c) `git symbolic-ref refs/remotes/origin/HEAD` -> wynik `refs/remotes/origin/<nazwa>`, bierzesz `<nazwa>`. Błąd (np. "is not a symbolic ref" w klonie bez `git remote set-head origin -a`) albo pusty wynik -> d).
d) `glab repo view -F json --jq .default_branch` -> nazwa domyślnego brancha projektu w GitLabie.
e) Nic nie dało wyniku - NIE zgaduj `main`. Potraktuj to jak "jesteś poza branchem integracyjnym" (bezpieczny default: nie zapisuj) i **zakończ** z komunikatem: _"Nie udało się ustalić brancha integracyjnego. Ustaw `MONOLYNX_BASE_BRANCH` albo `MONOLYNX_MR_TARGET` i uruchom ponownie."_

Gdy `<branch_integracyjny>` jest ustalony, sprawdź aktualny branch:

```bash
git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(brak repozytorium git)"
```

- **Branch równy `<branch_integracyjny>`** - kontynuuj normalnie.
- **Inny branch** - NIE zapisuj automatycznie. Poinformuj użytkownika: _"Jesteś na branchu `<branch>`, nie na branchu integracyjnym `<branch_integracyjny>`. Uruchom skill ponownie po merge."_ i **zakończ** bez zapisu.

---

## Krok 0: Gating - sprawdź czy metoda LLM Wiki jest włączona

```
mcp__monolynx__get_wiki_config(project_slug="<PROJECT_SLUG>")
```

- **Jeśli `wiki_llm_enabled` jest `false`** - zakończ z informacją: _"LLM Wiki wyłączona dla tego projektu, INGEST pominięty. Włącz metodę przez `/monolynx:wiki-init`."_ Nie wykonuj żadnych zapisów.
- **Jeśli `true`** - zapamiętaj `schema_page_id` (regulamin) i `index_page_id` (katalog), kontynuuj.

---

## Krok 1: Ustal listę ticketów do przetworzenia

Faktyczne argumenty wywołania to ticket-id lub lista ticket-id (np. `MON-75 MON-76` albo samo UUID).

- **Argumenty podane** - użyj ich bezpośrednio.
- **Brak argumentów** - zapytaj użytkownika: _"Podaj ID ticketów zmergowanych do main (np. MON-75 MON-76 lub UUID). Możesz podać kilka naraz."_ Poczekaj; nie kontynuuj bez listy.

---

## Krok 2: Przeczytaj regulamin wiki

Zawsze zacznij od regulaminu, żeby trzymać się konwencji projektu (typy stron, frontmatter, format markera sprzeczności):

```
mcp__monolynx__get_wiki_page(project_slug="<PROJECT_SLUG>", page_id="<schema_page_id>")
```

Zastosuj jego zasady w całym INGEST. Kluczowe zasady z regulaminu:

- **Typy stron**: `encja`, `koncept`, `źródło`, `synteza`.
- **Frontmatter** na początku `content`: `type`, `status` (`aktywna`|`szkic`|`przestarzała`), `ostatni_przeglad` (`YYYY-MM-DD`), `tagi`.
- **Summary**: pierwsza nie-nagłówkowa linia strony = 1-2 zdania.
- **Linkowanie**: ZAWSZE plain `[[slug]]`, NIGDY `[[slug|label]]` (MON-74 parser aliasów niezakończony).
- **Marker sprzeczności**: `> **Sprzeczność [YYYY-MM-DD]:** ...`

---

## Krok 3: Pobierz kontekst każdego ticketu

Dla każdego ticket-id z listy pobierz dane:

```
mcp__monolynx__get_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<ticket-id>")
```

Zapamiętaj dla każdego ticketu:
- tytuł i opis (źródło wiedzy do INGEST),
- etykiety i moduły, których dotyczy (pomoc przy klasyfikacji stron wiki),
- komentarze - mogą zawierać ustalenia architektoniczne, odkrycia z review.

Jeśli ticket nie istnieje lub nie można go pobrać - odnotuj to i pomiń dany ticket.

---

## Krok 4: INGEST każdego ticketu do wiki

Dla każdego ticketu wykonaj pełny INGEST (wzoruj się na flow z `wiki-ingest`):

### 4a. Znajdź powiązane istniejące strony

Zanim cokolwiek stworzysz, sprawdź co już jest - żeby aktualizować, nie duplikować:

```
mcp__monolynx__search_wiki(project_slug="<PROJECT_SLUG>", query="<kluczowe słowa z tytułu ticketu>")
```

Dla kandydatów do aktualizacji pobierz pełną treść (`get_wiki_page`) i sprawdź powiązania (`get_wiki_backlinks`).

### 4b. Zapisz i zaktualizuj strony

Typ strony ustalasz przez frontmatter YAML na początku `content`.

1. **Strona źródła ticketu** (typ `źródło`) - jedno na ticket, ze streszczeniem co zostało zrobione i dlaczego. Linkuj z niej `[[slug]]` do stron encji/konceptów, których dotyczy zmiana.

   ```
   mcp__monolynx__create_wiki_page(
     project_slug="<PROJECT_SLUG>",
     title="<np. 'MON-75: wiki-sync-merge - INGEST po merge'>",
     content="---\ntype: źródło\nstatus: aktywna\nostatni_przeglad: <YYYY-MM-DD>\ntagi: [...]\n---\n\n<1-2 zdania summary>\n\n<opis co zmieniono + wikilinki [[slug]]>"
   )
   ```

   Strony źródła ticketu twórz tylko jeśli wnoszą realną wiedzę (nie sam fakt "ticket zamknięty"). Pomiń jeśli ticket był drobnym bugfixem bez wartości dokumentacyjnej.

2. **Strony encji i konceptów** - zmiana w tickecie często dotyka kilku modułów. Dla każdego istotnego bytu/idei:
   - jeśli strona **istnieje** - `update_wiki_page(...)` (wzbogać treść, dodaj wikilinki `[[slug]]`),
   - jeśli **nie istnieje** - `create_wiki_page(...)` z odpowiednim `type` we frontmatterze.

3. **Linkowanie** - wszystko łącz przez plain `[[slug]]`. Nigdy pełny URL do stron wewnętrznych, nigdy `[[slug|label]]`.

4. **Sprzeczności** - gdy wiedza z ticketu przeczy istniejącej treści, NIE nadpisuj po cichu. Dodaj marker:

   ```
   > **Sprzeczność [<YYYY-MM-DD>]:** Ticket X mówi Y, dotychczasowa treść mówi Z. Nierozstrzygnięte.
   ```

---

## Krok 5: Odśwież katalog

Po przetworzeniu wszystkich ticketów:

```
mcp__monolynx__regenerate_wiki_index(project_slug="<PROJECT_SLUG>")
```

Katalog `wiki-index` zbiera streszczenia wszystkich stron; po dodaniu/zmianie stron trzeba go przebudować.

---

## Krok 6: Dopisz wpis do dziennika

Jeden wpis zbiorczy dla całego przebiegu (nie osobny na każdy ticket):

```
mcp__monolynx__append_wiki_log(project_slug="<PROJECT_SLUG>", entry="INGEST po merge: <lista ticketów, np. MON-75, MON-76> - utworzono N stron, zaktualizowano M stron")
```

Dziennik `wiki-log` jest append-only - nie kasuj historii.

---

## Podsumowanie dla użytkownika

Na koniec pokaż:

- które tickety przetworzono (i które pominięto z powodu braku wartości dokumentacyjnej),
- ile stron wiki utworzono i ile zaktualizowano (z tytułami),
- czy oznaczono jakieś sprzeczności do rozstrzygnięcia,
- przypomnienie, że katalog `wiki-index` jest odświeżony.

---

## Ważne zasady

1. **Gating jest twardy** - jeśli `wiki_llm_enabled=false`, skill kończy się bez żadnych zapisów.
2. **Tylko branch integracyjny** - żaden zapis do wiki na branchu feature.
3. **Plain `[[slug]]` zawsze** - nigdy `[[slug|label]]` (MON-74 niezakończony).
4. **Aktualizuj, nie duplikuj** - zawsze szukaj istniejących stron przed tworzeniem nowych.
5. **Jedno źródło dotyka wielu stron** - nie poprzestawaj na samej stronie `źródło`.
6. **Sprzeczności flaguj, nie nadpisuj** - dokładny format markera, decyzje zostawiasz człowiekowi.
7. **Index i log na końcu** - po całym przebiegu odśwież katalog i dopisz jeden wpis zbiorczy.
8. **Język**: polski (terminy techniczne i nazwy narzędzi w oryginale).
