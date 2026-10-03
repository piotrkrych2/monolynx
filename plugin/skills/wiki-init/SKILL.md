---
name: wiki-init
description: "Wlacz metode LLM Wiki dla biezacego projektu. Tworzy strony systemowe (regulamin, katalog, dziennik) i wlacza flage. Uzyj gdy chcesz zaczac prowadzic wiki metoda LLM Wiki (wg Karpathy'ego)."
user-invocable: true
argument-hint: []
---

# Inicjalizacja metody LLM Wiki

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Wlaczasz dla biezacego projektu metode **LLM Wiki** (wg pomyslu Andreja Karpathy'ego): wiki to narastajacy, kompilowany artefakt wiedzy, ktory pisze i utrzymuje agent AI, a czlowiek dostarcza zrodla i zadaje pytania. Ten skill jednorazowo przygotowuje projekt: tworzy strony systemowe i wlacza flage metody.

---

## Ustalenie slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika, następnie `MONOLYNX_PROJECT_SLUG` w środowisku procesu, następnie jawnie odczytana konfiguracja klienta lub `.env` projektu. Konfigurację czytaj jako dane, bez `source` i bez wypisywania sekretów. Brak sluga: zapytaj użytkownika i zatrzymaj operacje projektowe; nie zgaduj go. W przykładach MCP używaj `<PROJECT_SLUG>`.

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
python3 <katalog skilla>/scripts/monolynx_op.py get_ticket --project_slug <PROJECT_SLUG> --ticket_id <KEY>
```

Wartosc bool to `true` albo `false`, lista to tablica JSON w jednej wartosci. Dluga tresc (komentarz, opis, strona wiki) i kazda wartosc ze znakami specjalnymi powloki: zapisz do pliku i podaj jako `@<sciezka pliku>`. Komend `monolynx ...` nie skladaj samodzielnie - mapowanie operacji na komendy CLI zyje we wrapperze.

Tryb zwiezly: `--compact true` przy `list_tickets`, gdy lista sluzy do przegladu statusow i blokerow bez czytania opisow (elementy bez pola `description`), oraz `--brief true` przy `create_ticket` i `update_ticket`, gdy po zapisie nie czytasz opisu (wynik to tylko pola `id`, `key`, `title`, `status`, `sprint_id`, `blocked_by`, `blocks`). `compact` i `brief` to parametry wrappera, a nie tooli MCP: przy `mode=mcp` i przy fallbacku (kod 3) wywolujesz tool MCP bez nich. Wrapper sam niczego nie skraca - bez tych flag wynik jest pelny.

Kody wyjscia wrappera:

- `0` - stdout to JSON API v2 z CLI. Niesie te same dane co tekstowa odpowiedz toola MCP, pod nazwami pol JSON.
- `3` z ostatnia linia `OP-FALLBACK: mcp tool=<nazwa>` - wywolaj ten tool MCP z tymi samymi argumentami, z pominieciem parametrow wrappera `compact` i `brief` (tool MCP ich nie ma); jego aktualny schemat poznaj mechanizmem runtime przed wywolaniem. To zwykla sciezka operacji bez komendy CLI (Pipelines, `log_time`, zapis grafu, LLM Wiki). Brak tego toola w runtime: stop z nazwa brakujacej operacji.
- `2` - blad operacji (API, siec, przekroczony czas). Przy zapisie nie ponawiaj na slepo przez MCP: najpierw odczytem sprawdz, czy zapis doszedl.
- `64` - blad uzycia wrappera, CLI nie bylo wolane. Popraw argumenty.

Subagent pracuje przez MCP, chyba ze dostal w prompcie wynik `mode=cli` i literalna sciezke wrappera. `MONOLYNX_TRANSPORT=mcp` w srodowisku wymusza MCP w preflighcie i we wrapperze (zachowanie sprzed wprowadzenia CLI).
<!-- /shared:transport -->

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
mcp__monolynx__get_wiki_config(project_slug="<PROJECT_SLUG>")
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
mcp__monolynx__bootstrap_wiki_llm(project_slug="<PROJECT_SLUG>")
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
