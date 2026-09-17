---
name: mutation-check
description: "Uruchom przebieg testow mutacyjnych ograniczony do zmiany, pokaz przezywajacych mutantow i zamien ich na sledzona prace w Monolynx - kryteria akceptacji przy tickecie albo tickety w backlogu. Komendy i format raportu czyta z sekcji Mutacje strony wiki toolchain, przebieg raportuje jako job w module Pipelines. Uzyj gdy chcesz sprawdzic, czy testy faktycznie lapia zmiany w kodzie."
user-invocable: true
argument-hint: [sciezka modulu (opcjonalnie)]
allowed-tools: mcp__monolynx__list_wiki_pages, mcp__monolynx__get_wiki_page, mcp__monolynx__update_wiki_page, mcp__monolynx__get_ticket, mcp__monolynx__add_acceptance_criterion, mcp__monolynx__create_ticket, mcp__monolynx__create_pipeline_job, mcp__monolynx__update_pipeline_job, mcp__monolynx__append_job_log, AskUserQuestion, Bash, Read, Grep, Glob
---

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

### 0.1. Zaladuj narzedzia

Dwa wywolania rownolegle:

```
ToolSearch(query="+monolynx wiki page list get update", max_results=10)
ToolSearch(query="+monolynx ticket acceptance criterion pipeline job log", max_results=10)
```

Potrzebne toole: `list_wiki_pages`, `get_wiki_page`, `update_wiki_page`, `get_ticket`,
`add_acceptance_criterion`, `create_ticket` oraz opcjonalnie `create_pipeline_job`,
`update_pipeline_job`, `append_job_log`.

**Prefix tooli - ustal RAZ, nie zgaduj**: nazwy w tym skillu zapisane sa jako
`mcp__monolynx__<tool>` - to placeholder. Rzeczywisty prefix zalezy od instalacji
(`mcp__monolynx__*` plugin lokalny, `mcp__claude_ai_Monolynx__*` connector globalny
claude.ai, w innych runtime'ach inny). Odczytaj prefix z nazw zwroconych przez ToolSearch i
uzywaj go konsekwentnie. Zapytanie z `+monolynx` zwrocilo pusto mimo podpietego connectora
-> powtorz bez `+`. Runtime bez ToolSearch (toole dostepne od razu) -> uzywaj ich
bezposrednio.

W szablonach podstawiaj realne wartosci za `<PROJECT_SLUG>`, `<pipeline_id>`, `<job_id>`.
Nie zostawiaj placeholderow w faktycznych wywolaniach.

### 0.2. Slug projektu

Pierwsza znaleziona wartosc wygrywa:

```bash
echo "${MONOLYNX_PROJECT_SLUG:-(nie ustawiono)}"
```

1. `MONOLYNX_PROJECT_SLUG` ze srodowiska lub `.env` projektu.
2. `user_config.project_slug` pluginu.
3. Fallback `monolynx`.

Slug podany wprost przez uzytkownika bije wszystkie trzy.

### 0.3. Budzet czasu

```bash
echo "BUDZET=${MONOLYNX_MUTATION_BUDGET:-600}s"
```

Domyslnie **600 sekund (10 minut)**. Wartosc wieksza niz 600 skapuj do 600 i powiedz o tym
uzytkownikowi: narzedzie Bash przyjmuje maksymalnie `timeout: 600000` ms. Budzet
egzekwujesz parametrem `timeout` narzedzia Bash (w milisekundach), NIE komenda `timeout` /
`gtimeout` - na macOS bez coreutils ich nie ma.

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
- `user` -> zapytaj (AskUserQuestion): odpalic teraz w tej sesji, czy uzytkownik uruchomi
  komende u siebie i wklei sciezke raportu?

Uruchomienie (`<budzet_ms>` to budzet z 0.3 w milisekundach):

```
Bash(command="<komenda z toolchain>", timeout=<budzet_ms>, description="Przebieg mutacyjny")
```

### Przekroczenie budzetu

Nigdy nie zostawiaj wiszacego terminala. Po wyczerpaniu budzetu sprawdz plik z pola
`raport`:

- **Plik istnieje** -> sparsuj co jest i oznacz caly wynik jako **CZESCIOWY**. Napisz
  wprost, ze przebieg zostal przerwany po `<N>` sekundach, wiec liczba mutantow jest dolna
  granica, a nie pelnym obrazem.
- **Pliku nie ma** -> "budzet wyczerpany, brak raportu". Podaj uzytkownikowi komende do
  recznego dokonczenia i sposob podniesienia budzetu:

  ```bash
  MONOLYNX_MUTATION_BUDGET=600 # sekundy, maksimum 600
  ```

  Wiecej niz 600 s -> przebieg nalezy do uzytkownika, poza sesja. Zaproponuj wezszy zakres
  (pojedynczy modul, pojedynczy plik) zamiast dluzszego czekania.

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

Zapytaj uzytkownika (AskUserQuestion; runtime bez tego toola -> zwykle pytanie i czekasz):

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
`success` z jawnym "CZESCIOWY" w `summary`.

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
7. **Budzet zamiast wiszacego terminala** - domyslnie 600 s przez `timeout` narzedzia Bash;
   przekroczenie daje raport czesciowy albo jawne "brak raportu" z komenda do dokonczenia.
8. **Jeden parser** - dołączony `scripts/mutation_report.py`, kopia źródła z pluginu. Skill nie ma wlasnego silnika ani
   wlasnego formatu raportu; narzedzie bez parsera zglaszasz wprost.
9. **Pipeline jest best-effort** - blad toola pipeline nigdy nie przerywa skilla, a w trybie
   samodzielnym pipeline w ogole nie powstaje.
10. **Podstawiaj `<PROJECT_SLUG>`** i pozostale placeholdery realnymi wartosciami. Slug
    bierze sie z `MONOLYNX_PROJECT_SLUG`, potem `user_config.project_slug`, na koncu
    `monolynx`.
