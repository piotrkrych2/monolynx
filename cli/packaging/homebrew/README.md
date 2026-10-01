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

Job `bump-homebrew` (stage `publish`) uruchamia się tylko na tagu pasującym do `^cli-v\d+\.\d+\.\d+$` (bez `rc`) i ma `needs` na `publish-pypi`. Runner GitLab jest shell executorem (`image:` jest ignorowane), więc job startuje kontener z hosta przez `docker run` na obrazie `homebrew/brew:4.6.20` (przypięty tag, nie `latest`), jak `lint:cli` i `test:cli`. Repo jest montowane read-only (`/src`), a praca idzie na kopii w kontenerze. Obraz działa jako użytkownik `linuxbrew` (Homebrew odmawia pracy jako root), więc zaktualizowana formuła wraca na host przez osobny, zapisywalny katalog `/out`, z którego kopiuje ją runner do `cli/packaging/homebrew/monolynx.rb`. Dzięki temu artefakt ma właściciela runnera i nie blokuje `git clean`. `CI_COMMIT_TAG` i `HOMEBREW_TAP_TOKEN` trafiają do kontenera przez `-e NAZWA` bez wartości.

Kroki joba (wszystkie w jednym `docker run`, poza kopią artefaktu na końcu):

1. `brew install python@3.13`. Obraz nie ma Pythona w `PATH`, a `python@3.13` jest keg-only, więc skrypt jest wołany pełną ścieżką z `brew --prefix python@3.13`.
2. `bump_formula.py --tag "$CI_COMMIT_TAG" --formula <kopia formuły>` (skrypt i formuła są kopiowane z `/src/cli/...` do katalogu roboczego kontenera, bo repo jest read-only; tylko biblioteka standardowa; opcjonalnie `--package`, domyślnie `monolynx-cli`). Skrypt bierze wersję z tagu (`cli-v1.2.3` -> `1.2.3`), pobiera metadane z `https://pypi.org/pypi/monolynx-cli/<wersja>/json` (do 5 prób co 10 s, bo sdist bywa widoczny w JSON z opóźnieniem po `publish-pypi`) i podmienia w formule główne `url` oraz `sha256` na sdist tej wersji. Bloki `resource` zostawia bajt w bajt. Kody wyjścia: `0` sukces, `2` błąd danych (wersja, PyPI, formuła; plik formuły nietknięty), `64` błąd użycia.
3. Regeneracja bloków `resource`: `brew update-python-resources` nie przyjmuje ścieżki do pliku, więc job zakłada tymczasowy tap `local/scratch` bez gita, kopiuje do niego formułę, uruchamia `brew update-python-resources --ignore-main-package-cooldown local/scratch/monolynx` (świeżo opublikowana paczka jest jeszcze w okresie cooldownu Homebrew, a flaga działa tylko poza oficjalnymi tapami) i kopiuje wynik z powrotem do kopii roboczej formuły. Tę kopię job zapisuje do `/out/monolynx.rb` (jeszcze przed pushem), skąd runner na hoście kopiuje ją do `cli/packaging/homebrew/monolynx.rb`, czyli do artefaktu.
4. Sprawdzenie stanu tapu: `curl` na `https://raw.githubusercontent.com/Monolynx/homebrew-tap/main/Formula/monolynx.rb`.
   - `404`: pierwszy release, formuły w tapie jeszcze nie ma. Bez pushu.
   - `200`: push do tapu po HTTPS. Token idzie przez `credential.helper` czytający `HOMEBREW_TAP_TOKEN`, nigdy w URL-u. Brak zmiennej kończy job błędem.
   - Każdy inny kod (błąd sieci itp.): wariant bezpieczny, bez pushu.
5. Artefakt `cli/packaging/homebrew/monolynx.rb` powstaje zawsze (`when: always`, ważny 30 dni), także gdy push się nie odbył albo job padł.
6. Kopię formuły w monorepo aktualizuje człowiek: pobiera artefakt i commituje go przez MR. CI nie ma tokenu zapisu do monorepo.

> **Potwierdzone na `cli-v0.1.0`:** krok 3 pada na runnerze GitLab CI z błędem `Landlock ABI 2 or later is required; found ABI 1` (sandbox Homebrew wymaga nowszego Landlock niż kernel runnera; lokalnie pod Docker Desktop na macOS: `Landlock is not supported by this Linux kernel`). `set -e` przerywa skrypt przed zapisem do `/out`, więc artefakt to niezmieniony szablon z repo. Do czasu naprawy (MON-231) formułę dla nowej wersji przygotowuje się ręcznie na macOS: `bump_formula.py` na kopii w tapie `local/scratch`, potem `brew update-python-resources` (sekcja "Aktualizacja zależności Pythona").

## Release: pierwszy tag i kolejne

> **Bloki `resource` w szablonie:** formuła w monorepo ma bloki `resource` dla wszystkich zależności runtime z `cli/pyproject.toml` (w tym `pyyaml` i `tomli-w`; `tomli` pomijamy, bo jest potrzebny tylko dla Pythona poniżej 3.11). Job `bump-homebrew` regeneruje je na każdym tagu, ale szablon w repo aktualizujemy ręcznie, gdy zmieniają się zależności. Test `TestRealHomebrewFormulaResources` w `tests/unit/test_bump_formula.py` pilnuje, żeby żadna zależność nie została bez `resource`.

**Pierwszy produkcyjny tag `cli-v*`** stawia człowiek po merge MON-132. Na nim job `bump-homebrew` daje wyłącznie artefakt. Właściciel wtedy:

1. Pobiera formułę z artefaktu joba i sprawdza, że ma bloki `resource` dla wszystkich zależności. Jeśli nie ma, generuje je lokalnie (sekcja "Aktualizacja zależności Pythona").
2. Weryfikuje ją lokalnie (sekcja "Weryfikacja ręczna" niżej): build ze źródła i `brew test`.
3. Ręcznie wypycha ją do `github.com/monolynx/homebrew-tap` jako `Formula/monolynx.rb`.
4. Po dodaniu do tapu uruchamia audyt na formule z tapu:

   ```bash
   brew tap monolynx/tap
   brew install --build-from-source monolynx/tap/monolynx
   brew test monolynx/tap/monolynx
   brew audit --strict monolynx/tap/monolynx
   ```

5. Commituje tę samą formułę do monorepo jako `cli/packaging/homebrew/monolynx.rb` przez MR.

**Od drugiego tagu** push do tapu robi job `bump-homebrew`. Człowiekowi zostaje przeniesienie artefaktu do monorepo przez MR (krok 5).

## Poświadczenie: `HOMEBREW_TAP_TOKEN`

- **Typ:** fine-grained personal access token GitHuba.
- **Uprawnienia:** `Contents: Read and write`, wyłącznie do repozytorium `Monolynx/homebrew-tap`. Żadnych innych repozytoriów ani uprawnień.
- **Przechowywanie:** zmienna GitLab CI/CD `HOMEBREW_TAP_TOKEN`, oznaczona jako **masked** i **protected**.
- **Tagi `cli-v*` chronione** (Settings -> Repository -> Protected tags): zmienna protected jest dostępna tylko dla chronionych refów, więc bez tej ochrony job na tagu nie dostanie tokenu, a z nią tag może postawić tylko uprawniona osoba.
- **Wygasanie:** token ma datę wygaśnięcia. Rotuj go przed jej upływem (nowy token, podmiana wartości zmiennej, unieważnienie starego). Po wygaśnięciu push do tapu przestaje działać, a artefakt nadal powstaje.

Wartości tokenu nigdy nie wpisujemy do repozytorium, dokumentacji ani logów.

## Weryfikacja ręczna formuły przed releasem

`brew install --help` w Homebrew 6 (`Usage: brew install [options] formula|cask [...]`) opisuje tylko instalację po nazwie formuły, a nowsze wersje Homebrew ograniczają instalację z pliku `.rb` spoza tapu. Nie polegamy więc na `brew install ./cli/packaging/homebrew/monolynx.rb`. Formułę z monorepo sprawdzamy więc przez lokalny, tymczasowy tap `local/scratch` (ta sama nazwa, której używa job CI). Komendy uruchamiasz z katalogu głównego repozytorium `monolynx`:

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

- Bloki `resource` generuje `brew update-python-resources`. Gdy komenda nie jest dostępna (np. przy dopisaniu jednej zależności do szablonu), wpis bierzemy z PyPI JSON (`https://pypi.org/pypi/<pakiet>/json`: `url` i `sha256` sdista, nie koła) i trzymamy kolejność alfabetyczną; job `bump-homebrew` i tak nadpisuje je na następnym tagu. Komenda nie przyjmuje ścieżki do pliku, więc działa na formule w lokalnym tapie `local/scratch` (założonym jak w sekcji "Weryfikacja ręczna"):

  ```bash
  brew update-python-resources --ignore-main-package-cooldown local/scratch/monolynx
  cp "$(brew --repository local/scratch)/Formula/monolynx.rb" cli/packaging/homebrew/monolynx.rb
  ```

  `--ignore-main-package-cooldown` pomija okres cooldownu Homebrew dla świeżo opublikowanej wersji `monolynx-cli` (zależności nadal go respektują). Z `--print-only` komenda tylko wypisuje nowe bloki, bez zmiany pliku. Zmienioną formułę commitujesz do monorepo przez MR.
- Nazwę paczki komenda wywodzi z głównego `url` formuły, a wersję z wersji formuły, i szuka ich na PyPI. Formuła musi więc wskazywać wersję opublikowaną na PyPI; dla sdista z `file://` bloków nie wygenerujesz.
- **Nie używaj `homebrew-pypi-poet`:** projekt jest nieutrzymywany. `brew update-python-resources` to narzędzie wbudowane w Homebrew.
- **`rust` jako zależność build:** `pydantic` zależy od `pydantic-core`, który jest rozszerzeniem w Ruście. Budowa ze źródła w virtualenvie formuły wymaga więc `depends_on "rust" => :build`.
