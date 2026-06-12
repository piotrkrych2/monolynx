"""Testy integracyjne -- integracja pipelines z wiki (append_job_log, exclude_from_embeddings)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from monolynx.models.pipeline import Pipeline, PipelineJob, PipelineStep
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services import pipelines as svc
from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(suffix: str) -> Project:
    return Project(
        name=f"Pipeline Wiki {suffix}",
        slug=f"pip-wiki-{suffix}",
        code=("W" + uuid.uuid4().hex[:4]).upper(),
        api_key=uuid.uuid4().hex,
        is_active=True,
    )


async def _create_project(db_session, suffix: str) -> Project:
    project = _make_project(suffix)
    db_session.add(project)
    await db_session.flush()
    return project


async def _create_user(db_session, email: str) -> User:
    user = User(email=email, password_hash=hash_password("pass"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _create_pipeline_with_job(db_session, project: Project) -> tuple[Pipeline, PipelineStep, PipelineJob]:
    """Tworzy pipeline + step + job bezposrednio przez ORM (bez commit - transakcja testowa)."""
    pipeline = Pipeline(
        project_id=project.id,
        pipeline_type="ticket_work",
        status="running",
        meta={},
    )
    db_session.add(pipeline)
    await db_session.flush()

    step = PipelineStep(
        pipeline_id=pipeline.id,
        name="research",
        position=0,
        status="running",
    )
    db_session.add(step)
    await db_session.flush()

    job = PipelineJob(
        step_id=step.id,
        name="Research Job",
        agent_type="researcher",
        status="running",
    )
    db_session.add(job)
    await db_session.flush()

    return pipeline, step, job


# ---------------------------------------------------------------------------
# append_job_log
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAppendJobLog:
    @patch("monolynx.services.wiki.sync_backlinks", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_first_call_creates_wiki_page(
        self,
        mock_get_markdown,
        mock_upload,
        mock_embeddings,
        mock_backlinks,
        db_session,
    ):
        """Pierwsze wywolanie append_job_log tworzy strone wiki i ustawia job.wiki_page_id."""
        project = await _create_project(db_session, "ajl1")
        user = await _create_user(db_session, "pip-wiki-ajl1@test.com")
        _pipeline, _step, job = await _create_pipeline_with_job(db_session, project)

        mock_upload.return_value = f"{project.slug}/{uuid.uuid4()}.md"
        mock_get_markdown.return_value = "# Pipeline logi\n"

        result = await svc.append_job_log(
            db_session,
            job_id=job.id,
            content="# Log tresc\nPierwszy wpis",
            user_id=user.id,
        )

        assert isinstance(result, WikiPage)
        # job.wiki_page_id powinno byc ustawione
        refreshed_job = await db_session.get(PipelineJob, job.id)
        assert refreshed_job is not None
        assert refreshed_job.wiki_page_id is not None
        assert refreshed_job.wiki_page_id == result.id

    @patch("monolynx.services.wiki.sync_backlinks", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_page_has_exclude_from_embeddings_true(
        self,
        mock_get_markdown,
        mock_upload,
        mock_embeddings,
        mock_backlinks,
        db_session,
    ):
        """Strona wiki logu joba ma exclude_from_embeddings=True."""
        project = await _create_project(db_session, "ajl2")
        user = await _create_user(db_session, "pip-wiki-ajl2@test.com")
        _pipeline, _step, job = await _create_pipeline_with_job(db_session, project)

        mock_upload.return_value = f"{project.slug}/{uuid.uuid4()}.md"
        mock_get_markdown.return_value = "# Pipeline logi\n"

        result = await svc.append_job_log(
            db_session,
            job_id=job.id,
            content="Test log content",
            user_id=user.id,
        )

        assert isinstance(result, WikiPage)
        # Pobierz strone z DB i sprawdz flage
        page_result = await db_session.execute(select(WikiPage).where(WikiPage.id == result.id))
        page = page_result.scalar_one()
        assert page.exclude_from_embeddings is True

    @patch("monolynx.services.wiki.sync_backlinks", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_second_call_appends_content(
        self,
        mock_get_markdown,
        mock_upload,
        mock_embeddings,
        mock_backlinks,
        db_session,
    ):
        """Drugie wywolanie append_job_log dokleja tresc do istniejacaej strony."""
        project = await _create_project(db_session, "ajl3")
        user = await _create_user(db_session, "pip-wiki-ajl3@test.com")
        _pipeline, _step, job = await _create_pipeline_with_job(db_session, project)

        mock_upload.return_value = f"{project.slug}/{uuid.uuid4()}.md"
        mock_get_markdown.return_value = "# Pipeline logi\n"

        # Pierwsze wywolanie
        first_page = await svc.append_job_log(
            db_session,
            job_id=job.id,
            content="Pierwszy log",
            user_id=user.id,
        )
        assert isinstance(first_page, WikiPage)

        # Przygotuj mock get_markdown dla drugiego wywolania (czyta obecna tresc)
        mock_get_markdown.return_value = "Pierwszy log"

        # Drugie wywolanie
        second_result = await svc.append_job_log(
            db_session,
            job_id=job.id,
            content="Drugi log",
            user_id=user.id,
        )

        # Powinno zwrocic te sama strone
        assert isinstance(second_result, WikiPage)
        assert second_result.id == first_page.id

        # upload_markdown powinien byc wywolany dla drugiego append
        assert mock_upload.call_count >= 2

    async def test_nonexistent_job_returns_error_str(self, db_session):
        """Nieistniejacy job -> str z bledem."""
        user = await _create_user(db_session, "pip-wiki-nonjob@test.com")

        result = await svc.append_job_log(
            db_session,
            job_id=uuid.uuid4(),
            content="Log",
            user_id=user.id,
        )

        assert isinstance(result, str)
        assert "nie istnieje" in result


# ---------------------------------------------------------------------------
# exclude_from_embeddings - weryfikacja flagi bezposrednio
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExcludeFromEmbeddings:
    @patch("monolynx.services.wiki.sync_backlinks", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_parent_log_page_has_exclude_flag(
        self,
        mock_get_markdown,
        mock_upload,
        mock_embeddings,
        mock_backlinks,
        db_session,
    ):
        """Strona pipeline-logi ma exclude_from_embeddings=True."""
        project = await _create_project(db_session, "exc1")
        user = await _create_user(db_session, "pip-wiki-exc1@test.com")

        mock_upload.return_value = f"{project.slug}/{uuid.uuid4()}.md"
        mock_get_markdown.return_value = "# Pipeline logi\n"

        parent = await svc.ensure_pipeline_logs_parent(db_session, project, user.id)

        assert parent is not None
        page_result = await db_session.execute(select(WikiPage).where(WikiPage.id == parent.id))
        page = page_result.scalar_one()
        assert page.exclude_from_embeddings is True

    @patch("monolynx.services.wiki.sync_backlinks", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    @patch("monolynx.services.wiki.get_markdown")
    async def test_ensure_pipeline_logs_parent_idempotent(
        self,
        mock_get_markdown,
        mock_upload,
        mock_embeddings,
        mock_backlinks,
        db_session,
    ):
        """ensure_pipeline_logs_parent jest idempotentny - drugie wywolanie zwraca ta sama strone."""
        project = await _create_project(db_session, "exc2")
        user = await _create_user(db_session, "pip-wiki-exc2@test.com")

        mock_upload.return_value = f"{project.slug}/{uuid.uuid4()}.md"
        mock_get_markdown.return_value = "# Pipeline logi\n"

        parent1 = await svc.ensure_pipeline_logs_parent(db_session, project, user.id)
        parent2 = await svc.ensure_pipeline_logs_parent(db_session, project, user.id)

        assert parent1.id == parent2.id
        # Sprawdz ze w DB jest tylko jedna strona o slug pipeline-logi
        result = await db_session.execute(
            select(WikiPage).where(
                WikiPage.project_id == project.id,
                WikiPage.slug == "pipeline-logi",
            )
        )
        pages = result.scalars().all()
        assert len(pages) == 1
