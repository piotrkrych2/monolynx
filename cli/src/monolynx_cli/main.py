"""Punkt wejścia CLI monolynx-cli: opcja --version, globalne flagi i szkielet 17 grup podkomend.

`auth`, `config`, `project`, `member`, `ticket`, `sprint`, `wiki`, `blog` (metadane wpisów i lint) i `graph` (tylko odczyt) mają realne
komendy (moduły monolynx_cli.commands.*). Pozostałe grupy są nadal puste -- kolejne tickety dodadzą do
nich podkomendy, ewentualnie wynosząc rozrośniętą grupę do własnego modułu. Poza grupami root
ma samodzielne komendy `completion <shell>` (skrypt uzupełniania na stdout, patrz
monolynx_cli.commands.completion) i `version [--check]` (wersja CLI i sprawdzenie PyPI, patrz
monolynx_cli.commands.version); wbudowane --install-completion i --show-completion Typera
zostają.
`no_args_is_help=True` na każdej grupie sprawia, że wywołanie samej grupy bez
podkomendy pokazuje pomoc zamiast błędu "Missing command".

Root callback zapisuje globalne flagi --output/-o, --no-color, --quiet/-q, --profile,
--project, --debug, --timeout w ctx.obj jako monolynx_cli.options.GlobalOptions -- komendy
czytają je przez get_options(ctx) i przekazują wynik do render() przez emit(ctx, dane),
patrz monolynx_cli.output. Flagi globalne podaje się PRZED nazwą grupy: `monolynx -o json
auth whoami`. NO_COLOR (dowolna niepusta wartość, patrz no-color.org) działa tak samo jak
--no-color, a --no-color/env wygrywają niezależnie od siebie (albo).

Klasa root Typera `MonolynxGroup` łapie każdy `MonolynxError` z dowolnej grupy (patrz
monolynx_cli.client), wypisuje komunikat na stderr i kończy proces kodem `exc.exit_code`
(1 błąd API, 3 autoryzacja, 4 sieć; 2 zostaje dla błędów użycia Click). Handler siedzi w
`invoke` grupy, a nie w `main()`, bo `CliRunner.invoke(app)` omija `Typer.__call__`.
"""

from __future__ import annotations

import os
from typing import Any

import typer
from rich.console import Console
from typer.core import TyperGroup

from monolynx_cli.client import DEFAULT_TIMEOUT, MonolynxError
from monolynx_cli.commands.auth import app as auth_app
from monolynx_cli.commands.blog import app as blog_app
from monolynx_cli.commands.completion import completion
from monolynx_cli.commands.config import app as config_app
from monolynx_cli.commands.graph import app as graph_app
from monolynx_cli.commands.member import app as member_app
from monolynx_cli.commands.project import app as project_app
from monolynx_cli.commands.sprint import app as sprint_app
from monolynx_cli.commands.ticket import app as ticket_app
from monolynx_cli.commands.version import version as version_command
from monolynx_cli.commands.wiki import app as wiki_app
from monolynx_cli.options import GlobalOptions, OutputFormat
from monolynx_cli.version import __version__


class MonolynxGroup(TyperGroup):
    # Any zamiast click.Context: nowsze typer wendorują Clicka i nie ciągną pakietu `click`.
    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except MonolynxError as exc:
            # Nie echo_stderr(): --quiet wycisza progres, nie błędy.
            Console(stderr=True, markup=False, highlight=False, emoji=False).print(f"Błąd: {exc}", soft_wrap=True)
            ctx.exit(exc.exit_code)


app = typer.Typer(no_args_is_help=True, cls=MonolynxGroup)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Wypisz wersję monolynx-cli i zakończ.",
    ),
    output: OutputFormat | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Format wyjścia: table/json/yaml/csv. Bez podania: table w terminalu, json poza nim.",
    ),
    no_color: bool = typer.Option(
        False,
        "--no-color",
        help="Wyłącz kolorowanie tabel (patrz też zmienna środowiskowa NO_COLOR).",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Wycisz komunikaty progresu na stderr. Dane na stdout i błędy komend bez zmian.",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Nazwa profilu konfiguracji (wygrywa z MONOLYNX_PROFILE i aktywnym profilem).",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        help="Slug projektu Monolynx (wygrywa z MONOLYNX_PROJECT).",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Loguj żądania i odpowiedzi HTTP na stderr (token zawsze zamaskowany).",
    ),
    timeout: float = typer.Option(
        DEFAULT_TIMEOUT,
        "--timeout",
        help="Limit czasu żądania HTTP w sekundach (większy od zera).",
    ),
) -> None:
    """CLI do zarządzania platformą Monolynx."""
    if not timeout > 0:
        raise typer.BadParameter("musi być większy od zera.", param_hint="--timeout")
    no_color = no_color or bool(os.environ.get("NO_COLOR"))
    ctx.obj = GlobalOptions(output=output, no_color=no_color, quiet=quiet, profile=profile, project=project, debug=debug, timeout=timeout)


# Placeholdery sprint 2 -- puste aż do własnych ticketów.
monitoring_app = typer.Typer(no_args_is_help=True)
issues_app = typer.Typer(no_args_is_help=True)
time_app = typer.Typer(no_args_is_help=True)
plan_app = typer.Typer(no_args_is_help=True)
rozliczenia_app = typer.Typer(no_args_is_help=True)
pipeline_app = typer.Typer(no_args_is_help=True)
heartbeat_app = typer.Typer(no_args_is_help=True)
role_app = typer.Typer(no_args_is_help=True)

app.command(name="completion")(completion)
app.command(name="version")(version_command)
app.add_typer(auth_app, name="auth", help="Logowanie i zarządzanie tokenem dostępu do Monolynx.")
app.add_typer(config_app, name="config", help="Konfiguracja lokalna CLI (profile, adres API).")
app.add_typer(project_app, name="project", help="Zarządzanie projektami Monolynx.")
app.add_typer(member_app, name="member", help="Zarządzanie członkami projektu.")
app.add_typer(ticket_app, name="ticket", help="Zarządzanie ticketami: backlog, szczegóły, komentarze.")
app.add_typer(sprint_app, name="sprint", help="Zarządzanie sprintami.")
app.add_typer(wiki_app, name="wiki", help="Strony wiki projektu.")
app.add_typer(blog_app, name="blog", help="Wpisy bloga projektu: lista, treść, metadane i lint pod AI.")
app.add_typer(monitoring_app, name="monitoring", help="Monitorowanie adresów URL (uptime).")
app.add_typer(issues_app, name="issues", help="Śledzenie błędów modułu 500ki.")
app.add_typer(time_app, name="time", help="Ewidencja czasu pracy (log_time, raporty).")
app.add_typer(plan_app, name="plan", help="Plan pracy użytkownika (Work Plan / Gantt).")
app.add_typer(rozliczenia_app, name="rozliczenia", help="Rozliczenia i powiązane tickety.")
app.add_typer(graph_app, name="graph", help="Graf zależności kodu (moduł Połączenia).")
app.add_typer(pipeline_app, name="pipeline", help="Obserwowalność pracy agentów AI (pipeline'y).")
app.add_typer(heartbeat_app, name="heartbeat", help="Sygnały życia (heartbeaty) procesów.")
app.add_typer(role_app, name="role", help="Role i uprawnienia w projekcie.")
