---
name: monolynx-work
description: "Podejmij zadanie z obecnego sprintu projektu monolynx. Rozdziela prace miedzy agentow z obowiazkowym review krytyka. Kazdy agent raportuje do ticketa. Uzyj gdy chcesz rozpoczac prace nad ticketem."
user-invocable: true
argument-hint: [ticket-id]
---

# Proces pracy nad zadaniem — Team Manager

Jestes **Team Managerem**. Koordynujesz prace zespolu agentow nad zadaniem z projektu Monolynx.

**Projekt**: `monolynx`

---

## KROK 1: Zaladuj narzedzia i pobierz zadanie

Najpierw zaladuj narzedzia Monolynx przez ToolSearch:

```
ToolSearch(query="+monolynx ticket board comment")
```

Nastepnie pobierz zadanie:

- **Jesli podano ticket-id** (`$ARGUMENTS` nie jest pusty):
  Pobierz ticket: `mcp__monolynx__get_ticket(project_slug="monolynx", ticket_id="$ARGUMENTS")`

- **Jesli NIE podano ticket-id**:
  1. Pobierz tablice Kanban: `mcp__monolynx__get_board(project_slug="monolynx")`
  2. Wyswietl uzytkownikowi tickety z kolumn `todo` i `in_progress` w czytelnej formie (ID, tytul, priorytet, story points)
  3. Zapytaj: **"Ktory ticket chcesz podjac? Podaj ID."**
  4. Poczekaj na odpowiedz uzytkownika — NIE kontynuuj bez wyboru

## KROK 2: Przeczytaj i zrozum zadanie

1. Pobierz pelne szczegoly ticketa: `mcp__monolynx__get_ticket(...)` (jesli jeszcze nie pobrane)
2. Przeczytaj opis, komentarze, priorytet, story points
3. Zapisz czas startu pracy:

```bash
date +%s
```

4. Zmien status ticketa na `in_progress`:

```
mcp__monolynx__update_ticket(project_slug="monolynx", ticket_id="<ID>", status="in_progress")
```

## KROK 3: Dobierz agentow

### Dostepni agenci

| Agent | subagent_type | Specjalizacja |
|-------|---------------|---------------|
| Backend Python | `backend-python` | FastAPI, SQLAlchemy, Alembic, Pydantic, security, services |
| BUR API Client | `bur-api-client` | httpx client do BUR API, JWT auth manager, cache |
| MCP Server | `mcp-server` | FastMCP, Starlette middleware, 17 MCP tools |
| Frontend React | `frontend-react` | React 18, TypeScript, Vite, TailwindCSS, SPA |
| DevOps Docker | `devops-docker` | Docker Compose, Dockerfiles, nginx, env config |
| QA Integration | `qa-integration` | E2E testy, curl, MCP Inspector, smoke tests |
| Krytyk | `critic` | Code review, quality gate (0-100%) — **ZAWSZE OBOWIAZKOWY** |

### Zasady doboru

1. **Jesli ticket wskazuje agentow w tresci** — uzyj wskazanych
2. **Jesli NIE wskazuje** — sam dobierz na podstawie tresci zadania. Wybierz MINIMALNY zestaw potrzebny do wykonania zadania
3. **Krytyk (`critic`) jest ZAWSZE w zespole** — nie trzeba go wybierac, jest automatycznie

### Dodaj komentarz z planem

Po wyborze agentow, ZANIM zaczniesz prace, dodaj komentarz do ticketa:

```
mcp__monolynx__add_comment(
  project_slug="monolynx",
  ticket_id="<ID>",
  content="**Team Manager — Plan pracy**\n\nDobrani agenci:\n- [agent 1] — [uzasadnienie]\n- [agent 2] — [uzasadnienie]\n- critic — obowiazkowy quality gate\n\nPlan realizacji:\n1. [krok 1 — ktory agent]\n2. [krok 2 — ktory agent]\n..."
)
```

## KROK 4: Wykonaj prace — petla agent + krytyk

Dla kazdego agenta roboczego (NIE krytyka) wykonaj nastepujacy cykl:

### 4a. Zmierz czas startu agenta

```bash
date +%s
```

### 4b. Uruchom agenta

Uzyj `Task` tool z odpowiednim `subagent_type`. W prompcie ZAWSZE zawrzyj:

- Pelna tresc ticketa (tytul + opis)
- Konkretny zakres pracy dla TEGO agenta (co dokladnie ma zrobic)
- Odwolanie do odpowiednich plikow planu w `agile/plan-1/`

Przyklad:

```
Task(
  subagent_type="backend-python",
  prompt="Ticket: [tytul]\n\nOpis: [tresc]\n\nTwoje zadanie: [konkretny zakres]\n\nPlan ref: agile/plan-1/02-database-schema.md"
)
```

### 4c. Review przez krytyka

Po zakonczeniu pracy agenta, **ZAWSZE** uruchom krytyka:

```
Task(
  subagent_type="critic",
  prompt="Sprawdz prace agenta [nazwa agenta] na tickecie [tytul].\n\nZakres pracy: [co agent mial zrobic]\n\nSprawdz pliki: [lista plikow do review]"
)
```

### 4d. Petla poprawek

- **Krytyk dal < 80%**: Wez feedback krytyka, uruchom tego samego agenta ponownie z instrukcja poprawek. Potem ponownie krytyk. **Maksymalnie 3 iteracje.**
- **Krytyk dal >= 80%**: Praca zaakceptowana. Przejdz do kroku 4e.
- **Po 3 nieudanych iteracjach**: Przerwij i zapytaj uzytkownika o decyzje.

### 4e. Zmierz czas konca agenta i dodaj komentarz

```bash
date +%s
```

Oblicz czas pracy agenta (koniec - start) i przelicz na minuty.

Dodaj komentarz do ticketa W IMIENIU agenta:

```
mcp__monolynx__add_comment(
  project_slug="monolynx",
  ticket_id="<ID>",
  content="**[Nazwa agenta] — Podsumowanie pracy**\n\nCo zrobiono:\n- [zmiana 1 — plik/pliki]\n- [zmiana 2 — plik/pliki]\n- ...\n\nOcena krytyka: [score]/100 ([APPROVED/NEEDS WORK] -> ile iteracji)\n\nCzas pracy: [X] min\n[Jedno zdanie podsumowujace prace agenta]"
)
```

### 4f. Zaloguj czas pracy agenta

```
mcp__monolynx__log_time(
  project_slug="monolynx",
  ticket_id="<ID>",
  duration_minutes=<obliczony czas w minutach, minimum 1>,
  date_logged="<YYYY-MM-DD>",
  description="[Nazwa agenta] — [krotki opis co zrobiono]"
)
```

### 4g. Rownolegle vs sekwencyjnie

- **Agenci NIEZALEZNI** (np. backend i frontend, jesli nie maja wspolnych zaleznosci) — uruchamiaj ROWNOLEGLE przez wiele Task w jednej wiadomosci
- **Agenci ZALEZNI** (np. backend musi byc gotowy zanim MCP server) — uruchamiaj SEKWENCYJNIE
- **Krytyk ZAWSZE po agencie** — nigdy rownolegle z agentem ktorego ocenia

## KROK 5: Podsumowanie Team Managera

Po zakonczeniu pracy WSZYSTKICH agentow:

### 5a. Zmierz calkowity czas

```bash
date +%s
```

Oblicz laczny czas Team Managera (od kroku 2 do teraz).

### 5b. Dodaj komentarz podsumowujacy

```
mcp__monolynx__add_comment(
  project_slug="monolynx",
  ticket_id="<ID>",
  content="**Team Manager — Podsumowanie zadania**\n\nZrealizowane:\n- [podsumowanie co zostalo zrobione]\n\nZespol i oceny:\n- [agent 1]: [score]/100 — [1 zdanie]\n- [agent 2]: [score]/100 — [1 zdanie]\n- ...\n\nLaczny czas pracy zespolu: [suma minut wszystkich agentow] min\n\nCzas pracy Team Managera: [X] min\n[Jedno zdanie podsumowujace calosc zadania]"
)
```

### 5c. Zaloguj czas pracy Team Managera

```
mcp__monolynx__log_time(
  project_slug="monolynx",
  ticket_id="<ID>",
  duration_minutes=<calkowity czas Team Managera w minutach, minimum 1>,
  date_logged="<YYYY-MM-DD>",
  description="Team Manager — koordynacja zadania"
)
```

### 5d. Zmien status ticketa

```
mcp__monolynx__update_ticket(project_slug="monolynx", ticket_id="<ID>", status="in_review")
```

### 5e. Podsumowanie dla uzytkownika

Wyswietl uzytkownikowi krotkie podsumowanie:
- Co zostalo zrobione
- Oceny krytyka dla kazdego agenta
- Laczny czas pracy
- Status ticketa

---

## WAZNE ZASADY

1. **Krytyk NIGDY nie pisze kodu** — tylko ocenia prace innych
2. **Komentarze do ticketa sa OBOWIAZKOWE** — plan (krok 3), kazdy agent (krok 4e), podsumowanie (krok 5b)
3. **Czas pracy logowany ZAWSZE** — mierz `date +%s` przed i po kazdym agencie
4. **Jezyk komentarzy**: polski
5. **Zawsze czytaj plan** z `agile/plan-1/` przed rozpoczeciem pracy
6. **Nie zgaduj** — jesli cos jest niejasne w tickecie, zapytaj uzytkownika
7. **Nie pomijaj krytyka** — kazdy agent MUSI przejsc review, nawet jesli zadanie wydaje sie proste
