#!/usr/bin/env python3
"""Sync the graphify-extracted code graph to Monolynx (Connections module).

Thin mapper: reads graphify-out/graph.json (produced by `graphify update .`,
tree-sitter AST, offline), maps the graphify taxonomy to the Monolynx one
(see wiki page "Mapowanie taksonomii Graphify -> Monolynx", MON-115) and
pushes the result via the `replace_graph` MCP tool (full wipe + insert,
idempotent, MON-116).

No AST analysis here - graphify is the extractor. Stdlib only.

Env / args:
    MONOLYNX_URL           Monolynx base URL (default https://monolynx.com)
    MONOLYNX_GRAPH_TOKEN     API token (osk_...); missing -> skip with exit 0
    MONOLYNX_PROJECT_SLUG  project slug on Monolynx

Missing graphify-out/graph.json -> clear message and exit 0 (never a
traceback): the CI step must stay non-blocking for the host project.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

log = logging.getLogger("sync_graph")

MAX_RETRIES = 3
RETRY_DELAY = 2.0

# Server-side limits of replace_graph (services/graph.py); exceeding them
# usually means .graphifyignore is missing or too permissive.
MAX_NODES = 20_000
MAX_EDGES = 60_000

# graphify relation -> Monolynx edge type (MON-115). Relations absent from
# this map (references, rationale_for, ...) are skipped on purpose: v1 keeps
# the graph code-only.
RELATION_TO_EDGE_TYPE = {
    "calls": "CALLS",
    "indirect_call": "CALLS",
    "contains": "CONTAINS",
    "method": "CONTAINS",
    "imports": "IMPORTS",
    "imports_from": "IMPORTS",
    "re_exports": "IMPORTS",
    "inherits": "INHERITS",
    "uses": "USES",
}


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
                with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
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
                log.warning("HTTP %d on attempt %d/%d: %s", e.code, attempt, MAX_RETRIES, body_text[:200])
                if e.code < 500:
                    raise RuntimeError(f"MCP HTTP error {e.code}: {body_text[:500]}") from e
            except urllib.error.URLError as e:
                last_error = e
                log.warning("Connection error on attempt %d/%d: %s", attempt, MAX_RETRIES, e.reason)

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

        raise RuntimeError(f"MCP request failed after {MAX_RETRIES} attempts") from last_error

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
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
        log.info("Connecting to Monolynx MCP at %s ...", self.url)
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "sync_graph", "version": "3.0"},
            },
        )
        log.info("MCP session initialized (session_id=%s)", self.session_id)

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
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

    def replace_graph(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        clear_first: bool = True,
    ) -> dict[str, Any]:
        """Full graph replacement (wipe + insert). Returns operation stats."""
        return self._call_tool(
            "replace_graph",
            {
                "project_slug": self.project_slug,
                "nodes": nodes,
                "edges": edges,
                "clear_first": clear_first,
            },
        )


# ---------------------------------------------------------------------------
# graph.json -> Monolynx mapping (MON-115)
# ---------------------------------------------------------------------------


def _line_number(source_location: Any) -> int | None:
    """graphify stores locations as 'L42'."""
    if isinstance(source_location, str) and source_location.startswith("L"):
        try:
            return int(source_location[1:])
        except ValueError:
            return None
    return None


def _clean_name(label: str) -> str:
    """'sprint_create()' -> 'sprint_create', '.key()' -> 'key'."""
    return label.removesuffix("()").lstrip(".") or label


def classify_node(node: dict[str, Any], method_sources: set[str], method_targets: set[str]) -> str | None:
    """Monolynx node type from graphify node, or None to skip (v1 code-only).

    graphify does not type code nodes explicitly - the type is inferred with
    the heuristics from MON-115 (first matching rule wins, verified on
    graphify 0.9.18 output).
    """
    if node.get("file_type") == "rationale":
        return None
    if not node.get("source_file"):
        return None  # external symbol (library)
    if node.get("type") == "package":
        return None

    label = str(node.get("label", ""))
    if label.endswith(".py"):
        return "File"
    if node["id"] in method_targets or label.startswith("."):
        return "Method"
    if node["id"] in method_sources:
        return "Class"
    if label.endswith("()"):
        return "Function"
    if label[:1].isupper() and label.isidentifier():
        return "Class"
    return "Const"


def map_graph(graph: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Map graphify node-link data (nodes + links) to replace_graph payload."""
    links = graph.get("links", [])
    method_sources = {e["source"] for e in links if e.get("relation") == "method"}
    method_targets = {e["target"] for e in links if e.get("relation") == "method"}

    nodes_out: list[dict[str, Any]] = []
    kept_ids: set[str] = set()
    skipped_nodes = 0

    for node in graph.get("nodes", []):
        node_type = classify_node(node, method_sources, method_targets)
        if node_type is None:
            skipped_nodes += 1
            continue
        nodes_out.append(
            {
                "id": node["id"],
                "type": node_type,
                "name": _clean_name(str(node.get("label", ""))) or str(node["id"]),
                "file_path": node.get("source_file") or None,
                "line_number": _line_number(node.get("source_location")),
                "metadata": {"community": node.get("community")},
            }
        )
        kept_ids.add(node["id"])

    edges_out: list[dict[str, Any]] = []
    skipped_edges = 0
    for edge in links:
        edge_type = RELATION_TO_EDGE_TYPE.get(edge.get("relation", ""))
        if edge_type is None or edge["source"] not in kept_ids or edge["target"] not in kept_ids:
            skipped_edges += 1
            continue
        edges_out.append(
            {
                "source_id": edge["source"],
                "target_id": edge["target"],
                "type": edge_type,
                "metadata": {
                    "source_relation": edge.get("relation"),
                    "confidence": edge.get("confidence"),
                },
            }
        )

    log.info(
        "Mapped %d nodes (%d skipped) and %d edges (%d skipped)",
        len(nodes_out),
        skipped_nodes,
        len(edges_out),
        skipped_edges,
    )
    return nodes_out, edges_out


def load_graph(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        log.error("Missing %s - run `graphify update .` first (https://github.com/Graphify-Labs/graphify)", path)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.error("Cannot read %s: %s", path, e)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync graphify graph.json to Monolynx replace_graph")
    parser.add_argument("--monolynx-url", default=os.environ.get("MONOLYNX_URL", "https://monolynx.com"))
    parser.add_argument("--token", default=os.environ.get("MONOLYNX_GRAPH_TOKEN", ""))
    parser.add_argument("--project-slug", default=os.environ.get("MONOLYNX_PROJECT_SLUG", ""))
    parser.add_argument("--graph-json", default="graphify-out/graph.json")
    parser.add_argument("--dry-run", action="store_true", help="map and report counts, do not push")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    graph = load_graph(Path(args.graph_json))
    if graph is None:
        return 0  # non-blocking: the host project's build must pass

    nodes, edges = map_graph(graph)

    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        log.error(
            "Payload too big (%d nodes / %d edges, limits %d / %d) - tighten .graphifyignore",
            len(nodes),
            len(edges),
            MAX_NODES,
            MAX_EDGES,
        )
        return 1

    if args.dry_run:
        log.info("DRY-RUN: would push %d nodes / %d edges to project '%s'", len(nodes), len(edges), args.project_slug or "?")
        return 0

    if not args.token:
        log.info("MONOLYNX_GRAPH_TOKEN not set - skipping sync.")
        return 0
    if not args.project_slug:
        log.error("MONOLYNX_PROJECT_SLUG (or --project-slug) is required.")
        return 1

    try:
        client = MonolynxClient(args.monolynx_url, args.token, args.project_slug)
        stats = client.replace_graph(nodes, edges, clear_first=True)
    except RuntimeError as e:
        if "Unknown tool" in str(e):
            log.error("Serwer %s nie zna toola replace_graph - wdroz nowsza wersje Monolynx i sprobuj ponownie.", args.monolynx_url)
        else:
            log.error("Sync nieudany: %s", e)
        return 1
    log.info("replace_graph OK: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
