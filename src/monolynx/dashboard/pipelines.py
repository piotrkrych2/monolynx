"""Dashboard -- widoki HTML i API JSON modulu Pipelines."""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import (
    PIPELINE_JOB_STATUS_LABELS,
    PIPELINE_STATUS_LABELS,
    PIPELINE_STEP_NAME_LABELS,
    PIPELINE_STEP_STATUS_LABELS,
    PIPELINE_TYPE_LABELS,
    PIPELINE_TYPES,
)
from monolynx.dashboard.helpers import _get_user_id, render_project_page
from monolynx.database import get_db
from monolynx.models.pipeline import Pipeline, PipelineJob, PipelineStep
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.schemas.pipelines import build_list_item, build_pipeline_response
from monolynx.services import pipelines as pipelines_service

router = APIRouter(prefix="/dashboard", tags=["pipelines"])

_PER_PAGE = 20

# Klasy Tailwind per status pipeline'u
_STATUS_CLASSES: dict[str, str] = {
    "created": "bg-gray-700 text-gray-300",
    "running": "bg-blue-700 text-blue-100",
    "success": "bg-green-700 text-green-100",
    "failed": "bg-red-700 text-red-100",
    "canceled": "bg-gray-600 text-gray-400",
}

# Klasy kropek stepow i jobow (te same dla obu)
_STEP_DOT_CLASSES: dict[str, str] = {
    "pending": "bg-gray-500",
    "running": "bg-blue-500 animate-pulse",
    "success": "bg-green-500",
    "failed": "bg-red-500",
    "skipped": "bg-gray-400",
    "canceled": "bg-gray-500",
}

# Klasy badge jobow (bardziej rozbudowane niz dot)
_JOB_STATUS_CLASSES: dict[str, str] = {
    "created": "bg-gray-700 text-gray-300",
    "pending": "bg-gray-700 text-gray-300",
    "running": "bg-blue-700 text-blue-100",
    "success": "bg-green-700 text-green-100",
    "failed": "bg-red-700 text-red-100",
    "skipped": "bg-gray-600 text-gray-400",
    "canceled": "bg-gray-600 text-gray-400",
}


def _duration_str(started: datetime | None, finished: datetime | None) -> str | None:
    """Zwraca czytelny czas trwania (np. '1m 23s') lub None jesli brak danych."""
    if started is None:
        return None
    end = finished if finished is not None else datetime.now(UTC)
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    total = int((end - started).total_seconds())
    if total < 0:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {seconds:02d}s"


def _build_row(
    pipeline: Pipeline,
    ticket_key: str | None,
    ticket_title: str | None = None,
    triggered_by_name: str | None = None,
    sprint_name: str | None = None,
) -> dict[str, object]:
    """Buduje slownik danych wiersza dla szablonu listy pipeline'ow."""
    steps_data = []
    # Iteruj po realnych stepach posortowanych po position
    for step in sorted(pipeline.steps or [], key=lambda s: s.position):
        s_status = step.status
        steps_data.append(
            {
                "name": PIPELINE_STEP_NAME_LABELS.get(step.name, step.name),
                "status": s_status,
                "status_label": PIPELINE_STEP_STATUS_LABELS.get(s_status, s_status),
                "dot_class": _STEP_DOT_CLASSES.get(s_status, "bg-gray-500"),
            }
        )

    started_iso: str | None = None
    if pipeline.started_at is not None:
        started_iso = pipeline.started_at.isoformat()

    return {
        "id": str(pipeline.id),
        "status": pipeline.status,
        "status_label": PIPELINE_STATUS_LABELS.get(pipeline.status, pipeline.status),
        "status_class": _STATUS_CLASSES.get(pipeline.status, "bg-gray-700 text-gray-300"),
        "pipeline_type": pipeline.pipeline_type,
        "type_label": PIPELINE_TYPE_LABELS.get(pipeline.pipeline_type, pipeline.pipeline_type),
        "ticket_key": ticket_key,
        "ticket_title": ticket_title,
        "ticket_id": str(pipeline.ticket_id) if pipeline.ticket_id else None,
        "triggered_by_name": triggered_by_name,
        "branch": pipeline.branch,
        "created_at": pipeline.created_at,
        "started_at": pipeline.started_at,
        "started_iso": started_iso,
        "finished_at": pipeline.finished_at,
        "duration": _duration_str(pipeline.started_at, pipeline.finished_at),
        "is_running": pipeline.status == "running",
        "is_stale": pipelines_service.is_stale(pipeline),
        "steps": steps_data,
        "sprint_name": sprint_name,
    }


def _build_job_data(job: PipelineJob, pipeline_id: str | None = None, project_slug: str | None = None) -> dict[str, object]:
    """Buduje slownik danych joba do uzycia w szablonach."""
    started_iso: str | None = None
    if job.started_at is not None:
        started_iso = job.started_at.isoformat()

    job_url: str | None = None
    if pipeline_id and project_slug:
        job_url = f"/dashboard/{project_slug}/pipelines/{pipeline_id}/jobs/{job.id}"

    return {
        "id": str(job.id),
        "name": job.name,
        "agent_type": job.agent_type,
        "status": job.status,
        "status_label": PIPELINE_JOB_STATUS_LABELS.get(job.status, job.status),
        "status_class": _JOB_STATUS_CLASSES.get(job.status, "bg-gray-700 text-gray-300"),
        "dot_class": _STEP_DOT_CLASSES.get(job.status, "bg-gray-500"),
        "attempt": job.attempt,
        "score": job.score,
        "summary": job.summary,
        "wiki_page_id": str(job.wiki_page_id) if job.wiki_page_id else None,
        "started_at": job.started_at,
        "started_iso": started_iso,
        "finished_at": job.finished_at,
        "duration": _duration_str(job.started_at, job.finished_at),
        "is_running": job.status == "running",
        "url": job_url,
    }


def _build_steps_with_jobs(pipeline: Pipeline, project_slug: str) -> list[dict[str, object]]:
    """Buduje liste stepow z zagniezdzonymi jobami (posortowane po position)."""
    pipeline_id = str(pipeline.id)
    steps_out = []
    for step in sorted(pipeline.steps, key=lambda s: s.position):
        jobs_data = [_build_job_data(j, pipeline_id, project_slug) for j in step.jobs]
        s_status = step.status
        steps_out.append(
            {
                "id": str(step.id),
                "name": step.name,
                "name_label": PIPELINE_STEP_NAME_LABELS.get(step.name, step.name),
                "position": step.position,
                "status": s_status,
                "status_label": PIPELINE_STEP_STATUS_LABELS.get(s_status, s_status),
                "dot_class": _STEP_DOT_CLASSES.get(s_status, "bg-gray-500"),
                "started_at": step.started_at,
                "finished_at": step.finished_at,
                "duration": _duration_str(step.started_at, step.finished_at),
                "jobs": jobs_data,
            }
        )
    return steps_out


async def _get_project_for_member(
    slug: str,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> Project:
    """Zwraca aktywny projekt jezeli user jest jego czlonkiem.

    Raises HTTPException 404 gdy projekt nie istnieje lub 403 gdy brak czlonkostwa.
    """
    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Projekt nie istnieje")

    member_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user_id,
        )
    )
    if member_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Brak dostepu do projektu")

    return project


async def _build_ticket_key_map(
    db: AsyncSession,
    ticket_ids: list[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Zwraca slownik {ticket_id: ticket_key} dla podanej listy ID."""
    if not ticket_ids:
        return {}
    result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id.in_(ticket_ids)))
    return {t.id: t.key for t in result.scalars().all()}


async def _build_ticket_info_map(
    db: AsyncSession,
    ticket_ids: list[uuid.UUID],
) -> dict[uuid.UUID, tuple[str, str]]:
    """Zwraca slownik {ticket_id: (ticket_key, ticket_title)} dla podanej listy ID."""
    if not ticket_ids:
        return {}
    result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id.in_(ticket_ids)))
    return {t.id: (t.key, t.title) for t in result.scalars().all()}


async def _build_user_name_map(
    db: AsyncSession,
    user_ids: list[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Zwraca slownik {user_id: nazwa wyswietlana} (imie nazwisko lub email)."""
    ids = [u for u in user_ids if u is not None]
    if not ids:
        return {}
    result = await db.execute(select(User).where(User.id.in_(ids)))
    out: dict[uuid.UUID, str] = {}
    for u in result.scalars().all():
        full = f"{u.first_name} {u.last_name}".strip()
        out[u.id] = full or u.email
    return out


async def _build_sprint_info_map(
    db: AsyncSession,
    sprint_ids: list[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Zwraca slownik {sprint_id: sprint_name} dla podanej listy ID."""
    if not sprint_ids:
        return {}
    result = await db.execute(select(Sprint).where(Sprint.id.in_(sprint_ids)))
    return {s.id: s.name for s in result.scalars().all()}


@router.get("/{slug}/pipelines/api/list")
async def api_list_pipelines(
    slug: str,
    request: Request,
    status: str | None = Query(default=None),
    pipeline_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Zwraca liste pipeline'ow projektu (JSON, polling HTMX).

    Params: status, pipeline_type, page (domyslnie 1).
    """
    status = status or None
    pipeline_type = pipeline_type or None

    user_id = _get_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    project = await _get_project_for_member(slug, user_id, db)

    items, total = await pipelines_service.list_pipelines(db, project.id, status=status, pipeline_type=pipeline_type, page=page, per_page=_PER_PAGE)

    # Mapa ticket_key dla wyswietlenia
    ticket_ids = [p.ticket_id for p in items if p.ticket_id is not None]
    ticket_key_map = await _build_ticket_key_map(db, ticket_ids)

    total_pages = math.ceil(total / _PER_PAGE) if total > 0 else 1

    def _tkey(tid: uuid.UUID | None) -> str | None:
        return ticket_key_map.get(tid) if tid is not None else None

    return JSONResponse(
        {
            "pipelines": [build_list_item(p, _tkey(p.ticket_id)).model_dump(mode="json") for p in items],
            "total": total,
            "page": page,
            "total_pages": total_pages,
        }
    )


@router.get("/{slug}/pipelines/api/{pipeline_id}")
async def api_get_pipeline(
    slug: str,
    pipeline_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Zwraca pelne drzewo pipeline'u (stepy + joby) jako JSON (polling HTMX)."""
    user_id = _get_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    try:
        pipeline_uuid = uuid.UUID(pipeline_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Nieprawidlowy format pipeline_id") from None

    project = await _get_project_for_member(slug, user_id, db)

    p = await pipelines_service.get_pipeline(db, pipeline_uuid, project.id)
    if p is None:
        raise HTTPException(status_code=404, detail="Pipeline nie istnieje")

    ticket_key: str | None = None
    if p.ticket_id is not None:
        result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id == p.ticket_id))
        ticket = result.scalar_one_or_none()
        if ticket is not None:
            ticket_key = ticket.key

    return JSONResponse(build_pipeline_response(p, ticket_key).model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Widoki HTML
# ---------------------------------------------------------------------------


async def _list_context(
    request: Request,
    slug: str,
    db: AsyncSession,
    status: str | None,
    pipeline_type: str | None,
    page: int,
) -> tuple[dict[str, object], Project] | tuple[None, None]:
    """Wspólna logika dla list i partial list - zwraca (context, project) lub (None, None)."""
    # Puste stringi z query params (np. "Wszystkie" -> ?status=) traktuj jak brak filtra
    status = status or None
    pipeline_type = pipeline_type or None

    user_id = _get_user_id(request)
    if user_id is None:
        return None, None

    project = await _get_project_for_member(slug, user_id, db)

    items, total = await pipelines_service.list_pipelines(db, project.id, status=status, pipeline_type=pipeline_type, page=page, per_page=_PER_PAGE)

    ticket_ids = [p.ticket_id for p in items if p.ticket_id is not None]
    ticket_info_map = await _build_ticket_info_map(db, ticket_ids)
    user_ids = [p.triggered_by for p in items if p.triggered_by is not None]
    user_name_map = await _build_user_name_map(db, user_ids)
    sprint_ids = [p.sprint_id for p in items if p.sprint_id is not None]
    sprint_info_map = await _build_sprint_info_map(db, sprint_ids)

    rows = []
    for p in items:
        info = ticket_info_map.get(p.ticket_id) if p.ticket_id is not None else None
        key = info[0] if info else None
        title = info[1] if info else None
        author = user_name_map.get(p.triggered_by) if p.triggered_by is not None else None
        s_name = sprint_info_map.get(p.sprint_id) if p.sprint_id is not None else None
        rows.append(_build_row(p, key, ticket_title=title, triggered_by_name=author, sprint_name=s_name))

    total_pages = math.ceil(total / _PER_PAGE) if total > 0 else 1
    page = max(1, min(page, total_pages))

    context = {
        "project": project,
        "active_module": "pipelines",
        "rows": rows,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "filter_status": status or "",
        "filter_type": pipeline_type or "",
        "pipeline_status_labels": PIPELINE_STATUS_LABELS,
        "pipeline_type_labels": PIPELINE_TYPE_LABELS,
        "pipeline_types": PIPELINE_TYPES,
    }
    return context, project


@router.get("/{slug}/pipelines/", response_class=HTMLResponse, response_model=None)
async def pipelines_list(
    request: Request,
    slug: str,
    status: str | None = Query(default=None),
    pipeline_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Lista pipeline'ow projektu (widok HTML z pollingiem HTMX)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    try:
        context, _project = await _list_context(request, slug, db, status, pipeline_type, page)
    except HTTPException as exc:
        if exc.status_code == 403:
            return HTMLResponse("Brak dostepu", status_code=403)
        return HTMLResponse("Projekt nie istnieje", status_code=404)

    if context is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    return await render_project_page(request, "dashboard/pipelines/list.html", context, db=db)


@router.get("/{slug}/pipelines/partial/list", response_class=HTMLResponse, response_model=None)
async def pipelines_list_partial(
    request: Request,
    slug: str,
    status: str | None = Query(default=None),
    pipeline_type: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Partial HTML z wierszami listy - do pollingu HTMX (bez layoutu)."""
    user_id = _get_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    try:
        context, _project = await _list_context(request, slug, db, status, pipeline_type, page)
    except HTTPException:
        raise

    if context is None:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    from monolynx.dashboard.helpers import templates

    return templates.TemplateResponse(request, "dashboard/pipelines/_list_rows.html", context)


# ---------------------------------------------------------------------------
# Detail pipeline'u
# ---------------------------------------------------------------------------


async def _pipeline_detail_context(
    slug: str,
    pipeline_uuid: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> tuple[dict[str, object], Pipeline, Project]:
    """Wspólna logika dla pipeline_detail i pipeline_detail_partial.

    Raises HTTPException 403/404.
    """
    project = await _get_project_for_member(slug, user_id, db)

    p = await pipelines_service.get_pipeline(db, pipeline_uuid, project.id)
    if p is None:
        raise HTTPException(status_code=404, detail="Pipeline nie istnieje")

    ticket_key: str | None = None
    ticket_title: str | None = None
    if p.ticket_id is not None:
        t_result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id == p.ticket_id))
        ticket = t_result.scalar_one_or_none()
        if ticket is not None:
            ticket_key = ticket.key
            ticket_title = ticket.title

    sprint_name: str | None = None
    if p.sprint_id is not None:
        # get_pipeline nie eager-loaduje relacji sprint - query bezposrednio (async, brak lazy load)
        s_result = await db.execute(select(Sprint).where(Sprint.id == p.sprint_id))
        sprint_obj = s_result.scalar_one_or_none()
        if sprint_obj is not None:
            sprint_name = sprint_obj.name

    triggered_by_name: str | None = None
    if p.triggered_by is not None:
        name_map = await _build_user_name_map(db, [p.triggered_by])
        triggered_by_name = name_map.get(p.triggered_by)

    started_iso: str | None = None
    if p.started_at is not None:
        started_iso = p.started_at.isoformat()

    steps = _build_steps_with_jobs(p, slug)

    context: dict[str, object] = {
        "project": project,
        "active_module": "pipelines",
        "pipeline_id": str(p.id),
        "pipeline_id_short": str(p.id)[:8],
        "status": p.status,
        "status_label": PIPELINE_STATUS_LABELS.get(p.status, p.status),
        "status_class": _STATUS_CLASSES.get(p.status, "bg-gray-700 text-gray-300"),
        "pipeline_type": p.pipeline_type,
        "type_label": PIPELINE_TYPE_LABELS.get(p.pipeline_type, p.pipeline_type),
        "ticket_key": ticket_key,
        "ticket_title": ticket_title,
        "ticket_id": str(p.ticket_id) if p.ticket_id else None,
        "sprint_name": sprint_name,
        "sprint_id": str(p.sprint_id) if p.sprint_id else None,
        "triggered_by_name": triggered_by_name,
        "branch": p.branch,
        "created_at": p.created_at,
        "started_at": p.started_at,
        "started_iso": started_iso,
        "finished_at": p.finished_at,
        "duration": _duration_str(p.started_at, p.finished_at),
        "is_running": p.status == "running",
        "is_stale": pipelines_service.is_stale(p),
        "steps": steps,
    }
    return context, p, project


@router.get("/{slug}/pipelines/{pipeline_id}/partial/tree", response_class=HTMLResponse, response_model=None)
async def pipeline_detail_partial(
    request: Request,
    slug: str,
    pipeline_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Partial HTML z drzewem stepow/jobow - polling HTMX 15s (bez layoutu)."""
    user_id = _get_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Wymagane logowanie")

    try:
        pipeline_uuid = uuid.UUID(pipeline_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Nieprawidlowy format pipeline_id") from None

    context, _p, _project = await _pipeline_detail_context(slug, pipeline_uuid, user_id, db)

    from monolynx.dashboard.helpers import templates

    return templates.TemplateResponse(request, "dashboard/pipelines/_pipeline_tree.html", context)


@router.get("/{slug}/pipelines/{pipeline_id}", response_class=HTMLResponse, response_model=None)
async def pipeline_detail(
    request: Request,
    slug: str,
    pipeline_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Widok szczegolów pipeline'u z drzewem stepow i jobow."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    try:
        pipeline_uuid = uuid.UUID(pipeline_id)
    except ValueError:
        return HTMLResponse("Nieprawidlowy format pipeline_id", status_code=400)

    try:
        context, _p, _project = await _pipeline_detail_context(slug, pipeline_uuid, user_id, db)
    except HTTPException as exc:
        if exc.status_code == 403:
            return HTMLResponse("Brak dostepu", status_code=403)
        return HTMLResponse("Pipeline nie istnieje", status_code=404)

    return await render_project_page(request, "dashboard/pipelines/detail.html", context, db=db)


# ---------------------------------------------------------------------------
# Detail joba
# ---------------------------------------------------------------------------


@router.get(
    "/{slug}/pipelines/{pipeline_id}/jobs/{job_id}",
    response_class=HTMLResponse,
    response_model=None,
)
async def job_detail(
    request: Request,
    slug: str,
    pipeline_id: str,
    job_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """Widok szczegolów joba: metadane + log markdown z wiki."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    try:
        pipeline_uuid = uuid.UUID(pipeline_id)
        job_uuid = uuid.UUID(job_id)
    except ValueError:
        return HTMLResponse("Nieprawidlowy format ID", status_code=400)

    try:
        project = await _get_project_for_member(slug, user_id, db)
    except HTTPException as exc:
        if exc.status_code == 403:
            return HTMLResponse("Brak dostepu", status_code=403)
        return HTMLResponse("Projekt nie istnieje", status_code=404)

    p = await pipelines_service.get_pipeline(db, pipeline_uuid, project.id)
    if p is None:
        return HTMLResponse("Pipeline nie istnieje", status_code=404)

    # Znajdz job w drzewie (filtrowany przez project_id bo pipeline filtruje)
    found_job: PipelineJob | None = None
    found_step: PipelineStep | None = None
    for step in p.steps:
        for j in step.jobs:
            if j.id == job_uuid:
                found_job = j
                found_step = step
                break
        if found_job is not None:
            break

    if found_job is None:
        return HTMLResponse("Job nie istnieje", status_code=404)

    # Log z wiki
    log_html: str | None = None
    wiki_page_id_str: str | None = None

    if found_job.wiki_page_id is not None:
        wiki_page_id_str = str(found_job.wiki_page_id)
        wiki_result = await db.execute(
            select(WikiPage).where(
                WikiPage.id == found_job.wiki_page_id,
                WikiPage.project_id == project.id,
            )
        )
        wiki_page = wiki_result.scalar_one_or_none()
        if wiki_page is not None:
            from monolynx.services import wiki as wiki_svc

            raw = wiki_svc.get_page_content(wiki_page)
            log_html = wiki_svc.render_markdown_html(raw)

    ticket_key: str | None = None
    if p.ticket_id is not None:
        t_result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id == p.ticket_id))
        ticket = t_result.scalar_one_or_none()
        if ticket is not None:
            ticket_key = ticket.key

    job_data = _build_job_data(found_job, str(p.id), slug)

    started_iso: str | None = None
    if found_job.started_at is not None:
        started_iso = found_job.started_at.isoformat()

    context: dict[str, object] = {
        "project": project,
        "active_module": "pipelines",
        "pipeline_id": str(p.id),
        "pipeline_id_short": str(p.id)[:8],
        "pipeline_status": p.status,
        "pipeline_type_label": PIPELINE_TYPE_LABELS.get(p.pipeline_type, p.pipeline_type),
        "ticket_key": ticket_key,
        "ticket_id": str(p.ticket_id) if p.ticket_id else None,
        "step_name_label": PIPELINE_STEP_NAME_LABELS.get(found_step.name, found_step.name) if found_step else "",
        "job": job_data,
        "log_html": log_html,
        "wiki_page_id": wiki_page_id_str,
        "started_iso": started_iso,
    }

    return await render_project_page(request, "dashboard/pipelines/job_detail.html", context, db=db)
