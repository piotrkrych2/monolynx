---
name: sprint-run
description: "Dyspozytor sprintu: jeden idempotentny tick, który czyta stan sprintu i sesji w tle, startuje brakujące sesje /monolynx:work w worktree, zbiera zakończone i raportuje. Sam nigdy nie pracuje nad ticketem. Użyj gdy chcesz przerobić sprint sesjami w tle, w pętli /loop albo ze skryptu sprint_run.sh."
user-invocable: true
argument-hint: "[sloty]"
---

Przed wykonaniem kroków obowiązkowo odczytaj [Kontrakt runtime](references/runtime.md).

# SPRINT-RUN - dyspozytor nazwanych sesji w tle

Zasoby tego skilla: ustal absolutny katalog otrzymanego pliku `SKILL.md`.
Odnośniki do dołączonych `scripts/`, `references/` i pomocniczych plików
Markdown rozwiązuj względem tego katalogu, nie katalogu roboczego projektu.
Ścieżki plików tworzonych w projekcie pozostają względem projektu. Przed komendami
z `MONOLYNX_SKILL_DIR` przypisz tej lokalnej zmiennej powłoki ustaloną
literalną ścieżkę katalogu skilla; nie oczekuj jej w środowisku systemowym.

Jesteś **dyspozytorem sprintu**. Jedno uruchomienie tego skilla to jeden **tick**: czytasz stan sprintu i stan sesji w tle, startujesz brakujące sesje, zbierasz zakończone, raportujesz i kończysz turę.

Dyspozytor jest **cienki**. Trzy zasady, które go definiują:

1. **Nigdy nie pracujesz nad ticketem sam.** Nie czytasz kodu ticketu, nie edytujesz plików, nie commitujesz. Od pracy jest `/monolynx:work` w osobnej sesji.
2. **Świeży proces per ticket.** Każdy ticket dostaje własną nazwaną sesję w tle z własnym worktree, więc konteksty się nie mieszają, a jeden zablokowany ticket nie zatrzymuje reszty.
3. **Tick jest idempotentny.** Uruchomiony dwa razy pod rząd nie zdubluje sesji ani komentarzy: stan wynika z tego, co faktycznie żyje w `claude agents` i jaki status ma ticket, nie z pamięci poprzedniego przebiegu.

Sesje w tle działają bez człowieka dzięki flagom trybu w tle z MON-165 (`MONOLYNX_SPRINT_RUN=1` i `MONOLYNX_CONTRACT=auto`): `/monolynx:work` nie czeka na odpowiedzi, tylko kończy turę linią `SPRINT-RUN STOP: <powód>`, którą ten skill wykrywa jako werdykt `waiting`.

Pętla jest **na zewnątrz** tego skilla. Ty robisz jeden tick, pętlę robi `/loop 15m /monolynx:sprint-run` albo skrypt `sprint_run.sh` (patrz KROK 5).

---

## Ustalenie slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika > `MONOLYNX_PROJECT_SLUG` procesu > jawna konfiguracja klienta lub `.env` projektu czytane jako dane (nigdy `source`). Brak wartości: zapytaj o slug i zatrzymaj działania projektowe, bez zgadywania.


Slug jest częścią kontraktu nazw: sesja robocza ticketu nazywa się `<PROJECT_SLUG>-<KEY>-<MODEL>` (np. `monolynx-MON-167-opus`), gdzie `<MODEL>` to wartość `${MONOLYNX_SPRINT_MODEL:-opus}` z chwili startu, a sesja samego dyspozytora w `sprint_run.sh` `<PROJECT_SLUG>-sprint-run-<MODEL>`, gdzie `<MODEL>` to `${MONOLYNX_SPRINT_TICK_MODEL:-opus}` (tak samo `<PROJECT_SLUG>-sprint-end-<MODEL>` i `<PROJECT_SLUG>-retro-<MODEL>` przy `--close`). Każda nazwa sesji kończy się modelem, na którym sesja działa. Te nazwy są **zamrożone** - inne narzędzia po nich rozpoznają sesje sprintu. Nie zmieniaj ich i nie dodawaj innych sufiksów.

Sesję ticketu rozpoznajesz po nazwie równej `<PROJECT_SLUG>-<KEY>` albo zaczynającej się od `<PROJECT_SLUG>-<KEY>-`, z dowolnym modelem na końcu. WHY: po zmianie `MONOLYNX_SPRINT_MODEL` między tickami (i dla sesji nazwanych przed dodaniem modelu do nazwy) żywa sesja musi nadal zajmować ticket, inaczej tick wystartuje drugą. Myślnik po kluczu jest obowiązkowy: `monolynx-MON-1-` nie łapie `monolynx-MON-10-opus`.

---

## KROK 1: Preflight

Dyspozytor nadal wymaga Claude CLI także uruchomiony z innego klienta. Nie zastępuj go backendem agentów bieżącego runtime. AUTO* i tryb uprawnień nie uchylają instrukcji użytkownika ani odmowy runtime; brak wymaganych zgód zatrzymuje dispatch.

Sprawdź środowisko jednym przebiegiem. Bez tego tick w tle utknie na pierwszym pytaniu, na które nikt nie odpowie.

```bash
echo "AUTOTEST=${MONOLYNX_AUTOTEST:-false} AUTOCOMMIT=${MONOLYNX_AUTOCOMMIT:-false} AUTOPUSH=${MONOLYNX_AUTOPUSH:-false} AUTOMR=${MONOLYNX_AUTOMR:-false} CONTRACT=${MONOLYNX_CONTRACT:-ask} SPRINT_RUN=${MONOLYNX_SPRINT_RUN:-0}"
claude --version
claude agents --json --all >/dev/null && echo "agents-ok"
git rev-parse --show-toplevel
echo "MODE=${MONOLYNX_SPRINT_RUN_MODE:-interactive}"
```

Wymagania twarde. Brak któregokolwiek zatrzymuje tick **przed** startem jakiejkolwiek sesji:

| Warunek | Dlaczego wymagany |
|---|---|
| `claude --version` odpowiada | bez CLI nie ma czym startować sesji |
| `claude agents --json --all` działa | to jedyne źródło stanu sesji w tle |
| katalog jest repozytorium git | worktree per ticket wymaga repo |
| `MONOLYNX_AUTOTEST=true` | sesja w tle sama odpala lint i testy; przy `false` czeka na wklejony wynik |
| `MONOLYNX_AUTOCOMMIT=true` | bez commitu praca sesji nie przetrwa jej zamknięcia |
| `MONOLYNX_AUTOPUSH=true` | bez pusha nie ma czego przeglądać po tickecie |

`MONOLYNX_AUTOMR=true` jest **zalecane**, nie wymagane: bez niego sesja pushuje branch, ale merge request tworzysz ręcznie. Brak `AUTOMR` odnotuj w raporcie jako ostrzeżenie i pracuj dalej.

`MONOLYNX_CONTRACT` i `MONOLYNX_SPRINT_RUN` ustawia sobie sam skrypt `sprint_run.sh`. Gdy odpalasz tick ręcznie albo przez `/loop`, dopilnuj, żeby były w `.claude/settings.json`, inaczej sesje w tle będą czekać na kontrakt.

**Gdy brakuje wymaganej flagi albo narzędzia**: wypisz, czego brakuje, i pokaż gotową konfigurację do wklejenia. Potem zakończ turę **bez startowania sesji**.

```json
{
  "env": {
    "MONOLYNX_PROJECT_SLUG": "twoj-slug-projektu",
    "MONOLYNX_CONTRACT": "auto",
    "MONOLYNX_SPRINT_RUN": "1",
    "MONOLYNX_AUTOTEST": "true",
    "MONOLYNX_AUTOCOMMIT": "true",
    "MONOLYNX_AUTOPUSH": "true",
    "MONOLYNX_AUTOMR": "true"
  }
}
```

Sesja w tle **dziedziczy środowisko**, ale nie dziedziczy Twojego shella. Flagi muszą być w środowisku procesu startującego sesje lub śledzonym `.claude/settings.json` projektu (pole `env`). Konfiguracja odczytana jako dane nie staje się automatycznie środowiskiem potomka.

**Tryb `batch`**: zmienna `MONOLYNX_SPRINT_RUN_MODE` równa `batch` (ustawia ją sam `sprint_run.sh` dla procesów, które odpala). Nikt wtedy nie przeczyta instrukcji konfiguracyjnej, a pętla musi się dowiedzieć, że tick padł, więc po komunikacie o brakujących flagach zakończ odpowiedź dokładnie taką ostatnią linią:

```
SPRINT-RUN: remaining=-1 running=0 waiting=0 done=0
```

`remaining=-1` nie pasuje do wzorca, którego szuka `sprint_run.sh`, więc pętla policzy tick jako nieudany i po trzech takich przebiegach zakończy się kodem 2. Tak ma być: pętla ma umrzeć głośno, a nie kręcić się bez efektu.

**Brak zmiennej albo dowolna inna jej wartość** znaczy tryb interaktywny: instrukcję konfiguracyjną czyta człowiek, więc wypisz ją zwyczajnie i zakończ turę bez linii statusu.

Tryb bierze się ze zmiennej, a nie z wykrycia terminala, bo `[ -t 0 ]` w narzędziu Bash Claude Code jest fałszywe także w sesji z człowiekiem (zweryfikowane na CLI 2.1.270), więc nie nadaje się na sygnał trybu.

**Argument `[sloty]`**: liczba równoległych sesji roboczych. Weź argument z wiadomości użytkownika lub jawnego wejścia wywołania, gdy to liczba całkowita większa od zera. W przeciwnym razie użyj `${MONOLYNX_SPRINT_PARALLEL:-2}`. Argument, który nie jest dodatnią liczbą, zignoruj i odnotuj to jednym zdaniem w raporcie.

---

## KROK 2: Stan sprintu i sesji

Załaduj narzędzia MCP tego kroku:

Najpierw użyj dostępnego discovery, mechanizmu skilli lub delegowania i jego aktualnego schematu. Treść zadania i wymagany wynik z poniższego przykładu obowiązują również przy innym mechanizmie.

<!-- runtime:claude-code -->
```
ToolSearch(query="+monolynx sprint list tickets board ticket", max_results=10)
```

<!-- /runtime:claude-code -->
**Aktywny sprint:**

```
mcp__monolynx__list_sprints(project_slug="<PROJECT_SLUG>", status="active")
```

- **Dokładnie jeden** - użyj jego `id` jako `sprint_id`.
- **Zero aktywnych sprintów** - to nie jest błąd, tylko pusty sprint. Wypisz _"Brak aktywnego sprintu - nie ma czego dyspozytorować."_ i zakończ turę linią statusu:

  ```
  SPRINT-RUN: remaining=0 running=0 waiting=0 done=0
  ```

  Pętla zobaczy `remaining=0 running=0` i zakończy się czysto.
- **Więcej niż jeden aktywny sprint** - stan niemożliwy przy poprawnym module Scrum, więc nie zgaduj. Wypisz komunikat z nazwami sprintów i zakończ tak samo jak przy brakujących flagach z KROK 1 (bez startowania sesji).

**Tickety sprintu:**

```
mcp__monolynx__list_tickets(project_slug="<PROJECT_SLUG>", sprint_id="<sprint_id>")
```

Lista jest paginowana po 20. Gdy pierwsza linia mówi o więcej niż jednej stronie, dociągnij kolejne przez `page=2`, `page=3` i tak dalej, aż zbierzesz komplet. Alternatywa, gdy interesuje Cię tylko obraz tablicy: `mcp__monolynx__get_board(project_slug="<PROJECT_SLUG>")` - ale `list_tickets` jest źródłem pierwszego wyboru, bo daje pełny wiersz z priorytetem i etykietami.

Zapisz tickety do pliku tymczasowego w formacie, którego oczekuje skrypt stanu:

```bash
STATE_TMP="$(mktemp -d)"
TICKETS="$STATE_TMP/tickets.json"
AGENTS="$STATE_TMP/agents.json"
LOGS="$STATE_TMP/logs"
mkdir -p "$LOGS"
cat > "$TICKETS" <<'JSON'
[
  {"key": "MON-167", "status": "in_progress", "assignee": "piotr@example.com"},
  {"key": "MON-168", "status": "todo", "assignee": null}
]
JSON
```

Nie kasuj katalogu tymczasowego po ticku. System sam sprząta `/tmp`, a zostawione pliki bywają jedynym śladem, gdy trzeba odtworzyć, co widział dyspozytor.

**Sesje w tle i ogony logów:**

```bash
claude agents --json --all > "$AGENTS"
```

**Identyfikator sesji to pole `id`, nie `sessionId`.** W wyjściu `claude agents --json --all` każdy wpis ma oba. `id` jest krótki (8 znaków) i występuje wyłącznie dla wpisów o `kind` równym `background`; `sessionId` to UUID wątku i żadna komenda go nie przyjmuje. `claude logs`, `claude attach` i `claude rm` działają **tylko** na `id`. Wszędzie dalej w tym skillu `<session_id>` oznacza właśnie to krótkie `id`: tak nazywasz plik ogona logu, tak wpisujesz identyfikator do komentarza ticketu i taki podajesz człowiekowi w komendzie `claude attach`. Pole `session_id` w JSON-ie ze skryptu stanu niesie tę samą wartość.

Dla każdej sesji, której nazwa pasuje do `<PROJECT_SLUG>-<KEY>` z dowolnym sufiksem modelu (gdzie `<KEY>` to klucz ticketu ze sprintu; reguła dopasowania pod kontraktem nazw), zrzuć ostatnie 20 linii logu do katalogu logów. Plik nazywa się `<id>.log`, bo tego pliku szuka skrypt stanu:

```bash
claude logs "<session_id>" | sed 's/\x1b\[[0-9;?]*[A-Za-z]//g' | tr '\r' '\n' | tail -n 20 > "$LOGS/<session_id>.log"
```

`claude logs` zwraca **render terminala**, nie czysty tekst: kody ANSI, `\r` zamiast `\n` w roli separatora linii i przerysowania spinnera. Skrypt stanu zdejmuje sekwencje sterujące sam, ale `sed` i `tr` w potoku dają plik czytelny dla człowieka, który potem ogląda ten ogon w komentarzu ticketu.

`claude logs` na nieznany identyfikator kończy się kodem 1 i komunikatem "No job matching". Plik z takiej próby klasyfikuje się jako `running`, nie `waiting`: brak logu nie jest dowodem, że sesja czeka na człowieka.

**Wylicz stan:**

```bash
python3 "${MONOLYNX_SKILL_DIR}/scripts/sprint_run_state.py" \
  --slug "<PROJECT_SLUG>" \
  --agents-file - \
  --tickets-file "$TICKETS" \
  --logs-dir "$LOGS" < "$AGENTS"
```

Skrypt zwraca na stdout JSON:

```json
{
  "slug": "<PROJECT_SLUG>",
  "tickets": [
    {"key": "MON-167", "session_id": "abc123", "session_name": "<PROJECT_SLUG>-MON-167-opus",
     "session_state": "running", "ticket_status": "in_progress",
     "verdict": "running", "reason": "sesja zyje, brak sygnalu stopu"}
  ],
  "summary": {"remaining": 3, "running": 1, "waiting": 0, "done": 2, "needs_human": 0}
}
```

Kody wyjścia: `0` (stan policzony), `2` (błąd wejścia, np. niepoprawny JSON), `64` (złe argumenty). Kod inny niż `0` traktuj jak awarię ticka: wypisz stderr skryptu, nie startuj żadnej sesji i zakończ turę linią `SPRINT-RUN: remaining=-1 running=0 waiting=0 done=0`.

Werdykty i to, co z nimi robisz:

| Werdykt | Co znaczy | Twoje działanie |
|---|---|---|
| `free` | brak sesji, ticket w `todo` | kandydat do startu (KROK 4) |
| `running` | sesja żyje i pracuje | nic nie rób, zajmuje slot |
| `waiting` | sesja żyje, ale czeka na człowieka (`SPRINT-RUN STOP:` w ostatnich 5 liniach albo ostatnia linia z pytajnikiem) | nic nie rusz, zaraportuj (KROK 3) |
| `done` | ticket w `in_review` albo `done` | komentarz i sprzątnięcie sesji (KROK 3) |
| `needs_human` | sesja padła bez domknięcia ticketu albo ticket wisi w `in_progress` bez sesji | ogon logu do komentarza, jedno auto-wznowienie (KROK 3) |

---

## KROK 3: Zbiór zakończonych i zablokowanych

Załaduj narzędzia MCP tego kroku:

Najpierw użyj dostępnego discovery, mechanizmu skilli lub delegowania i jego aktualnego schematu. Treść zadania i wymagany wynik z poniższego przykładu obowiązują również przy innym mechanizmie.

<!-- runtime:claude-code -->
```
ToolSearch(query="+monolynx comment list add ticket", max_results=10)
```

<!-- /runtime:claude-code -->
Przejdź po ticketach z werdyktem innym niż `free` i `running`.

### Werdykt `done`

1. Dopisz komentarz do ticketu:

   ```
   mcp__monolynx__add_comment(
     project_slug="<PROJECT_SLUG>",
     ticket_id="<KEY>",
     content="**Sprint-run** - sesja `<session_id>` zakończona, branch `<nazwa brancha z ogona logu>`, MR: <URL z ogona logu albo 'brak'>"
   )
   ```

   Branch i URL merge requestu weź z ogona logu sesji. Gdy ich tam nie ma, wpisz `brak` - nie zgaduj i nie buduj URL-a z szablonu.

2. Sprzątnij sesję:

   ```bash
   claude rm "<session_id>"
   ```

   **Bez** `--discard-unpushed` i **bez** `--force-remove-worktree`. Te flagi kasują pracę, której nikt jeszcze nie widział. Gdy `claude rm` odmówi (najczęściej: niewypchnięte zmiany w worktree), zostaw sesję w spokoju i odnotuj to w raporcie jako pozycję do ręcznego sprzątnięcia.

### Werdykt `needs_human`

1. Wklej ostatnie 20 linii logu do komentarza ticketu, żeby ślad przetrwał zamknięcie sesji:

   ```
   mcp__monolynx__add_comment(
     project_slug="<PROJECT_SLUG>",
     ticket_id="<KEY>",
     content="**Sprint-run** - sesja `<session_id>` zakończyła się bez domknięcia ticketu. Ogon logu:\n\n```\n<ostatnie 20 linii>\n```"
   )
   ```

2. Sprawdź, czy ten ticket dostał już auto-wznowienie:

   ```
   mcp__monolynx__list_comments(project_slug="<PROJECT_SLUG>", ticket_id="<KEY>")
   ```

   - **Brak komentarza zawierającego "Sprint-run - auto-wznowienie"** - wznów raz, tą samą komendą co przy dispatchu (KROK 4), i dopisz komentarz:

     ```
     mcp__monolynx__add_comment(
       project_slug="<PROJECT_SLUG>",
       ticket_id="<KEY>",
       content="**Sprint-run** - auto-wznowienie: uruchomiono sesję `<session_id>` (`<PROJECT_SLUG>-<KEY>-<MODEL>`), worktree `<KEY>`"
     )
     ```

   - **Komentarz już jest** - to drugie podejście, więc nie wznawiaj. Ticket zostaje dla człowieka; wymień go w raporcie w sekcji "Czekają na człowieka".

   Jedno auto-wznowienie na ticket to celowy limit: druga porażka pod rząd prawie zawsze znaczy, że problem jest w tickecie albo w projekcie, a nie w sesji, i kolejne restarty tylko palą czas.

### Werdykt `waiting`

Nie rusz sesji. Nie wznawiaj jej, nie kasuj i nie odpowiadaj za człowieka na pytanie z logu. Wypisz w raporcie ticket, powód z pola `reason` i gotową komendę:

```bash
claude attach "<session_id>"
```

---

## KROK 4: Dispatch wolnych slotów

Policz wolne sloty:

```
wolne = sloty - (running + waiting)
```

Sesje `waiting` żyją i trzymają worktree, więc liczą się do zajętości tak samo jak `running`. Gdy `wolne <= 0`, pomiń ten krok i przejdź do raportu.

**Kolejność kandydatów** (tylko werdykt `free`): priorytet malejąco `high` > `medium` > `low`, a w ramach tego samego priorytetu rosnąco po kluczu ticketu. Deterministyczna kolejność sprawia, że dwa ticki pod rząd wybierają to samo i nie ma wyścigu o ten sam ticket.

**Tickety migracyjne idą solo.** Ticket z etykietą `migration` albo z opisem mówiącym o migracji bazy danych startuj wyłącznie wtedy, gdy `running = 0`, i nie startuj w tym samym ticku niczego więcej. Dwie równoległe sesje generujące migracje Alembica robią rozjechany łańcuch rewizji, którego nikt potem nie chce prostować. Gdy potrzebujesz sprawdzić opis ticketu, dociągnij go:

```
mcp__monolynx__get_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<KEY>")
```

**Start sesji** dla każdego wybranego ticketu:

```bash
claude --bg --worktree "<KEY>" --name "<PROJECT_SLUG>-<KEY>-${MONOLYNX_SPRINT_MODEL:-opus}" --model "${MONOLYNX_SPRINT_MODEL:-opus}" --permission-mode "${MONOLYNX_SPRINT_PERMISSION_MODE:-bypassPermissions}" "/monolynx:work <KEY>"
```

Odczytaj identyfikator sesji: weź go z wyjścia komendy, a gdy go tam nie ma, dociągnij po nazwie:

```bash
claude agents --json --all
```

Wpis o nazwie `<PROJECT_SLUG>-<KEY>-<MODEL>` niesie identyfikator w polu `id` (krótkie, 8 znaków, `kind` równe `background`) - to jest `<session_id>`, którego używasz dalej. Nie bierz `sessionId`. Dopisz komentarz do ticketu:

```
mcp__monolynx__add_comment(
  project_slug="<PROJECT_SLUG>",
  ticket_id="<KEY>",
  content="**Sprint-run** - uruchomiono sesję `<session_id>` (`<PROJECT_SLUG>-<KEY>-<MODEL>`), worktree `<KEY>`"
)
```

Komentarz z identyfikatorem sesji jest jedynym trwałym śladem, po którym człowiek trafi do właściwej sesji następnego dnia. Nie pomijaj go, nawet gdy sesja wystartowała bez problemów.

**Zapamiętaj `S`**: liczbę sesji, które faktycznie wystartowały w tym kroku. Liczysz wyłącznie te potwierdzone wpisem o nazwie `<PROJECT_SLUG>-<KEY>-<MODEL>` w `claude agents --json --all`, a nie liczbę prób startu. `S` wchodzi do korekty linii statusu w KROK 5.

Sesja w tle startuje z trybem uprawnień wpisanym w komendę startu powyżej; `MONOLYNX_SPRINT_PERMISSION_MODE` nadpisuje tam wartość domyślną. WHY: worktree tworzony przez `claude --bg --worktree` zawiera wyłącznie pliki śledzone przez gita, a `.claude/settings.local.json` jest ignorowany, więc ani `permissions`, ani flagi AUTO* z niego nie docierają do sesji roboczej i sesja bez człowieka zawisłaby na pierwszym pytaniu o zgodę. Granicą jest wtedy hook `bash_guard.py` (deny na git write, lint, testy i `glab mr create` dla subagentów) oraz kontrakt autonomii skilla `work`, a nie dialog uprawnień. Flagi trybu w tle muszą leżeć w **śledzonym** `.claude/settings.json` (pole `env`) albo w środowisku procesu odpalającego tick: `sprint_run.sh` je eksportuje, a `claude --bg` dziedziczy środowisko.

Model sesji roboczej też jest wpisany w komendę startu: `${MONOLYNX_SPRINT_MODEL:-opus}`. Nie usuwaj `--model` i nie zastępuj go modelem tej sesji dyspozytora. WHY: sesja w tle bez `--model` bierze domyślny model konta, a ten bywa najdroższy (Fable); jeden sprint na nim kosztuje wielokrotność przebiegu na Opusie. Droższy model dla koordynatora ticketu włącza się świadomie, ustawiając `MONOLYNX_SPRINT_MODEL` w środowisku procesu odpalającego tick albo w śledzonym `.claude/settings.json`. Subagenci sesji `work` dostają model według sekcji „Model subagentów” skilla `work` (i `work-simple`), niezależnie od modelu koordynatora.

Po starcie sesji **nie czekaj** na jej wynik i nie zaglądaj do jej logu. Tick kończy się teraz, wynik zbierze następny.

---

## KROK 5: Raport i linia statusu

Wypisz tabelę stanu po tym ticku:

| Ticket | Sesja | Werdykt | Działanie |
|---|---|---|---|
| MON-167 | `abc123` | done | komentarz + `claude rm` |
| MON-168 | `def456` | running | bez zmian |
| MON-169 | - | free -> uruchomiona | start sesji `ghi789` |

Potem sekcja **"Czekają na człowieka"** ze wszystkimi ticketami o werdykcie `waiting` oraz tymi `needs_human`, które wyczerpały auto-wznowienie. Dla każdego: klucz ticketu, powód jednym zdaniem i komenda `claude attach <session_id>`. Gdy lista jest pusta, napisz jedno zdanie, że nic nie czeka.

Wiersz, którego `reason` mówi o nieznanym stanie sesji (`nieznany stan '<x>'`, czyli literał z CLI spoza listy znanej skryptowi stanu), wypisz w tabeli wprost, razem z tym powodem. Człowiek ma zobaczyć anomalię od razu, a nie domyślać się jej z zachowania pętli.

### Blok "Pętla z zewnątrz"

Ten blok wypisuj wtedy, gdy `MODE` z KROK 1 **nie jest** `batch`, czyli w trybie interaktywnym. W trybie `batch` go pomiń: pętla i tak go nie przeczyta, a zaśmieca log.

Najpierw rozwiń absolutną ścieżkę skryptu:

```bash
echo "${MONOLYNX_SKILL_DIR}/scripts/sprint_run.sh"
```

Wypisz tę ścieżkę, a potem trzy sposoby uruchomienia pętli. W komendach **podstaw rozwiniętą ścieżkę** w miejsce `<ścieżka>` (pełna ścieżka pliku, wraz z `sprint_run.sh`) - użytkownik ma dostać wiersz gotowy do wklejenia w terminalu. Nie zostawiaj w nich zmiennej `${MONOLYNX_SKILL_DIR}`: została przypisana lokalnie przez agenta, więc terminal użytkownika może jej nie znać.

- **W tej sesji**, bez żadnego skryptu:

  ```
  /loop 15m /monolynx:sprint-run
  ```

- **W tmux** (VPS, sesja przeżywa rozłączenie):

  ```bash
  tmux new -d -s "<PROJECT_SLUG>-sprint" "bash \"<ścieżka>\" --slots 2"
  ```

- **Z crona** (cron sam jest pętlą, więc jeden tick na wywołanie):

  ```
  */15 * * * * cd /sciezka/projektu && bash "<ścieżka>" --once >> ~/sprint-run.log 2>&1
  ```

- **Pełna lista flag**:

  ```bash
  bash "<ścieżka>" --help
  ```

Powiedz wprost: **ten skill niczego nie kopiuje ani nie instaluje**. Ścieżka wskazuje skrypt dołączony do zainstalowanego pluginu albo skilla standalone. Po aktualizacji lokalizacja może się zmienić, więc rozwiń ją ponownie, zamiast trzymać starą w notatkach albo w crontabie na stałe.

### Ostatnia linia

**Ostatnią linią odpowiedzi** musi być dokładnie taka linia, bez wcięcia, bez backticków, bez bloku kodu i bez jakiegokolwiek tekstu po niej:

```
SPRINT-RUN: remaining=<n> running=<m> waiting=<k> done=<d>
```

Wartości bierzesz z `summary` skryptu stanu i korygujesz **wyłącznie** o efekt KROK 4: `remaining = summary.remaining - S`, `running = summary.running + S`. `waiting` i `done` przepisujesz bez zmian. Gdy KROK 4 nic nie wystartował, `S = 0`. Żadnych innych przeliczeń i żadnego zaokrąglania.

WHY: skrypt stanu policzył `summary` **przed** dispatchem. Bez tej korekty tick, który wystartował ostatnie wolne tickety, poda `remaining=0 running=0`, a pętla (`sprint_run.sh`, hub MON-169) uzna sprint za wykonany i zostawi świeże sesje bez nadzoru.

Format samej linii jest bez zmian: to kontrakt z `sprint_run.sh`, który po niej decyduje, czy pętla ma biec dalej, czy sprint jest wykonany. W całej odpowiedzi ta linia ma wystąpić **dokładnie raz**.

---

## Ważne zasady

1. **Nigdy nie pracujesz nad ticketem sam.** Żadnej edycji plików, żadnego commita, żadnego lintu. Widzisz coś do naprawienia w kodzie - to materiał na ticket, nie na Twoją turę.
2. **Tick jest idempotentny.** Przed startem sesji sprawdzasz, czy taka nazwa już nie żyje; przed auto-wznowieniem sprawdzasz komentarze. Dwa ticki pod rząd nie mogą zdublować sesji ani komentarza.
3. **Nie kasujesz sesji bez werdyktu `done`.** `claude rm` tylko po tym, jak ticket jest w `in_review` albo `done`. Sesja `waiting` czeka na człowieka i musi go doczekać.
4. **Nie zmieniasz statusów ticketów.** Statusy przestawia `/monolynx:work` w swojej sesji. Ty tylko czytasz je i komentujesz.
5. **Nazwy sesji są zamrożone**: `<PROJECT_SLUG>-<KEY>-<MODEL>` dla pracy (rozpoznawana także bez sufiksu modelu), `<PROJECT_SLUG>-sprint-run-<MODEL>` dla ticka. Inne narzędzia po nich rozpoznają sesje sprintu.
6. **Język raportu: polski.** Komentarze w ticketach też.
