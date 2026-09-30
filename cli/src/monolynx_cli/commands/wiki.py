"""Komendy monolynx_cli.commands.wiki: list, get, create, update, edit, delete, search oraz podgrupa config.

Komendy bija w /api/v2/projects/{slug}/wiki/* przez klienta z `_common.open_project` (nazwę
modułową `_common.MonolynxClient` podmieniają testy). Błędy serwera i sieci to wyjątki klienta
(MonolynxError) propagowane do globalnego handlera w monolynx_cli.main (kody 1/3/4), więc
komendy nie mają własnych typer.Exit dla odpowiedzi serwera; kod 2 zostaje dla błędów użycia
wykrytych lokalnie, przed jakimkolwiek żądaniem HTTP. Wyniki idą wyłącznie przez output.emit()
(wyjątek: `get --raw`, który wypisuje surowy markdown). Slug projektu pochodzi z --project /
MONOLYNX_PROJECT / profilu (config.resolve_settings).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import typer

from monolynx_cli.client import MonolynxAPIError, MonolynxClient
from monolynx_cli.commands import _common
from monolynx_cli.options import OutputFormat, get_options
from monolynx_cli.output import echo_stderr, emit, resolve_format

app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True)
app.add_typer(config_app, name="config", help="Konfiguracja metody LLM Wiki projektu.")

LIST_PER_PAGE = 100
DEFAULT_EDITOR = "vi"


def _open_client(ctx: typer.Context) -> tuple[MonolynxClient, str]:
    client, slug = _common.open_project(ctx)
    return client, _common.project_path(slug, "/wiki")


def _page_path(base: str, page_id: str) -> str:
    return f"{base}/pages/{_common.path_segment(page_id)}"


def _resolve_content(content: str | None, file: Path | None) -> str | None:
    return _common.read_text_option(content, file, option="--content", file_option="--file")


def _expect_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MonolynxAPIError(200, "Nieprawidłowa odpowiedź serwera", "Oczekiwano obiektu JSON.")
    return payload


@app.command("list")
def list_pages(
    ctx: typer.Context,
    tree: bool = typer.Option(False, "--tree", help="Wcięcie tytułu według głębokości strony w drzewie."),
) -> None:
    """Wyświetl wszystkie strony wiki projektu (kolejność z serwera)."""
    items: list[dict[str, Any]] = []
    client, base = _open_client(ctx)
    with client:
        page = 1
        while True:
            envelope = _expect_dict(client.get(f"{base}/pages", params={"page": page, "per_page": LIST_PER_PAGE}))
            batch = envelope.get("items")
            if not isinstance(batch, list):
                raise MonolynxAPIError(200, "Nieprawidłowa odpowiedź serwera", "Odpowiedź nie zawiera listy items.")
            items.extend(row for row in batch if isinstance(row, dict))
            total_pages = envelope.get("total_pages")
            if not isinstance(total_pages, int) or page >= total_pages:
                break
            page += 1

    if tree:
        items = [{**row, "title": "  " * int(row.get("depth") or 0) + str(row.get("title", ""))} for row in items]
    emit(ctx, items)


@app.command("get")
def get_page(
    ctx: typer.Context,
    page_id: str = typer.Argument(..., help="UUID strony wiki."),
    raw: bool = typer.Option(False, "--raw", help="Wypisz sam markdown treści strony, bez formatowania."),
) -> None:
    """Pobierz stronę wiki wraz z treścią."""
    client, base = _open_client(ctx)
    with client:
        payload = _expect_dict(client.get(_page_path(base, page_id)))
    if raw:
        content = payload.get("content")
        if not isinstance(content, str):
            raise MonolynxAPIError(200, "Nieprawidłowa odpowiedź serwera", "Odpowiedź nie zawiera treści strony.")
        sys.stdout.write(content)
        return
    emit(ctx, payload)


@app.command("create")
def create_page(
    ctx: typer.Context,
    title: str = typer.Option(..., "--title", help="Tytuł strony."),
    content: str | None = typer.Option(None, "--content", help="Treść markdown; `-` czyta ze stdin."),
    file: Path | None = typer.Option(None, "--file", help="Plik z treścią markdown (alternatywa dla --content)."),
    parent: str | None = typer.Option(None, "--parent", help="UUID strony nadrzędnej."),
    position: int | None = typer.Option(None, "--position", help="Pozycja wśród rodzeństwa."),
    public: bool | None = typer.Option(
        None,
        "--public/--no-public",
        help="UWAGA: --public publikuje stronę pod adresem /blog/{slug}, widoczną dla każdego bez logowania.",
    ),
) -> None:
    """Utwórz stronę wiki. Treść z --content, --file albo stdin (`--content -`)."""
    text = _resolve_content(content, file)
    if text is None:
        raise _common.usage_error("Podaj treść: --content, --file albo --content - (stdin).")

    body: dict[str, Any] = {"title": title, "content": text}
    if parent is not None:
        body["parent_id"] = parent
    if position is not None:
        body["position"] = position
    if public is not None:
        body["is_public"] = public

    client, base = _open_client(ctx)
    with client:
        result = client.post(f"{base}/pages", json=body)
    emit(ctx, _expect_dict(result))


@app.command("update")
def update_page(
    ctx: typer.Context,
    page_id: str = typer.Argument(..., help="UUID strony wiki."),
    title: str | None = typer.Option(None, "--title", help="Nowy tytuł."),
    content: str | None = typer.Option(None, "--content", help="Nowa treść markdown; `-` czyta ze stdin."),
    file: Path | None = typer.Option(None, "--file", help="Plik z nową treścią (alternatywa dla --content)."),
    position: int | None = typer.Option(None, "--position", help="Nowa pozycja wśród rodzeństwa."),
    public: bool | None = typer.Option(
        None,
        "--public/--no-public",
        help="UWAGA: --public publikuje stronę pod adresem /blog/{slug}, widoczną dla każdego bez logowania.",
    ),
) -> None:
    """Zaktualizuj stronę wiki - wysyłane są tylko podane pola."""
    text = _resolve_content(content, file)

    body: dict[str, Any] = {}
    if title is not None:
        body["title"] = title
    if text is not None:
        body["content"] = text
    if position is not None:
        body["position"] = position
    if public is not None:
        body["is_public"] = public
    if not body:
        raise _common.usage_error("Podaj co najmniej jedno pole do zmiany: --title, --content, --file, --position, --public/--no-public.")

    client, base = _open_client(ctx)
    with client:
        result = client.patch(_page_path(base, page_id), json=body)
    emit(ctx, _expect_dict(result))


@app.command("edit")
def edit_page(
    ctx: typer.Context,
    page_id: str = typer.Argument(..., help="UUID strony wiki."),
) -> None:
    """Edytuj treść strony w edytorze z $EDITOR (domyślnie vi); zapis tylko przy zmianie treści."""
    client, base = _open_client(ctx)
    with client:
        payload = _expect_dict(client.get(_page_path(base, page_id)))
        original = payload.get("content")
        if not isinstance(original, str):
            raise MonolynxAPIError(200, "Nieprawidłowa odpowiedź serwera", "Odpowiedź nie zawiera treści strony.")

        fd, name = tempfile.mkstemp(suffix=".md")
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(original.encode("utf-8"))

            argv = [*shlex.split(os.environ.get("EDITOR") or DEFAULT_EDITOR), name]
            try:
                completed = subprocess.run(argv)
            except OSError as exc:
                typer.echo(f"Nie można uruchomić edytora ({argv[0]}): {exc}", err=True)
                raise typer.Exit(1) from exc
            if completed.returncode != 0:
                typer.echo(f"Edytor zakończył się kodem {completed.returncode}, zmiany odrzucone.", err=True)
                raise typer.Exit(1)

            edited = path.read_bytes().decode("utf-8", errors="replace")
        finally:
            path.unlink(missing_ok=True)

        if edited == original:
            opts = get_options(ctx)
            echo_stderr("Brak zmian, strona nie została zaktualizowana.", quiet=opts.quiet, no_color=opts.no_color)
            return
        result = client.patch(_page_path(base, page_id), json={"content": edited})
    emit(ctx, _expect_dict(result))


@app.command("delete")
def delete_page(
    ctx: typer.Context,
    page_id: str = typer.Argument(..., help="UUID strony wiki."),
    yes: bool = typer.Option(False, "--yes", help="Pomiń potwierdzenie (wymagane poza terminalem)."),
) -> None:
    """Usuń stronę wiki RAZEM ze wszystkimi jej podstronami (operacji nie można cofnąć)."""
    _common.path_segment(page_id)
    _common.confirm_destructive(f"Usunąć stronę {page_id} wraz z podstronami?", yes=yes)

    client, base = _open_client(ctx)
    with client:
        client.delete(_page_path(base, page_id))
    opts = get_options(ctx)
    echo_stderr(f"Usunięto stronę {page_id}.", quiet=opts.quiet, no_color=opts.no_color)


@app.command("search")
def search_pages(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Zapytanie wyszukiwania semantycznego."),
    limit: int = typer.Option(10, "--limit", min=1, help="Maksymalna liczba wyników."),
) -> None:
    """Wyszukaj strony wiki semantycznie (embeddingi)."""
    client, base = _open_client(ctx)
    with client:
        payload = client.get(f"{base}/search", params={"query": query, "limit": limit})
    if not isinstance(payload, list):
        raise MonolynxAPIError(200, "Nieprawidłowa odpowiedź serwera", "Oczekiwano listy wyników.")

    rows = [{**{k: row[k] for k in ("title", "snippet") if k in row}, **row} for row in payload if isinstance(row, dict)]

    opts = get_options(ctx)
    if not rows and resolve_format(opts.output) is OutputFormat.TABLE:
        echo_stderr(
            "Brak wyników. Wyszukiwanie semantyczne wymaga embeddingów (OPENAI_API_KEY na serwerze); "
            "bez nich lub bez zaindeksowanych stron wynik jest pusty.",
            quiet=opts.quiet,
            no_color=opts.no_color,
        )
        return
    emit(ctx, rows)


@config_app.command("get")
def config_get(ctx: typer.Context) -> None:
    """Pokaż konfigurację metody LLM Wiki projektu (flaga i strony systemowe)."""
    client, base = _open_client(ctx)
    with client:
        result = client.get(f"{base}/config")
    emit(ctx, _expect_dict(result))


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    enabled: bool | None = typer.Option(None, "--enabled/--disabled", help="Włącz albo wyłącz metodę LLM Wiki (wymaga uprawnienia settings:write)."),
) -> None:
    """Włącz albo wyłącz metodę LLM Wiki dla projektu."""
    if enabled is None:
        raise _common.usage_error("Podaj --enabled albo --disabled.")

    client, base = _open_client(ctx)
    with client:
        result = client.patch(f"{base}/config", json={"enabled": enabled})
    emit(ctx, _expect_dict(result))
