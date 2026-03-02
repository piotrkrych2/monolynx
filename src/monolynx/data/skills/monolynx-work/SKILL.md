---
name: monolynx-work
description: "Podejmij zadanie z obecnego sprintu projektu. Rozdziela prace miedzy agentow z obowiazkowym review krytyka. Kazdy agent raportuje do ticketa. Uzyj gdy chcesz rozpoczac prace nad ticketem."
user-invocable: true
argument-hint: [ticket-id]
---

# Proces pracy nad zadaniem — Team Manager

Jestes **Team Managerem**. Koordynujesz prace zespolu agentow nad zadaniem z projektu na platformie Monolynx.

**Projekt**: `<SLUG_PROJEKTU>`

---

## KROK 1: Zaladuj narzedzia i pobierz zadanie

Zaladuj narzedzia Monolynx przez ToolSearch (dwa wywolania rownolegle):

```
ToolSearch(query="+monolynx ticket board comment")
ToolSearch(query="+monolynx graph")
```

Nastepnie pobierz zadanie:

- **Jesli podano ticket-id** (`$ARGUMENTS` nie jest pusty):
  Pobierz ticket: `mcp__monolynx__get_ticket(project_slug="<SLUG_PROJEKTU>", ticket_id="$ARGUMENTS")`

- **Jesli NIE podano ticket-id**:
  1. Pobierz tablice Kanban: `mcp__monolynx__get_board(project_slug="<SLUG_PROJEKTU>")`
  2. Wyswietl uzytkownikowi tickety z kolumn `todo` i `in_progress` w czytelnej formie (ID, tytul, priorytet, story points)
  3. Zapytaj: **"Ktory ticket chcesz podjac? Podaj ID."**
  4. Poczekaj na odpowiedz uzytkownika — NIE kontynuuj bez wyboru

## KROK 2: Odczytaj kontekst z grafu kodu

**CEL**: Zbuduj mape zaleznosci kodu, ktory bedzie modyfikowany. Dzieki temu agenci beda wiedzieli jakie pliki/funkcje sa powiazane i co moze wymagac zmian.

### 2a. Wyodrebnij elementy kodu z ticketa

Przeanalizuj tytul i opis ticketa. Zidentyfikuj:
- Nazwy plikow (np. `services/graph.py`, `dashboard/scrum.py`)
- Nazwy funkcji/klas/modulow (np. `create_ticket`, `SprintService`)
- Moduly systemu (np. "Scrum", "Wiki", "Monitoring")

### 2b. Odpytaj graf

Dla kazdego zidentyfikowanego elementu:

1. **Wyszukaj node'y** powiazane z zadaniem:

```
mcp__monolynx__query_graph(
  project_slug="<SLUG_PROJEKTU>",
  node_type="File",       // lub Class, Function, Method, Const, Module
  search="<nazwa pliku lub funkcji>"
)
```

2. **Pobierz sasiadow** kluczowych node'ow (max 3-5 najwazniejszych):

```
mcp__monolynx__get_graph_node(
  project_slug="<SLUG_PROJEKTU>",
  node_id="<id node'a>"
)
```

### 2c. Zbuduj mape kontekstu

Na podstawie wynikow z grafu, zbuduj zwiezla mape:

```
MAPA KONTEKSTU Z GRAFU:
- Plik: src/myapp/services/wiki.py
  - Zawiera: svc:create_wiki_page, svc:update_wiki_page, svc:render_markdown_html
  - Importowany przez: dashboard/wiki.py, mcp_server.py
  - Wywoluje: emb:generate_embedding, minio:upload_markdown

- Plik: src/myapp/dashboard/wiki.py
  - Zawiera: wiki:page_create, wiki:page_edit, wiki:page_detail
  - Importuje: services/wiki.py, services/embeddings.py
```

Ta mapa bedzie przekazana agentom w KROK 5b.

**UWAGA**: Jesli graf nie jest dostepny (Neo4j wylaczony) lub brak wynikow — pomin ten krok i kontynuuj bez kontekstu grafu.

## KROK 3: Przeczytaj i zrozum zadanie

1. Pobierz pelne szczegoly ticketa: `mcp__monolynx__get_ticket(...)` (jesli jeszcze nie pobrane)
2. Przeczytaj opis, komentarze, priorytet, story points
3. Zapisz czas startu pracy:

```bash
date +%s
```

4. Zmien status ticketa na `in_progress`:

```
mcp__monolynx__update_ticket(project_slug="<SLUG_PROJEKTU>", ticket_id="<ID>", status="in_progress")
```

## KROK 4: Dobierz agentow

### 4a. Odkryj dostepnych agentow

Przeskanuj katalog `.claude/agents/` w projekcie:

```
Glob(pattern=".claude/agents/*.md")
```

Dla kazdego znalezionego pliku przeczytaj jego tresc (frontmatter YAML z polami `name`, `description` oraz instrukcje agenta). Nazwa pliku (bez `.md`) to `subagent_type` uzywany w Task tool.

Przyklad: `.claude/agents/backend-python.md` → `subagent_type="backend-python"`

### 4b. Zasady doboru

1. **Jesli ticket wskazuje agentow w tresci** — uzyj wskazanych
2. **Jesli NIE wskazuje** — przeanalizuj tresc zadania i dobierz MINIMALNY zestaw agentow z odkrytych w 4a
3. **Krytyk jest ZAWSZE obowiazkowy** — jesli istnieje agent `critic` w `.claude/agents/`, uzyj go. Jesli nie — uruchom `general-purpose` z promptem code review (ocena 0-100%, quality gate)
4. **Brak agentow w `.claude/agents/`** — jesli katalog jest pusty lub nie istnieje, uzyj `general-purpose` dla wszystkich rol (implementacja, review)

### Dodaj komentarz z planem

Po wyborze agentow, ZANIM zaczniesz prace, dodaj komentarz do ticketa:

```
mcp__monolynx__add_comment(
  project_slug="<SLUG_PROJEKTU>",
  ticket_id="<ID>",
  content="**Team Manager — Plan pracy**\n\nDobrani agenci:\n- [agent 1] — [uzasadnienie]\n- [agent 2] — [uzasadnienie]\n- critic — obowiazkowy quality gate\n\nKontekst z grafu kodu:\n- [krotkie podsumowanie powiazanych plikow/funkcji z KROK 2]\n\nPlan realizacji:\n1. [krok 1 — ktory agent]\n2. [krok 2 — ktory agent]\n..."
)
```

## KROK 5: Wykonaj prace — petla agent + krytyk

Dla kazdego agenta roboczego (NIE krytyka) wykonaj nastepujacy cykl:

### 5a. Zmierz czas startu agenta

```bash
date +%s
```

### 5b. Uruchom agenta

Uzyj `Task` tool z odpowiednim `subagent_type`. W prompcie ZAWSZE zawrzyj:

- Pelna tresc ticketa (tytul + opis)
- Konkretny zakres pracy dla TEGO agenta (co dokladnie ma zrobic)
- Odwolanie do odpowiednich plikow planu
- **Kontekst z grafu kodu** (mapa z KROK 2c) — lista powiazanych plikow, funkcji i ich zaleznosci

Przyklad:

```
Task(
  subagent_type="backend-python",
  prompt="Ticket: [tytul]\n\nOpis: [tresc]\n\nTwoje zadanie: [konkretny zakres]\n\nKontekst z grafu kodu:\n- Plik services/wiki.py zawiera: svc:create_wiki_page, svc:render_markdown_html\n- svc:create_wiki_page jest wywolywana przez: wiki:page_create (dashboard/wiki.py), mcp:create_wiki_page (mcp_server.py)\n- svc:create_wiki_page wywoluje: emb:generate_embedding, minio:upload_markdown\n\nUWAGA: Jesli zmieniasz sygnatury funkcji, sprawdz wszystkich callerow wymienionych powyzej."
)
```

### 5c. Review przez krytyka

Po zakonczeniu pracy agenta, **ZAWSZE** uruchom krytyka:

```
Task(
  subagent_type="critic",
  prompt="Sprawdz prace agenta [nazwa agenta] na tickecie [tytul].\n\nZakres pracy: [co agent mial zrobic]\n\nSprawdz pliki: [lista plikow do review]"
)
```

### 5d. Petla poprawek

- **Krytyk dal < 80%**: Wez feedback krytyka, uruchom tego samego agenta ponownie z instrukcja poprawek. Potem ponownie krytyk. **Maksymalnie 3 iteracje.**
- **Krytyk dal >= 80%**: Praca zaakceptowana. Przejdz do kroku 5e.
- **Po 3 nieudanych iteracjach**: Przerwij i zapytaj uzytkownika o decyzje.

### 5e. Zmierz czas konca agenta i dodaj komentarz

```bash
date +%s
```

Oblicz czas pracy agenta (koniec - start) i przelicz na minuty.

Dodaj komentarz do ticketa W IMIENIU agenta:

```
mcp__monolynx__add_comment(
  project_slug="<SLUG_PROJEKTU>",
  ticket_id="<ID>",
  content="**[Nazwa agenta] — Podsumowanie pracy**\n\nCo zrobiono:\n- [zmiana 1 — plik/pliki]\n- [zmiana 2 — plik/pliki]\n- ...\n\nOcena krytyka: [score]/100 ([APPROVED/NEEDS WORK] -> ile iteracji)\n\nCzas pracy: [X] min\n[Jedno zdanie podsumowujace prace agenta]"
)
```

### 5f. Zaloguj czas pracy agenta

```
mcp__monolynx__log_time(
  project_slug="<SLUG_PROJEKTU>",
  ticket_id="<ID>",
  duration_minutes=<obliczony czas w minutach, minimum 1>,
  date_logged="<YYYY-MM-DD>",
  description="[Nazwa agenta] — [krotki opis co zrobiono]"
)
```

### 5g. Rownolegle vs sekwencyjnie

- **Agenci NIEZALEZNI** (np. backend i frontend, jesli nie maja wspolnych zaleznosci) — uruchamiaj ROWNOLEGLE przez wiele Task w jednej wiadomosci
- **Agenci ZALEZNI** (np. backend musi byc gotowy zanim MCP server) — uruchamiaj SEKWENCYJNIE
- **Krytyk ZAWSZE po agencie** — nigdy rownolegle z agentem ktorego ocenia

## KROK 6: Podsumowanie Team Managera

Po zakonczeniu pracy WSZYSTKICH agentow:

### 6a. Zmierz calkowity czas

```bash
date +%s
```

Oblicz laczny czas Team Managera (od kroku 3 do teraz).

### 6b. Dodaj komentarz podsumowujacy

```
mcp__monolynx__add_comment(
  project_slug="<SLUG_PROJEKTU>",
  ticket_id="<ID>",
  content="**Team Manager — Podsumowanie zadania**\n\nZrealizowane:\n- [podsumowanie co zostalo zrobione]\n\nZespol i oceny:\n- [agent 1]: [score]/100 — [1 zdanie]\n- [agent 2]: [score]/100 — [1 zdanie]\n- ...\n\nLaczny czas pracy zespolu: [suma minut wszystkich agentow] min\n\nCzas pracy Team Managera: [X] min\n[Jedno zdanie podsumowujace calosc zadania]"
)
```

### 6c. Zaloguj czas pracy Team Managera

```
mcp__monolynx__log_time(
  project_slug="<SLUG_PROJEKTU>",
  ticket_id="<ID>",
  duration_minutes=<calkowity czas Team Managera w minutach, minimum 1>,
  date_logged="<YYYY-MM-DD>",
  description="Team Manager — koordynacja zadania"
)
```

### 6d. Zmien status ticketa

```
mcp__monolynx__update_ticket(project_slug="<SLUG_PROJEKTU>", ticket_id="<ID>", status="in_review")
```

### 6e. Podsumowanie dla uzytkownika

Wyswietl uzytkownikowi krotkie podsumowanie:
- Co zostalo zrobione
- Oceny krytyka dla kazdego agenta
- Laczny czas pracy
- Status ticketa

---

## WAZNE ZASADY

1. **Krytyk NIGDY nie pisze kodu** — tylko ocenia prace innych
2. **Komentarze do ticketa sa OBOWIAZKOWE** — plan (krok 4), kazdy agent (krok 5e), podsumowanie (krok 6b)
3. **Czas pracy logowany ZAWSZE** — mierz `date +%s` przed i po kazdym agencie
4. **Jezyk komentarzy**: polski
5. **Nie zgaduj** — jesli cos jest niejasne w tickecie, zapytaj uzytkownika
6. **Nie pomijaj krytyka** — kazdy agent MUSI przejsc review, nawet jesli zadanie wydaje sie proste
7. **Graf kodu jest opcjonalny** — jesli Neo4j niedostepny, kontynuuj bez grafu (krok 2)
8. **Graf jest aktualizowany automatycznie** — skrypt `cicd/sync_graph.py` synchronizuje graf z kodem po merge do main. Nie trzeba reczenie aktualizowac grafu w trakcie pracy
