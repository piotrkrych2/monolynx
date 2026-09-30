"""Komenda `monolynx completion <shell>`: wypisuje skrypt uzupełniania poleceń na stdout.

Skrypt generuje `typer.completion.get_completion_script` -- ta sama funkcja, której używa
wbudowane `--show-completion`, więc wynik jest identyczny; różnica to jawna powłoka
zamiast autodetekcji i wybór nazwy programu. Moduł `typer.completion` jest publiczny i od
typer 0.12 do 0.27 ma tę samą sygnaturę (keyword-only `prog_name`, `complete_var`, `shell`);
nowsze typer wendorują Clicka, więc `click.shell_completion` nie jest dostępne i nie ma
innej publicznej drogi. Komenda nie czyta konfiguracji ani tokenu.
"""

from __future__ import annotations

import typer
from typer.completion import get_completion_script

from monolynx_cli.commands._common import usage_error

SHELLS = ("bash", "zsh", "fish")
# Skrypt jest wiązany z nazwą wywołania (zmienna _<PROG>_COMPLETE), a pakiet instaluje
# tylko te dwie; inna nazwa (np. `python -m monolynx_cli`) dostaje skrypt dla `monolynx`.
PROG_NAMES = ("monolynx", "mnx")
DEFAULT_PROG_NAME = "monolynx"


def completion(
    ctx: typer.Context,
    shell: str = typer.Argument(..., metavar="POWŁOKA", help="Powłoka: bash, zsh lub fish."),
) -> None:
    """Wypisz skrypt uzupełniania poleceń (bash, zsh, fish) na stdout.

    Skrypt dotyczy nazwy, pod którą uruchomiono program:

    monolynx completion zsh - skrypt dla monolynx,

    mnx completion zsh - skrypt dla mnx.

    Nie wymaga logowania ani konfiguracji.
    """
    if shell not in SHELLS:
        raise usage_error(f"Nieznana powłoka: {shell}. Dostępne: {', '.join(SHELLS)}.")
    prog_name = ctx.find_root().info_name
    if prog_name not in PROG_NAMES:
        prog_name = DEFAULT_PROG_NAME
    typer.echo(get_completion_script(prog_name=prog_name, complete_var=f"_{prog_name.upper()}_COMPLETE", shell=shell))
