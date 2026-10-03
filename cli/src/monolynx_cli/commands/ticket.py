"""Komendy monolynx_cli.commands.ticket: tickety modułu Scrum przez /api/v2.

Grupa `ticket` odwzorowuje toole MCP: list, search, get, create, update, delete,
bulk-update oraz podgrupy `comment` (list, add), `label` (list, create) i
`ac` (list, add, update, delete). Identyfikator ticketu (UUID albo klucz, np. MON-42)
i każda inna wartość użytkownika w ścieżce przechodzą przez `_common.path_segment`
(kodowanie, a `.` i `..` kończą komendę kodem 2 bez żądania HTTP). Wartości statusów,
priorytetów i dat idą do API bez walidacji po stronie CLI: `create`, `update` i `bulk-update`
odrzuca serwer (422 -> kod wyjścia 1), ale nieznany status lub priorytet w filtrze (`list`,
`search`) serwer po cichu ignoruje - filtr nie działa, a odpowiedź to sukces.
`list`, `search` zwracają jedną stronę w kopercie (`--page`); `list` waliduje, że każdy element
niesie listę `blocked_by` (table i csv pokazują ją jako klucze po przecinku, json i yaml w pełni),
a `get` waliduje obecność `spec_page_id`; brak pola to błąd kontraktu (kod 1) przed renderem;
`list --compact` pomija opisy (parametr `compact` API, bez zmiany reszty odpowiedzi), a `create --brief` i
`update --brief` wypisują tylko `id`, `key`, `title`, `status`, `sprint_id`, `blocked_by` i `blocks` (filtr po
stronie klienta, table i csv pokazują relacje jako klucze po przecinku); odpowiedź bez tych pól to błąd kontraktu; `comment list`, `label list`
i `ac list` pobierają wszystkie strony (`_common.fetch_all`) i wypisują płaską listę.

Tekst opisu i komentarza można podać flagą, przez `-` (stdin) albo z pliku
(`--description-file`); helper `_common.read_text_option` obsługuje te źródła.
Zmiany bez żadnego pola do wysłania (update, ac update) i sprzeczne flagi blokerów
kończą się kodem 2 bez żądania HTTP.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from monolynx_cli.commands import _common
from monolynx_cli.options import OutputFormat, get_options
from monolynx_cli.output import emit, resolve_format

app = typer.Typer(no_args_is_help=True)
comment_app = typer.Typer(no_args_is_help=True)
label_app = typer.Typer(no_args_is_help=True)
ac_app = typer.Typer(no_args_is_help=True)
app.add_typer(comment_app, name="comment", help="Komentarze ticketu.")
app.add_typer(label_app, name="label", help="Etykiety projektu.")
app.add_typer(ac_app, name="ac", help="Kryteria akceptacji ticketu.")

_TICKET_ARG = Annotated[str, typer.Argument(help="UUID lub klucz ticketu (np. MON-42).")]
_CRITERION_ARG = Annotated[str, typer.Argument(help="UUID kryterium akceptacji.")]
_PAGE_OPT = Annotated[int | None, typer.Option("--page", help="Numer strony wyników.")]


def _ticket_path(slug: str, ticket: str, suffix: str = "") -> str:
    return _common.project_path(slug, f"/tickets/{_common.path_segment(ticket)}{suffix}")


def _blocker_keys(blockers: list[Any]) -> str:
    return ", ".join(b["key"] for b in blockers)


def _emit_brief(ctx: typer.Context, payload: Any) -> None:
    brief = _common.require_brief_ticket(payload)
    if resolve_format(get_options(ctx).output) in (OutputFormat.TABLE, OutputFormat.CSV):
        brief = {**brief, "blocked_by": _blocker_keys(brief["blocked_by"]), "blocks": _blocker_keys(brief["blocks"])}
    emit(ctx, brief)


@app.command("list")
def list_tickets(
    ctx: typer.Context,
    status: Annotated[str | None, typer.Option("--status", help="Filtr statusu.")] = None,
    priority: Annotated[str | None, typer.Option("--priority", help="Filtr priorytetu.")] = None,
    search: Annotated[str | None, typer.Option("--search", help="Fraza w tytule ticketu (szerzej szuka ticket search).")] = None,
    sprint: Annotated[str | None, typer.Option("--sprint", help="ID sprintu.")] = None,
    due_before: Annotated[str | None, typer.Option("--due-before", help="Termin do dnia (RRRR-MM-DD).")] = None,
    due_after: Annotated[str | None, typer.Option("--due-after", help="Termin od dnia (RRRR-MM-DD).")] = None,
    overdue: Annotated[bool, typer.Option("--overdue", help="Tylko przeterminowane.")] = False,
    label: Annotated[str | None, typer.Option("--label", help="ID etykiety.")] = None,
    page: _PAGE_OPT = None,
    compact: Annotated[bool, typer.Option("--compact", help="Lista bez opisów ticketów (pole description pominięte).")] = False,
) -> None:
    """Wypisz tickety projektu z filtrami; każdy ticket niesie blokery (blocked_by), z --compact bez opisów."""
    client, slug = _common.open_project(ctx)
    params = _common.without_none(
        {
            "status": status,
            "priority": priority,
            "search": search,
            "sprint_id": sprint,
            "due_date_before": due_before,
            "due_date_after": due_after,
            "overdue": "true" if overdue else None,
            "label_id": label,
            "compact": "true" if compact else None,
            "page": page,
        }
    )
    with client:
        payload = _common.require_list_with_blockers(client.get(_common.project_path(slug, "/tickets"), params=params))
    if resolve_format(get_options(ctx).output) in (OutputFormat.TABLE, OutputFormat.CSV):
        payload = {**payload, "items": [{**item, "blocked_by": _blocker_keys(item["blocked_by"])} for item in payload["items"]]}
    emit(ctx, payload)


@app.command("search")
def search_tickets(
    ctx: typer.Context,
    query: Annotated[str | None, typer.Argument(help="Szukana fraza.")] = None,
    status: Annotated[str | None, typer.Option("--status", help="Filtr statusu.")] = None,
    priority: Annotated[str | None, typer.Option("--priority", help="Filtr priorytetu.")] = None,
    assignee: Annotated[str | None, typer.Option("--assignee", help="E-mail przypisanej osoby.")] = None,
    sprint: Annotated[str | None, typer.Option("--sprint", help="ID sprintu.")] = None,
    due_before: Annotated[str | None, typer.Option("--due-before", help="Termin do dnia (RRRR-MM-DD).")] = None,
    due_after: Annotated[str | None, typer.Option("--due-after", help="Termin od dnia (RRRR-MM-DD).")] = None,
    page: _PAGE_OPT = None,
) -> None:
    """Wyszukaj tickety po frazie i filtrach."""
    client, slug = _common.open_project(ctx)
    params = _common.without_none(
        {
            "query": query,
            "status": status,
            "priority": priority,
            "assignee_email": assignee,
            "sprint_id": sprint,
            "due_before": due_before,
            "due_after": due_after,
            "page": page,
        }
    )
    with client:
        payload = client.get(_common.project_path(slug, "/tickets/search"), params=params)
    emit(ctx, payload)


@app.command("get")
def get_ticket(ctx: typer.Context, ticket: _TICKET_ARG) -> None:
    """Pokaż szczegóły ticketu, w tym spec_page_id (null, gdy ticket nie ma strony specyfikacji)."""
    client, slug = _common.open_project(ctx)
    with client:
        payload = _common.require_spec_page_id(client.get(_ticket_path(slug, ticket)))
    emit(ctx, payload)


@app.command("create")
def create_ticket(
    ctx: typer.Context,
    title: Annotated[str, typer.Option("--title", help="Tytuł ticketu.")],
    description: Annotated[str | None, typer.Option("--description", help="Opis (markdown); `-` czyta ze stdin.")] = None,
    description_file: Annotated[Path | None, typer.Option("--description-file", help="Plik z opisem (UTF-8).")] = None,
    priority: Annotated[str | None, typer.Option("--priority", help="Priorytet.")] = None,
    story_points: Annotated[int | None, typer.Option("--sp", "--story-points", help="Story points.")] = None,
    sprint: Annotated[str | None, typer.Option("--sprint", help="ID sprintu.")] = None,
    assignee: Annotated[str | None, typer.Option("--assignee", help="E-mail przypisanej osoby.")] = None,
    due: Annotated[str | None, typer.Option("--due", help="Termin (RRRR-MM-DD).")] = None,
    label: Annotated[list[str] | None, typer.Option("--label", help="ID etykiety; można powtarzać.")] = None,
    ac: Annotated[list[str] | None, typer.Option("--ac", help="Kryterium akceptacji; można powtarzać.")] = None,
    spec_page: Annotated[str | None, typer.Option("--spec-page", help="ID strony wiki ze specyfikacją.")] = None,
    blocked_by: Annotated[list[str] | None, typer.Option("--blocked-by", help="UUID ticketu blokującego; można powtarzać.")] = None,
    brief: Annotated[bool, typer.Option("--brief", help="Wypisz tylko id, key, title, status, sprint_id, blocked_by i blocks.")] = False,
) -> None:
    """Utwórz ticket; z --brief wypisuje tylko id, key, title, status, sprint_id i relacje."""
    text = _common.read_text_option(description, description_file, option="--description", file_option="--description-file")
    client, slug = _common.open_project(ctx)
    body = _common.without_none(
        {
            "title": title,
            "description": text,
            "priority": priority,
            "story_points": story_points,
            "sprint_id": sprint,
            "assignee_email": assignee,
            "due_date": due,
            "label_ids": label,
            "acceptance_criteria": ac,
            "spec_page_id": spec_page,
            "blocked_by_ids": blocked_by,
        }
    )
    with client:
        payload = client.post(_common.project_path(slug, "/tickets"), json=body)
    if brief:
        _emit_brief(ctx, payload)
        return
    emit(ctx, payload)


@app.command("update")
def update_ticket(
    ctx: typer.Context,
    ticket: _TICKET_ARG,
    title: Annotated[str | None, typer.Option("--title", help="Nowy tytuł.")] = None,
    description: Annotated[str | None, typer.Option("--description", help="Nowy opis (markdown); `-` czyta ze stdin.")] = None,
    description_file: Annotated[Path | None, typer.Option("--description-file", help="Plik z nowym opisem (UTF-8).")] = None,
    status: Annotated[str | None, typer.Option("--status", help="Nowy status.")] = None,
    priority: Annotated[str | None, typer.Option("--priority", help="Nowy priorytet.")] = None,
    story_points: Annotated[int | None, typer.Option("--sp", "--story-points", help="Story points.")] = None,
    sprint: Annotated[str | None, typer.Option("--sprint", help="ID sprintu; pusty tekst odpina od sprintu.")] = None,
    assignee: Annotated[str | None, typer.Option("--assignee", help="E-mail przypisanej osoby; pusty tekst zdejmuje przypisanie.")] = None,
    due: Annotated[str | None, typer.Option("--due", help="Termin (RRRR-MM-DD); pusty tekst czyści termin.")] = None,
    label: Annotated[list[str] | None, typer.Option("--label", help="ID etykiety (zastępuje dotychczasowe); można powtarzać.")] = None,
    blocked_by: Annotated[list[str] | None, typer.Option("--blocked-by", help="UUID ticketu blokującego (nadpisuje); można powtarzać.")] = None,
    no_blockers: Annotated[bool, typer.Option("--no-blockers", help="Usuń wszystkie blokery.")] = False,
    brief: Annotated[bool, typer.Option("--brief", help="Wypisz tylko id, key, title, status, sprint_id, blocked_by i blocks.")] = False,
) -> None:
    """Zaktualizuj ticket; do API trafiają tylko podane pola, z --brief wynik skrócony do pól identyfikujących i relacji."""
    if blocked_by and no_blockers:
        raise _common.usage_error("Podaj --blocked-by albo --no-blockers, nie oba naraz.")
    text = _common.read_text_option(description, description_file, option="--description", file_option="--description-file")
    body = _common.without_none(
        {
            "title": title,
            "description": text,
            "status": status,
            "priority": priority,
            "story_points": story_points,
            "sprint_id": sprint,
            "assignee_email": assignee,
            "due_date": due,
            "label_ids": label,
            "blocked_by_ids": [] if no_blockers else blocked_by,
        }
    )
    if not body:
        raise _common.usage_error("Brak zmian: podaj co najmniej jedną flagę do aktualizacji.")
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.patch(_ticket_path(slug, ticket), json=body)
    if brief:
        _emit_brief(ctx, payload)
        return
    emit(ctx, payload)


@app.command("delete")
def delete_ticket(
    ctx: typer.Context,
    ticket: _TICKET_ARG,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Pomiń pytanie o potwierdzenie.")] = False,
) -> None:
    """Usuń ticket; bez --yes pyta o potwierdzenie (wymaga terminala)."""
    _common.path_segment(ticket)
    client, slug = _common.open_project(ctx)
    _common.confirm_destructive(f"Usunąć ticket {ticket}?", yes=yes)
    with client:
        client.delete(_ticket_path(slug, ticket))
    emit(ctx, {"id": ticket, "deleted": True})


@app.command("bulk-update")
def bulk_update_tickets(
    ctx: typer.Context,
    tickets: Annotated[list[str], typer.Argument(help="UUID lub klucze ticketów.")],
    status: Annotated[str | None, typer.Option("--status", help="Nowy status.")] = None,
    priority: Annotated[str | None, typer.Option("--priority", help="Nowy priorytet.")] = None,
    assignee: Annotated[str | None, typer.Option("--assignee", help="E-mail przypisanej osoby; pusty tekst zdejmuje przypisanie.")] = None,
    sprint: Annotated[str | None, typer.Option("--sprint", help="ID sprintu; pusty tekst odpina od sprintu.")] = None,
    due: Annotated[str | None, typer.Option("--due", help="Termin (RRRR-MM-DD); pusty tekst czyści termin.")] = None,
) -> None:
    """Zaktualizuj wiele ticketów jednym żądaniem."""
    client, slug = _common.open_project(ctx)
    body = _common.without_none(
        {
            "ticket_ids": tickets,
            "status": status,
            "priority": priority,
            "assignee_email": assignee,
            "sprint_id": sprint,
            "due_date": due,
        }
    )
    with client:
        payload = client.patch(_common.project_path(slug, "/tickets/bulk"), json=body)
    emit(ctx, payload)


@comment_app.command("list")
def list_comments(ctx: typer.Context, ticket: _TICKET_ARG) -> None:
    """Wypisz komentarze ticketu."""
    client, slug = _common.open_project(ctx)
    with client:
        items = _common.fetch_all(client, _ticket_path(slug, ticket, "/comments"))
    emit(ctx, items)


@comment_app.command("add")
def add_comment(
    ctx: typer.Context,
    ticket: _TICKET_ARG,
    body: Annotated[str, typer.Option("--body", help="Treść komentarza (markdown); `-` czyta ze stdin.")],
) -> None:
    """Dodaj komentarz do ticketu."""
    content = _common.read_text_option(body, None, option="--body")
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.post(_ticket_path(slug, ticket, "/comments"), json={"content": content})
    emit(ctx, payload)


@label_app.command("list")
def list_labels(ctx: typer.Context) -> None:
    """Wypisz etykiety projektu."""
    client, slug = _common.open_project(ctx)
    with client:
        items = _common.fetch_all(client, _common.project_path(slug, "/labels"))
    emit(ctx, items)


@label_app.command("create")
def create_label(
    ctx: typer.Context,
    name: Annotated[str, typer.Option("--name", help="Nazwa etykiety.")],
    color: Annotated[str | None, typer.Option("--color", help="Kolor (np. #ff0000).")] = None,
) -> None:
    """Utwórz etykietę projektu."""
    client, slug = _common.open_project(ctx)
    body: dict[str, Any] = _common.without_none({"name": name, "color": color})
    with client:
        payload = client.post(_common.project_path(slug, "/labels"), json=body)
    emit(ctx, payload)


@ac_app.command("list")
def list_criteria(ctx: typer.Context, ticket: _TICKET_ARG) -> None:
    """Wypisz kryteria akceptacji ticketu."""
    client, slug = _common.open_project(ctx)
    with client:
        items = _common.fetch_all(client, _ticket_path(slug, ticket, "/acceptance-criteria"))
    emit(ctx, items)


@ac_app.command("add")
def add_criterion(
    ctx: typer.Context,
    ticket: _TICKET_ARG,
    description: Annotated[str, typer.Argument(help="Opis kryterium akceptacji.")],
) -> None:
    """Dodaj kryterium akceptacji do ticketu."""
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.post(
            _ticket_path(slug, ticket, "/acceptance-criteria"),
            json={"description": description},
        )
    emit(ctx, payload)


@ac_app.command("update")
def update_criterion(
    ctx: typer.Context,
    ticket: _TICKET_ARG,
    criterion_id: _CRITERION_ARG,
    description: Annotated[str | None, typer.Option("--description", help="Nowy opis kryterium.")] = None,
    done: Annotated[bool | None, typer.Option("--done/--not-done", help="Oznacz jako spełnione albo niespełnione.")] = None,
) -> None:
    """Zmień opis lub stan spełnienia kryterium akceptacji."""
    body = _common.without_none({"description": description, "is_completed": done})
    if not body:
        raise _common.usage_error("Brak zmian: podaj --description, --done albo --not-done.")
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.patch(
            _ticket_path(slug, ticket, f"/acceptance-criteria/{_common.path_segment(criterion_id)}"),
            json=body,
        )
    emit(ctx, payload)


@ac_app.command("delete")
def delete_criterion(ctx: typer.Context, ticket: _TICKET_ARG, criterion_id: _CRITERION_ARG) -> None:
    """Usuń kryterium akceptacji."""
    client, slug = _common.open_project(ctx)
    with client:
        client.delete(_ticket_path(slug, ticket, f"/acceptance-criteria/{_common.path_segment(criterion_id)}"))
    emit(ctx, {"id": criterion_id, "deleted": True})
