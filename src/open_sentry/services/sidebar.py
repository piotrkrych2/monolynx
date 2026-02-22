"""Serwis badge'ow alertowych w sidebarze projektu."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_sentry.models.issue import Issue
from open_sentry.models.monitor import Monitor
from open_sentry.models.monitor_check import MonitorCheck


@dataclass(frozen=True, slots=True)
class SidebarBadges:
    issues_count: int = 0
    issues_pulse: bool = False
    monitors_failing_count: int = 0
    monitors_failing_pulse: bool = False
    monitoring_uptime_24h: float | None = None


async def get_sidebar_badges(project_id: uuid.UUID, db: AsyncSession) -> SidebarBadges:
    seven_days_ago = datetime.now(UTC) - timedelta(days=7)

    # 500ki: nierozwiazane issues + pulse jesli last_seen < 7d
    issues_result = await db.execute(
        select(
            func.count(Issue.id),
            func.count(Issue.id).filter(Issue.last_seen >= seven_days_ago),
        ).where(
            Issue.project_id == project_id,
            Issue.status == "unresolved",
        )
    )
    issues_row = issues_result.one()
    issues_count: int = issues_row[0]
    issues_recent: int = issues_row[1]

    # Monitoring: aktywne monitory z nieudanym ostatnim checkiem
    latest_check_sq = (
        select(
            MonitorCheck.monitor_id,
            func.max(MonitorCheck.checked_at).label("max_checked_at"),
        )
        .group_by(MonitorCheck.monitor_id)
        .subquery()
    )

    monitors_result = await db.execute(
        select(
            func.count(Monitor.id),
            func.count(Monitor.id).filter(MonitorCheck.checked_at >= seven_days_ago),
        )
        .join(
            latest_check_sq,
            Monitor.id == latest_check_sq.c.monitor_id,
        )
        .join(
            MonitorCheck,
            (MonitorCheck.monitor_id == latest_check_sq.c.monitor_id) & (MonitorCheck.checked_at == latest_check_sq.c.max_checked_at),
        )
        .where(
            Monitor.project_id == project_id,
            Monitor.is_active.is_(True),
            MonitorCheck.is_success.is_(False),
        )
    )
    monitors_row = monitors_result.one()
    monitors_failing: int = monitors_row[0]
    monitors_failing_recent: int = monitors_row[1]

    # Monitoring: uptime 24h (wszystkie aktywne monitory w projekcie)
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)
    uptime_result = await db.execute(
        select(
            func.count(MonitorCheck.id),
            func.count(case((MonitorCheck.is_success.is_(True), 1))),
        )
        .join(Monitor, MonitorCheck.monitor_id == Monitor.id)
        .where(
            Monitor.project_id == project_id,
            Monitor.is_active.is_(True),
            MonitorCheck.checked_at >= twenty_four_hours_ago,
        )
    )
    uptime_row = uptime_result.one()
    total_checks: int = uptime_row[0]
    success_checks: int = uptime_row[1]
    monitoring_uptime_24h: float | None = None
    if total_checks > 0:
        monitoring_uptime_24h = round((success_checks / total_checks) * 100, 1)

    return SidebarBadges(
        issues_count=issues_count,
        issues_pulse=issues_recent > 0,
        monitors_failing_count=monitors_failing,
        monitors_failing_pulse=monitors_failing_recent > 0,
        monitoring_uptime_24h=monitoring_uptime_24h,
    )
