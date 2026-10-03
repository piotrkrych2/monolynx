"""Testy komend monolynx_cli.commands.graph (MON-248): nodes, node, query, path, stats.

Wszystkie testy przechodza przez root app (`monolynx_cli.main.app`) z flagami globalnymi PRZED grupa
`graph` (sama komenda bez roota nie dowodzi, ze grupa jest podpieta). Punkt wstrzykniecia HTTP to nazwa
modulowa `monolynx_cli.commands._common.MonolynxClient` (transport z fixture `mock_transport`, sleep no-op).
Wszystkie 5 komend to odczyt (GET); wynik idzie przez `emit`, wiec w `-o json` na stdout laduje pelna
struktura z API. Odpowiedz o zlym ksztalcie to blad kontraktu: kod 1 i komunikat "Nieprawidłowa odpowiedź
serwera" na stderr, nigdy pusty wynik z kodem 0.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any

import httpx
import pytest
from monolynx_cli import client as client_mod
from monolynx_cli.main import app

pytestmark = pytest.mark.unit

SLUG = "proj-x"
NODE_ID = "n1"
BASE = f"/api/v2/projects/{SLUG}/graph"
_PROBLEM_CT = {"content-type": "application/problem+json"}
_GRAPH_DISABLED = "Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)"


def _patch_client(monkeypatch, mock_transport, handler):
    from monolynx_cli.commands import _common

    monkeypatch.setenv("MONOLYNX_TOKEN", "tok-value-1234")
    transport, requests = mock_transport(handler)
    monkeypatch.setattr(
        _common,
        "MonolynxClient",
        functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None),
    )
    return requests


def _json(payload, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _problem(status, detail="Szczegoly", title="Blad"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"type": "about:blank", "title": title, "status": status, "detail": detail}, headers=_PROBLEM_CT)

    return handler


def _run(runner, *args, output="json"):
    return runner.invoke(app, ["-o", output, "--project", SLUG, "graph", *args])


def _node(node_id="n1", name="Alfa", node_type="Class", **extra):
    base = {
        "id": node_id,
        "project_id": "11111111-1111-4111-8111-111111111111",
        "name": name,
        "type": node_type,
        "file_path": "app/alfa.py",
        "line_number": 7,
        "metadata": {},
    }
    return {**base, **extra}


def _edge(source_id="n1", target_id="n2", edge_type="CALLS"):
    return {"source_id": source_id, "target_id": target_id, "type": edge_type, "confidence": "EXTRACTED", "source_relation": "calls", "metadata": {}}


_NODES = [_node("n1", "Alfa", "Class"), _node("n2", "beta_fn", "Function")]
_SUBGRAPH = {"nodes": _NODES, "edges": [_edge()]}
_NODE_DETAIL = {"node": _NODES[0], "nodes": _NODES, "edges": [_edge()], "depth_map": {"n1": 0, "n2": 1}}
_STATS = {
    "total_nodes": 2,
    "total_edges": 1,
    "nodes_by_type": {"File": 0, "Class": 1, "Function": 1, "Method": 0, "Const": 0, "Module": 0},
    "edges_by_type": {"CALLS": 1},
}


def _without(payload: dict[str, Any], key: str) -> dict[str, Any]:
    return {name: value for name, value in payload.items() if name != key}


# Odpowiedzi 200 o zlym ksztalcie podkomendy `query` i `path` (ten sam walidator podgrafu): po jednym
# przypadku na kazde wymagane pole i na element nie-obiekt w kazdej z list.
_BAD_SUBGRAPHS: list[Any] = [
    {"nodes": "nie-lista", "edges": []},
    _without(_SUBGRAPH, "edges"),
    _without(_SUBGRAPH, "nodes"),
    {"nodes": ["x"], "edges": []},
    {"nodes": [], "edges": [1]},
    [_SUBGRAPH],
]

# (id, argumenty komendy, sufiks sciezki API, poprawna odpowiedz, niepoprawne odpowiedzi 200)
_COMMANDS: list[tuple[str, list[str], str, Any, list[Any]]] = [
    ("nodes", ["nodes"], "/nodes", _NODES, [{"nodes": []}, "tekst", 5, [1]]),
    (
        "node",
        ["node", NODE_ID],
        f"/nodes/{NODE_ID}",
        _NODE_DETAIL,
        [
            {**_NODE_DETAIL, "nodes": "nie-lista"},
            {**_NODE_DETAIL, "nodes": ["x"]},
            {**_NODE_DETAIL, "edges": [1]},
            _without(_NODE_DETAIL, "edges"),
            _without(_NODE_DETAIL, "node"),
            {**_NODE_DETAIL, "node": "nie-obiekt"},
            _without(_NODE_DETAIL, "depth_map"),
            {**_NODE_DETAIL, "depth_map": [0, 1]},
            [_NODE_DETAIL],
        ],
    ),
    ("query", ["query"], "/query", _SUBGRAPH, _BAD_SUBGRAPHS),
    ("path", ["path", "--source-id", "a", "--target-id", "b"], "/path", _SUBGRAPH, _BAD_SUBGRAPHS),
    (
        "stats",
        ["stats"],
        "/stats",
        _STATS,
        [
            {"total_edges": 1},
            _without(_STATS, "total_nodes"),
            _without(_STATS, "total_edges"),
            {**_STATS, "total_nodes": True},
            {**_STATS, "total_edges": True},
            {**_STATS, "total_edges": "1"},
            _without(_STATS, "nodes_by_type"),
            _without(_STATS, "edges_by_type"),
            {**_STATS, "nodes_by_type": [1]},
            [_STATS],
        ],
    ),
]
_COMMAND_IDS = [case[0] for case in _COMMANDS]
_INVALID_CASES = [(case[0], case[1], bad_index, bad) for case in _COMMANDS for bad_index, bad in enumerate(case[4])]
_INVALID_IDS = [f"{case_id}-{bad_index}" for case_id, _args, bad_index, _bad in _INVALID_CASES]


# ---------------------------------------------------------------------------
# Wspolne: sciezka, metoda, wynik JSON, bledy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("_id", "args", "suffix", "payload", "_invalid"), _COMMANDS, ids=_COMMAND_IDS)
def test_command_sends_get_to_graph_path_of_project(runner, monkeypatch, mock_transport, _id, args, suffix, payload, _invalid):
    requests = _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, *args)

    assert result.exit_code == 0, result.output
    assert [(r.method, r.url.path) for r in requests] == [("GET", f"{BASE}{suffix}")]
    assert requests[0].content == b""


@pytest.mark.parametrize(("_id", "args", "_suffix", "payload", "_invalid"), _COMMANDS, ids=_COMMAND_IDS)
def test_json_output_is_full_api_structure(runner, monkeypatch, mock_transport, _id, args, _suffix, payload, _invalid):
    _patch_client(monkeypatch, mock_transport, _json(payload))

    result = _run(runner, *args)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == payload


@pytest.mark.parametrize(("case_id", "args", "_bad_index", "bad"), _INVALID_CASES, ids=_INVALID_IDS)
def test_malformed_200_response_is_contract_error_exit_1_with_empty_stdout(runner, monkeypatch, mock_transport, case_id, args, _bad_index, bad):
    _patch_client(monkeypatch, mock_transport, _json(bad))

    result = _run(runner, *args)

    assert result.exit_code == 1
    assert "Nieprawidłowa odpowiedź serwera" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(("_id", "args", "_suffix", "_payload", "_invalid"), _COMMANDS, ids=_COMMAND_IDS)
def test_graph_disabled_503_is_api_error_exit_1_with_detail_on_stderr(runner, monkeypatch, mock_transport, _id, args, _suffix, _payload, _invalid):
    _patch_client(monkeypatch, mock_transport, _problem(503, _GRAPH_DISABLED, "Service Unavailable"))

    result = _run(runner, *args)

    assert result.exit_code == 1
    assert _GRAPH_DISABLED in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(("_id", "args", "_suffix", "_payload", "_invalid"), _COMMANDS, ids=_COMMAND_IDS)
def test_forbidden_403_exits_3_with_detail_on_stderr(runner, monkeypatch, mock_transport, _id, args, _suffix, _payload, _invalid):
    _patch_client(monkeypatch, mock_transport, _problem(403, "Brak uprawnienia connections:read", "Forbidden"))

    result = _run(runner, *args)

    assert result.exit_code == 3
    assert "Brak uprawnienia connections:read" in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(("_id", "args", "_suffix", "_payload", "_invalid"), _COMMANDS, ids=_COMMAND_IDS)
def test_network_error_exits_4(runner, monkeypatch, mock_transport, _id, args, _suffix, _payload, _invalid):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("polaczenie odrzucone", request=request)

    _patch_client(monkeypatch, mock_transport, handler)

    result = _run(runner, *args)

    assert result.exit_code == 4
    assert result.stdout == ""


@pytest.mark.parametrize(("_id", "args", "_suffix", "_payload", "_invalid"), _COMMANDS, ids=_COMMAND_IDS)
def test_missing_project_exits_2_without_http(runner, monkeypatch, mock_transport, _id, args, _suffix, _payload, _invalid):
    requests = _patch_client(monkeypatch, mock_transport, _json({}))

    result = runner.invoke(app, ["-o", "json", "graph", *args])

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


# ---------------------------------------------------------------------------
# graph nodes
# ---------------------------------------------------------------------------


def test_nodes_forwards_type_search_and_limit_as_query_params(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODES))

    result = _run(runner, "nodes", "--type", "Class", "--search", "alf", "--limit", "5")

    assert result.exit_code == 0, result.output
    params = requests[0].url.params
    assert (params["type"], params["search"], params["limit"]) == ("Class", "alf", "5")
    assert set(params) == {"type", "search", "limit"}


def test_nodes_without_filters_sends_neither_type_nor_search(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODES))

    result = _run(runner, "nodes")

    assert result.exit_code == 0, result.output
    assert "type" not in requests[0].url.params
    assert "search" not in requests[0].url.params


@pytest.mark.parametrize("limit", ["1", "1000"], ids=["min", "max"])
def test_nodes_limit_at_boundary_is_accepted_and_sent(runner, monkeypatch, mock_transport, limit):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODES))

    result = _run(runner, "nodes", "--limit", limit)

    assert result.exit_code == 0, result.output
    assert requests[0].url.params["limit"] == limit


@pytest.mark.parametrize("limit", ["0", "1001", "-1"], ids=["zero", "above-max", "negative"])
def test_nodes_limit_out_of_range_exits_2_without_http(runner, monkeypatch, mock_transport, limit):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODES))

    result = _run(runner, "nodes", "--limit", limit)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_nodes_empty_list_json_is_empty_array_with_exit_0(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json([]))

    result = _run(runner, "nodes")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


def test_nodes_table_shows_name_and_type_of_every_node(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    _patch_client(monkeypatch, mock_transport, _json(_NODES))

    result = _run(runner, "nodes", output="table")

    assert result.exit_code == 0, result.output
    assert "Alfa" in result.stdout
    assert "beta_fn" in result.stdout
    assert "Function" in result.stdout


# ---------------------------------------------------------------------------
# graph node
# ---------------------------------------------------------------------------


def test_node_forwards_depth_and_repeated_relation_and_node_types(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODE_DETAIL))

    result = _run(
        runner,
        "node",
        NODE_ID,
        "--depth",
        "2",
        "--relation-type",
        "CALLS",
        "--relation-type",
        "IMPORTS",
        "--node-type",
        "Class",
        "--node-type",
        "Method",
    )

    assert result.exit_code == 0, result.output
    params = requests[0].url.params
    assert params["depth"] == "2"
    assert params.get_list("relation_types") == ["CALLS", "IMPORTS"]
    assert params.get_list("node_types") == ["Class", "Method"]
    assert set(params) == {"depth", "relation_types", "node_types"}


def test_node_single_relation_type_is_sent_as_one_param(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODE_DETAIL))

    result = _run(runner, "node", NODE_ID, "--relation-type", "INHERITS")

    assert result.exit_code == 0, result.output
    assert requests[0].url.params.get_list("relation_types") == ["INHERITS"]
    assert "node_types" not in requests[0].url.params


def test_node_without_filters_sends_no_type_params(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODE_DETAIL))

    result = _run(runner, "node", NODE_ID)

    assert result.exit_code == 0, result.output
    assert "relation_types" not in requests[0].url.params
    assert "node_types" not in requests[0].url.params


def test_node_without_id_exits_2_without_http(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODE_DETAIL))

    result = _run(runner, "node")

    assert result.exit_code == 2
    assert len(requests) == 0


def test_node_id_with_slash_is_sent_as_one_encoded_path_segment(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODE_DETAIL))

    result = _run(runner, "node", "app/alfa.py::Alfa")

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].url.raw_path == b"/api/v2/projects/proj-x/graph/nodes/app%2Falfa.py%3A%3AAlfa"


@pytest.mark.parametrize("node_id", [".", "..", ""], ids=["dot", "dotdot", "empty"])
def test_node_id_that_normalizes_out_of_path_exits_2_without_http(runner, monkeypatch, mock_transport, node_id):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODE_DETAIL))

    result = _run(runner, "node", node_id)

    assert result.exit_code == 2
    assert "Niedozwolony identyfikator" in result.stderr
    assert len(requests) == 0


def test_node_depth_one_at_boundary_is_accepted_and_sent(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODE_DETAIL))

    result = _run(runner, "node", NODE_ID, "--depth", "1")

    assert result.exit_code == 0, result.output
    assert requests[0].url.params["depth"] == "1"


@pytest.mark.parametrize("depth", ["0", "-1"], ids=["zero", "negative"])
def test_node_depth_below_one_exits_2_without_http(runner, monkeypatch, mock_transport, depth):
    requests = _patch_client(monkeypatch, mock_transport, _json(_NODE_DETAIL))

    result = _run(runner, "node", NODE_ID, "--depth", depth)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_node_not_found_404_exits_1_with_detail(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _problem(404, "Node nie istnieje", "Not Found"))

    result = _run(runner, "node", "brak")

    assert result.exit_code == 1
    assert "Node nie istnieje" in result.stderr
    assert result.stdout == ""


def test_node_unknown_relation_type_422_exits_1_with_detail(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _problem(422, "Nieznane typy relacji: ['ZLY']", "Unprocessable Entity"))

    result = _run(runner, "node", NODE_ID, "--relation-type", "ZLY")

    assert result.exit_code == 1
    assert "Nieznane typy relacji" in result.stderr


# ---------------------------------------------------------------------------
# graph query
# ---------------------------------------------------------------------------


def test_query_forwards_node_type_search_and_limit_as_query_params(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_SUBGRAPH))

    result = _run(runner, "query", "--node-type", "Class", "--search", "alf", "--limit", "7")

    assert result.exit_code == 0, result.output
    params = requests[0].url.params
    assert (params["node_type"], params["search"], params["limit"]) == ("Class", "alf", "7")
    assert set(params) == {"node_type", "search", "limit"}


def test_query_without_options_sends_neither_node_type_nor_search(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_SUBGRAPH))

    result = _run(runner, "query")

    assert result.exit_code == 0, result.output
    assert "node_type" not in requests[0].url.params
    assert "search" not in requests[0].url.params


@pytest.mark.parametrize("limit", ["1", "1000"], ids=["min", "max"])
def test_query_limit_at_boundary_is_accepted_and_sent(runner, monkeypatch, mock_transport, limit):
    requests = _patch_client(monkeypatch, mock_transport, _json(_SUBGRAPH))

    result = _run(runner, "query", "--search", "a", "--limit", limit)

    assert result.exit_code == 0, result.output
    assert requests[0].url.params["limit"] == limit


@pytest.mark.parametrize("limit", ["0", "1001", "-1"], ids=["zero", "above-max", "negative"])
def test_query_limit_out_of_range_exits_2_without_http(runner, monkeypatch, mock_transport, limit):
    requests = _patch_client(monkeypatch, mock_transport, _json(_SUBGRAPH))

    result = _run(runner, "query", "--search", "a", "--limit", limit)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_query_blank_search_422_from_api_exits_1_with_detail(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _problem(422, "search nie moze byc pusty", "Unprocessable Entity"))

    result = _run(runner, "query", "--search", " ")

    assert result.exit_code == 1
    assert "search nie moze byc pusty" in result.stderr


def test_query_empty_subgraph_json_keeps_both_empty_lists(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json({"nodes": [], "edges": []}))

    result = _run(runner, "query")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# graph path
# ---------------------------------------------------------------------------


def test_path_forwards_source_and_target_ids(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_SUBGRAPH))

    result = _run(runner, "path", "--source-id", "a", "--target-id", "b")

    assert result.exit_code == 0, result.output
    params = requests[0].url.params
    assert (params["source_id"], params["target_id"]) == ("a", "b")
    assert set(params) == {"source_id", "target_id"}


@pytest.mark.parametrize(
    "args",
    [["path", "--target-id", "b"], ["path", "--source-id", "a"], ["path"]],
    ids=["no-source", "no-target", "neither"],
)
def test_path_missing_required_id_exits_2_without_http(runner, monkeypatch, mock_transport, args):
    requests = _patch_client(monkeypatch, mock_transport, _json(_SUBGRAPH))

    result = _run(runner, *args)

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert len(requests) == 0


def test_path_no_route_json_is_empty_lists_with_exit_0(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _json({"nodes": [], "edges": []}))

    result = _run(runner, "path", "--source-id", "a", "--target-id", "z")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"nodes": [], "edges": []}


# ---------------------------------------------------------------------------
# graph stats
# ---------------------------------------------------------------------------


def test_stats_sends_no_query_params(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _json(_STATS))

    result = _run(runner, "stats")

    assert result.exit_code == 0, result.output
    assert len(requests[0].url.params) == 0


def test_stats_table_shows_totals(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("COLUMNS", "200")
    _patch_client(monkeypatch, mock_transport, _json({**_STATS, "total_nodes": 4242}))

    result = _run(runner, "stats", output="table")

    assert result.exit_code == 0, result.output
    assert "4242" in result.stdout


# ---------------------------------------------------------------------------
# Grupa podpieta w root app
# ---------------------------------------------------------------------------


def test_graph_group_help_lists_all_five_commands(runner):
    result = runner.invoke(app, ["graph", "--help"])

    assert result.exit_code == 0
    for name in ("nodes", "node", "query", "path", "stats"):
        assert re.search(rf"^\s*│?\s*{name}\s", result.stdout, flags=re.MULTILINE), name
