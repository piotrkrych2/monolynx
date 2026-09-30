---
name: pypi-trusted-publishing-gitlab
description: Jak skonfigurować PyPI/TestPyPI Trusted Publishing (OIDC) w GitLab CI bez długożyjącego tokenu, i jaki format `license` w pyproject.toml unika konfliktu z klasyfikatorami PEP 639
metadata:
  type: reference
---

Źródło: docs.pypi.org/trusted-publishers/using-a-publisher/ (sekcja GitLab CI/CD), changelog twine (WebFetch w MON-136, 2026-09-28) - nie z pamięci, bo to obszar szybko się zmieniający.

## Mechanizm (GitLab CI)

```yaml
job-pypi:
  id_tokens:
    PYPI_ID_TOKEN:
      aud: pypi
  script:
    - pip install -U twine
    - twine upload dist/*

job-testpypi:
  id_tokens:
    TESTPYPI_ID_TOKEN:
      aud: testpypi
  script:
    - pip install -U twine
    - twine upload --repository testpypi dist/*
```

- **KOREKTA (MON-136, zweryfikowane empirycznie w kontenerze przez TM, nie tylko z docs.pypi.org):** nazwa zmiennej w `id_tokens` NIE jest stała "PYPI_ID_TOKEN" dla obu targetów. Twine (przez pakiet `id`, `id/_internal/oidc/ambient.py` funkcja detekcji GitLab) czyta audience z `https://<domena>/_/oidc/audience`, a potem szuka zmiennej środowiskowej `<AUD>_ID_TOKEN` (uppercase). Dla `aud=pypi` -> `PYPI_ID_TOKEN`, dla `aud=testpypi` -> `TESTPYPI_ID_TOKEN`. Przykład w docs.pypi.org pokazuje tylko przypadek pypi i myląco sugeruje jedną stałą nazwę - **nie ufać temu bez sprawdzenia dla testpypi**. Pierwsza wersja tego ticketu miała ten błąd (`TESTPYPI_ID_TOKEN` pomylone z `PYPI_ID_TOKEN` w jobie testpypi) i przeszła review dopiero po korekcie krytyka/TM.
- Twine wymienia token OIDC na krótkożyjący token uploadu SAM, wewnętrznie (endpoint mint-token PyPI/TestPyPI) - żadnego ręcznego `curl` do mint-token, żaden sekret nie trafia do logu joba ani do zmiennych CI/CD.
- Wymaga tylko `twine>=6.1.0`; w MON-136 przypięto `twine==7.0.0` (najnowsza w tamtym czasie), `build==1.6.1`.
- Dwa joby (pypi/testpypi) różnią się: kluczem w `id_tokens` (`PYPI_ID_TOKEN` vs `TESTPYPI_ID_TOKEN`), `aud`, `environment`, `rules`, i wartością zmiennej repozytorium (`TWINE_REPOSITORY: pypi|testpypi` przekazywaną do `twine upload --repository "$TWINE_REPOSITORY"`) - dobry kandydat na hidden job (`.publish-cli`) + `extends`, ale `id_tokens` trzeba zadeklarować osobno per job (różny klucz, nie tylko wartość).
- Nazwa `environment:` w `.gitlab-ci.yml` musi być identyczna z nazwą Trusted Publisher skonfigurowanego po stronie PyPI/TestPyPI (konfiguracja ręczna, poza CI).

## Format `license` w pyproject.toml (hatchling) vs klasyfikator `License ::`

Jeśli ticket/projekt wymaga zachowania klasyfikatora `License :: OSI Approved :: ... License` w `classifiers`, NIE używać SPDX-string (`license = "MIT"`) - ustawia core-metadata `License-Expression`, co nowsze narzędzia (setuptools >=77, zgodnie z duchem PEP 639) traktują jako konflikt z klasycznym klasyfikatorem `License ::`.

Bezpieczny wybór: `license = { text = "MIT" }` (classic core-metadata `License` field, bez `License-Expression`) - działa niezależnie od wersji hatchling, klasyfikator zostaje bez ostrzeżeń w `twine check --strict`. Nie trzeba wtedy pinować hatchling w `[build-system]` tylko z tego powodu.

## Bramka wersji tag <-> paczka (bez Pythona)

Gdy wersja pakietu trzymana jest w osobnym pliku (`version.py`, `__version__ = "X.Y.Z"`) czytanym przez `[tool.hatch.version] path = "..."`, bramkę w CI da się zrobić bez importu pakietu, samym `sed`:

```bash
PKG_VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/pkg/version.py)"
TAG_VERSION="${CI_COMMIT_TAG#cli-v}"   # prefiks zależny od projektu
[ "$PKG_VERSION" != "$TAG_VERSION" ] && exit 1
```

Prostsze niż `python -c` (unika problemu z multi-line w Makefile z [[makefile-cli]], choć ta reguła dotyczy Makefile a nie `.gitlab-ci.yml` - w `.gitlab-ci.yml` blok `script: - |` z wieloliniowym shellem jest normalny i bezpieczny).
