#!/usr/bin/env python3
"""Podbij glowny url/sha256 formuly Homebrew monolynx.rb na podstawie tagu wydania (MON-138).

Wywolywany przez job CI `bump-homebrew` (stage `publish`, ten sam warunek co
`publish-pypi`: tag `cli-v<wersja>`) po opublikowaniu sdista na PyPI. Zrodlem
prawdy o url/sha256 sdista jest sam PyPI (endpoint per-wersja), nie lokalny
build - `publish-pypi` juz przeslal ten dokladny plik, wiec jego metadane sa
tym, co uzytkownik faktycznie pobierze. Bloki `resource` (zaleznosci
przechodnie) sa poza zakresem tego skryptu - reguluje je osobno
`brew update-python-resources`, uruchamiane po tym skrypcie w tym samym
jobie CI.

Modul eksportuje:
    PYPI_JSON_URL = "https://pypi.org/pypi/{package}/{version}/json"
    class BumpError(Exception): ...
    version_from_tag(tag) -> str
    sdist_from_metadata(metadata, version) -> tuple[str, str]
    fetch_metadata(package, version, *, attempts=5, delay=10.0,
                    urlopen=..., sleep=...) -> dict
    update_formula(text, url, sha256) -> str
    main(argv=None) -> int

Kody wyjscia main(): 0 OK; 2 BumpError (komunikat na stderr, plik formuly
NIETKNIETY); 64 blad uzycia CLI (brak/nieznany argument). Po sukcesie jedna
linia na stdout: wersja, url, sha256.

Zero zaleznosci spoza biblioteki standardowej - skrypt dziala w obrazie CI
Homebrew, gdzie nic poza systemowym Pythonem nie jest zagwarantowane.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PYPI_JSON_URL = "https://pypi.org/pypi/{package}/{version}/json"

# "cli-vX.Y.Z" scisle - bez sufiksu rc (release candidate na TestPyPI nigdy
# nie trafia do Homebrew) i bez niepelnych wersji.
_TAG_RE = re.compile(r"^cli-v(\d+\.\d+\.\d+)$")

# Wciecie dokladnie 2 spacje odroznia glowny url/sha256 formuly od tych
# samych slow kluczowych wewnatrz blokow `resource "..." do ... end`,
# wcietych 4 spacjami (patrz SAMPLE_FORMULA w tests/unit/test_bump_formula.py).
_MAIN_URL_RE = re.compile(r'^  url "([^"]*)"\n', re.MULTILINE)
_MAIN_SHA256_RE = re.compile(r'^  sha256 "([^"]*)"\n', re.MULTILINE)


class BumpError(Exception):
    """Blad tresci wejscia: tag, metadane PyPI albo formula (kod wyjscia 2)."""


class _UsageError(Exception):
    """Blad argumentow CLI (kod wyjscia 64)."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        # argparse domyslnie wola sys.exit(2), co kolidowaloby z kodem 2
        # zarezerwowanym dla BumpError - zamieniamy na wyjatek do
        # przechwycenia w main() i zamapowania na kod 64.
        raise _UsageError(message)


def version_from_tag(tag: str) -> str:
    match = _TAG_RE.match(tag)
    if match is None:
        raise BumpError(f"nieprawidlowy tag wydania '{tag}' (oczekiwano cli-vX.Y.Z, bez sufiksu rc)")
    return match.group(1)


def sdist_from_metadata(metadata: dict, version: str) -> tuple[str, str]:
    info = metadata.get("info")
    if not isinstance(info, dict) or "version" not in info:
        raise BumpError("metadane PyPI: brak klucza 'info.version'")
    if info["version"] != version:
        raise BumpError(f"metadane PyPI: wersja odpowiedzi ({info['version']!r}) != oczekiwana ({version!r})")

    urls = metadata.get("urls")
    if not isinstance(urls, list):
        raise BumpError("metadane PyPI: brak klucza 'urls' (lista)")

    for index, entry in enumerate(urls):
        # Kazdy element 'urls' walidowany osobno (nie tylko korzen) -
        # zdeformowany wpis konczy sie czytelnym BumpError, nie
        # AttributeError/TypeError (.claude/rules/input-boundary-elements.md).
        if not isinstance(entry, dict):
            raise BumpError(f"metadane PyPI: urls[{index}] nie jest obiektem JSON")
        if entry.get("packagetype") != "sdist":
            continue
        digests = entry.get("digests")
        if not isinstance(digests, dict) or "sha256" not in digests:
            raise BumpError(f"metadane PyPI: urls[{index}] (sdist) bez digests.sha256")
        url, sha256 = entry.get("url"), digests["sha256"]
        if not isinstance(url, str) or not url or not isinstance(sha256, str) or not sha256:
            raise BumpError(f"metadane PyPI: urls[{index}] (sdist) z pustym albo niepoprawnym url/sha256")
        return url, sha256

    raise BumpError("metadane PyPI: brak wpisu 'sdist' w 'urls' (sama paczka .whl?)")


def fetch_metadata(
    package: str,
    version: str,
    *,
    attempts: int = 5,
    delay: float = 10.0,
    urlopen: Any = urllib.request.urlopen,
    sleep: Any = time.sleep,
) -> dict:
    url = PYPI_JSON_URL.format(package=package, version=version)
    last_error: BaseException | None = None

    for attempt in range(1, attempts + 1):
        try:
            with urlopen(url, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            # 404 (metadane jeszcze nie rozpropagowane) i 5xx (awaria PyPI) sa
            # przejsciowe - ponawiamy. Kazdy inny kod 4xx (np. 403) jest
            # trwaly, wiec ponowienie nic by nie dalo.
            if exc.code != 404 and exc.code < 500:
                raise BumpError(f"PyPI zwrocilo blad {exc.code} dla {package} {version}") from exc
            last_error = exc
            if attempt < attempts:
                sleep(delay)
            continue
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt < attempts:
                sleep(delay)
            continue

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BumpError(f"PyPI: niepoprawny JSON w odpowiedzi dla {package} {version}: {exc}") from exc
        # Korzen musi byc obiektem JSON - inaczej sdist_from_metadata() dostalby
        # cos bez .get() i wywrocilby sie AttributeError-em zamiast BumpError
        # (granica z zewnetrznym API, patrz .claude/rules/input-boundary-elements.md).
        if not isinstance(parsed, dict):
            raise BumpError(f"PyPI: oczekiwano obiektu JSON na najwyzszym poziomie odpowiedzi dla {package} {version}")
        return parsed

    raise BumpError(f"PyPI niedostepne dla {package} {version} po {attempts} probach: {last_error}")


def update_formula(text: str, url: str, sha256: str) -> str:
    if _MAIN_URL_RE.search(text) is None:
        raise BumpError("formula: brak glownego wiersza 'url' (wciecie dokladnie 2 spacje)")
    if _MAIN_SHA256_RE.search(text) is None:
        raise BumpError("formula: brak glownego wiersza 'sha256' (wciecie dokladnie 2 spacje)")

    updated = _MAIN_URL_RE.sub(f'  url "{url}"\n', text, count=1)
    updated = _MAIN_SHA256_RE.sub(f'  sha256 "{sha256}"\n', updated, count=1)
    return updated


def _read_formula(path: Path) -> str:
    if not path.is_file():
        raise BumpError(f"nie znaleziono pliku formuly '{path}'")
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = _ArgumentParser(
        prog="bump_formula.py",
        description="Zaktualizuj glowny url/sha256 formuly Homebrew na podstawie tagu wydania monolynx-cli.",
    )
    parser.add_argument("--tag", required=True, help="tag wydania, np. cli-v1.2.3")
    parser.add_argument("--formula", required=True, help="sciezka do pliku formuly .rb")
    parser.add_argument("--package", default="monolynx-cli", help="nazwa paczki PyPI (domyslnie monolynx-cli)")

    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"bump_formula: {exc}", file=sys.stderr)
        return 64

    formula_path = Path(args.formula)

    try:
        version = version_from_tag(args.tag)
        original_text = _read_formula(formula_path)
        metadata = fetch_metadata(args.package, version)
        url, sha256 = sdist_from_metadata(metadata, version)
        updated_text = update_formula(original_text, url, sha256)
    except BumpError as exc:
        print(f"bump_formula: {exc}", file=sys.stderr)
        return 2

    formula_path.write_text(updated_text, encoding="utf-8")
    print(f"{version} {url} {sha256}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
