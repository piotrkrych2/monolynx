---
name: monolynx-work
description: "Podejmij zadanie z obecnego sprintu projektu monolynx. Waliduje branch, uruchamia Researchera, dobiera zespol agentow i prowadzi rownolegle prace z obowiazkowym krytykiem. Uzyj gdy chcesz rozpoczac prace nad ticketem."
user-invocable: true
argument-hint: [ticket-id]
---

# Proces pracy nad zadaniem — Team Manager

Jestes **Team Managerem**. Koordynujesz prace zespolu agentow nad zadaniem z projektu Monolynx.

**Projekt**: `monolynx`

---

## KROK 1: Zaladuj narzedzia i pobierz zadanie

Zaladuj narzedzia Monolynx przez ToolSearch (dwa wywolania rownolegle):

```
ToolSearch(query="+monolynx ticket board comment")
ToolSearch(query="+monolynx graph wiki search")
```

Nastepnie pobierz zadanie:

- **Jesli podano ticket-id** (`$ARGUMENTS` nie jest pusty):
  Pobierz ticket: `mcp__monolynx__get_ticket(project_slug="monolynx", ticket_id="$ARGUMENTS")`

- **Jesli NIE podano ticket-id**:
  1. Pobierz tablice Kanban: `mcp__monolynx__get_board(project_slug="monolynx")`
  2. Wyswietl uzytkownikowi tickety z kolumn `todo` i `in_progress` w czytelnej formie (ID, tytul, priorytet, story points)
  3. Zapytaj: **"Ktory ticket chcesz podjac? Podaj ID."**
  4. Poczekaj na odpowiedz uzytkownika — NIE kontynuuj bez wyboru

## KROK 2: Walidacja brancha Git

**CEL**: Upewnic sie, ze developer pracuje na wlasciwym branchu przed rozpoczeciem pracy.

### 2a. Sprawdz aktualny branch

```bash
git branch --show-current
```

### 2b. Porownaj z oczekiwanym wzorcem

Oczekiwany wzorzec nazwy brancha: `feature-<numer_ticketu>-<slug>` (np. `feature-42-kopiowanie-id`).

Wyodrebnij numer ticketu z pobranego ticketa (pole `key`, np. `MON-42` → numer `42`).

### 2c. Decyzja

- **Jesli branch pasuje do wzorca** (zawiera numer ticketu) → kontynuuj do KROK 3
- **Jesli branch NIE pasuje** → zapytaj uzytkownika:

> Pracujesz nad ticketem **#[numer]** (`[tytul]`), ale jestes na branchu `[aktualny_branch]`.
>
> Co chcesz zrobic?
> - **(a)** Kontynuowac na obecnym branchu `[aktualny_branch]`
> - **(b)** Przejsc na `main`, pobrac zmiany i utworzyc nowy branch `feature-[numer]-[slug]`

**Jesli uzytkownik wybral (b)**:

```bash
git checkout main && git pull origin main && git checkout -b feature-<numer>-<slug>
```

Gdzie `<slug>` to skrocony, kebab-case tytul ticketu (max 4-5 slow, bez polskich znakow).

**Poczekaj na odpowiedz uzytkownika** — NIE kontynuuj bez decyzji.

## KROK 3: Researcher — analiza zadania

**CEL**: Pelna analiza zadania ZANIM zespol zacznie prace. Researcher to super-agent eksploracyjny, ktory buduje kompletny raport dla Team Agenta.

### 3a. Uruchom Researchera

Uruchom agenta `Explore` z nastepujacym zadaniem:

```
Agent(
  subagent_type="Explore",
  description="Researcher — analiza ticketu",
  prompt="Jestes Researcherem projektu Monolynx. Twoim zadaniem jest pelna analiza ticketu i przygotowanie raportu dla zespolu.

TICKET: [tytul]
OPIS: [pelny opis ticketu]
KOMENTARZE: [jesli sa]

Wykonaj nastepujace kroki:

1. **Przeczytaj i zrozum ticket** — stresz zadanie wlasnymi slowami
2. **Zbadaj kod** — znajdz pliki, klasy i funkcje powiazane z zadaniem. Uzyj Glob i Grep do przeszukania kodu.
3. **Przeszukaj wiki** — uzyj mcp__monolynx__search_wiki(project_slug='monolynx', query='<zapytanie>') dla kazdego istotnego tematu z ticketu
4. **Przeszukaj graf** — uzyj mcp__monolynx__query_graph(project_slug='monolynx', search='<nazwa pliku/funkcji>') dla kluczowych elementow kodu. Jesli graf niedostepny — pomin.

Na koniec wygeneruj RAPORT w dokladnie tym formacie:

## Raport Researchera

### Opis zadania
[Streszczenie ticketu wlasnymi slowami — co i dlaczego trzeba zrobic]

### Analiza kodu
- Pliki do modyfikacji: [lista z krotkim opisem co trzeba zmienic]
- Powiazane moduly: [lista modulow ktorych dotyka zmiana]
- Potencjalne ryzyka: [co moze sie zepsuc, na co uwazac]

### Kontekst z Wiki
[Wyciag z powiazanych stron wiki — lub 'Brak powiazanych stron']

### Zaleznosci z Grafu
[Mapa powiazanych wezlow i krawedzi — lub 'Graf niedostepny/brak wynikow']

### Rekomendacje
- Sugerowane podejscie: [opis jak najlepiej zrealizowac zadanie]
- Estymowany zakres zmian: [maly/sredni/duzy]
- Potrzebni agenci: [lista rekomendowanych typow agentow z uzasadnieniem]"
)
```

### 3b. Jesli Researcher nie moze byc uruchomiony

Jesli z jakiegokolwiek powodu agent `Explore` nie jest dostepny:

1. Poinformuj uzytkownika: _"Potrzebuje agenta Explore do pelnej analizy. Czy chcesz go skonfigurowac? Mozesz tez kontynuowac bez niego — sam zrobie uproszczona analize."_
2. **Jesli uzytkownik chce kontynuowac bez Researchera** — wykonaj uproszczona analize samodzielnie:
   - Przeczytaj ticket
   - Uzyj Glob/Grep do znalezienia powiazanych plikow
   - Zbuduj uproszczony raport i przejdz do KROK 4

### 3c. Zapisz raport

Zapisz raport Researchera — bedzie uzyty w KROK 4 i KROK 5.

## KROK 4: Przeczytaj zadanie, zmien status, zapisz czas

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

## KROK 5: Team Agent — dobierz agentow na podstawie raportu

### Dostepni agenci

| Agent | subagent_type | Specjalizacja |
|-------|---------------|---------------|
| Backend Developer | `backend-developer` | FastAPI, SQLAlchemy, Alembic, Pydantic, security, services |
| Frontend Developer | `frontend-developer` | Jinja2, Tailwind CSS, HTMX, Cytoscape.js, EasyMDE |
| Database Specialist | `database-specialist` | Alembic migrations, query optimization, pgvector, Neo4j |
| QA Tester | `qa-tester` | pytest, fixtures, mocking, coverage, regression tests |
| DevOps Infra | `devops-infra` | Docker, Docker Compose, GitLab CI, Traefik, MinIO |
| Krytyk | `code-reviewer` | Code review, quality gate (0-100%) — **ZAWSZE OBOWIAZKOWY** |

### Zasady doboru

1. **Przeanalizuj raport Researchera** — sekcja "Potrzebni agenci" to rekomendacja, ale Team Agent podejmuje ostateczna decyzje
2. **Jesli ticket wskazuje agentow w tresci** — uzyj wskazanych
3. **Jesli NIE wskazuje** — dobierz na podstawie raportu i tresci zadania. Wybierz MINIMALNY zestaw potrzebny do wykonania zadania
4. **Krytyk (`code-reviewer`) jest ZAWSZE w zespole** — jest automatycznie dodawany, nie trzeba go wybierac

### Dodaj komentarz z planem

Po wyborze agentow, ZANIM zaczniesz prace, dodaj komentarz do ticketa:

```
mcp__monolynx__add_comment(
  project_slug="monolynx",
  ticket_id="<ID>",
  content="**Team Manager — Plan pracy**\n\n**Raport Researchera (streszczenie):**\n- [krotkie podsumowanie raportu — zakres zmian, ryzyka, podejscie]\n\n**Dobrani agenci:**\n- [agent 1] — [uzasadnienie]\n- [agent 2] — [uzasadnienie]\n- code-reviewer — obowiazkowy quality gate\n\n**Plan realizacji:**\n1. [krok 1 — ktory agent, co robi]\n2. [krok 2 — ktory agent, co robi]\n..."
)
```

## KROK 6: Agents Team — praca rownlegla

**ZASADA KLUCZOWA**: Wszyscy wybrani developerzy + krytyk startuja **JEDNOCZESNIE** (rownolegle). Krytyk pracuje rownolegle z developerami i robi review na biezaco.

### 6.0 Sprawdz czy Agent Teams jest wlaczony

Sprawdz wartosc zmiennej srodowiskowej:

```bash
echo $CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
```

- **Jesli `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`** → uzyj natywnych Agent Teams (TeamCreate) do uruchomienia zespolu. Stworz team z wybranymi agentami i krytykiem, kazdy z wlasnym promptem. Agent Teams zarzadza rownlegloscia automatycznie.
- **Jesli zmienna nie jest ustawiona lub != 1** → uzyj standardowego podejscia z wieloma wywolaniami `Agent()` w jednej wiadomosci (opisane ponizej w 6b).

### 6a. Zmierz czas startu

```bash
date +%s
```

### 6b. Uruchom WSZYSTKICH agentow rownoczesnie

**Jesli Agent Teams wlaczony** — uzyj `TeamCreate` do stworzenia zespolu z agentami i krytykiem.

**Jesli Agent Teams wylaczony** — w **jednej wiadomosci** uruchom WSZYSTKICH wybranych agentow + krytyka. Kazdy agent dostaje:

- Pelna tresc ticketa (tytul + opis)
- **Raport Researchera** (pelny lub odpowiedni fragment)
- Konkretny zakres pracy dla TEGO agenta
- Liste powiazanych plikow i zaleznosci z raportu

Przyklad (3 agentow + krytyk w jednej wiadomosci):

```
Agent(
  subagent_type="backend-developer",
  description="Backend — [krotki opis]",
  prompt="Ticket: [tytul]\nOpis: [tresc]\n\nRAPORT RESEARCHERA:\n[pelny raport]\n\nTwoje zadanie: [konkretny zakres dla backendu]\n\nUWAGA: Jesli zmieniasz sygnatury funkcji, sprawdz wszystkich callerow wymienionych w raporcie."
)

Agent(
  subagent_type="frontend-developer",
  description="Frontend — [krotki opis]",
  prompt="Ticket: [tytul]\nOpis: [tresc]\n\nRAPORT RESEARCHERA:\n[pelny raport]\n\nTwoje zadanie: [konkretny zakres dla frontendu]"
)

Agent(
  subagent_type="code-reviewer",
  description="Krytyk — review kodu",
  prompt="Jestes Krytykiem (code-reviewer). Sprawdzasz prace WSZYSTKICH agentow na tickecie [tytul].\n\nRAPORT RESEARCHERA:\n[pelny raport]\n\nZakres pracy zespolu:\n- backend-developer: [co robi]\n- frontend-developer: [co robi]\n\nTwoje zadanie:\n1. Poczekaj az agenci skoncza prace (sprawdz git diff lub zmodyfikowane pliki)\n2. Sprawdz WSZYSTKIE zmienione pliki\n3. Ocen kazde agenta osobno (0-100%)\n4. Podaj feedback co poprawic jesli < 80%\n\nFormat odpowiedzi:\n**Code Review**\n- [agent 1]: [score]/100 — [feedback]\n- [agent 2]: [score]/100 — [feedback]\n- Ogolna ocena: [score]/100\n- Status: APPROVED / NEEDS WORK"
)
```

### 6c. Obsluz wyniki

Po zakonczeniu pracy WSZYSTKICH agentow:

1. **Zbierz wyniki** od wszystkich agentow i krytyka
2. **Jesli krytyk dal >= 80% kazdemu agentowi** → przejdz do KROK 6d
3. **Jesli krytyk dal < 80% jakiemus agentowi**:
   - Uruchom TYLKO tego agenta ponownie z feedbackiem krytyka
   - Uruchom krytyka ponownie dla poprawionego kodu
   - **Maksymalnie 3 iteracje** na agenta
   - Po 3 nieudanych iteracjach → zapytaj uzytkownika o decyzje

### 6d. Zmierz czas konca i dodaj komentarze

```bash
date +%s
```

Oblicz czas pracy (koniec - start) i przelicz na minuty.

Dodaj komentarz do ticketa **W IMIENIU KAZDEGO agenta**:

```
mcp__monolynx__add_comment(
  project_slug="monolynx",
  ticket_id="<ID>",
  content="**[Nazwa agenta] — Podsumowanie pracy**\n\nCo zrobiono:\n- [zmiana 1 — plik/pliki]\n- [zmiana 2 — plik/pliki]\n- ...\n\nOcena krytyka: [score]/100 ([APPROVED/NEEDS WORK] -> ile iteracji)\n\nCzas pracy: [X] min\n[Jedno zdanie podsumowujace prace agenta]"
)
```

### 6e. Zaloguj czas pracy kazdego agenta

```
mcp__monolynx__log_time(
  project_slug="monolynx",
  ticket_id="<ID>",
  duration_minutes=<obliczony czas w minutach, minimum 1>,
  date_logged="<YYYY-MM-DD>",
  description="[Nazwa agenta] — [krotki opis co zrobiono]"
)
```

## KROK 7: Podsumowanie Team Managera

Po zakonczeniu pracy WSZYSTKICH agentow:

### 7a. Zmierz calkowity czas

```bash
date +%s
```

Oblicz laczny czas Team Managera (od kroku 4 do teraz).

### 7b. Dodaj komentarz podsumowujacy

```
mcp__monolynx__add_comment(
  project_slug="monolynx",
  ticket_id="<ID>",
  content="**Team Manager — Podsumowanie zadania**\n\nZrealizowane:\n- [podsumowanie co zostalo zrobione]\n\nRaport Researchera: [1-2 zdania podsumowania]\n\nZespol i oceny:\n- [agent 1]: [score]/100 — [1 zdanie]\n- [agent 2]: [score]/100 — [1 zdanie]\n- ...\n\nLaczny czas pracy zespolu: [suma minut wszystkich agentow] min\n\nCzas pracy Team Managera: [X] min\n[Jedno zdanie podsumowujace calosc zadania]"
)
```

### 7c. Zaloguj czas pracy Team Managera

```
mcp__monolynx__log_time(
  project_slug="monolynx",
  ticket_id="<ID>",
  duration_minutes=<calkowity czas Team Managera w minutach, minimum 1>,
  date_logged="<YYYY-MM-DD>",
  description="Team Manager — koordynacja zadania"
)
```

### 7d. Zmien status ticketa

```
mcp__monolynx__update_ticket(project_slug="monolynx", ticket_id="<ID>", status="in_review")
```

### 7e. Podsumowanie dla uzytkownika

Wyswietl uzytkownikowi krotkie podsumowanie:
- Co zostalo zrobione
- Oceny krytyka dla kazdego agenta
- Laczny czas pracy
- Status ticketa

---

## WAZNE ZASADY

1. **Krytyk NIGDY nie pisze kodu** — tylko ocenia prace innych
2. **Komentarze do ticketa sa OBOWIAZKOWE** — plan (krok 5), kazdy agent (krok 6d), podsumowanie (krok 7b)
3. **Czas pracy logowany ZAWSZE** — mierz `date +%s` przed i po kazdym agencie
4. **Jezyk komentarzy**: polski
5. **Nie zgaduj** — jesli cos jest niejasne w tickecie, zapytaj uzytkownika
6. **Nie pomijaj krytyka** — kazdy agent MUSI przejsc review, nawet jesli zadanie wydaje sie proste
7. **Graf kodu jest opcjonalny** — jesli Neo4j niedostepny, Researcher kontynuuje bez grafu
8. **Graf jest aktualizowany automatycznie** — skrypt `cicd/sync_graph.py` synchronizuje graf z kodem po merge do main. Nie trzeba recznie aktualizowac grafu w trakcie pracy
9. **Branch musi byc zwalidowany** — KROK 2 jest obowiazkowy, NIE wolno go pominac
10. **Researcher jest pierwszym krokiem** — KROK 3 jest obowiazkowy. Bez raportu Researchera nie uruchamiaj zespolu agentow (chyba ze uzytkownik swiadomie zrezygnuje z Researchera)
11. **Praca rownlegla jest obowiazkowa** — w KROK 6 WSZYSCY agenci (developerzy + krytyk) startuja JEDNOCZESNIE w jednej wiadomosci
