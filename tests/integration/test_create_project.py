"""Testy integracyjne -- tworzenie projektow z dashboardu."""

import pytest
from sqlalchemy import select

from open_sentry.models.project import Project
from tests.conftest import login_session


@pytest.mark.integration
class TestCreateProject:
    async def test_create_project_form_requires_auth(self, client):
        """GET /dashboard/create-project bez sesji redirectuje na login."""
        response = await client.get("/dashboard/create-project", follow_redirects=False)
        assert response.status_code == 303
        assert "/auth/login" in response.headers["location"]

    async def test_create_project_post_requires_auth(self, client):
        """POST /dashboard/create-project bez sesji redirectuje na login."""
        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Test", "slug": "test"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "/auth/login" in response.headers["location"]

    async def test_create_project_success(self, client, db_session):
        """POST z poprawnymi danymi tworzy projekt i redirectuje."""
        client = await login_session(client, db_session)

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Moj Projekt", "slug": "moj-projekt"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard/"

        result = await db_session.execute(
            select(Project).where(Project.slug == "moj-projekt")
        )
        project = result.scalar_one_or_none()
        assert project is not None
        assert project.name == "Moj Projekt"
        assert project.api_key is not None

    async def test_create_project_duplicate_slug(self, client, db_session):
        """POST z istniejacym slugiem pokazuje blad."""
        client = await login_session(client, db_session, email="create-dup@example.com")

        # Tworzymy pierwszy projekt
        await client.post(
            "/dashboard/create-project",
            data={"name": "Projekt 1", "slug": "duplikat"},
            follow_redirects=False,
        )

        # Proba stworzenia drugiego z tym samym slugiem
        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Projekt 2", "slug": "duplikat"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "juz istnieje" in response.text

    async def test_create_project_empty_name(self, client, db_session):
        """Walidacja pustych pol."""
        client = await login_session(
            client, db_session, email="create-empty@example.com"
        )

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "", "slug": "test-slug"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "wymagane" in response.text

    async def test_create_project_invalid_slug(self, client, db_session):
        """Walidacja formatu sluga."""
        client = await login_session(client, db_session, email="create-inv@example.com")

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Test", "slug": "Invalid Slug!"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "male litery" in response.text
