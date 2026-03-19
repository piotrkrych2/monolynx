"""Serwis time trackingu -- logika zarzadzania wpisami czasu pracy."""

from __future__ import annotations

import math
import re
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.models.time_tracking_entry import TimeTrackingEntry
from monolynx.schemas.time_tracking import TimeTrackingEntryResponse, TimeTrackingFilter, WorkReportResult

# Regex for flexible duration parsing: "2h30m", "2h", "30m", "2.5", "2.5h", "90m", "8"
_DURATION_RE = re.compile(
    r"^\s*(?:(\d+(?:[.,]\d+)?)\s*h)?\s*(?:(\d+(?:[.,]\d+)?)\s*m)?\s*$",
    re.IGNORECASE,
)


def parse_duration(value: str | int | float) -> int | None:
    """Parse flexible duration string into minutes.

    Rules:
    - Plain number without unit (e.g. "8", "2.5") → treated as HOURS
    - Number with 'h' (e.g. "4h", "2.5h") → hours
    - Number with 'm' (e.g. "30m", "45m") → minutes
    - Combined (e.g. "2h30m", "1h 15m") → hours + minutes
    - Already int/float → treated as hours

    Returns total minutes (int, rounded) or None if unparseable.
    """
    if isinstance(value, (int, float)):
        minutes = round(value * 60)
        return minutes if minutes > 0 else None

    raw = str(value).strip().replace(",", ".")
    if not raw:
        return None

    match = _DURATION_RE.match(raw)
    if match:
        h_str, m_str = match.group(1), match.group(2)
        if h_str is None and m_str is None:
            return None
        hours = float(h_str) if h_str else 0.0
        mins = float(m_str) if m_str else 0.0
        total = round(hours * 60 + mins)
        return total if total > 0 else None

    # Fallback: try as plain number → hours
    try:
        hours = float(raw)
        total = round(hours * 60)
        return total if total > 0 else None
    except ValueError:
        return None


async def add_time_entry(
    ticket_id: uuid.UUID,
    user_id: uuid.UUID,
    duration_minutes: int,
    date_logged: date,
    description: str | None,
    db: AsyncSession,
    *,
    created_via_ai: bool = False,
) -> TimeTrackingEntry | str:
    """Tworzy wpis czasu pracy. Zwraca wpis lub komunikat bledu."""
    # Sprawdz czy ticket istnieje
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id))
    ticket = result.scalar_one_or_none()
    if ticket is None:
        return "Ticket nie istnieje"

    # Sprawdz czy uzytkownik jest czlonkiem projektu
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == ticket.project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        return "Uzytkownik nie jest czlonkiem projektu"

    entry = TimeTrackingEntry(
        ticket_id=ticket_id,
        user_id=user_id,
        sprint_id=ticket.sprint_id,
        project_id=ticket.project_id,
        duration_minutes=duration_minutes,
        date_logged=date_logged,
        description=description,
        status="draft",
        created_via_ai=created_via_ai,
    )
    db.add(entry)
    await db.commit()
    return entry


async def delete_time_entry(entry_id: uuid.UUID, user_id: uuid.UUID, db: AsyncSession) -> str | None:
    """Usuwa wpis czasu pracy. Zwraca blad lub None."""
    result = await db.execute(select(TimeTrackingEntry).where(TimeTrackingEntry.id == entry_id))
    entry = result.scalar_one_or_none()
    if entry is None:
        return "Wpis nie istnieje"

    if entry.user_id != user_id:
        return "Brak uprawnien do usuniecia tego wpisu"

    await db.delete(entry)
    await db.commit()
    return None


async def get_ticket_total_hours(ticket_id: uuid.UUID, db: AsyncSession) -> float:
    """Zwraca sume godzin zalogowanych na tickecie."""
    result = await db.execute(select(func.coalesce(func.sum(TimeTrackingEntry.duration_minutes), 0)).where(TimeTrackingEntry.ticket_id == ticket_id))
    total_minutes = result.scalar_one()
    return round(total_minutes / 60, 2)


def _apply_filters(query, filters: TimeTrackingFilter):  # type: ignore[no-untyped-def]
    """Aplikuje filtry do zapytania bazowego."""
    if filters.project_ids:
        query = query.where(TimeTrackingEntry.project_id.in_(filters.project_ids))
    if filters.user_ids:
        query = query.where(TimeTrackingEntry.user_id.in_(filters.user_ids))
    if filters.sprint_ids:
        query = query.where(TimeTrackingEntry.sprint_id.in_(filters.sprint_ids))
    if filters.date_from is not None:
        query = query.where(TimeTrackingEntry.date_logged >= filters.date_from)
    if filters.date_to is not None:
        query = query.where(TimeTrackingEntry.date_logged <= filters.date_to)
    if filters.status is not None:
        query = query.where(TimeTrackingEntry.status == filters.status)
    if filters.created_via_ai is not None:
        query = query.where(TimeTrackingEntry.created_via_ai == filters.created_via_ai)
    return query


async def get_work_report(
    filters: TimeTrackingFilter,
    db: AsyncSession,
    sort_by: str | None = None,
    *,
    paginate: bool = True,
) -> WorkReportResult:
    """Zwraca przefiltrowane wpisy czasu pracy z agregacjami."""
    # Bazowe zapytanie z filtrami
    base_query = _apply_filters(select(TimeTrackingEntry), filters)

    # Policz wszystkie wpisy (bez paginacji)
    count_query = select(func.count()).select_from(base_query.subquery())
    total_count_result = await db.execute(count_query)
    entry_count = total_count_result.scalar_one()

    # Suma minut (bez paginacji)
    sub = base_query.subquery()
    sum_query = select(func.coalesce(func.sum(sub.c.duration_minutes), 0))
    sum_result = await db.execute(sum_query)
    total_minutes = sum_result.scalar_one()
    total_hours = round(total_minutes / 60, 2)

    # Zakres dat z calego przefiltrowanego zbioru
    date_range_query = select(func.min(sub.c.date_logged), func.max(sub.c.date_logged))
    date_range_result = await db.execute(date_range_query)
    date_range_row = date_range_result.one()
    date_range = (date_range_row[0], date_range_row[1]) if date_range_row[0] is not None else None

    # Paginacja
    total_pages = max(1, math.ceil(entry_count / filters.per_page)) if paginate else 1
    offset = (filters.page - 1) * filters.per_page if paginate else 0

    # Sortowanie
    sort_column_map = {
        "date": TimeTrackingEntry.date_logged.desc(),
        "hours": TimeTrackingEntry.duration_minutes.desc(),
        "user": TimeTrackingEntry.user_id.asc(),
    }
    order_clause = sort_column_map.get(sort_by, TimeTrackingEntry.date_logged.desc())  # type: ignore[arg-type]

    entries_query = base_query.options(selectinload(TimeTrackingEntry.user), selectinload(TimeTrackingEntry.ticket)).order_by(
        order_clause, TimeTrackingEntry.created_at.desc()
    )
    if paginate:
        entries_query = entries_query.offset(offset).limit(filters.per_page)
    entries_result = await db.execute(entries_query)
    entries = list(entries_result.scalars().all())

    # Agregacje
    hours_by_user = await aggregate_hours_per_user(filters, db)
    hours_by_sprint = await aggregate_hours_per_sprint(filters, db)
    hours_by_project = await aggregate_hours_per_project(filters, db)

    # Konwertuj wpisy na response
    entry_responses = []
    for e in entries:
        entry_responses.append(
            TimeTrackingEntryResponse(
                id=e.id,
                ticket_id=e.ticket_id,
                user_id=e.user_id,
                sprint_id=e.sprint_id,
                project_id=e.project_id,
                duration_minutes=e.duration_minutes,
                date_logged=e.date_logged,
                description=e.description,
                status=e.status,
                created_via_ai=e.created_via_ai,
                created_at=e.created_at.isoformat(),
                updated_at=e.updated_at.isoformat(),
            )
        )

    return WorkReportResult(
        entries=entry_responses,
        total_hours=total_hours,
        entry_count=entry_count,
        hours_by_user={str(k): v for k, v in hours_by_user.items()},
        hours_by_sprint={str(k): v for k, v in hours_by_sprint.items()},
        hours_by_project={str(k): v for k, v in hours_by_project.items()},
        date_range=date_range,
        page=filters.page,
        total_pages=total_pages,
    )


async def aggregate_hours_per_sprint(
    filters: TimeTrackingFilter,
    db: AsyncSession,
) -> dict[uuid.UUID, float]:
    """Zwraca dict {sprint_id: total_hours} dla wykresu slupkowego."""
    query = _apply_filters(
        select(
            TimeTrackingEntry.sprint_id,
            func.sum(TimeTrackingEntry.duration_minutes).label("total_minutes"),
        )
        .where(TimeTrackingEntry.sprint_id.isnot(None))
        .group_by(TimeTrackingEntry.sprint_id),
        filters,
    )

    result = await db.execute(query)
    rows = result.all()
    return {row.sprint_id: round(row.total_minutes / 60, 2) for row in rows}


async def aggregate_hours_per_user(
    filters: TimeTrackingFilter,
    db: AsyncSession,
) -> dict[uuid.UUID, float]:
    """Zwraca dict {user_id: total_hours} dla wykresu kolowego."""
    query = _apply_filters(
        select(
            TimeTrackingEntry.user_id,
            func.sum(TimeTrackingEntry.duration_minutes).label("total_minutes"),
        ).group_by(TimeTrackingEntry.user_id),
        filters,
    )

    result = await db.execute(query)
    rows = result.all()
    return {row.user_id: round(row.total_minutes / 60, 2) for row in rows}


async def aggregate_hours_per_project(
    filters: TimeTrackingFilter,
    db: AsyncSession,
) -> dict[uuid.UUID, float]:
    """Zwraca dict {project_id: total_hours} dla wykresu wg projektu."""
    query = _apply_filters(
        select(
            TimeTrackingEntry.project_id,
            func.sum(TimeTrackingEntry.duration_minutes).label("total_minutes"),
        ).group_by(TimeTrackingEntry.project_id),
        filters,
    )

    result = await db.execute(query)
    rows = result.all()
    return {row.project_id: round(row.total_minutes / 60, 2) for row in rows}
