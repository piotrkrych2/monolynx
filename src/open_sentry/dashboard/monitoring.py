"""Dashboard -- modul monitoringu online (lista, CRUD, szczegoly)."""

from __future__ import annotations

import ipaddress
import socket
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from open_sentry.constants import INTERVAL_UNIT_LABELS, INTERVAL_UNITS
from open_sentry.database import get_db
from open_sentry.models.monitor import Monitor
from open_sentry.models.monitor_check import MonitorCheck
from open_sentry.models.project import Project

from .helpers import _get_user_id, flash, render_project_page, templates

router = APIRouter(prefix="/dashboard", tags=["monitoring"])

MAX_MONITORS_PER_PROJECT = 20


def _is_url_safe(url: str) -> str | None:
    """Waliduj URL pod katem SSRF. Zwraca blad lub None jesli OK."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "Nieprawidlowy URL"

    hostname = parsed.hostname
    if not hostname:
        return "URL nie zawiera hosta"

    # Zblokuj oczywiste hosty lokalne
    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"}
    if hostname.lower() in blocked_hosts:
        return "Adresy lokalne sa niedozwolone"

    # Rozwiaz DNS i sprawdz czy IP jest prywatne/loopback
    try:
        addr_infos = socket.getaddrinfo(
            hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
        )
    except socket.gaierror:
        return "Nie mozna rozwiazac hosta"

    for _family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return "Adresy prywatne i wewnetrzne sa niedozwolone"

    return None


async def _get_project(slug: str, db: AsyncSession) -> Project | None:
    result = await db.execute(
        select(Project).where(Project.slug == slug, Project.is_active.is_(True))
    )
    return result.scalar_one_or_none()


async def _get_monitor(
    monitor_id: uuid.UUID, project_id: uuid.UUID, db: AsyncSession
) -> Monitor | None:
    result = await db.execute(
        select(Monitor).where(
            Monitor.id == monitor_id, Monitor.project_id == project_id
        )
    )
    return result.scalar_one_or_none()


# --- Lista monitorow ---


@router.get("/{slug}/monitoring/", response_class=HTMLResponse, response_model=None)
async def monitor_list(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    # Pobierz monitory z ostatnim checkiem (subquery na max checked_at)
    latest_check_sq = (
        select(
            MonitorCheck.monitor_id,
            func.max(MonitorCheck.checked_at).label("max_checked_at"),
        )
        .group_by(MonitorCheck.monitor_id)
        .subquery()
    )

    result = await db.execute(
        select(Monitor)
        .where(Monitor.project_id == project.id)
        .order_by(Monitor.is_active.desc(), Monitor.created_at.desc())
    )
    monitors = list(result.scalars().all())

    # Pobierz ostatni check dla kazdego monitora
    monitor_ids = [m.id for m in monitors]
    last_checks: dict[uuid.UUID, MonitorCheck | None] = {m.id: None for m in monitors}

    if monitor_ids:
        checks_result = await db.execute(
            select(MonitorCheck)
            .join(
                latest_check_sq,
                (MonitorCheck.monitor_id == latest_check_sq.c.monitor_id)
                & (MonitorCheck.checked_at == latest_check_sq.c.max_checked_at),
            )
            .where(MonitorCheck.monitor_id.in_(monitor_ids))
        )
        for check in checks_result.scalars().all():
            last_checks[check.monitor_id] = check

    return await render_project_page(
        request,
        "dashboard/monitoring/list.html",
        {
            "project": project,
            "monitors": monitors,
            "last_checks": last_checks,
            "active_module": "monitoring",
            "interval_unit_labels": INTERVAL_UNIT_LABELS,
        },
        db=db,
    )


# --- Tworzenie monitora ---


@router.get(
    "/{slug}/monitoring/create", response_class=HTMLResponse, response_model=None
)
async def monitor_create_form(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    return await render_project_page(
        request,
        "dashboard/monitoring/create.html",
        {
            "project": project,
            "interval_units": INTERVAL_UNITS,
            "interval_unit_labels": INTERVAL_UNIT_LABELS,
            "error": None,
            "active_module": "monitoring",
            "form_data": None,
        },
        db=db,
    )


@router.post(
    "/{slug}/monitoring/create", response_class=HTMLResponse, response_model=None
)
async def monitor_create(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    form = await request.form()
    url = str(form.get("url", "")).strip()
    name = str(form.get("name", "")).strip() or None
    interval_value_raw = str(form.get("interval_value", "5")).strip()
    interval_unit = str(form.get("interval_unit", "minutes")).strip()

    error = None

    # Walidacja URL
    if not url:
        error = "URL jest wymagany"
    elif not url.startswith(("http://", "https://")):
        error = "URL musi zaczynac sie od http:// lub https://"
    else:
        ssrf_error = _is_url_safe(url)
        if ssrf_error:
            error = ssrf_error

    # Walidacja interwalu
    try:
        interval_value = int(interval_value_raw)
        if interval_value < 1 or interval_value > 60:
            error = "Interwal musi byc miedzy 1 a 60"
    except ValueError:
        interval_value = 5
        error = "Interwal musi byc liczba calkowita"

    if interval_unit not in INTERVAL_UNITS:
        interval_unit = "minutes"
        error = "Nieprawidlowa jednostka interwalu"

    # Limit monitorow na projekt
    if error is None:
        count_result = await db.execute(
            select(func.count(Monitor.id)).where(Monitor.project_id == project.id)
        )
        monitor_count = count_result.scalar() or 0
        if monitor_count >= MAX_MONITORS_PER_PROJECT:
            error = f"Osiagnieto limit {MAX_MONITORS_PER_PROJECT} monitorow na projekt"

    if error:
        return templates.TemplateResponse(
            request,
            "dashboard/monitoring/create.html",
            {
                "project": project,
                "interval_units": INTERVAL_UNITS,
                "interval_unit_labels": INTERVAL_UNIT_LABELS,
                "error": error,
                "active_module": "monitoring",
                "form_data": {
                    "url": url,
                    "name": name or "",
                    "interval_value": interval_value,
                    "interval_unit": interval_unit,
                },
            },
        )

    monitor = Monitor(
        project_id=project.id,
        url=url,
        name=name,
        interval_value=interval_value,
        interval_unit=interval_unit,
    )
    db.add(monitor)
    await db.commit()

    flash(request, "Monitor zostal utworzony")
    return RedirectResponse(url=f"/dashboard/{slug}/monitoring/", status_code=303)


# --- Szczegoly monitora ---


async def _compute_uptime(
    monitor_id: uuid.UUID, days: int, db: AsyncSession
) -> float | None:
    """Oblicz uptime (%) monitora z ostatnich N dni."""
    since = datetime.now(UTC) - timedelta(days=days)
    result = await db.execute(
        select(
            func.count(MonitorCheck.id),
            func.count(case((MonitorCheck.is_success.is_(True), 1))),
        ).where(
            MonitorCheck.monitor_id == monitor_id,
            MonitorCheck.checked_at >= since,
        )
    )
    row = result.one()
    total, success = int(row[0]), int(row[1])
    if total == 0:
        return None
    return round((success / total) * 100, 1)


async def _compute_avg_response_time(
    monitor_id: uuid.UUID, db: AsyncSession
) -> int | None:
    """Sredni czas odpowiedzi (ms) z ostatnich 24h."""
    since = datetime.now(UTC) - timedelta(hours=24)
    result = await db.execute(
        select(func.avg(MonitorCheck.response_time_ms)).where(
            MonitorCheck.monitor_id == monitor_id,
            MonitorCheck.checked_at >= since,
            MonitorCheck.response_time_ms.isnot(None),
        )
    )
    avg = result.scalar()
    if avg is None:
        return None
    return int(avg)


@router.get(
    "/{slug}/monitoring/{monitor_id}",
    response_class=HTMLResponse,
    response_model=None,
)
async def monitor_detail(
    request: Request,
    slug: str,
    monitor_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    monitor = await _get_monitor(monitor_id, project.id, db)
    if monitor is None:
        return HTMLResponse("Monitor not found", status_code=404)

    # Paginacja checkow
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except (ValueError, TypeError):
        page = 1
    per_page = 25

    total_count_result = await db.execute(
        select(func.count(MonitorCheck.id)).where(MonitorCheck.monitor_id == monitor.id)
    )
    total_count = total_count_result.scalar() or 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)

    checks_result = await db.execute(
        select(MonitorCheck)
        .where(MonitorCheck.monitor_id == monitor.id)
        .order_by(MonitorCheck.checked_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    checks = list(checks_result.scalars().all())

    # Statystyki uptime
    uptime_24h = await _compute_uptime(monitor.id, 1, db)
    uptime_7d = await _compute_uptime(monitor.id, 7, db)
    uptime_30d = await _compute_uptime(monitor.id, 30, db)
    avg_response_time = await _compute_avg_response_time(monitor.id, db)

    return await render_project_page(
        request,
        "dashboard/monitoring/detail.html",
        {
            "project": project,
            "monitor": monitor,
            "checks": checks,
            "last_check": checks[0] if checks and page == 1 else None,
            "total_checks": total_count,
            "uptime_24h": uptime_24h,
            "uptime_7d": uptime_7d,
            "uptime_30d": uptime_30d,
            "avg_response_time": avg_response_time,
            "active_module": "monitoring",
            "interval_unit_labels": INTERVAL_UNIT_LABELS,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
        db=db,
    )


# --- Toggle wlacz/wylacz ---


@router.post("/{slug}/monitoring/{monitor_id}/toggle", response_model=None)
async def monitor_toggle(
    request: Request,
    slug: str,
    monitor_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    monitor = await _get_monitor(monitor_id, project.id, db)
    if monitor is None:
        return HTMLResponse("Monitor not found", status_code=404)

    monitor.is_active = not monitor.is_active
    await db.commit()

    status_text = "wlaczony" if monitor.is_active else "wylaczony"
    flash(request, f"Monitor zostal {status_text}")

    referer = request.headers.get("referer")
    if referer:
        return RedirectResponse(url=referer, status_code=303)
    return RedirectResponse(url=f"/dashboard/{slug}/monitoring/", status_code=303)


# --- Usuwanie ---


@router.post("/{slug}/monitoring/{monitor_id}/delete", response_model=None)
async def monitor_delete(
    request: Request,
    slug: str,
    monitor_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if project is None:
        return HTMLResponse("Project not found", status_code=404)

    monitor = await _get_monitor(monitor_id, project.id, db)
    if monitor is None:
        return HTMLResponse("Monitor not found", status_code=404)

    await db.delete(monitor)
    await db.commit()

    flash(request, "Monitor zostal usuniety")
    return RedirectResponse(url=f"/dashboard/{slug}/monitoring/", status_code=303)
