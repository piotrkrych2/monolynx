"""Schematy response API v2 dla odczytu grafu Połączeń (MON-248).

Kształt zwracany przez `services/graph.py` (dict-y z Neo4j) bez zmian; krawędź dodatkowo
wynosi `confidence` i `source_relation` z `metadata` na wierzch, a `metadata` zostaje
nietknięte.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, model_validator


class GraphNodeResponse(BaseModel):
    id: str
    project_id: str
    name: str
    type: str
    file_path: str | None = None
    line_number: int | None = None
    metadata: dict[str, Any]


class GraphEdgeResponse(BaseModel):
    source_id: str
    target_id: str
    type: str
    confidence: str | None = None
    source_relation: str | None = None
    metadata: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def _lift_provenance(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        metadata = data.get("metadata") or {}
        return {
            **data,
            "metadata": metadata,
            "confidence": data.get("confidence", metadata.get("confidence")),
            "source_relation": data.get("source_relation", metadata.get("source_relation")),
        }


class GraphSubgraphResponse(BaseModel):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]


class GraphNodeDetailResponse(BaseModel):
    node: GraphNodeResponse
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    depth_map: dict[str, int]


class GraphStatsResponse(BaseModel):
    total_nodes: int
    total_edges: int
    nodes_by_type: dict[str, int]
    edges_by_type: dict[str, int]
