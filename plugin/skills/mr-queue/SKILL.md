---
name: mr-queue
description: "Kolejka merge MR (GitLab, glab) i PR (GitHub, gh): jeden idempotentny tick bierze pierwszy niezmergowany MR/PR z kolejki i robi dokładnie jedno działanie w kolejności konflikt -> CI -> approve -> merge, a po merge do domyślnego brancha repo czeka na zielone CI brancha docelowego; po merge na branch integracyjny (np. sprint/*) domyka pozycję i ticket od razu na podstawie zielonego CI samego MR, anuluje pipeline brancha integracyjnego wywołany merge i bierze następny MR. Bez argumentów sam układa kolejkę z otwartych MR/PR. Użyj gdy chcesz przeprowadzić MR-y do main po kolei, ręcznie albo w pętli /loop."
user-invocable: true
argument-hint: "[!iid | #pr | URL ...]"
---

Przed wykonaniem kroków obowiązkowo odczytaj [Kontrakt runtime](references/runtime.md).

# MR-QUEUE - kolejka merge MR/PR

Zasoby tego skilla: ustal absolutny katalog otrzymanego pliku `SKILL.md`.
Odnośniki do dołączonych `scripts/`, `references/` i pomocniczych plików
Markdown rozwiązuj względem tego katalogu, nie katalogu roboczego projektu.
Ścieżki plików tworzonych w projekcie pozostają względem projektu. Przed komendami
z `MONOLYNX_SKILL_DIR` przypisz tej lokalnej zmiennej powłoki ustaloną
literalną ścieżkę katalogu skilla; nie oczekuj jej w środowisku systemowym.

Jesteś **strażnikiem kolejki merge**. Jedno uruchomienie tego skilla to jeden **tick**: czytasz stan pierwszego MR/PR z kolejki, robisz dokładnie jedno działanie, raportujesz i kończysz turę.

Zasady, które definiują ten skill:

1. **Monitorujesz, nie pracujesz na branchu.** Stan czytasz przez `glab` / `gh`. Na branch MR/PR wchodzisz tylko wtedy, gdy trzeba go poprawić (czerwone CI albo konflikt), i tylko w tymczasowym worktree, który usuwasz po pushu. Checkout użytkownika zostaje nietknięty, z jednym wyjątkiem: fast-forward lokalnego brancha docelowego wolno wykonać wyłącznie w Domknięciu: w wariancie (a) po zielonym CI brancha docelowego, w wariancie (b) od razu po merge na branch integracyjny i dla zastanych `verifying` (krok 4 **Domknięcia**, wspólnego dla 3a, 3.0, 4b i 4c); nigdy reset, rebase, stash ani force.
2. **Stan liczy skrypt, nie Ty.** Werdykt daje `mr_queue_state.py` z JSON-a `glab` / `gh`. Nie wnioskujesz stanu z opisu MR, komentarzy ani z tekstowego wyjścia CLI.
3. **Kolejność jest święta.** Pierwszy MR/PR blokuje resztę. Na domyślnym branchu repozytorium (np. `main`) następny wchodzi dopiero, gdy poprzedni jest zmergowany i CI brancha docelowego jest zielone. Na **branchu integracyjnym** (branch docelowy MR/PR różny od domyślnego brancha repozytorium, np. `sprint/*`) następny wchodzi zaraz po merge poprzedniego: zielone CI samego MR przed merge już dowodzi, że zmiana przechodzi, a całość weryfikuje MR brancha integracyjnego do domyślnego. Pozycja i ticket są domykane od razu (4b), a pipeline brancha integracyjnego wywołany merge tick anuluje, bo blokowałby `resource_group` pipeline'u następnego MR.
4. **Tick jest idempotentny.** Stan kolejki leży w pliku, a stan MR/PR w GitLabie / GitHubie. Dwa ticki pod rząd nie zdublują pusha, merge ani pytania.
5. **Najpierw konflikt, potem CI.** Kolejność sprawdzeń to konflikt -> CI -> approve -> merge. CI na branchu, który ma konflikt z branchem docelowym, nie da mergowalnego wyniku, a runda CI trwa i blokuje runner innym MR-om. Dlatego konflikt wygrywa z każdym stanem CI (także z pipeline'em w toku): tick anuluje trwający pipeline MR/PR i od razu przechodzi do rozwiązania konfliktu (KROK 6). Wyjątek: GitHub `BEHIND` bez `CONFLICTING` (branch tylko w tyle, bez konfliktu) jest sprawdzany po CI.

Pętla jest **na zewnątrz** skilla: `/loop 10m /monolynx:mr-queue`.

---

Każdą komendę helpera uruchom przez dostępny mechanizm procesów. Zachowaj identyfikator działającej sesji i odpytuj ją do potwierdzonego exit; nie czytaj częściowego JSON ani nie powtarzaj mutacji po samym yield. Uprawnienia użytkownika i runtime mają pierwszeństwo przed AUTO* i hookiem.

## KROK 0: Stałe i tryb

```bash
COMMON="$(git rev-parse --git-common-dir)"
STATE_DIR="$COMMON/monolynx/mr-queue"
QUEUE="$COMMON/monolynx/mr-queue.json"
SCRIPT="${MONOLYNX_SKILL_DIR}/scripts/mr_queue_state.py"
mkdir -p "$STATE_DIR"
echo "STATE_DIR=$STATE_DIR QUEUE=$QUEUE SCRIPT=$SCRIPT"
echo "MODE=${MONOLYNX_SPRINT_RUN_MODE:-interactive} AUTOPUSH=${MONOLYNX_AUTOPUSH:-false} AUTOMERGE=${MONOLYNX_AUTOMERGE:-false} MERGE_METHOD=${MONOLYNX_MERGE_METHOD:-merge}"
```

`--git-common-dir` sprawia, że kolejka jest wspólna dla głównego checkoutu i wszystkich worktree tego repozytorium. Każdą następną komendę w tym skillu pisz z tymi ścieżkami rozwiniętymi literalnie (wartości z wyjścia powyżej).

**Tryb `batch`** (`MONOLYNX_SPRINT_RUN_MODE=batch`): nikt nie odpowie na pytanie. Tam, gdzie tick zatrzymuje się na człowieku (pytanie o approve, zgoda na push do cudzego brancha albo raport **Potrzebne** z sekcji niżej), tryb batch kończy turę linią `MR-QUEUE STOP: <ref> <powód>` (przed linią statusu z KROK 7).

**Zatrzymanie z raportem "Potrzebne"**: gdy tick staje na czerwonym CI brancha docelowego, werdykcie `needs_human`, limicie rund poprawek albo nierozwiązywalnym konflikcie, nie pytasz "co dalej?". Kończysz tick raportem, w którym linia `Potrzebne: <konkretne działanie człowieka>` mówi, co dokładnie trzeba zrobić i jaką komendą wraca się do kolejki (np. `queue-update --ref <ref> --status pending` po ręcznej poprawce). Następny tick sam policzy stan od nowa.

**Merge i approve pilnuje hook `bash_guard.py`**: `glab mr merge` / `gh pr merge` pyta o zgodę, dopóki `MONOLYNX_AUTOMERGE` nie jest `true` (w trybie w tle `MONOLYNX_SPRINT_RUN=1` pytanie zamienia się w odmowę). `glab mr approve` / `gh pr review --approve` pyta zawsze. Ty nigdy nie zatwierdzasz MR/PR - approve daje człowiek.

**Hook nie jest gwarancją.** Działa dopiero po załadowaniu pluginu i nadaniu mu zaufania w kliencie, a samo pytanie (decyzja `ask`) egzekwuje tylko klient, który tę decyzję obsługuje - klient, który jej nie wspiera, wykonuje komendę mimo hooka. Stan ochrony rozpoznajesz po markerze `~/.cache/monolynx/hook-active/<session_id>`, który hook zapisuje przy każdym wywołaniu PreToolUse (pole `subagent_identification` mówi, czy klient w ogóle identyfikuje subagenta). Po pierwszej komendzie Bash tego skilla odczytaj marker **dwiema osobnymi komendami**, tak samo jak KROK 0 najpierw wypisuje ścieżki, a dopiero potem wstawiasz je literalnie. Najpierw katalog domowy:

```bash
echo $HOME
```

Potem `find` po ścieżce wpisanej literalnie - w miejsce `<HOME>` wstaw to, co zwróciła poprzednia komenda (np. `/Users/jan`):

```bash
find <HOME>/.cache/monolynx/hook-active -type f -mmin -5 -exec cat {} + 2>/dev/null
```

Żadnej zmiennej ani `$(...)` w tej drugiej komendzie: tick bywa uruchamiany z worktree, a guard izolacji worktree odrzuca komendę, której argument jest wyliczany w runtime - jednolinijkowiec z `"$HOME/..."` w ogóle się nie wykona.

Brak wpisów po pierwszej komendzie Bash: nie zakładaj, że hook zatrzyma merge ani approve. Traktuj zgodę jako **swój** obowiązek - pytasz użytkownika wprost i czekasz na odpowiedź (w trybie `batch`: `MR-QUEUE STOP: <ref> brak potwierdzonej ochrony hooka`), a w podsumowaniu ticku napisz jedną linią, że zgody były egzekwowane rozmową, nie hookiem. Użytkownik może włączyć `MONOLYNX_HOOK_ASK_UNSUPPORTED=true` (opt-in), co zamienia `ask` na odmowę z wyjaśnieniem - wskaż tę opcję, ale nie ustawiaj jej sam.

## KROK 1: Preflight

```bash
git rev-parse --show-toplevel
git remote get-url origin
glab auth status 2>&1 | head -3
gh auth status 2>&1 | head -3
```

- **Dostawca domyślny** (dla kolejki układanej automatycznie) wynika z hosta `origin`: `github.com` -> `gh`, każdy inny host -> `glab`.
- Dostawca potrzebny dla kolejki nie jest zalogowany -> wypisz komendę logowania (`glab auth login` / `gh auth login`), nic nie zmieniaj i zakończ tick (w batch linią `MR-QUEUE STOP: - brak logowania <glab|gh>`).
- Konfiguracja brancha docelowego: `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` muszą wskazywać ten sam branch, wystarczy ustawić jedną z nich. Podstaw wartości obu zmiennych (puste, gdy nieustawione):

  ```bash
  python3 "$SCRIPT" config-check --base-branch "$MONOLYNX_BASE_BRANCH" --mr-target "$MONOLYNX_MR_TARGET"
  ```

  Kod 2 (`MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` ustawione i różne: ERROR) -> wypisz komunikat ze stderr, nic nie zmieniaj i zakończ tick (w batch linią `MR-QUEUE STOP: - rozbieżne MONOLYNX_BASE_BRANCH i MONOLYNX_MR_TARGET`). Kod 0 -> dalej.

## KROK 2: Kolejka

**Podano argumenty** (argument wejściowy niepusty): to jest kolejność merge. Zapisz ją:

```bash
python3 "$SCRIPT" queue-set --queue-file "$QUEUE" <refy z wiadomości użytkownika w podanej kolejności>
```

Ref to `!<iid>` (GitLab), `#<numer>` (GitHub) albo pełny URL MR/PR. Kod `2` (nieznany ref, duplikat) -> wypisz stderr i zakończ tick.

**Brak argumentów**: pokaż zapisaną kolejkę i pobierz listę otwartych MR/PR dostawcy domyślnego.

```bash
python3 "$SCRIPT" queue-show --queue-file "$QUEUE"
glab mr list -F json -P 100 > "$STATE_DIR/list.json"
```

albo dla GitHuba:

```bash
python3 "$SCRIPT" queue-show --queue-file "$QUEUE"
gh pr list --state open -L 100 --json number,createdAt,baseRefName,headRefName,isDraft,url > "$STATE_DIR/list.json"
```

**Pole `halted` niepuste** -> kolejka stoi na pozycji `stopped` ze `stop_reason=target_ci_failed` (`next` jest wtedy `null`, ale to nie znaczy "wszystko obsłużone"). Nie układaj nowej kolejki ani nie dopisuj pozycji; przejdź od razu do KROK 3 (3.0 zaraportuje zatrzymanie).

**Pole `next` puste i `halted` puste** (brak pliku albo wszystko obsłużone; zastane pozycje `verifying` nie są w `next`, a `queue-set` ich nie gubi) -> ułóż nową kolejkę:

```bash
python3 "$SCRIPT" queue-propose --provider <glab|gh> --list-file "$STATE_DIR/list.json"
```

`queue-propose` zwraca `order` (MR oparty o branch innego otwartego MR idzie zaraz po nim, reszta od najstarszego) i `skipped` (drafty i MR-y oparte o draft).

- `order` puste -> nie ma nowych MR/PR do mergowania. Gdy w kolejce są zastane pozycje `verifying` (pole `verifying` z `queue-show`), idź do KROK 3 (3.0): trzeba je jeszcze domknąć i przełączyć ich tickety na `done`. Dopiero bez pozycji `verifying` tick kończy się raportem i linią statusu z `remaining=0`.
- `order` niepuste -> przyjmij je bez pytania, w obu trybach, i zapisz przez `queue-set` jak wyżej (refy z `order` w tej kolejności). W raporcie ticka wypisz przyjętą kolejkę (ref, tytuł z `list.json`, branch -> branch docelowy) i pominięte z powodem. Inną kolejność użytkownik ustawia argumentami skilla w dowolnym momencie.

**Pole `next` niepuste** -> dopisz na koniec kolejki otwarte MR/PR, których w niej jeszcze nie ma:

```bash
python3 "$SCRIPT" queue-append --queue-file "$QUEUE" --provider <glab|gh> --list-file "$STATE_DIR/list.json"
```

`queue-append` zostawia istniejące pozycje nietknięte (w dowolnym statusie, także `stopped` i `done`, więc zatrzymany MR nie wraca sam), a nowe dopisuje w kolejności `queue-propose`. Zwraca `appended` i `skipped` (pominięte spoza kolejki, z powodem). `appended` niepuste -> wypisz dopisane refy w raporcie ticka.

Kod `2` z `queue-propose` / `queue-append` -> wypisz stderr i zakończ tick bez działania.

Ułożenie i uzupełnienie kolejki to odczyt i zapis lokalnego pliku, nie działanie ticku: w tym samym ticku idź do KROK 3.

## KROK 3: Element `next`

Najpierw, raz na tick, domknięcie zastanych pozycji `verifying` (3.0). Potem weź element kolejki o refie `next` z ostatniego wyniku skryptu (`queue-show` albo `queue-update`; pola `provider`, `number`, `repo`, `status`, `fix_rounds`, `user_approved_sha`, `merge_sha`). Gdy `repo` jest niepuste, dopisuj `-R <repo>` do każdej komendy `glab` / `gh` tego elementu - dotyczy to także zastanych pozycji `verifying` w 3.0.

### 3.0. Zastane pozycje `verifying`

Status `verifying` pochodzi z toru 1.22.0 (merge na branch integracyjny, CI commita merge sprawdzane wstecz); nowych takich pozycji ten skill nie tworzy (4b), ale plik kolejki może je jeszcze zawierać. Domykasz je od razu, **bez `target-ci`, bez czekania na CI brancha integracyjnego i bez werdyktu `needs_human`**: zielone CI MR zostało ocenione przed merge, a całość weryfikuje MR brancha integracyjnego do domyślnego.

```bash
python3 "$SCRIPT" queue-show --queue-file "$QUEUE"
```

Wynik niesie `next`, `halted` i `verifying` (lista refów w kolejności kolejki). Dla każdego refu z `verifying`, po kolei:

1. Odczytaj `$STATE_DIR/mr.json` albo `$STATE_DIR/pr.json` (jak w 3b: `glab mr view <n> -F json` / `gh pr view <n> --json baseRefName,headRefName,headRefOid,title`) - dostarcza `target_branch`, `source_branch`, `title` i dowód CI MR do komentarza.
2. Wykonaj **Domknięcie** (niżej) w wariancie (b), z `merge_sha` z pozycji. Gdy pipeline MR nie da się ustalić, komentarz mówi wprost: "pozycja zastana po torze 1.22.0 domknięta bez odczytu CI brancha".

To księgowość, nie działanie na MR/PR (zasada 1 z "Ważnych zasad"): nie zużywa "jednego działania" ticka. Błąd odczytu `mr.json` / `pr.json` dla jednej pozycji nie blokuje reszty: pozycja zostaje `verifying`, w raporcie jest powód, następny tick spróbuje ponownie.

**`halted` niepuste** (pozycja `stopped` ze `stop_reason=target_ci_failed`, zastana po starym torze; `next` jest wtedy `null`) -> po domknięciu pozycji `verifying` nie bierz żadnego elementu. Zakończ tick raportem: ref zatrzymanej pozycji i `Potrzebne: poprawka brancha <target_branch> (nowy ticket albo osobny MR, nie Twój tick); po zielonym CI brancha odblokuj kolejkę queue-update --ref <halted> --status closed`. Ticket tej pozycji zostaje nietknięty. Batch: `MR-QUEUE STOP: <halted> czerwone CI brancha docelowego`.

**`halted` puste** -> weź `next` z ostatniego wyniku skryptu i przejdź do 3a albo 3b według jego statusu. Pusty `next` -> raport i linia statusu (KROK 7).

### 3a. Status `merging` - czekanie na CI brancha docelowego

MR jest już zmergowany na domyślny branch repozytorium (`merge_sha` ustawione), a kolejka czeka na zielone CI commita merge. Ten tor jest bez zmian względem wcześniejszych wersji. Wykonaj **Odczyt CI commita merge** (niżej) i działaj według `status` z `target-ci`:

| `status` | Działanie |
|---|---|
| `success` | **Domknięcie** (niżej), wariant (a): ticket -> `done`, fast-forward brancha docelowego, `queue-update --status done` |
| `wait` | nic nie rób, zakończ tick |
| `failed` | `queue-update --ref <ref> --status stopped`; zakończ tick raportem z nazwami czerwonych jobów i identyfikatorem pipeline'u/runu. `Potrzebne: poprawka brancha <target_branch> (nowy ticket albo osobny MR, nie Twój tick); po zielonym CI domknij pozycję queue-update --ref <ref> --status done`. Batch: `MR-QUEUE STOP: <ref> czerwone CI brancha docelowego` |
| `needs_human` | bez zmiany statusu kolejki (pozycja zostaje w `merging`, następny tick sprawdzi CI ponownie); zakończ tick raportem z `reason`. `Potrzebne:` przy braku pipeline'u/runów - uruchomienie CI dla commita `<merge_sha>` na `<target_branch>`; przy pipeline `canceled` / `skipped` - ponowienie go. Batch: `MR-QUEUE STOP: <ref> <reason>` |

Komenda `queue-update` w pełnej postaci: `python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --status <status>`.

#### Odczyt CI commita merge (tylko 3a)

Odczytaj `target_branch` razem z branchem źródłowym i tytułem - ten sam JSON zasili później klucz ticketu w **Domknięciu**, więc czytaj go zawsze, niezależnie od tego, czy `target_branch` znasz skądinąd:

```bash
glab mr view <n> -F json > "$STATE_DIR/mr.json"   # zwraca m.in. target_branch, source_branch, title (pole gotowe w -F json)
```

albo dla GitHuba:

```bash
gh pr view <n> --json baseRefName,headRefName,title > "$STATE_DIR/pr.json"
```

Sprawdź pipeline commita merge na branchu docelowym. Dla GitLaba:

```bash
glab api "projects/:fullpath/pipelines?sha=<merge_sha>&ref=<target_branch>" > "$STATE_DIR/pipelines.json"
glab api "projects/:fullpath/pipelines/<id najnowszego pipeline'u>/jobs?per_page=100" > "$STATE_DIR/jobs.json"
python3 "$SCRIPT" target-ci --provider glab --pipelines-file "$STATE_DIR/pipelines.json" --jobs-file "$STATE_DIR/jobs.json"
```

Druga komenda tylko wtedy, gdy lista pipeline'ów jest niepusta. Dla GitHuba:

```bash
gh run list --commit <merge_sha> --json databaseId,status,conclusion,name,headBranch,headSha,event,workflowName > "$STATE_DIR/runs.json"
python3 "$SCRIPT" target-ci --provider gh --runs-file "$STATE_DIR/runs.json"
```

Wynik `failed` niesie `failed_jobs` (nazwy czerwonych jobów GitLaba albo runów GitHuba) - te nazwy idą do raportu ticka.

#### Domknięcie: ticket -> `done` i `queue-update` (wspólne dla 3a, 3.0, 4b i 4c)

Domknięcie przełącza pozycję kolejki na `done` **i** (gdy da się rozpoznać powiązany ticket) powiązany ticket na `done`. Dowód zielonego CI zależy od wariantu:

- **(a) Domyślny branch repozytorium** (z 3a, `success` z `target-ci`): ticket dostaje `done` dopiero po zielonym CI commita merge na branchu docelowym.
- **(b) Branch integracyjny** (z 4b i 4c przy `close_now=true` z `post-merge`, oraz zastane `verifying` z 3.0): dowodem jest zielone CI samego MR/PR przed merge, bez czekania na CI brancha integracyjnego (jego pipeline anuluje 4b/4c).

To wciąż **jedno działanie ticka** (w 3.0 i 4c: księgowość) - zmiana statusu ticketu i fast-forward lokalnego brancha docelowego są częścią tego samego domknięcia pozycji kolejki, nie osobnymi krokami.

**Transport: CLI albo MCP (raz, przy pierwszym dojściu do tej gałęzi w danej sesji)**:

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

Tryb zwiezly: `--compact true` przy `list_tickets`, gdy lista sluzy do przegladu statusow i blokerow bez czytania opisow (elementy bez pola `description`), oraz `--brief true` przy `create_ticket` i `update_ticket`, gdy po zapisie nie czytasz opisu (wynik to tylko pola `id`, `key`, `title`, `status`, `sprint_id`, `blocked_by`, `blocks`). `compact` i `brief` to parametry wrappera, a nie tooli MCP: przy `mode=mcp` i przy fallbacku (kod 3) wywolujesz tool MCP bez nich. Wrapper sam niczego nie skraca - bez tych flag wynik jest pelny.

Kody wyjscia wrappera:

- `0` - stdout to JSON API v2 z CLI. Niesie te same dane co tekstowa odpowiedz toola MCP, pod nazwami pol JSON.
- `3` z ostatnia linia `OP-FALLBACK: mcp tool=<nazwa>` - wywolaj ten tool MCP z tymi samymi argumentami, z pominieciem parametrow wrappera `compact` i `brief` (tool MCP ich nie ma); jego aktualny schemat poznaj mechanizmem runtime przed wywolaniem. To zwykla sciezka operacji bez komendy CLI (Pipelines, `log_time`, zapis grafu, LLM Wiki). Brak tego toola w runtime: stop z nazwa brakujacej operacji.
- `2` - blad operacji (API, siec, przekroczony czas). Przy zapisie nie ponawiaj na slepo przez MCP: najpierw odczytem sprawdz, czy zapis doszedl.
- `64` - blad uzycia wrappera, CLI nie bylo wolane. Popraw argumenty.

Subagent pracuje przez MCP, chyba ze dostal w prompcie wynik `mode=cli` i literalna sciezke wrappera. `MONOLYNX_TRANSPORT=mcp` w srodowisku wymusza MCP w preflighcie i we wrapperze (zachowanie sprzed wprowadzenia CLI).
<!-- /shared:transport -->

**Załaduj narzędzia MCP (raz, gdy preflight dał `mode=mcp` albo wrapper zwrócił kod 3)**:

<!-- runtime:claude-code -->
```
ToolSearch(query="+monolynx ticket get update comment", max_results=10)
```
<!-- /runtime:claude-code -->

Potrzebne toole: `get_ticket`, `update_ticket`, `add_comment`.

**Prefix tooli - ustal RAZ, nie zgaduj**: nazwy w tym skillu zapisane sa jako
`mcp__monolynx__<tool>` - to placeholder. Rzeczywisty prefix zalezy od instalacji
(`mcp__monolynx__*` plugin lokalny, `mcp__claude_ai_Monolynx__*` connector globalny
claude.ai, w innych runtime'ach inny). Odczytaj prefix z nazw z aktualnego katalogu narzędzi i
uzywaj go konsekwentnie. Zapytanie z `+monolynx` zwrocilo pusto mimo podpietego connectora
-> powtorz bez `+`. Runtime bez ToolSearch: sprawdź faktyczny katalog narzędzi i użyj jego discovery oraz aktualnych schematów.

`project_slug` zawsze jako `<PROJECT_SLUG>` (patrz `.claude/rules/skill-md-project-slug.md`) -
w faktycznym wywolaniu podstaw realny slug projektu, nie zostawiaj placeholdera.

1. **Klucz ticketu - licz go skryptem, nie zgaduj z prozy**:

   ```bash
   python3 "$SCRIPT" ticket-key --source-branch "<source_branch>" --title "<tytuł MR/PR>"
   ```

   `source_branch` i `title` bierzesz z `$STATE_DIR/mr.json` (GitLab: pola `source_branch`, `title`) albo `$STATE_DIR/pr.json` (GitHub: pola `headRefName`, `title`) zapisanego wyżej przy odczycie `target_branch` - nie dociągaj nowego zapytania tylko po te dwa pola. Wynik zawsze `{"key": "MON-xxx"}` albo `{"key": null}`, exit 0 w obu przypadkach - brak klucza to poprawny wynik, nie błąd.

2. **`key` jest `null`** - klucza nie rozpoznano. Ticket zostaje **nietknięty**. Wykonaj tylko kroki 4 (fast-forward) i 5 (`queue-update --status done`) i w raporcie ticka jawnie zaznacz, z czego próbowano wyznaczyć klucz i że się nie udało, np.: "ticket nie zaktualizowany - nie rozpoznano klucza z brancha `<source_branch>` ani tytułu `<title>`".

3. **`key` rozpoznany** - najpierw przeczytaj stan ticketu, dopiero potem ewentualnie go zmień (read-before-write, żeby nie dublować komentarza w kolejnym idempotentnym ticku):

   ```
   mcp__monolynx__get_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<key>")
   ```

   Opis niżej podaje tekstową odpowiedź MCP. Przy `mode=cli` wynik to JSON: klucz ticketu jest w polu `key`, status w polu `status`.

   - **Guard prefiksu projektu.** Pierwsza linia wyniku ma kształt `<KOD>-<numer> | tytuł`. Porównaj `<KOD>-<numer>` z `<key>` wyliczonym przez `ticket-key` - muszą być identyczne. Rozjazd (np. klucz `JIRA-45` z brancha innego repo trafia tu jako `<key>`, a projekt Monolynx ma kod `MON`) traktuj **dokładnie jak `key: null`**: ticket zostaje nietknięty, kroki 4 i 5 wykonują się mimo to, w raporcie ticka jawnie zaznacz "klucz `<key>` nie należy do tego projektu (`get_ticket` zwrócił `<KOD>-<numer>`)". Sprawdzenie istnieje, bo `_resolve_ticket_uuid` (`src/monolynx/mcp_server.py`) dopasowuje ticket wyłącznie po numerze i ignoruje prefiks kodu projektu - bez tego guardu klucz z cudzego projektu cicho przestawiłby na `done` przypadkowy, tak samo ponumerowany ticket w tym projekcie.
   - Prefiks się zgadza i druga linia wyniku zaczyna się `Status: done` -> ticket już zamknięty (ręcznie albo w poprzednim ticku). **Nie zmieniaj statusu, nie dodawaj drugiego komentarza.** Odnotuj w raporcie: "ticket `<key>` już `done`, bez zmian" (idempotencja ticka).
   - Prefiks się zgadza i status inny niż `done` -> zmień na `done` i dopisz komentarz z merge SHA oraz identyfikatorem pipeline'u/runu, który stanowi dowód zielonego CI:

     - wariant (a): pipeline/run brancha docelowego, który `target-ci` już ocenił jako `success` - GitLab: pole `id` najnowszego wpisu w `pipelines.json`; GitHub: `databaseId` runu (albo runów, gdy `runs.json` ma ich kilka) z `runs.json`:

       ```
       mcp__monolynx__update_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<key>", status="done")
       mcp__monolynx__add_comment(project_slug="<PROJECT_SLUG>", ticket_id="<key>", content="Merge SHA: <merge_sha> - CI brancha docelowego zielone (pipeline <id>).")
       ```

     - wariant (b): pipeline/run **MR/PR**, nie brancha integracyjnego. GitLab: `head_pipeline.id` i `head_pipeline.status` z `$STATE_DIR/mr.json`. GitHub: runy głowy PR (`<headRefOid>` z `$STATE_DIR/pr.json`):

       ```bash
       gh run list --commit <headRefOid> --json databaseId,status,conclusion,workflowName
       ```

       ```
       mcp__monolynx__update_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<key>", status="done")
       mcp__monolynx__add_comment(project_slug="<PROJECT_SLUG>", ticket_id="<key>", content="Merge SHA: <merge_sha> - zmergowane na branch integracyjny <target_branch>, CI MR zielone (pipeline <id>); CI brancha integracyjnego nie jest czekane, całość weryfikuje MR brancha do domyślnego.")
       ```

       Dowodu nie da się ustalić (brak `head_pipeline`, pusta lista runów, błąd odczytu) albo CI MR nie jest zielone (merge wykonał człowiek poza kolejką, 4c): komentarz i raport ticka mówią to wprost - "zmergowane poza kolejką, CI MR: `<status>` albo nieustalone" (zastane `verifying` z 3.0: "pozycja zastana po torze 1.22.0 domknięta bez odczytu CI brancha") - ale pozycja i ticket i tak są domykane (merge już się stał; kolejka nie czeka na CI brancha integracyjnego).

   Błąd operacji (kod 2 wrappera albo błąd wywołania MCP: sieć, autoryzacja, projekt bez tego ticketu) **nie blokuje domknięcia pozycji kolejki**: złap go, wykonaj mimo to kroki 4 i 5, i zaznacz w raporcie ticka "aktualizacja ticketu `<key>` nie powiodła się: `<błąd>`" - obserwacyjna warstwa (ticket) nigdy nie trzyma zakładnika kolejki merge.

4. **Fast-forward lokalnego brancha docelowego** - niezależnie od wyniku punktów 2-3 (także przy `key: null` i po błędzie MCP). To jedyny ruch w checkoucie użytkownika i tylko fast-forward; skrypt `target-sync` decyduje, co wolno. Każdą komendę git uruchom **osobno**, w katalogu głównego checkoutu, `<target_branch>` z `mr.json` / `pr.json`:

   ```bash
   git fetch origin <target_branch>
   git rev-parse --abbrev-ref HEAD
   git rev-list --left-right --count <target_branch>...origin/<target_branch>
   git status --porcelain --untracked-files=no
   ```

   Z wyników zbuduj wywołanie: `--current-branch` = wynik `rev-parse` (`HEAD` przy detached), `--ahead` i `--behind` = lewa i prawa kolumna `rev-list` (lewa: lokalne commity spoza origin, prawa: commity origin, których lokalnie brak), `--dirty` = `true` gdy `status` wypisał cokolwiek, inaczej `false`:

   ```bash
   python3 "$SCRIPT" target-sync --target-branch <target_branch> --current-branch <branch> --ahead <n> --behind <m> --dirty <true|false>
   ```

   - `action` = `none` -> nic do zrobienia.
   - `action` = `ff-merge` albo `ff-fetch` -> wykonaj komendy z `commands` (`git merge --ff-only origin/<target_branch>` albo `git fetch origin <target_branch>:<target_branch>`, dokładnie w tej postaci, bez `+`). Niezerowy kod wyjścia komendy = ostrzeżenie, nie błąd ticka.
   - `action` = `skip` -> nic nie wykonuj, ostrzeżenie z `reason` i komendą z `manual`.
   - Błąd `git fetch`, `rev-list`, `rev-parse`, `status` albo kod != 0 skryptu `target-sync` -> ostrzeżenie z komendą, która padła (albo `manual`, gdy skrypt go zwrócił). Nic nie wykonuj "na oko".
   - Ostrzeżenie trafia do raportu ticka (KROK 7) i **nigdy nie blokuje** kroku 5. Nigdy reset, rebase, stash ani force - także jako "naprawa" po nieudanym fast-forwardzie.

5. **Zawsze na końcu, niezależnie od wyniku punktów 2-4**, w tej kolejności - ticket najpierw:

   ```bash
   python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --status done
   ```

   W wariancie (b) z 4b / 4c dopisz `--merge-sha <sha commita merge>`, bo pozycja nie przeszła wcześniej przez `queue-update --merge-sha`.

   Ticket zmieniasz **przed** `queue-update`, nie po: `queue-update --status done` zdejmuje pozycję z `next`, więc przerwanie ticka między nimi (crash, koniec kontekstu) po samym `queue-update` zostawiłoby pozycję kolejki trwale domkniętą, a ticket na zawsze w `in_review` - żaden kolejny tick już by na niego nie spojrzał.

   Domknięcie z 3.0 -> następna pozycja `verifying` (lista w 3.0). Domknięcie z 3a -> wróć do KROK 3 z nowym `next` z wyniku `queue-update` (odczyt jest tani, działanie zostaje jedno), bez ponownego 3.0: idzie raz na tick. Domknięcie z 4b -> koniec ticka (merge był działaniem). Domknięcie z 4c -> wróć do KROK 3 z nowym `next`.

### 3b. Status `pending` - werdykt

```bash
glab mr view <n> -F json > "$STATE_DIR/mr.json"
glab api "projects/:fullpath/merge_requests/<n>/approvals" > "$STATE_DIR/approvals.json"
python3 "$SCRIPT" classify --provider glab --mr-file "$STATE_DIR/mr.json" --approvals-file "$STATE_DIR/approvals.json" --user-approved-sha "<user_approved_sha albo pomiń flagę, gdy null>"
```

albo dla GitHuba:

```bash
gh pr view <n> --json number,state,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,headRefName,headRefOid,baseRefName,url,title > "$STATE_DIR/pr.json"
python3 "$SCRIPT" classify --provider gh --pr-file "$STATE_DIR/pr.json" --user-approved-sha "<jak wyżej>"
```

Kod inny niż `0` -> wypisz stderr i zakończ tick bez działania.

`classify` sprawdza w kolejności: zmergowany / zamknięty / nieznany stan / draft, potem **konflikt**, potem CI, approve i merge. Konflikt to GitLab `has_conflicts` albo `detailed_merge_status` równe `conflict` lub `need_rebase`, GitHub `mergeable=CONFLICTING` albo `mergeStateStatus=DIRTY`. Werdykt `conflict` zawsze niesie pola `pipeline_id` i `pipeline_status`, których używa KROK 6:

- GitLab: id i status `head_pipeline`, gdy pipeline jest aktywny (`running`, `pending`, `created`, `waiting_for_resource`, `preparing`, `scheduled`), inaczej oba `null`.
- GitHub: `pipeline_id` zawsze `null` (`statusCheckRollup` nie niesie id runu), `pipeline_status` równe `running`, gdy jakiś check jest w toku, inaczej `null`.

## KROK 4: Działanie według werdyktu

| Werdykt | Działanie |
|---|---|
| `merged` | zmergowany poza kolejką: 4c |
| `closed` | `queue-update --status closed`, wróć do KROK 3 |
| `wait` | nic nie rób (CI w toku bez konfliktu albo GitHub/GitLab liczy mergeability); zakończ tick |
| `ci_failed` | KROK 5 |
| `conflict` | KROK 6: anulowanie aktywnego pipeline'u MR/PR (gdy `pipeline_status` niepuste), potem merge brancha docelowego. Konflikt ma pierwszeństwo przed CI, także przed pipeline'em w toku |
| `needs_approval` | 4a |
| `needs_human` | 4d. Kolejka stoi. Pipeline anulowany na HEAD MR bez konfliktu (GitLab) daje ten werdykt: anulowanie to decyzja człowieka, tick go nie ponawia |
| `ready` | 4b |

### 4a. `needs_approval`

- **Interaktywnie**: zapytaj *"Approve <ref> (<tytuł>, SHA <pierwsze 8 znaków sha>)? tak / nie"*. `tak` -> zapisz approve dla **tego SHA** i policz werdykt ponownie (KROK 3b):

  ```bash
  python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --user-approved-sha <sha>
  ```

  `nie` -> zakończ tick; kolejka czeka na approve w MR/PR. Approve z czatu wiąże się z SHA: każdy nowy commit na branchu (także Twoja poprawka) wymaga nowego approve.
- **Batch**: `MR-QUEUE STOP: <ref> czeka na approve`.

### 4b. `ready` - merge

Merge zawsze z blokadą na SHA, który właśnie oceniłeś, i bez auto-merge. `MONOLYNX_MERGE_METHOD` (`merge` domyślnie, `squash`, `rebase`):

```bash
glab mr merge <n> --auto-merge=false --sha <sha> -y
```

(dla `squash` dopisz `--squash`, dla `rebase` `--rebase`)

```bash
gh pr merge <n> --merge --match-head-commit <sha>
```

(`--squash` albo `--rebase` zamiast `--merge` wg zmiennej)

Merge odrzucony (SHA się zmienił, reguła ochrony, hook odmówił) -> nie ponawiaj w tym ticku; zaraportuj komunikat i zakończ tick. Następny tick policzy werdykt od nowa.

Po udanym merge odczytaj commit merge. GitLab: ponów `glab mr view` i `classify` z KROK 3b - werdykt `merged` niesie pole `merge_sha`. GitHub:

```bash
gh pr view <n> --json mergeCommit -q .mergeCommit.oid
```

Potem **ustal tor po merge** i przełącz element:

1. Domyślny branch repozytorium. GitLab:

   ```bash
   glab repo view -F json --jq .default_branch
   ```

   GitHub:

   ```bash
   gh repo view --json defaultBranchRef --jq .defaultBranchRef.name
   ```

   Gdy element ma niepuste `repo`, podaj je jako argument pozycyjny (`glab repo view <repo> -F json --jq .default_branch`, `gh repo view <repo> --json defaultBranchRef --jq .defaultBranchRef.name`) - `repo view` nie przyjmuje `-R`. Błąd albo pusty wynik -> podaj pusty string; nie zgaduj `main`.

2. Tor liczy skrypt. `<target_branch>` to GitLab `target_branch` z `$STATE_DIR/mr.json`, GitHub `baseRefName` z `$STATE_DIR/pr.json`:

   ```bash
   python3 "$SCRIPT" post-merge --target-branch "<target_branch>" --default-branch "<domyślny branch albo pusty>"
   ```

   Wynik `{"status": "done"|"merging", "close_now": true|false, "reason": ...}`. `close_now=true` (`done`) wychodzi tylko wtedy, gdy oba branche są niepuste i różne (branch integracyjny); każdy inny przypadek, także nieustalony domyślny branch, daje `merging` z `close_now=false` - czekanie jest odwracalne następnym tickiem, a domknięcie ticketu bez CI brancha nie.

3. Dalej według `close_now`:

   - `close_now=false` (`merging`): zapis i koniec ticka. Następny tick sprawdzi CI brancha docelowego (3a), kolejka czeka.

     ```bash
     python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --status merging --merge-sha <sha commita merge>
     ```

   - `close_now=true` (branch integracyjny): w **tym samym ticku**, jako część tego samego działania (merge), wykonaj kolejno: (1) **Anulowanie pipeline'u brancha integracyjnego** (niżej), (2) **Domknięcie** (wyżej), wariant (b), z `merge_sha` = SHA commita merge. Zakończ tick; następny weźmie kolejny `pending`.

#### Anulowanie pipeline'u brancha integracyjnego (4b i 4c, `close_now=true`)

Push merge na branch integracyjny uruchamia pipeline, którego job `clean-code` z `resource_group` blokuje pipeline następnego MR w kolejce (15-20 min). Zielone CI MR już dowodzi, że zmiana przechodzi, więc ten pipeline jest zbędny. To część działania merge, nie osobne działanie. GitLab:

```bash
glab api "projects/:fullpath/pipelines?sha=<merge_sha>&ref=<target_branch>" > "$STATE_DIR/cancel-pipelines.json"
python3 "$SCRIPT" target-cancel --provider glab --pipelines-file "$STATE_DIR/cancel-pipelines.json"
glab ci cancel pipeline <id>
```

GitHub:

```bash
gh run list --commit <merge_sha> --json databaseId,status,conclusion,workflowName > "$STATE_DIR/cancel-runs.json"
python3 "$SCRIPT" target-cancel --provider gh --runs-file "$STATE_DIR/cancel-runs.json"
gh run cancel <databaseId>
```

`glab ci cancel pipeline` / `gh run cancel` wywołaj osobno dla każdego id z pola `cancel` wyniku `target-cancel`. Pole `skipped` (pipeline/run już zakończony albo o nieznanym statusie) niczego nie dotyka: anulowanie jest nieodwracalne, więc skrypt kwalifikuje tylko status znany jako trwający. Gdy `repo` pozycji jest niepuste, dopisuj `-R <repo>` do komend `glab` / `gh`, jak wszędzie w tym skillu.

Pusty `cancel` (pipeline jeszcze nie powstał po pushu albo już się skończył), błąd odczytu i błąd anulowania to **ostrzeżenie w raporcie ticka** (z `reason` ze skryptu albo komunikatem komendy), **nigdy zatrzymanie kolejki ani blokada Domknięcia**. Bez ponawiania w pętli.

### 4c. `merged` - merge poza kolejką

MR/PR zmergowano ręcznie albo tick przerwano po merge, przed `queue-update`. Pozycja idzie tym samym torem co merge z 4b. Ustal `merge_sha`. GitLab: pole `merge_sha` z werdyktu `classify` (KROK 3b). GitHub: werdykt go nie niesie, odczytaj:

```bash
gh pr view <n> --json mergeCommit -q .mergeCommit.oid
```

Pusty wynik -> nie zgaduj SHA (`headRefOid` to nie commit na branchu docelowym): traktuj jak werdykt `needs_human` (4d) z powodem "PR zmergowany poza kolejką bez mergeCommit - brak SHA na branchu docelowym". Z SHA wykonaj kroki 1-2 **ustalenia toru po merge** z 4b (domyślny branch, `post-merge`), potem:

- `close_now=false` (`merging`): `queue-update --status merging --merge-sha <merge_sha>`; pozycja wchodzi w 3a (z KROK 3, w tym samym ticku, z `next` z wyniku `queue-update`), a 3a z ewentualnym domknięciem jest jedynym działaniem ticka.
- `close_now=true` (branch integracyjny): **Anulowanie pipeline'u brancha integracyjnego** (jak w 4b), potem **Domknięcie**, wariant (b), z `merge_sha`. Dowód CI MR: GitLab `head_pipeline.status` z `$STATE_DIR/mr.json` (zapisanego w KROK 3b), GitHub `statusCheckRollup` z `$STATE_DIR/pr.json` albo `gh run list --commit <headRefOid>`. CI MR niezielone albo nieustalone nie wstrzymuje domknięcia: merge wykonał człowiek, a komentarz i raport ticka mówią "zmergowane poza kolejką, CI MR: `<status>`". W odróżnieniu od 4b tick nie wykonał jeszcze działania (merge stał się poza kolejką, tu były same odczyty i księgowość), więc po domknięciu wróć do KROK 3 z `next` z wyniku `queue-update`.

### 4d. `needs_human` - raport "Potrzebne"

Bez pytania i bez zmiany statusu kolejki: pozycja zostaje `pending`, a następny tick policzy werdykt od nowa, więc po działaniu człowieka kolejka rusza sama. Zakończ tick raportem z `reason` i linią `Potrzebne:` dobraną do powodu:

| `reason` zawiera | `Potrzebne:` |
|---|---|
| `draft` | autor zdejmuje status draft z <ref> |
| `nie ma pipeline'u` / `nie ma checkow` | uruchomienie CI na branchu `<source_branch>` (albo konfiguracja CI w repozytorium) |
| `pipeline <id> canceled` / `skipped` / `manual` | ponowienie pipeline'u `<id>` albo uruchomienie jego ręcznych jobów |
| `CHANGES_REQUESTED` | poprawki autora i nowe review, które zdejmie prośbę o zmiany |
| `blokuje merge` | spełnienie reguły z `reason` (wartość `detailed_merge_status` / `mergeStateStatus`, np. nierozwiązane dyskusje, reguła ochrony brancha) |
| `bez mergeCommit` (4c) | SHA commita merge na branchu docelowym: tor liczy `post-merge` (4b): `close_now=false` -> `queue-update --ref <ref> --status merging --merge-sha <sha>`, `close_now=true` -> anulowanie pipeline'u i Domknięcie (4b) |
| inny | przepisz `reason` i wskaż MR/PR (`url` z werdyktu) do ręcznego sprawdzenia |

Batch: `MR-QUEUE STOP: <ref> <reason>`.

## KROK 5: `ci_failed` - poprawka

**Limit**: `fix_rounds >= 2` -> nie poprawiaj trzeci raz. `queue-update --status stopped` i zakończ tick raportem z oboma nieudanymi podejściami (przyczyna i czerwone joby każdej rundy). `Potrzebne: ręczna poprawka CI na branchu <source_branch>; po pushu przywróć pozycję queue-update --ref <ref> --status pending`. Batch: `MR-QUEUE STOP: <ref> 2 rundy poprawek bez zielonego CI`.

**Cudzy branch**: autor MR/PR (`glab mr view <n> -F json --jq .author.username` / `gh pr view <n> --json author -q .author.login`) różny od zalogowanego użytkownika (`glab api user` pole `username` / `gh api user -q .login`) -> nie pushuj bez zgody. Interaktywnie zapytaj; batch: `MR-QUEUE STOP: <ref> branch innego autora`.

1. **Log**. GitLab: joby pipeline'u `pipeline_id` z werdyktu i ogon logu każdego czerwonego joba:

   ```bash
   glab ci get --pipeline-id <pipeline_id> -F json --jq '.jobs[] | select(.status=="failed") | "\(.id) \(.name)"'
   glab ci trace <job_id> 2>&1 | tail -n 150
   ```

   GitHub:

   ```bash
   gh run list --commit <sha> --json databaseId,conclusion,workflowName -q '.[] | select(.conclusion=="failure") | "\(.databaseId) \(.workflowName)"'
   gh run view <databaseId> --log-failed 2>&1 | tail -n 150
   ```

   `exit status 137` bez czerwonego testu to zabity kontener, nie błąd kodu: `glab ci retry <job_id>` (GitHub: `gh run rerun <databaseId> --failed`), bez zwiększania `fix_rounds`, i zakończ tick. Ten retry dotyczy joba zabitego przez runner, nie pipeline'u anulowanego przez człowieka - takiego pipeline'u tick nigdy nie ponawia (werdykt `needs_human`).

2. **Worktree** na aktualnym HEAD brancha MR, odłączony (działa także wtedy, gdy ten branch jest checkoutem użytkownika):

   ```bash
   git fetch origin <source_branch>
   git worktree add --detach "$STATE_DIR/wt-<provider>-<n>" "origin/<source_branch>"
   ```

3. **Poprawka** robi subagent z jawnym modelem (`.claude/rules/session-name-model.md` w repo pluginu): agent projektu dopasowany do obszaru błędu, a gdy go nie ma, `general-purpose`, z `model="sonnet"`. Prompt zawiera: ogon logu, ścieżkę worktree jako jedyne miejsce pracy, zakaz `git add/commit/push` (i tak blokuje go hook), zakaz zmian niezwiązanych z błędem, prośbę o listę zmienionych plików i jednozdaniową przyczynę.
4. **Commit i push** w głównej sesji:

   ```bash
   git -C "$STATE_DIR/wt-<provider>-<n>" add -A
   git -C "$STATE_DIR/wt-<provider>-<n>" commit -m "fix(ci): <przyczyna jednym zdaniem> (<ref>)"
   git -C "$STATE_DIR/wt-<provider>-<n>" push origin HEAD:refs/heads/<source_branch>
   git worktree remove "$STATE_DIR/wt-<provider>-<n>"
   python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --fix-round
   ```

   Nigdy `--force`, nigdy rebase. Push odrzucony (branch poszedł do przodu) -> usuń worktree, nie zwiększaj `fix_rounds`, zakończ tick.

Zakończ tick. Nowy pipeline oceni następny.

## KROK 6: `conflict` - merge brancha docelowego

Ten sam limit `fix_rounds` i ta sama reguła cudzego brancha co w KROK 5.

Oba sprawdzenia idą **przed** anulowaniem pipeline'u: anulowanie przed limitem albo regułą cudzego brancha skasowałoby pipeline, po czym tick i tak zatrzymałby się bez rozwiązania konfliktu.

**Anulowanie aktywnego pipeline'u** - tylko gdy `pipeline_status` z werdyktu jest niepuste. CI na branchu z konfliktem to zmarnowana runda, a merge brancha docelowego i tak da nowy commit z nowym CI. GitLab (`pipeline_id` z werdyktu):

```bash
glab ci cancel pipeline <pipeline_id>
```

GitHub: werdykt nie niesie id runu, więc odczytaj runy HEAD w toku (`<sha>` = `headRefOid` z `$STATE_DIR/pr.json`) i anuluj każdy z nich:

```bash
gh run list --commit <sha> --json databaseId,status -q '.[] | select(.status!="completed") | .databaseId'
gh run cancel <databaseId>
```

Anulowanie, które się nie powiodło (pipeline skończył się w międzyczasie), nie zatrzymuje ticka: odnotuj je w raporcie i przejdź do merge. Anulowanie i merge brancha docelowego z pushem to **jedno działanie ticka** (zasada 1 z "Ważnych zasad"): anulowanie jest częścią rozwiązania konfliktu, nie osobnym krokiem.

**Merge brancha docelowego** w tymczasowym worktree:

```bash
git fetch origin <source_branch> <target_branch>
git worktree add --detach "$STATE_DIR/wt-<provider>-<n>" "origin/<source_branch>"
git -C "$STATE_DIR/wt-<provider>-<n>" merge --no-ff --no-edit "origin/<target_branch>"
```

- Merge bez konfliktu (np. GitHub `BEHIND`) -> od razu push jak w KROK 5 pkt 4 (bez `add` i `commit`, merge zrobił commit).
- **Konflikty**: listę plików daje `git -C "$STATE_DIR/wt-<provider>-<n>" diff --name-only --diff-filter=U`. Rozwiązanie robi subagent z `model="opus"` (rozstrzyga między dwiema intencjami, nie tylko edytuje): dostaje ścieżkę worktree, listę plików w konflikcie, opis MR (`title`, `description`) i log commitów obu stron (`git -C <wt> log --oneline origin/<target_branch>..origin/<source_branch>` i odwrotnie). Zakaz komend git poza odczytem. Gdy intencje są sprzeczne i nie da się ich pogodzić, subagent przerywa z `STOP: <powód>` - wtedy `git -C <wt> merge --abort`, usuń worktree, `queue-update --status stopped` i zakończ tick raportem z listą plików w konflikcie i powodem subagenta. `Potrzebne: ręczne rozwiązanie konfliktu z <target_branch> na branchu <source_branch>; po pushu queue-update --ref <ref> --status pending`. Batch: `MR-QUEUE STOP: <ref> konflikt wymaga decyzji`.
- Po rozwiązaniu w głównej sesji: `git -C <wt> add -A`, `git -C <wt> commit --no-edit`, push, usunięcie worktree i `--fix-round` jak w KROK 5 pkt 4.

Po pushu zakończ tick: nowy commit oznacza nowe CI, więc następny tick zaczyna od sprawdzenia konfliktu, a dopiero potem ocenia nowe CI (zasada 5 na początku skilla: konflikt -> CI -> approve -> merge).

## KROK 7: Raport i linia statusu

Wypisz krótko: kolejkę przyjętą albo dopisaną w KROK 2 (gdy była), ref, tytuł, werdykt i `reason`, wykonane działanie (albo "bez działania"), a gdy tick stoi na człowieku - linię `Potrzebne:` z tym, czego dokładnie potrzebuje. Gdy Domknięcie zakończyło się ostrzeżeniem fast-forwardu, dopisz je w treści raportu z komendą ręczną; to samo dla ostrzeżeń anulowania pipeline'u brancha integracyjnego (linia `MR-QUEUE:` bez zmian).

Potem, jako **ostatnią linię odpowiedzi**, zamrożony format:

```
MR-QUEUE: remaining=<n> current=<ref albo -> verdict=<werdykt albo -> done=<d>
```

- `remaining` - elementy w statusie `pending` albo `merging` (w `merging` pozycja wchodzi po merge na domyślny branch z 4b albo z werdyktu `merged`, 4c). Zastane pozycje `verifying` się tu nie liczą (3.0 domyka je na początku ticka); po merge na branch integracyjny pozycja od razu jest `done`,
- `current` / `verdict` - element obsłużony w tym ticku (tick zakończony w 3.0 na `halted`: zatrzymana pozycja i `verdict=target_ci_failed`, wartość `stop_reason`),
- `done` - wyłącznie elementy `done` (nie zastane `verifying`).

Ten format jest kontraktem dla pętli i innych narzędzi. Nie zmieniaj go i nie dopisuj pól.

---

## Ważne zasady

1. **Jedno działanie na tick.** Push, merge albo pytanie - potem koniec tury. Anulowanie pipeline'u w KROK 6 jest częścią rozwiązania konfliktu, nie osobnym działaniem. Odczyty (`queue-show`, `classify`, `target-ci`, `target-cancel`) i ułożenie albo uzupełnienie kolejki (`queue-propose` + `queue-set`, `queue-append`) nie są działaniem; fast-forward brancha docelowego jest częścią Domknięcia, nie osobnym działaniem, a anulowanie pipeline'u brancha integracyjnego (4b) częścią merge. Domknięcia zastanych pozycji `verifying` (3.0), także kilku w jednym ticku, i domknięcie po merge poza kolejką (4c) to księgowość, nie działanie na MR/PR: po nich tick nadal może wykonać jedno działanie na `next`.
2. **Pytasz tylko o decyzje człowieka**: approve (4a), merge bez `MONOLYNX_AUTOMERGE` (hook) i push do brancha innego autora. Kolejność kolejki przyjmujesz sama, a stan wymagający człowieka (czerwone CI brancha docelowego, `needs_human`, limit rund, sprzeczny konflikt) kończysz raportem `Potrzebne:`, nie pytaniem "co dalej?".
3. **Nigdy nie zatwierdzasz MR/PR** i nigdy nie mergujesz bez werdyktu `ready` policzonego w tym samym ticku, z blokadą na ten SHA.
4. **Nigdy `--force`, rebase ani `git reset`** na branchu MR. Konflikt rozwiązuje merge brancha docelowego, poprzedzony anulowaniem aktywnego pipeline'u MR/PR.
5. **Checkout użytkownika zostaje nietknięty.** Poprawki tylko w `$STATE_DIR/wt-*`, usuwanym przez `git worktree remove` po pushu. Jedyny wyjątek: fast-forward lokalnego brancha docelowego w Domknięciu - po zielonym CI brancha docelowego (wariant (a)) albo od razu po merge na branch integracyjny (wariant (b)) (`git merge --ff-only` albo `git fetch origin <branch>:<branch>` bez `+`, komendy wyłącznie z `target-sync`); nigdy reset, rebase, stash, force. Niewykonalny fast-forward zostawia checkout bez zmian i daje ostrzeżenie z komendą ręczną.
6. **Po merge na domyślny branch repozytorium czekasz na zielone CI brancha docelowego**, zanim weźmiesz następny element (`merging`, 3a); ticket dostaje `done` dopiero po zielonym CI commita merge na tym branchu. **Po merge na branch integracyjny nie czekasz na jego CI**: pozycja i ticket są domykane od razu (4b, `close_now=true`) na podstawie zielonego CI samego MR/PR, a pipeline brancha integracyjnego wywołany merge anulujesz (ostrzeżenie zamiast zatrzymania, gdy się nie da). Tor wybiera `post-merge` (4b), nie Ty; nieustalony domyślny branch to `merging`. Dotyczy to także merge'a wykonanego poza kolejką (werdykt `merged`, 4c: tam CI MR niezielone albo nieustalone jest odnotowane, ale domknięcia nie wstrzymuje). Zastana pozycja `verifying` z toru 1.22.0 jest domykana w 3.0 bez odczytu CI brancha; zastana pozycja `stopped` ze `stop_reason=target_ci_failed` nadal zatrzymuje kolejkę (`halted`) do odblokowania przez człowieka (`queue-update --ref <ref> --status closed`).
7. **Subagent zawsze z jawnym modelem**: `sonnet` do poprawki CI, `opus` do rozwiązania konfliktu.
8. **Język raportu: polski.** Komunikaty commitów: konwencja repozytorium.
9. **Nie ponawiasz pipeline'u anulowanego przez człowieka.** Anulowany pipeline na HEAD MR bez konfliktu to `needs_human`, nie `ci_failed`. Retry dotyczy wyłącznie joba zabitego przez runner (`exit status 137`, KROK 5).
