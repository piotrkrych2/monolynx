"""Serwis grafu Neo4j -- polaczenie, sesja, CRUD, zapytania."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from monolynx.config import settings
from monolynx.constants import GRAPH_EDGE_TYPES, GRAPH_NODE_TYPES

logger = logging.getLogger("monolynx.graph")

# Globalny driver (singleton)
_driver: Any = None


def is_enabled() -> bool:
    """Czy graf Neo4j jest wlaczony."""
    return settings.ENABLE_GRAPH_DB


async def init_driver() -> None:
    """Inicjalizuj async Neo4j driver. Wywolywane w lifespan."""
    global _driver
    if not is_enabled():
        logger.info("Graf Neo4j wylaczony (ENABLE_GRAPH_DB=false)")
        return

    try:
        from neo4j import AsyncGraphDatabase

        _driver = AsyncGraphDatabase.driver(
            settings.NEO4J_URI,
            auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
        )
        # Verify connectivity
        await _driver.verify_connectivity()
        logger.info("Polaczono z Neo4j: %s", settings.NEO4J_URI)
    except Exception:
        logger.warning("Nie udalo sie polaczyc z Neo4j -- graf niedostepny", exc_info=True)
        _driver = None


async def close_driver() -> None:
    """Zamknij driver. Wywolywane przy shutdown."""
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None
        logger.info("Neo4j driver zamkniety")


async def init_schema() -> None:
    """Inicjalizuj constraints i indexy w Neo4j."""
    if _driver is None:
        return
    try:
        from monolynx.constants import GRAPH_NODE_TYPES

        async with _driver.session() as session:
            # Constraint unikalnosci id per typ node'a
            for node_type in GRAPH_NODE_TYPES:
                await session.run(f"CREATE CONSTRAINT {node_type.lower()}_id IF NOT EXISTS FOR (n:{node_type}) REQUIRE n.id IS UNIQUE")
            # Index na project_id dla szybkiego filtrowania per projekt
            for node_type in GRAPH_NODE_TYPES:
                await session.run(f"CREATE INDEX {node_type.lower()}_project_id IF NOT EXISTS FOR (n:{node_type}) ON (n.project_id)")
        logger.info("Neo4j schema zainicjalizowana (%d typow node'ow)", len(GRAPH_NODE_TYPES))
    except Exception:
        logger.warning("Blad inicjalizacji schema Neo4j", exc_info=True)


@asynccontextmanager
async def get_neo4j_session() -> AsyncGenerator[Any, None]:
    """Context manager zwracajacy sesje Neo4j. Rzuca RuntimeError jesli driver niedostepny."""
    if _driver is None:
        raise RuntimeError("Neo4j driver niedostepny")
    async with _driver.session() as session:
        yield session


# ---------------------------------------------------------------------------
# Helpery
# ---------------------------------------------------------------------------


def _node_to_dict(node: Any, node_type: str) -> dict[str, Any]:
    """Konwertuj Neo4j node na dict zgodny z GraphNodeResponse."""
    return {
        "id": node["id"],
        "project_id": node["project_id"],
        "name": node["name"],
        "type": node_type,
        "file_path": node.get("file_path"),
        "line_number": node.get("line_number"),
        "metadata": _parse_metadata(node.get("metadata", "{}")),
    }


def _parse_metadata(raw: Any) -> dict[str, Any]:
    """Parsuj metadata z Neo4j (przechowywane jako string JSON)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            result: dict[str, Any] = json.loads(raw)
            return result
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


# ---------------------------------------------------------------------------
# Node CRUD
# ---------------------------------------------------------------------------


async def create_node(
    project_id: uuid.UUID,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Utwórz node w grafie. data zawiera: name, type, file_path, line_number, metadata."""
    node_id = uuid.uuid4().hex
    node_type = data["type"]  # np. "File", "Class"
    if node_type not in GRAPH_NODE_TYPES:
        raise ValueError(f"Nieznany typ node'a: {node_type}")
    async with get_neo4j_session() as session:
        result = await session.run(
            f"CREATE (n:{node_type} {{id: $id, project_id: $project_id, name: $name, "
            f"file_path: $file_path, line_number: $line_number, metadata: $metadata}}) "
            f"RETURN n",
            id=node_id,
            project_id=str(project_id),
            name=data["name"],
            file_path=data.get("file_path"),
            line_number=data.get("line_number"),
            metadata=json.dumps(data.get("metadata", {})),
        )
        record = await result.single()
        node = record["n"]
        return _node_to_dict(node, node_type)


async def get_node(project_id: uuid.UUID, node_id: str) -> dict[str, Any] | None:
    """Pobierz node po ID z relacjami."""
    async with get_neo4j_session() as session:
        result = await session.run(
            "MATCH (n {id: $node_id, project_id: $project_id}) RETURN n, labels(n) AS labels",
            node_id=node_id,
            project_id=str(project_id),
        )
        record = await result.single()
        if record is None:
            return None
        node = record["n"]
        labels = [lbl for lbl in record["labels"] if lbl in GRAPH_NODE_TYPES]
        node_type = labels[0] if labels else "Unknown"
        return _node_to_dict(node, node_type)


async def list_nodes(
    project_id: uuid.UUID,
    type_filter: str | None = None,
    search: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Lista node'ów z opcjonalnym filtrowaniem po typie i nazwie."""
    # Buduj query dynamicznie w zależności od filtrów
    if type_filter and type_filter in GRAPH_NODE_TYPES:
        match_clause = f"MATCH (n:{type_filter} {{project_id: $project_id}})"
    else:
        match_clause = "MATCH (n {project_id: $project_id})"

    where_clause = ""
    params: dict[str, Any] = {"project_id": str(project_id), "limit": limit}

    if search:
        where_clause = "WHERE n.name CONTAINS $search"
        params["search"] = search

    query = f"{match_clause} {where_clause} RETURN n, labels(n) AS labels ORDER BY n.name LIMIT $limit"

    async with get_neo4j_session() as session:
        result = await session.run(query, **params)
        records = [record async for record in result]

    nodes = []
    for record in records:
        labels = [lbl for lbl in record["labels"] if lbl in GRAPH_NODE_TYPES]
        node_type = labels[0] if labels else "Unknown"
        nodes.append(_node_to_dict(record["n"], node_type))
    return nodes


async def update_node(
    project_id: uuid.UUID,
    node_id: str,
    data: dict[str, Any],
) -> dict[str, Any] | None:
    """Zaktualizuj node. data zawiera tylko pola do zmiany."""
    set_clauses = []
    params: dict[str, Any] = {"node_id": node_id, "project_id": str(project_id)}

    for field in ("name", "file_path", "line_number"):
        if field in data and data[field] is not None:
            set_clauses.append(f"n.{field} = ${field}")
            params[field] = data[field]

    if "metadata" in data and data["metadata"] is not None:
        set_clauses.append("n.metadata = $metadata")
        params["metadata"] = json.dumps(data["metadata"])

    if not set_clauses:
        return await get_node(project_id, node_id)

    set_str = ", ".join(set_clauses)
    query = f"MATCH (n {{id: $node_id, project_id: $project_id}}) SET {set_str} RETURN n, labels(n) AS labels"

    async with get_neo4j_session() as session:
        result = await session.run(query, **params)
        record = await result.single()
        if record is None:
            return None
        labels = [lbl for lbl in record["labels"] if lbl in GRAPH_NODE_TYPES]
        node_type = labels[0] if labels else "Unknown"
        return _node_to_dict(record["n"], node_type)


async def delete_node(project_id: uuid.UUID, node_id: str) -> bool:
    """Usuń node i wszystkie jego krawędzie (cascade)."""
    async with get_neo4j_session() as session:
        result = await session.run(
            "MATCH (n {id: $node_id, project_id: $project_id}) DETACH DELETE n RETURN count(n) AS deleted",
            node_id=node_id,
            project_id=str(project_id),
        )
        record = await result.single()
        return record is not None and record["deleted"] > 0


# ---------------------------------------------------------------------------
# Edge CRUD
# ---------------------------------------------------------------------------


async def create_edge(
    project_id: uuid.UUID,
    source_id: str,
    target_id: str,
    edge_type: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Utwórz krawędź między dwoma node'ami."""
    if edge_type not in GRAPH_EDGE_TYPES:
        raise ValueError(f"Nieznany typ krawedzi: {edge_type}")

    async with get_neo4j_session() as session:
        result = await session.run(
            f"MATCH (a {{id: $source_id, project_id: $project_id}}), "
            f"(b {{id: $target_id, project_id: $project_id}}) "
            f"CREATE (a)-[r:{edge_type} {{metadata: $metadata}}]->(b) "
            f"RETURN a.id AS source_id, b.id AS target_id, type(r) AS type, r.metadata AS metadata",
            source_id=source_id,
            target_id=target_id,
            project_id=str(project_id),
            metadata=json.dumps(metadata or {}),
        )
        record = await result.single()
        if record is None:
            return None  # source or target not found
        return {
            "source_id": record["source_id"],
            "target_id": record["target_id"],
            "type": record["type"],
            "metadata": _parse_metadata(record["metadata"]),
        }


async def delete_edge(
    project_id: uuid.UUID,
    source_id: str,
    target_id: str,
    edge_type: str,
) -> bool:
    """Usuń krawędź między dwoma node'ami."""
    if edge_type not in GRAPH_EDGE_TYPES:
        raise ValueError(f"Nieznany typ krawedzi: {edge_type}")
    async with get_neo4j_session() as session:
        result = await session.run(
            f"MATCH (a {{id: $source_id, project_id: $project_id}})"
            f"-[r:{edge_type}]->"
            f"(b {{id: $target_id, project_id: $project_id}}) "
            f"DELETE r RETURN count(r) AS deleted",
            source_id=source_id,
            target_id=target_id,
            project_id=str(project_id),
        )
        record = await result.single()
        return record is not None and record["deleted"] > 0


# ---------------------------------------------------------------------------
# Zapytania (query)
# ---------------------------------------------------------------------------


async def get_neighbors(
    project_id: uuid.UUID,
    node_id: str,
    depth: int = 1,
) -> dict[str, Any]:
    """Pobierz sąsiadów node'a do zadanej głębokości. Zwraca GraphSearchResult."""
    depth = min(depth, 5)  # max 5 poziomów dla bezpieczeństwa
    async with get_neo4j_session() as session:
        # Pobierz node'y i krawędzie do zadanej głębokości
        result = await session.run(
            f"MATCH path = (start {{id: $node_id, project_id: $project_id}})"
            f"-[*1..{depth}]-(neighbor) "
            f"WHERE neighbor.project_id = $project_id "
            f"UNWIND nodes(path) AS n "
            f"UNWIND relationships(path) AS r "
            f"RETURN DISTINCT n, labels(n) AS labels, "
            f"startNode(r).id AS source_id, endNode(r).id AS target_id, "
            f"type(r) AS edge_type, r.metadata AS edge_metadata",
            node_id=node_id,
            project_id=str(project_id),
        )
        records = [record async for record in result]

    nodes_map: dict[str, dict[str, Any]] = {}
    edges_set: set[tuple[str, str, str]] = set()
    edges: list[dict[str, Any]] = []

    for record in records:
        node = record["n"]
        node_id_val = node["id"]
        if node_id_val not in nodes_map:
            labels = [lbl for lbl in record["labels"] if lbl in GRAPH_NODE_TYPES]
            node_type = labels[0] if labels else "Unknown"
            nodes_map[node_id_val] = _node_to_dict(node, node_type)

        edge_key = (record["source_id"], record["target_id"], record["edge_type"])
        if edge_key not in edges_set:
            edges_set.add(edge_key)
            edges.append(
                {
                    "source_id": record["source_id"],
                    "target_id": record["target_id"],
                    "type": record["edge_type"],
                    "metadata": _parse_metadata(record.get("edge_metadata", "{}")),
                }
            )

    return {
        "nodes": list(nodes_map.values()),
        "edges": edges,
    }


async def get_graph(
    project_id: uuid.UUID,
    type_filter: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Pobierz cały graf lub podgraf dla wizualizacji. Zwraca GraphSearchResult."""
    if type_filter and type_filter in GRAPH_NODE_TYPES:
        node_match = f"MATCH (n:{type_filter} {{project_id: $project_id}})"
    else:
        node_match = "MATCH (n {project_id: $project_id})"

    params: dict[str, Any] = {"project_id": str(project_id), "limit": limit}

    async with get_neo4j_session() as session:
        # Pobierz node'y
        node_result = await session.run(
            f"{node_match} RETURN n, labels(n) AS labels ORDER BY n.name LIMIT $limit",
            **params,
        )
        node_records = [record async for record in node_result]

        # Pobierz krawędzie między node'ami projektu
        edge_result = await session.run(
            "MATCH (a {project_id: $project_id})-[r]->(b {project_id: $project_id}) "
            "RETURN a.id AS source_id, b.id AS target_id, type(r) AS type, r.metadata AS metadata "
            "LIMIT $limit",
            **params,
        )
        edge_records = [record async for record in edge_result]

    nodes = []
    for record in node_records:
        labels = [lbl for lbl in record["labels"] if lbl in GRAPH_NODE_TYPES]
        node_type = labels[0] if labels else "Unknown"
        nodes.append(_node_to_dict(record["n"], node_type))

    edges = [
        {
            "source_id": record["source_id"],
            "target_id": record["target_id"],
            "type": record["type"],
            "metadata": _parse_metadata(record.get("metadata", "{}")),
        }
        for record in edge_records
    ]

    return {"nodes": nodes, "edges": edges}


async def find_path(
    project_id: uuid.UUID,
    source_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Znajdź najkrótszą ścieżkę między dwoma node'ami. Zwraca GraphSearchResult."""
    async with get_neo4j_session() as session:
        result = await session.run(
            "MATCH path = shortestPath("
            "(a {id: $source_id, project_id: $project_id})-[*]-(b {id: $target_id, project_id: $project_id})"
            ") "
            "UNWIND nodes(path) AS n "
            "UNWIND relationships(path) AS r "
            "RETURN DISTINCT n, labels(n) AS labels, "
            "startNode(r).id AS source_id, endNode(r).id AS target_id, "
            "type(r) AS edge_type, r.metadata AS edge_metadata",
            source_id=source_id,
            target_id=target_id,
            project_id=str(project_id),
        )
        records = [record async for record in result]

    nodes_map: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    edges_set: set[tuple[str, str, str]] = set()

    for record in records:
        node = record["n"]
        nid = node["id"]
        if nid not in nodes_map:
            labels = [lbl for lbl in record["labels"] if lbl in GRAPH_NODE_TYPES]
            node_type = labels[0] if labels else "Unknown"
            nodes_map[nid] = _node_to_dict(node, node_type)

        edge_key = (record["source_id"], record["target_id"], record["edge_type"])
        if edge_key not in edges_set:
            edges_set.add(edge_key)
            edges.append(
                {
                    "source_id": record["source_id"],
                    "target_id": record["target_id"],
                    "type": record["edge_type"],
                    "metadata": _parse_metadata(record.get("edge_metadata", "{}")),
                }
            )

    return {"nodes": list(nodes_map.values()), "edges": edges}


async def get_stats(project_id: uuid.UUID) -> dict[str, Any]:
    """Statystyki grafu: ilość node'ów i edge'ów per typ."""
    async with get_neo4j_session() as session:
        node_counts: dict[str, int] = {}
        for node_type in GRAPH_NODE_TYPES:
            result = await session.run(
                f"MATCH (n:{node_type} {{project_id: $project_id}}) RETURN count(n) AS count",
                project_id=str(project_id),
            )
            record = await result.single()
            node_counts[node_type] = record["count"] if record else 0

        # Ilość krawędzi per typ
        edge_result = await session.run(
            "MATCH (a {project_id: $project_id})-[r]->(b) RETURN type(r) AS type, count(r) AS count",
            project_id=str(project_id),
        )
        edge_records = [record async for record in edge_result]
        edge_counts = {record["type"]: record["count"] for record in edge_records}

    return {
        "total_nodes": sum(node_counts.values()),
        "total_edges": sum(edge_counts.values()),
        "nodes_by_type": node_counts,
        "edges_by_type": edge_counts,
    }
