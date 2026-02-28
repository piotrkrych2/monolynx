"""Schematy Pydantic dla modulu grafu (polaczenia)."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from monolynx.constants import GRAPH_EDGE_TYPES, GRAPH_NODE_TYPES


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
    """Tworzenie krawędzi (edge) między node'ami."""

    source_id: str
    target_id: str
    type: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def validate_type(self) -> bool:
        return self.type in GRAPH_EDGE_TYPES


class GraphEdgeResponse(BaseModel):
    """Odpowiedź z danymi krawędzi."""

    source_id: str
    target_id: str
    type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphSearchResult(BaseModel):
    """Wynik wyszukiwania w grafie."""

    nodes: list[GraphNodeResponse] = Field(default_factory=list)
    edges: list[GraphEdgeResponse] = Field(default_factory=list)
