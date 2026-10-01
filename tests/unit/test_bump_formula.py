"""Testy skryptu bump_formula: cli/scripts/bump_formula.py (MON-138).

Kontrakt (ustalony przez Team Managera, patrz opis ticketu MON-138):

    PYPI_JSON_URL = "https://pypi.org/pypi/{package}/{version}/json"

    class BumpError(Exception): ...

    def version_from_tag(tag: str) -> str
        "cli-v1.2.3" -> "1.2.3". BumpError dla: brak prefiksu "cli-v", rc
        ("cli-v1.2.3rc1"), niepelna wersja ("cli-v1.2"), smieci.

    def sdist_from_metadata(metadata: dict, version: str) -> tuple[str, str]
        Z odpowiedzi PyPI JSON (endpoint wersji) zwraca (url, sha256) wpisu
        z metadata["urls"] o packagetype == "sdist" (sha256 z
        digests["sha256"]). BumpError gdy: brak wpisu sdist (same wheele),
        metadata["info"]["version"] != version, brak klucza
        urls/digests/sha256 (na poziomie calego dokumentu ALBO pojedynczego
        elementu "urls" - kazdy element jest walidowany, patrz
        .claude/rules/input-boundary-elements.md).

    def fetch_metadata(package: str, version: str, *, attempts: int = 5,
                        delay: float = 10.0, urlopen=urllib.request.urlopen,
                        sleep=time.sleep) -> dict
        GET PYPI_JSON_URL; ponawia na urllib.error.HTTPError 404 i 5xx oraz
        urllib.error.URLError, max `attempts` prob, miedzy probami
        sleep(delay). Inny kod 4xx (np. 403) -> BumpError od razu, bez
        ponowien. Po wyczerpaniu prob -> BumpError. Niepoprawny JSON ->
        BumpError. urlopen wolany jak urllib.request.urlopen(url,
        timeout=...) i zwraca obiekt-context-manager z .read() -> bytes.

    def update_formula(text: str, url: str, sha256: str) -> str
        Podmienia GLOWNE `url "..."` i `sha256 "..."` formuly (linie wciete
        dokladnie 2 spacjami, przed pierwszym blokiem `resource`), bloki
        `resource "..." do ... end` (wciete 4 spacjami) zostawia bajt w
        bajt. BumpError gdy glownego url albo sha256 brak.

    def main(argv: list[str] | None = None) -> int
        Argumenty: --tag (wymagany), --formula (sciezka, wymagany),
        --package (domyslnie "monolynx-cli"). Zapisuje zaktualizowana
        formule w miejscu. Kody wyjscia: 0 OK, 2 BumpError (komunikat na
        stderr, plik formuly NIETKNIETY), 64 blad uzycia (brak argumentu).
        Na stdout po sukcesie jedna linia z wersja, url i sha256.

Bloki `resource` formuly (MON-231) generuje ten sam skrypt, bez Homebrew:

    def normalize_name(name: str) -> str
        PEP 503: lowercase, ciagi "-", "_", "." -> pojedynczy "-".

    def resources_from_report(report: dict, main_package: str)
            -> list[tuple[str, str]]
        Z raportu `pip install --dry-run --ignore-installed --report`:
        (znormalizowana_nazwa, wersja) posortowane po nazwie, BEZ paczki
        glownej (porownanie po znormalizowanej nazwie). BumpError dla
        zlego ksztaltu raportu (kazdy element `install` walidowany osobno,
        komunikat wskazuje indeks `install[i]`), braku paczki glownej,
        duplikatu znormalizowanej nazwy i pustej listy po odjeciu paczki
        glownej.

    def render_resources(resources: list[tuple[str, str, str]]) -> str
        Wejscie (nazwa, url, sha256); bloki `  resource "..." do` rozdzielone
        jedna pusta linia, zakonczone pojedynczym "\\n".

    def replace_resources(text: str, rendered: str) -> str
        Podmienia zakres od pierwszej linii `  resource "` do `  end`
        ostatniego bloku wlacznie; reszta bajt w bajt. BumpError bez bloku.

    main(): opcjonalne `--pip-report PATH`. Z nim druga linia stdout to
        `resources <N>`, a kazdy BumpError (takze w polowie pobierania
        zaleznosci) konczy sie kodem 2 i NIETKNIETA formula.

Fixture raportu i zaleznosci (realne przebiegi, nie modyfikowane):
tests/fixtures/pip/pip-report-monolynx-cli-0.1.0.json (pip 26.2.1, python:3.13,
22 elementy `install`: paczka glowna + 21 zaleznosci) oraz
tests/fixtures/pypi/resources/<znormalizowana-nazwa>-<wersja>.json (21
odpowiedzi `https://pypi.org/pypi/<nazwa>/<wersja>/json`). Bloki `resource` w
cli/packaging/homebrew/monolynx.rb wygenerowal prawdziwy `brew
update-python-resources` dla 0.1.0, wiec przebieg z fixture musi je odtworzyc
bajt w bajt (TestResourcesMatchRealFormula).

Fixture PyPI: tests/fixtures/pypi/pypi-version-json.json - realna odpowiedz
`curl -s https://pypi.org/pypi/platformdirs/4.3.6/json` (monolynx-cli
jeszcze nie jest opublikowany, wiec przechwycono inna paczke z identycznym
schematem endpointu wersji - jedna z zaleznosci CLI). Ma DOKLADNIE 2 wpisy w
"urls": bdist_wheel (pierwszy) i sdist (drugi) - pozwala to odroznic wybor
sdista od "biore pierwszy z listy". Klucz info.description usuniety jako
nieuzywany (patrz naglowek _meta w pliku fixture) - wszystkie pozostale
wartosci sa bajt w bajt takie jak w przechwyconej odpowiedzi. Stale
WHEEL_URL/WHEEL_SHA256/SDIST_URL/SDIST_SHA256 ponizej sa przepisane z tego
pliku recznie (nie wyliczone przez indeksowanie w tescie), zeby asercja nie
zalezala od tej samej logiki wyboru, ktora testuje.
"""

import copy
import importlib.util
import json
import re
import tomllib
import urllib.error
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "cli" / "scripts" / "bump_formula.py"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "pypi" / "pypi-version-json.json"
PIP_REPORT_PATH = REPO_ROOT / "tests" / "fixtures" / "pip" / "pip-report-monolynx-cli-0.1.0.json"
RESOURCES_DIR = REPO_ROOT / "tests" / "fixtures" / "pypi" / "resources"

# Brak pliku ma dac czerwony test/blad zbierania (FileNotFoundError z
# exec_module), nie cichy skip - modul juz istnieje w repo od implementacji
# devops-infra; ciche pomijanie schowaloby wszystkie testy tego pliku, gdyby
# sciezka kiedys sie przesunela.
_spec = importlib.util.spec_from_file_location("bump_formula", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
bump_formula = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bump_formula)


# ---------------------------------------------------------------------------
# Dane z fixture - przepisane recznie, patrz docstring modulu
# ---------------------------------------------------------------------------

FIXTURE_VERSION = "4.3.6"
WHEEL_URL = (
    "https://files.pythonhosted.org/packages/3c/a6/bc1012356d8ece4d66dd75c4b9fc6c1f6650ddd5991e421177d9f8f671be/platformdirs-4.3.6-py3-none-any.whl"
)
WHEEL_SHA256 = "73e575e1408ab8103900836b97580d5307456908a03e92031bab39e4554cc3fb"
SDIST_URL = "https://files.pythonhosted.org/packages/13/fc/128cc9cb8f03208bdbf93d3aa862e16d376844a14f9a0ce5cf4507372de4/platformdirs-4.3.6.tar.gz"
SDIST_SHA256 = "357fb2acbc885b0419afd3ce3ed34564c13c9b95c89360cd9563f73aa5e2b907"


def _load_fixture_metadata() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    raw.pop("_meta", None)
    return raw


# ---------------------------------------------------------------------------
# version_from_tag
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVersionFromTag:
    def test_valid_tag_returns_version(self):
        assert bump_formula.version_from_tag("cli-v1.2.3") == "1.2.3"

    def test_valid_tag_with_larger_numbers(self):
        assert bump_formula.version_from_tag("cli-v12.34.567") == "12.34.567"

    def test_rc_suffix_raises(self):
        with pytest.raises(bump_formula.BumpError):
            bump_formula.version_from_tag("cli-v1.2.3rc1")

    def test_missing_prefix_raises(self):
        with pytest.raises(bump_formula.BumpError):
            bump_formula.version_from_tag("v1.2.3")

    def test_missing_cli_prefix_but_has_v_raises(self):
        with pytest.raises(bump_formula.BumpError):
            bump_formula.version_from_tag("1.2.3")

    def test_incomplete_version_two_components_raises(self):
        with pytest.raises(bump_formula.BumpError):
            bump_formula.version_from_tag("cli-v1.2")

    def test_garbage_raises(self):
        with pytest.raises(bump_formula.BumpError):
            bump_formula.version_from_tag("release-2026-09-28")

    def test_empty_string_raises(self):
        with pytest.raises(bump_formula.BumpError):
            bump_formula.version_from_tag("")


# ---------------------------------------------------------------------------
# sdist_from_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSdistFromMetadata:
    def test_returns_exact_sdist_url_and_sha256_from_fixture(self):
        metadata = _load_fixture_metadata()

        url, sha256 = bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

        assert url == SDIST_URL
        assert sha256 == SDIST_SHA256

    def test_does_not_pick_the_wheel_entry(self):
        """Fixture ma wheel jako PIERWSZY wpis w "urls" - wybor "biore
        indeks 0" przeszedlby test wyzej tylko gdyby sdist byl pierwszy.
        Ta asercja jawnie odrzuca wynik rowny wheelowi."""
        metadata = _load_fixture_metadata()
        assert metadata["urls"][0]["packagetype"] == "bdist_wheel"
        assert metadata["urls"][1]["packagetype"] == "sdist"

        url, sha256 = bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

        assert url != WHEEL_URL
        assert sha256 != WHEEL_SHA256

    def test_missing_sdist_entry_raises(self):
        """Same wheele - filtr na realnym fixture, nie recznie pisany JSON."""
        metadata = _load_fixture_metadata()
        metadata["urls"] = [u for u in metadata["urls"] if u["packagetype"] != "sdist"]
        assert len(metadata["urls"]) == 1

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    def test_version_mismatch_raises(self):
        metadata = _load_fixture_metadata()

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, "9.9.9")

    def test_missing_urls_key_raises(self):
        metadata = _load_fixture_metadata()
        del metadata["urls"]

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    def test_missing_info_version_key_raises(self):
        metadata = _load_fixture_metadata()
        del metadata["info"]["version"]

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    def test_sdist_entry_missing_digests_key_raises(self):
        """Element 'urls' bez klucza 'digests' - walidacja elementu, nie
        tylko korzenia (.claude/rules/input-boundary-elements.md)."""
        metadata = _load_fixture_metadata()
        for entry in metadata["urls"]:
            if entry["packagetype"] == "sdist":
                del entry["digests"]

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    def test_sdist_entry_digests_missing_sha256_key_raises(self):
        metadata = _load_fixture_metadata()
        for entry in metadata["urls"]:
            if entry["packagetype"] == "sdist":
                del entry["digests"]["sha256"]

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    def test_malformed_non_dict_url_entry_among_valid_ones_raises(self):
        """Zdeformowany element w 'urls' (nie-dict) obok poprawnego sdista
        konczy sie czytelnym BumpError, nie AttributeError/TypeError -
        regresja typu MON-149 (parse_report nie walidowal elementow
        'mutants')."""
        metadata = _load_fixture_metadata()
        metadata["urls"] = ["nie-slownik-tylko-string"] + metadata["urls"]

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    def test_urls_not_a_list_raises(self):
        metadata = _load_fixture_metadata()
        metadata["urls"] = {"sdist": "not-a-list-anymore"}

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    def test_sdist_entry_with_empty_url_raises(self):
        """digests.sha256 obecny i poprawny, ale url pusty - mutant
        usuwajacy warunek `not url` (bump_formula.py:102-103)
        zwrocilby (url="", sha256) zamiast BumpError."""
        metadata = _load_fixture_metadata()
        for entry in metadata["urls"]:
            if entry["packagetype"] == "sdist":
                entry["url"] = ""

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    def test_sdist_entry_with_empty_sha256_raises(self):
        metadata = _load_fixture_metadata()
        for entry in metadata["urls"]:
            if entry["packagetype"] == "sdist":
                entry["digests"]["sha256"] = ""

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    def test_sdist_entry_with_non_str_falsy_sha256_raises(self):
        """sha256 obecny w digests, ale wartosc nie-str i falsy (np. 0) -
        rowniez musi trafic w ta sama galaz `not sha256`, nie w KeyError ani
        cichy przejazd dalej."""
        metadata = _load_fixture_metadata()
        for entry in metadata["urls"]:
            if entry["packagetype"] == "sdist":
                entry["digests"]["sha256"] = 0

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)

    @pytest.mark.parametrize("field", ["url", "sha256"])
    def test_sdist_entry_with_non_str_truthy_value_raises(self, field):
        """Wartosc niepusta, ale nie-str (123) - galaz `not x` jej nie
        odrzuca, wiec tylko `isinstance(x, str)` chroni przed wpisaniem
        liczby do formuly."""
        metadata = _load_fixture_metadata()
        for entry in metadata["urls"]:
            if entry["packagetype"] == "sdist":
                if field == "url":
                    entry["url"] = 123
                else:
                    entry["digests"]["sha256"] = 123

        with pytest.raises(bump_formula.BumpError):
            bump_formula.sdist_from_metadata(metadata, FIXTURE_VERSION)


# ---------------------------------------------------------------------------
# fetch_metadata - urlopen/sleep wstrzykniete
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False

    def read(self) -> bytes:
        return self._data


class _FakeUrlopen:
    """Zwraca/rzuca kolejne elementy `items` przy kazdym wywolaniu, w kolejnosci."""

    def __init__(self, items: list):
        self._items = list(items)
        self.calls: list[tuple[str, Any]] = []

    def __call__(self, url: str, timeout: Any = None) -> _FakeResponse:
        self.calls.append((url, timeout))
        if not self._items:
            raise AssertionError("_FakeUrlopen: wywolane wiecej razy niz przygotowano odpowiedzi")
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


class _FakeSleep:
    def __init__(self):
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://pypi.org/pypi/monolynx-cli/1.2.3/json", code, "erreur", None, None)


_VALID_PAYLOAD = json.dumps({"info": {"version": "1.2.3"}, "urls": []}).encode("utf-8")


@pytest.mark.unit
class TestFetchMetadata:
    def test_pypi_json_url_template(self):
        assert bump_formula.PYPI_JSON_URL == "https://pypi.org/pypi/{package}/{version}/json"

    def test_success_on_first_attempt_no_sleep(self):
        fake_urlopen = _FakeUrlopen([_VALID_PAYLOAD])
        fake_sleep = _FakeSleep()

        result = bump_formula.fetch_metadata("monolynx-cli", "1.2.3", urlopen=fake_urlopen, sleep=fake_sleep)

        assert result == {"info": {"version": "1.2.3"}, "urls": []}
        assert len(fake_urlopen.calls) == 1
        assert fake_sleep.calls == []

    def test_request_url_contains_package_and_version(self):
        fake_urlopen = _FakeUrlopen([_VALID_PAYLOAD])
        fake_sleep = _FakeSleep()

        bump_formula.fetch_metadata("some-package", "9.8.7", urlopen=fake_urlopen, sleep=fake_sleep)

        url, timeout = fake_urlopen.calls[0]
        assert url == "https://pypi.org/pypi/some-package/9.8.7/json"
        assert timeout is not None

    def test_retries_on_404_twice_then_succeeds(self):
        fake_urlopen = _FakeUrlopen([_http_error(404), _http_error(404), _VALID_PAYLOAD])
        fake_sleep = _FakeSleep()

        result = bump_formula.fetch_metadata("monolynx-cli", "1.2.3", delay=7.5, urlopen=fake_urlopen, sleep=fake_sleep)
        assert result == {"info": {"version": "1.2.3"}, "urls": []}
        assert len(fake_urlopen.calls) == 3
        assert fake_sleep.calls == [7.5, 7.5]

    def test_retries_on_5xx(self):
        fake_urlopen = _FakeUrlopen([_http_error(503), _VALID_PAYLOAD])
        fake_sleep = _FakeSleep()

        bump_formula.fetch_metadata("monolynx-cli", "1.2.3", urlopen=fake_urlopen, sleep=fake_sleep)

        assert len(fake_urlopen.calls) == 2
        assert len(fake_sleep.calls) == 1

    def test_retries_on_urlerror(self):
        fake_urlopen = _FakeUrlopen(
            [
                urllib.error.URLError("connection refused"),
                urllib.error.URLError("connection refused"),
                _VALID_PAYLOAD,
            ]
        )
        fake_sleep = _FakeSleep()

        result = bump_formula.fetch_metadata("monolynx-cli", "1.2.3", urlopen=fake_urlopen, sleep=fake_sleep)

        assert result == {"info": {"version": "1.2.3"}, "urls": []}
        assert len(fake_urlopen.calls) == 3
        assert len(fake_sleep.calls) == 2

    def test_exhausting_all_attempts_raises_with_exact_call_count(self):
        attempts = 3
        fake_urlopen = _FakeUrlopen([_http_error(404) for _ in range(attempts)])
        fake_sleep = _FakeSleep()

        with pytest.raises(bump_formula.BumpError):
            bump_formula.fetch_metadata(
                "monolynx-cli",
                "1.2.3",
                attempts=attempts,
                delay=0.01,
                urlopen=fake_urlopen,
                sleep=fake_sleep,
            )

        # dokladnie `attempts` prob - lapie off-by-one w obie strony
        assert len(fake_urlopen.calls) == attempts
        # sleep tylko MIEDZY probami, nie po ostatniej porazce
        assert len(fake_sleep.calls) == attempts - 1

    def test_403_raises_immediately_without_retry(self):
        fake_urlopen = _FakeUrlopen([_http_error(403)])
        fake_sleep = _FakeSleep()

        with pytest.raises(bump_formula.BumpError):
            bump_formula.fetch_metadata("monolynx-cli", "1.2.3", attempts=5, urlopen=fake_urlopen, sleep=fake_sleep)

        assert len(fake_urlopen.calls) == 1
        assert fake_sleep.calls == []

    def test_invalid_json_raises_without_retry(self):
        fake_urlopen = _FakeUrlopen([b"{to nie jest json"])
        fake_sleep = _FakeSleep()

        with pytest.raises(bump_formula.BumpError):
            bump_formula.fetch_metadata("monolynx-cli", "1.2.3", attempts=5, urlopen=fake_urlopen, sleep=fake_sleep)

        assert len(fake_urlopen.calls) == 1
        assert fake_sleep.calls == []

    def test_valid_json_with_non_dict_root_raises_without_retry(self):
        """PyPI zawsze zwraca obiekt JSON dla endpointu wersji - poprawny,
        ale nie-obiektowy korzen (np. lista) jest tresciowo zly, nie
        przejsciowy blad sieci: brak retry, BumpError zamiast dalszego
        przetwarzania jako dict (co wybuchloby AttributeError w
        sdist_from_metadata na .get())."""
        fake_urlopen = _FakeUrlopen([json.dumps([1, 2, 3]).encode("utf-8")])
        fake_sleep = _FakeSleep()

        with pytest.raises(bump_formula.BumpError):
            bump_formula.fetch_metadata("monolynx-cli", "1.2.3", attempts=5, urlopen=fake_urlopen, sleep=fake_sleep)

        assert len(fake_urlopen.calls) == 1
        assert fake_sleep.calls == []


# ---------------------------------------------------------------------------
# update_formula
# ---------------------------------------------------------------------------

OLD_MAIN_URL = "https://files.pythonhosted.org/packages/aa/bb/monolynx_cli-1.2.2.tar.gz"
OLD_MAIN_SHA256 = "0" * 64

SAMPLE_FORMULA = f'''class Monolynx < Formula
  include Language::Python::Virtualenv

  desc "CLI klienta Monolynx"
  homepage "https://monolynx.com"
  url "{OLD_MAIN_URL}"
  sha256 "{OLD_MAIN_SHA256}"
  license "MIT"

  depends_on "python@3.12"

  resource "certifi" do
    url "https://files.pythonhosted.org/packages/certifi/certifi-2024.8.30.tar.gz"
    sha256 "bec941d2aa8195e248a60b31ff9f0558284cf01a52591ceda73ea9afffd69fd"
  end

  resource "click" do
    url "https://files.pythonhosted.org/packages/click/click-8.1.7.tar.gz"
    sha256 "ca9853ad459e787e2192211578cc907e7594e294c7ccc834310722b41b9ca61"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    system bin/"monolynx", "--version"
  end
end
'''

NEW_URL = SDIST_URL
NEW_SHA256 = SDIST_SHA256

_RESOURCE_BLOCK_RE = re.compile(r'  resource "[^"]+" do\n(?:.*\n)*?  end\n')


def _resource_blocks(text: str) -> list[str]:
    return _RESOURCE_BLOCK_RE.findall(text)


@pytest.mark.unit
class TestUpdateFormula:
    def test_replaces_main_url_and_sha256(self):
        updated = bump_formula.update_formula(SAMPLE_FORMULA, NEW_URL, NEW_SHA256)

        assert f'  url "{NEW_URL}"\n' in updated
        assert f'  sha256 "{NEW_SHA256}"\n' in updated
        assert OLD_MAIN_URL not in updated
        assert OLD_MAIN_SHA256 not in updated

    def test_resource_blocks_are_byte_identical(self):
        original_blocks = _resource_blocks(SAMPLE_FORMULA)
        assert len(original_blocks) == 2  # certifi + click, sanity na sam fixture tekstowy

        updated = bump_formula.update_formula(SAMPLE_FORMULA, NEW_URL, NEW_SHA256)
        updated_blocks = _resource_blocks(updated)

        assert updated_blocks == original_blocks

    def test_only_one_url_line_and_one_sha256_line_changed(self):
        """Mutant 'zamien WSZYSTKIE url/sha256 w tekscie' zmienilby tez linie
        resource - liczba wystapien starych wartosci resource musi zostac
        taka sama."""
        certifi_sha = "bec941d2aa8195e248a60b31ff9f0558284cf01a52591ceda73ea9afffd69fd"
        click_sha = "ca9853ad459e787e2192211578cc907e7594e294c7ccc834310722b41b9ca61"

        updated = bump_formula.update_formula(SAMPLE_FORMULA, NEW_URL, NEW_SHA256)

        assert updated.count(certifi_sha) == SAMPLE_FORMULA.count(certifi_sha) == 1
        assert updated.count(click_sha) == SAMPLE_FORMULA.count(click_sha) == 1
        assert updated.count(NEW_SHA256) == 1
        assert updated.count(NEW_URL) == 1

    def test_missing_main_url_raises(self):
        text = SAMPLE_FORMULA.replace(f'  url "{OLD_MAIN_URL}"\n', "")

        with pytest.raises(bump_formula.BumpError):
            bump_formula.update_formula(text, NEW_URL, NEW_SHA256)

    def test_missing_main_sha256_raises(self):
        text = SAMPLE_FORMULA.replace(f'  sha256 "{OLD_MAIN_SHA256}"\n', "")

        with pytest.raises(bump_formula.BumpError):
            bump_formula.update_formula(text, NEW_URL, NEW_SHA256)

    def test_formula_with_only_resource_blocks_no_main_raises(self):
        """Bez glownego url/sha256 w ogole - resource url jest wciety 4
        spacjami, wiec nie moze zostac wzieta za glowna (regex na dokladnie
        2 spacje wciecia)."""
        text = (
            "class Foo < Formula\n"
            '  desc "x"\n'
            "\n"
            '  resource "certifi" do\n'
            '    url "https://example.com/certifi.tar.gz"\n'
            '    sha256 "aaaa"\n'
            "  end\n"
            "end\n"
        )

        with pytest.raises(bump_formula.BumpError):
            bump_formula.update_formula(text, NEW_URL, NEW_SHA256)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMain:
    def _fixture_metadata_with_version(self, version: str) -> dict:
        metadata = _load_fixture_metadata()
        metadata["info"]["version"] = version
        return metadata

    def test_success_writes_formula_and_prints_single_summary_line(self, tmp_path, monkeypatch, capsys):
        formula_path = tmp_path / "monolynx.rb"
        formula_path.write_text(SAMPLE_FORMULA)

        seen_calls: list[tuple[str, str]] = []

        def fake_fetch_metadata(package: str, version: str, **kwargs: Any) -> dict:
            seen_calls.append((package, version))
            return self._fixture_metadata_with_version("1.2.3")

        monkeypatch.setattr(bump_formula, "fetch_metadata", fake_fetch_metadata)

        exit_code = bump_formula.main(["--tag", "cli-v1.2.3", "--formula", str(formula_path)])

        assert exit_code == 0
        assert seen_calls == [("monolynx-cli", "1.2.3")]

        updated = formula_path.read_text()
        assert f'  url "{SDIST_URL}"\n' in updated
        assert f'  sha256 "{SDIST_SHA256}"\n' in updated

        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        assert len(lines) == 1
        assert "1.2.3" in lines[0]
        assert SDIST_URL in lines[0]
        assert SDIST_SHA256 in lines[0]
        assert captured.err == ""

    def test_default_package_is_monolynx_cli(self, tmp_path, monkeypatch):
        formula_path = tmp_path / "monolynx.rb"
        formula_path.write_text(SAMPLE_FORMULA)

        seen_calls: list[tuple[str, str]] = []

        def fake_fetch_metadata(package: str, version: str, **kwargs: Any) -> dict:
            seen_calls.append((package, version))
            return self._fixture_metadata_with_version("1.2.3")

        monkeypatch.setattr(bump_formula, "fetch_metadata", fake_fetch_metadata)

        bump_formula.main(["--tag", "cli-v1.2.3", "--formula", str(formula_path)])

        assert seen_calls == [("monolynx-cli", "1.2.3")]

    def test_package_override_is_passed_through(self, tmp_path, monkeypatch):
        formula_path = tmp_path / "monolynx.rb"
        formula_path.write_text(SAMPLE_FORMULA)

        seen_calls: list[tuple[str, str]] = []

        def fake_fetch_metadata(package: str, version: str, **kwargs: Any) -> dict:
            seen_calls.append((package, version))
            return self._fixture_metadata_with_version("1.2.3")

        monkeypatch.setattr(bump_formula, "fetch_metadata", fake_fetch_metadata)

        bump_formula.main(
            [
                "--tag",
                "cli-v1.2.3",
                "--formula",
                str(formula_path),
                "--package",
                "inny-pakiet",
            ]
        )

        assert seen_calls == [("inny-pakiet", "1.2.3")]

    def test_bump_error_from_fetch_exits_2_and_leaves_file_untouched(self, tmp_path, monkeypatch, capsys):
        formula_path = tmp_path / "monolynx.rb"
        formula_path.write_text(SAMPLE_FORMULA)
        original = formula_path.read_text()

        def fake_fetch_metadata(package: str, version: str, **kwargs: Any) -> dict:
            raise bump_formula.BumpError("PyPI niedostepne")

        monkeypatch.setattr(bump_formula, "fetch_metadata", fake_fetch_metadata)

        exit_code = bump_formula.main(["--tag", "cli-v1.2.3", "--formula", str(formula_path)])

        assert exit_code == 2
        assert formula_path.read_text() == original
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_invalid_tag_exits_2_never_touches_network_or_file(self, tmp_path, monkeypatch, capsys):
        formula_path = tmp_path / "monolynx.rb"
        formula_path.write_text(SAMPLE_FORMULA)
        original = formula_path.read_text()

        fetch_was_called = False

        def fake_fetch_metadata(package: str, version: str, **kwargs: Any) -> dict:
            nonlocal fetch_was_called
            fetch_was_called = True
            return self._fixture_metadata_with_version("1.2.3")

        monkeypatch.setattr(bump_formula, "fetch_metadata", fake_fetch_metadata)

        exit_code = bump_formula.main(["--tag", "cli-v1.2.3rc1", "--formula", str(formula_path)])

        assert exit_code == 2
        assert fetch_was_called is False
        assert formula_path.read_text() == original
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_nonexistent_formula_path_exits_2_without_traceback(self, tmp_path, monkeypatch, capsys):
        """--formula wskazuje plik, ktorego nie ma - BumpError czytelny na
        stderr, kod 2, bez tracebacka i bez dotykania sieci (bump_formula.py
        czyta formule PRZED fetch_metadata, wiec siec nigdy nie jest
        wolana)."""
        missing_formula = tmp_path / "nie-istnieje.rb"
        fetch_was_called = False

        def fake_fetch_metadata(package: str, version: str, **kwargs: Any) -> dict:
            nonlocal fetch_was_called
            fetch_was_called = True
            return self._fixture_metadata_with_version("1.2.3")

        monkeypatch.setattr(bump_formula, "fetch_metadata", fake_fetch_metadata)

        exit_code = bump_formula.main(["--tag", "cli-v1.2.3", "--formula", str(missing_formula)])

        assert exit_code == 2
        assert fetch_was_called is False
        assert not missing_formula.exists()
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()
        assert "Traceback" not in captured.err

    def test_missing_tag_argument_exits_64(self, tmp_path, capsys):
        formula_path = tmp_path / "monolynx.rb"
        formula_path.write_text(SAMPLE_FORMULA)

        exit_code = bump_formula.main(["--formula", str(formula_path)])

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_missing_formula_argument_exits_64(self, capsys):
        exit_code = bump_formula.main(["--tag", "cli-v1.2.3"])

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_unknown_argument_exits_64(self, tmp_path, capsys):
        formula_path = tmp_path / "monolynx.rb"
        formula_path.write_text(SAMPLE_FORMULA)

        exit_code = bump_formula.main(
            [
                "--tag",
                "cli-v1.2.3",
                "--formula",
                str(formula_path),
                "--nieznany-argument",
                "x",
            ]
        )

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()


FORMULA_PATH = REPO_ROOT / "cli" / "packaging" / "homebrew" / "monolynx.rb"
CLI_PYPROJECT_PATH = REPO_ROOT / "cli" / "pyproject.toml"


def _normalize(name: str) -> str:
    """Normalizacja nazwy pakietu wg PEP 503 (tak nazywa resource `brew update-python-resources`)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _runtime_dependencies() -> set[str]:
    """Zaleznosci runtime z cli/pyproject.toml bez tych wylaczonych na Pythonie 3.13 z Homebrew (marker python_version < "3.11")."""
    data = tomllib.loads(CLI_PYPROJECT_PATH.read_text(encoding="utf-8"))
    names: set[str] = set()
    for dependency in data["project"]["dependencies"]:
        requirement, _, marker = dependency.partition(";")
        if re.search(r'python_version\s*<\s*["\']3\.11["\']', marker):
            continue
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        assert match is not None, dependency
        names.add(_normalize(match.group(1)))
    return names


@pytest.mark.unit
class TestRealHomebrewFormulaResources:
    """MON-228 A2: formula Homebrew ma resource dla KAZDEJ zaleznosci runtime CLI.

    Bez resource `pyyaml` i `tomli-w` formula instaluje sie, a `monolynx --version` pada na
    `import yaml` / `import tomli_w`. Test czyta prawdziwe pliki, nie wlasny SAMPLE_FORMULA.
    """

    def test_every_runtime_dependency_has_a_resource_block(self):
        formula = FORMULA_PATH.read_text(encoding="utf-8")
        resources = {_normalize(name) for name in re.findall(r'^\s+resource "([^"]+)" do$', formula, re.MULTILINE)}

        missing = sorted(_runtime_dependencies() - resources)

        assert missing == [], f"brak resource w {FORMULA_PATH.name}: {missing}"

    def test_dependency_extraction_is_not_vacuous(self):
        # Zabezpiecza test wyzej przed mutantem "zadna zaleznosc nie jest wymagana": pyyaml i tomli-w
        # (brakujace przed MON-228) sa wymagane, a tomli pominiety przez marker python_version < "3.11".
        dependencies = _runtime_dependencies()
        assert {"typer", "httpx", "pyyaml", "tomli-w"} <= dependencies
        assert "tomli" not in dependencies

    def test_main_package_is_not_declared_as_resource(self):
        formula = FORMULA_PATH.read_text(encoding="utf-8")
        resources = {_normalize(name) for name in re.findall(r'^\s+resource "([^"]+)" do$', formula, re.MULTILINE)}

        assert "monolynx" not in resources
        assert "monolynx-cli" not in resources

    def test_every_resource_has_sdist_url_and_sha256(self):
        formula = FORMULA_PATH.read_text(encoding="utf-8")
        names = re.findall(r'^\s+resource "([^"]+)" do$', formula, re.MULTILINE)
        complete = re.findall(
            r'^  resource "([^"]+)" do\n'
            r'    url "https://files\.pythonhosted\.org/packages/[^"\n]+\.(?:tar\.gz|zip)"\n'
            r'    sha256 "[0-9a-f]{64}"\n'
            r"  end$",
            formula,
            re.MULTILINE,
        )

        assert names
        assert complete == names


# ---------------------------------------------------------------------------
# Bloki resource z raportu pip (MON-231)
# ---------------------------------------------------------------------------

REPORT_MAIN_PACKAGE = "monolynx-cli"
REPORT_VERSION = "0.1.0"

# Przepisane recznie z raportu fixture (21 zaleznosci bez paczki glownej), posortowane po
# znormalizowanej nazwie - oczekiwana lista nie jest wyliczona ta sama logika, ktora testujemy.
EXPECTED_RESOURCES = [
    ("annotated-doc", "0.0.5"),
    ("annotated-types", "0.8.0"),
    ("anyio", "4.15.1"),
    ("certifi", "2026.7.22"),
    ("h11", "0.16.0"),
    ("httpcore", "1.0.9"),
    ("httpx", "0.28.1"),
    ("idna", "3.20"),
    ("markdown-it-py", "4.2.0"),
    ("mdurl", "0.1.2"),
    ("platformdirs", "4.12.2"),
    ("pydantic", "2.13.5"),
    ("pydantic-core", "2.46.5"),
    ("pygments", "2.21.0"),
    ("pyyaml", "6.0.3"),
    ("rich", "15.0.0"),
    ("shellingham", "1.5.4"),
    ("tomli-w", "1.2.0"),
    ("typer", "0.27.2"),
    ("typing-extensions", "4.16.0"),
    ("typing-inspection", "0.4.4"),
]


def _load_report() -> dict:
    return json.loads(PIP_REPORT_PATH.read_text(encoding="utf-8"))


def _load_resource_metadata(name: str, version: str) -> dict:
    return json.loads((RESOURCES_DIR / f"{name}-{version}.json").read_text(encoding="utf-8"))


def _real_formula_text() -> str:
    return FORMULA_PATH.read_text(encoding="utf-8")


def _resources_span(text: str) -> tuple[int, int]:
    """Zakres [start, end) od pierwszej linii `  resource "` do `  end\\n` ostatniego bloku (przed `def install`)."""
    start = text.index('  resource "')
    end = text.index("\n\n  def install") + 1
    return start, end


def _install_index(report: dict, normalized_name: str) -> int:
    for index, element in enumerate(report["install"]):
        if re.sub(r"[-_.]+", "-", element["metadata"]["name"]).lower() == normalized_name:
            return index
    raise AssertionError(normalized_name)


def _resource_triples(report: dict, main_package: str = REPORT_MAIN_PACKAGE) -> list[tuple[str, str, str]]:
    triples = []
    for name, version in bump_formula.resources_from_report(report, main_package):
        url, sha256 = bump_formula.sdist_from_metadata(_load_resource_metadata(name, version), version)
        triples.append((name, url, sha256))
    return triples


@pytest.mark.unit
class TestNormalizeName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("PyYAML", "pyyaml"),
            ("Pygments", "pygments"),
            ("pydantic_core", "pydantic-core"),
            ("tomli_w", "tomli-w"),
            ("zope.interface", "zope-interface"),
            ("Foo.Bar_Baz-Qux", "foo-bar-baz-qux"),
            ("a-_.b", "a-b"),
            ("a__b", "a-b"),
            ("httpx", "httpx"),
        ],
    )
    def test_follows_pep_503(self, raw, expected):
        assert bump_formula.normalize_name(raw) == expected


@pytest.mark.unit
class TestResourcesFromReport:
    def test_returns_sorted_normalized_name_version_pairs_without_main_package(self):
        result = bump_formula.resources_from_report(_load_report(), REPORT_MAIN_PACKAGE)

        assert result == EXPECTED_RESOURCES

    def test_report_fixture_order_is_not_alphabetical(self):
        """Sanity na fixture: gdyby raport juz byl posortowany, test wyzej nie odroznilby sortowania od jego braku."""
        report = _load_report()
        names = [element["metadata"]["name"].lower() for element in report["install"][1:]]

        assert len(report["install"]) == 22
        assert names != sorted(names)

    def test_main_package_matched_by_normalized_name(self):
        """Raport z `Monolynx_CLI`, argument `monolynx-cli` - porownanie surowych napisow zostawiloby paczke glowna."""
        report = _load_report()
        report["install"][_install_index(report, "monolynx-cli")]["metadata"]["name"] = "Monolynx_CLI"

        result = bump_formula.resources_from_report(report, REPORT_MAIN_PACKAGE)

        assert result == EXPECTED_RESOURCES

    def test_main_package_argument_is_normalized_too(self):
        result = bump_formula.resources_from_report(_load_report(), "Monolynx_CLI")

        assert result == EXPECTED_RESOURCES

    def test_main_package_at_the_end_of_install_is_excluded(self):
        """Paczka glowna nie jest zawsze pierwsza - pominiecie `install[0]` zamiast filtra po nazwie zostawiloby ja."""
        report = _load_report()
        report["install"].append(report["install"].pop(0))

        result = bump_formula.resources_from_report(report, REPORT_MAIN_PACKAGE)

        assert result == EXPECTED_RESOURCES

    def test_main_package_missing_from_report_raises(self):
        report = _load_report()
        del report["install"][_install_index(report, "monolynx-cli")]

        with pytest.raises(bump_formula.BumpError):
            bump_formula.resources_from_report(report, REPORT_MAIN_PACKAGE)

    def test_main_package_name_is_exact_not_prefix(self):
        """`monolynx` jest prefiksem `monolynx-cli`, ale to inna paczka - brak w raporcie."""
        with pytest.raises(bump_formula.BumpError):
            bump_formula.resources_from_report(_load_report(), "monolynx")

    def test_report_with_only_main_package_raises(self):
        report = _load_report()
        report["install"] = [report["install"][_install_index(report, "monolynx-cli")]]

        with pytest.raises(bump_formula.BumpError):
            bump_formula.resources_from_report(report, REPORT_MAIN_PACKAGE)

    def test_duplicate_normalized_name_raises(self):
        """`HTTPX` i `httpx` to ta sama paczka po normalizacji - cichy wybor jednej ukrylby niejednoznaczny raport."""
        report = _load_report()
        duplicate = copy.deepcopy(report["install"][_install_index(report, "httpx")])
        duplicate["metadata"]["name"] = "HTTPX"
        report["install"].append(duplicate)

        with pytest.raises(bump_formula.BumpError):
            bump_formula.resources_from_report(report, REPORT_MAIN_PACKAGE)

    @pytest.mark.parametrize("root", [[], "install", None, 7])
    def test_non_dict_report_raises(self, root):
        with pytest.raises(bump_formula.BumpError):
            bump_formula.resources_from_report(root, REPORT_MAIN_PACKAGE)

    def test_missing_install_key_raises(self):
        report = _load_report()
        del report["install"]

        with pytest.raises(bump_formula.BumpError):
            bump_formula.resources_from_report(report, REPORT_MAIN_PACKAGE)

    @pytest.mark.parametrize("install", [{}, None, "pydantic"])
    def test_install_not_a_list_raises(self, install):
        report = _load_report()
        report["install"] = install

        with pytest.raises(bump_formula.BumpError):
            bump_formula.resources_from_report(report, REPORT_MAIN_PACKAGE)

    @pytest.mark.parametrize("case", ["non_dict_element", "no_metadata", "metadata_not_dict"])
    def test_malformed_element_raises_with_its_index(self, case):
        """Zdeformowany element WEWNATRZ listy (nie korzen) - BumpError z indeksem, nie AttributeError/TypeError
        (.claude/rules/input-boundary-elements.md). Indeks 5 lezy za pierwszym elementem."""
        report = _load_report()
        if case == "non_dict_element":
            report["install"][5] = "nie-slownik"
        elif case == "no_metadata":
            del report["install"][5]["metadata"]
        else:
            report["install"][5]["metadata"] = "nie-slownik"

        with pytest.raises(bump_formula.BumpError, match=r"install\[5\]"):
            bump_formula.resources_from_report(report, REPORT_MAIN_PACKAGE)

    @pytest.mark.parametrize("field", ["name", "version"])
    @pytest.mark.parametrize("bad", ["missing", "", 123, None])
    def test_metadata_name_or_version_not_non_empty_str_raises_with_index(self, field, bad):
        report = _load_report()
        if bad == "missing":
            del report["install"][5]["metadata"][field]
        else:
            report["install"][5]["metadata"][field] = bad

        with pytest.raises(bump_formula.BumpError, match=r"install\[5\]"):
            bump_formula.resources_from_report(report, REPORT_MAIN_PACKAGE)


@pytest.mark.unit
class TestRenderResources:
    def test_renders_blocks_separated_by_one_blank_line_ending_with_single_newline(self):
        rendered = bump_formula.render_resources(
            [
                ("alfa", "https://example.com/alfa-1.tar.gz", "a" * 64),
                ("beta", "https://example.com/beta-2.tar.gz", "b" * 64),
            ]
        )

        assert rendered == (
            '  resource "alfa" do\n'
            '    url "https://example.com/alfa-1.tar.gz"\n'
            f'    sha256 "{"a" * 64}"\n'
            "  end\n"
            "\n"
            '  resource "beta" do\n'
            '    url "https://example.com/beta-2.tar.gz"\n'
            f'    sha256 "{"b" * 64}"\n'
            "  end\n"
        )

    def test_single_resource_has_no_blank_line_before_or_after(self):
        rendered = bump_formula.render_resources([("alfa", "https://example.com/alfa-1.tar.gz", "a" * 64)])

        assert rendered == ('  resource "alfa" do\n    url "https://example.com/alfa-1.tar.gz"\n' + f'    sha256 "{"a" * 64}"\n  end\n')


@pytest.mark.unit
class TestReplaceResources:
    @staticmethod
    def _new_blocks() -> str:
        return bump_formula.render_resources(
            [
                ("alfa", "https://example.com/alfa-1.tar.gz", "a" * 64),
                ("beta", "https://example.com/beta-2.tar.gz", "b" * 64),
                ("gamma", "https://example.com/gamma-3.tar.gz", "c" * 64),
            ]
        )

    def test_replaces_whole_range_and_keeps_head_and_tail_byte_identical(self):
        start, end = _resources_span(SAMPLE_FORMULA)
        new_blocks = self._new_blocks()

        result = bump_formula.replace_resources(SAMPLE_FORMULA, new_blocks)

        assert result == SAMPLE_FORMULA[:start] + new_blocks + SAMPLE_FORMULA[end:]

    def test_every_old_block_is_gone_not_only_the_first(self):
        result = bump_formula.replace_resources(SAMPLE_FORMULA, self._new_blocks())

        assert "certifi" not in result
        assert "click" not in result
        assert result.count('  resource "') == 3

    def test_def_install_and_test_block_survive_the_last_end(self):
        """Ostatni `  end` zakresu to koniec ostatniego bloku resource, nie ostatni `  end` w pliku."""
        result = bump_formula.replace_resources(SAMPLE_FORMULA, self._new_blocks())

        assert "\n  def install\n    virtualenv_install_with_resources\n  end\n" in result
        assert '  test do\n    system bin/"monolynx", "--version"\n  end\nend\n' in result

    def test_comment_above_first_resource_is_kept(self):
        text = SAMPLE_FORMULA.replace('  resource "certifi" do', '  # komentarz nad pierwszym blokiem\n  resource "certifi" do', 1)

        result = bump_formula.replace_resources(text, self._new_blocks())

        assert '  # komentarz nad pierwszym blokiem\n  resource "alfa" do\n' in result

    def test_main_url_and_sha256_are_not_touched(self):
        result = bump_formula.replace_resources(SAMPLE_FORMULA, self._new_blocks())

        assert f'  url "{OLD_MAIN_URL}"\n' in result
        assert f'  sha256 "{OLD_MAIN_SHA256}"\n' in result

    def test_fewer_blocks_than_before_removes_the_surplus(self):
        one_block = bump_formula.render_resources([("alfa", "https://example.com/alfa-1.tar.gz", "a" * 64)])
        start, end = _resources_span(SAMPLE_FORMULA)

        result = bump_formula.replace_resources(SAMPLE_FORMULA, one_block)

        assert result == SAMPLE_FORMULA[:start] + one_block + SAMPLE_FORMULA[end:]

    def test_last_resource_block_without_closing_end_raises(self):
        """Bloki resource sa, ale po ostatnim `  resource "` nie ma linii `  end` - inna galaz niz brak blokow."""
        truncated = SAMPLE_FORMULA[: SAMPLE_FORMULA.index("  def install")]
        truncated = truncated[: truncated.rindex("  end\n")]
        assert truncated.count('  resource "') == 2
        assert "\n  end\n" not in truncated[truncated.rindex('  resource "') :]

        with pytest.raises(bump_formula.BumpError, match="zamykajacego"):
            bump_formula.replace_resources(truncated, self._new_blocks())

    def test_formula_without_resource_block_raises(self):
        text = SAMPLE_FORMULA[: SAMPLE_FORMULA.index('  resource "')] + SAMPLE_FORMULA[SAMPLE_FORMULA.index("  def install") :]

        with pytest.raises(bump_formula.BumpError):
            bump_formula.replace_resources(text, self._new_blocks())


@pytest.mark.unit
class TestResourcesMatchRealFormula:
    """Realny raport pip + realne odpowiedzi PyPI musza odtworzyc bloki wygenerowane przez `brew update-python-resources`."""

    def test_fixture_pipeline_reproduces_formula_text_byte_for_byte(self):
        formula = _real_formula_text()
        assert formula.count('  resource "') == 21  # sanity: porownanie nie jest puste

        rendered = bump_formula.render_resources(_resource_triples(_load_report()))
        result = bump_formula.replace_resources(formula, rendered)

        assert result == formula

    def test_rendered_blocks_equal_the_formula_resource_region(self):
        """To samo bez replace_resources: render_resources sam odtwarza zakres blokow z formuly."""
        formula = _real_formula_text()
        start, end = _resources_span(formula)

        rendered = bump_formula.render_resources(_resource_triples(_load_report()))

        assert rendered == formula[start:end]


def _fixture_args(
    tmp_path: Path,
    formula_text: str,
    *extra: str,
    tag: str = "cli-v0.1.0",
    report_path: Path = PIP_REPORT_PATH,
) -> tuple[list[str], Path]:
    formula_path = tmp_path / "monolynx.rb"
    formula_path.write_text(formula_text, encoding="utf-8")
    argv = ["--tag", tag, "--formula", str(formula_path), "--pip-report", str(report_path), *extra]
    return argv, formula_path


class _FakePyPI:
    """Podstawia fetch_metadata bez sieci: zaleznosci z fixture resources/, kazda inna paczka (glowna) z fixture platformdirs."""

    def __init__(self, overrides: dict[tuple[str, str], Any] | None = None):
        self.calls: list[tuple[str, str]] = []
        self._overrides = overrides or {}

    def __call__(self, package: str, version: str, **kwargs: Any) -> dict:
        self.calls.append((package, version))
        override = self._overrides.get((package, version))
        if isinstance(override, Exception):
            raise override
        if override is not None:
            return override
        resource_path = RESOURCES_DIR / f"{package}-{version}.json"
        if resource_path.is_file():
            return json.loads(resource_path.read_text(encoding="utf-8"))
        metadata = _load_fixture_metadata()
        metadata["info"]["version"] = version
        return metadata

    @property
    def dependency_calls(self) -> list[tuple[str, str]]:
        return [call for call in self.calls if call in EXPECTED_RESOURCES]


@pytest.mark.unit
class TestMainWithPipReport:
    def test_replaces_resources_main_url_and_prints_resource_count(self, tmp_path, monkeypatch, capsys):
        """Formula z dwoma starymi resource -> 21 blokow z raportu; oczekiwany wynik zlozony z fragmentow bez uzycia replace_resources."""
        real = _real_formula_text()
        real_start, real_end = _resources_span(real)
        start, end = _resources_span(SAMPLE_FORMULA)
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA)
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        assert exit_code == 0
        head = SAMPLE_FORMULA[:start].replace(OLD_MAIN_URL, SDIST_URL).replace(OLD_MAIN_SHA256, SDIST_SHA256)
        assert formula_path.read_text(encoding="utf-8") == head + real[real_start:real_end] + SAMPLE_FORMULA[end:]
        lines = capsys.readouterr().out.splitlines()
        assert len(lines) == 2
        assert lines[0] == f"{REPORT_VERSION} {SDIST_URL} {SDIST_SHA256}"
        assert lines[1] == "resources 21"

    def test_fetches_main_package_then_each_dependency_exactly_once(self, tmp_path, monkeypatch):
        argv, _ = _fixture_args(tmp_path, SAMPLE_FORMULA)
        fake = _FakePyPI()
        monkeypatch.setattr(bump_formula, "fetch_metadata", fake)

        bump_formula.main(argv)

        assert fake.calls[0] == (REPORT_MAIN_PACKAGE, REPORT_VERSION)
        assert sorted(fake.calls[1:]) == EXPECTED_RESOURCES

    def test_real_formula_run_leaves_resource_region_byte_identical(self, tmp_path, monkeypatch):
        """Przebieg main() na realnej formule: bloki z raportu = bloki w repo, zmienia sie tylko glowny url/sha256."""
        real = _real_formula_text()
        argv, formula_path = _fixture_args(tmp_path, real)
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        assert exit_code == 0
        main_url_line = re.search(r'^  url "[^"]*"\n', real, re.MULTILINE)
        main_sha_line = re.search(r'^  sha256 "[^"]*"\n', real, re.MULTILINE)
        assert main_url_line is not None and main_sha_line is not None
        expected = real.replace(main_url_line.group(0), f'  url "{SDIST_URL}"\n').replace(main_sha_line.group(0), f'  sha256 "{SDIST_SHA256}"\n')
        assert formula_path.read_text(encoding="utf-8") == expected

    def test_package_option_reaches_resources_from_report(self, tmp_path, monkeypatch, capsys):
        """`--package inny-pakiet` nie ma w raporcie - gdyby main() na sztywno szukal `monolynx-cli`, skonczyloby sie kodem 0."""
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA, "--package", "inny-pakiet")
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        assert exit_code == 2
        assert formula_path.read_text(encoding="utf-8") == SAMPLE_FORMULA
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_package_option_is_normalized_when_matching_report(self, tmp_path, monkeypatch, capsys):
        argv, _ = _fixture_args(tmp_path, SAMPLE_FORMULA, "--package", "Monolynx_CLI")
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        assert exit_code == 0
        assert capsys.readouterr().out.splitlines()[1] == "resources 21"

    def test_without_pip_report_resources_stay_untouched_and_no_resources_line(self, tmp_path, monkeypatch, capsys):
        formula_path = tmp_path / "monolynx.rb"
        formula_path.write_text(SAMPLE_FORMULA, encoding="utf-8")
        fake = _FakePyPI()
        monkeypatch.setattr(bump_formula, "fetch_metadata", fake)

        exit_code = bump_formula.main(["--tag", "cli-v0.1.0", "--formula", str(formula_path)])

        assert exit_code == 0
        start, end = _resources_span(SAMPLE_FORMULA)
        result = formula_path.read_text(encoding="utf-8")
        result_start, result_end = _resources_span(result)
        assert result[result_start:result_end] == SAMPLE_FORMULA[start:end]
        assert fake.calls == [(REPORT_MAIN_PACKAGE, REPORT_VERSION)]
        assert len(capsys.readouterr().out.splitlines()) == 1


@pytest.mark.unit
class TestMainWithPipReportErrors:
    """Kazdy BumpError z `--pip-report`: kod 2, komunikat na stderr, pusty stdout, formula NIETKNIETA."""

    def _assert_failed_untouched(self, exit_code: int, formula_path: Path, original: str, capsys) -> str:
        assert exit_code == 2
        assert formula_path.read_text(encoding="utf-8") == original
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()
        assert "Traceback" not in captured.err
        return captured.err

    def test_missing_report_file(self, tmp_path, monkeypatch, capsys):
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA, report_path=tmp_path / "nie-ma.json")
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        self._assert_failed_untouched(exit_code, formula_path, SAMPLE_FORMULA, capsys)

    def test_report_is_not_valid_json(self, tmp_path, monkeypatch, capsys):
        report_path = tmp_path / "report.json"
        report_path.write_text("{to nie jest json", encoding="utf-8")
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA, report_path=report_path)
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        self._assert_failed_untouched(exit_code, formula_path, SAMPLE_FORMULA, capsys)

    def test_report_root_is_not_an_object(self, tmp_path, monkeypatch, capsys):
        report_path = tmp_path / "report.json"
        report_path.write_text("[1, 2, 3]", encoding="utf-8")
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA, report_path=report_path)
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        self._assert_failed_untouched(exit_code, formula_path, SAMPLE_FORMULA, capsys)

    def test_report_without_main_package(self, tmp_path, monkeypatch, capsys):
        report = _load_report()
        del report["install"][_install_index(report, "monolynx-cli")]
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA, report_path=report_path)
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        self._assert_failed_untouched(exit_code, formula_path, SAMPLE_FORMULA, capsys)

    def test_report_version_differs_from_tag_version_stops_before_dependency_fetch(self, tmp_path, monkeypatch, capsys):
        """Tag 0.1.1, raport 0.1.0: glowny url/sha256 dalo by sie podbic, a mimo to plik zostaje i zaleznosci nie sa pobierane."""
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA, tag="cli-v0.1.1")
        fake = _FakePyPI()
        monkeypatch.setattr(bump_formula, "fetch_metadata", fake)

        exit_code = bump_formula.main(argv)

        stderr = self._assert_failed_untouched(exit_code, formula_path, SAMPLE_FORMULA, capsys)
        assert fake.dependency_calls == []
        # 0.1.0 pochodzi wylacznie z raportu (tag niesie 0.1.1) - komunikat wskazuje obie wersje
        assert "0.1.0" in stderr
        assert "0.1.1" in stderr

    def test_dependency_without_sdist_in_the_middle_leaves_file_untouched(self, tmp_path, monkeypatch, capsys):
        """pyyaml (15. z 21) tylko jako wheel; wczesniejsze zaleznosci juz pobrane - zapis czesciowy zmienilby plik."""
        wheel_only = _load_resource_metadata("pyyaml", "6.0.3")
        wheel_only["urls"] = [entry for entry in wheel_only["urls"] if entry["packagetype"] != "sdist"]
        assert wheel_only["urls"]  # sanity: zostaly same wheele
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA)
        fake = _FakePyPI({("pyyaml", "6.0.3"): wheel_only})
        monkeypatch.setattr(bump_formula, "fetch_metadata", fake)

        exit_code = bump_formula.main(argv)

        stderr = self._assert_failed_untouched(exit_code, formula_path, SAMPLE_FORMULA, capsys)
        # komunikat sdist_from_metadata nie zawiera nazwy ani wersji paczki - daje je tylko opakowanie zaleznosci
        assert "pyyaml" in stderr
        assert "6.0.3" in stderr
        assert ("pyyaml", "6.0.3") in fake.calls
        assert ("anyio", "4.15.1") in fake.calls  # wczesniejsza zaleznosc zostala juz pobrana

    def test_dependency_fetch_failure_in_the_middle_leaves_file_untouched(self, tmp_path, monkeypatch, capsys):
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA)
        fake = _FakePyPI({("rich", "15.0.0"): bump_formula.BumpError("PyPI niedostepne")})
        monkeypatch.setattr(bump_formula, "fetch_metadata", fake)

        exit_code = bump_formula.main(argv)

        stderr = self._assert_failed_untouched(exit_code, formula_path, SAMPLE_FORMULA, capsys)
        # wstrzykniety komunikat nie niesie nazwy ani wersji - dopisuje je tylko opakowanie zaleznosci
        assert "rich" in stderr
        assert "15.0.0" in stderr
        assert "PyPI niedostepne" in stderr

    def test_dependency_metadata_for_another_version_leaves_file_untouched(self, tmp_path, monkeypatch, capsys):
        """Odpowiedz PyPI dla innej wersji niz w raporcie to blad (sdist_from_metadata), nie ciche uzycie cudzego sha256."""
        other = _load_resource_metadata("typer", "0.27.2")
        other["info"]["version"] = "0.0.1"
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA)
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI({("typer", "0.27.2"): other}))

        exit_code = bump_formula.main(argv)

        stderr = self._assert_failed_untouched(exit_code, formula_path, SAMPLE_FORMULA, capsys)
        assert "typer" in stderr  # nazwy nie ma w komunikacie sdist_from_metadata ani w wstrzyknietych danych

    def test_report_without_main_package_names_the_missing_package(self, tmp_path, monkeypatch, capsys):
        report = _load_report()
        del report["install"][_install_index(report, "monolynx-cli")]
        report_path = tmp_path / "report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        argv, formula_path = _fixture_args(tmp_path, SAMPLE_FORMULA, report_path=report_path)
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        stderr = self._assert_failed_untouched(exit_code, formula_path, SAMPLE_FORMULA, capsys)
        assert "paczki glownej" in stderr

    def test_formula_without_resource_block_leaves_file_untouched(self, tmp_path, monkeypatch, capsys):
        no_resources = SAMPLE_FORMULA[: SAMPLE_FORMULA.index('  resource "')] + SAMPLE_FORMULA[SAMPLE_FORMULA.index("  def install") :]
        argv, formula_path = _fixture_args(tmp_path, no_resources)
        monkeypatch.setattr(bump_formula, "fetch_metadata", _FakePyPI())

        exit_code = bump_formula.main(argv)

        self._assert_failed_untouched(exit_code, formula_path, no_resources, capsys)
