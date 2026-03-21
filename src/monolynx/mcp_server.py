"""MCP Server -- narzedzia do zarzadzania Monolynx z Claude Code."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from monolynx.config import settings as app_settings
from monolynx.constants import (
    ACTIVITY_ENTITY_TYPES,
    BOARD_STATUSES,
    GRAPH_EDGE_TYPES,
    GRAPH_NODE_TYPES,
    INTERVAL_UNITS,
    INVITATION_DAYS,
    LABEL_COLOR_PALETTE,
    PRIORITIES,
    TICKET_STATUSES,
)
from monolynx.dashboard.helpers import SLUG_PATTERN
from monolynx.dashboard.monitoring import _is_url_safe
from monolynx.dashboard.projects import CODE_PATTERN
from monolynx.database import async_session_factory
from monolynx.models.event import Event
from monolynx.models.heartbeat import Heartbeat
from monolynx.models.issue import Issue
from monolynx.models.label import Label, TicketLabel
from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_attachment import TicketAttachment
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.user import User
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_file import WikiFile
from monolynx.models.wiki_page import WikiPage
from monolynx.services import graph as graph_service
from monolynx.services.activity import get_activity_log as svc_get_activity_log
from monolynx.services.burndown import get_burndown_data as svc_get_burndown_data
from monolynx.services.email import send_invitation_email
from monolynx.services.heartbeat import create_heartbeat as svc_create_heartbeat
from monolynx.services.heartbeat import delete_heartbeat as svc_delete_heartbeat
from monolynx.services.heartbeat import get_heartbeat_status
from monolynx.services.heartbeat import update_heartbeat as svc_update_heartbeat
from monolynx.services.mcp_auth import verify_mcp_token
from monolynx.services.minio_client import get_attachment as minio_get_attachment
from monolynx.services.minio_client import upload_attachment as minio_upload_attachment
from monolynx.services.minio_client import upload_object as minio_upload_object
from monolynx.services.sprint import complete_sprint as svc_complete_sprint
from monolynx.services.sprint import start_sprint as svc_start_sprint
from monolynx.services.ticket_numbering import get_next_ticket_number
from monolynx.services.time_tracking import add_time_entry
from monolynx.services.wiki import (
    create_wiki_page as svc_create_wiki_page,
)
from monolynx.services.wiki import (
    delete_wiki_page as svc_delete_wiki_page,
)
from monolynx.services.wiki import (
    get_page_content,
    get_page_tree,
)
from monolynx.services.wiki import (
    update_wiki_page as svc_update_wiki_page,
)

logger = logging.getLogger("monolynx.mcp")


def _build_allowed_hosts() -> list[str]:
    """Zbuduj liste dozwolonych hostow z MCP_ALLOWED_HOSTS (env/.env)."""
    hosts = ["localhost", "localhost:*", "127.0.0.1", "127.0.0.1:*"]
    if app_settings.MCP_ALLOWED_HOSTS:
        for h in app_settings.MCP_ALLOWED_HOSTS.split(","):
            h = h.strip()
            if h:
                hosts.append(h)
    return hosts


mcp = FastMCP(
    "Monolynx",
    instructions=(
        "Serwer MCP platformy Monolynx. "
        "Moduly: Scrum (tickety, sprinty, tablica Kanban), "
        "500ki (error tracking — issues, eventy), "
        "Monitoring (URL health checks, uptime), "
        "Wiki (strony markdown z hierarchia). "
        "Wymaga tokenu API (Bearer) w naglowku Authorization."
    ),
    streamable_http_path="/",
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_build_allowed_hosts(),
    ),
)


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
    """Zwaliduj token (OAuth + legacy) i zwroc uzytkownika."""
    # Sprobuj najpierw OAuth access token
    try:
        from monolynx.services.oauth import verify_oauth_access_token

        async with async_session_factory() as db:
            user = await verify_oauth_access_token(raw_token, db)
        if user is not None:
            return user
    except Exception:
        logger.debug("OAuth verification skipped (tables may not exist)")

    # Fallback na legacy osk_* token
    async with async_session_factory() as db:
        user = await verify_mcp_token(raw_token, db)
    if user is None:
        raise ValueError("Nieprawidlowy lub nieaktywny token API")
    return user


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


def _format_graph_dsl(data: dict[str, Any]) -> str:
    """Konwertuj dict z nodes/edges na kompaktowy Arrow DSL dla LLM.

    Format bez depth_map (backwards-compatible):
        [Type] name (key=val,key2=val2)
        source_name --EDGE_TYPE--> target_name

    Format z depth_map (grupowanie po depth rings i typie relacji):
        --- Depth 1 ---
        [Class] Contact (path=contacts/models.py,line=13)

        --- Depth 2 ---
        [Method] Contact.clean (path=contacts/models.py,line=168)

        === INHERITS ===
        Contact --INHERITS--> BaseInfoModel

        === CONTAINS ===
        contacts/models.py --CONTAINS--> Contact
    """
    nodes: list[dict[str, Any]] = data.get("nodes", [])
    edges: list[dict[str, Any]] = data.get("edges", [])
    depth_map: dict[str, int] | None = data.get("depth_map")

    node_map: dict[str, dict[str, Any]] = {n["id"]: n for n in nodes}

    lines: list[str] = [f"{len(nodes)} nodes, {len(edges)} edges", ""]

    def _node_line(n: dict[str, Any]) -> str:
        ntype = n.get("type", "Unknown")
        meta_parts: list[str] = []
        if n.get("file_path"):
            meta_parts.append(f"path={n['file_path']}")
        if n.get("line_number"):
            meta_parts.append(f"line={n['line_number']}")
        md = n.get("metadata") or {}
        for k, v in md.items():
            meta_parts.append(f"{k}={v}")
        meta_str = f" ({','.join(meta_parts)})" if meta_parts else ""
        return f"[{ntype}] {n['name']}{meta_str}"

    if depth_map:
        # Grupuj nodes po depth level
        by_depth: dict[int, list[dict[str, Any]]] = {}
        for n in nodes:
            d = depth_map.get(n["id"], 0)
            by_depth.setdefault(d, []).append(n)

        for depth_level in sorted(by_depth.keys()):
            lines.append(f"--- Depth {depth_level} ---")
            for n in by_depth[depth_level]:
                lines.append(_node_line(n))
            lines.append("")
    else:
        # Backwards-compatible: grupuj po typie node'a
        by_type: dict[str, list[dict[str, Any]]] = {}
        for n in nodes:
            by_type.setdefault(n.get("type", "Unknown"), []).append(n)

        for _ntype, nlist in by_type.items():
            for n in nlist:
                lines.append(_node_line(n))
        lines.append("")

    if edges:
        if depth_map:
            # Grupuj edges po typie relacji
            by_edge_type: dict[str, list[dict[str, Any]]] = {}
            for e in edges:
                by_edge_type.setdefault(e["type"], []).append(e)

            for etype in sorted(by_edge_type.keys()):
                lines.append(f"=== {etype} ===")
                for e in by_edge_type[etype]:
                    src = node_map.get(e["source_id"], {}).get("name", e["source_id"])
                    tgt = node_map.get(e["target_id"], {}).get("name", e["target_id"])
                    lines.append(f"{src} --{etype}--> {tgt}")
                lines.append("")
        else:
            # Backwards-compatible: płaska lista edges
            for e in edges:
                src = node_map.get(e["source_id"], {}).get("name", e["source_id"])
                tgt = node_map.get(e["target_id"], {}).get("name", e["target_id"])
                lines.append(f"{src} --{e['type']}--> {tgt}")

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

        Key      | Title                    | Status      | Pri    | Assignee     | Sprint   | SP | Due        | Labels
        MON-20   | Fix login timeout        | in_progress | high   | jan@ex.com   | Sprint 5 | 3  | 2026-03-20 | backend, urgent
        MON-19   | Dodaj eksport PDF        | todo        | medium | --           | Sprint 5 | 5  | --         |
    """
    header_line = f"{total} tickets (page {page}/{total_pages})"

    if not tickets:
        return f"{header_line}\n\n(brak ticketow)"

    col_header = (
        "Key      | Title                                    | Status      | Pri    "
        "| Assignee              | Sprint            | SP | Due        | Labels"
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
        rows.append(f"{key:<8} | {title:<40} | {status:<11} | {priority:<6} | {assignee:<21} | {sprint:<17} | {sp:<2} | {due:<10} | {labels}")

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


_TICKET_KEY_RE = re.compile(r"^([A-Za-z]{1,10})-(\d+)$")


async def _resolve_ticket_uuid(ticket_id: str, project_id: uuid.UUID) -> uuid.UUID:
    """Zamien ticket_id (UUID lub klucz jak MNX-12) na UUID ticketa."""
    # Sprobuj UUID
    try:
        return uuid.UUID(ticket_id)
    except ValueError:
        pass

    # Sprobuj klucz (np. MNX-12)
    match = _TICKET_KEY_RE.match(ticket_id.strip())
    if not match:
        raise ValueError(f"Nieprawidlowy identyfikator ticketa: '{ticket_id}'. Podaj UUID lub klucz (np. MNX-12)")

    number = int(match.group(2))
    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket.id).where(
                Ticket.project_id == project_id,
                Ticket.number == number,
            )
        )
        ticket_uuid = result.scalar_one_or_none()
        if ticket_uuid is None:
            raise ValueError(f"Ticket '{ticket_id}' nie istnieje w projekcie")
        return ticket_uuid


# --- Projekty ---


@mcp.tool()
async def list_projects(ctx: Context[Any, Any]) -> list[dict[str, Any]]:
    """Lista projektow, do ktorych uzytkownik jest przypisany."""
    user = await _auth(ctx)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Project, ProjectMember.role)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                ProjectMember.user_id == user.id,
                Project.is_active.is_(True),
            )
            .order_by(Project.name)
        )
        rows = result.all()

    return [
        {
            "name": project.name,
            "slug": project.slug,
            "role": role,
            "created_at": project.created_at.isoformat(),
        }
        for project, role in rows
    ]


@mcp.tool()
async def get_project(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Pelne szczegoly projektu: nazwa, opis, czlonkowie, statystyki, aktywny sprint."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        # Rola uzytkownika
        role_result = await db.execute(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        role = role_result.scalar_one_or_none()
        if role is None:
            raise ValueError("Uzytkownik nie jest czlonkiem projektu")

        # Liczba czlonkow
        members_count = (
            await db.execute(
                select(func.count(ProjectMember.id)).where(
                    ProjectMember.project_id == project.id,
                )
            )
        ).scalar() or 0

        # Liczba ticketow
        tickets_count = (
            await db.execute(
                select(func.count(Ticket.id)).where(
                    Ticket.project_id == project.id,
                )
            )
        ).scalar() or 0

        # Aktywny sprint
        sprint_result = await db.execute(
            select(Sprint).where(
                Sprint.project_id == project.id,
                Sprint.status == "active",
            )
        )
        sprint = sprint_result.scalar_one_or_none()

        active_sprint = None
        if sprint:
            ticket_stats = await db.execute(select(Ticket.status, func.count(Ticket.id)).where(Ticket.sprint_id == sprint.id).group_by(Ticket.status))
            status_counts: dict[str, int] = {row[0]: row[1] for row in ticket_stats.all()}
            active_sprint = {
                "name": sprint.name,
                "goal": sprint.goal,
                "tickets_by_status": status_counts,
            }

        # Liczba monitorow
        monitors_count = (
            await db.execute(
                select(func.count(Monitor.id)).where(
                    Monitor.project_id == project.id,
                )
            )
        ).scalar() or 0

        # Liczba heartbeatow
        heartbeats_count = (
            await db.execute(
                select(func.count(Heartbeat.id)).where(
                    Heartbeat.project_id == project.id,
                )
            )
        ).scalar() or 0

    return {
        "id": str(project.id),
        "name": project.name,
        "slug": project.slug,
        "description": project.description,
        "created_at": project.created_at.isoformat(),
        "role": role,
        "members_count": members_count,
        "tickets_count": tickets_count,
        "active_sprint": active_sprint,
        "monitors_count": monitors_count,
        "heartbeats_count": heartbeats_count,
    }


@mcp.tool()
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
        # Sprawdz role - tylko owner/admin moze edytowac projekt
        result = await db.execute(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        role = result.scalar_one_or_none()
        if role not in ("owner", "admin"):
            raise ValueError("Tylko owner lub admin moze edytowac projekt")

        # Walidacja new_slug
        if new_slug is not None:
            if len(new_slug) > 255:
                raise ValueError("Slug moze miec maksymalnie 255 znakow")
            if not SLUG_PATTERN.match(new_slug):
                raise ValueError("Slug moze zawierac tylko male litery, cyfry i myslniki")
            # Sprawdz unikalnosc
            existing = await db.execute(select(Project.id).where(Project.slug == new_slug, Project.id != project.id))
            if existing.scalar_one_or_none() is not None:
                raise ValueError(f"Slug '{new_slug}' jest juz zajety")

        # Pobierz projekt do edycji w tej sesji
        proj_result = await db.execute(select(Project).where(Project.id == project.id))
        proj = proj_result.scalar_one()

        if name is not None:
            stripped_name = name.strip()
            if not stripped_name:
                raise ValueError("Nazwa nie moze byc pusta")
            if len(stripped_name) > 255:
                raise ValueError("Nazwa moze miec maksymalnie 255 znakow")
            proj.name = stripped_name
        if description is not None:
            stripped_desc = description.strip()
            if len(stripped_desc) > 1000:
                raise ValueError("Opis moze miec maksymalnie 1000 znakow")
            proj.description = stripped_desc if stripped_desc else None
        if new_slug is not None:
            proj.slug = new_slug

        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise ValueError("Projekt z takim slugiem juz istnieje") from None
        await db.refresh(proj)

        return {
            "id": str(proj.id),
            "name": proj.name,
            "slug": proj.slug,
            "code": proj.code,
            "description": proj.description,
            "created_at": proj.created_at.isoformat(),
        }


@mcp.tool()
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
        # Sprawdz role - tylko owner moze usunac projekt
        result = await db.execute(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        role = result.scalar_one_or_none()
        if role not in ("owner",):
            raise ValueError("Tylko owner moze usunac projekt")

        # Pobierz projekt do edycji w tej sesji
        proj_result = await db.execute(select(Project).where(Project.id == project.id))
        proj = proj_result.scalar_one()

        proj.is_active = False
        await db.commit()

        deleted_at = datetime.now(UTC).isoformat()

    return {"message": f"Projekt '{proj.name}' usuniety", "deleted_at": deleted_at}


@mcp.tool()
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

    Zwraca: { message, user_email, role } oraz opcjonalnie
    { invitation_id, expires_at } jesli wyslano zaproszenie emailowe.
    """
    # Walidacja roli
    if role not in ("member", "admin"):
        raise ValueError("Rola musi byc 'member' lub 'admin' (owner nie moze byc przyznany przez zaproszenie)")

    # Sprawdz uprawnienia wywołujacego
    _calling_user, calling_member, project = await _get_user_member_and_project(ctx, project_slug)
    if calling_member.role not in ("owner", "admin"):
        raise ValueError("Tylko owner lub admin moze zapraszac czlonkow do projektu")

    email = email.strip().lower()
    if not email:
        raise ValueError("Email nie moze byc pusty")
    if "@" not in email or len(email) > 255:
        raise ValueError("Nieprawidlowy format adresu email")

    async with async_session_factory() as db:
        # Sprawdz czy user juz istnieje w systemie (takze nieaktywny)
        any_user_result = await db.execute(select(User).where(User.email == email))
        any_user = any_user_result.scalar_one_or_none()
        if any_user is not None and not any_user.is_active:
            raise ValueError(f"Uzytkownik {email} jest dezaktywowany")

        existing_user = any_user if (any_user is not None and any_user.is_active) else None

        # Sprawdz czy uzytkownik jest juz czlonkiem projektu
        if existing_user is not None:
            existing_member_result = await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.user_id == existing_user.id,
                )
            )
            if existing_member_result.scalar_one_or_none() is not None:
                raise ValueError(f"Uzytkownik '{email}' jest juz czlonkiem projektu '{project_slug}'")

            # Dodaj istniejacego uzytkownika bezposrednio jako czlonka
            new_member = ProjectMember(
                project_id=project.id,
                user_id=existing_user.id,
                role=role,
            )
            db.add(new_member)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                raise ValueError(f"Uzytkownik '{email}' jest juz czlonkiem projektu '{project_slug}'") from None

            return {
                "message": f"Uzytkownik '{email}' zostal dodany do projektu '{project_slug}' jako {role}.",
                "user_email": email,
                "role": role,
            }

        # Nowy uzytkownik — utworz konto z tokenem zaproszenia
        invitation_token = uuid.uuid4()
        expires_at = datetime.now(UTC) + timedelta(days=INVITATION_DAYS)

        new_user = User(
            email=email,
            first_name="",
            last_name="",
            password_hash=None,
            invitation_token=invitation_token,
            invitation_expires_at=expires_at,
        )
        db.add(new_user)

        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise ValueError(f"Uzytkownik z emailem '{email}' juz istnieje w systemie") from None

        new_member = ProjectMember(
            project_id=project.id,
            user_id=new_user.id,
            role=role,
        )
        db.add(new_member)
        await db.commit()

        # Wysylaj email z zaproszeniem
        send_invitation_email(email, "", invitation_token)

        return {
            "message": (
                f"Zaproszenie wyslane na adres '{email}'. Uzytkownik zostanie dodany do projektu '{project_slug}' jako {role} po aktywacji konta."
            ),
            "user_email": email,
            "role": role,
            "invitation_id": str(invitation_token),
            "expires_at": expires_at.isoformat(),
        }


def _slugify(name: str) -> str:
    """Zamien nazwe projektu na slug (male litery, myslniki zamiast spacji/znakow)."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    slug = slug.strip("-")
    return slug or "projekt"


def _code_from_slug(slug: str) -> str:
    """Wygeneruj kod projektu ze sluga (np. 'anna-miastkowska' -> 'ANNAM')."""
    parts = slug.split("-")
    code = slug[:5].upper() if len(parts) == 1 else "".join(p[0] for p in parts if p)[:10].upper()
    if not code:
        code = "PROJ"
    # Upewnij sie ze kod zaczyna sie od litery
    if not code[0].isalpha():
        code = "P" + code[:9]
    # Upewnij sie ze kod ma minimum 2 znaki
    if len(code) < 2:
        code = code * 2
    return code


@mcp.tool()
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

    # Walidacja nazwy
    name = name.strip()
    if not name:
        raise ValueError("Nazwa projektu nie moze byc pusta.")
    if len(name) > 255:
        raise ValueError("Nazwa projektu nie moze przekraczac 255 znakow.")
    if description is not None and len(description) > 1000:
        raise ValueError("Opis projektu nie moze przekraczac 1000 znakow.")

    # Przygotuj slug
    slug = slug.strip().lower() if slug else _slugify(name)

    if not SLUG_PATTERN.match(slug):
        raise ValueError(f"Nieprawidlowy slug '{slug}'. Slug moze zawierac tylko male litery, cyfry i myslniki (np. 'anna-miastkowska').")

    # Przygotuj kod projektu
    code = code.strip().upper() if code else _code_from_slug(slug)

    if not CODE_PATTERN.match(code):
        raise ValueError(f"Nieprawidlowy kod '{code}'. Kod musi miec 2-10 znakow: wielkie litery i cyfry, zaczynac sie od litery (np. PIM, PROJ2).")

    async with async_session_factory() as db:
        # Sprawdz unikalnosc sluga
        existing = await db.execute(select(Project.id).where(Project.slug == slug))
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Projekt ze slugiem '{slug}' juz istnieje. Podaj inny slug.")

        # Sprawdz unikalnosc kodu
        existing_code = await db.execute(select(Project.id).where(Project.code == code))
        if existing_code.scalar_one_or_none() is not None:
            # Sprobuj dodac suffix numeryczny do kodu (batch query)
            candidates = [(code + str(i))[:10] for i in range(2, 100) if CODE_PATTERN.match((code + str(i))[:10])]
            taken_result = await db.execute(select(Project.code).where(Project.code.in_(candidates)))
            taken_codes = {row[0] for row in taken_result.all()}
            code = next((c for c in candidates if c not in taken_codes), None)
            if code is None:
                raise ValueError(f"Kod '{code}' jest juz zajety i nie udalo sie znalezc wolnego wariantu. Podaj inny kod.")

        project = Project(
            name=name,
            slug=slug,
            code=code,
            description=description,
            api_key=secrets.token_urlsafe(32),
        )
        db.add(project)
        try:
            await db.flush()
        except IntegrityError as e:
            await db.rollback()
            raise ValueError("Projekt z takim slugiem lub kodem juz istnieje.") from e

        member = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role="owner",
        )
        db.add(member)
        await db.commit()
        await db.refresh(project)

        return {
            "id": str(project.id),
            "name": project.name,
            "slug": project.slug,
            "description": project.description,
            "code": project.code,
            "created_at": project.created_at.isoformat(),
            "message": f"Projekt '{project.name}' zostal utworzony pomyslnie.",
        }


@mcp.tool()
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
        result = await db.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project.id)
            .order_by(
                case(
                    (ProjectMember.role == "owner", 1),
                    (ProjectMember.role == "admin", 2),
                    else_=3,
                ),
                (User.first_name + " " + User.last_name),
            )
        )
        rows = result.all()

    return [
        {
            "user_id": str(member.user_id),
            "name": f"{user.first_name} {user.last_name}".strip() or user.email,
            "email": user.email,
            "role": member.role,
            "joined_at": member.created_at.isoformat(),
        }
        for member, user in rows
    ]


@mcp.tool()
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
    _calling_user, calling_member, project = await _get_user_member_and_project(ctx, project_slug)
    if calling_member.role not in ("owner", "admin"):
        raise ValueError("Tylko owner lub admin moze usuwac czlonkow z projektu")

    email = email.strip().lower()
    if not email:
        raise ValueError("Email nie moze byc pusty")
    if "@" not in email or len(email) > 255:
        raise ValueError("Nieprawidlowy format adresu email")

    async with async_session_factory() as db:
        # Znajdz uzytkownika po emailu
        user_result = await db.execute(select(User).where(User.email == email))
        target_user = user_result.scalar_one_or_none()
        if target_user is None:
            raise ValueError(f"Uzytkownik z emailem '{email}' nie istnieje w systemie")

        # Znajdz membership
        member_result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == target_user.id,
            )
        )
        member = member_result.scalar_one_or_none()
        if member is None:
            raise ValueError(f"Uzytkownik '{email}' nie jest czlonkiem projektu '{project_slug}'")

        # Nie mozna usunac ownera
        if member.role == "owner":
            raise ValueError("Nie mozna usunac ownera projektu. Zmien najpierw role ownera na innego czlonka.")

        await db.delete(member)
        await db.commit()

    return {"message": f"Uzytkownik '{email}' zostal usuniety z projektu '{project_slug}'"}


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


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)
    per_page = 20

    async with async_session_factory() as db:
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


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

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

        events_result = await db.execute(select(Event).where(Event.issue_id == issue.id).order_by(Event.timestamp.desc()).limit(5))
        events = events_result.scalars().all()

    return _format_issue(issue, list(events))


@mcp.tool()
async def update_issue_status(
    ctx: Context[Any, Any],
    project_slug: str,
    issue_id: str,
    status: str,
) -> dict[str, Any]:
    """Zmien status bledu (unresolved/resolved)."""
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
async def list_monitors(
    ctx: Context[Any, Any],
    project_slug: str,
) -> str:
    """Lista monitorow URL z aktualnym statusem i uptime 24h.

    Zwraca kompaktowa tabele w formacie:
        ID | Name | URL | Active | Interval | Uptime 24h | Last Check
    gdzie Last Check = 'OK/FAIL status_code response_time timestamp' lub '--' jesli brak.
    Active: yes/no. Interval: '5 min', '1 hr', '1 day'.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

    async with async_session_factory() as db:
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


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

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


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
async def delete_monitor(
    ctx: Context[Any, Any],
    project_slug: str,
    monitor_id: str,
) -> dict[str, Any]:
    """Usuwa monitor URL z projektu (wraz z historia checkow). Wymaga roli owner lub admin."""
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        # Sprawdz role - tylko owner/admin moze usuwac monitor
        role_result = await db.execute(
            select(ProjectMember.role).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        role = role_result.scalar_one_or_none()
        if role not in ("owner", "admin"):
            raise ValueError("Tylko owner lub admin moze usuwac monitor")

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


@mcp.tool()
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
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        sprint_result = await db.execute(
            select(Sprint).where(
                Sprint.project_id == project.id,
                Sprint.status == "active",
            )
        )
        sprint = sprint_result.scalar_one_or_none()
        if sprint is None:
            return "(Brak aktywnego sprintu)"

        result = await db.execute(
            select(Ticket)
            .options(selectinload(Ticket.assignee), selectinload(Ticket.labels))
            .where(Ticket.sprint_id == sprint.id)
            .order_by(Ticket.order, Ticket.created_at)
        )
        tickets = result.scalars().all()

    columns: dict[str, list[dict[str, Any]]] = {s: [] for s in BOARD_STATUSES}
    for t in tickets:
        if t.status in columns:
            columns[t.status].append(
                {
                    "key": f"{project.code}-{t.number}",
                    "title": t.title,
                    "priority": t.priority,
                    "story_points": t.story_points,
                    "assignee": t.assignee.email if t.assignee else None,
                    "labels": [lbl.name for lbl in t.labels] if t.labels else [],
                }
            )

    return _format_board(sprint, project.code, columns)


# --- Podsumowanie projektu ---


@mcp.tool()
async def get_project_summary(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Zagregowane statystyki projektu: otwarte bledy, monitory, aktywny sprint."""
    _user, project = await _get_user_and_project(ctx, project_slug)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

    async with async_session_factory() as db:
        # 500ki: nierozwiazane issues
        issues_count = (
            await db.execute(
                select(func.count(Issue.id)).where(
                    Issue.project_id == project.id,
                    Issue.status == "unresolved",
                )
            )
        ).scalar() or 0

        # Monitoring: failing + uptime 24h
        latest_check_sq = (
            select(
                MonitorCheck.monitor_id,
                func.max(MonitorCheck.checked_at).label("max_checked_at"),
            )
            .group_by(MonitorCheck.monitor_id)
            .subquery()
        )
        failing_result = await db.execute(
            select(func.count(Monitor.id))
            .join(latest_check_sq, Monitor.id == latest_check_sq.c.monitor_id)
            .join(
                MonitorCheck,
                (MonitorCheck.monitor_id == latest_check_sq.c.monitor_id) & (MonitorCheck.checked_at == latest_check_sq.c.max_checked_at),
            )
            .where(
                Monitor.project_id == project.id,
                Monitor.is_active.is_(True),
                MonitorCheck.is_success.is_(False),
            )
        )
        monitors_failing = failing_result.scalar() or 0

        uptime_result = await db.execute(
            select(
                func.count(MonitorCheck.id),
                func.count(case((MonitorCheck.is_success.is_(True), 1))),
            )
            .join(Monitor, MonitorCheck.monitor_id == Monitor.id)
            .where(
                Monitor.project_id == project.id,
                Monitor.is_active.is_(True),
                MonitorCheck.checked_at >= twenty_four_hours_ago,
            )
        )
        uptime_row = uptime_result.one()
        uptime_24h: float | None = None
        if uptime_row[0] > 0:
            uptime_24h = round((uptime_row[1] / uptime_row[0]) * 100, 1)

        # Scrum: aktywny sprint
        sprint_result = await db.execute(
            select(Sprint).where(
                Sprint.project_id == project.id,
                Sprint.status == "active",
            )
        )
        sprint = sprint_result.scalar_one_or_none()

        sprint_summary = None
        if sprint:
            ticket_stats = await db.execute(select(Ticket.status, func.count(Ticket.id)).where(Ticket.sprint_id == sprint.id).group_by(Ticket.status))
            status_counts: dict[str, int] = {row[0]: row[1] for row in ticket_stats.all()}
            sprint_summary = {
                "name": sprint.name,
                "goal": sprint.goal,
                "tickets_by_status": status_counts,
                "total_tickets": sum(status_counts.values()),
            }

        # Backlog count
        backlog_count = (
            await db.execute(
                select(func.count(Ticket.id)).where(
                    Ticket.project_id == project.id,
                    Ticket.status == "backlog",
                )
            )
        ).scalar() or 0

    return {
        "project": {"name": project.name, "slug": project.slug},
        "issues_unresolved": issues_count,
        "monitors_failing": monitors_failing,
        "uptime_24h": uptime_24h,
        "active_sprint": sprint_summary,
        "backlog_count": backlog_count,
    }


# --- Tickety ---


@mcp.tool()
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
    Naglowek kolumn + wiersze: Key | Title | Status | Pri | Assignee | Sprint | SP | Due | Labels

    Filtrowanie po statusie, priorytecie, sprincie, tekscie, dacie granicznej i etykiecie.
    due_date_before: tickety z due_date <= data (YYYY-MM-DD)
    due_date_after: tickety z due_date >= data (YYYY-MM-DD)
    overdue: True = tylko tickety po terminie (due_date < dzisiaj, status != done)
    label_id: UUID etykiety — filtruj tickety z tym labelem
    Paginacja po 20.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)
    per_page = 20

    async with async_session_factory() as db:
        conditions = [Ticket.project_id == project.id]
        if status and status in TICKET_STATUSES:
            conditions.append(Ticket.status == status)
        if priority and priority in PRIORITIES:
            conditions.append(Ticket.priority == priority)
        if sprint_id:
            conditions.append(Ticket.sprint_id == uuid.UUID(sprint_id))
        if search:
            conditions.append(Ticket.title.ilike(f"%{search}%"))
        if due_date_before:
            try:
                conditions.append(Ticket.due_date <= date.fromisoformat(due_date_before))
            except ValueError as e:
                raise ValueError(f"Nieprawidlowy format due_date_before: '{due_date_before}'. Uzyj YYYY-MM-DD") from e
        if due_date_after:
            try:
                conditions.append(Ticket.due_date >= date.fromisoformat(due_date_after))
            except ValueError as e:
                raise ValueError(f"Nieprawidlowy format due_date_after: '{due_date_after}'. Uzyj YYYY-MM-DD") from e
        if overdue:
            today = date.today()
            conditions.append(Ticket.due_date < today)
            conditions.append(Ticket.due_date.is_not(None))
            conditions.append(Ticket.status != "done")
        if label_id:
            conditions.append(Ticket.id.in_(select(TicketLabel.ticket_id).where(TicketLabel.label_id == uuid.UUID(label_id))))

        total = (await db.execute(select(func.count(Ticket.id)).where(*conditions))).scalar() or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        result = await db.execute(
            select(Ticket)
            .options(selectinload(Ticket.assignee), selectinload(Ticket.sprint), selectinload(Ticket.labels))
            .where(*conditions)
            .order_by(Ticket.order, Ticket.created_at.desc())
            .limit(per_page)
            .offset((page - 1) * per_page)
        )
        tickets = result.scalars().all()

    return _format_tickets_table(
        tickets=list(tickets),
        project_code=project.code,
        page=page,
        total_pages=total_pages,
        total=total,
    )


@mcp.tool()
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
    from sqlalchemy import or_

    _user, project = await _get_user_and_project(ctx, project_slug)
    per_page = 20

    async with async_session_factory() as db:
        conditions = [Ticket.project_id == project.id]

        if query:
            pattern = f"%{query}%"
            conditions.append(
                or_(
                    Ticket.title.ilike(pattern),
                    Ticket.description.ilike(pattern),
                )
            )

        if status and status in TICKET_STATUSES:
            conditions.append(Ticket.status == status)

        if priority and priority in PRIORITIES:
            conditions.append(Ticket.priority == priority)

        if sprint_id:
            conditions.append(Ticket.sprint_id == uuid.UUID(sprint_id))

        if due_before:
            try:
                conditions.append(Ticket.due_date <= date.fromisoformat(due_before))
            except ValueError as e:
                raise ValueError(f"Nieprawidlowy format due_before: '{due_before}'. Uzyj YYYY-MM-DD") from e

        if due_after:
            try:
                conditions.append(Ticket.due_date >= date.fromisoformat(due_after))
            except ValueError as e:
                raise ValueError(f"Nieprawidlowy format due_after: '{due_after}'. Uzyj YYYY-MM-DD") from e

        if assignee_email:
            assignee_result = await db.execute(select(User).where(User.email == assignee_email))
            assignee = assignee_result.scalar_one_or_none()
            if assignee is None:
                return {"results": [], "total": 0, "page": 1, "total_pages": 1}
            conditions.append(Ticket.assignee_id == assignee.id)

        total = (await db.execute(select(func.count(Ticket.id)).where(*conditions))).scalar() or 0
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        result = await db.execute(
            select(Ticket)
            .options(selectinload(Ticket.assignee), selectinload(Ticket.sprint))
            .where(*conditions)
            .order_by(Ticket.order, Ticket.created_at.desc())
            .limit(per_page)
            .offset((page - 1) * per_page)
        )
        tickets = result.scalars().all()

    return {
        "results": [
            {
                "id": str(t.id),
                "key": f"{project.code}-{t.number}",
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "story_points": t.story_points,
                "assignee": t.assignee.email if t.assignee else None,
                "due_date": t.due_date.isoformat() if t.due_date else None,
            }
            for t in tickets
        ],
        "total": total,
        "page": page,
        "total_pages": total_pages,
    }


@mcp.tool()
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
    _user, project = await _get_user_and_project(ctx, project_slug)
    resolved_id = await _resolve_ticket_uuid(ticket_id, project.id)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket)
            .options(
                selectinload(Ticket.assignee),
                selectinload(Ticket.sprint),
                selectinload(Ticket.comments).selectinload(TicketComment.author),
                selectinload(Ticket.labels),
                selectinload(Ticket.attachments),
            )
            .where(
                Ticket.id == resolved_id,
                Ticket.project_id == project.id,
            )
        )
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError("Ticket nie istnieje")

    return _format_ticket_detail(ticket, project.code)


@mcp.tool()
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
) -> dict[str, Any]:
    """Utworz nowy ticket w projekcie. Oznaczany jako created_via_ai=True.

    due_date: opcjonalna data graniczna w formacie YYYY-MM-DD
    label_ids: opcjonalna lista UUID etykiet do przypisania
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not title.strip():
        raise ValueError("Tytul jest wymagany")
    if priority not in PRIORITIES:
        priority = "medium"

    parsed_due_date: date | None = None
    if due_date:
        try:
            parsed_due_date = date.fromisoformat(due_date)
        except ValueError as e:
            raise ValueError(f"Nieprawidlowy format daty due_date: '{due_date}'. Uzyj YYYY-MM-DD") from e

    async with async_session_factory() as db:
        assignee_id = None
        if assignee_email:
            result = await db.execute(select(User).where(User.email == assignee_email))
            assignee = result.scalar_one_or_none()
            if assignee:
                assignee_id = assignee.id

        resolved_sprint_id = None
        if sprint_id:
            resolved_sprint_id = uuid.UUID(sprint_id)

        next_number = await get_next_ticket_number(project.id, db)

        ticket = Ticket(
            project_id=project.id,
            number=next_number,
            title=title.strip(),
            description=description.strip() if description else None,
            priority=priority,
            story_points=story_points,
            sprint_id=resolved_sprint_id,
            assignee_id=assignee_id,
            status="backlog" if resolved_sprint_id is None else "todo",
            created_via_ai=True,
            due_date=parsed_due_date,
        )
        db.add(ticket)
        await db.flush()

        if label_ids:
            label_uuids = [uuid.UUID(lid) for lid in label_ids]
            labels_result = await db.execute(
                select(Label).where(
                    Label.id.in_(label_uuids),
                    Label.project_id == project.id,
                )
            )
            labels = labels_result.scalars().all()
            for lb in labels:
                db.add(TicketLabel(ticket_id=ticket.id, label_id=lb.id))

        await db.commit()
        await db.refresh(ticket)

    return {
        "id": str(ticket.id),
        "key": f"{project.code}-{ticket.number}",
        "title": ticket.title,
        "status": ticket.status,
        "due_date": ticket.due_date.isoformat() if ticket.due_date else None,
        "created_via_ai": True,
        "message": f"Ticket '{ticket.title}' utworzony",
    }


@mcp.tool()
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
) -> dict[str, Any]:
    """Aktualizuj istniejacy ticket. Podaj tylko pola do zmiany.

    ticket_id: UUID ticketa lub klucz (np. MNX-12).
    due_date: data graniczna w formacie YYYY-MM-DD, lub pusty string aby wyczysc
    label_ids: lista UUID etykiet — zastepuje wszystkie poprzednie etykiety; [] = usun etykiety
    """
    _user, project = await _get_user_and_project(ctx, project_slug)
    resolved_id = await _resolve_ticket_uuid(ticket_id, project.id)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket).where(
                Ticket.id == resolved_id,
                Ticket.project_id == project.id,
            )
        )
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError("Ticket nie istnieje")

        if title is not None:
            title = title.strip()
            if not title:
                raise ValueError("Tytul nie moze byc pusty")
            ticket.title = title

        if description is not None:
            ticket.description = description.strip() or None

        if status is not None:
            if status not in TICKET_STATUSES:
                raise ValueError(f"Nieprawidlowy status: {status}")
            ticket.status = status

        if priority is not None:
            if priority not in PRIORITIES:
                raise ValueError(f"Nieprawidlowy priorytet: {priority}")
            ticket.priority = priority

        if story_points is not None:
            ticket.story_points = story_points

        if sprint_id is not None:
            if sprint_id == "":
                ticket.sprint_id = None
            else:
                ticket.sprint_id = uuid.UUID(sprint_id)

        if assignee_email is not None:
            if assignee_email == "":
                ticket.assignee_id = None
            else:
                result = await db.execute(select(User).where(User.email == assignee_email))
                assignee = result.scalar_one_or_none()
                if assignee:
                    ticket.assignee_id = assignee.id

        if due_date is not None:
            if due_date == "":
                ticket.due_date = None
            else:
                try:
                    ticket.due_date = date.fromisoformat(due_date)
                except ValueError as e:
                    raise ValueError(f"Nieprawidlowy format daty due_date: '{due_date}'. Uzyj YYYY-MM-DD") from e

        if label_ids is not None:
            # Usun stare etykiety
            await db.execute(delete(TicketLabel).where(TicketLabel.ticket_id == ticket.id))
            if label_ids:
                label_uuids = [uuid.UUID(lid) for lid in label_ids]
                labels_result = await db.execute(
                    select(Label).where(
                        Label.id.in_(label_uuids),
                        Label.project_id == project.id,
                    )
                )
                labels = labels_result.scalars().all()
                for lb in labels:
                    db.add(TicketLabel(ticket_id=ticket.id, label_id=lb.id))

        await db.commit()

    return {
        "id": str(ticket.id),
        "key": f"{project.code}-{ticket.number}",
        "title": ticket.title,
        "status": ticket.status,
        "due_date": ticket.due_date.isoformat() if ticket.due_date else None,
        "message": f"Ticket '{ticket.title}' zaktualizowany",
    }


@mcp.tool()
async def delete_ticket(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
) -> dict[str, Any]:
    """Usun ticket z projektu. ticket_id: UUID lub klucz (np. MNX-12)."""
    _user, project = await _get_user_and_project(ctx, project_slug)
    resolved_id = await _resolve_ticket_uuid(ticket_id, project.id)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket).where(
                Ticket.id == resolved_id,
                Ticket.project_id == project.id,
            )
        )
        ticket = result.scalar_one_or_none()
        if ticket is None:
            raise ValueError("Ticket nie istnieje")

        title = ticket.title
        await db.delete(ticket)
        await db.commit()

    return {"message": f"Ticket '{title}' usuniety"}


# --- Labele ---


@mcp.tool()
async def list_labels(
    ctx: Context[Any, Any],
    project_slug: str,
) -> list[dict[str, Any]]:
    """Lista etykiet (labels) projektu z liczba powiazanych ticketow.

    Zwraca: lista obiektow { id, name, color, tickets_count }.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(
                Label.id,
                Label.name,
                Label.color,
                func.count(TicketLabel.ticket_id).label("tickets_count"),
            )
            .outerjoin(TicketLabel, TicketLabel.label_id == Label.id)
            .where(Label.project_id == project.id)
            .group_by(Label.id, Label.name, Label.color)
            .order_by(Label.name)
        )
        rows = result.all()

    return [
        {
            "id": str(row.id),
            "name": row.name,
            "color": row.color,
            "tickets_count": row.tickets_count,
        }
        for row in rows
    ]


@mcp.tool()
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
    _user, project = await _get_user_and_project(ctx, project_slug)

    name = name.strip()
    if not name:
        raise ValueError("Nazwa etykiety nie moze byc pusta.")
    if len(name) > 100:
        raise ValueError("Nazwa etykiety nie moze przekraczac 100 znakow.")

    if color is not None:
        color = color.strip()
        if not color.startswith("#") or len(color) != 7:
            raise ValueError("Kolor musi byc w formacie hex np. '#e74c3c'.")
    else:
        color = secrets.choice(LABEL_COLOR_PALETTE)

    async with async_session_factory() as db:
        existing = await db.execute(
            select(Label.id).where(
                Label.project_id == project.id,
                Label.name == name,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Etykieta o nazwie '{name}' juz istnieje w tym projekcie.")

        label = Label(
            project_id=project.id,
            name=name,
            color=color,
        )
        db.add(label)
        await db.commit()
        await db.refresh(label)

    return {
        "id": str(label.id),
        "name": label.name,
        "color": label.color,
        "message": f"Etykieta '{label.name}' zostala utworzona.",
    }


@mcp.tool()
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
    _user, project = await _get_user_and_project(ctx, project_slug)

    if len(ticket_ids) > 100:
        raise ValueError(f"Zbyt duzo ticketow: {len(ticket_ids)}. Limit to 100 na jedno wywolanie.")

    # Walidacja statusu i priorytetu przed petla (fail fast)
    if status is not None and status not in TICKET_STATUSES:
        raise ValueError(f"Nieprawidlowy status: {status}. Dozwolone: {', '.join(TICKET_STATUSES)}")
    if priority is not None and priority not in PRIORITIES:
        raise ValueError(f"Nieprawidlowy priorytet: {priority}. Dozwolone: {', '.join(PRIORITIES)}")

    # Parsowanie due_date przed petla (fail fast)
    # clear_due_date=True oznacza ze nalezy ustawic None; parsed_due_date=date = nowa wartosc
    clear_due_date = False
    parsed_due_date: date | None = None
    if due_date is not None:
        if due_date == "":
            clear_due_date = True
        else:
            try:
                parsed_due_date = date.fromisoformat(due_date)
            except ValueError as e:
                raise ValueError(f"Nieprawidlowy format daty due_date: '{due_date}'. Uzyj YYYY-MM-DD") from e

    updated = 0
    failed: list[dict[str, str]] = []

    async with async_session_factory() as db:
        # Wyszukaj assignee raz (jesli podano email)
        resolved_assignee_id = None
        if assignee_email is not None and assignee_email != "":
            assignee_result = await db.execute(select(User).where(User.email == assignee_email))
            assignee = assignee_result.scalar_one_or_none()
            if assignee:
                resolved_assignee_id = assignee.id

        # Resolve ticket identifiers (UUID or key like MNX-12)
        valid_uuids: dict[uuid.UUID, str] = {}
        for ticket_id_str in ticket_ids:
            try:
                resolved = await _resolve_ticket_uuid(ticket_id_str, project.id)
                valid_uuids[resolved] = ticket_id_str
            except ValueError:
                failed.append({"id": ticket_id_str, "reason": "Nieprawidlowy identyfikator ticketa"})

        if valid_uuids:
            tickets_result = await db.execute(
                select(Ticket).where(
                    Ticket.id.in_(valid_uuids.keys()),
                    Ticket.project_id == project.id,
                )
            )
            found_tickets = {t.id: t for t in tickets_result.scalars().all()}

            for ticket_uuid, ticket_id_str in valid_uuids.items():
                ticket = found_tickets.get(ticket_uuid)
                if ticket is None:
                    failed.append({"id": ticket_id_str, "reason": "Ticket nie istnieje"})
                    continue

                try:
                    if status is not None:
                        ticket.status = status
                    if priority is not None:
                        ticket.priority = priority
                    if sprint_id is not None:
                        ticket.sprint_id = None if sprint_id == "" else uuid.UUID(sprint_id)
                    if assignee_email is not None:
                        ticket.assignee_id = None if assignee_email == "" else resolved_assignee_id
                    if due_date is not None:
                        ticket.due_date = None if clear_due_date else parsed_due_date

                    updated += 1
                except Exception as e:
                    failed.append({"id": ticket_id_str, "reason": str(e)})

        await db.commit()

    return {
        "updated": updated,
        "failed": failed,
    }


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    if priority not in PRIORITIES:
        priority = "medium"

    async with async_session_factory() as db:
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


@mcp.tool()
async def list_sprints(
    ctx: Context[Any, Any],
    project_slug: str,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Lista sprintow w projekcie ze statystykami (liczba ticketow, suma SP)."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        conditions = [Sprint.project_id == project.id]
        if status:
            conditions.append(Sprint.status == status)

        result = await db.execute(select(Sprint).where(*conditions).order_by(Sprint.created_at.desc()))
        sprints = result.scalars().all()

        sprint_data = []
        for s in sprints:
            stats_result = await db.execute(
                select(
                    func.count(Ticket.id),
                    func.coalesce(func.sum(Ticket.story_points), 0),
                ).where(Ticket.sprint_id == s.id)
            )
            row = stats_result.one()
            sprint_data.append(
                {
                    "id": str(s.id),
                    "name": s.name,
                    "goal": s.goal,
                    "status": s.status,
                    "start_date": s.start_date.isoformat(),
                    "end_date": s.end_date.isoformat() if s.end_date else None,
                    "ticket_count": row[0],
                    "story_points_total": row[1],
                    "created_at": s.created_at.isoformat(),
                }
            )

    return sprint_data


@mcp.tool()
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
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Sprint)
            .options(selectinload(Sprint.tickets).selectinload(Ticket.assignee))
            .where(
                Sprint.id == uuid.UUID(sprint_id),
                Sprint.project_id == project.id,
            )
        )
        sprint = result.scalar_one_or_none()
        if sprint is None:
            raise ValueError("Sprint nie istnieje")

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


@mcp.tool()
async def create_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    name: str,
    start_date: str,
    goal: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Utworz nowy sprint (status: planning). start_date w formacie YYYY-MM-DD."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not name.strip():
        raise ValueError("Nazwa sprintu jest wymagana")

    async with async_session_factory() as db:
        sprint = Sprint(
            project_id=project.id,
            name=name.strip(),
            goal=goal.strip() if goal else None,
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date) if end_date else None,
        )
        db.add(sprint)
        await db.commit()
        await db.refresh(sprint)

    return {
        "id": str(sprint.id),
        "name": sprint.name,
        "status": sprint.status,
        "message": f"Sprint '{sprint.name}' utworzony",
    }


@mcp.tool()
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
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(Sprint).where(
                Sprint.id == uuid.UUID(sprint_id),
                Sprint.project_id == project.id,
            )
        )
        sprint = result.scalar_one_or_none()
        if sprint is None:
            raise ValueError("Sprint nie istnieje")

        if name is not None:
            name = name.strip()
            if not name:
                raise ValueError("Nazwa sprintu nie moze byc pusta")
            if len(name) > 255:
                raise ValueError("Nazwa sprintu nie moze przekraczac 255 znakow")
            sprint.name = name

        if goal is not None:
            sprint.goal = goal.strip() or None

        if start_date is not None or end_date is not None:
            if sprint.status == "completed":
                raise ValueError("Nie mozna zmienic dat zakonczonego sprintu")

            new_start = date.fromisoformat(start_date) if start_date is not None else sprint.start_date
            new_end = None if end_date == "" else (date.fromisoformat(end_date) if end_date is not None else sprint.end_date)

            if new_end is not None and new_end <= new_start:
                raise ValueError("Data zakonczenia musi byc pozniejsza niz data rozpoczecia")

            if start_date is not None:
                sprint.start_date = new_start
            if end_date is not None:
                sprint.end_date = new_end

        await db.commit()
        await db.refresh(sprint)

    return {
        "id": str(sprint.id),
        "name": sprint.name,
        "goal": sprint.goal,
        "start_date": sprint.start_date.isoformat() if sprint.start_date else None,
        "end_date": sprint.end_date.isoformat() if sprint.end_date else None,
        "status": sprint.status,
        "created_at": sprint.created_at.isoformat() if sprint.created_at else None,
    }


@mcp.tool()
async def start_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
) -> dict[str, Any]:
    """Rozpocznij sprint (zmiana statusu na active).

    Tylko jeden aktywny sprint na projekt.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        error = await svc_start_sprint(uuid.UUID(sprint_id), project.id, db)
        if error:
            raise ValueError(error)

    return {"message": "Sprint rozpoczety", "sprint_id": sprint_id}


@mcp.tool()
async def complete_sprint(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str,
) -> dict[str, Any]:
    """Zakoncz sprint. Niedokonczone tickety wracaja do backloga."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        error = await svc_complete_sprint(uuid.UUID(sprint_id), project.id, db)
        if error:
            raise ValueError(error)

    return {"message": "Sprint zakonczony", "sprint_id": sprint_id}


# --- Komentarze ---


@mcp.tool()
async def list_comments(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
) -> list[dict[str, Any]]:
    """Lista komentarzy do ticketa. ticket_id: UUID lub klucz (np. MNX-12)."""
    _user, project = await _get_user_and_project(ctx, project_slug)
    resolved_id = await _resolve_ticket_uuid(ticket_id, project.id)

    async with async_session_factory() as db:
        ticket_result = await db.execute(
            select(Ticket).where(
                Ticket.id == resolved_id,
                Ticket.project_id == project.id,
            )
        )
        if ticket_result.scalar_one_or_none() is None:
            raise ValueError("Ticket nie istnieje")

        comment_result = await db.execute(
            select(TicketComment)
            .options(selectinload(TicketComment.author))
            .where(TicketComment.ticket_id == resolved_id)
            .order_by(TicketComment.created_at)
        )
        comments = comment_result.scalars().all()

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


@mcp.tool()
async def add_comment(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    content: str,
) -> dict[str, Any]:
    """Dodaj komentarz do ticketa. ticket_id: UUID lub klucz (np. MNX-12). Oznaczany jako created_via_ai=True."""
    user, project = await _get_user_and_project(ctx, project_slug)
    resolved_id = await _resolve_ticket_uuid(ticket_id, project.id)

    if not content.strip():
        raise ValueError("Tresc komentarza nie moze byc pusta")

    async with async_session_factory() as db:
        result = await db.execute(
            select(Ticket).where(
                Ticket.id == resolved_id,
                Ticket.project_id == project.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("Ticket nie istnieje")

        comment = TicketComment(
            ticket_id=resolved_id,
            user_id=user.id,
            content=content.strip(),
            created_via_ai=True,
        )
        db.add(comment)
        await db.commit()
        await db.refresh(comment)

    return {
        "id": str(comment.id),
        "message": "Komentarz dodany",
        "created_via_ai": True,
    }


@mcp.tool()
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
    """
    max_size = 200 * 1024 * 1024  # 200MB

    _user, project = await _get_user_and_project(ctx, project_slug)
    resolved_id = await _resolve_ticket_uuid(ticket_id, project.id)

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


@mcp.tool()
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
    """
    user, project = await _get_user_and_project(ctx, project_slug)
    resolved_id = await _resolve_ticket_uuid(ticket_id, project.id)

    if duration_minutes <= 0:
        raise ValueError("Czas musi byc wiekszy niz 0")

    parsed_date = date.fromisoformat(date_logged)

    async with async_session_factory() as db:
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


@mcp.tool()
async def list_wiki_pages(
    ctx: Context[Any, Any],
    project_slug: str,
) -> str:
    """Lista stron wiki w projekcie (drzewo z hierarchia).

    Zwraca kompaktowy string z wcieciami pokazujacymi hierarchie parent-child.
    Kolumny: ID (pelne UUID), Title (z wcieciem 2 spacje na poziom), Updated (YYYY-MM-DD).
    Uzyj ID strony do wywolania get_wiki_page, update_wiki_page lub delete_wiki_page.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        tree = await get_page_tree(project.id, db)

    def _flatten(nodes: list[dict[str, Any]], depth: int = 0) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for node in nodes:
            p = node["page"]
            result.append(
                {
                    "id": str(p.id),
                    "title": p.title,
                    "depth": depth,
                    "updated_at": p.updated_at.isoformat(),
                }
            )
            result.extend(_flatten(node["children"], depth + 1))
        return result

    return _format_wiki_tree(_flatten(tree))


@mcp.tool()
async def get_wiki_page(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
) -> dict[str, Any]:
    """Szczegoly strony wiki z pelna trescia markdown."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        from sqlalchemy.orm import selectinload

        result = await db.execute(
            select(WikiPage)
            .options(selectinload(WikiPage.created_by), selectinload(WikiPage.last_edited_by))
            .where(WikiPage.id == uuid.UUID(page_id), WikiPage.project_id == project.id)
        )
        page = result.scalar_one_or_none()
        if page is None:
            raise ValueError("Strona wiki nie istnieje")

        content = get_page_content(page)

    return {
        "id": str(page.id),
        "title": page.title,
        "slug": page.slug,
        "parent_id": str(page.parent_id) if page.parent_id else None,
        "position": page.position,
        "content": content,
        "is_ai_touched": page.is_ai_touched,
        "created_by": page.created_by.email,
        "last_edited_by": page.last_edited_by.email,
        "created_at": page.created_at.isoformat(),
        "updated_at": page.updated_at.isoformat(),
    }


@mcp.tool()
async def create_wiki_page(
    ctx: Context[Any, Any],
    project_slug: str,
    title: str,
    content: str,
    parent_id: str | None = None,
    position: int = 0,
) -> dict[str, Any]:
    """Utworz nowa strone wiki. Oznaczana jako is_ai_touched=True.

    parent_id -- UUID strony nadrzednej (opcjonalnie, dla podstron).
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    if not title.strip():
        raise ValueError("Tytul jest wymagany")

    async with async_session_factory() as db:
        page = await svc_create_wiki_page(
            project_id=project.id,
            project_slug=project.slug,
            title=title,
            content=content,
            user_id=user.id,
            parent_id=uuid.UUID(parent_id) if parent_id else None,
            position=position,
            is_ai=True,
            db=db,
        )

    return {
        "id": str(page.id),
        "title": page.title,
        "slug": page.slug,
        "is_ai_touched": True,
        "message": f"Strona wiki '{page.title}' utworzona",
    }


@mcp.tool()
async def update_wiki_page(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
    title: str | None = None,
    content: str | None = None,
    position: int | None = None,
) -> dict[str, Any]:
    """Aktualizuj strone wiki. Oznaczana jako is_ai_touched=True.

    Podaj tylko pola do zmiany.
    """
    user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        from sqlalchemy.orm import selectinload

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
            db=db,
        )

    return {
        "id": str(page.id),
        "title": page.title,
        "is_ai_touched": True,
        "message": f"Strona wiki '{page.title}' zaktualizowana",
    }


@mcp.tool()
async def search_wiki(
    ctx: Context[Any, Any],
    project_slug: str,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Wyszukaj semantycznie w wiki projektu (RAG).

    query -- zapytanie w jezyku naturalnym
    limit -- maksymalna liczba wynikow (domyslnie 10)
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    from monolynx.services.embeddings import search_wiki_pages

    async with async_session_factory() as db:
        return await search_wiki_pages(project.id, query, db, limit=limit)


@mcp.tool()
async def delete_wiki_page(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
) -> dict[str, Any]:
    """Usun strone wiki wraz z podstronami."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(select(WikiPage).where(WikiPage.id == uuid.UUID(page_id), WikiPage.project_id == project.id))
        page = result.scalar_one_or_none()
        if page is None:
            raise ValueError("Strona wiki nie istnieje")

        title = page.title
        await svc_delete_wiki_page(page, db)

    return {"message": f"Strona wiki '{title}' usunieta"}


# --- Graf (polaczenia) ---


@mcp.tool()
async def create_graph_node(
    ctx: Context[Any, Any],
    project_slug: str,
    type: str,
    name: str,
    file_path: str | None = None,
    line_number: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Utworz node w grafie polaczen. type: File, Class, Method, Function, Const, Module."""
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
async def list_graph_nodes(
    ctx: Context[Any, Any],
    project_slug: str,
    type: str | None = None,
    search: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Lista node'ow w grafie polaczen z opcjonalnym filtrowaniem po typie i nazwie."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    return await graph_service.list_nodes(project.id, type_filter=type, search=search, limit=limit)


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
async def delete_graph_node(
    ctx: Context[Any, Any],
    project_slug: str,
    node_id: str,
) -> dict[str, Any]:
    """Usun node i wszystkie jego krawedzie."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    deleted = await graph_service.delete_node(project.id, node_id)
    if not deleted:
        raise ValueError("Node nie istnieje")

    return {"message": "Node usuniety", "node_id": node_id}


@mcp.tool()
async def create_graph_edge(
    ctx: Context[Any, Any],
    project_slug: str,
    source_id: str,
    target_id: str,
    type: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Utworz krawedz miedzy node'ami. type: CONTAINS, CALLS, IMPORTS, INHERITS, USES, IMPLEMENTS."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    if type not in GRAPH_EDGE_TYPES:
        raise ValueError(f"Nieznany typ krawedzi: {type}. Dozwolone: {', '.join(GRAPH_EDGE_TYPES)}")

    edge = await graph_service.create_edge(project.id, source_id, target_id, type, metadata)
    if edge is None:
        raise ValueError("Nie znaleziono node'ow zrodlowego lub docelowego")

    return {**edge, "message": f"Krawedz {type} utworzona"}


@mcp.tool()
async def delete_graph_edge(
    ctx: Context[Any, Any],
    project_slug: str,
    source_id: str,
    target_id: str,
    type: str,
) -> dict[str, Any]:
    """Usun krawedz miedzy node'ami."""
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
async def query_graph(
    ctx: Context[Any, Any],
    project_slug: str,
    node_type: str | None = None,
    limit: int = 200,
) -> str:
    """Pobierz graf lub podgraf projektu (node'y + krawedzie) w kompaktowym formacie Arrow DSL.

    Zwraca tekst z node'ami jako [Type] name (metadata) i krawędziami jako src --TYPE--> tgt.
    Opcjonalnie filtruj po typie node'a.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    data = await graph_service.get_graph(project.id, type_filter=node_type, limit=limit)
    return _format_graph_dsl(data)


@mcp.tool()
async def find_graph_path(
    ctx: Context[Any, Any],
    project_slug: str,
    source_id: str,
    target_id: str,
) -> str:
    """Znajdz najkrotsza sciezke miedzy dwoma node'ami. Zwraca kompaktowy format Arrow DSL."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    data = await graph_service.find_path(project.id, source_id, target_id)
    return _format_graph_dsl(data)


@mcp.tool()
async def get_graph_stats(
    ctx: Context[Any, Any],
    project_slug: str,
) -> dict[str, Any]:
    """Statystyki grafu: liczba node'ow i krawedzi per typ."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    if not graph_service.is_enabled():
        raise ValueError("Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)")

    return await graph_service.get_stats(project.id)


@mcp.tool()
async def bulk_create_graph_nodes(
    ctx: Context[Any, Any],
    project_slug: str,
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Masowe tworzenie node'ow. Kazdy element: {type, name, file_path?, line_number?, metadata?}."""
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
async def bulk_create_graph_edges(
    ctx: Context[Any, Any],
    project_slug: str,
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Masowe tworzenie krawedzi. Kazdy element: {source_id, target_id, type, metadata?}."""
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
async def list_heartbeats(
    ctx: Context[Any, Any],
    project_slug: str,
) -> list[dict[str, Any]]:
    """Lista heartbeatow projektu z aktualnym statusem i URL do pingowania."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(select(Heartbeat).where(Heartbeat.project_id == project.id).order_by(Heartbeat.created_at))
        heartbeats = result.scalars().all()

    return [_heartbeat_dict(hb) for hb in heartbeats]


@mcp.tool()
async def get_heartbeat(
    ctx: Context[Any, Any],
    project_slug: str,
    heartbeat_id: str,
) -> dict[str, Any]:
    """Szczegoly heartbeatu: token, URL do pinga, status, last_ping_at, period, grace."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
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


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

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


@mcp.tool()
async def delete_heartbeat(
    ctx: Context[Any, Any],
    project_slug: str,
    heartbeat_id: str,
) -> dict[str, Any]:
    """Usuwa heartbeat z projektu."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
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


@mcp.tool()
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
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    if limit > 200:
        limit = 200
    if limit < 1:
        limit = 1

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


@mcp.tool()
async def get_burndown(
    ctx: Context[Any, Any],
    project_slug: str,
    sprint_id: str | None = None,
) -> dict[str, Any]:
    """Dane burndown chart dla sprintu -- ideal line, actual line, velocity, prognoza.

    Jesli sprint_id nie podany, uzywa aktywnego sprintu.
    """
    _user, project = await _get_user_and_project(ctx, project_slug)

    sprint_uuid: uuid.UUID | None = None
    if sprint_id is not None:
        sprint_uuid = uuid.UUID(sprint_id)

    async with async_session_factory() as db:
        return await svc_get_burndown_data(db, project.id, sprint_uuid)


# --- Zalaczniki do ticketow (pobieranie) ---


@mcp.tool()
async def get_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    ticket_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    """Pobierz zawartosc zalacznika z ticketa (base64). ticket_id: UUID lub klucz (np. MNX-12). Obrazki zwracane jako data URI."""
    _user, project = await _get_user_and_project(ctx, project_slug)
    resolved_id = await _resolve_ticket_uuid(ticket_id, project.id)

    async with async_session_factory() as db:
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


@mcp.tool()
async def get_wiki_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
    attachment_filename: str,
) -> dict[str, Any]:
    """Pobierz zawartosc zalacznika ze strony Wiki (base64)."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(WikiAttachment)
            .join(WikiPage, WikiAttachment.wiki_page_id == WikiPage.id)
            .where(
                WikiAttachment.filename == attachment_filename,
                WikiPage.id == uuid.UUID(page_id),
                WikiPage.project_id == project.id,
            )
            .order_by(WikiAttachment.created_at.desc())
            .limit(1)
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
        "filename": attachment.filename,
        "mime_type": attachment.mime_type,
        "size": attachment.size,
        "content_base64": content_b64,
    }


@mcp.tool()
async def add_wiki_page_attachment(
    ctx: Context[Any, Any],
    project_slug: str,
    page_id: str,
    file_base64: str,
    filename: str,
    mime_type: str | None = None,
) -> dict[str, Any]:
    """Dodaj zalacznik do strony Wiki. Plik zakodowany w base64. Maksymalny rozmiar: 200MB."""
    max_size = 200 * 1024 * 1024

    _user, project = await _get_user_and_project(ctx, project_slug)

    if not filename or not filename.strip():
        raise ValueError("Nazwa pliku nie moze byc pusta")
    filename = os.path.basename(filename.strip())
    filename = re.sub(r'[\x00-\x1f"\\]', "_", filename)
    if not filename or filename in (".", ".."):
        raise ValueError("Nieprawidlowa nazwa pliku")
    if len(filename) > 255:
        raise ValueError("Nazwa pliku nie moze przekraczac 255 znakow")

    if not file_base64 or not file_base64.strip():
        raise ValueError("Zawartosc pliku (file_base64) nie moze byc pusta")

    try:
        file_bytes = base64.b64decode(file_base64, validate=True)
    except Exception as exc:
        raise ValueError("Nieprawidlowy format base64") from exc

    if len(file_bytes) > max_size:
        raise ValueError(f"Plik za duzy: max 200MB, otrzymano {len(file_bytes) / 1024 / 1024:.1f}MB")

    if mime_type is not None:
        if not re.match(r"^[a-z]+/[a-z0-9.+\-]+$", mime_type):
            raise ValueError("Nieprawidlowy format mime_type")
    else:
        mime_type = "application/octet-stream"

    async with async_session_factory() as db:
        result = await db.execute(
            select(WikiPage).where(
                WikiPage.id == uuid.UUID(page_id),
                WikiPage.project_id == project.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError("Strona wiki nie istnieje")

        ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
        now = datetime.now(UTC)
        date_prefix = f"{now.year}/{now.month:02d}/{now.day:02d}"
        storage_path = f"{project_slug}/wiki-attachments/{page_id}/{date_prefix}/{uuid.uuid4().hex}.{ext}"

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            await loop.run_in_executor(
                executor,
                lambda: minio_upload_object(storage_path, file_bytes, mime_type),
            )

        attachment = WikiAttachment(
            wiki_page_id=uuid.UUID(page_id),
            filename=filename,
            storage_path=storage_path,
            mime_type=mime_type,
            size=len(file_bytes),
            created_via_ai=True,
        )
        db.add(attachment)
        await db.commit()
        await db.refresh(attachment)

    return {
        "attachment_id": str(attachment.id),
        "filename": attachment.filename,
        "size": attachment.size,
        "uploaded_at": attachment.created_at.isoformat(),
    }


# --- Globalne pliki Wiki ---


@mcp.tool()
async def add_wiki_file(
    ctx: Context[Any, Any],
    project_slug: str,
    file_base64: str,
    filename: str,
    mime_type: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    """Dodaj globalny plik do repozytorium Wiki projektu. Plik zakodowany w base64. Maksymalny rozmiar: 200MB."""
    max_size = 200 * 1024 * 1024

    _user, project = await _get_user_and_project(ctx, project_slug)

    if not filename or not filename.strip():
        raise ValueError("Nazwa pliku nie moze byc pusta")
    filename = os.path.basename(filename.strip())
    filename = re.sub(r'[\x00-\x1f"\\]', "_", filename)
    if not filename or filename in (".", ".."):
        raise ValueError("Nieprawidlowa nazwa pliku")
    if len(filename) > 255:
        raise ValueError("Nazwa pliku nie moze przekraczac 255 znakow")

    if not file_base64 or not file_base64.strip():
        raise ValueError("Zawartosc pliku (file_base64) nie moze byc pusta")

    try:
        file_bytes = base64.b64decode(file_base64, validate=True)
    except Exception as exc:
        raise ValueError("Nieprawidlowy format base64") from exc

    if len(file_bytes) > max_size:
        raise ValueError(f"Plik za duzy: max 200MB, otrzymano {len(file_bytes) / 1024 / 1024:.1f}MB")

    if mime_type is not None:
        if not re.match(r"^[a-z]+/[a-z0-9.+\-]+$", mime_type):
            raise ValueError("Nieprawidlowy format mime_type")
    else:
        mime_type = "application/octet-stream"

    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    now = datetime.now(UTC)
    date_prefix = f"{now.year}/{now.month:02d}/{now.day:02d}"
    storage_path = f"{project_slug}/wiki-files/{date_prefix}/{uuid.uuid4().hex}.{ext}"

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as executor:
        await loop.run_in_executor(
            executor,
            lambda: minio_upload_object(storage_path, file_bytes, mime_type),
        )

    async with async_session_factory() as db:
        wiki_file = WikiFile(
            project_id=project.id,
            filename=filename,
            storage_path=storage_path,
            mime_type=mime_type,
            size=len(file_bytes),
            description=description,
            created_via_ai=True,
        )
        db.add(wiki_file)
        await db.commit()
        await db.refresh(wiki_file)

    return {
        "file_id": str(wiki_file.id),
        "filename": wiki_file.filename,
        "size": wiki_file.size,
        "description": wiki_file.description,
        "uploaded_at": wiki_file.created_at.isoformat(),
    }


@mcp.tool()
async def get_wiki_file(
    ctx: Context[Any, Any],
    project_slug: str,
    file_id: str,
) -> dict[str, Any]:
    """Pobierz zawartosc globalnego pliku Wiki (base64)."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(WikiFile).where(
                WikiFile.id == uuid.UUID(file_id),
                WikiFile.project_id == project.id,
            )
        )
        wiki_file = result.scalar_one_or_none()
        if wiki_file is None:
            raise ValueError("Plik nie istnieje")

        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            data, content_type = await loop.run_in_executor(
                executor,
                lambda: minio_get_attachment(wiki_file.storage_path),
            )

    content_b64 = base64.b64encode(data).decode("ascii")
    if content_type.startswith("image/"):
        content_b64 = f"data:{content_type};base64,{content_b64}"

    return {
        "file_id": str(wiki_file.id),
        "filename": wiki_file.filename,
        "mime_type": wiki_file.mime_type,
        "size": wiki_file.size,
        "description": wiki_file.description,
        "content_base64": content_b64,
    }


@mcp.tool()
async def update_wiki_file(
    ctx: Context[Any, Any],
    project_slug: str,
    file_id: str,
    description: str | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Zaktualizuj globalny plik Wiki — opis lub nazwe. Podaj tylko pola do zmiany."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(
            select(WikiFile).where(
                WikiFile.id == uuid.UUID(file_id),
                WikiFile.project_id == project.id,
            )
        )
        wiki_file = result.scalar_one_or_none()
        if wiki_file is None:
            raise ValueError("Plik nie istnieje")

        if description is not None:
            wiki_file.description = description or None
        if filename is not None:
            safe = re.sub(r"[^\w.\-]", "_", filename.strip())[:255]
            if safe:
                wiki_file.filename = safe

        await db.commit()
        await db.refresh(wiki_file)

    return {
        "file_id": str(wiki_file.id),
        "filename": wiki_file.filename,
        "description": wiki_file.description,
        "message": "Plik zaktualizowany",
    }


@mcp.tool()
async def list_wiki_files(
    ctx: Context[Any, Any],
    project_slug: str,
) -> list[dict[str, Any]]:
    """Lista globalnych plikow w repozytorium Wiki projektu."""
    _user, project = await _get_user_and_project(ctx, project_slug)

    async with async_session_factory() as db:
        result = await db.execute(select(WikiFile).where(WikiFile.project_id == project.id).order_by(WikiFile.created_at.desc()))
        files = result.scalars().all()

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
