"""Testy jednostkowe -- serwis grafu Neo4j (CRUD, zapytania, helpery)."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.services.graph import (
    _parse_metadata,
    create_edge,
    create_node,
    delete_edge,
    delete_node,
    find_path,
    get_graph,
    get_node,
    get_stats,
    is_enabled,
    list_nodes,
    update_node,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class AsyncIterMock:
    """Async iterator wrapper for mocking Neo4j result iteration."""

    def __init__(self, items: list):
        self._items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration:
            raise StopAsyncIteration from None


def _make_result_with_records(*records) -> AsyncMock:
    """Creates a mock Neo4j result that supports async iteration."""
    result_mock = AsyncMock()
    result_mock.__aiter__ = lambda self: AsyncIterMock(list(records))
    return result_mock


def _make_record(data: dict) -> MagicMock:
    """Tworzy mock rekordu Neo4j (dict-like)."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: data[key]
    record.get = lambda key, default=None: data.get(key, default)
    return record


def _make_node_props(
    node_id: str | None = None,
    project_id: str | None = None,
    name: str = "TestNode",
    file_path: str | None = None,
    line_number: int | None = None,
    metadata: str = "{}",
) -> MagicMock:
    """Tworzy mock Neo4j node z properties."""
    node_id = node_id or uuid.uuid4().hex
    project_id = project_id or str(uuid.uuid4())
    node = MagicMock()
    _data = {
        "id": node_id,
        "project_id": project_id,
        "name": name,
        "file_path": file_path,
        "line_number": line_number,
        "metadata": metadata,
    }
    node.__getitem__ = lambda self, key: _data[key]
    node.get = lambda key, default=None: _data.get(key, default)
    return node


@pytest.fixture
def mock_driver():
    """Mock Neo4j driver z async sesja."""
    with patch("monolynx.services.graph._driver") as driver:
        session = AsyncMock()
        driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
        driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
        yield driver, session


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsEnabled:
    @patch("monolynx.services.graph.settings")
    def test_is_enabled_true(self, mock_settings: MagicMock) -> None:
        mock_settings.ENABLE_GRAPH_DB = True
        assert is_enabled() is True

    @patch("monolynx.services.graph.settings")
    def test_is_enabled_false(self, mock_settings: MagicMock) -> None:
        mock_settings.ENABLE_GRAPH_DB = False
        assert is_enabled() is False


# ---------------------------------------------------------------------------
# create_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateNode:
    async def test_create_node(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        node = _make_node_props(name="MyFile", file_path="/src/main.py")
        record = _make_record({"n": node})
        result_mock = AsyncMock()
        result_mock.single.return_value = record
        session.run.return_value = result_mock

        data = {
            "type": "File",
            "name": "MyFile",
            "file_path": "/src/main.py",
            "metadata": {},
        }
        result = await create_node(project_id, data)

        assert result["name"] == "MyFile"
        assert result["type"] == "File"
        assert result["file_path"] == "/src/main.py"
        session.run.assert_called_once()

    async def test_create_node_invalid_type(self, mock_driver: tuple) -> None:
        _driver, _session = mock_driver
        project_id = uuid.uuid4()
        data = {"type": "InvalidType", "name": "Test"}
        with pytest.raises(ValueError, match="Nieznany typ node'a"):
            await create_node(project_id, data)


# ---------------------------------------------------------------------------
# get_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNode:
    async def test_get_node(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        node_id = uuid.uuid4().hex
        node = _make_node_props(node_id=node_id, name="FoundNode")
        record = _make_record({"n": node, "labels": ["File"]})
        result_mock = AsyncMock()
        result_mock.single.return_value = record
        session.run.return_value = result_mock

        result = await get_node(project_id, node_id)

        assert result is not None
        assert result["name"] == "FoundNode"
        assert result["type"] == "File"

    async def test_get_node_not_found(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        result_mock = AsyncMock()
        result_mock.single.return_value = None
        session.run.return_value = result_mock

        result = await get_node(project_id, "nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# list_nodes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListNodes:
    async def test_list_nodes(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        node1 = _make_node_props(name="Node1")
        node2 = _make_node_props(name="Node2")

        # Mock async iteration: session.run returns result, iterating yields records
        record1 = _make_record({"n": node1, "labels": ["File"]})
        record2 = _make_record({"n": node2, "labels": ["Class"]})
        session.run.return_value = _make_result_with_records(record1, record2)

        result = await list_nodes(project_id)

        assert len(result) == 2
        assert result[0]["name"] == "Node1"
        assert result[1]["name"] == "Node2"

    async def test_list_nodes_with_filter(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        node = _make_node_props(name="FilteredNode")
        record = _make_record({"n": node, "labels": ["File"]})
        session.run.return_value = _make_result_with_records(record)

        result = await list_nodes(project_id, type_filter="File")

        assert len(result) == 1
        # Sprawdz ze query zawiera typ File w MATCH
        call_args = session.run.call_args
        query = call_args[0][0]
        assert "File" in query


# ---------------------------------------------------------------------------
# update_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateNode:
    async def test_update_node(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        node_id = uuid.uuid4().hex
        node = _make_node_props(node_id=node_id, name="UpdatedNode")
        record = _make_record({"n": node, "labels": ["File"]})
        result_mock = AsyncMock()
        result_mock.single.return_value = record
        session.run.return_value = result_mock

        result = await update_node(project_id, node_id, {"name": "UpdatedNode"})

        assert result is not None
        assert result["name"] == "UpdatedNode"
        session.run.assert_called_once()

    async def test_update_node_not_found(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        result_mock = AsyncMock()
        result_mock.single.return_value = None
        session.run.return_value = result_mock

        result = await update_node(project_id, "nonexistent", {"name": "X"})
        assert result is None

    async def test_update_node_no_changes(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        node_id = uuid.uuid4().hex

        # update_node z pustym data wywoluje get_node zamiast UPDATE
        node = _make_node_props(node_id=node_id, name="Unchanged")
        record = _make_record({"n": node, "labels": ["File"]})
        result_mock = AsyncMock()
        result_mock.single.return_value = record
        session.run.return_value = result_mock

        result = await update_node(project_id, node_id, {})

        assert result is not None
        assert result["name"] == "Unchanged"


# ---------------------------------------------------------------------------
# delete_node
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteNode:
    async def test_delete_node(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        record = _make_record({"deleted": 1})
        result_mock = AsyncMock()
        result_mock.single.return_value = record
        session.run.return_value = result_mock

        result = await delete_node(project_id, "node-123")
        assert result is True

    async def test_delete_node_not_found(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        record = _make_record({"deleted": 0})
        result_mock = AsyncMock()
        result_mock.single.return_value = record
        session.run.return_value = result_mock

        result = await delete_node(project_id, "nonexistent")
        assert result is False


# ---------------------------------------------------------------------------
# create_edge
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateEdge:
    async def test_create_edge(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        record = _make_record(
            {
                "source_id": "src-1",
                "target_id": "tgt-1",
                "type": "CALLS",
                "metadata": "{}",
            }
        )
        result_mock = AsyncMock()
        result_mock.single.return_value = record
        session.run.return_value = result_mock

        result = await create_edge(project_id, "src-1", "tgt-1", "CALLS")

        assert result is not None
        assert result["source_id"] == "src-1"
        assert result["target_id"] == "tgt-1"
        assert result["type"] == "CALLS"

    async def test_create_edge_invalid_type(self, mock_driver: tuple) -> None:
        _driver, _session = mock_driver
        project_id = uuid.uuid4()
        with pytest.raises(ValueError, match="Nieznany typ krawedzi"):
            await create_edge(project_id, "src", "tgt", "INVALID_EDGE")

    async def test_create_edge_nodes_not_found(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        result_mock = AsyncMock()
        result_mock.single.return_value = None
        session.run.return_value = result_mock

        result = await create_edge(project_id, "missing-src", "missing-tgt", "CALLS")
        assert result is None


# ---------------------------------------------------------------------------
# delete_edge
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteEdge:
    async def test_delete_edge(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        record = _make_record({"deleted": 1})
        result_mock = AsyncMock()
        result_mock.single.return_value = record
        session.run.return_value = result_mock

        result = await delete_edge(project_id, "src", "tgt", "CALLS")
        assert result is True

    async def test_delete_edge_invalid_type(self, mock_driver: tuple) -> None:
        _driver, _session = mock_driver
        project_id = uuid.uuid4()
        with pytest.raises(ValueError, match="Nieznany typ krawedzi"):
            await delete_edge(project_id, "src", "tgt", "BAD_TYPE")

    async def test_delete_edge_not_found(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()
        record = _make_record({"deleted": 0})
        result_mock = AsyncMock()
        result_mock.single.return_value = record
        session.run.return_value = result_mock

        result = await delete_edge(project_id, "src", "tgt", "CALLS")
        assert result is False


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStats:
    async def test_get_stats(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()

        # session.run jest wolany wielokrotnie: raz per node type + raz per edges
        node_type_count = 6  # File, Class, Method, Function, Const, Module
        node_results = []
        for _ in range(node_type_count):
            result_mock = AsyncMock()
            result_mock.single.return_value = _make_record({"count": 3})
            node_results.append(result_mock)

        # Edge result -- async iterable
        edge_record1 = _make_record({"type": "CALLS", "count": 5})
        edge_record2 = _make_record({"type": "IMPORTS", "count": 2})
        edge_result = _make_result_with_records(edge_record1, edge_record2)

        session.run.side_effect = [*node_results, edge_result]

        result = await get_stats(project_id)

        assert result["total_nodes"] == 18  # 6 typow * 3
        assert result["total_edges"] == 7  # 5 + 2
        assert result["nodes_by_type"]["File"] == 3
        assert result["edges_by_type"]["CALLS"] == 5


# ---------------------------------------------------------------------------
# get_graph
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetGraph:
    async def test_get_graph(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()

        node_id = uuid.uuid4().hex
        node = _make_node_props(node_id=node_id, name="GraphNode")
        node_record = _make_record({"n": node, "labels": ["File"]})
        node_result = _make_result_with_records(node_record)

        edge_record = _make_record(
            {
                "source_id": node_id,
                "target_id": node_id,
                "type": "CALLS",
                "metadata": "{}",
            }
        )
        edge_result = _make_result_with_records(edge_record)

        session.run.side_effect = [node_result, edge_result]

        result = await get_graph(project_id)

        assert len(result["nodes"]) == 1
        assert len(result["edges"]) == 1
        assert result["nodes"][0]["name"] == "GraphNode"
        assert result["edges"][0]["type"] == "CALLS"


# ---------------------------------------------------------------------------
# find_path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindPath:
    async def test_find_path(self, mock_driver: tuple) -> None:
        _driver, session = mock_driver
        project_id = uuid.uuid4()

        node_a = _make_node_props(node_id="a1", name="NodeA")
        node_b = _make_node_props(node_id="b1", name="NodeB")

        record1 = _make_record(
            {
                "n": node_a,
                "labels": ["File"],
                "source_id": "a1",
                "target_id": "b1",
                "edge_type": "CALLS",
                "edge_metadata": "{}",
            }
        )
        record2 = _make_record(
            {
                "n": node_b,
                "labels": ["Method"],
                "source_id": "a1",
                "target_id": "b1",
                "edge_type": "CALLS",
                "edge_metadata": "{}",
            }
        )

        session.run.return_value = _make_result_with_records(record1, record2)

        result = await find_path(project_id, "a1", "b1")

        assert len(result["nodes"]) == 2
        assert len(result["edges"]) == 1  # deduplikacja edge'y
        node_names = {n["name"] for n in result["nodes"]}
        assert "NodeA" in node_names
        assert "NodeB" in node_names


# ---------------------------------------------------------------------------
# _parse_metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseMetadata:
    def test_parse_metadata_json_string(self) -> None:
        result = _parse_metadata('{"key": "value"}')
        assert result == {"key": "value"}

    def test_parse_metadata_dict(self) -> None:
        result = _parse_metadata({"key": "value"})
        assert result == {"key": "value"}

    def test_parse_metadata_invalid_string(self) -> None:
        result = _parse_metadata("not-json")
        assert result == {}

    def test_parse_metadata_none(self) -> None:
        result = _parse_metadata(None)
        assert result == {}
