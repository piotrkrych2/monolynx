"""Schematy Pydantic dla modulu Pipelines."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from monolynx.services.pipelines import is_stale


def _duration(started: datetime | None, finished: datetime | None) -> int | None:
    """Oblicza czas trwania w sekundach. None gdy ktorys timestamp brakuje."""
    if started is not None and finished is not None:
        return int((finished - started).total_seconds())
    return None


def build_job_response(job: Any) -> PipelineJobResponse:
    """Buduje PipelineJobResponse z ORM PipelineJob."""
    return PipelineJobResponse(
        id=str(job.id),
        name=job.name,
        agent_type=job.agent_type,
        status=job.status,
        attempt=job.attempt,
        score=job.score,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_seconds=_duration(job.started_at, job.finished_at),
        summary=job.summary,
        wiki_page_id=str(job.wiki_page_id) if job.wiki_page_id is not None else None,
    )


def build_step_response(step: Any) -> PipelineStepResponse:
    """Buduje PipelineStepResponse z ORM PipelineStep (wymaga zaladowanych jobs)."""
    return PipelineStepResponse(
        id=str(step.id),
        name=step.name,
        position=step.position,
        status=step.status,
        started_at=step.started_at,
        finished_at=step.finished_at,
        duration_seconds=_duration(step.started_at, step.finished_at),
        jobs=[build_job_response(j) for j in step.jobs],
    )


def build_pipeline_response(pipeline: Any, ticket_key: str | None = None) -> PipelineResponse:
    """Buduje PipelineResponse z ORM Pipeline (wymaga zaladowanych steps->jobs)."""
    return PipelineResponse(
        id=str(pipeline.id),
        pipeline_type=pipeline.pipeline_type,
        status=pipeline.status,
        ticket_id=str(pipeline.ticket_id) if pipeline.ticket_id is not None else None,
        ticket_key=ticket_key,
        sprint_id=str(pipeline.sprint_id) if pipeline.sprint_id is not None else None,
        branch=pipeline.branch,
        triggered_by=str(pipeline.triggered_by) if pipeline.triggered_by is not None else None,
        created_at=pipeline.created_at,
        started_at=pipeline.started_at,
        finished_at=pipeline.finished_at,
        duration_seconds=_duration(pipeline.started_at, pipeline.finished_at),
        is_stale=is_stale(pipeline),
        meta=pipeline.meta,
        steps=[build_step_response(s) for s in pipeline.steps],
    )


def build_list_item(pipeline: Any, ticket_key: str | None = None) -> PipelineListItem:
    """Buduje PipelineListItem z ORM Pipeline (wymaga zaladowanych steps, BEZ jobs)."""
    return PipelineListItem(
        id=str(pipeline.id),
        pipeline_type=pipeline.pipeline_type,
        status=pipeline.status,
        ticket_id=str(pipeline.ticket_id) if pipeline.ticket_id is not None else None,
        ticket_key=ticket_key,
        branch=pipeline.branch,
        triggered_by=str(pipeline.triggered_by) if pipeline.triggered_by is not None else None,
        created_at=pipeline.created_at,
        started_at=pipeline.started_at,
        finished_at=pipeline.finished_at,
        duration_seconds=_duration(pipeline.started_at, pipeline.finished_at),
        is_stale=is_stale(pipeline),
        step_statuses=[{"name": s.name, "status": s.status} for s in pipeline.steps],
    )


class PipelineJobResponse(BaseModel):
    """Odpowiedz z danymi joba pipeline'u."""

    id: str
    name: str
    agent_type: str
    status: str
    attempt: int
    score: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: int | None = None
    summary: str | None = None
    wiki_page_id: str | None = None


class PipelineStepResponse(BaseModel):
    """Odpowiedz z danymi stepu pipeline'u (zawiera joby)."""

    id: str
    name: str
    position: int
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: int | None = None
    jobs: list[PipelineJobResponse] = []


class PipelineResponse(BaseModel):
    """Pelna odpowiedz z danymi pipeline'u (stepy + joby)."""

    id: str
    pipeline_type: str
    status: str
    ticket_id: str | None = None
    ticket_key: str | None = None
    sprint_id: str | None = None
    branch: str | None = None
    triggered_by: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: int | None = None
    is_stale: bool
    meta: dict[str, Any] = {}
    steps: list[PipelineStepResponse] = []


class PipelineListItem(BaseModel):
    """Lekka odpowiedz do listy pipeline'ow (bez jobów)."""

    id: str
    pipeline_type: str
    status: str
    ticket_id: str | None = None
    ticket_key: str | None = None
    branch: str | None = None
    triggered_by: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: int | None = None
    is_stale: bool
    step_statuses: list[dict[str, str]] = []
