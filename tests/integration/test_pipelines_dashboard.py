"""Testy integracyjne -- dashboard endpointy modulu Pipelines."""

from __future__ import annotations

import secrets
import uuid

import pytest

from monolynx.models.pipeline import Pipeline, PipelineJob, PipelineStep
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(suffix: str) -> Project:
    slug = f"pip-dash-{suffix}"
    return Project(
        name=f"Pipeline Dash {suffix}",
        slug=slug,
        code=("P" + secrets.token_hex(3)).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


async def _create_project_with_member(db_session, suffix: str, user: User) -> Project:
    """Tworzy projekt i dodaje usera jako czlonka."""
    project = _make_project(suffix)
    db_session.add(project)
    await db_session.flush()

    member = ProjectMember(project_id=project.id, user_id=user.id, role="owner")
    db_session.add(member)
    await db_session.flush()

    return project


async def _login_existing_user(client, email: str) -> None:
    """Loguje istniejacego uzytkownika (nie tworzy nowego)."""
    response = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


# ---------------------------------------------------------------------------
# GET /dashboard/{slug}/pipelines/
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPipelinesList:
    async def test_list_returns_200(self, client, db_session):
        """GET lista pipeline'ow -> 200."""
        email = "pip-list-200@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project = await _create_project_with_member(db_session, "list200", user)
        await _login_existing_user(client, email)

        resp = await client.get(f"/dashboard/{project.slug}/pipelines/")
        assert resp.status_code == 200

    async def test_list_no_session_redirects(self, client, db_session):
        """Brak sesji -> redirect do /auth/login."""
        resp = await client.get("/dashboard/any-slug/pipelines/", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_list_nonexistent_project_returns_404(self, client, db_session):
        """Nieistniejacy projekt -> 404."""
        await login_session(client, db_session, email="pip-list-404@test.com")

        resp = await client.get("/dashboard/nonexistent-pip-proj-xyz/pipelines/")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /dashboard/{slug}/pipelines/api/list
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApiListPipelines:
    async def test_api_list_returns_200_with_structure(self, client, db_session):
        """GET api/list -> 200 z poprawna struktura JSON."""
        email = "pip-apilist-200@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project = await _create_project_with_member(db_session, "apilist200", user)
        await _login_existing_user(client, email)

        resp = await client.get(f"/dashboard/{project.slug}/pipelines/api/list")
        assert resp.status_code == 200

        data = resp.json()
        assert "pipelines" in data
        assert "total" in data
        assert "page" in data
        assert isinstance(data["pipelines"], list)
        assert data["total"] == 0

    async def test_api_list_shows_created_pipelines(self, client, db_session):
        """api/list zawiera pipeline'y projektu."""
        email = "pip-apilist-items@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project = await _create_project_with_member(db_session, "apilitems", user)

        # Tworzy pipeline bezposrednio (pomijamy mock_factory - create_pipeline commituje)
        pipeline = Pipeline(
            project_id=project.id,
            pipeline_type="sprint_close",
            status="created",
            meta={},
        )
        db_session.add(pipeline)
        await db_session.flush()

        await _login_existing_user(client, email)

        resp = await client.get(f"/dashboard/{project.slug}/pipelines/api/list")
        assert resp.status_code == 200

        data = resp.json()
        assert data["total"] == 1
        assert len(data["pipelines"]) == 1

    async def test_api_list_no_session_returns_401(self, client, db_session):
        """Brak sesji -> 401."""
        resp = await client.get("/dashboard/any-slug/pipelines/api/list")
        assert resp.status_code == 401

    async def test_api_list_pagination_params(self, client, db_session):
        """api/list akceptuje parametr page."""
        email = "pip-apilist-page@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project = await _create_project_with_member(db_session, "apilpage", user)
        await _login_existing_user(client, email)

        resp = await client.get(f"/dashboard/{project.slug}/pipelines/api/list?page=1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["page"] == 1


# ---------------------------------------------------------------------------
# GET /dashboard/{slug}/pipelines/api/{pipeline_id}
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApiGetPipeline:
    async def test_api_get_returns_200_tree(self, client, db_session):
        """GET api/{id} -> 200 z drzewem pipeline'u."""
        email = "pip-apiget-200@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project = await _create_project_with_member(db_session, "apiget200", user)

        pipeline = Pipeline(
            project_id=project.id,
            pipeline_type="sprint_close",
            status="created",
            meta={},
        )
        db_session.add(pipeline)
        await db_session.flush()

        await _login_existing_user(client, email)

        resp = await client.get(f"/dashboard/{project.slug}/pipelines/api/{pipeline.id}")
        assert resp.status_code == 200

        data = resp.json()
        assert "id" in data
        assert "status" in data
        assert "steps" in data

    async def test_api_get_nonexistent_pipeline_returns_404(self, client, db_session):
        """Nieistniejacy pipeline -> 404."""
        email = "pip-apiget-404@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project = await _create_project_with_member(db_session, "apiget404", user)
        await _login_existing_user(client, email)

        resp = await client.get(f"/dashboard/{project.slug}/pipelines/api/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_api_get_different_project_returns_404(self, client, db_session):
        """Pipeline innego projektu -> 404 (izolacja projektow)."""
        email = "pip-apiget-xproj@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project_a = await _create_project_with_member(db_session, "apigetxa", user)
        project_b = _make_project("apigetxb")
        db_session.add(project_b)
        await db_session.flush()

        pipeline = Pipeline(
            project_id=project_b.id,
            pipeline_type="sprint_close",
            status="created",
            meta={},
        )
        db_session.add(pipeline)
        await db_session.flush()

        await _login_existing_user(client, email)

        # Pipeline nalezacy do project_b, zapytanie przez project_a
        resp = await client.get(f"/dashboard/{project_a.slug}/pipelines/api/{pipeline.id}")
        assert resp.status_code == 404

    async def test_api_get_no_session_returns_401(self, client, db_session):
        """Brak sesji -> 401."""
        resp = await client.get(f"/dashboard/any-slug/pipelines/api/{uuid.uuid4()}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /dashboard/{slug}/pipelines/{id} (detail)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPipelineDetail:
    async def test_detail_returns_200(self, client, db_session):
        """GET detail pipeline'u -> 200."""
        email = "pip-detail-200@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project = await _create_project_with_member(db_session, "detail200", user)

        pipeline = Pipeline(
            project_id=project.id,
            pipeline_type="sprint_close",
            status="created",
            meta={},
        )
        db_session.add(pipeline)
        await db_session.flush()

        await _login_existing_user(client, email)

        resp = await client.get(f"/dashboard/{project.slug}/pipelines/{pipeline.id}")
        assert resp.status_code == 200

    async def test_detail_nonexistent_returns_404(self, client, db_session):
        """Nieistniejacy pipeline -> 404."""
        email = "pip-detail-404@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project = await _create_project_with_member(db_session, "detail404", user)
        await _login_existing_user(client, email)

        resp = await client.get(f"/dashboard/{project.slug}/pipelines/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_detail_no_session_redirects(self, client, db_session):
        """Brak sesji -> redirect do /auth/login."""
        resp = await client.get(f"/dashboard/any-slug/pipelines/{uuid.uuid4()}", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]


# ---------------------------------------------------------------------------
# GET /dashboard/{slug}/pipelines/{pipeline_id}/jobs/{job_id}
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestJobDetail:
    async def test_job_detail_returns_200(self, client, db_session):
        """GET job detail -> 200."""
        email = "pip-job-200@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project = await _create_project_with_member(db_session, "job200", user)

        # Twórz pipeline + step + job recznie (bez commit z svc - wewnatrz transakcji)
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
            name="Test Research Job",
            agent_type="researcher",
            status="running",
        )
        db_session.add(job)
        await db_session.flush()

        await _login_existing_user(client, email)

        resp = await client.get(f"/dashboard/{project.slug}/pipelines/{pipeline.id}/jobs/{job.id}")
        assert resp.status_code == 200

    async def test_job_detail_job_from_other_pipeline_returns_404(self, client, db_session):
        """Job z innego projektu -> 404."""
        email = "pip-job-404@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=True)
        db_session.add(user)
        await db_session.flush()

        project_a = await _create_project_with_member(db_session, "joba1", user)
        project_b = _make_project("jobb1")
        db_session.add(project_b)
        await db_session.flush()

        # Pipeline i job naleza do project_b
        pipeline_b = Pipeline(
            project_id=project_b.id,
            pipeline_type="ticket_work",
            status="running",
            meta={},
        )
        db_session.add(pipeline_b)
        await db_session.flush()

        step_b = PipelineStep(
            pipeline_id=pipeline_b.id,
            name="research",
            position=0,
            status="running",
        )
        db_session.add(step_b)
        await db_session.flush()

        job_b = PipelineJob(
            step_id=step_b.id,
            name="Other Project Job",
            agent_type="researcher",
            status="running",
        )
        db_session.add(job_b)
        await db_session.flush()

        await _login_existing_user(client, email)

        # Zapytanie przez project_a, ale pipeline nalezacy do project_b
        resp = await client.get(f"/dashboard/{project_a.slug}/pipelines/{pipeline_b.id}/jobs/{job_b.id}")
        assert resp.status_code == 404

    async def test_job_detail_no_session_redirects(self, client, db_session):
        """Brak sesji -> redirect."""
        resp = await client.get(
            f"/dashboard/any/pipelines/{uuid.uuid4()}/jobs/{uuid.uuid4()}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]
