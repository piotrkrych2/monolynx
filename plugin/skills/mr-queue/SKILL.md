---
name: mr-queue
description: "Kolejka merge MR (GitLab, glab) i PR (GitHub, gh): jeden idempotentny tick bierze pierwszy niezmergowany MR/PR z kolejki i robi dokładnie jedno działanie w kolejności konflikt -> CI -> approve -> merge, a po merge do domyślnego brancha repo czeka na zielone CI; po merge na branch integracyjny (np. sprint/*) bierze następny MR od razu, a CI commita merge sprawdza wstecz w kolejnych tickach. Bez argumentów sam układa kolejkę z otwartych MR/PR. Użyj gdy chcesz przeprowadzić MR-y do main po kolei, ręcznie albo w pętli /loop."
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

1. **Monitorujesz, nie pracujesz na branchu.** Stan czytasz przez `glab` / `gh`. Na branch MR/PR wchodzisz tylko wtedy, gdy trzeba go poprawić (czerwone CI albo konflikt), i tylko w tymczasowym worktree, który usuwasz po pushu. Checkout użytkownika zostaje nietknięty, z jednym wyjątkiem: po zielonym CI brancha docelowego wolno wykonać wyłącznie fast-forward lokalnego brancha docelowego (krok 4 **Domknięcia na `success`**, wspólnego dla 3.0 i 3a); nigdy reset, rebase, stash ani force.
2. **Stan liczy skrypt, nie Ty.** Werdykt daje `mr_queue_state.py` z JSON-a `glab` / `gh`. Nie wnioskujesz stanu z opisu MR, komentarzy ani z tekstowego wyjścia CLI.
3. **Kolejność jest święta.** Pierwszy MR/PR blokuje resztę. Na domyślnym branchu repozytorium (np. `main`) następny wchodzi dopiero, gdy poprzedni jest zmergowany i CI brancha docelowego jest zielone. Na **branchu integracyjnym** (branch docelowy MR/PR różny od domyślnego brancha repozytorium, np. `sprint/*`) następny wchodzi zaraz po merge poprzedniego: całość i tak weryfikuje MR brancha integracyjnego do domyślnego, więc CI commita merge sprawdzasz wstecz w kolejnych tickach (KROK 3.0), a czerwone zatrzymuje całą kolejkę.
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

**Pole `halted` niepuste** -> kolejka stoi na czerwonym CI brancha integracyjnego (`next` jest wtedy `null`, ale to nie znaczy "wszystko obsłużone"). Nie układaj nowej kolejki ani nie dopisuj pozycji; przejdź od razu do KROK 3 (3.0 zaraportuje zatrzymanie).

**Pole `next` puste i `halted` puste** (brak pliku albo wszystko obsłużone; pozycje `verifying` nie są w `next` i `queue-set` ich nie gubi) -> ułóż nową kolejkę:

```bash
python3 "$SCRIPT" queue-propose --provider <glab|gh> --list-file "$STATE_DIR/list.json"
```

`queue-propose` zwraca `order` (MR oparty o branch innego otwartego MR idzie zaraz po nim, reszta od najstarszego) i `skipped` (drafty i MR-y oparte o draft).

- `order` puste -> nie ma nowych MR/PR do mergowania. Gdy w kolejce są pozycje `verifying` (pole `verifying` z `queue-show`), idź do KROK 3 (3.0 weryfikacja wsteczna): ich CI trzeba jeszcze ocenić, a ticket przełączyć na `done`. Dopiero bez pozycji `verifying` tick kończy się raportem i linią statusu z `remaining=0`.
- `order` niepuste -> przyjmij je bez pytania, w obu trybach, i zapisz przez `queue-set` jak wyżej (refy z `order` w tej kolejności). W raporcie ticka wypisz przyjętą kolejkę (ref, tytuł z `list.json`, branch -> branch docelowy) i pominięte z powodem. Inną kolejność użytkownik ustawia argumentami skilla w dowolnym momencie.

**Pole `next` niepuste** -> dopisz na koniec kolejki otwarte MR/PR, których w niej jeszcze nie ma:

```bash
python3 "$SCRIPT" queue-append --queue-file "$QUEUE" --provider <glab|gh> --list-file "$STATE_DIR/list.json"
```

`queue-append` zostawia istniejące pozycje nietknięte (w dowolnym statusie, także `stopped` i `done`, więc zatrzymany MR nie wraca sam), a nowe dopisuje w kolejności `queue-propose`. Zwraca `appended` i `skipped` (pominięte spoza kolejki, z powodem). `appended` niepuste -> wypisz dopisane refy w raporcie ticka.

Kod `2` z `queue-propose` / `queue-append` -> wypisz stderr i zakończ tick bez działania.

Ułożenie i uzupełnienie kolejki to odczyt i zapis lokalnego pliku, nie działanie ticku: w tym samym ticku idź do KROK 3.

## KROK 3: Element `next`

Najpierw, raz na tick, weryfikacja wsteczna brancha integracyjnego (3.0). Potem weź element kolejki o refie `next` z ostatniego wyniku skryptu (`queue-show` albo `queue-update`; pola `provider`, `number`, `repo`, `status`, `fix_rounds`, `user_approved_sha`, `merge_sha`). Gdy `repo` jest niepuste, dopisuj `-R <repo>` do każdej komendy `glab` / `gh` tego elementu - dotyczy to także pozycji `verifying` w 3.0.

### 3.0. Weryfikacja wsteczna brancha integracyjnego

Pozycja w statusie `verifying` to MR/PR zmergowany na branch integracyjny (4b / 4c), którego CI commita merge nie zostało jeszcze ocenione. Kolejka nie czeka na nie (`verifying` nigdy nie jest `next`), więc sprawdzasz je tu, na początku każdego ticka:

```bash
python3 "$SCRIPT" queue-show --queue-file "$QUEUE"
```

Wynik niesie `next`, `halted` (ref pozycji, która zatrzymała kolejkę czerwonym CI brancha integracyjnego, albo `null`) i `verifying` (lista refów w kolejności kolejki).

**`halted` niepuste** -> kolejka stoi od poprzedniego ticka i `next` jest `null`. Nie bierz żadnego elementu i nie sprawdzaj pozycji `verifying`. Zakończ tick raportem: ref zatrzymanej pozycji i linia `Potrzebne: poprawka brancha <target_branch> (nowy ticket albo osobny MR, nie Twój tick); po zielonym CI brancha odblokuj kolejkę queue-update --ref <halted> --status closed`. Ticket tej pozycji zostaje nietknięty (nie przeszedł zielonego CI). Batch: `MR-QUEUE STOP: <halted> czerwone CI brancha docelowego`.

**`halted` puste** -> dla każdego refu z `verifying`, po kolei: wykonaj **Odczyt CI commita merge** (niżej; `merge_sha` z pozycji, `--jobs-file` zawsze, bo raport czerwonego CI potrzebuje nazw jobów) i działaj według `status` z `target-ci`:

| `status` | Działanie |
|---|---|
| `success` | **Domknięcie na `success`** (niżej): ticket -> `done`, fast-forward brancha docelowego, `queue-update --status done`. Potem następna pozycja `verifying` |
| `wait` | pozycja zostaje `verifying`; następna pozycja `verifying` |
| `failed` | `python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --status stopped --stop-reason target_ci_failed`. Zakończ tick raportem z nazwami czerwonych jobów (`failed_jobs` z `target-ci`) i identyfikatorem pipeline'u/runu (GitLab: `id` najnowszego wpisu w `pipelines.json`; GitHub: `databaseId` czerwonych runów z `runs.json`). `Potrzebne: poprawka brancha <target_branch> (nowy ticket albo osobny MR, nie Twój tick); po zielonym CI brancha odblokuj kolejkę queue-update --ref <ref> --status closed`. Pozostałych pozycji `verifying` ani `next` w tym ticku nie ruszasz. Batch: `MR-QUEUE STOP: <ref> czerwone CI brancha docelowego` |
| `needs_human` | pozycja zostaje `verifying` (następny tick sprawdzi CI ponownie); w raporcie `reason` i `Potrzebne:` jak w 3a (uruchomienie CI dla commita `<merge_sha>` na `<target_branch>` albo ponowienie pipeline'u `canceled` / `skipped`). Kolejka nie staje: następna pozycja `verifying` |

Domknięcia kilku zielonych pozycji w jednym ticku to księgowość, nie działanie na MR/PR (zasada 1 z "Ważnych zasad"). Po ostatniej pozycji `verifying` weź `next` z ostatniego wyniku skryptu i przejdź do 3a albo 3b według jego statusu. Pusty `next` -> raport i linia statusu (KROK 7).

### 3a. Status `merging` - czekanie na CI brancha docelowego

MR jest już zmergowany na domyślny branch repozytorium (`merge_sha` ustawione), a kolejka czeka na zielone CI commita merge. Wykonaj **Odczyt CI commita merge** (niżej) i działaj według `status` z `target-ci`:

| `status` | Działanie |
|---|---|
| `success` | **Domknięcie na `success`** (niżej): ticket -> `done`, fast-forward brancha docelowego, `queue-update --status done` |
| `wait` | nic nie rób, zakończ tick |
| `failed` | `queue-update --ref <ref> --status stopped`; zakończ tick raportem z nazwami czerwonych jobów i identyfikatorem pipeline'u/runu. `Potrzebne: poprawka brancha <target_branch> (nowy ticket albo osobny MR, nie Twój tick); po zielonym CI domknij pozycję queue-update --ref <ref> --status done`. Batch: `MR-QUEUE STOP: <ref> czerwone CI brancha docelowego` |
| `needs_human` | bez zmiany statusu kolejki (pozycja zostaje w `merging`, następny tick sprawdzi CI ponownie); zakończ tick raportem z `reason`. `Potrzebne:` przy braku pipeline'u/runów - uruchomienie CI dla commita `<merge_sha>` na `<target_branch>`; przy pipeline `canceled` / `skipped` - ponowienie go. Batch: `MR-QUEUE STOP: <ref> <reason>` |

Komenda `queue-update` w pełnej postaci: `python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --status <status>`.

#### Odczyt CI commita merge (wspólny dla 3.0 i 3a)

Odczytaj `target_branch` razem z branchem źródłowym i tytułem - ten sam JSON zasili później klucz ticketu w **Domknięciu na `success`**, więc czytaj go zawsze, niezależnie od tego, czy `target_branch` znasz skądinąd:

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

#### Domknięcie na `success`: ticket -> `done` i `queue-update` (wspólne dla 3.0 i 3a)

Zielone CI commita merge na branchu docelowym domyka pozycję kolejki **i** (gdy da się rozpoznać powiązany ticket) przełącza ten ticket na `done`. Pozycja przychodzi tu z 3a (`merging`, domyślny branch repozytorium) albo z 3.0 (`verifying`, branch integracyjny) - w obu torach ticket dostaje `done` dopiero po zielonym CI swojego commita merge. To wciąż **jedno działanie tick** - zmiana statusu ticketu i fast-forward lokalnego brancha docelowego są częścią tego samego domknięcia pozycji kolejki, nie osobnymi krokami.

**Załaduj narzędzia (raz, przy pierwszym dojściu do tej gałęzi w danej sesji)**:

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

   - **Guard prefiksu projektu.** Pierwsza linia wyniku ma kształt `<KOD>-<numer> | tytuł`. Porównaj `<KOD>-<numer>` z `<key>` wyliczonym przez `ticket-key` - muszą być identyczne. Rozjazd (np. klucz `JIRA-45` z brancha innego repo trafia tu jako `<key>`, a projekt Monolynx ma kod `MON`) traktuj **dokładnie jak `key: null`**: ticket zostaje nietknięty, kroki 4 i 5 wykonują się mimo to, w raporcie ticka jawnie zaznacz "klucz `<key>` nie należy do tego projektu (`get_ticket` zwrócił `<KOD>-<numer>`)". Sprawdzenie istnieje, bo `_resolve_ticket_uuid` (`src/monolynx/mcp_server.py`) dopasowuje ticket wyłącznie po numerze i ignoruje prefiks kodu projektu - bez tego guardu klucz z cudzego projektu cicho przestawiłby na `done` przypadkowy, tak samo ponumerowany ticket w tym projekcie.
   - Prefiks się zgadza i druga linia wyniku zaczyna się `Status: done` -> ticket już zamknięty (ręcznie albo w poprzednim ticku). **Nie zmieniaj statusu, nie dodawaj drugiego komentarza.** Odnotuj w raporcie: "ticket `<key>` już `done`, bez zmian" (idempotencja ticka).
   - Prefiks się zgadza i status inny niż `done` -> zmień na `done` i dopisz komentarz z merge SHA oraz identyfikatorem pipeline'u/runu brancha docelowego, który `target-ci` już ocenił wyżej jako `success` - GitLab: pole `id` najnowszego wpisu w `pipelines.json`; GitHub: `databaseId` runu (albo runów, gdy `runs.json` ma ich kilka) z `runs.json`:

     ```
     mcp__monolynx__update_ticket(project_slug="<PROJECT_SLUG>", ticket_id="<key>", status="done")
     mcp__monolynx__add_comment(project_slug="<PROJECT_SLUG>", ticket_id="<key>", content="Merge SHA: <merge_sha> - CI brancha docelowego zielone (pipeline <id>).")
     ```

   Błąd wywołania MCP (sieć, autoryzacja, projekt bez tego ticketu) **nie blokuje domknięcia pozycji kolejki**: złap go, wykonaj mimo to kroki 4 i 5, i zaznacz w raporcie ticka "aktualizacja ticketu `<key>` nie powiodła się: `<błąd>`" - obserwacyjna warstwa (ticket) nigdy nie trzyma zakładnika kolejki merge.

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

   Ticket zmieniasz **przed** `queue-update`, nie po: `queue-update --status done` zdejmuje pozycję z `next`, więc przerwanie ticka między nimi (crash, koniec kontekstu) po samym `queue-update` zostawiłoby pozycję kolejki trwale domkniętą, a ticket na zawsze w `in_review` - żaden kolejny tick już by na niego nie spojrzał.

   Domknięcie z 3.0 -> następna pozycja `verifying` (tabela w 3.0). Domknięcie z 3a -> wróć do KROK 3 z nowym `next` z wyniku `queue-update` (odczyt jest tani, działanie zostaje jedno), bez ponownej weryfikacji wstecznej: 3.0 idzie raz na tick.

### 3b. Status `pending` - werdykt

```bash
glab mr view <n> -F json > "$STATE_DIR/mr.json"
glab api "projects/:fullpath/merge_requests/<n>/approvals" > "$STATE_DIR/approvals.json"
python3 "$SCRIPT" classify --provider glab --mr-file "$STATE_DIR/mr.json" --approvals-file "$STATE_DIR/approvals.json" --user-approved-sha "<user_approved_sha albo pomiń flagę, gdy null>"
```

albo dla GitHuba:

```bash
gh pr view <n> --json number,state,isDraft,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup,headRefName,headRefOid,baseRefName,url > "$STATE_DIR/pr.json"
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

   Wynik `{"status": "verifying"|"merging", "reason": ...}`. `verifying` wychodzi tylko wtedy, gdy oba branche są niepuste i różne (branch integracyjny); każdy inny przypadek, także nieustalony domyślny branch, daje `merging` - czekanie jest odwracalne następnym tickiem, a merge następnego MR na czerwony branch nie.

3. Zapis:

   ```bash
   python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --status <status z post-merge> --merge-sha <sha commita merge>
   ```

Zakończ tick. `merging`: CI brancha docelowego sprawdzi następny tick (3a), kolejka czeka. `verifying`: następny tick weźmie od razu kolejny `pending`, a CI tego commita merge sprawdzi wstecz (3.0).

### 4c. `merged` - merge poza kolejką

MR/PR zmergowano ręcznie albo tick przerwano po merge, przed `queue-update`. Pozycja idzie tym samym torem co merge z 4b: `merging` (domyślny branch, potem 3a) albo `verifying` (branch integracyjny, potem 3.0) z `merge_sha`; ticket -> `done` dopiero po zielonym CI commita merge. Ustal `merge_sha`. GitLab: pole `merge_sha` z werdyktu `classify` (KROK 3b). GitHub: werdykt go nie niesie, odczytaj:

```bash
gh pr view <n> --json mergeCommit -q .mergeCommit.oid
```

Pusty wynik -> nie zgaduj SHA (`headRefOid` to nie commit na branchu docelowym): traktuj jak werdykt `needs_human` (4d) z powodem "PR zmergowany poza kolejką bez mergeCommit - brak SHA na branchu docelowym". Z SHA wykonaj kroki 1-3 **ustalenia toru po merge** z 4b (domyślny branch, `post-merge`, `queue-update --status <status z post-merge> --merge-sha <merge_sha>`).

Wróć do KROK 3 w tym samym ticku, z `next` z wyniku `queue-update`. W odróżnieniu od 4b tick nie wykonał jeszcze żadnego działania (merge stał się poza kolejką, tu były same odczyty). `merging`: pozycja wchodzi w 3a, a 3a z ewentualnym domknięciem na `success` jest jedynym działaniem ticka. `verifying`: pozycja wychodzi z `next`, jej CI sprawdzi 3.0 następnego ticka, a ten tick bierze kolejny element.

### 4d. `needs_human` - raport "Potrzebne"

Bez pytania i bez zmiany statusu kolejki: pozycja zostaje `pending`, a następny tick policzy werdykt od nowa, więc po działaniu człowieka kolejka rusza sama. Zakończ tick raportem z `reason` i linią `Potrzebne:` dobraną do powodu:

| `reason` zawiera | `Potrzebne:` |
|---|---|
| `draft` | autor zdejmuje status draft z <ref> |
| `nie ma pipeline'u` / `nie ma checkow` | uruchomienie CI na branchu `<source_branch>` (albo konfiguracja CI w repozytorium) |
| `pipeline <id> canceled` / `skipped` / `manual` | ponowienie pipeline'u `<id>` albo uruchomienie jego ręcznych jobów |
| `CHANGES_REQUESTED` | poprawki autora i nowe review, które zdejmie prośbę o zmiany |
| `blokuje merge` | spełnienie reguły z `reason` (wartość `detailed_merge_status` / `mergeStateStatus`, np. nierozwiązane dyskusje, reguła ochrony brancha) |
| `bez mergeCommit` (4c) | SHA commita merge na branchu docelowym: `queue-update --ref <ref> --status <merging albo verifying> --merge-sha <sha>` (`verifying`, gdy branch docelowy jest branchem integracyjnym, tak jak liczy `post-merge` w 4b) |
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

Wypisz krótko: kolejkę przyjętą albo dopisaną w KROK 2 (gdy była), ref, tytuł, werdykt i `reason`, wykonane działanie (albo "bez działania"), a gdy tick stoi na człowieku - linię `Potrzebne:` z tym, czego dokładnie potrzebuje. Gdy Domknięcie na `success` zakończyło się ostrzeżeniem fast-forwardu, dopisz je w treści raportu z komendą ręczną (linia `MR-QUEUE:` bez zmian).

Potem, jako **ostatnią linię odpowiedzi**, zamrożony format:

```
MR-QUEUE: remaining=<n> current=<ref albo -> verdict=<werdykt albo -> done=<d>
```

- `remaining` - elementy w statusie `pending` albo `merging` (w `merging` pozycja wchodzi po merge na domyślny branch z 4b albo z werdyktu `merged`, 4c). Pozycje `verifying` (zmergowane na branch integracyjny, CI w weryfikacji wstecznej) się tu nie liczą; wypisz je w treści raportu,
- `current` / `verdict` - element obsłużony w tym ticku (tick zakończony w 3.0 czerwonym CI brancha integracyjnego albo `halted`: zatrzymana pozycja i `verdict=target_ci_failed`, wartość `stop_reason`),
- `done` - wyłącznie elementy `done` (nie `verifying`).

Ten format jest kontraktem dla pętli i innych narzędzi. Nie zmieniaj go i nie dopisuj pól.

---

## Ważne zasady

1. **Jedno działanie na tick.** Push, merge albo pytanie - potem koniec tury. Anulowanie pipeline'u w KROK 6 jest częścią rozwiązania konfliktu, nie osobnym działaniem. Odczyty (`queue-show`, `classify`, `target-ci`) i ułożenie albo uzupełnienie kolejki (`queue-propose` + `queue-set`, `queue-append`) nie są działaniem; fast-forward brancha docelowego jest częścią Domknięcia na `success`, nie osobnym działaniem. Domknięcia pozycji `verifying` w weryfikacji wstecznej (3.0), także kilku w jednym ticku, to księgowość, nie działanie na MR/PR: po nich tick nadal może wykonać jedno działanie na `next`.
2. **Pytasz tylko o decyzje człowieka**: approve (4a), merge bez `MONOLYNX_AUTOMERGE` (hook) i push do brancha innego autora. Kolejność kolejki przyjmujesz sama, a stan wymagający człowieka (czerwone CI brancha docelowego, `needs_human`, limit rund, sprzeczny konflikt) kończysz raportem `Potrzebne:`, nie pytaniem "co dalej?".
3. **Nigdy nie zatwierdzasz MR/PR** i nigdy nie mergujesz bez werdyktu `ready` policzonego w tym samym ticku, z blokadą na ten SHA.
4. **Nigdy `--force`, rebase ani `git reset`** na branchu MR. Konflikt rozwiązuje merge brancha docelowego, poprzedzony anulowaniem aktywnego pipeline'u MR/PR.
5. **Checkout użytkownika zostaje nietknięty.** Poprawki tylko w `$STATE_DIR/wt-*`, usuwanym przez `git worktree remove` po pushu. Jedyny wyjątek: fast-forward lokalnego brancha docelowego po zielonym CI (`git merge --ff-only` albo `git fetch origin <branch>:<branch>` bez `+`, komendy wyłącznie z `target-sync`); nigdy reset, rebase, stash, force. Niewykonalny fast-forward zostawia checkout bez zmian i daje ostrzeżenie z komendą ręczną.
6. **Po merge na domyślny branch repozytorium czekasz na zielone CI brancha docelowego**, zanim weźmiesz następny element (`merging`, 3a). **Po merge na branch integracyjny nie czekasz** (`verifying`): następny element wchodzi od razu, a CI commita merge sprawdza 3.0 w każdym kolejnym ticku. Czerwone CI dowolnego niezweryfikowanego commita merge zatrzymuje całą kolejkę (`stopped` z `stop_reason=target_ci_failed`) do odblokowania przez człowieka (`queue-update --ref <ref> --status closed`). Tor wybiera `post-merge` (4b), nie Ty; nieustalony domyślny branch to `merging`. W obu torach ticket przechodzi na `done` dopiero po zielonym CI swojego commita merge. Dotyczy to także merge'a wykonanego poza kolejką (werdykt `merged`, 4c).
7. **Subagent zawsze z jawnym modelem**: `sonnet` do poprawki CI, `opus` do rozwiązania konfliktu.
8. **Język raportu: polski.** Komunikaty commitów: konwencja repozytorium.
9. **Nie ponawiasz pipeline'u anulowanego przez człowieka.** Anulowany pipeline na HEAD MR bez konfliktu to `needs_human`, nie `ci_failed`. Retry dotyczy wyłącznie joba zabitego przez runner (`exit status 137`, KROK 5).
