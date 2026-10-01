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

Rozpocznij nową sesję po instalacji. W Codex wybierz `$monolynx:next` albo `$monolynx:help`; w Claude Code użyj `/monolynx:next` albo `/monolynx:help`. Definicje agentów Claude w `agents/` nie stają się automatycznie agentami Codex. Dostępne role i sposób delegowania zależą od klienta. Dyspozytor `sprint-run` ma od MON-185 dwa backendy: `MONOLYNX_SPRINT_RUNTIME=claude` (domyślny) i `codex`. Jedna zmienna wybiera binarkę, w której `sprint_run.sh` odpala tick dyspozytora oraz `sprint-end`/`retro`; algorytm dyspozytora i kontrakt werdyktów (`free`, `running`, `waiting`, `done`, `needs_human`) są wspólne dla obu. Zakres, ograniczenia Codex i ścieżki logów opisuje sekcja [Backend sesji: `claude` i `codex`](#backend-sesji-claude-i-codex).

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
| `sprint-run` | Backend `claude` (domyślny) | Backend `codex`: tick, `sprint-end`, `retro` i sesje robocze ticketów (`sprint_run_tick.py start`/`stop`) | Codex bez `--name`/`--bg`, bez natywnego sygnału "czeka na zgodę" i bez `attach`; stan sesji z własnego rejestru dyspozytora, nie z `codex agents` |

Tabela modeli poniżej dotyczy backendu Claude. W pozostałych klientach wybór opiera się na faktycznie dostępnych rolach, modelach i parametrach; aliasów `sonnet`/`opus` nie należy przekazywać do API, które ich nie obsługuje. Sam brak parametru lub inna nazwa produktu nie jest dowodem braku agentów.

### Skille (komendy `/monolynx:*`)

| Komenda | Opis |
|---------|------|
| `/monolynx:next` | Router read-only bez argumentów: sprawdza git, bieżący ticket, aktywny sprint, sesje, MR i konfigurację. Zwraca 1-3 komendy z uzasadnieniem i linię `NEXT: <komenda>`. Ticketu z niezamkniętym blokerem nie poleca do pracy, tylko wskazuje komendę dla blokera. Nie uruchamia polecanych skilli, nie zmienia ticketów ani konfiguracji. |
| `/monolynx:setup` | Checklista konfiguracji projektu, bez argumentów: slug, strona wiki `toolchain`, metoda LLM Wiki, graf zależności i testy mutacyjne w CI, flagi `MONOLYNX_*`. Każdą pozycję pokazuje jako OK albo BRAK i za zgodą uruchamia skill naprawczy (`/monolynx:project-toolchain`, `/monolynx:wiki-init`, `/monolynx:create-graph-ci-script`, `/monolynx:create-mutation-ci-script`); projekt bez CI może zamiast grafu w CI sam uruchomić `/monolynx:graph-sync`. Sam niczego nie zmienia; flagi autonomii tylko raportuje, nie ustawia ich. Pierwszy krok w nowym projekcie. |
| `/monolynx:brief` | Kontrakt epizodu bez ticketu (konfiguracja, investigation, docs): w jednej turze ustala "gotowe" jako obserwowalny stan, "nie ruszać" i prawa decyzyjne (sam / stop przed). ENTER = akceptacja i start pracy w tej samej turze. Działa bez MCP. |
| `/monolynx:work` | Podejmij zadanie z aktualnego sprintu: walidacja brancha, wznowienie przerwanej pracy, tryb defektu (mechanizm + test regresyjny przed fixem), research, dobór zespołu agentów, kontrakt autonomii (STOP CONDITIONS w promptach), praca równoległa, rekoncyliacja raportów, obowiązkowy krytyk z regułami do pamięci agentów, pełny lint/test przed `in_review`. Raportuje przebieg pracy do modułu Pipelines (obserwowalność): pipeline `ticket_work` ze stepami research → coding → wrap-up, job per agent, log każdego agenta jako strona wiki. Raportowanie best-effort - nie blokuje pracy. Po zielonym lincie i testach, przed krytykiem, proponuje diffowy przebieg `/monolynx:mutation-check` dla większych ticketów (od 5 SP albo 4 zmienionych plików z kodem); survivorzy trafiają jako kryteria akceptacji do bieżącego ticketu. Propozycja, nie automat. Nie merguje do swojego brancha brancha zależności, która nie jest `done`: w sesji w tle kończy turę linią `SPRINT-RUN STOP: czeka na merge <KEY>`, interaktywnie pyta (wyjątek: jawne polecenie użytkownika). Gdy po review zostają same odjęcia `BLOKADA-ZEWNĘTRZNA`, nie zleca kolejnej iteracji: zostawia komentarz z opcjami decyzji i w sesji w tle kończy turę linią `SPRINT-RUN STOP: blokada zewnetrzna - <powód>`, interaktywnie od razu pyta. Merge request dostaje tytuł `<KEY>: <tytuł ticketu>`. |
| `/monolynx:resume` | Wznowienie po `/clear` lub kompakcji: odtwarza stan ticketu z komentarzy, pipeline, kryteriów i gita, wypisuje "gdzie jesteśmy, co zostało, następny krok". Read-only. |
| `/monolynx:retro` | Retro korekt sprintu: zbiera uwagi krytyka, NEEDS WORK, reguły i eskalacje z komentarzy ticketów, pamięci agentów oraz auto-memory Claude Code (korekty użytkownika z czatu, katalog wykrywany z `$HOME`/`CLAUDE_CONFIG_DIR` i `$PWD`), grupuje powtarzające się i proponuje maszynerię (reguła w `.claude/rules/`, wpis w `constitution`, hook; wiersz rubryki tylko w repo pluginu, poza nim reguła projektu albo ticket do projektu Monolynx). Opcjonalny job w `sprint-end`. |
| `/monolynx:work-simple` | Uproszczony flow dla mniejszych ticketów (max 3 SP): jeden dobrany dev + krytyk jako zwykłe subagenty (bez Agent Teams), research opt-in, pełna ceremonia self-reporting, eskalacja do `/monolynx:work` gdy scope się rozrasta. Raportuje do modułu Pipelines (uproszczona instrumentacja: dev + krytyk), best-effort. Diffowy przebieg mutacyjny proponuje wyłącznie po `SCOPE GREW`, na ścieżce "kontynuuj simple" - zwykły prosty ticket nie płaci za mutacje. |
| `/monolynx:ticket-create` | Utwórz nowy ticket: zbiera kontekst z wiki, kodu i grafu zależności, generuje opis w ustalonej formie (cel, kontekst, zakres, pliki dotykane, nie ruszać, odwracalność, zależności, kryteria akceptacji). Sekcje "Nie ruszać" i "Odwracalność" są granicami zadania, z których `/monolynx:work` buduje kontrakt autonomii. Sekcja "Pliki dotykane" to przewidywane ścieżki repo-relatywne od Researchera (jedna linia `` - `ścieżka` `` na plik, bez globów; ticket poza repo: `- brak (poza repo)`), z której przegląd sprintu wykrywa kolizje plików. Tickety blokujące start zapisuje strukturalnie (`blocked_by_ids` w `create_ticket`), nie w komentarzu. Ticket trafia do sprintu albo backlogu. |
| `/monolynx:ticket-review` | Zrecenzuj ticket ze sprintu: sprawdza formę (8 kryteriów, w tym granice "Nie ruszać" i "Odwracalność"), zgodność z wiki i kodem, generuje tabelkę raportu z oceną i proponuje brakujące sekcje z dowodami `plik:linia`. Sprawdza, czy blokery z opisu zgadzają się z relacjami ticketu (`## Blokery (relacja)` w `get_ticket`), i przy rozjeździe proponuje `update_ticket` z pełną listą `blocked_by_ids`. Brak sekcji "Pliki dotykane" ocenia jako SŁABE w kryterium "Zakres zmian". `/monolynx:ticket-review sprint` (argument `[ticket-id lub klucz np. MNX-12 \| sprint]`) przegląda tickety `todo` aktywnego sprintu: skrypt `scripts/ticket_overlap.py` porównuje ich listy "Pliki dotykane" i dla par bez wzajemnej relacji, które dzielą pliki, proponuje `blocked_by` (bloker wg priorytetu, potem numeru klucza). Pliki zawsze wspólne (changelog `### Unreleased`, manifesty, liczniki skilli, kopie w `src/monolynx/static/skills/`) raportuje osobno i nie robi z nich blokerów. Relację zapisuje dopiero po zgodzie człowieka, z pełną listą `blocked_by_ids`. |
| `/monolynx:sprint-end` | Zamknij sprint jako pipeline `sprint_close` (stepy wiki-update → wrap-up): INGEST logów pracy do wiki, LINT (audyt wiki), czyszczenie stron logów pipeline sprintu, opcjonalne `retro` (przed zamknięciem, bo `complete_sprint` wypisuje niedokończone tickety ze sprintu) i realne `complete_sprint` (niedokończone tickety wracają do backlogu, z potwierdzeniem). Raportowanie do Pipelines best-effort. Pierwszym jobem stepu `wrap-up` jest `mutation-run`: pełny przebieg mutacyjny na modułach rdzenia, wiersze na stronie wiki `Trend mutacji` (data, moduł, score, baseline, delta) i zapadka jednego pola `baseline` w `toolchain` - tylko w górę. Krok jest nieblokujący: odmowa albo brak narzędzia nie wstrzymuje zamknięcia sprintu. Przed `close-sprint` job `version-bump` nadaje numer wydania pluginu, gdy branch docelowy ma w changelogu `### Unreleased` (patrz „Wersjonowanie pluginu”); w projekcie bez pluginu jest pomijany. Bez argumentu bierze aktywny sprint. |
| `/monolynx:sprint-run` | Dyspozytor sprintu: jeden idempotentny tick czyta stan sprintu i sesji w tle, startuje brakujące sesje `MONOLYNX_SPRINT_RUN=1 claude --bg --worktree <KEY> --name <slug>-<KEY>-<model>` z komendą `/monolynx:work <KEY>`, zbiera zakończone (komentarz z identyfikatorem sesji i `claude rm`), raportuje czekające na człowieka i kończy linią `SPRINT-RUN: remaining=... running=... waiting=... done=...`. Ticketu z niezamkniętym blokerem (linia `Blokowany przez:` w sekcji `## Blokery (relacja)` z `get_ticket`, bloker w statusie innym niż `done`, także `in_review`) nie startuje; w raporcie taki ticket ma powód `czeka na merge <KEY>` i zostaje w `remaining`. Ticketu z etykietą `needs-local` też nie startuje w tle: wypisuje go w sekcji "Czekają na człowieka" z komendą `/monolynx:work <KEY>` do uruchomienia interaktywnie. Sam nigdy nie pracuje nad ticketem. Gdy preflight zgłosi `BASE_BRANCH_STALE=true`, przed dispatchem przewija główny checkout przez `sprint_run_tick.py sync-base` (tylko `fetch` + `merge --ff-only` na czystym checkoucie stojącym na branchu bazowym). Liczba slotów: argument, a bez niego `MONOLYNX_SPRINT_PARALLEL` albo 2; slot zajmuje tylko sesja `running`, a żywe sesje `waiting` mają osobny limit `MONOLYNX_SPRINT_MAX_WAITING` (domyślnie 2), po którego osiągnięciu tick nie startuje nowych sesji. Pętla jest na zewnątrz: `/loop 15m /monolynx:sprint-run` albo `plugin/scripts/sprint_run.sh`. Backend wybiera `MONOLYNX_SPRINT_RUNTIME` (`claude` domyślnie): przy `codex` tick idzie przez `codex exec`, preflight i raport stanu dostają `--runtime codex` / `--backend codex`, a sesje robocze startuje i sprząta `sprint_run_tick.py start|stop --backend codex` zamiast `claude --bg` i `claude rm`. |
| `/monolynx:mr-queue` | Kolejka merge MR (GitLab, `glab`) i PR (GitHub, `gh`): jeden idempotentny tick bierze pierwszy niezmergowany MR/PR z `.git/monolynx/mr-queue.json` i robi jedno działanie w kolejności konflikt -> CI -> approve -> merge: konflikt -> anulowanie trwającego pipeline'u MR/PR, merge brancha docelowego i push, czerwone CI -> poprawka w tymczasowym worktree i push (maks. 2 rundy łącznie z konfliktami), brak approve -> pytanie do użytkownika, gotowe -> merge z blokadą na SHA. Po merge na domyślny branch repozytorium czeka na zielone CI brancha docelowego przed kolejnym elementem; po merge na branch integracyjny (branch docelowy różny od domyślnego, np. `sprint/*`) domyka pozycję w tym samym ticku na podstawie zielonego CI samego MR/PR i anuluje pipeline brancha integracyjnego wywołany tym merge (`glab ci cancel pipeline <id>` / `gh run cancel`; brak pipeline'u albo błąd anulowania to ostrzeżenie w raporcie, nie zatrzymanie kolejki). Ticket przechodzi na `done` po zielonym CI brancha docelowego (domyślny branch) albo od razu po merge (branch integracyjny, komentarz podaje pipeline MR/PR). Bez argumentów układa kolejkę z otwartych MR/PR (stack zaraz po swojej bazie, reszta od najstarszego, drafty pominięte) i przyjmuje ją bez pytania, a w kolejnych tickach dopisuje na koniec nowe otwarte MR/PR (`queue-append`); argumenty nadpisują kolejność. Pyta tylko o approve, merge bez `MONOLYNX_AUTOMERGE` i push do cudzego brancha; czerwone CI brancha docelowego, `needs_human`, limit rund poprawek i sprzeczny konflikt kończą tick raportem z linią `Potrzebne:`, a nie pytaniem. Checkout użytkownika zostaje nietknięty z jednym wyjątkiem: domknięcie pozycji (po zielonym CI brancha docelowego, a na branchu integracyjnym od razu po merge) przesuwa lokalny branch docelowy w głównym checkoucie wyłącznie fast-forwardem (`git merge --ff-only origin/<b>` na tym branchu, `git fetch origin <b>:<b>` z innego), a lokalne commity albo niezapisane zmiany kończą się ostrzeżeniem z komendą do ręcznego wykonania; nigdy reset, rebase, stash ani force. Pipeline'u anulowanego przez człowieka nie ponawia. Werdykty liczy `scripts/mr_queue_state.py` (w tym `post-merge` wybierający tor po merge, `target-cancel` wskazujący pipeline'y brancha integracyjnego do anulowania, `target-sync` dla fast-forwardu i `config-check`, który w preflighcie odrzuca różne `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET`). Kończy linią `MR-QUEUE: remaining=... current=... verdict=... done=...`; pętla: `/loop 10m /monolynx:mr-queue`. |
| `/monolynx:search` | Szukaj informacji w wiki projektu (architektura, API, integracje, standardy kodu) przez wyszukiwanie semantyczne; przy włączonej metodzie LLM Wiki proponuje zapis odpowiedzi jako stronę syntezy. Zwykle nie trzeba wpisywać komendy: skill włącza się sam przy pytaniach o dokumentację projektu. |
| `/monolynx:wiki-init` | Włącz metodę LLM Wiki dla projektu: tworzy strony systemowe (regulamin `wiki-schema`, katalog `wiki-index`, dziennik `wiki-log`) i włącza flagę. Idempotentny bootstrap. Opcjonalny: potrzebny, gdy wiki ma być rosnącym artefaktem, a nie zbiorem luźnych stron. |
| `/monolynx:wiki-ingest` | Zintegruj nowe źródło (plik, URL, wklejona treść) z wiki: strona źródła, aktualizacja powiązanych stron encji/konceptów, wikilinki, odświeżenie katalogu i wpis do dziennika. Wymaga włączonej metody LLM Wiki. |
| `/monolynx:wiki-lint` | Audyt zdrowia wiki: wykrywa sieroty, martwe linki, sprzeczności i luki, prezentuje raport i proponuje naprawy. |
| `/monolynx:wiki-sync-merge` | Post-merge INGEST do wiki metodą LLM Wiki. Odpala człowiek po merge ticketów/PR do brancha integracyjnego - pobiera dane ticketów, integruje wiedzę z zamkniętych zadań, aktualizuje powiązane strony i odświeża katalog. |
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

1. Ustala **branch bazowy** (`MONOLYNX_BASE_BRANCH`, a bez niej łańcuch fallbacku) i waliduje branch roboczy: twardy gate na branchu bazowym, niezależnie od `MONOLYNX_BRANCH_MODE`; ticket `in_progress` z planem -> proponuje **wznowienie** od ostatniego etapu.
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

Treść ról ma jedno źródło - `plugin/agents/*.md`. Kopie w `.claude/agents/` (czytane przez Claude Code także bez zainstalowanego pluginu) powstają z niego przez `python3 scripts/sync_agents.py`; `--check` wykrywa rozjazd dystrybucji.

Skille nie zakładają z góry żadnego zestawu agentów: przed startem zespołu sprawdzają faktycznie dostępny mechanizm delegowania i role zdefiniowane w projekcie, a agenci pluginu są fallbackiem. Niezmienne są trzy rzeczy, niezależnie od klienta: rozłączny przydział plików per agent, raport od każdego agenta i **osobny krytyk** - recenzję zawsze wykonuje inne powołanie niż to, które pisało kod. Gdy w katalogu narzędzi nie ma żadnego delegowania, skille schodzą na tryb solo (jeden agent, rola po roli, review jako osobny przebieg według tej samej rubryki) i zapisują wprost, że **review nie jest niezależne** - w komentarzu planu, w komentarzu podsumowującym do ticketu i w podsumowaniu dla użytkownika. Tryb solo wybiera się wyłącznie po braku capability, nigdy po nazwie klienta.

### Rubryka krytyka

Krytyk nie ocenia "na wyczucie": punktuje według rubryki z twardymi odjęciami i progiem zaliczenia. Rubryka ma **jedno źródło** - `plugin/skills/work/review-rubric.md` - i jest **wspólna dla `work` i `work-simple`**: oba skille czytają plik `review-rubric.md` z katalogu własnego skilla i wklejają jego pełną treść do promptu krytyka. Ta sama rubryka obowiązuje w trybie solo, gdy runtime nie ma delegowania.

Odjęcie, którego żaden agent nie usunie z poziomu repo i swoich narzędzi, krytyk oznacza znacznikiem `BLOKADA-ZEWNĘTRZNA` z jednozdaniowym powodem. Są trzy klasy: środowisko lokalne (brak narzędzia, usługi, danych), uprawnienia (odmowa klasyfikatora albo hooka, brak dostępu) i decyzja człowieka (wariant, sekret, zmiana poza kontraktem). Odjęcie liczy się do score jak każde inne, próg 82 się nie zmienia. Gdy agentowi poniżej progu zostają wyłącznie takie odjęcia, `work` i `work-simple` nie zlecają kolejnej iteracji (wspólny blok `blokada-zewnetrzna`): dopisują do ticketu komentarz z listą blokad, opcjami decyzji i sugestią etykiety `needs-local` (na razie informacyjnej; obsługę w `sprint-run` wprowadza osobny ticket), w sesji w tle kończą turę `SPRINT-RUN STOP`, a interaktywnie od razu pytają użytkownika.

Kopia dla `work-simple` (`plugin/skills/work-simple/review-rubric.md` oraz dystrybucja w katalogu `monolynx-work-simple` w `src/monolynx/static/skills/`) jest generowana przez `python3 scripts/sync_skills.py` (`make sync-skills`); `--check` wykrywa rozjazd, więc edycja samej kopii nie przejdzie. Nowy wiersz rubryki (np. z `/monolynx:retro`) dopisuje się **wyłącznie** do źródła w `work`, a synchronizacja rozwozi go dalej.

### Reguły i pamięć projektu

Trwałe reguły żyją w repozytorium, jako zwykłe pliki Markdown pod kontrolą gita, a nie w prywatnej pamięci któregoś klienta:

| Lokalizacja (od korzenia repo) | Co tam jest | Kto zapisuje |
|---|---|---|
| `.claude/rules/*.md` | twarde reguły projektu; krytyk czyta je na starcie review, naruszenie to -25 w rubryce | `/monolynx:retro` po akceptacji użytkownika |
| `.claude/agent-memory/<typ-agenta>/MEMORY.md`, sekcja `## Reguły z review krytyka` | reguły z review per rola | `work` / `work-simple` po werdykcie NEEDS WORK |

Oba źródła czyta każdy klient wykonujący instrukcje skilli - to zwykły odczyt plików, więc reguła zapisana w jednym przebiegu wraca w kolejnym niezależnie od tego, w czym go uruchomiono. Każdy zapis jest **addytywny** (dopisanie do sekcji, nigdy nadpisanie pliku), żeby wcześniejsze reguły nie ginęły. Nazwa katalogu `.claude/` jest historyczna: to istniejąca lokalizacja z danymi użytkownika, wspólna dla klientów, nie zastrzeżona dla jednego - plugin jej nie migruje. Prywatna pamięć klienta (np. auto-memory Claude Code) pozostaje opcjonalnym, dodatkowym źródłem dla `/monolynx:retro`; jej brak nie blokuje niczego i nie jest przenoszona między klientami.

### Hooki

Codex wykrywa ten sam `hooks/hooks.json` i udostępnia `CLAUDE_PLUGIN_ROOT` jako alias zgodności. Hooki pluginu wymagają przeglądu i nadania zaufania przez użytkownika; sama instalacja ich nie aktywuje. Instalacja pojedynczego skilla przez MCP nie rejestruje hooków. Obsługa zdarzeń i kontekst subagentów zależą od klienta, więc odczyt manifestu nie stanowi dowodu egzekwowania wszystkich reguł guardów.

Plugin dostarcza hook PreToolUse na narzędziu Bash (`hooks/hooks.json` → `hooks/bash_guard.py`), który deterministycznie egzekwuje reguły workflow, dotąd zapisane wyłącznie w promptach skilli `work` / `work-simple`:

| Kto | Komenda | Decyzja |
|---|---|---|
| Subagent (dev, krytyk, ...) | `git add/commit/push/reset --hard/rebase` | **deny** - operacje zapisu w gicie należą do sesji głównej i użytkownika |
| Subagent | lint/testy (`pytest`, `ruff`, `eslint`, ...) | **deny** - lint/testy odpala Team Manager wg `MONOLYNX_AUTOTEST` |
| Subagent | `ruff check --no-fix` / `ruff format --check` albo `--diff` (także przez `docker compose exec [-T] app` i `docker compose ... run <flagi> app` z `-v` wyłącznie z korzenia projektu, bez `--entrypoint`, `--privileged`, `-u`/`--user`) | **allow** - odczyt, nie wymaga pośrednictwa Team Managera (MON-176, zawężone w MON-207). `ruff check` bez `--no-fix` (może naprawiać pliki przez `fix = true` w `pyproject.toml`) i `docker run <obraz> ruff` spadają do zwykłej reguły lint/testów |
| Sesja główna | read-only `ruff` jak wyżej | **ask**, chyba że `MONOLYNX_AUTOTEST=true` - wyjątek read-only dotyczy tylko subagenta (MON-207) |
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

Hook nigdy nie blokuje sesji głównej na twardo - decyzję podejmuje użytkownik. Deny u subagenta to oczekiwane zachowanie workflow, nie błąd. Hook wymaga `python3` w PATH.

"Subagent" w tabeli to każdy agent pomocniczy, czyli payload PreToolUse z niepustym `agent_id`, niezależnie od `agent_type` (MON-224). Obejmuje to subagenta wywołanego narzędziem Agent i teammate'a Agent Teams w trybie in-process (domyślnym, m.in. w sesjach `claude --bg` startowanych przez `sprint-run`): jego payload ma `agent_id` i `agent_type` równe nazwie teammate'a, a `session_id` wspólny z liderem. Teammate nazwany `main` też jest subagentem. Payload bez `agent_id` (albo z pustym) to sesja główna.

**Ograniczenie: teammate w trybie `teammateMode: "tmux"`.** Taki teammate to osobny proces `claude` z własnym `session_id`, bez `agent_id`, z `agent_type` równym typowi agenta. Tak samo wygląda payload sesji głównej uruchomionej przez `claude --agent <typ>`, a zmienne `CLAUDE_*` w środowisku hooka są takie same jak u lidera (Claude Code 2.1.284, fixture'y `tests/fixtures/claude/pretooluse-bash-*.2.1.284.json`). Hook nie zgaduje, więc traktuje teammate'a tmux jak sesję główną: `ask` dla `git push` i lint/testów, przepuszczenie przy `MONOLYNX_AUTOPUSH`/`MONOLYNX_AUTOTEST`/`MONOLYNX_AUTOMR=true`, a `git add`/`git commit` idą zwykłym systemem uprawnień. Marker `hook-active` jego sesji ma `subagent_identification=false`, więc `work` zgłasza stan „niepełna”. Obejście: tryb in-process (domyślny poza tmux) albo brak flag `MONOLYNX_AUTO*` w sesji z teammate'ami tmux.

#### Ładowanie, zaufanie i wykrywanie braku ochrony

Hook nie uruchamia się dlatego, że leży w katalogu pluginu. Klient musi go załadować z manifestu (`hooks/hooks.json`, wskazany przez `plugin.json` w Claude Code albo `.codex/hooks.json` w Codex) **i** dostać od użytkownika zaufanie do pluginu. Bez tej decyzji klient w ogóle nie uruchamia procesu hooka, więc żadna reguła z tabeli wyżej nie działa: `deny` i `ask` działają dopiero PO nadaniu zaufania, nie zamiast niego. Instalacja pojedynczego skilla przez `install_monolynx_skills` nie rejestruje hooków w ogóle.

Żeby brak ochrony nie był cichy, hook przy każdym wywołaniu PreToolUse z rozpoznanym `session_id` zapisuje best-effort marker `~/.cache/monolynx/hook-active/<session_id>` (zapis fail-open: błąd IO nigdy nie zmienia decyzji). Treść to JSON: `{"subagent_identification": <bool>, "checked_at": "<ISO 8601>"}`, gdzie pole `subagent_identification` mówi, czy w tej sesji hook widział już payload z niepustym `agent_id`. Wartość jest trwała w obrębie sesji: raz zapisane `true` zostaje `true`, a kolejne komendy lidera (bez `agent_id`) odświeżają tylko `checked_at` (MON-224). Wcześniej każda komenda lidera, także `find` czytające marker w skillu, nadpisywała `true` wartością `false`, więc stan „potwierdzona” był nieosiągalny. Uszkodzony lub nieczytelny poprzedni marker liczy się jak brak wcześniejszego `true`. Decyzja hooka nigdy nie zależy od markera. Skille `work`, `work-simple` i `mr-queue` czytają marker i raportują wynik jako jeden z trzech stanów:

| Stan | Co widać | Znaczenie |
|---|---|---|
| **potwierdzona** | marker z `"subagent_identification": true` | hook żyje i w tej sesji widział już payload subagenta z `agent_id` - twarde blokady subagentów działają |
| **niepełna** | marker jest, ale wyłącznie z `false` | hook żyje, ale w tej sesji nie widział jeszcze payloadu z `agent_id`: żaden subagent nie uruchomił Basha albo klient nie przekazuje identyfikacji w PreToolUse (Codex CLI, teammate tmux - subagent dostaje traktowanie sesji głównej) i może nie egzekwować decyzji `ask` |
| **brak potwierdzenia** | brak markera po pierwszej komendzie Bash | hook nie zdążył zadziałać albo nie jest załadowany/zaufany |

Odczyt markera idzie **dwiema osobnymi komendami**: najpierw `echo $HOME`, potem `find <HOME>/.cache/monolynx/hook-active -type f -mmin -5 -exec cat {} +` z podstawioną literalnie ścieżką. Powód jest praktyczny: `work` i `work-simple` biegną zwykle w git worktree, a guard izolacji worktree odrzuca komendę, której argument jest wyliczany w runtime (`$HOME`, `$(...)`) - jednolinijkowiec z `"$HOME/..."` nie wykonałby się wcale i skill zamiast stanu dostałby odmowę.

W stanie innym niż „potwierdzona” skille nie deklarują deterministycznej ochrony: piszą o tym jedną linią w podsumowaniu i egzekwują zgody rozmową z użytkownikiem, a w trybie w tle (`MONOLYNX_SPRINT_RUN=1`) kończą turę linią `SPRINT-RUN STOP` zamiast udawać działającą blokadę.

Zakres egzekwowania zależy od klienta i nie da się go wywnioskować z nazwy produktu: obsługa zdarzeń, identyfikacja subagenta i respektowanie decyzji `ask` to właściwości konkretnego klienta. Klient, który parsuje `ask`, ale mimo to wykonuje komendę (fail-open), zostawia zgody sesji głównej bez egzekucji. Dla takich klientów jest opt-in `MONOLYNX_HOOK_ASK_UNSUPPORTED=true`: hook zamienia wtedy każdą decyzję `ask` na `deny` z wyjaśnieniem, że operację musi wykonać człowiek poza hookiem. Domyślnie wyłączone - to świadomy wybór użytkownika, nie zmiana domyślnej polityki, i nie zastępuje nadania zaufania hookom.

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
| `MONOLYNX_BRANCH_MODE` | `ticket` | Walidacja **nazwy** brancha roboczego: `ticket` - branch musi zawierać numer ticketu; `sprint` - dowolny branch poza branchem bazowym, bez pytań (praca nad całym sprintem na jednym branchu); `off` - brak walidacji |
| `MONOLYNX_AUTOTEST` | `false` | `true` - skill sam odpala lint i testy; `false` - wypisuje komendy i czeka na Twój wynik |
| `MONOLYNX_AUTOCOMMIT` | `false` | `true` - commit po zielonym teście; `false` - wypisuje gotową komendę |
| `MONOLYNX_AUTOPUSH` | `false` | `true` - push bez pytania; `false` - nigdy nie pushuje |
| `MONOLYNX_CONTRACT` | `ask` | `ask` - kontrakt autonomii czeka na ENTER; `auto` - lista "stop przed" jest wypisywana i zapisywana w komentarzu planu jako KONTRAKT (auto), praca idzie dalej bez potwierdzenia |
| `MONOLYNX_SPRINT_RUN` | `0` | `1` - tryb sesji w tle (dyspozytor `/monolynx:sprint-run`): każde czekanie na odpowiedź użytkownika poza kontraktem kończy turę linią `SPRINT-RUN STOP: <powód>`; implikuje `MONOLYNX_CONTRACT=auto`. Ustawia ją dyspozytor prefiksem komendy startu sesji (`MONOLYNX_SPRINT_RUN=1 claude --bg ...`) albo `sprint_run.sh`; nie wpisuj jej do śledzonego `.claude/settings.json` (MON-207) |
| `MONOLYNX_AUTOMR` | `false` | `true` - po faktycznym pushu tworzy merge request przez `glab mr create` (działa TYLKO razem z `AUTOCOMMIT=true` i `AUTOPUSH=true`) |
| `MONOLYNX_MR_TARGET` | - | Branch docelowy MR tworzonego przez `MONOLYNX_AUTOMR` w `work` i `work-simple`, np. `develop`, gdy tickety mergują się do brancha integracyjnego. Pusta -> domyślny branch repozytorium: `git symbolic-ref refs/remotes/origin/HEAD`, a gdy ref nie istnieje (klon bez ustawionego `origin/HEAD`, np. bez `git remote set-head origin -a`) - `glab repo view -F json --jq .default_branch`. Gdy nic nie da wyniku, MR nie powstaje (skill nie zgaduje `main`). Wskazuje ten sam branch co `MONOLYNX_BASE_BRANCH` i to ją ustawiasz, gdy ma być jedna zmienna: pusta `MONOLYNX_BASE_BRANCH` bierze jej wartość, odwrotnie nie. Obie ustawione i różne to `ERROR` w preflighcie `sprint-run` i w `config-check` skilla `mr-queue` (MON-219) |
| `MONOLYNX_BASE_BRANCH` | - | **Punkt startu** brancha roboczego i worktree ticketu w `work` i `work-simple`: z tego brancha powstaje `feature-<KEY>`. Pusta -> ustalana łańcuchem `MONOLYNX_BASE_BRANCH` -> `MONOLYNX_MR_TARGET` -> `git symbolic-ref refs/remotes/origin/HEAD` -> `glab repo view -F json --jq .default_branch`. Gdy nic nie da wyniku, skill zatrzymuje pracę i prosi o ustawienie tej zmiennej zamiast zgadywać `main` - branch roboczy założony ze złego punktu startu trzeba przepiąć ręcznie. Wskazuje ten sam branch co `MONOLYNX_MR_TARGET`, więc zwykle zostaje pusta. Obie ustawione i różne to `ERROR` w preflighcie `sprint-run` i w `config-check` skilla `mr-queue` (MON-219) |
| `MONOLYNX_SPRINT_RUN_MODE` | - | Tryb ticka dyspozytora. `batch` ustawia sam `sprint_run.sh`; skill `sprint-run` traktuje brak zmiennej (i każdą inną wartość) jako tryb interaktywny |
| `MONOLYNX_SPRINT_PERMISSION_MODE` | `auto` | Tryb uprawnień sesji roboczych `--bg` startowanych przez dyspozytora (patrz KROK 4 tego skilla). `bypassPermissions` i `--dangerously-skip-permissions` blokują tick już w preflight (patrz sekcja „Uprawnienia ticku i preflight”) zamiast startować sesję, którą klasyfikator trybu auto i tak odrzuci |
| `MONOLYNX_SPRINT_MAX_WAITING` | `2` | Limit żywych sesji `waiting` (czekających na człowieka) w dyspozytorze `/monolynx:sprint-run`. Sesja `waiting` nie zajmuje slotu z `--slots` / `MONOLYNX_SPRINT_PARALLEL`; po osiągnięciu limitu tick nie startuje nowych sesji. Wartość spoza dodatnich liczb całkowitych oznacza `2`. `sprint_run.sh` eksportuje ją do ticku |
| `MONOLYNX_SPRINT_MODEL` | `opus` | Model sesji roboczych `--bg` (`/monolynx:work <KEY>`) startowanych przez dyspozytora (patrz sekcja Modele) |
| `MONOLYNX_AUTOMERGE` | `false` | `true` - `/monolynx:mr-queue` merguje MR/PR bez pytania (tylko przy werdykcie `ready`, z blokadą na SHA); `false` - hook pyta o każdy merge |
| `MONOLYNX_MERGE_METHOD` | `merge` | Metoda merge w `/monolynx:mr-queue`: `merge`, `squash` albo `rebase` |
| `MONOLYNX_SPRINT_TICK_MODEL` | `opus` | Model procesu `claude -p`, w którym `sprint_run.sh` odpala tick dyspozytora oraz `--close` (`sprint-end`, `retro`). Przy `MONOLYNX_SPRINT_RUNTIME=codex` nie trafia do argumentów `codex exec` (CLI nie przyjmuje aliasów `opus`/`sonnet`) - zostaje tylko w linii `start:` logu pętli |
| `MONOLYNX_SPRINT_RUNTIME` | `claude` | Backend dyspozytora: `claude` albo `codex`. Jedna zmienna dla całego przebiegu `sprint_run.sh` - tick, `sprint-end` i `retro`. Inna wartość kończy skrypt kodem 64, brak binarki wybranego runtime kodem 69. Tę samą wartość podaje się tickowi jako `--runtime` (preflight) i `--backend` (stan) |
| `MONOLYNX_SPRINT_CODEX_SANDBOX` | `workspace-write` | Wartość `--sandbox` dla `codex exec` (`read-only`, `workspace-write`, `danger-full-access`). Dotyczy wyłącznie backendu `codex`. Plugin nigdy nie ustawia domyślnie `--dangerously-bypass-approvals-and-sandbox` ani `--approve-for-me` |
| `MONOLYNX_SPRINT_TICK_ALLOWED_TOOLS` | `Bash(claude:*),Bash(MONOLYNX_SPRINT_RUN=1 claude:*),Bash(python3:*),Read(~/.claude/plugins/**),mcp__monolynx,mcp__claude_ai_Monolynx,mcp__plugin_monolynx_monolynx` | Lista narzędzi (po przecinkach), którą `sprint_run.sh` przekazuje tickowi w `claude -p --allowedTools`. Ustaw w środowisku procesu odpalającego pętlę; zastępuje całą listę domyślną (patrz sekcja „Uprawnienia ticku i preflight”) |
| `MONOLYNX_MUTATION_BUDGET` | `600` | Budżet czasu przebiegu `/monolynx:mutation-check` w sekundach. Maksimum 600 (limit narzędzia Bash); większa wartość jest skracana do 600 |
| `MONOLYNX_BRIEF_GUARD` | `auto` | Tryb hooka `brief_guard`: `auto` - goły imperatyw dostaje skill `brief` z propozycją kontraktu; `block` - prompt odrzucony z odesłaniem do `/monolynx:brief`; `off` - wyłączony (patrz sekcja Hooki) |
| `MONOLYNX_HOOK_ASK_UNSUPPORTED` | `false` | **Wyłącznie wartość `true`** (jak pozostałe flagi tego hooka) - `bash_guard.py` zamienia wtedy każdą decyzję `ask` na `deny` z wyjaśnieniem, że operację musi wykonać człowiek poza hookiem; każda inna wartość (w tym `1`) zostawia domyślne zachowanie. Opt-in dla klientów, które parsują `ask`, ale mimo to wykonują komendę (fail-open) - bez tej flagi zgody sesji głównej w takim kliencie nie są egzekwowane. Nie zmienia zachowania klientów, które `ask` obsługują (patrz sekcja Hooki) |

Trzy zmienne od branchy mylą się najczęściej, bo brzmią podobnie, a dotyczą trzech różnych momentów. `MONOLYNX_BRANCH_MODE` waliduje **nazwę** brancha roboczego (`ticket` / `sprint` / `off`) i nic nie mówi o tym, z czego ten branch powstał. `MONOLYNX_BASE_BRANCH` to **punkt startu** przed pracą: z czego `work` i `work-simple` zakładają branch roboczy i worktree ticketu. `MONOLYNX_MR_TARGET` to **cel MR** po pracy: dokąd idzie merge request. To dwa momenty, ale jeden branch: ticket startuje z tego brancha, do którego wraca jego MR, więc projekt mergujący tickety do `develop` ustawia jedną zmienną, nie dwie: `MONOLYNX_MR_TARGET`, bo pusta `MONOLYNX_BASE_BRANCH` bierze jej wartość z łańcucha fallbacku, a cel MR w `work` i `work-simple` nie sięga po `MONOLYNX_BASE_BRANCH` (sama `MONOLYNX_BASE_BRANCH` ustala punkt startu i branch docelowy `mr-queue`, ale MR z `MONOLYNX_AUTOMR` poszedłby wtedy do domyślnego brancha repozytorium). Ustawione obie z różnymi wartościami (`MONOLYNX_BASE_BRANCH` różne od `MONOLYNX_MR_TARGET`) dają `ERROR`: preflight `sprint-run` zatrzymuje tick, a `mr_queue_state.py config-check` w preflighcie `mr-queue` kończy tick kodem 2 (MON-219). Twardy gate na branchu bazowym działa niezależnie od `MONOLYNX_BRANCH_MODE`: rozpoczęcie pracy wprost na branchu, który jest punktem startu dla innych, zawsze wymaga zgody.

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

Tabela dotyczy procesów `claude`. Przy `MONOLYNX_SPRINT_RUNTIME=codex` model **nie trafia do CLI**: `codex exec -m` nie przyjmuje aliasów `opus`/`sonnet`, więc `sprint_run.sh` uruchamia tick bez `-m`, a Codex bierze model ze swojej konfiguracji (`~/.codex/config.toml`, `codex exec -c model=...`). `MONOLYNX_SPRINT_TICK_MODEL` i `MONOLYNX_SPRINT_MODEL` służą wtedy wyłącznie do logowania i nazewnictwa. Reguła `.claude/rules/session-name-model.md` (jawny model, model na końcu nazwy sesji) obowiązuje dalej dla procesów `claude` - sesje robocze ticketów nadal startują tak samo.

## Sprint bez sesji interaktywnej

Sprint da się przerobić bez człowieka siedzącego przy terminalu. Robią to dwa elementy, które można używać osobno.

**Dyspozytor `/monolynx:sprint-run [sloty]`** to jeden tick: czyta aktywny sprint i stan sesji w tle, startuje brakujące sesje `/monolynx:work` (każdą w osobnym worktree, pod nazwą `<slug>-<KEY>-<model>`, np. `monolynx-MON-12-opus`), zbiera zakończone, raportuje te, które czekają na człowieka, i kończy ostatnią linią:

```
SPRINT-RUN: remaining=<n> running=<m> waiting=<k> done=<d>
```

Stan sprintu liczony jest **przed** startem sesji, więc tick koryguje linię o `S`, czyli liczbę sesji faktycznie wystartowanych w tym ticku: `remaining = summary.remaining - S`, `running = summary.running + S`, a `waiting` i `done` przepisuje bez zmian. Przykład: stan przed dispatchem `remaining=5 running=0 waiting=0 done=1`, tick wystartował dwie sesje (`S = 2`), więc ostatnia linia to `SPRINT-RUN: remaining=3 running=2 waiting=0 done=1`. Linia `remaining=5 running=2` jest błędna: to korekta samego `running` bez odjęcia `S` od `remaining`.

Dyspozytor nie startuje ticketu z niezamkniętym blokerem. Blokerem jest każda linia `Blokowany przez: <KEY> | <status> | <tytuł>` w sekcji `## Blokery (relacja)`, którą `get_ticket` generuje z pola `blocked_by_ids` (MON-198, nagłówek od MON-207); sekcja `## Zaleznosci` w tekście opisu (szablon `ticket-create`) i komentarz `**Zaleznosci:**` to proza, nie relacja. Stare zależności zapisane tylko prozą przenosi do relacji jednorazowa migracja danych `make backfill-dependencies` (dry-run; zapis: `make backfill-dependencies ARGS=--apply`). Status w tej linii jest liczony przy odczycie, więc zawsze świeży. Blokuje każdy status inny niż `done`, także `in_review`, bo sesja startująca przy zależności w review mergowała cudzy, niezmergowany branch i jej MR niósł cudze commity (MON-150, MON-152). Zablokowany ticket nie zajmuje slotu, nie wchodzi do `S`, w raporcie ma powód `czeka na merge <KEY>` i zostaje w `remaining`. Gdy w sprincie zostały wyłącznie takie tickety, pętla nie uzna sprintu za wykonany i tyka dalej, aż człowiek zmerguje bloker albo pętla dojdzie do `--max-ticks`.

Dyspozytor nie startuje w tle ticketu z etykietą `needs-local`, bo taki ticket wymaga lokalnego środowiska albo decyzji człowieka (MON-138: `brew update-python-resources` uruchamiane lokalnie i edycja `.gitlab-ci.yml`, której klasyfikator trybu automatycznego agentowi odmawia). Etykietę czyta z linii `Labels:` tego samego `get_ticket`, którym sprawdza blokery. Taki ticket nie zajmuje slotu, nie wchodzi do `S`, zostaje w `remaining`, a raport wymienia go w sekcji "Czekają na człowieka" z komendą `/monolynx:work <KEY>` do uruchomienia interaktywnie. Komentarza na tickecie tick nie dopisuje.

**Sloty i sesje `waiting`.** Slot zajmuje tylko sesja `running`: `wolne = sloty - running`. Sesja `waiting` (czeka na odpowiedź człowieka) slotu nie zajmuje, ale żywe sesje `waiting` mają osobny limit `MONOLYNX_SPRINT_MAX_WAITING` (domyślnie 2). Po jego osiągnięciu tick nie startuje nowych sesji, a sesje `waiting` i ich worktree zostają nietknięte. Przepustowość liczy skrypt stanu (pole `dispatch` w wyniku `sprint_run_tick.py state --slots <n> --max-waiting <n>`), nie model. WHY: przy `PARALLEL=2` sesja MON-138 czekała na człowieka od wieczora do rana i przez całą noc zajmowała połowę slotów sprintu.

Sprint, w którym zostały wyłącznie tickety `needs-local`, tickety z niezamkniętym blokerem i sesje `waiting`, ma w linii statusu `remaining>0`, więc `sprint_run.sh` nie uzna go za wykonany: loguje `UWAGA: nic nie biegnie` i tyka dalej do `--max-ticks`.

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

### Backend sesji: `claude` i `codex`

Dyspozytor ma dwa backendy. Wybiera je **jedna** zmienna `MONOLYNX_SPRINT_RUNTIME` (domyślnie `claude`), obejmująca cały przebieg `sprint_run.sh`: tick dyspozytora oraz `sprint-end` i `retro` przy `--close`. Algorytm, werdykty (`free`, `running`, `waiting`, `done`, `needs_human`) i zamrożona linia statusu `SPRINT-RUN: remaining=... running=... waiting=... done=...` są wspólne - `sprint_run_state.py` nie zmienił się wraz z dodaniem Codeksa, więc zachowanie backendu `claude` jest dokładnie dotychczasowe.

```bash
MONOLYNX_SPRINT_RUNTIME=codex plugin/scripts/sprint_run.sh --interval 15m --slots 2
```

Co robi który backend:

| Element | `claude` | `codex` |
|---|---|---|
| Proces ticku | `claude -p "<prompt>" --name <slug>-sprint-run-<model> --model <model> --permission-mode acceptEdits --allowedTools ... --permission-prompts none --max-turns 80 --output-format text` | `codex exec --sandbox workspace-write -C <katalog projektu> --skip-git-repo-check "<prompt>"` |
| Prefiks skilla w promptcie | `/monolynx:sprint-run` | `$monolynx:sprint-run` (zamiana prefiksu robi `sprint_run.sh`; Codex zna skille z `.agents/skills`, MON-182) |
| Model w komendzie | `--model` (patrz sekcja Modele) | brak - `codex exec -m` nie przyjmuje aliasów `opus`/`sonnet` |
| Preflight | `sprint_run_tick.py preflight` (domyślnie `--runtime claude`) | `sprint_run_tick.py preflight --runtime codex` |
| Źródło stanu sesji | `claude agents --json --all` + `claude logs <id>` | własny rejestr dyspozytora (`sessions.json`), bez wywołania `claude` |
| Start sesji roboczej ticketu | `claude --bg --worktree <KEY> --name <slug>-<KEY>-<model> --model <model> --permission-mode <tryb> "/monolynx:work <KEY>"` wprost z KROKU 4 skilla | `sprint_run_tick.py start --backend codex --slug <slug> --key <KEY> --model <model> --prompt '$monolynx:work <KEY>'` |
| Worktree ticketu | `claude --bg --worktree <KEY>` - nazwę podaje dyspozytor | `codex exec --worktree` - przełącznik bez wartości, katalogiem zarządza Codex; obok niego zostaje `-C <katalog>`, czyli working root sesji wg `codex exec --help` |
| Sprzątnięcie zakończonej sesji | `claude rm <id>` (bez `--discard-unpushed` i `--force-remove-worktree`) | `sprint_run_tick.py stop --backend codex --slug <slug> --key <KEY>` |
| Podłączenie się do sesji czekającej na człowieka | `claude attach <id>` | brak odpowiednika - człowiek czyta plik logu sesji |

Adapter obu backendów siedzi w `plugin/scripts/sprint_run_backend.py` (`session_identity`, `normalize_record`, `load_registry`/`save_registry`, `parse_codex_jsonl`, `preflight`, `find_active_session`, `start`, `status`, `logs`, `resume`, `stop`, `list_sessions`, `classify_normalized`). Tick używa jego części odczytowej (preflight, `status`, `logs`, klasyfikacja przez `classify_normalized`), a sesje Codeksa prowadzi przez podkomendy `start` i `stop`. Sesje `claude` startują **poza** adapterem, wprost w KROKU 4 skilla, i `start --backend claude` celowo kończy się kodem 64: komenda uruchomiona przez `subprocess` skryptu ticku nie przechodzi przez system uprawnień Claude Code, więc `claude --bg` nie może iść tą drogą (MON-191/MON-192).

**Podkomendy `start` i `stop`.** Obie przyjmują `--backend`, `--slug`, `--key` (plus `--model` i `--prompt` w `start`), opcjonalnie `--cwd` (domyślnie katalog bieżący) i `--tick-dir`. Linie wyjścia są zamrożonym kontraktem, asertowanym w testach:

| Linia | Kod | Znaczenie |
|---|---|---|
| `STARTED <identity> <runtime_id> <pid>` | `0` | sesja wystartowała; `<identity>` to `<slug>:<KEY>`, `<runtime_id>` jest identyfikatorem sesji w komentarzu ticketu |
| `ALREADY-RUNNING <identity> <runtime_id>` | `0` | żywa sesja tego ticketu już istnieje - żaden proces nie powstał. To normalny wynik idempotentnego ticku |
| `STOPPED <identity>` | `0` | wpis usunięty z rejestru; `SIGTERM` tylko wtedy, gdy wpis w rejestrze ma stan żywy **i** pod jego PID nadal istnieje proces (patrz cleanup niżej) |
| `NOT-FOUND <identity>` | `0` | w rejestrze nie było takiego wpisu; brak sesji do zatrzymania nie jest błędem |
| komunikat na stderr | `2` | preflight Codeksa nieudany albo katalog ticku niedostępny - bez żadnego efektu ubocznego |
| komunikat na stderr | `64` | błąd użycia: brak wymaganego argumentu, nieznany `--backend`, `start --backend claude` |

`start` jest idempotentny na dwa sposoby naraz: sprawdza własny rejestr **oraz**, best-effort, `claude agents --json --all` (brak binarki `claude` nie jest tu błędem). Żywa sesja ticketu w którymkolwiek backendzie daje `ALREADY-RUNNING`, więc przełączenie `MONOLYNX_SPRINT_RUNTIME` między tickami nie zdubluje pracy.

**Preflight per backend.** `--runtime codex` całkowicie pomija pojęcia Claude Code: nie czyta `.claude/settings.json` ani `.claude/settings.local.json`, ignoruje `MONOLYNX_SPRINT_PERMISSION_MODE` i nie woła `claude --version` ani `claude agents`. Sprawdza `codex --version` oraz `codex login status` (wymagana obecność napisu `Logged in`). Wspólne dla obu runtime zostają: `MONOLYNX_PROJECT_SLUG`, flagi `MONOLYNX_AUTOTEST` / `AUTOCOMMIT` / `AUTOPUSH` (wymagane `true`), ostrzeżenie o `MONOLYNX_AUTOMR` oraz ustalenie `BASE_BRANCH` / `BASE_BRANCH_STALE` - one gatują działania samego ticku (git, testy), niezależnie od backendu. Wyjście preflightu ma dodatkowy klucz `RUNTIME=<claude|codex>`.

**Logi i rejestr.** Oba pliki leżą w katalogu ticku (`<git-dir>/monolynx/tick/`, w zwykłym checkoutcie `.git/monolynx/tick/`), nigdy w `/tmp`:

- rejestr sesji: `sessions.json`, obiekt `{"<slug>:<KEY>": {rekord}}`, zapisywany atomowo (plik tymczasowy + `replace`). Uszkodzony albo brakujący plik jest traktowany jak pusty rejestr, nie jak błąd ticku;
- log sesji Codex: `codex-logs/<slug>-<KEY>-<runtime_id>.jsonl` - stdout `codex exec --json`, czyli JSONL ze zdarzeniami (`thread.started`, `turn.started`, `item.completed`, `turn.completed`);
- stderr tej samej sesji: `codex-logs/<slug>-<KEY>-<runtime_id>.stderr.log`, osobny strumień. Nie jest JSONL-em: niesie kody ANSI i szum klienta MCP, więc klasyfikator go nie parsuje. Zagląda do niego człowiek przy diagnozie.

**Wyjście samego ticku.** `codex exec` bez `--json` wypisuje na stdout wyłącznie finalną odpowiedź agenta, bez kodów ANSI, więc zamrożona linia statusu przechodzi przez `grep -E '^SPRINT-RUN: ...'` bez zdejmowania sekwencji sterujących - inaczej niż `claude logs`, który jest renderem TUI. Cały render klienta (nagłówek sesji, echo promptu, szum klienta MCP, `tokens used`) idzie na stderr z kodami ANSI, a że pętla łączy strumienie (`2>&1 | tee`), ta sama linia trafia do logu dwa razy: raz ze stdout, raz z renderu. Dlatego skrypt bierze ostatnie dopasowanie (`| tail -1`), nie pierwsze. Oba strumienie jednego przebiegu leżą jako `tests/fixtures/codex/exec-plain.0.155.1.stdout.txt` i `tests/fixtures/codex/exec-plain.0.155.1.stderr.txt`.

**Odzyskanie pracy po restarcie.** Rejestr jest czytany z dysku na każdym ticku, więc zatrzymanie i ponowne uruchomienie pętli (albo całej maszyny) nie gubi sesji: wpis w stanie `running` lub `waiting` nadal zajmuje swój ticket. Tożsamość sesji to `<slug>:<KEY>` - **nie zawiera modelu ani backendu**, więc zmiana `MONOLYNX_SPRINT_MODEL` albo przełączenie `MONOLYNX_SPRINT_RUNTIME` między tickami nie sprawi, że żywa sesja przestanie być widoczna i ticket dostanie drugą. Stan wpisu Codex jest przeliczany na starcie raportu: proces żywy (sonda `kill(pid, 0)`) to `running`; proces martwy z `turn.completed` w JSONL to `success`; proces martwy bez `turn.completed` (ubity, padł) to `failure`, nigdy `success`.

**Cleanup dotyka wyłącznie własnych zasobów.** `stop()` usuwa z rejestru **tylko** wpis, którego `runtime_id` i `pid` zgadzają się z rekordem sesji. `SIGTERM` leci wyłącznie pod ten `pid` i tylko wtedy, gdy spełnione są **oba** warunki: wpis **w rejestrze** (nie rekord przekazany przez wywołującego, który bywa nieodświeżony, gdy `stop` woła się bez poprzedzającego `state`) ma stan żywy (`running` albo `waiting`) oraz `is_process_alive(pid)` potwierdza, że pod tym PID nadal siedzi jakiś proces. Stan terminalny (`success`, `failure`) w ogóle nie próbuje `os.kill` - nie ma kogo zatrzymywać, a PID zakończonego procesu mógł już przypaść komuś innemu; stan żywy z martwym PID-em (proces skończył się między ostatnim odświeżeniem rejestru a tym wywołaniem) również nie. Sprzątanie ticketu z werdyktem `done` idzie więc zawsze bez sygnału - zostaje samo usunięcie wpisu. Granica, której ta reguła świadomie nie zamyka: `is_process_alive` to `os.kill(pid, 0)`, czyli stwierdzenie istnienia procesu, nie jego tożsamości, więc PID przejęty w międzyczasie przez **inny żywy** proces przy wpisie wciąż w stanie żywym nadal dostanie `SIGTERM`. Weryfikacja tożsamości wymagałaby czytania `/proc` albo `ps`, czyli kolejnego parsera cudzego wyjścia wraz z wymogiem fixture'u - większa powierzchnia niż zamykany problem. Okno wyścigu jest węższe (liczone od ostatniego odświeżenia stanu), nie zerowe. Wpis podmieniony w międzyczasie przez inny proces zostaje nietknięty, tak samo cudze procesy. Worktree sesji Codeksa zostaje na dysku razem z tym, co w nim leży - dyspozytor nie zna jego ścieżki i nie próbuje jej odgadywać, więc żadne cudze ani brudne drzewo nie ginie przy sprzątaniu. Skrypt nie kasuje worktree ani brudnych zmian: sprzątanie sesji `claude` idzie dalej przez `claude rm` bez `--discard-unpushed` i `--force-remove-worktree`, a odmowa `claude rm` (niewypchnięte zmiany) kończy się pozycją w raporcie do ręcznego sprzątnięcia, nie usunięciem pracy.

**Ograniczenia Codeksa wobec Claude Code** (wszystkie potwierdzone wobec `codex-cli 0.155.1`):

1. **Brak natywnego sygnału "czeka na zgodę".** W Claude Code żywa sesja ma `waitingFor == "permission prompt"`; `codex exec` nie ma odpowiednika, więc `status()` dla Codeksa **nigdy** nie zwraca `waiting`. Jedynym sygnałem oczekiwania na człowieka jest marker tekstowy `SPRINT-RUN STOP:` w treści logu sesji - ten sam, którego skill `work` używa w trybie `MONOLYNX_SPRINT_RUN=1`, wykrywany tak samo dla obu backendów.
2. **`permissionDecision: "ask"` jest pod Codeksem fail-open** (MON-184): hook oznacza się jako failed, a komenda i tak się wykonuje. Mechanizm "ask" realnie chroni tylko w Claude Code. To znana luka, poza zakresem MON-185; częściowym obejściem jest `MONOLYNX_HOOK_ASK_UNSUPPORTED=true`, które zamienia każdą decyzję `ask` na `deny`.
3. **`resume` działa tylko dla Codeksa.** Adapter woła `codex exec resume <thread_id> [prompt] --last`; dla backendu `claude` `resume()` celowo podnosi `NotImplementedError` - dyspozytor nie wznawia sesji Claude (auto-wznowienie z KROKU 3 to start nowej sesji, nie `resume`).
4. **Model nie trafia do `codex exec`.** CLI nie przyjmuje aliasów `opus`/`sonnet`, więc `MONOLYNX_SPRINT_MODEL` i `MONOLYNX_SPRINT_TICK_MODEL` dotyczą wyłącznie backendu `claude`; przy Codeksie służą tylko tożsamości i nazewnictwu, a model wybiera konfiguracja Codeksa.
5. **Worktree ticketu nazywa sam Codex.** Sesja dostaje świeży worktree, tak jak przy backendzie `claude` - ale `codex exec --worktree` jest **przełącznikiem bez wartości** i katalogiem zarządza Codex, podczas gdy `claude --bg --worktree <KEY>` dostaje nazwę od dyspozytora. Dyspozytor nie zna ścieżki tego katalogu i jej nie potrzebuje: `stop` ogranicza się do własnego wpisu w rejestrze i własnego PID, a worktree - cudzych i brudnych również - nie usuwa nigdy. Ślad, po którym człowiek trafi do pracy sesji, to komentarz w tickecie i log JSONL.
6. **Brak `--name`, `--bg` i `codex agents --json`.** `codex exec` nie ma flagi nazywającej sesję ani trybu w tle, a `codex agents` to przeglądarka TUI bez wyjścia JSON. Dlatego dyspozytor prowadzi **własny rejestr** zamiast odpytywać klienta - inaczej niż przy Claude Code, gdzie źródłem prawdy jest `claude agents --json --all`. Dwie konsekwencje: sesja Codeksa wystartowana poza dyspozytorem nie jest dla niego widoczna, a proces sesji nie ma w systemie nazwy, po której rozpoznasz ticket - służy do tego wpis w rejestrze i nazwa pliku logu.

#### Wyniki smoke testu backendów (2026-09-20)

Sprawdzone wersje klientów: Claude Code **2.1.278**, Codex CLI **0.155.1**. Przebieg na macOS, w worktree `MON-185`, bez uruchamiania sesji roboczej ticketu.

| Klient / ścieżka | Sprawdzenie | Wynik |
|---|---|---|
| Claude Code / preflight | `sprint_run_tick.py preflight --runtime claude` | `PREFLIGHT: ok`, kod `0`, `RUNTIME=claude`, `CLAUDE_VERSION=2.1.278 (Claude Code)`; komplet flag `MONOLYNX_AUTO*` odczytany ze środowiska procesu |
| Codex / preflight | `sprint_run_tick.py preflight --runtime codex` | `PREFLIGHT: ok`, kod `0`, `RUNTIME=codex`; `codex --version` i `codex login status` przeszły, żadne wywołanie `claude` ani odczyt `.claude/settings*.json` nie nastąpiło |
| Codex / `codex exec --json` | `codex exec --json --sandbox read-only --skip-git-repo-check -C /tmp/codexprobe "<prompt PONG>"` | Kod `0`, JSONL ze zdarzeniami `thread.started` / `turn.started` / `item.completed` / `turn.completed`; wyjście zapisane jako fixture `tests/fixtures/codex/exec-json.0.155.1.jsonl` i parsowane przez testy `parse_codex_jsonl` |

Oba preflighty są bezskutkowe: nie powstał żaden proces sesji ani wpis w rejestrze. Smoke nie zastępuje pełnego przebiegu sprintu - sprawdza wybór backendu, obecność klienta i kształt wyjścia `codex exec --json`, na którym opiera się klasyfikacja stanu.

Uwierzytelnienie Codeksa sprawdzaj `codex login status` (oczekiwane `Logged in using ChatGPT` przy OAuth). Sandbox sesji Codeksa nadpisuje `MONOLYNX_SPRINT_CODEX_SANDBOX` (`read-only`, `workspace-write`, `danger-full-access`); domyślnie `workspace-write`, czyli odpowiednik `acceptEdits` po stronie Claude. Plugin nigdy nie ustawia domyślnie `--dangerously-bypass-approvals-and-sandbox` ani `--approve-for-me`.

### Uprawnienia ticku i preflight

Tick w `claude -p` nie ma komu zadać pytania o zgodę, więc `sprint_run.sh` przekazuje mu uprawnienia sam, niezależnie od `permissions` w settings projektu:

- `--allowedTools` z listą `Bash(claude:*),Bash(MONOLYNX_SPRINT_RUN=1 claude:*),Bash(python3:*),Read(~/.claude/plugins/**),mcp__monolynx,mcp__claude_ai_Monolynx,mcp__plugin_monolynx_monolynx`. Reguła `Bash(MONOLYNX_SPRINT_RUN=1 claude:*)` pokrywa komendę startu sesji roboczej z prefiksem zmiennej (MON-207). Wpis `mcp__<serwer>` zezwala na wszystkie narzędzia serwera; są trzy prefiksy, bo nazwa serwera zależy od sposobu instalacji (plugin, connector claude.ai, `.mcp.json`). `Read` jest zawężony do `~/.claude/plugins/**`, bo gołe `Read` dawałoby tickowi odczyt całego dysku, w tym `~/.ssh`; odczyt w katalogu projektu Claude Code pozwala domyślnie. Całą listę zastępuje `MONOLYNX_SPRINT_TICK_ALLOWED_TOOLS` ze środowiska procesu; linia `start:` w logu pętli podaje tylko `allowed_tools=default` albo `allowed_tools=custom`.
- `--permission-prompts none`: wszystko, co wymagałoby potwierdzenia, jest odrzucane od razu i widać to w logu, zamiast wisieć bez odpowiedzi.

Stąd wymóg, żeby tick nie wołał gołego shella. Cała logika ticku, która wcześniej była inline bashem w skillu, siedzi w jednym skrypcie `sprint_run_tick.py` (katalog `scripts/` skilla `sprint-run`), wołanym przez `python3`. Komendy, które skrypt uruchamia przez `subprocess`, nie przechodzą przez uprawnienia Claude Code.

`sprint_run_tick.py preflight` to pierwszy krok każdego ticku. Zbiera wszystkie problemy naraz, zamiast przerywać na pierwszym. Punkty 1, 3 i 4 dotyczą wyłącznie backendu `claude`; przy `--runtime codex` zastępuje je `codex --version` i `codex login status` (patrz sekcja „Backend sesji”), a punkty 2, 5 i 6 obowiązują tak samo dla obu:

1. `MONOLYNX_SPRINT_PERMISSION_MODE` (domyślnie `auto`, gdy zmienna nie jest ustawiona) nie jest `bypassPermissions` ani nie zawiera `--dangerously-skip-permissions`. Ten tryb sesji `--bg` w KROK 4 trafia w sesji auto na domyślną regułę soft_deny "Create Unsafe Agents" - bez tego checku tick przechodził resztę preflightu i odpadał dopiero przy starcie sesji, bez startu żadnej sesji (19.09, MON-191/MON-192). Poprawka: `MONOLYNX_SPRINT_PERMISSION_MODE=auto` w `env` śledzonego `.claude/settings.json`, albo dla zespołów, które celowo zostają przy `bypassPermissions`, świadoma reguła w `autoMode.allow` w `~/.claude/settings.json` (`autoMode` czyta wyłącznie ten plik, ustawienia zarządzane albo `--settings` - nigdy pliki projektu). `allow` to lista opisów w wolnym tekście dla klasyfikatora, np. `"Sprint-run background sessions in <repo>: the user intentionally starts ticket sessions via \`claude --bg --worktree <KEY> --name <slug>-<KEY>-<model> --model <model> --permission-mode auto \"/monolynx:work <KEY>\"\`. This is the monolynx:sprint-run dispatcher and is not Create Unsafe Agents, as long as the permission mode is auto (never bypassPermissions)."`, nie strukturalny JSON z nazwami narzędzi;
2. katalog projektu jest repozytorium git;
3. `claude --version` i `claude agents --json --all` odpowiadają (drugie zwraca listę JSON);
4. `.claude/settings.json` i `.claude/settings.local.json`, o ile istnieją, są poprawnym obiektem JSON, `permissions` jest obiektem z listami napisów w `allow` / `deny` / `ask`, a `env` jest obiektem. Tryb `-p` po cichu pomija plik, który nie przechodzi walidacji (`claude --help`, opis `-p`), a razem z nim jego `env`, hooki i model; preflight zamienia tę ciszę na błąd z nazwą pliku i powodem (przy błędzie składni z linią i kolumną);
5. `MONOLYNX_PROJECT_SLUG` jest niepusty, a `MONOLYNX_AUTOTEST`, `MONOLYNX_AUTOCOMMIT` i `MONOLYNX_AUTOPUSH` są równe `true` w **środowisku procesu**, nie w pliku. Flaga obecna w `env` pliku settings, której proces nie widzi, dostaje dopisek, że plik mógł zostać odrzucony. `MONOLYNX_AUTOMR` różne od `true` daje tylko ostrzeżenie;
6. `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` w środowisku procesu (po obcięciu białych znaków) nie są obie ustawione na różne branche. Rozbieżne wartości to jedna linia `ERROR:` z nazwami obu zmiennych (MON-219): sesje startowałyby z innego brancha niż ten, do którego wracają ich MR-y. Jedna zmienna albo obie równe przechodzą. Samo ustalenie `BASE_BRANCH` / `BASE_BRANCH_STALE` nadal nigdy nie trafia do problemów.

Wynik to linie `KLUCZ=wartość` (slug, tryb, sloty, limit sesji `waiting` `MAX_WAITING`, model, tryb uprawnień sesji, flagi AUTO*, wersja `claude`, katalog projektu, katalog ticku, branch bazowy i jego aktualność oraz `RUNTIME`), potem `WARN:` i `ERROR:`, na końcu `PREFLIGHT: ok` albo `PREFLIGHT: failed problems=<n>`. Przy `--runtime codex` kształt wyjścia jest ten sam, ale `CLAUDE_VERSION` jest puste (komenda nie została uruchomiona), a `PERMISSION_MODE` wypisane tylko informacyjnie - nic nie gatuje. Skrypt nie wypisuje żadnej innej zmiennej środowiska ani całego `env` z plików settings. Przy `failed` tick nie startuje żadnej sesji; w trybie `batch` kończy się linią `SPRINT-RUN: remaining=-1 running=0 waiting=0 done=0`.

**Punkt startu przed dispatchem.** `claude --bg --worktree` zakłada worktree ticketu z HEAD-a głównego checkoutu dyspozytora, więc przestarzały checkout daje przestarzały punkt startu każdej sesji wystartowanej w tym ticku. `sprint_run_tick.py sync-base --base-branch <B> [--project-dir <katalog>]` wywołuje KROK 4 skilla, gdy preflight zgłosi `BASE_BRANCH_STALE=true`. Działa tylko wtedy, gdy główny checkout stoi na `<B>` i śledzone pliki są czyste: `git fetch origin <B>`, potem `git merge --ff-only origin/<B>`. Nigdy nie robi reset, rebase ani checkout. Wypisuje jedną zamrożoną linię `SYNC-BASE: ok|skipped|failed reason=<powód>`: `ok` (`up-to-date`, `fast-forwarded`) - tick startuje sesje normalnie; `skipped` (`not-on-base-branch`, `dirty-tracked-files`, `local-ahead`, `diverged`) i `failed` (`not-a-git-repo`, `git-error`, `fetch-failed`, `merge-failed`) - dotychczasowe zachowanie: ostrzeżenie i dispatch w trybie interaktywnym, `SPRINT-RUN STOP` w trybie `batch`. Kody wyjścia: `0` dla `ok`/`skipped`, `2` dla `failed`, `64` dla błędu użycia.

Pliki stanu ticku (lista ticketów, `claude agents --json --all`, ogony logów sesji, a dla backendu `codex` rejestr `sessions.json` i katalog `codex-logs/`) lądują w `.git/monolynx/tick/`, obok logu pętli, a nie w `/tmp`: katalog tymczasowy jest poza zapisem ticku, a `.git/` nigdy nie trafia do commita. Każdy tick nadpisuje te pliki; rejestr sesji jest wyjątkiem - trwa między tickami, bo to z niego dyspozytor odzyskuje wiedzę o żywych sesjach po restarcie.

> **Nie wypisuj środowiska.** Ani w ticku, ani przy diagnozie: `printenv`, `env`, `set`, `export -p` i `declare -x` wypisują wszystkie zmienne procesu, w tym klucze API i tokeny, do transkryptu i do logu pętli. Konfigurację sprawdzaj przez `sprint_run_tick.py preflight`, który pokazuje wyłącznie zmienne `MONOLYNX_*` potrzebne tickowi.

### Flagi skryptu

| Flaga | Domyślnie | Znaczenie |
|---|---|---|
| `--interval` | `15m` | Odstęp między tickami (`15m`, `900`, `1h`) |
| `--slots` | `$MONOLYNX_SPRINT_PARALLEL` albo `2` | Liczba równoległych sesji roboczych `running`; sesje `waiting` mają osobny limit `MONOLYNX_SPRINT_MAX_WAITING` (domyślnie `2`, w logu startu pętli jako `max_waiting=`) |
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

Kody `64`, `65`, `69`, `75` i `78` to błędy startu (nieznany argument, flaga `--interval`, `--slots`, `--max-ticks` bez wartości albo `MONOLYNX_SPRINT_RUNTIME` spoza `claude`/`codex`; uruchomienie spoza repozytorium git; brak binarki wybranego runtime w `PATH`; pętla już działa dla tego repozytorium; brak sluga projektu).

### Wymagane flagi trybu w tle

Tryb `-p` nie odpowie na żadne pytanie, dlatego sesje w tle muszą mieć komplet flag w `.claude/settings.json` projektu (pole `env`, nie tylko eksport w shellu - sesja dziedziczy środowisko, ale nie Twój terminal):

```json
{
  "env": {
    "MONOLYNX_PROJECT_SLUG": "twoj-projekt",
    "MONOLYNX_CONTRACT": "auto",
    "MONOLYNX_AUTOTEST": "true",
    "MONOLYNX_AUTOCOMMIT": "true",
    "MONOLYNX_AUTOPUSH": "true",
    "MONOLYNX_AUTOMR": "true"
  }
}
```

`AUTOTEST`, `AUTOCOMMIT` i `AUTOPUSH` są wymagane - bez nich preflight dyspozytora zatrzymuje tick i nie startuje żadnej sesji. `AUTOMR` jest zalecane: bez niego branch jest wypchnięty, ale merge request tworzysz ręcznie. Projekt, który merguje tickety do innego brancha niż domyślny branch repozytorium (np. `develop`), ustawia też `MONOLYNX_MR_TARGET` - jest on wtedy zarazem branchem bazowym sesji roboczych. `MONOLYNX_BASE_BRANCH` jest zbędna; jeśli ją ustawisz, musi wskazywać ten sam branch, bo różne `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` to `ERROR` w preflighcie. Checkout, z którego startuje tick, musi stać na tym samym branchu bazowym: `claude --bg --worktree` zakłada worktree z HEAD-a checkoutu, więc rozjazd dałby branche robocze z niewłaściwego punktu startu. Checkout stojący na branchu bazowym, ale za `origin`, tick dogania sam fast-forwardem (`sync-base`, patrz „Punkt startu przed dispatchem”); checkoutu na innym branchu nie przełącza. Skrypt `sprint_run.sh` ustawia te flagi sam dla procesów, które uruchamia; każdą można nadpisać w środowisku. Poza nimi eksportuje też `MONOLYNX_SPRINT_RUN_MODE=batch` (tick wie, że jego raportu nikt nie przeczyta) i `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` (domyślnie `1`).

Plik z flagami musi być **śledzony przez gita**. Sesja robocza dostaje worktree utworzony przez `claude --bg --worktree`, a w worktree trafia tylko to, co git zna: `.claude/settings.local.json` jest ignorowany, więc ani uprawnienia, ani flagi AUTO* z niego nie docierają do sesji w tle. Druga poprawna droga to środowisko procesu, który odpala tick - `sprint_run.sh` eksportuje flagi sam, a `claude --bg` dziedziczy środowisko.

Trzech zmiennych nie wpisujesz do śledzonego pliku (MON-207). `MONOLYNX_SPRINT_RUN=1` dostaje wyłącznie sesja robocza: dyspozytor dokleja ją jako prefiks komendy startu (`MONOLYNX_SPRINT_RUN=1 claude --bg ...`), a `sprint_run.sh` eksportuje ją dla ticku. W śledzonym pliku przełączyłaby w tryb sesji w tle każdą zwykłą sesję po merge do `main` (`bash_guard.py` zamienia wtedy `ask` na `deny`). `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` ustawiasz na czas sprintu w środowisku procesu dyspozytora albo w `.claude/settings.local.json` jego checkoutu. `scripts/validate_plugin_consistency.py --release` odrzuca śledzony `.claude/settings.json` z którąkolwiek z tych trzech zmiennych w `env`.

Tick w pętli `sprint_run.sh` dostaje uprawnienia z `--allowedTools`, więc blok `permissions` nie jest mu potrzebny. Tick interaktywny (`/loop 15m /monolynx:sprint-run` albo ręczne `/monolynx:sprint-run`) korzysta z uprawnień sesji; żeby nie pytał przy każdym ticku, dopisz do śledzonego `.claude/settings.json` poprawny blok `permissions`:

```json
{
  "env": {
    "MONOLYNX_PROJECT_SLUG": "twoj-projekt",
    "MONOLYNX_CONTRACT": "auto",
    "MONOLYNX_AUTOTEST": "true",
    "MONOLYNX_AUTOCOMMIT": "true",
    "MONOLYNX_AUTOPUSH": "true",
    "MONOLYNX_AUTOMR": "true"
  },
  "permissions": {
    "allow": [
      "Bash(claude:*)",
      "Bash(MONOLYNX_SPRINT_RUN=1 claude --bg:*)",
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

Narzędzie zwraca `skills: [{name, files: [{relative_path, content}]}]`. **Zapisz wszystkie pliki z listy `files`** - skill może mieć pliki towarzyszące obok `SKILL.md` (np. `monolynx-work` ma `pipeline.md`, `review-rubric.md`, `lint-test.md` i `commit-mr.md`, a `monolynx-work-simple` własne kopie `review-rubric.md`, `lint-test.md` i `commit-mr.md`); pominięcie ich okroi skill o część instrukcji.

Kopie plików wspólnych w `monolynx-work-simple` - rubryki krytyka oraz procedur lintu i testów (`lint-test.md`) i commita, pusha i MR (`commit-mr.md`) - są **generowane** ze źródeł w `plugin/skills/work/` (patrz sekcja „Rubryka krytyka”), więc instalacja samego `work-simple` - bez `work` - zawiera pełną rubrykę i obie procedury, i nie zależy ani od innych skilli, ani od zmiennej `CLAUDE_PLUGIN_ROOT` (poza Claude Code jest pusta).

Serwer MCP podłącza się poza skillami:

- **Codex**: konfiguracja MCP i logowanie OAuth opisane w sekcji instalacji; token API pozostaje opcją dla konfiguracji ręcznej.
- **Cursor**: `.cursor/mcp.json` w projekcie lub `~/.cursor/mcp.json` globalnie, klucz `mcpServers` - ten sam kształt co `.mcp.json` pluginu.

Definicje `plugin/agents/` (i ich kopie w `.claude/agents/`) są formatem Claude Code. W każdym kliencie skille sprawdzają faktycznie dostępne mechanizmy delegowania i role projektu. Sam napis „Codex” nie oznacza braku subagentów ani dostępności określonego modelu. Przeniesienie konfiguracji agentów i hooków między klientami nie jest częścią kontraktu runtime - każdy klient ładuje je po swojemu i wymaga własnej decyzji o zaufaniu. Nie dotyczy to trwałych reguł projektu: te leżą w repozytorium i czyta je każdy klient (patrz „Reguły i pamięć projektu”), więc niczego nie trzeba migrować.

### Serwer MCP bez zmian funkcjonalnych

Plugin nie modyfikuje serwera ani nie odwołuje się do jego wewnętrznych funkcji. Deklaruje wyłącznie dostęp (HTTP + OAuth) do **istniejącego** serwera MCP pod adresem `https://monolynx.com/mcp`. Cała logika narzędzi (Scrum, 500ki, Monitoring, Wiki, Połączenia, Plan pracy, w tym samo `install_monolynx_skills`) działa po stronie serwera, niezależnie od tego, czy łączysz się przez plugin, czy w inny sposób.

## Praca nad pluginem: dyspozytor na pluginie z brancha sprintu

Dotyczy repozytorium samego pluginu (dogfooding), nie projektów, które plugin tylko używają. Zainstalowany plugin to kopia w cache (`~/.claude/plugins/cache/monolynx/monolynx/<wersja>/`), więc poprawka autopilota zmergowana do brancha sprintu działa dopiero po release i aktualizacji pluginu. Przykład: `sync-base` (MON-142, MON-219) był już na `sprint/cli-monolynx`, ale dyspozytor chodził na zainstalowanym 1.21.4 bez tej zmiany, więc przez całą noc preflight zgłaszał `BASE_BRANCH_STALE=true`, a sesje startowały z przestarzałego HEAD-a.

Rozwiązanie: dyspozytor ładuje plugin z katalogu `plugin/` **głównego checkoutu repozytorium**, tego, który stoi na branchu sprintu i który `sync-base` przewija fast-forwardem. Nie wskazuj worktree ticketu. Ścieżka ma być absolutna i prowadzić do katalogu `plugin` (tam leży `.claude-plugin/plugin.json`), nie do korzenia repozytorium. W przykładach niżej `/sciezka/do/repo` to ten checkout.

### Przełączenie

Claude Code ma dwa mechanizmy. Oba działają tylko na proces, który je dostał, i niczego nie zapisują w konfiguracji.

| Mechanizm | Zasięg | Do czego |
|---|---|---|
| Zmienna `CLAUDE_CODE_PLUGIN_DIRS=<katalog>` | proces i procesy, które z niego startują (także `claude --bg`) | dyspozytor: pętla `sprint_run.sh` i interaktywny `/loop` |
| Flaga `--plugin-dir <katalog>` | tylko ta jedna sesja | ręczny test skilla, interaktywny `/monolynx:mr-queue` |

**Pętla skryptowa** (tick w świeżym `claude -p`, `sprint_run.sh` bez zmian):

```bash
CLAUDE_CODE_PLUGIN_DIRS=/sciezka/do/repo/plugin plugin/scripts/sprint_run.sh --interval 15m --slots 2
```

**Dyspozytor interaktywny**: uruchom sesję z wyeksportowaną zmienną, a w niej pętlę jak zwykle:

```bash
CLAUDE_CODE_PLUGIN_DIRS=/sciezka/do/repo/plugin claude
```

```
/loop 15m /monolynx:sprint-run
```

W tej samej sesji działa też `/loop 10m /monolynx:mr-queue`.

**Pojedyncza sesja** (udokumentowana flaga z `claude --help`; flaga stoi przed podkomendą i przed `--bg`):

```bash
claude --plugin-dir /sciezka/do/repo/plugin
```

Flaga **nie przechodzi** do sesji w tle. Dyspozytor uruchomiony z `--plugin-dir` startuje sesje `/monolynx:work` przez `claude --bg ...` bez tej flagi (KROK 4 skilla `sprint-run`), a te dostają plugin z cache. Sesja z `--plugin-dir` nie eksportuje też żadnej zmiennej. Do dyspozytora służy więc wyłącznie zmienna.

Zmiennej nie wpisuj do śledzonego `.claude/settings.json`: ścieżka absolutna jest inna na każdej maszynie, a efekt miałby każdy, kto sklonuje repo. Jej miejsce to środowisko procesu, który startuje dyspozytora, tak jak `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` (patrz „Wymagane flagi trybu w tle”).

### Jak sprawdzić, że działa

```bash
CLAUDE_CODE_PLUGIN_DIRS=/sciezka/do/repo/plugin claude plugin list --json
```

Na liście są wtedy dwa wpisy pluginu: `monolynx@monolynx` z `installPath` w cache i `monolynx@inline` ze `scope: "session"` i `installPath` równym katalogowi z checkoutu. Pole `version` obu wpisów może być identyczne (manifest na branchu sprintu nosi numer ostatniego wydania, bo numer nadaje mergujący), więc rozstrzyga `installPath`. W sesji skill wypisuje przy wywołaniu `Base directory for this skill: /sciezka/do/repo/plugin/skills/sprint-run`; bez zmiennej i bez flagi ścieżka prowadzi do cache.

### Powrót do zainstalowanego pluginu

```bash
unset CLAUDE_CODE_PLUGIN_DIRS
```

Nową sesję uruchom bez `--plugin-dir`. Nic więcej nie trzeba cofać, bo żaden z mechanizmów nie zmienił konfiguracji. Sesje w tle wystartowane ze zmienną zostają na kodzie z checkoutu do swojego końca. Bez zmiennej i bez flagi skille idą z cache zainstalowanej wersji.

### Kiedy tak, kiedy nie

Dyspozytor chodzi wtedy na kodzie niezreleasowanym i, co jest celem, zmieniającym się między tickami: każdy fast-forward `sync-base` podmienia skille, skrypty i hooki pod działającą pętlą. Sesja robocza ticketu dostaje kod z głównego checkoutu, nie ze swojego worktree, więc niezmergowana zmiana ticketu nie steruje jego własną sesją.

Tak, gdy:

- branch sprintu niesie poprawki autopilota, na które czekasz (jak MON-142, MON-219, MON-220);
- CI brancha sprintu jest zielone, a `python3 scripts/validate_plugin_consistency.py` przechodzi;
- człowiek jest w pobliżu albo dyspozytor chodzi interaktywnie.

Nie, gdy:

- CI brancha sprintu jest czerwone;
- sprint zmienia `hooks/bash_guard.py` (to on pilnuje autonomii sesji w tle) albo `sprint_run.sh` / `sprint_run_state.py`, a pętla ma chodzić nocą w trybie batch bez człowieka;
- chodzi o obcy projekt: tam zostaje zainstalowany plugin.

Po sprincie wróć do zainstalowanego pluginu. Release nadaje mergujący jak dotąd (`/monolynx:sprint-end`, job `version-bump`, patrz „Wersjonowanie pluginu”); dogfooding nie zmienia procesu wersjonowania.

### Ograniczenia i to, czego nie sprawdzono

Sprawdzone na Claude Code **2.1.284**, 2026-09-29:

- `--plugin-dir` ładuje `/monolynx:help`, `/monolynx:sprint-run` i `/monolynx:mr-queue` z checkoutu mimo włączonego `monolynx@monolynx` 1.21.4; działa też z `--bg` (flaga przed `--bg`);
- `claude -p` z `--plugin-dir`, który sam uruchomił `claude --bg` bez flagi, dał dziecku skill z cache;
- `claude -p` z `CLAUDE_CODE_PLUGIN_DIRS` ładuje `sprint-run` z checkoutu, a `claude --bg` bez flagi, uruchomiony z shella z wyeksportowaną zmienną, dostał skill z checkoutu.

Ryzyka i luki:

- **`CLAUDE_CODE_PLUGIN_DIRS` jest nieudokumentowana**: nie ma jej w `claude --help`, znaleziono ją w binarce. Nowsza wersja CLI może ją zmienić albo usunąć; po aktualizacji Claude Code sprawdź ją ponownie komendą z „Jak sprawdzić, że działa”.
- Pełny łańcuch jednym przebiegiem (tick ze zmienną -> `claude --bg` -> sesja `/monolynx:work`) nie był sprawdzony; oba ogniwa sprawdzono osobno.
- Wariant interaktywny wynika z tego samego mechanizmu, ale sprawdzono tylko `-p` i `--bg`. Nie sprawdzono też, czy długa sesja interaktywna widzi skille zmienione przez fast-forward bez restartu; pętla `sprint_run.sh` nie ma tego problemu, bo każdy tick to świeży proces.
- Separator kilku ścieżek w zmiennej nie jest znany; podawaj jedną.
- **Podwójne hooki**: przy zmiennej albo fladze oba wpisy (`monolynx@monolynx` i `monolynx@inline`) są `enabled`. Nie sprawdzono, czy `bash_guard.py` i `brief_guard.py` odpalają się wtedy dwa razy. To kolejny powód, żeby nie puszczać tak nocnego batcha bez człowieka.

### Odradzane: lokalny marketplace

`claude plugin marketplace add /sciezka/do/repo` i `claude plugin install monolynx@monolynx` (sprawdzone w izolowanym `CLAUDE_CONFIG_DIR`) nie nadają się do dogfoodingu. Manifest repozytorium nosi nazwę `monolynx`, tę samą co istniejący marketplace git, więc na prawdziwej konfiguracji koliduje z nim. Instalacja robi kopię do cache, a po zmianie w checkoucie ani `claude plugin update monolynx@monolynx` („already at the latest version”), ani `claude plugin marketplace update monolynx` nie odświeżają kopii; pomaga dopiero odinstalowanie i ponowna instalacja. Powrót wymaga ponownego dodania marketplace git.

## Wersjonowanie pluginu

Tickety sprintu pracują równolegle i prawie każdy zmienia plugin. Gdyby każdy podbijał wersję we własnym branchu, każdy merge do `main` kończyłby się konfliktem w manifestach i changelogu oraz ręcznym przenumerowaniem. Dlatego numer wersji nadaje ten, kto merguje, a nie ticket. Pełna reguła: `.claude/rules/plugin-version-bump.md`.

1. **Ticket** dopisuje wpis pod nagłówkiem `### Unreleased` na początku sekcji `## Changelog` (tworzy go, jeśli go nie ma). Każdy punkt najwyższego poziomu zaczyna się od klucza ticketu: `- MON-123: opis`; wcięte kontynuacje są bez klucza. Manifestów `plugin/.claude-plugin/plugin.json` i `plugin/.codex-plugin/plugin.json` ticket nie zmienia. Skille `work` i `work-simple` wklejają tę regułę do promptu każdego developera (blok `changelog-unreleased`).
2. **Mergujący** zamienia `### Unreleased` na `### X.Y.Z` i ustawia tę samą wartość w obu manifestach, w jednym commicie. Minor, gdy od ostatniego wydania doszedł nowy skill, hook albo skrypt; patch w pozostałych przypadkach. Robi to człowiek przy merge do `main` (na branchu MR, przed merge) albo `/monolynx:sprint-end`, job `version-bump`: proponuje numer z uzasadnieniem, pyta o zgodę (w sesji w tle bierze numer z reguły bez pytania), przygotowuje branch `release/plugin-X.Y.Z` w osobnym worktree, sprawdza go walidatorem w trybie wydania i commituje, pushuje i zakłada MR zgodnie z flagami `MONOLYNX_AUTOCOMMIT`, `MONOLYNX_AUTOPUSH` i `MONOLYNX_AUTOMR`.
3. **Wyjątek**: ticket, który sam jest wydaniem (hotfix mergowany od razu, poza sprintem), może nadać numer, jeśli jest jedynym otwartym MR-em zmieniającym plugin. Team Manager zapisuje tę decyzję w komentarzu planu.

`scripts/validate_plugin_consistency.py` (job CI `plugin-consistency`) rozróżnia dwa tryby:

| Tryb | Kiedy | `### Unreleased` jako pierwszy wpis |
|---|---|---|
| Branch roboczy | domyślnie, np. pipeline MR | dozwolony; `version` w manifeście musi się równać pierwszemu numerycznemu `### X.Y.Z` pod nim (ostatnio wydanej wersji) |
| Wydanie | flaga `--release` albo `CI_COMMIT_BRANCH=main` | błąd „wydanie bez numeru” |

W obu trybach błędem jest `### Unreleased` na innej pozycji niż pierwsza, pusta sekcja `### Unreleased` i punkt najwyższego poziomu bez klucza ticketu.

`plugin-consistency` chodzi także na `main` (tryb wydania), więc to, gdzie mergują się tickety, decyduje o tym, kto i kiedy nadaje numer:

- **Branch integracyjny** (`MONOLYNX_MR_TARGET`, np. `sprint/<nazwa>`; docelowy tryb dyspozytora `sprint-run`): MR-y ticketów są zielone, `### Unreleased` zbiera wpisy całego sprintu, a `sprint-end` (job `version-bump`) nadaje numer na branchu integracyjnym, zanim ten trafi do `main`. `main` nigdy nie widzi `### Unreleased`.
- **Merge ticketu wprost do `main`**: numer nadaje mergujący na branchu MR, przed merge (dziś człowiek; `mr-queue` tego nie robi). Merge bez numeru czerwieni pipeline `main`, a `sprint-end` z branchem docelowym `main` działa wtedy jako siatka: przygotowuje MR wydania po fakcie.

## Changelog

### Unreleased

- MON-233: **`/monolynx:mr-queue` domyka pozycję na branchu integracyjnym od razu po merge i sam anuluje pipeline tego brancha**. Zastępuje tor `verifying` z MON-221 dla nowych merge'y. Po udanym merge MR/PR z zielonym CI na branch integracyjny (branch docelowy różny od domyślnego brancha repozytorium, np. `sprint/*`) ten sam tick wykonuje Domknięcie: ticket -> `done`, fast-forward lokalnego brancha docelowego (`target-sync`), pozycja -> `done`. Komentarz na tickecie podaje pipeline MR/PR, nie pipeline brancha.
  - Anulowanie pipeline'u brancha integracyjnego wywołanego pushem merge jest częścią tego samego działania (`glab ci cancel pipeline <id>`, na GitHubie `gh run cancel <databaseId>`, osobno dla każdego id). Brak pipeline'u, błąd odczytu albo błąd anulowania to ostrzeżenie w raporcie ticka, nie zatrzymanie kolejki ani blokada Domknięcia.
  - To samo dotyczy MR/PR zmergowanego poza kolejką (werdykt `merged`, 4c) na branch integracyjny: anulowanie pipeline'u brancha i Domknięcie w tym samym ticku. CI MR niezielone albo nieustalone nie wstrzymuje wtedy domknięcia (merge wykonał człowiek), a komentarz i raport mówią to wprost.
  - `mr_queue_state.py post-merge --target-branch T --default-branch D` dla brancha integracyjnego (T i D niepuste i różne) zwraca `{"status": "done", "close_now": true, "reason"}`, a dla brancha domyślnego, pustego albo nieustalonego `{"status": "merging", "close_now": false, "reason"}`. Statusu `verifying` już nie nadaje.
  - Nowa podkomenda `mr_queue_state.py target-cancel` (`--provider glab --pipelines-file P` albo `--provider gh --runs-file R`) zwraca `{"cancel": [id...], "skipped": [{"id", "status"}], "reason"}`: do anulowania wybiera wyłącznie pipeline'y / runy commita merge w statusie aktywnym, każdy inny i nieznany status trafia do `skipped` (anulowanie jest nieodwracalne, więc nieznany literał to wariant odwracalny). Pusta lista daje `cancel: []` i kod 0.
  - KROK 3.0 nie sprawdza już CI commita merge (weryfikacja wsteczna znika). Zostaje jako domykanie pozycji `verifying` zastanych w istniejącym pliku kolejki: pierwszy tick domyka je tym samym Domknięciem, bez `target-ci` i bez czekania na CI brancha. Status jest nadal rozpoznawany przy odczycie pliku (pole `verifying` w `queue-show`), ale nowe merge go nie nadają.
  - Żaden nowy merge nie nadaje już `stop_reason=target_ci_failed` (ustawiał go wyłącznie stary KROK 3.0), więc `halted` dotyczy tylko pozycji `stopped` z `target_ci_failed` zastanej w pliku kolejki po torze 1.22.0; taka pozycja nadal blokuje kolejkę do `queue-update --ref <ref> --status closed`.
  - Merge na domyślny branch (`main`) bez zmian: pozycja `merging`, czekanie na zielone CI brancha docelowego w 3a, ticket na `done` dopiero po nim; czerwone CI ustawia `stopped` bez `stop_reason`, jak dotąd. Zamrożona linia `MR-QUEUE:` bez zmian.
  - WHY: pipeline'y brancha sprintu są anulowane po każdym merge, bo job `clean-code` ma `resource_group` i pipeline brancha (15-20 min) blokował pipeline następnego MR. Anulowany pipeline dawał w `target-ci` `needs_human`, pozycja zostawała `verifying` na stałe i ticket nie dostawał `done` (MON-221..MON-227 domykano ręcznie). Całość weryfikuje MR brancha sprintu do `main`.

### 1.22.0

- MON-228: **`mr_queue_state.py queue-set` nie gubi pozycji zatrzymującej kolejkę**. Poza pozycjami `verifying` zachowuje też pozycje `stopped` ze `stop_reason` `target_ci_failed` spoza nowej listy (na początku kolejki, w dotychczasowej kolejności), więc ponowne `queue-set` nie zdejmuje blokady czerwonego brancha integracyjnego (`halted` zostaje). `stopped` z innym powodem albo bez powodu nadal wypada.
- MON-228: **`/monolynx:mr-queue`, KROK 2: pusty `order` z pozycjami `verifying` idzie do KROK 3.** Wcześniej tick kończył się od razu z `remaining=0` i weryfikacja wsteczna (3.0) nie startowała, przez co zmergowane MR-y czekały z ticketem na `done`; teraz `remaining=0` i koniec ticka następuje dopiero bez pozycji `verifying`.
- MON-142: **`mr-queue` przyjmuje kolejność sama i uzupełnia kolejkę**. Bez argumentów i przy pustym `next` tryb interaktywny nie pyta już "ENTER = ta kolejność": przyjmuje `order` z `queue-propose` jak tryb batch, zapisuje go przez `queue-set`, wypisuje przyjętą kolejkę i pominięte z powodem, i w tym samym ticku przechodzi do KROK 3 (ułożenie kolejki nie jest działaniem ticku). Przy niepustym `next` nowa podkomenda `mr_queue_state.py queue-append --queue-file F --provider glab|gh --list-file L` dopisuje na koniec otwarte MR/PR, których w kolejce nie ma (kolejność i pominięcia jak w `queue-propose`, pozycje obecne w dowolnym statusie zostają nietknięte). Argumenty skilla nadal nadpisują kolejność.
- MON-142: **`mr-queue` kończy tick raportem `Potrzebne:` zamiast pytania "co dalej?"** przy czerwonym CI brancha docelowego, werdykcie `needs_human` (nowa sekcja 4d z tabelą powód -> działanie człowieka), limicie 2 rund poprawek i sprzecznym konflikcie. Raport mówi, co trzeba zrobić i jaką komendą `queue-update` wrócić do kolejki; tryb batch nadal kończy się `MR-QUEUE STOP`. Bez zmian: pytanie o approve (4a), merge bez `MONOLYNX_AUTOMERGE` i zgoda na push do brancha innego autora.
- MON-142: **`sprint-run` sam przewija przestarzały główny checkout**. Nowa podkomenda `sprint_run_tick.py sync-base --base-branch <B> [--project-dir <katalog>]`: tylko na checkoucie stojącym na `<B>` z czystymi śledzonymi plikami robi `git fetch origin <B>` i `git merge --ff-only origin/<B>`, nigdy reset, rebase ani checkout, i wypisuje zamrożoną linię `SYNC-BASE: ok|skipped|failed reason=<powód>` (kod 0 dla `ok`/`skipped`, 2 dla `failed`, 64 przy błędzie użycia). KROK 4 skilla woła ją przy `BASE_BRANCH_STALE=true`; na `ok` dispatch idzie normalnie, w pozostałych przypadkach zachowanie jak dotąd (ostrzeżenie interaktywnie, `SPRINT-RUN STOP` w batch).
- MON-219: **lokalny branch docelowy nadąża za merge, a `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` wskazują jeden branch**.
  - `/monolynx:mr-queue`, KROK 3a: po zielonym CI brancha docelowego, przed `queue-update --status done`, domknięcie pozycji przesuwa lokalny branch docelowy w głównym checkoucie. Nowa podkomenda `mr_queue_state.py target-sync --target-branch T --current-branch C --ahead N --behind M --dirty true|false` zwraca `{"action", "reason", "commands", "manual"}`: `none` (aktualny), `ff-merge` (`git merge --ff-only origin/T`, checkout stoi na `T` i jest czysty), `ff-fetch` (`git fetch origin T:T`, checkout na innym branchu) albo `skip` (lokalne commity albo niezapisane zmiany; `manual` niesie komendę dla człowieka). Każdy błąd fetch, porównania albo samej komendy to ostrzeżenie w raporcie ticka, nie zatrzymanie kolejki. To wyjątek od zasady "checkout użytkownika zostaje nietknięty", ograniczony do fast-forwardu. Zamrożona linia `MR-QUEUE:` bez zmian.
  - `/monolynx:sprint-run`, KROK 4: przy `BASE_BRANCH_STALE=true` tick najpierw woła `sprint_run_tick.py sync-base` (kontrakt `--base-branch <B>` i linia `SYNC-BASE: ok|skipped|failed reason=<powód>` z MON-142; MON-219 wprowadzało tę samą podkomendę równolegle i przy merge przyjęło kontrakt z MON-142). Checkoutu na innym branchu niż bazowy nie zmienia (`skipped reason=not-on-base-branch`), bo worktree sesji powstaje z HEAD-a.
  - Różne `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` (obie ustawione) to `ERROR` w preflighcie `sprint-run` i w nowej podkomendzie `mr_queue_state.py config-check`, wołanej w preflighcie `mr-queue` (kod 2). Wystarczy jedna zmienna, zwykle `MONOLYNX_MR_TARGET`, bo tylko ona ustala też cel MR w `work` i `work-simple`; żadna nie została usunięta.
  - Wszystkie nowe ścieżki wykonują wyłącznie fast-forward: nigdy reset, rebase, stash, force ani refspec z `+`.
  - `/monolynx:setup`, KROK 6: wykaz flag obejmuje `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET`, a rozbieżne wartości oznacza ostrzeżeniem.
- MON-220: **`/monolynx:mr-queue` sprawdza konflikt przed CI**. Kolejność sprawdzeń to teraz konflikt -> CI -> approve -> merge (wcześniej konflikt był sprawdzany dopiero po CI). `mr_queue_state.py classify` zwraca `conflict` przed oceną CI: GitLab `has_conflicts` albo `detailed_merge_status` `conflict`/`need_rebase`, GitHub `mergeable=CONFLICTING` albo `mergeStateStatus=DIRTY` (`BEHIND` bez `CONFLICTING` nadal po CI). Werdykt `conflict` niesie pola `pipeline_id` i `pipeline_status` aktywnego pipeline'u MR/PR.
  - KROK 6 po dotychczasowych sprawdzeniach limitu `fix_rounds` i reguły cudzego brancha anuluje aktywny pipeline (`glab ci cancel pipeline <pipeline_id>`, na GitHubie `gh run cancel` dla runów HEAD w toku), a potem merguje branch docelowy w worktree jak dotąd. Anulowanie i merge to jedno działanie ticka.
  - Pipeline anulowany na HEAD MR bez konfliktu daje na GitLabie `needs_human` i nie jest ponawiany, bo anulowanie to decyzja człowieka. Retry joba po `exit status 137` (KROK 5) zostaje bez zmian.
  - WHY: 29.09 MR !117 (MON-128) miał konflikt z `sprint/cli-monolynx`. Człowiek anulował pipeline, bo runda `clean-code` na branchu z konfliktem trwa 15-20 min i przez `resource_group` blokuje inne MR-y. `classify` pokazał `needs_human` zamiast `conflict`, a tick ponowił anulowany pipeline, cofając decyzję człowieka.
- MON-223: **blokada poza zasięgiem agentów kończy iterowanie z krytykiem**. Rubryka krytyka (`plugin/skills/work/review-rubric.md`, sekcja „Blokada zewnetrzna”) wprowadza znacznik `BLOKADA-ZEWNĘTRZNA` z jednozdaniowym powodem dla odjęć zależnych od środowiska lokalnego, uprawnień albo decyzji człowieka; odjęcie liczy się do score, próg 82 bez zmian, a NEEDS WORK z samymi blokadami nie ma linii `REGULA:`. `work` (6.6b) i `work-simple` (3.5.2) dostają wspólny blok `blokada-zewnetrzna`: gdy agentowi poniżej progu zostają wyłącznie takie odjęcia, Team Manager weryfikuje każdą blokadę, nie zleca kolejnej iteracji (blokada nie zużywa limitu poprawek), dopisuje do ticketu komentarz z listą blokad, opcjami decyzji i sugestią etykiety `needs-local` (na razie informacyjnej; obsługę w `sprint-run` wprowadza osobny ticket). Przy `MONOLYNX_SPRINT_RUN=1` kończy turę linią `SPRINT-RUN STOP: blokada zewnetrzna - <powód>`, interaktywnie od razu pyta użytkownika. WHY: w MON-138 trzy iteracje z krytykiem (65/100) nie mogły zmienić wyniku, bo odjęte punkty zależały wyłącznie od człowieka.
- MON-222: **sesje `waiting` poza pulą slotów `/monolynx:sprint-run`**. Slot zajmuje tylko sesja `running` (`wolne = sloty - running`, dotąd `sloty - (running + waiting)`), a żywe sesje `waiting` mają osobny limit `MONOLYNX_SPRINT_MAX_WAITING` (domyślnie 2). Po osiągnięciu limitu tick nie startuje nowych sesji; sesje `waiting` i ich worktree zostają nietknięte, a raport podaje powód wstrzymania z liczbami `waiting`/`MAX_WAITING`. WHY: przy `PARALLEL=2` sesja MON-138 czekała na człowieka od wieczora do rana i zajmowała połowę slotów sprintu.
  - `sprint_run_tick.py preflight` wypisuje klucz `MAX_WAITING` zaraz po `PARALLEL` (dodatnia liczba całkowita, inaczej `2`).
  - `sprint_run_tick.py state ... --slots <n> --max-waiting <n>` dodaje do JSON-a addytywny klucz `dispatch` (`slots`, `max_waiting`, `free_slots`, `waiting_blocked`, `can_start`) liczony przez `sprint_run_state.dispatch_capacity`. KROK 4 skilla bierze liczbę sesji do startu z `dispatch.can_start` zamiast liczyć ją sam.
- MON-222: **tickety z etykietą `needs-local` nie startują w tle.** KROK 4 czyta etykiety z linii `Labels:` tego samego `get_ticket`, którym sprawdza blokery (bez dodatkowego wywołania). Kandydat `free` z `needs-local` nie zajmuje slotu, nie wchodzi do `S`, zostaje w `remaining` i trafia do sekcji "Czekają na człowieka" z komendą `/monolynx:work <KEY>` do uruchomienia interaktywnie; tick nie dopisuje komentarza na tickecie. WHY: MON-138 wymagał `brew update-python-resources` uruchomionego lokalnie i edycji `.gitlab-ci.yml`, której klasyfikator trybu automatycznego agentowi odmawia. Format linii `SPRINT-RUN: ...` i sentinel `remaining=-1` bez zmian; werdykty skryptu stanu, filtr blokerów i nazwy sesji bez zmian.
- MON-222: `sprint_run.sh` eksportuje `MONOLYNX_SPRINT_MAX_WAITING` (domyślnie 2) do ticku i wypisuje `max_waiting=` w logu startu. Sprint z samymi ticketami `needs-local`, zablokowanymi i sesjami `waiting` ma `remaining>0`, więc nie jest uznany za wykonany: skrypt loguje `UWAGA: nic nie biegnie` i tyka dalej do `--max-ticks`.
- MON-224: **`bash_guard.py` rozpoznaje teammate'ów Agent Teams jako subagentów**.
  - Agent pomocniczy to payload PreToolUse z niepustym `agent_id`, bez warunku na `agent_type`. Teammate in-process (domyślny tryb Agent Teams, także w sesjach `claude --bg` z `sprint-run`) ma `agent_type` równe swojej nazwie, więc teammate nazwany `main` omijał dotąd blokady git write, lint/testów i `glab mr create`.
  - Marker `~/.cache/monolynx/hook-active/<session_id>` jest trwały w obrębie sesji: raz zapisane `subagent_identification: true` zostaje `true`. Wcześniej każda komenda lidera, także `find` czytające marker w `work`, nadpisywała je wartością `false`, więc stan „ochrona potwierdzona” był nieosiągalny (stąd raport z MON-143, że hook nie rozpoznaje subagentów). Uszkodzony poprzedni marker liczy się jak brak `true`; decyzja hooka nie zależy od markera.
  - Udokumentowane ograniczenie (sekcja „Hooki”): teammate w trybie `teammateMode: "tmux"` nie ma `agent_id`, a jego payload i środowisko hooka są nieodróżnialne od sesji głównej uruchomionej z `claude --agent`. Hook traktuje go jak sesję główną, a marker ma `subagent_identification=false`; obejście to tryb in-process albo brak flag `MONOLYNX_AUTO*`.
  - Fixture'y payloadów z realnego przechwycenia w Claude Code 2.1.284: `tests/fixtures/claude/pretooluse-bash-*.2.1.284.json` (sesja główna, sesja z `--agent`, subagent, teammate in-process i tmux, lider tmux).
- MON-225: **planowanie wykrywa tickety na tych samych plikach i proponuje blokery**.
  - `/monolynx:ticket-create`: opis ticketu ma nową sekcję `## Pliki dotykane` (po "Zakres", przed "Nie ruszać") z przewidywanymi ścieżkami repo-relatywnymi, które ticket zmieni lub utworzy. Wypełnia ją Researcher (nowe pole "Pliki dotykane" obok "Pliki do zmiany"). Format jest sztywny, bo czyta go skrypt: jedna linia `` - `ścieżka` `` na plik, bez globów; ticket poza repo: `- brak (poza repo)`.
  - `/monolynx:ticket-review`: brak sekcji "Pliki dotykane" to SŁABE w kryterium "Zakres zmian" (liczba kryteriów bez zmian, 8). Nowy tryb `/monolynx:ticket-review sprint` (KROK 9) zbiera tickety `todo` aktywnego sprintu, z `get_ticket` czyta priorytet, UUID, listę plików i blokery z `## Blokery (relacja)`, a werdykty liczy nowy skrypt `plugin/scripts/ticket_overlap.py --input <plik.json>` (stdlib, JSON na stdout, kody wyjścia 0/2/64). Dla par bez relacji (także pośredniej), które dzielą pliki, proponuje `blocked_by`: bloker wg priorytetu (critical > high > medium > low), potem numeru klucza; trzy tickety na jednym pliku dostają łańcuch, nie klikę. Pliki zawsze wspólne (`plugin/README.md`, oba manifesty pluginu, liczniki skilli, kopie w `src/monolynx/static/skills/`) trafiają do osobnej grupy "wspólne, bez blokera", a tickety bez listy do "nie porównano". Zapis `update_ticket(blocked_by_ids=[...])` z pełną listą blokerów tylko dla propozycji wybranych przez człowieka.
  - Semantyka relacji w `sprint-run` bez zmian: bloker jest zamknięty tylko przy `done`.
- MON-226: **dogfooding: dyspozytor może chodzić na pluginie z brancha sprintu bez czekania na release**. Nowa sekcja README „Praca nad pluginem: dyspozytor na pluginie z brancha sprintu”: `CLAUDE_CODE_PLUGIN_DIRS=/sciezka/do/repo/plugin` w środowisku procesu dyspozytora (pętla `sprint_run.sh` albo sesja pod `/loop`) ładuje skille z głównego checkoutu na branchu sprintu i przechodzi do sesji `claude --bg`; `claude --plugin-dir` działa tylko dla jednej sesji i do sesji w tle nie przechodzi. Powrót: `unset CLAUDE_CODE_PLUGIN_DIRS`, nic nie zostaje w konfiguracji.
  - Zmienna jest nieudokumentowana (sprawdzona na Claude Code 2.1.284); sekcja opisuje, kiedy dyspozytor na kodzie niezreleasowanym ma sens, a kiedy nie (czerwone CI, zmiany hooków lub skryptów pętli w nocnym batchu, niesprawdzone podwójne hooki), oraz dlaczego lokalny marketplace się nie nadaje.
  - `/monolynx:setup`, KROK 6: informacyjnie pokazuje `CLAUDE_CODE_PLUGIN_DIRS` w stanie wiersza flag (`plugin z checkoutu: <ścieżka>`) i podaje komendy przełączenia i powrotu. Liczba punktów checklisty i flag `MONOLYNX_*` bez zmian.
  - `sprint_run.sh`, skille `sprint-run` i `mr-queue` oraz proces wersjonowania bez zmian.
- MON-221: **`/monolynx:mr-queue` nie czeka na CI brancha integracyjnego przed kolejnym merge**. Branch integracyjny to branch docelowy MR/PR różny od domyślnego brancha repozytorium (np. `sprint/*`). Po merge na niego pozycja dostaje nowy status `verifying` i od razu wychodzi z `next`, więc kolejny MR/PR wchodzi w tym samym rytmie ticków zamiast po 15-20 min czekania na CI. Merge na domyślny branch (`main`) działa jak dotąd (`merging`, czekanie w 3a).
  - Nowa podkomenda `mr_queue_state.py post-merge --target-branch T --default-branch D` wybiera tor (`verifying` tylko dla niepustych i różnych branchy, w każdym innym przypadku `merging`). Domyślny branch skill czyta w 4b / 4c przez `glab repo view -F json --jq .default_branch` albo `gh repo view --json defaultBranchRef --jq .defaultBranchRef.name`; nieustalony daje `merging`.
  - Nowy KROK 3.0 (weryfikacja wsteczna): na początku każdego ticka `target-ci` ocenia CI commita merge każdej pozycji `verifying`. Zielone -> wspólne dla obu torów Domknięcie (ticket -> `done` z komentarzem, fast-forward brancha docelowego, `queue-update --status done`); ticket nadal dostaje `done` dopiero po zielonym CI swojego commita merge. Czerwone -> `queue-update --status stopped --stop-reason target_ci_failed`, raport z nazwami czerwonych jobów (nowe pole `failed_jobs` w wyniku `target-ci`) i `Potrzebne:` z komendą odblokowania `queue-update --ref <ref> --status closed`; batch `MR-QUEUE STOP: <ref> czerwone CI brancha docelowego`.
  - Wynik `queue-show` / `queue-update` niesie pola `halted` (pozycja zatrzymana czerwonym CI brancha integracyjnego; wtedy `next` jest `null` i kolejne ticki kończą się raportem bez brania elementu) i `verifying`. `queue-set` nie gubi pozycji `verifying`. W linii `MR-QUEUE:` `remaining` nie liczy `verifying`, a `done` liczy tylko `done`; format linii bez zmian.
  - WHY: po każdym merge na `sprint/*` kolejka czekała 15-20 min na zielone CI, więc 3 MR-y zajmowały ok. 2 h, a całość i tak weryfikuje MR brancha sprintu do `main`.
- MON-227: **czytelny ogon logu sesji w `/monolynx:sprint-run`**. Nowa funkcja `render_log` w `sprint_run_state.py` to minimalny emulator ekranu terminala (stdlib) zamiast regexowego zdejmowania ANSI: odtwarza stan ekranu ze scrollbackiem, skok kursora o N komórek zamienia na N spacji, `\r` traktuje jako powrót karetki, a log czyta jako bajty. Odrzuca linie samego spinnera i statusu (glif `✢✳✶✻✽·` na początku, liczniki czasu i tokenów) i skleja przylegające identyczne bloki. Z tej samej funkcji korzystają `read_log_tail` i zapis pliku w `TICK_DIR/logs/` (`sprint_run_tick.py`); ogon nadal ma 60 niepustych linii, a wykrywanie `SPRINT-RUN STOP:` i pytania w ostatnim bloku (MON-191) działa bez zmian. Fixture z realnego przebiegu: `tests/fixtures/claude/logs-spinner-redraw.2.1.284.txt`. WHY: render TUI przerysowuje ekran różnicowo, więc strip ANSI gubił litery („R jestracja”, „com it”), a każde pełne przerysowanie dublowało bloki; ogony z nocy 28/29.09 w komentarzach `needs_human` były przez to nieczytelne.

### 1.21.4

- MON-207: **poprawki z code review MR !98 przed merge sprintu do `main`**.
  - `hooks/bash_guard.py` zawęża wyjątek read-only lint z MON-176. Subagent dostaje allow tylko dla `ruff check --no-fix` i `ruff format --check`/`--diff`; `ruff check` bez `--no-fix` dostaje deny, bo `fix = true` w `pyproject.toml` pozwala mu cicho naprawiać pliki. `docker run <obraz> ruff` wypada z allow-listy (dowolny obraz i dowolny bind mount, np. `-v /:/host`), a `docker compose ... run` przechodzi tylko z `-v` z korzenia projektu i bez `--entrypoint`, `--privileged`, `-u`/`--user`. **Zmiana zachowania:** wyjątek dotyczy wyłącznie subagenta - sesja główna dla read-only `ruff` dostaje teraz `ask`, chyba że `MONOLYNX_AUTOTEST=true`. `work` i `work-simple` każą deweloperowi uruchamiać `ruff check --no-fix`.
  - `get_ticket` nazywa generowaną sekcję relacji `## Blokery (relacja)` zamiast `## Zaleznosci`, więc nie myli się z sekcją `## Zaleznosci` w opisie ticketu (proza szablonu `ticket-create`). `sprint-run`, `work`, `work-simple`, `next`, `ticket-review` i `ticket-create` czytają relację wyłącznie z nowego nagłówka.
  - `/monolynx:sprint-run` ustawia `MONOLYNX_SPRINT_RUN=1` wyłącznie w środowisku startowanej sesji: komenda startu to `MONOLYNX_SPRINT_RUN=1 claude --bg ...`. Przykłady `.claude/settings.json` w skillu i w README nie zawierają już tej zmiennej; `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` należą do środowiska procesu dyspozytora albo `.claude/settings.local.json`. Domyślne `--allowedTools` ticku w `sprint_run.sh` dostaje regułę `Bash(MONOLYNX_SPRINT_RUN=1 claude:*)`, żeby tick w `claude -p` mógł wystartować sesję z tym prefiksem.
  - `scripts/validate_plugin_consistency.py` w trybie wydania (`--release` albo `CI_COMMIT_BRANCH=main`) odrzuca śledzony `.claude/settings.json` z `MONOLYNX_SPRINT_RUN`, `MONOLYNX_BASE_BRANCH` albo `MONOLYNX_MR_TARGET` w `env` (nowa opcja `--settings-json`). `/monolynx:sprint-end`, job `version-bump` (4c), przed walidacją przywraca `env` tego pliku do stanu z `main` i dołącza go do commita wydania.
  - `make backfill-dependencies`: jednorazowa migracja danych, która przenosi zależności zapisane prozą (sekcja `## Zaleznosci` w opisie, komentarz `**Zaleznosci:**`) do relacji `blocked_by_ids`. Domyślnie dry-run, zapis z `ARGS=--apply`; tickety `done` są pomijane, nieznane klucze i cykle są raportowane bez zapisu dla danego ticketu.
  - `services/ticket_dependencies.py` wykrywa pełne cykle blokerów, także tranzytywne (A -> B -> C -> A), i podaje w błędzie całą ścieżkę kluczy ticketów. Walidację i zapis serializuje `pg_advisory_xact_lock`, więc dwa równoległe `set_blockers` nie zamkną cyklu.

### 1.21.3

- MON-204: **`mr-queue` domyka ticket także po merge poza kolejką**. Werdykt `merged` (MR/PR zmergowany ręcznie albo tick przerwany po merge, przed `queue-update`) nie kończy już pozycji przez `queue-update --status done`, tylko przełącza ją w `merging` z `merge_sha` (nowa sekcja 4c w `plugin/skills/mr-queue/SKILL.md`) i wraca do KROK 3 w tym samym ticku. Pozycja idzie torem 3a jak po merge z 4b: `target-ci` na branchu docelowym, a na `success` ticket -> `done` z komentarzem z SHA i id pipeline'u. `merge_sha` dla GitLaba pochodzi z werdyktu `classify` (fallback na `sha` przy fast-forward), dla GitHuba z `gh pr view <n> --json mergeCommit -q .mergeCommit.oid`; pusty wynik to `needs_human` z powodem, bez zgadywania SHA. Przyczyna: od MON-196 ticket domykała wyłącznie pozycja w `merging`, więc MON-201 i MON-202, zmergowane poza kolejką, zostały w `in_review` mimo zielonego `main`. `plugin/scripts/mr_queue_state.py` bez zmian.
- MON-178: **zależności w statusie `in_review` nie odblokowują pracy.** `/monolynx:sprint-run` w KROK 4 filtruje kandydatów `free`: dla każdego dociąga `get_ticket` (opis i komentarze), zbiera klucze ticketów z sekcji `## Zależności` / `## Zaleznosci` opisu oraz z komentarza `**Zaleznosci:**` z `ticket-create` i pomija ticket, którego któraś zależność ma status inny niż `done`, także `in_review` (review to nie merge). Wiersz miękki (`miękka`, `miekka`, `soft`, `opcjonalna`) albo `spójne z` nie blokuje. Status zależności pochodzi zawsze ze świeżego odczytu (lista ticketów sprintu, a dla klucza spoza sprintu `get_ticket`), nigdy ze statusu wpisanego w opis. Zablokowany ticket nie zajmuje slotu, nie wchodzi do `S`, zostaje w `remaining` i w raporcie KROK 5 ma powód `czeka na merge <KEY>`. Przyczyna: MON-150 i MON-152 startowały przy zależnościach w review, zmergowały do swoich branchy `worktree-MON-149` / `worktree-MON-150`, a ich MR-y niosły cudze commity.
- MON-178: `/monolynx:work` i `/monolynx:work-simple` nie mergują do swojego brancha brancha zależności, która nie jest `done`. W sesji w tle kończą turę linią `SPRINT-RUN STOP: czeka na merge <KEY>`, interaktywnie pytają użytkownika; wyjątkiem jest jawne polecenie użytkownika. Definicja zależności jest ta sama co w `sprint-run`.
- MON-178: `plugin/skills/work/commit-mr.md` (i generowana kopia dla `work-simple`): merge request powstaje z `--title "<KEY>: <tytuł ticketu>"` i `--description` z podsumowania pracy zamiast `--fill`, który brał tytuł z nazwy brancha (`worktree-MON-...`).
- MON-178: `/monolynx:sprint-run` KROK 5: przykład liczbowy korekty linii statusu. Stan przed dispatchem `remaining=5 running=0 waiting=0 done=1` i `S = 2` dają `SPRINT-RUN: remaining=3 running=2 waiting=0 done=1`; raport `remaining=5 running=2` (jak w MON-167) to korekta samego `running`. Wzór i format linii bez zmian (kontrakt MON-169), `sprint_run_state.py` i `sprint_run.sh` bez zmian.
- MON-178: `/monolynx:ticket-create`: przypomnienie w komentarzu `**Zaleznosci:**` wymaga, żeby zależności były `done`, a nie "ukończone lub w review". Źródłem zależności dla `sprint-run`, `work` i `work-simple` pozostaje sekcja opisu i ten komentarz; strukturalne pole blokerów (MON-198, MON-200) jeszcze nie istnieje.
- MON-178: numer wydania nada mergujący według `.claude/rules/plugin-version-bump.md` (pkt 2): zamieni `### Unreleased` na `### X.Y.Z` i ustawi tę samą wersję w obu manifestach pluginu. Ticket nie zmienia `plugin.json`.
- MON-177: **numer wydania pluginu nadaje mergujący, nie ticket**. Tickety sprintu dopisują wpis pod `### Unreleased` z kluczem ticketu w każdym punkcie i nie zmieniają manifestów. WHY: przy równoległych ticketach każdy własny bump kończył się konfliktem w `plugin.json` i changelogu przy merge, a wersje nadawano po fakcie (sprint "Testy mutacyjne w pluginie": pięć ticketów, pięć kolizji). Opis flow w nowej sekcji „Wersjonowanie pluginu”.
- MON-177: `scripts/validate_plugin_consistency.py` obsługuje `### Unreleased`. Na branchu roboczym nagłówek jest dozwolony jako pierwszy wpis `## Changelog`, a `version` z manifestu porównywana jest z pierwszym numerycznym wpisem pod nim. W trybie wydania (`--release` albo `CI_COMMIT_BRANCH=main`, bez zmian w `.gitlab-ci.yml`) `### Unreleased` na pierwszym miejscu jest błędem „wydanie bez numeru”. Walidator odrzuca też `### Unreleased` poza pierwszą pozycją, pustą sekcję i punkt najwyższego poziomu bez prefiksu `KLUCZ-123: `. WHY: do tej pory walidator wymagał numeru w każdym branchu, więc reguła "bump przy merge" wymuszała obejście (pkt 4 reguły, ręczny wybór kolejnego wolnego numeru), a MON-191 skończył z czerwonym `plugin-consistency` i numerem nadanym po fakcie. Pkt 4 reguły `.claude/rules/plugin-version-bump.md` jest usunięty.
- MON-177: `work` i `work-simple` wklejają do promptu każdego developera wspólny blok `changelog-unreleased`: wpis pod `### Unreleased`, klucz ticketu w każdym punkcie, manifest nietknięty, wyjątek hotfixu tylko z decyzją Team Managera w komentarzu planu (bez niej zmiana wersji to STOP). Treść jest warunkowa, bo skille działają w dowolnym projekcie.
- MON-200: **skille respektują zależności ticketów** z MON-198. Jedynym źródłem blokerów jest wynik `get_ticket`: generowana sekcja `## Zaleznosci` z liniami `Blokowany przez: <KEY> | <status> | <tytuł>` (poza `## Description`; `list_tickets`, `get_board` i `search_tickets` blokerów nie zwracają). Bloker jest zamknięty wyłącznie przy statusie `done`, `in_review` też blokuje.
  - `/monolynx:work` (KROK 1) i `/monolynx:work-simple` (0.2) sprawdzają blokery przed startem: przy niezamkniętym blokerze wypisują listę i pytają, czy mimo to pracować; w trybie sesji w tle kończą turę linią `SPRINT-RUN STOP: czeka na merge <KEY>`, bez zmiany statusu ticketu.
  - `/monolynx:sprint-run` (KROK 4) wywołuje `get_ticket` dla każdego wolnego kandydata i nie startuje sesji dla ticketu z niezamkniętym blokerem. Pominięty ticket nie zajmuje slotu, zostaje w `remaining` i ma w raporcie wiersz `free -> pominięty` z powodem `czeka na merge <KEY>`; następny tick sprawdza go ponownie. Linia statusu bez zmian.
  - `/monolynx:ticket-create` przekazuje blokery do `create_ticket` jako `blocked_by_ids` (UUID z `search_tickets`) i nie dodaje już komentarza z listą zależności. W szablonie opisu blokery mają prefiks `Bloker:`, żeby nie mylić ich z relacją w wyniku `get_ticket`. Seria ticketów wiąże kolejne tickety z poprzednimi przez `blocked_by_ids`.
  - `/monolynx:next` nie poleca `/monolynx:work <KEY>` dla ticketu z niezamkniętym blokerem, tylko komendę dla blokera (`mr-queue` dla blokera w `in_review` z otwartym MR/PR, `work` dla blokera w `todo`), z oboma kluczami w uzasadnieniu.
  - `/monolynx:ticket-review` w kryterium "Zależności" porównuje blokery z opisu z `## Zaleznosci` z `get_ticket`; rozjazd to SŁABE z propozycją `update_ticket(..., blocked_by_ids=[...])` z pełną listą blokerów (replace-all).
  - Połączenie z MON-178: źródłem zależności w `sprint-run`, `work` i `work-simple` jest wyłącznie sekcja `## Zaleznosci` z `get_ticket`, a nie sekcja opisu ani komentarz `**Zaleznosci:**`. Z MON-178 zostają: reguła, że `in_review` blokuje, powód `czeka na merge <KEY>` (raport `sprint-run` i linia `SPRINT-RUN STOP`), blok `shared:zaleznosci` z zakazem mergowania brancha niezamkniętego blokera i pytaniem (a)/(b)/(c) w trybie interaktywnym (zamiast pytania, czy mimo to kontynuować), przykład liczbowy KROK 5 i tytuł MR. Bramka blokerów w `work` przeszła do 1d, w `work-simple` do 0.25; obie stoją przed zmianą statusu i utworzeniem pipeline'u.
- MON-177: `/monolynx:sprint-end` dostaje job `version-bump` (4c) w stepie `wrap-up`, przed `close-sprint`. Ustala branch docelowy (`MONOLYNX_MR_TARGET`, potem domyślny branch repozytorium), czyta changelog i manifest z `origin/<branch>`, proponuje minor albo patch na podstawie plików dodanych w `plugin/skills/`, `plugin/hooks/` i `plugin/scripts/` od commita ostatniego wydania, a po zgodzie przygotowuje branch `release/plugin-X.Y.Z` w osobnym worktree ze zmianą changelogu i obu manifestów, sprawdzoną `validate_plugin_consistency.py --release`. Commit, push i MR według flag `MONOLYNX_AUTO*`. W sesji w tle (`MONOLYNX_SPRINT_RUN=1`) numer idzie z reguły bez pytania, bo wynik czeka na merge MR przez człowieka. Krok jest nieblokujący i pomijany w projekcie bez pluginu. Dotychczasowe `close-sprint` i `summary` to teraz 4d i 4e.
- MON-206: **`/monolynx:retro` proponuje wiersz rubryki tylko w repo pluginu**. Repo pluginu skill rozpoznaje w KROK 2c po istnieniu `plugin/skills/work/review-rubric.md` i `scripts/sync_skills.py` w korzeniu repo. Tam czyta aktualną rubrykę i odrzuca kandydatów, których wzorzec (kategoria + mechanizm, nie dosłowny napis) już w niej jest. Poza repo pluginu cel „rubryka” zamienia się na regułę projektu w `.claude/rules/` (krytyk odejmuje -25 za jej naruszenie) albo, gdy wada dotyczy samego procesu pluginu, na opis zgłoszenia do projektu Monolynx przez `/monolynx:ticket-create` z jawnym slugiem `monolynx`. Nigdy nie kończy się instrukcją „wklej do `plugin/skills/work/review-rubric.md`”. Zasada 5 (eskalacja reguł istniejących i łamanych) rozróżnia oba przypadki. WHY: retro w projekcie-konsumencie (sprint-end 2026-09-23) podsunęło zmianę globalnej rubryki na podstawie jednego sprintu jednego projektu, ze ścieżką, której w tym repo nie ma, a obaj kandydaci byli duplikatami istniejących wierszy.
- MON-206: w `plugin/skills/work/review-rubric.md` wiersz „Test przechodzi na mutancie” mieści się w jednej linii. Kolumna Uwagi była złamana na dwie linie i rozbijała tabelę markdown.
- MON-176: `hooks/bash_guard.py` przepuszcza subagentowi read-only lint: `ruff check` bez żadnej flagi i `ruff format --check`/`--diff`, także przez `docker compose exec [-T] app`, `docker compose ... run <flagi> app` (wariant worktree z sekcji `## Worktree` strony `toolchain`) i `docker run <flagi> <obraz>`. Reszta wywołań `ruff` (`--fix`, `--unsafe-fixes`, `--add-noqa`, `ruff format` bez `--check/--diff`, komendy złożone, przekierowania `<`/`>` - także doklejone bez spacji jak `>/etc/hosts` - ekspansja `$`/`~`, glob w dowolnym tokenie komendy, nieznane flagi dockera) nadal spada do dotychczasowej gałęzi `TEST_LINT` (subagent -> deny, sesja główna -> ask). Prompty `work`/`work-simple` każą deweloperowi uruchomić ten read-only lint na własnych plikach przed oddaniem pracy.

### 1.21.2

- MON-123: `/monolynx:wiki-lint` nie zgłasza już logów pipeline'ów jako sierot. Serwis `lint_wiki` pomija w `orphans` strony z `exclude_from_embeddings=True` (dziś to wyłącznie strony pod rodzicem `pipeline-logi`: powiązane z jobem przez `PipelineJob.wiki_page_id`, więc bez backlinków z definicji), a raport dostaje nowy klucz `skipped_generated` z liczbą tak pominiętych stron. Skill pokazuje tę liczbę przy sierotach ("Sieroty: N (pominięto M stron generowanych maszynowo)"), żeby wykluczenie było widoczne. `dead_links`, `contradictions` i `gaps` bez zmian.
- Numer wydania nadany w tickecie wg pkt 4 reguły `.claude/rules/plugin-version-bump.md` (walidator nie obsługuje jeszcze `### Unreleased`): najwyższa wersja na `main` to 1.21.1, `glab mr list` bez otwartych MR-ów, więc patch 1.21.2.

### 1.21.1

- MON-188: **wspólne instrukcje lintu i testów oraz commita, pusha i MR dla `work` i `work-simple`**. Powtórzona w obu skillach treść obu bramek wyszła z SKILL.md do dwóch plików towarzyszących. `plugin/skills/work/lint-test.md`: skąd brać komendy (strona wiki `toolchain`), wybór wariantu główny checkout kontra git worktree przez dwie **osobne** komendy `git rev-parse --git-dir` i `git rev-parse --git-common-dir` (jednolinijkowiec z podstawieniem odrzuca guard izolacji worktree), podstawienie dosłownego wyniku `pwd` za `"$PWD"` w komendzie z sekcji `## Worktree`, tryb defektu (`git stash -- <pliki fixu, bez pliku testu>` -> test regresyjny -> `git stash pop` -> ta sama komenda, oczekiwane czerwony potem zielony), decyzja po wyniku i pełny przebieg po ostatniej zmianie. `plugin/skills/work/commit-mr.md`: `MONOLYNX_AUTOCOMMIT`, imienny `git add` przy niepustych `PLIKI_SPRZED_STARTU`, `MONOLYNX_AUTOPUSH` i `MONOLYNX_AUTOMR` z pełną kolejnością ustalania brancha docelowego. Oba skille czytają te pliki z katalogu własnego skilla i **wykonują** opisaną procedurę - w odróżnieniu od rubryki krytyka nie wklejają ich do promptu subagenta, bo adresatem jest agent prowadzący pracę.
- Treść plików wspólnych jest **neutralna wobec flow**: bez numerów kroków i faz, więc ta sama procedura czyta się identycznie z `KROK 6.5` w `work` i z `FAZA 3` w `work-simple`. Świadome różnice obu flow zostały przy odpowiednim SKILL.md, wokół odwołania: moment detekcji środowiska (`work` wykonuje ją w bramce, `work-simple` bierze wynik z wcześniejszego kroku razem z oboma kompletami komend), warunek auto-commita (score >= 82 dla każdego ocenianego agenta kontra werdykt APPROVED) oraz limit poprawek (3 na agenta kontra jeden wspólny licznik). Bramki jakości i zgody bez zmian: nadal `MONOLYNX_AUTOTEST=false` oznacza, że komendy uruchamia człowiek, a pominięcie lintu i testów nie znosi odjęcia za brak testu dla nowego kodu.
- Kopie dla `work-simple` (`plugin/skills/work-simple/lint-test.md`, `commit-mr.md` i dystrybucja w katalogu `monolynx-work-simple` w `src/monolynx/static/skills/`) generuje `scripts/sync_skills.py` mechanizmem `WHOLE_FILE_RESOURCES`, tym samym, którym od 1.19.1 rozwożona jest rubryka krytyka; `--check` pilnuje ich aktualności. Testy synchronizacji są sparametryzowane wpisami tego słownika, więc kolejny plik wspólny dostaje pokrycie bez dopisywania testu.
- Numer wydania nadany przy scalaniu `main` do brancha MR wg pkt 4 reguły `.claude/rules/plugin-version-bump.md` (walidator `scripts/validate_plugin_consistency.py` nie obsługuje jeszcze nagłówka `### Unreleased`): najwyższy numer na `main` to 1.21.0 (MON-185), w otwartych MR-ach pluginu nic wyżej, więc wydanie dostaje 1.21.1. Patch, bo nie powstał żaden nowy skill, hook ani skrypt - to reorganizacja treści istniejących skilli. Pierwotnie ticket nadał 1.19.2 przy `main` na 1.19.1; MON-185 wszedł w międzyczasie i numer trzeba było przesunąć przy rozwiązaniu konfliktu.

### 1.21.0

- MON-185: dyspozytor sprintu dostaje drugi backend sesji. Nowa zmienna `MONOLYNX_SPRINT_RUNTIME` (`claude` domyślnie, `codex`) wybiera binarkę dla całego przebiegu `sprint_run.sh`: ticku dyspozytora oraz `sprint-end`/`retro` przy `--close`. Dla `codex` skrypt uruchamia `codex exec --sandbox workspace-write -C <katalog projektu> --skip-git-repo-check "$monolynx:<skill> ..."` (bez `-m` i bez `--name` - CLI ich nie ma), z sandboxem nadpisywalnym przez `MONOLYNX_SPRINT_CODEX_SANDBOX`; wartość runtime spoza `claude`/`codex` kończy skrypt kodem 64, brak binarki kodem 69 (bez zmiany kontraktu).
- Tick: `sprint_run_tick.py preflight --runtime claude|codex` i `sprint_run_tick.py state --slug ... --tickets-json ... --backend claude|codex`, obie flagi domyślnie `claude`. Przy `codex` preflight pomija `.claude/settings*.json`, `MONOLYNX_SPRINT_PERMISSION_MODE` oraz `claude --version`/`claude agents` i sprawdza `codex --version` z `codex login status`; raport stanu powstaje z własnego rejestru dyspozytora i nie woła binarki `claude`. Wyjście preflightu ma nowy klucz `RUNTIME`.
- Nowe podkomendy ticku: `sprint_run_tick.py start --backend codex --slug <slug> --key <KEY> --model <model> --prompt <prompt> [--cwd <dir>] [--tick-dir <dir>]` i `sprint_run_tick.py stop --backend codex --slug <slug> --key <KEY>`. Zamrożone linie wyjścia `STARTED <identity> <runtime_id> <pid>`, `ALREADY-RUNNING <identity> <runtime_id>`, `STOPPED <identity>`, `NOT-FOUND <identity>` i kody 0 / 2 (preflight albo katalog ticku, bez efektów ubocznych) / 64 (błąd użycia). `start --backend claude` kończy się kodem 64: sesje Claude startują wprost w KROKU 4 skilla, żeby przejść przez system uprawnień Claude Code, którego wywołanie z `subprocess` skryptu by ominęło (MON-191/MON-192). `start` sprawdza przed spawnem rejestr oraz best-effort `claude agents --json --all`, więc żywa sesja w którymkolwiek backendzie daje `ALREADY-RUNNING` zamiast duplikatu.
- Nowy adapter `plugin/scripts/sprint_run_backend.py` (rejestr `<git-dir>/monolynx/tick/sessions.json` zapisywany atomowo, logi sesji Codeksa w `codex-logs/<slug>-<KEY>-<runtime_id>.jsonl` plus osobny `.stderr.log`). Tożsamość sesji `<slug>:<KEY>` nie zawiera modelu ani backendu, więc zmiana modelu albo runtime między tickami nie tworzy duplikatu, a rejestr czytany z dysku odzyskuje żywe sesje po restarcie pętli. `stop()` usuwa wyłącznie własny wpis (dopasowanie po `runtime_id` i `pid`) - nie dotyka cudzych procesów, cudzych wpisów ani brudnych worktree.
- `stop()` wysyła `SIGTERM` tylko wtedy, gdy wpis **w rejestrze** ma stan żywy (`running`, `waiting`) **i** `is_process_alive(pid)` potwierdza istnienie procesu pod tym PID. WHY: KROK 3 skilla woła `stop` przy werdykcie `done`, czyli gdy proces Codeksa już się zakończył, a jego PID mógł w międzyczasie przypaść innemu procesowi systemu - dyspozytorowi nie wolno zabić cudzego procesu. Drugi warunek pokrywa wywołanie `sprint_run_tick.py stop` bez poprzedzającego `state`, gdzie stan wpisu bywa nieodświeżony i sam warunek na stanie terminalnym by nie wystarczył. Wpis znika z rejestru tak samo jak dotąd; sygnał leci wyłącznie pod sesję żywą i wciąż istniejącą w systemie, przerywaną świadomie. Granica świadomie niezamknięta: `is_process_alive` to `os.kill(pid, 0)`, więc PID przejęty przez inny żywy proces przy żywym wpisie nadal dostanie sygnał - weryfikacja tożsamości procesu wymagałaby parsera `/proc` albo `ps` z fixture'em, czyli większej powierzchni niż zamykany problem. Okno wyścigu węższe, nie zerowe.
- Gałąź `codex` w `plugin/scripts/sprint_run.sh` woła klienta z `< /dev/null`. WHY: `codex exec` czyta stdin, gdy ten nie jest terminalem (komunikat `Reading additional input from stdin...` widać nawet przy zamkniętym stdinie), więc tick odpalony ze stdinem będącym otwartym pipe'em zawisłby bez żadnego wyjścia. Adapter robił to poprawnie od początku (`stdin=subprocess.DEVNULL`), pętla nie.
- Linia statusu pod Codeksem nie wymaga zdejmowania ANSI: stdout `codex exec` bez `--json` to sama finalna odpowiedź agenta, a cały render klienta idzie na stderr. Przy `2>&1` linia trafia do logu dwa razy (stdout plus render), więc skrypt bierze `tail -1`. Przechwycone strumienie: `tests/fixtures/codex/exec-plain.0.155.1.stdout.txt` i `.stderr.txt`.
- `sprint_run_state.py` bez zmian: werdykty `free`/`running`/`waiting`/`done`/`needs_human` i linia `SPRINT-RUN: remaining=... running=... waiting=... done=...` są wspólne dla obu backendów, więc zachowanie przy `claude` jest dotychczasowe.
- Sesja Codeksa dostaje świeży worktree na ticket (`codex exec --worktree`, przełącznik bez wartości - katalogiem zarządza Codex, inaczej niż `claude --bg --worktree <KEY>`, gdzie nazwę podaje dyspozytor). Dyspozytor nie zna ścieżki tego katalogu i jej nie potrzebuje: sprzątanie obejmuje wpis w rejestrze i własny PID, nigdy worktree.
- **Ograniczenia Codeksa, świadomie nieukryte** (`codex-cli 0.155.1`): brak natywnego sygnału "czeka na zgodę" (`status()` nigdy nie zwraca `waiting`; jedynym sygnałem jest marker `SPRINT-RUN STOP:` w logu), `permissionDecision: "ask"` fail-open pod Codeksem (MON-184, poza zakresem), `resume` tylko dla Codeksa (`codex exec resume`), model nieprzekazywany do `codex exec`, brak `--name`/`--bg`, `codex agents --json` i `attach`.
- Smoke test obu klientów zapisany w sekcji "Wyniki smoke testu backendów (2026-09-20)": Claude Code 2.1.278 i Codex CLI 0.155.1, oba preflighty `ok` bez efektów ubocznych, realne wyjście `codex exec --json` zachowane jako fixture `tests/fixtures/codex/exec-json.0.155.1.jsonl` (pierwsze przechwycenie w tym katalogu, zgodnie z pkt 1 reguły `.claude/rules/external-tool-output.md`).
- Numer wydania nadany przy scalaniu wg pkt 4 reguły `.claude/rules/plugin-version-bump.md` (walidator `scripts/validate_plugin_consistency.py` nie obsługuje jeszcze nagłówka `### Unreleased`): w chwili scalania `main` do brancha MR najwyższy numer na `main` to 1.19.1 (MON-186), a w otwartych MR-ach pluginu 1.20.0 (ten MR), więc wydanie dostaje kolejny wolny numer - 1.21.0. Minor, bo dochodzi nowy skrypt `sprint_run_backend.py` i nowe podkomendy CLI ticku.

### 1.19.1

- MON-186: **jedna rubryka krytyka dla `work` i `work-simple`**. `plugin/skills/work/review-rubric.md` jest jedynym źródłem; `work-simple` nie ma już własnej, uboższej tabeli wklejonej w SKILL.md (8 wierszy zamiast 10, bez wiersza o zmianie pliku poza przydziałem i bez wiersza o komentarzach opisujących CO robi kod, plus martwe odwołanie do wiersza, którego ta tabela nie miała). Skill czyta teraz `review-rubric.md` z katalogu własnego skilla i wkleja pełną treść do promptu krytyka w miejsce `[pełna treść review-rubric.md]` - tak samo jak `work`. Kopie (`plugin/skills/work-simple/review-rubric.md` i dystrybucja w katalogu `monolynx-work-simple` w `src/monolynx/static/skills/`) generuje `scripts/sync_skills.py`, a `--check` pilnuje ich aktualności, więc instalacja samego `work-simple` zawiera rubrykę i nie zależy od `CLAUDE_PLUGIN_ROOT` (poza Claude Code zmienna jest pusta).
- Rubryka jest neutralna wobec obu trybów: wiersz o zmianie pliku poza przydziałem czyta przydział z listy zespołu **albo** z `ZAKRES PRACY DEV` jednego developera (wykrywanie przez `git diff --stat <BASELINE_SHA>` + `git status --porcelain` zostaje), a toolchain projektu bierze z sekcji "Toolchain" raportu Researchera **albo** z treści strony wiki `toolchain` wklejonej wprost do promptu - krytyk nadal nie pobiera wiki sam. Wiersze trybu defektu odwołujące się do researchu obowiązują tylko wtedy, gdy research w ogóle był. Wartości odjęć, próg 82 i format odpowiedzi bez zmian.
- `/monolynx:retro` wskazuje jedno miejsce edycji rubryki (`plugin/skills/work/review-rubric.md`) i mówi wprost, że ręczne wklejanie wiersza do `work-simple` jest błędem, bo kopia powstaje automatycznie.

### 1.19.0

- MON-197: punkt startu brancha roboczego i worktree ticketu pochodzi z konfiguracji projektu, a nie z zaszytego `main`. Nowa zmienna `MONOLYNX_BASE_BRANCH`; pusta -> `MONOLYNX_MR_TARGET`, potem wykrycie domyślnego brancha repozytorium (`git symbolic-ref refs/remotes/origin/HEAD`, potem `glab repo view`). Gdy nic nie da wyniku, `work` i `work-simple` zatrzymują się i proszą o ustawienie zmiennej zamiast zgadywać `main`, a twardy gate "nie pracuj na punkcie startu innych" przenosi się z literału `main`/`master` na ustalony branch bazowy. **Projekt pracujący na `develop`: ustaw `MONOLYNX_MR_TARGET` (albo `MONOLYNX_BASE_BRANCH`, gdy branch bazowy ma być inny niż cel MR) - bez żadnej z nich skille ustalą branch bazowy z repozytorium, a przy braku wyniku zatrzymają pracę.**
- Numer wydania nadany w tickecie wg pkt 4 reguły `.claude/rules/plugin-version-bump.md`: w chwili commita nie było żadnego otwartego MR-a zmieniającego plugin (`glab mr list` pusty, najwyższa wersja na `main`: 1.18.1), więc nie ma ryzyka kolizji numeru.

### 1.18.1

- MON-195: domyślny `MONOLYNX_SPRINT_PERMISSION_MODE` zmieniony z `bypassPermissions` na `auto` - ten pierwszy trafiał w sesji auto na domyślną regułę soft_deny "Create Unsafe Agents", więc tick `sprint-run` przechodził cały preflight i odpadał dopiero przy starcie sesji w KROKU 4, bez startu jakiejkolwiek sesji (19.09, MON-191/MON-192). Preflight (`sprint_run_tick.py`) sprawdza to ustawienie jako pierwszy check: przy `bypassPermissions` albo `--dangerously-skip-permissions` tick nie startuje żadnej sesji i wypisuje gotową poprawkę (`MONOLYNX_SPRINT_PERMISSION_MODE=auto` w `env` śledzonego `.claude/settings.json`, albo świadoma reguła `autoMode.allow` w `~/.claude/settings.json` dla zespołów, które celowo zostają przy `bypassPermissions`), a w trybie `batch` kończy się linią `SPRINT-RUN: remaining=-1 running=0 waiting=0 done=0`. **Zespoły, które dziś świadomie ustawiają `MONOLYNX_SPRINT_PERMISSION_MODE=bypassPermissions`, muszą po tej zmianie albo przełączyć się na `auto`, albo dodać regułę `autoMode.allow` - inaczej tick przestanie startować sesje.**

### 1.18.0

- MON-184: agenci, reguły i hooki opisane pod oba klienty. `plugin/agents/*.md` jest jedynym źródłem treści ról, a kopie w `.claude/agents/` powstają z niego przez `scripts/sync_agents.py --check`. Skille `work` i `work-simple` dobierają wykonawców po faktycznie dostępnym delegowaniu, zawsze z **osobnym krytykiem**; tryb solo wolno włączyć wyłącznie przy braku capability (nigdy po nazwie klienta) i zawsze z jawną adnotacją, że review nie jest niezależne - w planie, w komentarzu podsumowującym i w podsumowaniu dla użytkownika.
- **Trwałe reguły mają jedną, projektową lokalizację**: `.claude/rules/*.md` i `.claude/agent-memory/<typ-agenta>/MEMORY.md`, ścieżki od korzenia repozytorium, zapis addytywny (dopisanie do sekcji, nigdy nadpisanie pliku). To zwykłe pliki repo czytane przez każdego klienta wykonującego instrukcje skilli - nie zakładamy prywatnego formatu pamięci żadnego z nich. Nazwa katalogu `.claude/` zostaje jako historyczna i wspólna. Opisane w `work`, `work-simple`, `retro`, `work/review-rubric.md` i w nowej sekcji README „Reguły i pamięć projektu”; w `retro` odczyt pamięci ról wyszedł poza blok specyficzny dla Claude Code, prywatna auto-memory klienta została w nim jako źródło opcjonalne.
- **Wykrywanie braku aktywnej ochrony hooków**: `bash_guard.py` zapisuje best-effort marker `~/.cache/monolynx/hook-active/<session_id>` z polem `subagent_identification`. `work` (6b, 6d), `work-simple` (0.45, 2.4) i `mr-queue` czytają go i raportują jeden z trzech stanów - ochrona potwierdzona / niepełna / brak potwierdzenia. W dwóch ostatnich skille nie deklarują deterministycznej ochrony, egzekwują zgody rozmową z użytkownikiem, a w trybie w tle kończą turę linią `SPRINT-RUN STOP`. README opisuje wymagane załadowanie i zaufanie do hooków (instalacja ich nie aktywuje).
- **Nowa zmienna `MONOLYNX_HOOK_ASK_UNSUPPORTED`** (domyślnie wyłączona, opt-in): zamienia decyzję `ask` na `deny` z wyjaśnieniem, dla klientów, które `ask` parsują, ale mimo to wykonują komendę (fail-open). To wybór użytkownika, nie zmiana domyślnej polityki.
- Wyniki weryfikacji runtime dla tej zmiany: `plugin/runtime-verification.md`, sekcja MON-184.

### 1.17.0

- MON-196: `mr-queue` domyka ticket przy zielonym CI brancha docelowego. Nowa podkomenda `mr_queue_state.py ticket-key --source-branch B --title T` wyznacza klucz ticketu (`[A-Z]+-[0-9]+`, branch źródłowy ma pierwszeństwo przed tytułem MR/PR). KROK 3a skilla `mr-queue`, gałąź `target-ci` = `success`: rozpoznany klucz przełącza ticket na `done` (`update_ticket` + `add_comment` z merge SHA i id pipeline'u brancha docelowego), z read-before-write, żeby nie dublować komentarza przy ticketach już `done`; brak klucza zostawia ticket nietknięty i jest odnotowany w raporcie ticka. Guard prefiksu projektu: `get_ticket` porównuje kod projektu w odpowiedzi z `<key>` - rozjazd (klucz z innego repo/projektu) traktowany jest jak brak klucza, bo `_resolve_ticket_uuid` dopasowuje ticket tylko po numerze i inaczej cicho przestawiłby cudzy, tak samo ponumerowany ticket. To wciąż jedno działanie na tick - `queue-update --status done` domyka pozycję kolejki dopiero po ticketcie.

### 1.16.2

- MON-191: `sprint_run_state.py` odporny na zgubioną spację w markerze `SPRINT-RUN STOP:` - `claude logs` czasem przesuwa kursor między słowami skokiem kolumny (`\x1b[NG`/`\x1b[NC`) zamiast wypisywać spację, więc marker przychodził jako `SPRINT-RUNSTOP:` i nie pasował. Skoki kolumny są teraz zamieniane na spację przed zdjęciem reszty ANSI, a dopasowanie markera toleruje zero albo więcej białych znaków między `SPRINT-RUN` i `STOP:`. Okno klasyfikacji (marker i heurystyka pytania) kotwiczy się do ostatniego bloku odpowiedzi asystenta (od ostatniej linii `⏺` do końca), nie do stałych 5 linii - `build_report` czyta ogon logu 60 linii zamiast 5, bo linie spinnera/statusu/promptu między kotwicą a treścią odpowiedzi potrafią być dłuższe niż to stałe okno. Podkomenda `state` w `sprint_run_tick.py` (MON-190) zapisuje ostatnie 60 niepustych linii logu (zamiast 20) i zamienia skoki kolumny na spację przed zdjęciem ANSI, tak samo jak `sprint_run_state.py`.

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
