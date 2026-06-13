"""Testy jednostkowe serwisu pipelines."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from monolynx.constants import PIPELINE_SPRINT_CLOSE_STEPS, PIPELINE_TICKET_WORK_STEPS
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


async def _flush_ticket(db_session, project_id: uuid.UUID, number: int = 1, sprint_id: uuid.UUID | None = None):
    """Tworzy minimalny ticket w DB i zwraca jego id."""
    from monolynx.models.ticket import Ticket

    ticket = Ticket(
        project_id=project_id,
        number=number,
        title="Test Ticket",
        status="backlog",
        sprint_id=sprint_id,
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _flush_sprint(db_session, project_id: uuid.UUID, name: str = "Sprint testowy"):
    """Tworzy minimalny sprint w DB i zwraca obiekt."""
    from monolynx.models.sprint import Sprint

    sprint = Sprint(
        project_id=project_id,
        name=name,
        start_date=date.today(),
        status="planning",
    )
    db_session.add(sprint)
    await db_session.flush()
    return sprint


async def _make_user(db_session, suffix: str):
    """Tworzy minimalnego uzytkownika w DB."""
    from monolynx.models.user import User
    from monolynx.services.auth import hash_password

    user = User(
        email=f"pipe-test-{suffix}@example.com",
        password_hash=hash_password("test"),
        is_superuser=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


# ---------------------------------------------------------------------------
# create_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreatePipeline:
    async def test_ticket_work_creates_three_steps(self, db_session):
        """ticket_work tworzy 3 stepy w statusie pending."""
        project = await _flush_project(db_session, "tw1")
        ticket = await _flush_ticket(db_session, project.id, number=1)

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
            ticket_id=ticket.id,
        )

        assert pipeline.pipeline_type == "ticket_work"
        assert pipeline.status == "created"
        assert len(pipeline.steps) == 3
        step_names = [s.name for s in pipeline.steps]
        assert step_names == list(PIPELINE_TICKET_WORK_STEPS)
        for step in pipeline.steps:
            assert step.status == "pending"

    async def test_sprint_close_seeds_two_steps(self, db_session):
        """sprint_close seeduje 2 stepy: wiki-update i wrap-up w statusie pending."""
        project = await _flush_project(db_session, "sc1")
        sprint = await _flush_sprint(db_session, project.id)

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="sprint_close",
            sprint_id=sprint.id,
        )

        assert pipeline.pipeline_type == "sprint_close"
        assert len(pipeline.steps) == 2
        step_names = [s.name for s in pipeline.steps]
        assert step_names == ["wiki-update", "wrap-up"]
        assert pipeline.steps[0].position == 0
        assert pipeline.steps[1].position == 1
        for step in pipeline.steps:
            assert step.status == "pending"

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
        sprint = await _flush_sprint(db_session, project.id, name="Sprint opt1")
        meta = {"key": "value"}

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="sprint_close",
            sprint_id=sprint.id,
            branch="feature/test",
            triggered_by=None,
            meta=meta,
        )

        assert pipeline.branch == "feature/test"
        assert pipeline.meta == meta

    async def test_steps_have_correct_positions(self, db_session):
        """Stepy ticket_work maja position 0, 1, 2."""
        project = await _flush_project(db_session, "pos1")
        ticket = await _flush_ticket(db_session, project.id, number=2)

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
            ticket_id=ticket.id,
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
        ticket = await _flush_ticket(db_session, project.id, number=3)
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work", ticket_id=ticket.id)

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
        ticket = await _flush_ticket(db_session, project.id, number=4)
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work", ticket_id=ticket.id)

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
        ticket = await _flush_ticket(db_session, project.id, number=5)
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work", ticket_id=ticket.id)

        job = await svc.create_job(
            db_session,
            pipeline_id=pipeline.id,
            step_name="coding",
            name="Coding Agent",
            agent_type="coder",
        )

        assert isinstance(job, PipelineJob)
        assert job.step.name == "coding"

    async def test_create_job_in_sprint_close_step(self, db_session):
        """Regresja: job w stepie sprint_close (wiki-update) tworzy sie poprawnie.

        Pilnuje, by walidacja stepu nie byla zawezona do stepow ticket_work
        (skill sprint-end tworzy joby w stepie wiki-update).
        """
        project = await _flush_project(db_session, "cj4")
        sprint = await _flush_sprint(db_session, project.id)
        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="sprint_close",
            sprint_id=sprint.id,
        )

        job = await svc.create_job(
            db_session,
            pipeline_id=pipeline.id,
            step_name="wiki-update",
            name="wiki-ingest",
            agent_type="skill",
        )

        assert isinstance(job, PipelineJob)
        assert job.step.name == "wiki-update"


# ---------------------------------------------------------------------------
# update_job_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateJobById:
    async def _make_job(self, db_session, step_name: str = "research") -> tuple[Pipeline, PipelineJob]:
        """Tworzy pipeline + job dla testow."""
        project = await _flush_project(db_session)
        ticket = await _flush_ticket(db_session, project.id, number=10)
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work", ticket_id=ticket.id)
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
        sprint = await _flush_sprint(db_session, project.id, name="Sprint fp1")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close", sprint_id=sprint.id)

        other_project_id = uuid.uuid4()
        result = await svc.finish_pipeline(db_session, pipeline.id, project_id=other_project_id)

        assert isinstance(result, str)
        assert "nie nalezy" in result

    async def test_sets_finished_at(self, db_session):
        """finish_pipeline ustawia finished_at."""
        project = await _flush_project(db_session, "fp2")
        sprint = await _flush_sprint(db_session, project.id, name="Sprint fp2")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close", sprint_id=sprint.id)

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
        ticket = await _flush_ticket(db_session, project.id, number=20)
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work", ticket_id=ticket.id)

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
        ticket = await _flush_ticket(db_session, project.id, number=21)
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work", ticket_id=ticket.id)

        for step in pipeline.steps:
            step.status = "success"
        await db_session.flush()

        result = await svc.finish_pipeline(db_session, pipeline.id, project_id=project.id)

        assert isinstance(result, Pipeline)
        assert result.status == "success"

    async def test_canceled_only_steps_make_pipeline_canceled(self, db_session):
        """Tylko canceled stepy -> canceled pipeline."""
        project = await _flush_project(db_session, "fp5")
        ticket = await _flush_ticket(db_session, project.id, number=22)
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work", ticket_id=ticket.id)

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
        sprint = await _flush_sprint(db_session, project.id, name="Sprint lp2")

        for _ in range(3):
            await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close", sprint_id=sprint.id)

        items, total = await svc.list_pipelines(db_session, project.id, per_page=2)

        assert len(items) == 2
        assert total == 3

    async def test_filter_by_status(self, db_session):
        """Filtr status zwraca tylko pasujace pipeline'y."""
        project = await _flush_project(db_session, "lp3")
        sprint = await _flush_sprint(db_session, project.id, name="Sprint lp3")

        p1 = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close", sprint_id=sprint.id)
        await svc.finish_pipeline(db_session, p1.id, project_id=project.id, status="success")

        await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close", sprint_id=sprint.id)

        items, total = await svc.list_pipelines(db_session, project.id, status="success")

        assert total == 1
        assert all(p.status == "success" for p in items)

    async def test_filter_by_pipeline_type(self, db_session):
        """Filtr pipeline_type zwraca tylko pasujace pipeline'y."""
        project = await _flush_project(db_session, "lp4")
        sprint = await _flush_sprint(db_session, project.id, name="Sprint lp4")
        ticket = await _flush_ticket(db_session, project.id, number=30)

        await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close", sprint_id=sprint.id)
        await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work", ticket_id=ticket.id)

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
        ticket = await _flush_ticket(db_session, project.id, number=40)
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="ticket_work", ticket_id=ticket.id)

        result = await svc.get_pipeline(db_session, pipeline.id, project.id)

        assert result is not None
        assert result.id == pipeline.id
        assert len(result.steps) == 3

    async def test_wrong_project_id_returns_none(self, db_session):
        """Zly project_id -> None."""
        project = await _flush_project(db_session, "gp2")
        sprint = await _flush_sprint(db_session, project.id, name="Sprint gp2")
        pipeline = await svc.create_pipeline(db_session, project_id=project.id, pipeline_type="sprint_close", sprint_id=sprint.id)

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


# ---------------------------------------------------------------------------
# MON-98: sprint_close stepy, walidacja XOR, clean_pipeline_logs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreatePipelineSprintClose:
    """B: Regresja ticket_work + nowe testy sprint_close + C: walidacja XOR."""

    async def test_ticket_work_still_seeds_three_steps(self, db_session):
        """Regresja: ticket_work nadal seeduje research/coding/wrap-up."""
        project = await _flush_project(db_session, "mon98tw1")
        ticket = await _flush_ticket(db_session, project.id, number=50)

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
            ticket_id=ticket.id,
        )

        assert len(pipeline.steps) == 3
        step_names = [s.name for s in pipeline.steps]
        assert step_names == list(PIPELINE_TICKET_WORK_STEPS)
        assert step_names == ["research", "coding", "wrap-up"]
        for step in pipeline.steps:
            assert step.status == "pending"

    async def test_ticket_work_step_positions_unchanged(self, db_session):
        """Regresja: pozycje stepow ticket_work sa 0,1,2."""
        project = await _flush_project(db_session, "mon98tw2")
        ticket = await _flush_ticket(db_session, project.id, number=51)

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
            ticket_id=ticket.id,
        )

        positions = [s.position for s in pipeline.steps]
        assert positions == [0, 1, 2]

    async def test_sprint_close_step_names_match_constant(self, db_session):
        """sprint_close stepy odpowiadaja PIPELINE_SPRINT_CLOSE_STEPS."""
        project = await _flush_project(db_session, "mon98sc2")
        sprint = await _flush_sprint(db_session, project.id, name="Sprint mon98sc2")

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="sprint_close",
            sprint_id=sprint.id,
        )

        step_names = [s.name for s in pipeline.steps]
        assert step_names == list(PIPELINE_SPRINT_CLOSE_STEPS)

    async def test_sprint_close_step_name_label_wiki_update(self):
        """PIPELINE_STEP_NAME_LABELS zawiera 'wiki-update' -> 'Aktualizacja Wiki'."""
        from monolynx.constants import PIPELINE_STEP_NAME_LABELS

        assert "wiki-update" in PIPELINE_STEP_NAME_LABELS
        assert PIPELINE_STEP_NAME_LABELS["wiki-update"] == "Aktualizacja Wiki"

    # -- XOR: ticket_work --

    async def test_ticket_work_with_sprint_id_raises_value_error(self, db_session):
        """XOR: ticket_work + sprint_id -> ValueError (forbids sprint_id)."""
        project = await _flush_project(db_session, "mon98xor1")
        ticket = await _flush_ticket(db_session, project.id, number=60)

        with pytest.raises(ValueError, match="forbids sprint_id"):
            await svc.create_pipeline(
                db_session,
                project_id=project.id,
                pipeline_type="ticket_work",
                ticket_id=ticket.id,
                sprint_id=uuid.uuid4(),
            )

    async def test_ticket_work_without_ticket_id_raises_value_error(self, db_session):
        """XOR: ticket_work bez ticket_id -> ValueError (requires ticket_id)."""
        project = await _flush_project(db_session, "mon98xor3")

        with pytest.raises(ValueError, match="requires ticket_id"):
            await svc.create_pipeline(
                db_session,
                project_id=project.id,
                pipeline_type="ticket_work",
                ticket_id=None,
                sprint_id=None,
            )

    # -- XOR: sprint_close --

    async def test_sprint_close_with_ticket_id_raises_value_error(self, db_session):
        """XOR: sprint_close + ticket_id -> ValueError (forbids ticket_id)."""
        project = await _flush_project(db_session, "mon98xor2")
        sprint = await _flush_sprint(db_session, project.id, name="Sprint xor2")

        with pytest.raises(ValueError, match="forbids ticket_id"):
            await svc.create_pipeline(
                db_session,
                project_id=project.id,
                pipeline_type="sprint_close",
                sprint_id=sprint.id,
                ticket_id=uuid.uuid4(),
            )

    async def test_sprint_close_without_sprint_id_raises_value_error(self, db_session):
        """XOR: sprint_close bez sprint_id -> ValueError (requires sprint_id)."""
        project = await _flush_project(db_session, "mon98xor4")

        with pytest.raises(ValueError, match="requires sprint_id"):
            await svc.create_pipeline(
                db_session,
                project_id=project.id,
                pipeline_type="sprint_close",
                sprint_id=None,
            )

    async def test_sprint_close_with_sprint_id_succeeds(self, db_session):
        """sprint_close + sprint_id -> sukces."""
        project = await _flush_project(db_session, "mon98xor5")
        sprint = await _flush_sprint(db_session, project.id, name="Sprint xor5")

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="sprint_close",
            sprint_id=sprint.id,
        )

        assert pipeline.sprint_id == sprint.id
        assert pipeline.pipeline_type == "sprint_close"

    async def test_ticket_work_with_ticket_id_succeeds(self, db_session):
        """ticket_work + ticket_id -> sukces."""
        project = await _flush_project(db_session, "mon98xor6")
        ticket = await _flush_ticket(db_session, project.id, number=70)

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
            ticket_id=ticket.id,
        )

        assert pipeline.ticket_id == ticket.id
        assert pipeline.pipeline_type == "ticket_work"


@pytest.mark.unit
class TestCleanPipelineLogs:
    """D: clean_pipeline_logs_for_sprint."""

    async def _make_wiki_page(self, db_session, project, user, slug_suffix: str):
        """Tworzy minimalna strone wiki dla testu."""
        from monolynx.models.wiki_page import WikiPage

        page = WikiPage(
            project_id=project.id,
            title=f"Pipeline Log {slug_suffix}",
            slug=f"pipeline-log-{slug_suffix}",
            minio_path=f"wiki/{project.id}/pipeline-log-{slug_suffix}.md",
            created_by_id=user.id,
            last_edited_by_id=user.id,
            exclude_from_embeddings=True,
        )
        db_session.add(page)
        await db_session.flush()
        return page

    @patch("monolynx.services.wiki.delete_object")
    async def test_deletes_wiki_pages_for_sprint_jobs(self, mock_delete_object, db_session):
        """clean_pipeline_logs_for_sprint usuwa strony wiki jobow powiazanych ze sprintem."""
        from monolynx.models.wiki_page import WikiPage

        project = await _flush_project(db_session, "cpl1")
        user = await _make_user(db_session, "cpl1")
        sprint = await _flush_sprint(db_session, project.id, name="Sprint CLP1")
        ticket = await _flush_ticket(db_session, project.id, number=9901, sprint_id=sprint.id)

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
            ticket_id=ticket.id,
        )
        step = pipeline.steps[0]

        wiki_page = await self._make_wiki_page(db_session, project, user, "cpl1a")
        wiki_page_id = wiki_page.id

        job = PipelineJob(
            step_id=step.id,
            name="Test Job CLP1",
            agent_type="test",
            status="success",
            wiki_page_id=wiki_page.id,
        )
        db_session.add(job)
        await db_session.flush()

        deleted_count = await svc.clean_pipeline_logs_for_sprint(db_session, project_id=project.id, sprint_id=sprint.id)

        assert deleted_count >= 1
        mock_delete_object.assert_called()

        result = await db_session.execute(select(WikiPage).where(WikiPage.id == wiki_page_id))
        assert result.scalar_one_or_none() is None

    async def test_job_without_wiki_page_not_counted(self, db_session):
        """Job bez wiki_page_id nie jest liczony przez clean_pipeline_logs."""
        project = await _flush_project(db_session, "cpl2")
        sprint = await _flush_sprint(db_session, project.id, name="Sprint CLP2")
        ticket = await _flush_ticket(db_session, project.id, number=9902, sprint_id=sprint.id)

        pipeline = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
            ticket_id=ticket.id,
        )
        step = pipeline.steps[0]

        job = PipelineJob(
            step_id=step.id,
            name="Job bez wiki",
            agent_type="test",
            status="success",
            wiki_page_id=None,
        )
        db_session.add(job)
        await db_session.flush()

        deleted_count = await svc.clean_pipeline_logs_for_sprint(db_session, project_id=project.id, sprint_id=sprint.id)

        assert deleted_count == 0

    @patch("monolynx.services.wiki.delete_object")
    async def test_pipeline_from_other_sprint_not_touched(self, mock_delete_object, db_session):
        """Joby z innego sprintu nie sa usuwane."""
        from monolynx.models.wiki_page import WikiPage

        project = await _flush_project(db_session, "cpl3")
        user = await _make_user(db_session, "cpl3")

        sprint_a = await _flush_sprint(db_session, project.id, name="Sprint A cpl3")
        sprint_b = await _flush_sprint(db_session, project.id, name="Sprint B cpl3")

        ticket_b = await _flush_ticket(db_session, project.id, number=9903, sprint_id=sprint_b.id)

        pipeline_b = await svc.create_pipeline(
            db_session,
            project_id=project.id,
            pipeline_type="ticket_work",
            ticket_id=ticket_b.id,
        )
        step_b = pipeline_b.steps[0]

        wiki_page_b = await self._make_wiki_page(db_session, project, user, "cpl3b")
        wiki_page_b_id = wiki_page_b.id

        job_b = PipelineJob(
            step_id=step_b.id,
            name="Job Sprint B",
            agent_type="test",
            status="success",
            wiki_page_id=wiki_page_b.id,
        )
        db_session.add(job_b)
        await db_session.flush()

        # Czyscimy sprint_a - nie ma tam ticketow, wiec 0 usuniec
        deleted_count = await svc.clean_pipeline_logs_for_sprint(db_session, project_id=project.id, sprint_id=sprint_a.id)

        assert deleted_count == 0
        mock_delete_object.assert_not_called()

        # Strona wiki sprint_b nadal istnieje
        result = await db_session.execute(select(WikiPage).where(WikiPage.id == wiki_page_b_id))
        assert result.scalar_one_or_none() is not None

    @patch("monolynx.services.wiki.delete_object")
    async def test_pipeline_from_other_project_not_touched(self, mock_delete_object, db_session):
        """Joby z innego projektu nie sa usuwane."""
        from monolynx.models.wiki_page import WikiPage

        project_a = await _flush_project(db_session, "cpl4a")
        project_b = await _flush_project(db_session, "cpl4b")
        user = await _make_user(db_session, "cpl4")

        sprint_b = await _flush_sprint(db_session, project_b.id, name="Sprint Proj B cpl4")
        ticket_b = await _flush_ticket(db_session, project_b.id, number=9904, sprint_id=sprint_b.id)

        pipeline_b = await svc.create_pipeline(
            db_session,
            project_id=project_b.id,
            pipeline_type="ticket_work",
            ticket_id=ticket_b.id,
        )
        step_b = pipeline_b.steps[0]

        wiki_page_b = await self._make_wiki_page(db_session, project_b, user, "cpl4b")
        wiki_page_b_id = wiki_page_b.id

        job_b = PipelineJob(
            step_id=step_b.id,
            name="Job Proj B",
            agent_type="test",
            status="success",
            wiki_page_id=wiki_page_b.id,
        )
        db_session.add(job_b)
        await db_session.flush()

        # Sprzatamy projekt_a z sprint_b - inny projekt, nic nie dotykamy
        deleted_count = await svc.clean_pipeline_logs_for_sprint(db_session, project_id=project_a.id, sprint_id=sprint_b.id)

        assert deleted_count == 0
        mock_delete_object.assert_not_called()

        # Strona wiki projektu_b nadal istnieje
        result = await db_session.execute(select(WikiPage).where(WikiPage.id == wiki_page_b_id))
        assert result.scalar_one_or_none() is not None
