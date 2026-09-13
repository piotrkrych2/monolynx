"""Testy parsera raportow mutacyjnych: plugin/scripts/mutation_report.py.

TDD: plik mutation_report.py jeszcze NIE ISTNIEJE w chwili pisania tych testow.
Te testy SA specyfikacja jego kontraktu dla backend-developera (MON-149).

Modul eksportuje:
    FORMATS = ("mte-json", "pit-xml", "mutmut", "cargo-mutants")
    parse_report(path: str | Path, fmt: str) -> dict
        Zwraca znormalizowany wynik. Rzuca _InputError (blad tresci wejscia)
        albo _UsageError (nieznany format).
    main(argv: list[str]) -> int
        CLI: --report <plik> --format <fmt>. ZWRACA kod, nie rzuca SystemExit.
        Wynik JSON w JEDNEJ linii na stdout (json.dumps(..., ensure_ascii=False)).
        Bledy na stderr, wtedy stdout PUSTY.
    _InputError(Exception), _UsageError(Exception) - nazwy jak w sprint_run_state.py.

Kody wyjscia main(): 0 OK; 2 blad wejscia (plik nie istnieje, niepoprawny
JSON/XML, raport pusty, raport niezgodny ze schematem formatu); 64 blad
uzycia (brak --report/--format, nieznany --format, nieznany argument).

Wynik parse_report(): klucze ZAWSZE obecne: score (float 0-100 zaokraglony
do 2 miejsc albo None), total (int), killed (int), survived (int),
survivors (list). Niezmienniki: total == killed + survived;
len(survivors) == survived; score is None wtedy i tylko wtedy gdy total == 0;
w przeciwnym razie score == round(100 * killed / total, 2).

Survivor: dokladnie 7 kluczy: file (str), line (int|None), mutator (str),
original (str|None), replacement (str|None), status (str - literal narzedzia
BEZ tlumaczenia), description (str|None). Kolejnosc survivorow = kolejnosc
w raporcie zrodlowym.

Wszystkie literaly statusow ponizej sa wyprowadzone z realnych fixture'ow
(patrz .claude/rules/external-tool-output.md), nie z wyobrazenia autora.
Kazdy plik fixture ma naglowek proweniencji (klucz JSON "_meta", komentarz
XML przed rootem, albo pierwsza linia "# captured: ..." w plikach tekstowych
mutmut) z dokladna komenda, wersja narzedzia i data przechwycenia.

mte-json (Stryker/Stryker.NET/Infection):
    Fixture: tests/fixtures/stryker/mutation-report.10.0.0.json (10 mutantow:
    6 Killed, 4 Survived; projekt demo src/calc.js) i
    mutation-report-clean.10.0.0.json (10 Killed). Realny przebieg
    `npx stryker run` z testRunner=command na node --test.
    Wymagane "files" jako dict (brak albo nie-dict w JSON top-level ->
    _InputError). "files" == {} jest legalne: total 0, score None,
    survivors []. Killed: "Killed", "Timeout". Survived: "Survived",
    "NoCoverage". Poza total: "CompileError", "RuntimeError", "Ignored",
    "Pending" (nie wystepuja w fixture, sa czescia formalnego schematu
    statusow Stryker - https://mutation-testing-elements.netlify.app).
    Survivor: file = klucz w "files" (np. "src/calc.js"); line =
    location.start.line; mutator = mutatorName; replacement = replacement;
    original = wycinek jednolinijkowy z "source" gdy start.line == end.line
    (kolumny 1-based, koniec ekskluzywny: source_line[start.col-1:end.col-1]),
    None gdy wielolinijkowy albo brak "source"; description = description
    mutanta albo None (fixture nie ma tego pola dla zadnego mutanta - zawsze
    None w tych dwoch plikach); status = literal niezmieniony.

pit-xml (PIT):
    Fixture: tests/fixtures/pitest/mutations.1.30.0.xml (7 mutacji: 3
    KILLED, 2 SURVIVED, 2 NO_COVERAGE; projekt demo
    com/example/Calculator.java) i mutations-clean.1.30.0.xml (7 KILLED).
    Realny przebieg pitest 1.30.0 z JUnit4, XMLReportListener.
    Root musi byc "mutations" (inny root -> _InputError). "<mutations/>"
    bez dzieci jest legalne: total 0, score None. Liczenie po atrybucie
    detected: "true" -> killed; "false" -> survived, chyba ze
    status == "NON_VIABLE" (poza total - nie wystepuje w fixture, jest
    czescia formalnego schematu statusow PIT). Survivor: file = katalogi
    pakietu z mutatedClass (kropki -> "/") + "/" + sourceFile (np.
    "com/example/Calculator.java"); line = int(lineNumber); mutator =
    ostatni segment FQCN z <mutator> (np. "ConditionalsBoundaryMutator");
    original = None; replacement = None; description = tekst
    <description>; status = literal ("SURVIVED" / "NO_COVERAGE").

mutmut (mutmut 3):
    Fixture: tests/fixtures/mutmut/results-all.3.8.0.txt (wyjscie
    `mutmut results --all true`: 4 linie wyniku - 1 killed, 1 "no tests",
    2 survived) i results-all-clean.3.8.0.txt (4 killed). Dodatkowo
    results.3.8.0.txt (bez --all: domyslnie ukrywa killed, wiec tylko 3
    linie non-killed) i results-clean.3.8.0.txt (bez --all, WSZYSTKO
    killed -> ZERO linii wyniku w calym pliku, tylko ostrzezenia narzedzia).
    Wejscie to TEKST. Linie wyniku maja postac "<wciecie><id>: <status>"
    gdzie id = "<modul>.x_<funkcja>__mutmut_<N>"; wszystkie inne linie
    (ostrzezenia pip/mutmut, puste) sa ignorowane. ZERO linii wyniku w
    calym pliku -> _InputError (tekst bez markera strukturalnego nie
    odroznia "wszystko zabite bez --all" od "zly plik" - patrz
    results-clean.3.8.0.txt). Killed: "killed", "timeout" (klucz "timeout"
    zaobserwowany jako licznik w export-cicd-stats.3.8.0.json, dokumentacja
    formatu - ten plik NIE jest wejsciem parsera, nie jest testowany tutaj
    jako input, tylko uzasadnia literal). Survived: "survived", "no tests".
    Poza total: "skipped" (z tego samego licznika dokumentacyjnego). Inny
    literal (np. "suspicious") -> survivor z tym statusem. Survivor: file =
    czesc modulowa id z kropkami zamienionymi na "/" + ".py" (np. "calc" ->
    "calc.py"); line = None; mutator = pelny id mutanta (np.
    "calc.x_sub__mutmut_1"); original = None; replacement = None;
    description = nazwa funkcji wyciagnieta z id (np. "sub"); status =
    literal ("survived" / "no tests"). Kazdy plik .txt zaczyna sie od linii
    "# captured: ..." - to naglowek proweniencji fixture'u (patrz
    .claude/rules/external-tool-output.md), NIE czesc wyjscia narzedzia.
    Testy zdejmuja te linie helperem _bez_naglowka_provenience() przed
    podaniem parserowi, bo parser nie zna konwencji fixture'ow testowych.
    Pozostale pliki mutmut w tests/fixtures/mutmut/ (export-cicd-stats*.json,
    meta.3.8.0.json, show.3.8.0.txt) sa dokumentacja formatu - NIE sa
    wejsciem parsera i nie sa uzywane jako input w zadnym tescie ponizej.

cargo-mutants:
    Fixture: tests/fixtures/cargo-mutants/outcomes.27.1.0.json (16
    outcomes: 1 "Baseline" + 15 mutantow: 8 CaughtMutant, 7 MissedMutant;
    liczniki top-level total_mutants=15, caught=8, missed=7, timeout=0,
    unviable=0) i outcomes-clean.27.1.0.json (15 CaughtMutant). Realny
    przebieg cargo-mutants 27.1.0 w kontenerze Linux/Rust na projekcie demo.
    Wymagane "outcomes" jako lista (brak albo nie-lista -> _InputError).
    "outcomes" == [] jest legalne: total 0, score None. Element z
    scenario == "Baseline" (string, nie dict) jest pomijany - mutant wlasciwy
    to element z scenario["Mutant"]. Killed: "CaughtMutant", "Timeout"
    (Timeout zaobserwowany jako licznik top-level "timeout"=0 w obu
    fixture'ach - zaden pojedynczy mutant w fixture nie ma tego statusu,
    literal uzasadniony przez schemat licznikow cargo-mutants). Survived:
    "MissedMutant". Poza total: "Unviable" (licznik top-level "unviable"=0
    w obu fixture'ach, analogicznie). Inny literal -> survivor. Survivor:
    file = Mutant.file; line = Mutant.span.start.line; mutator =
    Mutant.genre; original = None; replacement = Mutant.replacement (string,
    np. "0"); description = Mutant.name (np.
    "src/lib.rs:6:5: replace sub -> i32 with 0"); status = literal
    ("MissedMutant").

Klucz "_meta" najwyzszego poziomu (obecny w fixture'ach stryker i
cargo-mutants; w pitest jest to komentarz XML przed rootem) jest ignorowany
przez parser - to metadane przechwycenia fixture'u dodane przez agenta,
ktory je przechwycil, nie czesc schematu narzedzia mutacyjnego.

Dopoki modul nie istnieje, cala klasa testow jest skipowana (pytest.skip na
poziomie modulu) -- czerwone/skipniete do czasu implementacji, potem zielone.
"""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "plugin" / "scripts" / "mutation_report.py"
FIXTURES = REPO_ROOT / "tests" / "fixtures"
STRYKER_DIR = FIXTURES / "stryker"
PITEST_DIR = FIXTURES / "pitest"
MUTMUT_DIR = FIXTURES / "mutmut"
CARGO_DIR = FIXTURES / "cargo-mutants"

if not MODULE_PATH.exists():
    pytest.skip("mutation_report.py jeszcze nie zaimplementowany (TDD)", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("mutation_report", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
mutation_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutation_report)


def _bez_naglowka_provenience(src_path: Path, tmp_path: Path) -> Path:
    """Zdejmij pierwsza linie "# captured: ..." z fixture'u mutmut i zapisz reszte do tmp_path.

    Fixture'y tekstowe mutmut maja ten naglowek jako konwencje testowa (patrz
    .claude/rules/external-tool-output.md), NIE jako czesc realnego wyjscia
    `mutmut results` - parser nie powinien go nigdy zobaczyc.
    """
    text = src_path.read_text()
    first_newline = text.index("\n")
    assert text.startswith("# captured:"), f"oczekiwano naglowka proweniencji w {src_path}"
    rest = text[first_newline + 1 :]
    dest = tmp_path / src_path.name
    dest.write_text(rest)
    return dest


SURVIVOR_KEYS = {"file", "line", "mutator", "original", "replacement", "status", "description"}
RESULT_KEYS = {"score", "total", "killed", "survived", "survivors"}


# ---------------------------------------------------------------------------
# FORMATS
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatsConstant:
    def test_formats_is_exact_tuple(self):
        assert mutation_report.FORMATS == ("mte-json", "pit-xml", "mutmut", "cargo-mutants")


# ---------------------------------------------------------------------------
# mte-json (Stryker)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseReportMteJson:
    def test_stryker_report_with_survivors(self):
        result = mutation_report.parse_report(STRYKER_DIR / "mutation-report.10.0.0.json", "mte-json")

        assert result["total"] == 10
        assert result["killed"] == 6
        assert result["survived"] == 4
        assert result["score"] == 60.0
        assert len(result["survivors"]) == 4

        assert result["survivors"] == [
            {
                "file": "src/calc.js",
                "line": 6,
                "mutator": "ArithmeticOperator",
                "original": "a - b",
                "replacement": "a + b",
                "status": "Survived",
                "description": None,
            },
            {
                "file": "src/calc.js",
                "line": 5,
                "mutator": "BlockStatement",
                "original": None,
                "replacement": "{}",
                "status": "Survived",
                "description": None,
            },
            {
                "file": "src/calc.js",
                "line": 10,
                "mutator": "ConditionalExpression",
                "original": "n > 0",
                "replacement": "true",
                "status": "Survived",
                "description": None,
            },
            {
                "file": "src/calc.js",
                "line": 10,
                "mutator": "EqualityOperator",
                "original": "n > 0",
                "replacement": "n >= 0",
                "status": "Survived",
                "description": None,
            },
        ]

    def test_stryker_clean_report_has_no_survivors(self):
        result = mutation_report.parse_report(STRYKER_DIR / "mutation-report-clean.10.0.0.json", "mte-json")

        assert result["total"] == 10
        assert result["killed"] == 10
        assert result["survived"] == 0
        assert result["score"] == 100.0
        assert result["survivors"] == []

    def test_stryker_meta_key_is_ignored(self):
        """Fixture ma klucz top-level "_meta" - parser go pomija, nie wybucha."""
        raw = json.loads((STRYKER_DIR / "mutation-report.10.0.0.json").read_text())
        assert "_meta" in raw

        result = mutation_report.parse_report(STRYKER_DIR / "mutation-report.10.0.0.json", "mte-json")
        assert result["total"] == 10


# ---------------------------------------------------------------------------
# pit-xml (PIT)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseReportPitXml:
    def test_pitest_report_with_survivors(self):
        result = mutation_report.parse_report(PITEST_DIR / "mutations.1.30.0.xml", "pit-xml")

        assert result["total"] == 7
        assert result["killed"] == 3
        assert result["survived"] == 4
        assert result["score"] == 42.86
        assert len(result["survivors"]) == 4

        assert result["survivors"] == [
            {
                "file": "com/example/Calculator.java",
                "line": 13,
                "mutator": "ConditionalsBoundaryMutator",
                "original": None,
                "replacement": None,
                "status": "SURVIVED",
                "description": "changed conditional boundary",
            },
            {
                "file": "com/example/Calculator.java",
                "line": 13,
                "mutator": "BooleanTrueReturnValsMutator",
                "original": None,
                "replacement": None,
                "status": "SURVIVED",
                "description": "replaced boolean return with true for com/example/Calculator::isPositive",
            },
            {
                "file": "com/example/Calculator.java",
                "line": 9,
                "mutator": "MathMutator",
                "original": None,
                "replacement": None,
                "status": "NO_COVERAGE",
                "description": "Replaced integer subtraction with addition",
            },
            {
                "file": "com/example/Calculator.java",
                "line": 9,
                "mutator": "PrimitiveReturnsMutator",
                "original": None,
                "replacement": None,
                "status": "NO_COVERAGE",
                "description": "replaced int return with 0 for com/example/Calculator::sub",
            },
        ]

    def test_pitest_clean_report_has_no_survivors(self):
        result = mutation_report.parse_report(PITEST_DIR / "mutations-clean.1.30.0.xml", "pit-xml")

        assert result["total"] == 7
        assert result["killed"] == 7
        assert result["survived"] == 0
        assert result["score"] == 100.0
        assert result["survivors"] == []

    def test_pitest_xml_comment_before_root_is_ignored(self):
        """Fixture ma komentarz XML <!-- captured: ... --> przed <mutations> - parser go pomija."""
        raw = (PITEST_DIR / "mutations.1.30.0.xml").read_text()
        assert raw.lstrip().startswith("<?xml")
        assert "<!-- captured:" in raw

        result = mutation_report.parse_report(PITEST_DIR / "mutations.1.30.0.xml", "pit-xml")
        assert result["total"] == 7


# ---------------------------------------------------------------------------
# mutmut
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseReportMutmut:
    def test_results_all_with_survivors(self, tmp_path):
        report_path = _bez_naglowka_provenience(MUTMUT_DIR / "results-all.3.8.0.txt", tmp_path)
        result = mutation_report.parse_report(report_path, "mutmut")

        assert result["total"] == 4
        assert result["killed"] == 1
        assert result["survived"] == 3
        assert result["score"] == 25.0

        assert result["survivors"] == [
            {
                "file": "calc.py",
                "line": None,
                "mutator": "calc.x_sub__mutmut_1",
                "original": None,
                "replacement": None,
                "status": "no tests",
                "description": "sub",
            },
            {
                "file": "calc.py",
                "line": None,
                "mutator": "calc.x_is_positive__mutmut_1",
                "original": None,
                "replacement": None,
                "status": "survived",
                "description": "is_positive",
            },
            {
                "file": "calc.py",
                "line": None,
                "mutator": "calc.x_is_positive__mutmut_2",
                "original": None,
                "replacement": None,
                "status": "survived",
                "description": "is_positive",
            },
        ]

    def test_results_all_clean_has_no_survivors(self, tmp_path):
        report_path = _bez_naglowka_provenience(MUTMUT_DIR / "results-all-clean.3.8.0.txt", tmp_path)
        result = mutation_report.parse_report(report_path, "mutmut")

        assert result["total"] == 4
        assert result["killed"] == 4
        assert result["survived"] == 0
        assert result["score"] == 100.0
        assert result["survivors"] == []

    def test_results_without_all_flag_hides_killed_documented_limitation(self, tmp_path):
        """Bez --all mutmut nie pokazuje linii killed - parser widzi tylko 3 linie
        non-killed z 4 istniejacych mutantow, wiec total/score sa zanizone. To
        udokumentowane ograniczenie formatu, nie blad parsera."""
        report_path = _bez_naglowka_provenience(MUTMUT_DIR / "results.3.8.0.txt", tmp_path)
        result = mutation_report.parse_report(report_path, "mutmut")

        assert result["total"] == 3
        assert result["killed"] == 0
        assert result["survived"] == 3
        assert result["score"] == 0.0
        assert len(result["survivors"]) == 3

    def test_results_clean_without_all_flag_zero_result_lines_is_input_error(self, tmp_path):
        """Bez --all i ze wszystkim killed, plik nie ma ani jednej linii wyniku
        (same ostrzezenia narzedzia) - nie da sie odroznic od zlego pliku,
        wiec parser rzuca _InputError zamiast cicho zwrocic total=0."""
        report_path = _bez_naglowka_provenience(MUTMUT_DIR / "results-clean.3.8.0.txt", tmp_path)
        with pytest.raises(mutation_report._InputError):
            mutation_report.parse_report(report_path, "mutmut")


# ---------------------------------------------------------------------------
# cargo-mutants
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseReportCargoMutants:
    def test_outcomes_with_survivors(self):
        result = mutation_report.parse_report(CARGO_DIR / "outcomes.27.1.0.json", "cargo-mutants")

        assert result["total"] == 15
        assert result["killed"] == 8
        assert result["survived"] == 7
        assert result["score"] == 53.33
        assert len(result["survivors"]) == 7

        assert result["survivors"][0] == {
            "file": "src/lib.rs",
            "line": 6,
            "mutator": "FnValue",
            "original": None,
            "replacement": "0",
            "status": "MissedMutant",
            "description": "src/lib.rs:6:5: replace sub -> i32 with 0",
        }

    def test_outcomes_clean_has_no_survivors(self):
        result = mutation_report.parse_report(CARGO_DIR / "outcomes-clean.27.1.0.json", "cargo-mutants")

        assert result["total"] == 15
        assert result["killed"] == 15
        assert result["survived"] == 0
        assert result["score"] == 100.0
        assert result["survivors"] == []

    def test_baseline_scenario_entry_is_skipped(self):
        """Pierwszy element outcomes ma scenario == "Baseline" (string, nie
        Mutant) - nie jest to mutant i nie liczy sie do total."""
        raw = json.loads((CARGO_DIR / "outcomes.27.1.0.json").read_text())
        assert raw["outcomes"][0]["scenario"] == "Baseline"

        result = mutation_report.parse_report(CARGO_DIR / "outcomes.27.1.0.json", "cargo-mutants")
        # 16 outcomes - 1 baseline = 15 mutanty, zgodnie z total_mutants w fixture.
        assert result["total"] == 15

    def test_meta_key_is_ignored(self):
        raw = json.loads((CARGO_DIR / "outcomes.27.1.0.json").read_text())
        assert "_meta" in raw

        result = mutation_report.parse_report(CARGO_DIR / "outcomes.27.1.0.json", "cargo-mutants")
        assert result["total"] == 15


# ---------------------------------------------------------------------------
# Niezmienniki modelu wyniku - wszystkie 8 fixture'y kanoniczne (4 formaty x
# z survivorem / bez)
# ---------------------------------------------------------------------------


def _canonical_fixture_cases(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("mutmut-stripped")
    return [
        (STRYKER_DIR / "mutation-report.10.0.0.json", "mte-json"),
        (STRYKER_DIR / "mutation-report-clean.10.0.0.json", "mte-json"),
        (PITEST_DIR / "mutations.1.30.0.xml", "pit-xml"),
        (PITEST_DIR / "mutations-clean.1.30.0.xml", "pit-xml"),
        (_bez_naglowka_provenience(MUTMUT_DIR / "results-all.3.8.0.txt", tmp_path), "mutmut"),
        (
            _bez_naglowka_provenience(MUTMUT_DIR / "results-all-clean.3.8.0.txt", tmp_path),
            "mutmut",
        ),
        (CARGO_DIR / "outcomes.27.1.0.json", "cargo-mutants"),
        (CARGO_DIR / "outcomes-clean.27.1.0.json", "cargo-mutants"),
    ]


@pytest.mark.unit
class TestResultModelInvariants:
    @pytest.fixture
    def cases(self, tmp_path_factory):
        return _canonical_fixture_cases(tmp_path_factory)

    def test_all_eight_canonical_fixtures_satisfy_invariants(self, cases):
        assert len(cases) == 8

        for path, fmt in cases:
            result = mutation_report.parse_report(path, fmt)

            assert set(result.keys()) == RESULT_KEYS, (path, fmt, result.keys())
            assert isinstance(result["total"], int)
            assert isinstance(result["killed"], int)
            assert isinstance(result["survived"], int)
            assert isinstance(result["survivors"], list)
            assert result["score"] is None or isinstance(result["score"], float)

            assert result["total"] == result["killed"] + result["survived"], (path, fmt)
            assert len(result["survivors"]) == result["survived"], (path, fmt)

            if result["total"] == 0:
                assert result["score"] is None, (path, fmt)
            else:
                assert result["score"] == round(100 * result["killed"] / result["total"], 2), (
                    path,
                    fmt,
                )

            for survivor in result["survivors"]:
                assert set(survivor.keys()) == SURVIVOR_KEYS, (path, fmt, survivor)
                assert isinstance(survivor["file"], str)
                assert survivor["line"] is None or isinstance(survivor["line"], int)
                assert isinstance(survivor["mutator"], str)
                assert survivor["original"] is None or isinstance(survivor["original"], str)
                assert survivor["replacement"] is None or isinstance(survivor["replacement"], str)
                assert isinstance(survivor["status"], str)
                assert survivor["description"] is None or isinstance(survivor["description"], str)


# ---------------------------------------------------------------------------
# Przypadki bledne (przez main() z capsys)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMainCliErrorCases:
    @pytest.mark.parametrize("fmt", ["mte-json", "pit-xml", "mutmut", "cargo-mutants"])
    def test_report_file_does_not_exist_exits_2(self, tmp_path, capsys, fmt):
        missing = tmp_path / "nie-istnieje.report"

        exit_code = mutation_report.main(["--report", str(missing), "--format", fmt])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    @pytest.mark.parametrize("fmt", ["mte-json", "pit-xml", "mutmut", "cargo-mutants"])
    def test_empty_report_file_zero_bytes_exits_2(self, tmp_path, capsys, fmt):
        empty_report = tmp_path / "pusty.report"
        empty_report.write_bytes(b"")

        exit_code = mutation_report.main(["--report", str(empty_report), "--format", fmt])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    @pytest.mark.parametrize("fmt", ["mte-json", "cargo-mutants"])
    def test_invalid_json_exits_2(self, tmp_path, capsys, fmt):
        broken = tmp_path / "zly.json"
        broken.write_text("{to nie jest poprawny json")

        exit_code = mutation_report.main(["--report", str(broken), "--format", fmt])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_invalid_xml_exits_2(self, tmp_path, capsys):
        broken = tmp_path / "zly.xml"
        broken.write_text("<mutations><mutation>brak zamkniecia")

        exit_code = mutation_report.main(["--report", str(broken), "--format", "pit-xml"])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_mutmut_zero_result_lines_via_cli_exits_2(self, tmp_path, capsys):
        report_path = _bez_naglowka_provenience(MUTMUT_DIR / "results-clean.3.8.0.txt", tmp_path)

        exit_code = mutation_report.main(["--report", str(report_path), "--format", "mutmut"])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    @pytest.mark.parametrize("bad_payload", ["{}", "[]"])
    def test_mte_json_schema_mismatch_missing_or_wrong_shape_files_exits_2(self, tmp_path, capsys, bad_payload):
        report_path = tmp_path / "schema-zly.json"
        report_path.write_text(bad_payload)

        exit_code = mutation_report.main(["--report", str(report_path), "--format", "mte-json"])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_mte_json_files_present_but_not_a_dict_exits_2(self, tmp_path, capsys):
        report_path = tmp_path / "files-nie-dict.json"
        report_path.write_text(json.dumps({"files": "x"}))

        exit_code = mutation_report.main(["--report", str(report_path), "--format", "mte-json"])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_cargo_mutants_missing_outcomes_key_exits_2(self, tmp_path, capsys):
        report_path = tmp_path / "brak-outcomes.json"
        report_path.write_text("{}")

        exit_code = mutation_report.main(["--report", str(report_path), "--format", "cargo-mutants"])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_cargo_mutants_outcomes_present_but_not_a_list_exits_2(self, tmp_path, capsys):
        report_path = tmp_path / "outcomes-nie-lista.json"
        report_path.write_text(json.dumps({"outcomes": {}}))

        exit_code = mutation_report.main(["--report", str(report_path), "--format", "cargo-mutants"])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_pit_xml_wrong_root_element_exits_2(self, tmp_path, capsys):
        report_path = tmp_path / "zly-root.xml"
        report_path.write_text("<report/>")

        exit_code = mutation_report.main(["--report", str(report_path), "--format", "pit-xml"])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_unknown_format_exits_64(self, tmp_path, capsys):
        report_path = tmp_path / "cokolwiek.json"
        report_path.write_text("{}")

        exit_code = mutation_report.main(["--report", str(report_path), "--format", "gremlins"])

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_missing_format_argument_exits_64(self, tmp_path, capsys):
        report_path = tmp_path / "cokolwiek.json"
        report_path.write_text("{}")

        exit_code = mutation_report.main(["--report", str(report_path)])

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_missing_report_argument_exits_64(self, capsys):
        exit_code = mutation_report.main(["--format", "mte-json"])

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_unknown_argument_exits_64(self, tmp_path, capsys):
        report_path = tmp_path / "cokolwiek.json"
        report_path.write_text("{}")

        exit_code = mutation_report.main(
            [
                "--report",
                str(report_path),
                "--format",
                "mte-json",
                "--nieznany-argument",
                "x",
            ]
        )

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()


# ---------------------------------------------------------------------------
# CLI happy path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMainCliHappyPath:
    def test_valid_mte_json_report_exits_0_and_prints_single_line_json(self, capsys):
        exit_code = mutation_report.main(
            [
                "--report",
                str(STRYKER_DIR / "mutation-report.10.0.0.json"),
                "--format",
                "mte-json",
            ]
        )

        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        lines = captured.out.splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["total"] == 10
        assert payload["killed"] == 6
        assert payload["survived"] == 4
        assert payload["score"] == 60.0
