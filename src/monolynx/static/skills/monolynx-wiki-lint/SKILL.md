---
name: monolynx-wiki-lint
description: "Audyt zdrowia wiki projektu (metoda LLM Wiki). Wykrywa sieroty, martwe linki, sprzecznosci i luki, prezentuje raport i proponuje naprawy. Uzyj gdy chcesz sprawdzic spojnosc wiki i ja uporzadkowac."
user-invocable: true
argument-hint: []
---

# LINT - audyt zdrowia wiki

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Realizujesz operacje **LINT** metody LLM Wiki: okresowy audyt spojnosci wiki. Sprawdzasz, czy graf wiedzy jest spojny (brak sierot i martwych linkow), czy nie ma nierozstrzygnietych sprzecznosci i czy nie brakuje stron dla wielokrotnie wzmiankowanych konceptow. Po raporcie pomagasz uzytkownikowi naprawic znalezione problemy.

---

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

Tryb zwiezly: `--compact true` przy `list_tickets`, gdy lista sluzy do przegladu statusow i blokerow bez czytania opisow (elementy bez pola `description`), oraz `--brief true` przy `create_ticket` i `update_ticket`, gdy po zapisie nie czytasz opisu (wynik to tylko pola `id`, `key`, `title`, `status`, `sprint_id`, `blocked_by`, `blocks`). `compact` i `brief` to parametry wrappera, a nie tooli MCP: przy `mode=mcp` i przy fallbacku (kod 3) wywolujesz tool MCP bez nich. Wrapper sam niczego nie skraca - bez tych flag wynik jest pelny.

Kody wyjscia wrappera:

- `0` - stdout to JSON API v2 z CLI. Niesie te same dane co tekstowa odpowiedz toola MCP, pod nazwami pol JSON.
- `3` z ostatnia linia `OP-FALLBACK: mcp tool=<nazwa>` - wywolaj ten tool MCP z tymi samymi argumentami, z pominieciem parametrow wrappera `compact` i `brief` (tool MCP ich nie ma); jego aktualny schemat poznaj mechanizmem runtime przed wywolaniem. To zwykla sciezka operacji bez komendy CLI (Pipelines, `log_time`, zapis grafu, LLM Wiki). Brak tego toola w runtime: stop z nazwa brakujacej operacji.
- `2` - blad operacji (API, siec, przekroczony czas). Przy zapisie nie ponawiaj na slepo przez MCP: najpierw odczytem sprawdz, czy zapis doszedl.
- `64` - blad uzycia wrappera, CLI nie bylo wolane. Popraw argumenty.

Subagent pracuje przez MCP, chyba ze dostal w prompcie wynik `mode=cli` i literalna sciezke wrappera. `MONOLYNX_TRANSPORT=mcp` w srodowisku wymusza MCP w preflighcie i we wrapperze (zachowanie sprzed wprowadzenia CLI).
<!-- /shared:transport -->

## Krok 0: Warunek wstepny - metoda musi byc wlaczona

Sprawdz stan:

```
mcp__monolynx__get_wiki_config(project_slug="<PROJECT-SLUG>")
```

- **Jesli `wiki_llm_enabled` jest `false`** - poinformuj uzytkownika: _"Metoda LLM Wiki nie jest wlaczona dla tego projektu. Uruchom najpierw `/monolynx:wiki-init`."_ i **przerwij** skill.
- **Jesli `true`** - kontynuuj.

---

## Krok 1: Uruchom audyt

```
mcp__monolynx__lint_wiki(project_slug="<PROJECT-SLUG>")
```

Zwraca raport z czterema listami i licznikiem pominietych stron:

- `orphans` - strony bez zadnego backlinku przychodzacego,
- `dead_links` - wikilinki do nieistniejacych stron,
- `contradictions` - strony z markerem sprzecznosci (`> **Sprzeczność ...`),
- `gaps` - koncepty wzmiankowane wielokrotnie jako wikilink, ale bez wlasnej strony,
- `skipped_generated` - liczba stron generowanych maszynowo (np. logow pipeline'ow), pominietych przy liczeniu sierot.

---

## Krok 2: Zaprezentuj raport

Pokaz raport czytelnie, w czterech sekcjach. Dla kazdej kategorii podaj liste pozycji i krotkie wyjasnienie, co oznacza:

### Sieroty (`orphans`)

Strony, do ktorych nic nie prowadzi. Nie da sie do nich dotrzec przez nawigacje grafu - latwo o nich zapomniec. Strony generowane maszynowo (np. logi pipeline'ow) sa z tej listy wylaczone z definicji - ich liczbe podaj osobno w podsumowaniu na podstawie `skipped_generated` (np. "Sieroty: N (pominieto M stron generowanych maszynowo)").

### Martwe linki (`dead_links`)

Wikilinki wskazujace na strony, ktore nie istnieja. Albo brakuje strony docelowej, albo slug w linku jest bledny.

### Sprzecznosci (`contradictions`)

Strony z markerem sprzecznosci. Czekaja na rozstrzygniecie przez czlowieka - zawieraja dwie wersje, ktore sie wykluczaja.

### Luki (`gaps`)

Koncepty wzmiankowane wielokrotnie jako wikilink, ale bez wlasnej strony. Sygnal, ze warto utworzyc dla nich dedykowana strone.

Jesli ktoras lista jest pusta - napisz to wprost (np. "Sieroty: brak").

---

## Warunek: naprawy tylko z brancha main/master

Audyt (Kroki 1-2) jest tylko do odczytu i dziala na kazdym branchu. Natomiast **naprawy** (create / update / delete stron w Kroku 3) wykonuj wylacznie z brancha `main` lub `master` - wiki ma odzwierciedlac stan zmergowany, nie prace w toku. Przed jakakolwiek naprawa sprawdz branch:

```bash
git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "(brak repozytorium git)"
```

- **Branch `main` lub `master`** - mozesz wykonywac naprawy.
- **Inny branch albo brak repozytorium git** - NIE naprawiaj automatycznie. Zapytaj uzytkownika (przez dostępne narzędzie pytań albo wiadomość): _"Jestes na branchu `<branch>`, nie na main/master. Zmiany w wiki zalecane dopiero po merge do main. Kontynuowac naprawy mimo to?"_ z opcjami:
  - **Nie, przerwij (zalecane)** - pokaz raport, ale nie zapisuj zmian.
  - **Tak, kontynuuj mimo to** - wykonaj naprawy.

  Rekomendujesz **Nie**. Czekaj na decyzje; bez wyraznej zgody nie modyfikuj stron.

---

## Krok 3: Zaproponuj naprawy

Dla kazdej niepustej kategorii zaproponuj konkretna akcje naprawcza:

- **Sieroty** - dolinkuj strone z `wiki-index` lub z powiazanej tematycznie strony (dodaj wikilink przez `update_wiki_page`). Uzyj `get_wiki_backlinks`, zeby znalezc dobre miejsce na link.
- **Martwe linki** - albo utworz brakujaca strone docelowa (`create_wiki_page` z odpowiednim frontmatterem), albo popraw bledny slug w stronie zrodlowej (`update_wiki_page`).
- **Sprzecznosci** - rozstrzygnij z uzytkownikiem ktora wersja jest aktualna, zaktualizuj tresc i **usun marker** sprzecznosci ze strony (`update_wiki_page`).
- **Luki** - rozwaz utworzenie strony konceptu (`create_wiki_page`, `type: koncept`) i podlaczenie do niej istniejacych wzmianek.

Zapytaj uzytkownika (przez dostępne narzędzie pytań albo wiadomość), ktore problemy naprawic teraz - nie naprawiaj wszystkiego automatycznie. Sprzecznosci ZAWSZE wymagaja decyzji czlowieka. Po decyzji wykonaj zaakceptowane naprawy.

---

## Krok 4 (opcjonalnie): Dopisz wpis do dziennika

Jesli cokolwiek naprawiono, odnotuj audyt w dzienniku:

```
mcp__monolynx__append_wiki_log(project_slug="<PROJECT-SLUG>", entry="LINT: audyt zdrowia - naprawiono <co> (sieroty: N, martwe linki: M, sprzecznosci: K, luki: L)")
```

---

## Podsumowanie dla uzytkownika

Na koniec pokaz:

- liczbe znalezionych problemow w kazdej kategorii, przy sierotach dopisz liczbe pominietych stron generowanych maszynowo z `skipped_generated` (np. "Sieroty: N (pominieto M stron generowanych maszynowo)"),
- co naprawiono w tej sesji,
- co pozostaje do rozstrzygniecia (zwlaszcza nierozstrzygniete sprzecznosci).

---

## Wazne zasady

1. **Warunek wstepny** - Krok 0 jest obowiazkowy; bez wlaczonej metody `lint_wiki` zwroci blad.
2. **Sprzecznosci rozstrzyga czlowiek** - nigdy nie usuwaj markera bez decyzji uzytkownika.
3. **Pytaj przed naprawa** - nie modyfikuj stron automatycznie; zatwierdz zakres przez dostępne narzędzie pytań lub wiadomość.
4. **Odnotuj audyt** - jesli cos naprawiono, dopisz wpis do `wiki-log`.
5. **Jezyk**: polski (terminy techniczne i nazwy narzedzi w oryginale).
