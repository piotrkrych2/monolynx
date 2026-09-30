"""Globalne opcje CLI: OutputFormat, GlobalOptions, get_options.

Moduł nie importuje z monolynx_cli.output -- output.py importuje stąd OutputFormat,
odwrotna zależność tworzyłaby cykl importów. GlobalOptions to migawka flag globalnych
(--output/--no-color/--quiet/--profile/--project/--debug/--timeout) zapisywana w ctx.obj
przez root callback (monolynx_cli.main.main); komendy odczytują ją przez get_options(ctx)
zamiast sięgać do ctx.obj bezpośrednio. `debug` i `timeout` trafiają do MonolynxClient.
"""

from __future__ import annotations

import dataclasses
import enum

import typer


class OutputFormat(str, enum.Enum):  # noqa: UP042 -- cli wspiera Python 3.10, gdzie enum nie ma StrEnum
    TABLE = "table"
    JSON = "json"
    YAML = "yaml"
    CSV = "csv"


@dataclasses.dataclass(frozen=True)
class GlobalOptions:
    output: OutputFormat | None = None
    no_color: bool = False
    quiet: bool = False
    profile: str | None = None
    project: str | None = None
    debug: bool = False
    timeout: float = 30.0


def get_options(ctx: typer.Context) -> GlobalOptions:
    """Odczytaj GlobalOptions zapisane w ctx.obj przez root callback.

    Brak obiektu (komenda wywołana bez przejścia przez root callback, np. sonda
    testowa zbudowana ręcznie) zwraca wartości domyślne zamiast rzucać wyjątek --
    każda komenda ma mieć sensowne zachowanie nawet bez globalnych flag.
    """
    options = ctx.find_object(GlobalOptions)
    return options if options is not None else GlobalOptions()
