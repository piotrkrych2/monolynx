#!/usr/bin/env python3
"""Sync code dependency graph from AST analysis to Monolynx platform.

Analyzes Python source files in the target directory and synchronizes
the dependency graph with Monolynx via the MCP Streamable HTTP API.

Edge management:
- Fully managed (create + delete): CONTAINS, IMPORTS, INHERITS, USES
- Append-only (create, never delete): CALLS
- Not managed: IMPLEMENTS (manual)

Deleting a node cascades to its edges (DETACH DELETE in Neo4j).

Usage:
  python cicd/sync_graph.py --dry-run --verbose
  python cicd/sync_graph.py --project-slug monolynx
  MONOLYNX_URL=https://open.monolynx.com MONOLYNX_GRAPH_TOKEN=osk_xxx python cicd/sync_graph.py
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("sync_graph")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NODE_TYPES = {"File", "Class", "Method", "Function", "Const", "Module"}

# Fully managed edge types (create + delete)
MANAGED_EDGE_TYPES = {"CONTAINS", "IMPORTS", "INHERITS", "USES"}

# Append-only edge types (create, never delete)
APPEND_EDGE_TYPES = {"CALLS"}

# File path (relative to src dir) -> function name prefix
PREFIX_MAP: dict[str, str] = {
    "services/graph.py": "graph",
    "services/embeddings.py": "emb",
    "services/wiki.py": "svc",
    "services/monitoring.py": "mon",
    "services/sprint.py": "svc",
    "services/time_tracking.py": "svc",
    "services/auth.py": "svc",
    "services/email.py": "svc",
    "services/event_processor.py": "svc",
    "services/fingerprint.py": "svc",
    "services/mcp_auth.py": "svc",
    "services/minio_client.py": "minio",
    "services/monitor_loop.py": "mon",
    "services/sidebar.py": "svc",
    "services/ticket_numbering.py": "svc",
    "dashboard/scrum.py": "scrum",
    "dashboard/wiki.py": "wiki",
    "dashboard/monitoring.py": "mon",
    "dashboard/connections.py": "conn",
    "dashboard/auth.py": "dash",
    "dashboard/projects.py": "dash",
    "dashboard/settings.py": "dash",
    "dashboard/users.py": "dash",
    "dashboard/profile.py": "dash",
    "dashboard/sentry.py": "500ki",
    "dashboard/reports.py": "dash",
    "dashboard/helpers.py": "dash",
    "mcp_server.py": "mcp",
    "api/events.py": "api",
    "api/issues.py": "api",
    "cli.py": "cli",
    "worker.py": "cli",
    "config.py": "cfg",
    "constants.py": "const",
    "database.py": "db",
    "main.py": "app",
}

# Directory -> module name
MODULE_MAP: dict[str, str] = {
    "services": "Services",
    "dashboard": "Dashboard",
    "models": "Models",
    "api": "API",
    "schemas": "Schemas",
}

# Package name for internal import detection
PACKAGE_NAME = "monolynx"

# Batch size for bulk operations
BATCH_SIZE = 50

# HTTP retry settings
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NodeKey:
    type: str
    name: str


@dataclass
class NodeDef:
    name: str
    type: str
    file_path: str | None = None
    line_number: int | None = None

    @property
    def key(self) -> NodeKey:
        return NodeKey(self.type, self.name)


@dataclass(frozen=True)
class EdgeKey:
    source_name: str
    target_name: str
    edge_type: str


@dataclass
class EdgeDef:
    source_name: str
    target_name: str
    edge_type: str

    @property
    def key(self) -> EdgeKey:
        return EdgeKey(self.source_name, self.target_name, self.edge_type)


# ---------------------------------------------------------------------------
# Monolynx MCP Client
# ---------------------------------------------------------------------------


class MonolynxClient:
    """HTTP client for Monolynx MCP Streamable HTTP API (JSON-RPC)."""

    def __init__(self, url: str, token: str, project_slug: str) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.project_slug = project_slug
        self.session_id: str | None = None
        self._request_id = 0
        self._ssl_ctx = ssl.create_default_context()
        self._initialize()

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _http_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send JSON-RPC request to Monolynx MCP endpoint with retries."""
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        data = json.dumps(payload).encode("utf-8")
        endpoint = f"{self.url}/mcp/"

        last_error: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                req = urllib.request.Request(
                    endpoint,
                    data=data,
                    headers=headers,
                    method="POST",
                )
                ctx = self._ssl_ctx if endpoint.startswith("https") else None
                with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
                    if not self.session_id:
                        sid = resp.headers.get("Mcp-Session-Id")
                        if sid:
                            self.session_id = sid
                            log.debug("MCP session: %s", sid)
                    body: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
                    return body
            except urllib.error.HTTPError as e:
                last_error = e
                body_text = e.read().decode("utf-8", errors="replace")
                log.warning(
                    "HTTP %d on attempt %d/%d: %s",
                    e.code,
                    attempt,
                    MAX_RETRIES,
                    body_text[:200],
                )
                if e.code < 500:
                    raise RuntimeError(
                        f"MCP HTTP error {e.code}: {body_text[:500]}"
                    ) from e
            except urllib.error.URLError as e:
                last_error = e
                log.warning(
                    "Connection error on attempt %d/%d: %s",
                    attempt,
                    MAX_RETRIES,
                    e.reason,
                )

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

        raise RuntimeError(f"MCP request failed after {MAX_RETRIES} attempts") from last_error

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Execute a JSON-RPC method and return the result."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._next_id(),
        }
        body = self._http_request(payload)

        if "error" in body:
            raise RuntimeError(f"MCP RPC error: {body['error']}")

        return body.get("result")

    def _initialize(self) -> None:
        """Initialize MCP session."""
        log.info("Connecting to Monolynx MCP at %s ...", self.url)
        self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "sync_graph", "version": "2.0"},
        })
        log.info("MCP session initialized (session_id=%s)", self.session_id)

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool and parse the JSON result from content[0].text."""
        result = self._rpc("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        content = result.get("content", [])
        if not content:
            raise RuntimeError(f"Tool {name} returned empty content")

        text = content[0].get("text", "")
        if result.get("isError"):
            raise RuntimeError(f"Tool {name} error: {text}")

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # -- Graph operations -----------------------------------------------------

    def query_graph(self, limit: int = 1000) -> dict[str, Any]:
        """Get current graph state (nodes + edges)."""
        return self._call_tool("query_graph", {
            "project_slug": self.project_slug,
            "limit": limit,
        })

    def bulk_create_nodes(self, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        """Create nodes in batch. Returns {created, errors, nodes}."""
        return self._call_tool("bulk_create_graph_nodes", {
            "project_slug": self.project_slug,
            "nodes": nodes,
        })

    def bulk_create_edges(self, edges: list[dict[str, Any]]) -> dict[str, Any]:
        """Create edges in batch. Returns {created, skipped, errors}."""
        return self._call_tool("bulk_create_graph_edges", {
            "project_slug": self.project_slug,
            "edges": edges,
        })

    def delete_node(self, node_id: str) -> dict[str, Any]:
        """Delete a node and cascade its edges."""
        return self._call_tool("delete_graph_node", {
            "project_slug": self.project_slug,
            "node_id": node_id,
        })

    def delete_edge(
        self, source_id: str, target_id: str, edge_type: str
    ) -> dict[str, Any]:
        """Delete an edge between two nodes."""
        return self._call_tool("delete_graph_edge", {
            "project_slug": self.project_slug,
            "source_id": source_id,
            "target_id": target_id,
            "type": edge_type,
        })


# ---------------------------------------------------------------------------
# AST Analysis
# ---------------------------------------------------------------------------


def _get_prefix(rel_path: str) -> str:
    return PREFIX_MAP.get(rel_path, "")


def _prefixed(prefix: str, name: str) -> str:
    return f"{prefix}:{name}" if prefix else name


def _is_constant(name: str) -> bool:
    return bool(re.match(r"^[A-Z][A-Z0-9_]+$", name))


class ASTAnalyzer:
    """Two-pass AST analysis of the entire codebase."""

    def __init__(self, src_dir: Path) -> None:
        self.src_dir = src_dir
        self.nodes: dict[NodeKey, NodeDef] = {}
        self.edges: dict[EdgeKey, EdgeDef] = {}

        # Map: (file_rel_path, bare_name) -> prefixed_name
        # Built in pass 1, used in pass 2 for CALLS
        self.func_registry: dict[tuple[str, str], str] = {}

        # Set of known class names (to filter CALLS — skip constructors)
        self.known_classes: set[str] = set()

        # Pending USES edges (collected in pass 1, flushed after all classes known)
        self._pending_uses: list[tuple[str, str]] = []

    def analyze(self) -> tuple[dict[NodeKey, NodeDef], dict[EdgeKey, EdgeDef]]:
        py_files = sorted(self.src_dir.rglob("*.py"))

        # Pass 1: structure (nodes, CONTAINS, IMPORTS, INHERITS)
        for py_file in py_files:
            rel_path = str(py_file.relative_to(self.src_dir))
            # Skip empty files (e.g. __init__.py with no code)
            content = py_file.read_text(encoding="utf-8").strip()
            if not content:
                log.debug("Skipping empty file: %s", rel_path)
                continue
            self._pass1_structure(py_file, rel_path)

        # Add Module nodes
        self._add_modules()

        # Flush pending USES edges (both ends must be known classes)
        for source, target in self._pending_uses:
            if source in self.known_classes and target in self.known_classes:
                self._add_edge(EdgeDef(source, target, "USES"))

        # Pass 2: CALLS
        for py_file in py_files:
            rel_path = str(py_file.relative_to(self.src_dir))
            self._pass2_calls(py_file, rel_path)

        return self.nodes, self.edges

    def _add_node(self, node: NodeDef) -> None:
        key = node.key
        if key in self.nodes:
            log.debug("Duplicate node: %s", node)
            return
        self.nodes[key] = node

    def _add_edge(self, edge: EdgeDef) -> None:
        self.edges[edge.key] = edge

    # -- Pass 1 ---------------------------------------------------------------

    def _pass1_structure(self, file_path: Path, rel_path: str) -> None:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError):
            log.warning("Cannot parse: %s", rel_path)
            return

        prefix = _get_prefix(rel_path)

        # File node
        self._add_node(NodeDef(
            name=rel_path,
            type="File",
            file_path=f"src/{PACKAGE_NAME}/{rel_path}",
        ))

        for node in ast.iter_child_nodes(tree):
            # --- Classes ---
            if isinstance(node, ast.ClassDef):
                self._extract_class(node, rel_path, prefix)

            # --- Top-level functions ---
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_name = _prefixed(prefix, node.name)
                self._add_node(NodeDef(
                    name=func_name,
                    type="Function",
                    file_path=f"src/{PACKAGE_NAME}/{rel_path}",
                    line_number=node.lineno,
                ))
                self._add_edge(EdgeDef(rel_path, func_name, "CONTAINS"))
                self.func_registry[(rel_path, node.name)] = func_name

            # --- Constants ---
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and _is_constant(target.id):
                        self._add_node(NodeDef(
                            name=target.id,
                            type="Const",
                            file_path=f"src/{PACKAGE_NAME}/{rel_path}",
                            line_number=node.lineno,
                        ))
                        self._add_edge(EdgeDef(rel_path, target.id, "CONTAINS"))

        # --- Internal imports (file -> file) ---
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(f"{PACKAGE_NAME}."):
                target_rel = self._resolve_import_path(node.module)
                if target_rel:
                    self._add_edge(EdgeDef(rel_path, target_rel, "IMPORTS"))

    def _extract_class(self, node: ast.ClassDef, rel_path: str, prefix: str) -> None:
        class_name = node.name
        self._add_node(NodeDef(
            name=class_name,
            type="Class",
            file_path=f"src/{PACKAGE_NAME}/{rel_path}",
            line_number=node.lineno,
        ))
        self._add_edge(EdgeDef(rel_path, class_name, "CONTAINS"))
        self.known_classes.add(class_name)

        # Inheritance
        for base in node.bases:
            base_name = None
            if isinstance(base, ast.Name):
                base_name = base.id
            elif isinstance(base, ast.Attribute):
                base_name = base.attr
            if base_name and base_name not in ("object", "BaseModel", "Base"):
                self._add_edge(EdgeDef(class_name, base_name, "INHERITS"))

        # Methods
        for item in ast.iter_child_nodes(node):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name.startswith("__") and item.name.endswith("__"):
                    continue
                method_name = _prefixed(prefix, item.name) if prefix else f"{class_name}.{item.name}"
                self._add_node(NodeDef(
                    name=method_name,
                    type="Method",
                    file_path=f"src/{PACKAGE_NAME}/{rel_path}",
                    line_number=item.lineno,
                ))
                self._add_edge(EdgeDef(class_name, method_name, "CONTAINS"))
                self.func_registry[(rel_path, item.name)] = method_name

        # SQLAlchemy relationship() USES edges
        for item in ast.iter_child_nodes(node):
            if not isinstance(item, ast.AnnAssign):
                continue
            if item.annotation is None or item.value is None:
                continue
            # Check that value is a relationship() call
            if not (
                isinstance(item.value, ast.Call)
                and isinstance(item.value.func, ast.Name)
                and item.value.func.id == "relationship"
            ):
                continue
            ref_classes = self._extract_mapped_class_names(item.annotation)
            for ref_class in ref_classes:
                self._pending_uses.append((class_name, ref_class))

    def _extract_mapped_class_names(self, annotation: ast.expr) -> list[str]:
        """Extract class names from Mapped[...] annotation."""
        if not (
            isinstance(annotation, ast.Subscript)
            and isinstance(annotation.value, ast.Name)
            and annotation.value.id == "Mapped"
        ):
            return []
        return self._extract_class_names_from_type(annotation.slice)

    def _extract_class_names_from_type(self, node: ast.expr) -> list[str]:
        """Recursively extract class names from type annotation nodes.

        Handles:
        - Name("Project") → ["Project"]
        - Subscript(Name("list"), Name("Event")) → ["Event"]
        - BinOp(Name("Sprint"), BitOr, Constant(None)) → ["Sprint"]
        """
        if isinstance(node, ast.Name):
            # Only return capitalized names (class references), skip builtins
            if node.id[:1].isupper():
                return [node.id]
            return []

        if isinstance(node, ast.Subscript):
            # e.g. list[Event] or Optional[Project]
            return self._extract_class_names_from_type(node.slice)

        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
            # e.g. Sprint | None
            return (
                self._extract_class_names_from_type(node.left)
                + self._extract_class_names_from_type(node.right)
            )

        if isinstance(node, ast.Constant):
            return []

        return []

    def _resolve_import_path(self, module: str) -> str | None:
        """Convert 'monolynx.services.graph' to 'services/graph.py'."""
        parts = module.replace(f"{PACKAGE_NAME}.", "", 1).split(".")
        candidate = "/".join(parts) + ".py"
        if (self.src_dir / candidate).exists():
            return candidate
        # Maybe it's a package (directory with __init__.py)
        candidate_init = "/".join(parts) + "/__init__.py"
        if (self.src_dir / candidate_init).exists():
            return candidate_init
        return None

    def _add_modules(self) -> None:
        """Add Module nodes and CONTAINS edges (Module -> File)."""
        seen_modules: set[str] = set()
        root_files: list[str] = []

        for key in list(self.nodes):
            if key.type != "File":
                continue
            rel_path = key.name
            parts = rel_path.split("/")
            if len(parts) > 1:
                dir_name = parts[0]
                if dir_name in MODULE_MAP:
                    module_name = MODULE_MAP[dir_name]
                    seen_modules.add(module_name)
                    self._add_edge(EdgeDef(module_name, rel_path, "CONTAINS"))
            else:
                root_files.append(rel_path)

        for module_name in seen_modules:
            self._add_node(NodeDef(name=module_name, type="Module"))

        if root_files:
            self._add_node(NodeDef(name="Core", type="Module"))
            for f in root_files:
                self._add_edge(EdgeDef("Core", f, "CONTAINS"))

    # -- Pass 2: CALLS --------------------------------------------------------

    def _pass2_calls(self, file_path: Path, rel_path: str) -> None:
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError):
            return

        # Build import maps for this file:
        # direct_imports: local_name -> (target_file, bare_name)
        # module_imports: alias -> target_file
        direct_imports: dict[str, tuple[str, str]] = {}
        module_imports: dict[str, str] = {}

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if not node.module or not node.module.startswith(f"{PACKAGE_NAME}."):
                continue

            target_file = self._resolve_import_path(node.module)
            if not target_file:
                continue

            for alias in node.names:
                imported_name = alias.name
                local_name = alias.asname or imported_name
                # Check if this imports a sub-module or an object
                candidate_file = self._resolve_import_path(node.module + "." + imported_name)
                if candidate_file:
                    module_imports[local_name] = candidate_file
                else:
                    direct_imports[local_name] = (target_file, imported_name)

        # Search function/method bodies for call expressions
        for top_node in ast.iter_child_nodes(tree):
            # Top-level functions
            if isinstance(top_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                caller = self.func_registry.get((rel_path, top_node.name))
                if caller:
                    self._find_calls_in(top_node, caller, direct_imports, module_imports)

            # Methods in classes
            elif isinstance(top_node, ast.ClassDef):
                for item in ast.iter_child_nodes(top_node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if item.name.startswith("__") and item.name.endswith("__"):
                            continue
                        caller = self.func_registry.get((rel_path, item.name))
                        if caller:
                            self._find_calls_in(item, caller, direct_imports, module_imports)

    def _find_calls_in(
        self,
        func_node: ast.FunctionDef | ast.AsyncFunctionDef,
        caller_name: str,
        direct_imports: dict[str, tuple[str, str]],
        module_imports: dict[str, str],
    ) -> None:
        for node in ast.walk(func_node):
            if not isinstance(node, ast.Call):
                continue

            target_name = self._resolve_call_target(node, direct_imports, module_imports)
            if target_name and target_name != caller_name:
                if (
                    NodeKey("Function", target_name) in self.nodes
                    or NodeKey("Method", target_name) in self.nodes
                ):
                    self._add_edge(EdgeDef(caller_name, target_name, "CALLS"))

    def _resolve_call_target(
        self,
        call_node: ast.Call,
        direct_imports: dict[str, tuple[str, str]],
        module_imports: dict[str, str],
    ) -> str | None:
        func = call_node.func

        # Simple call: foo()
        if isinstance(func, ast.Name):
            local_name = func.id
            if local_name in direct_imports:
                target_file, bare_name = direct_imports[local_name]
                return self.func_registry.get((target_file, bare_name))
            return None

        # Attribute call: module.foo()
        if isinstance(func, ast.Attribute):
            attr_name = func.attr
            value = func.value

            if isinstance(value, ast.Name):
                obj_name = value.id
                if obj_name in module_imports:
                    target_file = module_imports[obj_name]
                    return self.func_registry.get((target_file, attr_name))
            return None

        return None


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def compute_diff(
    desired_nodes: dict[NodeKey, NodeDef],
    current_nodes: dict[NodeKey, dict[str, Any]],
    desired_edges: dict[EdgeKey, EdgeDef],
    current_edges: dict[EdgeKey, dict[str, Any]],
) -> tuple[list[NodeDef], list[str], list[EdgeDef], list[dict[str, Any]]]:
    """Compute diff between desired and current graph state.

    Returns:
        (nodes_to_create, node_ids_to_delete, edges_to_create, edges_to_delete)
    """
    # -- Nodes --
    nodes_to_create = [
        desired_nodes[k] for k in desired_nodes if k not in current_nodes
    ]
    node_ids_to_delete = [
        current_nodes[k]["id"] for k in current_nodes if k not in desired_nodes
    ]

    # -- Edges --
    edges_to_create: list[EdgeDef] = []
    edges_to_delete: list[dict[str, Any]] = []

    for k, edge in desired_edges.items():
        if k not in current_edges:
            edges_to_create.append(edge)

    for k, edge_data in current_edges.items():
        if k not in desired_edges:
            # Only delete managed edges — CALLS are append-only
            if edge_data["edge_type"] in MANAGED_EDGE_TYPES:
                edges_to_delete.append(edge_data)

    return nodes_to_create, node_ids_to_delete, edges_to_create, edges_to_delete


# ---------------------------------------------------------------------------
# Current state loader
# ---------------------------------------------------------------------------


def load_current_state(
    client: MonolynxClient,
) -> tuple[dict[NodeKey, dict[str, Any]], dict[EdgeKey, dict[str, Any]]]:
    """Fetch current graph state from Monolynx and build keyed maps."""
    log.info("=== Fetching current graph from Monolynx ===")
    data = client.query_graph(limit=1000)

    raw_nodes = data.get("nodes", [])
    raw_edges = data.get("edges", [])

    # Build id -> name map for edge resolution
    id_to_name: dict[str, str] = {}
    current_nodes: dict[NodeKey, dict[str, Any]] = {}

    for n in raw_nodes:
        nid = n["id"]
        name = n["name"]
        ntype = n["type"]
        id_to_name[nid] = name
        key = NodeKey(ntype, name)
        current_nodes[key] = {
            "id": nid,
            "name": name,
            "type": ntype,
            "file_path": n.get("file_path"),
        }

    current_edges: dict[EdgeKey, dict[str, Any]] = {}
    for e in raw_edges:
        src_name = id_to_name.get(e["source_id"], "")
        tgt_name = id_to_name.get(e["target_id"], "")
        if src_name and tgt_name:
            key = EdgeKey(src_name, tgt_name, e["type"])
            current_edges[key] = {
                "source_id": e["source_id"],
                "target_id": e["target_id"],
                "source_name": src_name,
                "target_name": tgt_name,
                "edge_type": e["type"],
            }

    log.info("Current state: %d nodes, %d edges", len(current_nodes), len(current_edges))
    return current_nodes, current_edges


# ---------------------------------------------------------------------------
# Apply changes
# ---------------------------------------------------------------------------


def apply_changes(
    client: MonolynxClient,
    nodes_to_create: list[NodeDef],
    node_ids_to_delete: list[str],
    edges_to_create: list[EdgeDef],
    edges_to_delete: list[dict[str, Any]],
    current_nodes: dict[NodeKey, dict[str, Any]],
) -> None:
    """Apply diff to Monolynx graph via MCP API."""
    # Build name -> id map from current nodes
    name_to_id: dict[str, str] = {v["name"]: v["id"] for v in current_nodes.values()}

    # 1. Delete nodes (cascade removes their edges)
    if node_ids_to_delete:
        log.info("Deleting %d nodes...", len(node_ids_to_delete))
        deleted = 0
        for nid in node_ids_to_delete:
            try:
                client.delete_node(nid)
                deleted += 1
            except RuntimeError as e:
                log.warning("Failed to delete node %s: %s", nid, e)
        log.info("Deleted %d nodes", deleted)

        # Remove deleted nodes from name_to_id
        deleted_ids = set(node_ids_to_delete)
        name_to_id = {n: i for n, i in name_to_id.items() if i not in deleted_ids}

    # 2. Delete managed edges
    if edges_to_delete:
        log.info("Deleting %d edges...", len(edges_to_delete))
        deleted = 0
        for ed in edges_to_delete:
            try:
                client.delete_edge(ed["source_id"], ed["target_id"], ed["edge_type"])
                deleted += 1
            except RuntimeError as e:
                log.warning(
                    "Failed to delete edge %s -[%s]-> %s: %s",
                    ed["source_name"], ed["edge_type"], ed["target_name"], e,
                )
        log.info("Deleted %d edges", deleted)

    # 3. Create nodes in batches
    if nodes_to_create:
        log.info("Creating %d nodes...", len(nodes_to_create))
        total_created = 0
        for i in range(0, len(nodes_to_create), BATCH_SIZE):
            batch = nodes_to_create[i : i + BATCH_SIZE]
            payload = [
                {
                    "type": n.type,
                    "name": n.name,
                    "file_path": n.file_path,
                    "line_number": n.line_number,
                }
                for n in batch
            ]
            result = client.bulk_create_nodes(payload)
            created_count = result.get("created", 0)
            total_created += created_count

            # Capture new IDs for edge creation
            for created_node in result.get("nodes", []):
                name_to_id[created_node["name"]] = created_node["id"]

            errors = result.get("errors", [])
            if errors:
                for err in errors:
                    log.warning("  Node creation error: %s", err)

        log.info("Created %d nodes", total_created)

    # 4. Create edges in batches
    if edges_to_create:
        log.info("Creating %d edges...", len(edges_to_create))
        edge_payloads: list[dict[str, Any]] = []
        skipped_missing = 0

        for edge in edges_to_create:
            src_id = name_to_id.get(edge.source_name)
            tgt_id = name_to_id.get(edge.target_name)
            if src_id and tgt_id:
                edge_payloads.append({
                    "source_id": src_id,
                    "target_id": tgt_id,
                    "type": edge.edge_type,
                })
            else:
                skipped_missing += 1
                log.debug(
                    "  Skipping edge (missing node): %s -[%s]-> %s",
                    edge.source_name, edge.edge_type, edge.target_name,
                )

        total_created = 0
        for i in range(0, len(edge_payloads), BATCH_SIZE):
            batch = edge_payloads[i : i + BATCH_SIZE]
            result = client.bulk_create_edges(batch)
            total_created += result.get("created", 0)

            errors = result.get("errors", [])
            if errors:
                for err in errors:
                    log.warning("  Edge creation error: %s", err)

        log.info("Created %d edges (skipped %d missing)", total_created, skipped_missing)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync code dependency graph (AST -> Monolynx)",
    )
    parser.add_argument(
        "--monolynx-url",
        default=None,
        help="Monolynx instance URL (default: env MONOLYNX_URL or https://open.monolynx.com)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token for MCP API (default: env MONOLYNX_GRAPH_TOKEN)",
    )
    parser.add_argument(
        "--project-slug",
        default=None,
        help="Project slug on Monolynx (default: env MONOLYNX_PROJECT_SLUG)",
    )
    parser.add_argument(
        "--src-dir",
        default="src/monolynx",
        help="Source directory to analyze (default: src/monolynx)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show diff, do not apply changes",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)

    monolynx_url = args.monolynx_url or os.environ.get("MONOLYNX_URL", "https://open.monolynx.com")
    token = args.token or os.environ.get("MONOLYNX_GRAPH_TOKEN", "")
    project_slug = args.project_slug or os.environ.get("MONOLYNX_PROJECT_SLUG", "")

    if not token:
        log.error("Token required. Use --token or set MONOLYNX_GRAPH_TOKEN env var.")
        sys.exit(1)
    if not project_slug:
        log.error("Project slug required. Use --project-slug or set MONOLYNX_PROJECT_SLUG env var.")
        sys.exit(1)

    src_dir = Path(args.src_dir)
    if not src_dir.exists():
        log.error("Source directory does not exist: %s", src_dir)
        sys.exit(1)

    # 1. AST analysis
    log.info("=== Pass 1+2: AST analysis (%s) ===", src_dir)
    analyzer = ASTAnalyzer(src_dir)
    desired_nodes, desired_edges = analyzer.analyze()
    log.info("AST: %d nodes, %d edges", len(desired_nodes), len(desired_edges))

    # Summary per type
    node_counts: dict[str, int] = {}
    for k in desired_nodes:
        node_counts[k.type] = node_counts.get(k.type, 0) + 1
    edge_counts: dict[str, int] = {}
    for k in desired_edges:
        edge_counts[k.edge_type] = edge_counts.get(k.edge_type, 0) + 1
    log.info("  Nodes: %s", dict(sorted(node_counts.items())))
    log.info("  Edges: %s", dict(sorted(edge_counts.items())))

    # 2. Fetch current state from Monolynx
    client = MonolynxClient(monolynx_url, token, project_slug)
    current_nodes, current_edges = load_current_state(client)

    # 3. Compute diff
    log.info("=== Computing diff ===")
    nodes_to_create, node_ids_to_delete, edges_to_create, edges_to_delete = compute_diff(
        desired_nodes, current_nodes, desired_edges, current_edges,
    )

    log.info("Diff:")
    log.info("  Nodes: +%d, -%d", len(nodes_to_create), len(node_ids_to_delete))
    log.info("  Edges: +%d, -%d", len(edges_to_create), len(edges_to_delete))

    if args.verbose:
        for n in nodes_to_create:
            log.debug("  + Node [%s] %s", n.type, n.name)
        for nid in node_ids_to_delete:
            name = next(
                (v["name"] for v in current_nodes.values() if v["id"] == nid),
                nid,
            )
            log.debug("  - Node %s", name)
        for e in edges_to_create:
            log.debug("  + Edge %s -[%s]-> %s", e.source_name, e.edge_type, e.target_name)
        for e in edges_to_delete:
            log.debug(
                "  - Edge %s -[%s]-> %s",
                e["source_name"], e["edge_type"], e["target_name"],
            )

    if not nodes_to_create and not node_ids_to_delete and not edges_to_create and not edges_to_delete:
        log.info("Graph is up to date — no changes needed.")
        return

    # 4. Apply changes
    if args.dry_run:
        log.info("=== DRY RUN — changes NOT applied ===")
        return

    log.info("=== Applying changes ===")
    apply_changes(
        client,
        nodes_to_create,
        node_ids_to_delete,
        edges_to_create,
        edges_to_delete,
        current_nodes,
    )
    log.info("=== Sync complete ===")


if __name__ == "__main__":
    main()
