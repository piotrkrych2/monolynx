# Monolynx: plugin Claude Code

Plugin Monolynx pakuje w jeden, instalowalny zestaw to, czego potrzebujesz, żeby pracować z platformą Monolynx bezpośrednio z Claude Code:

- **18 skilli** dające komendy w przestrzeni nazw `/monolynx:*` (kontrakt epizodu bez ticketu, praca z ticketami w pełnym i uproszczonym flow, dyspozytor sprintu w sesjach w tle, wznowienie po `/clear`, tworzenie i recenzja zadań, retro korekt, zamknięcie sprintu, wyszukiwanie w wiki, metoda LLM Wiki - inicjalizacja, integracja źródeł, post-merge sync i audyt, pomoc, generowanie skryptu CI grafu zależności, lokalna synchronizacja grafu),
- **7 agentów** wyspecjalizowanych w rolach zespołu (backend, frontend, baza danych, DevOps, QA, code review, dokumentacja),
- **zdalny serwer MCP** Monolynx (HTTP, autoryzacja Bearer), który udostępnia narzędzia do Scrum, 500ki, Monitoringu, Wiki, Połączeń i Planu pracy.

Plugin nie zawiera kopii serwera MCP. Deklaruje jedynie dostęp do istniejącego, zdalnego serwera Monolynx pod adresem z konfiguracji.

## Wymagania

Zanim zainstalujesz plugin, przygotuj:

1. **Konto Monolynx** z dostępem do co najmniej jednego projektu (członkostwo w projekcie jest weryfikowane po stronie serwera MCP).
2. **Token API** w formacie `osk_...`. Wygeneruj go w panelu: `/dashboard/profile/tokens` (przycisk tworzenia tokenu). Token wyświetlany jest tylko raz przy tworzeniu, skopiuj go od razu.
3. **Claude Code CLI** w wersji obsługującej pluginy i marketplace (komendy `/plugin`).

## Instalacja

Instalacja przebiega w dwóch krokach: najpierw dodajesz marketplace (źródło pluginu), potem instalujesz z niego plugin `monolynx`.

### Z lokalnego repozytorium (tryb deweloperski)

Wskaż katalog zawierający `.claude-plugin/marketplace.json` (root repozytorium):

```text
/plugin marketplace add /Users/piotrkrych/projects/monolynx/monolynx
/plugin install monolynx@monolynx
```

Zapis `monolynx@monolynx` oznacza: plugin o nazwie `monolynx` ze źródła (marketplace) o nazwie `monolynx`.

### Ze zdalnego repozytorium

Wskaż adres repozytorium z `marketplace.json`:

```text
/plugin marketplace add <url-repo>
/plugin install monolynx@monolynx
```

Po instalacji Claude Code poprosi o uzupełnienie konfiguracji (`userConfig`). Sprawdź dostępność komend wpisując `/monolynx:help`.

## Konfiguracja (`userConfig`)

Plugin pyta o trzy parametry przy instalacji. Możesz je później zmienić przez `/plugin`.

| Parametr | Wymagany | Wartość domyślna | Opis |
|----------|----------|------------------|------|
| `mcp_token` | nie | brak | Token API Monolynx (`osk_...`). Oznaczony jako `sensitive`, trafia do keychain systemu. Potrzebny tylko przy ręcznej konfiguracji MCP - connector pluginu uwierzytelnia się przez OAuth. |
| `mcp_endpoint` | nie | `https://monolynx.com/mcp` | Adres Twojej instancji, używany przez skille (m.in. synchronizację grafu). **Nie zmienia adresu connectora** - ten jest zapisany na stałe w manifeście. |
| `project_slug` | nie | brak | Domyślny slug projektu. Używany jako fallback, gdy w projekcie nie ma `MONOLYNX_PROJECT_SLUG`. |

### Token API (`mcp_token`)

1. Zaloguj się do Monolynx i otwórz `/dashboard/profile/tokens`.
2. Utwórz nowy token, skopiuj wartość `osk_...` (pokazywana raz).
3. Wklej ją podczas konfiguracji pluginu. Ponieważ pole jest `sensitive`, Claude Code zapisze token w keychain, a nie w plikach konfiguracyjnych w repozytorium.

Token możesz w każdej chwili unieważnić w tym samym panelu (`/revoke`). Po unieważnieniu wygeneruj nowy i zaktualizuj konfigurację pluginu.

### Connector MCP

Plugin deklaruje serwer MCP w dwóch miejscach o identycznej treści: `mcpServers` w `.claude-plugin/plugin.json` (manifest) oraz `.mcp.json` w katalogu pluginu. Dublowanie jest celowe - różne środowiska Claude'a czytają różne źródła, a rozjazd między nimi kończy się brakiem connectora.

```json
{
  "mcpServers": {
    "monolynx": {
      "type": "http",
      "url": "https://monolynx.com/mcp"
    }
  }
}
```

Adres jest **literalny, bez placeholderów**. Składnia `${user_config.*}` działa wyłącznie w Claude Code; w pozostałych klientach trafia do konfiguracji dosłownie, daje nieprawidłowy URL i connector w ogóle nie powstaje.

Uwierzytelnianie idzie przez **OAuth 2.1 z Dynamic Client Registration** (RFC 7591) - klient rejestruje się sam i przeprowadza użytkownika przez logowanie. Statyczny token w nagłówku nie jest potrzebny.

**Self-hosted**: connector pluginu wskazuje na `https://monolynx.com/mcp`. Dla własnej instancji dodaj connector ręcznie w ustawieniach klienta, podając adres swojego serwera MCP.

Klucz `monolynx` w `mcpServers` jest częścią nazw narzędzi (`mcp__monolynx__get_ticket`), do których odwołują się wszystkie skille. **Nie zmieniaj go** - zmiana zerwie każdy skill w pakiecie.

### Ustalanie slug projektu (`project_slug`)

Skille pluginu pracują w kontekście jednego projektu. Slug ustalany jest w stałej kolejności:

1. **`MONOLYNX_PROJECT_SLUG`** z pliku `.env` projektu, w którym pracujesz (najwyższy priorytet, ustawienie per repozytorium),
2. **`user_config.project_slug`** z konfiguracji pluginu (fallback globalny dla użytkownika),
3. **`"monolynx"`** jako ostateczna wartość domyślna.

Dzięki temu plugin działa **cross-project**: ten sam token i ten sam plugin obsługują wiele projektów. Wystarczy w danym repozytorium ustawić `MONOLYNX_PROJECT_SLUG` w `.env`, a skille automatycznie zadziałają na właściwym projekcie. `user_config.project_slug` jest wygodny, gdy najczęściej pracujesz z jednym projektem i nie chcesz ustawiać go w każdym repozytorium.

## Zawartość pluginu

### Skille (komendy `/monolynx:*`)

| Komenda | Opis |
|---------|------|
| `/monolynx:brief` | Kontrakt epizodu bez ticketu (konfiguracja, investigation, docs): w jednej turze ustala "gotowe" jako obserwowalny stan, "nie ruszać" i prawa decyzyjne (sam / stop przed). ENTER = akceptacja i start pracy w tej samej turze. Działa bez MCP. |
| `/monolynx:work` | Podejmij zadanie z aktualnego sprintu: walidacja brancha, wznowienie przerwanej pracy, tryb defektu (mechanizm + test regresyjny przed fixem), research, dobór zespołu agentów, kontrakt autonomii (STOP CONDITIONS w promptach), praca równoległa, rekoncyliacja raportów, obowiązkowy krytyk z regułami do pamięci agentów, pełny lint/test przed `in_review`. Raportuje przebieg pracy do modułu Pipelines (obserwowalność): pipeline `ticket_work` ze stepami research → coding → wrap-up, job per agent, log każdego agenta jako strona wiki. Raportowanie best-effort - nie blokuje pracy. |
| `/monolynx:resume` | Wznowienie po `/clear` lub kompakcji: odtwarza stan ticketu z komentarzy, pipeline, kryteriów i gita, wypisuje "gdzie jesteśmy, co zostało, następny krok". Read-only. |
| `/monolynx:retro` | Retro korekt sprintu: zbiera uwagi krytyka, NEEDS WORK, reguły i eskalacje z komentarzy ticketów, pamięci agentów oraz auto-memory Claude Code (korekty użytkownika z czatu, katalog wykrywany z `$HOME`/`CLAUDE_CONFIG_DIR` i `$PWD`), grupuje powtarzające się i proponuje maszynerię (reguła w `.claude/rules/`, wiersz rubryki, wpis w `constitution`). Opcjonalny job w `sprint-end`. |
| `/monolynx:work-simple` | Uproszczony flow dla mniejszych ticketów (max 3 SP): jeden dobrany dev + krytyk jako zwykłe subagenty (bez Agent Teams), research opt-in, pełna ceremonia self-reporting, eskalacja do `/monolynx:work` gdy scope się rozrasta. Raportuje do modułu Pipelines (uproszczona instrumentacja: dev + krytyk), best-effort. |
| `/monolynx:ticket-create` | Utwórz nowy ticket: zbiera kontekst z wiki, kodu i grafu zależności, generuje opis w ustalonej formie (cel, kontekst, zakres, nie ruszać, odwracalność, zależności, kryteria akceptacji). |
| `/monolynx:ticket-review` | Zrecenzuj ticket ze sprintu: sprawdza formę (8 kryteriów, w tym granice "Nie ruszać" i "Odwracalność"), zgodność z wiki i kodem, generuje tabelkę raportu i proponuje poprawki. |
| `/monolynx:sprint-end` | Zamknij sprint jako pipeline `sprint_close` (stepy wiki-update → wrap-up): INGEST logów pracy do wiki, LINT (audyt wiki), czyszczenie stron logów pipeline sprintu, opcjonalne `retro` (przed zamknięciem, bo `complete_sprint` wypisuje niedokończone tickety ze sprintu) i realne `complete_sprint` (niedokończone tickety wracają do backlogu, z potwierdzeniem). Raportowanie do Pipelines best-effort. |
| `/monolynx:sprint-run` | Dyspozytor sprintu: jeden idempotentny tick czyta stan sprintu i sesji w tle, startuje brakujące sesje `claude --bg --worktree <KEY> --name <slug>-<KEY>` z komendą `/monolynx:work <KEY>`, zbiera zakończone (komentarz z identyfikatorem sesji i `claude rm`), raportuje czekające na człowieka i kończy linią `SPRINT-RUN: remaining=... running=... waiting=... done=...`. Sam nigdy nie pracuje nad ticketem. Pętla jest na zewnątrz: `/loop 15m /monolynx:sprint-run` albo `plugin/scripts/sprint_run.sh`. |
| `/monolynx:search` | Szukaj informacji w wiki projektu (architektura, API, integracje, standardy kodu) przez wyszukiwanie semantyczne; przy włączonej metodzie LLM Wiki proponuje zapis odpowiedzi jako stronę syntezy. |
| `/monolynx:wiki-init` | Włącz metodę LLM Wiki dla projektu: tworzy strony systemowe (regulamin `wiki-schema`, katalog `wiki-index`, dziennik `wiki-log`) i włącza flagę. Idempotentny bootstrap. |
| `/monolynx:wiki-ingest` | Zintegruj nowe źródło (plik, URL, wklejona treść) z wiki: strona źródła, aktualizacja powiązanych stron encji/konceptów, wikilinki, odświeżenie katalogu i wpis do dziennika. |
| `/monolynx:wiki-lint` | Audyt zdrowia wiki: wykrywa sieroty, martwe linki, sprzeczności i luki, prezentuje raport i proponuje naprawy. |
| `/monolynx:wiki-sync-merge` | Post-merge INGEST do wiki metodą LLM Wiki. Odpala człowiek po merge ticketów/PR do main - pobiera dane ticketów, integruje wiedzę z zamkniętych zadań, aktualizuje powiązane strony i odświeża katalog. |
| `/monolynx:help` | Wyświetl instrukcję użycia skilli Monolynx: flow pracy z ticketami oraz skille dodatkowe. |
| `/monolynx:project-toolchain` | Jednorazowo wykryj komendy lint i testów projektu (Makefile, Python, Node, Rust, Go, PHP, Ruby, Java) i zapisz je jako stronę wiki `toolchain`, którą czytają `/monolynx:work` i `/monolynx:work-simple`. Opcjonalnie wykrywa też narzędzie do testów mutacyjnych dla 9 ekosystemów i zapisuje sekcję `## Mutacje` (komenda pełna i diffowa, moduły rdzenia, baseline). Narzędzi nie instaluje. |
| `/monolynx:create-graph-ci-script` | Skonfiguruj graphify jako ekstraktor grafu zależności (dowolny język, offline): `.graphifyignore`, `cicd/sync_graph.py` (`replace_graph`) i non-blocking step CI (GitLab/GitHub/Bitbucket/Jenkins). Graphify instaluje właściciel runnera. |
| `/monolynx:graph-sync` | Lokalna synchronizacja grafu za rękę: wykrywa/pomaga zainstalować graphify (macOS/Linux/Windows), ekstrakcja offline i push przez `cicd/sync_graph.py` (`replace_graph`). |

### Agenci

Plugin dostarcza 7 agentów do delegowania pracy w odpowiednich rolach:

- **backend-developer**: implementacja backendu (FastAPI, SQLAlchemy, async).
- **frontend-developer**: szablony, HTMX, Tailwind, warstwa UI.
- **database-specialist**: modele, migracje Alembic, zapytania, indeksy.
- **devops-infra**: Docker, CI/CD, infrastruktura, deployment.
- **qa-tester**: testy (pytest), scenariusze, pokrycie.
- **code-reviewer**: recenzja kodu, jakość, bezpieczeństwo.
- **technical-writer**: dokumentacja (CLAUDE.md, wiki, README, SDK, przewodniki).

### Hooki

Plugin dostarcza hook PreToolUse na narzędziu Bash (`hooks/hooks.json` → `hooks/bash_guard.py`), który deterministycznie egzekwuje reguły workflow, dotąd zapisane wyłącznie w promptach skilli `work` / `work-simple`:

| Kto | Komenda | Decyzja |
|---|---|---|
| Subagent (dev, krytyk, ...) | `git add/commit/push/reset --hard/rebase` | **deny** - operacje zapisu w gicie należą do sesji głównej i użytkownika |
| Subagent | lint/testy (`pytest`, `ruff`, `eslint`, ...) | **deny** - lint/testy odpala Team Manager wg `MONOLYNX_AUTOTEST` |
| Sesja główna | `git push` | **ask** (potwierdzenie użytkownika), chyba że `MONOLYNX_AUTOPUSH=true` |
| Sesja główna | lint/testy | **ask**, chyba że `MONOLYNX_AUTOTEST=true` |
| Subagent | `glab mr create` | **deny** - "Subagent nie tworzy merge requestów - MR zakłada Team Manager (główna sesja) po pushu, wg MONOLYNX_AUTOMR. Zgłoś gotowość w raporcie końcowym." |
| Sesja główna | `glab mr create` | **ask** - "glab mr create wymaga potwierdzenia użytkownika (MONOLYNX_AUTOMR != true).", chyba że `MONOLYNX_AUTOMR=true` |
| Subagent | operacje destrukcyjne (`rm -rf`, `git push --force`, `curl \| sh`, zapis do `~/.ssh`/`~/.aws`, `DROP/TRUNCATE`, `mkfs`/`dd of=/dev`) | **deny** |
| Sesja główna | operacje destrukcyjne (jw.) oraz `git reset --hard` / `rebase` / `clean -f` | **ask** - zawsze, niezależnie od flag |
| Sesja główna | `git add` / `git commit` | przechodzi normalnie (zwykły system uprawnień) |

Hook nigdy nie blokuje sesji głównej na twardo - decyzję podejmuje użytkownik. Deny u subagenta to oczekiwane zachowanie workflow, nie błąd. Hook działa w każdej sesji z zainstalowanym pluginem; wymaga `python3` w PATH.

**Wyjątek: sprzątanie własnych plików w `/tmp`.** `rm -f` na pliku `*.log`, `*.txt` lub `*.md` pod `/tmp` albo `/private/tmp` - razem z podkatalogami, więc obejmuje scratchpad sesji (`/private/tmp/claude-<uid>/<projekt>/<sesja>/scratchpad/`) - przechodzi bez promptu, także dla subagenta (glob dozwolony w nazwie pliku i w segmentach katalogu, np. `rm -f /tmp/*.md`, `rm -f /private/tmp/claude-501/*/scratchpad/*.log`) - ma pozwolić agentowi posprzątać własny scratchpad bez pytania użytkownika o każdą operację. Wyjątek jest celowo wąski i fail-closed: bez flagi `-r`/`-R`/`--recursive` (glob z rozszerzeniem mógłby trafić w katalog o takiej nazwie i skasować go rekurencyjnie), bez `..` w ścieżce, bez komend złożonych (`|`, `;`, `&`, `` ` ``, `$(`, nowa linia/CR - shell wykonałby je jako osobne instrukcje), bez argumentów z `$` lub `~` (ekspansja zmiennej/home shella - hook widzi tekst przed ekspansją, więc nie da się bezpiecznie zweryfikować faktycznej ścieżki) , bez ścieżek poza `/tmp`/`/private/tmp` i bez samego katalogu (ścieżka musi kończyć się plikiem o dozwolonym rozszerzeniu, więc `rm -f /tmp/sub/dir` nie przechodzi) - każdy niespełniony warunek wraca do zwykłej reguły destrukcyjnej z tabeli wyżej, która parsuje flagi `rm` (krótki klaster, forma długa i jej jednoznaczny prefiks) zamiast wyliczać ich pisownię, więc łapie też warianty typu `-R`, `--rec` czy `-d`.

Drugi hook, `UserPromptSubmit` (`hooks/brief_guard.py`, od 1.7.0), pilnuje otwarcia epizodu. Pierwszy prompt sesji, który jest gołym imperatywem bez kontraktu ("napraw build", "W pliku X dodaj endpoint", "ogarnij CI"), dostaje kontrakt z `/monolynx:brief`. Tryby przez `MONOLYNX_BRIEF_GUARD`:

| Tryb | Zachowanie |
|---|---|
| `auto` (domyślny) | prompt przechodzi, a hook wstrzykuje do kontekstu treść skilla `brief` z podstawionym promptem - agent zaczyna od propozycji kontraktu (gotowe / nie ruszać / stop przed), ENTER, praca. Tak, jakbyś wpisał `/monolynx:brief <prompt>`, bez przepisywania |
| `block` | prompt odrzucony z komunikatem "uruchom `/monolynx:brief <prompt>` albo dopisz sam gotowe / nie ruszaj / pytaj przed". Jedyny tryb, w którym kontrakt zostaje w Twoim tekście (to mierzy skan MEGA) |
| `off` | wyłączony |

Jedno przypomnienie na sesję: drugi prompt przechodzi bez reakcji (marker `~/.cache/monolynx/brief-guard/<session_id>`, `/clear` otwiera nową sesję). Bez reakcji przechodzą też: slash-komendy, prompty z kluczem ticketu (`MON-12` niesie kryteria), dłuższe niż 200 znaków, pytania (zakończone `?`), prompty bez czasownika rozkazującego w pierwszych 60 znakach (lista ~80 czasowników PL/EN) i prompty ze słowami kontraktu. Źródło: raport MEGA (T01 139/322, T18 49/282: "most openings are a bare imperative verb with no stated end state").

## Zmienne konfiguracyjne

Ustawiane w `.claude/settings.json` lub `.claude/settings.local.json` (pole `env`):

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `MONOLYNX_PROJECT_SLUG` | - | Slug projektu. Bez niej skille nie zgadują, tylko proszą o konfigurację |
| `MONOLYNX_BRANCH_MODE` | `ticket` | `ticket` - branch musi zawierać numer ticketu; `sprint` - dowolny branch poza main, bez pytań (praca nad całym sprintem na jednym branchu); `off` - brak walidacji |
| `MONOLYNX_AUTOTEST` | `false` | `true` - skill sam odpala lint i testy; `false` - wypisuje komendy i czeka na Twój wynik |
| `MONOLYNX_AUTOCOMMIT` | `false` | `true` - commit po zielonym teście; `false` - wypisuje gotową komendę |
| `MONOLYNX_AUTOPUSH` | `false` | `true` - push bez pytania; `false` - nigdy nie pushuje |
| `MONOLYNX_CONTRACT` | `ask` | `ask` - kontrakt autonomii czeka na ENTER; `auto` - lista "stop przed" jest wypisywana i zapisywana w komentarzu planu jako KONTRAKT (auto), praca idzie dalej bez potwierdzenia |
| `MONOLYNX_SPRINT_RUN` | `0` | `1` - tryb sesji w tle (dyspozytor `/monolynx:sprint-run`): każde czekanie na odpowiedź użytkownika poza kontraktem kończy turę linią `SPRINT-RUN STOP: <powód>`; implikuje `MONOLYNX_CONTRACT=auto` |
| `MONOLYNX_AUTOMR` | `false` | `true` - po faktycznym pushu tworzy merge request przez `glab mr create` (działa TYLKO razem z `AUTOCOMMIT=true` i `AUTOPUSH=true`) |
| `MONOLYNX_SPRINT_RUN_MODE` | - | Tryb ticka dyspozytora. `batch` ustawia sam `sprint_run.sh`; skill `sprint-run` traktuje brak zmiennej (i każdą inną wartość) jako tryb interaktywny |
| `MONOLYNX_SPRINT_PERMISSION_MODE` | wartość z komendy startu sesji w skillu `sprint-run` | Tryb uprawnień sesji roboczych `--bg` startowanych przez dyspozytora (patrz KROK 4 tego skilla) |
| `MONOLYNX_BRIEF_GUARD` | `auto` | Tryb hooka `brief_guard`: `auto` - goły imperatyw dostaje skill `brief` z propozycją kontraktu; `block` - prompt odrzucony z odesłaniem do `/monolynx:brief`; `off` - wyłączony (patrz sekcja Hooki) |

Domyślne wartości są zachowawcze: skille **wypisują komendy zamiast je wykonywać**.
Flagi zmieniają to świadomie.

Przykład - praca nad sprintem na jednym branchu, z automatycznym lintem:

```json
{
  "env": {
    "MONOLYNX_PROJECT_SLUG": "twoj-projekt",
    "MONOLYNX_BRANCH_MODE": "sprint",
    "MONOLYNX_AUTOTEST": "true"
  }
}
```

Przykład - sesja w tle, odpalana przez dyspozytor `/monolynx:sprint-run`:

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

## Sprint bez sesji interaktywnej

Sprint da się przerobić bez człowieka siedzącego przy terminalu. Robią to dwa elementy, które można używać osobno.

**Dyspozytor `/monolynx:sprint-run [sloty]`** to jeden tick: czyta aktywny sprint i stan sesji w tle, startuje brakujące sesje `/monolynx:work` (każdą w osobnym worktree, pod nazwą `<slug>-<KEY>`), zbiera zakończone, raportuje te, które czekają na człowieka, i kończy ostatnią linią:

```
SPRINT-RUN: remaining=<n> running=<m> waiting=<k> done=<d>
```

Dyspozytor **nigdy sam nie pracuje nad ticketem**. Jest cienki z premedytacją: każdy ticket dostaje świeży proces, więc jeden zablokowany ticket nie zatrzymuje reszty, a konteksty się nie mieszają. Tick jest idempotentny - uruchomiony dwa razy pod rząd nie zdubluje sesji ani komentarzy.

**Pętla jest na zewnątrz.** Dwa sposoby:

1. **W sesji interaktywnej** - wbudowany `/loop`:

   ```
   /loop 15m /monolynx:sprint-run
   ```

2. **Bez sesji interaktywnej** - skrypt `plugin/scripts/sprint_run.sh`, który odpala każdy tick w świeżym procesie `claude -p`:

   ```bash
   plugin/scripts/sprint_run.sh --interval 15m --slots 2
   ```

Skrypt uruchamiasz z katalogu projektu (repo git ze slugiem w `.claude/settings.json`). Po instalacji pluginu leży on w katalogu cache pluginu, nie w Twoim repozytorium. Absolutną ścieżkę wypisuje sam dyspozytor na końcu raportu; można ją też rozwinąć ręcznie:

```bash
echo "${CLAUDE_PLUGIN_ROOT}/scripts/sprint_run.sh"
```

Ścieżka zawiera numer wersji pluginu, więc po aktualizacji rozwiń ją ponownie zamiast trzymać starą na stałe w crontabie.

### Flagi skryptu

| Flaga | Domyślnie | Znaczenie |
|---|---|---|
| `--interval` | `15m` | Odstęp między tickami (`15m`, `900`, `1h`) |
| `--slots` | `$MONOLYNX_SPRINT_PARALLEL` albo `2` | Liczba równoległych sesji roboczych |
| `--max-ticks` | `96` | Bezpiecznik: po tylu tickach pętla kończy się kodem 3 |
| `--close` | wyłączone | Po wyczerpaniu sprintu odpala `/monolynx:sprint-end` i `/monolynx:retro`. Domyślnie pętla zatrzymuje się przed zamknięciem, bo merge requesty przegląda człowiek |
| `--once` | wyłączone | Jeden tick i koniec (do crona, który sam jest pętlą) |
| `--help` | - | Wypisuje nagłówek skryptu z pełnym opisem |

### Kody wyjścia

| Kod | Znaczenie |
|---|---|
| `0` | Sprint wykonany: `remaining=0 running=0`, nic nie czeka na człowieka |
| `2` | Trzy ticki pod rząd bez poprawnej linii statusu - dyspozytor nie działa, pętla umiera głośno zamiast kręcić się bez efektu |
| `3` | Osiągnięto `--max-ticks` |
| `4` | Zostały wyłącznie sesje czekające na człowieka - dokończ je ręcznie (`claude agents`, `claude attach <id>`) |

Kody `64`, `65`, `69`, `75` i `78` to błędy startu (nieznany argument lub flaga `--interval`, `--slots`, `--max-ticks` bez wartości; uruchomienie spoza repozytorium git; brak `claude` w `PATH`; pętla już działa dla tego repozytorium; brak sluga projektu).

### Wymagane flagi trybu w tle

Tryb `-p` nie odpowie na żadne pytanie, dlatego sesje w tle muszą mieć komplet flag w `.claude/settings.json` projektu (pole `env`, nie tylko eksport w shellu - sesja dziedziczy środowisko, ale nie Twój terminal):

```json
{
  "env": {
    "MONOLYNX_PROJECT_SLUG": "twoj-projekt",
    "MONOLYNX_CONTRACT": "auto",
    "MONOLYNX_SPRINT_RUN": "1",
    "MONOLYNX_AUTOTEST": "true",
    "MONOLYNX_AUTOCOMMIT": "true",
    "MONOLYNX_AUTOPUSH": "true",
    "MONOLYNX_AUTOMR": "true"
  }
}
```

`AUTOTEST`, `AUTOCOMMIT` i `AUTOPUSH` są wymagane - bez nich preflight dyspozytora zatrzymuje tick i nie startuje żadnej sesji. `AUTOMR` jest zalecane: bez niego branch jest wypchnięty, ale merge request tworzysz ręcznie. Skrypt `sprint_run.sh` ustawia te flagi sam dla procesów, które uruchamia; każdą można nadpisać w środowisku. Poza nimi eksportuje też `MONOLYNX_SPRINT_RUN_MODE=batch` (tick wie, że jego raportu nikt nie przeczyta) i `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` (domyślnie `1`).

Plik z flagami musi być **śledzony przez gita**. Sesja robocza dostaje worktree utworzony przez `claude --bg --worktree`, a w worktree trafia tylko to, co git zna: `.claude/settings.local.json` jest ignorowany, więc ani uprawnienia, ani flagi AUTO* z niego nie docierają do sesji w tle. Druga poprawna droga to środowisko procesu, który odpala tick - `sprint_run.sh` eksportuje flagi sam, a `claude --bg` dziedziczy środowisko.

### Uruchomienie w tmux i z crona

`${CLAUDE_PLUGIN_ROOT}` istnieje **tylko wewnątrz sesji Claude Code**. W zwykłym terminalu rozwinie się do pustego stringu, więc najpierw rozwiń ścieżkę w Claude Code (dyspozytor wypisuje ją sam na końcu raportu):

```bash
echo "${CLAUDE_PLUGIN_ROOT}/scripts/sprint_run.sh"
```

Rozwiniętą ścieżkę podstaw do komend w terminalu:

```bash
tmux new -d -s monolynx-sprint "/pelna/sciezka/z/echo/sprint_run.sh --slots 2"
```

```
*/15 * * * * cd /sciezka/projektu && "/pelna/sciezka/z/echo/sprint_run.sh" --once >> ~/sprint-run.log 2>&1
```

Log pętli trafia do `.git/monolynx/sprint-run.log`, a plik PID w tym samym katalogu pilnuje, żeby dla jednego repozytorium działała tylko jedna pętla. Na stdout `sprint_run.sh --once` wypisuje wyłącznie linie logu pętli razem z linią statusu ticka; pełny raport dyspozytora zostaje w pliku logu, więc wpis w crontabie nie zalewa skrzynki.

## Migracja i kompatybilność

Plugin to **preferowana ścieżka** dla użytkowników Claude Code CLI. Nie zastępuje jednak dotychczasowego mechanizmu instalacji skilli, oba istnieją równolegle.

### Decyzja: `install_monolynx_skills` i kopie skilli pozostają

Narzędzie MCP `install_monolynx_skills` oraz kopie skilli w repozytorium **pozostają i nie są usuwane**:

- `src/monolynx/static/skills/`: źródło skilli pobieranych przez `install_monolynx_skills`, synchronizowane z `plugin/skills/` przez `make sync-skills`.

Są one **alternatywą dla użytkowników niekorzystających z mechanizmu pluginów**, między innymi:

- dostęp przez claude.ai (web), bez CLI i bez możliwości dodania marketplace,
- środowiska, w których nie da się dodać marketplace ani zainstalować pluginu,
- ręczna instalacja skilli do katalogu skilli projektu (narzędzie zwraca treść skilla z podmienionymi placeholderami `<PROJECT-SLUG>` i `<PROJECT-ID>`, gotową do zapisu na dysk),
- **runtime'y inne niż Claude Code** - patrz niżej.

Innymi słowy:

- **Plugin**: preferowana, jednorazowa instalacja dla Claude Code CLI (skille + agenci + zdalny MCP w jednym).
- **`install_monolynx_skills`**: ścieżka manualna / fallback dla środowisk bez pluginów oraz dla Codex i Cursora.

### Codex i Cursor

Marketplace pluginów jest mechanizmem Claude Code. Codex i Cursor nie mają jego odpowiednika, ale **czytają ten sam format `SKILL.md`** (katalog + frontmatter `name` / `description` + opcjonalne pliki towarzyszące). Różni się wyłącznie katalog docelowy, dlatego `install_monolynx_skills` przyjmuje parametr `target`:

| `target` | Katalog | Runtime |
|---|---|---|
| `claude` (domyślnie) | `.claude/skills/` | Claude Code |
| `codex` | `.codex/skills/` | Codex CLI |
| `cursor` | `.cursor/skills/` | Cursor |

```
install_monolynx_skills(project_slug="moj-projekt", target="codex")
install_monolynx_skills(project_slug="moj-projekt", skill_names=["monolynx-work"], target="codex")
```

Narzędzie zwraca `skills: [{name, files: [{relative_path, content}]}]`. **Zapisz wszystkie pliki z listy `files`** - skill może mieć pliki towarzyszące obok `SKILL.md` (np. `monolynx-work` ma `pipeline.md` i `review-rubric.md`); pominięcie ich okroi skill o część instrukcji.

Serwer MCP podłącza się poza skillami:

- **Codex**: wpis w `~/.codex/config.toml` (grupa `mcp_servers`), HTTP + nagłówek `Authorization: Bearer osk_...`.
- **Cursor**: `.cursor/mcp.json` w projekcie lub `~/.cursor/mcp.json` globalnie, klucz `mcpServers` - ten sam kształt co `.mcp.json` pluginu.

Agenci pluginowi (`plugin/agents/`) są mechanizmem Claude Code. W Codeksie i Cursorze skille `/monolynx:work` i `/monolynx:work-simple` dobierają role z `AGENTS.md` w korzeniu repo.

### Serwer MCP bez zmian funkcjonalnych

Plugin nie modyfikuje serwera ani nie odwołuje się do jego wewnętrznych funkcji. Deklaruje wyłącznie dostęp (HTTP + OAuth) do **istniejącego** serwera MCP pod adresem `https://monolynx.com/mcp`. Cała logika narzędzi (Scrum, 500ki, Monitoring, Wiki, Połączenia, Plan pracy, w tym samo `install_monolynx_skills`) działa po stronie serwera, niezależnie od tego, czy łączysz się przez plugin, czy w inny sposób.

## Changelog

### 1.8.2

Hotfix po pierwszym przebiegu dyspozytora na realnym sprincie (MON-173).

- **Hook `bash_guard.py` nie zwraca `ask` w trybie sesji w tle** (`MONOLYNX_SPRINT_RUN=1`): sesja startowana przez dyspozytor nie ma nikogo, kto kliknie potwierdzenie, a tryb uprawnien sesji nie znosi decyzji `ask` z hooka. Zamiast tego `deny` z instrukcja: uzyj wariantu nieniszczacego (`rm` bez `-f`/`-r` na wlasnym pliku, `docker stop` + `docker rm` bez `-f`) albo zakoncz ture linia `SPRINT-RUN STOP: <powod>`. Poza trybem w tle hook pyta jak dotad.
- **`sprint_run_state.py` rozpoznaje sesje stojaca na promptcie o zgode** (`waitingFor: "permission prompt"` w `claude agents --json --all`, pole wystepuje takze na wpisach background) jako `waiting` z komenda `claude attach`, zamiast liczyc ja jako `running` w nieskonczonosc.

### 1.8.1

Poprawki z pierwszego realnego przebiegu dyspozytora (MON-172).

- **Deadlista stanów sesji zamiast whitelisty**: literały `working` i `done` z realnego `claude agents --json --all` są rozpoznawane, a stan spoza listy liczy się jako sesja żywa i trafia do raportu z ostrzeżeniem. Wcześniej pracująca sesja w stanie `working` wypadała poza whitelistę, była uznawana za martwą i dostawała auto-wznowienie, czyli duplikat.
- **ANSI i `\r` w ogonach logów**: `claude logs` zwraca render terminala, nie tekst. Skrypt stanu zdejmuje sekwencje sterujące przed dopasowaniem wzorca, a skill przepuszcza ogon przez `sed` i `tr`, żeby plik był czytelny dla człowieka.
- **Korekta linii statusu o sesje wystartowane w ticku**: `remaining` i `running` w `SPRINT-RUN: ...` uwzględniają dispatch. Bez tego tick startujący ostatnie wolne tickety raportował `remaining=0 running=0`, a pętla kończyła się, zostawiając świeże sesje bez nadzoru.
- **`MONOLYNX_SPRINT_RUN_MODE` zamiast wykrywania terminala**: `[ -t 0 ]` w narzędziu Bash jest fałszywe także w sesji z człowiekiem, więc tryb `batch` bierze się ze zmiennej ustawianej przez `sprint_run.sh`.
- **`MONOLYNX_SPRINT_PERMISSION_MODE`**: tryb uprawnień sesji roboczych `--bg` da się nadpisać. Worktree sesji nie widzi `.claude/settings.local.json`, więc uprawnienia i flagi AUTO* muszą pochodzić ze śledzonego `.claude/settings.json` albo ze środowiska procesu odpalającego tick.
- **Fixture z realnego przebiegu**: wyjście `claude` 2.1.270 zapisane w `tests/fixtures/claude/`, na nim pracują testy parsera. Nowa reguła projektu `external-tool-output` wymaga tego od każdego kodu parsującego wyjście zewnętrznego narzędzia.

### 1.8.0

- **Nowy skill `/monolynx:sprint-run`**: cienki dyspozytor nazwanych sesji w tle. Jeden idempotentny tick czyta aktywny sprint i stan sesji, startuje brakujące sesje `claude --bg --worktree <KEY> --name <slug>-<KEY>` z komendą `/monolynx:work <KEY>`, zbiera zakończone (komentarz z identyfikatorem sesji, potem `claude rm`), raportuje czekające na człowieka i kończy linią `SPRINT-RUN: remaining=... running=... waiting=... done=...`. Nigdy sam nie pracuje nad ticketem. Preflight wymaga `AUTOTEST`, `AUTOCOMMIT` i `AUTOPUSH`; bez nich tick kończy się bez startowania sesji. Tickety migracyjne startują solo, jedno auto-wznowienie na ticket.
- **Pętla zewnętrzna `plugin/scripts/sprint_run.sh`**: świeży `claude -p` per tick, flagi `--interval`, `--slots`, `--max-ticks`, `--close`, `--once`, kody wyjścia 0/2/3/4, blokada drugiej pętli na tym samym repozytorium, log w `.git/monolynx/`. Działa tak samo lokalnie, w tmux na VPS i z crona.
- **`plugin/scripts/sprint_run_state.py`**: wyliczanie werdyktu per ticket (`done`, `needs_human`, `waiting`, `free`, `running`) z listy sesji, statusów ticketów i ogonów logów; dyspozytor nie zgaduje stanu na podstawie tekstu, tylko czyta policzony JSON. Testy jednostkowe skryptu pętli i skryptu stanu.
- **Tryb sesji w tle (MON-165)** wykorzystany przez dyspozytora: `MONOLYNX_SPRINT_RUN=1` sprawia, że `work` / `work-simple` nie czekają na odpowiedzi, tylko kończą turę linią `SPRINT-RUN STOP: <powód>`, a `MONOLYNX_CONTRACT=auto` przepuszcza kontrakt autonomii bez potwierdzenia.
- **Wyjątek w hooku na sprzątanie `/tmp`**: `rm -f` na pliku `*.log`, `*.txt` lub `*.md` pod `/tmp` albo `/private/tmp`, razem z podkatalogami (scratchpad sesji), przechodzi bez promptu, także dla subagenta (`is_tmp_cleanup`, sprawdzane przed listą operacji destrukcyjnych). Wyjątek jest wąski i fail-closed: bez `-r`/`-R`/`--recursive`, bez `..`, bez ekspansji shella (`$`, `~`), bez komend złożonych, bez ścieżek spoza `/tmp` i bez samego katalogu.
- **Reguła `rm` w denyliście parsuje flagi zamiast wyliczać ich pisownię**: łapane są też `rm -R`, `rm -RF`, jednoznaczne prefiksy długich opcji (`--rec`, `--fo`), krótka forma `-d` oraz flaga poza pierwszą pozycją (`rm -i -rf /`). Wcześniej wszystkie te warianty wychodziły z hooka bez żadnej decyzji.
- **Sekcja mutacji w `/monolynx:project-toolchain`**: nowy opcjonalny KROK 4 wykrywa narzędzie do testów mutacyjnych dla 9 ekosystemów (Python mutmut, JS/TS Stryker, Java/Kotlin PIT, C#/.NET Stryker.NET, Go gremlins, PHP Infection, Rust cargo-mutants, Scala Stryker4s, Ruby mutant). Sprawdzenie obecności jest per narzędzie - `command -v` nie znajdzie pluginu Mavena, `dotnet tool` ani podkomendy `cargo`.
- **Strona wiki `toolchain` zyskuje sekcję `## Mutacje`**: narzędzie, powód braku, komenda pełna i diffowa, ścieżka i format raportu, moduły rdzenia, baseline, cel, kto odpala. Sekcja powstaje zawsze - także pusta, gdy narzędzia nie ma - żeby kolejne skille miały stabilną strukturę do odczytu.
- **Skill nie instaluje narzędzia mutacyjnego** (precedens: graphify) i nie blokuje się na jego braku: pokazuje instrukcję instalacji i idzie dalej. Komenda diffowa zapisywana jest dopiero po odczytaniu faktycznej flagi z `--help` - warianty (`--since`, `--in-diff`, `--git-diff-lines`, `scmMutationCoverage`) różnią się między wersjami narzędzi.
- Naprawiony przykład wywołania `create_wiki_page` w kroku zapisu: tool nie ma parametru `slug`, slug powstaje z tytułu.

### 1.7.0

Poprawki wyprowadzone z raportu MEGA Assessment (skan 377 epizodów, 2026-09-09): najsłabsze cechy to prawa decyzyjne (T18), modelowanie stanu agenta (T03), orientacja na przyczynę (T08), precyzja ograniczeń (T05), integracja wyników (T20). Mocne strony (kryteria akceptacji, trwała pamięć, inspekcja przed edycją, niezależna weryfikacja) zostają bez zmian.

- **Kontrakt autonomii** w `work` / `work-simple`: przed startem zespołu Team Manager wypisuje listę "zatrzymuję się przed" (stałe: limit poprawek, flagi AUTO*, hook, SCOPE GUARD nie podlegają negocjacji), jedno potwierdzenie usera, zapis w komentarzu planu razem z baseline (`BASELINE_SHA`, brudne pliki) i identyfikatorami pipeline. Pozycje "stop przed" trafiają do promptów developerów jako STOP CONDITIONS; naruszenie bez zgłoszenia `STOP:` punktowane w rubryce (-10). Zastępuje sterowanie przez przerywanie w trakcie.
- **Sekcje "Nie ruszać" i "Odwracalność"** w szablonie `ticket-create`; `ticket-review` ocenia je jako 7. i 8. kryterium formy i proponuje brakujące z dowodami `plik:linia`; Researcher w `work` przepisuje je do sekcji "Granice" raportu.
- **Tryb defektu** (etykieta `bug`, tytuł "Fix/Napraw", wskazanie usera): Researcher musi nazwać mechanizm (reprodukcja z dosłownym wynikiem, hipoteza, dowód `plik:linia`, klasa błędu), developer pisze test regresyjny przez ścieżkę przyczyny w osobnym pliku, Team Manager w fazie lint/test chowa fix stashem i pokazuje test czerwony, potem zielony; rubryka ma trzy wiersze aktywne tylko w tym trybie (fix bez mechanizmu -20, brak testu regresyjnego -20, klasa błędu niesprawdzona -5). W `work-simple` research kodu jest wtedy obowiązkowy.
- **Rekoncyliacja raportów** (`work` 6e, `work-simple` 2.4): każdy raport developera dostaje decyzję Team Managera (akceptuję / odrzucam -> re-run / do wyjaśnienia -> jedno pytanie) po porównaniu z `git status`; sprzeczne założenia dwóch agentów rozstrzyga TM i przekazuje krytykowi.
- **Pełny lint+test po ostatniej zmianie** (`work` 6.6c, `work-simple` 3.5.3): iteracja może testować obszar, `in_review` wymaga pełnego przebiegu, po którym nikt nic nie zmienił.
- **Reguły krytyka do pamięci agenta** także w `work` (dotąd tylko `work-simple`): linia `REGUŁA:` obowiązkowa przy NEEDS WORK, opisuje wzorzec, nie plik; powtórzona reguła jest oznaczana jako materiał dla `retro`.
- **Re-run z diffem**: nowy subagent po NEEDS WORK dostaje `git diff <BASELINE_SHA>` swoich plików dosłownie, nie opis słowny poprzedniej pracy.
- **Wznowienie** w `work` (1b) i `work-simple`: ticket `in_progress` z komentarzem planu -> propozycja wznowienia od ostatniego domkniętego etapu, bez ponownego researchu i bez ponownego pomiaru baseline (bierze go z komentarza planu). Sygnał końca epizodu w podsumowaniu (`/clear` + `resume`).
- **Nowy skill `/monolynx:brief`**: kontrakt epizodu bez ticketu (gotowe / nie ruszać / autonomia) w jednej turze, bez MCP; po ENTER praca zaczyna się od razu.
- **Hook `brief_guard.py`** (`UserPromptSubmit`): pierwszy goły imperatyw sesji bez kontraktu dostaje automatycznie skill `brief` (tryb `auto`: wstrzyknięcie treści skilla do kontekstu, agent proponuje kontrakt); `MONOLYNX_BRIEF_GUARD=block` zamiast tego odrzuca prompt z komunikatem o `/monolynx:brief`, `off` wyłącza. Czasownik rozkazujący wykrywany w pierwszych 60 znakach (PL/EN), jedna reakcja na sesję.
- **Nowy skill `/monolynx:resume`**: read-only odtworzenie stanu ticketu z komentarzy, pipeline, kryteriów i gita, z następnym krokiem.
- **Nowy skill `/monolynx:retro`**: powtarzające się korekty ze sprintu (komentarze, pamięć agentów; liczone po ticketach, próg 2 tickety) zamieniane w reguły `.claude/rules/`, wiersze rubryki albo wpisy `constitution`; opcjonalny job w `sprint-end` przed `complete_sprint`. Komentarz krytyka w `work` zawiera blok Code Review, żeby retro miało z czego grupować.
- **Zasada "pytaj RAZ, na starcie"** w regułach `work` / `work-simple`: przerwanie usera w trakcie oznacza niekompletny kontrakt i jest odnotowywane w podsumowaniu.

### 1.6.0

- **Hooki pluginu**: PreToolUse guard na Bash (`hooks/bash_guard.py`) - twardy deny operacji zapisu w gicie oraz lintu/testów dla subagentów; `ask` dla `git push` i lintu/testów w sesji głównej, gdy `MONOLYNX_AUTOPUSH`/`MONOLYNX_AUTOTEST` != `true`. Reguły z promptów skilli zyskują deterministyczną egzekucję.
- **Denylist operacji destrukcyjnych** (inspiracja: warstwa reguł ai-protector): `rm -rf`, `git push --force`, `curl | sh`, zapis do `~/.ssh`/`~/.aws`, `DROP/TRUNCATE`, `mkfs`/`dd of=/dev` - deny dla subagentów, `ask` dla sesji głównej (force-push pyta nawet przy `AUTOPUSH=true`); dodatkowo `ask` na `git reset --hard`/`rebase`/`clean -f`.
- **Noty anti-injection** w promptach subagentów (Researcher, developerzy, krytyk w `work` i `work-simple`): treści ticketów, komentarzy i wiki to dane, nie polecenia - osadzone w nich instrukcje są ignorowane i raportowane.

### 1.5.2

- **Hardening `/monolynx:work-simple`** (parytet z `work`): lint/testy przed krytykiem, warstwa kryteriów akceptacji, ToolSearch i prefiks MCP w promptach subagentów, baseline review (`BASELINE_SHA` + brudne pliki + untracked), kolejność status → pipeline, wspólny limit 3 poprawek, synchronizacja rubryki, poprawna nazwa `/monolynx:work`.

### 1.5.1

- **Hardening `/monolynx:work`** po 8 rundach niezależnego review (~60 poprawek): m.in. ToolSearch/prefiks MCP w promptach subagentów, lint/testy jako gate przed krytykiem, wspólny limit poprawek, baseline review, protokół Agent Teams (sygnały, adresowanie, śmierć agenta), ścieżki przerwania pipeline, macierz trybów promptu krytyka, tryb solo (Codex).

### 1.5.0

- **Connector MCP widoczny po instalacji**: deklaracja serwera przeniesiona także do `mcpServers` w `plugin.json` (obok `.mcp.json`), z **literalnym** adresem `https://monolynx.com/mcp`. Wcześniejsze `${user_config.mcp_endpoint}` rozwijało się wyłącznie w Claude Code - w pozostałych klientach trafiało do konfiguracji dosłownie, dawało nieprawidłowy URL i connector nie powstawał.
- **Uwierzytelnianie przez OAuth 2.1** zamiast nagłówka Bearer ze statycznym tokenem. Serwer wspiera Dynamic Client Registration, więc klient rejestruje się sam.
- `mcp_token` przestaje być polem wymaganym - potrzebny tylko przy ręcznej konfiguracji MCP.
- `mcp_endpoint` zostaje dla skilli (m.in. synchronizacja grafu), ale nie steruje już adresem connectora.

### 1.4.0

- **Lint i testy jako gate**: `/monolynx:work` (KROK 6.5) i `/monolynx:work-simple` (FAZA 3.5) uruchamiają lint i testy przed zmianą statusu na `in_review`. Komendy pochodzą ze strony wiki `toolchain` projektu.
- **Nowy skill `/monolynx:project-toolchain`**: jednorazowo wykrywa komendy lint/test projektu i zapisuje je jako stronę wiki `toolchain`. Wspiera Makefile, Python, Node, Rust, Go, PHP, Ruby, Javę.
- **Flagi środowiskowe**: `MONOLYNX_AUTOTEST`, `MONOLYNX_AUTOCOMMIT`, `MONOLYNX_AUTOPUSH` (domyślnie `false` - skill wypisuje komendy zamiast je wykonywać) oraz `MONOLYNX_BRANCH_MODE` (`ticket` / `sprint` / `off`) dla pracy na jednym branchu przez cały sprint.
- **Krytyk ocenia według rubryki** (`review-rubric.md`): twarde odjęcia punktowe z lokalizacją `plik:linia` zamiast oceny "na wyczucie". Próg zaliczenia ujednolicony do 82.
- **Naprawiona synchronizacja krytyka z developerami**: w Agent Teams developerzy wołają krytyka przez `SendMessage`, bez Agent Teams krytyk uruchamiany jest sekwencyjnie po nich. Wcześniej krytyk mógł oceniać niekompletny `git diff`.
- **Czas pracy raportuje każdy agent osobno** - Team Manager loguje zgłoszone wartości zamiast kopiować jeden wspólny pomiar. Czas Researchera jest teraz logowany.
- **Rozłączny przydział plików** w KROK 5: agenci o wspólnych plikach pracują sekwencyjnie, nie równolegle.
- **Skille wieloplikowe**: `install_monolynx_skills` zwraca wszystkie pliki skilla (`files[]`), nie tylko `SKILL.md`.
- **Codex i Cursor**: parametr `target` w `install_monolynx_skills` (`claude` / `codex` / `cursor`).
- **`/monolynx:work-simple`** obsługuje tickety do 3 SP (wcześniej deklarował < 8 SP).
- **`/monolynx:help` przepisany**: wymienia wszystkie 14 skilli (wcześniej brakowało `work-simple` i całej rodziny `wiki-*`), dodaje sekcję setupu jednorazowego, tabelę wyboru `work` vs `work-simple`, tabelę zmiennych konfiguracyjnych i instrukcje dla Codeksa oraz Cursora.
- **Usunięty `src/monolynx/static/starter-pack/`**: martwa trzecia kopia skilli (7 z 14, `monolynx-work` starszy o 84 linie), nieserwowana przez żaden endpoint ani tool. Ręczna instalacja idzie przez `install_monolynx_skills`.

### 1.2.2

- `/monolynx:work` i `/monolynx:work-simple` dobieraja agentow dynamicznie i zaleznie od runtime'u: **Claude Code** czyta `.claude/agents/*.md` (plus agenci pluginowi jako fallback), **Codex** czyta role z `AGENTS.md` w korzeniu repo. Agenci/role zdefiniowane w projekcie sa preferowanym zrodlem prawdy dla stacku i konwencji.
- Przyklady uruchamiania agentow w `/monolynx:work` sa teraz schematem z placeholderami zamiast sugerowania stalego zestawu `backend-developer` / `frontend-developer` / `code-reviewer`.

### 1.2.1

- **Nowy skill `/monolynx:sprint-end`**: orkiestruje zamknięcie sprintu jako pipeline `sprint_close` (stepy `wiki-update` → `wrap-up`). Step `wiki-update` ma joby `wiki-ingest` (INGEST logów pracy ze sprintu do wiki), `wiki-lint` (audyt wiki) i `wiki-clean` (czyszczenie stron logów pipeline sprintu przez `clean_pipeline_logs`). Step `wrap-up` ma joby `close-sprint` (realne `complete_sprint` - niedokończone tickety wracają do backlogu, z potwierdzeniem użytkownika) i `summary`. Bez argumentu bierze aktywny sprint.
- Raportowanie do modułu Pipelines jest **best-effort**: błąd toola pipeline nigdy nie przerywa zamknięcia sprintu; gdy serwer MCP nie ma modułu Pipelines lub typu `sprint_close`, skill pomija instrumentację i wykonuje samo `complete_sprint`.
- **Wymaga narzędzia MCP** `clean_pipeline_logs` oraz obsługi `pipeline_type="sprint_close"` w `create_pipeline` na serwerze.

### 1.2.0

- **Instrumentacja pipeline w skillach `work` i `work-simple`**: każdy krok pracy raportuje przebieg do nowego modułu Pipelines (obserwowalność pracy agentów, wzorowana na GitLab CI/CD). Tworzy się pipeline `ticket_work` ze stepami research → coding → wrap-up, każdy agent dostaje swój job, a jego raport (co zrobił, decyzje, pliki) zapisywany jest jako strona wiki podpięta pod job. Status, czas trwania i logi widać na żywo w zakładce "Pipelines" projektu.
- Raportowanie jest **best-effort**: błąd toola pipeline nigdy nie przerywa pracy nad ticketem; gdy serwer MCP nie ma modułu Pipelines (starsza wersja), skille działają jak dotychczas.
- **Wymaga nowych narzędzi MCP na serwerze** (Monolynx >= moduł Pipelines): `create_pipeline`, `create_pipeline_job`, `update_pipeline_job`, `append_job_log`, `finish_pipeline`, `list_pipelines`, `get_pipeline`, `get_pipeline_job_log`.

### 1.1.x

- Skille LLM Wiki (`wiki-init`, `wiki-ingest`, `wiki-lint`, `wiki-sync-merge`), Spec-Driven Development (`spec_page_id`), pakiet skilli i agentów oraz zdalny dostęp MCP.
