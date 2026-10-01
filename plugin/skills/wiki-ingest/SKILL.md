---
name: wiki-ingest
description: "Zintegruj nowe zrodlo (plik, URL lub wklejona tresc) z wiki projektu metoda LLM Wiki. Tworzy strone zrodla, aktualizuje powiazane strony, linkuje wikilinkami, odswieza katalog i dziennik. Uzyj gdy masz dokument lub artykul do wlaczenia w wiki."
user-invocable: true
argument-hint: [sciezka pliku / URL / temat zrodla]
---

# INGEST - integracja zrodla z wiki

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Realizujesz operacje **INGEST** metody LLM Wiki: bierzesz jedno zrodlo (dokument, artykul, transkrypcje, wklejona tresc) i wpisujesz jego wiedze trwale w wiki projektu. Wiki to narastajacy artefakt - jedno dobre zrodlo zwykle dotyka wielu stron, a nie jednej. Twoim celem jest **aktualizowac istniejace strony**, nie duplikowac wiedzy, i wszystko **linkowac wikilinkami**, zeby graf rosl.

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

Kody wyjscia wrappera:

- `0` - stdout to JSON API v2 z CLI. Niesie te same dane co tekstowa odpowiedz toola MCP, pod nazwami pol JSON.
- `3` z ostatnia linia `OP-FALLBACK: mcp tool=<nazwa>` - wywolaj ten tool MCP z tymi samymi argumentami; jego aktualny schemat poznaj mechanizmem runtime przed wywolaniem. To zwykla sciezka operacji bez komendy CLI (Pipelines, `log_time`, graf, LLM Wiki). Brak tego toola w runtime: stop z nazwa brakujacej operacji.
- `2` - blad operacji (API, siec, przekroczony czas). Przy zapisie nie ponawiaj na slepo przez MCP: najpierw odczytem sprawdz, czy zapis doszedl.
- `64` - blad uzycia wrappera, CLI nie bylo wolane. Popraw argumenty.

`get_ticket` przez CLI nie zwraca `spec_page_id` (linia `OP-NOTE:` na stderr) - gdy pole jest potrzebne, doczytaj je toolem MCP `get_ticket`.

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

## Krok 0: Warunek wstepny - metoda musi byc wlaczona

Sprawdz stan:

```
mcp__monolynx__get_wiki_config(project_slug="<PROJECT_SLUG>")
```

- **Jesli `wiki_llm_enabled` jest `false`** - metoda nie jest jeszcze wlaczona. Poinformuj uzytkownika: _"Metoda LLM Wiki nie jest wlaczona dla tego projektu. Uruchom najpierw `/monolynx:wiki-init`."_ i **przerwij** skill.
- **Jesli `true`** - zapamietaj `schema_page_id` (regulamin) i `index_page_id` (katalog), kontynuuj.

---

## Krok 1: Ustal zrodlo

Faktyczny argument wywołania może być sciezka pliku, adresem URL albo tematem/wklejona trescia.

- **Sciezka pliku** - przeczytasz go w Kroku 3 narzedziem `Read`.
- **URL** - pobierzesz go w Kroku 3 narzedziem `WebFetch`.
- **Wklejona tresc / temat** - uzyj tego co podal uzytkownik.
- **Brak argumentu** - zapytaj uzytkownika (przez dostępne narzędzie pytań albo wiadomość): _"Jakie zrodlo chcesz zintegrowac z wiki? Podaj sciezke pliku, URL albo wklej tresc."_ Poczekaj na odpowiedz; nie kontynuuj bez zrodla.

---

## Krok 2: Przeczytaj regulamin wiki

ZAWSZE zacznij od regulaminu, zeby trzymac sie konwencji tego konkretnego projektu (typy stron, frontmatter, format markera sprzecznosci, zasada summary):

```
mcp__monolynx__get_wiki_page(project_slug="<PROJECT_SLUG>", page_id="<schema_page_id>")
```

Zastosuj jego zasady w calym ingest. Najwazniejsze z regulaminu:

- **Typy stron**: `encja` (modul/model/endpoint/usluga/tabela), `koncept` (idea/wzorzec/decyzja), `źródło` (streszczenie jednego dokumentu), `synteza` (przekrojowe opracowanie).
- **Frontmatter** na poczatku `content`: `type`, `status` (`aktywna`|`szkic`|`przestarzała`), `ostatni_przeglad` (`YYYY-MM-DD`), `tagi`.
- **Summary**: pierwsza nie-naglowkowa linia strony = 1-2 zdania (trafia do `wiki-index`).
- **Linkowanie**: ZAWSZE wikilink ze slugiem docelowej strony, nigdy pelny URL.
- **Marker sprzecznosci** w dokladnej formie: `> **Sprzeczność [YYYY-MM-DD]:** ...`.

---

## Krok 3: Przeczytaj zrodlo w calosci i omow wnioski

- Plik: `Read(file_path="<sciezka>")`.
- URL: `WebFetch(url="<url>", prompt="streszcz tresc i wyciagnij kluczowe fakty")`.
- Wklejona tresc: uzyj bezposrednio.

Przeczytaj **calosc**, nie fragmenty. Nastepnie omow z uzytkownikiem:

- co jest istotne i warte zapisania,
- co jest sporne lub przeczy temu, co moze juz byc w wiki,
- ktore obszary projektu zrodlo dotyka.

---

## Krok 4: Znajdz powiazane istniejace strony

Zanim cokolwiek utworzysz, sprawdz co juz jest - zeby AKTUALIZOWAC, nie duplikowac:

```
mcp__monolynx__search_wiki(project_slug="<PROJECT_SLUG>", query="<glowny temat zrodla>")
```

Dla szerszego rozeznania struktury mozesz tez wylistowac strony:

```
mcp__monolynx__list_wiki_pages(project_slug="<PROJECT_SLUG>")
```

Dla kandydatow do aktualizacji pobierz pelna tresc (`get_wiki_page`) i sprawdz powiazania (`get_wiki_backlinks`), zeby zrozumiec, gdzie strona siedzi w grafie.

---

## Krok 5: Zapisz i zaktualizuj strony

Typ strony ustalasz **przez frontmatter YAML** na poczatku `content` (narzedzia `create_wiki_page` / `update_wiki_page` nie maja parametru `type`). Slug generuje sie automatycznie z tytulu.

1. **Strona zrodla** (typ `źródło`) - jedna na ingestowane zrodlo, ze streszczeniem najwazniejszych faktow. Linkuj z niej wikilinkami do stron encji/konceptow, ktorych dotyczy.

   ```
   mcp__monolynx__create_wiki_page(
     project_slug="<PROJECT_SLUG>",
     title="<naturalny tytul zrodla>",
     content="---\ntype: źródło\nstatus: aktywna\nostatni_przeglad: <YYYY-MM-DD>\ntagi: [...]\n---\n\n<1-2 zdania summary>\n\n<streszczenie + wikilinki>"
   )
   ```

2. **Strony encji i konceptow** - jedno dobre zrodlo dotyka wielu stron. Dla kazdego istotnego bytu/idei:
   - jesli strona **istnieje** - `update_wiki_page(project_slug="<PROJECT_SLUG>", page_id=..., content=...)` (wzbogac tresc, dodaj wikilinki),
   - jesli **nie istnieje** - `create_wiki_page(...)` z odpowiednim `type` (`encja` lub `koncept`) we frontmatterze.

3. **Linkowanie** - wszystko lacz wikilinkami ze slugami docelowych stron (nie pelne URL), zeby graf powiazan rosl.

4. **Sprzecznosci** - gdy nowe zrodlo przeczy istniejacej tresci, **NIE nadpisuj po cichu**. Dodaj na stronie marker w dokladnej formie:

   ```
   > **Sprzeczność [<YYYY-MM-DD>]:** Zrodlo A mowi X, dotychczasowa tresc mowi Y. Nierozstrzygniete.
   ```

   Flaga zostaje, az czlowiek rozstrzygnie spor.

---

## Krok 6: Odswiez katalog

```
mcp__monolynx__regenerate_wiki_index(project_slug="<PROJECT_SLUG>")
```

Katalog `wiki-index` zbiera streszczenia wszystkich stron; po dodaniu/zmianie stron trzeba go przebudowac.

---

## Krok 7: Dopisz wpis do dziennika

```
mcp__monolynx__append_wiki_log(project_slug="<PROJECT_SLUG>", entry="INGEST: <co zaingestowano> - utworzono N stron, zaktualizowano M stron")
```

Dziennik `wiki-log` jest append-only - nie kasuj historii.

---

## Podsumowanie dla uzytkownika

Na koniec pokaz:

- jakie zrodlo zintegrowano,
- ile stron utworzono i ile zaktualizowano (z tytulami),
- czy oznaczono jakies sprzecznosci do rozstrzygniecia,
- przypomnienie, ze katalog `wiki-index` jest odswiezony.

---

## Wazne zasady

1. **Najpierw regulamin** - Krok 2 jest obowiazkowy, trzymaj sie konwencji projektu.
2. **Aktualizuj, nie duplikuj** - zawsze szukaj istniejacych stron (Krok 4) przed tworzeniem nowych.
3. **Jedno zrodlo dotyka wielu stron** - nie poprzestawaj na samej stronie `źródło`.
4. **Wszystko wikilinkami** - nigdy pelnymi URL do stron wewnetrznych.
5. **Sprzecznosci flaguj, nie nadpisuj** - dokladny format markera, decyzje zostawia czlowiekowi.
6. **Index i log na koncu** - po kazdym ingest odswiez katalog i dopisz wpis do dziennika.
7. **Jezyk**: polski (terminy techniczne i nazwy narzedzi w oryginale).
