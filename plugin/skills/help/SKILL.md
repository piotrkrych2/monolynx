---
description: "Wyswietl przewodnik po skillach Monolynx: setup projektu, flow pracy z ticketem, wiki metoda LLM Wiki, graf kodu, zmienne konfiguracyjne. Uzyj gdy chcesz wiedziec jak pracowac z Monolynx albo ktory skill wybrac."
user-invocable: true
argument-hint: ""
allowed-tools: ""
---

# Monolynx Skills - Przewodnik

Wyswietl ponizszy przewodnik uzytkownikowi i zakoncz. Nie wykonuj zadnych dodatkowych akcji.
Ten przewodnik wywoluje sie komenda `/monolynx:help`.

---

## Setup jednorazowy

Zanim zaczniesz pracowac z ticketami, ustaw projekt raz:

### 1. Slug projektu

Skille odczytuja slug w kolejnosci:

1. Zmienna srodowiskowa `MONOLYNX_PROJECT_SLUG` (lub `.env` w katalogu projektu)
2. Konfiguracja pluginu (`user_config.project_slug`)
3. Domyslny fallback: `monolynx`

Na stale w `.claude/settings.json` projektu:

```json
{
  "env": { "MONOLYNX_PROJECT_SLUG": "twoj-projekt" }
}
```

### 2. `/monolynx:project-toolchain`

Wykrywa komendy lint i testow projektu (Makefile, Python, Node, Rust, Go, PHP, Ruby, Java),
potwierdza je z Toba i zapisuje jako strone wiki `toolchain`.

**Po co**: `/monolynx:work` i `/monolynx:work-simple` czytaja te strone i uruchamiaja lint
oraz testy PRZED zmiana statusu ticketu na `in_review`. Bez tej strony skille zapytaja,
czy kontynuowac bez weryfikacji. Uruchom raz per projekt.

Opcjonalnie wykrywa tez narzedzie do testow mutacyjnych (mutmut, Stryker, PIT, Stryker.NET,
gremlins, Infection, cargo-mutants, Stryker4s, mutant) i zapisuje jego konfiguracje w sekcji
`## Mutacje` tej samej strony - sam niczego nie instaluje. Te sekcje czyta
`/monolynx:mutation-check`.

### 3. `/monolynx:wiki-init` (opcjonalnie)

Wlacza metode LLM Wiki (wg Karpathy'ego): tworzy strony systemowe `wiki-index`,
`wiki-log`, `wiki-schema` i ustawia flage projektu. Potrzebne tylko jesli chcesz
prowadzic wiki jako rosnacy artefakt, a nie zbior luznych stron.

---

## Start epizodu bez ticketu

### `/monolynx:brief [opis]`

Nie kazda praca to ticket: konfiguracja, investigation, dokumentacja, drobna zmiana.
`brief` w jednej turze ustala trzy rzeczy, ktorych goly imperatyw nie mowi: jak wyglada
"gotowe" (obserwowalny stan, nie lista edycji), czego nie ruszac, co agent robi sam,
a przed czym sie zatrzymuje. ENTER = akceptacja i start pracy w tej samej turze.
Dziala bez MCP i bez sluga. Uzyj zamiast pierwszego zdania "zrob X".
Hook pluginu (`brief_guard.py`) robi to sam: pierwszy goly imperatyw sesji
bez kontraktu ("napraw build", "w pliku X dodaj Y") automatycznie dostaje
skill `brief` - agent proponuje kontrakt, ENTER, praca. Drugi prompt w sesji
przechodzi bez reakcji. `MONOLYNX_BRIEF_GUARD=block` zamiast tego odrzuca
prompt z komunikatem "uruchom /monolynx:brief" (kontrakt piszesz sam - to
liczy skan MEGA); `off` wylacza.

---

## Flow pracy z ticketem

### 1. `/monolynx:ticket-create [opis zadania]`

Tworzysz nowy ticket. Skill zbiera kontekst z wiki, kodu i grafu zaleznosci, a nastepnie
generuje pelny opis (cel, kontekst, zakres, **nie ruszac**, **odwracalnosc**, zaleznosci,
kryteria akceptacji). Sekcje "Nie ruszac" i "Odwracalnosc" sa granicami zadania - `work`
buduje z nich kontrakt autonomii. Ticket trafia do sprintu lub backlogu.

### 2. `/monolynx:ticket-review [ticket-id lub klucz np. PROJ-12]`

Recenzujesz ticket przed podjeciem pracy. Skill sprawdza forme (8 kryteriow, w tym
granice "Nie ruszac" i "Odwracalnosc"), weryfikuje zalozenia z wiki i kodem, generuje
raport z ocena i proponuje brakujace sekcje z dowodami `plik:linia`.

**Wskazowka**: Powtarzaj cykl *review → poprawka → review* az raport pokaze same "OK"
w formie i "ZGODNE" w zalozeniach. Dobrze zrecenzowany ticket = szybsza realizacja.

### 3. Realizacja - ktory skill wybrac

| | `/monolynx:work` | `/monolynx:work-simple` |
|---|---|---|
| Story points | powyzej 3 SP | do 3 SP |
| Zespol | Researcher + N agentow + krytyk | 1 dev + krytyk |
| Research | obowiazkowy (agent Explore) | opt-in (wybierasz zrodla) |
| Agent Teams | tak, gdy dostepne | nie, zwykle subagenty |
| Typowe uzycie | nowy modul, cross-warstwowa zmiana | hotfix, cleanup, weryfikacja |
| Testy mutacyjne | propozycja przy >= 5 SP lub >= 4 plikach z kodem | tylko po eskalacji `SCOPE GREW` |

Oba maja pelna ceremonie: komentarze do ticketu, `log_time`, zmiane statusu, gate lint/test.
`work-simple` eskaluje do `work`, jesli dev wykryje, ze zakres urosl (sygnal `SCOPE GREW`).

**Co robi `/monolynx:work`**:

1. Waliduje branch (twardy gate na `main`/`master`); ticket `in_progress` z planem -> proponuje **wznowienie** od ostatniego etapu
2. Wykrywa **tryb defektu** (etykieta `bug`, tytul "Fix/Napraw") - Researcher musi wtedy nazwac mechanizm (reprodukcja, hipoteza, dowod `plik:linia`), developer pisze test regresyjny PRZED fixem
3. Uruchamia Researchera - laduje spec-page ticketu, `constitution`, `toolchain`, przeszukuje kod, wiki i graf, przepisuje granice ticketu
4. Dobiera zespol agentow, przydziela im **konkretne pliki** i ustala **kontrakt autonomii** (sam / stop przed / eskalacja) - jedno potwierdzenie na starcie zamiast przerywania w trakcie; pozycje "stop przed" ida do promptow jako STOP CONDITIONS
5. Prowadzi prace rownolegla; raporty developerow przechodza **rekoncyliacje** (akceptuje / odrzucam / do wyjasnienia) zanim pojda do lintu
6. Obowiazkowy krytyk ocenia wedlug rubryki punktowej (prog 82); po NEEDS WORK regula trafia do pamieci agenta, re-run dostaje diff, nie opis
7. Uruchamia lint i testy - **czerwone testy blokuja** `in_review`; ostatni przebieg przed zamknieciem jest zawsze **pelny**
8. Loguje czas kazdego agenta osobno, zmienia status, wypisuje komende commita i sygnal konca epizodu (`/clear` + `resume`)

Przebieg raportowany jest do **modulu Pipelines** (obserwowalnosc wzorowana na GitLab CI/CD):
pipeline `ticket_work` ze stepami research → coding → wrap-up, kazdy agent dostaje swoj job,
a jego raport (co zrobil, decyzje, pliki) trafia jako strona wiki podpieta pod job. Status,
czas i logi widac na zywo w zakladce "Pipelines". Raportowanie jest best-effort - brak modulu
Pipelines nie blokuje pracy.

### 4. `/monolynx:resume [ticket-id]`

Po `/clear`, kompakcji albo nastepnego dnia: odtwarza stan pracy z trwalych zrodel
(ticket, komentarze Team Managera, pipeline, kryteria, git) i wypisuje "gdzie jestesmy,
co zostalo, nastepny krok". Read-only. Dzieki temu `/clear` miedzy ticketami nic nie
kosztuje - kontekst nie zyje w watku, tylko w Monolynx.

### 5. `/monolynx:wiki-sync-merge [ticket-id]`

Po merge do `main`: integruje zmiany z ticketu z wiki metoda LLM Wiki (INGEST).
Odpalasz recznie, gdy praca jest juz na glownym branchu.

### 6. `/monolynx:retro [nazwa sprintu (opcjonalnie)]`

Koniec sprintu: zbiera korekty (review krytyka, NEEDS WORK, reguly, eskalacje) z komentarzy
ticketow, pamieci agentow i auto-memory Claude Code (Twoje korekty z czatu),
grupuje powtarzajace sie i dla kazdej proponuje maszynerie:
regule w `.claude/rules/`, wiersz rubryki, wpis w `constitution`. Zeby ta sama korekta
nie wracala trzeci raz. Wpinany opcjonalnie w `sprint-end`.

### 7. `/monolynx:sprint-end [nazwa sprintu (opcjonalnie)]`

Zamyka sprint jako pipeline `sprint_close`: integruje logi pracy z wiki (INGEST), audytuje
wiki (LINT), czysci strony logow pipeline'ow sprintu, opcjonalnie uruchamia `retro`
(przed zamknieciem - po nim niedokonczone tickety nie sa juz w sprincie) i realnie
zamyka sprint (`complete_sprint` - niedokonczone tickety wracaja do backlogu,
dlatego skill prosi o potwierdzenie). Bez argumentu bierze aktywny sprint.

### 8. `/monolynx:sprint-run [sloty]`

**Sprint z dyspozytora** - jeden tick, nie cała pętla. Czyta aktywny sprint i stan sesji
w tle, startuje brakujące sesje `/monolynx:work` (każda w osobnym worktree, pod nazwą
`<slug>-<KEY>`), zbiera zakończone (komentarz z identyfikatorem sesji, potem `claude rm`),
wypisuje te, które czekają na człowieka, i kończy linią
`SPRINT-RUN: remaining=... running=... waiting=... done=...`.
Sam **nigdy nie pracuje nad ticketem** - od pracy jest `work` w osobnej sesji.
Domyślnie 2 sloty; argument nadpisuje.

Wymagane flagi w `.claude/settings.json` (pole `env`), bo sesja w tle nie odpowie
na żadne pytanie: `MONOLYNX_AUTOTEST=true`, `MONOLYNX_AUTOCOMMIT=true`,
`MONOLYNX_AUTOPUSH=true` (zalecane też `MONOLYNX_AUTOMR=true`,
`MONOLYNX_CONTRACT=auto`, `MONOLYNX_SPRINT_RUN=1`). Bez nich preflight zatrzymuje tick
i nie startuje żadnej sesji.

Plik musi byc **sledzony przez gita**: worktree sesji roboczej nie widzi
`.claude/settings.local.json`, wiec flagi z niego nie docieraja do sesji w tle.
Tryb samego ticka bierze sie ze zmiennej `MONOLYNX_SPRINT_RUN_MODE` - `batch`
ustawia sam `sprint_run.sh`, brak zmiennej znaczy tryb interaktywny.

Pętla jest **na zewnątrz**. W tej sesji wystarczy:

```
/loop 15m /monolynx:sprint-run
```

Bez sesji interaktywnej (tmux na VPS, cron) użyj skryptu z pluginu. Absolutną ścieżkę
rozwiń **tutaj, w Claude Code** - `${CLAUDE_PLUGIN_ROOT}` nie istnieje w zwykłym terminalu
i wklejona tam rozwinie się do pustego stringu:

```bash
echo "${CLAUDE_PLUGIN_ROOT}/scripts/sprint_run.sh"
```

Wynik podstaw do komend w terminalu:

```bash
tmux new -d -s monolynx-sprint "/pelna/sciezka/z/echo/sprint_run.sh --slots 2"
```

```
*/15 * * * * cd /sciezka/projektu && "/pelna/sciezka/z/echo/sprint_run.sh" --once >> ~/sprint-run.log 2>&1
```

Skrypt leży w katalogu cache pluginu, nie w Twoim repozytorium, a jego ścieżka zawiera
numer wersji pluginu - po aktualizacji rozwiń ją ponownie. Pełna lista flag:
`sprint_run.sh --help`.

---

## Wiki

### `/monolynx:search [pytanie]`

Wyszukiwanie semantyczne (RAG) w wiki projektu. Uzyj, gdy potrzebujesz informacji
o architekturze, API, integracjach czy standardach kodu. Aktywuje sie tez automatycznie
przy pytaniach o dokumentacje projektu.

### `/monolynx:wiki-ingest [plik | URL | wklejona tresc]`

Wlacza nowe zrodlo do wiki: tworzy strone zrodla, aktualizuje powiazane strony, linkuje
wikilinkami, odswieza katalog i dziennik. Wymaga wlaczonej metody LLM Wiki.

### `/monolynx:wiki-lint`

Audyt zdrowia wiki: sieroty, martwe linki, sprzecznosci, luki. Prezentuje raport
i proponuje naprawy.

---

## Graf kodu

### `/monolynx:create-graph-ci-script`

Konfiguruje [graphify](https://github.com/Graphify-Labs/graphify) (zewnetrzny ekstraktor AST,
36 jezykow, offline) jako zrodlo grafu zaleznosci: wykrywa system CI
(GitLab/GitHub/Bitbucket/Jenkins), generuje `.graphifyignore` i cienki `cicd/sync_graph.py`
(graph.json -> `replace_graph`), dodaje non-blocking step CI.

**WYMOG**: graphify musi byc zainstalowane na runnerze CI przez wlasciciela projektu -
step CI go tylko uzywa, nigdy nie instaluje. Brak graphify nie psuje builda.

### `/monolynx:graph-sync`

Lokalna synchronizacja grafu za reke - komplement do wersji CI. Wykrywa graphify (przy braku
prowadzi przez instalacje na macOS/Linux/Windows), uruchamia offline ekstrakcje
(`graphify update .`) i wypycha graf. Uzyj przy pierwszym zasileniu grafu albo do
odswiezenia ad hoc bez CI.

---

## Testy mutacyjne

### `/monolynx:mutation-check [ścieżka modułu (opcjonalnie)]`

Sprawdza, czy testy faktycznie łapią zmiany w kodzie, i zamienia wynik w pracę, która
zostaje w systemie. Komendę przebiegu i format raportu bierze z sekcji `## Mutacje` strony
wiki `toolchain` - niczego nie zgaduje i niczego nie instaluje. Brak tej sekcji kończy się
odesłaniem do `/monolynx:project-toolchain`.

Bez argumentu idzie **tryb diffowy**: przebieg ograniczony do zmiany względem punktu
rozejścia z gałęzią główną (`git merge-base`). Raportuje wtedy **liczbę** przeżywających
mutantów, nie procent - przy kilku mutantach procent skacze i nic nie znaczy. Argument ze
ścieżką modułu daje pełny przebieg ze `score`, `baseline` i deltą.

Po tabeli survivorów pyta, co z nimi zrobić: kryteria akceptacji przy wskazanym tickecie,
ticket na każdego survivora, jeden ticket zbiorczy albo sam raport. Treść opisuje
**brakujący przypadek testowy**, nie "mutanta do zabicia" - mutant równoważny semantycznie
jest uzasadniany i odpuszczany, a dopisywanie asercji pinujących implementację tylko po to,
żeby podnieść score, jest wprost zabronione. Mutation score nie jest bramką: nic nie
blokuje ticketu ani merge'a.

Skill jest też **wpięty w przebiegi pracy**, zawsze jako propozycja, nigdy jako automat.
W `/monolynx:work` krok pojawia się po zielonym lincie i testach, a przed krytykiem, i tylko
wtedy, gdy ticket ma **5 SP lub więcej** albo diff obejmuje **4 lub więcej plików z kodem**
(plik źródłowy poza testami i fixture'ami). Poniżej progu kroku nie ma w ogóle; odmowa
zostaje odnotowana w podsumowaniu, a wynik nigdy nie blokuje zamknięcia ticketu.
W `/monolynx:work-simple` (sufit 3 SP) próg punktowy nigdy nie zapali, więc krok
materializuje się **wyłącznie po eskalacji zakresu** (sygnał `SCOPE GREW`), gdy praca
faktycznie wyszła poza założone ramy. `/monolynx:sprint-end` idzie dalej: uruchamia pełny
przebieg na **modułach rdzenia** i zapisuje wynik jako trend na stronie wiki
"Trend mutacji", gdzie baseline działa jak zapadka - może tylko rosnąć.

Przebieg ma twardy budżet czasu (`MONOLYNX_MUTATION_BUDGET`, domyślnie 600 s, maksimum
600 s). Po jego wyczerpaniu dostajesz raport częściowy albo jawne "brak raportu" wraz z
komendą do ręcznego dokończenia - nigdy wiszący terminal. Wywołany w trakcie pracy nad
ticketem dokłada job `mutation-check` w stepie `wrap-up` bieżącego pipeline'u (best-effort,
w trybie samodzielnym pipeline nie powstaje).

### `/monolynx:create-mutation-ci-script`

Generuje nieblokujacy krok CI z testami mutacyjnymi: wykrywa system CI
(GitLab/GitHub/Bitbucket/Jenkins), bierze komende diffowa i format raportu z sekcji
`## Mutacje` strony wiki `toolchain`, kopiuje do `cicd/` skrypty `mutation_report.py`
(normalizacja raportu) i `mutation_baseline.py` (zapadka), dodaje osobny etap `mutation`
uruchamiany tylko na MR/PR i publikuje raport jako artefakt.

**WYMOG**: narzedzie mutacyjne instaluje na runnerze wlasciciel projektu (obraz albo agent) -
krok CI go tylko uzywa. Brak narzedzia konczy krok komunikatem i sukcesem.

Zapadka: pierwszy przebieg zapisuje baseline (plik `cicd/mutation-baseline.json` commituje
czlowiek z artefaktu), kolejne raportuja liczbe survivorow oraz `score / baseline / delta`.
Baseline moze tylko rosnac, spadek jest widoczny w logu, ale **nigdy nie oblewa builda** -
mutation score to trend, nie bramka. Wymaga wczesniejszego `/monolynx:project-toolchain`.

---

## Zmienne konfiguracyjne

Ustawiane w `.claude/settings.json` lub `.claude/settings.local.json` (pole `env`):

| Zmienna | Domyslnie | Znaczenie |
|---|---|---|
| `MONOLYNX_PROJECT_SLUG` | - | Slug projektu. Bez niej skille nie zgaduja, tylko prosza o konfiguracje |
| `MONOLYNX_BRANCH_MODE` | `ticket` | `ticket` - branch musi zawierac numer ticketu; `sprint` - dowolny branch poza main, bez pytan (praca nad calym sprintem na jednym branchu); `off` - brak walidacji |
| `MONOLYNX_AUTOTEST` | `false` | `true` - skill sam odpala lint i testy; `false` - wypisuje komendy i czeka na Twoj wynik |
| `MONOLYNX_AUTOCOMMIT` | `false` | `true` - commit po zielonym tescie; `false` - wypisuje gotowa komende |
| `MONOLYNX_AUTOPUSH` | `false` | `true` - push bez pytania; `false` - nigdy nie pushuje |
| `MONOLYNX_CONTRACT` | `ask` | `ask` - kontrakt autonomii czeka na ENTER; `auto` - lista "stop przed" jest wypisywana i zapisywana w komentarzu planu jako KONTRAKT (auto), praca idzie dalej bez potwierdzenia |
| `MONOLYNX_SPRINT_RUN` | `0` | `1` - tryb sesji w tle (dyspozytor `/monolynx:sprint-run`): kazde czekanie na odpowiedz uzytkownika poza kontraktem konczy ture linia `SPRINT-RUN STOP: <powod>`; implikuje `MONOLYNX_CONTRACT=auto` |
| `MONOLYNX_AUTOMR` | `false` | `true` - po faktycznym pushu tworzy merge request przez `glab mr create` (dziala TYLKO razem z `AUTOCOMMIT=true` i `AUTOPUSH=true`) |
| `MONOLYNX_SPRINT_RUN_MODE` | - | Tryb ticka `/monolynx:sprint-run`: `batch` ustawia sam `sprint_run.sh`, brak zmiennej (i kazda inna wartosc) znaczy tryb interaktywny |
| `MONOLYNX_SPRINT_PERMISSION_MODE` | wartosc z komendy startu sesji w skillu `sprint-run` | Tryb uprawnien sesji roboczych `--bg` startowanych przez dyspozytora |
| `MONOLYNX_MUTATION_BUDGET` | `600` | Budżet czasu przebiegu `/monolynx:mutation-check` w sekundach. Maksimum 600 (limit narzędzia Bash); większa wartość jest skracana do 600 |
| `MONOLYNX_BRIEF_GUARD` | `auto` | Tryb hooka `brief_guard`: `auto` - goly imperatyw dostaje skill `brief` z propozycja kontraktu; `block` - prompt odrzucony z odeslaniem do `/monolynx:brief`; `off` - wylaczony |

Domyslne wartosci sa zachowawcze: skille **wypisuja komendy zamiast je wykonywac**.
Flagi zmieniaja to swiadomie.

Przyklad - praca nad sprintem na jednym branchu, z automatycznym lintem:

```json
{
  "env": {
    "MONOLYNX_PROJECT_SLUG": "twoj-projekt",
    "MONOLYNX_BRANCH_MODE": "sprint",
    "MONOLYNX_AUTOTEST": "true"
  }
}
```

Przyklad - sesja w tle, odpalana przez dyspozytor `/monolynx:sprint-run`:

```json
{
  "env": {
    "MONOLYNX_PROJECT_SLUG": "twoj-projekt",
    "MONOLYNX_CONTRACT": "auto",
    "MONOLYNX_SPRINT_RUN": "1",
    "MONOLYNX_AUTOTEST": "true",
    "MONOLYNX_AUTOCOMMIT": "true",
    "MONOLYNX_AUTOPUSH": "true",
    "MONOLYNX_AUTOMR": "false"
  }
}
```

---

## Runtime'y

Skille dzialaja w Claude Code, Codex i Cursorze - format `SKILL.md` jest wspolny.

| Runtime | Instalacja | Katalog | Role agentow |
|---|---|---|---|
| Claude Code | plugin z marketplace (zalecane) | `.claude/skills/` | `.claude/agents/*.md` + agenci pluginowi |
| Codex | `install_monolynx_skills(target="codex")` | `.codex/skills/` | `AGENTS.md` w korzeniu repo |
| Cursor | `install_monolynx_skills(target="cursor")` | `.cursor/skills/` | `AGENTS.md` w korzeniu repo |

Serwer MCP podlacza sie osobno: plugin robi to sam, Codex przez `~/.codex/config.toml`,
Cursor przez `.cursor/mcp.json` (klucz `mcpServers`, HTTP + naglowek
`Authorization: Bearer osk_...`). Token wygenerujesz w panelu: `/dashboard/profile/tokens`.

**Uwaga przy instalacji recznej**: `install_monolynx_skills` zwraca liste `files` -
zapisz WSZYSTKIE pliki, nie tylko `SKILL.md`. Skill `monolynx-work` ma dodatkowo
`pipeline.md` i `review-rubric.md`; ich pominiecie okroi go o czesc instrukcji.
