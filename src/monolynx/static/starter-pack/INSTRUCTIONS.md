# Monolynx Starter Pack — Instrukcja

Przewodnik po przygotowaniu projektu do pracy z Claude Code i platformą Monolynx.

---

## 1. Przygotowanie projektu

Na początku trzeba określić, co chcemy w projekcie zrobić.

**Świeży projekt (brak kodu):**
- Opisz cel projektu, stack technologiczny, główne moduły i funkcjonalności
- Spisz to do pliku (np. `PROJECT_BRIEF.md` lub bezpośrednio do `CLAUDE.md`)
- Claude Code użyje tego opisu jako kontekstu do generowania kodu i podejmowania decyzji architektonicznych

**Istniejący projekt (mamy już kod):**
- Jeśli w projekcie jest plik `CLAUDE.md` — Claude Code automatycznie go przeczyta i zrozumie kontekst
- Jeśli pliku `CLAUDE.md` nie ma — uruchom polecenie `/init`, które przeanalizuje projekt i wygeneruje `CLAUDE.md` z opisem architektury, komend, konwencji i struktury

---

## 2. Budowa agentów

Agenci to wyspecjalizowane role, które Claude Code może przyjmować podczas pracy nad zadaniami. Każdy agent ma swoją specjalizację i zestaw narzędzi.

### Krok 1: Określenie potrzebnych agentów

Poproś Claude Code o zaproponowanie zestawu agentów dopasowanych do projektu. Możesz też zgłosić własnych, których uważasz za ważnych.

Dla każdego agenta potrzebna jest:
- **Nazwa** — krótka, opisowa (np. `backend-developer`, `qa-tester`)
- **Opis** — jedno zdanie o specjalizacji agenta

### Krok 2: Tworzenie agentów

Użyj polecenia `/agents` w Claude Code. Każdego agenta tworzymy ręcznie, kopiując nazwę i opis. Claude Code wygeneruje pełną definicję agenta (plik `.md` w `.claude/agents/`).

### Dobór modelu

| Typ agenta | Rekomendowany model |
|---|---|
| Agenci developerzy (pisanie kodu, testy, frontend) | **Sonnet** — szybki i wystarczający |
| Agenci do badania kodu i zarządzania (code review, krytyk, Team Manager) | **Opus** — lepsze rozumienie kontekstu i ocena jakości |

---

## 3. Dobieranie skilli

Skille (umiejętności) to gotowe instrukcje i wzorce, które rozszerzają możliwości agentów.

### Krok 1: Zainstaluj `find-skills`

Jeśli nie masz zainstalowanego skilla `find-skills`, zainstaluj go globalnie:

```bash
npx skills add https://github.com/vercel-labs/skills --skill find-skills
```

### Krok 2: Znajdź skille dla agentów

Użyj polecenia `/find-skills` w Claude Code. Poproś, żeby znalazł odpowiednie skille dla każdego z Twoich agentów i zapisał je do pliku `install_skills.sh` w formacie:

```bash
npx skills install <nazwa-skilla>
```

Ze wszystkich agentów, których oferuje instalator, **wybieraj tylko Claude Code** jako docelowego agenta.

### Krok 3: Zainstaluj skille

Ręcznie odpal wygenerowany skrypt:

```bash
bash install_skills.sh
```

### Krok 4: Obsługa brakujących skilli

Czasami jakichś skilli nie ma w rejestrze. Wtedy powiedz o tym Claude Code i poproś go o zastąpienie ich innymi, podobnymi skillami.

### Krok 5: Przypisz skille do agentów

Po zainstalowaniu wszystkich skilli, poproś Claude Code, żeby zmienił pliki markdown agentów (`.claude/agents/*.md`), tak aby każdemu agentowi wskazać, z których skilli ma **zawsze** korzystać.

Po tych krokach jesteś gotowy do pracy.

---

## 4. Praca z ticketami

W zainstalowanych skillach dostępny jest skill `/monolynx-work`, który uruchamia pełny flow pracy nad zadaniem z projektu Monolynx.

### Wywołanie

```
/monolynx-work <ticket-id>
```

### Przykład

```
/monolynx-work 7
```

Skill automatycznie:
1. Pobiera ticket z Monolynx (tytuł, opis, priorytet, story points)
2. Zmienia status na `in_progress`
3. Sprawdza kontekst sprintu (co inni robią, czy są zależności)
4. Przeszukuje wiki projektu w poszukiwaniu powiązanej dokumentacji
5. Odczytuje graf zależności kodu (jeśli dostępny)
6. Dobiera agentów do zadania
7. Uruchamia agentów z obowiązkowym review krytyka
8. Każdy agent raportuje do ticketa (komentarz + zalogowany czas)
9. Aktualizuje wiki, jeśli praca wnosi nową wiedzę
10. Podsumowuje pracę i zmienia status ticketa na `in_review`

Pełny opis flow pracy znajdziesz w pliku skilla: `.claude/skills/monolynx-work/SKILL.md`

### Połączenie MCP

Dzięki połączeniu po MCP z platformą Monolynx, Claude Code może bezpośrednio zarządzać projektem — tworzyć sprinty, dodawać tickety, logować czas, aktualizować wiki i wiele więcej.

---

## 5. Dostępne narzędzia MCP (toole)

Poniżej lista wszystkich narzędzi dostępnych przez MCP w podziale na kategorie.

### Projekty

| Narzędzie | Opis |
|---|---|
| `list_projects` | Lista projektów, do których użytkownik jest przypisany |
| `get_project_summary` | Zagregowane statystyki projektu: otwarte błędy, monitory, aktywny sprint |

### Scrum — Tickety

| Narzędzie | Opis |
|---|---|
| `list_tickets` | Lista ticketów z filtrowaniem po statusie, priorytecie, sprincie, tekście |
| `get_ticket` | Szczegóły ticketa z komentarzami |
| `create_ticket` | Utwórz nowy ticket (oznaczany jako `created_via_ai`) |
| `update_ticket` | Aktualizuj ticket (status, priorytet, opis, sprint, assignee, story points) |
| `delete_ticket` | Usuń ticket |
| `create_ticket_from_issue` | Utwórz ticket powiązany z błędem 500ki (auto-wypełnia tytuł i opis) |

### Scrum — Sprinty

| Narzędzie | Opis |
|---|---|
| `list_sprints` | Lista sprintów ze statystykami (liczba ticketów, suma story points) |
| `get_sprint` | Szczegóły sprintu z listą ticketów |
| `create_sprint` | Utwórz nowy sprint (status: planning) |
| `start_sprint` | Rozpocznij sprint (tylko jeden aktywny na projekt) |
| `complete_sprint` | Zakończ sprint (niedokończone tickety wracają do backlogu) |

### Scrum — Tablica i komentarze

| Narzędzie | Opis |
|---|---|
| `get_board` | Tablica Kanban aktywnego sprintu (kolumny: todo, in_progress, in_review, done) |
| `list_comments` | Lista komentarzy do ticketa |
| `add_comment` | Dodaj komentarz do ticketa |
| `log_time` | Zaloguj czas pracy na tickecie |

### 500ki — Error Tracking

| Narzędzie | Opis |
|---|---|
| `list_issues` | Lista błędów z filtrowaniem po statusie i tekście |
| `get_issue` | Szczegóły błędu z ostatnimi 5 eventami (traceback, request, environment) |
| `update_issue_status` | Zmień status błędu (unresolved/resolved) |

### Monitoring

| Narzędzie | Opis |
|---|---|
| `list_monitors` | Lista monitorów URL z aktualnym statusem i uptime 24h |
| `get_monitor` | Szczegóły monitora z ostatnimi 20 checkami |

### Heartbeat

| Narzędzie | Opis |
|---|---|
| `list_heartbeats` | Lista heartbeatów z aktualnym statusem i URL do pingowania |
| `get_heartbeat` | Szczegóły heartbeatu (token, URL, status, period, grace) |
| `create_heartbeat` | Utwórz nowy heartbeat (zwraca URL do pingowania) |
| `update_heartbeat` | Aktualizuj konfigurację heartbeatu (period, grace) |
| `delete_heartbeat` | Usuń heartbeat |

### Wiki

| Narzędzie | Opis |
|---|---|
| `list_wiki_pages` | Lista stron wiki (drzewo z hierarchią) |
| `get_wiki_page` | Szczegóły strony wiki z pełną treścią markdown |
| `create_wiki_page` | Utwórz nową stronę wiki (opcjonalnie jako podstronę) |
| `update_wiki_page` | Aktualizuj stronę wiki |
| `delete_wiki_page` | Usuń stronę wiki wraz z podstronami |
| `search_wiki` | Wyszukiwanie semantyczne w wiki (RAG) |

### Connections — Graf zależności kodu

| Narzędzie | Opis |
|---|---|
| `list_graph_nodes` | Lista node'ów z filtrowaniem po typie i nazwie |
| `get_graph_node` | Szczegóły node'a z połączeniami do sąsiadów (konfigurowalna głębokość) |
| `create_graph_node` | Utwórz node (typy: File, Class, Method, Function, Const, Module) |
| `delete_graph_node` | Usuń node i wszystkie jego krawędzie |
| `create_graph_edge` | Utwórz krawędź (typy: CONTAINS, CALLS, IMPORTS, INHERITS, USES, IMPLEMENTS) |
| `delete_graph_edge` | Usuń krawędź |
| `bulk_create_graph_nodes` | Masowe tworzenie node'ów |
| `bulk_create_graph_edges` | Masowe tworzenie krawędzi |
| `query_graph` | Pobierz graf lub podgraf (node'y + krawędzie) |
| `find_graph_path` | Znajdź najkrótszą ścieżkę między dwoma node'ami |
| `get_graph_stats` | Statystyki grafu: liczba node'ów i krawędzi per typ |

## 6. Logowanie przez Google (OAuth)

Monolynx obsługuje logowanie przez Google. Aby je włączyć:

### Krok 1: Utwórz projekt w Google Cloud Console

1. Wejdź na [Google Cloud Console](https://console.cloud.google.com/)
2. Utwórz nowy projekt (lub wybierz istniejący)
3. Przejdź do **APIs & Services → Credentials**
4. Kliknij **Create Credentials → OAuth client ID**
5. Jeśli nie masz skonfigurowanego OAuth consent screen — kliknij **Configure Consent Screen**:
   - Wybierz **External** (lub Internal jeśli masz Google Workspace)
   - Podaj nazwę aplikacji (np. "Monolynx")
   - Dodaj **Authorized domains** (np. `monolynx.com`)
   - W **Scopes** dodaj: `email`, `profile`, `openid`
   - Zapisz

### Krok 2: Utwórz OAuth Client ID

1. **Application type**: Web application
2. **Name**: np. "Monolynx"
3. **Authorized redirect URIs**: dodaj URL callbacku:
   - Dla dev: `http://localhost:8000/auth/google/callback`
   - Dla produkcji: `https://twoja-domena.com/auth/google/callback`
4. Kliknij **Create** — skopiuj **Client ID** i **Client Secret**

### Krok 3: Ustaw zmienne środowiskowe

W pliku `.env` dodaj:

```env
GOOGLE_CLIENT_ID=123456789-xxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxxxx
```

### Krok 4: Zrestartuj aplikację

```bash
make down && make dev
```

### Jak to działa?

- Na stronie logowania pojawi się przycisk **Zaloguj się przez Google**
- Użytkownik **musi mieć wcześniej utworzone konto** w Monolynx (przez zaproszenie admina)
- Przy pierwszym logowaniu Google konto Google zostanie automatycznie powiązane z kontem Monolynx na podstawie adresu email
- Kolejne logowania będą automatyczne (po google_id)
- Jeśli zmienne `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` są puste — przycisk Google się nie pojawia

---

## 7. Notki końcowe

- Skopiuj `.mcp.json.example` do `.mcp.json`, zmień `https://open.monolynx.com/mcp/` na swój link, oraz wpisz swój token do `Authorization: Bearer osk_<...>`
- Token wygenerujesz i znajdziesz w zakładce `Tokeny API`