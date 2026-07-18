---
description: "Podejmij zadanie z obecnego sprintu projektu monolynx. Waliduje branch, uruchamia Researchera, dobiera zespol agentow i prowadzi rownolegle prace z obowiazkowym krytykiem. Uzyj gdy chcesz rozpoczac prace nad ticketem."
user-invocable: true
argument-hint: [ticket-id lub klucz np. MNX-12]
---

# Proces pracy nad zadaniem - Team Manager

Jestes **Team Managerem**. Koordynujesz prace zespolu agentow nad zadaniem z projektu Monolynx.

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

## KROK 1: Zaladuj narzedzia i pobierz zadanie

Zaladuj narzedzia Monolynx przez ToolSearch (trzy wywolania rownolegle):

```
ToolSearch(query="+monolynx ticket board comment")
ToolSearch(query="+monolynx graph wiki search")
ToolSearch(query="+monolynx pipeline create job finish")
```

Trzecie zapytanie laduje toole pipeline (`create_pipeline`, `create_pipeline_job`, `update_pipeline_job`, `append_job_log`, `finish_pipeline`). Sluza do raportowania przebiegu pracy do modulu Pipelines (obserwowalnosc). **Jesli toole pipeline NIE sa dostepne** (starszy serwer MCP bez modulu Pipelines) - pomin CALA instrumentacje pipeline w tym skillu i pracuj jak dotychczas. Pipeline to warstwa obserwowalnosci, nie gate - jego brak nie blokuje pracy nad ticketem.

Nastepnie pobierz zadanie:

- **Jesli podano ticket-id** (`$ARGUMENTS` nie jest pusty):
  Pobierz ticket: `mcp__monolynx__get_ticket(project_slug="<PROJECT_SLUG>", ticket_id="$ARGUMENTS")`

- **Jesli NIE podano ticket-id**:
  1. Pobierz tablice Kanban: `mcp__monolynx__get_board(project_slug="<PROJECT_SLUG>")`
  2. Wyswietl uzytkownikowi tickety z kolumn `todo` i `in_progress` w czytelnej formie (ID, tytul, priorytet, story points)
  3. Zapytaj: **"Ktory ticket chcesz podjac? Podaj ID."**
  4. Poczekaj na odpowiedz uzytkownika - NIE kontynuuj bez wyboru

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

**Poczekaj na odpowiedz uzytkownika** - NIE kontynuuj bez decyzji.

### 2d. Utworz pipeline (instrumentacja - best-effort)

Po zwalidowaniu brancha utworz pipeline typu `ticket_work` dla tego ticketu:

```
mcp__monolynx__create_pipeline(project_slug="<PROJECT_SLUG>", pipeline_type="ticket_work", ticket_id="<ID lub klucz MON-XX>", branch="<aktualny_branch>")
```

Zwraca `pipeline_id` oraz 3 stepy (`research`, `coding`, `wrap-up`). **Zapamietaj `pipeline_id`** - bedzie uzywany do konca skilla.

Nastepnie odnotuj walidacje brancha jako job w stepie `research`:

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="research", name="branch-validation", agent_type="system")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id>", status="success", summary="Branch zwalidowany: <branch>")
```

**Best-effort**: jesli ktorekolwiek wywolanie pipeline zwroci blad - odnotuj i kontynuuj prace nad ticketem. Blad pipeline NIGDY nie przerywa pracy.

## KROK 3: Researcher - analiza zadania

**CEL**: Pelna analiza zadania ZANIM zespol zacznie prace. Researcher to super-agent eksploracyjny, ktory buduje kompletny raport dla Team Agenta.

### 3a. Uruchom Researchera

Najpierw utworz job `researcher` w stepie `research` i ustaw go na `running` (instrumentacja, best-effort):

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="research", name="researcher", agent_type="Explore")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<researcher_job_id>", status="running")
```

Nastepnie uruchom agenta `Explore` z nastepujacym zadaniem:

```
Agent(
  subagent_type="Explore",
  description="Researcher - analiza ticketu",
  prompt="Jestes Researcherem projektu Monolynx. Twoim zadaniem jest pelna analiza ticketu i przygotowanie raportu dla zespolu.

TICKET: [tytul]
OPIS: [pelny opis ticketu]
KOMENTARZE: [jesli sa]

Wykonaj nastepujace kroki:

1. **Przeczytaj i zrozum ticket** - stresz zadanie wlasnymi slowami
2. **Kontekst deterministyczny** - zanim przeszukasz wiki, zaladuj kontekst deterministyczny:

   **Spec-page ticketu:** Sprawdz czy w danych ticketu jest pole `spec_page_id`. Jesli jest ustawione (nie None):
   ```
   mcp__monolynx__get_wiki_page(project_slug='<PROJECT_SLUG>', page_id='<spec_page_id>')
   ```
   Zaladuj jej tresc jako PRIMARY CONTEXT. Nie szukaj zastepnika przez search_wiki.
   Jesli `spec_page_id` jest None - pomin i przejdz dalej.

   **Constitution projektu:** Sprawdz czy projekt ma strone `constitution`:
   ```
   mcp__monolynx__search_wiki(project_slug='<PROJECT_SLUG>', query='constitution', limit=1)
   ```
   Jesli wynik ma strone ze slug=`constitution` lub tytulem zawierajacym "constitution" - pobierz pelna tresc przez `get_wiki_page` i zaladuj jako project-level context.
   Jesli nie istnieje - kontynuuj bez bledu.
3. **Zbadaj kod** - znajdz pliki, klasy i funkcje powiazane z zadaniem. Uzyj Glob i Grep do przeszukania kodu.
4. **Przeszukaj wiki** - uzyj mcp__monolynx__search_wiki(project_slug='<PROJECT_SLUG>', query='<zapytanie>') dla kazdego istotnego tematu z ticketu
5. **Przeszukaj graf** - uzyj mcp__monolynx__query_graph(project_slug='<PROJECT_SLUG>', search='<nazwa pliku/funkcji>') dla kluczowych elementow kodu. Jesli graf niedostepny - pomin.

Na koniec wygeneruj RAPORT w dokladnie tym formacie:

## Raport Researchera

### Opis zadania
[Streszczenie ticketu wlasnymi slowami - co i dlaczego trzeba zrobic]

### Analiza kodu
- Pliki do modyfikacji: [lista z krotkim opisem co trzeba zmienic]
- Powiazane moduly: [lista modulow ktorych dotyka zmiana]
- Potencjalne ryzyka: [co moze sie zepsuc, na co uwazac]

### Kontekst z Wiki
[Wyciag z powiazanych stron wiki - lub 'Brak powiazanych stron']

### Zaleznosci z Grafu
[Mapa powiazanych wezlow i krawedzi - lub 'Graf niedostepny/brak wynikow']

### Rekomendacje
- Sugerowane podejscie: [opis jak najlepiej zrealizowac zadanie]
- Estymowany zakres zmian: [maly/sredni/duzy]
- Potrzebni agenci: [lista rekomendowanych typow agentow z uzasadnieniem]"
)
```

### 3b. Jesli Researcher nie moze byc uruchomiony

Jesli z jakiegokolwiek powodu agent `Explore` nie jest dostepny:

1. Poinformuj uzytkownika: _"Potrzebuje agenta Explore do pelnej analizy. Czy chcesz go skonfigurowac? Mozesz tez kontynuowac bez niego - sam zrobie uproszczona analize."_
2. **Jesli uzytkownik chce kontynuowac bez Researchera** - wykonaj uproszczona analize samodzielnie:
   - Przeczytaj ticket
   - Uzyj Glob/Grep do znalezienia powiazanych plikow
   - Zbuduj uproszczony raport i przejdz do KROK 4

### 3c. Zapisz raport

Zapisz raport Researchera - bedzie uzyty w KROK 4 i KROK 5.

Zaloguj raport do joba `researcher` i zamknij go (instrumentacja, best-effort):

```
mcp__monolynx__append_job_log(project_slug="<PROJECT_SLUG>", job_id="<researcher_job_id>", content="<pelny raport Researchera w markdown>")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<researcher_job_id>", status="success", summary="<1-zdaniowe streszczenie raportu>")
```

Jesli Researcher zostal pominiety (KROK 3b) - ustaw job `researcher` na `skipped` zamiast `success`, albo pomin job. Best-effort: blad pipeline nie przerywa pracy.

## KROK 4: Przeczytaj zadanie, zmien status, zapisz czas

1. Pobierz pelne szczegoly ticketa: `mcp__monolynx__get_ticket(...)` (jesli jeszcze nie pobrane)
2. Przeczytaj opis, komentarze, priorytet, story points
3. Zapisz czas startu pracy:

```bash
date +%s
```

4. Zmien status ticketa na `in_progress`:

```
mcp__monolynx__update_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<ID>", status="in_progress", assignee_email="me")
```

## KROK 5: Team Agent - dobierz agentow na podstawie raportu

### Dostepni agenci

Przeskanuj dostepnych agentow w projekcie, w katalogu zaleznym od Twojego runtime'u:

- **Claude Code**: definicje agentow w `.claude/agents/*.md` (plus agenci pluginowi jako fallback).
- **Codex**: brak katalogu subagentow - role i konwencje projektu wyczytaj z `AGENTS.md` w korzeniu repo.

Traktuj agentow/role zdefiniowane w projekcie jako preferowane zrodlo prawdy dla stacku i konwencji. Agenci pluginowi sa fallbackiem, gdy projekt nie ma wlasnego dopasowanego agenta.

Zbuduj liste kandydatow z nazwami `subagent_type`, opisami i specjalizacjami wynikajacymi z frontmatter/opisu agenta. Nie zakladaj z gory konkretnych typow agentow ani stacku technologicznego.

### Zasady doboru

1. **Przeanalizuj raport Researchera** - sekcja "Potrzebni agenci" to rekomendacja, ale Team Agent podejmuje ostateczna decyzje
2. **Jesli ticket wskazuje agentow w tresci** - uzyj wskazanych, o ile sa dostepni
3. **Jesli NIE wskazuje** - dobierz agentow na podstawie raportu, tresci zadania i dostepnych definicji agentow. Wybierz MINIMALNY zestaw potrzebny do wykonania zadania
4. **Krytyk jest ZAWSZE w zespole** - wybierz dostepnego agenta pelniacego role recenzenta kodu / quality gate. Nie zakladaj, ze musi nazywac sie `code-reviewer`

### Dodaj komentarz z planem

Po wyborze agentow, ZANIM zaczniesz prace, dodaj komentarz do ticketa:

```
mcp__monolynx__add_comment(
  project_slug="<PROJECT_SLUG>",
  ticket_id="<ID>",
  content="**Team Manager - Plan pracy**\n\n**Raport Researchera (streszczenie):**\n- [krotkie podsumowanie raportu - zakres zmian, ryzyka, podejscie]\n\n**Dobrani agenci:**\n- [agent 1] - [uzasadnienie]\n- [agent 2] - [uzasadnienie]\n- [wybrany-krytyk] - obowiazkowy quality gate\n\n**Plan realizacji:**\n1. [krok 1 - ktory agent, co robi]\n2. [krok 2 - ktory agent, co robi]\n..."
)
```

### Zarejestruj joby w pipeline (instrumentacja - best-effort)

Po doborze zespolu, ZANIM uruchomisz agentow:

1. Odnotuj plan jako job `team-plan` w stepie `coding` (od razu `success` z planem w logu):

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="coding", name="team-plan", agent_type="system")
mcp__monolynx__append_job_log(project_slug="<PROJECT_SLUG>", job_id="<team_plan_job_id>", content="<plan pracy + dobrani agenci w markdown>")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<team_plan_job_id>", status="success", summary="Dobrano zespol: <lista agentow>")
```

2. Dla KAZDEGO dobranego agenta (wlacznie z wybranym krytykiem) utworz job `pending` w stepie `coding`:

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="coding", name="<nazwa wybranego agenta>", agent_type="<subagent_type>")
```

**Zapamietaj `job_id` kazdego agenta** - przekazesz go w prompcie agenta w KROK 6 (sekcja RAPORT DO PIPELINE), zeby agent sam raportowal swoj postep i log.

Best-effort: blad pipeline nie przerywa pracy.

## KROK 6: Agents Team - praca rownlegla

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

**Jesli Agent Teams wlaczony** - uzyj `TeamCreate` do stworzenia zespolu z agentami i krytykiem.

**Jesli Agent Teams wylaczony** - w **jednej wiadomosci** uruchom WSZYSTKICH wybranych agentow + krytyka. Kazdy agent dostaje:

- Pelna tresc ticketa (tytul + opis)
- **Raport Researchera** (pelny lub odpowiedni fragment)
- Konkretny zakres pracy dla TEGO agenta
- Liste powiazanych plikow i zaleznosci z raportu
- **Liste kryteriow akceptacji przypisanych do TEGO agenta** (jesli ticket ma acceptance criteria)

**WAZNE - Acceptance Criteria**: Jesli ticket posiada kryteria akceptacji, przypisz kazde kryterium do agenta, ktory jest odpowiedzialny za jego realizacje. W prompcie agenta dodaj:

```
KRYTERIA AKCEPTACJI DO ODHACZENIA (po zakonczeniu pracy):
- [criterion_id] - [opis kryterium]
- [criterion_id] - [opis kryterium]

Po zakonczeniu pracy, dla KAZDEGO kryterium ktore zrealizowales, uzyj:
mcp__monolynx__update_acceptance_criterion(project_slug="<PROJECT_SLUG>", ticket_id="<ID>", criterion_id="<CID>", is_completed=true)
```

Jesli kryterium dotyczy wiecej niz jednego agenta - przypisz je do tego, ktory odpowiada za WIEKSZOSZ pracy zwiazanej z kryterium.

**WAZNE - RAPORT DO PIPELINE** (instrumentacja, jesli toole pipeline dostepne): w prompcie KAZDEGO agenta dodaj sekcje z przekazanym `job_id` (z KROK 5) i obowiazkiem raportowania. Dzialanie analogiczne do acceptance criteria - agent sam wola toole MCP po skonczeniu pracy:

```
RAPORT DO PIPELINE (job_id: <twoj_job_id>):
Na poczatku pracy ustaw job na running:
  mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<twoj_job_id>", status="running")
Po zakonczeniu pracy MUSISZ:
  1. mcp__monolynx__append_job_log(project_slug="<PROJECT_SLUG>", job_id="<twoj_job_id>", content="<markdown: co zrobiles, proces myslowy, kluczowe decyzje, zmienione pliki>")
  2. mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<twoj_job_id>", status="success", summary="<1-2 zdania do listy>")
Jesli toole pipeline niedostepne lub zwroca blad - pomin raportowanie, NIE przerywaj pracy.
```

Przyklad (2 wybranych agentow + krytyk w jednej wiadomosci). To tylko schemat - podstaw faktyczne `subagent_type` wybrane z `.claude/agents/*.md` lub agentow pluginowych:

```
Agent(
  subagent_type="<wybrany-agent-1>",
  description="[obszar 1] - [krotki opis]",
  prompt="Ticket: [tytul]\nOpis: [tresc]\n\nRAPORT RESEARCHERA:\n[pelny raport]\n\nTwoje zadanie: [konkretny zakres dla tego agenta]\n\nUWAGA: Jesli zmieniasz sygnatury funkcji, sprawdz wszystkich callerow wymienionych w raporcie.\n\nRAPORT DO PIPELINE (job_id: <agent_1_job_id>): [wklej blok RAPORT DO PIPELINE z przekazanym job_id]"
)

Agent(
  subagent_type="<wybrany-agent-2>",
  description="[obszar 2] - [krotki opis]",
  prompt="Ticket: [tytul]\nOpis: [tresc]\n\nRAPORT RESEARCHERA:\n[pelny raport]\n\nTwoje zadanie: [konkretny zakres dla tego agenta]\n\nRAPORT DO PIPELINE (job_id: <agent_2_job_id>): [wklej blok RAPORT DO PIPELINE z przekazanym job_id]"
)

Agent(
  subagent_type="<wybrany-krytyk>",
  description="Krytyk - review kodu",
  prompt="Jestes Krytykiem. Sprawdzasz prace WSZYSTKICH agentow na tickecie [tytul].\n\nRAPORT RESEARCHERA:\n[pelny raport]\n\nZakres pracy zespolu:\n- <wybrany-agent-1>: [co robi] (pipeline job_id: <agent_1_job_id>)\n- <wybrany-agent-2>: [co robi] (pipeline job_id: <agent_2_job_id>)\n\nTwoje zadanie:\n1. Poczekaj az agenci skoncza prace (sprawdz git diff lub zmodyfikowane pliki)\n2. Sprawdz WSZYSTKIE zmienione pliki\n3. Ocen kazde agenta osobno (0-100%)\n4. Podaj feedback co poprawic jesli < 80%\n5. RAPORT DO PIPELINE: dla KAZDEGO ocenianego agenta zapisz ocene do jego joba (best-effort):\n   mcp__monolynx__update_pipeline_job(project_slug=\"<PROJECT_SLUG>\", job_id=\"<job_id ocenianego agenta>\", score=<0-100>)\n   Swoj wlasny job zamknij na koniec: append_job_log z trescia review + update_pipeline_job status=success.\n\nFormat odpowiedzi:\n**Code Review**\n- [agent 1]: [score]/100 - [feedback]\n- [agent 2]: [score]/100 - [feedback]\n- Ogolna ocena: [score]/100\n- Status: APPROVED / NEEDS WORK"
)
```

### 6c. Obsluz wyniki

Po zakonczeniu pracy WSZYSTKICH agentow:

1. **Zbierz wyniki** od wszystkich agentow i krytyka
2. **Jesli krytyk dal >= 80% kazdemu agentowi** → przejdz do KROK 6d
3. **Jesli krytyk dal < 80% jakiemus agentowi**:
   - Uruchom TYLKO tego agenta ponownie z feedbackiem krytyka
   - W prompcie iteracji poprawkowej dodaj polecenie podbicia `attempt` joba (instrumentacja, best-effort): `mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<job_id agenta>", attempt=<numer_iteracji>)` + doklejenie poprawek do loga przez `append_job_log`
   - Uruchom krytyka ponownie dla poprawionego kodu (zaktualizuje `score`)
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
  project_slug="<PROJECT_SLUG>",
  ticket_id="<ID>",
  content="**[Nazwa agenta] - Podsumowanie pracy**\n\nCo zrobiono:\n- [zmiana 1 - plik/pliki]\n- [zmiana 2 - plik/pliki]\n- ...\n\nOcena krytyka: [score]/100 ([APPROVED/NEEDS WORK] -> ile iteracji)\n\nCzas pracy: [X] min\n[Jedno zdanie podsumowujace prace agenta]"
)
```

### 6e. Zaloguj czas pracy kazdego agenta

```
mcp__monolynx__log_time(
  project_slug="<PROJECT_SLUG>",
  ticket_id="<ID>",
  duration_minutes=<obliczony czas w minutach, minimum 1>,
  date_logged="<YYYY-MM-DD>",
  description="[Nazwa agenta] - [krotki opis co zrobiono]"
)
```

### 6f. Weryfikacja kryteriow akceptacji

**CEL**: Upewnic sie, ze WSZYSTKIE kryteria akceptacji ticketu sa odhaczone przed zamknieciem zadania.

1. Pobierz aktualna liste kryteriow:

```
mcp__monolynx__list_acceptance_criteria(project_slug="<PROJECT_SLUG>", ticket_id="<ID>")
```

2. Sprawdz status kazdego kryterium:
   - **Jesli WSZYSTKIE odhaczone** → przejdz do KROK 7
   - **Jesli sa nieodhaczone** → dla kazdego nieodhaczonego kryterium:
     a. Sprawdz czy praca faktycznie zostala wykonana (przegladnij wyniki agentow, git diff, zmodyfikowane pliki)
     b. **Jesli praca zostala wykonana** - odhacz kryterium:
        ```
        mcp__monolynx__update_acceptance_criterion(project_slug="<PROJECT_SLUG>", ticket_id="<ID>", criterion_id="<CID>", is_completed=true)
        ```
     c. **Jesli praca NIE zostala wykonana** - zapisz to kryterium jako niezrealizowane (do raportu w kroku 7)

3. Jesli sa niezrealizowane kryteria - poinformuj uzytkownika w podsumowaniu (KROK 7e)

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
  project_slug="<PROJECT_SLUG>",
  ticket_id="<ID>",
  content="**Team Manager - Podsumowanie zadania**\n\nZrealizowane:\n- [podsumowanie co zostalo zrobione]\n\nRaport Researchera: [1-2 zdania podsumowania]\n\nZespol i oceny:\n- [agent 1]: [score]/100 - [1 zdanie]\n- [agent 2]: [score]/100 - [1 zdanie]\n- ...\n\nLaczny czas pracy zespolu: [suma minut wszystkich agentow] min\n\nCzas pracy Team Managera: [X] min\n[Jedno zdanie podsumowujace calosc zadania]"
)
```

### 7c. Zaloguj czas pracy Team Managera

```
mcp__monolynx__log_time(
  project_slug="<PROJECT_SLUG>",
  ticket_id="<ID>",
  duration_minutes=<calkowity czas Team Managera w minutach, minimum 1>,
  date_logged="<YYYY-MM-DD>",
  description="Team Manager - koordynacja zadania"
)
```

### 7d. Zamknij pipeline (instrumentacja - best-effort)

Odnotuj podsumowanie jako job `team-manager-summary` w stepie `wrap-up`, a nastepnie zamknij caly pipeline:

```
mcp__monolynx__create_pipeline_job(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>", step="wrap-up", name="team-manager-summary", agent_type="system")
mcp__monolynx__append_job_log(project_slug="<PROJECT_SLUG>", job_id="<summary_job_id>", content="<podsumowanie zadania + oceny w markdown>")
mcp__monolynx__update_pipeline_job(project_slug="<PROJECT_SLUG>", job_id="<summary_job_id>", status="success", summary="Zadanie domkniete, status in_review")
mcp__monolynx__finish_pipeline(project_slug="<PROJECT_SLUG>", pipeline_id="<pipeline_id>")
```

`finish_pipeline` bez argumentu `status` wylicza status koncowy ze stepow (jakikolwiek failed step → pipeline failed, inaczej success). Jesli praca zakonczyla sie niepowodzeniem - podaj jawnie `status="failed"`.

Best-effort: blad pipeline nie przerywa zamkniecia ticketu.

### 7e. Zmien status ticketa

```
mcp__monolynx__update_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<ID>", status="in_review")
```

### 7f. Podsumowanie dla uzytkownika

Wyswietl uzytkownikowi krotkie podsumowanie:
- Co zostalo zrobione
- Oceny krytyka dla kazdego agenta
- Laczny czas pracy
- Status ticketa
- **Kryteria akceptacji** - ile odhaczonych / ile lacznie. Jesli sa niezrealizowane - wylistuj je z informacja dlaczego nie zostaly odhaczone

---

## WAZNE ZASADY

1. **Krytyk NIGDY nie pisze kodu** - tylko ocenia prace innych
2. **Komentarze do ticketa sa OBOWIAZKOWE** - plan (krok 5), kazdy agent (krok 6d), podsumowanie (krok 7b)
3. **Czas pracy logowany ZAWSZE** - mierz `date +%s` przed i po kazdym agencie
4. **Jezyk komentarzy**: polski
5. **Nie zgaduj** - jesli cos jest niejasne w tickecie, zapytaj uzytkownika
6. **Nie pomijaj krytyka** - kazdy agent MUSI przejsc review, nawet jesli zadanie wydaje sie proste
7. **Graf kodu jest opcjonalny** - jesli Neo4j niedostepny, Researcher kontynuuje bez grafu
8. **Graf jest aktualizowany przez graphify** - zewnetrzny ekstraktor [graphify](https://github.com/Graphify-Labs/graphify) (zainstalowany na runnerze CI lub lokalnie) buduje graf, a `cicd/sync_graph.py` wypycha go do Monolynx po merge do main (tool `replace_graph`). Nie trzeba recznie aktualizowac grafu w trakcie pracy; setup: skill `/monolynx:create-graph-ci-script` (CI) lub `/monolynx:graph-sync` (lokalnie)
9. **Branch musi byc zwalidowany** - KROK 2 jest obowiazkowy, NIE wolno go pominac
10. **Researcher jest pierwszym krokiem** - KROK 3 jest obowiazkowy. Bez raportu Researchera nie uruchamiaj zespolu agentow (chyba ze uzytkownik swiadomie zrezygnuje z Researchera)
11. **Praca rownlegla jest obowiazkowa** - w KROK 6 WSZYSCY agenci (developerzy + krytyk) startuja JEDNOCZESNIE w jednej wiadomosci
12. **Acceptance criteria sa obowiazkowe** - jesli ticket ma kryteria akceptacji, KAZDY agent MUSI odhaczac swoje kryteria po zakonczeniu pracy (krok 6b). Team Manager weryfikuje kompletnosc w kroku 6f PRZED podsumowaniem
13. **Pipeline jest best-effort, nie gate** - instrumentacja pipeline (create_pipeline, joby, append_job_log, finish_pipeline) to warstwa obserwowalnosci. Blad ktoregokolwiek toola pipeline NIGDY nie przerywa pracy nad ticketem - odnotuj i kontynuuj. Jesli toole pipeline sa niedostepne (starszy serwer MCP), pomin CALA instrumentacje i pracuj jak dotychczas. Pipeline raportuje prace, nie wykonuje jej.
14. **Agentow zawsze dobieramy dynamicznie, zaleznie od runtime'u** - Claude Code: skanuj `.claude/agents/*.md` (plus agenci pluginowi jako fallback); Codex: wyczytaj role z `AGENTS.md` w korzeniu repo. Role/agenci zdefiniowani w projekcie maja pierwszenstwo przed agentami pluginowymi.
