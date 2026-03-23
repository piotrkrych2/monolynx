"""Serwis bulk statystyk projektów dla strony listy projektów."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.heartbeat import Heartbeat
from monolynx.models.issue import Issue
from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket


@dataclass
class ProjectStats:
    issues_count: int = 0
    issues_pulse: bool = False
    monitoring_uptime_24h: float | None = None
    monitoring_pulse: bool = False
    heartbeats_down: int = 0
    sp_done: int = 0
    sp_total: int = 0
    last_activity: datetime | None = None


async def get_bulk_project_stats(
    project_ids: list[uuid.UUID],
    db: AsyncSession,
) -> dict[uuid.UUID, ProjectStats]:
    """Pobiera statystyki dla wielu projektów w max 5 queries (GROUP BY project_id)."""
    if not project_ids:
        return {}

    result: dict[uuid.UUID, ProjectStats] = {pid: ProjectStats() for pid in project_ids}

    # 1. Issues: liczba unresolved
    issues_rows = (
        await db.execute(
            select(Issue.project_id, func.count(Issue.id))
            .where(
                Issue.project_id.in_(project_ids),
                Issue.status == "unresolved",
            )
            .group_by(Issue.project_id)
        )
    ).all()
    for project_id, count in issues_rows:
        result[project_id].issues_count = count
        result[project_id].issues_pulse = count >= 5

    # 2. Monitoring uptime 24h — łączymy MonitorCheck z Monitor per project_id
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)
    uptime_rows = (
        await db.execute(
            select(
                Monitor.project_id,
                func.count(MonitorCheck.id),
                func.count(case((MonitorCheck.is_success.is_(True), 1))),
            )
            .join(Monitor, MonitorCheck.monitor_id == Monitor.id)
            .where(
                Monitor.project_id.in_(project_ids),
                Monitor.is_active.is_(True),
                MonitorCheck.checked_at >= twenty_four_hours_ago,
            )
            .group_by(Monitor.project_id)
        )
    ).all()
    for project_id, total_checks, success_checks in uptime_rows:
        if total_checks > 0:
            uptime = round((success_checks / total_checks) * 100, 1)
            result[project_id].monitoring_uptime_24h = uptime
            result[project_id].monitoring_pulse = uptime < 90.0

    # 3. Heartbeats down
    hb_rows = (
        await db.execute(
            select(Heartbeat.project_id, func.count(Heartbeat.id))
            .where(
                Heartbeat.project_id.in_(project_ids),
                Heartbeat.status == "down",
            )
            .group_by(Heartbeat.project_id)
        )
    ).all()
    for project_id, count in hb_rows:
        result[project_id].heartbeats_down = count

    # 4. Scrum SP: aktywny sprint + tickety
    sp_rows = (
        await db.execute(
            select(
                Sprint.project_id,
                func.coalesce(func.sum(Ticket.story_points), 0),
                func.coalesce(
                    func.sum(case((Ticket.status == "done", func.coalesce(Ticket.story_points, 0)))),
                    0,
                ),
            )
            .join(Ticket, (Ticket.sprint_id == Sprint.id))
            .where(
                Sprint.project_id.in_(project_ids),
                Sprint.status == "active",
            )
            .group_by(Sprint.project_id)
        )
    ).all()
    for project_id, sp_total, sp_done in sp_rows:
        result[project_id].sp_total = int(sp_total)
        result[project_id].sp_done = int(sp_done)

    # 5. Last activity: max updated_at z ticketów
    activity_rows = (
        await db.execute(select(Ticket.project_id, func.max(Ticket.updated_at)).where(Ticket.project_id.in_(project_ids)).group_by(Ticket.project_id))
    ).all()
    for project_id, last_at in activity_rows:
        result[project_id].last_activity = last_at

    return result
