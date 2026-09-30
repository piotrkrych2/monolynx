"""Komendy monolynx_cli.commands.member: list/invite/remove.

Projekt rozstrzygany jest przez lokalne `--project` > globalne `--project` >
MONOLYNX_PROJECT > profil (patrz _common.load_settings); brak projektu to kod 2.
`remove` przyjmuje e-mail, a API usuwa po user_id, więc najpierw pobiera listę członków
i dopasowuje e-mail bez rozróżniania wielkości liter. Klienta HTTP (nazwę modułową
`_common.MonolynxClient` podmieniają testy) błędy propagują jako MonolynxError do monolynx_cli.main.
"""

from __future__ import annotations

import enum

import typer

from monolynx_cli.commands._common import confirm_destructive, fetch_all, load_settings, new_client, path_segment, project_path, require_project
from monolynx_cli.options import get_options
from monolynx_cli.output import echo_stderr, emit

app = typer.Typer(no_args_is_help=True)

_PROJECT_HELP = "Slug projektu (wygrywa z globalnym --project, MONOLYNX_PROJECT i profilem)."


class InviteRole(str, enum.Enum):  # noqa: UP042 -- cli wspiera Python 3.10, gdzie enum nie ma StrEnum
    MEMBER = "member"
    ADMIN = "admin"


@app.command("list")
def list_members(
    ctx: typer.Context,
    project: str | None = typer.Option(None, "--project", help=_PROJECT_HELP),
) -> None:
    """Wyświetl członków projektu (wszystkie strony, w kolejności z API)."""
    settings = load_settings(ctx, project)
    path = project_path(require_project(settings), "/members")
    with new_client(ctx, settings) as client:
        emit(ctx, fetch_all(client, path))


@app.command("invite")
def invite_member(
    ctx: typer.Context,
    email: str = typer.Argument(..., help="Adres e-mail zapraszanej osoby."),
    project: str | None = typer.Option(None, "--project", help=_PROJECT_HELP),
    role: InviteRole = typer.Option(InviteRole.MEMBER, "--role", help="Rola w projekcie: member lub admin."),
) -> None:
    """Zaproś osobę do projektu."""
    settings = load_settings(ctx, project)
    path = project_path(require_project(settings), "/members")
    with new_client(ctx, settings) as client:
        emit(ctx, client.post(path, json={"email": email, "role": role.value}))


@app.command("remove")
def remove_member(
    ctx: typer.Context,
    email: str = typer.Argument(..., help="Adres e-mail usuwanego członka."),
    project: str | None = typer.Option(None, "--project", help=_PROJECT_HELP),
    yes: bool = typer.Option(False, "--yes", "-y", help="Pomiń pytanie o potwierdzenie."),
) -> None:
    """Usuń członka z projektu (wymaga potwierdzenia albo --yes)."""
    opts = get_options(ctx)
    settings = load_settings(ctx, project)
    slug = require_project(settings)
    base = project_path(slug, "/members")
    confirm_destructive(f"Usunąć {email} z projektu {slug}?", yes=yes)
    with new_client(ctx, settings) as client:
        wanted = email.casefold()
        user_id = next((m.get("user_id") for m in fetch_all(client, base) if str(m.get("email", "")).casefold() == wanted), None)
        if not isinstance(user_id, str):
            typer.echo(f"{email} nie jest członkiem projektu {slug}.", err=True)
            raise typer.Exit(1)
        client.delete(f"{base}/{path_segment(user_id)}")
    echo_stderr(f"Usunięto {email} z projektu {slug}.", quiet=opts.quiet, no_color=opts.no_color)
