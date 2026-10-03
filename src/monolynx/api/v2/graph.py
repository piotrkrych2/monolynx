"""API v2 -- graf Połączeń: 5 endpointów odczytu (MON-248).

Odpowiadają narzędziom MCP `list_graph_nodes`, `get_graph_node`, `query_graph`,
`find_graph_path` i `get_graph_stats` i wołają te same funkcje `services/graph.py`.
Zapis grafu (tworzenie, usuwanie, `replace_graph`) zostaje wyłącznie w MCP.

Kolejność sprawdzeń (dependency `graph_project`): token 401, projekt 404, członkostwo 403,
`connections:read` 403, graf wyłączony 503. Awaria bazy grafowej w trakcie wywołania
serwisu (`RuntimeError` ze sterownika albo wyjątek `neo4j.exceptions`) to 503, nigdy 500;
`ValueError` serwisu to 422/404 przez `service_error()`. Wszystkie funkcje serwisu
dostają `project.id` projektu ze sluga, więc dane są zawężone do tego projektu.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Any

from fastapi import APIRouter, Depends, Query
from neo4j.exceptions import DriverError, Neo4jError
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.api.v2.common import not_found, require_permission, service_error
from monolynx.api.v2.deps import get_api_user_and_project
from monolynx.api.v2.errors import ApiV2Error
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.schemas.api_v2.graph import (
    GraphNodeDetailResponse,
    GraphNodeResponse,
    GraphStatsResponse,
    GraphSubgraphResponse,
)
from monolynx.services import graph as graph_service

logger = logging.getLogger("monolynx.api.v2.graph")

router = APIRouter(tags=["graph"])

_GRAPH_DISABLED = "Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)"
_GRAPH_UNAVAILABLE = "Baza grafowa jest niedostepna"


async def graph_project(
    user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> Project:
    user, project = user_and_project
    await require_permission(db, user, project, "connections", "read")
    if not graph_service.is_enabled():
        raise ApiV2Error(status=503, title="Service Unavailable", detail=_GRAPH_DISABLED)
    return project


async def _run[T](call: Awaitable[T]) -> T:
    try:
        return await call
    except ValueError as exc:
        raise service_error(exc) from exc
    except (RuntimeError, DriverError, Neo4jError) as exc:
        logger.exception("Odczyt grafu nie powiodl sie")
        raise ApiV2Error(status=503, title="Service Unavailable", detail=_GRAPH_UNAVAILABLE) from exc


@router.get("/projects/{project_slug}/graph/nodes", response_model=list[GraphNodeResponse])
async def list_graph_nodes(
    project: Project = Depends(graph_project),
    node_type: str | None = Query(None, alias="type"),
    search: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
) -> Any:
    return await _run(graph_service.list_nodes(project.id, type_filter=node_type, search=search, limit=limit))


@router.get("/projects/{project_slug}/graph/nodes/{node_id:path}", response_model=GraphNodeDetailResponse)
async def get_graph_node(
    node_id: str,
    project: Project = Depends(graph_project),
    depth: int = Query(1, ge=1),
    relation_types: list[str] | None = Query(None),
    node_types: list[str] | None = Query(None),
) -> Any:
    node = await _run(graph_service.get_node(project.id, node_id))
    if node is None:
        raise not_found("Node nie istnieje")
    neighbors = await _run(graph_service.get_neighbors(project.id, node_id, depth=depth, relation_types=relation_types, node_types=node_types))
    return {"node": node, "nodes": neighbors["nodes"], "edges": neighbors["edges"], "depth_map": neighbors["depth_map"]}


@router.get("/projects/{project_slug}/graph/query", response_model=GraphSubgraphResponse)
async def query_graph(
    project: Project = Depends(graph_project),
    node_type: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
) -> Any:
    try:
        graph_service.ensure_search_not_blank(search)
    except ValueError as exc:
        raise service_error(exc) from exc
    if search is not None:
        return await _run(graph_service.search_graph(project.id, search=search, type_filter=node_type, limit=limit))
    return await _run(graph_service.get_graph(project.id, type_filter=node_type, limit=limit))


@router.get("/projects/{project_slug}/graph/path", response_model=GraphSubgraphResponse)
async def find_graph_path(
    source_id: str = Query(...),
    target_id: str = Query(...),
    project: Project = Depends(graph_project),
) -> Any:
    return await _run(graph_service.find_path(project.id, source_id, target_id))


@router.get("/projects/{project_slug}/graph/stats", response_model=GraphStatsResponse)
async def get_graph_stats(project: Project = Depends(graph_project)) -> Any:
    return await _run(graph_service.get_stats(project.id))
