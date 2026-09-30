---
name: setup
description: "Checklista konfiguracji projektu Monolynx: slug, strona wiki toolchain, LLM Wiki, graf i mutacje w CI, flagi MONOLYNX_*. Pokazuje stan OK/BRAK każdego punktu i za zgodą uruchamia po kolei skille naprawcze. Użyj przy pierwszym starcie pracy w projekcie albo gdy podejrzewasz, że czegoś brakuje w konfiguracji."
user-invocable: true
argument-hint: ""
---

# SETUP - checklista konfiguracji projektu

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Jesteś **audytorem konfiguracji**. Sprawdzasz 6 punktów, pokazujesz tabelę stanu
i, tylko za wyraźną zgodą, wołasz po kolei skille naprawcze. Sam nic nie
zapisujesz - jedyne zapisy w tym przebiegu to te, które wykonają wywołane za
zgodą skille.

Uruchamiany **w każdym momencie pracy** z projektem, nie tylko raz - to
diagnostyka stanu, nie jednorazowy setup (tym jest każdy ze skilli, do których
odsyła).

---

## KROK 0: Tryb sesji w tle

```bash
echo "SPRINT_RUN=${MONOLYNX_SPRINT_RUN:-0}"
```

`MONOLYNX_SPRINT_RUN=1` znaczy, że nikt nie przeczyta pytania - sesja w tle
dyspozytora nie ma człowieka po drugiej stronie. W tym trybie wykonaj KROK 1-6
(zbiór stanu) i KROK 7 (tabela), ale pomiń KROK 8-9 (pytanie i wywołania
skilli) w całości. Zakończ na samej tabeli.

---

## KROK 1: Slug projektu

Slug jest wejściem do punktów 2 i 3. Ustal go zgodnie z kontraktem runtime: jawny argument użytkownika > `MONOLYNX_PROJECT_SLUG` w środowisku > jawnie odczytana konfiguracja klienta lub `.env`. Czytaj tylko ten klucz jako dane, bez `source` i bez wypisywania sekretów.

Znaleziono: **OK**, zapisz wartość i źródło; zgłoś rozbieżne źródła, stosując powyższe pierwszeństwo. Brak: **BRAK**, poproś o slug; punkty 2 i 3 oznacz „nie sprawdzono - brak sluga”, a niezależne punkty checklisty sprawdź dalej. Nie zgaduj projektu.

---

## KROK 2: Strona wiki `toolchain`

Pomiń, gdy punkt 1 to BRAK.

W katalogu dostępnych narzędzi znajdź funkcje Monolynx potrzebne poniżej i sprawdź ich schematy. Discovery jest opcjonalne, jeśli narzędzia są już dostępne.

<!-- runtime:claude-code -->
Opcjonalny wariant discovery w Claude Code:
```
ToolSearch(query="+monolynx wiki page list get config", max_results=10)
```
<!-- /runtime:claude-code -->

```
mcp__monolynx__list_wiki_pages(project_slug="<PROJECT_SLUG>")
```

Wynik to płaska lista Title/Updated bez pola `slug` - znajdź wiersz o
`Title` równym dosłownie `Toolchain` (wielkość liter ma znaczenie, bo od
tytułu zależy wygenerowany slug). Znaleziono kandydata - potwierdź przez
`get_wiki_page`, który zwraca faktyczne pole `slug`:

```
mcp__monolynx__get_wiki_page(project_slug="<PROJECT_SLUG>", page_id="<id>")
```

- **Brak wiersza `Toolchain` albo `slug` w odpowiedzi różny od `toolchain`**
  - stan **BRAK**. Skill naprawczy: `/monolynx:project-toolchain`.
- **Strona znaleziona** - sprawdź w polu `content` obecność czterech
  nagłówków: `## Lint`, `## Test`, `## Worktree`, `## Mutacje` (dosłowne
  dopasowanie, tak jak je zapisuje `/monolynx:project-toolchain`). Sekcja
  `## Mutacje` z treścią `narzedzie: brak` jest stanem **OK** - to świadoma
  decyzja właściciela repo, liczy się sama obecność sekcji, nie jej treść.
  - **Wszystkie cztery obecne** - stan **OK**.
  - **Brak któregokolwiek** (najczęściej `## Worktree` albo `## Mutacje` na
    stronach zapisanych starszą wersją skilla) - stan **BRAK**, w tabeli
    wymień brakujące nagłówki wprost. Skill naprawczy:
    `/monolynx:project-toolchain`.

Błąd wywołania MCP (np. projekt bez członkostwa) - stan **BŁĄD: <treść
błędu>**, bez skilla naprawczego i bez opcji w KROK 8 (proponowanie zapisu
strony po błędzie odczytu grozi duplikatem). Reszta checklisty biegnie dalej.

---

## KROK 3: LLM Wiki

Pomiń, gdy punkt 1 to BRAK.

```
mcp__monolynx__get_wiki_config(project_slug="<PROJECT_SLUG>")
```

Działa niezależnie od tego, czy LLM Wiki jest włączone - to jest właśnie
sprawdzane pole `wiki_llm_enabled`.

- **`wiki_llm_enabled: true`** - stan **OK**.
- **`wiki_llm_enabled: false`** - stan **BRAK**. Skill naprawczy:
  `/monolynx:wiki-init`.
- **Błąd wywołania MCP** - stan **BŁĄD: <treść błędu>**, bez skilla
  naprawczego i bez opcji w KROK 8. Reszta checklisty biegnie dalej.

---

## KROK 4: Graf zależności kodu w CI

Wykryj plik CI - jedno sprawdzenie na kandydata, bez łączenia w jedną
komendę:

```bash
ls .gitlab-ci.yml
```

```bash
ls .github/workflows/
```

```bash
ls bitbucket-pipelines.yml
```

```bash
ls Jenkinsfile
```

Sprawdź obecność skryptu synchronizacji:

```bash
ls cicd/sync_graph.py
```

Gdy plik CI istnieje, sprawdź w nim odwołanie do skryptu (dopasuj komendę do
znalezionego pliku, np. dla katalogu workflows przejrzyj każdy plik `.yml`
osobno):

```bash
grep -l "sync_graph.py" .gitlab-ci.yml
```

- **`cicd/sync_graph.py` istnieje ORAZ znaleziony plik CI go wywołuje** -
  stan **OK**.
- **Którykolwiek element brakuje** (brak skryptu, brak wpisu w CI, albo brak
  jakiegokolwiek pliku CI w repo) - stan **BRAK**. Skill naprawczy:
  `/monolynx:create-graph-ci-script`. W uwagach pod tabelą (KROK 7) wspomnij,
  że projekt bez CI (albo z CI, którego nikt nie chce ruszać) może zamiast
  tego używać `/monolynx:graph-sync` (synchronizacja lokalna, bez zapisu w
  plikach CI) - to alternatywa opisowa, nie osobna pozycja do wyboru w
  KROK 8.

---

## KROK 5: Testy mutacyjne w CI

```bash
ls cicd/mutation_report.py
```

```bash
ls cicd/mutation_baseline.py
```

Sprawdź w wykrytym pliku CI (z KROK 4) osobny etap mutacyjny:

```bash
grep -l "mutation_report.py" .gitlab-ci.yml
```

- **Oba skrypty istnieją ORAZ plik CI ma etap odwołujący się do
  `mutation_report.py`** - stan **OK**.
- **Którykolwiek element brakuje, a strona `toolchain` (punkt 2) ma w
  `## Mutacje` `narzedzie: brak` albo `komenda diffowa: brak`** - stan
  **BRAK (bez opcji w KROK 8)**: `/monolynx:create-mutation-ci-script` na tym
  warunku kończy się od razu bez generowania czegokolwiek, więc proponowanie
  go byłoby bezużyteczną akcją. W uwagach pod tabelą (KROK 7) napisz, że
  najpierw trzeba wskazać narzędzie mutacyjne (i komendę diffową) przez
  `/monolynx:project-toolchain`, i że sam krok mutacji w CI jest opcjonalny.
- **Którykolwiek element brakuje, a strona `toolchain` ma ustawione narzędzie
  i komendę diffową** - stan **BRAK**. Skill naprawczy:
  `/monolynx:create-mutation-ci-script` (kolejność wywołań w KROK 9).

---

## KROK 6: Flagi `MONOLYNX_*`

Bez skilla naprawczego - to zawsze decyzja człowieka, nigdy propozycja
włączenia. Sprawdź flagi w trzech źródłach: env procesu, `.claude/settings.json`
(pole `env`), `.claude/settings.local.json` (pole `env`).

Dla env procesu nie da się wylistować kluczy bez `$(...)`/`env | grep`, więc
sprawdzasz jawną listę znanych flag - jeden blok `echo` wypisujący wszystkie
19 flag naraz (pusta wartość po `:-` znaczy nieustawiona w env):

```bash
echo "MONOLYNX_PROJECT_SLUG=${MONOLYNX_PROJECT_SLUG:-}"
echo "MONOLYNX_BRANCH_MODE=${MONOLYNX_BRANCH_MODE:-}"
echo "MONOLYNX_AUTOTEST=${MONOLYNX_AUTOTEST:-}"
echo "MONOLYNX_AUTOCOMMIT=${MONOLYNX_AUTOCOMMIT:-}"
echo "MONOLYNX_AUTOPUSH=${MONOLYNX_AUTOPUSH:-}"
echo "MONOLYNX_CONTRACT=${MONOLYNX_CONTRACT:-}"
echo "MONOLYNX_SPRINT_RUN=${MONOLYNX_SPRINT_RUN:-}"
echo "MONOLYNX_AUTOMR=${MONOLYNX_AUTOMR:-}"
echo "MONOLYNX_SPRINT_RUN_MODE=${MONOLYNX_SPRINT_RUN_MODE:-}"
echo "MONOLYNX_SPRINT_PERMISSION_MODE=${MONOLYNX_SPRINT_PERMISSION_MODE:-}"
echo "MONOLYNX_SPRINT_MODEL=${MONOLYNX_SPRINT_MODEL:-}"
echo "MONOLYNX_AUTOMERGE=${MONOLYNX_AUTOMERGE:-}"
echo "MONOLYNX_MERGE_METHOD=${MONOLYNX_MERGE_METHOD:-}"
echo "MONOLYNX_SPRINT_TICK_MODEL=${MONOLYNX_SPRINT_TICK_MODEL:-}"
echo "MONOLYNX_BRIEF_GUARD=${MONOLYNX_BRIEF_GUARD:-}"
echo "MONOLYNX_MUTATION_BUDGET=${MONOLYNX_MUTATION_BUDGET:-}"
echo "MONOLYNX_SPRINT_PARALLEL=${MONOLYNX_SPRINT_PARALLEL:-}"
echo "MONOLYNX_BASE_BRANCH=${MONOLYNX_BASE_BRANCH:-}"
echo "MONOLYNX_MR_TARGET=${MONOLYNX_MR_TARGET:-}"
```

```bash
git ls-files .claude/settings.json
```

Pusty wynik znaczy, że `.claude/settings.json` nie jest śledzony (albo nie
istnieje) - dowolna flaga ustawiona tam ma tę samą wadę co flaga w
`settings.local.json`.

Odczytaj konfigurację klienta i, gdy diagnozujesz sesje Claude Code, oba powyższe pliki jako dane. Raportuj wyłącznie jawne flagi z listy `echo` powyżej; nigdy nie wypisuj całej konfiguracji ani sekretów. Nie wykonuj `source`.

Dla każdej flagi znalezionej w co najmniej jednym źródle zbuduj wiersz
`flaga | wartość | źródło | uwaga`:

- **Źródło `.claude/settings.json` i plik śledzony** (`git ls-files` zwrócił
  ścieżkę) - uwaga pusta, flaga działa także dla sesji `--bg` i worktree.
- **Źródło `.claude/settings.local.json`, LUB `.claude/settings.json`
  nieobecny w `git ls-files`** - uwaga: "niewidoczna dla worktree (sesje
  `sprint-run`, `claude --worktree`)". WHY: worktree tworzony przez
  `claude --bg --worktree` i `claude --worktree` zawiera wyłącznie pliki
  śledzone przez gita, więc `settings.local.json` (zawsze ignorowany) i
  jakikolwiek nieskomitowany `settings.json` nigdy tam nie dotrze.
- **Źródło: env procesu** - uwaga: "widoczna tylko dla procesów startowanych z
  tego środowiska - sesja `--bg` odpalona z innego terminala, crona albo
  skryptu bez eksportu jej nie dostanie".
- **`MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` obie ustawione i różne** (po
  obcięciu białych znaków, porównaj efektywne wartości: env procesu, potem
  pliki settings) - uwaga w wierszu obu flag: "ostrzeżenie: różne
  `MONOLYNX_BASE_BRANCH` i `MONOLYNX_MR_TARGET` - preflight `sprint-run`
  zwróci `ERROR`, a `config-check` w `mr-queue` zatrzyma tick; obie wskazują
  ten sam branch, wystarczy jedna (zwykle `MONOLYNX_MR_TARGET`, bo tylko ona
  ustala też cel MR w `work` / `work-simple`)". Nie poprawiasz żadnej z nich -
  wybór zostaje przy człowieku. Jedna z nich albo obie równe - bez uwagi.

Flagi nieznalezionej w żadnym źródle nie umieszczaj w wykazie - domyślna
wartość jest już udokumentowana w `plugin/README.md`, powielanie jej tutaj nie
niesie informacji.

<!-- runtime:claude-code -->
**Plugin z checkoutu (dogfooding).** Osobne sprawdzenie, poza listą flag:

```bash
echo "CLAUDE_CODE_PLUGIN_DIRS=${CLAUDE_CODE_PLUGIN_DIRS:-}"
```

Niepusta wartość znaczy, że skille tej sesji, a także sesji w tle
startowanych z jej środowiska, pochodzą z katalogu pluginu w checkoucie, nie
z zainstalowanej wersji. To nie jest flaga `MONOLYNX_*`: nie trafia do wykazu
flag ani do licznika `N`, dopisujesz ją wyłącznie do stanu wiersza 6 (patrz
niżej). Nigdy BRAK i nigdy propozycja zmiany - to świadomy wybór człowieka
pracującego nad samym pluginem. Zmienna jest nieudokumentowana w `claude
--help` (sprawdzona na Claude Code 2.1.284). Pusta wartość nie wyklucza
pluginu z checkoutu: sesja uruchomiona z flagą `--plugin-dir` nie ustawia
żadnej zmiennej.

Gdy człowiek pyta, jak przełączyć dyspozytora na plugin z brancha sprintu i
jak wrócić, podaj te komendy (dotyczą repozytorium pluginu; `/sciezka/do/repo`
to główny checkout na branchu sprintu, nie worktree ticketu):

- pętla skryptowa: `CLAUDE_CODE_PLUGIN_DIRS=/sciezka/do/repo/plugin plugin/scripts/sprint_run.sh --interval 15m --slots 2`
- dyspozytor interaktywny: `CLAUDE_CODE_PLUGIN_DIRS=/sciezka/do/repo/plugin claude`, a w tej sesji `/loop 15m /monolynx:sprint-run`
- pojedyncza sesja: `claude --plugin-dir /sciezka/do/repo/plugin` (flaga nie przechodzi do sesji `claude --bg` startowanych przez dyspozytora)
- sprawdzenie: `CLAUDE_CODE_PLUGIN_DIRS=/sciezka/do/repo/plugin claude plugin list --json` pokazuje wpis `monolynx@inline` z `installPath` checkoutu
- powrót: `unset CLAUDE_CODE_PLUGIN_DIRS` i nowa sesja bez `--plugin-dir`

Kiedy tego nie robić (czerwone CI brancha sprintu, zmiany hooków albo skryptów
pętli w nocnym batchu, obce projekty) i czego nie sprawdzono: `plugin/README.md`,
sekcja „Praca nad pluginem: dyspozytor na pluginie z brancha sprintu”.
<!-- /runtime:claude-code -->

W głównej tabeli 6 punktów wiersz "Flagi `MONOLYNX_*`" dostaje stan
informacyjny, nigdy BRAK - to nie jest usterka:

- **Wykaz niepusty** - `INFO (N jawnie ustawionych)`, gdzie `N` to liczba
  flag w wykazie. Gdy co najmniej jedna z nich ma uwagę "niewidoczna dla
  worktree", dopisz to do stanu, np. `INFO (3 jawnie ustawionych, 2
  niewidoczne dla worktree)`. Przy rozbieżnych `MONOLYNX_BASE_BRANCH` i
  `MONOLYNX_MR_TARGET` dopisz `ostrzeżenie: różne BASE_BRANCH i MR_TARGET`,
  np. `INFO (2 jawnie ustawionych, ostrzeżenie: różne BASE_BRANCH i
  MR_TARGET)` - stan zostaje informacyjny, bo punkt 6 nie ma skilla
  naprawczego.
- **Wykaz pusty** - `INFO (wszystkie domyślne)`.
- **`CLAUDE_CODE_PLUGIN_DIRS` niepusta** (tylko Claude Code, patrz wyżej) -
  dopisz do stanu `plugin z checkoutu: <ścieżka>`, np. `INFO (wszystkie
  domyślne, plugin z checkoutu: /sciezka/do/repo/plugin)`. Stan zostaje
  informacyjny.

---

## KROK 7: Tabela wynikowa

Wypisz dokładnie sześć wierszy:

| Punkt | Stan | Skill naprawczy |
|---|---|---|
| 1. Slug projektu | OK (`<slug>`, źródło: ...) / BRAK | - |
| 2. Strona wiki `toolchain` | OK / BRAK (brakuje: ...) / nie sprawdzono / BŁĄD: <treść> | `/monolynx:project-toolchain` |
| 3. LLM Wiki (`wiki_llm_enabled`) | OK / BRAK / nie sprawdzono / BŁĄD: <treść> | `/monolynx:wiki-init` |
| 4. Graf zależności w CI | OK / BRAK | `/monolynx:create-graph-ci-script` |
| 5. Testy mutacyjne w CI | OK / BRAK / BRAK (bez opcji w KROK 8) | `/monolynx:create-mutation-ci-script` |
| 6. Flagi `MONOLYNX_*` | INFO (N jawnie ustawionych) / INFO (wszystkie domyślne), opcjonalnie z `plugin z checkoutu: <ścieżka>` | - |

Zaraz pod tabelą, przed wykazem flag: uwagi z KROK 4 (alternatywa
`/monolynx:graph-sync` przy stanie BRAK) i z KROK 5 (wskazanie narzędzia
mutacyjnego przez `/monolynx:project-toolchain` przy stanie BRAK bez opcji w
KROK 8) - jedna uwaga na punkt, tylko gdy dotyczy, w kolejności punktów.

Dalej wykaz flag z KROK 6 (`flaga | wartość | źródło | uwaga`), albo jedno
zdanie "Brak jawnie ustawionych flag `MONOLYNX_*`", gdy wykaz jest pusty.

W trybie `MONOLYNX_SPRINT_RUN=1` (KROK 0) zakończ odpowiedź na tej tabeli.

---

## KROK 8: Zgoda na naprawę

Wszystkie punkty 2-5 mają stan OK, "nie sprawdzono" albo **BRAK (bez opcji w
KROK 8)** - napisz jedno zdanie ("Konfiguracja kompletna, nic do naprawy."
albo "Brak sluga blokuje sprawdzenie punktów 2-3 - uzupełnij go i uruchom
`/monolynx:setup` ponownie.") i zakończ. Punkty 1 i 6 nigdy nie trafiają do
pytania - nie mają skilla naprawczego. Punkt w stanie **BŁĄD** też nie trafia
do pytania (patrz KROK 2 i KROK 3) - błąd wywołania MCP nie jest brakiem
konfiguracji do naprawienia skillem.

W przeciwnym razie zapytaj przez narzędzie dostępne w bieżącym trybie albo wiadomość o wybór punktów. Przedstaw opcje
wyłącznie dla punktów spośród 2-5, które mają stan **BRAK** - każdy z nich ma
skill naprawczy, więc opcji jest najwyżej tyle, ile punktów BRAK, maksymalnie
4 (tyle ma cała checklista punktów ze skillem naprawczym). Punkt 5 w stanie
**BRAK (bez opcji w KROK 8)** (patrz KROK 5) nie trafia do pytania mimo
posiadania skilla naprawczego - proponowanie go byłoby bezużyteczną akcją.
Każda opcja niesie nazwę skilla i jednozdaniowy opis, co naprawi.

Brak zaznaczenia albo odmowa - nie wołaj żadnego skilla. Zakończ jednym
zdaniem, które punkty zostały nienaprawione, i że `/monolynx:setup` można
uruchomić ponownie w dowolnym momencie.

**Brak narzędzia pytań** - zadaj to samo pytanie zwykłą wiadomością i poczekaj na jawną zgodę. Brak odpowiedzi lub timeout nie są wyborem. W Codex znajdź wybrane skille w katalogu klienta; jeśli brak natywnego wywołania, odczytaj ich dostarczone instrukcje. Brak skilla zgłoś i zatrzymaj zależną naprawę.

---

## KROK 9: Wywołanie wybranych skilli

Niezależnie od kolejności zaznaczenia w KROK 8, wołaj wybrane skille w tej
stałej kolejności - `create-mutation-ci-script` wymaga sekcji `## Mutacje` ze
strony `toolchain`, którą dopiero co mógł zapisać `project-toolchain`:

1. skill `project-toolchain` - gdy wybrany punkt 2.
2. skill `wiki-init` - gdy wybrany punkt 3.
3. skill `create-graph-ci-script` - gdy wybrany punkt 4.
4. skill `create-mutation-ci-script` - gdy wybrany punkt 5.

Uruchamiaj przez natywny mechanizm klienta albo odczyt dostępnego `SKILL.md`. Wołaj je pojedynczo, jeden po drugim, nie równolegle - każdy z nich może sam
pytać o potwierdzenie, i te pytania nie mogą się nakładać.

Po ostatnim wywołanym skillu zaproponuj człowiekowi ponowne uruchomienie
`/monolynx:setup`, żeby zobaczyć odświeżony stan wszystkich sześciu punktów.

---

## WAŻNE ZASADY

1. **Read-only, zawsze.** Jedyne zapisy w tym przebiegu pochodzą z wywołanych
   za zgodą skilli - `setup` sam nie tworzy ani nie edytuje niczego.
2. **Bez zgody - bez wywołania.** Odmowa albo brak wyboru w KROK 8 kończy
   przebieg bez ani jednego wywołania zależnego skilla.
3. **Flagi autonomii tylko raportowane.** Punkt 6 nigdy nie proponuje włączenia
   `MONOLYNX_AUTOPUSH`, `MONOLYNX_AUTOMERGE` i podobnych - to decyzja
   człowieka, nie stan do naprawienia.
4. **Sesja w tle kończy się na tabeli.** `MONOLYNX_SPRINT_RUN=1` pomija KROK
   8-9 w całości - nikt nie odpowie na pytanie.
5. **Język raportu: polski.**
