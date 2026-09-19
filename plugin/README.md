# Monolynx: plugin Claude Code i Codex

Plugin Monolynx dostarcza wspólny zestaw skilli i zasobów dla Claude Code oraz Codex. Źródłem prawdy jest `plugin/skills`; instalator MCP udostępnia wygenerowane z niego, samowystarczalne pakiety pojedynczych skilli.

- **23 skilli** dające komendy w przestrzeni nazw `/monolynx:*` (checklista konfiguracji projektu, kontrakt epizodu bez ticketu, praca z ticketami w pełnym i uproszczonym flow, dyspozytor sprintu w sesjach w tle, kolejka merge MR/PR, wznowienie po `/clear`, tworzenie i recenzja zadań, retro korekt, zamknięcie sprintu, testy mutacyjne zamieniane na pracę, wyszukiwanie w wiki, metoda LLM Wiki - inicjalizacja, integracja źródeł, post-merge sync i audyt, mapa skilli, generowanie skryptu CI grafu zależności, lokalna synchronizacja grafu, generowanie nieblokującego kroku CI z testami mutacyjnymi),
- **7 agentów** wyspecjalizowanych w rolach zespołu (backend, frontend, baza danych, DevOps, QA, code review, dokumentacja),
- **zdalny serwer MCP** Monolynx (HTTP, autoryzacja Bearer), który udostępnia narzędzia do Scrum, 500ki, Monitoringu, Wiki, Połączeń i Planu pracy.

Plugin nie zawiera kopii serwera MCP. Deklaruje jedynie dostęp do istniejącego, zdalnego serwera Monolynx pod adresem z konfiguracji.

## Wymagania

Zanim zainstalujesz plugin, przygotuj:

1. **Konto Monolynx** z dostępem do co najmniej jednego projektu (członkostwo w projekcie jest weryfikowane po stronie serwera MCP).
2. **Logowanie OAuth** do zdalnego MCP. Token API `osk_...` z `/dashboard/profile/tokens` jest alternatywą dla ręcznej konfiguracji, a nie warunkiem instalacji pluginu.
3. **Claude Code CLI 2.1.273 lub Codex CLI 0.154.0**: wersje użyte do weryfikacji opisanej poniżej. Starsze wersje nie zostały sprawdzone. Skrypty pomocnicze wymagają Python 3.10+, a `sprint_run.sh` także Bash i Claude Code CLI.

## Instalacja

Instalacja przebiega w dwóch krokach: najpierw dodajesz marketplace (źródło pluginu), potem instalujesz z niego plugin `monolynx`.

### Claude Code: pełny plugin

Wskaż katalog zawierający `.claude-plugin/marketplace.json` (root repozytorium):

Uruchom z katalogu głównego sklonowanego repozytorium:

```bash
claude plugin marketplace add .
claude plugin install monolynx@monolynx
```

Zapis `monolynx@monolynx` oznacza: plugin o nazwie `monolynx` ze źródła (marketplace) o nazwie `monolynx`.

### Codex: pełny plugin

Ten sam marketplace `.claude-plugin/marketplace.json` wskazuje `./plugin`. Codex czyta natywny manifest `plugin/.codex-plugin/plugin.json`, a Claude Code swój manifest `.claude-plugin/plugin.json`; oba korzystają z tego samego `skills/` i endpointu MCP.

```bash
codex plugin marketplace add .
codex plugin add monolynx@monolynx
```

Rozpocznij nową sesję po instalacji. W Codex wybierz `$monolynx:next` albo `$monolynx:help`; w Claude Code użyj `/monolynx:next` albo `/monolynx:help`. Definicje agentów Claude w `agents/` nie stają się automatycznie agentami Codex. Dostępne role i sposób delegowania zależą od klienta. Dyspozytor `sprint-run` nadal uruchamia sesje przez CLI `claude`; ten ticket zmienia dystrybucję jego zasobów, nie algorytm ani backend sesji.

### Ze zdalnego repozytorium (Claude Code)

Wskaż adres repozytorium z `marketplace.json`:

```text
/plugin marketplace add <url-repo>
/plugin install monolynx@monolynx
```

Codex przyjmuje także adres Git przez `codex plugin marketplace add`. Dostęp do prywatnego repozytorium wymaga uprawnień Git. W nowym projekcie uruchom skill `setup`, aby sprawdzić konfigurację projektu.

### Pojedyncze skille przez MCP

Ta ścieżka działa bez instalacji pełnego pluginu. Najpierw skonfiguruj zdalny MCP w wybranym kliencie:

```bash
# Claude Code
claude mcp add --transport http monolynx https://monolynx.com/mcp
# Codex
codex mcp add monolynx --url https://monolynx.com/mcp
codex mcp login monolynx
```

W Claude Code dokończ logowanie przez `/mcp`. Nie dodawaj drugiego połączenia, jeśli MCP jest już dostępny z pełnego pluginu. Następnie poproś agenta o dwa wywołania:

```text
install_monolynx_skills(project_slug="twoj-projekt", target="codex")
install_monolynx_skills(project_slug="twoj-projekt", target="codex", skill_names=["monolynx-work"])
```

Pierwsze zwraca katalog bez treści plików. Drugie zwraca `skills[].files[]` z `relative_path` i `content`. Zapisz **każdy** plik, zachowując podkatalogi. Wybierz `target="claude"` dla `.claude/skills`, `target="codex"` dla `.agents/skills` albo `target="cursor"` dla `.cursor/skills`. Nie przenoś ani nie usuwaj automatycznie wcześniejszych instalacji w `.codex/skills`.

Standalone ma nazwę `monolynx-work`, a plugin namespace `monolynx:work`. Wybieraj skill po nazwie widocznej w kliencie. Sam instalator dostarcza skille i ich zasoby, nie instaluje globalnych hooków ani definicji agentów.

Skrypty są dostarczane w `scripts/`, wymagane fragmenty instrukcji w `references/`, względem danego `SKILL.md`. Kopie powstają z `plugin/scripts` i sekcji źródłowych skilli. Nie wymagają ścieżki autora, symlinków ani sąsiedniego skilla. Polecenia proponujące użytkownikowi osobny workflow nadal wymagają wybrania lub pobrania tego skilla.

### Wyniki smoke testu (2026-09-16)

Sprawdzone wersje: Claude Code **2.1.273**, Codex CLI **0.154.0**. Test używał kopii dystrybucji w katalogu tymczasowym i osobnego katalogu projektu.

| Klient / ścieżka | Sprawdzenie | Wynik |
|---|---|---|
| Claude Code / plugin | `claude plugin validate plugin` oraz `claude --plugin-dir <kopia-pluginu> plugin list --json` | Manifest poprawny; plugin `monolynx@inline` 1.15.0 aktywny w sesji, MCP `monolynx` wykryty. |
| Codex / plugin | App-server `plugin/read` ze wspólnym marketplace wskazującym `./plugin` | 23 skille `monolynx:*`, MCP `monolynx` i dwa hooki wykryte. |
| Codex / pojedynczy skill | Kopia `monolynx-work` do `.agents/skills`, app-server `skills/list` | Skill `monolynx-work` aktywny, brak błędów odczytu. |
| Instalator MCP | Testy integracyjne dla `claude`, `codex`, `cursor` | Katalog bez treści, wybór skilla i rekursywne pliki z właściwymi ścieżkami. |

To test ładowania lokalnej dystrybucji i kontraktu instalatora. Nie zastępuje logowania OAuth ani wykonania workflow; nie zmieniał trwałych instalacji użytkownika. Hooki wymagają osobnego zaufania w kliencie.

Formaty dystrybucji opisują [dokumentacja pluginów Codex](https://learn.chatgpt.com/docs/build-plugins), [skilli Codex](https://learn.chatgpt.com/docs/build-skills) oraz [pluginów Claude Code](https://code.claude.com/docs/en/plugins-reference).

## Konfiguracja (`userConfig`)

Poniższa konfiguracja `userConfig` dotyczy Claude Code. Codex korzysta ze swojej konfiguracji MCP i zmiennych środowiskowych; nie interpretuje pól `user_config` jako konfiguracji projektu.

| Parametr | Wymagany | Wartość domyślna | Opis |
|----------|----------|------------------|------|
| `mcp_token` | nie | brak | Token API Monolynx (`osk_...`). Oznaczony jako `sensitive`, trafia do keychain systemu. Potrzebny tylko przy ręcznej konfiguracji MCP - connector pluginu uwierzytelnia się przez OAuth. |
| `mcp_endpoint` | nie | `https://monolynx.com/mcp` | Adres Twojej instancji, używany przez skille (m.in. synchronizację grafu). **Nie zmienia adresu connectora** - ten jest zapisany na stałe w manifeście. |
| `project_slug` | nie | brak | Pole konfiguracji pluginu. Skille wymagają sluga podanego przez użytkownika lub zmiennej `MONOLYNX_PROJECT_SLUG`; samo to pole jej nie eksportuje. |

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

Rzeczywiste nazwy narzędzi zależą od klienta i connectora. Agent odczytuje dostępne schematy zamiast zakładać prefix `mcp__monolynx__`.

### Ustalanie slug projektu (`project_slug`)

Skille pluginu pracują w kontekście jednego projektu. Slug ustalany jest w stałej kolejności:

1. Slug podany jawnie przez użytkownika.
2. Zmienna środowiskowa **`MONOLYNX_PROJECT_SLUG`**.
3. Jawna konfiguracja projektu: ustawienie `MONOLYNX_PROJECT_SLUG` w konfiguracji klienta lub `.env`, odczytane jako dane zgodnie z [kontraktem runtime](references/runtime.md).
4. Jeśli żadna wartość nie jest dostępna, agent pyta o slug. Do tego czasu nie wykonuje operacji MCP wymagających projektu.

Dzięki temu jeden plugin obsługuje wiele projektów. Sam plik `.env` nie eksportuje zmiennych do procesu klienta; agent może odczytać z niego nazwany klucz bez wykonywania pliku i ujawniania pozostałych wartości. Sprzeczne ustawienia tego samego poziomu wymagają wyjaśnienia.

`MONOLYNX_PROJECT_SLUG` działa też jako zwykła zmienna środowiskowa, więc na stałe możesz ją wpisać w `.claude/settings.json` projektu (pole `env`, przykłady w sekcji [Zmienne konfiguracyjne](#zmienne-konfiguracyjne)).

W Codex użyj projektowego `.codex/config.toml`:

```toml
[shell_environment_policy.set]
MONOLYNX_PROJECT_SLUG = "twoj-projekt"
```

Pozostałe `MONOLYNX_*` zapisuj w tej samej tabeli jako stringi. Po zmianie konfiguracji rozpocznij nową sesję i sprawdź wartość zmiennej w shellu klienta.

## Zawartość pluginu

### Kontrakt runtime i zakres zgodności

Każdy skill zaczyna od lokalnego `references/runtime.md`. Źródło wspólnej instrukcji to [kontrakt runtime](references/runtime.md); synchronizacja dostarcza tę samą treść do pluginu i pakietów MCP. Argumenty pochodzą z wywołania lub wiadomości użytkownika. Przykłady MCP wymagają mapowania na dostępne nazwy i schematy; brak `ToolSearch`, `Skill` lub narzędzia pytań ma opisany wariant bez tych API. Uprawnienia sesji i instrukcje użytkownika mają pierwszeństwo przed flagami `MONOLYNX_AUTO*`.

Poniższa macierz opisuje wymagania instrukcji, **nie potwierdza pełnego wykonania każdego workflow**. Wyniki i ograniczenia prób są w [raporcie weryfikacji runtime](runtime-verification.md). Claude Code używa `/monolynx:<nazwa>`, Codex wybiera `$monolynx:<nazwa>`; dla pojedynczej instalacji wybierz nazwę widoczną w katalogu klienta, np. `monolynx-work`.

| Skill | Claude Code | Codex | Dodatkowe wymagania / granica |
|---|---|---|---|
| `brief` | Wspólny kontrakt | Wspólny kontrakt | Bez MCP; rozpoczęcie pracy po odpowiedzi użytkownika |
| `help` | Mapa komend Claude | Mapa komend klienta | Bez zapisów; nie wymaga projektu |
| `setup` | Konfiguracja Claude jako dane | Konfiguracja Codex jako dane | MCP dla checklisty projektu; naprawy po zgodzie |
| `next` | Router tylko do odczytu | Router tylko do odczytu | Git, MCP, opcjonalne CLI; brak danych oznaczony |
| `resume` | Odczyt stanu | Odczyt stanu | MCP, opcjonalny git i pipeline |
| `search` | MCP wiki | MCP wiki | Narzędzie wyszukiwania wiki |
| `ticket-create` | MCP + pytania | MCP + pytania | Zapis do wskazanego projektu |
| `ticket-review` | MCP + odczyt źródeł | MCP + odczyt źródeł | Review nie zastępuje zgody na zmiany |
| `project-toolchain` | Odczyt repo + MCP | Odczyt repo + MCP | Zapisy komend potwierdzone w repo |
| `graph-sync` | Terminal + MCP | Terminal + MCP | Dostępny generator grafu i repo |
| `create-graph-ci-script` | Repo + MCP | Repo + MCP | Projektowy CI i zgoda na jego zmianę |
| `wiki-init` | MCP | MCP | Bootstrap wiki wskazanego projektu |
| `wiki-ingest` | MCP + źródło | MCP + źródło | Dostęp do źródła i włączona metoda LLM Wiki |
| `wiki-lint` | MCP | MCP | Naprawy oddzielone od audytu |
| `wiki-sync-merge` | Git + MCP | Git + MCP | Wiedza z faktycznie scalonych zmian |
| `work` | Dostępne role i delegowanie | Dostępne role i delegowanie | Repo, MCP, toolchain; brak agentów obsługiwany jawnie |
| `work-simple` | Dostępne role i delegowanie | Dostępne role i delegowanie | Repo, MCP, toolchain; próg 3 SP bez zmian |
| `retro` | MCP, opcjonalna pamięć Claude | MCP, dostępne źródła pamięci | Brak automatycznej migracji pamięci między klientami |
| `sprint-end` | MCP + skille zależne | MCP + skille zależne | Potwierdzenie zamknięcia sprintu i zapisów |
| `mr-queue` | Terminal, git, `gh`/`glab` | Terminal, git, `gh`/`glab` | Lokalny skrypt stanu; zgody na push/merge |
| `mutation-check` | Terminal z kontrolą procesu | Terminal z kontrolą procesu | Toolchain mutacji; deadline procesu i polling do zakończenia |
| `create-mutation-ci-script` | Repo + lokalne skrypty | Repo + lokalne skrypty | Narzędzie mutacyjne i projektowy CI |
| `sprint-run` | Backend `claude` | Dyspozytor wymaga CLI `claude` | Brak natywnego backendu sesji Codex; `/loop` tylko gdy dostępne |

Tabela modeli poniżej dotyczy backendu Claude. W pozostałych klientach wybór opiera się na faktycznie dostępnych rolach, modelach i parametrach; aliasów `sonnet`/`opus` nie należy przekazywać do API, które ich nie obsługuje. Sam brak parametru lub inna nazwa produktu nie jest dowodem braku agentów.

### Skille (komendy `/monolynx:*`)

| Komenda | Opis |
|---------|------|
| `/monolynx:next` | Router read-only bez argumentów: sprawdza git, bieżący ticket, aktywny sprint, sesje, MR i konfigurację. Zwraca 1-3 komendy z uzasadnieniem i linię `NEXT: <komenda>`. Nie uruchamia polecanych skilli, nie zmienia ticketów ani konfiguracji. |
| `/monolynx:setup` | Checklista konfiguracji projektu, bez argumentów: slug, strona wiki `toolchain`, metoda LLM Wiki, graf zależności i testy mutacyjne w CI, flagi `MONOLYNX_*`. Każdą pozycję pokazuje jako OK albo BRAK i za zgodą uruchamia skill naprawczy (`/monolynx:project-toolchain`, `/monolynx:wiki-init`, `/monolynx:create-graph-ci-script`, `/monolynx:create-mutation-ci-script`); projekt bez CI może zamiast grafu w CI sam uruchomić `/monolynx:graph-sync`. Sam niczego nie zmienia; flagi autonomii tylko raportuje, nie ustawia ich. Pierwszy krok w nowym projekcie. |
| `/monolynx:brief` | Kontrakt epizodu bez ticketu (konfiguracja, investigation, docs): w jednej turze ustala "gotowe" jako obserwowalny stan, "nie ruszać" i prawa decyzyjne (sam / stop przed). ENTER = akceptacja i start pracy w tej samej turze. Działa bez MCP. |
| `/monolynx:work` | Podejmij zadanie z aktualnego sprintu: walidacja brancha, wznowienie przerwanej pracy, tryb defektu (mechanizm + test regresyjny przed fixem), research, dobór zespołu agentów, kontrakt autonomii (STOP CONDITIONS w promptach), praca równoległa, rekoncyliacja raportów, obowiązkowy krytyk z regułami do pamięci agentów, pełny lint/test przed `in_review`. Raportuje przebieg pracy do modułu Pipelines (obserwowalność): pipeline `ticket_work` ze stepami research → coding → wrap-up, job per agent, log każdego agenta jako strona wiki. Raportowanie best-effort - nie blokuje pracy. Po zielonym lincie i testach, przed krytykiem, proponuje diffowy przebieg `/monolynx:mutation-check` dla większych ticketów (od 5 SP albo 4 zmienionych plików z kodem); survivorzy trafiają jako kryteria akceptacji do bieżącego ticketu. Propozycja, nie automat. |
| `/monolynx:resume` | Wznowienie po `/clear` lub kompakcji: odtwarza stan ticketu z komentarzy, pipeline, kryteriów i gita, wypisuje "gdzie jesteśmy, co zostało, następny krok". Read-only. |
| `/monolynx:retro` | Retro korekt sprintu: zbiera uwagi krytyka, NEEDS WORK, reguły i eskalacje z komentarzy ticketów, pamięci agentów oraz auto-memory Claude Code (korekty użytkownika z czatu, katalog wykrywany z `$HOME`/`CLAUDE_CONFIG_DIR` i `$PWD`), grupuje powtarzające się i proponuje maszynerię (reguła w `.claude/rules/`, wiersz rubryki, wpis w `constitution`). Opcjonalny job w `sprint-end`. |
| `/monolynx:work-simple` | Uproszczony flow dla mniejszych ticketów (max 3 SP): jeden dobrany dev + krytyk jako zwykłe subagenty (bez Agent Teams), research opt-in, pełna ceremonia self-reporting, eskalacja do `/monolynx:work` gdy scope się rozrasta. Raportuje do modułu Pipelines (uproszczona instrumentacja: dev + krytyk), best-effort. Diffowy przebieg mutacyjny proponuje wyłącznie po `SCOPE GREW`, na ścieżce "kontynuuj simple" - zwykły prosty ticket nie płaci za mutacje. |
| `/monolynx:ticket-create` | Utwórz nowy ticket: zbiera kontekst z wiki, kodu i grafu zależności, generuje opis w ustalonej formie (cel, kontekst, zakres, nie ruszać, odwracalność, zależności, kryteria akceptacji). Sekcje "Nie ruszać" i "Odwracalność" są granicami zadania, z których `/monolynx:work` buduje kontrakt autonomii. Ticket trafia do sprintu albo backlogu. |
| `/monolynx:ticket-review` | Zrecenzuj ticket ze sprintu: sprawdza formę (8 kryteriów, w tym granice "Nie ruszać" i "Odwracalność"), zgodność z wiki i kodem, generuje tabelkę raportu z oceną i proponuje brakujące sekcje z dowodami `plik:linia`. |
| `/monolynx:sprint-end` | Zamknij sprint jako pipeline `sprint_close` (stepy wiki-update → wrap-up): INGEST logów pracy do wiki, LINT (audyt wiki), czyszczenie stron logów pipeline sprintu, opcjonalne `retro` (przed zamknięciem, bo `complete_sprint` wypisuje niedokończone tickety ze sprintu) i realne `complete_sprint` (niedokończone tickety wracają do backlogu, z potwierdzeniem). Raportowanie do Pipelines best-effort. Pierwszym jobem stepu `wrap-up` jest `mutation-run`: pełny przebieg mutacyjny na modułach rdzenia, wiersze na stronie wiki `Trend mutacji` (data, moduł, score, baseline, delta) i zapadka jednego pola `baseline` w `toolchain` - tylko w górę. Krok jest nieblokujący: odmowa albo brak narzędzia nie wstrzymuje zamknięcia sprintu. Bez argumentu bierze aktywny sprint. |
| `/monolynx:sprint-run` | Dyspozytor sprintu: jeden idempotentny tick czyta stan sprintu i sesji w tle, startuje brakujące sesje `claude --bg --worktree <KEY> --name <slug>-<KEY>-<model>` z komendą `/monolynx:work <KEY>`, zbiera zakończone (komentarz z identyfikatorem sesji i `claude rm`), raportuje czekające na człowieka i kończy linią `SPRINT-RUN: remaining=... running=... waiting=... done=...`. Sam nigdy nie pracuje nad ticketem. Liczba slotów: argument, a bez niego `MONOLYNX_SPRINT_PARALLEL` albo 2. Pętla jest na zewnątrz: `/loop 15m /monolynx:sprint-run` albo `plugin/scripts/sprint_run.sh`. |
| `/monolynx:mr-queue` | Kolejka merge MR (GitLab, `glab`) i PR (GitHub, `gh`): jeden idempotentny tick bierze pierwszy niezmergowany MR/PR z `.git/monolynx/mr-queue.json` i robi jedno działanie w kolejności: czerwone CI -> poprawka w tymczasowym worktree i push (maks. 2 rundy), konflikt -> merge brancha docelowego i push, brak approve -> pytanie do użytkownika, gotowe -> merge z blokadą na SHA i czekanie na zielone CI brancha docelowego przed kolejnym elementem. Bez argumentów układa kolejkę z otwartych MR/PR (stack zaraz po swojej bazie, reszta od najstarszego, drafty pominięte) i daje ją do akceptacji; argumenty nadpisują kolejność. Checkout użytkownika zostaje nietknięty. Werdykty liczy `scripts/mr_queue_state.py`. Kończy linią `MR-QUEUE: remaining=... current=... verdict=... done=...`; pętla: `/loop 10m /monolynx:mr-queue`. |
| `/monolynx:search` | Szukaj informacji w wiki projektu (architektura, API, integracje, standardy kodu) przez wyszukiwanie semantyczne; przy włączonej metodzie LLM Wiki proponuje zapis odpowiedzi jako stronę syntezy. Zwykle nie trzeba wpisywać komendy: skill włącza się sam przy pytaniach o dokumentację projektu. |
| `/monolynx:wiki-init` | Włącz metodę LLM Wiki dla projektu: tworzy strony systemowe (regulamin `wiki-schema`, katalog `wiki-index`, dziennik `wiki-log`) i włącza flagę. Idempotentny bootstrap. Opcjonalny: potrzebny, gdy wiki ma być rosnącym artefaktem, a nie zbiorem luźnych stron. |
| `/monolynx:wiki-ingest` | Zintegruj nowe źródło (plik, URL, wklejona treść) z wiki: strona źródła, aktualizacja powiązanych stron encji/konceptów, wikilinki, odświeżenie katalogu i wpis do dziennika. Wymaga włączonej metody LLM Wiki. |
| `/monolynx:wiki-lint` | Audyt zdrowia wiki: wykrywa sieroty, martwe linki, sprzeczności i luki, prezentuje raport i proponuje naprawy. |
| `/monolynx:wiki-sync-merge` | Post-merge INGEST do wiki metodą LLM Wiki. Odpala człowiek po merge ticketów/PR do main - pobiera dane ticketów, integruje wiedzę z zamkniętych zadań, aktualizuje powiązane strony i odświeża katalog. |
| `/monolynx:help` | Mapa skilli w 6 grupach (Setup, Planowanie, Praca, Integracja, Sprint, Wiki), każdy skill w jednej linii z argumentami, plus dwie ścieżki pracy: ręczna (jeden ticket) i autopilot (`/loop` z `sprint-run` i `mr-queue`). Szczegóły odsyła do tego README. |
| `/monolynx:project-toolchain` | Jednorazowo wykryj komendy lint i testów projektu (Makefile, Python, Node, Rust, Go, PHP, Ruby, Java) i zapisz je jako stronę wiki `toolchain`, którą czytają `/monolynx:work` i `/monolynx:work-simple`, żeby uruchomić lint i testy przed zmianą statusu na `in_review` (bez tej strony skille pytają, czy kontynuować bez weryfikacji). Opcjonalnie wykrywa też narzędzie do testów mutacyjnych dla 9 ekosystemów (mutmut, Stryker, PIT, Stryker.NET, gremlins, Infection, cargo-mutants, Stryker4s, mutant) i zapisuje sekcję `## Mutacje` (komenda pełna i diffowa, moduły rdzenia, baseline). Narzędzi nie instaluje. Przy stacku Dockera zapisuje dodatkowo sekcję `## Worktree` z wariantem komend dla kopii repo, który `work` i `work-simple` wybierają automatycznie. |
| `/monolynx:mutation-check` | Przebieg testów mutacyjnych i survivorzy jako śledzona praca. Komendę i format raportu czyta z sekcji `## Mutacje` strony wiki `toolchain` (brak sekcji - odesłanie do `/monolynx:project-toolchain`, zero zgadywania i zero instalacji). Bez argumentu tryb diffowy względem `git merge-base` z gałęzią główną, raportujący liczbę survivorów, nie procent; argument ze ścieżką modułu daje pełny przebieg ze `score`, `baseline` i deltą. Parsowanie przez `plugin/scripts/mutation_report.py`. Po tabeli survivorów pyta, co z nimi zrobić: kryteria akceptacji przy wskazanym tickecie, ticket na każdego survivora, jeden ticket zbiorczy albo sam raport; treść opisuje brakujący przypadek testowy. Mutant równoważny semantycznie jest uzasadniany i odpuszczany, a asercje pinujące implementację tylko dla podniesienia score są zabronione. Twardy budżet czasu (`MONOLYNX_MUTATION_BUDGET`, domyślnie 600 s) kończy się raportem częściowym albo jawnym "brak raportu" z komendą do ręcznego dokończenia, nigdy wiszącym terminalem. Job `mutation-check` w stepie `wrap-up` bieżącego pipeline'u, best-effort (w trybie samodzielnym pipeline nie powstaje). Score nie jest bramką. |
| `/monolynx:create-graph-ci-script` | Skonfiguruj [graphify](https://github.com/Graphify-Labs/graphify) jako ekstraktor grafu zależności (36 języków, offline): `.graphifyignore`, `cicd/sync_graph.py` (`replace_graph`) i non-blocking step CI (GitLab/GitHub/Bitbucket/Jenkins). Graphify instaluje na runnerze właściciel projektu - step CI go tylko używa, a jego brak nie psuje builda. Opcjonalny argument: URL instancji Monolynx. |
| `/monolynx:graph-sync` | Lokalna synchronizacja grafu za rękę, komplement do wersji CI: wykrywa/pomaga zainstalować graphify (macOS/Linux/Windows), ekstrakcja offline (`graphify update .`) i push przez `cicd/sync_graph.py` (`replace_graph`). Do pierwszego zasilenia grafu albo odświeżenia ad hoc bez CI. |
| `/monolynx:create-mutation-ci-script` | Wygeneruj nieblokujący krok CI z testami mutacyjnymi (GitLab/GitHub/Bitbucket/Jenkins): komenda diffowa i format raportu ze strony wiki `toolchain`, kopie `cicd/mutation_report.py` i `cicd/mutation_baseline.py`, osobny etap `mutation` tylko na MR/PR, raport jako artefakt i zapadka baseline (`score / baseline / delta` plus liczba survivorów). Narzędzia mutacyjnego nie instaluje (instaluje je na runnerze właściciel projektu; brak narzędzia kończy krok komunikatem i sukcesem), spadek wyniku nie oblewa builda. Plik `cicd/mutation-baseline.json` commituje człowiek z artefaktu. Wymaga wcześniejszego `/monolynx:project-toolchain`. |

### Praca z ticketem

Typowy cykl: `/monolynx:ticket-create` -> `/monolynx:ticket-review` -> `/monolynx:work` albo `/monolynx:work-simple` -> merge -> `/monolynx:wiki-sync-merge`. Recenzję powtarzaj w cyklu *review -> poprawka -> review*, aż raport pokaże same "OK" w formie i "ZGODNE" w założeniach: dobrze zrecenzowany ticket to szybsza realizacja.

**Który skill do realizacji:**

| | `/monolynx:work` | `/monolynx:work-simple` |
|---|---|---|
| Story points | powyżej 3 SP | do 3 SP |
| Zespół | Researcher + N agentów + krytyk | 1 dev + krytyk |
| Research | obowiązkowy (agent Explore) | opt-in (wybierasz źródła) |
| Agent Teams | tak, gdy dostępne | nie, zwykłe subagenty |
| Typowe użycie | nowy moduł, cross-warstwowa zmiana | hotfix, cleanup, weryfikacja |
| Testy mutacyjne | propozycja przy >= 5 SP lub >= 4 plikach z kodem | tylko po eskalacji `SCOPE GREW` |

Oba mają pełną ceremonię: komentarze do ticketu, `log_time`, zmianę statusu, gate lint/test. `work-simple` eskaluje do `work`, jeśli dev wykryje, że zakres urósł (sygnał `SCOPE GREW`). Plik z kodem w progu mutacji to plik źródłowy poza testami i fixture'ami; poniżej progu kroku nie ma w ogóle, a odmowa zostaje odnotowana w podsumowaniu.

**Co robi `/monolynx:work`:**

1. Waliduje branch (twardy gate na `main`/`master`); ticket `in_progress` z planem -> proponuje **wznowienie** od ostatniego etapu.
2. Wykrywa **tryb defektu** (etykieta `bug`, tytuł "Fix/Napraw") - Researcher musi wtedy nazwać mechanizm (reprodukcja, hipoteza, dowód `plik:linia`), developer pisze test regresyjny PRZED fixem.
3. Uruchamia Researchera - ładuje spec-page ticketu, `constitution`, `toolchain`, przeszukuje kod, wiki i graf, przepisuje granice ticketu.
4. Dobiera zespół agentów, przydziela im **konkretne pliki** i ustala **kontrakt autonomii** (sam / stop przed / eskalacja) - jedno potwierdzenie na starcie zamiast przerywania w trakcie; pozycje "stop przed" idą do promptów jako STOP CONDITIONS.
5. Prowadzi pracę równoległą; raporty developerów przechodzą **rekoncyliację** (akceptuję / odrzucam / do wyjaśnienia), zanim pójdą do lintu.
6. Obowiązkowy krytyk ocenia według rubryki punktowej (próg 82); po NEEDS WORK reguła trafia do pamięci agenta, a re-run dostaje diff, nie opis.
7. Uruchamia lint i testy - **czerwone testy blokują** `in_review`; ostatni przebieg przed zamknięciem jest zawsze **pełny**.
8. Loguje czas każdego agenta osobno, zmienia status, wypisuje komendę commita i sygnał końca epizodu (`/clear` + `/monolynx:resume`).

Status, czas i logi przebiegu widać na żywo w zakładce "Pipelines" projektu. Dzięki `/monolynx:resume` `/clear` między ticketami nic nie kosztuje: kontekst nie żyje w wątku, tylko w Monolynx.

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

Codex wykrywa ten sam `hooks/hooks.json` i udostępnia `CLAUDE_PLUGIN_ROOT` jako alias zgodności. Hooki pluginu wymagają przeglądu i nadania zaufania przez użytkownika; sama instalacja ich nie aktywuje. Instalacja pojedynczego skilla przez MCP nie rejestruje hooków. Obsługa zdarzeń i kontekst subagentów zależą od klienta, więc odczyt manifestu nie stanowi dowodu egzekwowania wszystkich reguł guardów.

Plugin dostarcza hook PreToolUse na narzędziu Bash (`hooks/hooks.json` → `hooks/bash_guard.py`), który deterministycznie egzekwuje reguły workflow, dotąd zapisane wyłącznie w promptach skilli `work` / `work-simple`:

| Kto | Komenda | Decyzja |
|---|---|---|
| Subagent (dev, krytyk, ...) | `git add/commit/push/reset --hard/rebase` | **deny** - operacje zapisu w gicie należą do sesji głównej i użytkownika |
| Subagent | lint/testy (`pytest`, `ruff`, `eslint`, ...) | **deny** - lint/testy odpala Team Manager wg `MONOLYNX_AUTOTEST` |
| Sesja główna | `git push` | **ask** (potwierdzenie użytkownika), chyba że `MONOLYNX_AUTOPUSH=true` |
| Sesja główna | lint/testy | **ask**, chyba że `MONOLYNX_AUTOTEST=true` |
| Subagent | `glab mr create` | **deny** - "Subagent nie tworzy merge requestów - MR zakłada Team Manager (główna sesja) po pushu, wg MONOLYNX_AUTOMR. Zgłoś gotowość w raporcie końcowym." |
| Sesja główna | `glab mr create` | **ask** - "glab mr create wymaga potwierdzenia użytkownika (MONOLYNX_AUTOMR != true).", chyba że `MONOLYNX_AUTOMR=true` |
| Subagent | `glab mr merge` / `glab mr accept` / `gh pr merge` | **deny** - merge robi `/monolynx:mr-queue` w sesji głównej |
| Sesja główna | `glab mr merge` / `glab mr accept` / `gh pr merge` | **ask**, chyba że `MONOLYNX_AUTOMERGE=true` |
| Subagent | `glab mr approve` / `gh pr review --approve` | **deny** |
| Sesja główna | `glab mr approve` / `gh pr review --approve` | **ask** - zawsze, żadna flaga tego nie znosi (approve daje człowiek) |
| Subagent | operacje destrukcyjne (`rm -rf`, `git push --force`, `curl \| sh`, zapis do `~/.ssh`/`~/.aws`, `DROP/TRUNCATE`, `mkfs`/`dd of=/dev`) | **deny** |
| Sesja glowna i subagent | `rm -rf` z celem pod `/tmp`, katalogiem projektu (korzen repo, takze worktree) albo katalogiem `.claude` - nigdy sam korzen | **allow** (MON-174) |
| Sesja glowna i subagent | `rm -rf` z celem poza tymi katalogami | **deny** (MON-174) |
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
| `MONOLYNX_MR_TARGET` | - | Branch docelowy MR tworzonego przez `MONOLYNX_AUTOMR` w `work` i `work-simple`, np. `develop`, gdy tickety mergują się do brancha integracyjnego. Pusta -> domyślny branch repozytorium: `git symbolic-ref refs/remotes/origin/HEAD`, a gdy ref nie istnieje (klon bez ustawionego `origin/HEAD`, np. bez `git remote set-head origin -a`) - `glab repo view -F json --jq .default_branch`. Gdy nic nie da wyniku, MR nie powstaje (skill nie zgaduje `main`) |
| `MONOLYNX_SPRINT_RUN_MODE` | - | Tryb ticka dyspozytora. `batch` ustawia sam `sprint_run.sh`; skill `sprint-run` traktuje brak zmiennej (i każdą inną wartość) jako tryb interaktywny |
| `MONOLYNX_SPRINT_PERMISSION_MODE` | wartość z komendy startu sesji w skillu `sprint-run` | Tryb uprawnień sesji roboczych `--bg` startowanych przez dyspozytora (patrz KROK 4 tego skilla) |
| `MONOLYNX_SPRINT_MODEL` | `opus` | Model sesji roboczych `--bg` (`/monolynx:work <KEY>`) startowanych przez dyspozytora (patrz sekcja Modele) |
| `MONOLYNX_AUTOMERGE` | `false` | `true` - `/monolynx:mr-queue` merguje MR/PR bez pytania (tylko przy werdykcie `ready`, z blokadą na SHA); `false` - hook pyta o każdy merge |
| `MONOLYNX_MERGE_METHOD` | `merge` | Metoda merge w `/monolynx:mr-queue`: `merge`, `squash` albo `rebase` |
| `MONOLYNX_SPRINT_TICK_MODEL` | `opus` | Model procesu `claude -p`, w którym `sprint_run.sh` odpala tick dyspozytora oraz `--close` (`sprint-end`, `retro`) |
| `MONOLYNX_SPRINT_TICK_ALLOWED_TOOLS` | `Bash(claude:*),Bash(python3:*),Read(~/.claude/plugins/**),mcp__monolynx,mcp__claude_ai_Monolynx,mcp__plugin_monolynx_monolynx` | Lista narzędzi (po przecinkach), którą `sprint_run.sh` przekazuje tickowi w `claude -p --allowedTools`. Ustaw w środowisku procesu odpalającego pętlę; zastępuje całą listę domyślną (patrz sekcja „Uprawnienia ticku i preflight”) |
| `MONOLYNX_MUTATION_BUDGET` | `600` | Budżet czasu przebiegu `/monolynx:mutation-check` w sekundach. Maksimum 600 (limit narzędzia Bash); większa wartość jest skracana do 600 |
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

## Modele

Proces bez jawnego modelu bierze domyślny model konta, a subagent bez modelu dziedziczy model sesji, która go powołała. Oba domyślne zachowania prowadzą do tego samego: cały sprint idzie na najdroższym modelu. Plugin ustawia więc model jawnie dla każdej roli. Zasada: droższy model tylko tam, gdzie zapadają decyzje, i tylko po świadomym włączeniu; wykonanie na Opusie albo Sonnecie.

| Rola | Model domyślny | Jak zmienić |
|---|---|---|
| Tick dyspozytora w `sprint_run.sh` (także `--close`) | `opus` | `MONOLYNX_SPRINT_TICK_MODEL` |
| Tick dyspozytora w `/loop 15m /monolynx:sprint-run` | model Twojej sesji interaktywnej | `/model` w tej sesji |
| Sesja robocza `--bg` (Team Manager ticketu) | `opus` | `MONOLYNX_SPRINT_MODEL`, np. `fable` dla koordynatora |
| Sesja `/monolynx:work` odpalona ręcznie | model Twojej sesji | `/model` albo `claude --model` |
| Researcher (`Explore`) w `work` | `sonnet` | treść skilla `work` (KROK 3b) |
| Developer i agent testowy | `model:` z frontmatter agenta; brak pola -> `sonnet` | frontmatter agenta w `.claude/agents/` albo `plugin/agents/` |
| Krytyk | `opus` | treść skilla `work` / `work-simple` (sekcja „Model subagentów”) |

Zmienne `MONOLYNX_SPRINT_*MODEL` ustaw w środowisku procesu odpalającego pętlę albo w śledzonym `.claude/settings.json`: sesja w tle dziedziczy środowisko, a worktree nie widzi `.claude/settings.local.json`. Wartość to alias (`opus`, `sonnet`, `fable`) albo pełna nazwa modelu, tak jak przyjmuje ją `claude --model`.

## Sprint bez sesji interaktywnej

Sprint da się przerobić bez człowieka siedzącego przy terminalu. Robią to dwa elementy, które można używać osobno.

**Dyspozytor `/monolynx:sprint-run [sloty]`** to jeden tick: czyta aktywny sprint i stan sesji w tle, startuje brakujące sesje `/monolynx:work` (każdą w osobnym worktree, pod nazwą `<slug>-<KEY>-<model>`, np. `monolynx-MON-12-opus`), zbiera zakończone, raportuje te, które czekają na człowieka, i kończy ostatnią linią:

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

Skrypt uruchamiasz z katalogu projektu. Ten zewnętrzny backend czyta slug ze środowiska lub konfiguracji Claude; nie interpretuje konfiguracji Codex ani całego kontraktu agenta. Agent przed uruchomieniem przekazuje rozstrzygnięty slug w środowisku procesu. Absolutną ścieżkę lokalnego `scripts/sprint_run.sh` ustala względem otrzymanego `SKILL.md` dyspozytora i wypisuje w raporcie. Po instalacji może to być katalog cache pluginu albo pakiet pojedynczego skilla. Po aktualizacji ustal ścieżkę ponownie.

### Uprawnienia ticku i preflight

Tick w `claude -p` nie ma komu zadać pytania o zgodę, więc `sprint_run.sh` przekazuje mu uprawnienia sam, niezależnie od `permissions` w settings projektu:

- `--allowedTools` z listą `Bash(claude:*),Bash(python3:*),Read(~/.claude/plugins/**),mcp__monolynx,mcp__claude_ai_Monolynx,mcp__plugin_monolynx_monolynx`. Wpis `mcp__<serwer>` zezwala na wszystkie narzędzia serwera; są trzy prefiksy, bo nazwa serwera zależy od sposobu instalacji (plugin, connector claude.ai, `.mcp.json`). `Read` jest zawężony do `~/.claude/plugins/**`, bo gołe `Read` dawałoby tickowi odczyt całego dysku, w tym `~/.ssh`; odczyt w katalogu projektu Claude Code pozwala domyślnie. Całą listę zastępuje `MONOLYNX_SPRINT_TICK_ALLOWED_TOOLS` ze środowiska procesu; linia `start:` w logu pętli podaje tylko `allowed_tools=default` albo `allowed_tools=custom`.
- `--permission-prompts none`: wszystko, co wymagałoby potwierdzenia, jest odrzucane od razu i widać to w logu, zamiast wisieć bez odpowiedzi.

Stąd wymóg, żeby tick nie wołał gołego shella. Cała logika ticku, która wcześniej była inline bashem w skillu, siedzi w jednym skrypcie `sprint_run_tick.py` (katalog `scripts/` skilla `sprint-run`), wołanym przez `python3`. Komendy, które skrypt uruchamia przez `subprocess`, nie przechodzą przez uprawnienia Claude Code.

`sprint_run_tick.py preflight` to pierwszy krok każdego ticku. Zbiera wszystkie problemy naraz, zamiast przerywać na pierwszym:

1. katalog projektu jest repozytorium git;
2. `claude --version` i `claude agents --json --all` odpowiadają (drugie zwraca listę JSON);
3. `.claude/settings.json` i `.claude/settings.local.json`, o ile istnieją, są poprawnym obiektem JSON, `permissions` jest obiektem z listami napisów w `allow` / `deny` / `ask`, a `env` jest obiektem. Tryb `-p` po cichu pomija plik, który nie przechodzi walidacji (`claude --help`, opis `-p`), a razem z nim jego `env`, hooki i model; preflight zamienia tę ciszę na błąd z nazwą pliku i powodem (przy błędzie składni z linią i kolumną);
4. `MONOLYNX_PROJECT_SLUG` jest niepusty, a `MONOLYNX_AUTOTEST`, `MONOLYNX_AUTOCOMMIT` i `MONOLYNX_AUTOPUSH` są równe `true` w **środowisku procesu**, nie w pliku. Flaga obecna w `env` pliku settings, której proces nie widzi, dostaje dopisek, że plik mógł zostać odrzucony. `MONOLYNX_AUTOMR` różne od `true` daje tylko ostrzeżenie.

Wynik to linie `KLUCZ=wartość` (slug, tryb, sloty, model, tryb uprawnień sesji, flagi AUTO*, wersja `claude`, katalog projektu i katalog ticku), potem `WARN:` i `ERROR:`, na końcu `PREFLIGHT: ok` albo `PREFLIGHT: failed problems=<n>`. Skrypt nie wypisuje żadnej innej zmiennej środowiska ani całego `env` z plików settings. Przy `failed` tick nie startuje żadnej sesji; w trybie `batch` kończy się linią `SPRINT-RUN: remaining=-1 running=0 waiting=0 done=0`.

Pliki stanu ticku (lista ticketów, `claude agents --json --all`, ogony logów sesji) lądują w `.git/monolynx/tick/`, obok logu pętli, a nie w `/tmp`: katalog tymczasowy jest poza zapisem ticku, a `.git/` nigdy nie trafia do commita. Każdy tick nadpisuje te pliki.

> **Nie wypisuj środowiska.** Ani w ticku, ani przy diagnozie: `printenv`, `env`, `set`, `export -p` i `declare -x` wypisują wszystkie zmienne procesu, w tym klucze API i tokeny, do transkryptu i do logu pętli. Konfigurację sprawdzaj przez `sprint_run_tick.py preflight`, który pokazuje wyłącznie zmienne `MONOLYNX_*` potrzebne tickowi.

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

`AUTOTEST`, `AUTOCOMMIT` i `AUTOPUSH` są wymagane - bez nich preflight dyspozytora zatrzymuje tick i nie startuje żadnej sesji. `AUTOMR` jest zalecane: bez niego branch jest wypchnięty, ale merge request tworzysz ręcznie. Projekt, który merguje tickety do innego brancha niż domyślny branch repozytorium (np. `develop`), ustawia też `MONOLYNX_MR_TARGET`. Skrypt `sprint_run.sh` ustawia te flagi sam dla procesów, które uruchamia; każdą można nadpisać w środowisku. Poza nimi eksportuje też `MONOLYNX_SPRINT_RUN_MODE=batch` (tick wie, że jego raportu nikt nie przeczyta) i `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` (domyślnie `1`).

Plik z flagami musi być **śledzony przez gita**. Sesja robocza dostaje worktree utworzony przez `claude --bg --worktree`, a w worktree trafia tylko to, co git zna: `.claude/settings.local.json` jest ignorowany, więc ani uprawnienia, ani flagi AUTO* z niego nie docierają do sesji w tle. Druga poprawna droga to środowisko procesu, który odpala tick - `sprint_run.sh` eksportuje flagi sam, a `claude --bg` dziedziczy środowisko.

Tick w pętli `sprint_run.sh` dostaje uprawnienia z `--allowedTools`, więc blok `permissions` nie jest mu potrzebny. Tick interaktywny (`/loop 15m /monolynx:sprint-run` albo ręczne `/monolynx:sprint-run`) korzysta z uprawnień sesji; żeby nie pytał przy każdym ticku, dopisz do śledzonego `.claude/settings.json` poprawny blok `permissions`:

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
  },
  "permissions": {
    "allow": [
      "Bash(claude:*)",
      "Bash(python3:*)",
      "Read(~/.claude/plugins/**)",
      "mcp__monolynx",
      "mcp__claude_ai_Monolynx"
    ]
  }
}
```

- `permissions` to obiekt z listą `allow`, nie sama lista. Plik z inną strukturą Claude Code pomija w trybie `-p` bez komunikatu, razem z `env`, hookami i modelem; `sprint_run_tick.py preflight` zgłasza taki plik jako `ERROR:`.
- `Read(~/.claude/plugins/**)` obejmuje skrypty pluginu bez ścieżki absolutnej z nazwą konta i bez numeru wersji pluginu, więc reguła przeżywa aktualizację i działa u każdego w zespole.
- Reguły osobiste (ścieżki na Twoim dysku, dodatkowe narzędzia) wpisuj do `.claude/settings.local.json`, nie do śledzonego pliku.

### Uruchomienie w tmux i z crona

Poproś dyspozytora o absolutną ścieżkę dołączonego `scripts/sprint_run.sh`. Nie używaj w terminalu zmiennych, których klient nie przekazał do Twojego shella. Poniższe szablony wymagają podstawienia otrzymanej ścieżki oraz katalogu projektu:

```bash
tmux new -d -s monolynx-sprint "bash '/pelna/sciezka/skilla/scripts/sprint_run.sh' --slots 2"
```

```
*/15 * * * * cd /sciezka/projektu && bash "/pelna/sciezka/skilla/scripts/sprint_run.sh" --once >> ~/sprint-run.log 2>&1
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

Claude Code i Codex obsługują pełny plugin opisany w sekcji instalacji. Instalator MCP jest osobną ścieżką dla pojedynczych skilli, również dla Cursora. Zwraca `SKILL.md` i wszystkie lokalne zasoby; parametr `target` wybiera katalog:

| `target` | Katalog | Runtime |
|---|---|---|
| `claude` (domyślnie) | `.claude/skills/` | Claude Code |
| `codex` | `.agents/skills/` | Codex CLI |
| `cursor` | `.cursor/skills/` | Cursor |

```
install_monolynx_skills(project_slug="moj-projekt", target="codex")
install_monolynx_skills(project_slug="moj-projekt", skill_names=["monolynx-work"], target="codex")
```

Narzędzie zwraca `skills: [{name, files: [{relative_path, content}]}]`. **Zapisz wszystkie pliki z listy `files`** - skill może mieć pliki towarzyszące obok `SKILL.md` (np. `monolynx-work` ma `pipeline.md` i `review-rubric.md`); pominięcie ich okroi skill o część instrukcji.

Serwer MCP podłącza się poza skillami:

- **Codex**: konfiguracja MCP i logowanie OAuth opisane w sekcji instalacji; token API pozostaje opcją dla konfiguracji ręcznej.
- **Cursor**: `.cursor/mcp.json` w projekcie lub `~/.cursor/mcp.json` globalnie, klucz `mcpServers` - ten sam kształt co `.mcp.json` pluginu.

Definicje `plugin/agents/` dotyczą Claude Code. W każdym kliencie skille sprawdzają faktycznie dostępne mechanizmy delegowania i role projektu. Sam napis „Codex” nie oznacza braku subagentów ani dostępności określonego modelu. Przeniesienie konfiguracji agentów, pamięci i hooków nie jest częścią kontraktu runtime.

### Serwer MCP bez zmian funkcjonalnych

Plugin nie modyfikuje serwera ani nie odwołuje się do jego wewnętrznych funkcji. Deklaruje wyłącznie dostęp (HTTP + OAuth) do **istniejącego** serwera MCP pod adresem `https://monolynx.com/mcp`. Cała logika narzędzi (Scrum, 500ki, Monitoring, Wiki, Połączenia, Plan pracy, w tym samo `install_monolynx_skills`) działa po stronie serwera, niezależnie od tego, czy łączysz się przez plugin, czy w inny sposób.

## Changelog

### 1.17.0

- MON-196: `mr-queue` domyka ticket przy zielonym CI brancha docelowego. Nowa podkomenda `mr_queue_state.py ticket-key --source-branch B --title T` wyznacza klucz ticketu (`[A-Z]+-[0-9]+`, branch źródłowy ma pierwszeństwo przed tytułem MR/PR). KROK 3a skilla `mr-queue`, gałąź `target-ci` = `success`: rozpoznany klucz przełącza ticket na `done` (`update_ticket` + `add_comment` z merge SHA i id pipeline'u brancha docelowego), z read-before-write, żeby nie dublować komentarza przy ticketach już `done`; brak klucza zostawia ticket nietknięty i jest odnotowany w raporcie ticka. Guard prefiksu projektu: `get_ticket` porównuje kod projektu w odpowiedzi z `<key>` - rozjazd (klucz z innego repo/projektu) traktowany jest jak brak klucza, bo `_resolve_ticket_uuid` dopasowuje ticket tylko po numerze i inaczej cicho przestawiłby cudzy, tak samo ponumerowany ticket. To wciąż jedno działanie na tick - `queue-update --status done` domyka pozycję kolejki dopiero po ticketcie.

### 1.16.1

- MON-192: `MONOLYNX_AUTOMR` w `work` i `work-simple` nie tworzy już MR na sztywno do `main`. Branch docelowy pochodzi z nowej zmiennej `MONOLYNX_MR_TARGET` (np. `develop`), a bez niej z domyślnego brancha repozytorium (`git symbolic-ref refs/remotes/origin/HEAD`, potem `glab repo view`); gdy nie da się go ustalić, MR nie powstaje, a skill wypisuje gotową komendę.

### 1.16.0

- MON-190: tick `sprint-run` w `claude -p` dostaje uprawnienia od `sprint_run.sh`: `--allowedTools` z listą domyślną (`Bash(claude:*)`, `Bash(python3:*)`, `Read(~/.claude/plugins/**)`, trzy prefiksy serwera MCP Monolynx; `Read` zawężony do skryptów pluginu, bo gołe `Read` otwierało cały dysk łącznie z `~/.ssh`), nadpisywaną przez nową zmienną `MONOLYNX_SPRINT_TICK_ALLOWED_TOOLS`, oraz `--permission-prompts none`, więc odmowa jest natychmiastowa i widoczna w logu. Incydent 18.09: tick w `--permission-mode acceptEdits` miał odrzucane komendy Bash i nie wystartował żadnej sesji.
- **Nowy skrypt `sprint_run_tick.py`** zastępuje inline bash w skillu: `preflight` sprawdza git, `claude --version` i `claude agents --json --all`, poprawność JSON i struktury `permissions` / `env` w `.claude/settings.json` i `.claude/settings.local.json` (tryb `-p` po cichu pomija niepoprawny plik razem z `env`, hookami i modelem) oraz flagi AUTO* w środowisku procesu; `state` zapisuje listę ticketów, sesje i ogony logów w `.git/monolynx/tick/` zamiast `mktemp -d` w `/tmp` i zwraca JSON werdyktów z `sprint_run_state.py`.
- **Zakaz `printenv`, `env`, `set`, `export -p` i `declare -x` w ticku**: 18.09 `printenv` wypisał sekrety do logu pętli. Preflight pokazuje wyłącznie zmienne `MONOLYNX_*` potrzebne tickowi.
- **Start sesji odrzucony przez uprawnienia** (np. klasyfikator trybu auto przy `claude --bg --permission-mode bypassPermissions`): dyspozytor nie ponawia i nie szuka obejścia; w trybie interaktywnym wypisuje gotową komendę startu do wklejenia i poleca pętlę `sprint_run.sh`, w trybie `batch` odnotowuje to w raporcie.
- README: sekcja „Uprawnienia ticku i preflight” oraz przykład poprawnego bloku `"permissions": {"allow": [...]}` dla ticku interaktywnego, bez ścieżek z nazwą konta i wersją pluginu.

### 1.15.1

- MON-183: wspólny kontrakt argumentów, projektu, narzędzi i uprawnień w 23 skillach; lokalne referencje runtime w dystrybucji standalone, warianty klientów i rozdzielenie budżetu procesu od oczekiwania terminala. Walidacja referencji oraz macierz możliwości z zakresem weryfikacji.

### 1.15.0

- MON-182: wspólne źródło skilli dla Claude Code i Codex, natywny manifest Codex, instalacja standalone w `.agents/skills` oraz rekurencyjne pakowanie skryptów i lokalnych referencji. Zachowano targety Claude/Cursor i konfigurację użytkownika.

### 1.14.0

- **Nowy skill `/monolynx:next` (MON-181)**: router read-only stanu projektu (git, ticket, sprint, sesje, MR i konfiguracja), 1-3 uzasadnione komendy oraz `NEXT: <komenda>`. Pierwsza komenda do zapamiętania w `help`; pakiet liczy teraz 23 skille.

Wejście do pluginu w jednym miejscu: checklista konfiguracji projektu i `help` skrócony do mapy skilli.

- **Nowy skill `/monolynx:setup` (MON-179)**: checklista konfiguracji projektu bez argumentów - slug, strona wiki `toolchain`, metoda LLM Wiki, graf zależności i testy mutacyjne w CI, flagi `MONOLYNX_*`. Każdą pozycję pokazuje jako OK albo BRAK i za zgodą uruchamia skill naprawczy (`project-toolchain`, `wiki-init`, `create-graph-ci-script`, `create-mutation-ci-script`); projekt bez CI może zamiast grafu w CI sam uruchomić `graph-sync`. Sam jest read-only; flagi autonomii tylko raportuje, nigdy ich nie ustawia.
- **`/monolynx:help` jako mapa**: 6 grup (Setup, Planowanie, Praca, Integracja, Sprint, Wiki), każdy skill w jednej linii z argumentami przepisanymi z `argument-hint`, dwie ścieżki pracy (ręczna dla jednego ticketu i autopilot `/loop 15m /monolynx:sprint-run` + `/loop 10m /monolynx:mr-queue`). Pierwsza linia mapy odsyła nowy projekt do `/monolynx:setup`. Plik ma najwyżej 100 linii zamiast 392.
- **Treść, która wyleciała z `help`, jest w tym README**: nowa podsekcja "Praca z ticketem" (cykl review -> poprawka -> review, tabela `work` vs `work-simple`, osiem kroków `/monolynx:work`), wiersz `MONOLYNX_MUTATION_BUDGET` w tabeli zmiennych, zapis sluga w `.claude/settings.json` oraz uzupełnione wiersze tabeli skilli (m.in. pytanie o kontynuację bez strony `toolchain`, lista narzędzi mutacyjnych, `graphify update .`, wymóg LLM Wiki w `wiki-ingest`, samoczynne włączanie `search`, kolejność kolejki w `mr-queue`).
- **Liczniki skilli wyrównane do 22** w README, `agent-bootstrap.md`, `how-to-use-monolynx.md`, `mcp_guide.html` i `main.py` (wcześniej README i `agent-bootstrap.md` podawały 20). Test `tests/unit/test_skill_counters.py` porównuje każdy licznik z liczbą katalogów `plugin/skills/*/SKILL.md`.

### 1.13.0

Jawny model dla każdej roli sprintu (bez ticketu, epizod `brief`). Sprint odpalony przez `sprint_run.sh` szedł na domyślnym modelu konta, którym był najdroższy Fable, a subagenci dziedziczyli go po sesji koordynatora.

- **`sprint_run.sh`: `claude -p --model`.** Tick dyspozytora i przebiegi `--close` idą na `MONOLYNX_SPRINT_TICK_MODEL` (domyślnie `opus`). Skrypt eksportuje też `MONOLYNX_SPRINT_MODEL` (domyślnie `opus`) i loguje oba modele w linii `start:`.
- **`/monolynx:sprint-run`: `--model "${MONOLYNX_SPRINT_MODEL:-opus}"` w komendzie startu sesji `--bg`.** Fable dla koordynatora ticketu włącza się wyłącznie przez `MONOLYNX_SPRINT_MODEL=fable`. Linia `SPRINT-RUN:` bez zmian.
- **Nazwa sesji roboczej niesie model: `<slug>-<KEY>-<model>`** (np. `monolynx-MON-12-opus`), więc w `claude agents` od razu widać, na czym pracuje ticket. Tak samo sesje procesów `sprint_run.sh`: `<slug>-sprint-run-<model>`, a przy `--close` `<slug>-sprint-end-<model>` i `<slug>-retro-<model>` (model z `MONOLYNX_SPRINT_TICK_MODEL`). Każda nazwa sesji kończy się modelem. `sprint_run_state.py` rozpoznaje sesję ticketu po nazwie `<slug>-<KEY>` z dowolnym sufiksem modelu albo bez niego: zmiana `MONOLYNX_SPRINT_MODEL` między tickami i sesje nazwane przed tą zmianą nie powodują startu drugiej sesji na tym samym tickecie.
- **`/monolynx:work` i `/monolynx:work-simple`: sekcja „Model subagentów”.** Każde powołanie subagenta (także re-run i nowy krytyk po śmierci poprzedniego) ma jawny model: Researcher `sonnet`, developer według `model:` z frontmatter agenta (brak pola -> `sonnet`), krytyk `opus`. Subagent nie dziedziczy już modelu sesji Team Managera.
- **README: sekcja „Modele”** z tabelą rola -> model domyślny -> sposób zmiany.
- **Nowy skill `/monolynx:mr-queue`: kolejka merge MR (`glab`) i PR (`gh`).** Jeden idempotentny tick na pierwszym niezmergowanym elemencie kolejki, jedno działanie w kolejności CI -> konflikt -> approve -> merge. Czerwone CI i konflikt poprawia w tymczasowym worktree (`git worktree add --detach`, push `HEAD:refs/heads/<branch>`, bez force i rebase; maks. 2 rundy, potem stop), o approve pyta użytkownika (approve z czatu wiąże się z SHA), po merge czeka na zielone CI brancha docelowego. Bez argumentów układa kolejkę z otwartych MR/PR; argumenty `!iid`, `#pr` albo URL nadpisują kolejność. W trybie batch pytania zamieniają się w `MR-QUEUE STOP: <ref> <powód>`.
- **`scripts/mr_queue_state.py`**: `queue-propose`, `queue-set`, `queue-show`, `queue-update`, `classify` (werdykty `merged`, `closed`, `wait`, `ci_failed`, `conflict`, `needs_approval`, `needs_human`, `ready`) i `target-ci`. Literały stanów z fixture realnych wyjść `glab 1.100.0` i `gh 2.100.0` (`tests/fixtures/glab/`, `tests/fixtures/gh/`); nieznany literał daje `wait`, nigdy `ready`. GitLab `approved=true` przy `approvals_required=0` bez `approved_by` nie jest approve; pipeline `manual` na branchu docelowym jest zielony, gdy żaden automatyczny job nie padł.
- **Hook `bash_guard.py`**: `glab mr merge|accept` i `gh pr merge` - deny dla subagenta, ask dla sesji głównej, chyba że `MONOLYNX_AUTOMERGE=true`; `glab mr approve` i `gh pr review --approve` - deny dla subagenta, zawsze ask dla sesji głównej. Nowe zmienne: `MONOLYNX_AUTOMERGE`, `MONOLYNX_MERGE_METHOD`.

### 1.12.0

Wpięcie mutacji w istniejące skille (MON-152) - trzeci krok serii po parserze (1.9.0) i skillu `/monolynx:mutation-check` (1.10.0). Mutacja kosztuje tylko tam, gdzie się zwraca, więc nigdzie nie jest odpalana domyślnie ani nie bramkuje pracy.

- **`/monolynx:work`: propozycja po zielonym lincie i testach, przed krytykiem.** Wyzwalacz to rozmiar zmiany: ticket od 5 punktów historii albo co najmniej 4 zmienione pliki z kodem. Przebieg diffowy względem punktu rozejścia z gałęzią główną, survivorzy lądują jako kryteria akceptacji bieżącego ticketu. Brak sekcji `## Mutacje` na stronie `toolchain` albo `narzedzie: brak` kończy się jedną linią w podsumowaniu, nie błędem. W sesji w tle (`MONOLYNX_SPRINT_RUN=1`) krok jest pomijany po cichu, bez `SPRINT-RUN STOP` - nikt nie odpowie na pytanie.
- **`/monolynx:work-simple`: wyłącznie po `SCOPE GREW`.** Prosty ticket nie płaci za przebieg mutacyjny; propozycja pojawia się dopiero na ścieżce "kontynuuj simple", gdy zakres okazał się większy, niż zakładał wybór flow.
- **`/monolynx:sprint-end`: job `mutation-run` jako pierwszy w stepie `wrap-up`.** Idzie przed `retro` i przed nieodwracalnym `close-sprint`, żeby `summary` znało wynik. Pełny przebieg per moduł rdzenia (sekwencyjnie, błąd jednego modułu nie przerywa pozostałych), agregat `razem` liczony jako suma zabitych mutantów do sumy wszystkich.
- **Strona wiki `Trend mutacji`** powstaje przy pierwszym przebiegu obok strony "Strategia testów i pokrycie" i dostaje wiersz per moduł plus wiersz `razem`: data, moduł, score, baseline, delta. Historia jest dopisywana na końcu tabeli, nigdy przepisywana.
- **Jedno pole `baseline` w `toolchain`, zapadka tylko w górę.** Baseline trzyma agregat modułów rdzenia, nie wynik pojedynczego modułu. Pierwszy przebieg zapisuje faktyczny wynik, kolejne raportują score, baseline i deltę; wynik niższy albo równy nie nadpisuje niczego. Brak globalnego progu procentowego - score jest trendem, nie bramką merge'a ani sprintu.
- **Krok jest pomijalny w każdym z trzech skilli**: odmowa użytkownika, brak narzędzia, przekroczony budżet (`MONOLYNX_MUTATION_BUDGET`, domyślnie 600 s) albo błąd narzędzia kończą się statusem `skipped` lub `failed` i jedną linią w podsumowaniu. Zamknięcie sprintu, zamknięcie ticketu i praca krytyka idą dalej bez zmian.
- **Survivor to brakujący przypadek testowy, nie "mutant do zabicia"** - wpięte skille dziedziczą blok antygamingowy z `/monolynx:mutation-check`. Dla przebiegu sprintowego rekomendowany zapis to jeden zbiorczy ticket per moduł do backlogu.
- **Seria domknięta**: krok CI z zapadką baseline to `/monolynx:create-mutation-ci-script` (MON-151, 1.11.0).

### 1.11.0

Testy mutacyjne jako nieblokujący sygnał w CI (MON-151).

- **Nowy skill `/monolynx:create-mutation-ci-script`**: wykrywa system CI (GitLab, GitHub Actions, Bitbucket Pipelines, Jenkins) i generuje osobny, nieblokujący etap `mutation` uruchamiany tylko na MR/PR. Komenda diffowa, ścieżka i format raportu pochodzą z sekcji `## Mutacje` strony wiki `toolchain` - skill nie wpisuje komend z pamięci i tej strony nie zmienia. Brak strony albo `komenda diffowa: brak` kończy skill odesłaniem do `/monolynx:project-toolchain`.
- **Krok CI nie instaluje narzędzia mutacyjnego** (precedens: graphify): guard obecności dobierany per ekosystem (`command -v` nie znajdzie pluginu Mavena, `dotnet tool` ani podkomendy `cargo`), a jego brak kończy krok czytelnym komunikatem i sukcesem. Raport trafia do artefaktów zawsze.
- **Zapadka baseline `plugin/scripts/mutation_baseline.py`** (kopiowana do `cicd/`): pierwszy przebieg zapisuje wynik, kolejne raportują liczbę survivorów oraz `score / baseline / delta`. Baseline może tylko rosnąć, plik `cicd/mutation-baseline.json` commituje człowiek z artefaktu - CI nie pushuje do repozytorium. Spadek wyniku to widoczna `UWAGA` w logu, nigdy czerwony build: mutation score jest trendem, nie bramką, a bramka na nim kończy się wyłączeniem checka.
- **Statusy zapadki**: `first`, `up`, `same`, `down`, `no-mutants` (diff bez mutantów) i `scope-mismatch` (baseline z pełnego przebiegu przy pomiarze diffowym - delta orientacyjna, bez zapisu). Każdy status kończy się kodem wyjścia `0`; ostatnia linia `MUTATION-BASELINE: ...` jest zamrożonym kontraktem do odczytu maszynowego.

### 1.10.0

Skill `/monolynx:mutation-check` (MON-150) - drugi krok serii po parserze z 1.9.0.

- **Nowy skill `/monolynx:mutation-check [ścieżka modułu]`**: uruchamia przebieg mutacyjny, parsuje raport przez `plugin/scripts/mutation_report.py` i pokazuje tabelę przeżywających mutantów (plik:linia, mutator, oryginał -> mutant). Puste pola raportu (PIT bez `original`/`replacement`, mutmut bez `line`) pokazywane są jako `description` albo `?`, nigdy jako literalne `None`.
- **Komenda pochodzi wyłącznie z wiki**: skill czyta sekcję `## Mutacje` strony `toolchain` deterministycznie (`list_wiki_pages` + slug, nie `search_wiki`). Brak strony albo sekcji kończy się instrukcją uruchomienia `/monolynx:project-toolchain`; `narzedzie: brak` pokazuje powód i też kończy skill. Zero zgadywania komendy, zero instalacji narzędzia, zero poprawiania cudzych flag.
- **Tryb diffowy jako domyślny**: bez argumentu przebieg ograniczony do zmiany względem `git merge-base` z gałęzią główną, raportowany **liczbą survivorów, nie procentem** - przy kilku mutantach procent skacze o dziesiątki punktów i nie niesie informacji. Argument ze ścieżką modułu daje pełny przebieg ze `score`, `baseline` i deltą; `komenda diffowa: brak` nie przechodzi cicho na pełny przebieg, tylko proponuje tryb modułowy.
- **Survivorzy jako śledzona praca**: kryteria akceptacji przy wskazanym tickecie (`add_acceptance_criterion`), ticket na survivora albo jeden zbiorczy z listą w `acceptance_criteria`. Treść opisuje brakujący przypadek testowy wyprowadzony z kodu wokół linii mutanta.
- **Blok anty-gamingowy**: survivor to brakujący przypadek testowy, mutant równoważny semantycznie jest uzasadniany i odpuszczany (poprawna odpowiedź, nie porażka), asercje pinujące szczegóły implementacji dla podniesienia score są zabronione, a słowa "zabij mutanta" nie wolno użyć w generowanych treściach. Mutation score nie jest bramką merge'a ani ticketu.
- **Budżet czasu zamiast wiszącego terminala**: `MONOLYNX_MUTATION_BUDGET` (sekundy, domyślnie i maksymalnie 600, egzekwowane parametrem `timeout` narzędzia Bash). Po przekroczeniu: raport częściowy, jeśli plik raportu istnieje, inaczej jawne "brak raportu" z komendą do ręcznego dokończenia.
- **Instrumentacja Pipelines best-effort**: job `mutation-check` w stepie `wrap-up` bieżącego pipeline'u, gdy skill działa w kontekście pracy nad ticketem; w trybie samodzielnym pipeline nie powstaje. Survivorzy nie ustawiają statusu `failed` - `failed` zostaje dla awarii przebiegu. Błąd toola pipeline nigdy nie przerywa skilla.
- **Dalej w serii**: krok CI (MON-151) i wpięcie w `work` oraz `sprint-end` (MON-152).

### 1.9.0

Parser raportów mutacyjnych do wspólnego modelu survivora (MON-149) - fundament serii MON-150..152.

- **Nowy skrypt `plugin/scripts/mutation_report.py`**: czyta raport narzędzia mutacyjnego i wypisuje na stdout znormalizowany JSON `{"score", "total", "killed", "survived", "survivors": [{file, line, mutator, original, replacement, status, description}]}`. Cztery formaty: `mte-json` (Mutation Testing Elements - Stryker, Stryker.NET, Infection), `pit-xml`, `mutmut` (wyjście `mutmut results --all true`), `cargo-mutants` (`outcomes.json`). Wywołanie: `python3 mutation_report.py --report <plik> --format <format>`. Wyłącznie biblioteka standardowa, bez nowych zależności.
- **Jedna semantyka statusów dla wszystkich narzędzi**: killed to mutant wykryty, razem z timeoutem; survived to mutant, który przeżył albo nie był pokryty testem (dosłowny literał statusu zostaje w wyniku); mutanty nieważne (`CompileError`, `Ignored`, `NON_VIABLE`, `Unviable`, `skipped`) wypadają poza `total`. Nieznany status liczy się jako survivor - lepiej pokazać za dużo niż przemilczeć mutanta.
- **Nigdy cicha pusta lista**: kody wyjścia to 0 (OK), 2 (błąd wejścia: brak pliku, niepoprawny JSON/XML, raport pusty albo niezgodny ze schematem) i 64 (błąd użycia: nieznany format, brak argumentu). `score` jest `null` wyłącznie wtedy, gdy `total == 0`.
- **Fixture'y z realnych przebiegów** w `tests/fixtures/{stryker,pitest,mutmut,cargo-mutants}/`: Stryker 10.0.0, PIT 1.30.0, mutmut 3.8.0, cargo-mutants 27.1.0. Zgodnie z regułą projektu `external-tool-output` parser nie powstaje na wymyślonym wyjściu narzędzia.
- **Ograniczenie mutmut**: wymagane jest wyjście `mutmut results --all true`. Domyślne `mutmut results` pomija killed, więc score byłby zaniżony, a pusty wynik nieodróżnialny od złego pliku - skrypt zwraca wtedy błąd zamiast zera.
- **Skrypt sam nie uruchamia narzędzia, nie bramkuje i nie zapisuje plików**, i nie jest jeszcze wołany przez żaden skill. Jest wejściem dla kolejnych ticketów serii: skill `mutation-check` (MON-150), krok CI (MON-151), wpięcie w `work` i `sprint-end` (MON-152).

### 1.8.3

Wariant komend lint i testów dla git worktree (MON-166).

- **Nowa sekcja `## Worktree` na stronie `toolchain`**: `/monolynx:project-toolchain` zapisuje w niej pola `lint:` i `test:`. Przy stacku Dockera wyprowadza wariant `docker compose -p <projekt> [--profile <p>] run --rm --no-deps -v "$PWD":<workdir> <service> <komenda>` i pokazuje go do potwierdzenia. `-p` stoi przed `run`, bo to flaga globalna (`--project-name`); po `run` ten sam skrót znaczy `--publish`. Projekt bez Dockera dostaje w obu polach `jak wyzej`, czyli odesłanie do `## Lint` i `## Test`.
- **Automatyczny wybór wariantu w `/monolynx:work` i `/monolynx:work-simple`**: przed uruchomieniem lintu i testów skille sprawdzają na hoście, czy pracują w kopii repo. Detekcja to dwie osobne komendy, `git rev-parse --git-dir` i `git rev-parse --git-common-dir`, których wyniki skill porównuje sam. W worktree bierze komendy z `## Worktree`, poza nim z `## Lint` i `## Test`, jak dotąd.
- **Bez podstawień w komendzie uruchamianej z worktree**: strona zapisuje formę przenośną z `"$PWD"`, ale przed uruchomieniem skill podstawia dosłowną ścieżkę z `pwd`. Guard izolacji worktree odrzuca komendę `docker`, w której została zmienna albo `$(...)`, i z tego samego powodu wariant nie używa `--env-file` ze ścieżką liczoną w locie. Projekt, który nie wstaje na wartościach domyślnych, dostaje `--env-file <absolutna ścieżka>` wpisany literalnie przy potwierdzeniu.
- **Dlaczego**: `docker compose exec` wchodzi do działającego kontenera, a ten ma zamontowany główny checkout. Sesja `claude --bg --worktree <KEY>` z `MONOLYNX_AUTOTEST=true` lintowała i testowała cudzy kod. Flagi `-p <projekt>` i `--no-deps` są obowiązkowe: bez nich compose bierze nazwę katalogu worktree i stawia drugi komplet kontenerów razem z drugą bazą.
- **Brak sekcji przy pracy w worktree** zatrzymuje skill pytaniem i odsyła do `/monolynx:project-toolchain`; w sesji w tle (`MONOLYNX_SPRINT_RUN=1`) kończy się jako `SPRINT-RUN STOP`. Poza worktree brak sekcji nic nie zmienia, więc zachowanie stron zapisanych przed tą wersją zostaje takie jak w 1.7.0.

### 1.8.2

Hotfix po pierwszym przebiegu dyspozytora na realnym sprincie (MON-173).

- **Hook `bash_guard.py` nie zwraca `ask` w trybie sesji w tle** (`MONOLYNX_SPRINT_RUN=1`): sesja startowana przez dyspozytor nie ma nikogo, kto kliknie potwierdzenie, a tryb uprawnien sesji nie znosi decyzji `ask` z hooka. Zamiast tego `deny` z instrukcja: uzyj wariantu nieniszczacego (`rm` bez `-f`/`-r` na wlasnym pliku, `docker stop` + `docker rm` bez `-f`) albo zakoncz ture linia `SPRINT-RUN STOP: <powod>`. Poza trybem w tle hook pyta jak dotad.
- **`sprint_run_state.py` rozpoznaje sesje stojaca na promptcie o zgode** (`waitingFor: "permission prompt"` w `claude agents --json --all`, pole wystepuje takze na wpisach background) jako `waiting` z komenda `claude attach`, zamiast liczyc ja jako `running` w nieskonczonosc.
- **`rm -rf` tylko pod /tmp, katalogiem projektu i katalogami `.claude`** (MON-174): cel pod tymi katalogami (nigdy sam korzen) przechodzi bez pytania, takze u subagenta; cel poza nimi to twarde `deny` dla wszystkich; cel nie do zweryfikowania (ekspansja `$`/`~`, glob, komenda zlozona) zachowuje dotychczasowe `ask`/`deny`. Korzen projektu to `git rev-parse --show-toplevel` z `cwd` hooka, wiec worktree dyspozytora liczy sie jako projekt.

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
