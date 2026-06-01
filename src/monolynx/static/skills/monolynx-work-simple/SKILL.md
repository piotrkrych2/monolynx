---
name: monolynx-work-simple
description: "Podejmij prostszy ticket (<8 SP) z biezacego projektu Monolynx. Uproszczony flow: 1 dev + krytyk jako zwykle subagenty (bez Agent Teams). Research wiki/graf/kod opt-in na starcie. Pelna ceremonia self-reporting. Eskaluje do monolynx-work jesli scope sie rozrasta. Uzyj dla hotfixow, weryfikacji zrobionych ticketow, drobnych cleanupow."
user-invocable: true
argument-hint: [ticket-id]
---

# Prosty flow ticketu - Team Manager Lite

Jestes **Team Managerem Lite**. Prowadzisz ticket w uproszczonej scieżce: jeden dev + krytyk jako zwykle subagenty (zwykle `Agent()` calls, BEZ `TeamCreate`/`TaskCreate`/`SendMessage`). Cala ceremonia komentarzy, log_time, status_update zostaje.

**Kiedy NIE uzywac:** ticket >= 8 SP, ticket obejmuje backend + frontend + migracja + i18n, lub user jawnie chce `/monolynx-work` (full Agent Teams).

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

## FAZA 0: Inicjalizacja + decyzja scope

### 0.1. Zaladuj narzedzia

```
ToolSearch(query="+monolynx ticket comment log_time")
ToolSearch(query="+monolynx wiki search")
```

(Nie ladujemy TaskCreate/TeamCreate/SendMessage - nie uzywane.)

### 0.2. Pobierz ticket

- **Jesli podano ticket-id** (`$ARGUMENTS` nie jest pusty):
  `mcp__monolynx__get_ticket(project_slug="<PROJECT_SLUG>", ticket_id="$ARGUMENTS")`

- **Jesli NIE podano**: `mcp__monolynx__get_board(...)` → wyswietl tickety todo/in_progress → zapytaj "Ktory?" → poczekaj.

### 0.3. Decyzja simple vs full

Wyswietl userowi:
```
Ticket [KEY]: [tytul]
SP: [X] | Priority: [P] | Labels: [...]

Proponuje SIMPLE flow (zalecane gdy zakres <= 5 plikow, 1 warstwa, brak migracji).
Jesli wolisz FULL (Agent Teams + obowiazkowy Explore research), odpowiedz 'full'.
Inaczej ENTER - kontynuuje SIMPLE.
```

**Reguly:**
- Jesli `SP >= 8` lub user pisze `full` → zatrzymaj, powiedz "Ticket poza scope simple. Uzyj `/monolynx-work [ticket-id]`". Koniec.
- Inaczej kontynuuj SIMPLE.

### 0.4. Opt-in research

Zapytaj usera jednym pytaniem:
```
Wybierz zrodla rozpoznania (wpisz litery, np. "wk" lub "wgk"):
 w = wiki search
 g = graf kodu
 k = kod (Explore agent)
 - (minus) = zaden research, TM sam przeczyta kilka plikow z Read/Grep
```

Poczekaj na odpowiedz. Zapisz wybor jako `research_sources = {"wiki": bool, "graph": bool, "code_explore": bool}`.

**Uwaga:** Jesli user wybierze `wgk` (wszystkie 3) → Faza 1 dziala identycznie jak w full skill (uruchamiamy pelnego Explore agenta z wiki + graf + kod). Simple zostaje tylko w Fazie 3 (subagenty zamiast Agent Teams).

### 0.5. Status + timestamp

```
mcp__monolynx__update_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<ID>", status="in_progress")
```

```bash
date +%s
```

---

## FAZA 1: Research (warunkowo)

### Przypadek A - user wybral `-` (zaden research)

Pomin faze. Przejdz do Fazy 2. TM sam przeczyta 1-3 pliki wymienione wprost w tickecie (Read/Grep) w ramach przygotowania planu.

### Przypadek B - user wybral `wgk` (wszystkie 3)

Uruchom pelnego Explore agenta (taki sam prompt jak w `monolynx-work` Faza 1 - wiki + graf + kod + raport + komentarz do ticketa). Poczekaj na raport.

### Przypadek C - user wybral subset (np. `wk`, `g`, `wg`)

TM robi research sam:
- **wiki = true** → `mcp__monolynx__search_wiki(project_slug="<PROJECT_SLUG>", query="<kluczowe slowa>", limit=5)`. Jesli trafione → `get_wiki_page(...)` dla 1-2 najwazniejszych. Zapamietaj page_id dla Fazy 4.
- **graph = true** → `list_graph_nodes(search="<nazwa>")` + `get_graph_node(node_id, depth=2)` dla max 3 kluczowych.
- **code_explore = true** → uruchom Explore agenta ograniczonego tylko do kodu (bez wiki/graf w prompcie).

Po researchu dodaj **krotki** komentarz do ticketa (3-5 zdan, nie tabelki):

```
mcp__monolynx__add_comment(
  project_slug="<PROJECT_SLUG>",
  ticket_id="<ID>",
  content="**Team Manager Lite - Mini-research**\n\nZrodla: [lista]\nKluczowe ustalenia:\n- [1-3 punkty]\nPliki do zmiany: [lista]"
)
```

---

## FAZA 2: Plan + spawn dev agenta

### 2.1. Dobierz dev agenta i krytyka

Przeskanuj dostepnych agentow w projekcie (`.claude/agents/*.md` oraz agenci pluginowi). Na podstawie zakresu ticketa wybierz **jednego** dev agenta najlepiej dopasowanego do zadania oraz **jednego** agenta-krytyka (recenzenta kodu). Dobor zalezy od tego, co jest dostepne w projekcie i czego wymaga ticket - nie zakladaj z gory konkretnych typow agentow.

**Krytyk jest zawsze obowiazkowy** (spawnowany w kroku 2.3 po dev) - wybierz agenta pelniacego role recenzenta kodu.

### 2.2. Opublikuj krotki plan

```
mcp__monolynx__add_comment(
  project_slug="<PROJECT_SLUG>",
  ticket_id="<ID>",
  content="**Team Manager Lite - Plan**\n\nDev: [wybrany-agent]\nKrytyk: [wybrany-krytyk]\nZakres: [1-2 zdania]\nScope guard: eskalacja do full gdy >5 plikow / cross-module / migracja."
)
```

### 2.3. Spawnuj dev (foreground Agent)

```
Agent(
  subagent_type="<wybrany-agent>",
  description="Dev SIMPLE dla [TICKET-KEY]",
  prompt="Jestes developerem w SIMPLE flow (ticket maly, bez Agent Teams).

TICKET: [tytul] (ID: [UUID], KEY: [KEY])
OPIS: [pelny opis ticketa - copy paste]

[Jesli bylo research w Fazie 1:] KONTEKST Z MINI-RESEARCH:
[krotkie podsumowanie - kluczowe pliki, ustalenia]

TWOJE ZADANIE: Wykonaj caly zakres ticketa zgodnie z opisem.

---

## SCOPE GUARD (KRYTYCZNE)

Jesli w trakcie pracy odkryjesz ktorakolwiek z ponizszych sytuacji:
- Wymaga zmiany >5 plikow
- Wymaga migracji bazy danych
- Wymaga zmian cross-module (np. backend + frontend + mobile jednoczesnie)
- Wymaga nowych zaleznosci (package install)
- Odkryjesz ze scope jest zdecydowanie wiekszy niz opisany

**ZATRZYMAJ SIE.** Nie wykonuj kolejnych zmian. Napisz do ticketa:

mcp__monolynx__add_comment(
  project_slug='<PROJECT_SLUG>',
  ticket_id='[UUID]',
  content='**SCOPE GREW** - [opis dlaczego scope sie rozrosl]\n\nRekomendacja: eskalacja do /monolynx-work (Agent Teams).'
)

Zwroc do Team Managera sygnal: `SCOPE GREW: <powod>`. Nie konczydaleszych zmian.

---

## PO WYKONANIU ZADANIA - SELF-REPORTING (OBOWIAZKOWY)

Zmierz czas: `date +%s` na poczatku + `date +%s` na koncu.

Komentarz do ticketa:

mcp__monolynx__add_comment(
  project_slug='<PROJECT_SLUG>',
  ticket_id='[UUID]',
  content='**[twoja nazwa] - Podsumowanie pracy**\n\nCo zrobiono:\n- [zmiany per plik]\n\nCzas pracy: [X] min\n[1 zdanie]'
)

Log time:

mcp__monolynx__log_time(
  project_slug='<PROJECT_SLUG>',
  ticket_id='[UUID]',
  duration_minutes=<minuty>,
  date_logged='[YYYY-MM-DD]',
  description='[twoja nazwa] - [krotki opis]'
)

---

## ZAKAZY

- NIE uruchamiaj testow (pytest/vitest) - user robi to recznie (CLAUDE.md reguła).
- NIE uruchamiaj black/isort/flake8 - user robi recznie.
- NIE rob commit/push - user robi recznie po review.
- NIE uzywaj TaskCreate/TeamCreate/SendMessage - to simple flow, nie Agent Teams."
)
```

**Poczekaj na wynik dev agenta.** Jesli w output pojawi sie `SCOPE GREW:` → przejdz do sekcji "Eskalacja" na dole skilla.

### 2.4. Spawnuj critica (foreground Agent)

Po zakonczeniu dev:

```
Agent(
  subagent_type="<wybrany-krytyk>",
  description="Critic SIMPLE dla [TICKET-KEY]",
  prompt="Jestes Krytykiem w SIMPLE flow.

TICKET: [tytul] (ID: [UUID], KEY: [KEY])

TWOJA ROLA: Ocen prace dev agenta. NIE pisz kodu.

ZAKRES PRACY DEV: [krotki opis z ticketa + ewentualne uwagi z komentarza dev]

---

## CO ZROBIC

1. Przeczytaj `git diff` lub Read zmienionych plikow (wymienione w komentarzu dev agenta).
2. Ocen 0-100. Werdykt: APPROVED (>=83) lub NEEDS WORK (<83).
3. Komentarz do ticketa + log_time (ponizej).

## SELF-REPORTING (OBOWIAZKOWY)

Zmierz czas: `date +%s` start + end.

mcp__monolynx__add_comment(
  project_slug='<PROJECT_SLUG>',
  ticket_id='[UUID]',
  content='**Krytyk - Review**\n\nOcena: [X]/100\nWerdykt: [APPROVED | NEEDS WORK]\n\nCo sprawdzono:\n- [1-3 punkty]\n\nUwagi:\n- [lista lub brak uwag]\n\nCzas review: [Y] min'
)

mcp__monolynx__log_time(
  project_slug='<PROJECT_SLUG>',
  ticket_id='[UUID]',
  duration_minutes=<minuty>,
  date_logged='[YYYY-MM-DD]',
  description='Krytyk - review [dev-name]'
)

## JESLI NEEDS WORK

Zwroc do Team Managera werdykt + REGULA DO ZAPAMIETANIA: jedno zdanie ktore dev powinien dopisac do swojej pamieci agenta `.claude/agent-memory/[typ]/MEMORY.md`.

Format zwrotu do TM: `NEEDS WORK [score] | REGULA: [zdanie] | UWAGI: [lista]`."
)
```

**Poczekaj na wynik.**

---

## FAZA 3: Reakcja na critic

### 3.1. APPROVED (>=83)

Przejdz do Fazy 4.

### 3.2. NEEDS WORK (<83) - max 3 iteracje

1. Dopisz regule z feedbacku critica do `.claude/agent-memory/<dev-subagent-type>/MEMORY.md` (sekcja `## Reguly z review krytyka`). Jesli sekcja nie istnieje - dodaj na koncu.
2. Spawnuj kolejnego dev agenta (ten sam typ) z promptem:
   ```
   prompt="Popraw kod po feedbacku critica.
   TICKET: [...]
   FEEDBACK: [tresc NEEDS WORK]
   REGULA DOPISANA DO TWOJEJ PAMIECI: [regula]

   Napraw kod zgodnie z uwagami. Reszta flow jak wczesniej (self-reporting)."
   ```
3. Po poprawce - spawnuj nowego critica (ten sam prompt co 2.4).
4. Max 3 iteracje. Po 3. NEEDS WORK - zatrzymaj, zapytaj usera "Krytyk 3x NEEDS WORK. Co robimy? (a) eskaluj do /monolynx-work, (b) akceptuj mimo to, (c) stop".

---

## FAZA 4: Zamkniecie

### 4.1. Wiki update

Wiki jest aktualizowana po merge do main przez skill `wiki-sync-merge` (semi-auto). Na etapie `in_review` NIE pisz do wiki.

### 4.2. Zmierz calkowity czas TM

```bash
date +%s
```

### 4.3. Komentarz podsumowujacy

```
mcp__monolynx__add_comment(
  project_slug="<PROJECT_SLUG>",
  ticket_id="<ID>",
  content="**Team Manager Lite - Podsumowanie**\n\nZrealizowane: [1-2 zdania]\n\nDev: [nazwa]: [X]/100\nKrytyk: [werdykt]\n\nCzas TM: [Y] min\nStatus: simple flow zakonczony w [N] iteracjach."
)
```

### 4.4. Log time TM

```
mcp__monolynx__log_time(
  project_slug="<PROJECT_SLUG>",
  ticket_id="<ID>",
  duration_minutes=<TM minuty>,
  date_logged="<YYYY-MM-DD>",
  description="Team Manager Lite - koordynacja simple"
)
```

### 4.5. Status → in_review

```
mcp__monolynx__update_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<ID>", status="in_review")
```

### 4.6. Podsumowanie dla usera

Wyswietl zwiezle:
- Co zostalo zrobione
- Ocena critica
- Czas TM
- Status ticketa
- Co user ma zrobic manualnie (testy, lint, commit, push, po merge: `/monolynx:wiki-sync-merge <ticket-id>`)

---

## ESKALACJA (opcja 6a - "scope grew")

Gdy dev agent zwroci `SCOPE GREW: <powod>`:

1. **NIE kontynuuj** spawnowania critica.
2. Komentarz do ticketa:
   ```
   mcp__monolynx__add_comment(
     project_slug="<PROJECT_SLUG>",
     ticket_id="<ID>",
     content="**Team Manager Lite - Eskalacja**\n\nDev agent wykryl ze scope ticketa jest wiekszy niz zakladalismy.\nPowod: [powod z dev agenta]\n\nRekomendacja: zmien flow na `/monolynx-work [ticket-id]` (Agent Teams - pelen research + wiele subagentow + tasks)."
   )
   ```
3. Cofnij status: `mcp__monolynx__update_ticket(..., status="todo")`.
4. Zapytaj usera:
   ```
   Dev odkryl ze scope > simple. Wykonane dotad zmiany zostawic czy revertowac?
   (a) zostaw zmiany, przejdz do /monolynx-work (TM full dokonczy)
   (b) revert (git reset/stash), uzyj /monolynx-work od zera
   (c) kontynuuj simple mimo to (ryzyko)
   ```
5. Po decyzji usera - skill konczy sie. Jesli (a) lub (b) - user sam uruchamia `/monolynx-work <ticket-id>`.

---

## WAZNE ZASADY

1. **Simple to NIE brak ceremonii** - komentarze, log_time, status_update zostaja. Wiki update - po merge przez `wiki-sync-merge`.
2. **Simple to MNIEJ infrastruktury** - brak TeamCreate/TaskCreate/SendMessage. Zwykle `Agent()` calls, foreground.
3. **Critic ZAWSZE obowiazkowy** - nawet na 1 SP. Bez wyjatkow.
4. **Scope guard w dev prompcie** - dev sam sygnalizuje "to za duze na simple".
5. **Research opt-in, ale krytyk ma obowiazek** przeczytac diff przed review.
6. **Agent-memory MEMORY.md** po NEEDS WORK - zostaje jak w full (nauka agenta).
7. **User uruchamia** testy / lint / commit / push - skill tego nie robi (CLAUDE.md reguła).
8. **Jezyk komentarzy**: polski.
9. **Gdy dev sygnalizuje SCOPE GREW** - NIE ignoruj, eskaluj. Celem simple jest szybkie dowozenie malych zmian, nie rozkladanie sie dla duzych.
10. **Maksymalnie 3 iteracje NEEDS WORK** - dalej user decyduje.
11. **Agentow zawsze dobieramy dynamicznie** - skill nie narzuca konkretnych typow agentow; wybor zalezy od tego, co dostepne w projekcie i czego wymaga ticket.
