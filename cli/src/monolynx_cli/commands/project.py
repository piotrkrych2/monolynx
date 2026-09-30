"""Komendy monolynx_cli.commands.project: list/get/create/update/delete/summary.

Cienki klient /api/v2/projects przez _common.new_client (nazwę modułową
`_common.MonolynxClient` podmieniają testy). Walidacja i komunikaty błędów żyją w backendzie, błędy
HTTP propagują jako MonolynxError do handlera w monolynx_cli.main. Slug projektu to argument
pozycyjny (nie --project), bo dotyczy samego zasobu. Wyniki idą przez output.emit().
"""

from __future__ import annotations

from typing import Any

import typer

from monolynx_cli.commands._common import confirm_destructive, fetch_all, load_settings, new_client, project_path
from monolynx_cli.options import get_options
from monolynx_cli.output import echo_stderr, emit

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_projects(ctx: typer.Context) -> None:
    """Wyświetl projekty, do których masz dostęp (wszystkie strony)."""
    settings = load_settings(ctx)
    with new_client(ctx, settings) as client:
        emit(ctx, fetch_all(client, "/api/v2/projects"))


@app.command("get")
def get_project(
    ctx: typer.Context,
    slug: str = typer.Argument(..., help="Slug projektu."),
) -> None:
    """Wyświetl szczegóły projektu: rolę, liczbę członków i ticketów, aktywny sprint."""
    path = project_path(slug)
    settings = load_settings(ctx)
    with new_client(ctx, settings) as client:
        emit(ctx, client.get(path))


@app.command("create")
def create_project(
    ctx: typer.Context,
    name: str = typer.Option(..., "--name", help="Nazwa projektu."),
    slug: str | None = typer.Option(None, "--slug", help="Slug projektu (bez podania serwer generuje go z nazwy)."),
    description: str | None = typer.Option(None, "--description", help="Opis projektu."),
    code: str | None = typer.Option(None, "--code", help="Kod projektu, prefiks numerów ticketów (bez podania serwer generuje go z nazwy)."),
) -> None:
    """Utwórz nowy projekt."""
    fields: dict[str, Any] = {"name": name, "slug": slug, "description": description, "code": code}
    body = {key: value for key, value in fields.items() if value is not None}
    settings = load_settings(ctx)
    with new_client(ctx, settings) as client:
        emit(ctx, client.post("/api/v2/projects", json=body))


@app.command("update")
def update_project(
    ctx: typer.Context,
    slug: str = typer.Argument(..., help="Slug projektu."),
    name: str | None = typer.Option(None, "--name", help="Nowa nazwa projektu."),
    description: str | None = typer.Option(None, "--description", help="Nowy opis projektu (pusty tekst czyści opis)."),
    new_slug: str | None = typer.Option(None, "--new-slug", help="Nowy slug projektu."),
) -> None:
    """Zmień projekt: wysyłane są tylko podane flagi."""
    fields: dict[str, Any] = {"name": name, "description": description, "slug": new_slug}
    body = {key: value for key, value in fields.items() if value is not None}
    if not body:
        typer.echo("Podaj co najmniej jedną z flag: --name, --description, --new-slug.", err=True)
        raise typer.Exit(2)
    path = project_path(slug)
    settings = load_settings(ctx)
    with new_client(ctx, settings) as client:
        emit(ctx, client.patch(path, json=body))


@app.command("delete")
def delete_project(
    ctx: typer.Context,
    slug: str = typer.Argument(..., help="Slug projektu."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Pomiń pytanie o potwierdzenie."),
) -> None:
    """Usuń projekt (wymaga potwierdzenia albo --yes)."""
    opts = get_options(ctx)
    path = project_path(slug)
    confirm_destructive(f"Usunąć projekt {slug}?", yes=yes)
    settings = load_settings(ctx)
    with new_client(ctx, settings) as client:
        client.delete(path)
    echo_stderr(f"Usunięto projekt {slug}.", quiet=opts.quiet, no_color=opts.no_color)


@app.command("summary")
def project_summary(
    ctx: typer.Context,
    slug: str = typer.Argument(..., help="Slug projektu."),
) -> None:
    """Wyświetl podsumowanie projektu: nierozwiązane błędy, monitory, uptime, aktywny sprint, backlog."""
    path = project_path(slug, "/summary")
    settings = load_settings(ctx)
    with new_client(ctx, settings) as client:
        emit(ctx, client.get(path))
