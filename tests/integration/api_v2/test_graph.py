"""Testy integracyjne API v2 -- graf Połączeń (MON-248).

Pokrywa 5 endpointów odczytu `src/monolynx/api/v2/graph.py`: `GET .../graph/nodes`,
`.../graph/nodes/{node_id}`, `.../graph/query`, `.../graph/path`, `.../graph/stats`.
Router woła funkcje modułu `monolynx.services.graph` (importowanego jako `graph_service`).

Neo4j nie działa w testach, więc fixture `graph` podmienia funkcje serwisu na mocki z podpisem
oryginału (`autospec`): asercje na argumentach nie rozróżniają wywołania pozycyjnego od
nazwanego, a pierwszym argumentem zawsze musi być `project.id` projektu ze sluga (izolacja
danych między projektami). Wartości zwracane i wyjątki ustawia się przez `graph.returns` /
`graph.errors`, włączenie bazy grafowej przez `graph.flags["enabled"]`.

Kolejność sprawdzeń, którą pinują testy: brak tokenu 401, nieistniejący slug 404, brak
członkostwa 403, brak `connections:read` 403 (bez sprawdzania `is_enabled`), wyłączony graf
503, `RuntimeError` albo wyjątek sterownika neo4j z serwisu 503 (nigdy 500).
"""

from __future__ import annotations

import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.services import graph as graph_service

from .conftest import ApiV2Actor, add_member, make_actor, make_owner_project, make_project

pytestmark = pytest.mark.integration

_PROBLEM_JSON = "application/problem+json"
_GRAPH_DISABLED = "Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)"
_SERVICE_FUNCTIONS = ("list_nodes", "get_node", "get_neighbors", "get_graph", "search_graph", "find_path", "get_stats")

# (id, sufiks ścieżki, query, funkcja serwisu, którą endpoint woła jako pierwszą po stronie danych)
_ENDPOINTS: list[tuple[str, str, dict[str, str], str]] = [
    ("nodes", "/graph/nodes", {}, "list_nodes"),
    ("node", "/graph/nodes/n1", {}, "get_node"),
    ("query", "/graph/query", {}, "get_graph"),
    ("path", "/graph/path", {"source_id": "a", "target_id": "b"}, "find_path"),
    ("stats", "/graph/stats", {}, "get_stats"),
]
_ENDPOINT_IDS = [case[0] for case in _ENDPOINTS]

# Oryginał zapisany przy imporcie modułu, zanim fixture `graph` podmieni funkcje serwisu.
_REAL_GET_NEIGHBORS = graph_service.get_neighbors


@pytest.fixture
def graph():
    returns: dict[str, Any] = {}
    errors: dict[str, BaseException] = {}
    flags = {"enabled": True}

    def behaviour(name: str):
        def side_effect(*args: Any, **kwargs: Any) -> Any:
            if name in errors:
                raise errors[name]
            return returns.get(name)

        return side_effect

    with ExitStack() as stack:
        mocks = {
            name: stack.enter_context(patch.object(graph_service, name, autospec=True, side_effect=behaviour(name))) for name in _SERVICE_FUNCTIONS
        }
        is_enabled = stack.enter_context(patch.object(graph_service, "is_enabled", autospec=True, side_effect=lambda: flags["enabled"]))
        yield SimpleNamespace(is_enabled=is_enabled, returns=returns, errors=errors, flags=flags, **mocks)


def _url(project: Project, suffix: str) -> str:
    return f"/api/v2/projects/{project.slug}{suffix}"


def _node(project: Project, node_id: str, name: str, node_type: str = "File", **fields: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "project_id": str(project.id),
        "name": name,
        "type": node_type,
        "file_path": fields.get("file_path"),
        "line_number": fields.get("line_number"),
        "metadata": fields.get("metadata", {}),
    }


def _edge(source_id: str, target_id: str, edge_type: str = "CALLS", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"source_id": source_id, "target_id": target_id, "type": edge_type, "metadata": metadata or {}}


def _assert_problem(response: httpx.Response, status: int, detail: str | None = None) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith(_PROBLEM_JSON)
    if detail is not None:
        assert response.json()["detail"] == detail


def _assert_graph_untouched(graph: SimpleNamespace) -> None:
    for name in _SERVICE_FUNCTIONS:
        getattr(graph, name).assert_not_awaited()


async def _limited_actor(db_session, project: Project, tag: str, permissions: dict[str, list[str]]) -> ApiV2Actor:
    actor = await make_actor(db_session, tag)
    role = Role(name=f"graph-{tag}", project_id=project.id, permissions=permissions, is_system=False)
    db_session.add(role)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=actor.user.id, role="member", role_id=role.id))
    await db_session.flush()
    return actor


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/graph/nodes
# ---------------------------------------------------------------------------


class TestListGraphNodes:
    async def test_returns_bare_list_and_forwards_filters_to_service(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-nodes")
        nodes = [
            _node(project, "n1", "alfa.py", "File", file_path="app/alfa.py", line_number=1),
            _node(project, "n2", "Alfa", "Class", file_path="app/alfa.py", line_number=7, metadata={"docstring": "opis"}),
        ]
        graph.returns["list_nodes"] = nodes

        response = await client.get(_url(project, "/graph/nodes"), headers=actor.headers, params={"type": "Class", "search": "alf", "limit": 5})

        assert response.status_code == 200
        assert response.json() == nodes
        graph.list_nodes.assert_awaited_once_with(project.id, type_filter="Class", search="alf", limit=5)

    async def test_defaults_are_no_filters_and_limit_100(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-nodes-def")
        graph.returns["list_nodes"] = []

        response = await client.get(_url(project, "/graph/nodes"), headers=actor.headers)

        assert response.status_code == 200
        assert response.json() == []
        graph.list_nodes.assert_awaited_once_with(project.id, type_filter=None, search=None, limit=100)

    async def test_service_gets_id_of_project_from_slug(self, client, db_session, graph):
        actor, project_a = await make_owner_project(db_session, "graph-iso-a")
        project_b = await make_project(db_session, "graph-iso-b")
        await add_member(db_session, project_b, actor.user)
        graph.returns["list_nodes"] = []

        response = await client.get(_url(project_b, "/graph/nodes"), headers=actor.headers)

        assert response.status_code == 200
        graph.list_nodes.assert_awaited_once_with(project_b.id, type_filter=None, search=None, limit=100)
        assert project_a.id != project_b.id

    @pytest.mark.parametrize(("tag", "limit"), [("min", 1), ("max", 1000)], ids=["min", "max"])
    async def test_limit_at_boundary_is_accepted_and_forwarded(self, tag, limit, client, db_session, graph):
        actor, project = await make_owner_project(db_session, f"graph-lim-ok-{tag}")
        graph.returns["list_nodes"] = []

        response = await client.get(_url(project, "/graph/nodes"), headers=actor.headers, params={"limit": limit})

        assert response.status_code == 200
        graph.list_nodes.assert_awaited_once_with(project.id, type_filter=None, search=None, limit=limit)

    @pytest.mark.parametrize(("tag", "limit"), [("zero", 0), ("negative", -1), ("above-max", 1001)], ids=["zero", "negative", "above-max"])
    async def test_limit_out_of_range_returns_422_problem(self, tag, limit, client, db_session, graph):
        actor, project = await make_owner_project(db_session, f"graph-lim-bad-{tag}")

        response = await client.get(_url(project, "/graph/nodes"), headers=actor.headers, params={"limit": limit})

        _assert_problem(response, 422)
        _assert_graph_untouched(graph)


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/graph/nodes/{node_id}
# ---------------------------------------------------------------------------


class TestGetGraphNode:
    async def test_returns_node_neighbors_edges_and_depth_map(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-node")
        root = _node(project, "n1", "Alfa", "Class", file_path="app/alfa.py", line_number=7)
        child = _node(project, "n2", "Alfa.run", "Method", file_path="app/alfa.py", line_number=12)
        graph.returns["get_node"] = root
        graph.returns["get_neighbors"] = {
            "nodes": [root, child],
            "edges": [_edge("n1", "n2", "CONTAINS", {"confidence": "EXTRACTED", "source_relation": "contains"})],
            "depth_map": {"n1": 0, "n2": 1},
        }

        response = await client.get(
            _url(project, "/graph/nodes/n1"),
            headers=actor.headers,
            params={"depth": 2, "relation_types": ["CONTAINS", "CALLS"], "node_types": ["Class", "Method"]},
        )

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"node", "nodes", "edges", "depth_map"}
        assert body["node"] == root
        assert body["nodes"] == [root, child]
        assert body["depth_map"] == {"n1": 0, "n2": 1}
        assert body["edges"] == [
            {
                "source_id": "n1",
                "target_id": "n2",
                "type": "CONTAINS",
                "confidence": "EXTRACTED",
                "source_relation": "contains",
                "metadata": {"confidence": "EXTRACTED", "source_relation": "contains"},
            }
        ]
        graph.get_node.assert_awaited_once_with(project.id, "n1")
        graph.get_neighbors.assert_awaited_once_with(project.id, "n1", depth=2, relation_types=["CONTAINS", "CALLS"], node_types=["Class", "Method"])

    async def test_defaults_are_depth_1_and_no_type_filters(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-node-def")
        root = _node(project, "n1", "Alfa", "Class")
        graph.returns["get_node"] = root
        graph.returns["get_neighbors"] = {"nodes": [root], "edges": [_edge("n1", "n1")], "depth_map": {"n1": 0}}

        response = await client.get(_url(project, "/graph/nodes/n1"), headers=actor.headers)

        assert response.status_code == 200
        edge = response.json()["edges"][0]
        assert (edge["confidence"], edge["source_relation"], edge["metadata"]) == (None, None, {})
        graph.get_neighbors.assert_awaited_once_with(project.id, "n1", depth=1, relation_types=None, node_types=None)

    async def test_nonexistent_node_returns_404_without_loading_neighbors(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-node-404")
        graph.returns["get_node"] = None

        response = await client.get(_url(project, "/graph/nodes/brak"), headers=actor.headers)

        _assert_problem(response, 404, "Node nie istnieje")
        graph.get_node.assert_awaited_once_with(project.id, "brak")
        graph.get_neighbors.assert_not_awaited()

    async def test_node_id_with_encoded_slash_reaches_service_as_one_id(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-node-slash")
        root = _node(project, "app/alfa.py::Alfa", "Alfa", "Class", file_path="app/alfa.py", line_number=7)
        graph.returns["get_node"] = root
        graph.returns["get_neighbors"] = {"nodes": [root], "edges": [], "depth_map": {"app/alfa.py::Alfa": 0}}

        response = await client.get(_url(project, "/graph/nodes/app%2Falfa.py::Alfa"), headers=actor.headers)

        assert response.status_code == 200
        assert response.json()["node"] == root
        graph.get_node.assert_awaited_once_with(project.id, "app/alfa.py::Alfa")
        graph.get_neighbors.assert_awaited_once_with(project.id, "app/alfa.py::Alfa", depth=1, relation_types=None, node_types=None)

    async def test_depth_zero_returns_422_and_skips_graph(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-node-depth0")

        response = await client.get(_url(project, "/graph/nodes/n1"), headers=actor.headers, params={"depth": 0})

        _assert_problem(response, 422)
        _assert_graph_untouched(graph)

    async def test_depth_one_at_boundary_is_accepted_and_forwarded(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-node-depth1")
        root = _node(project, "n1", "Alfa")
        graph.returns["get_node"] = root
        graph.returns["get_neighbors"] = {"nodes": [root], "edges": [], "depth_map": {"n1": 0}}

        response = await client.get(_url(project, "/graph/nodes/n1"), headers=actor.headers, params={"depth": 1})

        assert response.status_code == 200
        graph.get_neighbors.assert_awaited_once_with(project.id, "n1", depth=1, relation_types=None, node_types=None)

    async def test_value_error_from_neighbors_returns_422_with_service_message(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-node-422")
        graph.returns["get_node"] = _node(project, "n1", "Alfa")
        graph.errors["get_neighbors"] = ValueError("Nieznane typy relacji: ['ZLY']")

        response = await client.get(_url(project, "/graph/nodes/n1"), headers=actor.headers, params={"relation_types": "ZLY"})

        _assert_problem(response, 422, "Nieznane typy relacji: ['ZLY']")

    @pytest.mark.parametrize(
        ("tag", "params", "expected"),
        [
            ("relation", {"relation_types": "ZLY"}, "Nieznane typy relacji: ['ZLY']"),
            ("node", {"node_types": "ZLY"}, "Nieznane typy node'ów: ['ZLY']"),
        ],
        ids=["relation_types", "node_types"],
    )
    async def test_unknown_types_are_rejected_by_real_service_validation_with_422(
        self, tag, params, expected, client, db_session, graph, monkeypatch
    ):
        actor, project = await make_owner_project(db_session, f"graph-node-real-{tag}")
        graph.returns["get_node"] = _node(project, "n1", "Alfa")
        monkeypatch.setattr(graph_service, "get_neighbors", _REAL_GET_NEIGHBORS)

        response = await client.get(_url(project, "/graph/nodes/n1"), headers=actor.headers, params=params)

        _assert_problem(response, 422)
        assert response.json()["detail"].startswith(expected)


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/graph/query
# ---------------------------------------------------------------------------


class TestQueryGraph:
    async def test_without_search_reads_whole_graph_with_type_filter_and_limit(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-query")
        data = {"nodes": [_node(project, "n2", "Beta", "Function"), _node(project, "n1", "Alfa", "Class")], "edges": [_edge("n1", "n2")]}
        graph.returns["get_graph"] = data

        response = await client.get(_url(project, "/graph/query"), headers=actor.headers, params={"node_type": "Function", "limit": 9})

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"nodes", "edges"}
        assert [n["id"] for n in body["nodes"]] == ["n2", "n1"]
        assert [(e["source_id"], e["target_id"], e["type"]) for e in body["edges"]] == [("n1", "n2", "CALLS")]
        graph.get_graph.assert_awaited_once_with(project.id, type_filter="Function", limit=9)
        graph.search_graph.assert_not_awaited()

    async def test_defaults_are_no_type_filter_and_limit_200(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-query-def")
        graph.returns["get_graph"] = {"nodes": [], "edges": []}

        response = await client.get(_url(project, "/graph/query"), headers=actor.headers)

        assert response.status_code == 200
        assert response.json() == {"nodes": [], "edges": []}
        graph.get_graph.assert_awaited_once_with(project.id, type_filter=None, limit=200)

    @pytest.mark.parametrize(("tag", "limit"), [("min", 1), ("max", 1000)], ids=["min", "max"])
    async def test_limit_at_boundary_is_accepted_and_forwarded(self, tag, limit, client, db_session, graph):
        actor, project = await make_owner_project(db_session, f"graph-query-lim-ok-{tag}")
        graph.returns["get_graph"] = {"nodes": [], "edges": []}

        response = await client.get(_url(project, "/graph/query"), headers=actor.headers, params={"limit": limit})

        assert response.status_code == 200
        graph.get_graph.assert_awaited_once_with(project.id, type_filter=None, limit=limit)

    @pytest.mark.parametrize(("tag", "limit"), [("zero", 0), ("above-max", 1001)], ids=["zero", "above-max"])
    async def test_limit_out_of_range_returns_422_and_skips_graph(self, tag, limit, client, db_session, graph):
        actor, project = await make_owner_project(db_session, f"graph-query-lim-bad-{tag}")

        response = await client.get(_url(project, "/graph/query"), headers=actor.headers, params={"limit": limit})

        _assert_problem(response, 422)
        _assert_graph_untouched(graph)

    async def test_with_search_uses_search_graph_with_type_filter_and_limit(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-query-search")
        alfa = _node(project, "n1", "Alfa", "Class")
        graph.returns["search_graph"] = {"nodes": [alfa], "edges": []}

        response = await client.get(_url(project, "/graph/query"), headers=actor.headers, params={"search": "alf", "node_type": "Class", "limit": 7})

        assert response.status_code == 200
        assert response.json() == {"nodes": [alfa], "edges": []}
        graph.search_graph.assert_awaited_once_with(project.id, search="alf", type_filter="Class", limit=7)
        graph.get_graph.assert_not_awaited()

    @pytest.mark.parametrize(("tag", "search"), [("empty", ""), ("whitespace", "   ")], ids=["empty", "whitespace"])
    async def test_blank_search_returns_422_with_exact_detail_and_skips_graph(self, tag, search, client, db_session, graph):
        actor, project = await make_owner_project(db_session, f"graph-query-blank-{tag}")

        response = await client.get(_url(project, "/graph/query"), headers=actor.headers, params={"search": search})

        _assert_problem(response, 422, "search nie moze byc pusty")
        _assert_graph_untouched(graph)


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/graph/path
# ---------------------------------------------------------------------------


class TestFindGraphPath:
    async def test_returns_nodes_and_edges_of_shortest_path(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-path")
        start = _node(project, "a", "Start", "Function")
        end = _node(project, "b", "Koniec", "Function")
        graph.returns["find_path"] = {"nodes": [start, end], "edges": [_edge("a", "b", "CALLS")]}

        response = await client.get(_url(project, "/graph/path"), headers=actor.headers, params={"source_id": "a", "target_id": "b"})

        assert response.status_code == 200
        body = response.json()
        assert body["nodes"] == [start, end]
        assert [(e["source_id"], e["target_id"], e["type"]) for e in body["edges"]] == [("a", "b", "CALLS")]
        graph.find_path.assert_awaited_once_with(project.id, "a", "b")

    async def test_no_path_returns_200_with_empty_lists(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-path-none")
        graph.returns["find_path"] = {"nodes": [], "edges": []}

        response = await client.get(_url(project, "/graph/path"), headers=actor.headers, params={"source_id": "a", "target_id": "z"})

        assert response.status_code == 200
        assert response.json() == {"nodes": [], "edges": []}

    @pytest.mark.parametrize(
        ("tag", "params"),
        [("no-source", {"target_id": "b"}), ("no-target", {"source_id": "a"}), ("neither", {})],
        ids=["no-source", "no-target", "neither"],
    )
    async def test_missing_required_id_returns_422_without_calling_service(self, tag, params, client, db_session, graph):
        actor, project = await make_owner_project(db_session, f"graph-path-422-{tag}")

        response = await client.get(_url(project, "/graph/path"), headers=actor.headers, params=params)

        _assert_problem(response, 422)
        _assert_graph_untouched(graph)


# ---------------------------------------------------------------------------
# GET /api/v2/projects/{slug}/graph/stats
# ---------------------------------------------------------------------------


class TestGetGraphStats:
    async def test_returns_totals_and_per_type_counts(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-stats")
        stats = {
            "total_nodes": 3,
            "total_edges": 2,
            "nodes_by_type": {"File": 1, "Class": 1, "Function": 1, "Method": 0, "Const": 0, "Module": 0},
            "edges_by_type": {"CONTAINS": 1, "CALLS": 1},
        }
        graph.returns["get_stats"] = stats

        response = await client.get(_url(project, "/graph/stats"), headers=actor.headers)

        assert response.status_code == 200
        assert response.json() == stats
        graph.get_stats.assert_awaited_once_with(project.id)

    async def test_service_gets_id_of_project_from_slug(self, client, db_session, graph):
        actor, _project_a = await make_owner_project(db_session, "graph-stats-iso-a")
        project_b = await make_project(db_session, "graph-stats-iso-b")
        await add_member(db_session, project_b, actor.user)
        graph.returns["get_stats"] = {"total_nodes": 0, "total_edges": 0, "nodes_by_type": {}, "edges_by_type": {}}

        response = await client.get(_url(project_b, "/graph/stats"), headers=actor.headers)

        assert response.status_code == 200
        graph.get_stats.assert_awaited_once_with(project_b.id)


# ---------------------------------------------------------------------------
# Autoryzacja i błędy wspólne dla wszystkich endpointów
# ---------------------------------------------------------------------------


class TestGraphAccessAndErrors:
    @pytest.mark.parametrize(("_id", "suffix", "params", "_fn"), _ENDPOINTS, ids=_ENDPOINT_IDS)
    async def test_missing_token_returns_401_problem_with_www_authenticate(self, _id, suffix, params, _fn, client, db_session, graph):
        _actor, project = await make_owner_project(db_session, f"graph-401-{_id}")

        response = await client.get(_url(project, suffix), params=params)

        _assert_problem(response, 401)
        assert response.headers["www-authenticate"] == "Bearer"
        graph.is_enabled.assert_not_called()
        _assert_graph_untouched(graph)

    @pytest.mark.parametrize(("_id", "suffix", "params", "_fn"), _ENDPOINTS, ids=_ENDPOINT_IDS)
    async def test_unknown_project_slug_returns_404_problem(self, _id, suffix, params, _fn, client, db_session, graph):
        actor, _project = await make_owner_project(db_session, f"graph-404-{_id}")
        slug = f"nie-ma-{uuid.uuid4().hex[:8]}"

        response = await client.get(f"/api/v2/projects/{slug}{suffix}", headers=actor.headers, params=params)

        _assert_problem(response, 404, f"Projekt '{slug}' nie istnieje")
        graph.is_enabled.assert_not_called()
        _assert_graph_untouched(graph)

    @pytest.mark.parametrize(("_id", "suffix", "params", "_fn"), _ENDPOINTS, ids=_ENDPOINT_IDS)
    async def test_user_outside_project_returns_403_problem(self, _id, suffix, params, _fn, client, db_session, graph):
        _actor, project = await make_owner_project(db_session, f"graph-out-{_id}")
        outsider = await make_actor(db_session, f"graph-out-{_id}-user")

        response = await client.get(_url(project, suffix), headers=outsider.headers, params=params)

        _assert_problem(response, 403, f"Uzytkownik nie jest czlonkiem projektu '{project.slug}'")
        graph.is_enabled.assert_not_called()
        _assert_graph_untouched(graph)

    @pytest.mark.parametrize(("_id", "suffix", "params", "_fn"), _ENDPOINTS, ids=_ENDPOINT_IDS)
    async def test_role_without_connections_read_returns_403_before_graph_is_checked(self, _id, suffix, params, _fn, client, db_session, graph):
        _actor, project = await make_owner_project(db_session, f"graph-perm-{_id}")
        limited = await _limited_actor(db_session, project, f"graph-perm-{_id}", {"scrum": ["read"], "wiki": ["read"]})
        graph.flags["enabled"] = False

        response = await client.get(_url(project, suffix), headers=limited.headers, params=params)

        _assert_problem(response, 403, "Brak uprawnienia connections:read")
        graph.is_enabled.assert_not_called()
        _assert_graph_untouched(graph)

    @pytest.mark.parametrize(("_id", "suffix", "params", "fn"), _ENDPOINTS, ids=_ENDPOINT_IDS)
    async def test_default_member_role_has_connections_read(self, _id, suffix, params, fn, client, db_session, graph):
        _actor, project = await make_owner_project(db_session, f"graph-member-{_id}")
        member = await make_actor(db_session, f"graph-member-{_id}-user")
        await add_member(db_session, project, member.user, role="member")
        graph.returns.update(
            {
                "list_nodes": [],
                "get_node": _node(project, "n1", "Alfa"),
                "get_neighbors": {"nodes": [], "edges": [], "depth_map": {}},
                "get_graph": {"nodes": [], "edges": []},
                "find_path": {"nodes": [], "edges": []},
                "get_stats": {"total_nodes": 0, "total_edges": 0, "nodes_by_type": {}, "edges_by_type": {}},
            }
        )

        response = await client.get(_url(project, suffix), headers=member.headers, params=params)

        assert response.status_code == 200
        getattr(graph, fn).assert_awaited_once()

    @pytest.mark.parametrize(("_id", "suffix", "params", "_fn"), _ENDPOINTS, ids=_ENDPOINT_IDS)
    async def test_disabled_graph_returns_503_problem_without_calling_service(self, _id, suffix, params, _fn, client, db_session, graph):
        actor, project = await make_owner_project(db_session, f"graph-off-{_id}")
        graph.flags["enabled"] = False

        response = await client.get(_url(project, suffix), headers=actor.headers, params=params)

        _assert_problem(response, 503, _GRAPH_DISABLED)
        graph.is_enabled.assert_called()
        _assert_graph_untouched(graph)

    @pytest.mark.parametrize(("_id", "suffix", "params", "fn"), _ENDPOINTS, ids=_ENDPOINT_IDS)
    async def test_runtime_error_from_service_returns_503_problem_not_500(self, _id, suffix, params, fn, client, db_session, graph):
        actor, project = await make_owner_project(db_session, f"graph-rt-{_id}")
        graph.errors[fn] = RuntimeError("Neo4j driver niedostepny")

        response = await client.get(_url(project, suffix), headers=actor.headers, params=params)

        _assert_problem(response, 503)
        getattr(graph, fn).assert_awaited_once()

    @pytest.mark.parametrize(("_id", "suffix", "params", "fn"), _ENDPOINTS, ids=_ENDPOINT_IDS)
    async def test_neo4j_driver_exception_from_service_returns_503_problem_not_500(self, _id, suffix, params, fn, client, db_session, graph):
        from neo4j.exceptions import ServiceUnavailable

        actor, project = await make_owner_project(db_session, f"graph-drv-{_id}")
        graph.errors[fn] = ServiceUnavailable("Unable to retrieve routing information")

        response = await client.get(_url(project, suffix), headers=actor.headers, params=params)

        _assert_problem(response, 503)
        getattr(graph, fn).assert_awaited_once()

    @pytest.mark.parametrize(("_id", "suffix", "params", "fn"), _ENDPOINTS, ids=_ENDPOINT_IDS)
    async def test_neo4j_error_subclass_from_service_returns_503_problem_not_500(self, _id, suffix, params, fn, client, db_session, graph):
        from neo4j.exceptions import DriverError, Neo4jError, TransientError

        actor, project = await make_owner_project(db_session, f"graph-neo4j-err-{_id}")
        error = TransientError("Deadlock detected")
        # Neo4jError nie jest ani RuntimeError, ani DriverError: tylko ta klauzula `except` go łapie.
        assert isinstance(error, Neo4jError)
        assert not isinstance(error, (RuntimeError, DriverError))
        graph.errors[fn] = error

        response = await client.get(_url(project, suffix), headers=actor.headers, params=params)

        _assert_problem(response, 503, "Baza grafowa jest niedostepna")
        getattr(graph, fn).assert_awaited_once()

    async def test_runtime_error_in_search_branch_of_query_returns_503(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-rt-search")
        graph.errors["search_graph"] = RuntimeError("Neo4j driver niedostepny")

        response = await client.get(_url(project, "/graph/query"), headers=actor.headers, params={"search": "alf"})

        _assert_problem(response, 503)
        graph.get_graph.assert_not_awaited()

    async def test_runtime_error_in_neighbors_returns_503(self, client, db_session, graph):
        actor, project = await make_owner_project(db_session, "graph-rt-neighbors")
        graph.returns["get_node"] = _node(project, "n1", "Alfa")
        graph.errors["get_neighbors"] = RuntimeError("Neo4j driver niedostepny")

        response = await client.get(_url(project, "/graph/nodes/n1"), headers=actor.headers)

        _assert_problem(response, 503)


class TestGraphOpenApi:
    async def test_five_graph_read_routes_are_listed_in_v2_openapi(self, client):
        response = await client.get("/api/v2/openapi.json")

        assert response.status_code == 200
        paths = response.json()["paths"]
        expected = {
            "/api/v2/projects/{project_slug}/graph/nodes": "get",
            "/api/v2/projects/{project_slug}/graph/nodes/{node_id}": "get",
            "/api/v2/projects/{project_slug}/graph/query": "get",
            "/api/v2/projects/{project_slug}/graph/path": "get",
            "/api/v2/projects/{project_slug}/graph/stats": "get",
        }
        for path, method in expected.items():
            assert method in paths[path], path

    async def test_graph_routes_expose_no_write_methods(self, client):
        paths = (await client.get("/api/v2/openapi.json")).json()["paths"]

        graph_paths = {path: set(methods) for path, methods in paths.items() if "/graph/" in path}
        assert len(graph_paths) == 5
        for path, methods in graph_paths.items():
            assert methods == {"get"}, path
