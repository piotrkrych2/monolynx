---
name: create-mutation-ci-script
description: "Wygeneruj nieblokujacy krok CI z testami mutacyjnymi dla biezacego projektu: wykrywa system CI (GitLab/GitHub/Bitbucket/Jenkins), czyta komende diffowa i format raportu z sekcji Mutacje strony wiki toolchain, kopiuje do cicd/ skrypty mutation_report.py i mutation_baseline.py, dodaje osobny etap mutation uruchamiany tylko na MR/PR, publikuje raport jako artefakt i porownuje wynik z zapisanym baseline (zapadka: baseline tylko rosnie, spadek jest raportowany widocznie i nigdy nie oblewa builda). Uzyj raz per projekt, ktory ma juz wykryte narzedzie mutacyjne i chce widziec trend przy kazdym MR."
user-invocable: true
allowed-tools: mcp__monolynx__list_wiki_pages, mcp__monolynx__get_wiki_page, AskUserQuestion, Bash, Read, Write, Edit, Glob, Grep
---

# Nieblokujacy krok CI z testami mutacyjnymi

Zasoby tego skilla: ustal absolutny katalog otrzymanego pliku `SKILL.md`.
Odnośniki do dołączonych `scripts/`, `references/` i pomocniczych plików
Markdown rozwiązuj względem tego katalogu, nie katalogu roboczego projektu.
Ścieżki plików tworzonych w projekcie pozostają względem projektu. Przed komendami
z `MONOLYNX_SKILL_DIR` przypisz tej lokalnej zmiennej powłoki ustaloną
literalną ścieżkę katalogu skilla; nie oczekuj jej w środowisku systemowym.

Twoim zadaniem jest wygenerowanie w **biezacym projekcie** kroku CI, ktory przy kazdym MR/PR
uruchamia przebieg mutacyjny ograniczony do zmiany, publikuje raport jako artefakt i porownuje
wynik z zapisanym baseline.

**Podzial rol:**
- **Narzedzie mutacyjne** (mutmut, Stryker, PIT, gremlins, Infection, cargo-mutants, ...) -
  instaluje je wlasciciel repo na runnerze albo dostarcza w obrazie. Skill go NIE instaluje i krok
  CI go NIE instaluje.
- **`cicd/mutation_report.py`** - normalizacja raportu narzedzia do jednej linii JSON
  (`score`, `total`, `killed`, `survived`, `survivors[]`). Kopia skryptu z pluginu, stdlib only.
- **`cicd/mutation_baseline.py`** - zapadka: porownanie wyniku z `cicd/mutation-baseline.json`
  i zapis nowego baseline, gdy wynik urosl. Kopia skryptu z pluginu, stdlib only.
- **CI** - osobny, nieblokujacy etap `mutation`, uruchamiany wylacznie na MR/PR.
- **Strona wiki `toolchain` w Monolynx** - jedyne zrodlo komend i sciezek raportu (sekcja
  `## Mutacje`, zapisywana przez `/monolynx:project-toolchain`).

**Zasada kluczowa: CI NIE instaluje narzedzia mutacyjnego.** Instalacja na runnerze to
jednorazowy setup wlasciciela projektu (ten sam precedens co graphify w
`/monolynx:create-graph-ci-script`). Krok tylko UZYWA narzedzia; gdy go brak - konczy sie
czytelnym komunikatem i sukcesem, a build projektu idzie dalej.

**Mutation score to trend, nie bramka.** Krok nigdy nie oblewa builda, nie ma progu procentowego
i nie blokuje merge'a.

---

## KROK 0: Resolucja konfiguracji

### 0a. Slug projektu

Pierwsza znaleziona wartosc wygrywa:

1. zmienna `MONOLYNX_PROJECT_SLUG` z `.env` / srodowiska,
2. `user_config.project_slug` pluginu,
3. zapytaj uzytkownika (AskUserQuestion).

```bash
echo "${MONOLYNX_PROJECT_SLUG:-(nie ustawiono)}"
```

W dokumentacji i przykladach ponizej uzywaj placeholdera `<PROJECT_SLUG>` - NIGDY konkretnego
sluga.

### 0b. Odczytaj sekcje `## Mutacje` strony wiki `toolchain`

```
mcp__monolynx__list_wiki_pages(project_slug='<PROJECT_SLUG>')
```

Z wyniku wez strone o tytule `Toolchain` (slug powstaje z tytulu), pobierz jej tresc i sprawdz,
ze pole `slug` w odpowiedzi rowna sie dokladnie `toolchain`:

```
mcp__monolynx__get_wiki_page(project_slug='<PROJECT_SLUG>', page_id='<id strony>')
```

Nie uzywaj `search_wiki` - wykrywanie i odczyt musza isc ta sama droga, ktora czyta
`/monolynx:project-toolchain`. Slabe trafienie semantyczne kazaloby uznac, ze strony nie ma.

Z sekcji `## Mutacje` wyciagnij cztery pola:

| Pole strony | Do czego sluzy |
|---|---|
| `narzedzie` | wybor wiersza w tabeli guardow (KROK 3) |
| `komenda diffowa` | przebieg ograniczony do zmiany, trzon kroku CI |
| `raport` | sciezka pliku raportu + format w nawiasie (`mte-json`, `pit-xml`, `mutmut`, `cargo-mutants`) |
| `moduly rdzenia` | sciezki na pelny przebieg lokalny (KROK 5) |

**Warunek stopu**: brak strony `toolchain`, `narzedzie: brak` albo `komenda diffowa: brak`.
Wypisz wtedy uzytkownikowi:

```
Projekt nie ma zapisanej komendy mutacyjnej. Uruchom /monolynx:project-toolchain
(krok wykrywania mutacji), potem wroc tutaj.
```

i **zakoncz skill bez generowania czegokolwiek**. Nie wpisuj komendy z pamieci i nie edytuj
strony `toolchain` - to kontrakt `/monolynx:project-toolchain`.

---

## KROK 1: Wykryj system CI

Sprawdz w korzeniu repo (w tej kolejnosci). To ta sama tabela wykrywania CI, ktorej uzywa skill
`/monolynx:create-graph-ci-script`:

| Plik / katalog | System | Wariant w KROK 3 |
|---|---|---|
| `.gitlab-ci.yml` | GitLab CI | wariant A |
| `.github/workflows/` | GitHub Actions | wariant B |
| `bitbucket-pipelines.yml` | Bitbucket Pipelines | wariant C |
| `Jenkinsfile` | Jenkins | wariant D |
| zaden z powyzszych | brak CI | pomin KROK 3, przejdz do KROK 5 (uruchamianie reczne) |

---

## KROK 2: Skopiuj skrypty do `cicd/`

Utworz katalog `cicd/` (jesli go nie ma) i skopiuj do niego dwa skrypty z lokalnego katalogu `scripts/` tego skilla:

```bash
mkdir -p cicd
cp "${MONOLYNX_SKILL_DIR}/scripts/mutation_report.py" cicd/mutation_report.py
cp "${MONOLYNX_SKILL_DIR}/scripts/mutation_baseline.py" cicd/mutation_baseline.py
```

Kopia juz istnieje -> zapytaj (AskUserQuestion), czy nadpisac. **Kopii nie edytuj recznie** -
aktualizacja idzie przez ponowne uruchomienie tego skilla, inaczej lokalna poprawka wyparuje przy
nastepnym kopiowaniu.

**Brak któregoś dołączonego skryptu** oznacza niekompletną instalację
pluginu lub skilla standalone. Wypisz użytkownikowi, żeby
pobral je z repozytorium Monolynx ze sciezek `plugin/scripts/mutation_report.py` i
`plugin/scripts/mutation_baseline.py`, i **kontynuuj** generowanie kroku CI.

### Kontrakt `cicd/mutation_report.py`

```bash
python3 cicd/mutation_report.py --report <RAPORT> --format <FORMAT>
```

Na stdout jedna linia JSON: `{"score": ..., "total": ..., "killed": ..., "survived": ...,
"survivors": [...]}`. Kody wyjscia: `0` sukces, `2` blad wejscia (brak lub uszkodzony raport),
`64` blad uzycia (zly `--format`).

### Kontrakt `cicd/mutation_baseline.py`

```bash
python3 cicd/mutation_baseline.py --summary <plik|-> --baseline <plik> \
  [--scope full|diff] [--commit <sha>] [--tool <nazwa>] [--dry-run]
```

- `--scope` domyslnie `diff`; `--summary -` czyta stdin; `--dry-run` nigdy nie zapisuje.
- Kody wyjscia: **`0` dla KAZDEGO statusu, takze `down`**; `2` blad wejscia (brak lub niepoprawny
  summary, uszkodzony baseline - nigdy nie jest cicho nadpisywany, nieudany zapis); `64` blad
  uzycia.
- Statusy: `first` (brak baseline, zapis), `up` (wynik wyzszy, zapis), `same`, `down` (UWAGA, bez
  zapisu), `no-mutants` (`score` null albo `total` 0, bez zapisu), `scope-mismatch` (bez zapisu).
- Ostatnia linia stdout to **zamrozony kontrakt** do odczytu maszynowego:

```
MUTATION-BASELINE: status=<s> score=<x|none> baseline=<y|none> delta=<z|none> survived=<n> total=<t> scope=<scope>
```

### Pliki

`cicd/mutation-baseline.json` jest **commitowany do repo**; `mutation-summary.json` i `<RAPORT>`
to pliki robocze CI (mozesz zaproponowac dopisanie ich do `.gitignore`).

```json
{"score": 71.4, "survived": 3, "total": 10, "scope": "diff", "measured_at": "2026-09-13", "commit": "<sha|null>", "tool": "<narzedzie|null>"}
```

---

## KROK 3: Dodaj krok CI (wariant wg KROK 1)

Sekwencja jest wspolna dla wszystkich czterech wariantow i **zamrozona**:

1. guard obecnosci narzedzia (brak -> komunikat i `exit 0`),
2. pobranie galezi bazowej (pelna historia, nie shallow),
3. `<KOMENDA_DIFFOWA> || true`,
4. normalizacja raportu przez `cicd/mutation_report.py`,
5. zapadka przez `cicd/mutation_baseline.py --scope diff --commit <SHA>`,
6. artefakty publikowane zawsze,
7. uruchomienie tylko na MR/PR,
8. osobny etap `mutation`,
9. krok nie instaluje narzedzia.

### Podstawienia (obowiazkowe przed zapisem pliku CI)

| Placeholder | Zrodlo wartosci |
|---|---|
| `<KOMENDA_DIFFOWA>` | pole `komenda diffowa` ze strony `toolchain`, z podmieniona galezia bazowa |
| `<RAPORT>` | sciezka z pola `raport` |
| `<FORMAT>` | format z nawiasu w polu `raport` |
| `<GUARD_NARZEDZIA>` | wiersz tabeli guardow dla pola `narzedzie` |
| `<OBRAZ_LUB_RUNNER_Z_NARZEDZIEM>` | obraz Dockera z narzedziem ALBO runner/agent przygotowany przez wlasciciela repo |
| SHA commita, galaz bazowa | tabela zmiennych CI |

**Podmiana galezi bazowej jest obowiazkowa.** `komenda diffowa` ze strony wiki czesto ma zaszyta
galaz (np. `--since main`). Zamien ja na zmienna CI wlasciwa dla wykrytego systemu - inaczej
kazdy MR liczy diff wzgledem tej samej galezi zamiast wzgledem swojej galezi docelowej.

**Wyjatek mutmut**: mutmut nie zapisuje raportu do pliku sam. Dla `narzedzie: mutmut` dopisz po
linii `<KOMENDA_DIFFOWA> || true` jedna linie:

```bash
mutmut results --all true > <RAPORT>
```

Pozostale narzedzia z tabeli guardow generuja plik raportu same - dla nich tej linii NIE dodawaj.

### Wariant A: GitLab CI (`.gitlab-ci.yml`)

```yaml
mutation:
  stage: mutation
  image: <OBRAZ_LUB_RUNNER_Z_NARZEDZIEM>
  variables:
    GIT_DEPTH: "0"
  allow_failure: true
  script:
    - <GUARD_NARZEDZIA> || { echo "narzedzie mutacyjne nie zainstalowane na runnerze - krok mutacji pominiety (instalacja to setup wlasciciela projektu, patrz strona wiki toolchain)"; exit 0; }
    - git fetch origin $CI_MERGE_REQUEST_TARGET_BRANCH_NAME
    - <KOMENDA_DIFFOWA> || true
    - python3 cicd/mutation_report.py --report <RAPORT> --format <FORMAT> > mutation-summary.json
    - python3 cicd/mutation_baseline.py --summary mutation-summary.json --baseline cicd/mutation-baseline.json --scope diff --commit $CI_COMMIT_SHA || true
  artifacts:
    when: always
    paths:
      - <RAPORT>
      - mutation-summary.json
      - cicd/mutation-baseline.json
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

Uwagi:

- **PULAPKA**: `stages:` na gorze pliku musi zawierac wpis `mutation`, inaczej GitLab odrzuci caly
  plik z bledem walidacji. Dopisz `mutation` do istniejacej listy `stages:`, nie tylko sam job.
- `GIT_DEPTH: "0"` wylacza domyslny shallow clone runnera - bez tego `<KOMENDA_DIFFOWA>` nie
  znajdzie wspolnego przodka z galezia docelowa.
- Guard moze tu zostac osobna pozycja `script:`, bo GitLab sklada cala liste w jeden skrypt
  powloki - `exit 0` konczy caly job. W wariantach B, C i D tak nie jest, stad tam jeden blok.
- `CI_MERGE_REQUEST_TARGET_BRANCH_NAME` istnieje tylko w pipeline'ach `merge_request_event`, czyli
  w jedynym trybie, w ktorym ten job startuje (`rules:`).
- `allow_failure: true` puszcza pipeline dalej mimo dowolnego kodu wyjscia, ale ostatnia komenda
  i tak ma `|| true`, zeby job byl zielony zamiast "allowed to fail".
- `image:` jest opcjonalny: narzedzie na samym runnerze (self-hosted) znaczy, ze job moze
  odziedziczyc domyslny obraz projektu. `artifacts: when: always` publikuje pliki takze przy
  niepowodzeniu kroku.

### Wariant B: GitHub Actions (`.github/workflows/mutation.yml`)

```yaml
name: mutation

on:
  pull_request:

jobs:
  mutation:
    runs-on: <OBRAZ_LUB_RUNNER_Z_NARZEDZIEM>
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: przebieg mutacyjny i zapadka
        run: |
          <GUARD_NARZEDZIA> || { echo "narzedzie mutacyjne nie zainstalowane na runnerze - krok mutacji pominiety (instalacja to setup wlasciciela projektu, patrz strona wiki toolchain)"; exit 0; }
          git fetch origin ${{ github.base_ref }}
          <KOMENDA_DIFFOWA> || true
          python3 cicd/mutation_report.py --report <RAPORT> --format <FORMAT> > mutation-summary.json
          python3 cicd/mutation_baseline.py --summary mutation-summary.json --baseline cicd/mutation-baseline.json --scope diff --commit ${{ github.sha }} || true

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: mutation-report
          path: |
            <RAPORT>
            mutation-summary.json
            cicd/mutation-baseline.json
```

Uwagi:

- **PULAPKA**: kazdy `run:` to OSOBNA powloka, wiec `exit 0` w guardzie nie przerwalby kolejnych
  krokow - guard konczylby tylko sam siebie, a przebieg mutacyjny ruszylby mimo braku narzedzia.
  Dlatego guard, `git fetch`, przebieg, normalizacja i zapadka sa w JEDNYM wieloliniowym `run: |`.
  `checkout` i `upload-artifact` zostaja osobnymi krokami (to akcje, nie komendy powloki).
- Osobny plik workflow zamiast joba w istniejacym: GitHub Actions nie ma odpowiednika `stages:`
  z GitLaba, wiec izolacja idzie przez osobny plik. `on: pull_request` bez `branches:` to juz jest
  "tylko na MR/PR".
- `fetch-depth: 0` daje pelna historie; domyslny plytki klon nie ma commitow galezi bazowej.
  W evencie `pull_request` `github.sha` to SHA tymczasowego merge commita, nie head commita galezi
  PR - do identyfikacji przebiegu w zapadce to wystarczy.
- `continue-on-error: true` jest siatka bezpieczenstwa na blad w `checkout` / `git fetch`, a nie
  glownym mechanizmem non-blocking - tym sa `|| true` w bloku `run`.

### Wariant C: Bitbucket Pipelines (`bitbucket-pipelines.yml`)

```yaml
pipelines:
  pull-requests:
    '**':
      - step:
          name: mutation
          clone:
            depth: full
          script:
            - |
              <GUARD_NARZEDZIA> || { echo "narzedzie mutacyjne nie zainstalowane na runnerze - krok mutacji pominiety (instalacja to setup wlasciciela projektu, patrz strona wiki toolchain)"; exit 0; }
              git fetch origin $BITBUCKET_PR_DESTINATION_BRANCH || true
              <KOMENDA_DIFFOWA> || true
              python3 cicd/mutation_report.py --report <RAPORT> --format <FORMAT> > mutation-summary.json || true
              python3 cicd/mutation_baseline.py --summary mutation-summary.json --baseline cicd/mutation-baseline.json --scope diff --commit $BITBUCKET_COMMIT || true
          artifacts:
            - <RAPORT>
            - mutation-summary.json
            - cicd/mutation-baseline.json
```

Uwagi:

- **PULAPKA**: Bitbucket raportuje kazda pozycje `script:` osobno i nie gwarantuje, ze `exit 0`
  z guardu zakonczy caly step - guard w osobnej linii uciolby tylko sam siebie, a przebieg
  mutacyjny ruszylby mimo braku narzedzia. Dlatego cala sekwencja jest w JEDNYM bloku `- |`,
  tak jak w wariantach B i D, a guard stoi w nim jako pierwsza komenda.
- Bitbucket nie ma klucza `allow_failure` - non-blocking realizuja wylacznie guardy `|| true` /
  `|| exit 0`. Dlatego `|| true` wystepuje tu czesciej niz w GitLabie; to zamierzone.
- **PULAPKA**: dla pipeline'ow `pull-requests:` Bitbucket sam merguje galaz docelowa do zrodlowej
  przy przygotowaniu builda i z tego powodu `clone: depth: full` nie jest respektowane - pelna
  historia i tak jest. Klucz zostaje jako jawna deklaracja intencji (i dziala dla `branches:`),
  ale powiedz uzytkownikowi, ze dla PR-ow Bitbucket dba o historie sam.
- Artefakty: `artifacts:` na poziomie stepu to lista sciezek wzglednych do `BITBUCKET_CLONE_DIR`.
  Bitbucket nie ma odpowiednika `when: always`, ale zbiera je niezaleznie od wyniku stepu - pod
  warunkiem, ze zadna komenda nie przerwie stepu przed wygenerowaniem plikow (stad `|| true`).
- Narzedzie dostarcza `image:` na poziomie stepu albo self-hosted runner - nigdy instalacja
  w `script`. "Osobny etap" realizuje wpis `step: name: mutation` w sekcji `pull-requests:`,
  odrebny od stepow w `default:` / `branches:`.

### Wariant D: Jenkins (`Jenkinsfile`, nowy stage)

```groovy
stage('mutation') {
  when { changeRequest() }
  steps {
    catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE') {
      sh '''
        <GUARD_NARZEDZIA> || { echo "narzedzie mutacyjne nie zainstalowane na runnerze - krok mutacji pominiety (instalacja to setup wlasciciela projektu, patrz strona wiki toolchain)"; exit 0; }
        git fetch origin $CHANGE_TARGET
        <KOMENDA_DIFFOWA> || true
        python3 cicd/mutation_report.py --report <RAPORT> --format <FORMAT> > mutation-summary.json
        python3 cicd/mutation_baseline.py --summary mutation-summary.json --baseline cicd/mutation-baseline.json --scope diff --commit $GIT_COMMIT || true
      '''
    }
  }
  post {
    always {
      archiveArtifacts allowEmptyArchive: true, artifacts: '<RAPORT>,mutation-summary.json,cicd/mutation-baseline.json'
    }
  }
}
```

Uwagi:

- **PULAPKA (powod jednego `sh`)**: kazda linia `sh '...'` to osobne wywolanie powloki, wiec
  `exit 0` w guardzie zakonczylby tylko te jedna powloke, a kolejne `sh` wykonalyby sie mimo braku
  narzedzia - `catchError` lapie wynik bloku, nie przerywa go jak `set -e`. Dlatego caly przebieg
  jest w JEDNYM wieloliniowym `sh '''...'''`: guard i reszta komend dziela ta sama powloke, wiec
  `exit 0` konczy caly skrypt kroku. `set -e` nie dodawaj - `|| true` i guard wystarcza za
  sterowanie przeplywem.
- `when { changeRequest() }` bez parametrow uruchamia stage na kazdym pull/merge request,
  niezaleznie od SCM.
- `catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE')` oznacza stage jako `UNSTABLE`, a
  build zostawia `SUCCESS`. Parametr `buildResult` jest kluczowy - domyslne `catchError` bez
  parametrow ustawia `FAILURE` i zlamaloby wymaganie non-blocking.
- W apostrofach pojedynczych Groovy nie interpoluje `$CHANGE_TARGET` ani `$GIT_COMMIT` - rozwija
  je powloka ze srodowiska joba. To zamierzone, nie blad cytowania.
- `post { always { ... } }` gwarantuje `archiveArtifacts` niezaleznie od wyniku bloku, a
  `allowEmptyArchive: true` przepuszcza przypadek "guard uciol krok, nie ma raportu". Narzedzie
  dostarcza `agent { docker { image '<OBRAZ_LUB_RUNNER_Z_NARZEDZIEM>' } }` albo
  `agent { label '<runner-z-narzedziem>' }` na poziomie stage'a - stage nigdy nie instaluje.

### Tabela guardow obecnosci narzedzia

`command -v` znajdzie tylko samodzielna binarke w PATH, a PIT jest pluginem Mavena, Stryker.NET
narzedziem `dotnet tool`, cargo-mutants podkomenda `cargo`, Stryker JS pakietem node.

| Ekosystem | Narzedzie | `<GUARD_NARZEDZIA>` | `<FORMAT>` |
|---|---|---|---|
| Python | mutmut 3 | `command -v mutmut` | `mutmut` |
| JS/TS | Stryker + plugin runnera | `npx stryker --version` | `mte-json` |
| Java/Kotlin | PIT | `grep -q pitest <PLIK_BUILDA>` | `pit-xml` |
| C# / .NET | Stryker.NET | `dotnet tool list -g \| grep -q dotnet-stryker` | `mte-json` |
| Go | gremlins | `command -v gremlins` | brak adaptera |
| PHP | Infection | `vendor/bin/infection --version` | zalezy od loggera |
| Rust | cargo-mutants | `cargo mutants --version` | `cargo-mutants` |
| Scala | Stryker4s | `grep -q stryker4s project/plugins.sbt` | `mte-json` |
| Ruby | mutant | `bundle exec mutant --version` | brak adaptera |

Dla PIT i Stryker4s guard sprawdza wpis w pliku builda, nie binarke. Nazwe pliku (`pom.xml` vs
`build.gradle` vs `build.gradle.kts`) wez z pola `narzedzie` strony `toolchain` albo zapytaj
uzytkownika - nie zgaduj.

**Formaty**: `cicd/mutation_report.py` normalizuje cztery - `mte-json` (schemat
mutation-testing-elements, wspolny dla rodziny Stryker: JS, .NET, Scala), `pit-xml`, `mutmut`
i `cargo-mutants`. Wartosc `<FORMAT>` bierz z pola `raport` strony `toolchain`, a kolumna wyzej
jest tylko podpowiedzia. Infection ma wlasny logger `json` (raport HTML oparty na Stryker
Elements nie przesadza o schemacie JSON) - sprawdz, ktory logger projekt ma skonfigurowany.
Narzedzie zapisujace raport w formacie spoza tych czterech (gremlins, mutant, prawdopodobnie
Infection) -> krok CI i tak publikuje surowy raport jako artefakt, ale normalizacja i zapadka
wymagaja wlasnego adaptera, co jest **poza zakresem tego skilla**. Wypisz to uzytkownikowi
zamiast zgadywac format.

### Tabela zmiennych CI

| System | Galaz bazowa (target) | SHA commita |
|---|---|---|
| GitLab CI | `$CI_MERGE_REQUEST_TARGET_BRANCH_NAME` | `$CI_COMMIT_SHA` |
| GitHub Actions | `${{ github.base_ref }}` | `${{ github.sha }}` |
| Bitbucket Pipelines | `$BITBUCKET_PR_DESTINATION_BRANCH` | `$BITBUCKET_COMMIT` |
| Jenkins (multibranch) | `$CHANGE_TARGET` | `$GIT_COMMIT` |

### Skladnia zweryfikowana w dokumentacji

- GitLab `allow_failure`, `rules`, `artifacts: when: always` - https://docs.gitlab.com/ci/yaml/
- GitLab zmienne predefiniowane - https://docs.gitlab.com/ci/variables/predefined_variables/
- GitHub Actions `on: pull_request`, konteksty `github.base_ref` / `github.sha` -
  https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows
- `actions/checkout@v4` (`fetch-depth: 0`) - https://github.com/actions/checkout
- `actions/upload-artifact@v4` (`if: always()`) - https://github.com/actions/upload-artifact
- Bitbucket: depth przy pipeline'ach PR -
  https://support.atlassian.com/bitbucket-cloud/kb/clone-depth-not-applying-for-pull-request-pipelines/
- Bitbucket artefakty w kroku - https://support.atlassian.com/bitbucket-cloud/docs/use-artifacts-in-steps/
- Bitbucket zmienne - https://support.atlassian.com/bitbucket-cloud/docs/variables-and-secrets/
- Jenkins `when { changeRequest() }` - https://www.jenkins.io/doc/book/pipeline/syntax/
- Jenkins `catchError` - https://www.jenkins.io/doc/pipeline/steps/workflow-basic-steps/
- Jenkins `archiveArtifacts` - https://www.jenkins.io/doc/pipeline/steps/core/

### Po dodaniu kroku

Wypisz uzytkownikowi:

- ktory plik CI zmieniles i co dokladnie doszlo,
- co musi zrobic sam (GitLab: dopisac `mutation` do listy `stages:`; pozostale: wskazac obraz albo
  runner z zainstalowanym narzedziem),
- ze pierwszy przebieg zapisze baseline do artefaktu, ktory trzeba zacommitowac (KROK 4).

---

## KROK 4: Zapadka baseline w praktyce

Krok CI liczy przebieg diffowy, wiec zapadka porownuje wynik z tego samego rodzaju przebiegu.

| Status | Kiedy | Co robi krok |
|---|---|---|
| `first` | brak `cicd/mutation-baseline.json` | zapisuje plik, wypisuje "pierwszy pomiar", job zielony |
| `up` | wynik wyzszy niz baseline | zapisuje nowy baseline do artefaktu (baseline rosnie) |
| `same` | wynik rowny | nic nie zapisuje |
| `down` | wynik nizszy | linia `UWAGA` w logu, baseline BEZ zmian, job zielony |
| `no-mutants` | diff bez mutantow (np. sam markdown) | brak porownania, job zielony |
| `scope-mismatch` | baseline z przebiegu `full`, krok liczy `diff` | delta orientacyjna, bez zapisu |

**Commit baseline nalezy do czlowieka.** CI nie pushuje do repo - nie ma tokenu i nie modyfikuje
repozytorium. Po przebiegu ze statusem `first` albo `up` wlasciciel pobiera
`cicd/mutation-baseline.json` z artefaktu i commituje go. Dopoki tego nie zrobi, kazdy kolejny MR
raportuje `first`.

**Sygnalem glownym w trybie diffowym jest liczba survivorow, nie procent.** Przy kilku mutantach
w diffie procent skacze o dziesiatki punktow miedzy MR-ami. Raport zapadki drukuje zawsze i liczbe
survivorow, i `score / baseline / delta`. Pelna wartosc procent i zapadka maja dopiero na pelnym
przebiegu modulow rdzenia (`--scope full`, lokalnie albo przy zamykaniu sprintu - poza zakresem
tego skilla).

**Spadek nie oblewa builda.** Mutation score to trend; bramka na delcie konczy sie wylaczeniem
checka. Nie dodawaj `exit 1` na statusie `down` i nie ustawiaj progu procentowego.

Pole `baseline:` na stronie wiki `toolchain` aktualizuje czlowiek (albo skill
`/monolynx:mutation-check`, jesli projekt go ma) - krok CI nigdy nie pisze do wiki.

---

## KROK 5: Uruchomienie lokalne (takze gdy brak CI)

Przebieg diffowy, dokladnie ten sam co w CI:

```bash
<KOMENDA_DIFFOWA> || true
python3 cicd/mutation_report.py --report <RAPORT> --format <FORMAT> > mutation-summary.json
python3 cicd/mutation_baseline.py --summary mutation-summary.json \
  --baseline cicd/mutation-baseline.json --scope diff --dry-run
```

Najpierw `--dry-run` (pokazuje status i delte, nie zapisuje), potem ta sama komenda bez flagi.

Pelny przebieg na sciezkach z pola `moduly rdzenia` - wtedy `--scope full`:

```bash
<KOMENDA_PELNA> || true
python3 cicd/mutation_report.py --report <RAPORT> --format <FORMAT> \
  | python3 cicd/mutation_baseline.py --summary - --baseline cicd/mutation-baseline.json --scope full
```

Opcjonalny Makefile (`mutation-dry` to ta sama tresc z `--dry-run` na koncu ostatniej linii):

```makefile
mutation: ## Przebieg mutacyjny na zmianie + zapadka
	<KOMENDA_DIFFOWA> || true
	python3 cicd/mutation_report.py --report <RAPORT> --format <FORMAT> > mutation-summary.json
	python3 cicd/mutation_baseline.py --summary mutation-summary.json --baseline cicd/mutation-baseline.json --scope diff
```

---

## WAZNE ZASADY

1. **CI nie instaluje narzedzia mutacyjnego** - to jednorazowy setup wlasciciela repo (obraz albo
   runner), per jezyk i per runner. Krok tylko uzywa.
2. **Brak narzedzia, raportu albo baseline = komunikat i `exit 0`.** Krok mutacji NIGDY nie oblewa
   builda projektu.
3. **Spadek score to UWAGA, nie bramka** - zadnego `exit 1` na delcie, zadnego progu procentowego,
   zadnego blokowania merge'a.
4. **Baseline moze tylko rosnac i commituje go czlowiek** - CI nie pushuje do repo.
5. **Nie modyfikuj kodu projektu** - tworzysz `cicd/mutation_report.py`,
   `cicd/mutation_baseline.py`, krok CI i opcjonalnie Makefile oraz wpis w `.gitignore`.
6. **Kopii skryptow nie edytuj recznie** - aktualizacja przez ponowne uruchomienie skilla.
7. **Komendy i etykiety pochodza ze strony `toolchain`** (kontrakt `/monolynx:project-toolchain`).
   Nie wpisuj komend z pamieci i nie zmieniaj tej strony.
8. **Placeholder `<PROJECT_SLUG>` w dokumentacji** - nigdy konkretny slug. Komunikaty `echo` w CI
   po polsku bez diakrytyk, komentarze w plikach CI po angielsku.
