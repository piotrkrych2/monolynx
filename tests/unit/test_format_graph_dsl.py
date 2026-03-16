"""Testy jednostkowe -- _format_graph_dsl() i nowe parametry get_graph_node."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.mcp_server import _format_graph_dsl, get_graph_node

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


def _sample_node(
    node_id=None,
    name="TestNode",
    node_type="File",
    file_path=None,
    line_number=None,
    metadata=None,
):
    """Zwraca przykladowy dict node'a."""
    return {
        "id": node_id or uuid.uuid4().hex,
        "project_id": str(uuid.uuid4()),
        "name": name,
        "type": node_type,
        "file_path": file_path,
        "line_number": line_number,
        "metadata": metadata or {},
    }


def _sample_edge(source_id, target_id, edge_type="CALLS"):
    """Zwraca przykladowy dict edge'a."""
    return {
        "source_id": source_id,
        "target_id": target_id,
        "type": edge_type,
        "metadata": {},
    }


class _AsyncIterEmpty:
    """Async iterator ktory natychmiast konczy -- symuluje pusty wynik Neo4j."""

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


def _make_mock_graph_driver(session: AsyncMock):
    """Skonfiguruj mock Neo4j driver zwracajacy podana sesje."""
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver


# ---------------------------------------------------------------------------
# A) Testy unit dla _format_graph_dsl() -- podstawy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatGraphDslBasic:
    def test_empty_data_returns_header(self) -> None:
        """Pusty dict: wynik zawiera '0 nodes, 0 edges'."""
        result = _format_graph_dsl({})
        assert "0 nodes, 0 edges" in result

    def test_header_shows_correct_counts(self) -> None:
        """Naglowek pokazuje poprawne ilosci nodes i edges."""
        node_id = "n1"
        data = {
            "nodes": [_sample_node(node_id=node_id, name="MyFile")],
            "edges": [_sample_edge(node_id, node_id, "CALLS")],
        }
        result = _format_graph_dsl(data)
        assert "1 nodes, 1 edges" in result

    def test_node_format_type_and_name(self) -> None:
        """Node bez metadanych: format '[Type] name'."""
        data = {
            "nodes": [_sample_node(node_id="n1", name="main.py", node_type="File")],
            "edges": [],
        }
        result = _format_graph_dsl(data)
        assert "[File] main.py" in result

    def test_node_with_file_path_and_line_number(self) -> None:
        """Node z file_path i line_number: meta w nawiasach."""
        data = {
            "nodes": [
                _sample_node(
                    node_id="n1",
                    name="Contact",
                    node_type="Class",
                    file_path="contacts/models.py",
                    line_number=13,
                )
            ],
            "edges": [],
        }
        result = _format_graph_dsl(data)
        assert "[Class] Contact (path=contacts/models.py,line=13)" in result

    def test_node_with_metadata_keys(self) -> None:
        """Node z metadata: klucze metadanych sa w nawiasach."""
        data = {
            "nodes": [
                _sample_node(
                    node_id="n1",
                    name="MyConst",
                    node_type="Const",
                    metadata={"visibility": "public"},
                )
            ],
            "edges": [],
        }
        result = _format_graph_dsl(data)
        assert "visibility=public" in result


# ---------------------------------------------------------------------------
# A1) Format bez depth_map -- backwards compatibility
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatGraphDslWithoutDepthMap:
    """Backwards-compatible format (bez depth_map): nodes po typie, edges plaska lista."""

    def test_nodes_shown_without_depth_headers(self) -> None:
        """Bez depth_map -- brak naglowkow '--- Depth N ---'."""
        data = {
            "nodes": [
                _sample_node(node_id="f1", name="file.py", node_type="File"),
                _sample_node(node_id="c1", name="MyClass", node_type="Class"),
            ],
            "edges": [],
        }
        result = _format_graph_dsl(data)
        assert "[File] file.py" in result
        assert "[Class] MyClass" in result
        assert "--- Depth" not in result

    def test_edges_flat_list_format(self) -> None:
        """Bez depth_map -- edges jako plaska lista 'src --TYPE--> tgt'."""
        data = {
            "nodes": [
                _sample_node(node_id="s1", name="SourceNode"),
                _sample_node(node_id="t1", name="TargetNode"),
            ],
            "edges": [_sample_edge("s1", "t1", "CALLS")],
        }
        result = _format_graph_dsl(data)
        assert "SourceNode --CALLS--> TargetNode" in result
        assert "=== CALLS ===" not in result

    def test_multiple_edge_types_flat(self) -> None:
        """Wiele typow edges bez depth_map -- w jednym bloku bez grupowania."""
        data = {
            "nodes": [
                _sample_node(node_id="a", name="A"),
                _sample_node(node_id="b", name="B"),
                _sample_node(node_id="c", name="C"),
            ],
            "edges": [
                _sample_edge("a", "b", "CALLS"),
                _sample_edge("b", "c", "IMPORTS"),
            ],
        }
        result = _format_graph_dsl(data)
        assert "A --CALLS--> B" in result
        assert "B --IMPORTS--> C" in result
        assert "=== CALLS ===" not in result
        assert "=== IMPORTS ===" not in result

    def test_unknown_source_id_falls_back_to_raw_id(self) -> None:
        """Edge z nieznanym source_id: fallback do raw ID zamiast nazwy."""
        data = {
            "nodes": [_sample_node(node_id="known", name="KnownNode")],
            "edges": [_sample_edge("unknown-id", "known", "CALLS")],
        }
        result = _format_graph_dsl(data)
        assert "unknown-id --CALLS--> KnownNode" in result

    def test_none_depth_map_is_backwards_compatible(self) -> None:
        """depth_map=None: traktowany jak brak depth_map -- format backwards-compatible."""
        data = {
            "nodes": [_sample_node(node_id="n1", name="MyNode")],
            "edges": [],
            "depth_map": None,
        }
        result = _format_graph_dsl(data)
        assert "[File] MyNode" in result
        assert "--- Depth" not in result

    def test_empty_depth_map_is_falsy(self) -> None:
        """Pusty depth_map ({}) jest falsy -- uzywa backwards-compatible formatu."""
        data = {
            "nodes": [_sample_node(node_id="n1", name="Node1")],
            "edges": [],
            "depth_map": {},
        }
        result = _format_graph_dsl(data)
        assert "[File] Node1" in result
        assert "--- Depth" not in result


# ---------------------------------------------------------------------------
# A2) Format z depth_map -- nowy format z depth rings i grupowaniem edges
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatGraphDslWithDepthMap:
    """Nowy format z depth_map: nodes per depth ring, edges per typ relacji."""

    def test_nodes_show_depth_headers(self) -> None:
        """Z depth_map -- sekcje '--- Depth N ---' dla kazdego poziomu."""
        data = {
            "nodes": [
                _sample_node(node_id="n0", name="StartNode", node_type="File"),
                _sample_node(node_id="n1", name="Neighbor", node_type="Class"),
            ],
            "edges": [],
            "depth_map": {"n0": 0, "n1": 1},
        }
        result = _format_graph_dsl(data)
        assert "--- Depth 0 ---" in result
        assert "--- Depth 1 ---" in result
        assert "[File] StartNode" in result
        assert "[Class] Neighbor" in result

    def test_depth_sections_sorted_ascending(self) -> None:
        """Depth levels sortowane rosnaco (0, 1, 3 a nie losowo)."""
        data = {
            "nodes": [
                _sample_node(node_id="n3", name="Deep"),
                _sample_node(node_id="n1", name="Mid"),
                _sample_node(node_id="n0", name="Root"),
            ],
            "edges": [],
            "depth_map": {"n3": 3, "n1": 1, "n0": 0},
        }
        result = _format_graph_dsl(data)
        pos_d0 = result.index("--- Depth 0 ---")
        pos_d1 = result.index("--- Depth 1 ---")
        pos_d3 = result.index("--- Depth 3 ---")
        assert pos_d0 < pos_d1 < pos_d3

    def test_edges_grouped_by_type_with_section_header(self) -> None:
        """Z depth_map -- edges w sekcjach '=== TYPE ==='."""
        data = {
            "nodes": [
                _sample_node(node_id="a", name="A"),
                _sample_node(node_id="b", name="B"),
            ],
            "edges": [_sample_edge("a", "b", "INHERITS")],
            "depth_map": {"a": 0, "b": 1},
        }
        result = _format_graph_dsl(data)
        assert "=== INHERITS ===" in result
        assert "A --INHERITS--> B" in result

    def test_multiple_edge_types_each_in_own_section(self) -> None:
        """Wiele typow edges -- kazdy typ w osobnej sekcji."""
        data = {
            "nodes": [
                _sample_node(node_id="a", name="A"),
                _sample_node(node_id="b", name="B"),
                _sample_node(node_id="c", name="C"),
            ],
            "edges": [
                _sample_edge("a", "b", "CALLS"),
                _sample_edge("a", "c", "IMPORTS"),
                _sample_edge("b", "c", "CALLS"),
            ],
            "depth_map": {"a": 0, "b": 1, "c": 1},
        }
        result = _format_graph_dsl(data)
        assert "=== CALLS ===" in result
        assert "=== IMPORTS ===" in result
        assert "A --CALLS--> B" in result
        assert "B --CALLS--> C" in result
        assert "A --IMPORTS--> C" in result

    def test_edge_type_sections_sorted_alphabetically(self) -> None:
        """Sekcje edge types sortowane alfabetycznie."""
        data = {
            "nodes": [
                _sample_node(node_id="a", name="A"),
                _sample_node(node_id="b", name="B"),
                _sample_node(node_id="c", name="C"),
            ],
            "edges": [
                _sample_edge("a", "b", "USES"),
                _sample_edge("a", "c", "CALLS"),
            ],
            "depth_map": {"a": 0, "b": 1, "c": 1},
        }
        result = _format_graph_dsl(data)
        pos_calls = result.index("=== CALLS ===")
        pos_uses = result.index("=== USES ===")
        assert pos_calls < pos_uses

    def test_node_missing_from_depth_map_defaults_to_depth_zero(self) -> None:
        """Node nieobecny w depth_map: domyslnie depth=0 (fallback w depth_map.get)."""
        data = {
            "nodes": [
                _sample_node(node_id="known", name="KnownNode"),
                _sample_node(node_id="orphan", name="OrphanNode"),
            ],
            "edges": [],
            "depth_map": {"known": 1},
        }
        result = _format_graph_dsl(data)
        assert "--- Depth 0 ---" in result
        assert "OrphanNode" in result

    def test_no_edges_section_when_empty(self) -> None:
        """Z depth_map ale bez edges -- brak sekcji edge."""
        data = {
            "nodes": [_sample_node(node_id="n1", name="Solo")],
            "edges": [],
            "depth_map": {"n1": 0},
        }
        result = _format_graph_dsl(data)
        assert "Solo" in result
        assert "===" not in result
        assert "--->" not in result

    def test_single_node_in_depth_zero(self) -> None:
        """Pojedynczy node z depth_map=0 -- pojawia sie w sekcji Depth 0."""
        data = {
            "nodes": [_sample_node(node_id="root", name="Root", node_type="Module")],
            "edges": [],
            "depth_map": {"root": 0},
        }
        result = _format_graph_dsl(data)
        assert "--- Depth 0 ---" in result
        assert "[Module] Root" in result


# ---------------------------------------------------------------------------
# B) Testy unit dla walidacji parametrow get_neighbors()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNeighborsValidation:
    """Testy walidacji relation_types i node_types w services/graph.get_neighbors()."""

    async def test_invalid_relation_type_raises_value_error(self) -> None:
        """Nieprawidlowy relation_types -- ValueError z komunikatem."""
        from monolynx.services.graph import get_neighbors

        with patch("monolynx.services.graph._driver"), pytest.raises(ValueError, match="Nieznane typy relacji"):
            await get_neighbors(uuid.uuid4(), "n1", relation_types=["INVALID_RELATION"])

    async def test_invalid_node_type_raises_value_error(self) -> None:
        """Nieprawidlowy node_types -- ValueError z komunikatem."""
        from monolynx.services.graph import get_neighbors

        with patch("monolynx.services.graph._driver"), pytest.raises(ValueError, match="Nieznane typy node"):
            await get_neighbors(uuid.uuid4(), "n1", node_types=["BadNodeType"])

    async def test_multiple_invalid_relation_types_all_listed_in_error(self) -> None:
        """Wiele blednych relation_types -- wszystkie wymienione w komunikacie bledu."""
        from monolynx.services.graph import get_neighbors

        with patch("monolynx.services.graph._driver"), pytest.raises(ValueError) as exc_info:
            await get_neighbors(uuid.uuid4(), "n1", relation_types=["FOO", "BAR"])

        error_msg = str(exc_info.value)
        assert "FOO" in error_msg
        assert "BAR" in error_msg

    async def test_multiple_invalid_node_types_all_listed_in_error(self) -> None:
        """Wiele blednych node_types -- wszystkie wymienione w komunikacie bledu."""
        from monolynx.services.graph import get_neighbors

        with patch("monolynx.services.graph._driver"), pytest.raises(ValueError) as exc_info:
            await get_neighbors(uuid.uuid4(), "n1", node_types=["Unknown1", "Unknown2"])

        error_msg = str(exc_info.value)
        assert "Unknown1" in error_msg
        assert "Unknown2" in error_msg

    async def test_empty_relation_types_list_acts_as_no_filter(self) -> None:
        """Pusta lista relation_types: dziala jak None (brak filtra relacji w Cypher)."""
        from monolynx.services.graph import get_neighbors

        session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.__aiter__ = lambda self: _AsyncIterEmpty()
        session.run.return_value = result_mock

        with patch("monolynx.services.graph._driver", _make_mock_graph_driver(session)):
            result = await get_neighbors(uuid.uuid4(), "n1", relation_types=[])

        assert result["nodes"] == []
        assert result["edges"] == []
        query = session.run.call_args[0][0]
        # Pusta lista -> brak filtra relacji w Cypher (nie ma [: w wzorcu)
        assert "[:" not in query

    async def test_empty_node_types_list_acts_as_no_filter(self) -> None:
        """Pusta lista node_types: dziala jak None (brak WHERE lbl IN labels)."""
        from monolynx.services.graph import get_neighbors

        session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.__aiter__ = lambda self: _AsyncIterEmpty()
        session.run.return_value = result_mock

        with patch("monolynx.services.graph._driver", _make_mock_graph_driver(session)):
            result = await get_neighbors(uuid.uuid4(), "n1", node_types=[])

        assert result["nodes"] == []
        query = session.run.call_args[0][0]
        assert "lbl IN labels" not in query

    async def test_valid_relation_types_appear_in_cypher(self) -> None:
        """Prawidlowe relation_types generuja odpowiedni fragment Cypher."""
        from monolynx.services.graph import get_neighbors

        session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.__aiter__ = lambda self: _AsyncIterEmpty()
        session.run.return_value = result_mock

        with patch("monolynx.services.graph._driver", _make_mock_graph_driver(session)):
            await get_neighbors(uuid.uuid4(), "n1", relation_types=["CALLS", "INHERITS"])

        query = session.run.call_args[0][0]
        assert "CALLS|INHERITS" in query or "INHERITS|CALLS" in query

    async def test_valid_node_types_generate_where_clause(self) -> None:
        """Prawidlowe node_types generuja WHERE clause z lbl IN labels(neighbor)."""
        from monolynx.services.graph import get_neighbors

        session = AsyncMock()
        result_mock = AsyncMock()
        result_mock.__aiter__ = lambda self: _AsyncIterEmpty()
        session.run.return_value = result_mock

        with patch("monolynx.services.graph._driver", _make_mock_graph_driver(session)):
            await get_neighbors(uuid.uuid4(), "n1", node_types=["Class", "Method"])

        query = session.run.call_args[0][0]
        assert "lbl IN labels(neighbor)" in query


# ---------------------------------------------------------------------------
# C) Testy integracyjne -- get_graph_node z nowymi parametrami
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetGraphNodeWithFilters:
    async def test_relation_types_passed_to_get_neighbors(self) -> None:
        """get_graph_node przekazuje relation_types do graph_service.get_neighbors."""
        mock_auth_fn, _user, _project = _mock_auth()
        node = _sample_node(node_id="abc123", name="details.py", node_type="File")
        neighbors_with_depth = {
            "nodes": [_sample_node(node_id="n1", name="Neighbor", node_type="Class")],
            "edges": [_sample_edge("abc123", "n1", "CONTAINS")],
            "depth_map": {"abc123": 0, "n1": 1},
        }

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_node = AsyncMock(return_value=node)
            mock_gs.get_neighbors = AsyncMock(return_value=neighbors_with_depth)

            result = await get_graph_node(
                _make_ctx(),
                "test-project",
                node_id="abc123",
                depth=2,
                relation_types=["CONTAINS"],
            )

        assert isinstance(result, str)
        call_kwargs = mock_gs.get_neighbors.call_args.kwargs
        assert call_kwargs["relation_types"] == ["CONTAINS"]
        assert call_kwargs["depth"] == 2

    async def test_node_types_passed_to_get_neighbors(self) -> None:
        """get_graph_node przekazuje node_types do graph_service.get_neighbors."""
        mock_auth_fn, _user, _project = _mock_auth()
        node = _sample_node(node_id="root", name="root.py", node_type="File")
        neighbors_with_depth = {"nodes": [], "edges": [], "depth_map": {"root": 0}}

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_node = AsyncMock(return_value=node)
            mock_gs.get_neighbors = AsyncMock(return_value=neighbors_with_depth)

            await get_graph_node(
                _make_ctx(),
                "test-project",
                node_id="root",
                node_types=["Class", "Method"],
            )

        call_kwargs = mock_gs.get_neighbors.call_args.kwargs
        assert call_kwargs["node_types"] == ["Class", "Method"]

    async def test_output_contains_depth_ring_headers_when_depth_map_present(self) -> None:
        """get_graph_node z depth_map: wynik zawiera sekcje '--- Depth N ---'."""
        mock_auth_fn, _user, _project = _mock_auth()
        node = _sample_node(node_id="root", name="root.py", node_type="File")
        neighbors_with_depth = {
            "nodes": [
                _sample_node(node_id="root", name="root.py", node_type="File"),
                _sample_node(node_id="child", name="child.py", node_type="Class"),
            ],
            "edges": [_sample_edge("root", "child", "CONTAINS")],
            "depth_map": {"root": 0, "child": 1},
        }

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_node = AsyncMock(return_value=node)
            mock_gs.get_neighbors = AsyncMock(return_value=neighbors_with_depth)

            result = await get_graph_node(_make_ctx(), "test-project", node_id="root")

        assert "--- Depth 0 ---" in result
        assert "--- Depth 1 ---" in result
        assert "=== CONTAINS ===" in result

    async def test_default_params_pass_none_for_filters(self) -> None:
        """Domyslne parametry: relation_types i node_types sa None, depth=1."""
        mock_auth_fn, _user, _project = _mock_auth()
        node = _sample_node(node_id="n1", name="file.py")
        neighbors = {"nodes": [], "edges": [], "depth_map": {"n1": 0}}

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_node = AsyncMock(return_value=node)
            mock_gs.get_neighbors = AsyncMock(return_value=neighbors)

            await get_graph_node(_make_ctx(), "test-project", node_id="n1")

        call_kwargs = mock_gs.get_neighbors.call_args.kwargs
        assert call_kwargs.get("relation_types") is None
        assert call_kwargs.get("node_types") is None
        assert call_kwargs.get("depth") == 1

    async def test_node_not_found_raises_value_error(self) -> None:
        """get_graph_node gdy node nie istnieje -- ValueError."""
        mock_auth_fn, _user, _project = _mock_auth()

        with (
            patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn),
            patch("monolynx.mcp_server.graph_service") as mock_gs,
        ):
            mock_gs.is_enabled.return_value = True
            mock_gs.get_node = AsyncMock(return_value=None)

            with pytest.raises(ValueError, match="Node nie istnieje"):
                await get_graph_node(_make_ctx(), "test-project", node_id="missing")
