"""Domyślne treści stron systemowych wiki (metoda LLM Wiki).

Moduł zawiera wyłącznie stałe string z domyślną treścią markdown. Bez importów
i bez logiki. `DEFAULT_WIKI_SCHEMA` to regulamin metody LLM Wiki zapisywany przy
bootstrapie projektu jako strona o slugu `wiki-schema`.
"""

DEFAULT_WIKI_SCHEMA: str = """# Regulamin wiki (metoda LLM Wiki)

Ta strona to regulamin utrzymania wiki tego projektu metodą LLM Wiki (wg pomysłu Andreja Karpathy'ego). Czyta ją agent AI na początku każdej operacji INGEST, QUERY i LINT, żeby wiedzieć JAK pisać i utrzymywać wiki. To najważniejszy plik konfiguracyjny metody.

Podział ról: agent AI pisze i utrzymuje wiki, człowiek dostarcza źródła i zadaje pytania. Wiki to narastający, kompilowany artefakt wiedzy o projekcie - nie buduje się jej od zera przy każdym pytaniu (to nie RAG ad hoc). Każde źródło i każda odpowiedź wzbogaca trwały zasób, który z czasem gęstnieje i sam staje się najlepszym kontekstem.

## Typy stron

Każda strona ma jeden z czterech typów (pole `type` we frontmatterze):

- `encja` - rzeczywisty byt w projekcie: moduł, model, endpoint API, usługa, tabela.
- `koncept` - idea, wzorzec lub decyzja projektowa (np. "graceful degradation", "fingerprint issue").
- `źródło` - streszczenie pojedynczego źródła: dokument, artykuł, transkrypcja, wątek.
- `synteza` - przekrojowe opracowanie łączące wiele stron: porównanie, przegląd, teza, odpowiedź na pytanie.

Zasada rozmiaru: jedna strona = jeden temat, możliwy do przeczytania za jednym posiedzeniem (jak hasło w Wikipedii). Gdy strona puchnie i miesza tematy - podziel ją i połącz linkami.

## Frontmatter

Na początku strony może (opcjonalnie, ale zalecane) stać blok YAML:

```yaml
---
type: encja        # encja | koncept | źródło | synteza
status: aktywna    # aktywna | szkic | przestarzała
ostatni_przeglad: 2026-05-22
tagi: [scrum, api, sdk]
---
```

Pola:

- `type` - typ strony (patrz wyżej).
- `status` - `aktywna`, `szkic` lub `przestarzała`.
- `ostatni_przeglad` - data ostatniego przeglądu w formacie `YYYY-MM-DD`.
- `tagi` - lista etykiet tematycznych.

## Konwencje nazw i linkowania

- Slug: lowercase z myślnikami, np. `modul-scrum`, `decyzja-uuid-pk`.
- Tytuł: naturalny, czytelny dla człowieka (np. "Moduł Scrum").
- Linkowanie wewnętrzne: ZAWSZE wikilink w formie podwójnych nawiasów kwadratowych ze slugiem docelowej strony, nigdy pełny URL. Parser backlinków rozpoznaje ten format i buduje z niego graf powiązań. Pełne adresy `http(s)://...` nie tworzą backlinku.
- Summary: pierwsza nie-nagłówkowa linia strony to 1-2 zdaniowe streszczenie. Trafia ono do katalogu na stronie `wiki-index`, więc pisz je tak, by samodzielnie mówiło o czym jest strona.

Przykład wikilinka w tekście: zobacz stronę modułu zapisaną jako podwójny nawias kwadratowy ze slugiem `modul-scrum` w środku.

## Workflow - trzy operacje

### INGEST (dodanie źródła)

1. Przeczytaj źródło w całości.
2. Omów wnioski z człowiekiem - co jest istotne, co sporne, co warto zapisać.
3. Zapisz lub zaktualizuj stronę źródła (typ `źródło`) ze streszczeniem.
4. Zaktualizuj powiązane strony encji i konceptów. Jedno dobre źródło zwykle dotyka 10-15 stron - nie poprzestawaj na jednej.
5. Linkuj wszystko wikilinkami, żeby graf rósł.
6. Dopisz wpis do dziennika na stronie `wiki-log`.

Nie nadpisuj treści po cichu. Gdy nowe źródło przeczy temu co już jest - oznacz sprzeczność (patrz niżej).

### QUERY (pytanie)

1. Przeszukaj wiki - najpierw katalog `wiki-index`, potem wyszukiwanie semantyczne.
2. Syntetyzuj odpowiedź z cytatami i wikilinkami do stron źródłowych.
3. KLUCZOWE: dobrą odpowiedź zapisz z powrotem jako nową stronę typu `synteza`. Dzięki temu eksploracje się kumulują w wiki, zamiast ginąć w oknie czatu. Następne pytanie startuje z lepszego miejsca.

### LINT (audyt zdrowia)

Okresowo audytuj spójność wiki. Szukaj:

- sierot - stron bez żadnego backlinku przychodzącego,
- martwych linków - wikilinków do nieistniejących stron,
- sprzeczności - stron z markerem sprzeczności,
- luk - konceptów wzmiankowanych wielokrotnie jako wikilink, ale bez własnej strony,
- przestarzałych twierdzeń - treści oznaczonej `status: przestarzała` lub dawno nieprzeglądanej.

W Monolynx audyt realizuje narzędzie MCP `lint_wiki` - uruchom je i przejdź po raporcie.

## Flagowanie sprzeczności

Gdy nowe źródło przeczy istniejącej treści, NIE nadpisuj jej po cichu. Dodaj na stronie marker sprzeczności. Marker musi mieć dokładną formę zaczynającą się od cytatu z pogrubionym słowem `Sprzeczność` - tak wykrywa go `lint_wiki`:

> **Sprzeczność [2026-05-22]:** Źródło A mówi X, źródło B mówi Y. Nierozstrzygnięte.

Flaga zostaje na stronie, aż człowiek rozstrzygnie spór. Dopiero wtedy aktualizujesz treść i usuwasz marker.

## Strony systemowe

- `wiki-index` - katalog wszystkich stron projektu z ich streszczeniami. Odbudowywany automatycznie narzędziem; nie edytuj go ręcznie.
- `wiki-log` - dziennik operacji w trybie append-only. Dopisuj wpisy przy każdym INGEST i większej syntezie; nie kasuj historii.
- `wiki-schema` - ten regulamin. Współewoluuje z metodą; można go edytować, gdy konwencje się dostrajają.

## Współewolucja

Ten schemat nie jest zamrożony. Gdy odkryjesz lepsze konwencje dla tego konkretnego projektu - zaktualizuj go i dopisz uzasadnienie do `wiki-log`.
"""
