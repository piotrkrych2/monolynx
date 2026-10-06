"""MCP Server -- narzedzia do zarzadzania Monolynx z Claude Code."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from monolynx.config import settings as app_settings
from monolynx.constants import (
    ACTIVITY_ENTITY_TYPES,
    GRAPH_EDGE_TYPES,
    GRAPH_NODE_TYPES,
    INTERVAL_UNITS,
    PERMISSION_ACTIONS,
    PERMISSION_MODULES,
    PIPELINE_JOB_STATUSES,
    PIPELINE_STATUSES,
    PIPELINE_TYPES,
    PRIORITIES,
    TIME_TRACKING_STATUSES,
)
from monolynx.dashboard.monitoring import _is_url_safe
from monolynx.dashboard.reports import _get_user_project_ids
from monolynx.database import async_session_factory
from monolynx.models.event import Event
from monolynx.models.heartbeat import Heartbeat
from monolynx.models.issue import Issue
from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.pipeline import Pipeline, PipelineJob, PipelineStep
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_project import SettlementProject
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_attachment import TicketAttachment
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.models.work_plan import WorkPlanEntry
from monolynx.schemas.pipelines import build_list_item, build_pipeline_response
from monolynx.schemas.time_tracking import TimeTrackingEntryResponse, TimeTrackingFilter
from monolynx.schemas.work_plan import WorkPlanEntryResponse
from monolynx.services import blog as blog_service
from monolynx.services import blog_lint as blog_lint_service
from monolynx.services import graph as graph_service
from monolynx.services import pipelines as pipelines_service
from monolynx.services import projects as projects_service
from monolynx.services import tickets as tickets_service
from monolynx.services import wiki_attachments as wiki_attachments_service
from monolynx.services import work_plan as work_plan_service
from monolynx.services.activity import get_activity_log as svc_get_activity_log
from monolynx.services.burndown import get_burndown_data as svc_get_burndown_data
from monolynx.services.email import send_invitation_email
from monolynx.services.graph import ensure_search_not_blank
from monolynx.services.graph import format_graph_dsl as _format_graph_dsl
from monolynx.services.heartbeat import create_heartbeat as svc_create_heartbeat
from monolynx.services.heartbeat import delete_heartbeat as svc_delete_heartbeat
from monolynx.services.heartbeat import get_heartbeat_status
from monolynx.services.heartbeat import update_heartbeat as svc_update_heartbeat
from monolynx.services.mcp_auth import verify_bearer_token, verify_mcp_token
from monolynx.services.minio_client import get_attachment as minio_get_attachment
from monolynx.services.minio_client import upload_attachment as minio_upload_attachment
from monolynx.services.minio_client import upload_object as minio_upload_object
from monolynx.services.permissions import check_permission, ensure_can_manage_role, ensure_permission, get_user_permissions
from monolynx.services.settlements import (
    change_settlement_status as svc_change_settlement_status,
)
from monolynx.services.settlements import (
    create_settlement as svc_create_settlement,
)
from monolynx.services.settlements import (
    delete_settlement as svc_delete_settlement,
)
from monolynx.services.settlements import (
    delete_settlement_attachment as svc_delete_settlement_attachment,
)
from monolynx.services.settlements import (
    ensure_settlement_permission,
    ensure_ticket_project_write,
    fully_permitted_settlements_clause,
    get_permitted_project_ids,
    get_settlement_attachment_bytes,
    validate_settlement_ticket_link,
)
from monolynx.services.settlements import (
    update_settlement as svc_update_settlement,
)
from monolynx.services.settlements import (
    upload_settlement_attachment as svc_upload_settlement_attachment,
)
from monolynx.services.sprint import complete_sprint as svc_complete_sprint
from monolynx.services.sprint import start_sprint as svc_start_sprint
from monolynx.services.ticket_numbering import get_next_ticket_number
from monolynx.services.time_tracking import add_time_entry
from monolynx.services.time_tracking import (
    get_work_report as svc_get_work_report,
)
from monolynx.services.wiki import (
    append_log as svc_append_wiki_log,
)
from monolynx.services.wiki import (
    create_wiki_page as svc_create_wiki_page,
)
from monolynx.services.wiki import (
    delete_wiki_page as svc_delete_wiki_page,
)
from monolynx.services.wiki import (
    get_backlinks as svc_get_wiki_backlinks,
)
from monolynx.services.wiki import (
    get_outlinks as svc_get_wiki_outlinks,
)
from monolynx.services.wiki import (
    get_page_content,
    is_wiki_llm_enabled,
)
from monolynx.services.wiki import (
    get_wiki_config as svc_get_wiki_config,
)
from monolynx.services.wiki import (
    get_wiki_page_detail as svc_get_wiki_page_detail,
)
from monolynx.services.wiki import (
    list_pages_flat as svc_list_pages_flat,
)
from monolynx.services.wiki import (
    regenerate_index as svc_regenerate_wiki_index,
)
from monolynx.services.wiki import (
    set_wiki_config as svc_set_wiki_config,
)
from monolynx.services.wiki import (
    update_wiki_page as svc_update_wiki_page,
)
from monolynx.services.wiki_bootstrap import (
    bootstrap_wiki_llm as svc_bootstrap_wiki_llm,
)
from monolynx.services.wiki_lint import lint_wiki as svc_lint_wiki
from monolynx.services.work_plan import _UNSET as _WORK_PLAN_UNSET

_STATIC_SKILLS_DIR = Path(__file__).resolve().parent / "static" / "skills"
_SKILL_SLUG_PLACEHOLDERS = ("<PROJECT-SLUG>", "<PROJECT-ID>")
# Katalog skilli per runtime. Format SKILL.md (frontmatter + pliki towarzyszace)
# jest wspolny dla Claude Code, Codex i Cursora - rozni sie tylko lokalizacja.
_SKILL_TARGET_DIRS = {
    "claude": ".claude/skills",
    "codex": ".agents/skills",
    "cursor": ".cursor/skills",
}


def _build_allowed_hosts() -> list[str]:
    """Zbuduj liste dozwolonych hostow z MCP_ALLOWED_HOSTS (env/.env)."""
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    if app_settings.MCP_ALLOWED_HOSTS:
        for h in app_settings.MCP_ALLOWED_HOSTS.split(","):
            h = h.strip()
            if h:
                hosts.append(h)
    return hosts


mcp = MCPServer(
    "Monolynx",
    instructions=(
        "Serwer MCP platformy Monolynx. "
        "Moduly: Scrum (tickety, sprinty, tablica Kanban), "
        "500ki (error tracking — issues, eventy), "
        "Monitoring (URL health checks, uptime), "
        "Wiki (strony markdown z hierarchia). "
        "Wymaga tokenu API (Bearer) w naglowku Authorization."
    ),
)

# Adnotacje MCP: hinty dla klienta, jak tool traktuje stan serwera.
# Domena jest zamknieta (wlasna baza Monolynx), stad openWorldHint=False wszedzie.
READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, open_world_hint=False)


async def _auth(ctx: Context[Any, Any]) -> User:
    """Wyciagnij token z naglowka HTTP i zwaliduj uzytkownika (OAuth + legacy)."""
    raw_token = await _get_auth_header(ctx)
    return await _verify_token(raw_token)


async def _get_auth_header(ctx: Context[Any, Any]) -> str:
    """Pobierz raw token z kontekstu."""
    request_ctx = ctx.request_context
    if request_ctx is None:
        raise ValueError("Brak kontekstu HTTP")
    starlette_request = getattr(request_ctx, "request", None)
    if starlette_request is None:
        raise ValueError("Brak kontekstu HTTP request")
    headers = starlette_request.headers
    auth_header = headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise ValueError("Brak tokenu Bearer")
    return str(auth_header[7:])


async def _verify_token(raw_token: str) -> User:
    """Zwaliduj token (OAuth + legacy) i zwroc uzytkownika.

    Cienki wrapper nad verify_bearer_token (MON-140, wspoldzielony z API v2) --
    ta funkcja i jej sygnatura zostaja, bo _auth(), _MCPBearerAuthMiddleware i
    _get_user_and_project() ja wolaja, a ~440 istniejacych testow MCP patchuje
    monolynx.mcp_server.verify_mcp_token (nie nazwe w services/mcp_auth.py) i
    oczekuje, ze podmiana zadziala tutaj -- stad explicite
    legacy_verifier=verify_mcp_token (modulowy import ponizej).
    """
    async with async_session_factory() as db:
        user = await verify_bearer_token(raw_token, db, legacy_verifier=verify_mcp_token)
    if user is None:
        raise ValueError("Nieprawidlowy lub nieaktywny token API")
    return user


BLOG_WRITE_TOOLS = frozenset({"set_blog_post_meta", "publish_blog_post", "unpublish_blog_post"})


def _hide_blog_write_tools(body: bytes) -> bytes:
    """Usun toole zapisu bloga z odpowiedzi JSON-RPC `tools/list`; kazde inne cialo zwroc bez zmian."""
    try:
        data = json.loads(body)
    except ValueError:
        return body
    if not isinstance(data, dict):
        return body
    result = data.get("result")
    if not isinstance(result, dict):
        return body
    tools = result.get("tools")
    if not isinstance(tools, list):
        return body
    visible = [t for t in tools if not (isinstance(t, dict) and t.get("name") in BLOG_WRITE_TOOLS)]
    if len(visible) == len(tools):
        return body
    result["tools"] = visible
    return json.dumps(data).encode()


def _filtering_send(send: Send) -> Send:
    """Opakuj `send`: buforuj odpowiedz JSON, przepusc cialo przez _hide_blog_write_tools, popraw content-length.

    Odpowiedzi innego typu niz application/json (np. strumien SSE) leca dalej bez buforowania.
    """
    start: Message | None = None
    chunks: list[bytes] = []

    async def wrapped(message: Message) -> None:
        nonlocal start
        if message["type"] == "http.response.start":
            content_type = dict((k.lower(), v) for k, v in message.get("headers", [])).get(b"content-type", b"")
            if content_type.startswith(b"application/json"):
                start = message
            else:
                await send(message)
            return
        if start is None or message["type"] != "http.response.body":
            await send(message)
            return
        chunks.append(message.get("body", b""))
        if message.get("more_body", False):
            return
        body = _hide_blog_write_tools(b"".join(chunks))
        headers = [(k, v) for k, v in start["headers"] if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(body)).encode()))
        await send({**start, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return wrapped


class _MCPBearerAuthMiddleware:
    """Lekki ASGI middleware wymuszajacy Bearer auth na calym mountcie /mcp.

    Dlaczego nie MCPServer(auth=AuthSettings(...))?
    AuthSettings wymaga issuer_url i resource_server_url (pola OAuth AS/RS),
    co dodaloby .well-known/oauth-protected-resource bezposrednio pod /mcp
    i wymagaloby pelnej konfiguracji OAuth serwera autoryzacji. Nasz OAuth
    router jest juz zamontowany na glownej aplikacji FastAPI (api/oauth.py),
    wiec duplikowanie go pod /mcp byloby nieprawidlowe.

    Middleware reuzywac _verify_token (OAuth + legacy osk_*) bez zmian w
    116 toolach. Per-tool _auth(ctx) pozostaje jako defense-in-depth.

    MON-270: konto bez uprawnienia `blog:write` (nie superuser, bez `can_edit_blog`) dostaje
    odpowiedz JSON przepuszczona przez _hide_blog_write_tools, wiec nie widzi tooli zapisu bloga
    na liscie narzedzi. To ukrycie, nie zabezpieczenie: odmowe przy wywolaniu daje serwis bloga.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Przepuszczamy websockets i lifespan bez weryfikacji
            await self.app(scope, receive, send)
            return

        # Wyciagnij naglowek Authorization
        headers: dict[bytes, bytes] = {k.lower(): v for k, v in scope.get("headers", [])}
        auth_bytes = headers.get(b"authorization", b"")
        auth_header = auth_bytes.decode("latin-1")

        raw_token: str | None = None
        if auth_header.lower().startswith("bearer "):
            raw_token = auth_header[7:]

        if not raw_token:
            await _send_401(send, scope)
            return

        try:
            user = await _verify_token(raw_token)
        except Exception:
            await _send_401(send, scope)
            return

        if user.is_superuser or user.can_edit_blog:
            await self.app(scope, receive, send)
            return

        await self.app(scope, receive, _filtering_send(send))


def _resource_metadata_url(scope: Scope) -> str:
    """Wybierz dokument RFC 9728 dopasowany do mountu, ktory obsluzyl request.

    _mcp_http_app jest zamontowany pod DWOMA sciezkami w main.py (`/mcp`
    i `/`), wiec ten sam middleware widzi oba mounty. Mount w Starlette NIE
    modyfikuje scope["path"] (zawsze pelna sciezka requestu) -- modyfikuje
    tylko scope["root_path"], a Mount("/mcp") dopasowuje wylacznie z
    koncowym "/" (regex "/mcp/{path:path}"), wiec bare "POST /mcp" bez
    slasha trafia w mount "/" i tak samo widzi pelna sciezke "/mcp" w
    scope["path"]. Dlatego liczy sie scope["path"], nie root_path: request
    pod /mcp (z lub bez koncowego slasha) musi dostac dokument wskazujacy
    resource={APP_URL}/mcp (MON-193), inaczej klient wiaze serwer pod
    goly host i reconnect konczy sie "MCP endpoint not found".
    """
    path = scope.get("path", "")
    if path == "/mcp" or path.startswith("/mcp/"):
        return f"{app_settings.APP_URL}/.well-known/oauth-protected-resource/mcp"
    return f"{app_settings.APP_URL}/.well-known/oauth-protected-resource"


async def _send_401(send: Send, scope: Scope) -> None:
    """Wyslij odpowiedz HTTP 401 z naglowkiem WWW-Authenticate.

    Naglowek niesie resource_metadata (RFC 9728) dopasowane do mountu,
    pod ktorym przyszedl request (patrz _resource_metadata_url) -- klient
    podpiety pod /mcp musi dostac dokument, ktorego "resource" to /mcp,
    inaczej wiaze serwer pod goly host (MON-193). To sygnal ktorego
    klienci MCP (m.in. claude.ai) uzywaja zeby odpalic OAuth flow i
    pokazac "Connect" zamiast traktowac serwer jako publiczny.
    """
    resource_metadata = _resource_metadata_url(scope)
    www_authenticate = f'Bearer resource_metadata="{resource_metadata}"'
    body = json.dumps({"error": "unauthorized", "error_description": "Bearer token required"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", www_authenticate.encode("latin-1")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_mcp_http_app() -> ASGIApp:
    """Zbuduj aplikacje MCP z middleware Bearer auth.

    Wywolaj zamiast mcp.streamable_http_app() zeby zapewnic
    ze kazdy JSON-RPC request (initialize, tools/list, tools/call)
    wymaga waznego tokenu Bearer.

    Parametry transportu ida tutaj, nie do konstruktora MCPServer - w MCP SDK 2.0
    konstruktor odpowiada tylko za tozsamosc serwera.
    """
    return _MCPBearerAuthMiddleware(
        mcp.streamable_http_app(
            streamable_http_path="/",
            json_response=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=_build_allowed_hosts(),
            ),
        )
    )


def _format_board(sprint: Sprint, project_code: str, columns: dict[str, list[dict[str, Any]]]) -> str:
    """Konwertuj dane tablicy Kanban na kompaktowy string dla LLM.

    Format:
        Sprint: <name> (<start> → <end>) | <sp>sp total

        ## Todo
        CODE-1 | Tytul | priority | @assignee | 3sp

        ## In Progress
        (brak)
        ...
    """
    end_str = sprint.end_date.isoformat() if sprint.end_date else "?"
    total_sp = sum(t.get("story_points") or 0 for col in columns.values() for t in col)
    header = f"Sprint: {sprint.name} ({sprint.start_date.isoformat()} → {end_str}) | {total_sp}sp total"

    status_labels = {
        "todo": "Todo",
        "in_progress": "In Progress",
        "in_review": "In Review",
        "done": "Done",
    }

    lines: list[str] = [header, ""]
    for status, label in status_labels.items():
        lines.append(f"## {label}")
        tickets = columns.get(status, [])
        if not tickets:
            lines.append("(brak)")
        else:
            for t in tickets:
                key = t["key"]
                title = t["title"]
                priority = t["priority"] or "--"
                assignee = f"@{t['assignee']}" if t.get("assignee") else "--"
                sp = f"{t['story_points']}sp" if t.get("story_points") is not None else "--"
                labels_part = ""
                if t.get("labels"):
                    labels_part = f" [{', '.join(t['labels'])}]"
                lines.append(f"{key} | {title} | {priority} | {assignee} | {sp}{labels_part}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _format_ticket_detail(
    ticket: Ticket,
    project_code: str,
) -> str:
    """Konwertuj ticket na kompaktowy string dla LLM.

    Format:
        MON-12 | Tytul ticketa
        Status: in_progress | Priority: high | Sprint: Sprint 5 | Assignee: jan@example.com
        Story Points: 3 | Due: 2026-03-20 | Labels: backend, urgent
        Created: 2026-03-10 | Updated: 2026-03-12 | AI: no
        ID: 550e8400-e29b-41d4-a716-446655440000

        ## Description
        Tresc opisu...

        ## Attachments (2)
        - report.pdf (application/pdf, 1.2 MB)

        ## Blokery (relacja)
        Blokowany przez: MON-5 | todo | Tytul blokera
        Blokuje: MON-9 | in_progress | Tytul blokowanego

        ## Comments (3)
        [2026-03-11 jan@example.com] Tresc komentarza
    """

    def _human_size(size_bytes: int) -> str:
        if size_bytes >= 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        return f"{size_bytes / 1024:.0f} KB"

    key = f"{project_code}-{ticket.number}"
    lines: list[str] = [f"{key} | {ticket.title}"]

    sprint_name = ticket.sprint.name if ticket.sprint else "—"
    assignee_email = ticket.assignee.email if ticket.assignee else "—"
    lines.append(f"Status: {ticket.status} | Priority: {ticket.priority} | Sprint: {sprint_name} | Assignee: {assignee_email}")

    due_str = ticket.due_date.isoformat() if ticket.due_date else "—"
    label_names = ", ".join(lb.name for lb in ticket.labels) if ticket.labels else "—"
    sp_str = str(ticket.story_points) if ticket.story_points is not None else "—"
    lines.append(f"Story Points: {sp_str} | Due: {due_str} | Labels: {label_names}")

    created_str = ticket.created_at.date().isoformat()
    updated_str = ticket.updated_at.date().isoformat()
    ai_str = "yes" if ticket.created_via_ai else "no"
    lines.append(f"Created: {created_str} | Updated: {updated_str} | AI: {ai_str}")
    lines.append(f"ID: {ticket.id}")

    if ticket.spec_page_id:
        spec_title = ticket.spec_page.title if ticket.spec_page else str(ticket.spec_page_id)
        lines.append(f"Spec: {spec_title} | Spec ID: {ticket.spec_page_id}")

    if ticket.description:
        lines.append("")
        lines.append("## Description")
        lines.append(ticket.description)

    attachments = list(ticket.attachments) if ticket.attachments else []
    if attachments:
        lines.append("")
        lines.append(f"## Attachments ({len(attachments)})")
        for att in attachments:
            mime = att.mime_type or "application/octet-stream"
            size_str = _human_size(att.size)
            lines.append(f"- {att.filename} ({mime}, {size_str})")

    criteria = list(ticket.acceptance_criteria) if ticket.acceptance_criteria else []
    if criteria:
        lines.append("")
        lines.append(f"## Acceptance Criteria ({len(criteria)})")
        for ac in criteria:
            checkbox = "[x]" if ac.is_completed else "[ ]"
            ai_str = " (AI)" if ac.created_via_ai else ""
            lines.append(f"{checkbox} {ac.description}{ai_str}")

    blocked_by = list(ticket.blocked_by) if ticket.blocked_by else []
    blocking = list(ticket.blocking) if ticket.blocking else []
    if blocked_by or blocking:
        lines.append("")
        lines.append("## Blokery (relacja)")
        for blocker in blocked_by:
            blocker_key = f"{project_code}-{blocker.number}"
            lines.append(f"Blokowany przez: {blocker_key} | {blocker.status} | {blocker.title}")
        for blocked in blocking:
            blocked_key = f"{project_code}-{blocked.number}"
            lines.append(f"Blokuje: {blocked_key} | {blocked.status} | {blocked.title}")

    comments = list(ticket.comments) if ticket.comments else []
    if comments:
        lines.append("")
        lines.append(f"## Comments ({len(comments)})")
        for c in comments:
            date_str = c.created_at.date().isoformat()
            author_str = "ai" if c.created_via_ai else c.author.email
            content = c.content.replace("\n", " ")
            lines.append(f"[{date_str} {author_str}] {content}")

    return "\n".join(lines)


def _format_sprint_detail(sprint: Sprint, project_code: str, tickets: list[dict[str, Any]]) -> str:
    """Konwertuj dane sprintu na kompaktowy string dla LLM."""
    end_str = sprint.end_date.isoformat() if sprint.end_date else "(brak)"
    header = f"Sprint: {sprint.name} | Status: {sprint.status} | {sprint.start_date.isoformat()} \u2192 {end_str}"

    total_sp = sum(t.get("story_points") or 0 for t in tickets)
    done_sp = sum(t.get("story_points") or 0 for t in tickets if t.get("status") == "done")

    lines: list[str] = [
        f"ID: {sprint.id}",
        header,
    ]
    if sprint.goal:
        lines.append(f"Goal: {sprint.goal}")
    lines.append(f"Total: {total_sp}sp | Done: {done_sp}sp")

    lines.append("")
    lines.append(f"## Tickets ({len(tickets)})")
    if not tickets:
        lines.append("(brak)")
    else:
        lines.append(f"{'Key':<10} | {'Title':<24} | {'Status':<11} | {'Pri':<6} | {'Assignee':<11} | SP")
        for t in tickets:
            key = t["key"]
            title = t["title"][:24]
            status = t["status"] or "--"
            priority = t["priority"] or "--"
            assignee = t["assignee"] or "--"
            sp = str(t["story_points"]) if t.get("story_points") is not None else "--"
            lines.append(f"{key:<10} | {title:<24} | {status:<11} | {priority:<6} | {assignee:<11} | {sp}")

    return "\n".join(lines)


def _interval_human(value: int, unit: str) -> str:
    """Zamien (5, 'minutes') -> '5 min', (1, 'hours') -> '1 hr', (1, 'days') -> '1 day'."""
    u = unit.lower()
    if u in ("minutes", "minute"):
        return f"{value} min"
    if u in ("hours", "hour"):
        return f"{value} hr"
    if u in ("days", "day"):
        return f"{value} day"
    return f"{value} {unit}"


def _format_monitor_detail(
    monitor: Any,
    checks: list[Any],
    uptime_24h: float | None,
    page: int = 1,
    total_pages: int = 1,
) -> str:
    """Konwertuj monitor i historię checków na kompaktowy string dla LLM.

    Format:
        Monitor: API Health | https://api.example.com/health
        ID: 550e8400-e29b-41d4-a716-446655440000
        Active: yes | Interval: 5 min | Uptime 24h: 99.8%

        ## Check History (20) (page 1/3)
        Timestamp           | Status | Code | Response Time | Error
        2026-03-15 14:22:00 | OK     | 200  | 45ms          |
        2026-03-15 14:12:00 | FAIL   | 503  | --            | Connection refused
    """
    name = monitor.name or monitor.url
    active_str = "yes" if monitor.is_active else "no"
    interval_str = _interval_human(monitor.interval_value, monitor.interval_unit)
    uptime_str = f"{uptime_24h}%" if uptime_24h is not None else "--"

    lines: list[str] = [
        f"Monitor: {name} | {monitor.url}",
        f"ID: {monitor.id}",
        f"Active: {active_str} | Interval: {interval_str} | Uptime 24h: {uptime_str}",
    ]

    count = len(checks)
    page_info = f" (page {page}/{total_pages})" if total_pages > 1 else ""
    lines.append("")
    lines.append(f"## Check History ({count}){page_info}")
    lines.append(f"{'Timestamp':<20} | {'Status':<6} | {'Code':<4} | {'Response Time':<13} | Error")

    for c in checks:
        ts = c.checked_at.strftime("%Y-%m-%d %H:%M:%S") if c.checked_at else "--"
        status = "OK" if c.is_success else "FAIL"
        code = str(c.status_code) if c.status_code is not None else "--"
        rt = f"{c.response_time_ms}ms" if c.response_time_ms is not None else "--"
        error = c.error_message or ""
        lines.append(f"{ts:<20} | {status:<6} | {code:<4} | {rt:<13} | {error}")

    return "\n".join(lines)


def _format_monitors_table(monitors_data: list[dict[str, Any]]) -> str:
    """Konwertuj liste monitorow na kompaktowy string tabelaryczny dla LLM."""
    if not monitors_data:
        return "0 monitors"

    def _interval_human(interval_str: str) -> str:
        """Zamien '5 minutes' -> '5 min', '1 hours' -> '1 hr', '1 days' -> '1 day'."""
        parts = interval_str.split()
        if len(parts) != 2:
            return interval_str
        val, unit = parts[0], parts[1].lower()
        if unit in ("minutes", "minute"):
            return f"{val} min"
        if unit in ("hours", "hour"):
            return f"{val} hr"
        if unit in ("days", "day"):
            return f"{val} day"
        return interval_str

    def _last_check_str(last_check: dict[str, Any] | None) -> str:
        if last_check is None:
            return "--"
        status = "OK" if last_check.get("is_success") else "FAIL"
        code = last_check.get("status_code") or "--"
        rt = last_check.get("response_time_ms")
        rt_str = f"{rt}ms" if rt is not None else "--"
        ts = last_check.get("checked_at", "")
        # Skracamy ISO timestamp do "YYYY-MM-DD HH:MM"
        if "T" in ts:
            ts = ts.replace("T", " ")[:16]
        return f"{status} {code} {rt_str} {ts}"

    header = "ID                                   | Name           | URL                          | Active | Interval | Uptime 24h | Last Check"
    sep = "-" * len(header)
    lines: list[str] = [f"{len(monitors_data)} monitors", "", header, sep]

    for m in monitors_data:
        mid = m.get("id", "")
        name = (m.get("name") or "")[:14]
        url = (m.get("url") or "")[:28]
        active = "yes" if m.get("is_active") else "no"
        interval = _interval_human(m.get("interval", ""))
        uptime = m.get("uptime_24h")
        uptime_str = f"{uptime}%" if uptime is not None else "--"
        last_check = _last_check_str(m.get("last_check"))
        lines.append(f"{mid} | {name:<14} | {url:<28} | {active:<6} | {interval:<8} | {uptime_str:<10} | {last_check}")

    return "\n".join(lines)


def _format_tickets_table(
    tickets: list[Any],
    project_code: str,
    page: int,
    total_pages: int,
    total: int,
) -> str:
    """Konwertuj liste ticketow na kompaktowy string pipe-separated dla LLM.

    Format:
        20 tickets (page 1/3)

        Key      | Title                    | Status      | Pri    | Assignee     | Sprint   | SP | Due        | Labels          | Blocked by
        MON-20   | Fix login timeout        | in_progress | high   | jan@ex.com   | Sprint 5 | 3  | 2026-03-20 | backend, urgent | MON-3, MON-7
        MON-19   | Dodaj eksport PDF        | todo        | medium | --           | Sprint 5 | 5  | --         |                 | --

    "Blocked by": klucze blokerow rosnaco po numerze, `--` gdy ticket nie ma blokerow.
    """
    header_line = f"{total} tickets (page {page}/{total_pages})"

    if not tickets:
        return f"{header_line}\n\n(brak ticketow)"

    col_header = (
        "Key      | Title                                    | Status      | Pri    "
        "| Assignee              | Sprint            | SP | Due        | Labels | Blocked by"
    )
    rows: list[str] = [header_line, "", col_header]

    for t in tickets:
        key = f"{project_code}-{t.number}"
        title = (t.title or "")[:40]
        status = t.status or "--"
        priority = t.priority or "--"
        assignee = t.assignee.email if t.assignee else "--"
        sprint = t.sprint.name if t.sprint else "--"
        sp = str(t.story_points) if t.story_points is not None else "--"
        due = t.due_date.isoformat() if t.due_date else "--"
        labels = ", ".join(lb.name for lb in t.labels) if t.labels else ""
        blockers = ", ".join(f"{project_code}-{b.number}" for b in sorted(t.blocked_by, key=lambda b: b.number)) or "--"
        rows.append(
            f"{key:<8} | {title:<40} | {status:<11} | {priority:<6} | {assignee:<21} | {sprint:<17} | {sp:<2} | {due:<10} | {labels} | {blockers}"
        )

    return "\n".join(rows)


async def _get_user_and_project(ctx: Context[Any, Any], project_slug: str) -> tuple[User, Project]:
    """Autoryzuj uzytkownika i sprawdz dostep do projektu."""
    raw_token = await _get_auth_header(ctx)
    user = await _verify_token(raw_token)

    async with async_session_factory() as db:
        result = await db.execute(select(Project).where(Project.slug == project_slug, Project.is_active.is_(True)))
        project = result.scalar_one_or_none()
        if project is None:
            raise ValueError(f"Projekt '{project_slug}' nie istnieje")

        result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        member = result.scalar_one_or_none()
        if member is None:
            raise ValueError(f"Uzytkownik nie jest czlonkiem projektu '{project_slug}'")

    return user, project


async def _get_user_member_and_project(ctx: Context[Any, Any], project_slug: str) -> tuple[User, ProjectMember, Project]:
    """Autoryzuj uzytkownika, sprawdz dostep do projektu i zwroc obiekt ProjectMember."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        member_result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        member = member_result.scalar_one_or_none()
        if member is None:
            raise ValueError(f"Uzytkownik nie jest czlonkiem projektu '{project_slug}'")

    return user, member, project


async def _require(db: AsyncSession, user: User, project: Project, module: str, action: str) -> None:
    """Sprawdz uprawnienie roli do modulu i akcji (ValueError "Brak uprawnienia <modul>:<akcja>")."""
    await ensure_permission(db, user.id, project.id, module, action)


# --- Projekty ---


@mcp.tool(annotations=READ_ONLY)
async def list_projects(ctx: Context[Any, Any]) -> list[dict[str, Any]]:
    """Lista projektow, do ktorych uzytkownik jest przypisany."""
    user = await _auth(ctx)

    async with async_session_factory() as db:
        items = await projects_service.list_projects(db, user)

    return [
        {
            "name": item.project.name,
            "slug": item.project.slug,
            "role": item.role,
            "created_at": item.project.created_at.isoformat(),
        }
        for item in items
    ]


@mcp.tool(annotations=READ_ONLY)
async def get_project(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Pelne szczegoly projektu: nazwa, opis, czlonkowie, statystyki, aktywny sprint."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        summary = await projects_service.get_project(db, user, project)

    return {
        "id": str(summary.project.id),
        "name": summary.project.name,
        "slug": summary.project.slug,
        "description": summary.project.description,
        "created_at": summary.project.created_at.isoformat(),
        "role": summary.role,
        "members_count": summary.members_count,
        "tickets_count": summary.tickets_count,
        "active_sprint": summary.active_sprint,
        "monitors_count": summary.monitors_count,
        "heartbeats_count": summary.heartbeats_count,
    }


@mcp.tool(annotations=WRITE)
async def update_project(
    ctx: Context[Any, Any],
    project_slug: str,
    name: str | None = None,
    description: str | None = None,
    new_slug: str | None = None,
) -> dict[str, Any]:
    """Aktualizuj projekt. Podaj tylko pola do zmiany. Wymaga roli owner lub admin."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        proj = await projects_service.update_project(db, user, project, name=name, description=description, new_slug=new_slug)

        return {
            "id": str(proj.id),
            "name": proj.name,
            "slug": proj.slug,
            "code": proj.code,
            "description": proj.description,
            "created_at": proj.created_at.isoformat(),
        }


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_project(
    ctx: Context[Any, Any],
    project_slug: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Usun projekt (soft delete). Wymaga potwierdzenia confirm=true oraz roli owner."""
    if not confirm:
        raise ValueError("Aby usunac projekt, podaj confirm=true")

    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await projects_service.delete_project(db, user, project)

    return {"message": f"Projekt '{project.name}' usuniety", "deleted_at": datetime.now(UTC).isoformat()}


@mcp.tool(annotations=WRITE)
async def invite_member(
    ctx: Context[Any, Any],
    project_slug: str,
    email: str,
    role: str = "member",
) -> dict[str, Any]:
    """Zaprosz nowa osobe do projektu lub dodaj istniejacego uzytkownika jako czlonka.

    Parametry:
    - project_slug: slug projektu, do ktorego zapraszamy
    - email: adres email osoby do zaproszenia
    - role: rola w projekcie — "member" (domyslnie) lub "admin"

    Wymaga roli owner lub admin w projekcie.

    Zachowanie:
    - Jesli uzytkownik z danym emailem juz istnieje w systemie — zostaje dodany
      bezposrednio jako czlonek projektu (bez wysylki emaila).
    - Jesli uzytkownik NIE istnieje — zostaje utworzone nowe konto (bez hasla),
      generowany jest token zaproszenia wazny 7 dni, a na podany email wysylany
      jest link do ustawienia hasla.
    - Nie mozna zaprosic osoby, ktora juz jest czlonkiem projektu.

    Rola nadawana przy zaproszeniu nie moze miec wiecej uprawnien niz zapraszajacy.
    Token zaproszenia trafia wylacznie mailem, nie jest zwracany.

    Zwraca: { message, user_email, role } oraz opcjonalnie
    { expires_at } jesli wyslano zaproszenie emailowe.
    """
    calling_user, _calling_member, project = await _get_user_member_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        invite = await projects_service.invite_member(db, calling_user, project, email, role=role)

    if not invite.is_new_user:
        return {
            "message": f"Uzytkownik '{invite.email}' zostal dodany do projektu '{project_slug}' jako {invite.role}.",
            "user_email": invite.email,
            "role": invite.role,
        }

    # Wysylaj email z zaproszeniem
    assert invite.invitation_token is not None and invite.expires_at is not None
    send_invitation_email(invite.email, "", invite.invitation_token)

    return {
        "message": (
            f"Zaproszenie wyslane na adres '{invite.email}'. "
            f"Uzytkownik zostanie dodany do projektu '{project_slug}' jako {invite.role} po aktywacji konta."
        ),
        "user_email": invite.email,
        "role": invite.role,
        "expires_at": invite.expires_at.isoformat(),
    }


@mcp.tool(annotations=WRITE)
async def create_project(
    ctx: Context[Any, Any],
    name: str,
    slug: str | None = None,
    description: str | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    """Tworzy nowy projekt w Monolynx.

    Parametry:
    - name: wymagany, nazwa projektu (np. "Anna Miastkowska")
    - slug: opcjonalny, jesli nie podany — generowany automatycznie z nazwy
    - description: opcjonalny, krotki opis projektu
    - code: opcjonalny, unikalny kod projektu (2-10 wielkich liter/cyfr, np. "PIM"); jesli nie podany — generowany ze sluga

    Zwraca: { id, name, slug, description, code, created_at, message }
    """
    user = await _auth(ctx)

    async with async_session_factory() as db:
        project = await projects_service.create_project(db, user, name, slug=slug, description=description, code=code)

    return {
        "id": str(project.id),
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "code": project.code,
        "created_at": project.created_at.isoformat(),
        "message": f"Projekt '{project.name}' zostal utworzony pomyslnie.",
    }


@mcp.tool(annotations=READ_ONLY)
async def list_members(
    ctx: Context[Any, Any],
    project_slug: str,
) -> list[dict[str, Any]]:
    """Lista czlonkow projektu z ich rolami i emailami.

    Zwraca: lista obiektow { user_id, name, email, role, joined_at }.
    Sortowanie: owner na gorze, potem admin, potem member — kazda grupa alfabetycznie po name.
    Wymaga czlonkostwa w projekcie.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        members = await projects_service.list_members(db, _user, project)

    return [
        {
            "user_id": str(m.user_id),
            "name": m.name,
            "email": m.email,
            "role": m.role,
            "joined_at": m.joined_at.isoformat(),
        }
        for m in members
    ]


@mcp.tool(annotations=DESTRUCTIVE)
async def remove_member(
    ctx: Context[Any, Any],
    project_slug: str,
    email: str,
) -> dict[str, Any]:
    """Usun czlonka z projektu.

    Parametry:
    - project_slug: slug projektu
    - email: adres email osoby do usuniecia

    Wymaga roli owner lub admin w projekcie.
    Nie mozna usunac ownera projektu.
    Tickety przypisane do usuwanej osoby pozostaja bez zmian (assignee nie jest czyszczony).

    Zwraca: { message }
    """
    calling_user, _calling_member, project = await _get_user_member_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await projects_service.remove_member(db, calling_user, project, email)

    return {"message": f"Uzytkownik '{email.strip().lower()}' zostal usuniety z projektu '{project_slug}'"}


# --- 500ki (Error Tracking) ---


def _format_issues_table(issues: list[Any], page: int, total_pages: int, total: int) -> str:
    """Formatuje liste issues jako kompaktowy string oszczedzajacy tokeny."""
    lines = [f"{total} issues (page {page}/{total_pages})", ""]
    if not issues:
        lines.append("(brak wynikow)")
        return "\n".join(lines)

    header = f"{'ID':<36} | {'Title':<40} | {'Level':<7} | {'Status':<10} | {'Events':>6} | {'First Seen':>10} | {'Last Seen':>10}"
    lines.append(header)
    lines.append("-" * len(header))

    for i in issues:
        issue_id = str(i.id)
        title = (i.title or "")[:40]
        level = (i.level or "")[:7]
        status = (i.status or "")[:10]
        events = i.event_count or 0
        first_seen = i.first_seen.strftime("%Y-%m-%d") if i.first_seen else ""
        last_seen = i.last_seen.strftime("%Y-%m-%d") if i.last_seen else ""
        lines.append(f"{issue_id:<36} | {title:<40} | {level:<7} | {status:<10} | {events:>6} | {first_seen:>10} | {last_seen:>10}")

    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def list_issues(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str = "unresolved",
    search: str | None = None,
    page: int = 1,
) -> str:
    """Lista bledow (issues) w projekcie jako kompaktowa tabela tekstowa.

    Filtrowanie po statusie (unresolved/resolved) i tekscie.
    Paginacja po 20. Domyslnie tylko nierozwiazane.
    ID w tabeli to pelne UUID uzywane w get_issue.
    Wymaga 500ki:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)
    per_page = 20

    async with async_session_factory() as db:
        await _require(db, user, project, "500ki", "read")
        conditions: list[Any] = [Issue.project_id == project.id]
        if status in ("unresolved", "resolved"):
            conditions.append(Issue.status == status)
        if search:
            conditions.append(Issue.title.ilike(f"%{search}%"))

        total = (await db.execute(select(func.count(Issue.id)).where(*conditions))).scalar() or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        result = await db.execute(select(Issue).where(*conditions).order_by(Issue.last_seen.desc()).limit(per_page).offset((page - 1) * per_page))
        issues = result.scalars().all()

    return _format_issues_table(list(issues), page, total_pages, total)


def _format_event_compact(event: Event) -> str:
    """Wyciagnij kluczowe info z JSONB eventu i zwroc jako kompaktowy string."""
    lines: list[str] = []

    ts = event.timestamp.strftime("%Y-%m-%d %H:%M")
    exc = event.exception or {}
    exc_type = exc.get("type") or exc.get("exception_type") or "Exception"
    exc_msg = exc.get("value") or exc.get("message") or exc.get("exception_value") or ""
    lines.append(f"[{ts}] {exc_type}: {exc_msg}" if exc_msg else f"[{ts}] {exc_type}")

    frames: list[Any] = []
    stacktrace = exc.get("stacktrace")
    if isinstance(stacktrace, dict):
        frames = stacktrace.get("frames", [])
    elif "frames" in exc:
        frames = exc["frames"]
    if frames:
        tail = frames[-4:]
        parts = [f"{f.get('filename', '?')}:{f.get('function', '?')}:{f.get('lineno', '?')}" for f in tail]
        lines.append(f"  at {' -> '.join(parts)}")

    req = event.request_data or {}
    method = req.get("method") or req.get("request_method") or ""
    path = req.get("url") or req.get("path") or req.get("request_url") or ""
    status = req.get("status_code") or req.get("response_status") or ""
    if method or path:
        req_line = f"  {method} {path}".strip()
        if status:
            req_line += f" {status}"
        lines.append(req_line)

    env = event.environment or {}
    env_name = env.get("environment") or env.get("env") or ""
    python_ver = env.get("python_version") or env.get("python") or env.get("runtime_version") or ""
    env_parts = [p for p in [env_name, f"python {python_ver}" if python_ver else ""] if p]
    if env_parts:
        lines.append(f"  env: {' | '.join(env_parts)}")

    return "\n".join(lines)


def _format_issue(issue: Issue, events: list[Event]) -> str:
    """Konwertuj Issue + eventy na kompaktowy string oszczedzajacy tokeny."""
    culprit = f" | {issue.culprit}" if issue.culprit else ""
    lines: list[str] = [
        f"Issue #{issue.id} | {issue.title}{culprit} | level: {issue.level} | status: {issue.status}",
    ]

    first = issue.first_seen.strftime("%Y-%m-%d")
    last = issue.last_seen.strftime("%Y-%m-%d")
    source = getattr(issue, "source", "auto")
    lines.append(f"Events: {issue.event_count} | First: {first} | Last: {last} | Source: {source}")

    if events:
        lines.append(f"\n## Latest Events ({len(events)})")
        for e in events:
            lines.append("")
            lines.append(_format_event_compact(e))

    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def get_issue(
    ctx: Context[Any, Any],
    project_slug: str,
    issue_id: str,
) -> str:
    """Szczegoly bledu z ostatnimi 5 eventami w kompaktowym formacie tekstowym.

    Zwraca naglowek z ID/title/level/status, statystyki zdarzen oraz do 5 ostatnich
    eventow z: exception type+message, kompaktowym stack trace (ostatnie 3-4 ramki),
    request method+path+status i srodowiskiem.
    ID issue jest widoczne w pierwszej linii - uzywaj go w update_issue_status.
    Wymaga 500ki:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "500ki", "read")
        result = await db.execute(
            select(Issue).where(
                Issue.id == uuid.UUID(issue_id),
                Issue.project_id == project.id,
            )
        )
        issue = result.scalar_one_or_none()
        if issue is None:
            raise ValueError("Issue nie istnieje")

        events_result = await db.execute(select(Event).where(Event.issue_id == issue.id).order_by(Event.timestamp.desc()).limit(5))
        events = events_result.scalars().all()

    return _format_issue(issue, list(events))


@mcp.tool(annotations=WRITE)
async def update_issue_status(
    ctx: Context[Any, Any],
    project_slug: str,
    issue_id: str,
    status: str,
) -> dict[str, Any]:
    """Zmien status bledu (unresolved/resolved).

    Wymaga 500ki:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "500ki", "write")

    if status not in ("unresolved", "resolved"):
        raise ValueError("Status musi byc 'unresolved' lub 'resolved'")

    async with async_session_factory() as db:
        result = await db.execute(
            select(Issue).where(
                Issue.id == uuid.UUID(issue_id),
                Issue.project_id == project.id,
            )
        )
        issue = result.scalar_one_or_none()
        if issue is None:
            raise ValueError("Issue nie istnieje")

        issue.status = status
        await db.commit()

    return {
        "id": str(issue.id),
        "title": issue.title,
        "status": status,
        "message": f"Issue '{issue.title}' — status zmieniony na {status}",
    }


@mcp.tool(annotations=WRITE)
async def create_issue(
    ctx: Context[Any, Any],
    project_slug: str,
    title: str,
    description: str | None = None,
    severity: str = "medium",
    environment: str | None = None,
    traceback: str | None = None,
) -> dict[str, Any]:
    """Recznie tworzy blad (issue) w projekcie 500ki.

    Umozliwia zgłoszenie bugu przez AI bez generowania prawdziwego bledu 500.
    Parametry:
    - title: krotki opis bledu (wymagany)
    - description: szczegolowy opis (opcjonalny)
    - severity: low / medium / high / critical (domyslnie medium)
    - environment: production / staging / development (opcjonalny)
    - traceback: stack trace jako string (opcjonalny)

    Wymaga 500ki:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "500ki", "write")

    title_stripped = title.strip() if title else ""
    if not title_stripped:
        raise ValueError("Tytul bledu nie moze byc pusty")

    if len(title_stripped) > 512:
        raise ValueError("Tytul nie moze przekraczac 512 znakow")

    allowed_severities = {"low", "medium", "high", "critical"}
    if severity not in allowed_severities:
        raise ValueError(f"Severity musi byc jednym z: {', '.join(sorted(allowed_severities))}")

    allowed_environments = {"production", "staging", "development"}
    if environment is not None and environment not in allowed_environments:
        raise ValueError(f"Environment musi byc jednym z: {', '.join(sorted(allowed_environments))}")

    fingerprint = f"manual-{uuid.uuid4().hex}"

    exception_data: dict[str, Any] = {
        "type": title_stripped,
        "value": description or "",
        "severity": severity,
    }
    if traceback:
        exception_data["traceback"] = traceback

    environment_data: dict[str, Any] | None = None
    if environment:
        environment_data = {"environment": environment}

    now = datetime.now(tz=UTC)

    new_issue = Issue(
        project_id=project.id,
        fingerprint=fingerprint,
        title=title_stripped,
        culprit=None,
        level=severity,
        status="unresolved",
        event_count=1,
        source="manual",
    )

    async with async_session_factory() as db:
        try:
            db.add(new_issue)
            await db.flush()

            new_event = Event(
                issue_id=new_issue.id,
                timestamp=now,
                exception=exception_data,
                request_data=None,
                environment=environment_data,
            )
            db.add(new_event)
            await db.commit()
            await db.refresh(new_issue)
        except IntegrityError as exc:
            await db.rollback()
            raise ValueError("Nie udalo sie utworzyc issue — konflikt fingerprint") from exc

    return {
        "id": str(new_issue.id),
        "title": new_issue.title,
        "status": new_issue.status,
        "severity": new_issue.level,
        "source": new_issue.source,
        "created_at": new_issue.first_seen.isoformat(),
    }


# --- Monitoring ---


@mcp.tool(annotations=READ_ONLY)
async def list_monitors(
    ctx: Context[Any, Any],
    project_slug: str,
) -> str:
    """Lista monitorow URL z aktualnym statusem i uptime 24h.

    Zwraca kompaktowa tabele w formacie:
        ID | Name | URL | Active | Interval | Uptime 24h | Last Check
    gdzie Last Check = 'OK/FAIL status_code response_time timestamp' lub '--' jesli brak.
    Active: yes/no. Interval: '5 min', '1 hr', '1 day'.
    Wymaga monitoring:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

    async with async_session_factory() as db:
        await _require(db, user, project, "monitoring", "read")
        result = await db.execute(select(Monitor).where(Monitor.project_id == project.id).order_by(Monitor.created_at))
        monitors = result.scalars().all()

        monitor_data = []
        for m in monitors:
            # Ostatni check
            last_check_result = await db.execute(
                select(MonitorCheck).where(MonitorCheck.monitor_id == m.id).order_by(MonitorCheck.checked_at.desc()).limit(1)
            )
            last_check = last_check_result.scalar_one_or_none()

            # Uptime 24h
            uptime_result = await db.execute(
                select(
                    func.count(MonitorCheck.id),
                    func.count(case((MonitorCheck.is_success.is_(True), 1))),
                ).where(
                    MonitorCheck.monitor_id == m.id,
                    MonitorCheck.checked_at >= twenty_four_hours_ago,
                )
            )
            uptime_row = uptime_result.one()
            total_checks: int = uptime_row[0]
            success_checks: int = uptime_row[1]
            uptime_24h: float | None = None
            if total_checks > 0:
                uptime_24h = round((success_checks / total_checks) * 100, 1)

            monitor_data.append(
                {
                    "id": str(m.id),
                    "name": m.name or m.url,
                    "url": m.url,
                    "interval": f"{m.interval_value} {m.interval_unit}",
                    "is_active": m.is_active,
                    "last_check": {
                        "is_success": last_check.is_success,
                        "status_code": last_check.status_code,
                        "response_time_ms": last_check.response_time_ms,
                        "checked_at": last_check.checked_at.isoformat(),
                    }
                    if last_check
                    else None,
                    "uptime_24h": uptime_24h,
                }
            )

    return _format_monitors_table(monitor_data)


@mcp.tool(annotations=READ_ONLY)
async def get_monitor(
    ctx: Context[Any, Any],
    project_slug: str,
    monitor_id: str,
) -> str:
    """Szczegoly monitora z ostatnimi 20 checkami w kompaktowym formacie.

    Zwraca string z danymi monitora i historia ostatnich 20 checków:
        Monitor: API Health | https://api.example.com/health
        ID: 550e8400-e29b-41d4-a716-446655440000
        Active: yes | Interval: 5 min | Uptime 24h: 99.8%

        ## Check History (20)
        Timestamp           | Status | Code | Response Time | Error
        2026-03-15 14:22:00 | OK     | 200  | 45ms          |
        2026-03-15 14:12:00 | FAIL   | 503  | --            | Connection refused

    Wymaga monitoring:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

    async with async_session_factory() as db:
        await _require(db, user, project, "monitoring", "read")
        result = await db.execute(
            select(Monitor).where(
                Monitor.id == uuid.UUID(monitor_id),
                Monitor.project_id == project.id,
            )
        )
        monitor = result.scalar_one_or_none()
        if monitor is None:
            raise ValueError("Monitor nie istnieje")

        checks_result = await db.execute(
            select(MonitorCheck).where(MonitorCheck.monitor_id == monitor.id).order_by(MonitorCheck.checked_at.desc()).limit(20)
        )
        checks = list(checks_result.scalars().all())

        # Uptime 24h
        uptime_result = await db.execute(
            select(
                func.count(MonitorCheck.id),
                func.count(case((MonitorCheck.is_success.is_(True), 1))),
            ).where(
                MonitorCheck.monitor_id == monitor.id,
                MonitorCheck.checked_at >= twenty_four_hours_ago,
            )
        )
        uptime_row = uptime_result.one()
        total_checks: int = uptime_row[0]
        success_checks: int = uptime_row[1]
        uptime_24h: float | None = None
        if total_checks > 0:
            uptime_24h = round((success_checks / total_checks) * 100, 1)

    return _format_monitor_detail(monitor, checks, uptime_24h)


@mcp.tool(annotations=WRITE)
async def create_monitor(
    ctx: Context[Any, Any],
    project_slug: str,
    name: str,
    url: str,
    interval_value: int = 5,
    interval_unit: str = "minutes",
) -> dict[str, Any]:
    """Tworzy nowy monitor URL w projekcie.

    interval_unit: minutes | hours | days (domyslnie: minutes)
    interval_value: 1-60 (domyslnie: 5)
    Wymaga monitoring:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "monitoring", "write")

    # Walidacja nazwy
    name = name.strip()
    if not name:
        raise ValueError("Nazwa monitora nie moze byc pusta")

    # Walidacja URL scheme
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL musi zaczynac sie od http:// lub https://")

    # SSRF protection
    ssrf_error = _is_url_safe(url)
    if ssrf_error:
        raise ValueError(f"Niedozwolony URL: {ssrf_error}")

    # Walidacja interval_unit
    if interval_unit not in INTERVAL_UNITS:
        raise ValueError(f"interval_unit musi byc jednym z: {', '.join(INTERVAL_UNITS)}")

    # Walidacja interval_value
    if not (1 <= interval_value <= 60):
        raise ValueError("interval_value musi byc liczba od 1 do 60")

    async with async_session_factory() as db:
        # Limit monitorow na projekt
        count_result = await db.execute(select(func.count(Monitor.id)).where(Monitor.project_id == project.id))
        monitor_count: int = count_result.scalar_one()
        if monitor_count >= 20:
            raise ValueError("Osiagnieto limit 20 monitorow na projekt")

        monitor = Monitor(
            project_id=project.id,
            name=name,
            url=url,
            interval_value=interval_value,
            interval_unit=interval_unit,
            is_active=True,
        )
        db.add(monitor)
        await db.commit()
        await db.refresh(monitor)

    return {
        "id": str(monitor.id),
        "name": monitor.name,
        "url": monitor.url,
        "interval_value": monitor.interval_value,
        "interval_unit": monitor.interval_unit,
        "is_active": monitor.is_active,
        "created_at": monitor.created_at.isoformat(),
    }


@mcp.tool(annotations=WRITE)
async def update_monitor(
    ctx: Context[Any, Any],
    project_slug: str,
    monitor_id: str,
    name: str | None = None,
    url: str | None = None,
    interval_value: int | None = None,
    interval_unit: str | None = None,
) -> dict[str, Any]:
    """Aktualizuj istniejacy monitor URL. Podaj tylko pola do zmiany.

    interval_unit: minutes | hours | days
    interval_value: 1-60
    Wymaga monitoring:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "monitoring", "write")

    # Walidacja przed otwarciem sesji DB
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Nazwa monitora nie moze byc pusta")
        if len(name) > 255:
            raise ValueError("Nazwa monitora moze miec maksymalnie 255 znakow")

    if url is not None:
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL musi zaczynac sie od http:// lub https://")
        if len(url) > 2048:
            raise ValueError("URL moze miec maksymalnie 2048 znakow")
        ssrf_error = _is_url_safe(url)
        if ssrf_error:
            raise ValueError(f"Niedozwolony URL: {ssrf_error}")

    if interval_unit is not None and interval_unit not in INTERVAL_UNITS:
        raise ValueError(f"interval_unit musi byc jednym z: {', '.join(INTERVAL_UNITS)}")

    if interval_value is not None and not (1 <= interval_value <= 60):
        raise ValueError("interval_value musi byc liczba od 1 do 60")

    async with async_session_factory() as db:
        result = await db.execute(
            select(Monitor).where(
                Monitor.id == uuid.UUID(monitor_id),
                Monitor.project_id == project.id,
            )
        )
        monitor = result.scalar_one_or_none()
        if monitor is None:
            raise ValueError("Monitor nie istnieje")

        if name is not None:
            monitor.name = name

        if url is not None:
            monitor.url = url

        if interval_value is not None:
            monitor.interval_value = interval_value

        if interval_unit is not None:
            monitor.interval_unit = interval_unit

        await db.commit()
        await db.refresh(monitor)

    return {
        "id": str(monitor.id),
        "name": monitor.name,
        "url": monitor.url,
        "interval_value": monitor.interval_value,
        "interval_unit": monitor.interval_unit,
        "is_active": monitor.is_active,
        "created_at": monitor.created_at.isoformat(),
    }


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_monitor(
    ctx: Context[Any, Any],
    project_slug: str,
    monitor_id: str,
) -> dict[str, Any]:
    """Usuwa monitor URL z projektu (wraz z historia checkow). Wymaga roli owner lub admin."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        # Sprawdz uprawnienia - monitoring:delete
        if not await check_permission(db, user.id, project.id, "monitoring", "delete"):
            raise ValueError("Brak uprawnienia do usuniecia monitora (monitoring:delete)")

        result = await db.execute(
            select(Monitor).where(
                Monitor.id == uuid.UUID(monitor_id),
                Monitor.project_id == project.id,
            )
        )
        monitor = result.scalar_one_or_none()
        if monitor is None:
            raise ValueError("Monitor nie istnieje")

        monitor_name = monitor.name or monitor.url
        await db.delete(monitor)
        await db.commit()

    deleted_at = datetime.now(UTC).isoformat()
    return {"message": f"Monitor '{monitor_name}' usuniety", "deleted_at": deleted_at}


# --- Scrum: Tablica Kanban ---


@mcp.tool(annotations=READ_ONLY)
async def get_board(
    ctx: Context[Any, Any],
    project_slug: str,
) -> str:
    """Tablica Kanban — tickety aktywnego sprintu pogrupowane po statusie.

    Zwraca kompaktowy tekstowy widok tablicy.
    Kolumny: Todo, In Progress, In Review, Done.
    Format wiersza: CODE-N | Tytul | priority | @assignee | Nsp [label1, label2]
    Jesli brak aktywnego sprintu — zwraca "(Brak aktywnego sprintu)".
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await tickets_service.get_board(db, user, project)

    if result.sprint is None:
        return "(Brak aktywnego sprintu)"

    return _format_board(result.sprint, project.code, result.columns)


# --- Podsumowanie projektu ---


@mcp.tool(annotations=READ_ONLY)
async def get_project_summary(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Zagregowane statystyki projektu: otwarte bledy, monitory, aktywny sprint."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        summary = await projects_service.get_project_summary(db, user, project)

    return {
        "project": {"name": project.name, "slug": project.slug},
        "issues_unresolved": summary.issues_unresolved,
        "monitors_failing": summary.monitors_failing,
        "uptime_24h": summary.uptime_24h,
        "active_sprint": summary.active_sprint,
        "backlog_count": summary.backlog_count,
    }


# --- Tickety ---


@mcp.tool(annotations=READ_ONLY)
async def list_tickets(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    sprint_id: str | None = None,
    due_date_before: str | None = None,
    due_date_after: str | None = None,
    overdue: bool = False,
    label_id: str | None = None,
    page: int = 1,
) -> str:
    """Lista ticketow w projekcie — kompaktowy format pipe-separated.

    Pierwsza linia: "<total> tickets (page <page>/<total_pages>)"
    Naglowek kolumn + wiersze: Key | Title | Status | Pri | Assignee | Sprint | SP | Due | Labels | Blocked by
    (Blocked by: klucze blokerow rosnaco po numerze, `--` gdy brak)

    Filtrowanie po statusie, priorytecie, sprincie, tekscie, dacie granicznej i etykiecie.
    due_date_before: tickety z due_date <= data (YYYY-MM-DD)
    due_date_after: tickety z due_date >= data (YYYY-MM-DD)
    overdue: True = tylko tickety po terminie (due_date < dzisiaj, status != done)
    label_id: UUID etykiety — filtruj tickety z tym labelem
    Paginacja po 20.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await tickets_service.list_tickets(
            db,
            user,
            project,
            status=status,
            priority=priority,
            search=search,
            sprint_id=sprint_id,
            due_date_before=due_date_before,
            due_date_after=due_date_after,
            overdue=overdue,
            label_id=label_id,
            page=page,
        )

    return _format_tickets_table(
        tickets=result.tickets,
        project_code=project.code,
        page=result.page,
        total_pages=result.total_pages,
        total=result.total,
    )


@mcp.tool(annotations=READ_ONLY)
async def search_tickets(
    ctx: Context[Any, Any],
    project_slug: str,
    query: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee_email: str | None = None,
    sprint_id: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """Wyszukaj tickety w projekcie z filtrami i paginacja.

    query: ILIKE search po tytule i opisie
    status: todo | in_progress | in_review | done | backlog
    priority: low | medium | high
    assignee_email: email przypisanej osoby
    sprint_id: UUID sprintu
    due_before: tickety z due_date <= data (YYYY-MM-DD)
    due_after: tickety z due_date >= data (YYYY-MM-DD)
    page: strona wynikow (domyslnie 1), 20 wynikow na strone
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await tickets_service.search_tickets(
            db,
            user,
            project,
            query=query,
            status=status,
            priority=priority,
            assignee_email=assignee_email,
            sprint_id=sprint_id,
            due_before=due_before,
            due_after=due_after,
            page=page,
        )

    return {
        "results": result.results,
        "total": result.total,
        "page": result.page,
        "total_pages": result.total_pages,
    }


@mcp.tool(annotations=READ_ONLY)
async def get_ticket(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
) -> str:
    """Szczegoly ticketa z komentarzami i zalacznikami w kompaktowym formacie tekstowym.

    ticket_id: UUID ticketa lub klucz (np. MNX-12).

    Zwraca jednolinijkowy naglowek z kluczem i tytulem, metadane (status, priority, sprint,
    assignee, story points, due date, labels, daty, flaga AI, UUID), opcjonalny opis,
    liste zalacznikow oraz komentarze.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        detail_result = await tickets_service.get_ticket(db, user, project, ticket_id)

    ticket = detail_result.ticket
    detail = _format_ticket_detail(ticket, project.code)

    if detail_result.can_read_settlements and ticket.settlements:
        active_settlements = [s for s in ticket.settlements if s.is_active]
        if active_settlements:
            lines = [detail, "", "## Rozliczenia"]
            for s in active_settlements:
                period = f"{s.period_from} - {s.period_to}"
                lines.append(f"- ROZ-{s.number} | {s.name} | {s.status} | {period} | ID: {s.id}")
            detail = "\n".join(lines)

    return detail


@mcp.tool(annotations=WRITE)
async def create_ticket(
    ctx: Context[Any, Any],
    project_slug: str,
    title: str,
    description: str | None = None,
    priority: str = "medium",
    story_points: int | None = None,
    sprint_id: str | None = None,
    assignee_email: str | None = None,
    due_date: str | None = None,
    label_ids: list[str] | None = None,
    acceptance_criteria: list[str] | None = None,
    spec_page_id: str | None = None,
    blocked_by_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Utworz nowy ticket w projekcie. Oznaczany jako created_via_ai=True.

    due_date: opcjonalna data graniczna w formacie YYYY-MM-DD
    label_ids: opcjonalna lista UUID etykiet do przypisania
    acceptance_criteria: opcjonalna lista opisów kryteriów akceptacji — tworzone razem z ticketem, created_via_ai=True
    spec_page_id: opcjonalny UUID strony wiki jako specyfikacja techniczna ticketa
    blocked_by_ids: opcjonalna lista UUID ticketow-blokerow (musza byc w tym samym projekcie, bez self-reference i cyklu bezposredniego)
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        create_result = await tickets_service.create_ticket(
            db,
            user,
            project,
            title,
            description=description,
            priority=priority,
            story_points=story_points,
            sprint_id=sprint_id,
            assignee_email=assignee_email,
            due_date=due_date,
            label_ids=label_ids,
            acceptance_criteria=acceptance_criteria,
            spec_page_id=spec_page_id,
            blocked_by_ids=blocked_by_ids,
        )

    ticket = create_result.ticket
    return {
        "id": str(ticket.id),
        "key": f"{project.code}-{ticket.number}",
        "title": ticket.title,
        "status": ticket.status,
        "due_date": ticket.due_date.isoformat() if ticket.due_date else None,
        "spec_page_id": str(ticket.spec_page_id) if ticket.spec_page_id else None,
        "created_via_ai": True,
        "acceptance_criteria_count": create_result.criteria_count,
        "message": f"Ticket '{ticket.title}' utworzony",
    }


@mcp.tool(annotations=WRITE)
async def update_ticket(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    story_points: int | None = None,
    sprint_id: str | None = None,
    assignee_email: str | None = None,
    due_date: str | None = None,
    label_ids: list[str] | None = None,
    blocked_by_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Aktualizuj istniejacy ticket. Podaj tylko pola do zmiany.

    ticket_id: UUID ticketa lub klucz (np. MNX-12).
    assignee_email: email uzytkownika, pusty string aby wyczysc, lub "me" dla aktualnego uzytkownika
    due_date: data graniczna w formacie YYYY-MM-DD, lub pusty string aby wyczysc
    label_ids: lista UUID etykiet — zastepuje wszystkie poprzednie etykiety; [] = usun etykiety
    blocked_by_ids: lista UUID ticketow-blokerow — zastepuje wszystkie poprzednie; [] = usun blokery; brak = bez zmian
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        ticket = await tickets_service.update_ticket(
            db,
            user,
            project,
            ticket_id,
            title=title,
            description=description,
            status=status,
            priority=priority,
            story_points=story_points,
            sprint_id=sprint_id,
            assignee_email=assignee_email,
            due_date=due_date,
            label_ids=label_ids,
            blocked_by_ids=blocked_by_ids,
        )

    return {
        "id": str(ticket.id),
        "key": f"{project.code}-{ticket.number}",
        "title": ticket.title,
        "status": ticket.status,
        "due_date": ticket.due_date.isoformat() if ticket.due_date else None,
        "message": f"Ticket '{ticket.title}' zaktualizowany",
    }


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_ticket(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
) -> dict[str, Any]:
    """Usun ticket z projektu. ticket_id: UUID lub klucz (np. MNX-12)."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        title = await tickets_service.delete_ticket(db, user, project, ticket_id)

    return {"message": f"Ticket '{title}' usuniety"}


# --- Labele ---


@mcp.tool(annotations=READ_ONLY)
async def list_labels(
    ctx: Context[Any, Any],
    project_slug: str,
) -> list[dict[str, Any]]:
    """Lista etykiet (labels) projektu z liczba powiazanych ticketow.

    Zwraca: lista obiektow { id, name, color, tickets_count }.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        labels = await projects_service.list_labels(db, user, project)

    return [
        {
            "id": str(lb.id),
            "name": lb.name,
            "color": lb.color,
            "tickets_count": lb.tickets_count,
        }
        for lb in labels
    ]


@mcp.tool(annotations=WRITE)
async def create_label(
    ctx: Context[Any, Any],
    project_slug: str,
    name: str,
    color: str | None = None,
) -> dict[str, Any]:
    """Tworzy nowa etykiete (label) w projekcie.

    Parametry:
    - name: nazwa etykiety (wymagana, max 100 znakow)
    - color: kolor w formacie hex np. "#e74c3c" (opcjonalny, domyslnie losowy z palety)

    Zwraca: { id, name, color, message }
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        label = await projects_service.create_label(db, user, project, name, color=color)

    return {
        "id": str(label.id),
        "name": label.name,
        "color": label.color,
        "message": f"Etykieta '{label.name}' zostala utworzona.",
    }


@mcp.tool(annotations=WRITE)
async def bulk_update_tickets(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_ids: list[str],
    status: str | None = None,
    priority: str | None = None,
    assignee_email: str | None = None,
    sprint_id: str | None = None,
    due_date: str | None = None,
) -> dict[str, Any]:
    """Masowa aktualizacja ticketow. Aktualizuje status, priorytet, assignee lub sprint.

    ticket_ids: lista UUID lub kluczy (np. MNX-12) ticketow do aktualizacji (max 100)
    assignee_email: pusty string = usun assignee
    sprint_id: pusty string = przesun do backlogu
    due_date: data YYYY-MM-DD lub pusty string = usun date
    Zwraca: {"updated": N, "failed": [{"id": "...", "reason": "..."}]}
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await tickets_service.bulk_update_tickets(
            db,
            user,
            project,
            ticket_ids,
            status=status,
            priority=priority,
            assignee_email=assignee_email,
            sprint_id=sprint_id,
            due_date=due_date,
        )

    return {
        "updated": result.updated,
        "failed": result.failed,
    }


@mcp.tool(annotations=WRITE)
async def create_ticket_from_issue(
    ctx: Context[Any, Any],
    project_slug: str,
    issue_id: str,
    sprint_id: str | None = None,
    priority: str = "medium",
    story_points: int | None = None,
) -> dict[str, Any]:
    """Tworzy ticket Scrum powiazany z bledem 500ki.

    Automatycznie wypelnia tytul i opis na podstawie danych issue.
    Jesli sprint_id nie podano, ticket laduje w backlogu.
    Wymaga 500ki:write i scrum:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        # scrum:write first: the MON-241 contract expects this message for roles lacking both
        await _require(db, user, project, "scrum", "write")
        await _require(db, user, project, "500ki", "write")

        if priority not in PRIORITIES:
            priority = "medium"

        result = await db.execute(
            select(Issue)
            .options(selectinload(Issue.tickets))
            .where(
                Issue.id == uuid.UUID(issue_id),
                Issue.project_id == project.id,
            )
        )
        issue = result.scalar_one_or_none()
        if issue is None:
            raise ValueError("Issue nie istnieje")

        if issue.tickets:
            existing_ticket = issue.tickets[0]
            ticket_key = f"{project.code}-{existing_ticket.number}"
            raise ValueError(f"Issue already has a linked ticket: {ticket_key}")

        event_result = await db.execute(select(Event).where(Event.issue_id == issue.id).order_by(Event.timestamp.desc()).limit(1))
        last_event = event_result.scalar_one_or_none()

        issue_title_parts = issue.title.split(": ", 1)
        exception_type = issue_title_parts[0] if issue_title_parts else issue.title

        ticket_title = f"[500ki] {issue.title}"
        if len(ticket_title) > 512:
            ticket_title = ticket_title[:509] + "..."

        traceback_text = ""
        request_url = "—"
        request_method = "—"
        environment_name = "—"

        if last_event is not None:
            exc_data = last_event.exception or {}
            if isinstance(exc_data, dict):
                stacktrace = exc_data.get("stacktrace") or {}
                frames = stacktrace.get("frames", []) if isinstance(stacktrace, dict) else []
                if frames:
                    lines = []
                    for frame in frames:
                        if isinstance(frame, dict):
                            filename = frame.get("filename", "")
                            function = frame.get("function", "?")
                            lineno = frame.get("lineno")
                            lineno_str = f":{lineno}" if lineno is not None else ""
                            lines.append(f'  File "{filename}"{lineno_str}, in {function}')
                            ctx_line = frame.get("context_line")
                            if ctx_line:
                                lines.append(f"    {ctx_line.strip()}")
                    traceback_text = "\n".join(lines)
                elif "traceback" in exc_data:
                    traceback_text = str(exc_data["traceback"])
                else:
                    exc_type = exc_data.get("type", "")
                    exc_value = exc_data.get("value", "")
                    traceback_text = f"{exc_type}: {exc_value}" if exc_type else str(exc_data)

            request_data = last_event.request_data or {}
            if isinstance(request_data, dict):
                request_url = request_data.get("url") or "—"
                request_method = request_data.get("method") or "—"

            env_data = last_event.environment or {}
            if isinstance(env_data, dict):
                environment_name = env_data.get("environment") or env_data.get("hostname") or "—"

        fingerprint_short = issue.fingerprint[:8] if issue.fingerprint else "?"
        first_seen_str = issue.first_seen.strftime("%Y-%m-%d %H:%M") if issue.first_seen else "—"
        last_seen_str = issue.last_seen.strftime("%Y-%m-%d %H:%M") if issue.last_seen else "—"

        description = (
            f"## Powiazany blad 500ki\n\n"
            f"**Issue:** [#{fingerprint_short}](/dashboard/{project_slug}/500ki/issues/{issue.id})"
            f" — {issue.title}\n"
            f"**Typ wyjatku:** `{exception_type}`\n"
            f"**Liczba wystapien:** {issue.event_count}\n"
            f"**Pierwsze wystapienie:** {first_seen_str}\n"
            f"**Ostatnie wystapienie:** {last_seen_str}\n\n"
            f"## Traceback\n\n"
            f"```\n{traceback_text}\n```\n\n"
            f"## Srodowisko\n\n"
            f"- **URL zadania:** {request_url}\n"
            f"- **Metoda:** {request_method}\n"
            f"- **Srodowisko:** {environment_name}\n"
        )

        resolved_sprint_id = uuid.UUID(sprint_id) if sprint_id else None
        if resolved_sprint_id is not None:
            await tickets_service.ensure_sprint_in_project(db, project.id, resolved_sprint_id)

        next_number = await get_next_ticket_number(project.id, db)

        ticket = Ticket(
            project_id=project.id,
            number=next_number,
            title=ticket_title,
            description=description,
            priority=priority,
            story_points=story_points,
            sprint_id=resolved_sprint_id,
            status="backlog" if resolved_sprint_id is None else "todo",
            issue_id=issue.id,
            created_via_ai=True,
        )
        db.add(ticket)
        await db.commit()
        await db.refresh(ticket)

    ticket_key = f"{project.code}-{ticket.number}"
    return {
        "ticket_id": str(ticket.id),
        "ticket_key": ticket_key,
        "url": f"/dashboard/{project_slug}/scrum/tickets/{ticket.id}",
    }


# --- Sprinty ---


@mcp.tool(annotations=READ_ONLY)
async def list_sprints(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Lista sprintow w projekcie ze statystykami (liczba ticketow, suma SP)."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        summaries = await tickets_service.list_sprints(db, user, project, status)

    return [
        {
            "id": str(item.sprint.id),
            "name": item.sprint.name,
            "goal": item.sprint.goal,
            "status": item.sprint.status,
            "start_date": item.sprint.start_date.isoformat(),
            "end_date": item.sprint.end_date.isoformat() if item.sprint.end_date else None,
            "ticket_count": item.ticket_count,
            "story_points_total": item.story_points_total,
            "created_at": item.sprint.created_at.isoformat(),
        }
        for item in summaries
    ]


@mcp.tool(annotations=READ_ONLY)
async def get_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
) -> str:
    """Szczegoly sprintu z lista ticketow w kompaktowym formacie tekstowym.

    Zwraca string z naglowkiem sprintu (ID, nazwa, status, daty, cel, story points)
    oraz tabela ticketow (klucz, tytul, status, priorytet, assignee, SP).
    ID sprintu jest zawarte w naglowku i mozna go uzyc do operacji start/complete.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        sprint = await tickets_service.get_sprint(db, user, project, sprint_id)

    tickets = [
        {
            "key": f"{project.code}-{t.number}",
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "story_points": t.story_points,
            "assignee": t.assignee.email if t.assignee else None,
        }
        for t in sprint.tickets
    ]

    return _format_sprint_detail(sprint, project.code, tickets)


@mcp.tool(annotations=WRITE)
async def create_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    name: str,
    start_date: str,
    goal: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Utworz nowy sprint (status: planning). start_date w formacie YYYY-MM-DD."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        sprint = await tickets_service.create_sprint(db, user, project, name, start_date, goal=goal, end_date=end_date)

    return {
        "id": str(sprint.id),
        "name": sprint.name,
        "status": sprint.status,
        "message": f"Sprint '{sprint.name}' utworzony",
    }


@mcp.tool(annotations=WRITE)
async def update_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
    name: str | None = None,
    goal: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Zaktualizuj istniejacy sprint. Podaj tylko pola do zmiany (PATCH semantics).

    Daty w formacie YYYY-MM-DD.
    Zmiana dat jest zablokowana dla sprintow o statusie 'completed'.
    Walidacja: end_date musi byc pozniejsza niz start_date.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        sprint = await tickets_service.update_sprint(
            db,
            user,
            project,
            sprint_id,
            name=name,
            goal=goal,
            start_date=start_date,
            end_date=end_date,
        )

    return {
        "id": str(sprint.id),
        "name": sprint.name,
        "goal": sprint.goal,
        "start_date": sprint.start_date.isoformat() if sprint.start_date else None,
        "end_date": sprint.end_date.isoformat() if sprint.end_date else None,
        "status": sprint.status,
        "created_at": sprint.created_at.isoformat() if sprint.created_at else None,
    }


@mcp.tool(annotations=WRITE)
async def start_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
) -> dict[str, Any]:
    """Rozpocznij sprint (zmiana statusu na active).

    Tylko jeden aktywny sprint na projekt.
    Wymaga scrum:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "scrum", "write")
        error = await svc_start_sprint(uuid.UUID(sprint_id), project.id, db)
        if error:
            raise ValueError(error)

    return {"message": "Sprint rozpoczety", "sprint_id": sprint_id}


@mcp.tool(annotations=WRITE)
async def complete_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
) -> dict[str, Any]:
    """Zakoncz sprint. Niedokonczone tickety wracaja do backloga.

    Wymaga scrum:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "scrum", "write")
        error = await svc_complete_sprint(uuid.UUID(sprint_id), project.id, db)
        if error:
            raise ValueError(error)

    return {"message": "Sprint zakonczony", "sprint_id": sprint_id}


# --- Komentarze ---


@mcp.tool(annotations=READ_ONLY)
async def list_comments(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
) -> list[dict[str, Any]]:
    """Lista komentarzy do ticketa. ticket_id: UUID lub klucz (np. MNX-12)."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        comments = await tickets_service.list_comments(db, user, project, ticket_id)

    return [
        {
            "id": str(c.id),
            "author": c.author.email,
            "content": c.content,
            "created_via_ai": c.created_via_ai,
            "created_at": c.created_at.isoformat(),
        }
        for c in comments
    ]


@mcp.tool(annotations=WRITE)
async def add_comment(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    content: str,
) -> dict[str, Any]:
    """Dodaj komentarz do ticketa. ticket_id: UUID lub klucz (np. MNX-12). Oznaczany jako created_via_ai=True."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        comment = await tickets_service.add_comment(db, user, project, ticket_id, content)

    return {
        "id": str(comment.id),
        "message": "Komentarz dodany",
        "created_via_ai": True,
    }


@mcp.tool(annotations=WRITE)
async def add_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    file_base64: str,
    filename: str,
    mime_type: str | None = None,
) -> dict[str, Any]:
    """Dodaj zalacznik do ticketa (screenshot, log, dokument). Plik zakodowany w base64.

    ticket_id: UUID ticketa lub klucz (np. MNX-12).
    Maksymalny rozmiar: 200MB. Zwraca attachment_id, filename, url, size, uploaded_at.
    Wymaga scrum:write.
    """
    max_size = 200 * 1024 * 1024  # 200MB

    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "scrum", "write")

    # Walidacja i sanityzacja filename
    if not filename or not filename.strip():
        raise ValueError("Nazwa pliku nie moze byc pusta")
    filename = os.path.basename(filename.strip())
    filename = re.sub(r'[\x00-\x1f"\\]', "_", filename)
    if not filename or filename in (".", ".."):
        raise ValueError("Nieprawidlowa nazwa pliku")
    if len(filename) > 255:
        raise ValueError("Nazwa pliku nie moze przekraczac 255 znakow")

    # Walidacja base64
    if not file_base64 or not file_base64.strip():
        raise ValueError("Zawartosc pliku (file_base64) nie moze byc pusta")

    # Dekodowanie base64
    try:
        file_bytes = base64.b64decode(file_base64, validate=True)
    except Exception as exc:
        raise ValueError("Nieprawidlowy format base64") from exc

    # Sprawdzenie rozmiaru
    if len(file_bytes) > max_size:
        raise ValueError(f"Plik za duzy: max 200MB, otrzymano {len(file_bytes) / 1024 / 1024:.1f}MB")

    # Walidacja mime_type
    if mime_type is not None:
        if not re.match(r"^[a-z]+/[a-z0-9.+\-]+$", mime_type):
            raise ValueError("Nieprawidlowy format mime_type, oczekiwano np. image/png, text/plain")
    else:
        mime_type = "application/octet-stream"

    async with async_session_factory() as db:
        resolved_id = await tickets_service.resolve_ticket_uuid(ticket_id, project.id, db)

        # Sprawdz czy ticket istnieje w projekcie
        result = await db.execute(
            select(Ticket).where(
                Ticket.id == resolved_id,
                Ticket.project_id == project.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("Ticket nie istnieje")

        # Upload do MinIO (blokujaca operacja — wywolywana synchronicznie)
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            storage_path = await loop.run_in_executor(
                executor,
                lambda: minio_upload_attachment(project_slug, filename, file_bytes, mime_type),
            )

        # Zapis rekordu w DB
        attachment = TicketAttachment(
            ticket_id=resolved_id,
            filename=filename,
            storage_path=storage_path,
            mime_type=mime_type,
            size=len(file_bytes),
            created_via_ai=True,
        )
        db.add(attachment)
        await db.commit()
        await db.refresh(attachment)

    url = f"/dashboard/{project_slug}/scrum/tickets/{resolved_id}/attachments/{attachment.id}/{filename}"

    return {
        "attachment_id": str(attachment.id),
        "filename": attachment.filename,
        "url": url,
        "size": attachment.size,
        "uploaded_at": attachment.created_at.isoformat(),
    }


# --- Time Tracking ---


@mcp.tool(annotations=WRITE)
async def log_time(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    duration_minutes: int,
    date_logged: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Zaloguj czas pracy na tickecie. Oznaczany jako created_via_ai=True.

    ticket_id: UUID ticketa lub klucz (np. MNX-12).
    date_logged w formacie YYYY-MM-DD. duration_minutes musi byc > 0.
    Wymaga scrum:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "scrum", "write")

    if duration_minutes <= 0:
        raise ValueError("Czas musi byc wiekszy niz 0")

    parsed_date = date.fromisoformat(date_logged)

    async with async_session_factory() as db:
        resolved_id = await tickets_service.resolve_ticket_uuid(ticket_id, project.id, db)
        result = await add_time_entry(
            ticket_id=resolved_id,
            user_id=user.id,
            duration_minutes=duration_minutes,
            date_logged=parsed_date,
            description=description.strip() if description else None,
            db=db,
            created_via_ai=True,
        )

        if isinstance(result, str):
            raise ValueError(result)

    return {
        "id": str(result.id),
        "ticket_id": str(result.ticket_id),
        "duration_minutes": result.duration_minutes,
        "date_logged": result.date_logged.isoformat(),
        "description": result.description,
        "created_via_ai": True,
        "message": f"Zalogowano {duration_minutes} min na tickecie",
    }


@mcp.tool(annotations=READ_ONLY)
async def get_work_report(
    ctx: Context[Any, Any],
    project_slug: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    user_email: str | None = None,
    sprint_id: str | None = None,
    status: str | None = None,
    created_via_ai: bool | None = None,
    include_entries: bool = False,
    page: int = 1,
    per_page: int = 20,
) -> dict[str, Any]:
    """Raport zalogowanego czasu pracy: suma godzin + agregacje wg uzytkownika, projektu i sprintu.

    project_slug: opcjonalne - bez niego raport obejmuje wszystkie projekty uzytkownika z uprawnieniem reports:read.
    Wymaga reports:read w projekcie (superuser: wszystkie projekty).
    date_from, date_to: YYYY-MM-DD, granice wlacznie. Pominiecie obu = caly okres
    (inaczej niz dashboard, ktory domyslnie pokazuje ostatnie 30 dni).
    user_email: filtr po autorze wpisow. sprint_id: UUID sprintu.
    status: draft, submitted, approved, rejected. created_via_ai: True=AI, False=recznie.
    include_entries: dolacz pojedyncze wpisy (paginowane przez page/per_page).
    """
    user = await _auth(ctx)

    parsed_from = date.fromisoformat(date_from) if date_from else None
    parsed_to = date.fromisoformat(date_to) if date_to else None
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise ValueError("date_from nie moze byc pozniejsze niz date_to")

    if status is not None and status not in TIME_TRACKING_STATUSES:
        raise ValueError(f"Nieprawidlowy status: '{status}'. Dozwolone: {', '.join(TIME_TRACKING_STATUSES)}")

    parsed_sprint_id = None
    if sprint_id is not None:
        try:
            parsed_sprint_id = uuid.UUID(sprint_id)
        except ValueError:
            raise ValueError(f"Nieprawidlowy sprint_id: '{sprint_id}'. Podaj UUID") from None

    async with async_session_factory() as db:
        allowed_project_ids = await _get_user_project_ids(user.id, db, user.is_superuser)

        if project_slug is not None:
            proj_result = await db.execute(select(Project.id).where(Project.slug == project_slug, Project.is_active.is_(True)))
            requested_id = proj_result.scalar_one_or_none()
            if requested_id is None:
                raise ValueError(f"Projekt '{project_slug}' nie istnieje")
            if requested_id not in allowed_project_ids:
                # allowed_project_ids jest zawezone do reports:read, wiec odroznij brak czlonkostwa od braku uprawnienia
                member_result = await db.execute(
                    select(ProjectMember.user_id).where(ProjectMember.project_id == requested_id, ProjectMember.user_id == user.id)
                )
                if member_result.scalar_one_or_none() is None:
                    raise ValueError(f"Uzytkownik nie jest czlonkiem projektu '{project_slug}'")
                raise ValueError(f"Brak uprawnienia do raportow projektu '{project_slug}' (reports:read)")
            project_ids = [requested_id]
        else:
            if not allowed_project_ids:
                raise ValueError("Uzytkownik nie ma dostepu do zadnego projektu")
            project_ids = allowed_project_ids

        user_ids = None
        if user_email is not None:
            user_result = await db.execute(select(User.id).where(func.lower(User.email) == user_email.strip().lower()))
            filtered_user_id = user_result.scalar_one_or_none()
            if filtered_user_id is None:
                raise ValueError(f"Uzytkownik '{user_email}' nie istnieje")
            user_ids = [filtered_user_id]

        filters = TimeTrackingFilter(
            project_ids=project_ids,
            user_ids=user_ids,
            sprint_ids=[parsed_sprint_id] if parsed_sprint_id else None,
            date_from=parsed_from,
            date_to=parsed_to,
            status=status,
            created_via_ai=created_via_ai,
            page=max(1, page),
            per_page=min(max(1, per_page), 100),
        )

        # Zawsze paginowane - bez include_entries wpisy sa odrzucane, a paginacja
        # chroni przed zaciagnieciem calego zbioru tylko dla agregacji
        report = await svc_get_work_report(filters, db)

        # Zamien UUID w agregacjach na czytelne nazwy - surowe UUID sa bezuzyteczne dla agenta
        emails = await _lookup_names(db, User.id, User.email, report.hours_by_user)
        slugs = await _lookup_names(db, Project.id, Project.slug, report.hours_by_project)
        sprint_names = await _lookup_sprint_names(db, report.hours_by_sprint, prefix_project=len(report.hours_by_project) > 1)

        result: dict[str, Any] = {
            "total_hours": report.total_hours,
            "entry_count": report.entry_count,
            "date_range": ({"from": report.date_range[0].isoformat(), "to": report.date_range[1].isoformat()} if report.date_range else None),
            "hours_by_user": {emails.get(k, k): v for k, v in report.hours_by_user.items()},
            "hours_by_project": {slugs.get(k, k): v for k, v in report.hours_by_project.items()},
            "hours_by_sprint": {sprint_names.get(k, k): v for k, v in report.hours_by_sprint.items()},
        }

        if include_entries:
            result["entries"] = await _enrich_time_entries(db, report.entries)
            result["page"] = report.page
            result["total_pages"] = report.total_pages

    return result


async def _lookup_names(db: AsyncSession, id_column: Any, name_column: Any, keyed_by_uuid: dict[str, float]) -> dict[str, str]:
    """Zbuduj mape {uuid_str: nazwa} dla kluczy wystepujacych w agregacji."""
    if not keyed_by_uuid:
        return {}
    rows = await db.execute(select(id_column, name_column).where(id_column.in_([uuid.UUID(k) for k in keyed_by_uuid])))
    return {str(row[0]): row[1] for row in rows.all()}


async def _lookup_sprint_names(db: AsyncSession, keyed_by_uuid: dict[str, float], *, prefix_project: bool) -> dict[str, str]:
    """Zbuduj mape {uuid_str: nazwa sprintu}, z prefiksem slug projektu przy raporcie cross-project."""
    if not keyed_by_uuid:
        return {}
    rows = await db.execute(
        select(Sprint.id, Sprint.name, Project.slug)
        .join(Project, Project.id == Sprint.project_id)
        .where(Sprint.id.in_([uuid.UUID(k) for k in keyed_by_uuid]))
    )
    return {str(row[0]): (f"{row[2]}/{row[1]}" if prefix_project else row[1]) for row in rows.all()}


async def _enrich_time_entries(db: AsyncSession, entries: list[TimeTrackingEntryResponse]) -> list[dict[str, Any]]:
    """Dodaj do wpisow klucz ticketa, tytul i e-mail autora zamiast surowych UUID."""
    if not entries:
        return []

    ticket_rows = await db.execute(
        select(Ticket.id, Ticket.number, Ticket.title, Project.code, Project.slug)
        .join(Project, Project.id == Ticket.project_id)
        .where(Ticket.id.in_([e.ticket_id for e in entries]))
    )
    tickets = {row[0]: row for row in ticket_rows.all()}

    user_rows = await db.execute(select(User.id, User.email).where(User.id.in_([e.user_id for e in entries])))
    emails = {row[0]: row[1] for row in user_rows.all()}

    enriched: list[dict[str, Any]] = []
    for e in entries:
        ticket = tickets.get(e.ticket_id)
        enriched.append(
            {
                "id": str(e.id),
                "ticket_key": f"{ticket[3]}-{ticket[1]}" if ticket else str(e.ticket_id),
                "ticket_title": ticket[2] if ticket else None,
                "project_slug": ticket[4] if ticket else None,
                "user_email": emails.get(e.user_id, str(e.user_id)),
                "hours": round(e.duration_minutes / 60, 2),
                "duration_minutes": e.duration_minutes,
                "date_logged": e.date_logged.isoformat(),
                "description": e.description,
                "status": e.status,
                "created_via_ai": e.created_via_ai,
            }
        )
    return enriched


# --- Wiki ---


def _format_wiki_tree(pages_data: list[dict[str, Any]]) -> str:
    """Konwertuj plaska liste stron wiki na kompaktowy string z wcieciami.

    Format:
        N pages

        ID                                   | Title              | Updated
        <uuid> | Title                        | YYYY-MM-DD
        <uuid> |   Child Title               | YYYY-MM-DD
    """
    if not pages_data:
        return "0 pages"

    lines: list[str] = [f"{len(pages_data)} pages", ""]
    lines.append(f"{'ID':<36} | Title              | Updated")

    for p in pages_data:
        indent = "  " * p["depth"]
        title = indent + p["title"]
        date_str = p["updated_at"][:10]
        lines.append(f"{p['id']:<36} | {title:<18} | {date_str}")

    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def list_wiki_pages(
    ctx: Context[Any, Any],
    project_slug: str,
) -> str:
    """Lista stron wiki w projekcie (drzewo z hierarchia).

    Zwraca kompaktowy string z wcieciami pokazujacymi hierarchie parent-child.
    Kolumny: ID (pelne UUID), Title (z wcieciem 2 spacje na poziom), Updated (YYYY-MM-DD).
    Uzyj ID strony do wywolania get_wiki_page, update_wiki_page lub delete_wiki_page.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        pages = await svc_list_pages_flat(db, user, project)

    return _format_wiki_tree([{"id": str(p.id), "title": p.title, "depth": p.depth, "updated_at": p.updated_at.isoformat()} for p in pages])


@mcp.tool(annotations=READ_ONLY)
async def get_wiki_page(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
) -> dict[str, Any]:
    """Szczegoly strony wiki z pelna trescia markdown."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        detail = await svc_get_wiki_page_detail(db, user, project, page_id, content_fn=get_page_content)

    return {
        "id": str(detail.id),
        "title": detail.title,
        "slug": detail.slug,
        "parent_id": str(detail.parent_id) if detail.parent_id else None,
        "position": detail.position,
        "content": detail.content,
        "is_ai_touched": detail.is_ai_touched,
        "created_by": detail.created_by,
        "last_edited_by": detail.last_edited_by,
        "created_at": detail.created_at.isoformat(),
        "updated_at": detail.updated_at.isoformat(),
    }


@mcp.tool(annotations=WRITE)
async def create_wiki_page(
    ctx: Context[Any, Any],
    project_slug: str,
    title: str,
    content: str,
    parent_id: str | None = None,
    position: int = 0,
    is_public: bool = False,
) -> dict[str, Any]:
    """Utworz nowa strone wiki. Oznaczana jako is_ai_touched=True.

    parent_id -- UUID strony nadrzednej (opcjonalnie, dla podstron).
    is_public -- czy strona ma byc dostepna publicznie pod /blog/{slug} (bez logowania).
    Wymaga wiki:write; is_public=True wymaga dodatkowo uprawnienia konta do edycji bloga (blog:write,
    nadawanego przez superusera), inaczej "Brak uprawnienia blog:write" i nic nie jest zapisywane.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "wiki", "write")

        if not title.strip():
            raise ValueError("Tytul jest wymagany")

        page = await svc_create_wiki_page(
            project_id=project.id,
            project_slug=project.slug,
            title=title,
            content=content,
            user_id=user.id,
            parent_id=uuid.UUID(parent_id) if parent_id else None,
            position=position,
            is_ai=True,
            is_public=is_public,
            db=db,
        )

    return {
        "id": str(page.id),
        "title": page.title,
        "slug": page.slug,
        "is_ai_touched": True,
        "is_public": page.is_public,
        "message": f"Strona wiki '{page.title}' utworzona",
    }


@mcp.tool(annotations=WRITE)
async def update_wiki_page(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
    title: str | None = None,
    content: str | None = None,
    position: int | None = None,
    is_public: bool | None = None,
) -> dict[str, Any]:
    """Aktualizuj strone wiki. Oznaczana jako is_ai_touched=True.

    Podaj tylko pola do zmiany. is_public=True/False publikuje/wycofuje strone z /blog/{slug};
    None (domyslnie) nie zmienia flagi.
    Wymaga wiki:write; zmiana is_public (publikacja i cofniecie) wymaga dodatkowo uprawnienia konta do
    edycji bloga (blog:write, nadawanego przez superusera), inaczej "Brak uprawnienia blog:write".
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        from sqlalchemy.orm import selectinload

        await ensure_permission(db, user.id, project.id, "wiki", "write")

        result = await db.execute(
            select(WikiPage)
            .options(selectinload(WikiPage.created_by), selectinload(WikiPage.last_edited_by))
            .where(WikiPage.id == uuid.UUID(page_id), WikiPage.project_id == project.id)
        )
        page = result.scalar_one_or_none()
        if page is None:
            raise ValueError("Strona wiki nie istnieje")

        page = await svc_update_wiki_page(
            page=page,
            project_slug=project.slug,
            title=title,
            content=content,
            position=position,
            user_id=user.id,
            is_ai=True,
            is_public=is_public,
            db=db,
        )

    return {
        "id": str(page.id),
        "title": page.title,
        "is_ai_touched": True,
        "is_public": page.is_public,
        "message": f"Strona wiki '{page.title}' zaktualizowana",
    }


@mcp.tool(annotations=READ_ONLY)
async def search_wiki(
    ctx: Context[Any, Any],
    project_slug: str,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Wyszukaj semantycznie w wiki projektu (RAG).

    query -- zapytanie w jezyku naturalnym
    limit -- maksymalna liczba wynikow (domyslnie 10)
    Wymaga wiki:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    from monolynx.services.embeddings import search_wiki_pages

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "wiki", "read")
        return await search_wiki_pages(project.id, query, db, limit=limit)


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_wiki_page(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
) -> dict[str, Any]:
    """Usun strone wiki wraz z podstronami. Wymaga wiki:delete."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "wiki", "delete")

        result = await db.execute(select(WikiPage).where(WikiPage.id == uuid.UUID(page_id), WikiPage.project_id == project.id))
        page = result.scalar_one_or_none()
        if page is None:
            raise ValueError("Strona wiki nie istnieje")

        title = page.title
        await svc_delete_wiki_page(page, db)

    return {"message": f"Strona wiki '{title}' usunieta"}


# --- Blog (metadane wpisow bloga na stronach wiki, MON-261) ---


def _blog_view_dict(view: blog_service.BlogPostView) -> dict[str, Any]:
    return {
        "page_id": str(view.page_id),
        "title": view.title,
        "slug": view.slug,
        "previous_slugs": view.previous_slugs,
        "description": view.description,
        "published_at": view.published_at.isoformat() if view.published_at else None,
        "author_name": view.author_name,
        "tags": view.tags,
        "lang": view.lang,
        "translation_of_id": str(view.translation_of_id) if view.translation_of_id else None,
        "cover_attachment_id": str(view.cover_attachment_id) if view.cover_attachment_id else None,
        "last_verified_at": view.last_verified_at.isoformat() if view.last_verified_at else None,
        "is_public": view.is_public,
        "public_url": view.public_url,
        "markdown_url": view.markdown_url,
    }


@mcp.tool(annotations=READ_ONLY)
async def list_blog_posts(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Lista wpisow bloga projektu (strony wiki z metadanymi bloga), takze szkice.

    status -- published (tylko publiczne) lub draft (tylko niepubliczne); brak = wszystkie.
    public_url i markdown_url sa wypelnione tylko dla wpisu publicznego. Wymaga wiki:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        posts = await blog_service.list_posts(db, user, project, status=status)

    return [_blog_view_dict(p) for p in posts]


@mcp.tool(annotations=READ_ONLY)
async def get_blog_post(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
) -> dict[str, Any]:
    """Wpis bloga z metadanymi i pelna trescia markdown. Wymaga wiki:read."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "wiki", "read")
        try:
            parsed_page_id = uuid.UUID(page_id)
        except ValueError:
            raise ValueError("Nieprawidlowy page_id (oczekiwany UUID)") from None
        detail = await blog_service.get_post(db, user, project, parsed_page_id, content_fn=get_page_content)

    return {**_blog_view_dict(detail), "content": detail.content}


@mcp.tool(annotations=READ_ONLY)
async def lint_blog_post(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
) -> dict[str, Any]:
    """Lint wpisu bloga pod AI: lista problemow (linia, poziom, regula, komunikat) i ocena 0-100.

    Sprawdza opis, blok TLDR, teksty alternatywne obrazkow, bloki graficzne, naglowki, linki,
    data weryfikacji i tlumaczenie. Pole `table` to ta sama lista jako tabela markdown z linia
    oceny. Czysty odczyt, nic nie zapisuje. Wymaga wiki:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "wiki", "read")
        try:
            parsed_page_id = uuid.UUID(page_id)
        except ValueError:
            raise ValueError("Nieprawidlowy page_id (oczekiwany UUID)") from None
        result = await blog_lint_service.lint_post_by_id(db, user, project, parsed_page_id, content_fn=get_page_content)

    return {**blog_lint_service.lint_result_dict(parsed_page_id, result), "table": blog_lint_service.format_lint_table(result)}


@mcp.tool(annotations=WRITE)
async def set_blog_post_meta(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
    slug: str | None = None,
    description: str | None = None,
    author_name: str | None = None,
    tags: list[str] | None = None,
    lang: str | None = None,
    translation_of_id: str | None = None,
    cover_attachment_id: str | None = None,
    last_verified_at: str | None = None,
) -> dict[str, Any]:
    """Ustaw metadane wpisu bloga. Parametr nie podany (None) = bez zmian.

    tags -- lista tagow (male litery, cyfry, myslniki); pusta lista czyści tagi.
    translation_of_id, cover_attachment_id (UUID) i last_verified_at (YYYY-MM-DD) --
    pusty tekst czysci pole. lang: pl lub en. Zmiana sluga zachowuje stary adres jako
    przekierowanie. Nie zmienia is_public ani daty publikacji. Wymaga wiki:write i uprawnienia konta do edycji bloga (blog:write).
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "wiki", "write")

        fields: dict[str, Any] = {
            name: value
            for name, value in (("slug", slug), ("description", description), ("author_name", author_name), ("tags", tags), ("lang", lang))
            if value is not None
        }
        for name, raw in (("translation_of_id", translation_of_id), ("cover_attachment_id", cover_attachment_id)):
            if raw is None:
                continue
            try:
                fields[name] = uuid.UUID(raw) if raw else None
            except ValueError:
                raise ValueError(f"Nieprawidlowy {name} (oczekiwany UUID)") from None
        if last_verified_at is not None:
            try:
                fields["last_verified_at"] = date.fromisoformat(last_verified_at) if last_verified_at else None
            except ValueError:
                raise ValueError("Nieprawidlowy last_verified_at (oczekiwany format YYYY-MM-DD)") from None

        try:
            parsed_page_id = uuid.UUID(page_id)
        except ValueError:
            raise ValueError("Nieprawidlowy page_id (oczekiwany UUID)") from None

        post = await blog_service.set_post_meta(db, user, project, parsed_page_id, **fields)
        page = await db.get(WikiPage, post.page_id)
        if page is None:
            raise ValueError("Strona wiki nie istnieje")
        return _blog_view_dict(blog_service.build_post_view(post, page))


@mcp.tool(annotations=WRITE)
async def publish_blog_post(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """Opublikuj wpis bloga: lint, a potem strona staje sie publiczna (adres /blog/<slug> i .md).

    Przy bledach lintu publikacja jest odrzucana z lista regul, a strona zostaje prywatna;
    force=true publikuje mimo bledow. Ponowna publikacja nie zmienia daty ani adresu.
    Wynik: widok wpisu (public_url, markdown_url) i pole `lint` z ocena. Wymaga wiki:write i uprawnienia konta do edycji bloga (blog:write).
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "wiki", "write")
        try:
            parsed_page_id = uuid.UUID(page_id)
        except ValueError:
            raise ValueError("Nieprawidlowy page_id (oczekiwany UUID)") from None
        published = await blog_service.publish_post(db, user, project, parsed_page_id, force=force, content_fn=get_page_content)

    return {**_blog_view_dict(published.view), "lint": blog_lint_service.lint_result_dict(parsed_page_id, published.lint)}


@mcp.tool(annotations=DESTRUCTIVE)
async def unpublish_blog_post(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
) -> dict[str, Any]:
    """Cofnij publikacje wpisu bloga: adres /blog/<slug> i jego .md zwracaja 404.

    Data publikacji i slug zostaja, wiec ponowna publikacja przywraca ten sam adres.
    Idempotentne dla wpisu juz prywatnego. Wymaga wiki:write i uprawnienia konta do edycji bloga (blog:write).
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "wiki", "write")
        try:
            parsed_page_id = uuid.UUID(page_id)
        except ValueError:
            raise ValueError("Nieprawidlowy page_id (oczekiwany UUID)") from None
        view = await blog_service.unpublish_post(db, user, project, parsed_page_id)

    return _blog_view_dict(view)


# --- Wiki LLM (gating: project.wiki_llm_enabled) ---


@mcp.tool(annotations=READ_ONLY)
async def get_wiki_config(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Pobierz konfigurację LLM Wiki dla projektu.

    Nie wymaga wiki_llm_enabled=True - używaj do sprawdzenia stanu przed włączeniem.
    Zwraca: wiki_llm_enabled, id stron systemowych (wiki-index, wiki-log, wiki-schema) lub null.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        config = await svc_get_wiki_config(db, user, project)

    return {
        "wiki_llm_enabled": config.wiki_llm_enabled,
        "index_page_id": str(config.index_page_id) if config.index_page_id else None,
        "log_page_id": str(config.log_page_id) if config.log_page_id else None,
        "schema_page_id": str(config.schema_page_id) if config.schema_page_id else None,
    }


@mcp.tool(annotations=WRITE)
async def set_wiki_config(
    ctx: Context[Any, Any],
    project_slug: str,
    enabled: bool,
) -> dict[str, Any]:
    """Włącz lub wyłącz LLM Wiki dla projektu. Wymaga uprawnień settings:write."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        proj = await svc_set_wiki_config(db, user, project, enabled)

    return {
        "wiki_llm_enabled": proj.wiki_llm_enabled,
        "message": f"Wiki LLM {'włączone' if enabled else 'wyłączone'} dla projektu '{project_slug}'",
    }


@mcp.tool(annotations=WRITE)
async def bootstrap_wiki_llm(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Bootstrap metody LLM Wiki - tworzy wiki-schema, wiki-log, wiki-index i włącza flagę.

    Idempotentny: powtórny bootstrap nie tworzy duplikatów stron systemowych,
    odświeża index i dopisuje kolejny wpis do dziennika.
    Wymaga uprawnień settings:write.
    Zwraca: wiki_llm_enabled, id stron systemowych (schema, log, index), catalogued_pages.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "settings", "write"):
            raise ValueError("Brak uprawnienia do konfiguracji projektu (settings:write)")

        # Pobierz projekt z bieżącej sesji - serwis mutuje wiki_llm_enabled na tym obiekcie
        proj_result = await db.execute(select(Project).where(Project.id == project.id))
        proj = proj_result.scalar_one()

        return await svc_bootstrap_wiki_llm(project=proj, user_id=user.id, db=db)


@mcp.tool(annotations=READ_ONLY)
async def lint_wiki(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Przeskanuj wiki projektu - sieroty, martwe linki, sprzeczności, luki.

    Wymaga wiki:read i wiki_llm_enabled=True. Zwraca raport z kluczami:
    - orphans: podstrony bez backlinku przychodzącego
    - dead_links: referencje [[slug]] lub [[uuid]] nierozwiązane do stron projektu
    - contradictions: strony z markerem "> **Sprzeczność"
    - gaps: koncepty wzmiankowane >=2x bez własnej strony (heurystyka)
    - skipped_generated: liczba stron generowanych maszynowo (np. logów pipeline'ów)
      pominiętych przy liczeniu sierot
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "wiki", "read")

        if not is_wiki_llm_enabled(project):
            raise ValueError("Metoda LLM Wiki jest wyłączona dla tego projektu")

        return await svc_lint_wiki(project.id, db)


@mcp.tool(annotations=READ_ONLY)
async def get_wiki_backlinks(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
) -> dict[str, Any]:
    """Pobierz linki przychodzące i wychodzące dla strony wiki.

    Wymaga wiki:read i wiki_llm_enabled=True.
    Zwraca: incoming (strony wskazujące na tę stronę) i outgoing (strony na które wskazuje).
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "wiki", "read")

        if not is_wiki_llm_enabled(project):
            raise ValueError("Metoda LLM Wiki jest wyłączona dla tego projektu")

        page_uuid = uuid.UUID(page_id)

        # Sprawdź że strona należy do projektu
        result = await db.execute(select(WikiPage.id).where(WikiPage.id == page_uuid, WikiPage.project_id == project.id))
        if result.scalar_one_or_none() is None:
            raise ValueError("Strona wiki nie istnieje")

        incoming = await svc_get_wiki_backlinks(page_uuid, db)
        outgoing = await svc_get_wiki_outlinks(page_uuid, db)

    return {
        "incoming": [
            {
                "page_id": str(bl.source_page_id),
                "title": bl.source_page.title,
                "slug": bl.source_page.slug,
                "anchor": bl.anchor_text,
            }
            for bl in incoming
        ],
        "outgoing": [
            {
                "page_id": str(bl.target_page_id),
                "title": bl.target_page.title,
                "slug": bl.target_page.slug,
                "anchor": bl.anchor_text,
            }
            for bl in outgoing
        ],
    }


@mcp.tool(annotations=WRITE)
async def regenerate_wiki_index(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Przebuduj stronę wiki-index - katalog wszystkich stron projektu.

    Wymaga wiki:write i wiki_llm_enabled=True.
    Dla każdej strony: tytuł (link [[slug]]), 1-zdaniowe summary, liczba backlinków.
    Tworzy stronę wiki-index jeśli nie istnieje.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "wiki", "write")

        if not is_wiki_llm_enabled(project):
            raise ValueError("Metoda LLM Wiki jest wyłączona dla tego projektu")

        index_page, page_count = await svc_regenerate_wiki_index(project=project, user_id=user.id, db=db)

    return {
        "index_page_id": str(index_page.id),
        "index_page_slug": index_page.slug,
        "catalogued_pages": page_count,
        "message": f"Indeks wiki przebudowany - {page_count} stron skatalogowanych",
    }


@mcp.tool(annotations=WRITE)
async def append_wiki_log(
    ctx: Context[Any, Any],
    project_slug: str,
    entry: str,
) -> dict[str, Any]:
    """Dopisz wpis do dziennika wiki (wiki-log, append-only).

    Wymaga wiki:write i wiki_llm_enabled=True.
    Format wpisu: - [YYYY-MM-DD HH:MM] <entry>
    Tworzy stronę wiki-log jeśli nie istnieje.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "wiki", "write")

        if not is_wiki_llm_enabled(project):
            raise ValueError("Metoda LLM Wiki jest wyłączona dla tego projektu")

        if not entry.strip():
            raise ValueError("Wpis dziennika nie może być pusty")

        log_page = await svc_append_wiki_log(project=project, entry=entry.strip(), user_id=user.id, db=db)

    return {
        "log_page_id": str(log_page.id),
        "log_page_slug": log_page.slug,
        "message": "Wpis dodany do dziennika wiki",
    }


# --- Graf (polaczenia) ---


@mcp.tool(annotations=WRITE)
async def create_graph_node(
    ctx: Context[Any, Any],
    project_slug: str,
    type: str,
    name: str,
    file_path: str | None = None,
    line_number: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Utworz node w grafie polaczen. type: File, Class, Method, Function, Const, Module.

    Wymaga connections:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "write")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    if type not in GRAPH_NODE_TYPES:
        raise ValueError(f"Nieznany typ node'a: {type}. Dozwolone: {', '.join(GRAPH_NODE_TYPES)}")

    node = await graph_service.create_node(
        project.id,
        {
            "type": type,
            "name": name,
            "file_path": file_path,
            "line_number": line_number,
            "metadata": metadata or {},
        },
    )

    return {**node, "message": f"Node '{name}' ({type}) utworzony"}


@mcp.tool(annotations=READ_ONLY)
async def list_graph_nodes(
    ctx: Context[Any, Any],
    project_slug: str,
    type: str | None = None,
    search: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Lista node'ow w grafie polaczen z opcjonalnym filtrowaniem po typie i nazwie.

    Wymaga connections:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "read")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    return await graph_service.list_nodes(project.id, type_filter=type, search=search, limit=limit)


@mcp.tool(annotations=READ_ONLY)
async def get_graph_node(
    ctx: Context[Any, Any],
    project_slug: str,
    node_id: str,
    depth: int = 1,
    relation_types: list[str] | None = None,
    node_types: list[str] | None = None,
) -> str:
    """Szczegoly node'a z polaczeniami do sasiednich elementow w kompaktowym formacie Arrow DSL.

    depth: ile poziomow polaczen pokazac (domyslnie 1 = tylko bezposredni sasiedzi,
    2 = sasiedzi sasiadow itd., max 5). Uzyj depth=2 aby zobaczyc szerszy kontekst,
    np. pelna sciezke User -> ProjectMember -> Project w jednym zapytaniu.

    relation_types: opcjonalny filtr typow krawedzi. Dozwolone wartosci:
        CONTAINS, CALLS, IMPORTS, INHERITS, USES, IMPLEMENTS.
        Przyklad: ["INHERITS", "CALLS"] — pokaz tylko relacje dziedziczenia i wywolan.
        Jesli None (domyslnie) — zwroc wszystkie typy relacji.

    node_types: opcjonalny filtr typow sasiadow. Dozwolone wartosci:
        File, Class, Method, Function, Const, Module.
        Przyklad: ["Class", "Method"] — pokaz tylko klasy i metody w sasiedztwie.
        Jesli None (domyslnie) — zwroc wszystkie typy node'ow.

    Wynik zawiera sekcje "Depth N" (grupowanie po glebokosci sciezki) oraz
    sekcje "=== EDGE_TYPE ===" (grupowanie krawedzi po typie relacji).
    Wymaga connections:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "read")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    node = await graph_service.get_node(project.id, node_id)
    if node is None:
        raise ValueError("Node nie istnieje")

    neighbors = await graph_service.get_neighbors(
        project.id,
        node_id,
        depth=depth,
        relation_types=relation_types,
        node_types=node_types,
    )

    return _format_graph_dsl(neighbors)


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_graph_node(
    ctx: Context[Any, Any],
    project_slug: str,
    node_id: str,
) -> dict[str, Any]:
    """Usun node i wszystkie jego krawedzie.

    Wymaga connections:delete.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "delete")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    deleted = await graph_service.delete_node(project.id, node_id)
    if not deleted:
        raise ValueError("Node nie istnieje")

    return {"message": "Node usuniety", "node_id": node_id}


@mcp.tool(annotations=WRITE)
async def create_graph_edge(
    ctx: Context[Any, Any],
    project_slug: str,
    source_id: str,
    target_id: str,
    type: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Utworz krawedz miedzy node'ami. type: CONTAINS, CALLS, IMPORTS, INHERITS, USES, IMPLEMENTS.

    Wymaga connections:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "write")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    if type not in GRAPH_EDGE_TYPES:
        raise ValueError(f"Nieznany typ krawedzi: {type}. Dozwolone: {', '.join(GRAPH_EDGE_TYPES)}")

    edge = await graph_service.create_edge(project.id, source_id, target_id, type, metadata)
    if edge is None:
        raise ValueError("Nie znaleziono node'ow zrodlowego lub docelowego")

    return {**edge, "message": f"Krawedz {type} utworzona"}


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_graph_edge(
    ctx: Context[Any, Any],
    project_slug: str,
    source_id: str,
    target_id: str,
    type: str,
) -> dict[str, Any]:
    """Usun krawedz miedzy node'ami.

    Wymaga connections:delete.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "delete")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    deleted = await graph_service.delete_edge(project.id, source_id, target_id, type)
    if not deleted:
        raise ValueError("Krawedz nie istnieje")

    return {
        "message": "Krawedz usunieta",
        "source_id": source_id,
        "target_id": target_id,
        "type": type,
    }


@mcp.tool(annotations=READ_ONLY)
async def query_graph(
    ctx: Context[Any, Any],
    project_slug: str,
    node_type: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> str:
    """Pobierz graf lub podgraf projektu (node'y + krawedzie) w kompaktowym formacie Arrow DSL.

    Zwraca tekst z node'ami jako [Type] name (metadata) i krawędziami jako src --TYPE--> tgt
    (metadane krawedzi typu confidence/source_relation, gdy obecne, w nawiasie za strzalka).

    search: fragment nazwy lub sciezki pliku (case-insensitive) - zwraca dopasowane node'y
        oraz ich bezposrednich sasiadow (1 hop), bez potrzeby znajomosci node_id. To glowny
        punkt wejscia do "znajdz element kodu po nazwie i pokaz jego powiazania" (np. w
        researchu przed implementacja ticketu). Gdy podany razem z node_type, node_type
        filtruje TYLKO dopasowania - sasiedzi zwracani sa niezaleznie od typu. limit ogranicza
        wtedy liczbe dopasowan, nie calego podgrafu.
    node_type: bez `search` - filtruje caly zwracany podgraf po typie node'a.
    Wymaga connections:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "read")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    ensure_search_not_blank(search)
    if search is not None:
        data = await graph_service.search_graph(project.id, search=search, type_filter=node_type, limit=limit)
    else:
        data = await graph_service.get_graph(project.id, type_filter=node_type, limit=limit)
    return _format_graph_dsl(data)


@mcp.tool(annotations=READ_ONLY)
async def find_graph_path(
    ctx: Context[Any, Any],
    project_slug: str,
    source_id: str,
    target_id: str,
) -> str:
    """Znajdz najkrotsza sciezke miedzy dwoma node'ami. Zwraca kompaktowy format Arrow DSL.

    Wymaga connections:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "read")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    data = await graph_service.find_path(project.id, source_id, target_id)
    return _format_graph_dsl(data)


@mcp.tool(annotations=READ_ONLY)
async def get_graph_stats(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Statystyki grafu: liczba node'ow i krawedzi per typ.

    Wymaga connections:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "read")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    return await graph_service.get_stats(project.id)


@mcp.tool(annotations=WRITE)
async def bulk_create_graph_nodes(
    ctx: Context[Any, Any],
    project_slug: str,
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Masowe tworzenie node'ow. Kazdy element: {type, name, file_path?, line_number?, metadata?}.

    Wymaga connections:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "write")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    created_nodes: list[dict[str, Any]] = []
    errors: list[str] = []

    for i, node_data in enumerate(nodes):
        try:
            if "name" not in node_data or "type" not in node_data:
                errors.append(f"[{i}] Wymagane pola: type, name")
                continue

            node_type = node_data.get("type", "")
            if node_type not in GRAPH_NODE_TYPES:
                errors.append(f"[{i}] Nieznany typ node'a: {node_type}")
                continue

            node = await graph_service.create_node(
                project.id,
                {
                    "type": node_type,
                    "name": node_data["name"],
                    "file_path": node_data.get("file_path"),
                    "line_number": node_data.get("line_number"),
                    "metadata": node_data.get("metadata", {}),
                },
            )
            created_nodes.append(node)
        except Exception as e:
            errors.append(f"[{i}] {e}")

    return {
        "created": len(created_nodes),
        "errors": errors,
        "nodes": created_nodes,
    }


@mcp.tool(annotations=WRITE)
async def bulk_create_graph_edges(
    ctx: Context[Any, Any],
    project_slug: str,
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Masowe tworzenie krawedzi. Kazdy element: {source_id, target_id, type, metadata?}.

    Wymaga connections:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "write")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    created = 0
    skipped = 0
    errors: list[str] = []

    for i, edge_data in enumerate(edges):
        try:
            if not all(k in edge_data for k in ("source_id", "target_id", "type")):
                errors.append(f"[{i}] Wymagane pola: source_id, target_id, type")
                continue

            edge_type = edge_data.get("type", "")
            if edge_type not in GRAPH_EDGE_TYPES:
                errors.append(f"[{i}] Nieznany typ krawedzi: {edge_type}")
                continue

            edge = await graph_service.create_edge(
                project.id,
                edge_data["source_id"],
                edge_data["target_id"],
                edge_type,
                edge_data.get("metadata"),
            )
            if edge is None:
                skipped += 1
                errors.append(f"[{i}] Nie znaleziono node'ow zrodlowego lub docelowego")
            else:
                created += 1
        except Exception as e:
            errors.append(f"[{i}] {e}")

    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


@mcp.tool(annotations=DESTRUCTIVE)
async def replace_graph(
    ctx: Context[Any, Any],
    project_slug: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    clear_first: bool = True,
) -> dict[str, Any]:
    """Pelna podmiana grafu projektu: kasuje WSZYSTKIE node'y i krawedzie projektu, wstawia nowy zestaw.

    Idempotentny sync z zewnetrznego ekstraktora (graphify) -- rebuild from scratch.
    Node: {id, type, name, file_path?, line_number?, metadata?} -- `id` jest wymagane
    (krawedzie referuja source_id/target_id). Krawedz: {source_id, target_id, type, metadata?}.
    Bledny payload nie kasuje niczego (walidacja przed zapisem). Limity: 20000 node'ow,
    60000 krawedzi. Dla wiekszych payloadow dziel na czesci: pierwsze wywolanie z
    clear_first=True, kolejne z clear_first=False (dokladaja bez kasowania).
    Zwraca statystyki: deleted/inserted nodes+edges, skipped_edges (brakujacy endpoint).
    Wymaga connections:delete.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "connections", "delete")

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    return await graph_service.replace_graph(project.id, nodes, edges, clear_first=clear_first)


# --- Heartbeat ---


def _heartbeat_dict(hb: Heartbeat, *, include_token: bool = False) -> dict[str, Any]:
    """Format heartbeat response dictionary."""
    d: dict[str, Any] = {
        "id": str(hb.id),
        "name": hb.name,
        "period_minutes": hb.period // 60,
        "grace_minutes": hb.grace // 60,
        "status": get_heartbeat_status(hb),
        "last_ping_at": hb.last_ping_at.isoformat() if hb.last_ping_at else None,
        "ping_url": f"{app_settings.APP_URL}/hb/{hb.token}",
        "created_at": hb.created_at.isoformat(),
    }
    if include_token:
        d["token"] = hb.token
    return d


@mcp.tool(annotations=READ_ONLY)
async def list_heartbeats(
    ctx: Context[Any, Any],
    project_slug: str,
) -> list[dict[str, Any]]:
    """Lista heartbeatow projektu z aktualnym statusem i URL do pingowania.

    Wymaga heartbeat:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "heartbeat", "read")
        result = await db.execute(select(Heartbeat).where(Heartbeat.project_id == project.id).order_by(Heartbeat.created_at))
        heartbeats = result.scalars().all()

    return [_heartbeat_dict(hb) for hb in heartbeats]


@mcp.tool(annotations=READ_ONLY)
async def get_heartbeat(
    ctx: Context[Any, Any],
    project_slug: str,
    heartbeat_id: str,
) -> dict[str, Any]:
    """Szczegoly heartbeatu: token, URL do pinga, status, last_ping_at, period, grace.

    Wymaga heartbeat:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "heartbeat", "read")
        result = await db.execute(
            select(Heartbeat).where(
                Heartbeat.id == uuid.UUID(heartbeat_id),
                Heartbeat.project_id == project.id,
            )
        )
        hb = result.scalar_one_or_none()
        if hb is None:
            raise ValueError("Heartbeat nie istnieje")

    return _heartbeat_dict(hb, include_token=True)


@mcp.tool(annotations=WRITE)
async def create_heartbeat(
    ctx: Context[Any, Any],
    project_slug: str,
    name: str,
    period: int,
    grace: int = 1,
) -> dict[str, Any]:
    """Tworzy nowy heartbeat dla projektu. Zwraca URL do pingowania.

    period -- oczekiwany interwal w minutach (np. 60 = co godzine)
    grace -- dodatkowy czas tolerancji w minutach (domyslnie 1 minuta)
    Wymaga heartbeat:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "heartbeat", "write")

    if not name.strip():
        raise ValueError("Nazwa heartbeatu jest wymagana")
    if period <= 0:
        raise ValueError("Period musi byc wiekszy niz 0")
    if grace < 0:
        raise ValueError("Grace nie moze byc ujemne")

    async with async_session_factory() as db:
        hb = await svc_create_heartbeat(
            db,
            project.id,
            {
                "name": name.strip(),
                "period": period * 60,
                "grace": grace * 60,
            },
        )

    result = _heartbeat_dict(hb, include_token=True)
    result["message"] = f"Heartbeat '{hb.name}' utworzony"
    return result


@mcp.tool(annotations=WRITE)
async def update_heartbeat(
    ctx: Context[Any, Any],
    project_slug: str,
    heartbeat_id: str,
    name: str | None = None,
    period: int | None = None,
    grace: int | None = None,
) -> dict[str, Any]:
    """Aktualizuje konfiguracje heartbeatu. Podaj tylko pola do zmiany.

    period -- oczekiwany interwal w minutach
    grace -- dodatkowy czas tolerancji w minutach
    Wymaga heartbeat:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "heartbeat", "write")

    data: dict[str, Any] = {}
    if name is not None:
        if not name.strip():
            raise ValueError("Nazwa heartbeatu nie moze byc pusta")
        data["name"] = name.strip()
    if period is not None:
        if period <= 0:
            raise ValueError("Period musi byc wiekszy niz 0")
        data["period"] = period * 60
    if grace is not None:
        if grace < 0:
            raise ValueError("Grace nie moze byc ujemne")
        data["grace"] = grace * 60

    async with async_session_factory() as db:
        hb = await svc_update_heartbeat(db, project.id, uuid.UUID(heartbeat_id), data)

    result = _heartbeat_dict(hb)
    result["message"] = f"Heartbeat '{hb.name}' zaktualizowany"
    return result


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_heartbeat(
    ctx: Context[Any, Any],
    project_slug: str,
    heartbeat_id: str,
) -> dict[str, Any]:
    """Usuwa heartbeat z projektu.

    Wymaga heartbeat:delete.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "heartbeat", "delete")

        # Verify existence before deleting for a clear error message
        result = await db.execute(
            select(Heartbeat).where(
                Heartbeat.id == uuid.UUID(heartbeat_id),
                Heartbeat.project_id == project.id,
            )
        )
        hb = result.scalar_one_or_none()
        if hb is None:
            raise ValueError("Heartbeat nie istnieje")

        name = hb.name
        await svc_delete_heartbeat(db, project.id, uuid.UUID(heartbeat_id))

    return {"message": f"Heartbeat '{name}' usuniety", "heartbeat_id": heartbeat_id}


@mcp.tool(annotations=READ_ONLY)
async def get_activity_log(
    ctx: Context[Any, Any],
    project_slug: str,
    limit: int = 50,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_email: str | None = None,
) -> list[dict[str, Any]]:
    """Historia zmian w projekcie -- kto co zmienil i kiedy.

    Filtrowanie po entity_type (ticket, sprint, monitor, wiki, member),
    entity_id (UUID konkretnego obiektu), actor_email (email lub "ai" dla operacji AI).
    Wymaga settings:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "settings", "read")

    limit = min(limit, 200)
    limit = max(limit, 1)

    if entity_type is not None and entity_type not in ACTIVITY_ENTITY_TYPES:
        raise ValueError(f"Nieprawidlowy entity_type. Dozwolone: {', '.join(sorted(ACTIVITY_ENTITY_TYPES))}")

    actor_id: uuid.UUID | None = None
    actor_type_filter: str | None = None

    if actor_email == "ai":
        actor_type_filter = "ai"
    elif actor_email is not None:
        async with async_session_factory() as db:
            result = await db.execute(select(User).where(User.email == actor_email))
            found_user = result.scalar_one_or_none()
            if found_user is None:
                raise ValueError(f"Uzytkownik '{actor_email}' nie istnieje")
            actor_id = found_user.id

    async with async_session_factory() as db:
        entries = await svc_get_activity_log(
            db,
            project_id=project.id,
            limit=limit,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_id=actor_id,
            actor_type_filter=actor_type_filter,
        )

        # Load actor emails in the same session
        actor_emails: dict[uuid.UUID, str] = {}
        actor_ids = {e.actor_id for e in entries if e.actor_id is not None}
        if actor_ids:
            users_result = await db.execute(select(User).where(User.id.in_(actor_ids)))
            for u in users_result.scalars().all():
                actor_emails[u.id] = u.email

    output = []
    for entry in entries:
        if entry.actor_type == "ai":
            actor_display = "ai"
        elif entry.actor_id is not None:
            actor_display = actor_emails.get(entry.actor_id, str(entry.actor_id))
        else:
            actor_display = "unknown"

        output.append(
            {
                "id": str(entry.id),
                "timestamp": entry.created_at.isoformat(),
                "actor": actor_display,
                "action": entry.action,
                "entity_type": entry.entity_type,
                "entity_id": entry.entity_id,
                "entity_title": entry.entity_title,
                "changes": entry.changes,
            }
        )

    return output


@mcp.tool(annotations=READ_ONLY)
async def get_burndown(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str | None = None,
) -> dict[str, Any]:
    """Dane burndown chart dla sprintu -- ideal line, actual line, velocity, prognoza.

    Jesli sprint_id nie podany, uzywa aktywnego sprintu.
    Wymaga scrum:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "scrum", "read")

        sprint_uuid: uuid.UUID | None = None
        if sprint_id is not None:
            sprint_uuid = uuid.UUID(sprint_id)

        return await svc_get_burndown_data(db, project.id, sprint_uuid)


# --- Zalaczniki do ticketow (pobieranie) ---


@mcp.tool(annotations=READ_ONLY)
async def get_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    """Pobierz zawartosc zalacznika z ticketa (base64). ticket_id: UUID lub klucz (np. MNX-12). Obrazki zwracane jako data URI. Wymaga scrum:read."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await ensure_permission(db, user.id, project.id, "scrum", "read")

        resolved_id = await tickets_service.resolve_ticket_uuid(ticket_id, project.id, db)
        result = await db.execute(
            select(TicketAttachment)
            .join(Ticket, TicketAttachment.ticket_id == Ticket.id)
            .where(
                TicketAttachment.id == uuid.UUID(attachment_id),
                Ticket.id == resolved_id,
                Ticket.project_id == project.id,
            )
        )
        attachment = result.scalar_one_or_none()
        if attachment is None:
            raise ValueError("Zalacznik nie istnieje")

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            data, content_type = await loop.run_in_executor(
                executor,
                lambda: minio_get_attachment(attachment.storage_path),
            )

    content_b64 = base64.b64encode(data).decode("ascii")
    if content_type.startswith("image/"):
        content_b64 = f"data:{content_type};base64,{content_b64}"

    return {
        "attachment_id": str(attachment.id),
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "size": attachment.size,
        "content_base64": content_b64,
    }


# --- Zalaczniki do stron Wiki ---


@mcp.tool(annotations=READ_ONLY)
async def get_wiki_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
    attachment_filename: str,
) -> dict[str, Any]:
    """Pobierz zawartosc zalacznika ze strony Wiki (base64)."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        content = await wiki_attachments_service.get_wiki_attachment(db, user, project, page_id, attachment_filename, get_object=minio_get_attachment)

    return {
        "filename": content.attachment.filename,
        "mime_type": content.attachment.mime_type,
        "size": content.attachment.size,
        "content_base64": content.content_base64,
    }


@mcp.tool(annotations=WRITE)
async def add_wiki_page_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
    file_base64: str,
    filename: str,
    mime_type: str | None = None,
) -> dict[str, Any]:
    """Dodaj zalacznik do strony Wiki. Plik zakodowany w base64. Maksymalny rozmiar: 200MB."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        attachment = await wiki_attachments_service.add_wiki_page_attachment(
            db, user, project, page_id, file_base64, filename, mime_type, upload_object=minio_upload_object
        )

    return {
        "attachment_id": str(attachment.id),
        "filename": attachment.filename,
        "size": attachment.size,
        "uploaded_at": attachment.created_at.isoformat(),
    }


@mcp.tool(annotations=READ_ONLY)
async def list_wiki_page_attachments(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
) -> list[dict[str, Any]]:
    """Zwroc liste zalacznikow strony Wiki. Nie zwraca zawartosci plikow - tylko metadane."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        attachments = await wiki_attachments_service.list_wiki_page_attachments(db, user, project, page_id)

    return [
        {
            "attachment_id": str(a.id),
            "filename": a.filename,
            "mime_type": a.mime_type,
            "size": a.size,
            "created_at": a.created_at.isoformat(),
            "created_via_ai": a.created_via_ai,
        }
        for a in attachments
    ]


# --- Globalne pliki Wiki ---


@mcp.tool(annotations=WRITE)
async def add_wiki_file(
    ctx: Context[Any, Any],
    project_slug: str,
    file_base64: str,
    filename: str,
    mime_type: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Dodaj globalny plik do repozytorium Wiki projektu. Plik zakodowany w base64. Maksymalny rozmiar: 200MB."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        wiki_file = await wiki_attachments_service.add_wiki_file(
            db, user, project, file_base64, filename, mime_type, description, upload_object=minio_upload_object
        )

    return {
        "file_id": str(wiki_file.id),
        "filename": wiki_file.filename,
        "size": wiki_file.size,
        "description": wiki_file.description,
        "uploaded_at": wiki_file.created_at.isoformat(),
    }


@mcp.tool(annotations=READ_ONLY)
async def get_wiki_file(
    ctx: Context[Any, Any],
    project_slug: str,
    file_id: str,
) -> dict[str, Any]:
    """Pobierz zawartosc globalnego pliku Wiki (base64)."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        content = await wiki_attachments_service.get_wiki_file(db, user, project, file_id, get_object=minio_get_attachment)

    return {
        "file_id": str(content.wiki_file.id),
        "filename": content.wiki_file.filename,
        "mime_type": content.wiki_file.mime_type,
        "size": content.wiki_file.size,
        "description": content.wiki_file.description,
        "content_base64": content.content_base64,
    }


@mcp.tool(annotations=WRITE)
async def update_wiki_file(
    ctx: Context[Any, Any],
    project_slug: str,
    file_id: str,
    description: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Zaktualizuj globalny plik Wiki — opis lub nazwe. Podaj tylko pola do zmiany."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        wiki_file = await wiki_attachments_service.update_wiki_file(db, user, project, file_id, description, filename)

    return {
        "file_id": str(wiki_file.id),
        "filename": wiki_file.filename,
        "description": wiki_file.description,
        "message": "Plik zaktualizowany",
    }


@mcp.tool(annotations=READ_ONLY)
async def list_wiki_files(
    ctx: Context[Any, Any],
    project_slug: str,
) -> list[dict[str, Any]]:
    """Lista globalnych plikow w repozytorium Wiki projektu."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        files = await wiki_attachments_service.list_wiki_files(db, user, project)

    return [
        {
            "file_id": str(f.id),
            "filename": f.filename,
            "mime_type": f.mime_type,
            "size": f.size,
            "description": f.description,
            "created_at": f.created_at.isoformat(),
        }
        for f in files
    ]


# --- Kryteria akceptacji ---


@mcp.tool(annotations=READ_ONLY)
async def list_acceptance_criteria(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
) -> str:
    """Lista kryteriów akceptacji ticketa. ticket_id: UUID lub klucz (np. MNX-12).

    Zwraca tabele z kolumnami: checkbox, opis, autor, data, AI.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        criteria = await tickets_service.list_acceptance_criteria(db, user, project, ticket_id)

    if not criteria:
        return "Brak kryteriów akceptacji."

    lines: list[str] = [f"Kryteria akceptacji — {len(criteria)} pozycji\n"]
    for ac in criteria:
        checkbox = "[x]" if ac.is_completed else "[ ]"
        author = ac.created_by_user.email if ac.created_by_user else "—"
        ai_flag = " (AI)" if ac.created_via_ai else ""
        date_str = ac.created_at.date().isoformat()
        completed_str = ""
        if ac.is_completed and ac.completed_at:
            completed_by = ac.completed_by_user.email if ac.completed_by_user else "—"
            completed_str = f" | ukonczone: {ac.completed_at.date().isoformat()} przez {completed_by}"
        lines.append(f"{checkbox} [{ac.id}] {ac.description}{ai_flag} | autor: {author} | {date_str}{completed_str}")

    return "\n".join(lines)


@mcp.tool(annotations=WRITE)
async def add_acceptance_criterion(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    description: str,
) -> dict[str, Any]:
    """Dodaj kryterium akceptacji do ticketa. ticket_id: UUID lub klucz (np. MNX-12). Oznaczane jako created_via_ai=True."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        criterion = await tickets_service.add_acceptance_criterion(db, user, project, ticket_id, description)

    return {
        "id": str(criterion.id),
        "message": "Kryterium akceptacji dodane",
        "created_via_ai": True,
        "position": criterion.position,
    }


@mcp.tool(annotations=WRITE)
async def update_acceptance_criterion(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    criterion_id: str,
    description: str | None = None,
    is_completed: bool | None = None,
) -> dict[str, Any]:
    """Zaktualizuj kryterium akceptacji (opis i/lub status ukończenia). Podaj tylko pola do zmiany.

    ticket_id: UUID lub klucz (np. MNX-12).
    criterion_id: UUID kryterium.
    Przy ustawieniu is_completed=True: completed_via_ai=True.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        criterion = await tickets_service.update_acceptance_criterion(
            db,
            user,
            project,
            ticket_id,
            criterion_id,
            description=description,
            is_completed=is_completed,
        )

    return {
        "id": str(criterion.id),
        "message": "Kryterium akceptacji zaktualizowane",
        "is_completed": criterion.is_completed,
        "description": criterion.description,
    }


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_acceptance_criterion(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    criterion_id: str,
) -> dict[str, Any]:
    """Usuń kryterium akceptacji. ticket_id: UUID lub klucz (np. MNX-12). criterion_id: UUID kryterium."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await tickets_service.delete_acceptance_criterion(db, user, project, ticket_id, criterion_id)

    return {"message": "Kryterium akceptacji usunięte", "id": criterion_id}


@mcp.tool(annotations=READ_ONLY)
async def list_roles(
    ctx: Context[Any, Any],
    project_slug: str,
) -> str:
    """Lista ról w projekcie z uprawnieniami."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "settings", "read"):
            raise ValueError("Brak uprawnienia do przegladu rol (settings:read)")

        result = await db.execute(select(Role).where(Role.project_id == project.id).order_by(Role.is_system.desc(), Role.name))
        roles = result.scalars().all()

    if not roles:
        return "Brak zdefiniowanych ról w projekcie."

    lines = ["Name                 | System | Permissions summary                          | ID"]
    lines.append("-" * 100)
    for role in roles:
        perms_summary = ", ".join(f"{mod}:[{','.join(actions)}]" for mod, actions in role.permissions.items() if actions) or "(brak)"
        system_label = "tak" if role.is_system else "nie"
        name_col = role.name[:20].ljust(20)
        perms_col = perms_summary[:44].ljust(44)
        lines.append(f"{name_col} | {system_label.ljust(6)} | {perms_col} | {role.id}")

    return "\n".join(lines)


@mcp.tool(annotations=WRITE)
async def create_role(
    ctx: Context[Any, Any],
    project_slug: str,
    name: str,
    permissions: dict[str, list[str]],
) -> dict[str, Any]:
    """Utwórz nową rolę z określonymi uprawnieniami.

    permissions: dict mapujący moduł na listę akcji, np. {"scrum": ["read", "write"], "wiki": ["read"]}
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    name = name.strip()
    if not name:
        raise ValueError("Nazwa roli nie moze byc pusta")
    if len(name) > 50:
        raise ValueError("Nazwa roli nie moze byc dluzsza niz 50 znakow")

    invalid_modules = set(permissions.keys()) - set(PERMISSION_MODULES)
    if invalid_modules:
        raise ValueError(f"Nieprawidlowe moduly: {', '.join(sorted(invalid_modules))}. Dozwolone: {', '.join(PERMISSION_MODULES)}")

    for mod, actions in permissions.items():
        invalid_actions = set(actions) - set(PERMISSION_ACTIONS)
        if invalid_actions:
            allowed = ", ".join(PERMISSION_ACTIONS)
            raise ValueError(f"Nieprawidlowe akcje dla modulu '{mod}': {', '.join(sorted(invalid_actions))}. Dozwolone: {allowed}")

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "settings", "write"):
            raise ValueError("Brak uprawnienia do tworzenia rol (settings:write)")
        await ensure_can_manage_role(db, user.id, project.id, granted=permissions)

        role = Role(
            name=name,
            project_id=project.id,
            permissions=permissions,
            is_system=False,
        )
        db.add(role)
        try:
            await db.commit()
            await db.refresh(role)
        except IntegrityError:
            await db.rollback()
            raise ValueError(f"Rola o nazwie '{name}' juz istnieje w projekcie '{project_slug}'") from None

    return {
        "id": str(role.id),
        "name": role.name,
        "permissions": role.permissions,
        "message": f"Rola '{name}' zostala utworzona.",
    }


@mcp.tool(annotations=WRITE)
async def update_role(
    ctx: Context[Any, Any],
    project_slug: str,
    role_id: str,
    name: str | None = None,
    permissions: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Zaktualizuj istniejącą rolę (nazwę i/lub uprawnienia).

    role_id: UUID roli do edycji.
    name: nowa nazwa (opcjonalne).
    permissions: nowe uprawnienia jako dict modul -> lista akcji (opcjonalne).
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    try:
        role_uuid = uuid.UUID(role_id)
    except ValueError:
        raise ValueError("Nieprawidlowy format role_id (oczekiwany UUID)") from None

    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Nazwa roli nie moze byc pusta")
        if len(name) > 50:
            raise ValueError("Nazwa roli nie moze byc dluzsza niz 50 znakow")

    if permissions is not None:
        invalid_modules = set(permissions.keys()) - set(PERMISSION_MODULES)
        if invalid_modules:
            raise ValueError(f"Nieprawidlowe moduly: {', '.join(sorted(invalid_modules))}. Dozwolone: {', '.join(PERMISSION_MODULES)}")
        for mod, actions in permissions.items():
            invalid_actions = set(actions) - set(PERMISSION_ACTIONS)
            if invalid_actions:
                allowed = ", ".join(PERMISSION_ACTIONS)
                raise ValueError(f"Nieprawidlowe akcje dla modulu '{mod}': {', '.join(sorted(invalid_actions))}. Dozwolone: {allowed}")

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "settings", "write"):
            raise ValueError("Brak uprawnienia do edycji rol (settings:write)")

        role_result = await db.execute(select(Role).where(Role.id == role_uuid, Role.project_id == project.id))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise ValueError(f"Rola '{role_id}' nie istnieje w projekcie '{project_slug}'")

        if permissions is not None:
            await ensure_can_manage_role(db, user.id, project.id, granted=permissions, edited_role=role)

        if name is not None:
            if role.is_system:
                raise ValueError("Nie mozna zmienic nazwy roli systemowej")
            role.name = name

        if permissions is not None:
            role.permissions = permissions

        try:
            await db.commit()
            await db.refresh(role)
        except IntegrityError:
            await db.rollback()
            raise ValueError(f"Rola o nazwie '{name}' juz istnieje w projekcie '{project_slug}'") from None

    return {
        "id": str(role.id),
        "name": role.name,
        "permissions": role.permissions,
        "message": f"Rola '{role.name}' zostala zaktualizowana.",
    }


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_role(
    ctx: Context[Any, Any],
    project_slug: str,
    role_id: str,
) -> dict[str, Any]:
    """Usuń rolę z projektu.

    role_id: UUID roli do usuniecia.
    Nie mozna usunac ról systemowych ani ról przypisanych do czlonkow projektu.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    try:
        role_uuid = uuid.UUID(role_id)
    except ValueError:
        raise ValueError("Nieprawidlowy format role_id (oczekiwany UUID)") from None

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "settings", "delete"):
            raise ValueError("Brak uprawnienia do usuwania rol (settings:delete)")

        role_result = await db.execute(select(Role).where(Role.id == role_uuid, Role.project_id == project.id))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise ValueError(f"Rola '{role_id}' nie istnieje w projekcie '{project_slug}'")

        if role.is_system:
            raise ValueError(f"Nie mozna usunac roli systemowej '{role.name}'")

        members_count_result = await db.execute(select(func.count(ProjectMember.id)).where(ProjectMember.role_id == role_uuid))
        members_count = members_count_result.scalar_one()
        if members_count > 0:
            raise ValueError(
                f"Nie mozna usunac roli '{role.name}' — jest przypisana do {members_count} czlonka/czlonkow projektu. "
                "Najpierw przypisz tym czlonkom inna role."
            )

        role_name = role.name
        await db.delete(role)
        await db.commit()

    return {"message": f"Rola '{role_name}' zostala usunieta z projektu '{project_slug}'"}


@mcp.tool(annotations=WRITE)
async def assign_role(
    ctx: Context[Any, Any],
    project_slug: str,
    user_email: str,
    role_id: str,
) -> dict[str, Any]:
    """Przypisz rolę RBAC do członka projektu.

    user_email: email czlonka projektu.
    role_id: UUID roli do przypisania.
    """
    calling_user, project = await _get_user_and_project(ctx, project_slug)

    user_email = user_email.strip().lower()
    if not user_email:
        raise ValueError("Email nie moze byc pusty")
    if "@" not in user_email or len(user_email) > 255:
        raise ValueError("Nieprawidlowy format adresu email")

    try:
        role_uuid = uuid.UUID(role_id)
    except ValueError:
        raise ValueError("Nieprawidlowy format role_id (oczekiwany UUID)") from None

    async with async_session_factory() as db:
        if not await check_permission(db, calling_user.id, project.id, "settings", "write"):
            raise ValueError("Brak uprawnienia do przypisywania rol (settings:write)")

        # Znajdz uzytkownika po emailu
        target_user_result = await db.execute(select(User).where(User.email == user_email, User.is_active.is_(True)))
        target_user = target_user_result.scalar_one_or_none()
        if target_user is None:
            raise ValueError(f"Uzytkownik '{user_email}' nie istnieje lub jest dezaktywowany")

        # Znajdz membership
        member_result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == target_user.id,
            )
        )
        member = member_result.scalar_one_or_none()
        if member is None:
            raise ValueError(f"Uzytkownik '{user_email}' nie jest czlonkiem projektu '{project_slug}'")

        # Znajdz role
        role_result = await db.execute(select(Role).where(Role.id == role_uuid, Role.project_id == project.id))
        role = role_result.scalar_one_or_none()
        if role is None:
            raise ValueError(f"Rola '{role_id}' nie istnieje w projekcie '{project_slug}'")

        await ensure_can_manage_role(db, calling_user.id, project.id, granted=role.permissions, assigned_role=role, target_member=member)

        member.role_id = role.id
        member.role = role.name.lower()

        await db.commit()

    return {
        "message": f"Rola '{role.name}' zostala przypisana do uzytkownika '{user_email}' w projekcie '{project_slug}'.",
        "user_email": user_email,
        "role_id": str(role.id),
        "role_name": role.name,
    }


@mcp.tool(annotations=READ_ONLY)
async def get_member_permissions(
    ctx: Context[Any, Any],
    project_slug: str,
    user_email: str,
) -> dict[str, Any]:
    """Zwróć uprawnienia RBAC członka projektu.

    user_email: email czlonka projektu, ktorego uprawnienia chcemy sprawdzic.
    """
    calling_user, project = await _get_user_and_project(ctx, project_slug)

    user_email = user_email.strip().lower()
    if not user_email:
        raise ValueError("Email nie moze byc pusty")
    if "@" not in user_email or len(user_email) > 255:
        raise ValueError("Nieprawidlowy format adresu email")

    async with async_session_factory() as db:
        if not await check_permission(db, calling_user.id, project.id, "settings", "read"):
            raise ValueError("Brak uprawnienia do przegladu uprawnien (settings:read)")

        target_user_result = await db.execute(select(User).where(User.email == user_email, User.is_active.is_(True)))
        target_user = target_user_result.scalar_one_or_none()
        if target_user is None:
            raise ValueError(f"Uzytkownik '{user_email}' nie istnieje lub jest dezaktywowany")

        member_result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == target_user.id,
            )
        )
        member = member_result.scalar_one_or_none()
        if member is None:
            raise ValueError(f"Uzytkownik '{user_email}' nie jest czlonkiem projektu '{project_slug}'")

        perms = await get_user_permissions(db, target_user.id, project.id)

    return {
        "user_email": user_email,
        "project_slug": project_slug,
        "permissions": perms,
    }


# --- Rozliczenia (Settlements) ---


async def _get_settlement_for_mcp(
    db: AsyncSession,
    settlement_id: uuid.UUID,
    project_id: uuid.UUID,
) -> Settlement:
    """Pobierz settlement z eager load + walidacja M2M dla MCP."""
    result = await db.execute(
        select(Settlement)
        .join(SettlementProject, SettlementProject.settlement_id == Settlement.id)
        .where(
            Settlement.id == settlement_id,
            Settlement.is_active.is_(True),
            SettlementProject.project_id == project_id,
        )
        .options(
            selectinload(Settlement.projects.and_(Project.is_active.is_(True))),
            selectinload(Settlement.tickets).selectinload(Ticket.project),
            selectinload(Settlement.attachments),
            selectinload(Settlement.created_by),
        )
    )
    settlement = result.scalar_one_or_none()
    if settlement is None:
        raise ValueError("Rozliczenie nie istnieje lub brak dostepu")
    return settlement


async def _ensure_settlement_access_for_mcp(
    db: AsyncSession,
    user_id: uuid.UUID,
    settlement: Settlement,
    action: str,
) -> None:
    """Wymaga rozliczenia:<action> we wszystkich projektach rozliczenia; odmowa jako ValueError."""
    try:
        await ensure_settlement_permission(db, user_id, settlement, action)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


def _settlement_to_dict(s: Settlement) -> dict[str, Any]:
    """Konwertuj obiekt Settlement do dict dla MCP."""
    return {
        "settlement_id": str(s.id),
        "number": s.number,
        "name": s.name,
        "status": s.status,
        "period_from": s.period_from.isoformat(),
        "period_to": s.period_to.isoformat(),
        "notes": s.notes,
        "is_active": s.is_active,
        "created_at": s.created_at.isoformat(),
        "updated_at": s.updated_at.isoformat(),
        "sent_at": s.sent_at.isoformat() if s.sent_at else None,
        "paid_at": s.paid_at.isoformat() if s.paid_at else None,
        "created_by_email": s.created_by.email if s.created_by else None,
        "projects": [{"project_id": str(p.id), "name": p.name, "slug": p.slug} for p in (s.projects or [])],
        "ticket_count": len(s.tickets or []),
        "attachment_count": len(s.attachments or []),
    }


@mcp.tool(annotations=READ_ONLY)
async def list_settlements(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """Lista rozliczen projektu z filtrowaniem i paginacja.

    status: draft | sent | paid (opcjonalnie).
    date_from / date_to: YYYY-MM-DD (filtruje po period_from/period_to).
    Wymaga rozliczenia:read; rozliczenia z projektem, w ktorym user nie ma
    rozliczenia:read, nie sa listowane.
    """
    user, project = await _get_user_and_project(ctx, project_slug)
    per_page = 20

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "read"):
            raise ValueError("Brak uprawnienia rozliczenia:read")

        conditions = [
            SettlementProject.project_id == project.id,
            Settlement.is_active.is_(True),
            fully_permitted_settlements_clause(await get_permitted_project_ids(db, user.id, "read")),
        ]
        if status and status in {"draft", "sent", "paid"}:
            conditions.append(Settlement.status == status)
        if date_from:
            conditions.append(Settlement.period_from >= date.fromisoformat(date_from))
        if date_to:
            conditions.append(Settlement.period_to <= date.fromisoformat(date_to))

        count_result = await db.execute(
            select(func.count(Settlement.id.distinct())).join(SettlementProject, SettlementProject.settlement_id == Settlement.id).where(*conditions)
        )
        total = count_result.scalar() or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        result = await db.execute(
            select(Settlement)
            .join(SettlementProject, SettlementProject.settlement_id == Settlement.id)
            .where(*conditions)
            .options(
                selectinload(Settlement.projects.and_(Project.is_active.is_(True))),
                selectinload(Settlement.tickets),
                selectinload(Settlement.attachments),
                selectinload(Settlement.created_by),
            )
            .order_by(Settlement.created_at.desc())
            .distinct()
            .limit(per_page)
            .offset((page - 1) * per_page)
        )
        settlements = list(result.scalars().unique().all())

    return {
        "settlements": [_settlement_to_dict(s) for s in settlements],
        "page": page,
        "total_pages": total_pages,
        "total": total,
    }


@mcp.tool(annotations=READ_ONLY)
async def get_settlement(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
) -> dict[str, Any]:
    """Pobierz szczegoly rozliczenia (projekty, tickety, zalaczniki).

    Wymaga rozliczenia:read we wszystkich projektach powiazanych z rozliczeniem.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "read"):
            raise ValueError("Brak uprawnienia rozliczenia:read")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)
        await _ensure_settlement_access_for_mcp(db, user.id, settlement, "read")

    result = _settlement_to_dict(settlement)
    result["tickets"] = [
        {
            "ticket_id": str(t.id),
            "key": f"{t.project.code}-{t.number}" if t.project else str(t.id),
            "title": t.title,
            "status": t.status,
            "project_slug": t.project.slug if t.project else None,
        }
        for t in (settlement.tickets or [])
    ]
    result["attachments"] = [
        {
            "attachment_id": str(a.id),
            "filename": a.filename,
            "size": a.size,
            "category": a.category,
            "state": a.state,
            "mime_type": a.mime_type,
            "created_at": a.created_at.isoformat(),
        }
        for a in (settlement.attachments or [])
    ]
    return result


@mcp.tool(annotations=WRITE)
async def create_settlement(
    ctx: Context[Any, Any],
    project_slug: str,
    name: str,
    period_from: str,
    period_to: str,
    additional_project_slugs: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Utworz nowe rozliczenie.

    period_from / period_to: YYYY-MM-DD.
    additional_project_slugs: opcjonalna lista slug-ow dodatkowych projektow.
    Wymaga rozliczenia:write we wszystkich projektach (biezacym + dodatkowych).
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "write"):
            raise ValueError("Brak uprawnienia rozliczenia:write")

        project_ids: list[uuid.UUID] = [project.id]

        if additional_project_slugs:
            for slug in additional_project_slugs:
                slug = slug.strip()
                if not slug:
                    continue
                extra_result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
                extra_project = extra_result.scalar_one_or_none()
                if extra_project is None:
                    raise ValueError(f"Projekt '{slug}' nie istnieje lub jest nieaktywny")
                if not await check_permission(db, user.id, extra_project.id, "rozliczenia", "write"):
                    raise ValueError(f"Brak uprawnienia rozliczenia:write w projekcie '{slug}'")
                if extra_project.id not in project_ids:
                    project_ids.append(extra_project.id)

        settlement = await svc_create_settlement(
            db=db,
            user_id=user.id,
            name=name,
            period_from=date.fromisoformat(period_from),
            period_to=date.fromisoformat(period_to),
            project_ids=project_ids,
            notes=notes,
        )

        # Re-load z relacjami
        settlement = await _get_settlement_for_mcp(db, settlement.id, project.id)

    return _settlement_to_dict(settlement)


@mcp.tool(annotations=WRITE)
async def update_settlement(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
    name: str | None = None,
    period_from: str | None = None,
    period_to: str | None = None,
    project_slugs: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Edytuj rozliczenie (tylko draft).

    project_slugs: jesli podane -- zastepuje liste projektow (musi zawierac biezacy projekt).
    Wymaga rozliczenia:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "write"):
            raise ValueError("Brak uprawnienia rozliczenia:write")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)

        # Aktualne wartosci jako defaults
        new_name = name if name is not None else settlement.name
        new_period_from = date.fromisoformat(period_from) if period_from else settlement.period_from
        new_period_to = date.fromisoformat(period_to) if period_to else settlement.period_to
        new_notes = notes if notes is not None else settlement.notes

        # Resolve project_ids
        if project_slugs is not None:
            new_project_ids: list[uuid.UUID] = []
            for slug in project_slugs:
                slug = slug.strip()
                if not slug:
                    continue
                p_result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
                p = p_result.scalar_one_or_none()
                if p is None:
                    raise ValueError(f"Projekt '{slug}' nie istnieje lub jest nieaktywny")
                if p.id not in new_project_ids:
                    new_project_ids.append(p.id)
            if not new_project_ids:
                raise ValueError("Lista projektow nie moze byc pusta")
        else:
            new_project_ids = [p.id for p in settlement.projects]

        settlement = await svc_update_settlement(
            db=db,
            settlement=settlement,
            user_id=user.id,
            name=new_name,
            period_from=new_period_from,
            period_to=new_period_to,
            project_ids=new_project_ids,
            notes=new_notes,
        )
        settlement = await _get_settlement_for_mcp(db, settlement.id, project.id)

    return _settlement_to_dict(settlement)


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_settlement(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
) -> dict[str, Any]:
    """Usun rozliczenie (soft delete, tylko draft).

    Wymaga rozliczenia:delete.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "delete"):
            raise ValueError("Brak uprawnienia rozliczenia:delete")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)
        await svc_delete_settlement(db=db, settlement=settlement, user_id=user.id)

    return {"deleted": True, "settlement_id": settlement_id}


@mcp.tool(annotations=WRITE)
async def change_settlement_status(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
    new_status: str,
) -> dict[str, Any]:
    """Zmien status rozliczenia (draft->sent->paid, sent->draft, paid->sent).

    Wymaga rozliczenia:write we wszystkich projektach powiazanych z rozliczeniem.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "write"):
            raise ValueError("Brak uprawnienia rozliczenia:write")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)
        settlement = await svc_change_settlement_status(
            db=db,
            settlement=settlement,
            user_id=user.id,
            new_status=new_status,
        )

    return {
        "settlement_id": settlement_id,
        "status": settlement.status,
        "sent_at": settlement.sent_at.isoformat() if settlement.sent_at else None,
        "paid_at": settlement.paid_at.isoformat() if settlement.paid_at else None,
    }


@mcp.tool(annotations=WRITE)
async def link_ticket_to_settlement(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
    ticket_id: str,
) -> dict[str, Any]:
    """Podepnij ticket do rozliczenia (tylko draft, ticket musi byc z projektu M2M).

    Wymaga rozliczenia:read we wszystkich projektach powiazanych z rozliczeniem
    oraz rozliczenia:write w projekcie ticketu.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "write"):
            raise ValueError("Brak uprawnienia rozliczenia:write")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)
        await _ensure_settlement_access_for_mcp(db, user.id, settlement, "read")

        ticket_result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id == uuid.UUID(ticket_id)))
        ticket = ticket_result.scalar_one_or_none()
        if ticket is None:
            raise ValueError("Ticket nie istnieje")

        await validate_settlement_ticket_link(db, settlement, ticket)
        try:
            await ensure_ticket_project_write(db, user.id, ticket)
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc

        linked_ids = {t.id for t in settlement.tickets}
        if ticket.id not in linked_ids:
            settlement.tickets.append(ticket)
            await db.commit()

    return {"linked": True, "settlement_id": settlement_id, "ticket_id": ticket_id}


@mcp.tool(annotations=DESTRUCTIVE)
async def unlink_ticket_from_settlement(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
    ticket_id: str,
) -> dict[str, Any]:
    """Odepnij ticket od rozliczenia (tylko draft).

    Wymaga rozliczenia:read we wszystkich projektach powiazanych z rozliczeniem
    oraz rozliczenia:write w projekcie ticketu.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "write"):
            raise ValueError("Brak uprawnienia rozliczenia:write")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)
        await _ensure_settlement_access_for_mcp(db, user.id, settlement, "read")

        if settlement.status != "draft":
            raise ValueError("Mozna odpiac ticket tylko od rozliczenia w statusie draft")

        ticket_to_remove = next((t for t in settlement.tickets if t.id == uuid.UUID(ticket_id)), None)
        if ticket_to_remove is None:
            raise ValueError("Ticket nie jest podpiety do tego rozliczenia")
        try:
            await ensure_ticket_project_write(db, user.id, ticket_to_remove)
        except HTTPException as exc:
            raise ValueError(str(exc.detail)) from exc

        settlement.tickets.remove(ticket_to_remove)
        await db.commit()

    return {"unlinked": True, "settlement_id": settlement_id, "ticket_id": ticket_id}


@mcp.tool(annotations=READ_ONLY)
async def list_settlement_tickets(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
) -> list[dict[str, Any]]:
    """Lista ticketow podpietych do rozliczenia.

    Wymaga rozliczenia:read we wszystkich projektach powiazanych z rozliczeniem.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "read"):
            raise ValueError("Brak uprawnienia rozliczenia:read")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)
        await _ensure_settlement_access_for_mcp(db, user.id, settlement, "read")

    return [
        {
            "ticket_id": str(t.id),
            "key": f"{t.project.code}-{t.number}" if t.project else str(t.id),
            "title": t.title,
            "status": t.status,
            "project_slug": t.project.slug if t.project else None,
        }
        for t in (settlement.tickets or [])
    ]


@mcp.tool(annotations=READ_ONLY)
async def list_settlement_attachments(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
) -> list[dict[str, Any]]:
    """Lista metadanych zalacznikow rozliczenia (bez zawartosci pliku).

    Wymaga rozliczenia:read we wszystkich projektach powiazanych z rozliczeniem.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "read"):
            raise ValueError("Brak uprawnienia rozliczenia:read")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)
        await _ensure_settlement_access_for_mcp(db, user.id, settlement, "read")

        # Pobierz email uploadera dla kazdego zalacznika
        uploader_ids = {a.uploaded_by_id for a in (settlement.attachments or [])}
        uploader_map: dict[uuid.UUID, str] = {}
        if uploader_ids:
            uploaders_result = await db.execute(select(User).where(User.id.in_(uploader_ids)))
            for u in uploaders_result.scalars().all():
                uploader_map[u.id] = u.email

    return [
        {
            "attachment_id": str(a.id),
            "filename": a.filename,
            "size": a.size,
            "category": a.category,
            "state": a.state,
            "mime_type": a.mime_type,
            "uploaded_by_email": uploader_map.get(a.uploaded_by_id),
            "created_at": a.created_at.isoformat(),
        }
        for a in (settlement.attachments or [])
    ]


@mcp.tool(annotations=WRITE)
async def add_settlement_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
    file_base64: str,
    filename: str,
    category: str,
    state: str,
    mime_type: str | None = None,
) -> dict[str, Any]:
    """Dodaj zalacznik do rozliczenia. Plik zakodowany w base64.

    category: invoice | report | acceptance_protocol | other.
    state: draft | signed.
    Maksymalny rozmiar: 200MB. Wymaga rozliczenia:write.
    """
    max_size = 200 * 1024 * 1024  # 200MB

    user, project = await _get_user_and_project(ctx, project_slug)

    # Walidacja i sanityzacja filename
    if not filename or not filename.strip():
        raise ValueError("Nazwa pliku nie moze byc pusta")
    filename = os.path.basename(filename.strip())
    filename = re.sub(r'[\x00-\x1f"\\]', "_", filename)
    if not filename or filename in (".", ".."):
        raise ValueError("Nieprawidlowa nazwa pliku")
    if len(filename) > 255:
        raise ValueError("Nazwa pliku nie moze przekraczac 255 znakow")

    # Walidacja base64
    if not file_base64 or not file_base64.strip():
        raise ValueError("Zawartosc pliku (file_base64) nie moze byc pusta")

    try:
        file_bytes = base64.b64decode(file_base64, validate=True)
    except Exception as exc:
        raise ValueError("Nieprawidlowy format base64") from exc

    if len(file_bytes) > max_size:
        raise ValueError(f"Plik za duzy: max 200MB, otrzymano {len(file_bytes) / 1024 / 1024:.1f}MB")

    if mime_type is not None and not re.match(r"^[a-z]+/[a-z0-9.+\-]+$", mime_type):
        raise ValueError("Nieprawidlowy format mime_type, oczekiwano np. application/pdf, image/png")

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "write"):
            raise ValueError("Brak uprawnienia rozliczenia:write")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)

        attachment = await svc_upload_settlement_attachment(
            db=db,
            settlement=settlement,
            user_id=user.id,
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
            category=category,
            state=state,
        )
        await db.commit()

    return {
        "attachment_id": str(attachment.id),
        "filename": attachment.filename,
        "size": attachment.size,
        "uploaded_at": attachment.created_at.isoformat(),
    }


@mcp.tool(annotations=READ_ONLY)
async def get_settlement_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    """Pobierz zawartosc zalacznika rozliczenia (base64 + mime_type).

    Wymaga rozliczenia:read we wszystkich projektach powiazanych z rozliczeniem.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "read"):
            raise ValueError("Brak uprawnienia rozliczenia:read")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)
        await _ensure_settlement_access_for_mcp(db, user.id, settlement, "read")

        matching = [a for a in (settlement.attachments or []) if a.id == uuid.UUID(attachment_id)]
        if not matching:
            raise ValueError("Zalacznik nie istnieje")
        attachment = matching[0]

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            file_bytes, content_type = await loop.run_in_executor(
                executor,
                lambda: get_settlement_attachment_bytes(attachment),
            )

    return {
        "attachment_id": str(attachment.id),
        "filename": attachment.filename,
        "mime_type": content_type,
        "size": attachment.size,
        "file_base64": base64.b64encode(file_bytes).decode("utf-8"),
    }


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_settlement_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    settlement_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    """Usun zalacznik rozliczenia (MinIO + DB, tylko draft).

    Wymaga rozliczenia:delete.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        if not await check_permission(db, user.id, project.id, "rozliczenia", "delete"):
            raise ValueError("Brak uprawnienia rozliczenia:delete")

        settlement = await _get_settlement_for_mcp(db, uuid.UUID(settlement_id), project.id)

        matching = [a for a in (settlement.attachments or []) if a.id == uuid.UUID(attachment_id)]
        if not matching:
            raise ValueError("Zalacznik nie istnieje")
        attachment = matching[0]

        await svc_delete_settlement_attachment(db=db, attachment=attachment, user_id=user.id)
        await db.commit()

    return {"deleted": True, "attachment_id": attachment_id}


def _list_available_skills() -> list[str]:
    """Zwroc nazwy dostepnych skilli (subdirs z plikiem SKILL.md)."""
    if not _STATIC_SKILLS_DIR.is_dir():
        return []
    names = []
    for entry in sorted(_STATIC_SKILLS_DIR.iterdir()):
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            names.append(entry.name)
    return names


def _list_skill_files(skill_dir: Path) -> list[Path]:
    """Return skill resources in stable order, with SKILL.md first."""
    root = skill_dir.resolve()
    files = []
    for path in skill_dir.rglob("*"):
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Resource escapes skill directory: {path}")
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda path: (path != skill_dir / "SKILL.md", path.relative_to(skill_dir).as_posix()))


def _render_skill_content(raw: str, project_slug: str) -> str:
    """Podmien placeholdery projektu w tresci SKILL.md."""
    content = raw
    for placeholder in _SKILL_SLUG_PLACEHOLDERS:
        content = content.replace(placeholder, project_slug)
    return content


def _parse_skill_frontmatter(raw: str) -> dict[str, Any]:
    """Wyciagnij frontmatter YAML z poczatku SKILL.md."""
    import yaml  # type: ignore[import-untyped]

    if not raw.startswith("---\n"):
        return {}
    end = raw.find("\n---", 4)
    if end == -1:
        return {}
    try:
        data = yaml.safe_load(raw[4:end]) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


@mcp.tool(annotations=READ_ONLY)
async def install_monolynx_skills(
    ctx: Context[Any, Any],
    project_slug: str,
    skill_names: list[str] | None = None,
    target: str = "claude",
) -> dict[str, Any]:
    """Pobierz domyslne skille Monolynx do zapisu w katalogu skilli projektu uzytkownika.

    Dwa tryby:
    - `skill_names=None` (domyslnie): zwraca LISTE dostepnych skilli z nazwami i opisami
      (bez pelnej tresci). Uzyj zeby zobaczyc co jest dostepne.
    - `skill_names=[...]`: zwraca PELNA tresc wskazanych skilli do zapisu na dysk.
      Placeholdery `<PROJECT-SLUG>` i `<PROJECT-ID>` sa podmieniane na `project_slug`.

    Argumenty:
    - `project_slug`: slug projektu Monolynx (wymagany; weryfikuje czlonkostwo uzytkownika)
    - `skill_names`: opcjonalna lista nazw skilli do pobrania pelnej tresci
    - `target`: runtime docelowy - `claude` (domyslnie, `.claude/skills/`),
      `codex` (`.agents/skills/`) lub `cursor` (`.cursor/skills/`). Format plikow jest
      identyczny dla wszystkich trzech; zmienia sie tylko katalog w `relative_path`.

    Zwraca (tryb listy):
    - `project_slug`, `available` (nazwy), `catalog`: [{name, description}]

    Zwraca (tryb pelny):
    - `project_slug`, `available`, `skills`: [{name, files: [{relative_path, content}]}]
      gdzie `relative_path` to docelowa sciezka (np. `.claude/skills/monolynx-work/SKILL.md`).
      Skill moze miec pliki pomocnicze obok `SKILL.md` (np. `pipeline.md`) - zapisz
      WSZYSTKIE pliki z listy `files`, inaczej skill straci czesc instrukcji.

    Zalecane uzycie: najpierw wywolaj bez `skill_names` zeby zobaczyc katalog, potem
    pobieraj pelna tresc per 1-3 skille aby uniknac przekroczenia limitu odpowiedzi.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    skills_dir = _SKILL_TARGET_DIRS.get(target)
    if skills_dir is None:
        raise ValueError(f"Nieznany target: {target}. Dostepne: {', '.join(_SKILL_TARGET_DIRS)}")

    available = _list_available_skills()
    if not available:
        raise ValueError("Brak skilli do zainstalowania (katalog static/skills pusty lub niedostepny)")

    if skill_names is None:
        catalog: list[dict[str, str]] = []
        for name in available:
            raw = (_STATIC_SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
            meta = _parse_skill_frontmatter(raw)
            catalog.append(
                {
                    "name": name,
                    "description": str(meta.get("description", "")).strip(),
                }
            )
        return {
            "project_slug": project.slug,
            "target": target,
            "skills_dir": skills_dir,
            "available": available,
            "catalog": catalog,
            "hint": "Wywolaj ponownie z `skill_names=[...]` aby pobrac pelna tresc wybranych skilli.",
        }

    unknown = [n for n in skill_names if n not in available]
    if unknown:
        raise ValueError(f"Nieznane skille: {', '.join(unknown)}. Dostepne: {', '.join(available)}")
    selected = list(dict.fromkeys(skill_names))

    skills: list[dict[str, Any]] = []
    for name in selected:
        files = [
            {
                "relative_path": f"{skills_dir}/{name}/{path.relative_to(_STATIC_SKILLS_DIR / name).as_posix()}",
                "content": (
                    _render_skill_content(path.read_text(encoding="utf-8"), project.slug)
                    if path.suffix == ".md"
                    else path.read_text(encoding="utf-8")
                ),
            }
            for path in _list_skill_files(_STATIC_SKILLS_DIR / name)
        ]
        skills.append({"name": name, "files": files})

    return {
        "project_slug": project.slug,
        "target": target,
        "skills_dir": skills_dir,
        "available": available,
        "skills": skills,
    }


# --- Plan pracy ---

_TICKET_KEY_GLOBAL_RE = re.compile(r"^([A-Za-z]{1,10})-(\d+)$")


async def _resolve_ticket_globally(ticket_id_str: str, user_id: uuid.UUID) -> uuid.UUID:
    """Zamien ticket_id (UUID lub klucz globalny np. MON-70) na UUID ticketa.

    Nie wymaga project_id — resolwuje po project.code + ticket.number, tylko w aktywnych
    projektach uzytkownika. Komunikat bez echa klucza: ticket cudzy i nieistniejacy sa nieodroznialni.
    """
    try:
        return uuid.UUID(ticket_id_str)
    except ValueError:
        pass

    match = _TICKET_KEY_GLOBAL_RE.match(ticket_id_str.strip())
    if not match:
        raise ValueError(f"Nieprawidlowy identyfikator ticketa: '{ticket_id_str}'. Podaj UUID lub klucz (np. MON-12)")

    code = match.group(1).upper()
    number = int(match.group(2))

    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket.id)
            .join(Project, Ticket.project_id == Project.id)
            .join(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == user_id))
            .where(
                func.upper(Project.code) == code,
                Ticket.number == number,
                Project.is_active.is_(True),
            )
        )
        ticket_uuid = result.scalar_one_or_none()
        if ticket_uuid is None:
            raise ValueError("Ticket nie istnieje")
        return ticket_uuid


@mcp.tool(annotations=WRITE)
async def schedule_ticket(
    ctx: Context[Any, Any],
    ticket_id: str,
    scheduled_date: str,
    position: int = 0,
    notes: str | None = None,
) -> dict[str, Any]:
    """Zaplanuj ticket na konkretny dzien w planie pracy.

    ticket_id: UUID ticketa lub klucz (np. MON-12).
    scheduled_date: data w formacie YYYY-MM-DD.
    position: kolejnosc w danym dniu (0 = pierwszy, domyslnie 0).
    notes: opcjonalna notatka do wpisu.

    Zwraca utworzony wpis planu pracy z kluczem i titulem ticketa.
    Jesli ticket byl juz zaplanowany na ten dzien, zwraca blad z entry_id istniejacego wpisu.
    """
    user = await _auth(ctx)

    try:
        date_obj = date.fromisoformat(scheduled_date)
    except ValueError:
        raise ValueError("Data musi byc w formacie YYYY-MM-DD") from None

    ticket_uuid = await _resolve_ticket_globally(ticket_id, user.id)

    async with async_session_factory() as db:
        result = await work_plan_service.schedule(db, user.id, ticket_uuid, date_obj, position, notes)

    if isinstance(result, str):
        # Sprawdz czy to konflikt unique — pobierz istniejacy entry
        if "juz zaplanowany" in result:
            async with async_session_factory() as db:
                existing = await db.execute(
                    select(WorkPlanEntry)
                    .options(selectinload(WorkPlanEntry.ticket).selectinload(Ticket.project))
                    .where(
                        WorkPlanEntry.user_id == user.id,
                        WorkPlanEntry.ticket_id == ticket_uuid,
                        WorkPlanEntry.scheduled_date == date_obj,
                    )
                )
                existing_entry = existing.scalar_one_or_none()
            if existing_entry is not None:
                raise ValueError(f"Ticket juz zaplanowany na ten dzien. entry_id: {existing_entry.id}")
        raise ValueError(result)

    return WorkPlanEntryResponse.from_entry(result).model_dump(mode="json")


@mcp.tool(annotations=WRITE)
async def update_work_plan_entry(
    ctx: Context[Any, Any],
    entry_id: str,
    scheduled_date: str | None = None,
    position: int | None = None,
    notes: str = "__UNSET__",
) -> dict[str, Any]:
    """Zaktualizuj wpis planu pracy (PATCH).

    entry_id: UUID wpisu.
    scheduled_date: nowa data YYYY-MM-DD (opcjonalnie).
    position: nowa kolejnosc w dniu (opcjonalnie).
    notes: notatka. Pomin ten parametr zeby nie zmieniac notatki,
           podaj null/None zeby wyczysc, podaj string zeby ustawic.

    Wartosc domyslna "__UNSET__" oznacza brak zmiany pola notes.
    """
    user = await _auth(ctx)

    try:
        entry_uuid = uuid.UUID(entry_id)
    except ValueError:
        raise ValueError(f"Nieprawidlowy UUID wpisu: '{entry_id}'") from None

    parsed_date: date | None = None
    if scheduled_date is not None:
        try:
            parsed_date = date.fromisoformat(scheduled_date)
        except ValueError:
            raise ValueError("Data musi byc w formacie YYYY-MM-DD") from None

    # Mapuj string sentinel na Python sentinel
    if notes == "__UNSET__":
        notes_arg: Any = _WORK_PLAN_UNSET
    else:
        notes_arg = notes  # None lub string

    async with async_session_factory() as db:
        result = await work_plan_service.update(
            db,
            user.id,
            entry_uuid,
            scheduled_date=parsed_date,
            position=position,
            notes=notes_arg,
        )

    if isinstance(result, str):
        raise ValueError(result)

    return WorkPlanEntryResponse.from_entry(result).model_dump(mode="json")


@mcp.tool(annotations=DESTRUCTIVE)
async def delete_work_plan_entry(
    ctx: Context[Any, Any],
    entry_id: str,
) -> dict[str, Any]:
    """Usun wpis planu pracy.

    entry_id: UUID wpisu do usuniecia.
    Zwraca potwierdzenie usuniecia lub blad (np. brak wpisu, brak dostepu).
    """
    user = await _auth(ctx)

    try:
        entry_uuid = uuid.UUID(entry_id)
    except ValueError:
        raise ValueError(f"Nieprawidlowy UUID wpisu: '{entry_id}'") from None

    async with async_session_factory() as db:
        error = await work_plan_service.unschedule(db, user.id, entry_uuid)

    if error is not None:
        raise ValueError(error)

    return {"success": True, "message": "Wpis usuniety"}


@mcp.tool(annotations=READ_ONLY)
async def list_work_plan(
    ctx: Context[Any, Any],
    start_date: str,
    end_date: str,
    project_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Lista wpisow planu pracy w podanym zakresie dat.

    start_date, end_date: daty w formacie YYYY-MM-DD.
    Maksymalny zakres: 90 dni.
    project_slug: opcjonalne filtrowanie po projekcie.

    Zwraca liste wpisow posortowanych po dacie i pozycji.
    """
    user = await _auth(ctx)

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        raise ValueError("Daty musza byc w formacie YYYY-MM-DD") from None

    if (end - start).days > 90:
        raise ValueError("Zakres nie moze przekraczac 90 dni")

    project_ids: list[uuid.UUID] | None = None
    if project_slug is not None:
        async with async_session_factory() as db:
            proj_result = await db.execute(
                select(Project.id)
                .join(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == user.id))
                .where(
                    Project.slug == project_slug,
                    Project.is_active.is_(True),
                )
            )
            project_id = proj_result.scalar_one_or_none()
        if project_id is None:
            raise ValueError(f"Projekt '{project_slug}' nie istnieje")
        project_ids = [project_id]

    async with async_session_factory() as db:
        entries = await work_plan_service.list_for_user_range(db, user.id, start, end, project_ids=project_ids)

    return [WorkPlanEntryResponse.from_entry(e).model_dump(mode="json") for e in entries]


@mcp.tool(annotations=READ_ONLY)
async def get_today_tasks(
    ctx: Context[Any, Any],
    project_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Zwroc zadania zaplanowane na dzisiaj.

    project_slug: opcjonalne — jesli podany, zwraca tylko zadania z tego projektu.
    Bez parametru zwraca zadania ze wszystkich projektow uzytkownika.

    Zwraca liste wpisow posortowanych po pozycji.
    """
    user = await _auth(ctx)

    if project_slug is not None:
        async with async_session_factory() as db:
            proj_result = await db.execute(
                select(Project.id)
                .join(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == user.id))
                .where(
                    Project.slug == project_slug,
                    Project.is_active.is_(True),
                )
            )
            project_id = proj_result.scalar_one_or_none()
        if project_id is None:
            raise ValueError(f"Projekt '{project_slug}' nie istnieje")

        async with async_session_factory() as db:
            entries = await work_plan_service.today_for_user_in_project(db, user.id, project_id)
    else:
        async with async_session_factory() as db:
            entries = await work_plan_service.today_for_user(db, user.id)

    return [WorkPlanEntryResponse.from_entry(e).model_dump(mode="json") for e in entries]


@mcp.tool(annotations=READ_ONLY)
async def get_ticket_schedule(
    ctx: Context[Any, Any],
    ticket_id: str,
) -> list[dict[str, Any]]:
    """Pobierz harmonogram danego ticketa — wszystkie zaplanowane dni dla biezacego uzytkownika.

    ticket_id: UUID ticketa lub klucz (np. MON-12).

    Zwraca liste wpisow posortowanych po dacie i pozycji.
    """
    user = await _auth(ctx)

    ticket_uuid = await _resolve_ticket_globally(ticket_id, user.id)

    async with async_session_factory() as db:
        entries = await work_plan_service.schedule_for_ticket(db, user.id, ticket_uuid)

    return [WorkPlanEntryResponse.from_entry(e).model_dump(mode="json") for e in entries]


# ---------------------------------------------------------------------------
# Pipelines -- toole zapisu
# ---------------------------------------------------------------------------


@mcp.tool(annotations=WRITE)
async def create_pipeline(
    ctx: Context[Any, Any],
    project_slug: str,
    pipeline_type: str,
    ticket_id: str | None = None,
    sprint_id: str | None = None,
    branch: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Utwórz nowy pipeline w projekcie.

    pipeline_type: ticket_work | sprint_close
    ticket_id: UUID lub klucz ticketa (np. MON-12) — wymagany dla ticket_work, zabroniony dla sprint_close
    sprint_id: UUID sprintu — wymagany dla sprint_close, zabroniony dla ticket_work
    branch: nazwa brancha git (opcjonalna)
    meta: dodatkowe metadane (JSONB, opcjonalne)

    Zwraca pipeline_id i listę stepów.
    Wymaga scrum:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "scrum", "write")

    if pipeline_type not in PIPELINE_TYPES:
        raise ValueError(f"Nieprawidlowy pipeline_type: {pipeline_type!r}. Dozwolone: {PIPELINE_TYPES}")

    if ticket_id is not None and sprint_id is not None:
        raise ValueError("Nie można podać jednocześnie ticket_id i sprint_id")

    resolved_sprint_id: uuid.UUID | None = None
    if sprint_id is not None:
        try:
            resolved_sprint_id = uuid.UUID(sprint_id)
        except ValueError:
            raise ValueError(f"Nieprawidlowy format sprint_id: {sprint_id!r}") from None

    async with async_session_factory() as db:
        resolved_ticket_id: uuid.UUID | None = None
        if ticket_id is not None:
            resolved_ticket_id = await tickets_service.resolve_ticket_uuid(ticket_id, project.id, db)

        # Weryfikacja przynaleznosci ticketa do projektu (galaz czystego UUID nie jest filtrowana w resolve_ticket_uuid)
        if resolved_ticket_id is not None:
            ticket_check = await db.execute(select(Ticket.id).where(Ticket.id == resolved_ticket_id, Ticket.project_id == project.id))
            if ticket_check.scalar_one_or_none() is None:
                raise ValueError(f"Ticket '{ticket_id}' nie istnieje w projekcie")

        if resolved_sprint_id is not None:
            sprint_check = await db.execute(select(Sprint.id).where(Sprint.id == resolved_sprint_id, Sprint.project_id == project.id))
            if sprint_check.scalar_one_or_none() is None:
                raise ValueError(f"Sprint '{sprint_id}' nie istnieje w projekcie")

        pipeline = await pipelines_service.create_pipeline(
            db,
            project_id=project.id,
            pipeline_type=pipeline_type,
            ticket_id=resolved_ticket_id,
            sprint_id=resolved_sprint_id,
            branch=branch,
            triggered_by=user.id,
            meta=meta,
        )

    return {
        "pipeline_id": str(pipeline.id),
        "status": pipeline.status,
        "steps": [{"id": str(s.id), "name": s.name, "position": s.position, "status": s.status} for s in pipeline.steps],
    }


@mcp.tool(annotations=WRITE)
async def create_pipeline_job(
    ctx: Context[Any, Any],
    project_slug: str,
    pipeline_id: str,
    step: str,
    name: str,
    agent_type: str,
) -> dict[str, Any]:
    """Utwórz job w podanym stepie pipeline'u.

    pipeline_id: UUID pipeline'u
    step: nazwa stepu (ticket_work: research | coding | wrap-up; sprint_close: wiki-update | wrap-up)
    name: nazwa joba (np. backend-developer)
    agent_type: typ agenta (np. backend-developer)

    Zwraca job_id i status.
    Wymaga scrum:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "scrum", "write")

    # Walidacja stepu odbywa sie w create_job (wyszukanie po pipeline_id + step_name),
    # co pokrywa oba typy pipeline (ticket_work: research/coding/wrap-up,
    # sprint_close: wiki-update/wrap-up) bez sztywnej listy dozwolonych nazw.

    try:
        pipeline_uuid = uuid.UUID(pipeline_id)
    except ValueError:
        raise ValueError(f"Nieprawidlowy format pipeline_id: {pipeline_id!r}") from None

    async with async_session_factory() as db:
        # Weryfikacja przynaleznosci pipeline'u do projektu
        result = await db.execute(
            select(Pipeline.id).where(
                Pipeline.id == pipeline_uuid,
                Pipeline.project_id == project.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError(f"Pipeline {pipeline_id!r} nie istnieje w projekcie '{project_slug}'")

        job_or_err = await pipelines_service.create_job(db, pipeline_uuid, step, name, agent_type)

    if isinstance(job_or_err, str):
        raise ValueError(job_or_err)

    return {
        "job_id": str(job_or_err.id),
        "status": job_or_err.status,
        "step": step,
    }


@mcp.tool(annotations=WRITE)
async def update_pipeline_job(
    ctx: Context[Any, Any],
    project_slug: str,
    job_id: str,
    status: str | None = None,
    score: int | None = None,
    attempt: int | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """Zaktualizuj status / score / summary joba pipeline'u.

    job_id: UUID joba
    status: created | pending | running | success | failed | skipped | canceled
    score: ocena krytyka 0-100 (opcjonalna)
    attempt: numer iteracji (opcjonalna)
    summary: krotkie podsumowanie do listy (opcjonalne)

    Przejscie na 'running' ustawia started_at. Status terminalny ustawia finished_at
    i propaguje status do stepu i pipeline'u.
    Wymaga scrum:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "scrum", "write")

    if status is not None and status not in PIPELINE_JOB_STATUSES:
        raise ValueError(f"Nieprawidlowy status: {status!r}. Dozwolone: {PIPELINE_JOB_STATUSES}")

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise ValueError(f"Nieprawidlowy format job_id: {job_id!r}") from None

    async with async_session_factory() as db:
        # Weryfikacja przynaleznosci joba do projektu przez join job->step->pipeline
        result = await db.execute(
            select(PipelineJob.id)
            .join(PipelineStep, PipelineJob.step_id == PipelineStep.id)
            .join(Pipeline, PipelineStep.pipeline_id == Pipeline.id)
            .where(
                PipelineJob.id == job_uuid,
                Pipeline.project_id == project.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError(f"Job {job_id!r} nie istnieje w projekcie '{project_slug}'")

        job_or_err = await pipelines_service.update_job_by_id(db, job_uuid, status=status, score=score, attempt=attempt, summary=summary)

    if isinstance(job_or_err, str):
        raise ValueError(job_or_err)

    return {
        "job_id": str(job_or_err.id),
        "status": job_or_err.status,
        "attempt": job_or_err.attempt,
        "score": job_or_err.score,
        "started_at": job_or_err.started_at.isoformat() if job_or_err.started_at else None,
        "finished_at": job_or_err.finished_at.isoformat() if job_or_err.finished_at else None,
    }


@mcp.tool(annotations=WRITE)
async def append_job_log(
    ctx: Context[Any, Any],
    project_slug: str,
    job_id: str,
    content: str,
) -> dict[str, Any]:
    """Dopisz treść markdown do logu joba (strona wiki).

    job_id: UUID joba
    content: treść markdown do zapisania / dopisania

    Pierwsze wywołanie tworzy stronę wiki i ustawia job.wiki_page_id.
    Kolejne dopisują do istniejącej strony separator + znacznik czasu + content.
    Strona jest wykluczona z embeddingów RAG (exclude_from_embeddings=True).
    Wymaga scrum:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "scrum", "write")

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise ValueError(f"Nieprawidlowy format job_id: {job_id!r}") from None

    async with async_session_factory() as db:
        # Weryfikacja przynaleznosci joba do projektu przez join job->step->pipeline
        result = await db.execute(
            select(PipelineJob.id)
            .join(PipelineStep, PipelineJob.step_id == PipelineStep.id)
            .join(Pipeline, PipelineStep.pipeline_id == Pipeline.id)
            .where(
                PipelineJob.id == job_uuid,
                Pipeline.project_id == project.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError(f"Job {job_id!r} nie istnieje w projekcie '{project_slug}'")

        page_or_err = await pipelines_service.append_job_log(db, job_uuid, content, user.id)

    if isinstance(page_or_err, str):
        raise ValueError(page_or_err)

    return {
        "wiki_page_id": str(page_or_err.id),
        "message": "Log zapisany",
    }


@mcp.tool(annotations=WRITE)
async def finish_pipeline(
    ctx: Context[Any, Any],
    project_slug: str,
    pipeline_id: str,
    status: str | None = None,
) -> dict[str, Any]:
    """Zamknij pipeline — ustaw status końcowy i finished_at.

    pipeline_id: UUID pipeline'u
    status: success | failed | canceled (opcjonalny — gdy brak, wyliczany ze stepów)

    Po zamknięciu opcjonalnie dopisuje wpis do dziennika wiki-log
    (tylko gdy LLM Wiki włączone dla projektu).
    Wymaga scrum:write.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "scrum", "write")

    if status is not None and status not in PIPELINE_STATUSES:
        raise ValueError(f"Nieprawidlowy status: {status!r}. Dozwolone: {PIPELINE_STATUSES}")

    try:
        pipeline_uuid = uuid.UUID(pipeline_id)
    except ValueError:
        raise ValueError(f"Nieprawidlowy format pipeline_id: {pipeline_id!r}") from None

    async with async_session_factory() as db:
        pipeline_or_err = await pipelines_service.finish_pipeline(db, pipeline_uuid, project.id, status=status)

        if isinstance(pipeline_or_err, str):
            raise ValueError(pipeline_or_err)

        # Best-effort: wpis do wiki-log jesli LLM Wiki wlaczone
        pipeline_with_project = await db.execute(select(Pipeline).options(selectinload(Pipeline.project)).where(Pipeline.id == pipeline_uuid))
        full_pipeline = pipeline_with_project.scalar_one()
        with contextlib.suppress(Exception):
            await pipelines_service.maybe_log_pipeline_to_wiki_log(db, full_pipeline, user.id)

    return {
        "pipeline_id": str(pipeline_or_err.id),
        "status": pipeline_or_err.status,
        "finished_at": pipeline_or_err.finished_at.isoformat() if pipeline_or_err.finished_at else None,
    }


# ---------------------------------------------------------------------------
# Pipelines -- toole odczytu
# ---------------------------------------------------------------------------


@mcp.tool(annotations=READ_ONLY)
async def list_pipelines(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str | None = None,
    pipeline_type: str | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """Pobierz liste pipeline'ow projektu z paginacja.

    status: created | running | success | failed | canceled (opcjonalny filtr)
    pipeline_type: ticket_work | sprint_close (opcjonalny filtr)
    page: numer strony (domyslnie 1, 20 per_page)

    Zwraca liste z informacjami o stepach (bez jobow) oraz ticket_key gdy dostepny.
    Wymaga scrum:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    per_page = 20
    async with async_session_factory() as db:
        await _require(db, user, project, "scrum", "read")
        items, total = await pipelines_service.list_pipelines(
            db, project.id, status=status, pipeline_type=pipeline_type, page=page, per_page=per_page
        )

        # Zbuduj mape ticket_key: zbierz unikalne ticket_id z wynikow
        ticket_ids = [p.ticket_id for p in items if p.ticket_id is not None]
        ticket_key_map: dict[Any, str] = {}
        if ticket_ids:
            tickets_result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id.in_(ticket_ids)))
            for t in tickets_result.scalars().all():
                ticket_key_map[t.id] = t.key

    total_pages = math.ceil(total / per_page) if total > 0 else 1
    return {
        "pipelines": [build_list_item(p, ticket_key_map.get(p.ticket_id)).model_dump(mode="json") for p in items],
        "total": total,
        "page": page,
        "total_pages": total_pages,
    }


@mcp.tool(annotations=READ_ONLY)
async def get_pipeline(
    ctx: Context[Any, Any],
    project_slug: str,
    pipeline_id: str,
) -> dict[str, Any]:
    """Pobierz szczegoly pipeline'u z pelnym drzewem stepow i jobow.

    pipeline_id: UUID pipeline'u

    Zwraca pelne dane pipeline'u lacznie z jobami, czasami i ticket_key.
    Wymaga scrum:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "scrum", "read")

    try:
        pipeline_uuid = uuid.UUID(pipeline_id)
    except ValueError:
        raise ValueError(f"Nieprawidlowy format pipeline_id: {pipeline_id!r}") from None

    async with async_session_factory() as db:
        p = await pipelines_service.get_pipeline(db, pipeline_uuid, project.id)
        if p is None:
            raise ValueError(f"Pipeline {pipeline_id!r} nie istnieje w projekcie '{project_slug}'")

        ticket_key: str | None = None
        if p.ticket_id is not None:
            ticket_result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id == p.ticket_id))
            ticket = ticket_result.scalar_one_or_none()
            if ticket is not None:
                ticket_key = ticket.key

    return build_pipeline_response(p, ticket_key).model_dump(mode="json")


@mcp.tool(annotations=READ_ONLY)
async def get_pipeline_job_log(
    ctx: Context[Any, Any],
    project_slug: str,
    job_id: str,
) -> dict[str, Any]:
    """Pobierz log joba pipeline'u jako markdown.

    job_id: UUID joba

    Zwraca zawartosc strony wiki loga lub pusty content gdy log nie istnieje.
    Wymaga scrum:read.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "scrum", "read")

    try:
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        raise ValueError(f"Nieprawidlowy format job_id: {job_id!r}") from None

    async with async_session_factory() as db:
        # Weryfikacja przynaleznosci joba do projektu przez join job->step->pipeline
        result = await db.execute(
            select(PipelineJob)
            .join(PipelineStep, PipelineJob.step_id == PipelineStep.id)
            .join(Pipeline, PipelineStep.pipeline_id == Pipeline.id)
            .where(
                PipelineJob.id == job_uuid,
                Pipeline.project_id == project.id,
            )
        )
        job = result.scalar_one_or_none()
        if job is None:
            raise ValueError(f"Job {job_id!r} nie istnieje w projekcie '{project_slug}'")

        if job.wiki_page_id is None:
            return {"job_id": job_id, "wiki_page_id": None, "content": "", "message": "Brak loga"}

        page_result = await db.execute(select(WikiPage).where(WikiPage.id == job.wiki_page_id))
        page = page_result.scalar_one_or_none()
        if page is None:
            return {"job_id": job_id, "wiki_page_id": None, "content": "", "message": "Strona wiki nie istnieje"}

        content = get_page_content(page)

    return {
        "job_id": job_id,
        "wiki_page_id": str(job.wiki_page_id),
        "content": content,
    }


@mcp.tool(annotations=DESTRUCTIVE)
async def clean_pipeline_logs(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
) -> dict[str, int]:
    """Usuń strony wiki logów jobów pipeline'ów ticket_work powiązanych ze sprintem.

    project_slug: slug projektu
    sprint_id: UUID sprintu

    Zwraca liczbę usuniętych stron wiki ({"deleted": N}).
    Wymaga wiki:delete.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        await _require(db, user, project, "wiki", "delete")

    try:
        sprint_uuid = uuid.UUID(sprint_id)
    except ValueError:
        raise ValueError(f"Nieprawidlowy format sprint_id: {sprint_id!r}") from None

    async with async_session_factory() as db:
        sprint_check = await db.execute(select(Sprint.id).where(Sprint.id == sprint_uuid, Sprint.project_id == project.id))
        if sprint_check.scalar_one_or_none() is None:
            raise ValueError(f"Sprint '{sprint_id}' nie istnieje w projekcie '{project_slug}'")

        deleted = await pipelines_service.clean_pipeline_logs_for_sprint(db, project.id, sprint_uuid)

    return {"deleted": deleted}
