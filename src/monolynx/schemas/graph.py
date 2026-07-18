"""Schematy Pydantic dla modulu grafu (polaczenia)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from monolynx.constants import GRAPH_EDGE_CONFIDENCE, GRAPH_EDGE_TYPES, GRAPH_NODE_TYPES


class GraphNodeCreate(BaseModel):
    """Tworzenie node'a w grafie."""

    name: str = Field(min_length=1, max_length=512)
    type: str
    file_path: str | None = None
    line_number: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def validate_type(self) -> bool:
        return self.type in GRAPH_NODE_TYPES


class GraphNodeUpdate(BaseModel):
    """Aktualizacja node'a."""

    name: str | None = Field(default=None, min_length=1, max_length=512)
    file_path: str | None = None
    line_number: int | None = None
    metadata: dict[str, Any] | None = None


class GraphNodeResponse(BaseModel):
    """Odpowiedź z danymi node'a."""

    id: str
    project_id: uuid.UUID
    name: str
    type: str
    file_path: str | None = None
    line_number: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdgeCreate(BaseModel):
    """Tworzenie krawędzi (edge) między node'ami.

    Pola pochodzenia (sync z ekstraktora, np. graphify): confidence
    (EXTRACTED/INFERRED/AMBIGUOUS) i source_relation (oryginalna relacja
    przed mapowaniem na GRAPH_EDGE_TYPES). W Neo4j żyją w metadata krawędzi.
    """

    source_id: str
    target_id: str
    type: str
    confidence: str | None = None
    source_relation: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def validate_type(self) -> bool:
        return self.type in GRAPH_EDGE_TYPES

    def validate_confidence(self) -> bool:
        return self.confidence is None or self.confidence in GRAPH_EDGE_CONFIDENCE

    def to_metadata(self) -> dict[str, Any]:
        """Metadata z wtopionymi polami pochodzenia (pola jawne wygrywają)."""
        merged = dict(self.metadata)
        if self.confidence is not None:
            merged["confidence"] = self.confidence
        if self.source_relation is not None:
            merged["source_relation"] = self.source_relation
        return merged


class GraphEdgeResponse(BaseModel):
    """Odpowiedź z danymi krawędzi. confidence/source_relation wyciągane z metadata."""

    source_id: str
    target_id: str
    type: str
    confidence: str | None = None
    source_relation: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        if self.confidence is None:
            self.confidence = self.metadata.get("confidence")
        if self.source_relation is None:
            self.source_relation = self.metadata.get("source_relation")


class GraphSearchResult(BaseModel):
    """Wynik wyszukiwania w grafie."""

    nodes: list[GraphNodeResponse] = Field(default_factory=list)
    edges: list[GraphEdgeResponse] = Field(default_factory=list)
