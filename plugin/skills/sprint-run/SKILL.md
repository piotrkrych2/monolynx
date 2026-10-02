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

**Uprawnienia ticku (MON-190)**: tick uruchomiony w `claude -p` ma tylko trzy reguły Bash - `Bash(claude:*)`, `Bash(MONOLYNX_SPRINT_RUN=1 claude:*)` (start sesji w KROK 4, MON-207) i `Bash(python3:*)`. W komendach tego skilla podstawiaj ścieżkę katalogu skilla **literalnie** (rozwiniętą absolutną ścieżkę), nigdy jako `${MONOLYNX_SKILL_DIR}` czy inne przypisanie zmiennej powłoki - i nigdy `&&`, `|`, `>` w tej samej komendzie. Każda komenda tego skilla to pojedyncze, samodzielne wywołanie `claude ...` albo `python3 ...`; jedynym wyjątkiem jest prefiks `MONOLYNX_SPRINT_RUN=1` przed `claude --bg` w KROK 4.

Jesteś **dyspozytorem sprintu**. Jedno uruchomienie tego skilla to jeden **tick**: czytasz stan sprintu i stan sesji w tle, startujesz brakujące sesje, zbierasz zakończone, raportujesz i kończysz turę.

Dyspozytor jest **cienki**. Trzy zasady, które go definiują:

1. **Nigdy nie pracujesz nad ticketem sam.** Nie czytasz kodu ticketu, nie edytujesz plików, nie commitujesz. Od pracy jest `/monolynx:work` w osobnej sesji.
2. **Świeży proces per ticket.** Każdy ticket dostaje własną nazwaną sesję w tle z własnym worktree, więc konteksty się nie mieszają, a jeden zablokowany ticket nie zatrzymuje reszty.
3. **Tick jest idempotentny.** Uruchomiony dwa razy pod rząd nie zdubluje sesji ani komentarzy: stan wynika z tego, co faktycznie żyje w `claude agents` i jaki status ma ticket, nie z pamięci poprzedniego przebiegu.

Sesje w tle działają bez człowieka dzięki flagom trybu w tle z MON-165 (`MONOLYNX_SPRINT_RUN=1` i `MONOLYNX_CONTRACT=auto`): `/monolynx:work` nie czeka na odpowiedzi, tylko kończy turę linią `SPRINT-RUN STOP: <powód>`, którą ten skill wykrywa jako werdykt `waiting`.

Pętla jest **na zewnątrz** tego skilla. Ty robisz jeden tick, pętlę robi `/loop 15m /monolynx:sprint-run` albo skrypt `sprint_run.sh` (patrz KROK 5).

---

## Ustalenie slug projektu

`project_slug` bierzesz z linii `SLUG=` wyjścia preflightu (KROK 1) - nie z osobnego odczytu `MONOLYNX_PROJECT_SLUG`, `echo` ani pliku `.env`. Ten skill nie przyjmuje slugu jako argumentu. Brak wartości nie jest scenariuszem do obsłużenia tutaj: preflight wymaga `MONOLYNX_PROJECT_SLUG` w środowisku procesu i sam kończy się błędem (`PREFLIGHT: failed`), zanim dojdziesz do tego miejsca.


Slug jest częścią kontraktu nazw: sesja robocza ticketu nazywa się `<PROJECT_SLUG>-<KEY>-<MODEL>` (np. `monolynx-MON-167-opus`), gdzie `<MODEL>` to wartość `${MONOLYNX_SPRINT_MODEL:-opus}` z chwili startu, a sesja samego dyspozytora w `sprint_run.sh` `<PROJECT_SLUG>-sprint-run-<MODEL>`, gdzie `<MODEL>` to `${MONOLYNX_SPRINT_TICK_MODEL:-opus}` (tak samo `<PROJECT_SLUG>-sprint-end-<MODEL>` i `<PROJECT_SLUG>-retro-<MODEL>` przy `--close`). Każda nazwa sesji kończy się modelem, na którym sesja działa. Te nazwy są **zamrożone** - inne narzędzia po nich rozpoznają sesje sprintu. Nie zmieniaj ich i nie dodawaj innych sufiksów.

Sesję ticketu rozpoznajesz po nazwie równej `<PROJECT_SLUG>-<KEY>` albo zaczynającej się od `<PROJECT_SLUG>-<KEY>-`, z dowolnym modelem na końcu. WHY: po zmianie `MONOLYNX_SPRINT_MODEL` między tickami (i dla sesji nazwanych przed dodaniem modelu do nazwy) żywa sesja musi nadal zajmować ticket, inaczej tick wystartuje drugą. Myślnik po kluczu jest obowiązkowy: `monolynx-MON-1-` nie łapie `monolynx-MON-10-opus`.

Nazwa sesji jest kontraktem wyłącznie dla procesów `claude`. `codex exec` nie ma odpowiednika `--name` ani `--bg` (potwierdzone `codex exec --help`, `codex-cli 0.155.1`), więc sesje prowadzone przez adapter Codeksa rozpoznaje się po tożsamości `<PROJECT_SLUG>:<KEY>` w rejestrze dyspozytora, nie po nazwie procesu.

---

## Ustalenie backendu (runtime)

Dyspozytor ma dwa backendy: `claude` (domyślny) i `codex`. Wybiera je `MONOLYNX_SPRINT_RUNTIME`, którą czyta `sprint_run.sh` i uruchamia tick w odpowiedniej binarce. **Nie odczytujesz tej zmiennej osobną komendą** - tick w `claude -p` nie ma reguły na `echo`, a przy Codeksie ten odczyt i tak nic by nie zmienił. Backend poznajesz po kliencie, w którym wykonujesz ten skill:

- **Claude Code** - backend `claude`. Preflight (KROK 1) i stan (KROK 2) wołasz bez dodatkowych flag: domyślną wartością obu jest `claude`. To ścieżka niezmieniona od poprzednich wydań.
- **Codex** - backend `codex`. Preflight wołasz z `--runtime codex` (KROK 1), stan z `--backend codex` (KROK 2), a sesje robocze prowadzisz komendami z KROKU 4 dla tego backendu.

Backend rozstrzyga **czym sprawdzasz środowisko, skąd bierzesz stan sesji i czym startujesz sesje robocze**. Nie zmienia ani algorytmu ticku, ani werdyktów (`free`, `running`, `waiting`, `done`, `needs_human`), ani brzmienia linii `SPRINT-RUN: remaining=<n> running=<m> waiting=<k> done=<d>` i `SPRINT-RUN STOP: <powód>` - te są zamrożone i identyczne dla obu.

Jeden backend na tick: nie mieszaj `--runtime claude` z `--backend codex` ani odwrotnie. Stan policzony z niewłaściwego źródła pokaże żywe tickety jako `free` i KROK 4 wystartuje drugą sesję do tej samej pracy.

---

## KROK 1: Preflight

Dyspozytor prowadzi sesje robocze przez własny backend (`claude` albo `codex`, patrz sekcja wyżej). Nie zastępuj go natywnym mechanizmem subagentów bieżącego klienta ani sesją, której nie widzi w swoim źródle stanu. AUTO* i tryb uprawnień nie uchylają instrukcji użytkownika ani odmowy runtime; brak wymaganych zgód zatrzymuje dispatch.

Sprawdź środowisko jednym wywołaniem skryptu ticku. Bez tego tick w tle utknie na pierwszym pytaniu, na które nikt nie odpowie.

Backend `claude` (Claude Code):

```bash
python3 "<katalog skilla>/scripts/sprint_run_tick.py" preflight
```

Backend `codex` (ten skill wykonuje się w Codeksie):

```bash
python3 "<katalog skilla>/scripts/sprint_run_tick.py" preflight --runtime codex
```

Podstaw `<katalog skilla>` literalnie (rozwiniętą absolutną ścieżkę) - bez `${MONOLYNX_SKILL_DIR}`. Wołasz **jeden** z tych dwóch wariantów, zgodnie z sekcją "Ustalenie backendu (runtime)", i jest to **jedyna** komenda tego kroku.

**Jawny zakaz**: nigdy `printenv`, `env`, `set`, `export -p`, `declare -x` ani `echo` zmiennej spoza `MONOLYNX_*` - w tym kroku ani nigdzie indziej w tym skillu. WHY: 18.09 `printenv` wypisał do transkryptu i logu pętli klucz API OpenAI i token GitLaba zamiast samego statusu flag. Żadnych innych komend Bash w KROK 1 poza wywołaniem powyżej.

Zakres checków zależy od wybranego backendu. Przy `--runtime codex` skrypt **w ogóle nie czyta** `.claude/settings.json` ani `.claude/settings.local.json`, ignoruje `MONOLYNX_SPRINT_PERMISSION_MODE` i nie woła `claude --version` ani `claude agents --json --all`; zamiast tego sprawdza `codex --version` oraz `codex login status` (wymaga napisu `Logged in` w wyjściu). Wspólne dla obu backendów zostają: repozytorium git, `MONOLYNX_PROJECT_SLUG`, flagi `MONOLYNX_AUTOTEST` / `AUTOCOMMIT` / `AUTOPUSH` (wymagane `true`), ostrzeżenie o `MONOLYNX_AUTOMR` oraz `BASE_BRANCH`/`BASE_BRANCH_STALE` - one gatują działania samego ticku, niezależnie od tego, który klient go prowadzi. W wyjściu dochodzi klucz `RUNTIME=<claude|codex>`; przy `codex` `CLAUDE_VERSION` jest puste, a `PERMISSION_MODE` wypisane tylko informacyjnie.

Przy `--runtime claude` skrypt sprawdza, jako pierwszy check, że `MONOLYNX_SPRINT_PERMISSION_MODE` nie jest `bypassPermissions` ani nie zawiera `dangerously-skip-permissions` (ten tryb w sesji auto odrzuca domyślna reguła soft_deny "Create Unsafe Agents" - bez tego checku tick przechodził cały preflight i odpadał dopiero w KROKU 4 bez startu żadnej sesji), potem `claude --version`, `claude agents --json --all`, repozytorium git, kształt `.claude/settings.json` i `.claude/settings.local.json`, flagi `MONOLYNX_*` w środowisku procesu oraz `BASE_BRANCH`/`BASE_BRANCH_STALE` (MON-197: lancuch fallbacku `MONOLYNX_BASE_BRANCH` -> `MONOLYNX_MR_TARGET` -> `git symbolic-ref refs/remotes/origin/HEAD` -> `glab repo view`, i rozjazd głównego checkoutu dyspozytora wobec tego brancha), i wypisuje na stdout wyłącznie linie `KLUCZ=wartość` (`SLUG`, `MODE`, `PARALLEL`, `MAX_WAITING`, `SPRINT_MODEL`, `PERMISSION_MODE`, `AUTOTEST`, `AUTOCOMMIT`, `AUTOPUSH`, `AUTOMR`, `CONTRACT`, `SPRINT_RUN`, `CLAUDE_VERSION`, `PROJECT_DIR`, `TICK_DIR`, `BASE_BRANCH`, `BASE_BRANCH_STALE`, `RUNTIME`), potem ewentualne `WARN: ...`, potem `ERROR: ...`, a na końcu dokładnie jedną linię `PREFLIGHT: ok` albo `PREFLIGHT: failed problems=<n>`. Nigdy nie wypisuje wartości zmiennych spoza tej listy ani całego `env` pliku settings - nie próbuj tego obejść dociąganiem pliku ręcznie. `BASE_BRANCH` i `BASE_BRANCH_STALE` są informacyjne - puste/nieznane nigdy nie trafiają do `problems`, więc same z siebie nie blokują ticku (przy `BASE_BRANCH_STALE=true` KROK 4 najpierw próbuje `sync-base`, a blokadę w trybie `batch` robi dopiero wtedy, gdy przewinięcie się nie uda). Jedyny wyjątek (MON-219): gdy `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` są ustawione jednocześnie i różne, preflight dopisuje linię `ERROR:` (kod `2`, `PREFLIGHT: failed`), bo dyspozytor i `mr-queue` musiałyby celować w różne branche - wystarczy ustawić jedną z nich albo obie na ten sam branch.

**`PREFLIGHT: ok`** (kod wyjścia `0`): przejdź do KROK 2. Weź `SLUG` (patrz "Ustalenie slug projektu"), `PARALLEL` jako domyślną liczbę slotów, `MAX_WAITING` jako limit żywych sesji `waiting` (z `MONOLYNX_SPRINT_MAX_WAITING`, dodatnia liczba całkowita, inaczej `2`; podajesz go skryptowi stanu w KROK 2, a skutek widzisz w KROK 4), `SPRINT_MODEL` i `PERMISSION_MODE` do KROK 4, `MODE` do KROK 5, `BASE_BRANCH` i `BASE_BRANCH_STALE` do sprawdzenia na początku KROK 4, a `RUNTIME` jako potwierdzenie backendu, z którym pracujesz w tym ticku (wypisz go w raporcie KROK 5). Każdą linię `WARN:` (np. brak `MONOLYNX_AUTOMR`) dopisz do raportu jako ostrzeżenie, bez zatrzymywania ticku.

**`PREFLIGHT: failed problems=<n>`** (kod wyjścia `2`): wypisz użytkownikowi dosłownie każdą linię `ERROR:` i zakończ turę **bez startowania jakiejkolwiek sesji**. Pokaż gotową konfigurację do wklejenia:

```json
{
  "env": {
    "MONOLYNX_PROJECT_SLUG": "twoj-slug-projektu",
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

Ten blok jest dla ticku **interaktywnego** albo uruchamianego przez `/loop` w tej samej sesji - własne reguły osobiste trzymaj w `.claude/settings.local.json`, nie w tym pliku. Tick uruchomiony przez `sprint_run.sh` **nie potrzebuje** `permissions` w settings projektu: skrypt sam przekazuje uprawnienia ticku przez `--allowedTools`, niezależnie od tego, co jest w `.claude/settings.json`. Blok wyżej nie zawiera ścieżek absolutnych z nazwą konta ani numeru wersji pluginu - to szczegóły instalacji, nie projektu.

Sesja w tle **dziedziczy środowisko**, ale nie dziedziczy Twojego shella. Flagi muszą być w środowisku procesu startującego sesje lub śledzonym `.claude/settings.json` projektu (pole `env`). Konfiguracja odczytana jako dane nie staje się automatycznie środowiskiem potomka.

Blok wyżej celowo **nie zawiera** trzech zmiennych (MON-207). `MONOLYNX_SPRINT_RUN=1` dostaje wyłącznie sesja robocza, jako prefiks komendy startu w KROK 4 - w śledzonym `.claude/settings.json` przełączyłby w tryb dyspozytora także każdą zwykłą sesję na `main` (hook `bash_guard.py` zamienia wtedy `ask` na `deny`). `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` ustawiasz na czas sprintu w środowisku procesu dyspozytora albo w `.claude/settings.local.json`, nigdy w śledzonym `.claude/settings.json`, który trafia na `main`. `scripts/validate_plugin_consistency.py --release` odrzuca śledzony plik z którąkolwiek z tych trzech zmiennych.

**Tryb `batch`** (`MODE=batch` z wyjścia preflightu, ustawia go sam `sprint_run.sh` dla procesów, które odpala): nikt wtedy nie przeczyta instrukcji konfiguracyjnej, a pętla musi się dowiedzieć, że tick padł, więc po komunikacie o brakujących flagach zakończ odpowiedź dokładnie taką ostatnią linią:

```
SPRINT-RUN: remaining=-1 running=0 waiting=0 done=0
```

`remaining=-1` nie pasuje do wzorca, którego szuka `sprint_run.sh`, więc pętla policzy tick jako nieudany i po trzech takich przebiegach zakończy się kodem 2. Tak ma być: pętla ma umrzeć głośno, a nie kręcić się bez efektu.

**`MODE=interactive`** (brak `MONOLYNX_SPRINT_RUN_MODE` albo dowolna inna wartość): instrukcję konfiguracyjną czyta człowiek, więc wypisz ją zwyczajnie i zakończ turę bez linii statusu.

**Argument `[sloty]`**: liczba równoległych sesji roboczych. Weź argument z wiadomości użytkownika lub jawnego wejścia wywołania, gdy to liczba całkowita większa od zera. W przeciwnym razie użyj `PARALLEL` z wyjścia preflightu. Argument, który nie jest dodatnią liczbą, zignoruj i odnotuj to jednym zdaniem w raporcie.

---

## KROK 2: Stan sprintu i sesji

**Transport: CLI albo MCP** - ustal raz na tick, osobną komendą, dopiero po `PREFLIGHT: ok`:

<!-- shared:transport -->
Operacje na platformie Monolynx wykonujesz jednym z dwoch transportow: CLI `monolynx` (zawsze przez wrapper) albo tools MCP. Zapis `mcp__monolynx__<tool>(...)` w tym skillu oznacza operacje `<tool>` z podanymi argumentami, niezaleznie od transportu. Regula jest ta sama w Claude Code i w Codex.

**Preflight - raz, na starcie skilla** (nie przed kazda operacja):

```bash
python3 <katalog skilla>/scripts/monolynx_transport.py
```

`<katalog skilla>` to absolutna sciezka katalogu tego pliku, wpisana literalnie. Komendy obu skryptow pisz bez zmiennych powloki, bez `~`, bez pipe, bez przekierowan i bez nawiasow `(` `)`: hook pluginu i sesje w worktree odrzucaja takie formy. Ostatnia linia wyniku jest zamrozona:

`TRANSPORT: mode=cli|mcp cli=<wersja|none> auth=ok|missing latest=<wersja|unknown> update=yes|no install=brew|pipx|uv|unknown|none`

| Wynik preflightu | Postepowanie |
|---|---|
| `mode=cli` | Operacje wykonujesz wrapperem `monolynx_op.py` (nizej). |
| `mode=mcp` | Tylko tools MCP, wrappera nie wolasz. |
| Jest linia `TRANSPORT-HINT:` (brak CLI, CLI niezalogowane albo nowsza wersja) | Przepisz ja uzytkownikowi jedna linia i pracuj dalej wedlug `mode`. Nie pytaj, nie czekaj, niczego nie instaluj. |
| `mode=mcp` i runtime nie udostepnia tooli MCP Monolynx | STOP, bez zgadywania. Podaj instrukcje: `pipx install monolynx-cli` i `monolynx auth login`, albo podpiecie connectora MCP Monolynx. |
| Preflight sie nie uruchomil (brak `python3`, odmowa uprawnienia) | Pracuj jak przy `mode=mcp`. |

**Operacja przy `mode=cli`** - pierwszy argument to nazwa toola, flagi to dokladne nazwy jego parametrow:

```bash
python3 <katalog skilla>/scripts/monolynx_op.py get_ticket --project_slug <PROJECT_SLUG> --ticket_id <KEY>
```

Wartosc bool to `true` albo `false`, lista to tablica JSON w jednej wartosci. Dluga tresc (komentarz, opis, strona wiki) i kazda wartosc ze znakami specjalnymi powloki: zapisz do pliku i podaj jako `@<sciezka pliku>`. Komend `monolynx ...` nie skladaj samodzielnie - mapowanie operacji na komendy CLI zyje we wrapperze.

Kody wyjscia wrappera:

- `0` - stdout to JSON API v2 z CLI. Niesie te same dane co tekstowa odpowiedz toola MCP, pod nazwami pol JSON.
- `3` z ostatnia linia `OP-FALLBACK: mcp tool=<nazwa>` - wywolaj ten tool MCP z tymi samymi argumentami; jego aktualny schemat poznaj mechanizmem runtime przed wywolaniem. To zwykla sciezka operacji bez komendy CLI (Pipelines, `log_time`, graf, LLM Wiki). Brak tego toola w runtime: stop z nazwa brakujacej operacji.
- `2` - blad operacji (API, siec, przekroczony czas). Przy zapisie nie ponawiaj na slepo przez MCP: najpierw odczytem sprawdz, czy zapis doszedl.
- `64` - blad uzycia wrappera, CLI nie bylo wolane. Popraw argumenty.

`get_ticket` przez CLI nie zwraca `spec_page_id` (linia `OP-NOTE:` na stderr) - gdy pole jest potrzebne, doczytaj je toolem MCP `get_ticket`.

Subagent pracuje przez MCP, chyba ze dostal w prompcie wynik `mode=cli` i literalna sciezke wrappera. `MONOLYNX_TRANSPORT=mcp` w srodowisku wymusza MCP w preflighcie i we wrapperze (zachowanie sprzed wprowadzenia CLI).
<!-- /shared:transport -->

Podpowiedź `TRANSPORT-HINT:` wypisuje dyspozytor; sesje robocze startowane z `MONOLYNX_SPRINT_RUN=1` jej nie dostają. Komentarz dłuższy niż jedna linia (ogon logu w KROK 3) zapisz do pliku w `TICK_DIR` i podaj jako `@<ścieżka>`; gdy tick nie ma narzędzia zapisu pliku, wykonaj `add_comment` toolem MCP. Przy `mode=cli` wyniki to JSON: `list_sprints` daje listę z polem `id`, `list_tickets` kopertę `items` (pola `key`, `status`, `assignee_email`) z `page` i `total_pages`, a blokery ticketu w `get_ticket` to lista `blocked_by` (elementy `key`, `status`, `title`).

Załaduj narzędzia MCP tego kroku (dla `mode=mcp` i operacji, przy których wrapper zwrócił kod 3):

Najpierw użyj dostępnego discovery, mechanizmu skilli lub delegowania i jego aktualnego schematu. Treść zadania i wymagany wynik z poniższego przykładu obowiązują również przy innym mechanizmie.

<!-- runtime:claude-code -->
```
ToolSearch(query="+monolynx sprint list tickets board get ticket", max_results=10)
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

Lista jest paginowana po 20. Gdy pierwsza linia (w JSON z CLI: pole `total_pages`) mówi o więcej niż jednej stronie, dociągnij kolejne przez `page=2`, `page=3` i tak dalej, aż zbierzesz komplet. Alternatywa, gdy interesuje Cię tylko obraz tablicy: `mcp__monolynx__get_board(project_slug="<PROJECT_SLUG>")` - ale `list_tickets` jest źródłem pierwszego wyboru, bo daje pełny wiersz z priorytetem i etykietami.

Zbuduj z ticketów listę JSON w kształcie, którego oczekuje skrypt stanu (klucze `key`, `status`, opcjonalnie `assignee`):

```json
[
  {"key": "MON-167", "status": "in_progress", "assignee": "piotr@example.com"},
  {"key": "MON-168", "status": "todo", "assignee": null}
]
```

**Identyfikator sesji to pole `id`, nie `sessionId`.** W wyjściu `claude agents --json --all` każdy wpis ma oba. `id` jest krótki (8 znaków) i występuje wyłącznie dla wpisów o `kind` równym `background`; `sessionId` to UUID wątku i żadna komenda go nie przyjmuje. `claude logs`, `claude attach` i `claude rm` działają **tylko** na `id`. Wszędzie dalej w tym skillu `<session_id>` oznacza właśnie to krótkie `id`: tak nazywasz plik ogona logu, tak wpisujesz identyfikator do komentarza ticketu i taki podajesz człowiekowi w komendzie `claude attach`. Pole `session_id` w JSON-ie ze skryptu stanu niesie tę samą wartość.

**Wylicz stan** jednym wywołaniem. Podkomenda `state` sama woła `claude agents --json --all`, dla każdej sesji dopasowanej do ticketu (nazwa `<PROJECT_SLUG>-<KEY>` z dowolnym sufiksem modelu) woła `claude logs <id>` i przepuszcza render terminala przez `render_log` ze `scripts/sprint_run_state.py` (`claude logs` nie zwraca czystego tekstu - kolory, ruch kursora, przerysowania spinnera): minimalny emulator ekranu odtwarza, co widać na ekranie, odrzuca linie samego spinnera i statusu (glif `✢✳✶✻✽·` na początku, liczniki czasu i tokenów) i skleja przylegające identyczne bloki w jeden. Potem bierze ostatnie 60 niepustych linii i zapisuje pliki stanu w `TICK_DIR` (`.git/monolynx/tick/`, **nigdy** w `/tmp`):

```bash
python3 "<katalog skilla>/scripts/sprint_run_tick.py" state --slug "<PROJECT_SLUG>" --tickets-json '<JSON ticketów z kroku wyżej>' --slots <sloty> --max-waiting <MAX_WAITING>
```

Backend `codex` - ta sama komenda z jawną flagą:

```bash
python3 "<katalog skilla>/scripts/sprint_run_tick.py" state --slug "<PROJECT_SLUG>" --tickets-json '<JSON ticketów z kroku wyżej>' --slots <sloty> --max-waiting <MAX_WAITING> --backend codex
```

`<sloty>` to liczba slotów ustalona w KROK 1 (argument `[sloty]` albo `PARALLEL`), `<MAX_WAITING>` to wartość z wyjścia preflightu. Obie wpisujesz jako literalne liczby, np. `--slots 2 --max-waiting 2`.

Domyślną wartością `--backend` jest `claude`, więc w Claude Code flagi nie dopisujesz. Wariant `codex` bierze stan **wyłącznie** z rejestru dyspozytora (`sessions.json` w `TICK_DIR`), odświeżanego przed klasyfikacją, i nie woła binarki `claude` ani razu - `codex exec` nie ma odpowiednika `claude agents --json --all`. Ogon logu pochodzi wtedy z JSONL sesji (`codex-logs/<PROJECT_SLUG>-<KEY>-<runtime_id>.jsonl`), a nie z `claude logs`.

Podstaw `<katalog skilla>`, JSON ticketów i obie liczby literalnie, w jednej linii - bez `${MONOLYNX_SKILL_DIR}`, bez `&&`, `|`, `>`. Nic z tego, co robi ta podkomenda (pobranie sesji, pobranie logów, render ekranu, zapis plików), nie powtarzasz ręcznie ani osobnymi komendami `claude agents`/`claude logs`/`sed`/`tr`. Zwykłe zdjęcie kodów ANSI nie wystarcza: render TUI przerysowuje ekran różnicowo i przeskakuje kursorem nad komórkami, które się nie zmieniły, więc strip gubi litery i spacje (marker `SPRINT-RUN STOP:` przychodził jako `SPRINT-RUNSTOP:`, w ogonach z nocy 28/29.09 było `com it`), a każde przerysowanie od początku dopisuje kopię tych samych bloków. Emulator traktuje skok kursora o N komórek jako N spacji, a `\r` jako powrót karetki, nie koniec linii. Ogon 60 linii mieści cały ostatni blok odpowiedzi asystenta (od ostatniej linii `⏺`), w którym klasyfikator szuka markera i pytania (MON-191). Plik czyta też człowiek (ogon trafia do komentarza przy `needs_human`), więc ma być czytelny. Same werdykty (`done`, `needs_human`, `waiting`, `free`, `running`) liczy `scripts/sprint_run_state.py` (`build_report`, `match_session`) - `sprint_run_tick.py` go importuje jako moduł, więc oba skrypty jadą razem w paczce skilla.

`claude logs` na nieznany identyfikator kończy się kodem 1 i komunikatem "No job matching" - podkomenda `state` i tak zapisuje to, co dostała (bez przerywania), a taki przypadek klasyfikuje się jako `running`, nie `waiting`: brak logu nie jest dowodem, że sesja czeka na człowieka.

Skrypt zwraca na stdout JSON:

```json
{
  "slug": "<PROJECT_SLUG>",
  "tickets": [
    {"key": "MON-167", "session_id": "abc123", "session_name": "<PROJECT_SLUG>-MON-167-opus",
     "session_state": "running", "ticket_status": "in_progress",
     "verdict": "running", "reason": "sesja zyje, brak sygnalu stopu"}
  ],
  "summary": {"remaining": 3, "running": 1, "waiting": 0, "done": 2, "needs_human": 0},
  "dispatch": {"slots": 2, "max_waiting": 2, "free_slots": 1, "waiting_blocked": false, "can_start": 1}
}
```

Klucz `dispatch` pojawia się, gdy komenda dostała `--slots`, i niesie przepustowość KROK 4 policzoną przez skrypt (`sprint_run_state.dispatch_capacity`), nie przez Ciebie:

- `free_slots = max(0, slots - running)` - sesja `waiting` **nie zajmuje slotu**;
- `waiting_blocked = waiting >= max_waiting` - limit żywych sesji `waiting` osiągnięty;
- `can_start` - `0`, gdy `waiting_blocked` jest `true`, w przeciwnym razie `free_slots`.

Kody wyjścia: `0` (stan policzony), `2` (błąd wejścia - niepoprawny JSON ticketów, zły kształt ticketu, albo `claude agents --json --all` nie działa), `64` (złe argumenty). Kod inny niż `0` traktuj jak awarię ticka: wypisz stderr skryptu, nie startuj żadnej sesji i zakończ turę linią `SPRINT-RUN: remaining=-1 running=0 waiting=0 done=0`.

Werdykty i to, co z nimi robisz:

| Werdykt | Co znaczy | Twoje działanie |
|---|---|---|
| `free` | brak sesji, ticket w `todo` | kandydat do startu, o ile nie ma niezamkniętego blokera ani etykiety `needs-local` (KROK 4) |
| `running` | sesja żyje i pracuje | nic nie rób, zajmuje slot |
| `waiting` | sesja żyje, ale czeka na człowieka (`SPRINT-RUN STOP:` w ostatnich 5 liniach albo ostatnia linia z pytajnikiem) | nic nie rusz, zaraportuj (KROK 3); nie zajmuje slotu, ale liczy się do limitu `MAX_WAITING` |
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

2. Sprzątnij sesję. Backend `claude`:

   ```bash
   claude rm "<session_id>"
   ```

   **Bez** `--discard-unpushed` i **bez** `--force-remove-worktree`. Te flagi kasują pracę, której nikt jeszcze nie widział. Gdy `claude rm` odmówi (najczęściej: niewypchnięte zmiany w worktree), zostaw sesję w spokoju i odnotuj to w raporcie jako pozycję do ręcznego sprzątnięcia.

   Backend `codex`:

   ```bash
   python3 "<katalog skilla>/scripts/sprint_run_tick.py" stop --backend codex --slug "<PROJECT_SLUG>" --key "<KEY>"
   ```

   `STOPPED <identity>` (kod 0) - wpis usunięty z rejestru. Przy werdykcie `done` stan rekordu jest terminalny (`success` albo `failure`) i **żaden sygnał nie leci**: proces Codeksa już nie żyje, a jego PID mógł w międzyczasie przypaść komuś innemu. `SIGTERM` idzie wyłącznie pod sesję żywą **i wciąż istniejącą w systemie**, przerywaną świadomie, i wyłącznie pod jej własny PID. `NOT-FOUND <identity>` (również kod 0) - w rejestrze nie było takiego wpisu, czyli nie ma czego zatrzymywać; to nie jest błąd i nie ponawiasz. Ta komenda rusza **tylko** własny wpis dyspozytora: nie kasuje worktree, nie dotyka niewypchniętych zmian ani cudzych procesów. Katalog roboczy sesji i jej log (`codex-logs/<PROJECT_SLUG>-<KEY>-<runtime_id>.jsonl`) zostają na dysku do wglądu.

### Werdykt `needs_human`

1. Zostaw w tickecie ślad, który przetrwa zamknięcie sesji.

   Backend `claude` - wklej ostatnie 60 linii logu (całą treść `$LOGS/<session_id>.log`):

   ```
   mcp__monolynx__add_comment(
     project_slug="<PROJECT_SLUG>",
     ticket_id="<KEY>",
     content="**Sprint-run** - sesja `<session_id>` zakończyła się bez domknięcia ticketu. Ogon logu:\n\n```\n<ostatnie 60 linii>\n```"
   )
   ```

   Backend `codex` - pliku `$LOGS/<session_id>.log` tu nie ma i nie szukaj go: katalog `logs/` zapisuje wyłącznie ścieżka claude, a przebieg sesji Codeksa leży w logu JSONL w katalogu ticku. Identyfikatorem jest `runtime_id` z rejestru (pole `session_id` w raporcie z KROKU 2), a śladem dla człowieka dwie ścieżki: log sesji i osobno `.stderr.log` tej samej sesji, w którym ląduje render klienta i błędy poza turą:

   ```
   mcp__monolynx__add_comment(
     project_slug="<PROJECT_SLUG>",
     ticket_id="<KEY>",
     content="**Sprint-run** - sesja Codex `<runtime_id>` zakończyła się bez domknięcia ticketu (`<reason>`). Log: `<TICK_DIR>/codex-logs/<PROJECT_SLUG>-<KEY>-<runtime_id>.jsonl`, stderr: `<TICK_DIR>/codex-logs/<PROJECT_SLUG>-<KEY>-<runtime_id>.stderr.log`"
   )
   ```

   `<reason>` bierzesz z pola `reason` wiersza tego ticketu w raporcie z KROKU 2. Ogona logu nie wklejasz: JSONL Codeksa to zapis zdarzeń tury, nie tekst do czytania w komentarzu, a plik zostaje na dysku pod podaną ścieżką.

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

     Dla backendu `codex` ten sam komentarz bez nazwy sesji i bez nazwy worktree, tak samo jak przy pierwszym starcie w KROKU 4 - Codex nie nadaje nazwy sesji, a worktree tworzy i nazywa sam, więc obu wartości nie znasz i ich nie zgadujesz:

     ```
     mcp__monolynx__add_comment(
       project_slug="<PROJECT_SLUG>",
       ticket_id="<KEY>",
       content="**Sprint-run** - auto-wznowienie: uruchomiono sesję Codex `<runtime_id>`, log `<TICK_DIR>/codex-logs/<PROJECT_SLUG>-<KEY>-<runtime_id>.jsonl`"
     )
     ```

     `<runtime_id>` to wartość z linii `STARTED` nowego wywołania `start`, nie identyfikator sesji, która właśnie padła.

   - **Komentarz już jest** - to drugie podejście, więc nie wznawiaj. Ticket zostaje dla człowieka; wymień go w raporcie w sekcji "Czekają na człowieka".

   Jedno auto-wznowienie na ticket to celowy limit: druga porażka pod rząd prawie zawsze znaczy, że problem jest w tickecie albo w projekcie, a nie w sesji, i kolejne restarty tylko palą czas.

### Werdykt `waiting`

Nie rusz sesji. Nie wznawiaj jej, nie kasuj i nie odpowiadaj za człowieka na pytanie z logu. Wypisz w raporcie ticket, powód z pola `reason` i gotową komendę dla backendu `claude`:

```bash
claude attach "<session_id>"
```

Dla backendu `codex` takiej komendy nie ma - `codex exec` nie udostępnia sesji do podłączenia się w trakcie. Zamiast niej podaj człowiekowi ścieżkę logu: `<TICK_DIR>/codex-logs/<PROJECT_SLUG>-<KEY>-<runtime_id>.jsonl` (`TICK_DIR` z wyjścia preflightu), a przy diagnozie błędów klienta także plik `.stderr.log` obok niego.

---

## KROK 4: Dispatch wolnych slotów

**Sprawdź punkt startu przed dispatchem (MON-197, MON-142).** `BASE_BRANCH` i `BASE_BRANCH_STALE` pochodzą z preflightu (KROK 1) i opisują **główny checkout dyspozytora** (katalog roboczy procesu ticku) - nie worktree ticketu, który dopiero powstanie w tym kroku. `claude --bg --worktree` bierze **HEAD głównego checkoutu w chwili startu sesji**, więc każda sesja wystartowana teraz dziedziczy ten sam rozjazd, jeśli checkout jest przestarzały.

- **`BASE_BRANCH_STALE` puste albo `false`** - kontynuuj dispatch normalnie.
- **`BASE_BRANCH_STALE=true`** - najpierw spróbuj przewinąć główny checkout jedną samodzielną komendą (bez `&&`, potoków i zmiennych; `<katalog skilla>`, `<BASE_BRANCH>` i `<PROJECT_DIR>` z preflightu wpisz literalnie):

  ```bash
  python3 "<katalog skilla>/scripts/sprint_run_tick.py" sync-base --base-branch <BASE_BRANCH> --project-dir <PROJECT_DIR>
  ```

  Skrypt robi `git fetch origin <BASE_BRANCH>` i `git merge --ff-only origin/<BASE_BRANCH>` wyłącznie wtedy, gdy checkout stoi na `<BASE_BRANCH>` i śledzone pliki są czyste; nigdy reset, rebase ani checkout. Wypisuje jedną linię `SYNC-BASE: ok|skipped|failed reason=<powód>` (kod `0` dla `ok`/`skipped`, `2` dla `failed`). Dopisz tę linię do raportu (KROK 5).

  - **`SYNC-BASE: ok`** - checkout jest aktualny; kontynuuj dispatch normalnie.
  - **`skipped` albo `failed`** (inny branch, lokalne zmiany, lokalne commity, rozjechana historia, brak sieci) i `MODE=interactive` - dopisz do raportu ostrzeżenie: _"Główny checkout nie stoi na branchu bazowym `<BASE_BRANCH>` albo jest za `origin/<BASE_BRANCH>`, a `sync-base` go nie przewinął (`reason=<powód>`) - nowe sesje odziedziczą przestarzały punkt startu. Zaktualizuj checkout ręcznie przed kolejnym tickiem."_ i **kontynuuj dispatch** mimo to - człowiek dostaje ostrzeżenie, tick nie blokuje sam siebie bez konieczności.
  - **`skipped` albo `failed`** i `MODE=batch` - **nie startuj żadnej sesji** w tym ticku, pomiń resztę tego kroku i zakończ całą odpowiedź dokładnie taką linią (zamiast linii statusu z KROK 5):

    ```
    SPRINT-RUN STOP: glowny checkout dyspozytora nie jest aktualny wobec brancha bazowego <BASE_BRANCH>
    ```

    Analogicznie do blokady brakujących flag w KROK 1 (sentinel `remaining=-1`): pętla ma się dowiedzieć, że tick wymaga ręcznej interwencji na hoście (aktualizacja głównego checkoutu), a nie kręcić się dalej, startując sesje ze złego punktu startu.

**Wolne sloty bierzesz z `dispatch.can_start`** w JSON-ie skryptu stanu (KROK 2) - nie liczysz ich sam. Skrypt liczy `free_slots = max(0, sloty - running)`: sesja `waiting` nie zajmuje slotu. Żywe sesje `waiting` mają za to własny limit `MAX_WAITING`:

- **`dispatch.waiting_blocked` = `true`** - dispatch jest wstrzymany (`can_start` = `0`). Nie startujesz żadnej sesji w tym ticku. Sesje `waiting` i ich worktree zostają nietknięte: bez `claude attach`, bez `claude rm`, bez odpowiadania za człowieka. W raporcie KROK 5 zapisz powód: `dispatch wstrzymany: <waiting>/<MAX_WAITING> sesji czeka na człowieka`.
- **`dispatch.can_start <= 0`** (także bez blokady `waiting`, gdy wszystkie sloty zajmują sesje `running`) - pomiń resztę tego kroku i przejdź do raportu.
- W przeciwnym razie startujesz najwyżej `can_start` sesji.

WHY: przy `sloty - (running + waiting)` jedna sesja czekająca na odpowiedź człowieka zabierała slot na całą noc. Przy `PARALLEL=2` sesja MON-138 (pytanie o bloki `resource` Homebrew i job CI) trzymała połowę przepustowości sprintu od wieczora do rana. Limit `MAX_WAITING` zatrzymuje z kolei sprint, w którym sesje po kolei stają na pytaniach: zamiast mnożyć worktree czekające na człowieka, tick przestaje startować nowe.

**Kolejność kandydatów** (tylko werdykt `free`): priorytet malejąco `high` > `medium` > `low`, a w ramach tego samego priorytetu rosnąco po kluczu ticketu. Deterministyczna kolejność sprawia, że dwa ticki pod rząd wybierają to samo i nie ma wyścigu o ten sam ticket.

**Pełny ticket kandydata.** Skrypt stanu nie zna zależności między ticketami, a `list_tickets` i `get_board` ich nie zwracają. Dla **każdego** kandydata `free`, w kolejności wyżej, zanim zajmie slot, dociągnij pełny ticket - jedno wywołanie na kandydata, wspólne dla filtra blokerów, reguły `needs-local` i reguły migracyjnej niżej:

```
mcp__monolynx__get_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<KEY>")
```

**Tickety z niezamkniętym blokerem nie startują.** W JSON z CLI blokery to lista `blocked_by` (status w polu `status` każdego elementu; pusta lista albo same wpisy w `blocks` oznaczają brak blokerów), a reszta akapitu opisuje tekstową odpowiedź MCP. Odpowiedź `get_ticket` ma sekcję `## Blokery (relacja)` (literał narzędzia, bez polskich znaków) tylko wtedy, gdy ticket ma relacje; linia `Blokowany przez: <KEY-B> | <status> | <tytul>` wskazuje bloker, a status w niej jest liczony przy odczycie, więc zawsze świeży. Relacja to wyłącznie ta sekcja: stoi po `## Description` i `## Acceptance Criteria`, przed `## Comments`. Sekcja `## Zaleznosci` w tekście opisu (szablon `ticket-create`) i komentarz `**Zaleznosci:**` to proza autora, nie relacja - ignoruj je; blokery zapisuje `blocked_by_ids`. Stare zależności zapisane tylko prozą przenosi do relacji jednorazowa migracja danych `make backfill-dependencies` (dry-run; zapis: `make backfill-dependencies ARGS=--apply`) - dyspozytor jej nie uruchamia. Bloker jest zamknięty wyłącznie przy statusie `done`; każdy inny (`backlog`, `todo`, `in_progress`, `in_review`) to **niezamknięty bloker** - także `in_review`, bo review to nie merge. Kandydat z co najmniej jednym niezamkniętym blokerem jest pominięty: nie startujesz sesji, nie zajmuje slotu, bierzesz następnego z kolejki. Brak sekcji, same linie `Blokuje:` albo wszystkie blokery `done` - kandydat przechodzi dalej. Pominięty ticket nie wchodzi do `S` i zostaje w `remaining` (nadal jest pracą do zrobienia, skrypt stanu liczy tam każdy werdykt `free`); następny tick sprawdzi go ponownie, więc po zamknięciu blokera wystartuje bez niczyjej interwencji. Komentarza na tickecie nie dopisujesz - pominięcie powtarza się w każdym ticku i zaśmieciłoby ticket; ślad jest w raporcie (KROK 5), z powodem `czeka na merge <KEY-B>`.

WHY: sesja `work` wystartowana przy blokerze w `in_review` merguje do swojego brancha cudzy, niezmergowany branch, a jej MR niesie cudze commity (MON-150 i MON-152 zmergowały `worktree-MON-149` / `worktree-MON-150`).

Gdy w sprincie zostały **wyłącznie** tickety z niezamkniętym blokerem (reszta `done`, nic nie biegnie), linia statusu ma `remaining>0 running=0`, więc `sprint_run.sh` nie uzna sprintu za wykonany i dalej tyka co interwał, aż człowiek zmerguje bloker (następny tick zobaczy `done` i wystartuje ticket) albo pętla dojdzie do `--max-ticks` (kod wyjścia 3). Wymień wtedy w sekcji "Czekają na człowieka" każdy pominięty ticket z kluczami blokerów do zmergowania.

**Tickety z etykietą `needs-local` nie startują w tle.** Etykietę bierzesz z linii `... | Labels: <lista po przecinku>` w odpowiedzi tego samego `get_ticket` (np. `Story Points: 3 | ... | Labels: needs-local, skill`) - bez dodatkowego wywołania. Ticket bez etykiet ma w tym polu sam znak zastępczy (długi myślnik), który nie jest etykietą. Kandydat `free` z etykietą `needs-local` na liście jest pominięty: nie startujesz sesji, nie zajmuje slotu, nie wchodzi do `S` i zostaje w `remaining` (skrypt stanu liczy tam każdy werdykt `free`). Komentarza na tickecie nie dopisujesz - pominięcie powtarza się w każdym ticku. Ślad jest w raporcie KROK 5: wiersz `free -> pominięty` z powodem `needs-local` i wpis w sekcji "Czekają na człowieka" z komendą do uruchomienia interaktywnie, w sesji człowieka na jego maszynie:

```
/monolynx:work <KEY>
```

WHY: ticket z tą etykietą wymaga lokalnego środowiska albo decyzji człowieka, więc sesja w tle i tak stanęłaby na pytaniu i zajęła worktree. MON-138 potrzebował `brew update-python-resources` uruchomionego lokalnie i edycji `.gitlab-ci.yml`, której klasyfikator trybu automatycznego agentowi odmawia.

Sprint, w którym zostały wyłącznie tickety `needs-local`, tickety z niezamkniętym blokerem i sesje `waiting`, ma w linii statusu `remaining>0`, więc `sprint_run.sh` nie uzna go za wykonany: zaloguje `UWAGA: nic nie biegnie` i tyka dalej, aż człowiek zrobi swoją część (przerobi ticket `needs-local` interaktywnie, zmerguje bloker, odpowie sesji `waiting`) albo pętla dojdzie do `--max-ticks` (kod wyjścia 3).

**Tickety migracyjne idą solo.** Ticket z etykietą `migration` albo z opisem mówiącym o migracji bazy danych startuj wyłącznie wtedy, gdy `running = 0`, i nie startuj w tym samym ticku niczego więcej. Dwie równoległe sesje generujące migracje Alembica robią rozjechany łańcuch rewizji, którego nikt potem nie chce prostować. Etykiety i opis bierzesz z tego samego `get_ticket` co wyżej.

**Start sesji** dla każdego wybranego ticketu, z wartościami **literalnymi** z wyjścia preflightu (`SPRINT_MODEL`, `PERMISSION_MODE` - KROK 1), nie jako `${...}`:

```bash
MONOLYNX_SPRINT_RUN=1 claude --bg --worktree "<KEY>" --name "<PROJECT_SLUG>-<KEY>-<SPRINT_MODEL>" --model "<SPRINT_MODEL>" --permission-mode "<PERMISSION_MODE>" "/monolynx:work <KEY>"
```

Powyższa komenda dotyczy **backendu `claude`**. Dla **backendu `codex`** sesję startuje podkomenda `start` skryptu ticku - jedno samodzielne wywołanie `python3`, z wartościami literalnymi:

```bash
python3 "<katalog skilla>/scripts/sprint_run_tick.py" start --backend codex --slug "<PROJECT_SLUG>" --key "<KEY>" --model "<SPRINT_MODEL>" --prompt '$monolynx:work <KEY>'
```

Prompt w **apostrofach**, nie w cudzysłowie: w cudzysłowie powłoka rozwinęłaby `$monolynx` do pustego napisu i Codex dostałby `:work <KEY>` zamiast nazwy skilla. Prefiks `$monolynx:` jest poprawny - Codex zna skille pod taką nazwą (MON-182). `--cwd` jest opcjonalny (domyślnie katalog bieżący); podaj go literalnie tylko wtedy, gdy proces ticku nie stoi w katalogu projektu, biorąc wartość z `PROJECT_DIR` wyjścia preflightu.

Wyjście `start` to jedna linia na stdout albo komunikat na stderr; format linii jest **zamrożonym kontraktem**:

| Wyjście | Kod | Twoje działanie |
|---|---|---|
| `STARTED <identity> <runtime_id> <pid>` | `0` | sesja wystartowała. `<identity>` to `<PROJECT_SLUG>:<KEY>`, a `<runtime_id>` jest identyfikatorem sesji, którego używasz dalej zamiast `<session_id>`. Ticket wchodzi do `S`, dopisz komentarz |
| `ALREADY-RUNNING <identity> <runtime_id>` | `0` | żywa sesja tego ticketu już istnieje (rejestr albo `claude agents`); żaden proces nie powstał. Ticket **nie** wchodzi do `S`, komentarza nie dopisujesz - to normalny wynik idempotentnego ticku, nie błąd |
| komunikat na stderr | `2` | preflight Codeksa nieudany albo katalog ticku niedostępny. Żaden proces ani wpis rejestru nie powstał: wypisz stderr, nie ponawiaj startu w tym ticku, ticket nie wchodzi do `S` |
| komunikat na stderr | `64` | błąd użycia (brak wymaganego argumentu albo `--backend claude`) |

`start --backend claude` **celowo** kończy się kodem 64 i niczego nie robi. WHY: sesje Claude startują komendą `claude --bg` wprost z tego kroku, żeby przejść przez system uprawnień Claude Code; to samo wywołanie puszczone przez `subprocess` skryptu ticku ominęłoby te uprawnienia (MON-191/MON-192). Nie próbuj obejść tego kodu 64.

Sesja Codeksa dostaje **świeży worktree na ticket**, tak jak sesja Claude - z jedną różnicą, którą masz uwzględnić w raporcie: katalog tworzy i nazywa sam Codex (`codex exec --worktree` jest przełącznikiem bez wartości), a nie dyspozytor. Nie znasz jego ścieżki i nie zgaduj jej: w komentarzu ticketu dla backendu `codex` podaj identyfikator sesji i ścieżkę logu, a pole „worktree `<KEY>`” zostaw wyłącznie dla backendu `claude`.

Nie startuj sesji Codeksa gołym `codex exec`. Sesja założona poza rejestrem dyspozytora nie jest przez niego widziana, więc następny tick uznałby ticket za wolny i wystartował drugą.

**Start odrzucony przez uprawnienia**: gdy to wywołanie zostanie odrzucone przez klasyfikator trybu automatycznego (komunikat w rodzaju "Blocked by classifier" albo "Permission ... denied") - nie ponawiaj, nie zmieniaj `PERMISSION_MODE` na inny, nie szukaj obejścia. Gdy `MODE=interactive`: wypisz użytkownikowi gotową komendę startu dla każdego nieuruchomionego ticketu (z prefiksem `!`, do wklejenia wprost w tej sesji) i rekomendację uruchomienia pętli przez `sprint_run.sh` (patrz KROK 5). Gdy `MODE=batch`: odnotuj to samo w raporcie ticku, nikt tam i tak komendy nie wklei. W obu przypadkach taki ticket **nie** wchodzi do `S`.

Odczytaj identyfikator sesji. Dla backendu `codex` jest nim `<runtime_id>` z linii `STARTED` - masz go od razu, żadnej dodatkowej komendy nie wołasz. Dla backendu `claude` weź identyfikator z wyjścia komendy startu, a gdy go tam nie ma, dociągnij po nazwie:

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

Dla backendu `codex` ten sam komentarz bez nazwy sesji i bez nazwy worktree (Codex nie nadaje pierwszej, a drugą tworzy sam), za to ze ścieżką logu, bo to jedyne miejsce, w którym człowiek zobaczy przebieg tej sesji:

```
mcp__monolynx__add_comment(
  project_slug="<PROJECT_SLUG>",
  ticket_id="<KEY>",
  content="**Sprint-run** - uruchomiono sesję Codex `<runtime_id>`, log `<TICK_DIR>/codex-logs/<PROJECT_SLUG>-<KEY>-<runtime_id>.jsonl`"
)
```

Komentarz z identyfikatorem sesji jest jedynym trwałym śladem, po którym człowiek trafi do właściwej sesji następnego dnia. Nie pomijaj go, nawet gdy sesja wystartowała bez problemów.

**Zapamiętaj `S`**: liczbę sesji, które faktycznie wystartowały w tym kroku, a nie liczbę prób startu. Dla backendu `claude` liczysz wyłącznie te potwierdzone wpisem o nazwie `<PROJECT_SLUG>-<KEY>-<MODEL>` w `claude agents --json --all`; dla backendu `codex` wyłącznie te, które dały linię `STARTED` (`ALREADY-RUNNING`, kod 2 i kod 64 nie liczą się do `S`). `S` wchodzi do korekty linii statusu w KROK 5.

Poniższe akapity o trybie uprawnień i modelu dotyczą backendu `claude`. Sesja Codeksa nie ma trybu uprawnień Claude Code ani modelu w komendzie: jej granicą jest sandbox (`MONOLYNX_SPRINT_CODEX_SANDBOX`, domyślnie `workspace-write`), a `--model` z komendy `start` służy tożsamości i nazewnictwu, bo `codex exec` nie przyjmuje aliasów `opus`/`sonnet`. Flagi AUTO* i `MONOLYNX_SPRINT_RUN=1` działają tak samo dla obu. Sesja Claude dostaje `MONOLYNX_SPRINT_RUN=1` z prefiksu komendy startu, sesja Codeksa ze środowiska procesu ticku (`sprint_run.sh` eksportuje tę zmienną), a flagi AUTO* obie dziedziczą ze środowiska procesu ticku.

Sesja w tle startuje z trybem uprawnień `PERMISSION_MODE` z wyjścia preflightu (domyślnie `auto`, nadpisywalne przez `MONOLYNX_SPRINT_PERMISSION_MODE` w środowisku procesu ticku - preflight w KROK 1 blokuje tick, zanim wystartuje jakąkolwiek sesję, jeśli ta zmienna jest ustawiona na `bypassPermissions` albo `--dangerously-skip-permissions`). WHY: worktree tworzony przez `claude --bg --worktree` zawiera wyłącznie pliki śledzone przez gita, a `.claude/settings.local.json` jest ignorowany, więc ani `permissions`, ani flagi AUTO* z niego nie docierają do sesji roboczej i sesja bez człowieka zawisłaby na pierwszym pytaniu o zgodę. Granicą jest wtedy hook `bash_guard.py` (deny na git write, lint, testy i `glab mr create` dla subagentów) oraz kontrakt autonomii skilla `work`, a nie dialog uprawnień. Flagi `MONOLYNX_AUTOTEST`, `MONOLYNX_AUTOCOMMIT` i `MONOLYNX_AUTOPUSH` mogą leżeć w **śledzonym** `.claude/settings.json` (pole `env`) albo w środowisku procesu odpalającego tick: `sprint_run.sh` je eksportuje, a `claude --bg` dziedziczy środowisko. `MONOLYNX_SPRINT_RUN=1` idzie wyłącznie w środowisko startowanej sesji - prefiks komendy startu wyżej - bo w śledzonym pliku działałby także poza sprintem. `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` należą do środowiska procesu dyspozytora albo do `.claude/settings.local.json` na czas sprintu, nigdy do śledzonego `.claude/settings.json` na `main` (walidator `--release` je odrzuca). Dodatkowo (MON-197): `claude --bg --worktree` tworzy worktree ticketu z **HEAD głównego checkoutu dyspozytora w chwili startu sesji** - stąd sprawdzenie `BASE_BRANCH`/`BASE_BRANCH_STALE` (z próbą fast-forwardu przez `sync-base`) na początku KROK 4, zanim ten krok wystartuje jakąkolwiek sesję.

Model sesji roboczej to `SPRINT_MODEL` z wyjścia preflightu (domyślnie `opus`, nadpisywalne przez `MONOLYNX_SPRINT_MODEL`). Nie usuwaj `--model` i nie zastępuj go modelem tej sesji dyspozytora. WHY: sesja w tle bez `--model` bierze domyślny model konta, a ten bywa najdroższy (Fable); jeden sprint na nim kosztuje wielokrotność przebiegu na Opusie. Droższy model dla koordynatora ticketu włącza się świadomie, ustawiając `MONOLYNX_SPRINT_MODEL` w środowisku procesu odpalającego tick albo w śledzonym `.claude/settings.json`. Subagenci sesji `work` dostają model według sekcji „Model subagentów” skilla `work` (i `work-simple`), niezależnie od modelu koordynatora.

Po starcie sesji **nie czekaj** na jej wynik i nie zaglądaj do jej logu. Tick kończy się teraz, wynik zbierze następny.

---

## KROK 5: Raport i linia statusu

Wypisz tabelę stanu po tym ticku:

| Ticket | Sesja | Werdykt | Działanie |
|---|---|---|---|
| MON-167 | `abc123` | done | komentarz + `claude rm` |
| MON-168 | `def456` | running | bez zmian |
| MON-169 | - | free -> uruchomiona | start sesji `ghi789` |
| MON-170 | - | free -> pominięty | czeka na merge MON-166 (niezamknięty bloker, `in_review`) |
| MON-138 | - | free -> pominięty | `needs-local` - uruchom interaktywnie `/monolynx:work MON-138` |

Każdy kandydat pominięty w KROK 4 przez niezamknięty bloker ma w tabeli wiersz z werdyktem `free -> pominięty` i w kolumnie "Działanie" powód `czeka na merge <KEY-B>` ze statusem każdego niezamkniętego blokera, np. `czeka na merge MON-149 (in_review), MON-150 (todo)`. Kandydat pominięty przez etykietę `needs-local` ma wiersz `free -> pominięty` z powodem `needs-local`. Linia statusu niżej się nie zmienia.

Gdy KROK 4 wstrzymał dispatch przez limit sesji `waiting` (`dispatch.waiting_blocked` = `true`), pod tabelą napisz jednym zdaniem: `Dispatch wstrzymany: <waiting>/<MAX_WAITING> sesji czeka na człowieka - odpowiedz im, żeby tick znów startował sesje.`

Potem sekcja **"Czekają na człowieka"** ze wszystkimi ticketami o werdykcie `waiting` oraz tymi `needs_human`, które wyczerpały auto-wznowienie. Dla każdego: klucz ticketu, powód jednym zdaniem i komenda `claude attach <session_id>`. Dochodzą do niej:

- każdy ticket pominięty przez etykietę `needs-local` - z powodem `needs-local` i komendą `/monolynx:work <KEY>` do uruchomienia interaktywnie zamiast `claude attach` (sesji w tle nie ma);
- ticket pominięty przez niezamknięty bloker, ale tylko wtedy, gdy nic w sprincie już nie biegnie (patrz KROK 4) - z kluczami blokerów do zmergowania zamiast komendy `claude attach`;
- przy wstrzymanym dispatchu - liczby `waiting` i `MAX_WAITING` jako powód, dla którego wolne tickety nie wystartowały.

Gdy lista jest pusta, napisz jedno zdanie, że nic nie czeka.

Wiersz, którego `reason` mówi o nieznanym stanie sesji (`nieznany stan '<x>'`, czyli literał z CLI spoza listy znanej skryptowi stanu), wypisz w tabeli wprost, razem z tym powodem. Człowiek ma zobaczyć anomalię od razu, a nie domyślać się jej z zachowania pętli.

### Blok "Pętla z zewnątrz"

Ten blok wypisuj wtedy, gdy `MODE` z KROK 1 **nie jest** `batch`, czyli w trybie interaktywnym. W trybie `batch` go pomiń: pętla i tak go nie przeczyta, a zaśmieca log.

Podstaw ścieżkę **literalnie**: `<katalog skilla>/scripts/sprint_run.sh` (katalog skilla to ten ustalony na początku tego dokumentu) - bez `echo "${MONOLYNX_SKILL_DIR}/..."`, tick w `claude -p` nie ma reguły na `echo`. Wypisz tę ścieżkę, a potem trzy sposoby uruchomienia pętli. W komendach **podstaw tę samą literalną ścieżkę** w miejsce `<ścieżka>` (pełna ścieżka pliku, wraz z `sprint_run.sh`) - użytkownik ma dostać wiersz gotowy do wklejenia w terminalu. Nie zostawiaj w nich zmiennej `${MONOLYNX_SKILL_DIR}`: to lokalna zmienna tej sesji, terminal użytkownika jej nie zna.

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

Dopisz jedno zdanie o backendzie: `sprint_run.sh` bierze go z `MONOLYNX_SPRINT_RUNTIME` w środowisku procesu, który uruchamia pętlę (`claude` domyślnie, `codex` prowadzi tick przez `codex exec`; wartość spoza tych dwóch kończy skrypt kodem 64, brak binarki wybranego runtime kodem 69). Nie wpisuj tej zmiennej do komend wyżej - użytkownik ustawia ją tam, gdzie trzyma resztę flag `MONOLYNX_*`.

Powiedz wprost: **ten skill niczego nie kopiuje ani nie instaluje**. Ścieżka wskazuje skrypt dołączony do zainstalowanego pluginu albo skilla standalone. Po aktualizacji lokalizacja może się zmienić, więc rozwiń ją ponownie, zamiast trzymać starą w notatkach albo w crontabie na stałe.

### Ostatnia linia

**Ostatnią linią odpowiedzi** musi być dokładnie taka linia, bez wcięcia, bez backticków, bez bloku kodu i bez jakiegokolwiek tekstu po niej:

```
SPRINT-RUN: remaining=<n> running=<m> waiting=<k> done=<d>
```

Wartości bierzesz z `summary` skryptu stanu i korygujesz **wyłącznie** o efekt KROK 4: `remaining = summary.remaining - S`, `running = summary.running + S`. `waiting` i `done` przepisujesz bez zmian. Gdy KROK 4 nic nie wystartował, `S = 0`. Żadnych innych przeliczeń i żadnego zaokrąglania.

Przykład: `summary` ma `remaining=5 running=0 waiting=0 done=1`, KROK 4 wystartował dwie sesje (`S = 2`). Ostatnia linia to `SPRINT-RUN: remaining=3 running=2 waiting=0 done=1` (`5 - 2 = 3`, `0 + 2 = 2`). Linia `remaining=5 running=2` jest błędna: to korekta samego `running` bez odjęcia `S` od `remaining` (MON-167 raportował dokładnie tak). Ticket pominięty przez niezamknięty bloker albo przez etykietę `needs-local` nie wchodzi do `S`, więc zostaje w `remaining`. Tick wstrzymany limitem `MAX_WAITING` ma `S = 0`.

WHY: skrypt stanu policzył `summary` **przed** dispatchem. Bez tej korekty tick, który wystartował ostatnie wolne tickety, poda `remaining=0 running=0`, a pętla (`sprint_run.sh`, hub MON-169) uzna sprint za wykonany i zostawi świeże sesje bez nadzoru.

Format samej linii jest bez zmian: to kontrakt z `sprint_run.sh`, który po niej decyduje, czy pętla ma biec dalej, czy sprint jest wykonany. W całej odpowiedzi ta linia ma wystąpić **dokładnie raz**.

---

## Ważne zasady

1. **Nigdy nie pracujesz nad ticketem sam.** Żadnej edycji plików, żadnego commita, żadnego lintu. Widzisz coś do naprawienia w kodzie - to materiał na ticket, nie na Twoją turę. Jedyna zmiana w repozytorium, którą robi tick, to fast-forward głównego checkoutu przez `sprint_run_tick.py sync-base` w KROK 4.
2. **Tick jest idempotentny.** Przed startem sesji sprawdzasz, czy taka nazwa już nie żyje; przed auto-wznowieniem sprawdzasz komentarze. Dwa ticki pod rząd nie mogą zdublować sesji ani komentarza.
3. **Nie kasujesz sesji bez werdyktu `done`.** `claude rm` (backend `claude`) i `sprint_run_tick.py stop` (backend `codex`) tylko po tym, jak ticket jest w `in_review` albo `done`. Sesja `waiting` czeka na człowieka i musi go doczekać.
4. **Nie zmieniasz statusów ticketów.** Statusy przestawia `/monolynx:work` w swojej sesji. Ty tylko czytasz je i komentujesz.
5. **Nazwy sesji są zamrożone**: `<PROJECT_SLUG>-<KEY>-<MODEL>` dla pracy (rozpoznawana także bez sufiksu modelu), `<PROJECT_SLUG>-sprint-run-<MODEL>` dla ticka. Inne narzędzia po nich rozpoznają sesje sprintu.
6. **Język raportu: polski.** Komentarze w ticketach też.
7. **Backend nie zmienia kontraktu.** Wybór `claude`/`codex` zmienia komendy (KROK 1, 2 i 4), nie kontrakt: linia `SPRINT-RUN: ...`, `SPRINT-RUN STOP: ...`, werdykty i reguła jednej żywej sesji na ticket są wspólne. Jeden backend na tick, bez mieszania flag. Czego Codex nie umie (nazwa sesji, tryb w tle, natywny sygnał "czeka na zgodę", model w `codex exec`), tego nie zastępujesz obejściem - wypisujesz to w raporcie. Jedynym sygnałem oczekiwania na człowieka pod Codeksem jest marker `SPRINT-RUN STOP:` w treści logu sesji.
8. **Preflight i stan sesji idą tylko przez skrypt, zero `printenv`/`env`.** `scripts/sprint_run_tick.py` (`preflight`, `state`) jest jedynym sposobem sprawdzenia środowiska (KROK 1) i policzenia stanu sesji (KROK 2) - żadnych ręcznych `claude agents`/`claude logs`/`mktemp`/potoków obok niego w tych dwóch krokach. KROK 4 przy backendzie `claude` nadal woła `claude agents --json --all` wprost, żeby odczytać `id` świeżo wystartowanej sesji i potwierdzić `S` - to jedyne miejsce w tym skillu, gdzie ta komenda idzie bezpośrednio; przy backendzie `codex` identyfikator przychodzi w linii `STARTED` i tej komendy nie wołasz w ogóle. Nigdy `printenv`, `env`, `set`, `export -p`, `declare -x` ani `echo` zmiennej spoza `MONOLYNX_*`: incydent 18.09 wypisał tak sekrety (klucz OpenAI, token GitLaba) do transkryptu i logu pętli.
