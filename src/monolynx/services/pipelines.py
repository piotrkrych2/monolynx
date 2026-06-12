"""Serwis pipelines -- logika tworzenia i aktualizacji pipeline'ow agentow."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.constants import (
    PIPELINE_JOB_STATUSES,
    PIPELINE_STATUSES,
    PIPELINE_TICKET_WORK_STEPS,
    PIPELINE_TYPES,
)
from monolynx.models.pipeline import Pipeline, PipelineJob, PipelineStep

if TYPE_CHECKING:
    from monolynx.models.wiki_page import WikiPage

# Statusy terminalne (koniec pracy joba/stepu)
_JOB_TERMINAL = {"success", "failed", "skipped", "canceled"}


async def create_pipeline(
    db: AsyncSession,
    project_id: uuid.UUID,
    pipeline_type: str,
    ticket_id: uuid.UUID | None = None,
    sprint_id: uuid.UUID | None = None,
    branch: str | None = None,
    triggered_by: uuid.UUID | None = None,
    meta: dict[str, Any] | None = None,
) -> Pipeline:
    """Tworzy pipeline z automatycznymi stepami dla ticket_work.

    Raises:
        ValueError: gdy pipeline_type nie jest w PIPELINE_TYPES.
    """
    if pipeline_type not in PIPELINE_TYPES:
        raise ValueError(f"Nieprawidlowy pipeline_type: {pipeline_type!r}. Dozwolone: {PIPELINE_TYPES}")

    pipeline = Pipeline(
        project_id=project_id,
        pipeline_type=pipeline_type,
        ticket_id=ticket_id,
        sprint_id=sprint_id,
        status="created",
        branch=branch,
        triggered_by=triggered_by,
        meta=meta or {},
    )
    db.add(pipeline)
    await db.flush()  # potrzebujemy pipeline.id dla stepow

    if pipeline_type == "ticket_work":
        for position, step_name in enumerate(PIPELINE_TICKET_WORK_STEPS):
            step = PipelineStep(
                pipeline_id=pipeline.id,
                name=step_name,
                position=position,
                status="pending",
            )
            db.add(step)

    await db.commit()

    result = await db.execute(select(Pipeline).options(selectinload(Pipeline.steps)).where(Pipeline.id == pipeline.id))
    return result.scalar_one()


async def create_job(
    db: AsyncSession,
    pipeline_id: uuid.UUID,
    step_name: str,
    name: str,
    agent_type: str,
) -> PipelineJob | str:
    """Tworzy job w podanym stepie (wyszukiwanie po pipeline_id + step_name).

    Zwraca PipelineJob lub str z komunikatem bledu.
    """
    result = await db.execute(
        select(PipelineStep).where(
            PipelineStep.pipeline_id == pipeline_id,
            PipelineStep.name == step_name,
        )
    )
    step = result.scalar_one_or_none()
    if step is None:
        return f"Step {step_name!r} nie istnieje w pipeline {pipeline_id}"

    job = PipelineJob(
        step_id=step.id,
        name=name,
        agent_type=agent_type,
        status="pending",
    )
    db.add(job)
    await db.commit()

    result2 = await db.execute(select(PipelineJob).options(selectinload(PipelineJob.step)).where(PipelineJob.id == job.id))
    return result2.scalar_one()


async def update_job_by_id(
    db: AsyncSession,
    job_id: uuid.UUID,
    status: str | None = None,
    score: int | None = None,
    attempt: int | None = None,
    summary: str | None = None,
) -> PipelineJob | str:
    """Aktualizuje job po ID. Propaguje status do stepu i pipeline'u.

    Zwraca PipelineJob lub str z komunikatem bledu.

    Raises:
        ValueError: gdy status nie jest w PIPELINE_JOB_STATUSES lub score poza 0-100.
    """
    if status is not None and status not in PIPELINE_JOB_STATUSES:
        raise ValueError(f"Nieprawidlowy status joba: {status!r}. Dozwolone: {PIPELINE_JOB_STATUSES}")
    if score is not None and not (0 <= score <= 100):
        raise ValueError(f"score musi byc w zakresie 0-100, podano: {score}")

    result = await db.execute(
        select(PipelineJob)
        .options(
            selectinload(PipelineJob.step).selectinload(PipelineStep.pipeline),
            selectinload(PipelineJob.step).selectinload(PipelineStep.jobs),
        )
        .where(PipelineJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return f"Job {job_id} nie istnieje"

    now = datetime.now(UTC)

    if status is not None:
        job.status = status

        if status == "running":
            if job.started_at is None:
                job.started_at = now
            # Propaguj do stepu
            step = job.step
            if step.status != "running":
                step.status = "running"
                if step.started_at is None:
                    step.started_at = now
            # Propaguj do pipeline'u
            pipeline = step.pipeline
            if pipeline.status not in ("running", "success", "failed", "canceled"):
                pipeline.status = "running"
                if pipeline.started_at is None:
                    pipeline.started_at = now

        elif status in _JOB_TERMINAL:
            if job.finished_at is None:
                job.finished_at = now
            # Przelicz status stepu na podstawie wszystkich jobow
            _update_step_status(job.step, now)

    if score is not None:
        job.score = score
    if attempt is not None:
        job.attempt = attempt
    if summary is not None:
        job.summary = summary

    await db.commit()

    # Przeladuj po commicie
    result2 = await db.execute(
        select(PipelineJob)
        .options(
            selectinload(PipelineJob.step).selectinload(PipelineStep.pipeline),
        )
        .where(PipelineJob.id == job_id)
    )
    return result2.scalar_one()


def _update_step_status(step: PipelineStep, now: datetime) -> None:
    """Przelicza status stepu na podstawie stanu jego jobow.

    Helper wywoływany przed db.commit() - nie commituje samodzielnie.
    Wymaga zaladowanych step.jobs.
    """
    jobs = step.jobs
    if not jobs:
        return

    statuses = {j.status for j in jobs}

    if statuses <= _JOB_TERMINAL:
        # Wszystkie joby terminalne
        if "failed" in statuses:
            step.status = "failed"
        elif "canceled" in statuses and statuses <= {"canceled", "skipped"}:
            step.status = "canceled"
        else:
            step.status = "success"
        if step.finished_at is None:
            step.finished_at = now
    elif "running" in statuses:
        step.status = "running"
    # inaczej zostaje "pending"


async def finish_pipeline(
    db: AsyncSession,
    pipeline_id: uuid.UUID,
    project_id: uuid.UUID,
    status: str | None = None,
) -> Pipeline | str:
    """Zamyka pipeline: ustawia status i finished_at.

    Jesli status nie podany, wylicza ze stepow.
    Zwraca Pipeline lub str z komunikatem bledu.

    Raises:
        ValueError: gdy status nie jest w PIPELINE_STATUSES.
    """
    if status is not None and status not in PIPELINE_STATUSES:
        raise ValueError(f"Nieprawidlowy status pipeline'u: {status!r}. Dozwolone: {PIPELINE_STATUSES}")

    result = await db.execute(
        select(Pipeline).options(selectinload(Pipeline.steps).selectinload(PipelineStep.jobs)).where(Pipeline.id == pipeline_id)
    )
    pipeline = result.scalar_one_or_none()
    if pipeline is None:
        return f"Pipeline {pipeline_id} nie istnieje"
    if pipeline.project_id != project_id:
        return "Pipeline nie nalezy do tego projektu"

    now = datetime.now(UTC)

    if status is not None:
        pipeline.status = status
    else:
        # Wylicz ze stepow
        step_statuses = {s.status for s in pipeline.steps}
        if "failed" in step_statuses:
            pipeline.status = "failed"
        elif step_statuses and step_statuses <= {"canceled", "skipped"}:
            pipeline.status = "canceled"
        else:
            pipeline.status = "success"

    pipeline.finished_at = now
    await db.commit()

    result2 = await db.execute(
        select(Pipeline).options(selectinload(Pipeline.steps).selectinload(PipelineStep.jobs)).where(Pipeline.id == pipeline_id)
    )
    return result2.scalar_one()


async def list_pipelines(
    db: AsyncSession,
    project_id: uuid.UUID,
    status: str | None = None,
    pipeline_type: str | None = None,
    page: int = 1,
    per_page: int = 20,
) -> tuple[list[Pipeline], int]:
    """Zwraca liste pipeline'ow z paginacja.

    Returns:
        (items, total) - lista pipeline'ow i calkowita liczba wynikow.
    """
    base_where = [Pipeline.project_id == project_id]
    if status is not None:
        base_where.append(Pipeline.status == status)
    if pipeline_type is not None:
        base_where.append(Pipeline.pipeline_type == pipeline_type)

    count_result = await db.execute(select(func.count()).select_from(Pipeline).where(*base_where))
    total: int = count_result.scalar_one()

    offset = (page - 1) * per_page
    items_result = await db.execute(
        select(Pipeline).options(selectinload(Pipeline.steps)).where(*base_where).order_by(Pipeline.created_at.desc()).limit(per_page).offset(offset)
    )
    items = list(items_result.scalars().all())

    return items, total


async def get_pipeline(
    db: AsyncSession,
    pipeline_id: uuid.UUID,
    project_id: uuid.UUID,
) -> Pipeline | None:
    """Zwraca pelne drzewo pipeline'u (stepy + joby) lub None jesli nie znaleziono."""
    result = await db.execute(
        select(Pipeline)
        .options(selectinload(Pipeline.steps).selectinload(PipelineStep.jobs))
        .where(
            Pipeline.id == pipeline_id,
            Pipeline.project_id == project_id,
        )
    )
    return result.scalar_one_or_none()


def is_stale(pipeline: Pipeline) -> bool:
    """Zwraca True gdy pipeline jest w statusie 'running' i nie byl aktualizowany od >6h."""
    if pipeline.status != "running":
        return False
    reference = pipeline.started_at if pipeline.started_at is not None else pipeline.created_at
    if reference is None:
        return False
    # Upewnij sie ze reference jest timezone-aware
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return (datetime.now(UTC) - reference).total_seconds() > 6 * 3600


# ---------------------------------------------------------------------------
# Integracja z wiki -- logi jobow
# ---------------------------------------------------------------------------


async def ensure_pipeline_logs_parent(
    db: AsyncSession,
    project: Any,
    user_id: uuid.UUID,
) -> Any:
    """Idempotentnie zwraca stronę-rodzica logów pipeline'ów (slug: pipeline-logi).

    Tworzy stronę jeśli nie istnieje. Strona jest wykluczona z embeddingów RAG.
    """
    from monolynx.models.wiki_page import WikiPage
    from monolynx.services import wiki as wiki_svc

    result = await db.execute(
        select(WikiPage).where(
            WikiPage.project_id == project.id,
            WikiPage.slug == "pipeline-logi",
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    # generate_slug("Pipeline logi") -> "pipeline-logi"
    return await wiki_svc.create_wiki_page(
        project_id=project.id,
        project_slug=project.slug,
        title="Pipeline logi",
        content="# Pipeline logi\n\nLogi jobów pipeline'ów (automatyczne).\n",
        user_id=user_id,
        is_ai=True,
        exclude_from_embeddings=True,
        db=db,
    )


async def append_job_log(
    db: AsyncSession,
    job_id: uuid.UUID,
    content: str,
    user_id: uuid.UUID,
) -> WikiPage | str:
    """Tworzy lub dopisuje treść do strony wiki logu joba.

    Pierwsze wywołanie: tworzy stronę pod pipeline-logi, ustawia job.wiki_page_id.
    Kolejne: doklejają do istniejącej strony separator + znacznik czasu + content.

    Zwraca WikiPage lub str z komunikatem błędu.
    """
    from monolynx.models.ticket import Ticket
    from monolynx.models.wiki_page import WikiPage
    from monolynx.services import wiki as wiki_svc

    # Laduj job z pelnym drzewem: step -> pipeline -> project
    result = await db.execute(
        select(PipelineJob)
        .options(selectinload(PipelineJob.step).selectinload(PipelineStep.pipeline).selectinload(Pipeline.project))
        .where(PipelineJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        return f"Job {job_id} nie istnieje"

    pipeline = job.step.pipeline
    project = pipeline.project

    # Zbuduj ticket_key do tytulu strony
    ticket_key: str | None = None
    if pipeline.ticket_id is not None:
        ticket_result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id == pipeline.ticket_id))
        ticket = ticket_result.scalar_one_or_none()
        if ticket is not None:
            ticket_key = ticket.key

    if ticket_key:
        page_title = f"Pipeline {ticket_key} - {job.name}"
    else:
        short_id = str(pipeline.id)[:8]
        page_title = f"Pipeline {short_id} - {job.name}"

    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    if job.wiki_page_id is None:
        # Pierwsza wizyta - tworz nowa strone
        parent = await ensure_pipeline_logs_parent(db, project, user_id)
        page = await wiki_svc.create_wiki_page(
            project_id=project.id,
            project_slug=project.slug,
            title=page_title,
            content=content,
            user_id=user_id,
            parent_id=parent.id,
            is_ai=True,
            exclude_from_embeddings=True,
            db=db,
        )
        # create_wiki_page wywoluje sync_backlinks wewnatrz - OK
        job.wiki_page_id = page.id
        await db.commit()
        return page

    # Kolejna wizyta - doklejaj do istniejącej strony
    page_result = await db.execute(select(WikiPage).where(WikiPage.id == job.wiki_page_id))
    existing_page = page_result.scalar_one_or_none()
    if existing_page is None:
        return f"Strona wiki {job.wiki_page_id} nie istnieje"

    current_content = wiki_svc.get_page_content(existing_page)
    updated_content = current_content.rstrip("\n") + f"\n\n---\n\n**[{now_str}]**\n\n{content}"

    await wiki_svc.update_wiki_page(
        page=existing_page,
        project_slug=project.slug,
        content=updated_content,
        user_id=user_id,
        is_ai=True,
        db=db,
    )
    # update_wiki_page wywoluje sync_backlinks gdy content sie zmienil - OK
    return existing_page


async def maybe_log_pipeline_to_wiki_log(
    db: AsyncSession,
    pipeline: Pipeline,
    user_id: uuid.UUID,
) -> None:
    """Dopisuje wpis do dziennika wiki-log po zakończeniu pipeline'u.

    Best-effort: jeśli LLM Wiki nie jest włączone dla projektu, nie robi nic.
    Wymaga załadowanego pipeline.project (przez selectinload).
    """
    from monolynx.services import wiki as wiki_svc

    project = pipeline.project
    if not wiki_svc.is_wiki_llm_enabled(project):
        return

    short_id = str(pipeline.id)[:8]
    entry = f"Pipeline {pipeline.pipeline_type} {short_id} - status {pipeline.status}"
    await wiki_svc.append_log(project=project, entry=entry, user_id=user_id, db=db)
