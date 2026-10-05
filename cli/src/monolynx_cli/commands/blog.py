"""Komendy monolynx_cli.commands.blog: list, get, meta (metadane wpisów bloga zapisanych jako strony wiki).

Komendy biją w /api/v2/projects/{slug}/blog/posts*. `list` i `get` to odczyty, `meta` to częściowy
zapis (PATCH): do body trafiają tylko opcje podane przez użytkownika, a pusty tekst w polach
nullowalnych (`--translation-of`, `--cover`, `--verified`) wysyła `null` i czyści pole. Publikacja
wpisu i jej cofnięcie zostają w `monolynx wiki`. Kształt każdej odpowiedzi jest sprawdzany przed
`emit()`: brak wymaganego pola albo zły typ to MonolynxAPIError (kod wyjścia 1), nigdy pusty wynik
z kodem 0. `blog list` w trybie table pokazuje tylko tytuł, slug, status publikacji, język, datę
publikacji i tagi; pozostałe formaty niosą pełne pola.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from monolynx_cli.client import MonolynxAPIError
from monolynx_cli.commands import _common
from monolynx_cli.options import OutputFormat, get_options
from monolynx_cli.output import emit, resolve_format

app = typer.Typer(no_args_is_help=True)

_POST_ARG = Annotated[str, typer.Argument(help="UUID strony wiki, która jest wpisem bloga (pole page_id z `blog list`).")]


def _invalid(detail: str) -> MonolynxAPIError:
    return MonolynxAPIError(200, "Nieprawidłowa odpowiedź serwera", detail)


def _require_post(payload: Any, where: str = "Odpowiedź") -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _invalid(f"{where} nie jest obiektem JSON.")
    for name in ("page_id", "title", "slug"):
        if not isinstance(payload.get(name), str):
            raise _invalid(f"{where}: pole {name} nie jest tekstem.")
    if not isinstance(payload.get("is_public"), bool):
        raise _invalid(f"{where}: pole is_public nie jest wartością logiczną.")
    tags = payload.get("tags")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise _invalid(f"{where}: pole tags nie jest listą tekstów.")
    for name in ("public_url", "markdown_url"):
        if name not in payload:
            raise _invalid(f"{where}: brak pola {name}.")
        if payload[name] is not None and not isinstance(payload[name], str):
            raise _invalid(f"{where}: pole {name} nie jest tekstem ani null.")
    return payload


def _table_row(item: dict[str, Any]) -> dict[str, Any]:
    # Pełny rekord ma 15 kolumn i Rich ucina w nich każdą wartość; tabela dostaje zwięzły zestaw,
    # json/yaml/csv zostają z pełnymi polami.
    return {
        "title": item["title"],
        "slug": item["slug"],
        "is_public": item["is_public"],
        "lang": item.get("lang"),
        "published_at": item.get("published_at"),
        "tags": ", ".join(item["tags"]),
    }


def _require_detail(payload: Any) -> dict[str, Any]:
    data = _require_post(payload)
    if not isinstance(data.get("content"), str):
        raise _invalid("Odpowiedź: pole content nie jest tekstem.")
    return data


@app.command("list")
def list_posts(
    ctx: typer.Context,
    status: Annotated[
        str | None, typer.Option("--status", help="Filtr: published (opublikowane) albo draft (szkice); bez filtra wszystkie wpisy.")
    ] = None,
) -> None:
    """Wyświetl wpisy bloga projektu (opublikowane i szkice) z metadanymi, bez treści."""
    client, slug = _common.open_project(ctx)
    params = _common.without_none({"status": status})
    with client:
        items = _common.fetch_all(client, _common.project_path(slug, "/blog/posts"), params)
    for index, item in enumerate(items):
        _require_post(item, f"Element items[{index}]")
    if resolve_format(get_options(ctx).output) is OutputFormat.TABLE:
        items = [_table_row(item) for item in items]
    emit(ctx, items)


@app.command("get")
def get_post(ctx: typer.Context, page_id: _POST_ARG) -> None:
    """Pokaż wpis bloga: metadane, adresy publiczne i treść markdown."""
    path_id = _common.path_segment(page_id)
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.get(_common.project_path(slug, f"/blog/posts/{path_id}"))
    emit(ctx, _require_detail(payload))


@app.command("meta")
def set_post_meta(
    ctx: typer.Context,
    page_id: _POST_ARG,
    slug: Annotated[str | None, typer.Option("--slug", help="Nowy slug wpisu (stary trafia do historii i przekierowuje).")] = None,
    description: Annotated[str | None, typer.Option("--description", help="Opis wpisu (meta description).")] = None,
    author: Annotated[str | None, typer.Option("--author", help="Nazwa autora.")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", help="Tag wpisu (zastępuje dotychczasowe); można powtarzać.")] = None,
    no_tags: Annotated[bool, typer.Option("--no-tags", help="Usuń wszystkie tagi.")] = False,
    lang: Annotated[str | None, typer.Option("--lang", help="Język wpisu, np. pl albo en.")] = None,
    translation_of: Annotated[
        str | None, typer.Option("--translation-of", help="UUID strony wiki z oryginałem tłumaczenia; pusty tekst czyści powiązanie.")
    ] = None,
    cover: Annotated[str | None, typer.Option("--cover", help="UUID załącznika strony wiki użytego jako okładka; pusty tekst usuwa okładkę.")] = None,
    verified: Annotated[
        str | None, typer.Option("--verified", help="Data ostatniej weryfikacji treści (RRRR-MM-DD); pusty tekst czyści datę.")
    ] = None,
) -> None:
    """Zmień metadane wpisu bloga; do API trafiają tylko podane opcje, pusty tekst czyści pola nullowalne."""
    if tag and no_tags:
        raise _common.usage_error("Podaj --tag albo --no-tags, nie oba naraz.")
    body: dict[str, Any] = _common.without_none(
        {"slug": slug, "description": description, "author_name": author, "tags": [] if no_tags else tag, "lang": lang}
    )
    # Nie przez without_none: tam None znaczy "nie podano", a tu pusty tekst ma dojść do API jako null.
    for field, value in (("translation_of_id", translation_of), ("cover_attachment_id", cover), ("last_verified_at", verified)):
        if value is not None:
            body[field] = value or None
    if not body:
        raise _common.usage_error("Brak zmian: podaj co najmniej jedną opcję do aktualizacji.")
    path_id = _common.path_segment(page_id)
    client, project_slug = _common.open_project(ctx)
    with client:
        payload = client.patch(_common.project_path(project_slug, f"/blog/posts/{path_id}"), json=body)
    emit(ctx, _require_post(payload))
