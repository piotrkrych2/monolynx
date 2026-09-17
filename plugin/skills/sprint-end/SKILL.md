---
name: sprint-end
description: "Zamknij sprint jako pipeline sprint_close: zintegruj logi pracy z wiki (INGEST), zaudytuj wiki (LINT), wyczysc logi pipeline'ow sprintu, uruchom pelny przebieg mutacyjny z trendem i domknij sprint. Przebieg raportowany do modulu Pipelines (best-effort). Uzyj gdy chcesz zamknac sprint."
user-invocable: true
argument-hint: [nazwa sprintu (opcjonalnie)]
allowed-tools: mcp__monolynx__list_sprints, mcp__monolynx__list_wiki_pages, mcp__monolynx__get_wiki_page, mcp__monolynx__create_wiki_page, mcp__monolynx__update_wiki_page, mcp__monolynx__create_pipeline, mcp__monolynx__create_pipeline_job, mcp__monolynx__update_pipeline_job, mcp__monolynx__append_job_log, mcp__monolynx__finish_pipeline, mcp__monolynx__complete_sprint, mcp__monolynx__clean_pipeline_logs, AskUserQuestion, Bash, Skill
---

# SPRINT-END - zamkniecie sprintu jako pipeline sprint_close

Orkiestrujesz zamkniecie sprintu w projekcie Monolynx. Calosc modelujesz jako pipeline `sprint_close` z dwoma stepami: **wiki-update** (zebranie wiedzy ze sprintu do wiki) i **wrap-up** (realne zamkniecie sprintu i podsumowanie). Pipeline to warstwa obserwowalnosci - raportujesz do niego **best-effort**, ale nigdy nie pozwalasz, by blad raportowania zablokowal zamkniecie sprintu.

---

## Ustalenie slug projektu

Slug projektu pochodzi ze zmiennej srodowiskowej `MONOLYNX_PROJECT_SLUG`. Sprawdz ja:

```bash
echo "${MONOLYNX_PROJECT_SLUG:-(nie ustawiono)}"
```

- **Zmienna ustawiona** - uzyj jej wartosci jako `project_slug` we wszystkich wywolaniach narzedzi MCP ponizej. Slug podany wprost przez uzytkownika ma pierwszenstwo.
- **Zmienna nie ustawiona** - NIE zgaduj sluga i NIE rozpoczynaj pracy. Popros uzytkownika, by skonfigurowal slug w pliku `.claude/settings.json` projektu (pole `env`), po czym uruchomil skill ponownie:

  ```json
  {
    "env": { "MONOLYNX_PROJECT_SLUG": "twoj-slug-projektu" }
  }
  ```

  Zakoncz bez dalszych akcji, dopoki slug nie jest znany.

---

## KROK 1: Ustal sprint do zamkniecia

`$ARGUMENTS` moze byc nazwa sprintu albo puste.

- **Podano nazwe** - wylistuj sprinty i znajdz pasujacy po nazwie:

  ```
  mcp__monolynx__list_sprints(project_slug="<PROJECT_SLUG>")
  ```

  Dopasuj sprint po polu nazwy (case-insensitive). Gdy nie znajdziesz - wypisz czytelny komunikat PL (_"Nie znalazlem sprintu o nazwie `<nazwa>`. Dostepne sprinty: ..."_) i **przerwij**.

- **Brak argumentu** - wez aktywny sprint:

  ```
  mcp__monolynx__list_sprints(project_slug="<PROJECT_SLUG>", status="active")
  ```

  - Dokladnie jeden aktywny sprint - uzyj go.
  - Brak aktywnego sprintu - wypisz czytelny komunikat PL (_"Brak aktywnego sprintu do zamkniecia. Podaj nazwe sprintu jako argument albo uruchom sprint."_) i **przerwij**.
  - Wiecej niz jeden aktywny sprint - zapytaj uzytkownika (`AskUserQuestion`), ktory zamknac, i poczekaj na wybor.

Zapamietaj `sprint_id` (UUID) oraz nazwe wybranego sprintu - uzywasz ich do konca skilla.

---

## KROK 2: Utworz pipeline sprint_close (instrumentacja - best-effort)

```
mcp__monolynx__create_pipeline(project_slug="<PROJECT_SLUG>", pipeline_type="sprint_close", sprint_id="<sprint_id>")
```

Zwraca `pipeline_id` oraz dwa stepy: `wiki-update` i `wrap-up`. **Zapamietaj `pipeline_id`**.

**Best-effort**: jesli wywolanie zwroci blad - odnotuj i kontynuuj BEZ instrumentacji pipeline. Jesli toole pipeline sa niedostepne (starszy serwer MCP bez modulu Pipelines lub bez typu `sprint_close`), pomin tylko WARSTWE instrumentacji (`create_pipeline_job`, `update_pipeline_job`, `append_job_log`, `finish_pipeline`), ale dalej wykonaj cala realna prace domkniecia: INGEST, LINT, CLEAN (KROK 3) oraz zamkniecie sprintu (KROK 4). `monolynx:wiki-ingest`, `monolynx:wiki-lint`, `clean_pipeline_logs` i `complete_sprint` nie zaleza od modulu Pipelines - dzialaja niezaleznie. Tak samo przebieg mutacyjny (4a): `monolynx:mutation-check`, strona trendu i zapadka baseline w `toolchain` korzystaja z tooli wiki, nie z modulu Pipelines - brak pipeline degraduje tylko raportowanie przebiegu. Brak pipeline NIGDY nie blokuje ani zamkniecia sprintu, ani aktualizacji wiki - degraduje jedynie raportowanie przebiegu.

---

## KROK 3: Step `wiki-update` - zebranie wiedzy ze sprintu do wiki

Joby wykonuj **sekwencyjnie**. Dla kazdego joba wzorzec instrumentacji (best-effort): `create_pipeline_job` -> `update_pipeline_job(status="running")` -> wykonaj prace -> `append_job_log` -> `update_pipeline_job(status="success"|"failed")`.

### 3a. Job `wiki-ingest`

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wiki-update", name="wiki-ingest", agent_type="skill")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="running")
```

Uruchom skill `monolynx:wiki-ingest` (przez narzedzie `Skill`), wskazujac jako zrodlo logi pracy ze stron "Pipeline logi" tego sprintu - to one zawieraja raporty agentow z pracy nad ticketami sprintu. Skill zintegruje te wiedze z wiki (strony encji/konceptow, wikilinki, katalog, dziennik).

Po zakonczeniu: `append_job_log` z podsumowaniem (ile stron utworzono/zaktualizowano), nastepnie `update_pipeline_job(status="success")` (lub `failed`, gdy ingest sie nie powiodl).

**Zapamietaj wynik ingestu** (sukces/porazka) - decyduje o tym, czy wolno wykonac `wiki-clean` (3c). Jesli INGEST sie nie powiodl, NIE wolno czyscic logow - to one sa jedynym zrodlem wiedzy ze sprintu.

### 3b. Job `wiki-lint`

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wiki-update", name="wiki-lint", agent_type="skill")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="running")
```

Uruchom skill `monolynx:wiki-lint` (przez narzedzie `Skill`). Audyt moze wymagac interakcji z uzytkownikiem przy rozstrzyganiu sprzecznosci - to normalne, poczekaj na jego decyzje.

Po zakonczeniu: `append_job_log` z liczba znalezionych/naprawionych problemow, nastepnie `update_pipeline_job(status="success"|"failed")`.

### 3c. Job `wiki-clean`

> **WARUNEK**: Wykonaj `wiki-clean` TYLKO gdy job `wiki-ingest` (3a) zakonczyl sie sukcesem. `clean_pipeline_logs` trwale usuwa strony "Pipeline logi" sprintu - jesli INGEST sie nie powiodl, wiedza ze sprintu NIE trafila do wiki i te strony sa jej jedynym zrodlem. Czyszczenie po nieudanym ingescie = bezpowrotna utrata raportow pracy agentow.

Gdy INGEST sie nie powiodl - **pomin `wiki-clean`**: utworz job ze statusem `skipped` (lub pomin go calkowicie), w logu/podsumowaniu zaznacz _"Pominieto czyszczenie logow - ingest nieudany, logi zachowane"_ i przejdz do KROKU 4.

Gdy INGEST sie powiodl - strony "Pipeline logi" sprintu nie sa juz potrzebne. Usun je:

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wiki-update", name="wiki-clean", agent_type="system")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="running")
```

```
mcp__monolynx__clean_pipeline_logs(project_slug="<PROJECT_SLUG>", sprint_id="<sprint_id>")
```

Zwraca `{deleted: N}` - liczbe usunietych stron wiki. Zapamietaj `N` do podsumowania.

Po zakonczeniu: `append_job_log` (np. _"Usunieto N stron logow pipeline sprintu"_), nastepnie `update_pipeline_job(status="success"|"failed")`.

---

## KROK 4: Step `wrap-up` - zamkniecie sprintu i podsumowanie

### 4a. Job `mutation-run` - pelny przebieg mutacyjny z trendem (opcjonalny, PIERWSZY w `wrap-up`)

Krok jest **nieblokujacy i pomijalny**. Brak konfiguracji mutacji, brak narzedzia, odmowa
uzytkownika, przekroczony budzet, blad toola wiki albo blad toola pipeline NIE wstrzymuja
zamkniecia sprintu. W kazdym z tych przypadkow odnotowujesz powod JEDNA linia w
podsumowaniu (4d) i przechodzisz do `retro` (4b).

Mutacja idzie PRZED `close-sprint` (4c), bo `complete_sprint` jest nieodwracalne, i PRZED
`summary` (4d), zeby podsumowanie znalo wynik.

**1. Odczytaj sekcje `## Mutacje` strony wiki `toolchain`**

Deterministycznie, po tytule - nie przez `search_wiki` (RAG moze zwrocic inna strone, a
slabe trafienie kazaloby uznac, ze konfiguracji nie ma):

```
mcp__monolynx__list_wiki_pages(project_slug="<PROJECT_SLUG>")
```

Wez strone o tytule dokladnie `Toolchain` i pobierz jej tresc:

```
mcp__monolynx__get_wiki_page(project_slug="<PROJECT_SLUG>", page_id="<page_id>")
```

**Zapamietaj `page_id` strony `toolchain` oraz jej pelna tresc** - jedno i drugie jest
potrzebne przy zapadce baseline (punkt 6).

Z sekcji `## Mutacje` odczytaj pola (etykiety sa kontraktem, nie parafrazuj ich):
`narzedzie`, `powod`, `komenda pelna`, `moduly rdzenia`, `baseline`. Wartosc `brak` jest
legalna dla kazdego pola.

Kazda degradacja konczy krok tak samo: job `mutation-run` ze statusem `skipped` i powodem w
`summary`, JEDNA linia w 4d i w podsumowaniu, przejscie do 4b. Sprint zamykasz normalnie.

| Sytuacja | Co pokazujesz uzytkownikowi |
|---|---|
| Brak strony `toolchain` albo brak naglowka `## Mutacje` | _"Projekt nie ma konfiguracji mutacji - uruchom `/monolynx:project-toolchain`"_ |
| `narzedzie: brak` | wartosc pola `powod` (`nie zainstalowane` albo `uzytkownik zrezygnowal`) |
| `moduly rdzenia: brak` | _"Nie uzgodniono modulow rdzenia - nie ma czego mierzyc w trendzie"_ |
| `komenda pelna: brak` | _"Pelny przebieg modulowy wymaga pola `komenda pelna`. Bez niego `mutation-check` nie ma czego uruchomic - uzupelnij pole przez `/monolynx:project-toolchain`"_ |

Niczego nie instalujesz i nie zgadujesz komendy.

**2. Zaproponuj przebieg - nie odpalaj go sam**

Zapytaj uzytkownika (`AskUserQuestion`): _"Uruchomic pelny przebieg mutacyjny na modulach
rdzenia (`<lista modulow>`)? Budzet do 600 s na modul."_ z opcjami **Tak** / **Nie, pomin**.

**Nie** -> job `mutation-run` ze statusem `skipped`, summary _"odmowa uzytkownika"_, jedna
linia w podsumowaniu, przejdz do 4b.

**3. Tak -> przebieg per modul**

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wrap-up", name="mutation-run", agent_type="skill")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="running")
```

Dla KAZDEGO modulu z pola `moduly rdzenia`, **sekwencyjnie**:

```
Skill(skill="monolynx:mutation-check", args="<sciezka modulu>")
```

W kontekscie wywolania przekaz skillowi dwa ograniczenia - bez nich zdubluje prace
`sprint-end`:

- **NIE zapisuj baseline**: propozycje zapisu z jego KROKU 5 odrzuc. Agregacje po modulach i
  zapadke robi `sprint-end` raz, w punkcie 6.
- **NIE tworz joba pipeline** (jego KROK 8): traktuj przebieg jak samodzielny. `sprint-end`
  ma wlasny job `mutation-run`.

Z wyniku kazdego modulu zbierz `score`, `killed`, `total` i `survived` (JSON parsera z
KROKU 4 skilla `mutation-check`). Blad modulu - budzet wyczerpany bez raportu, parser z
kodem 2, narzedzie nie wystartowalo - zapisz dla tego modulu `blad: <powod>` i **idz dalej
do nastepnego**. Blad jednego modulu nie przerywa calego przebiegu.

Survivorow `mutation-check` zamienia na prace sam (pyta uzytkownika w swoim KROKU 7). Dla
przebiegu sprintowego rekomenduj **jeden ticket zbiorczy per modul do backlogu** - kryteria
przy tickecie nie maja tu sensu, bo sprint wlasnie sie zamyka.

**4. Policz agregat**

```
razem = (suma killed po modulach BEZ bledu / suma total po tych modulach) * 100
```

Zaokraglij do 0,1 punktu procentowego. **Wszystkie moduly z bledem** -> brak agregatu,
zadnych zapisow (ani trend, ani baseline), job `failed` z powodem, przejdz do 4b.

**5. Dopisz wynik na strone wiki `Trend mutacji`**

Znajdz strone: `list_wiki_pages` -> strona o tytule dokladnie `Trend mutacji`.

- **Strona istnieje** -> `get_wiki_page` po jej `page_id`, dopisz wiersze NA KONCU tabeli i
  zapisz cala tresc przez `update_wiki_page`.
- **Strony nie ma** (pierwszy przebieg) -> utworz ja. Rodzica wez ze strony
  `Strategia testów i pokrycie`: `get_wiki_page` po jej `page_id` zwraca `parent_id` i tej
  wartosci uzywasz, zeby trend stanal OBOK strategii, nie pod nia. Strony strategii nie ma
  -> utworz trend bez `parent_id`.

```
mcp__monolynx__create_wiki_page(project_slug="<PROJECT_SLUG>", title="Trend mutacji", content="<szkielet ponizej + wiersze tego przebiegu>", parent_id="<parent_id strony strategii; pomin argument, gdy strategii nie ma>")
```

Slug powstaje z tytulu - `create_wiki_page` nie ma parametru `slug`, wiec go nie podajesz.

Szkielet strony:

    # Trend mutacji

    Trend mutation score modulow rdzenia, jeden przebieg na sprint. Kazde zamkniecie
    sprintu (`/monolynx:sprint-end`) dopisuje wiersz per modul i wiersz zbiorczy `razem`.
    Score jest trendem, nie bramka - nie blokuje ticketu, merge'a ani sprintu.
    Kryteria doboru modulow opisuje [[strategia-testow-i-pokrycie]], komendy przebiegu i
    pole `baseline` trzyma [[toolchain]].

    | data | modul | score | baseline | delta |
    |---|---|---|---|---|

Wiersze tego przebiegu (data z `date +%F`), dopisywane chronologicznie NA KONIEC tabeli:

- jeden wiersz **per modul**: `score` z parsera, `baseline` = wartosc pola `baseline` ze
  strony `toolchain` SPRZED tego przebiegu (albo `brak`), `delta` = score minus baseline w
  punktach procentowych (albo `n/d`, gdy baseline `brak`),
- jeden wiersz **`razem`** z agregatem z punktu 4,
- modul zakonczony bledem: wiersz z `blad: <powod>` w kolumnie `score`, bez delty.

Nie sortujesz tabeli, nie przepisujesz starych wierszy i nie usuwasz historii.

**6. Zapadka baseline w `toolchain`**

Pole `baseline` na stronie `toolchain` jest JEDNO i trzyma **zagregowany** wynik modulow
rdzenia, nie wynik pojedynczego modulu. Format:

    baseline: 71.4% (YYYY-MM-DD)

Porownaj agregat z punktu 4 z wartoscia zapisana:

| Sytuacja | Co robisz |
|---|---|
| `baseline: brak` | zapisz agregat jako pierwszy baseline (data = data przebiegu) |
| agregat > zapisany | zapisz nowy agregat (data = data przebiegu) |
| agregat <= zapisany | **NIE zapisujesz** - pokazujesz spadek albo brak zmiany |

Baseline moze tylko **rosnac**. Nigdy nie obnizasz go automatycznie: spadek jest informacja
o trendzie, nie powodem do przepisania punktu odniesienia.

```
mcp__monolynx__update_wiki_page(project_slug="<PROJECT_SLUG>", page_id="<page_id strony toolchain>", content="<pelna tresc z punktu 1 ze zmieniona WYLACZNIE linia baseline>")
```

> **UWAGA**: `update_wiki_page` nadpisuje cala tresc strony. Podmieniasz **wylacznie wartosc
> w linii `baseline:`**, reszta strony zostaje bajt w bajt. `toolchain` trzyma krytyczne
> sekcje `## Worktree` i `## Uwagi` - ich skasowanie zepsuje `work` i `work-simple`.

**7. Zamknij job**

```
mcp__monolynx__append_job_log(project_slug="<PROJECT_SLUG>", job_id="<job_id>", content="<tabela per modul (modul | score | killed/total | survivorzy) + agregat razem + decyzja o baseline (podbity / bez zmian / spadek) + klucze ticketow z survivorami, jesli powstaly>")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="success", summary="<np. 'Mutacje: razem 71.4% (+3.4 pp), baseline podbity, 2 tickety survivorow'>")
```

Status koncowy:

- `success` - gdy choc jeden modul dal wynik. **Takze wtedy, gdy sa survivorzy**, i
  **takze wtedy, gdy agregat spadl** - score jest trendem, nie bramka.
- `success` z jawnym **CZESCIOWY** w `summary` - gdy czesc modulow skonczyla sie bledem.
- `failed` - tylko gdy zaden modul nie dal wyniku.
- `skipped` - degradacja z punktu 1 albo odmowa uzytkownika z punktu 2.

Do 4b przechodzisz niezaleznie od statusu.

### 4b. Job `retro` - korekty sprintu w reguly (opcjonalny, PRZED zamknieciem)

Zapytaj uzytkownika (`AskUserQuestion`): _"Uruchomic retro korekt sprintu
(`/monolynx:retro`)? Zbierze powtarzajace sie uwagi krytyka i zaproponuje
reguly / wiersze rubryki."_ z opcjami **Tak** / **Nie, pomin**.

**Tak** -> job `retro` w stepie `wrap-up` (instrumentacja best-effort,
`agent_type="skill"`), uruchom skill `monolynx:retro` (przez narzedzie `Skill`)
z nazwa zamykanego sprintu. Retro czyta komentarze ticketow i pamiec agentow,
nie strony logow pipeline - dlatego moze isc po `wiki-clean` (3c). Musi jednak
isc PRZED `close-sprint` (4c): `complete_sprint` wypisuje niedokonczone tickety
ze sprintu (`sprint_id = null`, status `backlog`), a to wlasnie one nosza
eskalacje, STOP i "ponizej progu" - po zamknieciu `list_tickets(sprint_id=...)`
juz ich nie zwroci. Po zakonczeniu: `append_job_log` (ile korekt zebrano,
ile regul zapisano) i `update_pipeline_job(status="success"|"failed")`.

**Nie** -> pomin bez joba (albo job `skipped`).

### 4c. Job `close-sprint` - REALNE zamkniecie sprintu

> **UWAGA**: `complete_sprint` to nieodwracalne zamkniecie sprintu. **Niedokonczone tickety wracaja do backlogu.** Zanim wywolasz tool, ostrzez uzytkownika i poczekaj na potwierdzenie.

Zapytaj uzytkownika (`AskUserQuestion`): _"Zamykam sprint `<nazwa>`. Niedokonczone tickety wroca do backlogu. Kontynuowac?"_ z opcjami:

- **Tak, zamknij sprint** - przejdz dalej.
- **Nie, przerwij** - zakoncz bez zamykania sprintu (wiki-update juz wykonane; poinformuj uzytkownika).

Po potwierdzeniu:

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wrap-up", name="close-sprint", agent_type="system")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="running")
```

```
mcp__monolynx__complete_sprint(project_slug="<PROJECT_SLUG>", sprint_id="<sprint_id>")
```

Po zakonczeniu: `append_job_log` (co domkniete, ile ticketow wrocilo do backlogu jesli wiesz), nastepnie `update_pipeline_job(status="success"|"failed")`.

### 4d. Job `summary` - podsumowanie sprintu

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wrap-up", name="summary", agent_type="system")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="running")
```

Zbierz ladne podsumowanie tego, co zdzialo sie w sprincie:

- co zostalo domkniete (sprint zamkniety),
- **wynik mutacji** (4a): agregat `razem`, delta wzgledem baseline i to, czy baseline
  zostal podbity - albo `pominiete: <powod>`, gdy krok sie nie odbyl,
- wynik INGEST (ile stron wiki utworzono/zaktualizowano),
- wynik LINT (ile problemow znaleziono/naprawiono, co pozostaje do rozstrzygniecia),
- ile stron logow pipeline wyczyszczono (`N` z kroku 3c),
- wynik RETRO, jesli uruchomione (ile korekt, ile regul zapisano, co zostalo do decyzji).

Zapisz je przez `append_job_log`, nastepnie `update_pipeline_job(status="success")`.

---

## KROK 5: Zamknij pipeline (instrumentacja - best-effort)

```
mcp__monolynx__finish_pipeline(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>")
```

`finish_pipeline` bez argumentu `status` wylicza status koncowy ze stepow (jakikolwiek failed step -> pipeline failed, inaczej success).

Best-effort: blad `finish_pipeline` nie zmienia faktu, ze sprint zostal zamkniety.

---

## Podsumowanie dla uzytkownika

Na koniec pokaz:

- ktory sprint zamknieto,
- wynik mutacji: agregat `razem`, delta i decyzja o baseline (podbity / bez zmian /
  spadek) - albo jedna linia `pominiete: <powod>`,
- wynik INGEST i LINT (krotko),
- wynik RETRO (jesli uruchomione) - zapisane reguly i to, co czeka na decyzje,
- ile stron logow pipeline wyczyszczono,
- przypomnienie, ze niedokonczone tickety wrocily do backlogu,
- (jesli pipeline raportowany) link/info, ze przebieg widac w zakladce "Pipelines".

---

## Wazne zasady

1. **Pipeline jest best-effort, nie gate** - blad ktoregokolwiek toola pipeline (`create_pipeline`, joby, `append_job_log`, `finish_pipeline`) NIGDY nie przerywa zamkniecia sprintu. Odnotuj i kontynuuj. Gdy toole pipeline sa niedostepne (starszy serwer MCP), pomin CALA instrumentacje i wykonaj samo `complete_sprint`.
2. **complete_sprint to realne zamkniecie** - niedokonczone tickety wracaja do backlogu. ZAWSZE potwierdz z uzytkownikiem przed jobem `close-sprint` (4c).
3. **Joby step `wiki-update` wykonuj sekwencyjnie** - ingest, potem lint, potem clean (clean dopiero po zaingestowaniu wiedzy).
4. **Nigdy nie czysc logow po nieudanym ingescie** - `wiki-clean` wolno wykonac TYLKO gdy `wiki-ingest` sie powiodl. Inaczej skasujesz jedyne zrodlo wiedzy ze sprintu.
5. **Praca wiki jest niezalezna od pipeline** - INGEST, LINT, CLEAN, przebieg mutacyjny i `complete_sprint` dzialaja nawet bez modulu Pipelines. Niedostepnosc pipeline degraduje tylko raportowanie, nie zakres pracy domkniecia.
6. **Mutacje nie blokuja zamkniecia sprintu** - krok 4a jest nieblokujacy i pomijalny: brak narzedzia, brak konfiguracji, odmowa uzytkownika, przekroczony budzet i blad toola koncza sie jedna linia w podsumowaniu, nigdy wstrzymaniem 4b-4d. Przebieg **proponujesz**, nie odpalasz z automatu. Mutation score jest trendem, nie bramka. Pole `baseline` w `toolchain` jest JEDNO (agregat modulow rdzenia) i dziala jak zapadka - rosnie po lepszym wyniku, nigdy nie jest obnizane automatycznie.
7. **Brak aktywnego/nieznaleziony sprint** - czytelny komunikat PL i przerwanie, bez zgadywania.
8. **Jezyk**: polski (terminy techniczne i nazwy narzedzi w oryginale).
