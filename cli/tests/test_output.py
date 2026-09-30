"""Testy monolynx_cli.output: render()/echo_stderr()/emit()/resolve_format() oraz
monolynx_cli.options: GlobalOptions/OutputFormat/get_options.

Specyfikacja dla MON-127 -- moduły monolynx_cli.output i monolynx_cli.options jeszcze
nie istnieją w chwili pisania tych testów (TDD, red phase). Kontrakt: patrz
interface.md ticketa MON-127 (sekcje output.py i options.py).

Testy wołają render() bezpośrednio i przechwytują sys.stdout/sys.stderr przez capsys --
render() ma pobierać sys.stdout w chwili wywołania (kontrakt), więc capsys wystarcza bez
CliRunner. Testy mechanizmu emit()/get_options() na poziomie CLI (globalne flagi na
root app) żyją w test_main.py.
"""

from __future__ import annotations

import csv
import io
import json
import re

import pytest
import typer
import yaml
from monolynx_cli import output as output_mod
from monolynx_cli.options import GlobalOptions, OutputFormat, get_options
from monolynx_cli.output import echo_stderr, emit, render, resolve_format

pytestmark = pytest.mark.unit

SAMPLE_LIST = [
    {"id": "aaa", "name": "Ala"},
    {"id": "bbb", "name": "Bela"},
]

SAMPLE_DICT = {"id": "aaa", "name": "Ala"}

SAMPLE_ENVELOPE = {
    "items": [{"id": "aaa", "name": "Ala"}, {"id": "bbb", "name": "Bela"}],
    "page": 1,
    "per_page": 20,
    "total": 42,
    "total_pages": 3,
}

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_SGR_COLOR_PARAMS = frozenset(range(30, 39)) | frozenset(range(40, 49)) | frozenset(range(90, 98)) | frozenset(range(100, 108))


def _has_color_sgr(text: str) -> bool:
    """Czy `text` zawiera kod SGR koloru (pierwszy plan 30-38/90-97, tlo 40-48/100-107,
    w tym `38;5;n`/`38;2;r;g;b` -- wystarczy, ze 38/48 jest jednym z parametrow).

    Rich nie usuwa STYLU (bold=1, italic=3, underline=4...) przy no_color=True, tylko
    KOLOR -- goly substring "\\x1b[3" myli kursywe (SGR 3, np. repr.bool_true =
    "italic bright_green") z zakresem kodow koloru 30-39. Rozbicie parametrow po ";" i
    porownanie z konkretnymi kodami koloru odrozna te dwa przypadki.
    """
    for match in _SGR_RE.finditer(text):
        params = match.group(1)
        if not params:
            continue
        if any(int(p) in _SGR_COLOR_PARAMS for p in params.split(";") if p):
            return True
    return False


# ---------------------------------------------------------------------------
# resolve_format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", list(OutputFormat))
def test_resolve_format_explicit_always_wins_over_tty_detection(monkeypatch, fmt):
    monkeypatch.setattr(output_mod, "_stdout_is_tty", lambda: True)
    assert resolve_format(fmt) == fmt

    monkeypatch.setattr(output_mod, "_stdout_is_tty", lambda: False)
    assert resolve_format(fmt) == fmt


def test_resolve_format_defaults_to_json_outside_tty(monkeypatch):
    monkeypatch.setattr(output_mod, "_stdout_is_tty", lambda: False)

    assert resolve_format(None) == OutputFormat.JSON


def test_resolve_format_defaults_to_table_in_tty(monkeypatch):
    monkeypatch.setattr(output_mod, "_stdout_is_tty", lambda: True)

    assert resolve_format(None) == OutputFormat.TABLE


# ---------------------------------------------------------------------------
# render: json
# ---------------------------------------------------------------------------


def test_render_json_list_round_trips(capsys):
    render(SAMPLE_LIST, OutputFormat.JSON)

    captured = capsys.readouterr()
    assert json.loads(captured.out) == SAMPLE_LIST


def test_render_json_single_dict_round_trips(capsys):
    render(SAMPLE_DICT, OutputFormat.JSON)

    captured = capsys.readouterr()
    assert json.loads(captured.out) == SAMPLE_DICT


def test_render_json_uses_indent_2_and_preserves_unicode(capsys):
    render([{"imie": "Zażółć gęślą jaźń"}], OutputFormat.JSON)

    captured = capsys.readouterr()
    expected = json.dumps([{"imie": "Zażółć gęślą jaźń"}], ensure_ascii=False, indent=2) + "\n"
    assert captured.out == expected


def test_render_json_empty_list_is_empty_array(capsys):
    render([], OutputFormat.JSON)

    captured = capsys.readouterr()
    assert captured.out.strip() == "[]"


def test_render_json_envelope_unchanged_and_no_stderr_metadata(capsys):
    render(SAMPLE_ENVELOPE, OutputFormat.JSON)

    captured = capsys.readouterr()
    assert json.loads(captured.out) == SAMPLE_ENVELOPE
    assert captured.err == ""


# ---------------------------------------------------------------------------
# render: yaml
# ---------------------------------------------------------------------------


def test_render_yaml_list_round_trips(capsys):
    render(SAMPLE_LIST, OutputFormat.YAML)

    captured = capsys.readouterr()
    assert yaml.safe_load(captured.out) == SAMPLE_LIST


def test_render_yaml_single_dict_round_trips(capsys):
    render(SAMPLE_DICT, OutputFormat.YAML)

    captured = capsys.readouterr()
    assert yaml.safe_load(captured.out) == SAMPLE_DICT


def test_render_yaml_preserves_unicode_unescaped(capsys):
    render([{"imie": "Zażółć gęślą jaźń"}], OutputFormat.YAML)

    captured = capsys.readouterr()
    assert "Zażółć gęślą jaźń" in captured.out


def test_render_yaml_empty_list_round_trips_to_empty_list(capsys):
    render([], OutputFormat.YAML)

    captured = capsys.readouterr()
    assert yaml.safe_load(captured.out) == []


def test_render_yaml_envelope_unchanged_and_no_stderr_metadata(capsys):
    render(SAMPLE_ENVELOPE, OutputFormat.YAML)

    captured = capsys.readouterr()
    assert yaml.safe_load(captured.out) == SAMPLE_ENVELOPE
    assert captured.err == ""


# ---------------------------------------------------------------------------
# render: csv
# ---------------------------------------------------------------------------


def test_render_csv_list_header_and_rows(capsys):
    render(SAMPLE_LIST, OutputFormat.CSV)

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))
    assert rows == [["id", "name"], ["aaa", "Ala"], ["bbb", "Bela"]]


def test_render_csv_header_is_union_of_keys_in_first_occurrence_order(capsys):
    # Kolumny "zulu"/"alpha"/"mid" w kolejnosci innej niz alfabetyczna -- zabija
    # mutanta ktory sortuje klucze zamiast zachowac kolejnosc pierwszego wystapienia.
    rows_in = [{"zulu": "1", "alpha": "2"}, {"alpha": "3", "mid": "4"}]

    render(rows_in, OutputFormat.CSV)

    captured = capsys.readouterr()
    header = next(csv.reader(io.StringIO(captured.out)))
    assert header == ["zulu", "alpha", "mid"]


def test_render_csv_row_missing_key_from_union_is_empty_cell(capsys):
    rows_in = [{"zulu": "1", "alpha": "2"}, {"alpha": "3", "mid": "4"}]

    render(rows_in, OutputFormat.CSV)

    captured = capsys.readouterr()
    data_rows = list(csv.reader(io.StringIO(captured.out)))[1:]
    assert data_rows[0] == ["1", "2", ""]
    assert data_rows[1] == ["", "3", "4"]


def test_render_csv_single_dict_header_and_one_row(capsys):
    render(SAMPLE_DICT, OutputFormat.CSV)

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))
    assert rows == [["id", "name"], ["aaa", "Ala"]]


def test_render_csv_empty_list_writes_nothing(capsys):
    render([], OutputFormat.CSV)

    captured = capsys.readouterr()
    assert captured.out == ""


def test_render_csv_none_value_is_empty_cell(capsys):
    render([{"id": "x", "note": None}], OutputFormat.CSV)

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))
    assert rows == [["id", "note"], ["x", ""]]


def test_render_csv_dict_value_is_compact_json(capsys):
    render([{"id": "x", "meta": {"a": 1, "b": [1, 2]}}], OutputFormat.CSV)

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))
    expected = json.dumps({"a": 1, "b": [1, 2]}, ensure_ascii=False, separators=(",", ":"))
    assert rows[1][1] == expected


def test_render_csv_envelope_writes_item_rows_and_page_metadata_on_stderr(capsys):
    render(SAMPLE_ENVELOPE, OutputFormat.CSV)

    captured = capsys.readouterr()
    rows = list(csv.reader(io.StringIO(captured.out)))
    assert rows == [["id", "name"], ["aaa", "Ala"], ["bbb", "Bela"]]
    assert captured.err.strip() == "Strona 1/3, razem: 42"


def test_render_csv_envelope_quiet_true_suppresses_stderr_metadata(capsys):
    render(SAMPLE_ENVELOPE, OutputFormat.CSV, quiet=True)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "aaa" in captured.out


# ---------------------------------------------------------------------------
# render: table
# ---------------------------------------------------------------------------


def test_render_table_list_shows_columns_and_values(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")

    render(SAMPLE_LIST, OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    assert "id" in captured.out
    assert "name" in captured.out
    assert "aaa" in captured.out
    assert "Ala" in captured.out
    assert "bbb" in captured.out
    assert "Bela" in captured.out


def test_render_table_list_columns_are_union_of_keys_in_first_occurrence_order(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    rows_in = [{"zulu": "1", "alpha": "2"}, {"alpha": "3", "mid": "4"}]

    render(rows_in, OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    idx_zulu = captured.out.index("zulu")
    idx_alpha = captured.out.index("alpha")
    idx_mid = captured.out.index("mid")
    assert idx_zulu < idx_alpha < idx_mid


def test_render_table_single_dict_has_klucz_wartosc_columns(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")

    render(SAMPLE_DICT, OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    assert "klucz" in captured.out
    assert "wartość" in captured.out
    assert "id" in captured.out
    assert "aaa" in captured.out
    assert "name" in captured.out
    assert "Ala" in captured.out


def test_render_table_dict_with_non_list_items_key_is_not_treated_as_envelope(capsys, monkeypatch):
    # "items" obecne, ale nie jest lista -- ma byc traktowane jak zwykly pojedynczy
    # dict (dwie kolumny klucz/wartosc), nie jak koperta paginacji.
    monkeypatch.setenv("COLUMNS", "200")

    render({"items": "not-a-list", "other": "x"}, OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    assert "klucz" in captured.out
    assert captured.err == ""


def test_render_table_empty_list_writes_nothing_and_brak_wynikow_on_stderr(capsys):
    render([], OutputFormat.TABLE)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "Brak wyników."


def test_render_table_empty_envelope_items_writes_nothing_and_brak_wynikow_on_stderr(capsys):
    envelope = {"items": [], "page": 1, "per_page": 20, "total": 0, "total_pages": 0}

    render(envelope, OutputFormat.TABLE)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "Brak wyników."


def test_render_table_empty_list_quiet_true_suppresses_brak_wynikow_message(capsys):
    render([], OutputFormat.TABLE, quiet=True)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_render_table_none_value_is_not_rendered_as_literal_none(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")

    render([{"id": "x", "note": None}], OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    assert "None" not in captured.out


def test_render_table_dict_value_is_compact_json_substring(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")

    render([{"id": "x", "meta": {"a": 1, "b": [1, 2]}}], OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    expected = json.dumps({"a": 1, "b": [1, 2]}, ensure_ascii=False, separators=(",", ":"))
    # rich moze zlamac linie na krawedzi kolumny -- usuwamy znaki nowej linii przed
    # porownaniem, tabela ma byc wystarczajaco szeroka (COLUMNS=200), zeby nie zawijac
    # w srodku komorki mimo obramowania.
    assert expected in captured.out.replace("\n", "")


def test_render_table_envelope_shows_item_rows_and_page_metadata_on_stderr(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")

    render(SAMPLE_ENVELOPE, OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    assert "aaa" in captured.out
    assert "bbb" in captured.out
    assert captured.err.strip() == "Strona 1/3, razem: 42"


def test_render_table_envelope_quiet_true_suppresses_stderr_metadata(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")

    render(SAMPLE_ENVELOPE, OutputFormat.TABLE, no_color=True, quiet=True)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "aaa" in captured.out


def test_render_table_no_color_suppresses_color_but_keeps_bold(capsys, monkeypatch):
    # Pod capsys/CliRunner stdout nie jest TTY i rich sam nie emituje zadnego koloru
    # niezaleznie od no_color -- mutant usuwajacy `no_color=no_color` z Console(...) w
    # _render_table by przezyl bez wymuszenia trybu terminala. FORCE_COLOR to wymusza
    # (rich.console.Console je respektuje). Naglowek zostaje bold, a wartosc True
    # kursywna (repr.bool_true = "italic bright_green") nawet przy no_color=True -- oba to
    # STYL, nie KOLOR, wiec asercja uzywa _has_color_sgr (rozbija parametry SGR i
    # sprawdza konkretne kody koloru), nie goly substring "\x1b[3"/"\x1b[9" (3 to kursywa,
    # nie zakres kolorow 30-39).
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("FORCE_COLOR", "1")
    rows = [{"aktywny": True}]

    render(rows, OutputFormat.TABLE, no_color=False)
    with_color = capsys.readouterr().out

    render(rows, OutputFormat.TABLE, no_color=True)
    without_color = capsys.readouterr().out

    assert _has_color_sgr(with_color)
    assert not _has_color_sgr(without_color)


# ---------------------------------------------------------------------------
# render table: komorki i naglowki kolumn NIE sa interpretowane jako markup rich
# ---------------------------------------------------------------------------


def test_render_table_cell_with_markup_like_text_is_rendered_literally(capsys, monkeypatch):
    # Wartosc komorki nie moze byc parsowana przez Text.from_markup -- "a[/b]c" to
    # zamykajacy tag bez otwierajacego i dzis rzuca rich.errors.MarkupError, jesli komorka
    # trafia do rich jako markup zamiast tekstu literalnego.
    monkeypatch.setenv("COLUMNS", "200")

    render([{"k": "a[/b]c"}], OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    assert "a[/b]c" in captured.out


def test_render_table_dict_value_with_markup_like_text_is_rendered_literally(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")

    render({"x": "[bold]y[/bold]"}, OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    assert "[bold]y[/bold]" in captured.out


def test_render_table_column_header_with_brackets_is_rendered_literally(capsys, monkeypatch):
    # Klucz z nawiasami jako naglowek kolumny -- ta sama pulapka markup, tylko po
    # stronie naglowka zamiast wartosci komorki.
    monkeypatch.setenv("COLUMNS", "200")

    render([{"a[b]": "v"}], OutputFormat.TABLE, no_color=True)

    captured = capsys.readouterr()
    assert "a[b]" in captured.out


# ---------------------------------------------------------------------------
# render: brak ANSI w json/yaml/csv niezaleznie od TTY
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", [OutputFormat.JSON, OutputFormat.YAML, OutputFormat.CSV])
def test_render_never_emits_ansi_for_non_table_formats_even_when_tty(capsys, monkeypatch, fmt):
    monkeypatch.setattr(output_mod, "_stdout_is_tty", lambda: True)

    render(SAMPLE_LIST, fmt)

    captured = capsys.readouterr()
    assert "\x1b[" not in captured.out


# ---------------------------------------------------------------------------
# echo_stderr
# ---------------------------------------------------------------------------


def test_echo_stderr_quiet_true_writes_nothing(capsys):
    echo_stderr("wiadomosc-testowa", quiet=True)

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_echo_stderr_quiet_false_writes_to_stderr_only(capsys):
    echo_stderr("wiadomosc-testowa", quiet=False)

    captured = capsys.readouterr()
    assert "wiadomosc-testowa" in captured.err
    assert captured.out == ""


# ---------------------------------------------------------------------------
# Probe app: pomocniczy typer.Typer budowany w kazdym tescie mechanizmu ponizej.
# Wylacznie publiczne API typera (typer.Typer/typer.Context) -- typer>=0.27 nie
# eksponuje juz publicznego modulu `click` (wendoruje go prywatnie jako
# typer._click), wiec `import click` w tym pliku wywala sie na nowszych typerach.
# ---------------------------------------------------------------------------


def _build_probe_app(data, *, output=None, no_color=False, quiet=False):
    probe = typer.Typer()

    @probe.callback()
    def _root(ctx: typer.Context) -> None:
        ctx.obj = GlobalOptions(output=output, no_color=no_color, quiet=quiet)

    @probe.command("go")
    def _go(ctx: typer.Context) -> None:
        emit(ctx, data)

    return probe


# ---------------------------------------------------------------------------
# get_options
# ---------------------------------------------------------------------------


def test_get_options_without_ctx_obj_returns_default_global_options(runner):
    captured: list[GlobalOptions] = []
    probe = typer.Typer()

    @probe.callback()
    def _root() -> None:
        # Celowo NIE ustawia ctx.obj -- symuluje komende wywolana bez przejscia
        # przez root callback (np. gdyby sub-app zostal odpalony osobno).
        pass

    @probe.command("go")
    def _go(ctx: typer.Context) -> None:
        captured.append(get_options(ctx))

    result = runner.invoke(probe, ["go"])

    assert result.exit_code == 0, result.output
    assert captured == [GlobalOptions()]


def test_get_options_returns_object_set_on_ctx(runner):
    captured: list[GlobalOptions] = []
    probe = typer.Typer()

    @probe.callback()
    def _root(ctx: typer.Context) -> None:
        ctx.obj = GlobalOptions(output=OutputFormat.CSV, no_color=True, quiet=True, profile="p", project="proj")

    @probe.command("go")
    def _go(ctx: typer.Context) -> None:
        captured.append(get_options(ctx))

    result = runner.invoke(probe, ["go"])

    assert result.exit_code == 0, result.output
    assert captured == [GlobalOptions(output=OutputFormat.CSV, no_color=True, quiet=True, profile="p", project="proj")]


# ---------------------------------------------------------------------------
# emit: mechanizm
# ---------------------------------------------------------------------------


def test_emit_uses_global_options_output_format(runner):
    result = runner.invoke(_build_probe_app(SAMPLE_LIST, output=OutputFormat.CSV), ["go"])

    assert result.exit_code == 0, result.output
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows == [["id", "name"], ["aaa", "Ala"], ["bbb", "Bela"]]


def test_emit_without_explicit_output_defaults_to_json_outside_tty(runner):
    result = runner.invoke(_build_probe_app(SAMPLE_LIST), ["go"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == SAMPLE_LIST


def test_emit_defaults_to_table_when_stdout_is_tty(runner, monkeypatch):
    monkeypatch.setattr(output_mod, "_stdout_is_tty", lambda: True)

    result = runner.invoke(_build_probe_app(SAMPLE_LIST), ["go"])

    assert result.exit_code == 0, result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)


def test_emit_respects_quiet_flag_for_envelope_metadata(runner):
    result = runner.invoke(_build_probe_app(SAMPLE_ENVELOPE, output=OutputFormat.TABLE, quiet=True), ["go"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""


def test_emit_without_quiet_writes_envelope_metadata_to_stderr(runner):
    result = runner.invoke(_build_probe_app(SAMPLE_ENVELOPE, output=OutputFormat.TABLE, quiet=False), ["go"])

    assert result.exit_code == 0, result.output
    assert result.stderr.strip() == "Strona 1/3, razem: 42"


def test_emit_passes_no_color_and_quiet_from_global_options_to_render(runner, monkeypatch):
    calls: list[dict[str, object]] = []
    original_render = output_mod.render

    def spy(data, fmt, *, no_color=False, quiet=False):
        calls.append({"data": data, "fmt": fmt, "no_color": no_color, "quiet": quiet})
        return original_render(data, fmt, no_color=no_color, quiet=quiet)

    monkeypatch.setattr(output_mod, "render", spy)

    probe = _build_probe_app(SAMPLE_LIST, output=OutputFormat.JSON, no_color=True, quiet=True)
    result = runner.invoke(probe, ["go"])

    assert result.exit_code == 0, result.output
    assert calls == [{"data": SAMPLE_LIST, "fmt": OutputFormat.JSON, "no_color": True, "quiet": True}]
