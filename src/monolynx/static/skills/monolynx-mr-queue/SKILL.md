---
name: monolynx-mr-queue
description: "Kolejka merge MR (GitLab, glab) i PR (GitHub, gh): jeden idempotentny tick bierze pierwszy niezmergowany MR/PR z kolejki i robi dokładnie jedno działanie w kolejności CI -> konflikt -> approve -> merge, a po merge czeka na zielone CI brancha docelowego. Bez argumentów sam układa kolejkę z otwartych MR/PR. Użyj gdy chcesz przeprowadzić MR-y do main po kolei, ręcznie albo w pętli /loop."
user-invocable: true
argument-hint: "[!iid | #pr | URL ...]"
allowed-tools: Bash, Read, Edit, Write, Agent
---

# MR-QUEUE - kolejka merge MR/PR

Jesteś **strażnikiem kolejki merge**. Jedno uruchomienie tego skilla to jeden **tick**: czytasz stan pierwszego MR/PR z kolejki, robisz dokładnie jedno działanie, raportujesz i kończysz turę.

Zasady, które definiują ten skill:

1. **Monitorujesz, nie pracujesz na branchu.** Stan czytasz przez `glab` / `gh`. Na branch MR/PR wchodzisz tylko wtedy, gdy trzeba go poprawić (czerwone CI albo konflikt), i tylko w tymczasowym worktree, który usuwasz po pushu. Checkout użytkownika zostaje nietknięty.
2. **Stan liczy skrypt, nie Ty.** Werdykt daje `mr_queue_state.py` z JSON-a `glab` / `gh`. Nie wnioskujesz stanu z opisu MR, komentarzy ani z tekstowego wyjścia CLI.
3. **Kolejność jest święta.** Pierwszy MR/PR blokuje resztę: następny wchodzi dopiero, gdy poprzedni jest zmergowany i CI brancha docelowego jest zielone.
4. **Tick jest idempotentny.** Stan kolejki leży w pliku, a stan MR/PR w GitLabie / GitHubie. Dwa ticki pod rząd nie zdublują pusha, merge ani pytania.

Pętla jest **na zewnątrz** skilla: `/loop 10m /monolynx:mr-queue`.

---

## KROK 0: Stałe i tryb

```bash
COMMON="$(git rev-parse --git-common-dir)"
STATE_DIR="$COMMON/monolynx/mr-queue"
QUEUE="$COMMON/monolynx/mr-queue.json"
SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/mr_queue_state.py"
mkdir -p "$STATE_DIR"
echo "STATE_DIR=$STATE_DIR QUEUE=$QUEUE SCRIPT=$SCRIPT"
echo "MODE=${MONOLYNX_SPRINT_RUN_MODE:-interactive} AUTOPUSH=${MONOLYNX_AUTOPUSH:-false} AUTOMERGE=${MONOLYNX_AUTOMERGE:-false} MERGE_METHOD=${MONOLYNX_MERGE_METHOD:-merge}"
```

`--git-common-dir` sprawia, że kolejka jest wspólna dla głównego checkoutu i wszystkich worktree tego repozytorium. Każdą następną komendę w tym skillu pisz z tymi ścieżkami rozwiniętymi literalnie (wartości z wyjścia powyżej).

**Tryb `batch`** (`MONOLYNX_SPRINT_RUN_MODE=batch`): nikt nie odpowie na pytanie. Tam, gdzie tryb interaktywny pyta użytkownika, tryb batch kończy turę linią `MR-QUEUE STOP: <ref> <powód>` (przed linią statusu z KROK 7).

**Merge i approve pilnuje hook `bash_guard.py`**: `glab mr merge` / `gh pr merge` pyta o zgodę, dopóki `MONOLYNX_AUTOMERGE` nie jest `true` (w trybie w tle `MONOLYNX_SPRINT_RUN=1` pytanie zamienia się w odmowę). `glab mr approve` / `gh pr review --approve` pyta zawsze. Ty nigdy nie zatwierdzasz MR/PR - approve daje człowiek.

## KROK 1: Preflight

```bash
git rev-parse --show-toplevel
git remote get-url origin
glab auth status 2>&1 | head -3
gh auth status 2>&1 | head -3
```

- **Dostawca domyślny** (dla kolejki układanej automatycznie) wynika z hosta `origin`: `github.com` -> `gh`, każdy inny host -> `glab`.
- Dostawca potrzebny dla kolejki nie jest zalogowany -> wypisz komendę logowania (`glab auth login` / `gh auth login`), nic nie zmieniaj i zakończ tick (w batch linią `MR-QUEUE STOP: - brak logowania <glab|gh>`).

## KROK 2: Kolejka

**Podano argumenty** (`$ARGUMENTS` niepusty): to jest kolejność merge. Zapisz ją:

```bash
python3 "$SCRIPT" queue-set --queue-file "$QUEUE" <refy z $ARGUMENTS w podanej kolejności>
```

Ref to `!<iid>` (GitLab), `#<numer>` (GitHub) albo pełny URL MR/PR. Kod `2` (nieznany ref, duplikat) -> wypisz stderr i zakończ tick.

**Brak argumentów**: pokaż zapisaną kolejkę.

```bash
python3 "$SCRIPT" queue-show --queue-file "$QUEUE"
```

Pole `next` niepuste -> idź do KROK 3. Pole `next` puste (brak pliku albo wszystko obsłużone) -> ułóż nową kolejkę z otwartych MR/PR dostawcy domyślnego:

```bash
glab mr list -F json -P 100 > "$STATE_DIR/list.json"
python3 "$SCRIPT" queue-propose --provider glab --list-file "$STATE_DIR/list.json"
```

albo dla GitHuba:

```bash
gh pr list --state open -L 100 --json number,createdAt,baseRefName,headRefName,isDraft,url > "$STATE_DIR/list.json"
python3 "$SCRIPT" queue-propose --provider gh --list-file "$STATE_DIR/list.json"
```

`queue-propose` zwraca `order` (MR oparty o branch innego otwartego MR idzie zaraz po nim, reszta od najstarszego) i `skipped` (drafty i MR-y oparte o draft).

- `order` puste -> nie ma czego mergować; raport i linia statusu z `remaining=0`.
- **Interaktywnie**: pokaż kolejkę (ref, tytuł z `list.json`, branch -> branch docelowy) i pominięte z powodem. Zapytaj: *"ENTER = ta kolejność, albo wpisz refy w swojej kolejności"*. Odpowiedź użytkownika wygrywa.
- **Batch**: przyjmij `order` bez pytania.

Zapisz wynik przez `queue-set` jak wyżej.

## KROK 3: Element `next`

Weź element kolejki o refie `next` (pola `provider`, `number`, `repo`, `status`, `fix_rounds`, `user_approved_sha`, `merge_sha`). Gdy `repo` jest niepuste, dopisuj `-R <repo>` do każdej komendy `glab` / `gh` tego elementu.

### 3a. Status `merging` - czekanie na CI brancha docelowego

MR jest już zmergowany (`merge_sha` ustawione). Sprawdź pipeline commita merge na branchu docelowym (`target_branch` z ostatniego `classify`; gdy go nie znasz, odczytaj go z `glab mr view <n> -F json` / `gh pr view <n> --json baseRefName`):

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

| `status` | Działanie |
|---|---|
| `success` | `queue-update --ref <ref> --status done`, potem wróć do KROK 3 z nowym `next` (odczyt jest tani, działanie zostaje jedno) |
| `wait` | nic nie rób, zakończ tick |
| `failed` | `queue-update --ref <ref> --status stopped`; zaraportuj czerwony pipeline z nazwami jobów. Poprawka na branchu docelowym to nowa praca (ticket albo osobny MR), nie Twój tick. Interaktywnie zapytaj, co dalej; batch: `MR-QUEUE STOP` |
| `needs_human` | jak `failed`, bez zmiany statusu kolejki |

Komenda `queue-update` w pełnej postaci: `python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --status <status>`.

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

## KROK 4: Działanie według werdyktu

| Werdykt | Działanie |
|---|---|
| `merged` | zmergowany poza kolejką: `queue-update --status done`, wróć do KROK 3 |
| `closed` | `queue-update --status closed`, wróć do KROK 3 |
| `wait` | nic nie rób (CI w toku albo GitHub/GitLab liczy mergeability); zakończ tick |
| `ci_failed` | KROK 5 |
| `conflict` | KROK 6 |
| `needs_approval` | 4a |
| `needs_human` | zaraportuj `reason`; interaktywnie zapytaj, co zrobić; batch: `MR-QUEUE STOP: <ref> <reason>`. Kolejka stoi |
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

Po udanym merge odczytaj commit merge i przełącz element w `merging`. GitLab: ponów `glab mr view` i `classify` z KROK 3b - werdykt `merged` niesie pole `merge_sha`. GitHub:

```bash
gh pr view <n> --json mergeCommit -q .mergeCommit.oid
```

Potem:

```bash
python3 "$SCRIPT" queue-update --queue-file "$QUEUE" --ref <ref> --status merging --merge-sha <sha commita merge>
```

Zakończ tick. CI brancha docelowego sprawdzi następny (3a).

## KROK 5: `ci_failed` - poprawka

**Limit**: `fix_rounds >= 2` -> nie poprawiaj trzeci raz. `queue-update --status stopped`, zaraportuj oba nieudane podejścia; interaktywnie zapytaj, batch: `MR-QUEUE STOP: <ref> 2 rundy poprawek bez zielonego CI`.

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

   `exit status 137` bez czerwonego testu to zabity kontener, nie błąd kodu: `glab ci retry <job_id>` (GitHub: `gh run rerun <databaseId> --failed`), bez zwiększania `fix_rounds`, i zakończ tick.

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

```bash
git fetch origin <source_branch> <target_branch>
git worktree add --detach "$STATE_DIR/wt-<provider>-<n>" "origin/<source_branch>"
git -C "$STATE_DIR/wt-<provider>-<n>" merge --no-ff --no-edit "origin/<target_branch>"
```

- Merge bez konfliktu (np. GitHub `BEHIND`) -> od razu push jak w KROK 5 pkt 4 (bez `add` i `commit`, merge zrobił commit).
- **Konflikty**: listę plików daje `git -C "$STATE_DIR/wt-<provider>-<n>" diff --name-only --diff-filter=U`. Rozwiązanie robi subagent z `model="opus"` (rozstrzyga między dwiema intencjami, nie tylko edytuje): dostaje ścieżkę worktree, listę plików w konflikcie, opis MR (`title`, `description`) i log commitów obu stron (`git -C <wt> log --oneline origin/<target_branch>..origin/<source_branch>` i odwrotnie). Zakaz komend git poza odczytem. Gdy intencje są sprzeczne i nie da się ich pogodzić, subagent przerywa z `STOP: <powód>` - wtedy `git -C <wt> merge --abort`, usuń worktree, `queue-update --status stopped`, pytanie albo `MR-QUEUE STOP`.
- Po rozwiązaniu w głównej sesji: `git -C <wt> add -A`, `git -C <wt> commit --no-edit`, push, usunięcie worktree i `--fix-round` jak w KROK 5 pkt 4.

Po pushu zakończ tick: nowy commit oznacza nowe CI, więc następny tick wraca do sprawdzenia CI (pkt 1 kolejności).

## KROK 7: Raport i linia statusu

Wypisz krótko: ref, tytuł, werdykt i `reason`, wykonane działanie (albo "bez działania"), a gdy tick stoi na człowieku - czego dokładnie potrzebuje.

Potem, jako **ostatnią linię odpowiedzi**, zamrożony format:

```
MR-QUEUE: remaining=<n> current=<ref albo -> verdict=<werdykt albo -> done=<d>
```

- `remaining` - elementy w statusie `pending` albo `merging`,
- `current` / `verdict` - element obsłużony w tym ticku,
- `done` - elementy `done`.

Ten format jest kontraktem dla pętli i innych narzędzi. Nie zmieniaj go i nie dopisuj pól.

---

## Ważne zasady

1. **Jedno działanie na tick.** Push, merge albo pytanie - potem koniec tury. Odczyty (`queue-show`, `classify`, `target-ci`) nie są działaniem.
2. **Nigdy nie zatwierdzasz MR/PR** i nigdy nie mergujesz bez werdyktu `ready` policzonego w tym samym ticku, z blokadą na ten SHA.
3. **Nigdy `--force`, rebase ani `git reset`** na branchu MR. Konflikt rozwiązuje merge brancha docelowego.
4. **Checkout użytkownika zostaje nietknięty.** Poprawki tylko w `$STATE_DIR/wt-*`, usuwanym przez `git worktree remove` po pushu.
5. **Po merge czekasz na zielone CI brancha docelowego**, zanim weźmiesz następny element.
6. **Subagent zawsze z jawnym modelem**: `sonnet` do poprawki CI, `opus` do rozwiązania konfliktu.
7. **Język raportu: polski.** Komunikaty commitów: konwencja repozytorium.
