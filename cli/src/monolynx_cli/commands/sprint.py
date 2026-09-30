"""Komendy monolynx_cli.commands.sprint: sprinty, tablica Kanban i burndown przez API v2.

Odwzorowują toole MCP modułu Scrum (`list_sprints`, `get_sprint`, `create_sprint`,
`update_sprint`, `start_sprint`, `complete_sprint`, `get_board`, `get_burndown`).
Wartości flag (daty, status) idą do API bez walidacji po stronie CLI -- serwer zwraca 422,
które globalny handler zamienia na exit 1. Identyfikator sprintu w ścieżce przechodzi przez
`_common.path_segment` (`.` i `..` to exit 2 bez żądania). `list` pobiera wszystkie strony
(`_common.fetch_all`, filtr `--status` na każdej) i wypisuje płaską listę sprintów, nie kopertę.
Wyniki przechodzą przez output.emit().
`board` w formatach table/csv spłaszcza kolumny do wierszy z polem `status`; w json/yaml
wypisuje surową odpowiedź.
"""

from __future__ import annotations

from typing import Any

import typer

from monolynx_cli.commands import _common
from monolynx_cli.options import OutputFormat, get_options
from monolynx_cli.output import echo_stderr, emit, resolve_format

app = typer.Typer(no_args_is_help=True)

_BOARD_COLUMNS = ("todo", "in_progress", "in_review", "done")
_NO_ACTIVE_SPRINT = "(Brak aktywnego sprintu)"


def _sprint_path(slug: str, sprint_id: str, suffix: str = "") -> str:
    return _common.project_path(slug, f"/sprints/{_common.path_segment(sprint_id)}{suffix}")


@app.command("list")
def list_sprints(
    ctx: typer.Context,
    status: str | None = typer.Option(None, "--status", help="Filtr statusu sprintu (np. planning, active, completed)."),
) -> None:
    """Wypisz sprinty projektu (wszystkie strony)."""
    client, slug = _common.open_project(ctx)
    with client:
        items = _common.fetch_all(client, _common.project_path(slug, "/sprints"), _common.without_none({"status": status}))
    emit(ctx, items)


@app.command("get")
def get_sprint(ctx: typer.Context, sprint_id: str = typer.Argument(..., help="Identyfikator sprintu.")) -> None:
    """Pokaż szczegóły sprintu."""
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.get(_sprint_path(slug, sprint_id))
    emit(ctx, payload)


@app.command("create")
def create_sprint(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Nazwa sprintu."),
    start: str = typer.Option(..., "--start", help="Data rozpoczęcia (YYYY-MM-DD)."),
    end: str | None = typer.Option(None, "--end", help="Data zakończenia (YYYY-MM-DD)."),
    goal: str | None = typer.Option(None, "--goal", help="Cel sprintu."),
) -> None:
    """Utwórz nowy sprint."""
    body = _common.without_none({"name": name, "start_date": start, "end_date": end, "goal": goal})
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.post(_common.project_path(slug, "/sprints"), json=body)
    emit(ctx, payload)


@app.command("update")
def update_sprint(
    ctx: typer.Context,
    sprint_id: str = typer.Argument(..., help="Identyfikator sprintu."),
    name: str | None = typer.Option(None, "--name", help="Nowa nazwa sprintu."),
    goal: str | None = typer.Option(None, "--goal", help="Nowy cel sprintu."),
    start: str | None = typer.Option(None, "--start", help="Nowa data rozpoczęcia (YYYY-MM-DD)."),
    end: str | None = typer.Option(None, "--end", help="Nowa data zakończenia (YYYY-MM-DD)."),
) -> None:
    """Zmień nazwę, cel lub daty sprintu."""
    body = _common.without_none({"name": name, "goal": goal, "start_date": start, "end_date": end})
    if not body:
        raise _common.usage_error("Podaj co najmniej jedną zmianę: --name, --goal, --start lub --end.")
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.patch(_sprint_path(slug, sprint_id), json=body)
    emit(ctx, payload)


@app.command("start")
def start_sprint(ctx: typer.Context, sprint_id: str = typer.Argument(..., help="Identyfikator sprintu.")) -> None:
    """Uruchom sprint (tylko jeden sprint może być aktywny naraz)."""
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.post(_sprint_path(slug, sprint_id, "/start"))
    emit(ctx, payload)


@app.command("complete")
def complete_sprint(ctx: typer.Context, sprint_id: str = typer.Argument(..., help="Identyfikator sprintu.")) -> None:
    """Zakończ sprint; niezamknięte tickety wracają do backlogu."""
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.post(_sprint_path(slug, sprint_id, "/complete"))
    emit(ctx, payload)


@app.command("board")
def board(ctx: typer.Context) -> None:
    """Pokaż tablicę Kanban aktywnego sprintu."""
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.get(_common.project_path(slug, "/board"))

    opts = get_options(ctx)
    fmt = resolve_format(opts.output)
    if fmt in (OutputFormat.JSON, OutputFormat.YAML):
        emit(ctx, payload)
        return

    sprint = payload.get("sprint")
    if sprint is None:
        typer.echo(_NO_ACTIVE_SPRINT)
        return

    columns = payload.get("columns") or {}
    rows: list[dict[str, Any]] = [{"status": column, **ticket} for column in _BOARD_COLUMNS for ticket in columns.get(column) or []]
    echo_stderr(f"Sprint: {sprint.get('name')}", quiet=opts.quiet, no_color=opts.no_color)
    emit(ctx, rows)


@app.command("burndown")
def burndown(
    ctx: typer.Context,
    sprint_id: str | None = typer.Argument(None, help="Identyfikator sprintu. Bez podania: aktywny sprint."),
) -> None:
    """Pokaż burndown sprintu (domyślnie aktywnego)."""
    if sprint_id is not None:
        _common.path_segment(sprint_id)  # identyfikator jest w query, ale `..` odrzucamy tak samo jak w ścieżkach
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.get(_common.project_path(slug, "/burndown"), params=_common.without_none({"sprint_id": sprint_id}))
    emit(ctx, payload)
