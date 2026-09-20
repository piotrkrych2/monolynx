---
name: monolynx-wiki-init
description: "Wlacz metode LLM Wiki dla biezacego projektu. Tworzy strony systemowe (regulamin, katalog, dziennik) i wlacza flage. Uzyj gdy chcesz zaczac prowadzic wiki metoda LLM Wiki (wg Karpathy'ego)."
user-invocable: true
argument-hint: []
---

# Inicjalizacja metody LLM Wiki

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Wlaczasz dla biezacego projektu metode **LLM Wiki** (wg pomyslu Andreja Karpathy'ego): wiki to narastajacy, kompilowany artefakt wiedzy, ktory pisze i utrzymuje agent AI, a czlowiek dostarcza zrodla i zadaje pytania. Ten skill jednorazowo przygotowuje projekt: tworzy strony systemowe i wlacza flage metody.

---

## Ustalenie slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika, następnie `MONOLYNX_PROJECT_SLUG` w środowisku procesu, następnie jawnie odczytana konfiguracja klienta lub `.env` projektu. Konfigurację czytaj jako dane, bez `source` i bez wypisywania sekretów. Brak sluga: zapytaj użytkownika i zatrzymaj operacje projektowe; nie zgaduj go. W przykładach MCP używaj `<PROJECT-SLUG>`.

## Warunek wstepny: branch integracyjny z konfiguracji

Operacje zapisu do wiki (create / update / delete stron) wykonuj wylacznie z brancha integracyjnego projektu, nie z literalu `main`/`master`. Wiki ma odzwierciedlac stan zmergowany do glownej galezi, a nie prace w toku na branchu feature.

Ustal branch integracyjny (`<branch_integracyjny>`) w tej kolejnosci, kazda komende uruchamiajac OSOBNO i wpisujac wynik do kolejnych komend jako literal (bez `$VAR` i `$(...)` - sesja w worktree je odrzuca):

a) `MONOLYNX_BASE_BRANCH` z linii konfiguracji niepusta -> ta wartosc.
b) `MONOLYNX_MR_TARGET` niepusta -> ta wartosc. Branch integracyjny jest domyslnie tez bazowym - jedna konfiguracja zamiast dwoch.
c) `git symbolic-ref refs/remotes/origin/HEAD` -> wynik `refs/remotes/origin/<nazwa>`, bierzesz `<nazwa>`. Blad (np. "is not a symbolic ref" w klonie bez `git remote set-head origin -a`) albo pusty wynik -> d).
d) `glab repo view -F json --jq .default_branch` -> nazwa domyslnego brancha projektu w GitLabie.
e) Nic nie dalo wyniku - NIE zgaduj `main`. Potraktuj to jak "jestes poza branchem integracyjnym" (bezpieczny default: nie zapisuj) i **zakoncz** z komunikatem: _"Nie udalo sie ustalic brancha integracyjnego. Ustaw `MONOLYNX_BASE_BRANCH` albo `MONOLYNX_MR_TARGET` i uruchom ponownie."_ Nie pytaj o kontynuacje - nie ma z czym porownac aktualnego brancha.

Gdy `<branch_integracyjny>` jest ustalony, sprawdz aktualny branch:

```bash
git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(brak repozytorium git)"
```

- **Branch rowny `<branch_integracyjny>`** - kontynuuj normalnie.
- **Inny branch albo brak repozytorium git** - NIE zapisuj automatycznie. Zapytaj uzytkownika (przez dostępne narzędzie pytań albo wiadomość): _"Jestes na branchu `<branch>`, nie na branchu integracyjnym `<branch_integracyjny>`. Zapis do wiki zalecany dopiero po merge. Kontynuowac mimo to?"_ z opcjami:
  - **Nie, przerwij (zalecane)** - zakoncz bez zapisu do wiki.
  - **Tak, kontynuuj mimo to** - przejdz dalej.

  Rekomendujesz **Nie**. Czekaj na decyzje uzytkownika; bez wyraznej zgody nie zapisuj do wiki.

---

## Krok 1: Sprawdz aktualny stan

Zanim cokolwiek utworzysz, sprawdz czy metoda nie jest juz wlaczona:

```
mcp__monolynx__get_wiki_config(project_slug="<PROJECT-SLUG>")
```

Zwraca `wiki_llm_enabled` oraz id stron systemowych (`schema_page_id`, `log_page_id`, `index_page_id`) lub `null`, gdy strona jeszcze nie istnieje. To narzedzie dziala niezaleznie od flagi - mozesz go wywolac zawsze.

Decyzja:

- **Jesli `wiki_llm_enabled` jest `true` i wszystkie trzy strony systemowe istnieja** (id nie sa null) - metoda jest juz wlaczona. Poinformuj uzytkownika i zapytaj (przez dostępne narzędzie pytań albo wiadomość):
  - **Odswiez** - ponowny bootstrap jest idempotentny: odbuduje katalog `wiki-index` i dopisze wpis do dziennika `wiki-log`, nie nadpisze ani nie usunie istniejacych stron.
  - **Przerwij** - nic nie rob, zakoncz.

  Poczekaj na decyzje. Jesli **Przerwij** - zakoncz skill z krotka informacja, ze metoda jest juz aktywna (podaj linki/id stron systemowych z `get_wiki_config`).

- **W przeciwnym razie** (flaga off lub brak ktorejs strony) - przejdz do Kroku 2.

---

## Krok 2: Bootstrap metody

Wywolaj:

```
mcp__monolynx__bootstrap_wiki_llm(project_slug="<PROJECT-SLUG>")
```

Co robi to narzedzie (idempotentnie, samo wlacza flage):

- tworzy `wiki-schema` - **regulamin** metody LLM Wiki: typy stron, frontmatter, konwencje linkowania, workflow INGEST/QUERY/LINT, marker sprzecznosci. Agent czyta go na poczatku kazdej operacji,
- tworzy `wiki-index` - **katalog** wszystkich stron ze streszczeniami, odbudowywany automatycznie (nie edytuj recznie),
- tworzy `wiki-log` - **dziennik** operacji w trybie append-only,
- ustawia `wiki_llm_enabled = true` dla projektu.

Bootstrap kataloguje tez istniejace strony wiki (jesli jakies sa) do `wiki-index`.

---

## Krok 3: Pokaz wynik i nastepne kroki

Bootstrap zwraca m.in. `wiki_llm_enabled`, `schema_page_id`, `log_page_id`, `index_page_id`, `catalogued_pages`, `message`.

Podsumuj uzytkownikowi:

- metoda LLM Wiki jest **wlaczona**,
- utworzone/odswiezone strony systemowe z ich id (regulamin `wiki-schema`, katalog `wiki-index`, dziennik `wiki-log`),
- ile stron skatalogowano (`catalogued_pages`).

Dodaj wskazowki:

- **Regulamin (`wiki-schema`) jest edytowalny** - to najwazniejszy plik konfiguracyjny metody. Wspolewoluuje z projektem; dostraj konwencje, gdy zajdzie potrzeba, i dopisuj uzasadnienie do `wiki-log`.
- **Nastepny krok**: uruchom `/monolynx:wiki-ingest`, zeby zintegrowac pierwsze zrodlo (dokument, artykul, wklejona tresc) z wiki.
- Do audytu zdrowia wiki sluzy `/monolynx:wiki-lint`.

---

## Wazne zasady

1. **Nie pomijaj Kroku 1** - zawsze sprawdz stan przez `get_wiki_config`, zanim wywolasz bootstrap. Gdy metoda juz dziala, pytaj uzytkownika o decyzje (odswiez/przerwij).
2. **Bootstrap jest idempotentny** - bezpieczny do ponownego uruchomienia, nie niszczy danych.
3. **Slug systemowy ma prefiks `wiki-`** - poprawne nazwy stron systemowych to `wiki-index`, `wiki-log`, `wiki-schema`.
4. **Jezyk**: polski (terminy techniczne i nazwy narzedzi w oryginale).
