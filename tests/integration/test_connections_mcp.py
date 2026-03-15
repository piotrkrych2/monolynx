"""Testy integracyjne -- MCP tools dla grafu polaczen."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.mcp_server import (
    bulk_create_graph_edges,
    bulk_create_graph_nodes,
    create_graph_edge,
    create_graph_node,
    delete_graph_edge,
    delete_graph_node,
    find_graph_path,
    get_graph_node,
    get_graph_stats,
    list_graph_nodes,
    query_graph,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(token: str = "test-token") -> MagicMock:
    """Mock MCP Context z Bearer token w naglowku."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


def _mock_auth():
    """Zwraca mock _get_user_and_project z mock user i project."""
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_user.email = f"mcp-{uuid.uuid4().hex[:8]}@test.com"
    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project.slug = "test-project"
    return AsyncMock(return_value=(mock_user, mock_project)), mock_user, mock_project


def _sample_node(node_id=None, name="TestNode", node_type="File"):
    """Zwraca przykladowy dict node'a."""
    return {
        "id": node_id or uuid.uuid4().hex,
        "project_id": str(uuid.uuid4()),
        "name": name,
        "type": node_type,
        "file_path": "/src/test.py",
        "line_number": 42,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# 1. create_graph_node
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateGraphNode:
    async def test_create_graph_node(self):
        mock_auth_fn, _user, _project = _mock_auth()
        node = _sample_node(name="main.py", node_type="File")

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_node = AsyncMock(return_value=node)

            result = await create_graph_node(_make_ctx(), "test-project", type="File", name="main.py")

        assert result["name"] == "main.py"
        assert result["type"] == "File"
        assert "message" in result
        mock_gs.create_node.assert_called_once()

    async def test_create_graph_node_invalid_type(self):
        mock_auth_fn, _user, _project = _mock_auth()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True

            with pytest.raises(ValueError, match="Nieznany typ node'a"):
                await create_graph_node(_make_ctx(), "test-project", type="BadType", name="test")

    async def test_create_graph_node_disabled(self):
        mock_auth_fn, _user, _project = _mock_auth()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = False

            with pytest.raises(ValueError, match="Baza grafowa nie jest wlaczona"):
                await create_graph_node(_make_ctx(), "test-project", type="File", name="test")


# ---------------------------------------------------------------------------
# 2. list_graph_nodes
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListGraphNodes:
    async def test_list_graph_nodes(self):
        mock_auth_fn, _user, _project = _mock_auth()
        nodes = [_sample_node(name="a.py"), _sample_node(name="b.py")]

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.list_nodes = AsyncMock(return_value=nodes)

            result = await list_graph_nodes(_make_ctx(), "test-project")

        assert len(result) == 2
        assert result[0]["name"] == "a.py"


# ---------------------------------------------------------------------------
# 3. get_graph_node
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetGraphNode:
    async def test_get_graph_node(self):
        mock_auth_fn, _user, _project = _mock_auth()
        node = _sample_node(node_id="abc123", name="details.py")
        neighbors = {"nodes": [_sample_node()], "edges": []}

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_node = AsyncMock(return_value=node)
            mock_gs.get_neighbors = AsyncMock(return_value=neighbors)

            result = await get_graph_node(_make_ctx(), "test-project", node_id="abc123")

        assert isinstance(result, str)
        assert "[File] TestNode" in result

    async def test_get_graph_node_not_found(self):
        mock_auth_fn, _user, _project = _mock_auth()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_node = AsyncMock(return_value=None)

            with pytest.raises(ValueError, match="Node nie istnieje"):
                await get_graph_node(_make_ctx(), "test-project", node_id="missing")


# ---------------------------------------------------------------------------
# 4. delete_graph_node
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteGraphNode:
    async def test_delete_graph_node(self):
        mock_auth_fn, _user, _project = _mock_auth()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.delete_node = AsyncMock(return_value=True)

            result = await delete_graph_node(_make_ctx(), "test-project", node_id="abc123")

        assert result["message"] == "Node usuniety"
        assert result["node_id"] == "abc123"

    async def test_delete_graph_node_not_found(self):
        mock_auth_fn, _user, _project = _mock_auth()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.delete_node = AsyncMock(return_value=False)

            with pytest.raises(ValueError, match="Node nie istnieje"):
                await delete_graph_node(_make_ctx(), "test-project", node_id="missing")


# ---------------------------------------------------------------------------
# 5. create_graph_edge
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateGraphEdge:
    async def test_create_graph_edge(self):
        mock_auth_fn, _user, _project = _mock_auth()
        edge = {
            "source_id": "src-1",
            "target_id": "tgt-1",
            "type": "CALLS",
            "metadata": {},
        }

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_edge = AsyncMock(return_value=edge)

            result = await create_graph_edge(
                _make_ctx(),
                "test-project",
                source_id="src-1",
                target_id="tgt-1",
                type="CALLS",
            )

        assert result["source_id"] == "src-1"
        assert result["type"] == "CALLS"
        assert "message" in result

    async def test_create_graph_edge_invalid_type(self):
        mock_auth_fn, _user, _project = _mock_auth()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True

            with pytest.raises(ValueError, match="Nieznany typ krawedzi"):
                await create_graph_edge(
                    _make_ctx(),
                    "test-project",
                    source_id="src",
                    target_id="tgt",
                    type="INVALID",
                )


# ---------------------------------------------------------------------------
# 6. delete_graph_edge
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteGraphEdge:
    async def test_delete_graph_edge(self):
        mock_auth_fn, _user, _project = _mock_auth()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.delete_edge = AsyncMock(return_value=True)

            result = await delete_graph_edge(
                _make_ctx(),
                "test-project",
                source_id="src-1",
                target_id="tgt-1",
                type="CALLS",
            )

        assert result["message"] == "Krawedz usunieta"
        assert result["source_id"] == "src-1"


# ---------------------------------------------------------------------------
# 7. query_graph
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestQueryGraph:
    async def test_query_graph(self):
        mock_auth_fn, _user, _project = _mock_auth()
        graph_data = {
            "nodes": [_sample_node(name="A"), _sample_node(name="B")],
            "edges": [{"source_id": "a", "target_id": "b", "type": "CALLS", "metadata": {}}],
        }

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_graph = AsyncMock(return_value=graph_data)

            result = await query_graph(_make_ctx(), "test-project")

        assert isinstance(result, str)
        assert "2 nodes, 1 edges" in result
        assert "[File] A" in result
        assert "[File] B" in result


# ---------------------------------------------------------------------------
# 8. find_graph_path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFindGraphPath:
    async def test_find_graph_path(self):
        mock_auth_fn, _user, _project = _mock_auth()
        path_data = {
            "nodes": [_sample_node(name="Start"), _sample_node(name="End")],
            "edges": [{"source_id": "s1", "target_id": "e1", "type": "CALLS", "metadata": {}}],
        }

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.find_path = AsyncMock(return_value=path_data)

            result = await find_graph_path(
                _make_ctx(),
                "test-project",
                source_id="s1",
                target_id="e1",
            )

        assert isinstance(result, str)
        assert "2 nodes, 1 edges" in result
        assert "[File] Start" in result
        assert "[File] End" in result


# ---------------------------------------------------------------------------
# 9. get_graph_stats
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetGraphStats:
    async def test_get_graph_stats(self):
        mock_auth_fn, _user, _project = _mock_auth()
        stats = {
            "total_nodes": 10,
            "total_edges": 5,
            "nodes_by_type": {"File": 4, "Class": 6},
            "edges_by_type": {"CALLS": 3, "IMPORTS": 2},
        }

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_stats = AsyncMock(return_value=stats)

            result = await get_graph_stats(_make_ctx(), "test-project")

        assert result["total_nodes"] == 10
        assert result["total_edges"] == 5
        assert result["nodes_by_type"]["File"] == 4


# ---------------------------------------------------------------------------
# 10. bulk_create_graph_nodes
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBulkCreateGraphNodes:
    async def test_bulk_create_graph_nodes(self):
        mock_auth_fn, _user, _project = _mock_auth()
        nodes_input = [
            {"type": "File", "name": "a.py"},
            {"type": "Class", "name": "MyClass"},
            {"type": "Method", "name": "do_stuff"},
        ]
        created_nodes = [
            _sample_node(name="a.py", node_type="File"),
            _sample_node(name="MyClass", node_type="Class"),
            _sample_node(name="do_stuff", node_type="Method"),
        ]

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_node = AsyncMock(side_effect=created_nodes)

            result = await bulk_create_graph_nodes(_make_ctx(), "test-project", nodes=nodes_input)

        assert result["created"] == 3
        assert len(result["errors"]) == 0
        assert len(result["nodes"]) == 3

    async def test_bulk_create_graph_nodes_with_errors(self):
        mock_auth_fn, _user, _project = _mock_auth()
        nodes_input = [
            {"type": "File", "name": "ok.py"},
            {"type": "BadType", "name": "bad"},  # bledny typ
            {"name": "no-type"},  # brak pola type
        ]
        good_node = _sample_node(name="ok.py", node_type="File")

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_node = AsyncMock(return_value=good_node)

            result = await bulk_create_graph_nodes(_make_ctx(), "test-project", nodes=nodes_input)

        assert result["created"] == 1
        assert len(result["errors"]) == 2
        assert "[1]" in result["errors"][0]
        assert "[2]" in result["errors"][1]


# ---------------------------------------------------------------------------
# 11. bulk_create_graph_edges
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBulkCreateGraphEdges:
    async def test_bulk_create_graph_edges(self):
        mock_auth_fn, _user, _project = _mock_auth()
        edges_input = [
            {"source_id": "s1", "target_id": "t1", "type": "CALLS"},
            {"source_id": "s2", "target_id": "t2", "type": "IMPORTS"},
        ]
        edge_results = [
            {"source_id": "s1", "target_id": "t1", "type": "CALLS", "metadata": {}},
            {"source_id": "s2", "target_id": "t2", "type": "IMPORTS", "metadata": {}},
        ]

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.create_edge = AsyncMock(side_effect=edge_results)

            result = await bulk_create_graph_edges(_make_ctx(), "test-project", edges=edges_input)

        assert result["created"] == 2
        assert result["skipped"] == 0
        assert len(result["errors"]) == 0

    async def test_bulk_create_graph_edges_with_errors(self):
        mock_auth_fn, _user, _project = _mock_auth()
        edges_input = [
            {"source_id": "s1", "target_id": "t1", "type": "CALLS"},
            {"source_id": "s2", "target_id": "t2", "type": "BAD_TYPE"},  # bledny typ
            {"source_id": "s3"},  # brak wymaganych pol
            {"source_id": "s4", "target_id": "t4", "type": "IMPORTS"},  # nodes not found
        ]
        edge_ok = {"source_id": "s1", "target_id": "t1", "type": "CALLS", "metadata": {}}

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            # Pierwszy call zwraca edge, czwarty zwraca None (nodes not found)
            mock_gs.create_edge = AsyncMock(side_effect=[edge_ok, None])

            result = await bulk_create_graph_edges(_make_ctx(), "test-project", edges=edges_input)

        assert result["created"] == 1
        assert result["skipped"] == 1  # s4/t4 not found
        assert len(result["errors"]) == 3  # [1] bad type, [2] missing fields, [3] not found
