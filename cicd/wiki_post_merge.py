#!/usr/bin/env python3
"""Prepare wiki INGEST manifest after merge to main.

Fetches recently closed/merged tickets from Monolynx via MCP and prints a
manifest of sources that the human operator should run through the
wiki-sync-merge skill.

This script does NOT write to the wiki. It only prepares the list.
The operator then runs: /monolynx:wiki-ingest (or wiki-sync-merge skill)
for each relevant source.

Usage:
  python cicd/wiki_post_merge.py
  MONOLYNX_URL=https://monolynx.com MONOLYNX_MCP_TOKEN=osk_xxx python cicd/wiki_post_merge.py
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("wiki_post_merge")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# Ticket statuses that indicate the work is done and worth INGESTing
DONE_STATUSES = {"done", "closed"}

# ---------------------------------------------------------------------------
# Monolynx MCP Client (minimal - only list_tickets needed)
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
                with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                    if not self.session_id:
                        sid = resp.headers.get("Mcp-Session-Id")
                        if sid:
                            self.session_id = sid
                    body: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
                    return body
            except urllib.error.HTTPError as e:
                last_error = e
                body_text = e.read().decode("utf-8", errors="replace")
                log.warning("HTTP %d attempt %d/%d: %s", e.code, attempt, MAX_RETRIES, body_text[:200])
                if e.code < 500:
                    raise RuntimeError(f"MCP HTTP {e.code}: {body_text[:300]}") from e
            except urllib.error.URLError as e:
                last_error = e
                log.warning("Connection error attempt %d/%d: %s", attempt, MAX_RETRIES, e.reason)

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
        self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "wiki_post_merge", "version": "1.0"},
        })

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

    def search_tickets(self, status: str | None = None) -> list[dict[str, Any]]:
        args: dict[str, Any] = {"project_slug": self.project_slug}
        if status:
            args["status"] = status
        result = self._call_tool("search_tickets", args)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("results", [])
        return []


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def build_manifest(client: MonolynxClient, project_slug: str) -> list[dict[str, Any]]:
    """Collect tickets in done/closed state as INGEST candidates."""
    candidates: list[dict[str, Any]] = []

    for status in DONE_STATUSES:
        try:
            tickets = client.search_tickets(status=status)
            for t in tickets:
                candidates.append({
                    "id": t.get("id", ""),
                    "number": t.get("key") or t.get("number", ""),
                    "title": t.get("title", "(brak tytulu)"),
                    "status": t.get("status", status),
                    "url": f"{client.url}/dashboard/{project_slug}/scrum/tickets/{t.get('id', '')}",
                })
        except RuntimeError as e:
            log.warning("Could not fetch tickets (status=%s): %s", status, e)

    # Deduplicate by id
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in candidates:
        if c["id"] not in seen:
            seen.add(c["id"])
            unique.append(c)

    return unique


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def print_manifest(manifest: list[dict[str, Any]], project_slug: str, monolynx_url: str) -> None:
    separator = "-" * 60

    print()
    print(separator)
    print("WIKI POST-MERGE MANIFEST")
    print(separator)
    print(f"Projekt: {project_slug}")
    print(f"Platforma: {monolynx_url}")
    print()

    if not manifest:
        print("Brak ticketow do INGEST (statusy: done/closed).")
        print()
    else:
        print(f"Znaleziono {len(manifest)} kandydatow do INGEST wiki:")
        print()
        for i, item in enumerate(manifest, 1):
            number = item.get("number") or item["id"][:8]
            print(f"  {i}. [{number}] {item['title']}")
            print(f"     Status: {item['status']}")
            print(f"     URL:    {item['url']}")
            print()

    print(separator)
    print("NASTEPNY KROK:")
    print()
    print("  Uruchom /monolynx:wiki-sync-merge aby zaktualizowac wiki")
    print("  na podstawie powyzszych zrodel:")
    print()
    print("    /monolynx:wiki-sync-merge")
    print()
    print("  Skill przeprowadzi Cie przez wybor zrodel i INGEST.")
    print(separator)
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    monolynx_url = os.environ.get("MONOLYNX_URL", "https://monolynx.com")
    token = os.environ.get("MONOLYNX_MCP_TOKEN", "")
    project_slug = os.environ.get("MONOLYNX_PROJECT_SLUG", "")

    if not token:
        log.info("MONOLYNX_MCP_TOKEN nie ustawiony - pomijam wiki-post-merge (skip).")
        print()
        print("INFO: Ustaw MONOLYNX_MCP_TOKEN w zmiennych CI aby aktywowac wiki-post-merge.")
        print()
        sys.exit(0)

    if not project_slug:
        log.error("MONOLYNX_PROJECT_SLUG wymagany.")
        sys.exit(1)

    try:
        client = MonolynxClient(monolynx_url, token, project_slug)
        manifest = build_manifest(client, project_slug)
        print_manifest(manifest, project_slug, monolynx_url)
    except RuntimeError as e:
        log.error("Blad podczas pobierania danych: %s", e)
        # Non-fatal: CI nie failuje z powodu wiki-post-merge
        print()
        print(f"OSTRZEZENIE: Nie udalo sie pobrac listy ticketow: {e}")
        print("Uruchom /monolynx:wiki-ingest recznie po mergu.")
        print()
        sys.exit(0)


if __name__ == "__main__":
    main()
