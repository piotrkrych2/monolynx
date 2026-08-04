# Monolynx: plugin Claude Code

Plugin Monolynx pakuje w jeden, instalowalny zestaw to, czego potrzebujesz, żeby pracować z platformą Monolynx bezpośrednio z Claude Code:

- **13 skilli** dające komendy w przestrzeni nazw `/monolynx:*` (praca z ticketami w pełnym i uproszczonym flow, tworzenie i recenzja zadań, zamknięcie sprintu, wyszukiwanie w wiki, metoda LLM Wiki - inicjalizacja, integracja źródeł, post-merge sync i audyt, pomoc, generowanie skryptu CI grafu zależności, lokalna synchronizacja grafu),
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
| `mcp_token` | tak | brak | Token API Monolynx (`osk_...`). Oznaczony jako `sensitive`, trafia do keychain systemu, nie jest zapisywany jako zwykły tekst. Trafia do nagłówka `Authorization: Bearer <token>` zdalnego serwera MCP. |
| `mcp_endpoint` | nie | `https://monolynx.com/mcp` | URL zdalnego serwera MCP. Zmień tylko jeśli korzystasz z własnej instancji Monolynx. |
| `project_slug` | nie | brak | Domyślny slug projektu. Używany jako fallback, gdy w projekcie nie ma `MONOLYNX_PROJECT_SLUG`. |

### Token API (`mcp_token`)

1. Zaloguj się do Monolynx i otwórz `/dashboard/profile/tokens`.
2. Utwórz nowy token, skopiuj wartość `osk_...` (pokazywana raz).
3. Wklej ją podczas konfiguracji pluginu. Ponieważ pole jest `sensitive`, Claude Code zapisze token w keychain, a nie w plikach konfiguracyjnych w repozytorium.

Token możesz w każdej chwili unieważnić w tym samym panelu (`/revoke`). Po unieważnieniu wygeneruj nowy i zaktualizuj konfigurację pluginu.

### Punkt końcowy MCP (`mcp_endpoint`)

Domyślnie plugin łączy się z `https://monolynx.com/mcp`. Połączenie jest typu HTTP, z autoryzacją Bearer (`${user_config.mcp_token}`). Adres podmień tylko dla self-hosted instancji.

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
| `/monolynx:work` | Podejmij zadanie z aktualnego sprintu: walidacja brancha, research, dobór zespołu agentów i praca równoległa z obowiązkowym krytykiem. Raportuje przebieg pracy do modułu Pipelines (obserwowalność): pipeline `ticket_work` ze stepami research → coding → wrap-up, job per agent, log każdego agenta jako strona wiki. Raportowanie best-effort - nie blokuje pracy. |
| `/monolynx:work-simple` | Uproszczony flow dla mniejszych ticketów (<8 SP): jeden dobrany dev + krytyk jako zwykłe subagenty (bez Agent Teams), research opt-in, pełna ceremonia self-reporting, eskalacja do `/monolynx:work` gdy scope się rozrasta. Raportuje do modułu Pipelines (uproszczona instrumentacja: dev + krytyk), best-effort. |
| `/monolynx:ticket-create` | Utwórz nowy ticket: zbiera kontekst z wiki, kodu i grafu zależności, generuje opis w ustalonej formie (cel, kontekst, zakres, kryteria akceptacji, zależności). |
| `/monolynx:ticket-review` | Zrecenzuj ticket ze sprintu: sprawdza formę, zgodność z wiki i kodem, generuje tabelkę raportu i proponuje poprawki. |
| `/monolynx:sprint-end` | Zamknij sprint jako pipeline `sprint_close` (stepy wiki-update → wrap-up): INGEST logów pracy do wiki, LINT (audyt wiki), czyszczenie stron logów pipeline sprintu i realne `complete_sprint` (niedokończone tickety wracają do backlogu, z potwierdzeniem). Raportowanie do Pipelines best-effort. |
| `/monolynx:search` | Szukaj informacji w wiki projektu (architektura, API, integracje, standardy kodu) przez wyszukiwanie semantyczne; przy włączonej metodzie LLM Wiki proponuje zapis odpowiedzi jako stronę syntezy. |
| `/monolynx:wiki-init` | Włącz metodę LLM Wiki dla projektu: tworzy strony systemowe (regulamin `wiki-schema`, katalog `wiki-index`, dziennik `wiki-log`) i włącza flagę. Idempotentny bootstrap. |
| `/monolynx:wiki-ingest` | Zintegruj nowe źródło (plik, URL, wklejona treść) z wiki: strona źródła, aktualizacja powiązanych stron encji/konceptów, wikilinki, odświeżenie katalogu i wpis do dziennika. |
| `/monolynx:wiki-lint` | Audyt zdrowia wiki: wykrywa sieroty, martwe linki, sprzeczności i luki, prezentuje raport i proponuje naprawy. |
| `/monolynx:wiki-sync-merge` | Post-merge INGEST do wiki metodą LLM Wiki. Odpala człowiek po merge ticketów/PR do main - pobiera dane ticketów, integruje wiedzę z zamkniętych zadań, aktualizuje powiązane strony i odświeża katalog. |
| `/monolynx:help` | Wyświetl instrukcję użycia skilli Monolynx: flow pracy z ticketami oraz skille dodatkowe. |
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

`src/monolynx/mcp_server.py` **pozostaje bez zmian funkcjonalnych**. Plugin nie modyfikuje serwera ani nie odwołuje się do jego wewnętrznych funkcji. W pliku `.mcp.json` pluginu deklaruje wyłącznie dostęp (HTTP + Bearer) do **istniejącego** serwera MCP pod adresem `${user_config.mcp_endpoint}`. Cała logika narzędzi (Scrum, 500ki, Monitoring, Wiki, Połączenia, Plan pracy, w tym samo `install_monolynx_skills`) działa po stronie serwera, niezależnie od tego, czy łączysz się przez plugin, czy w inny sposób.

## Changelog

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
