"""Testy integracyjne -- edycja i usuwanie projektow."""

import secrets

import pytest

from monolynx.models.project import Project
from tests.conftest import login_session


async def _create_project(db_session, name="Test Projekt", slug="test-projekt"):
    """Tworzy projekt w bazie i zwraca go."""
    project = Project(
        name=name,
        slug=slug,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.mark.integration
class TestEditProject:
    async def test_edit_project_requires_auth(self, client, db_session):
        """GET /dashboard/{slug}/settings bez sesji redirectuje na login."""
        await _create_project(db_session)
        response = await client.get("/dashboard/test-projekt/settings", follow_redirects=False)
        assert response.status_code == 303
        assert "/auth/login" in response.headers["location"]

    async def test_edit_project_form_loads(self, client, db_session):
        """GET /dashboard/{slug}/settings wyswietla formularz z aktualnymi danymi."""
        await _create_project(db_session)
        client = await login_session(client, db_session, email="edit-test@example.com")

        response = await client.get("/dashboard/test-projekt/settings", follow_redirects=False)
        assert response.status_code == 200
        assert "Test Projekt" in response.text
        assert "test-projekt" in response.text

    async def test_edit_project_success(self, client, db_session):
        """POST z poprawnymi danymi zmienia nazwe i slug projektu."""
        project = await _create_project(db_session)
        client = await login_session(client, db_session, email="edit-test2@example.com")

        response = await client.post(
            "/dashboard/test-projekt/settings",
            data={"name": "Nowa Nazwa", "slug": "nowy-slug"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard/"

        await db_session.refresh(project)
        assert project.name == "Nowa Nazwa"
        assert project.slug == "nowy-slug"

    async def test_edit_project_duplicate_slug(self, client, db_session):
        """POST z istniejacym slugiem pokazuje blad walidacji."""
        await _create_project(db_session, name="Projekt A", slug="dup-edit-a")
        await _create_project(db_session, name="Projekt B", slug="dup-edit-b")
        client = await login_session(client, db_session, email="edit-test3@example.com")

        response = await client.post(
            "/dashboard/dup-edit-b/settings",
            data={"name": "Projekt B", "slug": "dup-edit-a"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "juz istnieje" in response.text

    async def test_edit_project_empty_name(self, client, db_session):
        """Walidacja pustych pol przy edycji."""
        await _create_project(db_session)
        client = await login_session(client, db_session, email="edit-test4@example.com")

        response = await client.post(
            "/dashboard/test-projekt/settings",
            data={"name": "", "slug": "test-projekt"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "wymagane" in response.text

    async def test_edit_project_invalid_slug(self, client, db_session):
        """Walidacja formatu sluga przy edycji."""
        await _create_project(db_session)
        client = await login_session(client, db_session, email="edit-test5@example.com")

        response = await client.post(
            "/dashboard/test-projekt/settings",
            data={"name": "Test", "slug": "Invalid Slug!"},
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert "male litery" in response.text


@pytest.mark.integration
class TestDeleteProject:
    async def test_delete_project_requires_auth(self, client, db_session):
        """POST /dashboard/{slug}/settings/delete bez sesji redirectuje na login."""
        await _create_project(db_session)
        response = await client.post("/dashboard/test-projekt/settings/delete", follow_redirects=False)
        assert response.status_code == 303
        assert "/auth/login" in response.headers["location"]

    async def test_delete_project_success(self, client, db_session):
        """POST ustawia is_active=False i redirectuje."""
        project = await _create_project(db_session)
        client = await login_session(client, db_session, email="del-test@example.com")

        response = await client.post("/dashboard/test-projekt/settings/delete", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard/"

        await db_session.refresh(project)
        assert project.is_active is False

    async def test_deleted_project_not_on_list(self, client, db_session):
        """Usuniety projekt nie pojawia sie na liscie."""
        await _create_project(db_session)
        client = await login_session(client, db_session, email="del-test2@example.com")

        # Usuwamy projekt
        await client.post("/dashboard/test-projekt/settings/delete", follow_redirects=False)

        # Sprawdzamy liste
        response = await client.get("/dashboard/", follow_redirects=False)
        assert response.status_code == 200
        assert "test-projekt" not in response.text
