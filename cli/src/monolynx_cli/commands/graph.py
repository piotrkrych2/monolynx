"""Komendy monolynx_cli.commands.graph: nodes, node, query, path, stats (tylko odczyt grafu Połączeń).

Komendy biją w /api/v2/projects/{slug}/graph/* przez klienta z `_common.open_project`. Zapis
grafu (tworzenie, usuwanie, replace_graph) jest wyłącznie w MCP i synchronizacji CI. Kształt
każdej odpowiedzi jest sprawdzany przed użyciem: brak wymaganego pola albo zły typ to
MonolynxAPIError (kod wyjścia 1), nigdy pusty wynik z kodem 0. Filtry nieustawione przez
użytkownika nie trafiają do żądania, więc domyślne wartości zostają po stronie serwera.
"""

from __future__ import annotations

from typing import Any

import typer

from monolynx_cli.client import MonolynxAPIError
from monolynx_cli.commands import _common
from monolynx_cli.output import emit

app = typer.Typer(no_args_is_help=True)


def _invalid(detail: str) -> MonolynxAPIError:
    return MonolynxAPIError(200, "Nieprawidłowa odpowiedź serwera", detail)


def _require_objects(value: Any, name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _invalid(f"Pole {name} nie jest listą.")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise _invalid(f"Element {name}[{index}] nie jest obiektem JSON.")
    return value


def _require_object(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise _invalid("Odpowiedź nie jest obiektem JSON.")
    return payload


def _require_subgraph(payload: Any) -> dict[str, Any]:
    data = _require_object(payload)
    _require_objects(data.get("nodes"), "nodes")
    _require_objects(data.get("edges"), "edges")
    return data


def _require_node_detail(payload: Any) -> dict[str, Any]:
    data = _require_subgraph(payload)
    if not isinstance(data.get("node"), dict):
        raise _invalid("Pole node nie jest obiektem JSON.")
    if not isinstance(data.get("depth_map"), dict):
        raise _invalid("Pole depth_map nie jest obiektem JSON.")
    return data


def _require_stats(payload: Any) -> dict[str, Any]:
    data = _require_object(payload)
    for name in ("total_nodes", "total_edges"):
        value = data.get(name)
        if not isinstance(value, int) or isinstance(value, bool):
            raise _invalid(f"Pole {name} nie jest liczbą całkowitą.")
    for name in ("nodes_by_type", "edges_by_type"):
        if not isinstance(data.get(name), dict):
            raise _invalid(f"Pole {name} nie jest obiektem JSON.")
    return data


@app.command("nodes")
def list_nodes(
    ctx: typer.Context,
    node_type: str | None = typer.Option(None, "--type", help="Typ node'a: File, Class, Method, Function, Const lub Module."),
    search: str | None = typer.Option(None, "--search", help="Fragment nazwy node'a (bez rozróżniania wielkości liter)."),
    limit: int | None = typer.Option(None, "--limit", min=1, max=1000, help="Maksymalna liczba node'ów (domyślnie 100 po stronie serwera)."),
) -> None:
    """Wyświetl node'y grafu Połączeń z opcjonalnym filtrem po typie i nazwie."""
    client, slug = _common.open_project(ctx)
    params = _common.without_none({"type": node_type, "search": search, "limit": limit})
    with client:
        payload = client.get(_common.project_path(slug, "/graph/nodes"), params=params)
    emit(ctx, _require_objects(payload, "odpowiedź"))


@app.command("node")
def get_node(
    ctx: typer.Context,
    node_id: str = typer.Argument(..., help="Identyfikator node'a (pole id z `graph nodes`)."),
    depth: int | None = typer.Option(
        None, "--depth", min=1, help="Głębokość sąsiedztwa, min. 1, serwer obcina do 5 (domyślnie 1 po stronie serwera)."
    ),
    relation_type: list[str] | None = typer.Option(
        None, "--relation-type", help="Typ relacji: CONTAINS, CALLS, IMPORTS, INHERITS, USES lub IMPLEMENTS. Można powtarzać."
    ),
    node_type: list[str] | None = typer.Option(
        None, "--node-type", help="Typ sąsiada: File, Class, Method, Function, Const lub Module. Można powtarzać."
    ),
) -> None:
    """Pokaż node wraz z sąsiedztwem (node'y, krawędzie i głębokość każdego node'a)."""
    client, slug = _common.open_project(ctx)
    params = _common.without_none({"depth": depth, "relation_types": relation_type or None, "node_types": node_type or None})
    with client:
        payload = client.get(_common.project_path(slug, f"/graph/nodes/{_common.path_segment(node_id)}"), params=params)
    emit(ctx, _require_node_detail(payload))


@app.command("query")
def query_graph(
    ctx: typer.Context,
    node_type: str | None = typer.Option(None, "--node-type", help="Typ node'a: File, Class, Method, Function, Const lub Module."),
    search: str | None = typer.Option(
        None, "--search", help="Fragment nazwy lub ścieżki pliku: zwraca dopasowane node'y i ich bezpośrednich sąsiadów."
    ),
    limit: int | None = typer.Option(None, "--limit", min=1, max=1000, help="Maksymalna liczba node'ów, 1-1000 (domyślnie 200 po stronie serwera)."),
) -> None:
    """Pobierz graf albo podgraf projektu (node'y i krawędzie)."""
    client, slug = _common.open_project(ctx)
    params = _common.without_none({"node_type": node_type, "search": search, "limit": limit})
    with client:
        payload = client.get(_common.project_path(slug, "/graph/query"), params=params)
    emit(ctx, _require_subgraph(payload))


@app.command("path")
def find_path(
    ctx: typer.Context,
    source_id: str = typer.Option(..., "--source-id", help="Identyfikator node'a początkowego."),
    target_id: str = typer.Option(..., "--target-id", help="Identyfikator node'a docelowego."),
) -> None:
    """Znajdź najkrótszą ścieżkę między dwoma node'ami (brak ścieżki daje puste listy)."""
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.get(_common.project_path(slug, "/graph/path"), params={"source_id": source_id, "target_id": target_id})
    emit(ctx, _require_subgraph(payload))


@app.command("stats")
def graph_stats(ctx: typer.Context) -> None:
    """Pokaż statystyki grafu: liczba node'ów i krawędzi, także per typ."""
    client, slug = _common.open_project(ctx)
    with client:
        payload = client.get(_common.project_path(slug, "/graph/stats"))
    emit(ctx, _require_stats(payload))
