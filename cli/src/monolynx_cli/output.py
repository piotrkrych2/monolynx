"""Warstwa renderowania wyników komend CLI: render()/emit()/resolve_format()/echo_stderr().

Konwencja dla autorów komend: żadna komenda NIE woła print()/typer.echo()/rich.print()
dla danych wynikowych. Komenda buduje strukturę (list[dict] dla listy rekordów, dict dla
pojedynczego rekordu albo dla koperty paginacji) i przekazuje ją przez emit(ctx, dane) jako
ostatni krok -- emit() czyta GlobalOptions z ctx.obj (ustawione przez root callback
monolynx_cli.main.main) i woła render() z właściwym formatem/no_color/quiet. Komunikaty
progresu (nie dane, nie błędy) idą przez echo_stderr(), która respektuje --quiet. Komunikaty
błędów komend zostają przez typer.echo(..., err=True) -- --quiet ich NIE wycisza, bo to
sygnał o niepowodzeniu dla człowieka/skryptu, nie progres.

Koperta paginacji to dict z kluczem "items" będącym listą (page/per_page/total/total_pages
obok). json/yaml wypisują ją bez zmian; table/csv wypisują wiersze z items, a metadane strony
("Strona X/Y, razem: Z") idą na stderr przez echo_stderr -- --quiet je wycisza, dane na
stdout zostają bez zmian.

sys.stdout/sys.stderr są pobierane w chwili wywołania render()/echo_stderr(), nigdy przy
imporcie modułu -- CliRunner (testy) podmienia te strumienie per-wywołanie, więc wiązanie
wcześniejsze złapałoby stary, nieprzechwytywany strumień.
"""

from __future__ import annotations

import csv
import json
import sys
from typing import Any, TypeGuard

import typer
import yaml
from rich.console import Console
from rich.table import Table

from monolynx_cli.options import OutputFormat, get_options


def _stdout_is_tty() -> bool:
    return sys.stdout.isatty()


def resolve_format(explicit: OutputFormat | None) -> OutputFormat:
    """Jawny format zawsze wygrywa; bez niego: table w terminalu, json poza nim."""
    if explicit is not None:
        return explicit
    return OutputFormat.TABLE if _stdout_is_tty() else OutputFormat.JSON


def _is_envelope(data: object) -> TypeGuard[dict[str, Any]]:
    """Koperta paginacji: dict z kluczem "items" będącym listą."""
    return isinstance(data, dict) and isinstance(data.get("items"), list)


def _page_metadata_line(envelope: dict[str, Any]) -> str:
    page = envelope.get("page")
    total_pages = envelope.get("total_pages")
    total = envelope.get("total")
    return f"Strona {page}/{total_pages}, razem: {total}"


def _format_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, str):
        return value
    return str(value)


def _column_union(rows: list[dict[str, Any]]) -> list[str]:
    columns: dict[str, None] = {}
    for row in rows:
        for key in row:
            columns.setdefault(key, None)
    return list(columns)


def echo_stderr(msg: str, *, quiet: bool = False, no_color: bool = False) -> None:
    """Wypisz komunikat progresu na stderr; --quiet wycisza go całkowicie."""
    if quiet:
        return
    # markup=False: msg może pochodzić z danych spoza naszej kontroli (metadane strony
    # zbudowane z odpowiedzi serwera) -- bez tego "[" w treści rich próbowałby
    # sparsować jako znacznik i skończyłby się rich.errors.MarkupError zamiast
    # literalnego wypisania.
    Console(stderr=True, no_color=no_color, markup=False).print(msg)


def _render_json(data: list[dict[str, Any]] | dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _render_yaml(data: list[dict[str, Any]] | dict[str, Any]) -> None:
    yaml.safe_dump(data, sys.stdout, allow_unicode=True, sort_keys=False)


def _write_csv_rows(writer: Any, rows: list[dict[str, Any]]) -> None:
    columns = _column_union(rows)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_format_cell(row.get(column)) for column in columns])


def _render_csv(data: list[dict[str, Any]] | dict[str, Any], *, no_color: bool, quiet: bool) -> None:
    writer = csv.writer(sys.stdout)
    if _is_envelope(data):
        rows = data["items"]
        if rows:
            _write_csv_rows(writer, rows)
        echo_stderr(_page_metadata_line(data), quiet=quiet, no_color=no_color)
        return
    if isinstance(data, dict):
        _write_csv_rows(writer, [data])
        return
    if data:
        _write_csv_rows(writer, data)


def _print_rows_table(console: Console, rows: list[dict[str, Any]]) -> None:
    # highlight=True: ReprHighlighter koloruje wartości komórek (liczby, True/False/None,
    # URL-e itd.) niezależnie od markup=False na Console -- to osobny etap stylowania
    # tekstu literalnego, nie parsowanie znaczników. no_color na Console zdejmuje z tego
    # kolor, zostawiając pogrubienie nagłówka.
    columns = _column_union(rows)
    table = Table(*columns, highlight=True)
    for row in rows:
        table.add_row(*[_format_cell(row.get(column)) for column in columns])
    console.print(table)


def _print_single_dict_table(console: Console, data: dict[str, Any]) -> None:
    table = Table("klucz", "wartość", highlight=True)
    for key, value in data.items():
        table.add_row(key, _format_cell(value))
    console.print(table)


def _render_table(data: list[dict[str, Any]] | dict[str, Any], *, no_color: bool, quiet: bool) -> None:
    # markup=False: nagłówki kolumn (klucze danych) i wartości komórek mogą zawierać
    # "[" (np. token z configu edytowanego ręcznie, odpowiedź serwera) -- bez tego rich
    # próbowałby je sparsować jako znaczniki i albo je wyciąłby z wyniku (np. "[bold]"),
    # albo rzucił rich.errors.MarkupError na niesparowany tag zamykający. Dane mają być
    # zawsze literalne.
    console = Console(file=sys.stdout, no_color=no_color, markup=False)

    if _is_envelope(data):
        rows = data["items"]
        if not rows:
            echo_stderr("Brak wyników.", quiet=quiet, no_color=no_color)
            return
        _print_rows_table(console, rows)
        echo_stderr(_page_metadata_line(data), quiet=quiet, no_color=no_color)
        return

    if isinstance(data, dict):
        _print_single_dict_table(console, data)
        return

    if not data:
        echo_stderr("Brak wyników.", quiet=quiet, no_color=no_color)
        return
    _print_rows_table(console, data)


def render(
    data: list[dict[str, Any]] | dict[str, Any],
    fmt: OutputFormat,
    *,
    no_color: bool = False,
    quiet: bool = False,
) -> None:
    """Jedyne miejsce piszące dane wynikowe komend na stdout, w jednym z 4 formatów."""
    if fmt is OutputFormat.JSON:
        _render_json(data)
    elif fmt is OutputFormat.YAML:
        _render_yaml(data)
    elif fmt is OutputFormat.CSV:
        _render_csv(data, no_color=no_color, quiet=quiet)
    else:
        _render_table(data, no_color=no_color, quiet=quiet)


def emit(ctx: typer.Context, data: list[dict[str, Any]] | dict[str, Any]) -> None:
    """Przekaż wynik komendy do render(), z formatem/no_color/quiet z GlobalOptions."""
    opts = get_options(ctx)
    render(data, resolve_format(opts.output), no_color=opts.no_color, quiet=opts.quiet)
