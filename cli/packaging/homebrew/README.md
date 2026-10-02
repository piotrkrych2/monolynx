# Homebrew: tap `monolynx/tap` i formuła `monolynx`

Katalog zawiera formułę Homebrew dla klienta linii komend Monolynx (`monolynx-cli`) oraz opis tego, jak trafia ona do użytkowników. Dotyczy macOS i Linuxa (Homebrew działa na obu).

## Po co

Homebrew to domyślny menedżer pakietów wielu programistów na macOS i popularna opcja na Linuxie. Formuła pozwala zainstalować CLI jedną komendą, bez ręcznego zarządzania Pythonem i środowiskiem wirtualnym: Homebrew buduje izolowany virtualenv z przypiętymi zależnościami i wystawia komendy `monolynx` i `mnx` w `PATH`. Kanał uzupełnia instalację z PyPI (`pipx install monolynx-cli`), nie zastępuje jej.

## Tap

| Element | Wartość |
|---|---|
| Nazwa tapu w Homebrew | `monolynx/tap` |
| Repozytorium | [github.com/monolynx/homebrew-tap](https://github.com/monolynx/homebrew-tap) (organizacja GitHub `Monolynx`) |
| Ścieżka formuły w tapie | `Formula/monolynx.rb` |
| Źródło prawdy formuły | `cli/packaging/homebrew/monolynx.rb` w tym monorepo |

Homebrew rozwiązuje jednoczłonową nazwę `monolynx/tap` do `https://github.com/monolynx/homebrew-tap` (prefiks `homebrew-` dopisuje sam, co opisuje `brew tap --help`). GitHub nie rozróżnia wielkości liter w nazwach organizacji i repozytoriów, więc `monolynx` i `Monolynx` wskazują to samo repozytorium.

Warunek wstępny: organizacja `Monolynx` na GitHubie i repozytorium `homebrew-tap`. To krok ręczny właściciela, wykonany 2026-09-27. CI niczego tam nie zakłada.

## Nazwy: `monolynx` vs `monolynx-cli`

- **Formuła Homebrew: `monolynx`** - tak jak komenda wpisywana w terminalu. Użytkownik instaluje `monolynx` i uruchamia `monolynx`.
- **Paczka PyPI: `monolynx-cli`** - nazwa odróżnia paczkę od backendu, którego dystrybucja w głównym `pyproject.toml` nazywa się `monolynx` (instalacja obu w jednym środowisku nadpisywałaby się w `pip`). Nazwa `monolynx` na PyPI jest wolna, ale świadomie jej nie używamy. Formuła pobiera sdist `monolynx-cli` z PyPI (główne `url` i `sha256`).
- **Komendy po instalacji:** `monolynx` i `mnx` (sekcja `[project.scripts]` w `cli/pyproject.toml`).

Kopia formuły w tapie jest pochodną pliku `cli/packaging/homebrew/monolynx.rb`. Zmiany formuły (zależności, test, `depends_on`) robimy w monorepo przez MR, nigdy bezpośrednio w tapie.

## Instalacja

Obie formy są równoważne.

Jedna komenda, która sama dodaje tap:

```bash
brew install monolynx/tap/monolynx
```

Albo najpierw tap, potem instalacja po krótkiej nazwie:

```bash
brew tap monolynx/tap
brew install monolynx
```

Sprawdzenie:

```bash
monolynx --version
```

Aktualizacja do nowej wersji: `brew upgrade monolynx`.

Samo `brew install monolynx` bez wcześniejszego `brew tap monolynx/tap` zadziała dopiero, gdy formuła trafi do homebrew-core. To nie jest w zakresie tego sprintu (patrz niżej).

## Decyzja: własny tap teraz, homebrew-core później

**Wybór: własny tap `monolynx/tap` teraz.**

- **Kontrola:** repozytorium należy do właściciela projektu. Nowa wersja trafia do użytkowników zaraz po releasie, bez czekania na cudzy przegląd.
- **Natychmiastowa dostępność:** formuła działa od pierwszego tagu `cli-v*`, niezależnie od popularności projektu.
- **Zero wymagań wstępnych:** tap nie ma progów popularności, stabilności ani wymogów formalnych poza poprawną formułą.

**homebrew-core: później.** Dodanie formuły do homebrew-core to zgłoszenie PR do cudzego repozytorium, przegląd przez maintainerów Homebrew oraz spełnienie ich progów popularności i stabilności projektu. Młody projekt bez bazy użytkowników tych progów nie spełni, a odrzucony PR nic nie daje. Wniosek do homebrew-core składamy dopiero po zbudowaniu realnej bazy instalacji z tapu. Do tego czasu krótkie `brew install monolynx` bez tapu nie jest dostępne i nie obiecujemy go w dokumentacji.

## Automatyzacja w CI

Job `bump-homebrew` (stage `publish`) uruchamia się tylko na tagu pasującym do `^cli-v\d+\.\d+\.\d+$` (bez `rc`) i ma `needs` na `publish-pypi`. Runner GitLab jest shell executorem (`image:` jest ignorowane), więc job startuje kontener z hosta przez `docker run` na obrazie `python:3.13`, jak `lint:cli` i `test:cli`. Job nie używa Homebrew (powód w sekcji "Dlaczego job nie używa Homebrew"): wszystko robi `cli/scripts/bump_formula.py` (tylko biblioteka standardowa) oraz `pip`. Repo jest montowane read-only (`/src`), a praca idzie na kopii w kontenerze. Formuła wraca na host przez osobny, zapisywalny katalog `/out`, z którego runner kopiuje ją do `cli/packaging/homebrew/monolynx.rb`. `CI_COMMIT_TAG` i `HOMEBREW_TAP_TOKEN` trafiają do kontenera przez `-e NAZWA` bez wartości.

Kroki joba (wszystkie w jednym `docker run`, poza kopią artefaktu na końcu):

1. `bump_formula.py --tag "$CI_COMMIT_TAG" --formula <kopia formuły>` (skrypt i formuła są kopiowane z `/src/cli/...` do katalogu roboczego kontenera, bo repo jest read-only; opcjonalnie `--package`, domyślnie `monolynx-cli`). Skrypt bierze wersję z tagu (`cli-v1.2.3` -> `1.2.3`), pobiera metadane z `https://pypi.org/pypi/monolynx-cli/<wersja>/json` (do 5 prób co 10 s, bo sdist bywa widoczny w JSON z opóźnieniem po `publish-pypi`) i podmienia w formule główne `url` oraz `sha256` na sdist tej wersji. Bez `--pip-report` bloki `resource` zostają bajt w bajt.
2. Pierwszy zapis do `/out/monolynx.rb`. Od tego miejsca artefakt ma poprawne główne `url` i `sha256`, ale jeszcze stare bloki `resource`.
3. Lista zależności: `pip install -q --disable-pip-version-check --dry-run --ignore-installed --report <plik> "monolynx-cli==<wersja>"`. Niczego nie instaluje, zapisuje tylko raport JSON z rozwiązanym zestawem paczek. Komenda jest ponawiana (do 5 prób co 15 s), bo indeks PyPI bywa nieaktualny tuż po publikacji; po piątej nieudanej próbie job kończy się błędem.
4. `bump_formula.py --tag "$CI_COMMIT_TAG" --formula <kopia formuły> --pip-report <plik>` regeneruje wszystkie bloki `resource`: zależności bierze z raportu pip, `url` i `sha256` sdistów (nie kół) z PyPI JSON API, a bloki sortuje po znormalizowanej nazwie paczki.
5. Drugi zapis do `/out/monolynx.rb`: pełna formuła, którą runner na hoście kopiuje do `cli/packaging/homebrew/monolynx.rb`, czyli do artefaktu.
6. Sprawdzenie stanu tapu: `curl` na `https://raw.githubusercontent.com/Monolynx/homebrew-tap/main/Formula/monolynx.rb`.
   - `404`: formuły w tapie nie ma. Bez pushu.
   - `200`: push do tapu po HTTPS. Token idzie przez `credential.helper` czytający `HOMEBREW_TAP_TOKEN`, nigdy w URL-u. Brak zmiennej kończy job błędem. Gdy formuła w tapie jest już identyczna (ponowne uruchomienie joba na tym samym tagu po udanym pushu), job kończy się sukcesem bez commita i bez pushu.
   - Każdy inny kod (błąd sieci itp.): wariant bezpieczny, bez pushu.
7. Artefakt `cli/packaging/homebrew/monolynx.rb` powstaje zawsze (`when: always`, ważny 30 dni), także gdy push się nie odbył albo job padł.
8. Kopię formuły w monorepo aktualizuje człowiek: pobiera artefakt i commituje go przez MR. CI nie ma tokenu zapisu do monorepo.

Kody wyjścia `bump_formula.py`: `0` sukces, `2` błąd treści (tag, PyPI, raport pip, formuła; plik formuły nietknięty), `64` błąd użycia.

### Gdy job padnie w połowie

Artefakt po awarii nie zawsze nadaje się do tapu. Stan rozpoznasz po logu joba:

| Job padł | Artefakt | Co z nim zrobić |
|---|---|---|
| w kroku 1 | niezmieniona formuła z repo (poprzednia wersja) | nic, to nie jest formuła nowej wersji |
| w krokach 3-4 | nowe `url`/`sha256`, **stare bloki `resource`** | nie wgrywać do tapu; dokończyć ręcznie (niżej) |
| w kroku 6 | pełna, poprawna formuła | wypchnąć do tapu ręcznie albo ponowić job |

Formuła z nowym `url` i starymi blokami `resource` instaluje nową wersję CLI ze starymi wersjami zależności, a nowej zależności nie ma w niej wcale. Najprościej ponowić job (`glab ci retry <job_id>`): jest idempotentny. Gdy awaria się powtarza, bloki regenerujesz lokalnie tymi samymi dwoma komendami co job, z katalogu głównego repozytorium `monolynx` (podstaw wersję za `X.Y.Z`):

```bash
docker run --rm -v "$PWD":/work -w /work python:3.13 sh -c '
  pip install -q --disable-pip-version-check --dry-run --ignore-installed \
    --report /tmp/pip-report.json "monolynx-cli==X.Y.Z" &&
  python cli/scripts/bump_formula.py --tag cli-vX.Y.Z \
    --formula cli/packaging/homebrew/monolynx.rb --pip-report /tmp/pip-report.json'
```

Raport pip zależy od wersji Pythona i platformy (markery środowiskowe), dlatego komenda idzie w tym samym obrazie co job, a nie na Pythonie hosta. Wynik to gotowa formuła w `cli/packaging/homebrew/monolynx.rb`: weryfikujesz ją (sekcja "Weryfikacja ręczna"), wypychasz do tapu jako `Formula/monolynx.rb` i commitujesz do monorepo przez MR.

### Dlaczego job nie używa Homebrew

Do `cli-v0.1.0` job działał w obrazie `homebrew/brew:4.6.20` i regenerował bloki `resource` komendą `brew update-python-resources`. Na runnerze GitLab padł z błędem `Landlock ABI 2 or later is required; found ABI 1`, a artefakt był niezmienionym szablonem z repo.

- Tag 4.6.20 nie ma kodu Landlock. `brew install` w jobie robił auto-update do bieżącego Homebrew, w którym `brew update-python-resources` wymaga sandboxa Landlock ABI 2. Kernel runnera daje ABI 1.
- Sandboxa nie da się wyłączyć zmienną: `HOMEBREW_NO_SANDBOX_LINUX` jest przestarzała i nie omija tego błędu.
- Zablokowanie auto-update (`HOMEBREW_NO_AUTO_UPDATE=1`) nie pomaga: na Homebrew 4.6.20 pada wtedy `brew install python@3.13`.

Lista zależności i sumy sdistów nie wymagają Homebrew: zestaw paczek rozwiązuje `pip`, a `url` i `sha256` daje PyPI JSON API. Dla `monolynx-cli` 0.1.0 raport pip (pip 26.2.1, obraz `python:3.13`) daje te same 21 zależności w tych samych wersjach co bloki wygenerowane wcześniej przez `brew update-python-resources`. Zgodności bajt w bajt pilnuje test w `tests/unit/test_bump_formula.py`.

Na runnerze GitLab job w nowym kształcie uruchomi się pierwszy raz na `cli-v0.1.1`. Tag `rc` go nie uruchamia.

## Release

> **Bloki `resource` w szablonie:** formuła w monorepo ma bloki `resource` dla wszystkich zależności runtime z `cli/pyproject.toml` (w tym `pyyaml` i `tomli-w`; `tomli` pomijamy, bo jest potrzebny tylko dla Pythona poniżej 3.11). Job `bump-homebrew` regeneruje je na każdym tagu, ale szablon w repo aktualizujemy ręcznie, gdy zmieniają się zależności. Test `TestRealHomebrewFormulaResources` w `tests/unit/test_bump_formula.py` pilnuje, żeby żadna zależność nie została bez `resource`.

**`cli-v0.1.0` (pierwszy tag)** wyszedł ręcznie: job padł (sekcja "Dlaczego job nie używa Homebrew"), formułę przygotował i wypchnął do tapu właściciel, a do monorepo trafiła w MON-230.

**Każdy kolejny tag `cli-vX.Y.Z`:** formuła jest już w tapie, więc push robi job `bump-homebrew`. Człowiekowi zostaje:

1. Sprawdzić, że job jest zielony i w tapie jest commit `monolynx X.Y.Z`. Przy czerwonym jobie: sekcja "Gdy job padnie w połowie".
2. Pobrać formułę z artefaktu joba i commitować ją do monorepo jako `cli/packaging/homebrew/monolynx.rb` przez MR.

**Ręczne wypchnięcie formuły** (po awarii joba albo do nowego tapu, w którym formuły jeszcze nie ma):

1. Sprawdź, że formuła ma bloki `resource` dla wszystkich zależności tej wersji. Jeśli nie ma, zregeneruj je (sekcja "Gdy job padnie w połowie").
2. Zweryfikuj ją lokalnie (sekcja "Weryfikacja ręczna" niżej): build ze źródła i `brew test`.
3. Wypchnij ją do `github.com/monolynx/homebrew-tap` jako `Formula/monolynx.rb`.
4. Uruchom audyt na formule z tapu:

   ```bash
   brew tap monolynx/tap
   brew install --build-from-source monolynx/tap/monolynx
   brew test monolynx/tap/monolynx
   brew audit --strict monolynx/tap/monolynx
   ```

5. Commituj tę samą formułę do monorepo jako `cli/packaging/homebrew/monolynx.rb` przez MR.

## Bottle'e: decyzja

**Stan: odroczone (MON-231, decyzja Team Managera). Zmienić ją może właściciel projektu.**

Tap nie ma bottli, czyli gotowych binarnych paczek. `brew install monolynx/tap/monolynx` buduje więc wszystko ze źródła: pobiera `rust` i `llvm@22` (około 2 GB) i kompiluje `pydantic-core`, co trwa od 2 do 30 minut. Cel MON-231 "instalacja z Homebrew bez kompilacji Rusta" pozostaje przez to **niezrealizowany**.

Powody odroczenia:

- **Praca poza monorepo.** Bottle buduje się w repozytorium tapu na GitHubie: `brew test-bot` i workflowy GitHub Actions (`tests.yml`, `publish.yml`, generowane przez `brew tap-new`), runnery macOS arm64 i Linux x86_64, publikacja do GitHub Releases albo Packages, etykieta `pr-pull` na PR.
- **Kolizja z obecnym flow.** CI GitLaba wypycha formułę wprost na `main` tapu. `brew test-bot` zakłada PR w tapie, do którego workflow dopisuje blok `bottle do`. Trzeba by zamienić push na PR albo dodać workflow budujący po commicie. Formuła w monorepo (źródło prawdy) przestałaby być wtedy identyczna z formułą w tapie.
- **Koszt nieproporcjonalny do skali.** To pierwszy release i mała baza instalacji. `pipx install monolynx-cli`, `uv` i `pip` instalują gotowe koła bez kompilacji i są pełnoprawnymi kanałami. Kompilacja dotyczy tylko `brew install` z tapu.

Kiedy wracamy do tematu: realny ruch z Homebrew albo zgłoszenia o czasie instalacji. Wtedy osobny ticket z zakresem: workflow w repo tapu plus zmiana pushu z CI na PR.

Do tego czasu użytkownikowi, któremu zależy na szybkiej instalacji, polecamy `pipx install monolynx-cli`.

## Poświadczenie: `HOMEBREW_TAP_TOKEN`

- **Typ:** fine-grained personal access token GitHuba.
- **Uprawnienia:** `Contents: Read and write`, wyłącznie do repozytorium `Monolynx/homebrew-tap`. Żadnych innych repozytoriów ani uprawnień.
- **Przechowywanie:** zmienna GitLab CI/CD `HOMEBREW_TAP_TOKEN`, oznaczona jako **masked** i **protected**.
- **Tagi `cli-v*` chronione** (Settings -> Repository -> Protected tags): zmienna protected jest dostępna tylko dla chronionych refów, więc bez tej ochrony job na tagu nie dostanie tokenu, a z nią tag może postawić tylko uprawniona osoba.
- **Wygasanie:** token ma datę wygaśnięcia. Rotuj go przed jej upływem (nowy token, podmiana wartości zmiennej, unieważnienie starego). Po wygaśnięciu push do tapu przestaje działać, a artefakt nadal powstaje.

Wartości tokenu nigdy nie wpisujemy do repozytorium, dokumentacji ani logów.

## Weryfikacja ręczna formuły przed releasem

`brew install --help` w Homebrew 6 (`Usage: brew install [options] formula|cask [...]`) opisuje tylko instalację po nazwie formuły, a nowsze wersje Homebrew ograniczają instalację z pliku `.rb` spoza tapu. Nie polegamy więc na `brew install ./cli/packaging/homebrew/monolynx.rb`. Formułę z monorepo sprawdzamy przez lokalny, tymczasowy tap `local/scratch`. Weryfikacja wymaga Homebrew na maszynie człowieka (macOS albo Linux); job CI jej nie robi. Komendy uruchamiasz z katalogu głównego repozytorium `monolynx`:

```bash
brew tap-new --no-git local/scratch
mkdir -p "$(brew --repository local/scratch)/Formula"
cp cli/packaging/homebrew/monolynx.rb "$(brew --repository local/scratch)/Formula/monolynx.rb"
brew install --build-from-source local/scratch/monolynx
brew test local/scratch/monolynx
monolynx --version
```

Sprzątanie po weryfikacji:

```bash
brew uninstall local/scratch/monolynx
brew untap local/scratch
```

Formuła wskazuje sdist opublikowany na PyPI. Przed pierwszą publikacją wersji są dwie drogi:

- **Wersja rc z TestPyPI:** tag `cli-vX.Y.ZrcN` publikuje paczkę na TestPyPI (job `publish-testpypi`). W kopii formuły w lokalnym tapie ustaw `url` na sdist z TestPyPI, a `sha256` na jego sumę z tej samej strony.
- **Sdist zbudowany z `cli/`:** zbuduj sdist tak jak job `.publish-cli` (`python -m build` w katalogu `cli/`, wynik w `cli/dist/`), w kopii formuły w lokalnym tapie ustaw `url "file:///<pełna ścieżka>/cli/dist/monolynx_cli-X.Y.Z.tar.gz"`, a `sha256` na wynik `shasum -a 256 cli/dist/monolynx_cli-X.Y.Z.tar.gz`.

W obu przypadkach bloki `resource` pochodzą z formuły w monorepo. Jeśli zmieniły się zależności w `cli/pyproject.toml`, zaktualizuj je (sekcja niżej) przed buildem. `brew audit --strict` uruchamiaj na formule z docelowym `url` z PyPI, nie na wariancie z `file://`.

## Aktualizacja zależności Pythona

- Bloki `resource` generuje `bump_formula.py --pip-report`: w CI na każdym tagu (sekcja "Automatyzacja w CI"), ręcznie komendą z sekcji "Gdy job padnie w połowie". Skrypt nadpisuje wszystkie bloki naraz, w kolejności znormalizowanych nazw paczek.
- Generowanie wymaga wersji opublikowanej na PyPI: `pip` rozwiązuje zależności `monolynx-cli==<wersja>` z indeksu, a `url` i `sha256` sdistów pochodzą z PyPI JSON API. Dla wersji `rc` z TestPyPI i dla sdista z `file://` bloków tą drogą nie wygenerujesz.
- Przed publikacją (np. przy dopisaniu jednej zależności do szablonu) wpis bierzemy ręcznie z PyPI JSON (`https://pypi.org/pypi/<pakiet>/json`: `url` i `sha256` sdista, nie koła) i trzymamy kolejność alfabetyczną; job `bump-homebrew` i tak nadpisuje bloki na następnym tagu.
- **Nie używaj `homebrew-pypi-poet`:** projekt jest nieutrzymywany.
- **`brew update-python-resources`** to wbudowane w Homebrew narzędzie, którym bloki powstały dla `cli-v0.1.0`. W CI nie działa (sekcja "Dlaczego job nie używa Homebrew"), więc źródłem bloków jest `bump_formula.py`, a wynik obu narzędzi dla 0.1.0 jest identyczny.
- **`rust` jako zależność build:** `pydantic` zależy od `pydantic-core`, który jest rozszerzeniem w Ruście. Budowa ze źródła w virtualenvie formuły wymaga więc `depends_on "rust" => :build`.
