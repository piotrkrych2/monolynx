"""Testy schematow Pydantic dla modulu grafu (polaczenia)."""

import uuid

import pytest
from pydantic import ValidationError

from monolynx.constants import GRAPH_EDGE_TYPES, GRAPH_NODE_TYPES
from monolynx.schemas.graph import (
    GraphEdgeCreate,
    GraphEdgeResponse,
    GraphNodeCreate,
    GraphNodeResponse,
    GraphNodeUpdate,
    GraphSearchResult,
)


@pytest.mark.unit
class TestGraphNodeCreate:
    def test_minimal(self):
        node = GraphNodeCreate(name="my_func", type="Function")
        assert node.name == "my_func"
        assert node.type == "Function"
        assert node.file_path is None
        assert node.line_number is None
        assert node.metadata == {}

    def test_full(self):
        node = GraphNodeCreate(
            name="MyClass",
            type="Class",
            file_path="src/app.py",
            line_number=42,
            metadata={"description": "Main class"},
        )
        assert node.name == "MyClass"
        assert node.type == "Class"
        assert node.file_path == "src/app.py"
        assert node.line_number == 42
        assert node.metadata == {"description": "Main class"}

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            GraphNodeCreate(name="", type="File")

    def test_name_too_long_rejected(self):
        with pytest.raises(ValidationError):
            GraphNodeCreate(name="x" * 513, type="File")

    def test_name_at_max_length(self):
        node = GraphNodeCreate(name="x" * 512, type="File")
        assert len(node.name) == 512

    def test_validate_type_valid_all(self):
        for node_type in GRAPH_NODE_TYPES:
            node = GraphNodeCreate(name="n", type=node_type)
            assert node.validate_type() is True

    def test_validate_type_invalid(self):
        node = GraphNodeCreate(name="n", type="InvalidType")
        assert node.validate_type() is False

    def test_metadata_default_factory_independent(self):
        node1 = GraphNodeCreate(name="a", type="File")
        node2 = GraphNodeCreate(name="b", type="File")
        node1.metadata["key"] = "value"
        assert "key" not in node2.metadata


@pytest.mark.unit
class TestGraphNodeUpdate:
    def test_all_none_defaults(self):
        update = GraphNodeUpdate()
        assert update.name is None
        assert update.file_path is None
        assert update.line_number is None
        assert update.metadata is None

    def test_partial_update(self):
        update = GraphNodeUpdate(name="new_name")
        assert update.name == "new_name"
        assert update.file_path is None

    def test_full_update(self):
        update = GraphNodeUpdate(
            name="updated",
            file_path="new/path.py",
            line_number=100,
            metadata={"version": 2},
        )
        assert update.name == "updated"
        assert update.file_path == "new/path.py"
        assert update.line_number == 100
        assert update.metadata == {"version": 2}

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            GraphNodeUpdate(name="")

    def test_name_too_long_rejected(self):
        with pytest.raises(ValidationError):
            GraphNodeUpdate(name="x" * 513)

    def test_name_at_max_length(self):
        update = GraphNodeUpdate(name="x" * 512)
        assert len(update.name) == 512


@pytest.mark.unit
class TestGraphNodeResponse:
    def test_construction(self):
        pid = uuid.uuid4()
        resp = GraphNodeResponse(
            id="neo4j-id-123",
            project_id=pid,
            name="handler",
            type="Function",
        )
        assert resp.id == "neo4j-id-123"
        assert resp.project_id == pid
        assert resp.name == "handler"
        assert resp.type == "Function"
        assert resp.file_path is None
        assert resp.line_number is None
        assert resp.metadata == {}

    def test_full_construction(self):
        pid = uuid.uuid4()
        resp = GraphNodeResponse(
            id="abc",
            project_id=pid,
            name="Config",
            type="Class",
            file_path="config.py",
            line_number=10,
            metadata={"docstring": "App config"},
        )
        assert resp.file_path == "config.py"
        assert resp.line_number == 10
        assert resp.metadata == {"docstring": "App config"}

    def test_metadata_default_factory_independent(self):
        pid = uuid.uuid4()
        r1 = GraphNodeResponse(id="1", project_id=pid, name="a", type="File")
        r2 = GraphNodeResponse(id="2", project_id=pid, name="b", type="File")
        r1.metadata["key"] = "val"
        assert "key" not in r2.metadata


@pytest.mark.unit
class TestGraphEdgeCreate:
    def test_minimal(self):
        edge = GraphEdgeCreate(
            source_id="src-1",
            target_id="tgt-1",
            type="CALLS",
        )
        assert edge.source_id == "src-1"
        assert edge.target_id == "tgt-1"
        assert edge.type == "CALLS"
        assert edge.metadata == {}

    def test_with_metadata(self):
        edge = GraphEdgeCreate(
            source_id="s",
            target_id="t",
            type="IMPORTS",
            metadata={"weight": 3},
        )
        assert edge.metadata == {"weight": 3}

    def test_validate_type_valid_all(self):
        for edge_type in GRAPH_EDGE_TYPES:
            edge = GraphEdgeCreate(source_id="s", target_id="t", type=edge_type)
            assert edge.validate_type() is True

    def test_validate_type_invalid(self):
        edge = GraphEdgeCreate(source_id="s", target_id="t", type="UNKNOWN")
        assert edge.validate_type() is False

    def test_metadata_default_factory_independent(self):
        e1 = GraphEdgeCreate(source_id="s", target_id="t", type="CALLS")
        e2 = GraphEdgeCreate(source_id="s", target_id="t", type="CALLS")
        e1.metadata["key"] = "val"
        assert "key" not in e2.metadata


@pytest.mark.unit
class TestGraphEdgeResponse:
    def test_construction(self):
        resp = GraphEdgeResponse(
            source_id="src-1",
            target_id="tgt-1",
            type="INHERITS",
        )
        assert resp.source_id == "src-1"
        assert resp.target_id == "tgt-1"
        assert resp.type == "INHERITS"
        assert resp.metadata == {}

    def test_with_metadata(self):
        resp = GraphEdgeResponse(
            source_id="s",
            target_id="t",
            type="USES",
            metadata={"info": "test"},
        )
        assert resp.metadata == {"info": "test"}

    def test_metadata_default_factory_independent(self):
        r1 = GraphEdgeResponse(source_id="s", target_id="t", type="CALLS")
        r2 = GraphEdgeResponse(source_id="s", target_id="t", type="CALLS")
        r1.metadata["key"] = "val"
        assert "key" not in r2.metadata


@pytest.mark.unit
class TestGraphSearchResult:
    def test_empty_defaults(self):
        result = GraphSearchResult()
        assert result.nodes == []
        assert result.edges == []

    def test_with_nodes_and_edges(self):
        pid = uuid.uuid4()
        node = GraphNodeResponse(
            id="n1",
            project_id=pid,
            name="main",
            type="Function",
        )
        edge = GraphEdgeResponse(
            source_id="n1",
            target_id="n2",
            type="CALLS",
        )
        result = GraphSearchResult(nodes=[node], edges=[edge])
        assert len(result.nodes) == 1
        assert result.nodes[0].id == "n1"
        assert len(result.edges) == 1
        assert result.edges[0].type == "CALLS"

    def test_lists_default_factory_independent(self):
        r1 = GraphSearchResult()
        r2 = GraphSearchResult()
        pid = uuid.uuid4()
        r1.nodes.append(GraphNodeResponse(id="x", project_id=pid, name="a", type="File"))
        assert len(r2.nodes) == 0

    def test_multiple_nodes_and_edges(self):
        pid = uuid.uuid4()
        nodes = [GraphNodeResponse(id=f"n{i}", project_id=pid, name=f"node{i}", type="File") for i in range(3)]
        edges = [GraphEdgeResponse(source_id="n0", target_id=f"n{i}", type="CONTAINS") for i in range(1, 3)]
        result = GraphSearchResult(nodes=nodes, edges=edges)
        assert len(result.nodes) == 3
        assert len(result.edges) == 2
