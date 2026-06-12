# Monolynx: plugin Claude Code

Plugin Monolynx pakuje w jeden, instalowalny zestaw to, czego potrzebujesz, żeby pracować z platformą Monolynx bezpośrednio z Claude Code:

- **11 skilli** dające komendy w przestrzeni nazw `/monolynx:*` (praca z ticketami w pełnym i uproszczonym flow, tworzenie i recenzja zadań, wyszukiwanie w wiki, metoda LLM Wiki - inicjalizacja, integracja źródeł, post-merge sync i audyt, pomoc, generowanie skryptu CI grafu zależności),
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
| `/monolynx:search` | Szukaj informacji w wiki projektu (architektura, API, integracje, standardy kodu) przez wyszukiwanie semantyczne; przy włączonej metodzie LLM Wiki proponuje zapis odpowiedzi jako stronę syntezy. |
| `/monolynx:wiki-init` | Włącz metodę LLM Wiki dla projektu: tworzy strony systemowe (regulamin `wiki-schema`, katalog `wiki-index`, dziennik `wiki-log`) i włącza flagę. Idempotentny bootstrap. |
| `/monolynx:wiki-ingest` | Zintegruj nowe źródło (plik, URL, wklejona treść) z wiki: strona źródła, aktualizacja powiązanych stron encji/konceptów, wikilinki, odświeżenie katalogu i wpis do dziennika. |
| `/monolynx:wiki-lint` | Audyt zdrowia wiki: wykrywa sieroty, martwe linki, sprzeczności i luki, prezentuje raport i proponuje naprawy. |
| `/monolynx:wiki-sync-merge` | Post-merge INGEST do wiki metodą LLM Wiki. Odpala człowiek po merge ticketów/PR do main - pobiera dane ticketów, integruje wiedzę z zamkniętych zadań, aktualizuje powiązane strony i odświeża katalog. |
| `/monolynx:help` | Wyświetl instrukcję użycia skilli Monolynx: flow pracy z ticketami oraz skille dodatkowe. |
| `/monolynx:create-graph-ci-script` | Wygeneruj skrypt CI synchronizujący graf zależności kodu z Monolynx (analiza projektu Python: Django, FastAPI, Flask), tworzy `cicd/sync_graph.py` i stage w `.gitlab-ci.yml`. |

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

- `src/monolynx/static/skills/`: źródło skilli pobieranych przez `install_monolynx_skills`,
- `src/monolynx/static/starter-pack/`: gotowy pakiet startowy do ręcznej instalacji.

Są one **alternatywą dla użytkowników niekorzystających z mechanizmu pluginów**, między innymi:

- dostęp przez claude.ai (web), bez CLI i bez możliwości dodania marketplace,
- środowiska, w których nie da się dodać marketplace ani zainstalować pluginu,
- ręczna instalacja skilli do `.claude/skills/` projektu (narzędzie zwraca treść skilla z podmienionymi placeholderami `<PROJECT-SLUG>` i `<PROJECT-ID>`, gotową do zapisu na dysk).

Innymi słowy:

- **Plugin**: preferowana, jednorazowa instalacja dla Claude Code CLI (skille + agenci + zdalny MCP w jednym).
- **`install_monolynx_skills`**: ścieżka manualna / fallback dla środowisk bez pluginów.

### Serwer MCP bez zmian funkcjonalnych

`src/monolynx/mcp_server.py` **pozostaje bez zmian funkcjonalnych**. Plugin nie modyfikuje serwera ani nie odwołuje się do jego wewnętrznych funkcji. W pliku `.mcp.json` pluginu deklaruje wyłącznie dostęp (HTTP + Bearer) do **istniejącego** serwera MCP pod adresem `${user_config.mcp_endpoint}`. Cała logika narzędzi (Scrum, 500ki, Monitoring, Wiki, Połączenia, Plan pracy, w tym samo `install_monolynx_skills`) działa po stronie serwera, niezależnie od tego, czy łączysz się przez plugin, czy w inny sposób.

## Changelog

### 1.2.0

- **Instrumentacja pipeline w skillach `work` i `work-simple`**: każdy krok pracy raportuje przebieg do nowego modułu Pipelines (obserwowalność pracy agentów, wzorowana na GitLab CI/CD). Tworzy się pipeline `ticket_work` ze stepami research → coding → wrap-up, każdy agent dostaje swój job, a jego raport (co zrobił, decyzje, pliki) zapisywany jest jako strona wiki podpięta pod job. Status, czas trwania i logi widać na żywo w zakładce "Pipelines" projektu.
- Raportowanie jest **best-effort**: błąd toola pipeline nigdy nie przerywa pracy nad ticketem; gdy serwer MCP nie ma modułu Pipelines (starsza wersja), skille działają jak dotychczas.
- **Wymaga nowych narzędzi MCP na serwerze** (Monolynx >= moduł Pipelines): `create_pipeline`, `create_pipeline_job`, `update_pipeline_job`, `append_job_log`, `finish_pipeline`, `list_pipelines`, `get_pipeline`, `get_pipeline_job_log`.

### 1.1.x

- Skille LLM Wiki (`wiki-init`, `wiki-ingest`, `wiki-lint`, `wiki-sync-merge`), Spec-Driven Development (`spec_page_id`), pakiet skilli i agentów oraz zdalny dostęp MCP.
