---
name: mutation-check
description: "Uruchom przebieg testow mutacyjnych ograniczony do zmiany, pokaz przezywajacych mutantow i zamien ich na sledzona prace w Monolynx - kryteria akceptacji przy tickecie albo tickety w backlogu. Komendy i format raportu czyta z sekcji Mutacje strony wiki toolchain, przebieg raportuje jako job w module Pipelines. Uzyj gdy chcesz sprawdzic, czy testy faktycznie lapia zmiany w kodzie."
user-invocable: true
argument-hint: [sciezka modulu (opcjonalnie)]
---

Przed wykonaniem kroków obowiązkowo odczytaj [Kontrakt runtime](references/runtime.md).

# Mutacje jako praca - przebieg diffowy i survivorzy

Zasoby tego skilla: ustal absolutny katalog otrzymanego pliku `SKILL.md`.
Odnośniki do dołączonych `scripts/`, `references/` i pomocniczych plików
Markdown rozwiązuj względem tego katalogu, nie katalogu roboczego projektu.
Ścieżki plików tworzonych w projekcie pozostają względem projektu. Przed komendami
z `MONOLYNX_SKILL_DIR` przypisz tej lokalnej zmiennej powłoki ustaloną
literalną ścieżkę katalogu skilla; nie oczekuj jej w środowisku systemowym.

Uruchamiasz przebieg mutacyjny **biezacego projektu**, parsujesz raport i zamieniasz
przezywajacych mutantow (survivorow) na sledzona prace w Monolynx. Wartosc tego skilla nie
lezy w samym przebiegu - Strykera czy PIT-a uzytkownik odpali sam. Lezy w tym, ze raport
nie ginie razem z terminalem: survivor staje sie kryterium akceptacji przy tickecie albo
ticketem w backlogu.

Skill **niczego nie instaluje** i **nie jest bramka**. Mutation score to trend, nie prog do
przejscia.

---

## KROK 0: Konfiguracja

### 0.1. Transport i narzedzia

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

Narzedzia MCP (dla `mode=mcp` i dla operacji, przy ktorych wrapper zwraca kod 3, np. Pipelines) - dwa wywolania rownolegle:

Najpierw użyj dostępnego discovery, mechanizmu skilli lub delegowania i jego aktualnego schematu. Treść zadania i wymagany wynik z poniższego przykładu obowiązują również przy innym mechanizmie.

<!-- runtime:claude-code -->
```
ToolSearch(query="+monolynx wiki page list get update", max_results=10)
ToolSearch(query="+monolynx ticket acceptance criterion pipeline job log", max_results=10)
```

<!-- /runtime:claude-code -->
Potrzebne toole: `list_wiki_pages`, `get_wiki_page`, `update_wiki_page`, `get_ticket`,
`add_acceptance_criterion`, `create_ticket` oraz opcjonalnie `create_pipeline_job`,
`update_pipeline_job`, `append_job_log`.

**Prefix tooli - ustal RAZ, nie zgaduj**: nazwy w tym skillu zapisane sa jako
`mcp__monolynx__<tool>` - to placeholder. Rzeczywisty prefix zalezy od instalacji
(`mcp__monolynx__*` plugin lokalny, `mcp__claude_ai_Monolynx__*` connector globalny
claude.ai, w innych runtime'ach inny). Odczytaj prefix z nazw z aktualnego katalogu narzędzi i
uzywaj go konsekwentnie. Zapytanie z `+monolynx` zwrocilo pusto mimo podpietego connectora
-> powtorz bez `+`. Runtime bez ToolSearch: sprawdź faktyczny katalog narzędzi i użyj jego discovery oraz aktualnych schematów.

W szablonach podstawiaj realne wartosci za `<PROJECT_SLUG>`, `<pipeline_id>`, `<job_id>`.
Nie zostawiaj placeholderow w faktycznych wywolaniach.

### 0.2. Slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika > `MONOLYNX_PROJECT_SLUG` procesu > jawna konfiguracja klienta lub `.env` projektu czytane jako dane (nigdy `source`). Brak wartości: zapytaj o slug i zatrzymaj działania projektowe, bez zgadywania.

### 0.3. Budzet czasu

```bash
echo "BUDZET=${MONOLYNX_MUTATION_BUDGET:-600}s"
```

Domyślnie **600 sekund**, maksymalnie 600 sekund; większą wartość ogranicz do 600 i powiedz o tym użytkownikowi. To deadline całego procesu, niezależny od czasu yield lub timeout pojedynczego wywołania narzędzia. Przed startem sprawdź możliwość monitorowania oraz zakończenia procesu i wszystkich potomków. Brak tej kontroli: przekaż komendę użytkownikowi przed startem.

### 0.4. Argument

`$ARGUMENTS` to substytucja slash-commandu Claude Code. W runtime bez niej literalny napis
"$ARGUMENTS" traktuj jak brak argumentu.

- **Brak argumentu** -> tryb diffowy (domyslny).
- **Argument = sciezka modulu** -> tryb pelny na tej sciezce.

---

## KROK 1: Odczytaj sekcje Mutacje ze strony wiki toolchain

Deterministycznie, po slugu - nie przez `search_wiki` (RAG moze zwrocic inna strone, a
slabe trafienie kazaloby uznac, ze konfiguracji nie ma):

```
mcp__monolynx__list_wiki_pages(project_slug="<PROJECT_SLUG>")
```

Z wyniku wez strone o slugu dokladnie `toolchain` i pobierz jej tresc:

```
mcp__monolynx__get_wiki_page(project_slug="<PROJECT_SLUG>", page_id="<page_id>")
```

Z sekcji `## Mutacje` odczytaj pola (etykiety sa kontraktem, nie parafrazuj ich):
`narzedzie`, `powod`, `komenda pelna`, `komenda diffowa`, `raport` (sciezka + format),
`moduly rdzenia`, `baseline`, `cel`, `kto odpala`. Wartosc `brak` jest legalna dla kazdego
pola.

### Degradacja (a): brak strony `toolchain` albo brak naglowka `## Mutacje`

Nie zgaduj komendy. Wypisz i **zakoncz skill**:

> Projekt nie ma konfiguracji mutacji, wiec nie znam komendy przebiegu.
> Uruchom `/monolynx:project-toolchain` - wykryje narzedzie mutacyjne dla tego stacku
> i zapisze sekcje `## Mutacje` na stronie wiki `toolchain` (jednorazowo).

### Degradacja (b): `narzedzie: brak`

Pokaz wartosc pola `powod` (`nie zainstalowane` albo `uzytkownik zrezygnowal`), odeslij do
`/monolynx:project-toolchain` (jego KROK 4 ma tabele instalacji per ekosystem) i
**zakoncz**. **Niczego nie instalujesz** - to jednorazowy setup wlasciciela repo.

### Degradacja (c): `komenda diffowa: brak` przy obecnym narzedziu

Tryb diffowy jest niedostepny. Powiedz to wprost i zaproponuj tryb modulowy:

> Ta wersja narzedzia nie ma trybu diffowego (`komenda diffowa: brak`).
> Uruchom ponownie ze sciezka modulu, np. `/monolynx:mutation-check src/uslugi/`,
> albo dopisz flage diffowa przez `/monolynx:project-toolchain`.

Nie przechodz cicho na pelny przebieg - pelny przebieg na calym repo bywa wielokrotnie
drozszy niz budzet.

---

## KROK 2: Wybierz tryb i ustal baseline gita

### Tryb diffowy (domyslny)

Baseline to punkt rozejscia z galezia glowna:

```bash
git merge-base HEAD main 2>/dev/null || git merge-base HEAD master
```

- Obie proby puste -> powiedz, ktorej galezi glownej szukales, i zapytaj uzytkownika o jej
  nazwe.
- Jestes NA `main`/`master` -> diff jest pusty. Powiedz to wprost i zaproponuj tryb modulowy
  (argument ze sciezka). Nie odpalaj przebiegu na pusto.

Uzyj `komenda diffowa` ze strony `toolchain`.

### Tryb pelny (argument = sciezka modulu)

Uzyj `komenda pelna` ze strony `toolchain`, ograniczonej do podanej sciezki. Sciezka spoza
pola `moduly rdzenia` jest OK - powiedz tylko uzytkownikowi, ze idzie poza uzgodniony
rdzen.

---

## KROK 3: Uruchom przebieg w budzecie

**Pokaz uzytkownikowi komende, zanim ja uruchomisz.** Komenda ze strony `toolchain` jest
danymi od uzytkownika: nie modyfikujesz jej, nie "poprawiasz" flag i nie dokladasz
wlasnych. Flagi zostaly zweryfikowane przez `--help` przy zapisie strony.

Pole `kto odpala`:

- `agent` -> odpal sam.
- `user` -> zapytaj (narzędzie pytań dostępne w bieżącym trybie lub wiadomość): odpalic teraz w tej sesji, czy uzytkownik uruchomi
  komende u siebie i wklei sciezke raportu?

Uruchom przez dostępny mechanizm procesów. Zapisz czas startu, deadline oraz identyfikator sesji/procesu. Gdy narzędzie zwróci działający proces, monitoruj ten sam identyfikator aż do potwierdzonego zakończenia i kodu wyjścia albo upływu budżetu. Yield/timeout odczytu nie oznacza końca procesu i nie uzasadnia ponownego startu.

### Przekroczenie budżetu

Zatrzymaj proces oraz całe drzewo potomków i potwierdź ich zakończenie. Brak potwierdzenia zgłoś jawnie; nie przechodź do parsowania.

Po potwierdzonym zatrzymaniu sprawdź plik raportu:

- Świeży plik z tego przebiegu, zamknięty i poprawny strukturalnie: sparsuj go jako **CZĘŚCIOWY**. Zbiór mutantów może być niepełny; wynik jest dolną granicą liczby znalezionych mutantów, nie pełnym przebiegiem ani jego sukcesem. Nie aktualizuj baseline na tej podstawie.
- Stary, nadal zapisywany, nieparsowalny albo nieistniejący plik: zgłoś „budżet wyczerpany, brak raportu”. Nie przetwarzaj jego zawartości jako wyniku.

Podaj komendę do ręcznego dokończenia.

Przed parsowaniem po normalnym zakończeniu potwierdź, że raport pochodzi z tego przebiegu i jest kompletny. Niezerowy exit wyjaśnij zgodnie z kontraktem narzędzia; nie traktuj samej obecności pliku jako sukcesu.

---

## KROK 4: Sparsuj raport

Parsowanie idzie WYLACZNIE przez skrypt pluginu - skill nie ma wlasnego parsera ani
wlasnego formatu:

```bash
python3 "${MONOLYNX_SKILL_DIR}/scripts/mutation_report.py" --report <sciezka z pola raport> --format <format>
```

Format bierzesz z pola `raport` strony `toolchain`. Mapowanie narzedzie -> format:

| Narzedzie | `--format` |
|---|---|
| Stryker, Stryker.NET, Infection | `mte-json` |
| PIT | `pit-xml` |
| mutmut (wyjscie `mutmut results --all true`) | `mutmut` |
| cargo-mutants (`outcomes.json`) | `cargo-mutants` |

**Brak parsera**: gremlins, Stryker4s, mutant (Ruby). Powiedz to wprost - nie udawaj, ze
raport zostal przetworzony. Pokaz uzytkownikowi surowy raport "do reki" i zakoncz na
prezentacji; KROK 7 wtedy odpada.

Skrypt wypisuje JSON w jednej linii na stdout: `score` (float 0-100 albo `null`, gdy
`total == 0`), `total`, `killed`, `survived`,
`survivors[{file, line, mutator, original, replacement, status, description}]`.

Kody wyjscia:

- `0` - OK.
- `2` - blad wejscia (brak pliku, niepoprawny JSON/XML, raport pusty, niezgodny ze
  schematem formatu). Pokaz komunikat ze stderr (prefiks `mutation_report: `) i sprawdz z
  uzytkownikiem sciezke oraz format ze strony `toolchain`. Pusta lista survivorow NIGDY nie
  jest tu odpowiedzia - zero survivorow to kod `0`.
- `64` - blad uzycia (zle argumenty). To blad po Twojej stronie: popraw wywolanie.

---

## KROK 5: Pokaz wynik

### Tabela survivorow

```
| plik:linia | mutator | oryginal -> mutant |
|---|---|---|
| src/rozliczenia.py:88 | ConditionalBoundary | `>=` -> `>` |
```

Pola bywaja puste i to normalne: `pit-xml` nie ma `original`/`replacement` (ma
`description`), `mutmut` nie ma `line`/`original`/`replacement`. Wtedy:

- Brak `line` -> sam plik, bez `:linia`.
- Brak `original`/`replacement`, jest `description` -> wpisz `description`.
- Brak jednego i drugiego -> `?`.

Nigdy nie wypisuj literalnego `None` ani `null` - to wyciek reprezentacji, nie informacja.
Survivorow wiecej niz 30 -> pokaz pierwszych 30 i podaj laczna liczbe.

### Tryb diffowy: liczba, nie procent

Raportujesz **liczbe survivorow** i liczbe mutantow lacznie. Procentu NIE podajesz. Powiedz
uzytkownikowi dlaczego, jednym zdaniem: przy kilku mutantach w diffie procent skacze o
dziesiatki punktow miedzy przebiegami i nie niesie informacji.

```
Mutacje (diff wzgledem <baseline_sha>): 4 survivorow / 17 mutantow
```

### Tryb pelny: score, baseline, delta

```
Mutacje (modul <sciezka>): score 71.4% | baseline 68.0% (2026-08-02) | delta +3.4 pp
```

`baseline: brak` -> to pierwszy przebieg. Zaproponuj zapis wyniku jako baseline i zapisz
**tylko za zgoda uzytkownika**:

```
mcp__monolynx__update_wiki_page(project_slug="<PROJECT_SLUG>", page_id="<page_id strony toolchain>", content="<tresc strony z uzupelnionym polem baseline>")
```

Baseline moze tylko **rosnac** (zapadka). Nowy wynik nizszy od zapisanego -> nie nadpisuj,
tylko pokaz spadek. Score nigdy nie jest bramka: nie mow "nie przeszlo" i niczego nie
blokuj.

---

## KROK 6: Anty-gaming - przeczytaj przed KROK 7

To nie jest przypis. Bez tego kroku skill produkuje testy, ktore podnosza liczbe i psuja
projekt.

1. **Survivor to brakujacy PRZYPADEK TESTOWY**, nie "mutant do zabicia". Pytanie brzmi:
   jakiego zachowania nikt nie sprawdza, skoro ta zmiana kodu nie wywolala czerwonego testu?
2. **Mutant rownowazny semantycznie**: zmiana, ktora nie zmienia obserwowalnego zachowania
   (np. `<=` -> `<` na granicy nieosiagalnej, kolejnosc bez efektu ubocznego). Uzasadnij
   jednym zdaniem i **odpusc**. To poprawna odpowiedz, nie porazka i nie dlug.
3. **ZAKAZ asercji pinujacych szczegoly implementacji** tylko po to, zeby podniesc score.
   Test sprawdzajacy, ze zmienna nazywa sie tak a nie inaczej albo ze petla ma dokladnie
   trzy obroty, kupuje punkt i placi za niego kazdym przyszlym refaktorem.
4. **Slowa "zabij mutanta" nie wolno uzyc** w tresci kryteriow ani ticketow. Odbiorca ma
   przeczytac, czego brakuje w testach, a nie zagadke o narzedziu.
5. **Przed napisaniem tresci przeczytaj kod** wokol linii survivora (Read) i nazwij
   zachowanie wlasnymi slowami. Nie kopiuj mutanta jeden do jednego.

Szablon tresci kryterium / ticketu:

> Test nie wykrywa zmiany `<original>` -> `<replacement>` w `<plik>:<linia>` (`<mutator>`).
> Brakuje przypadku sprawdzajacego `<zachowanie wyprowadzone z kodu>`.

---

## KROK 7: Zamien survivorow na prace

Zapytaj uzytkownika (narzędzie pytań dostępne w bieżącym trybie lub wiadomość; runtime bez tego toola -> zwykle pytanie i czekasz):

| Opcja | Co robi |
|---|---|
| 1. Kryteria przy tickecie | Popros o klucz/ID ticketu, potem `add_acceptance_criterion` per survivor |
| 2. Ticket per survivor | `create_ticket` dla kazdego survivora osobno |
| 3. Jeden ticket zbiorczy | `create_ticket` z lista survivorow w `acceptance_criteria` |
| 4. Tylko raport | Nic nie zapisujesz |

**Przed kazdym zapisem pokaz gotowa tresc do akceptacji.** Survivory oznaczone w KROK 6
jako rownowazne **pomijasz** przy zapisie - wymien je osobno w raporcie z uzasadnieniem.

Opcja 1:

```
mcp__monolynx__get_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<klucz PROJ-12 albo UUID>")
mcp__monolynx__add_acceptance_criterion(project_slug="<PROJECT_SLUG>", ticket_id="<ID>", description="<tresc wg szablonu z KROK 6>")
```

Opcja 2 (per survivor):

```
mcp__monolynx__create_ticket(project_slug="<PROJECT_SLUG>", title="Test: <zachowanie> w <plik>", description="<tresc wg szablonu z KROK 6>", priority="low")
```

Opcja 3 (jeden zbiorczy):

```
mcp__monolynx__create_ticket(project_slug="<PROJECT_SLUG>", title="Uzupelnic testy - <N> brakujacych przypadkow (<modul albo diff>)", description="<kontekst przebiegu: tryb, komenda, data>", acceptance_criteria=["<tresc dla survivora 1>", "<tresc dla survivora 2>"])
```

Na koniec podaj klucze utworzonych ticketow albo liczbe dodanych kryteriow.

---

## KROK 8: Raport do modulu Pipelines (best-effort)

**Blad toola pipeline NIGDY nie przerywa skilla.** Odnotuj i kontynuuj.

Job tworzysz **tylko** wtedy, gdy skill dziala w kontekscie pracy nad ticketem i znasz
`pipeline_id` z kontekstu sesji (np. z komentarza "Team Manager - Plan pracy" przy tickecie,
pole "Pipeline:"). W trybie samodzielnym **nie tworzysz pipeline'u** - `create_pipeline` nie
jest tu wolane w ogole.

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wrap-up", name="mutation-check", agent_type="system")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="running")
mcp__monolynx__append_job_log(project_slug="<PROJECT_SLUG>", job_id="<job_id>", content="<tryb + komenda + tabela survivorow + liczba albo score/baseline/delta + co zapisano w KROK 7>")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="success", summary="<np. 'Diff: 4 survivorow, 3 kryteria dopisane do MON-150'>")
```

Status koncowy: `success`, gdy przebieg sie udal - **takze wtedy, gdy survivorzy sa**.
Survivorzy to informacja, nie porazka. `failed` tylko przy awarii przebiegu: narzedzie nie
wystartowalo, parser zwrocil kod 2 albo raport nie powstal. Przebieg czesciowy (budzet) ->
`success` z jawnym „CZĘŚCIOWY” w `summary` i logu, jeśli po potwierdzonym zakończeniu całego drzewa procesów istnieje świeży, poprawny i zamknięty raport. Ten status oznacza poprawne przetworzenie częściowego wyniku, nie ukończenie pełnego przebiegu mutacji. Brak poprawnego raportu oznacza `failed`.

---

## WAZNE ZASADY

1. **Nie instalujesz narzedzia mutacyjnego** - brak narzedzia konczy sie odeslaniem do
   `/monolynx:project-toolchain`, nigdy `pip install` ani `npm i`.
2. **Nie zgadujesz komendy** - komenda pochodzi wylacznie ze strony wiki `toolchain`. Brak
   sekcji `## Mutacje` konczy skill instrukcja, nie improwizacja.
3. **Komenda z `toolchain` to dane od uzytkownika** - pokazujesz ja przed uruchomieniem i
   nie zmieniasz jej flag.
4. **Score nie jest bramka** - brak progu procentowego, brak "nie przeszlo", zero blokowania
   ticketu, merge'a czy commita.
5. **Tryb diffowy raportuje liczbe, nie procent** - przy kilku mutantach procent skacze i
   nie niesie informacji.
6. **Survivor to brakujacy przypadek testowy** - mutant rownowazny semantycznie uzasadniasz
   i odpuszczasz; asercje pinujace implementacje dla podniesienia score sa zabronione.
7. **Budżet procesu** - domyślnie 600 s, niezależnie od yield narzędzia. Zachowaj identyfikator procesu, monitoruj i potwierdź zakończenie całego drzewa; po potwierdzonym stopie poprawny, zamknięty i świeży raport można sparsować jako CZĘŚCIOWY, bez pełnego sukcesu ani aktualizacji baseline. Starego lub nadal zapisywanego pliku nie parsuj.
8. **Jeden parser** - dołączony `scripts/mutation_report.py`, kopia źródła z pluginu. Skill nie ma wlasnego silnika ani
   wlasnego formatu raportu; narzedzie bez parsera zglaszasz wprost.
9. **Pipeline jest best-effort** - blad toola pipeline nigdy nie przerywa skilla, a w trybie
   samodzielnym pipeline w ogole nie powstaje.
10. **Podstawiaj `<PROJECT_SLUG>`** i pozostale placeholdery realnymi wartosciami. Slug
    ustalaj według kontraktu runtime, bez domyślnego projektu.
