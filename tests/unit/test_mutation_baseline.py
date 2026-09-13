"""Testy generatora zapadki baseline dla testow mutacyjnych: plugin/scripts/mutation_baseline.py.

TDD: plik mutation_baseline.py jeszcze NIE ISTNIEJE w chwili pisania tych testow.
Te testy SA specyfikacja jego zamrozonego kontraktu dla backend-developera (MON-151):

    load_summary(path: str | Path) -> dict
    load_baseline(path: str | Path) -> dict | None
    compare(summary: dict, baseline: dict | None, scope: str) -> dict
    build_baseline(summary: dict, scope: str, commit: str | None, tool: str | None, measured_at: str) -> dict
    write_baseline(path: str | Path, data: dict) -> None
    format_report(result: dict) -> str
    main(argv: list[str]) -> int
    _InputError(Exception), _UsageError(Exception)

Dopoki modul nie istnieje, cala klasa testow jest skipowana (pytest.skip na
poziomie modulu) -- czerwone/skipniete do czasu implementacji, potem zielone.

load_summary czyta wynik plugin/scripts/mutation_report.py (parser raportow
narzedzi mutacyjnych do wspolnego modelu: score, total, killed, survived,
survivors). Wymaga kluczy score (float|None), total (int), killed (int),
survived (int); inne klucze (survivors, _meta) ignoruje. Brak pliku,
niepoprawny JSON, brak wymaganego klucza, zly typ, top-level nie-dict ->
_InputError.

load_baseline czyta plik baseline (commitowany przez wlasciciela projektu w
repo docelowym). Brak pliku -> None (pierwszy przebieg). Plik obecny ale
uszkodzony (zly JSON, brak "score"/"scope", zly typ) -> _InputError -- baseline
uszkodzony NIGDY nie jest cicho nadpisywany.

compare wyznacza jeden z szesciu statusow, w tej kolejnosci sprawdzania:
    1. summary["score"] is None (total == 0) -> "no-mutants" (ma pierwszenstwo
       przed "first" i przed "scope-mismatch"; baseline w wyniku to
       baseline["score"] jesli baseline istnieje, inaczej None)
    2. baseline is None -> "first"
    3. baseline["scope"] != scope -> "scope-mismatch"
    4. score > baseline["score"] -> "up"
    5. score == baseline["score"] -> "same"
    6. score < baseline["score"] -> "down"
Wynik ZAWSZE ma dokladnie klucze: status, score, baseline, delta, survived,
total, scope, write. delta = round(score - baseline_score, 2) gdy oba
obecne, inaczej None. write=True tylko dla "first" i "up" -- zapadka: baseline
moze tylko rosnac, spadek jest widoczny (format_report, UWAGA) ale NIE oblewa
buildu (main() zawsze zwraca 0 dla kazdego statusu poza bledami wejscia/uzycia).

build_baseline zwraca dokladnie 7 kluczy: score, survived, total, scope,
measured_at, commit, tool -- przepisane z parametrow, bez przeliczen.

write_baseline zapisuje JSON (indent=2, ensure_ascii=False, newline na koncu),
tworzy katalog nadrzedny gdy trzeba; blad zapisu (sciezka jest katalogiem) ->
_InputError.

format_report zwraca raport konczacy sie DOKLADNIE jedna zamrozona linia:
    MUTATION-BASELINE: status=<status> score=<x|none> baseline=<y|none> delta=<z|none> survived=<n> total=<t> scope=<scope>
liczby przez str() (np. "100.0", "-15.5"), braki jako "none". Status "down"
dodaje gdzies w raporcie widoczne ostrzezenie "UWAGA"; status "first" wspomina
o zapisaniu pierwszego pomiaru.

main() CLI: --summary <plik|-> --baseline <plik> [--scope full|diff]
[--commit <sha>] [--tool <nazwa>] [--dry-run]. --scope domyslnie "diff".
Zwraca kod (nie rzuca SystemExit): 0 dla kazdego statusu (takze down,
no-mutants, scope-mismatch), 2 blad wejscia (_InputError), 64 blad uzycia
(_UsageError: brak wymaganego argumentu, nieznany argument, zly --scope).
Raport na stdout, bledy na stderr -- przy bledzie stdout jest pusty. Zapis
baseline'u tylko gdy result["write"] i nie --dry-run.

Fixture'y w tests/fixtures/mutation-report/ sa realnym zrzutem
plugin/scripts/mutation_report.py (przechwyconym przez Team Managera z
origin/worktree-MON-149, plugin 1.9.0) -- patrz .claude/rules/external-tool-output.md
pkt 1. Klucz "_meta" jest ignorowany przez load_summary.
"""

import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "mutation_baseline.py"
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "mutation-report"

if not MODULE_PATH.exists():
    pytest.skip("mutation_baseline.py jeszcze nie zaimplementowany (TDD)", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("mutation_baseline", MODULE_PATH)
assert _spec is not None and _spec.loader is not None
mb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mb)

FIXTURE_STRYKER = FIXTURES_DIR / "summary-stryker.1.9.0.json"
FIXTURE_STRYKER_CLEAN = FIXTURES_DIR / "summary-stryker-clean.1.9.0.json"
FIXTURE_MUTMUT = FIXTURES_DIR / "summary-mutmut.1.9.0.json"
FIXTURE_EMPTY = FIXTURES_DIR / "summary-empty.1.9.0.json"


def write_json(path, data):
    path.write_text(json.dumps(data))


def make_baseline_data(score=55.0, survived=9, total=20, scope="diff", measured_at="2026-01-01", commit=None, tool=None):
    return {
        "score": score,
        "survived": survived,
        "total": total,
        "scope": scope,
        "measured_at": measured_at,
        "commit": commit,
        "tool": tool,
    }


# ---------------------------------------------------------------------------
# load_summary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadSummary:
    def test_stryker_fixture_has_expected_values(self):
        result = mb.load_summary(FIXTURE_STRYKER)
        assert result["score"] == 60.0
        assert result["total"] == 10
        assert result["killed"] == 6
        assert result["survived"] == 4

    def test_stryker_clean_fixture_has_expected_values(self):
        result = mb.load_summary(FIXTURE_STRYKER_CLEAN)
        assert result["score"] == 100.0
        assert result["total"] == 10
        assert result["killed"] == 10
        assert result["survived"] == 0

    def test_mutmut_fixture_has_expected_values(self):
        result = mb.load_summary(FIXTURE_MUTMUT)
        assert result["score"] == 25.0
        assert result["total"] == 4
        assert result["killed"] == 1
        assert result["survived"] == 3

    def test_empty_fixture_has_none_score_and_zero_totals(self):
        result = mb.load_summary(FIXTURE_EMPTY)
        assert result["score"] is None
        assert result["total"] == 0
        assert result["killed"] == 0
        assert result["survived"] == 0

    def test_meta_and_survivors_keys_are_ignored_not_required(self):
        result = mb.load_summary(FIXTURE_MUTMUT)
        assert "score" in result
        assert "total" in result

    def test_missing_file_raises_input_error(self, tmp_path):
        missing = tmp_path / "nie-istnieje.json"
        with pytest.raises(mb._InputError):
            mb.load_summary(missing)

    def test_invalid_json_raises_input_error(self, tmp_path):
        path = tmp_path / "zly.json"
        path.write_text("{to nie jest poprawny json")
        with pytest.raises(mb._InputError):
            mb.load_summary(path)

    def test_top_level_list_raises_input_error(self, tmp_path):
        path = tmp_path / "lista.json"
        write_json(path, [1, 2, 3])
        with pytest.raises(mb._InputError):
            mb.load_summary(path)

    def test_missing_score_key_raises_input_error(self, tmp_path):
        path = tmp_path / "brak-score.json"
        write_json(path, {"total": 10, "killed": 6, "survived": 4})
        with pytest.raises(mb._InputError):
            mb.load_summary(path)

    def test_total_as_string_raises_input_error(self, tmp_path):
        path = tmp_path / "zly-typ.json"
        write_json(path, {"score": 60.0, "total": "10", "killed": 6, "survived": 4})
        with pytest.raises(mb._InputError):
            mb.load_summary(path)


# ---------------------------------------------------------------------------
# load_baseline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadBaseline:
    def test_missing_file_returns_none(self, tmp_path):
        missing = tmp_path / "brak-baseline.json"
        assert mb.load_baseline(missing) is None

    def test_valid_baseline_returns_dict(self, tmp_path):
        path = tmp_path / "baseline.json"
        data = make_baseline_data()
        write_json(path, data)
        result = mb.load_baseline(path)
        assert result["score"] == 55.0
        assert result["scope"] == "diff"

    def test_invalid_json_raises_input_error(self, tmp_path):
        path = tmp_path / "zly.json"
        path.write_text("{to nie jest poprawny json")
        with pytest.raises(mb._InputError):
            mb.load_baseline(path)

    def test_missing_score_key_raises_input_error(self, tmp_path):
        path = tmp_path / "brak-score.json"
        write_json(path, {"scope": "diff"})
        with pytest.raises(mb._InputError):
            mb.load_baseline(path)

    def test_missing_scope_key_raises_input_error(self, tmp_path):
        path = tmp_path / "brak-scope.json"
        write_json(path, {"score": 55.0})
        with pytest.raises(mb._InputError):
            mb.load_baseline(path)

    def test_score_as_string_raises_input_error(self, tmp_path):
        path = tmp_path / "zly-typ.json"
        write_json(path, {"score": "55.0", "scope": "diff"})
        with pytest.raises(mb._InputError):
            mb.load_baseline(path)


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


_COMPARE_RESULT_KEYS = frozenset({"status", "score", "baseline", "delta", "survived", "total", "scope", "write"})


@pytest.mark.unit
class TestCompare:
    def test_result_has_exactly_the_frozen_keys(self):
        summary = {"score": 60.0, "total": 10, "killed": 6, "survived": 4}
        result = mb.compare(summary, None, "diff")
        assert set(result.keys()) == _COMPARE_RESULT_KEYS

    def test_first_status_when_baseline_is_none(self):
        summary = {"score": 60.0, "total": 10, "killed": 6, "survived": 4}
        result = mb.compare(summary, None, "diff")
        assert result["status"] == "first"
        assert result["baseline"] is None
        assert result["delta"] is None
        assert result["write"] is True
        assert result["score"] == 60.0
        assert result["survived"] == 4
        assert result["total"] == 10
        assert result["scope"] == "diff"

    def test_up_status_when_score_higher_than_baseline(self):
        summary = {"score": 60.0, "total": 10, "killed": 6, "survived": 4}
        baseline = {"score": 55.0, "scope": "diff"}
        result = mb.compare(summary, baseline, "diff")
        assert result["status"] == "up"
        assert result["delta"] == 5.0
        assert result["baseline"] == 55.0
        assert result["write"] is True

    def test_same_status_when_score_equal_to_baseline(self):
        summary = {"score": 25.0, "total": 4, "killed": 1, "survived": 3}
        baseline = {"score": 25.0, "scope": "diff"}
        result = mb.compare(summary, baseline, "diff")
        assert result["status"] == "same"
        assert result["delta"] == 0.0
        assert result["write"] is False

    def test_down_status_when_score_lower_than_baseline(self):
        summary = {"score": 60.0, "total": 10, "killed": 6, "survived": 4}
        baseline = {"score": 75.5, "scope": "diff"}
        result = mb.compare(summary, baseline, "diff")
        assert result["status"] == "down"
        assert result["delta"] == -15.5
        assert result["write"] is False

    def test_no_mutants_status_when_score_none_and_no_baseline(self):
        summary = {"score": None, "total": 0, "killed": 0, "survived": 0}
        result = mb.compare(summary, None, "diff")
        assert result["status"] == "no-mutants"
        assert result["baseline"] is None
        assert result["delta"] is None
        assert result["write"] is False

    def test_no_mutants_status_uses_baseline_score_when_baseline_present(self):
        summary = {"score": None, "total": 0, "killed": 0, "survived": 0}
        baseline = {"score": 75.5, "scope": "diff"}
        result = mb.compare(summary, baseline, "diff")
        assert result["status"] == "no-mutants"
        assert result["baseline"] == 75.5
        assert result["delta"] is None
        assert result["write"] is False

    def test_no_mutants_has_precedence_over_scope_mismatch(self):
        summary = {"score": None, "total": 0, "killed": 0, "survived": 0}
        baseline = {"score": 75.5, "scope": "full"}
        result = mb.compare(summary, baseline, "diff")
        assert result["status"] == "no-mutants"
        assert result["baseline"] == 75.5

    def test_scope_mismatch_status_computes_orientation_delta(self):
        summary = {"score": 60.0, "total": 10, "killed": 6, "survived": 4}
        baseline = {"score": 55.0, "scope": "full"}
        result = mb.compare(summary, baseline, "diff")
        assert result["status"] == "scope-mismatch"
        assert result["delta"] == 5.0
        assert result["baseline"] == 55.0
        assert result["write"] is False

    @pytest.mark.parametrize(
        "summary,baseline,scope,expected_write",
        [
            ({"score": 60.0, "total": 10, "killed": 6, "survived": 4}, None, "diff", True),
            ({"score": 60.0, "total": 10, "killed": 6, "survived": 4}, {"score": 55.0, "scope": "diff"}, "diff", True),
            ({"score": 25.0, "total": 4, "killed": 1, "survived": 3}, {"score": 25.0, "scope": "diff"}, "diff", False),
            ({"score": 60.0, "total": 10, "killed": 6, "survived": 4}, {"score": 75.5, "scope": "diff"}, "diff", False),
            ({"score": None, "total": 0, "killed": 0, "survived": 0}, None, "diff", False),
            ({"score": 60.0, "total": 10, "killed": 6, "survived": 4}, {"score": 55.0, "scope": "full"}, "diff", False),
        ],
        ids=["first", "up", "same", "down", "no-mutants", "scope-mismatch"],
    )
    def test_write_flag_true_only_for_first_and_up(self, summary, baseline, scope, expected_write):
        result = mb.compare(summary, baseline, scope)
        assert result["write"] == expected_write


# ---------------------------------------------------------------------------
# build_baseline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildBaseline:
    def test_returns_exactly_seven_keys_with_copied_values(self):
        summary = {"score": 60.0, "total": 10, "killed": 6, "survived": 4}
        result = mb.build_baseline(summary, scope="diff", commit="abc123", tool="stryker", measured_at="2026-01-01")
        assert set(result.keys()) == {"score", "survived", "total", "scope", "measured_at", "commit", "tool"}
        assert result["score"] == 60.0
        assert result["survived"] == 4
        assert result["total"] == 10
        assert result["scope"] == "diff"
        assert result["measured_at"] == "2026-01-01"
        assert result["commit"] == "abc123"
        assert result["tool"] == "stryker"

    def test_commit_and_tool_are_none_when_not_provided(self):
        summary = {"score": 60.0, "total": 10, "killed": 6, "survived": 4}
        result = mb.build_baseline(summary, scope="diff", commit=None, tool=None, measured_at="2026-01-01")
        assert result["commit"] is None
        assert result["tool"] is None


# ---------------------------------------------------------------------------
# write_baseline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWriteBaseline:
    def test_file_is_created_with_trailing_newline_and_readable_json(self, tmp_path):
        path = tmp_path / "baseline.json"
        data = make_baseline_data(score=60.0, survived=4, total=10, measured_at="2026-01-01")
        mb.write_baseline(path, data)
        text = path.read_text()
        assert text.endswith("\n")
        assert json.loads(text) == data

    def test_parent_directory_is_created_if_missing(self, tmp_path):
        path = tmp_path / "nowy" / "podkatalog" / "baseline.json"
        data = make_baseline_data()
        mb.write_baseline(path, data)
        assert path.is_file()
        assert json.loads(path.read_text()) == data

    def test_directory_path_raises_input_error(self, tmp_path):
        dir_path = tmp_path / "to-jest-katalog"
        dir_path.mkdir()
        with pytest.raises(mb._InputError):
            mb.write_baseline(dir_path, make_baseline_data())


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatReport:
    def test_up_status_last_line_matches_frozen_format(self):
        result = {
            "status": "up",
            "score": 60.0,
            "baseline": 55.0,
            "delta": 5.0,
            "survived": 4,
            "total": 10,
            "scope": "diff",
            "write": True,
        }
        report = mb.format_report(result)
        last_line = report.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=up score=60.0 baseline=55.0 delta=5.0 survived=4 total=10 scope=diff")

    def test_first_status_none_baseline_and_delta_mentions_first_measurement(self):
        result = {
            "status": "first",
            "score": 60.0,
            "baseline": None,
            "delta": None,
            "survived": 4,
            "total": 10,
            "scope": "diff",
            "write": True,
        }
        report = mb.format_report(result)
        last_line = report.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=first score=60.0 baseline=none delta=none survived=4 total=10 scope=diff")
        assert "pierwszy" in report.lower()

    def test_no_mutants_status_score_is_none_in_last_line(self):
        result = {
            "status": "no-mutants",
            "score": None,
            "baseline": None,
            "delta": None,
            "survived": 0,
            "total": 0,
            "scope": "diff",
            "write": False,
        }
        report = mb.format_report(result)
        last_line = report.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=no-mutants score=none baseline=none delta=none survived=0 total=0 scope=diff")

    def test_down_status_report_contains_uwaga_warning(self):
        result = {
            "status": "down",
            "score": 60.0,
            "baseline": 75.5,
            "delta": -15.5,
            "survived": 4,
            "total": 10,
            "scope": "diff",
            "write": False,
        }
        report = mb.format_report(result)
        assert "UWAGA" in report
        last_line = report.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=down score=60.0 baseline=75.5 delta=-15.5 survived=4 total=10 scope=diff")

    def test_score_100_formats_without_trimming_trailing_zero(self):
        result = {
            "status": "up",
            "score": 100.0,
            "baseline": 90.0,
            "delta": 10.0,
            "survived": 0,
            "total": 10,
            "scope": "diff",
            "write": True,
        }
        report = mb.format_report(result)
        last_line = report.rstrip("\n").split("\n")[-1]
        assert "score=100.0" in last_line

    @pytest.mark.parametrize(
        "status,score,baseline_val,delta",
        [
            ("first", 60.0, None, None),
            ("up", 60.0, 55.0, 5.0),
            ("same", 25.0, 25.0, 0.0),
            ("down", 60.0, 75.5, -15.5),
            ("no-mutants", None, None, None),
            ("scope-mismatch", 60.0, 55.0, 5.0),
        ],
    )
    def test_last_line_matches_frozen_pattern_for_every_status(self, status, score, baseline_val, delta):
        result = {
            "status": status,
            "score": score,
            "baseline": baseline_val,
            "delta": delta,
            "survived": 1,
            "total": 2,
            "scope": "diff",
            "write": False,
        }
        report = mb.format_report(result)
        last_line = report.rstrip("\n").split("\n")[-1]
        pattern = re.compile(
            r"^MUTATION-BASELINE: status=\S+ score=\S+ baseline=\S+ delta=\S+ "
            r"survived=\d+ total=\d+ scope=\S+$"
        )
        assert pattern.fullmatch(last_line), last_line


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMainCliHappyPath:
    def test_first_run_creates_baseline_and_reports_first_status(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path)])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.err == ""
        last_line = captured.out.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=first score=60.0 baseline=none delta=none survived=4 total=10 scope=diff")
        assert "Nowy baseline zapisany:" in captured.out
        assert baseline_path.is_file()
        data = json.loads(baseline_path.read_text())
        assert data["score"] == 60.0
        assert data["survived"] == 4
        assert data["total"] == 10
        assert data["scope"] == "diff"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["measured_at"])
        assert data["commit"] is None
        assert data["tool"] is None

    def test_first_run_stores_commit_and_tool_when_provided(self, tmp_path):
        baseline_path = tmp_path / "baseline.json"

        exit_code = mb.main(
            [
                "--summary",
                str(FIXTURE_STRYKER),
                "--baseline",
                str(baseline_path),
                "--commit",
                "deadbeef",
                "--tool",
                "stryker",
            ]
        )

        assert exit_code == 0
        data = json.loads(baseline_path.read_text())
        assert data["commit"] == "deadbeef"
        assert data["tool"] == "stryker"

    def test_up_overwrites_existing_baseline(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"
        write_json(baseline_path, make_baseline_data(score=55.0, survived=9, total=20))

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path)])

        assert exit_code == 0
        captured_out = capsys.readouterr().out
        last_line = captured_out.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=up score=60.0 baseline=55.0 delta=5.0 survived=4 total=10 scope=diff")
        assert "Nowy baseline zapisany:" in captured_out
        data = json.loads(baseline_path.read_text())
        assert data["score"] == 60.0
        assert data["survived"] == 4
        assert data["total"] == 10

    def test_down_does_not_overwrite_baseline_but_exits_zero(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"
        original = make_baseline_data(score=75.5, survived=2, total=8)
        write_json(baseline_path, original)

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path)])

        assert exit_code == 0
        last_line = capsys.readouterr().out.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=down score=60.0 baseline=75.5 delta=-15.5 survived=4 total=10 scope=diff")
        assert json.loads(baseline_path.read_text()) == original

    def test_same_does_not_overwrite_baseline(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"
        original = make_baseline_data(score=25.0, survived=3, total=4)
        write_json(baseline_path, original)

        exit_code = mb.main(["--summary", str(FIXTURE_MUTMUT), "--baseline", str(baseline_path)])

        assert exit_code == 0
        last_line = capsys.readouterr().out.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=same score=25.0 baseline=25.0 delta=0.0 survived=3 total=4 scope=diff")
        assert json.loads(baseline_path.read_text()) == original

    def test_dry_run_on_first_run_does_not_create_baseline_file(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path), "--dry-run"])

        assert exit_code == 0
        assert not baseline_path.exists()
        captured_out = capsys.readouterr().out
        last_line = captured_out.rstrip("\n").split("\n")[-1]
        assert last_line.startswith("MUTATION-BASELINE: status=first")
        assert "Tryb dry-run: baseline NIE zostal zapisany" in captured_out
        assert "Nowy baseline zapisany:" not in captured_out

    def test_dry_run_on_up_does_not_overwrite_baseline(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"
        original = make_baseline_data(score=55.0, survived=9, total=20)
        write_json(baseline_path, original)

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path), "--dry-run"])

        assert exit_code == 0
        assert json.loads(baseline_path.read_text()) == original
        captured_out = capsys.readouterr().out
        assert "Tryb dry-run: baseline NIE zostal zapisany" in captured_out
        assert "Nowy baseline zapisany:" not in captured_out

    def test_scope_mismatch_does_not_overwrite_baseline(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"
        original = make_baseline_data(score=55.0, survived=9, total=20, scope="full")
        write_json(baseline_path, original)

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path), "--scope", "diff"])

        assert exit_code == 0
        last_line = capsys.readouterr().out.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=scope-mismatch score=60.0 baseline=55.0 delta=5.0 survived=4 total=10 scope=diff")
        assert json.loads(baseline_path.read_text()) == original

    def test_no_mutants_does_not_overwrite_baseline(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"
        original = make_baseline_data(score=55.0, survived=9, total=20)
        write_json(baseline_path, original)

        exit_code = mb.main(["--summary", str(FIXTURE_EMPTY), "--baseline", str(baseline_path)])

        assert exit_code == 0
        last_line = capsys.readouterr().out.rstrip("\n").split("\n")[-1]
        assert last_line == ("MUTATION-BASELINE: status=no-mutants score=none baseline=55.0 delta=none survived=0 total=0 scope=diff")
        assert json.loads(baseline_path.read_text()) == original

    def test_default_scope_is_diff_when_not_specified(self, tmp_path):
        baseline_path = tmp_path / "baseline.json"

        mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path)])

        data = json.loads(baseline_path.read_text())
        assert data["scope"] == "diff"

    def test_summary_dash_reads_from_stdin(self, tmp_path, monkeypatch):
        baseline_path = tmp_path / "baseline.json"
        summary_json = json.dumps({"score": 60.0, "total": 10, "killed": 6, "survived": 4})
        monkeypatch.setattr(sys, "stdin", io.StringIO(summary_json))

        exit_code = mb.main(["--summary", "-", "--baseline", str(baseline_path)])

        assert exit_code == 0
        assert baseline_path.is_file()
        assert json.loads(baseline_path.read_text())["score"] == 60.0


@pytest.mark.unit
class TestMainCliErrors:
    def test_missing_summary_argument_exits_64(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"

        exit_code = mb.main(["--baseline", str(baseline_path)])

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_missing_baseline_argument_exits_64(self, capsys):
        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER)])

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_invalid_scope_exits_64(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path), "--scope", "nope"])

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_unknown_argument_exits_64(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path), "--nieznana-flaga", "x"])

        assert exit_code == 64
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_missing_summary_file_exits_2(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"
        missing = tmp_path / "nie-istnieje.json"

        exit_code = mb.main(["--summary", str(missing), "--baseline", str(baseline_path)])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()

    def test_corrupted_baseline_exits_2_and_file_is_not_overwritten(self, tmp_path, capsys):
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text("{to nie jest poprawny json")
        original_content = baseline_path.read_text()

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path)])

        assert exit_code == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()
        assert baseline_path.read_text() == original_content

    def test_baseline_missing_scope_key_exits_2(self, tmp_path):
        baseline_path = tmp_path / "baseline.json"
        write_json(baseline_path, {"score": 50.0})

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path)])

        assert exit_code == 2

    def test_baseline_missing_score_key_exits_2(self, tmp_path):
        baseline_path = tmp_path / "baseline.json"
        write_json(baseline_path, {"scope": "diff"})

        exit_code = mb.main(["--summary", str(FIXTURE_STRYKER), "--baseline", str(baseline_path)])

        assert exit_code == 2
