"""Dashboard -- modul polaczen (graf, node'y, edge'y)."""

from __future__ import annotations

import contextlib
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.constants import (
    GRAPH_EDGE_LABELS,
    GRAPH_EDGE_TYPES,
    GRAPH_NODE_LABELS,
    GRAPH_NODE_TYPES,
)
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.services import graph as graph_service

from .helpers import _get_user_id, flash, render_project_page

router = APIRouter(prefix="/dashboard", tags=["connections"])


async def _get_project(slug: str, db: AsyncSession) -> Project | None:
    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    return result.scalar_one_or_none()


# --- Widok glowny (wizualizacja grafu) ---


@router.get(
    "/{slug}/connections/",
    response_class=HTMLResponse,
    response_model=None,
)
async def connections_index(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse("/auth/login", status_code=302)
    project = await _get_project(slug, db)
    if not project:
        return RedirectResponse("/dashboard/", status_code=302)

    # Sprawdz czy graf jest dostepny
    graph_enabled = graph_service.is_enabled() and graph_service._driver is not None

    stats = None
    if graph_enabled:
        with contextlib.suppress(Exception):
            stats = await graph_service.get_stats(project.id)

    return await render_project_page(
        request,
        "dashboard/connections/index.html",
        {
            "project": project,
            "active_module": "connections",
            "graph_enabled": graph_enabled,
            "stats": stats,
        },
        db,
    )


# --- Lista node'ow ---


@router.get(
    "/{slug}/connections/nodes",
    response_class=HTMLResponse,
    response_model=None,
)
async def node_list(
    request: Request,
    slug: str,
    type: str | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse("/auth/login", status_code=302)
    project = await _get_project(slug, db)
    if not project:
        return RedirectResponse("/dashboard/", status_code=302)

    nodes: list[dict[str, Any]] = []
    graph_enabled = graph_service.is_enabled() and graph_service._driver is not None
    if graph_enabled:
        with contextlib.suppress(Exception):
            nodes = await graph_service.list_nodes(project.id, type_filter=type, search=search)

    return await render_project_page(
        request,
        "dashboard/connections/nodes.html",
        {
            "project": project,
            "active_module": "connections",
            "nodes": nodes,
            "node_types": GRAPH_NODE_TYPES,
            "node_labels": GRAPH_NODE_LABELS,
            "current_type": type,
            "current_search": search or "",
            "graph_enabled": graph_enabled,
        },
        db,
    )


# --- Tworzenie node'a ---


@router.get(
    "/{slug}/connections/nodes/create",
    response_class=HTMLResponse,
    response_model=None,
)
async def node_create_form(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse("/auth/login", status_code=302)
    project = await _get_project(slug, db)
    if not project:
        return RedirectResponse("/dashboard/", status_code=302)

    graph_enabled = graph_service.is_enabled() and graph_service._driver is not None

    return await render_project_page(
        request,
        "dashboard/connections/create_node.html",
        {
            "project": project,
            "active_module": "connections",
            "node_types": GRAPH_NODE_TYPES,
            "node_labels": GRAPH_NODE_LABELS,
            "graph_enabled": graph_enabled,
        },
        db,
    )


@router.post("/{slug}/connections/nodes/create", response_model=None)
async def node_create(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse("/auth/login", status_code=302)
    project = await _get_project(slug, db)
    if not project:
        return RedirectResponse("/dashboard/", status_code=302)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    node_type = str(form.get("type", ""))
    file_path = str(form.get("file_path", "")).strip() or None
    line_number_str = str(form.get("line_number", "")).strip()
    try:
        line_number = int(line_number_str) if line_number_str else None
    except ValueError:
        line_number = None

    if not name:
        flash(request, "Nazwa jest wymagana", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/connections/nodes/create", status_code=303)

    if node_type not in GRAPH_NODE_TYPES:
        flash(request, "Nieprawidlowy typ node'a", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/connections/nodes/create", status_code=303)

    try:
        await graph_service.create_node(
            project.id,
            {
                "name": name,
                "type": node_type,
                "file_path": file_path,
                "line_number": line_number,
                "metadata": {},
            },
        )
        flash(request, f"Node '{name}' utworzony")
    except Exception:
        flash(request, "Blad tworzenia node'a", "error")

    return RedirectResponse(url=f"/dashboard/{slug}/connections/nodes", status_code=303)


# --- Tworzenie edge'a ---


@router.get(
    "/{slug}/connections/edges/create",
    response_class=HTMLResponse,
    response_model=None,
)
async def edge_create_form(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse("/auth/login", status_code=302)
    project = await _get_project(slug, db)
    if not project:
        return RedirectResponse("/dashboard/", status_code=302)

    nodes: list[dict[str, Any]] = []
    graph_enabled = graph_service.is_enabled() and graph_service._driver is not None
    if graph_enabled:
        with contextlib.suppress(Exception):
            nodes = await graph_service.list_nodes(project.id)

    return await render_project_page(
        request,
        "dashboard/connections/create_edge.html",
        {
            "project": project,
            "active_module": "connections",
            "nodes": nodes,
            "edge_types": GRAPH_EDGE_TYPES,
            "edge_labels": GRAPH_EDGE_LABELS,
            "graph_enabled": graph_enabled,
        },
        db,
    )


@router.post("/{slug}/connections/edges/create", response_model=None)
async def edge_create(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse("/auth/login", status_code=302)
    project = await _get_project(slug, db)
    if not project:
        return RedirectResponse("/dashboard/", status_code=302)

    form = await request.form()
    source_id = str(form.get("source_id", "")).strip()
    target_id = str(form.get("target_id", "")).strip()
    edge_type = str(form.get("type", ""))

    if not source_id or not target_id:
        flash(request, "Wybierz node zrodlowy i docelowy", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/connections/edges/create", status_code=303)

    if edge_type not in GRAPH_EDGE_TYPES:
        flash(request, "Nieprawidlowy typ krawedzi", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/connections/edges/create", status_code=303)

    try:
        result = await graph_service.create_edge(project.id, source_id, target_id, edge_type)
        if result is None:
            flash(
                request,
                "Nie znaleziono node'ow zrodlowego lub docelowego",
                "error",
            )
        else:
            flash(request, "Krawedz utworzona")
    except Exception:
        flash(request, "Blad tworzenia krawedzi", "error")

    return RedirectResponse(url=f"/dashboard/{slug}/connections/", status_code=303)


# --- Usuwanie node'a ---


@router.post("/{slug}/connections/nodes/{node_id}/delete", response_model=None)
async def node_delete(
    request: Request,
    slug: str,
    node_id: str,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse("/auth/login", status_code=302)
    project = await _get_project(slug, db)
    if not project:
        return RedirectResponse("/dashboard/", status_code=302)

    try:
        deleted = await graph_service.delete_node(project.id, node_id)
        if deleted:
            flash(request, "Node usuniety")
        else:
            flash(request, "Nie znaleziono node'a", "error")
    except Exception:
        flash(request, "Blad usuwania node'a", "error")

    return RedirectResponse(url=f"/dashboard/{slug}/connections/nodes", status_code=303)


# --- API endpoint dla wizualizacji ---


@router.get("/{slug}/connections/api/graph", response_model=None)
async def graph_api(
    request: Request,
    slug: str,
    type: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    user_id = _get_user_id(request)
    if not user_id:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    project = await _get_project(slug, db)
    if not project:
        return JSONResponse({"error": "Not found"}, status_code=404)

    graph_enabled = graph_service.is_enabled() and graph_service._driver is not None
    if not graph_enabled:
        return JSONResponse({"nodes": [], "edges": []})

    try:
        data = await graph_service.get_graph(project.id, type_filter=type)
        return JSONResponse(data)
    except Exception:
        return JSONResponse({"nodes": [], "edges": []})
