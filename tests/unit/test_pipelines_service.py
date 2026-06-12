"""Testy jednostkowe serwisu pipelines."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from monolynx.constants import PIPELINE_TICKET_WORK_STEPS
from monolynx.models.pipeline import Pipeline, PipelineJob, PipelineStep
from monolynx.models.project import Project
from monolynx.services import pipelines as svc

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(suffix: str = "") -> Project:
    slug = f"pip-svc-{suffix or uuid.uuid4().hex[:6]}"
    return Project(
        name=f"Pipeline Svc {suffix}",
        slug=slug,
        code=("P" + uuid.uuid4().hex[:4]).upper(),
        api_key=uuid.uuid4().hex,
        is_active=True,
    )


async def _flush_project(db_session, suffix: str = "") -> Project:
    project = _make_project(suffix)
    db_session.add(project)
    await db_session.flush()
    return project


# ---------------------------------------------------------------------------
# create_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreatePipeline:
    async def test_ticket_work_creates_three_steps(self, db_session):
        """ticket_work tworzy 3 stepy w statusie pending."""
        project = await _flush_project(db_session, "tw1")

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
        )

        assert pipeline.pipeline_type == "ticket_work"
        assert pipeline.status == "created"
        assert len(pipeline.steps) == 3
        step_names = [s.name for s in pipeline.steps]
        assert step_names == list(PIPELINE_TICKET_WORK_STEPS)
        for step in pipeline.steps:
            assert step.status == "pending"

    async def test_sprint_close_creates_no_steps(self, db_session):
        """sprint_close nie tworzy stepow."""
        project = await _flush_project(db_session, "sc1")

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="sprint_close",
        )

        assert pipeline.pipeline_type == "sprint_close"
        assert len(pipeline.steps) == 0

    async def test_invalid_type_raises_value_error(self, db_session):
        """Zly pipeline_type -> ValueError."""
        project = await _flush_project(db_session, "inv1")

        with pytest.raises(ValueError, match="Nieprawidlowy pipeline_type"):
            await svc.create_pipeline(
                db_session,
                project_id=project.id,
                pipeline_type="invalid_type",
            )

    async def test_optional_fields_stored(self, db_session):
        """branch, triggered_by, meta sa zapisywane."""
        project = await _flush_project(db_session, "opt1")
        meta = {"key": "value"}

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="sprint_close",
            branch="feature/test",
            triggered_by=None,
            meta=meta,
        )

        assert pipeline.branch == "feature/test"
        assert pipeline.meta == meta

    async def test_steps_have_correct_positions(self, db_session):
        """Stepy ticket_work maja position 0, 1, 2."""
        project = await _flush_project(db_session, "pos1")

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
        )

        positions = [s.position for s in pipeline.steps]
        assert positions == [0, 1, 2]


# ---------------------------------------------------------------------------
# create_job
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateJob:
    async def test_create_job_returns_pipeline_job(self, db_session):
        """create_job tworzy job i zwraca PipelineJob."""
        project = await _flush_project(db_session, "cj1")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work")

        job = await svc.create_job(
            db_session,
            pipeline_id=pipeline.id,
            step_name="research",
            name="Research Agent",
            agent_type="researcher",
        )

        assert isinstance(job, PipelineJob)
        assert job.name == "Research Agent"
        assert job.agent_type == "researcher"
        assert job.status == "pending"

    async def test_invalid_step_name_returns_error_str(self, db_session):
        """Zly step_name -> zwraca str z bledem."""
        project = await _flush_project(db_session, "cj2")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work")

        result = await svc.create_job(
            db_session,
            pipeline_id=pipeline.id,
            step_name="nonexistent_step",
            name="Test Job",
            agent_type="test",
        )

        assert isinstance(result, str)
        assert "nonexistent_step" in result

    async def test_job_linked_to_correct_step(self, db_session):
        """Job jest przypisany do wlasciwego stepu."""
        project = await _flush_project(db_session, "cj3")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work")

        job = await svc.create_job(
            db_session,
            pipeline_id=pipeline.id,
            step_name="coding",
            name="Coding Agent",
            agent_type="coder",
        )

        assert isinstance(job, PipelineJob)
        assert job.step.name == "coding"


# ---------------------------------------------------------------------------
# update_job_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateJobById:
    async def _make_job(self, db_session, step_name: str = "research") -> tuple[Pipeline, PipelineJob]:
        """Tworzy pipeline + job dla testow."""
        project = await _flush_project(db_session)
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work")
        job = await svc.create_job(
            db_session,
            pipeline_id=pipeline.id,
            step_name=step_name,
            name="Test Job",
            agent_type="test_agent",
        )
        assert isinstance(job, PipelineJob)
        return pipeline, job

    async def test_running_sets_started_at(self, db_session):
        """Status running -> started_at ustawione."""
        _pipeline, job = await self._make_job(db_session)

        updated = await svc.update_job_by_id(db_session, job.id, status="running")

        assert isinstance(updated, PipelineJob)
        assert updated.status == "running"
        assert updated.started_at is not None

    async def test_running_propagates_to_step_and_pipeline(self, db_session):
        """Status running propaguje sie do stepu i pipeline'u."""
        pipeline, job = await self._make_job(db_session)

        await svc.update_job_by_id(db_session, job.id, status="running")

        # Przeladuj pipeline
        result = await db_session.execute(select(Pipeline).options(selectinload(Pipeline.steps)).where(Pipeline.id == pipeline.id))
        refreshed_pipeline = result.scalar_one()
        assert refreshed_pipeline.status == "running"
        step = next(s for s in refreshed_pipeline.steps if s.name == "research")
        assert step.status == "running"

    async def test_terminal_status_sets_finished_at(self, db_session):
        """Status terminalny -> finished_at ustawione."""
        _pipeline, job = await self._make_job(db_session)

        # Najpierw ustaw running
        await svc.update_job_by_id(db_session, job.id, status="running")
        updated = await svc.update_job_by_id(db_session, job.id, status="success")

        assert isinstance(updated, PipelineJob)
        assert updated.finished_at is not None

    async def test_score_out_of_range_raises_value_error(self, db_session):
        """score poza 0-100 -> ValueError."""
        _pipeline, job = await self._make_job(db_session)

        with pytest.raises(ValueError, match="score musi byc w zakresie"):
            await svc.update_job_by_id(db_session, job.id, score=150)

    async def test_negative_score_raises_value_error(self, db_session):
        """Ujemny score -> ValueError."""
        _pipeline, job = await self._make_job(db_session)

        with pytest.raises(ValueError, match="score musi byc w zakresie"):
            await svc.update_job_by_id(db_session, job.id, score=-1)

    async def test_invalid_status_raises_value_error(self, db_session):
        """Zly status -> ValueError."""
        _pipeline, job = await self._make_job(db_session)

        with pytest.raises(ValueError, match="Nieprawidlowy status joba"):
            await svc.update_job_by_id(db_session, job.id, status="invalid_status")

    async def test_score_attempt_summary_set(self, db_session):
        """score, attempt i summary sa ustawiane."""
        _pipeline, job = await self._make_job(db_session)

        updated = await svc.update_job_by_id(
            db_session,
            job.id,
            score=85,
            attempt=2,
            summary="Zakonczono pomyslnie",
        )

        assert isinstance(updated, PipelineJob)
        assert updated.score == 85
        assert updated.attempt == 2
        assert updated.summary == "Zakonczono pomyslnie"

    async def test_nonexistent_job_returns_error_str(self, db_session):
        """Nieistniejacy job -> str z bledem."""
        result = await svc.update_job_by_id(db_session, uuid.uuid4(), status="running")

        assert isinstance(result, str)
        assert "nie istnieje" in result


# ---------------------------------------------------------------------------
# _update_step_status (agregacja)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateStepStatus:
    def _make_step_with_jobs(self, job_statuses: list[str]) -> PipelineStep:
        """Tworzy mock stepu z jobami w podanych statusach."""
        from unittest.mock import MagicMock

        step = MagicMock(spec=PipelineStep)
        step.status = "running"
        step.finished_at = None
        jobs = []
        for s in job_statuses:
            j = MagicMock(spec=PipelineJob)
            j.status = s
            jobs.append(j)
        step.jobs = jobs
        return step

    def test_failed_job_makes_step_failed(self):
        """Jeden failed job -> step failed."""
        step = self._make_step_with_jobs(["success", "failed"])
        now = datetime.now(UTC)

        svc._update_step_status(step, now)

        assert step.status == "failed"
        assert step.finished_at == now

    def test_all_success_makes_step_success(self):
        """Wszystkie success -> step success."""
        step = self._make_step_with_jobs(["success", "success"])
        now = datetime.now(UTC)

        svc._update_step_status(step, now)

        assert step.status == "success"

    def test_canceled_only_makes_step_canceled(self):
        """Tylko canceled -> step canceled."""
        step = self._make_step_with_jobs(["canceled", "skipped"])
        now = datetime.now(UTC)

        svc._update_step_status(step, now)

        assert step.status == "canceled"

    def test_running_job_keeps_step_running(self):
        """Job running -> step pozostaje running."""
        step = self._make_step_with_jobs(["success", "running"])
        now = datetime.now(UTC)

        svc._update_step_status(step, now)

        assert step.status == "running"

    def test_empty_jobs_no_change(self):
        """Brak jobow -> brak zmian stepu."""
        step = self._make_step_with_jobs([])
        step.status = "pending"
        now = datetime.now(UTC)

        svc._update_step_status(step, now)

        assert step.status == "pending"


# ---------------------------------------------------------------------------
# finish_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFinishPipeline:
    async def test_invalid_project_id_returns_error_str(self, db_session):
        """Zly project_id -> str z bledem."""
        project = await _flush_project(db_session, "fp1")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close")

        other_project_id = uuid.uuid4()
        result = await svc.finish_pipeline(db_session, pipeline.id, project_id=other_project_id)

        assert isinstance(result, str)
        assert "nie nalezy" in result

    async def test_sets_finished_at(self, db_session):
        """finish_pipeline ustawia finished_at."""
        project = await _flush_project(db_session, "fp2")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close")

        result = await svc.finish_pipeline(db_session, pipeline.id, project_id=project.id, status="success")

        assert isinstance(result, Pipeline)
        assert result.finished_at is not None
        assert result.status == "success"

    async def test_nonexistent_pipeline_returns_error_str(self, db_session):
        """Nieistniejacy pipeline -> str z bledem."""
        result = await svc.finish_pipeline(db_session, uuid.uuid4(), project_id=uuid.uuid4())

        assert isinstance(result, str)
        assert "nie istnieje" in result

    async def test_status_calculated_from_steps_failed(self, db_session):
        """Bez status - wylicza ze stepow (failed step -> failed pipeline)."""
        project = await _flush_project(db_session, "fp3")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work")

        # Ustaw jeden step na failed
        step = pipeline.steps[0]
        step.status = "failed"
        await db_session.flush()

        result = await svc.finish_pipeline(db_session, pipeline.id, project_id=project.id)

        assert isinstance(result, Pipeline)
        assert result.status == "failed"

    async def test_status_calculated_from_steps_success(self, db_session):
        """Bez status - wszystkie success -> success pipeline."""
        project = await _flush_project(db_session, "fp4")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work")

        for step in pipeline.steps:
            step.status = "success"
        await db_session.flush()

        result = await svc.finish_pipeline(db_session, pipeline.id, project_id=project.id)

        assert isinstance(result, Pipeline)
        assert result.status == "success"

    async def test_canceled_only_steps_make_pipeline_canceled(self, db_session):
        """Tylko canceled stepy -> canceled pipeline."""
        project = await _flush_project(db_session, "fp5")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work")

        for step in pipeline.steps:
            step.status = "canceled"
        await db_session.flush()

        result = await svc.finish_pipeline(db_session, pipeline.id, project_id=project.id)

        assert isinstance(result, Pipeline)
        assert result.status == "canceled"


# ---------------------------------------------------------------------------
# list_pipelines
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListPipelines:
    async def test_returns_empty_for_new_project(self, db_session):
        """Nowy projekt -> pusta lista."""
        project = await _flush_project(db_session, "lp1")

        items, total = await svc.list_pipelines(db_session, project.id)

        assert items == []
        assert total == 0

    async def test_paginacja(self, db_session):
        """Paginacja zwraca max per_page wynikow."""
        project = await _flush_project(db_session, "lp2")

        for _ in range(3):
            await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close")

        items, total = await svc.list_pipelines(db_session, project.id, per_page=2)

        assert len(items) == 2
        assert total == 3

    async def test_filter_by_status(self, db_session):
        """Filtr status zwraca tylko pasujace pipeline'y."""
        project = await _flush_project(db_session, "lp3")

        p1 = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close")
        await svc.finish_pipeline(db_session, p1.id, project_id=project.id, status="success")

        await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close")

        items, total = await svc.list_pipelines(db_session, project.id, status="success")

        assert total == 1
        assert all(p.status == "success" for p in items)

    async def test_filter_by_pipeline_type(self, db_session):
        """Filtr pipeline_type zwraca tylko pasujace pipeline'y."""
        project = await _flush_project(db_session, "lp4")

        await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close")
        await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work")

        items, total = await svc.list_pipelines(db_session, project.id, pipeline_type="sprint_close")

        assert total == 1
        assert items[0].pipeline_type == "sprint_close"


# ---------------------------------------------------------------------------
# get_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPipeline:
    async def test_returns_pipeline_tree(self, db_session):
        """get_pipeline zwraca pipeline ze stepami i jobami."""
        project = await _flush_project(db_session, "gp1")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work")

        result = await svc.get_pipeline(db_session, pipeline.id, project.id)

        assert result is not None
        assert result.id == pipeline.id
        assert len(result.steps) == 3

    async def test_wrong_project_id_returns_none(self, db_session):
        """Zly project_id -> None."""
        project = await _flush_project(db_session, "gp2")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close")

        result = await svc.get_pipeline(db_session, pipeline.id, uuid.uuid4())

        assert result is None

    async def test_nonexistent_pipeline_returns_none(self, db_session):
        """Nieistniejacy pipeline -> None."""
        project = await _flush_project(db_session, "gp3")

        result = await svc.get_pipeline(db_session, uuid.uuid4(), project.id)

        assert result is None


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsStale:
    def _make_pipeline(self, status: str, started_at: datetime | None) -> Pipeline:
        from unittest.mock import MagicMock

        p = MagicMock(spec=Pipeline)
        p.status = status
        p.started_at = started_at
        p.created_at = datetime.now(UTC) - timedelta(hours=1)
        return p

    def test_running_old_is_stale(self):
        """Running + started_at > 6h -> True."""
        started = datetime.now(UTC) - timedelta(hours=7)
        pipeline = self._make_pipeline("running", started)

        assert svc.is_stale(pipeline) is True

    def test_running_fresh_not_stale(self):
        """Running swiezy -> False."""
        started = datetime.now(UTC) - timedelta(hours=1)
        pipeline = self._make_pipeline("running", started)

        assert svc.is_stale(pipeline) is False

    def test_non_running_not_stale(self):
        """Nie-running -> False niezaleznie od czasu."""
        started = datetime.now(UTC) - timedelta(hours=10)
        pipeline = self._make_pipeline("success", started)

        assert svc.is_stale(pipeline) is False

    def test_running_no_started_at_uses_created_at(self):
        """Running bez started_at -> uzywa created_at."""
        from unittest.mock import MagicMock

        p = MagicMock(spec=Pipeline)
        p.status = "running"
        p.started_at = None
        p.created_at = datetime.now(UTC) - timedelta(hours=8)

        assert svc.is_stale(p) is True

    def test_running_no_started_at_fresh_created_at_not_stale(self):
        """Running bez started_at, swiezy created_at -> False."""
        from unittest.mock import MagicMock

        p = MagicMock(spec=Pipeline)
        p.status = "running"
        p.started_at = None
        p.created_at = datetime.now(UTC) - timedelta(hours=2)

        assert svc.is_stale(p) is False
