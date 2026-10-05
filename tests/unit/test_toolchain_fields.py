"""Testy plugin/scripts/toolchain_fields.py - pole `pełny przebieg` strony toolchain (MON-269).

Skrypt normalizuje wartość pola `pełny przebieg:` z sekcji `## Test` strony `toolchain`
(`lokalnie` albo `ci`). Brak pola to `lokalnie` bez ostrzeżenia; wartość nieznana albo pusta to
`lokalnie` z ostrzeżeniem, nigdy `ci` - pomyłka w stronie nie może po cichu wyłączyć lokalnych testów.
Kontrakt (sygnatury, kody wyjścia 0/2/64, kształt JSON) jest w specyfikacji MON-269, sekcja 2.
"""

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "toolchain_fields.py"

assert SCRIPT.exists(), f"Brak skryptu: {SCRIPT}"

_spec = importlib.util.spec_from_file_location("toolchain_fields_under_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
toolchain_fields = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(toolchain_fields)

STDERR_PREFIX = "toolchain_fields: ostrzezenie: "


def _page(test_body: str, *, before: str = "", after: str = "") -> str:
    return f"# toolchain\n{before}\n## Lint\n- komenda: ruff check\n\n## Test\n{test_body}\n{after}"


class TestNormalizeFullRun:
    @pytest.mark.parametrize("raw", ["ci", "CI", "Ci", "  ci  ", "\tCI\n"])
    def test_ci_in_any_case_and_whitespace_returns_ci_from_field(self, raw):
        assert toolchain_fields.normalize_full_run(raw) == ("ci", "pole", [])

    @pytest.mark.parametrize("raw", ["lokalnie", "LOKALNIE", "Lokalnie", "  lokalnie  "])
    def test_lokalnie_in_any_case_and_whitespace_returns_lokalnie_from_field(self, raw):
        assert toolchain_fields.normalize_full_run(raw) == ("lokalnie", "pole", [])

    @pytest.mark.parametrize("raw", ["zawsze", "cicd", "c i", "nie ci", "ci2", "true", "tak", "lokalnie!", "ci, lokalnie"])
    def test_unknown_value_falls_back_to_lokalnie_with_single_warning_naming_it(self, raw):
        mode, source, warnings = toolchain_fields.normalize_full_run(raw)

        assert mode == "lokalnie"
        assert source == "nieznana"
        assert len(warnings) == 1
        assert raw in warnings[0]

    @pytest.mark.parametrize("raw", ["", "   ", "\t"])
    def test_empty_value_falls_back_to_lokalnie_with_single_warning(self, raw):
        mode, source, warnings = toolchain_fields.normalize_full_run(raw)

        assert (mode, source) == ("lokalnie", "nieznana")
        assert len(warnings) == 1
        assert "pust" in warnings[0].casefold() or "empty" in warnings[0].casefold()


class TestParseFullRun:
    def test_missing_test_section_returns_default_without_warnings(self):
        text = "# toolchain\n\n## Lint\n- komenda: ruff check\n"

        assert toolchain_fields.parse_full_run(text) == ("lokalnie", "domyslna", [])

    def test_test_section_without_field_returns_default_without_warnings(self):
        text = _page("- komenda: pytest\n- framework: pytest")

        assert toolchain_fields.parse_full_run(text) == ("lokalnie", "domyslna", [])

    def test_field_with_polish_diacritic_ci(self):
        text = _page("- komenda: pytest\npełny przebieg: ci")

        assert toolchain_fields.parse_full_run(text) == ("ci", "pole", [])

    def test_field_in_ascii_spelling_ci(self):
        text = _page("- komenda: pytest\npelny przebieg: ci")

        assert toolchain_fields.parse_full_run(text) == ("ci", "pole", [])

    def test_field_with_polish_diacritic_lokalnie(self):
        text = _page("pełny przebieg: lokalnie")

        assert toolchain_fields.parse_full_run(text) == ("lokalnie", "pole", [])

    @pytest.mark.parametrize("label", ["Pełny przebieg", "PEŁNY PRZEBIEG", "Pelny Przebieg", "PELNY PRZEBIEG"])
    def test_label_case_does_not_matter(self, label):
        text = _page(f"{label}: ci")

        assert toolchain_fields.parse_full_run(text) == ("ci", "pole", [])

    @pytest.mark.parametrize("prefix", ["- ", "  ", "  - ", "\t"])
    def test_optional_list_marker_and_indent_are_accepted(self, prefix):
        text = _page(f"{prefix}pełny przebieg: ci")

        assert toolchain_fields.parse_full_run(text) == ("ci", "pole", [])

    def test_value_is_read_to_end_of_line_and_stripped(self):
        text = _page("pełny przebieg:    CI   \n- komenda: pytest")

        assert toolchain_fields.parse_full_run(text) == ("ci", "pole", [])

    def test_unknown_value_in_field_falls_back_to_lokalnie_with_warning(self):
        text = _page("pełny przebieg: czasem")

        mode, source, warnings = toolchain_fields.parse_full_run(text)

        assert (mode, source) == ("lokalnie", "nieznana")
        assert len(warnings) == 1
        assert "czasem" in warnings[0]

    def test_empty_value_in_field_falls_back_to_lokalnie_with_warning(self):
        text = _page("pełny przebieg:\n- komenda: pytest")

        mode, source, warnings = toolchain_fields.parse_full_run(text)

        assert (mode, source) == ("lokalnie", "nieznana")
        assert len(warnings) == 1

    def test_field_only_in_worktree_section_is_ignored(self):
        text = _page("- komenda: pytest", after="\n## Worktree\n- lint: jak wyzej\n- pełny przebieg: ci\n")

        assert toolchain_fields.parse_full_run(text) == ("lokalnie", "domyslna", [])

    def test_field_only_in_notes_section_is_ignored(self):
        text = _page("- komenda: pytest", after="\n## Uwagi\npełny przebieg: ci\n")

        assert toolchain_fields.parse_full_run(text) == ("lokalnie", "domyslna", [])

    def test_field_before_any_heading_is_ignored(self):
        text = "pełny przebieg: ci\n" + _page("- komenda: pytest")

        assert toolchain_fields.parse_full_run(text) == ("lokalnie", "domyslna", [])

    def test_field_in_section_before_test_is_ignored(self):
        text = _page("- komenda: pytest", before="\n## Framework\npełny przebieg: ci\n")

        assert toolchain_fields.parse_full_run(text) == ("lokalnie", "domyslna", [])

    def test_ci_in_worktree_does_not_override_lokalnie_in_test(self):
        text = _page("pełny przebieg: lokalnie", after="\n## Worktree\n- pełny przebieg: ci\n")

        assert toolchain_fields.parse_full_run(text) == ("lokalnie", "pole", [])

    def test_lokalnie_in_worktree_does_not_override_ci_in_test(self):
        text = _page("pełny przebieg: ci", after="\n## Worktree\n- pełny przebieg: lokalnie\n")

        assert toolchain_fields.parse_full_run(text) == ("ci", "pole", [])

    def test_test_section_ends_at_next_h2_heading(self):
        text = _page("- komenda: pytest", after="\n## Mutacje\npełny przebieg: ci\n")

        assert toolchain_fields.parse_full_run(text) == ("lokalnie", "domyslna", [])

    def test_test_section_continues_through_h3_subheading(self):
        text = _page("- komenda: pytest\n\n### Szczegóły\npełny przebieg: ci")

        assert toolchain_fields.parse_full_run(text) == ("ci", "pole", [])

    def test_test_section_at_end_of_text_without_trailing_newline(self):
        text = "## Test\n- komenda: pytest\npełny przebieg: ci"

        assert toolchain_fields.parse_full_run(text) == ("ci", "pole", [])

    def test_duplicate_field_first_value_wins_ci_then_lokalnie(self):
        text = _page("pełny przebieg: ci\npełny przebieg: lokalnie")

        mode, source, warnings = toolchain_fields.parse_full_run(text)

        assert (mode, source) == ("ci", "pole")
        assert len(warnings) == 1
        assert "duplik" in warnings[0].casefold()

    def test_duplicate_field_first_value_wins_lokalnie_then_ci(self):
        text = _page("pełny przebieg: lokalnie\npelny przebieg: ci")

        mode, source, warnings = toolchain_fields.parse_full_run(text)

        assert (mode, source) == ("lokalnie", "pole")
        assert len(warnings) == 1
        assert "duplik" in warnings[0].casefold()

    def test_unknown_first_value_wins_over_valid_second_value_and_warns_about_both(self):
        text = _page("pełny przebieg: czasem\npełny przebieg: ci")

        mode, source, warnings = toolchain_fields.parse_full_run(text)

        assert (mode, source) == ("lokalnie", "nieznana")
        assert len(warnings) == 2
        assert "czasem" in warnings[0]
        assert "duplik" in warnings[1].casefold()

    def test_duplicate_outside_test_section_gives_no_duplicate_warning(self):
        text = _page("pełny przebieg: ci", after="\n## Worktree\n- pełny przebieg: ci\n")

        assert toolchain_fields.parse_full_run(text) == ("ci", "pole", [])


class TestMain:
    @staticmethod
    def _stdout_json(captured) -> dict:
        lines = captured.out.splitlines()
        assert len(lines) == 1, captured.out
        return json.loads(lines[0])

    def test_value_ci_prints_one_json_line_with_ordered_keys(self, capsys):
        exit_code = toolchain_fields.main(["--value", "ci"])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert captured.err == ""
        assert captured.out.endswith("\n")
        assert list(self._stdout_json(captured).items()) == [("pelny_przebieg", "ci"), ("zrodlo", "pole"), ("ostrzezenia", [])]

    def test_value_lokalnie_prints_lokalnie_from_field(self, capsys):
        exit_code = toolchain_fields.main(["--value", "Lokalnie"])

        assert exit_code == 0
        assert self._stdout_json(capsys.readouterr()) == {"pelny_przebieg": "lokalnie", "zrodlo": "pole", "ostrzezenia": []}

    def test_unknown_value_exits_zero_with_lokalnie_and_warning_on_stdout_and_stderr(self, capsys):
        exit_code = toolchain_fields.main(["--value", "zawsze"])

        captured = capsys.readouterr()
        payload = self._stdout_json(captured)
        assert exit_code == 0
        assert payload["pelny_przebieg"] == "lokalnie"
        assert payload["zrodlo"] == "nieznana"
        assert len(payload["ostrzezenia"]) == 1
        assert "zawsze" in payload["ostrzezenia"][0]
        assert captured.err == f"{STDERR_PREFIX}{payload['ostrzezenia'][0]}\n"

    def test_unknown_first_value_and_duplicate_give_two_warnings_on_two_stderr_lines(self, tmp_path, capsys):
        page = tmp_path / "toolchain.md"
        page.write_text(_page("pełny przebieg: czasem\npełny przebieg: ci"), encoding="utf-8")

        exit_code = toolchain_fields.main(["--file", str(page)])

        captured = capsys.readouterr()
        payload = self._stdout_json(captured)
        assert exit_code == 0
        assert (payload["pelny_przebieg"], payload["zrodlo"]) == ("lokalnie", "nieznana")
        assert len(payload["ostrzezenia"]) == 2
        assert "czasem" in payload["ostrzezenia"][0]
        assert "duplik" in payload["ostrzezenia"][1].casefold()
        assert captured.err.splitlines() == [f"{STDERR_PREFIX}{warning}" for warning in payload["ostrzezenia"]]

    def test_empty_value_option_is_a_value_not_a_missing_option(self, capsys):
        exit_code = toolchain_fields.main(["--value", ""])

        captured = capsys.readouterr()
        payload = self._stdout_json(captured)
        assert exit_code == 0
        assert (payload["pelny_przebieg"], payload["zrodlo"]) == ("lokalnie", "nieznana")
        assert len(payload["ostrzezenia"]) == 1
        assert captured.err == f"{STDERR_PREFIX}{payload['ostrzezenia'][0]}\n"

    def test_json_keeps_polish_characters_unescaped(self, capsys):
        exit_code = toolchain_fields.main(["--value", "zażółć"])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert "zażółć" in captured.out
        assert "\\u" not in captured.out

    def test_file_with_ci_field_in_test_section(self, tmp_path, capsys):
        page = tmp_path / "toolchain.md"
        page.write_text(_page("- komenda: pytest\npełny przebieg: ci"), encoding="utf-8")

        exit_code = toolchain_fields.main(["--file", str(page)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert captured.err == ""
        assert list(self._stdout_json(captured).items()) == [("pelny_przebieg", "ci"), ("zrodlo", "pole"), ("ostrzezenia", [])]

    def test_file_without_field_returns_default_source_and_no_stderr(self, tmp_path, capsys):
        page = tmp_path / "toolchain.md"
        page.write_text(_page("- komenda: pytest"), encoding="utf-8")

        exit_code = toolchain_fields.main(["--file", str(page)])

        captured = capsys.readouterr()
        assert exit_code == 0
        assert captured.err == ""
        assert self._stdout_json(captured) == {"pelny_przebieg": "lokalnie", "zrodlo": "domyslna", "ostrzezenia": []}

    def test_file_with_field_only_outside_test_section_is_ignored(self, tmp_path, capsys):
        page = tmp_path / "toolchain.md"
        page.write_text(_page("- komenda: pytest", after="\n## Worktree\n- pełny przebieg: ci\n"), encoding="utf-8")

        exit_code = toolchain_fields.main(["--file", str(page)])

        assert exit_code == 0
        assert self._stdout_json(capsys.readouterr()) == {"pelny_przebieg": "lokalnie", "zrodlo": "domyslna", "ostrzezenia": []}

    def test_file_with_unknown_value_exits_zero_with_warning(self, tmp_path, capsys):
        page = tmp_path / "toolchain.md"
        page.write_text(_page("pełny przebieg: czasem"), encoding="utf-8")

        exit_code = toolchain_fields.main(["--file", str(page)])

        captured = capsys.readouterr()
        payload = self._stdout_json(captured)
        assert exit_code == 0
        assert (payload["pelny_przebieg"], payload["zrodlo"]) == ("lokalnie", "nieznana")
        assert captured.err == f"{STDERR_PREFIX}{payload['ostrzezenia'][0]}\n"

    @staticmethod
    def _assert_input_error(exit_code, captured):
        assert exit_code == 2
        assert captured.out == ""
        assert captured.err.startswith("toolchain_fields: ")
        assert "Traceback" not in captured.err

    def test_missing_file_exits_two(self, tmp_path, capsys):
        exit_code = toolchain_fields.main(["--file", str(tmp_path / "nie-ma.md")])

        self._assert_input_error(exit_code, capsys.readouterr())

    def test_directory_instead_of_file_exits_two(self, tmp_path, capsys):
        exit_code = toolchain_fields.main(["--file", str(tmp_path)])

        self._assert_input_error(exit_code, capsys.readouterr())

    @pytest.mark.parametrize("content", ["", "   \n\t\n"])
    def test_empty_file_exits_two(self, tmp_path, capsys, content):
        page = tmp_path / "pusta.md"
        page.write_text(content, encoding="utf-8")

        exit_code = toolchain_fields.main(["--file", str(page)])

        self._assert_input_error(exit_code, capsys.readouterr())

    def test_non_utf8_file_exits_two(self, tmp_path, capsys):
        page = tmp_path / "binarny.md"
        page.write_bytes(b"\xff\xfe\xfa## Test\npe\xb3ny przebieg: ci\n")

        exit_code = toolchain_fields.main(["--file", str(page)])

        self._assert_input_error(exit_code, capsys.readouterr())

    def _assert_usage_error(self, exit_code, captured):
        assert exit_code == 64
        assert captured.out == ""
        assert captured.err.strip() != ""
        assert "Traceback" not in captured.err

    def test_no_option_exits_64(self, capsys):
        exit_code = toolchain_fields.main([])

        self._assert_usage_error(exit_code, capsys.readouterr())

    def test_both_options_exit_64(self, tmp_path, capsys):
        page = tmp_path / "toolchain.md"
        page.write_text(_page("pełny przebieg: ci"), encoding="utf-8")

        exit_code = toolchain_fields.main(["--value", "ci", "--file", str(page)])

        self._assert_usage_error(exit_code, capsys.readouterr())

    def test_both_options_exit_64_before_file_is_read(self, tmp_path, capsys):
        exit_code = toolchain_fields.main(["--value", "ci", "--file", str(tmp_path / "nie-ma.md")])

        self._assert_usage_error(exit_code, capsys.readouterr())

    def test_abbreviated_option_exits_64(self, capsys):
        exit_code = toolchain_fields.main(["--val", "ci"])

        self._assert_usage_error(exit_code, capsys.readouterr())

    def test_unknown_flag_exits_64(self, capsys):
        exit_code = toolchain_fields.main(["--value", "ci", "--nieznana-flaga"])

        self._assert_usage_error(exit_code, capsys.readouterr())

    def test_unknown_flag_alone_exits_64(self, capsys):
        exit_code = toolchain_fields.main(["--nieznana-flaga", "x"])

        self._assert_usage_error(exit_code, capsys.readouterr())
