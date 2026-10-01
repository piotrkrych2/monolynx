---
name: sprint-end
description: "Zamknij sprint jako pipeline sprint_close: zintegruj logi pracy z wiki (INGEST), zaudytuj wiki (LINT), wyczysc logi pipeline'ow sprintu, uruchom pelny przebieg mutacyjny z trendem i domknij sprint. Przebieg raportowany do modulu Pipelines (best-effort). Uzyj gdy chcesz zamknac sprint."
user-invocable: true
argument-hint: [nazwa sprintu (opcjonalnie)]
---

Przed wykonaniem kroków obowiązkowo odczytaj [Kontrakt runtime](references/runtime.md).

# SPRINT-END - zamkniecie sprintu jako pipeline sprint_close

Orkiestrujesz zamkniecie sprintu w projekcie Monolynx. Calosc modelujesz jako pipeline `sprint_close` z dwoma stepami: **wiki-update** (zebranie wiedzy ze sprintu do wiki) i **wrap-up** (realne zamkniecie sprintu i podsumowanie). Pipeline to warstwa obserwowalnosci - raportujesz do niego **best-effort**, ale nigdy nie pozwalasz, by blad raportowania zablokowal zamkniecie sprintu.

---

## Ustalenie slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika > `MONOLYNX_PROJECT_SLUG` procesu > jawna konfiguracja klienta lub `.env` projektu czytane jako dane (nigdy `source`). Brak wartości: zapytaj o slug i zatrzymaj działania projektowe, bez zgadywania.

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

Ten skill jest mieszany: sprinty, tickety i strony wiki ida wrapperem przy `mode=cli`, a Pipelines, `clean_pipeline_logs` i operacje LLM Wiki zawsze toolem MCP (wrapper zwraca dla nich kod 3).

## KROK 1: Ustal sprint do zamkniecia

Argument z wiadomości użytkownika może być nazwa sprintu albo puste.

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
  - Wiecej niz jeden aktywny sprint - zapytaj uzytkownika (`narzędzie pytań dostępne w bieżącym trybie lub wiadomość`), ktory zamknac, i poczekaj na wybor.

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
podsumowaniu (4e) i przechodzisz do `retro` (4b).

Mutacja idzie PRZED `close-sprint` (4d), bo `complete_sprint` jest nieodwracalne, i PRZED
`summary` (4e), zeby podsumowanie znalo wynik.

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
`summary`, JEDNA linia w 4e i w podsumowaniu, przejscie do 4b. Sprint zamykasz normalnie.

| Sytuacja | Co pokazujesz uzytkownikowi |
|---|---|
| Brak strony `toolchain` albo brak naglowka `## Mutacje` | _"Projekt nie ma konfiguracji mutacji - uruchom `/monolynx:project-toolchain`"_ |
| `narzedzie: brak` | wartosc pola `powod` (`nie zainstalowane` albo `uzytkownik zrezygnowal`) |
| `moduly rdzenia: brak` | _"Nie uzgodniono modulow rdzenia - nie ma czego mierzyc w trendzie"_ |
| `komenda pelna: brak` | _"Pelny przebieg modulowy wymaga pola `komenda pelna`. Bez niego `mutation-check` nie ma czego uruchomic - uzupelnij pole przez `/monolynx:project-toolchain`"_ |

Niczego nie instalujesz i nie zgadujesz komendy.

**2. Zaproponuj przebieg - nie odpalaj go sam**

Zapytaj uzytkownika (`narzędzie pytań dostępne w bieżącym trybie lub wiadomość`): _"Uruchomic pelny przebieg mutacyjny na modulach
rdzenia (`<lista modulow>`)? Budzet do 600 s na modul."_ z opcjami **Tak** / **Nie, pomin**.

**Nie** -> job `mutation-run` ze statusem `skipped`, summary _"odmowa uzytkownika"_, jedna
linia w podsumowaniu, przejdz do 4b.

**3. Tak -> przebieg per modul**

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wrap-up", name="mutation-run", agent_type="skill")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="running")
```

Dla KAZDEGO modulu z pola `moduly rdzenia`, **sekwencyjnie**:

Najpierw użyj dostępnego discovery, mechanizmu skilli lub delegowania i jego aktualnego schematu. Treść zadania i wymagany wynik z poniższego przykładu obowiązują również przy innym mechanizmie.

<!-- runtime:claude-code -->
```
Skill(skill="monolynx:mutation-check", args="<sciezka modulu>")
```

<!-- /runtime:claude-code -->
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

Zapytaj uzytkownika (`narzędzie pytań dostępne w bieżącym trybie lub wiadomość`): _"Uruchomic retro korekt sprintu
(`/monolynx:retro`)? Zbierze powtarzajace sie uwagi krytyka i zaproponuje
reguly / wiersze rubryki."_ z opcjami **Tak** / **Nie, pomin**.

**Tak** -> job `retro` w stepie `wrap-up` (instrumentacja best-effort,
`agent_type="skill"`), uruchom skill `monolynx:retro` (przez narzedzie `Skill`)
z nazwa zamykanego sprintu. Retro czyta komentarze ticketow i pamiec agentow,
nie strony logow pipeline - dlatego moze isc po `wiki-clean` (3c). Musi jednak
isc PRZED `close-sprint` (4d): `complete_sprint` wypisuje niedokonczone tickety
ze sprintu (`sprint_id = null`, status `backlog`), a to wlasnie one nosza
eskalacje, STOP i "ponizej progu" - po zamknieciu `list_tickets(sprint_id=...)`
juz ich nie zwroci. Po zakonczeniu: `append_job_log` (ile korekt zebrano,
ile regul zapisano) i `update_pipeline_job(status="success"|"failed")`.

**Nie** -> pomin bez joba (albo job `skipped`).

### 4c. Job `version-bump` - numer wydania pluginu (warunkowy, PRZED zamknieciem)

Tickety sprintu dopisuja swoje wpisy changelogu pod `### Unreleased` i nie zmieniaja
numeru wersji (regula `.claude/rules/plugin-version-bump.md`, blok `changelog-unreleased`
w `work` i `work-simple`). Numer nadaje mergujacy: czlowiek przy merge do main albo ten
job. Krok jest **nieblokujacy**: kazdy powod pominiecia albo blad konczy sie JEDNA linia w
podsumowaniu (4e) i przejsciem do `close-sprint` (4d). Idzie przed `close-sprint`, zeby
podsumowanie znalo numer wydania.

**1. Odczytaj konfiguracje**

```bash
echo "AUTOCOMMIT=${MONOLYNX_AUTOCOMMIT:-false} AUTOPUSH=${MONOLYNX_AUTOPUSH:-false} AUTOMR=${MONOLYNX_AUTOMR:-false} SPRINT_RUN=${MONOLYNX_SPRINT_RUN:-0} MR_TARGET=${MONOLYNX_MR_TARGET:-}"
```

**2. Ustal branch docelowy i warunek**

Branch docelowy (`<branch_docelowy>`) ustal jak w `work/commit-mr.md`, kazda komende
uruchamiajac OSOBNO i wpisujac wynik do kolejnych komend jako literal (bez `$VAR` i
`$(...)`):

a) `MR_TARGET` z linii konfiguracji niepusta -> ta wartosc.
b) `git symbolic-ref refs/remotes/origin/HEAD` -> wynik `refs/remotes/origin/<nazwa>`,
   bierzesz `<nazwa>`. Blad albo pusty wynik -> c).
c) `glab repo view -F json --jq .default_branch`.
d) Nic nie dalo wyniku -> NIE zgaduj `main`: job `skipped`, linia _"version-bump
   pominiety: nie ustalono brancha docelowego (ustaw MONOLYNX_MR_TARGET)"_.

Stan czytasz z originu, nie z lokalnego checkoutu (moze byc nieaktualny albo na innym
branchu):

```bash
git fetch origin <branch_docelowy>
git show origin/<branch_docelowy>:plugin/.claude-plugin/plugin.json
git show origin/<branch_docelowy>:plugin/README.md
```

Warunek wykonania: oba pliki istnieja, README ma sekcje `## Changelog`, a jej pierwszym
wpisem `### ` jest `### Unreleased`. Brak ktoregokolwiek pliku (projekt bez pluginu),
brak sekcji albo pierwszy wpis numeryczny (nic do wydania) -> job `skipped` bez pytania,
jedna linia _"version-bump pominiety: <powod>"_.

Zapamietaj `OSTATNIA` = pierwszy naglowek numeryczny `### X.Y.Z` pod `### Unreleased`
(ostatnio wydana wersja; rowna `version` w `plugin.json`) oraz tresc punktow pod
`### Unreleased` (kazdy zaczyna sie od klucza ticketu).

Na branchu docelowym `main` taki stan oznacza, ze ktos zmergowal ticket bez nadania
numeru: pipeline `plugin-consistency` na `main` jest czerwony (tryb wydania) az do merge
MR-a z tego joba. Napisz to wprost w propozycji.

**3. Zaproponuj numer: minor albo patch**

Commit ostatniego wydania znajdz po zmianie wersji w manifescie:

```bash
git log -1 --format=%H -S'"version": "<OSTATNIA>"' origin/<branch_docelowy> -- plugin/.claude-plugin/plugin.json
git diff --name-status <commit_wydania> origin/<branch_docelowy> -- plugin/skills plugin/hooks plugin/scripts
```

- **minor** (`X.(Y+1).0`) - od ostatniego wydania doszedl nowy skill
  (`A` na `plugin/skills/*/SKILL.md`), nowy hook (`A` w `plugin/hooks/`) albo nowy
  skrypt (`A` w `plugin/scripts/`); to samo, gdy punkt pod `### Unreleased` mowi
  wprost o nowym skillu, hooku albo skrypcie.
- **patch** (`X.Y.(Z+1)`) - w pozostalych przypadkach (poprawki i zmiany tresci
  istniejacych skilli).
- `git log` nie znalazl commita wydania -> ocen tylko z tresci punktow pod
  `### Unreleased` i zaznacz to w uzasadnieniu.

Pokaz propozycje: numer, rodzaj (minor/patch), uzasadnienie (lista dodanych plikow z
diffu albo cytat punktu changelogu) i klucze ticketow z `### Unreleased`.

**4. Zgoda**

- **Interaktywnie** (`SPRINT_RUN` rozne od `1`): zapytaj uzytkownika (`narzędzie pytań dostępne w bieżącym trybie lub wiadomość`):
  _"Nadac wydaniu numer `<X.Y.Z>` (<minor|patch>: <uzasadnienie>)?"_ z opcjami **Tak** /
  **Inny numer** / **Nie, pomin**. **Inny numer** -> przyjmij podany numer, jesli jest
  wyzszy od `OSTATNIA`; inaczej zapytaj ponownie. **Nie** -> job `skipped`, summary
  _"odmowa uzytkownika"_, jedna linia w podsumowaniu, przejdz do 4d.
- **Sesja w tle** (`SPRINT_RUN=1`, `sprint_run.sh --close`): nie pytasz, bo nikt nie
  odpowie. Bierzesz numer z punktu 3 i idziesz dalej w granicach flag z punktu 1. Wynik
  jest odwracalny - nic nie trafia do brancha docelowego bez merge MR-a przez czlowieka -
  wiec to nie jest decyzja, ktora wymaga `SPRINT-RUN STOP`. W opisie MR i w podsumowaniu
  zaznacz _"numer wg reguly minor/patch, bez potwierdzenia"_.

**5. Wykonanie**

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wrap-up", name="version-bump", agent_type="system")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="running")
```

Checkout uzytkownika zostaje nietkniety: prace robisz w osobnym worktree. Katalog ustal
komenda `git rev-parse --git-common-dir` i wpisz literalnie
`<sciezka worktree>` = `<wynik>/monolynx/wt-version-bump` (sciezka bezwzgledna).

Najpierw sprawdz, czy branch nie istnieje na originie:
`git ls-remote --heads origin release/plugin-<X.Y.Z>` (niepusty wynik = istnieje;
lokalny duplikat zglosi sam `git worktree add -b`). Branch `release/plugin-<X.Y.Z>`
juz istnieje (lokalnie albo na originie) -> NIE nadpisuj go: job `failed`, linia
_"version-bump: branch release/plugin-<X.Y.Z> juz istnieje - sprawdz otwarty MR"_,
przejdz do 4d. Inaczej:

```bash
git worktree add -b release/plugin-<X.Y.Z> <sciezka worktree> origin/<branch_docelowy>
```

W worktree zmien DOKLADNIE trzy miejsca (plus ewentualnie `.claude/settings.json`,
patrz nizej), nic wiecej:

- `plugin/README.md`: naglowek `### Unreleased` -> `### <X.Y.Z>`; tresc punktow bez
  zmian,
- `plugin/.claude-plugin/plugin.json`: `"version": "<X.Y.Z>"`,
- `plugin/.codex-plugin/plugin.json`: `"version": "<X.Y.Z>"` (test dystrybucji
  wymaga rownosci obu manifestow).

Potem sprawdz sledzony `<sciezka worktree>/.claude/settings.json` (MON-207). Branch
sprintu mogl dostac w polu `env` flagi dyspozytora, ktore nie moga trafic na `main`:
`MONOLYNX_SPRINT_RUN`, `MONOLYNX_BASE_BRANCH`, `MONOLYNX_MR_TARGET` oraz flagi AUTO*
ustawione na czas sprintu. Porownaj pole `env` z wersja z `main`
(`git -C <sciezka worktree> show origin/main:.claude/settings.json`). Jest ktoras z tych
flag, ktorej nie ma na `main` -> przywroc `env` do stanu z `main` (usun te klucze, reszty
pliku nie ruszaj) i dopisz `.claude/settings.json` jako czwarty plik tego commita. Brak
roznic -> plik zostaje bez zmian. `validate_plugin_consistency.py --release` odrzuca
`.claude/settings.json` z `MONOLYNX_SPRINT_RUN`, `MONOLYNX_BASE_BRANCH` albo
`MONOLYNX_MR_TARGET` w `env`, wiec bez tego kroku walidator nizej bedzie czerwony.

Sprawdz walidatorem w trybie wydania, z plikami worktree:

```bash
python3 <sciezka worktree>/scripts/validate_plugin_consistency.py --release
```

Czerwony -> NIE commituj: job `failed` z pierwsza linia bledu w `summary`, usun
worktree i branch (`git worktree remove --force <sciezka worktree>` - `--force`, bo
worktree ma twoje niezacommitowane zmiany; potem `git branch -D release/plugin-<X.Y.Z>`),
jedna linia w podsumowaniu, przejdz do 4d. Walidator w trybie wydania odrzuca
`### Unreleased` jako pierwszy wpis, punkty bez klucza ticketu oraz flagi sprintu
w `env` sledzonego `.claude/settings.json` - czerwony wynik znaczy zwykle, ze wpis
ktoregos ticketu trzeba poprawic recznie.

Zielony -> commit, push i MR wg flag, na wzor `work/commit-mr.md` (wszystkie komendy
z `git -C <sciezka worktree>`):

- **`AUTOCOMMIT=false`** (domyslnie) - niczego nie commitujesz. Worktree zostaje ze
  zmianami, a uzytkownik dostaje gotowe komendy:

  ```bash
  git -C <sciezka worktree> add plugin/README.md plugin/.claude-plugin/plugin.json plugin/.codex-plugin/plugin.json
  git -C <sciezka worktree> add .claude/settings.json   # tylko gdy przywrociles env z main
  git -C <sciezka worktree> commit -m "Plugin <X.Y.Z>: wydanie <klucze ticketow z Unreleased>"
  git -C <sciezka worktree> push -u origin release/plugin-<X.Y.Z>
  glab mr create --fill --yes --source-branch release/plugin-<X.Y.Z> --target-branch <branch_docelowy>
  git worktree remove <sciezka worktree>
  ```

- **`AUTOCOMMIT=true`** - jeden commit z trzema plikami, z czterema, gdy przywrociles
  `env` w `.claude/settings.json` (imienny `git add`, nigdy `-A`).
- **`AUTOPUSH=true`** - `git -C <sciezka worktree> push -u origin release/plugin-<X.Y.Z>`,
  tylko gdy commit faktycznie sie odbyl.
- **`AUTOMR=true`** - po faktycznym pushu `glab mr create` jak wyzej; URL zapamietaj do
  podsumowania. Blad albo brak `glab` -> wypisz komende, nie powtarzaj.
- Po pushu usun worktree (`git worktree remove <sciezka worktree>`); bez pushu zostaw go
  i podaj sciezke w podsumowaniu.

Po zakonczeniu: `append_job_log` (numer, rodzaj i uzasadnienie, klucze ticketow, wynik
walidatora, commit/push/MR albo wypisane komendy), nastepnie
`update_pipeline_job(status="success"|"failed"|"skipped", summary="<np. 'Plugin 1.22.0 (minor: nowy skill X), MR !123'>")`.

### 4d. Job `close-sprint` - REALNE zamkniecie sprintu

> **UWAGA**: `complete_sprint` to nieodwracalne zamkniecie sprintu. **Niedokonczone tickety wracaja do backlogu.** Zanim wywolasz tool, ostrzez uzytkownika i poczekaj na potwierdzenie.

Zapytaj uzytkownika (`narzędzie pytań dostępne w bieżącym trybie lub wiadomość`): _"Zamykam sprint `<nazwa>`. Niedokonczone tickety wroca do backlogu. Kontynuowac?"_ z opcjami:

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

### 4e. Job `summary` - podsumowanie sprintu

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
- wynik RETRO, jesli uruchomione (ile korekt, ile regul zapisano, co zostalo do decyzji),
- **wydanie pluginu** (4c): numer, minor/patch z uzasadnieniem i MR albo wypisane
  komendy - albo jedna linia `version-bump pominiety: <powod>`.

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
- wydanie pluginu: numer i MR (albo komendy do wykonania i sciezka worktree przy
  `AUTOCOMMIT=false`), w sesji w tle z dopiskiem _"numer wg reguly minor/patch, bez
  potwierdzenia"_ - albo jedna linia `version-bump pominiety: <powod>`,
- ile stron logow pipeline wyczyszczono,
- przypomnienie, ze niedokonczone tickety wrocily do backlogu,
- (jesli pipeline raportowany) link/info, ze przebieg widac w zakladce "Pipelines".

---

## Wazne zasady

1. **Pipeline jest best-effort, nie gate** - blad ktoregokolwiek toola pipeline (`create_pipeline`, joby, `append_job_log`, `finish_pipeline`) NIGDY nie przerywa zamkniecia sprintu. Odnotuj i kontynuuj. Gdy toole pipeline sa niedostepne (starszy serwer MCP), pomin CALA instrumentacje i wykonaj samo `complete_sprint`.
2. **complete_sprint to realne zamkniecie** - niedokonczone tickety wracaja do backlogu. ZAWSZE potwierdz z uzytkownikiem przed jobem `close-sprint` (4d).
3. **Joby step `wiki-update` wykonuj sekwencyjnie** - ingest, potem lint, potem clean (clean dopiero po zaingestowaniu wiedzy).
4. **Nigdy nie czysc logow po nieudanym ingescie** - `wiki-clean` wolno wykonac TYLKO gdy `wiki-ingest` sie powiodl. Inaczej skasujesz jedyne zrodlo wiedzy ze sprintu.
5. **Praca wiki jest niezalezna od pipeline** - INGEST, LINT, CLEAN, przebieg mutacyjny i `complete_sprint` dzialaja nawet bez modulu Pipelines. Niedostepnosc pipeline degraduje tylko raportowanie, nie zakres pracy domkniecia.
6. **Mutacje nie blokuja zamkniecia sprintu** - krok 4a jest nieblokujacy i pomijalny: brak narzedzia, brak konfiguracji, odmowa uzytkownika, przekroczony budzet i blad toola koncza sie jedna linia w podsumowaniu, nigdy wstrzymaniem 4b-4e. Przebieg **proponujesz**, nie odpalasz z automatu. Mutation score jest trendem, nie bramka. Pole `baseline` w `toolchain` jest JEDNO (agregat modulow rdzenia) i dziala jak zapadka - rosnie po lepszym wyniku, nigdy nie jest obnizane automatycznie.
7. **Numer wydania nadaje sprint-end, nie ticket** - job `version-bump` (4c) zamienia `### Unreleased` na `### X.Y.Z` i ustawia ten sam numer w obu manifestach (`plugin/.claude-plugin/plugin.json`, `plugin/.codex-plugin/plugin.json`) jednym commitem na osobnym branchu `release/plugin-X.Y.Z`, w osobnym worktree, po zielonym `validate_plugin_consistency.py --release`. W tym samym commicie przywraca `env` sledzonego `.claude/settings.json` do stanu z `main`, jesli branch sprintu dodal tam `MONOLYNX_SPRINT_RUN`, `MONOLYNX_BASE_BRANCH`, `MONOLYNX_MR_TARGET` albo flagi AUTO* na czas sprintu. Commit, push i MR tylko wg `MONOLYNX_AUTOCOMMIT`/`AUTOPUSH`/`AUTOMR`. Krok jest nieblokujacy i warunkowy: projekt bez pluginu albo bez `### Unreleased` na branchu docelowym to `skipped`, nie blad.
8. **Brak aktywnego/nieznaleziony sprint** - czytelny komunikat PL i przerwanie, bez zgadywania.
9. **Jezyk**: polski (terminy techniczne i nazwy narzedzi w oryginale).
